"""Audit tag usage across the vault vs the approved list in {VAULT}/CLAUDE.md.

Read-only. Never modifies files. Run:

    python tags_audit.py
    python tags_audit.py --show-files

Reports three sections:
  - Tags used in notes but NOT in approved list (with fuzzy suggestions)
  - Tags in approved list but NEVER used
  - Top tags by usage
"""
import argparse
import difflib
import logging
import sys
from collections import Counter
from pathlib import Path

import frontmatter

from config import VAULT_PATH
from conventions import get_approved_tags, parse_conventions
from indexer import iter_vault_md_files

logger = logging.getLogger(__name__)


def _collect_tags() -> tuple[Counter, dict[str, list[str]]]:
    counter: Counter[str] = Counter()
    by_tag: dict[str, list[str]] = {}

    for path in iter_vault_md_files():
        try:
            post = frontmatter.load(path)
        except Exception as exc:
            logger.warning("Skip %s: %s", path, exc)
            continue
        raw_tags = post.metadata.get("tags") or []
        if not isinstance(raw_tags, list):
            raw_tags = [str(raw_tags)]
        rel = path.relative_to(VAULT_PATH).as_posix()
        for t in raw_tags:
            tag = str(t).strip()
            if not tag:
                continue
            counter[tag] += 1
            by_tag.setdefault(tag, []).append(rel)

    return counter, by_tag


def _print_header(title: str) -> None:
    print(f"\n== {title} ==")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit vault tag usage")
    parser.add_argument(
        "--show-files",
        action="store_true",
        help="Print up to 5 file paths for each unknown tag",
    )
    args = parser.parse_args()

    conventions = parse_conventions()
    approved = get_approved_tags()
    counter, by_tag = _collect_tags()
    used_tags = set(counter.keys())

    if not approved:
        print("WARNING: no approved tags parsed from vault/CLAUDE.md\n")
    else:
        print(f"Approved tags: {len(approved)} across {len(conventions)} categories")
    print(f"Tags used in notes: {len(used_tags)} ({sum(counter.values())} total usages)")

    unknown = sorted(used_tags - approved)
    if unknown:
        _print_header("Tags used in notes but NOT in approved list")
        for tag in unknown:
            count = counter[tag]
            suggestions = (
                difflib.get_close_matches(tag, approved, n=2, cutoff=0.7)
                if approved else []
            )
            hint = f" (similar to: {', '.join(suggestions)})" if suggestions else ""
            print(f"  {tag:<22} {count:>3} file(s){hint}")
            if args.show_files:
                for f in by_tag[tag][:5]:
                    print(f"      - {f}")

    unused = sorted(approved - used_tags) if approved else []
    if unused:
        _print_header("Tags in approved list but NEVER used")
        for tag in unused:
            print(f"  {tag}")

    if counter:
        _print_header("Top tags by usage")
        for tag, count in counter.most_common(20):
            marker = " " if tag in approved else "?"
            print(f"  [{marker}] {tag:<22} {count:>4}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
