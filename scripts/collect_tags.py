"""Разовый скрипт: собирает все теги из vault.

Источники:
  1. YAML frontmatter, поле `tags:` (список или строка).
  2. Inline `#tag` в теле заметки.

Печатает: тег -> сколько файлов используют + сколько inline-вхождений.
"""

import re
import sys
from collections import Counter
from pathlib import Path

import frontmatter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import VAULT_PATH  # noqa: E402

INLINE_TAG_RE = re.compile(r"(?<![\w/])#([A-Za-zА-Яа-я0-9_\-/]+)")

EXCLUDE_DIRS = {"_Secrets", "templates", ".obsidian", ".trash"}


def _is_excluded(rel_parts: tuple[str, ...]) -> bool:
    return bool(rel_parts) and rel_parts[0] in EXCLUDE_DIRS


def _normalize_fm_tags(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    s = str(value).strip()
    return [s] if s else []


def main() -> int:
    if not VAULT_PATH.exists():
        print(f"Vault not found: {VAULT_PATH}", file=sys.stderr)
        return 1

    fm_files = Counter()
    fm_total = Counter()
    inline_files = Counter()
    inline_total = Counter()
    files_seen = 0
    files_with_fm = 0

    for path in VAULT_PATH.rglob("*.md"):
        if not path.is_file():
            continue
        rel = path.relative_to(VAULT_PATH)
        if _is_excluded(rel.parts):
            continue

        files_seen += 1
        try:
            post = frontmatter.load(path)
        except Exception as exc:
            print(f"[warn] frontmatter parse fail: {rel} ({exc})", file=sys.stderr)
            continue

        meta = dict(post.metadata or {})
        fm_tags = _normalize_fm_tags(meta.get("tags"))
        if fm_tags:
            files_with_fm += 1
        for t in set(fm_tags):
            fm_files[t] += 1
        for t in fm_tags:
            fm_total[t] += 1

        body = post.content or ""
        inline = INLINE_TAG_RE.findall(body)
        for t in set(inline):
            inline_files[t] += 1
        for t in inline:
            inline_total[t] += 1

    all_tags = set(fm_files) | set(inline_files)

    print(f"Vault: {VAULT_PATH}")
    print(f"Файлов всего:                 {files_seen}")
    print(f"С тегами в frontmatter:       {files_with_fm}")
    print(f"Уникальных тегов (FM+inline): {len(all_tags)}")
    print()

    print("=" * 78)
    print(f"{'TAG':<40} {'FM files':>10} {'FM total':>10} {'IN files':>10} {'IN total':>10}")
    print("=" * 78)
    rows = sorted(
        all_tags,
        key=lambda t: -(fm_files[t] + inline_files[t]),
    )
    for t in rows:
        print(
            f"{t:<40} {fm_files[t]:>10} {fm_total[t]:>10} "
            f"{inline_files[t]:>10} {inline_total[t]:>10}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
