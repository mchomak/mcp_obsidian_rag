"""Validates note metadata against per-folder rules from vault CLAUDE.md.

Rules are derived from the "Правила: папка → frontmatter + обязательные теги" table.
Update _RULES here when vault CLAUDE.md changes.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Canonical tag categories and their allowed values (from vault CLAUDE.md)
TAG_CATEGORIES: dict[str, frozenset[str]] = {
    "Стадия": frozenset({"raw", "researching", "validated", "building", "archived"}),
    "Тип продукта": frozenset({
        "saas", "marketplace", "b2c-app", "b2b-tool",
        "hardware", "service", "community", "media",
    }),
    "Домен": frozenset({
        "ai-ml", "fintech", "edtech", "healthtech", "legaltech",
        "hr-tech", "e-commerce", "productivity", "dev-tools", "web3", "climate",
    }),
    "Рынок": frozenset({"b2b", "b2c", "b2g", "smb", "enterprise"}),
    "Моя роль": frozenset({"founder-fit", "side-project", "invest-idea", "need-cofounder"}),
    "Тип заметки": frozenset({
        "idea", "note", "reference", "decision", "error",
        "howto", "learning", "project", "contact", "daily", "inbox",
    }),
    "Тематика": frozenset({
        "ml", "ai", "education", "dev-tools", "prompt-engineering",
        "obsidian", "social", "youtube", "gamedev", "dataset",
        "object-detection", "startup", "web",
    }),
}

# Folder rules: (folder_pattern, required_type_or_None, required_category_names)
#
# Patterns use "/" as separator; "*" matches exactly one path segment.
# Matched against the PARENT FOLDER of the note (vault-relative, POSIX, no trailing slash).
# More specific patterns (more segments) take natural priority via length matching.
_RULES: list[tuple[str, str | None, list[str]]] = [
    # Ideas root
    ("Ideas",                           "idea",     ["Стадия", "Тип продукта", "Домен"]),
    # Projects/Work subfolders
    ("Projects/Work/*/decisions",       "decision", ["Тематика"]),
    ("Projects/Work/*/notes",           "note",     ["Тематика"]),
    ("Projects/Work/*/ideas",           "idea",     ["Тематика"]),
    ("Projects/Work/*",                 "project",  ["Тематика"]),
    # Projects/Personal subfolders (same rules as Work, but type at root is flexible)
    ("Projects/Personal/*/decisions",   "decision", ["Тематика"]),
    ("Projects/Personal/*/notes",       "note",     ["Тематика"]),
    ("Projects/Personal/*/ideas",       "idea",     ["Тематика"]),
    ("Projects/Personal/*",             None,       []),   # no mandatory type at project root
    # Notes
    ("Notes/Learning",                  "learning", ["Тематика"]),
    ("Notes/Tools",                     "reference",["Тематика"]),
    ("Notes/Howto",                     "howto",    ["Тематика"]),
    ("Notes/Misc",                      None,       []),   # free folder
    # Resources
    ("Resources/Articles",              "reference",["Тематика"]),
    ("Resources/Courses",               "reference",["Тематика"]),
    ("Resources/Videos",                "reference",["Тематика"]),
    ("Resources/Bookmarks",             None,       []),
    # Contacts
    ("Contacts/*",                      "contact",  []),
    # Misc
    ("Daily",                           "daily",    []),
    ("Inbox/*",                         "inbox",    []),
]


def _match_rule(folder: str) -> tuple[str | None, list[str]] | None:
    """Return (required_type, required_categories) for the first matching rule, or None."""
    folder_parts = folder.split("/") if folder else []
    for pattern, req_type, req_cats in _RULES:
        pattern_parts = pattern.split("/")
        if len(folder_parts) != len(pattern_parts):
            continue
        if all(p == f or p == "*" for p, f in zip(pattern_parts, folder_parts)):
            return req_type, req_cats
    return None


def validate_for_folder(
    rel_path: str,
    note_type: str,
    tags: list[str],
) -> str | None:
    """Check note_type and tags against folder rules.

    `rel_path` — vault-relative POSIX path, e.g. "Projects/Personal/X/note.md"
    Returns an error message string if validation fails, None if OK or no rule applies.
    """
    parts = rel_path.split("/")
    folder = "/".join(parts[:-1]) if len(parts) > 1 else ""

    match = _match_rule(folder)
    if match is None:
        logger.debug("No folder rule for '%s', skipping validation", folder)
        return None

    required_type, required_categories = match
    errors: list[str] = []

    if required_type and note_type != required_type:
        errors.append(
            f"folder '{folder}' requires type='{required_type}', got '{note_type}'"
        )

    for cat_name in required_categories:
        cat_tags = TAG_CATEGORIES.get(cat_name, frozenset())
        if not any(t in cat_tags for t in tags):
            choices = ", ".join(sorted(cat_tags))
            errors.append(
                f"missing tag from category '{cat_name}' — add one of: {choices}"
            )

    if errors:
        return "Validation error:\n" + "\n".join(f"  • {e}" for e in errors)
    return None
