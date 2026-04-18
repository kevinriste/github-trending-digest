#!/usr/bin/env python3
"""One-time migration: retrofit existing daily HTML pages for manifest-based nav.

Legacy pages had an inline <nav class="gtd-daynav"> baked into the HTML with prev/next
dates hardcoded at generation time. The new system has preference.js fetch docs/dates.json
at runtime and build the nav from the page's <body data-gtd-edition data-gtd-date> attrs.

This script:
  1. Strips any legacy <nav class="gtd-daynav">...</nav> block (idempotent).
  2. Adds data-gtd-edition + data-gtd-date attributes to <body>, inferred from the
     edition (gh/hn) and the parent directory name (YYYY-MM-DD).

Run once after the manifest-based refactor lands. New pages ship with the attrs already set.
"""
from __future__ import annotations

import re
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
EXISTING_NAV_RE = re.compile(
    r'\s*<nav class="gtd-daynav"[^>]*>.*?</nav>\s*', re.DOTALL | re.IGNORECASE
)
BODY_TAG_RE = re.compile(r"<body\b([^>]*)>", re.IGNORECASE)


def set_body_attrs(attrs: str, edition: str, date: str) -> str:
    """Upsert data-gtd-edition and data-gtd-date onto a <body> tag's attribute string."""
    attrs = re.sub(r'\s*data-gtd-edition="[^"]*"', "", attrs, flags=re.IGNORECASE)
    attrs = re.sub(r'\s*data-gtd-date="[^"]*"', "", attrs, flags=re.IGNORECASE)
    attrs = attrs.rstrip()
    return f'{attrs} data-gtd-edition="{edition}" data-gtd-date="{date}"'


def patch_file(html_path: Path, edition: str, date: str) -> bool:
    text = html_path.read_text(encoding="utf-8")
    new_text = EXISTING_NAV_RE.sub("", text, count=1)

    def body_sub(match: re.Match[str]) -> str:
        return f"<body{set_body_attrs(match.group(1), edition, date)}>"

    new_text, n = BODY_TAG_RE.subn(body_sub, new_text, count=1)
    if n == 0:
        return False
    if new_text == text:
        return False
    html_path.write_text(new_text, encoding="utf-8")
    return True


def patch_edition(root: Path, edition: str) -> tuple[int, int]:
    if not root.is_dir():
        return 0, 0
    patched = 0
    missing = 0
    for date_dir in sorted(root.iterdir()):
        if not date_dir.is_dir() or not DATE_RE.match(date_dir.name):
            continue
        for fname in ("index.html", "classic.html"):
            html_path = date_dir / fname
            if not html_path.exists():
                missing += 1
                continue
            if patch_file(html_path, edition, date_dir.name):
                patched += 1
    return patched, missing


def main() -> int:
    gh_patched, gh_missing = patch_edition(DOCS, "gh")
    hn_patched, hn_missing = patch_edition(DOCS / "hn", "hn")
    print(f"GH: patched {gh_patched} files ({gh_missing} missing)")
    print(f"HN: patched {hn_patched} files ({hn_missing} missing)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
