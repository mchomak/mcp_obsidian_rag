"""Access to Obsidian vault templates.

Templates live in {VAULT}/templates/template_<name>.md and contain:
  - YAML frontmatter with type/tag defaults
  - Body with section headings and {{placeholder}} text

`get_template(name)` returns raw content (so Claude can inspect the structure).
`load_template_body(name)` returns the body with Templater <% ... %> expressions
  replaced by real values, while {{...}} placeholders are preserved intact.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from config import VAULT_PATH

TEMPLATES_DIR = VAULT_PATH / "templates"

# Matches Obsidian Templater expressions: <% tp.date.now("...") %>, <%tp.file.title%>, etc.
_TEMPLATER_RE = re.compile(r"<%[- ]?(.+?)[- ]?%>", re.DOTALL)

# Strips YAML frontmatter (--- ... ---) without parsing it (templates may have invalid YAML).
_FM_RE = re.compile(r"^---[ \t]*\r?\n.*?\r?\n---[ \t]*\r?\n?", re.DOTALL)


def list_templates() -> list[str]:
    """Return sorted list of available template names (without prefix/suffix)."""
    if not TEMPLATES_DIR.is_dir():
        return []
    return sorted(
        p.stem[len("template_"):]
        for p in TEMPLATES_DIR.glob("template_*.md")
        if p.is_file()
    )


def get_template(name: str) -> str:
    """Return raw template file content (frontmatter + body with placeholders visible)."""
    path = TEMPLATES_DIR / f"template_{name}.md"
    if not path.is_file():
        available = list_templates()
        raise FileNotFoundError(
            f"Template '{name}' not found. "
            f"Available: {', '.join(available) or 'none'}"
        )
    return path.read_text(encoding="utf-8")


def load_template_body(name: str, title: str = "", date_str: str = "") -> str:
    """Load template body with Templater expressions replaced by real values.

    Strips YAML frontmatter without parsing it (templates may contain {{...}} or
    <% ... %> that break YAML parsers). Replaces:
      <% tp.date.now(...) %>  →  date_str (or today's date)
      <% tp.file.title %>     →  title
      other <% ... %>         →  removed

    Leaves {{...}} placeholders intact for Claude to fill via edit_note(find_replace).
    """
    text = get_template(name)

    # Strip frontmatter via regex — avoids YAML parse errors on {{placeholder}} keys
    m = _FM_RE.match(text)
    body = text[m.end():] if m else text

    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _replace(m: re.Match) -> str:
        expr = m.group(1).strip()
        if "tp.date" in expr or "tp.file.creation_date" in expr:
            return date_str
        if "tp.file.title" in expr:
            return title
        return ""  # unknown Templater expression — remove silently

    return _TEMPLATER_RE.sub(_replace, body)
