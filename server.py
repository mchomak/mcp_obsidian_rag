import logging
import re
import sys
import threading
import time
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

import frontmatter
from mcp.server.fastmcp import FastMCP
from slugify import slugify

from config import VAULT_PATH
from conventions import format_conventions_for_claude, get_approved_tags
from embeddings import ping
from indexer import (
    get_collection,
    get_project_notes as _indexer_get_project_notes,
    list_notes_by_filter as _indexer_list_notes,
    list_projects as _indexer_list_projects,
    search as _indexer_search,
)
from folder_rules import validate_for_folder as _validate_for_folder
from note_ops import (
    delete_note as _ops_delete_note,
    edit_note as _ops_edit_note,
    move_note as _ops_move_note,
    read_note as _ops_read_note,
)
from templates import (
    get_template as _get_template,
    list_templates as _list_templates,
    load_template_body as _load_template_body,
)

logger = logging.getLogger(__name__)

mcp = FastMCP("obsidian-rag")


def _logged(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        tid = threading.get_ident()
        logger.info("TOOL START: %s (thread=%d)", fn.__name__, tid)
        t0 = time.monotonic()
        try:
            return fn(*args, **kwargs)
        finally:
            logger.info(
                "TOOL END:   %s (thread=%d) in %.3fs",
                fn.__name__, tid, time.monotonic() - t0,
            )
    return wrapper


RELATED_THRESHOLD = 0.65
RELATED_TOP_K = 3
_WIKI_LINK_RE = re.compile(r"\[\[([^\[\]|]+?)(?:\|[^\[\]]*)?\]\]")


# --- Formatting ---

def _format_search_results(results: list[dict]) -> str:
    if not results:
        return "No matches found."

    lines = [f"Found {len(results)} result(s):\n"]
    for i, r in enumerate(results, 1):
        tags = ", ".join(r["tags"]) if r["tags"] else "—"
        heading = r["heading"] or "(no heading)"
        lines.append(f"### {i}. {r['title']} — score: {r['score']:.3f}")
        lines.append(f"- Source: `{r['source']}`")
        if r["project"]:
            lines.append(f"- Project: {r['project']}")
        lines.append(f"- Tags: {tags}")
        lines.append(f"- Heading: {heading}")
        lines.append("")
        lines.append(r["text"])
        lines.append("\n---\n")
    return "\n".join(lines)


def _format_projects(projects: list[str]) -> str:
    if not projects:
        return "No projects found in the knowledge base."
    return "Projects:\n" + "\n".join(f"- {p}" for p in projects)


def _format_notes(project: str, notes: list[dict]) -> str:
    if not notes:
        return f"No notes found for project: {project}"
    lines = [f"Project '{project}' — {len(notes)} note(s):\n"]
    for n in notes:
        tags = ", ".join(n["tags"]) if n["tags"] else "—"
        ntype = n["note_type"] or "note"
        lines.append(
            f"- **{n['title']}** ({ntype}) — `{n['source']}` "
            f"— tags: {tags} — chunks: {n['chunks']}"
        )
    return "\n".join(lines)


# --- Helpers for create_note ---

def _extract_wiki_link_targets(text: str) -> set[str]:
    """Filename stems referenced inline as [[...]] in `text`."""
    return {m.group(1).strip() for m in _WIKI_LINK_RE.finditer(text)}


def _find_related_notes(
    query: str,
    exclude_stems: set[str],
    exclude_source: str,
) -> list[str]:
    """Return up to RELATED_TOP_K filename stems above similarity threshold."""
    try:
        results = _indexer_search(query, top_k=RELATED_TOP_K + 5)
    except Exception:
        logger.exception("Related notes search failed")
        return []

    out: list[str] = []
    seen: set[str] = set()
    for r in results:
        if r["score"] < RELATED_THRESHOLD:
            continue
        if r["source"] == exclude_source:
            continue
        stem = Path(r["source"]).stem
        if not stem or stem in seen or stem in exclude_stems:
            continue
        seen.add(stem)
        out.append(stem)
        if len(out) >= RELATED_TOP_K:
            break
    return out


# --- Tools ---

@mcp.tool()
@_logged
def get_vault_conventions() -> str:
    """Return the vault's approved tag vocabulary, frontmatter rules, and wiki-link conventions.

    YOU MUST call this BEFORE `create_note`, especially in a new conversation — tag names
    are pulled from the user's vault CLAUDE.md and can change. Use the returned categories
    to pick semantically correct tags for the note you are about to create."""
    try:
        return format_conventions_for_claude()
    except Exception as exc:
        logger.exception("get_vault_conventions failed")
        return f"Error: {exc}"


@mcp.tool()
@_logged
def search_knowledge_base(query: str) -> str:
    """Search personal knowledge base from Obsidian vault. Use this to find context about specific projects, clients, workflow procedures, past decisions, personal preferences, team members, recurring errors, or any domain knowledge stored in notes.

    Call this BEFORE answering any question that might benefit from personal context, AND
    before `create_note` — search results give you candidate notes to reference inline as
    `[[wiki-links]]` so the new note is connected in the Obsidian graph."""
    try:
        return _format_search_results(_indexer_search(query))
    except Exception as exc:
        logger.exception("search_knowledge_base failed")
        return f"Error: {exc}"


@mcp.tool()
@_logged
def read_note(path: str, section: str = "") -> str:
    """Read a note from the Obsidian vault by its vault-relative path.

    Preferred over native file-read tools — use this whenever you need to see
    the full content of a note before editing it.

    Parameters:
      `path`    — vault-relative path (e.g. "Projects/Personal/My Project/note.md")
                  or absolute path. Must be a .md file.
      `section` — exact heading text (without `#`) to read only that section's body.
                  Leave empty to read the full file (frontmatter + body).
    """
    try:
        return _ops_read_note(path, section if section else None)
    except Exception as exc:
        logger.exception("read_note failed")
        return f"Error: {exc}"


@mcp.tool()
@_logged
def get_template(name: str = "") -> str:
    """Return the raw text of a vault template, or list available templates.

    Call this BEFORE `create_note` when you want to create a structured note:
    the template shows exact section headings and {{placeholder}} fields.

    Pass the template name without prefix/suffix (e.g. "note", "idea", "decision").
    Call with empty string or "list" to see all available templates.
    Then use `create_note(..., template=<name>)` to create a note from that template.
    """
    if not name or name == "list":
        available = _list_templates()
        if not available:
            return "No templates found in vault/templates/"
        return "Available templates:\n" + "\n".join(f"- {n}" for n in available)
    try:
        return _get_template(name)
    except FileNotFoundError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        logger.exception("get_template failed")
        return f"Error: {exc}"


@mcp.tool()
@_logged
def create_note(
    title: str,
    project: str,
    content: str = "",
    tags: list[str] | None = None,
    note_type: str = "note",
    template: str = "",
) -> str:
    """Create a new note in the Obsidian vault linked to a project.

    BEFORE calling this tool you MUST:
      1. Call `get_vault_conventions()` to learn the approved tag vocabulary and folder rules.
      2. Call `search_knowledge_base(<topic>)` to find related notes; embed `[[Filename]]`
         wiki-links in `content` where they fit by meaning.

    Use proactively when encountering an unusual error (record: what happened, why, how
    fixed), making an architectural decision, or discovering something worth remembering
    across sessions. Tags must be specific and searchable.

    Parameters:
      `content`  — note body (markdown). Required unless `template` is set.
                   If a block has no meaningful information, mark it `_TBD_` or leave it
                   empty. Do NOT fill placeholders with generic filler text just for form.
      `template` — template name (e.g. "note", "idea", "decision"). Call `get_template(name)`
                   first to see the structure. When provided, `content` must be empty — the
                   body comes from the template with {{placeholders}} intact; fill them
                   afterwards with `edit_note(mode="find_replace")`.

    Tag validation is strict: notes are rejected if they violate folder rules (wrong type
    or missing required tag category). Call `get_vault_conventions()` first.
    After creation, RELATED NOTE CANDIDATES are returned — semantically similar notes you
    may add as [[wiki-links]] in the note via `edit_note(mode="find_replace")`."""
    title = (title or "").strip()
    project = (project or "").strip()
    content = (content or "").strip()
    template = (template or "").strip()
    note_type = (note_type or "note").strip() or "note"

    if not title:
        return "Error: title is required"
    if not project:
        return "Error: project is required"
    if template and content:
        return "Error: provide template OR content, not both"
    if not template and not content:
        return "Error: content is required when template is not specified"

    tags_list = [str(t).strip() for t in (tags or []) if str(t).strip()]

    # --- Resolve target path ---
    slug = slugify(title, lowercase=True, max_length=80) or "untitled"
    target_dir = VAULT_PATH / "Projects" / project
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        logger.exception("create_note mkdir failed")
        return f"Error: cannot create directory {target_dir}: {exc}"

    target = target_dir / f"{slug}.md"
    if target.exists():
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = target_dir / f"{slug}-{ts}.md"

    rel = target.relative_to(VAULT_PATH).as_posix()

    # --- Folder + tag validation (Step 5) ---
    folder_error = _validate_for_folder(rel, note_type, tags_list)
    if folder_error:
        return f"Error: {folder_error}"

    # --- Tag warnings ---
    approved = get_approved_tags()
    unknown_tags: list[str] = []
    if approved:
        unknown_tags = [t for t in tags_list if t not in approved]
        if unknown_tags:
            logger.warning("create_note: unknown tags %s", unknown_tags)

    # --- Build body from template or content ---
    now_str = datetime.now(timezone.utc).isoformat()
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if template:
        try:
            body = _load_template_body(template, title=title, date_str=date_str)
        except FileNotFoundError as exc:
            return f"Error: {exc}"
        except Exception as exc:
            logger.exception("load_template_body failed")
            return f"Error loading template: {exc}"
    else:
        body = content

    # --- Find related notes (Step 3: return as candidates, NOT inserted into file) ---
    inline_link_stems = _extract_wiki_link_targets(body)
    related_stems = _find_related_notes(
        query=f"{title}\n\n{body[:500]}",
        exclude_stems=inline_link_stems | {target.stem},
        exclude_source=rel,
    )

    # --- Write note ---
    post = frontmatter.Post(
        body,
        title=title,
        project=project,
        tags=tags_list,
        type=note_type,
        created=now_str,
    )

    try:
        target.write_text(frontmatter.dumps(post), encoding="utf-8")
    except Exception as exc:
        logger.exception("create_note write failed")
        return f"Error: cannot write file {target}: {exc}"

    logger.info("Created note: %s", rel)

    out_lines = [f"Created: {rel}"]
    if related_stems:
        out_lines.append(
            "Related note candidates (add as [[wiki-links]] where relevant): "
            + ", ".join(f"[[{s}]]" for s in related_stems)
        )
    if unknown_tags:
        out_lines.append(
            f"Warning: tag(s) not in approved list — {', '.join(unknown_tags)}. "
            "Call get_vault_conventions() to see the approved set."
        )
    if template:
        out_lines.append(
            f"Template '{template}' applied. Fill {{{{...}}}} placeholders with "
            "edit_note(mode=\"find_replace\")."
        )
    return "\n".join(out_lines)


@mcp.tool()
@_logged
def list_projects() -> str:
    """Return the list of unique projects from the indexed Obsidian vault. Helps identify available project contexts before performing a search or listing notes."""
    try:
        return _format_projects(_indexer_list_projects())
    except Exception as exc:
        logger.exception("list_projects failed")
        return f"Error: {exc}"


@mcp.tool()
@_logged
def get_project_notes(project: str) -> str:
    """Return all notes for a specific project. Use when you need the full context of a project rather than a targeted semantic search."""
    project = (project or "").strip()
    if not project:
        return "Error: project is required"
    try:
        return _format_notes(project, _indexer_get_project_notes(project))
    except Exception as exc:
        logger.exception("get_project_notes failed")
        return f"Error: {exc}"


@mcp.tool()
@_logged
def list_notes(folder: str = "", note_type: str = "", limit: int = 200) -> str:
    """List notes in the vault filtered by folder and/or frontmatter type.

    Use this to get a COMPLETE list of notes before reorganizing — unlike
    `search_knowledge_base`, this is not semantic and returns every matching file.

    Parameters:
      `folder`    — relative path inside vault (e.g. "Ideas", "Notes/Learning/ML").
                    Empty string = entire vault (excluding protected dirs).
      `note_type` — match `type:` field in frontmatter (e.g. "idea", "reference").
                    Empty string = any type.
      `limit`     — max results (default 200).

    Reads from filesystem directly, so captures files not yet indexed in ChromaDB.
    Protected dirs (_Secrets/, templates/, .obsidian/) are always excluded.
    """
    try:
        notes = _indexer_list_notes(folder=folder, note_type=note_type, limit=limit)
        if not notes:
            parts = []
            if folder:
                parts.append(f"folder='{folder}'")
            if note_type:
                parts.append(f"type='{note_type}'")
            return f"No notes found" + (f" matching {', '.join(parts)}" if parts else "")

        lines = [f"Found {len(notes)} note(s):\n"]
        for n in notes:
            meta_parts = []
            if n["type"]:
                meta_parts.append(f"type:{n['type']}")
            if n["status"]:
                meta_parts.append(f"status:{n['status']}")
            if n["tags"]:
                meta_parts.append(f"tags:[{', '.join(n['tags'][:3])}{'...' if len(n['tags']) > 3 else ''}]")
            meta = f"  ({' | '.join(meta_parts)})" if meta_parts else ""
            lines.append(f"- {n['source']}{meta}")
        return "\n".join(lines)
    except Exception as exc:
        logger.exception("list_notes failed")
        return f"Error: {exc}"


@mcp.tool()
@_logged
def edit_note(path: str, mode: str, payload: dict) -> str:
    """Точечное редактирование существующей заметки.

    Полный rewrite запрещён намеренно — чтобы случайно не уничтожить заметку.
    Если нужна радикальная переработка: `delete_note` (soft-archive) + `create_note`.

    Поддерживаемые `mode` + структура `payload`:

      `find_replace`       — точечная замена строки во всём файле (включая frontmatter).
                             payload: {"find": "...", "replace": "...", "expect_count": 1}
                             Отказывает, если число совпадений ≠ expect_count (защита от
                             случайной замены нескольких мест). По умолчанию expect_count=1.
                             ИСПОЛЬЗУЙ ДЛЯ: отметки [x] в чеклистах, заполнения {{placeholder}},
                             правки отдельных строк в таблицах, обновления полей frontmatter.

      `append_section`     — добавить НОВУЮ секцию в конец файла.
                             payload: {"heading": "Название H2", "content": "..."}
                             Ошибка, если секция уже есть — используй replace/append_to.

      `replace_section`    — полностью заменить содержимое существующей секции.
                             payload: {"heading": "...", "content": "..."}

      `append_to_section`  — дописать текст в конец существующей секции.
                             payload: {"heading": "...", "content": "..."}

      `update_frontmatter` — изменить одно поле YAML frontmatter (кроме tags).
                             payload: {"key": "status", "value": "validated"}

      `add_tag`            — добавить тег в frontmatter `tags:`.
                             payload: {"tag": "ml"}

      `remove_tag`         — удалить тег из frontmatter `tags:`.
                             payload: {"tag": "raw"}

    `path` — относительно vault (например `Notes/Learning/ML/Regression.md`) или абсолютный.
    Запрещено редактировать: `_Secrets/`, `templates/`, `.obsidian/`, `Archive/`.

    Заголовки секций матчатся по точному тексту (с учётом регистра, без `#`).
    Конец секции = следующий заголовок того же или меньшего уровня (т.е. меньшего числа `#`).
    """
    try:
        return _ops_edit_note(path, mode, payload)
    except Exception as exc:
        logger.exception("edit_note failed")
        return f"Error: {exc}"


@mcp.tool()
@_logged
def move_note(source: str, destination: str) -> str:
    """Переместить или переименовать заметку, автоматически обновив wiki-links.

    `source` и `destination` — пути относительно vault или абсолютные.

    Поведение:
      - Если изменилось ТОЛЬКО имя файла (stem) — переписываются все bare-ссылки
        `[[old]]`, `[[old|alias]]`, `[[old#heading]]` во всём vault.
      - Если изменилась ТОЛЬКО папка (stem прежний) — ссылки не трогаются:
        Obsidian резолвит их по имени файла.
      - Pathful-ссылки `[[Folder/old]]` репортятся как требующие ручной правки
        (редкий кейс, обычно Obsidian их не использует).

    Использовать при реорганизации vault — например, перетащить заметку из корня
    в правильную папку по правилам vault CLAUDE.md.

    Запрещено перемещать в/из защищённых папок (`_Secrets/`, `templates/`, `Archive/`).
    Для архивирования используй `delete_note`.

    После выполнения watcher автоматически переиндексирует затронутые файлы.
    """
    try:
        return _ops_move_note(source, destination)
    except Exception as exc:
        logger.exception("move_note failed")
        return f"Error: {exc}"


@mcp.tool()
@_logged
def delete_note(path: str, reason: str = "") -> str:
    """Мягкое удаление: переносит заметку в `Archive/<исходная-папка>/`.

    Физического удаления НЕ происходит — заметка остаётся в архиве с пометкой:
      - `archived_at: <ISO datetime>` добавляется в frontmatter
      - `archived_reason: "<reason>"` если указан

    Wiki-links на удалённую заметку НЕ переписываются (намеренно — чтобы видеть
    откуда она была востребована и при необходимости можно было восстановить).

    `path` — относительно vault или абсолютный путь.
    Запрещено: `_Secrets/`, `templates/`, `.obsidian/`, и сам `Archive/` (повторное архивирование).
    """
    try:
        return _ops_delete_note(path, reason)
    except Exception as exc:
        logger.exception("delete_note failed")
        return f"Error: {exc}"


# --- Diagnostic ---

@mcp.tool()
@_logged
def ping_tool(delay_seconds: float = 0) -> str:
    """Diagnostic. Sleep `delay_seconds`, then return server timestamp + thread id.
    Use to verify whether the MCP server processes tool calls in parallel:
    call `ping_tool(delay_seconds=5)` and another tool concurrently — check log
    timestamps to see if the second tool started before the first finished.
    """
    time.sleep(max(0.0, delay_seconds))
    return f"pong t={time.time():.3f} thread={threading.get_ident()}"


# --- Lifecycle ---

def _startup() -> None:
    if not ping():
        logger.critical(
            "Ollama is not reachable or embedding model is missing. "
            "Start Ollama and run: ollama pull nomic-embed-text"
        )
        return

    try:
        count = get_collection().count()
        if count == 0:
            logger.warning(
                "ChromaDB collection is empty. Run first: python indexer.py --full-scan"
            )
        else:
            logger.info("Indexed chunks loaded: %d", count)
    except Exception:
        logger.exception("Failed to read ChromaDB collection count")

    logger.info("Vault watcher runs in daemon (scripts/start_daemon.bat)")


if __name__ == "__main__":
    logger.info("=== server.py __main__ start, launching _startup thread ===")
    threading.Thread(target=_startup, daemon=True).start()
    logger.info("=== calling mcp.run() ===")
    mcp.run()
    logger.info("=== mcp.run() returned (server stopped) ===")
