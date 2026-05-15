import atexit
import logging
import sys
from datetime import datetime, timezone

import frontmatter
from mcp.server.fastmcp import FastMCP
from slugify import slugify

from config import VAULT_PATH
from embeddings import ping
from indexer import (
    get_collection,
    get_project_notes as _indexer_get_project_notes,
    list_projects as _indexer_list_projects,
    search as _indexer_search,
)
from watcher import start_watching

logger = logging.getLogger(__name__)

mcp = FastMCP("obsidian-rag")


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


# --- Tools ---

@mcp.tool()
def search_knowledge_base(query: str) -> str:
    """Search personal knowledge base from Obsidian vault. Use this to find context about specific projects, clients, workflow procedures, past decisions, personal preferences, team members, recurring errors, or any domain knowledge stored in notes. Call this before answering any question that might benefit from personal context."""
    try:
        return _format_search_results(_indexer_search(query))
    except Exception as exc:
        logger.exception("search_knowledge_base failed")
        return f"Error: {exc}"


@mcp.tool()
def create_note(
    title: str,
    content: str,
    project: str,
    tags: list[str] | None = None,
    note_type: str = "note",
) -> str:
    """Create a new note in the Obsidian vault linked to a project. Use proactively when encountering an unusual error (record: what happened, why, how fixed), making an architectural decision, or discovering something worth remembering across sessions. Tags must be specific and searchable."""
    title = (title or "").strip()
    project = (project or "").strip()
    content = (content or "").strip()
    note_type = (note_type or "note").strip() or "note"

    if not title:
        return "Error: title is required"
    if not project:
        return "Error: project is required"
    if not content:
        return "Error: content is required"

    tags_list = [str(t) for t in (tags or []) if str(t).strip()]

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

    post = frontmatter.Post(
        content,
        title=title,
        project=project,
        tags=tags_list,
        type=note_type,
        created=datetime.now(timezone.utc).isoformat(),
    )

    try:
        target.write_text(frontmatter.dumps(post), encoding="utf-8")
    except Exception as exc:
        logger.exception("create_note write failed")
        return f"Error: cannot write file {target}: {exc}"

    rel = target.relative_to(VAULT_PATH).as_posix()
    logger.info("Created note: %s", rel)
    return f"Created: {rel}"


@mcp.tool()
def list_projects() -> str:
    """Returns the list of unique projects from the indexed Obsidian vault. Helps identify available project contexts before performing a search or listing notes."""
    try:
        return _format_projects(_indexer_list_projects())
    except Exception as exc:
        logger.exception("list_projects failed")
        return f"Error: {exc}"


@mcp.tool()
def get_project_notes(project: str) -> str:
    """Returns all notes for a specific project. Use when you need the full context of a project rather than a targeted semantic search."""
    project = (project or "").strip()
    if not project:
        return "Error: project is required"
    try:
        return _format_notes(project, _indexer_get_project_notes(project))
    except Exception as exc:
        logger.exception("get_project_notes failed")
        return f"Error: {exc}"


# --- Lifecycle ---

def _startup() -> None:
    if not ping():
        logger.critical(
            "Ollama is not reachable or embedding model is missing. "
            "Start Ollama and run: ollama pull nomic-embed-text"
        )
        sys.exit(1)

    count = get_collection().count()
    if count == 0:
        logger.warning(
            "ChromaDB collection is empty. Run first: python indexer.py --full-scan"
        )
    else:
        logger.info("Indexed chunks loaded: %d", count)

    observer = start_watching()

    def _shutdown() -> None:
        try:
            observer.stop()
            observer.join(timeout=5)
            logger.info("Watcher stopped")
        except Exception:
            pass

    atexit.register(_shutdown)


if __name__ == "__main__":
    _startup()
    mcp.run()
