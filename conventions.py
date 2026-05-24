"""Parser for vault conventions (approved tags) from {VAULT}/CLAUDE.md.

Two supported source formats:

1. Explicit markers (preferred — unambiguous):

       <!-- TAGS_START -->
       **Стадия:**
       - `raw` — сырая идея
       - `validated` — подтверждена
       <!-- TAGS_END -->

2. Fallback: heading "Утверждённые теги..." until next h1/h2/h3 or `---`.
   Inside the section tags are recognized in two shapes:
       a) bullet:   - `tag` — description
       b) inline:   `tag1`, `tag2`, `tag3`
"""

import logging
import re
from pathlib import Path

from config import VAULT_PATH

logger = logging.getLogger(__name__)

VAULT_CLAUDE_MD = VAULT_PATH / "CLAUDE.md"

_MARKER_START = re.compile(r"<!--\s*TAGS_START\s*-->", re.IGNORECASE)
_MARKER_END = re.compile(r"<!--\s*TAGS_END\s*-->", re.IGNORECASE)

_SECTION_HEADING_RE = re.compile(
    r"^#{1,6}\s+(?:Утвержд[её]нные\s+теги|Approved\s+tags).*$",
    re.MULTILINE | re.IGNORECASE,
)
_SECTION_END_RE = re.compile(r"^---\s*$|^#{1,3}\s+", re.MULTILINE)

_CATEGORY_SPLIT_RE = re.compile(r"^\s*\*\*(.+?)\*\*\s*:?\s*$", re.MULTILINE)
_BULLET_TAG_RE = re.compile(
    r"^\s*[-*]\s*`([^`]+)`\s*(?:[—–-]\s*(.+?))?\s*$",
    re.MULTILINE,
)
_INLINE_TAG_RE = re.compile(r"`([^`]+)`")


def _read_vault_claude_md() -> str | None:
    if not VAULT_CLAUDE_MD.is_file():
        logger.warning(
            "Vault CLAUDE.md not found at %s. Conventions will be empty.",
            VAULT_CLAUDE_MD,
        )
        return None
    try:
        return VAULT_CLAUDE_MD.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("Cannot read vault CLAUDE.md: %s", exc)
        return None


def _extract_tags_section(text: str) -> str | None:
    m_start = _MARKER_START.search(text)
    m_end = _MARKER_END.search(text)
    if m_start and m_end and m_end.start() > m_start.end():
        return text[m_start.end():m_end.start()]

    heading_match = _SECTION_HEADING_RE.search(text)
    if not heading_match:
        return None
    start = heading_match.end()
    rest = text[start:]
    end_match = _SECTION_END_RE.search(rest)
    end = start + end_match.start() if end_match else len(text)
    return text[start:end]


def parse_conventions(path: Path = VAULT_CLAUDE_MD) -> dict[str, dict[str, str]]:
    """Return {category: {tag: description}}. Description may be empty."""
    text = _read_vault_claude_md()
    if not text:
        return {}

    section = _extract_tags_section(text)
    if not section:
        logger.warning(
            "Approved tags section not found. Add <!-- TAGS_START -->/<!-- TAGS_END --> "
            "or a heading like '#### Утверждённые теги по категориям' in vault CLAUDE.md."
        )
        return {}

    parts = _CATEGORY_SPLIT_RE.split(section)
    if len(parts) < 3:
        logger.warning("No **Category:** blocks found in tags section.")
        return {}

    result: dict[str, dict[str, str]] = {}
    for i in range(1, len(parts), 2):
        category = parts[i].strip().rstrip(":").strip()
        block = parts[i + 1] if i + 1 < len(parts) else ""
        tags: dict[str, str] = {}

        for m in _BULLET_TAG_RE.finditer(block):
            tag = m.group(1).strip()
            desc = (m.group(2) or "").strip()
            if tag:
                tags[tag] = desc

        if not tags:
            for m in _INLINE_TAG_RE.finditer(block):
                tag = m.group(1).strip()
                if tag:
                    tags.setdefault(tag, "")

        if tags and category:
            result[category] = tags

    if not result:
        logger.warning("Tag section found but no tags parsed. Check format.")
    return result


def get_approved_tags() -> set[str]:
    """Flat set of all approved tags across categories."""
    flat: set[str] = set()
    for tags in parse_conventions().values():
        flat.update(tags.keys())
    return flat


def format_conventions_for_claude() -> str:
    """Markdown text returned by the get_vault_conventions tool."""
    conventions = parse_conventions()
    lines: list[str] = ["# Vault conventions", "", "## Approved tags", ""]

    if not conventions:
        lines.append(
            "_No approved tags parsed from vault CLAUDE.md. "
            "Validation will accept any tag, but consider adding an approved list._"
        )
    else:
        for category, tags in conventions.items():
            lines.append(f"### {category}")
            for tag, desc in tags.items():
                lines.append(f"- `{tag}` — {desc}" if desc else f"- `{tag}`")
            lines.append("")

    lines.extend([
        "## Rules for create_note",
        "",
        "- Pick tags ONLY from the approved list above. Folder rules enforce required tag "
        "categories — violating them returns an error (not a warning). Check the folder "
        "rules table in vault CLAUDE.md before choosing tags.",
        "- In the note `content` embed `[[Wiki-links]]` to related notes (Obsidian format, "
        "filename without `.md`). Use `search_knowledge_base` to find candidates first.",
        "- Do NOT use markdown links like `[text](path.md)` for vault notes — Obsidian "
        "will not pick them up in its graph.",
        "- After creation, `create_note` returns RELATED NOTE CANDIDATES — semantically "
        "similar notes you may add as `[[wiki-links]]`. They are suggestions only; the "
        "server does NOT insert them automatically. Add them via `edit_note(find_replace)`.",
        "- `tags` argument is a list of strings; `note_type` is a single string "
        "(e.g. `error`, `decision`, `research`, `note`).",
        "- To use a template: call `get_template(name)` to inspect its structure, then "
        "pass `template=name` to `create_note` (omit `content`). Fill `{{placeholders}}` "
        "afterwards with `edit_note(mode=\"find_replace\")`.",
    ])
    return "\n".join(lines)
