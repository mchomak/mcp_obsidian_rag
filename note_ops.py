"""Операции над заметками vault: edit / move / delete.

Все операции:
  - валидируют что путь внутри vault и не в защищённых папках;
  - выполняются атомарно в пределах одного файла (через tmp + replace);
  - НЕ вызывают индексацию руками — это делает watcher.
"""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import frontmatter

from config import ARCHIVE_DIR_NAME, EXCLUDED_DIRS, VAULT_PATH

logger = logging.getLogger(__name__)

# Папки, в которые ЗАПРЕЩЕНО писать любой из tools.
# Edit/move/delete отказывают если путь начинается с любой из них.
PROTECTED_DIRS: tuple[str, ...] = tuple(set(EXCLUDED_DIRS) | {".obsidian", ARCHIVE_DIR_NAME})


# --- Wiki-link regex ---
#
# Захватываем:
#   group 1 = optional path prefix ending in "/" (или пусто)
#   group 2 = stem (без / # |)
#   group 3 = опциональный "#heading" с #
#   group 4 = опциональный "|alias" с |
_WIKI_LINK_RE = re.compile(
    r"\[\[((?:[^\[\]|#\n]+/)*)([^\[\]|#/\n]+)(#[^\[\]|\n]+)?(\|[^\[\]\n]+)?\]\]"
)


# --- Path helpers ---

@dataclass(frozen=True)
class VaultPath:
    abs: Path
    rel: str  # POSIX-style, от корня vault


def _resolve_in_vault(user_path: str, *, must_exist: bool = True) -> VaultPath:
    """Резолвит путь относительно vault и валидирует."""
    if not user_path or not str(user_path).strip():
        raise ValueError("path is required")

    raw = Path(str(user_path).strip())
    if raw.is_absolute():
        abs_path = raw.resolve()
    else:
        abs_path = (VAULT_PATH / raw).resolve()

    try:
        rel = abs_path.relative_to(VAULT_PATH).as_posix()
    except ValueError:
        raise ValueError(f"Path is outside vault: {user_path}")

    if abs_path.suffix.lower() != ".md":
        raise ValueError(f"Only .md files are supported: {user_path}")

    if must_exist and not abs_path.is_file():
        raise FileNotFoundError(f"Note not found: {rel}")

    return VaultPath(abs=abs_path, rel=rel)


def _is_protected(rel: str) -> bool:
    parts = rel.split("/")
    return bool(parts) and parts[0] in PROTECTED_DIRS


def _ensure_writable(vp: VaultPath) -> None:
    if _is_protected(vp.rel):
        raise PermissionError(
            f"Refusing to modify protected path: {vp.rel} "
            f"(protected: {', '.join(sorted(PROTECTED_DIRS))})"
        )


# --- Atomic write ---

def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


# --- Section editing ---

# Любой ATX-заголовок: ловим уровень (# count) и текст.
_HEADING_LINE_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


def _find_section_bounds(body: str, heading: str) -> tuple[int, int, int] | None:
    """Найти границы секции по заголовку.

    Возвращает (heading_start, content_start, section_end) или None.
    section_end — позиция следующего заголовка того же или большего уровня (т.е. меньшего числа `#`), либо len(body).
    """
    heading_norm = heading.strip()
    for m in _HEADING_LINE_RE.finditer(body):
        if m.group(2).strip() == heading_norm:
            level = len(m.group(1))
            content_start = m.end()
            # newline after heading
            if content_start < len(body) and body[content_start] == "\n":
                content_start += 1

            end = len(body)
            for next_m in _HEADING_LINE_RE.finditer(body, pos=content_start):
                next_level = len(next_m.group(1))
                if next_level <= level:
                    end = next_m.start()
                    break
            return m.start(), content_start, end
    return None


def _apply_section_edit(body: str, mode: str, heading: str, content: str) -> str:
    """Возвращает новый body после правки секции."""
    bounds = _find_section_bounds(body, heading)
    payload = content.rstrip("\n")

    if mode == "append_section":
        if bounds is not None:
            raise ValueError(
                f"Section '{heading}' already exists. Use 'replace_section' or 'append_to_section'."
            )
        sep = "" if body.endswith("\n\n") else ("\n" if body.endswith("\n") else "\n\n")
        return f"{body}{sep}## {heading}\n\n{payload}\n"

    if bounds is None:
        raise ValueError(f"Section '{heading}' not found")

    heading_start, content_start, section_end = bounds

    if mode == "replace_section":
        before = body[:content_start]
        after = body[section_end:]
        new_content = f"{payload}\n\n" if payload else "\n"
        return f"{before}{new_content}{after}"

    if mode == "append_to_section":
        section_body = body[content_start:section_end].rstrip("\n")
        before = body[:content_start]
        after = body[section_end:]
        joiner = "\n\n" if section_body else ""
        new_section = f"{section_body}{joiner}{payload}\n\n"
        return f"{before}{new_section}{after}"

    raise ValueError(f"Unknown section mode: {mode}")


# --- Frontmatter operations ---

def _normalize_tags(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(t).strip() for t in value if str(t).strip()]
    s = str(value).strip()
    return [s] if s else []


def _apply_frontmatter_edit(
    path: Path,
    mode: str,
    payload: dict,
) -> tuple[str, str]:
    """Returns (new_full_text, change_summary)."""
    post = frontmatter.load(path)
    meta = dict(post.metadata or {})

    if mode == "update_frontmatter":
        key = str(payload.get("key", "")).strip()
        if not key:
            raise ValueError("update_frontmatter requires 'key'")
        if key == "tags":
            raise ValueError("Use add_tag / remove_tag for tags")
        old = meta.get(key, "<absent>")
        meta[key] = payload.get("value")
        summary = f"frontmatter[{key}]: {old!r} -> {meta[key]!r}"

    elif mode == "add_tag":
        tag = str(payload.get("tag", "")).strip()
        if not tag:
            raise ValueError("add_tag requires 'tag'")
        tags = _normalize_tags(meta.get("tags"))
        if tag in tags:
            summary = f"tag '{tag}' already present"
        else:
            tags.append(tag)
            meta["tags"] = tags
            summary = f"added tag '{tag}'"

    elif mode == "remove_tag":
        tag = str(payload.get("tag", "")).strip()
        if not tag:
            raise ValueError("remove_tag requires 'tag'")
        tags = _normalize_tags(meta.get("tags"))
        if tag not in tags:
            summary = f"tag '{tag}' was not present"
        else:
            tags = [t for t in tags if t != tag]
            meta["tags"] = tags
            summary = f"removed tag '{tag}'"

    else:
        raise ValueError(f"Unknown frontmatter mode: {mode}")

    new_post = frontmatter.Post(post.content, **meta)
    return frontmatter.dumps(new_post), summary


# --- Wiki-link rewriting ---

def _iter_vault_md_files() -> list[Path]:
    out: list[Path] = []
    for p in VAULT_PATH.rglob("*.md"):
        if not p.is_file():
            continue
        rel = p.relative_to(VAULT_PATH).as_posix()
        parts = rel.split("/")
        if parts and (parts[0] in EXCLUDED_DIRS or parts[0].startswith(".")):
            continue
        out.append(p)
    return out


@dataclass
class LinkRewriteReport:
    files_updated: int
    links_updated: int
    pathful_links_found: list[str]  # человекочитаемые "файл: [[Folder/X]]"


def _rewrite_wikilinks(old_stem: str, new_stem: str) -> LinkRewriteReport:
    """Заменить `[[old_stem]]` (с alias/heading) на `[[new_stem]]` во всём vault.

    Pathful ссылки `[[Folder/old_stem]]` НЕ переписываются автоматически —
    репортятся отдельно, чтобы пользователь поправил вручную.
    """
    files_updated = 0
    links_updated = 0
    pathful: list[str] = []

    for path in _iter_vault_md_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as exc:
            logger.warning("Skip %s during wiki-link rewrite: %s", path, exc)
            continue

        local_count = 0

        def _repl(m: re.Match) -> str:
            nonlocal local_count
            prefix, stem, heading, alias = m.group(1), m.group(2), m.group(3) or "", m.group(4) or ""
            if stem != old_stem:
                return m.group(0)
            if prefix:
                # Pathful link — не трогаем, репортим.
                rel = path.relative_to(VAULT_PATH).as_posix()
                pathful.append(f"{rel}: [[{prefix}{stem}{heading}{alias}]]")
                return m.group(0)
            local_count += 1
            return f"[[{new_stem}{heading}{alias}]]"

        new_text = _WIKI_LINK_RE.sub(_repl, text)
        if local_count > 0:
            _atomic_write(path, new_text)
            files_updated += 1
            links_updated += local_count

    return LinkRewriteReport(files_updated, links_updated, pathful)


# --- Public API ---

def edit_note(path: str, mode: str, payload: dict) -> str:
    """Точечное редактирование заметки. Полный rewrite запрещён намеренно."""
    vp = _resolve_in_vault(path)
    _ensure_writable(vp)

    section_modes = {"append_section", "replace_section", "append_to_section"}
    fm_modes = {"update_frontmatter", "add_tag", "remove_tag"}

    if mode in section_modes:
        heading = str(payload.get("heading", "")).strip()
        content = payload.get("content", "")
        if not heading:
            raise ValueError(f"{mode} requires 'heading'")
        if not isinstance(content, str):
            raise ValueError(f"{mode} requires string 'content'")

        post = frontmatter.load(vp.abs)
        new_body = _apply_section_edit(post.content or "", mode, heading, content)
        new_post = frontmatter.Post(new_body, **(post.metadata or {}))
        _atomic_write(vp.abs, frontmatter.dumps(new_post))
        return f"OK: {vp.rel} — {mode} on '{heading}'"

    if mode in fm_modes:
        new_text, summary = _apply_frontmatter_edit(vp.abs, mode, payload)
        _atomic_write(vp.abs, new_text)
        return f"OK: {vp.rel} — {summary}"

    raise ValueError(
        f"Unknown mode: {mode}. "
        f"Allowed: {sorted(section_modes | fm_modes)}"
    )


def move_note(source: str, destination: str) -> str:
    """Переместить/переименовать заметку и обновить wiki-links по stem.

    Если изменился ТОЛЬКО stem — переписываются все bare-ссылки `[[old]]`.
    Если изменилась ТОЛЬКО папка — ссылки не трогаются (Obsidian резолвит по имени).
    Pathful ссылки `[[Folder/old]]` репортятся как требующие ручной правки.
    """
    src_vp = _resolve_in_vault(source)
    _ensure_writable(src_vp)

    dst_vp = _resolve_in_vault(destination, must_exist=False)
    _ensure_writable(dst_vp)

    if dst_vp.abs.exists():
        raise FileExistsError(f"Destination already exists: {dst_vp.rel}")

    if src_vp.abs == dst_vp.abs:
        return f"NOOP: source equals destination ({src_vp.rel})"

    old_stem = src_vp.abs.stem
    new_stem = dst_vp.abs.stem
    needs_link_rewrite = old_stem != new_stem

    dst_vp.abs.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src_vp.abs), str(dst_vp.abs))

    lines = [f"Moved: {src_vp.rel} -> {dst_vp.rel}"]

    if needs_link_rewrite:
        report = _rewrite_wikilinks(old_stem, new_stem)
        lines.append(
            f"Wiki-links: rewrote {report.links_updated} link(s) in {report.files_updated} file(s)"
        )
        if report.pathful_links_found:
            lines.append(
                f"⚠️ Pathful links found ({len(report.pathful_links_found)}) — need manual fix:"
            )
            for item in report.pathful_links_found[:20]:
                lines.append(f"  - {item}")
            if len(report.pathful_links_found) > 20:
                lines.append(f"  ... and {len(report.pathful_links_found) - 20} more")
    else:
        lines.append("Wiki-links: stem unchanged, nothing to rewrite")

    return "\n".join(lines)


def delete_note(path: str, reason: str = "") -> str:
    """Soft-delete: move to Archive/<original-folder>/ with archived_at."""
    vp = _resolve_in_vault(path)
    _ensure_writable(vp)

    rel_parts = vp.rel.split("/")
    if rel_parts and rel_parts[0] == ARCHIVE_DIR_NAME:
        raise PermissionError(f"Already in archive: {vp.rel}")

    archive_root = VAULT_PATH / ARCHIVE_DIR_NAME
    dst = archive_root / vp.rel
    if dst.exists():
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        dst = dst.parent / f"{dst.stem}-{ts}{dst.suffix}"

    # Обновляем frontmatter перед перемещением — индексатор увидит уже архивную версию.
    post = frontmatter.load(vp.abs)
    meta = dict(post.metadata or {})
    meta["archived_at"] = datetime.now(timezone.utc).isoformat()
    if reason:
        meta["archived_reason"] = reason
    new_post = frontmatter.Post(post.content, **meta)
    _atomic_write(vp.abs, frontmatter.dumps(new_post))

    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(vp.abs), str(dst))

    archived_rel = dst.relative_to(VAULT_PATH).as_posix()
    return (
        f"Archived: {vp.rel} -> {archived_rel}\n"
        f"Wiki-links to '{vp.abs.stem}' were NOT rewritten (intentional — see archive)."
    )
