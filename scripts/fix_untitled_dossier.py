#!/usr/bin/env python3
"""Patch existing GH morning-edition HTML: replace '<h3>Untitled</h3>' in
dossier entries with the repo name extracted from the adjacent
'dossier-source' github.com link.

Idempotent: pages without 'Untitled' are skipped.
"""
from __future__ import annotations

import re
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"

PATTERN = re.compile(
    r"<h3>Untitled</h3>\s*"
    r'<p class="dossier-source">\s*'
    r'<a href="https://github\.com/([^"/]+/[^"]+)"',
    re.IGNORECASE,
)


def patch(html_path: Path) -> int:
    text = html_path.read_text(encoding="utf-8")
    if "<h3>Untitled</h3>" not in text:
        return 0

    def repl(match: re.Match[str]) -> str:
        repo = match.group(1)
        return match.group(0).replace("<h3>Untitled</h3>", f"<h3>{repo}</h3>", 1)

    new_text, n = PATTERN.subn(repl, text)
    if n == 0:
        return 0
    html_path.write_text(new_text, encoding="utf-8")
    return n


def main() -> int:
    total_files = 0
    total_fixed = 0
    residual = 0
    for html in sorted(DOCS.rglob("*.html")):
        n = patch(html)
        if n:
            total_files += 1
            total_fixed += n
            remaining = html.read_text(encoding="utf-8").count("<h3>Untitled</h3>")
            residual += remaining
            suffix = f" ({remaining} still Untitled)" if remaining else ""
            print(f"patched {n}: {html.relative_to(DOCS)}{suffix}")
    print(f"\nFiles patched: {total_files}, dossier titles fixed: {total_fixed}, residual Untitled: {residual}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
