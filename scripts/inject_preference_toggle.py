#!/usr/bin/env python3
"""Inject the preference.js <script> tag into every existing HTML page under docs/.

Idempotent: skips pages that already reference preference.js.
Relative path depth:
  docs/index.html                     -> preference.js
  docs/hn/index.html                  -> ../preference.js
  docs/YYYY-MM-DD/{index,classic}.html -> ../preference.js
  docs/hn/YYYY-MM-DD/{index,classic}.html -> ../../preference.js
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"
BODY_CLOSE = re.compile(r"</body>", re.IGNORECASE)


def rel_path_for(html_path: Path) -> str:
    depth = len(html_path.relative_to(DOCS).parts) - 1
    return ("../" * depth) + "preference.js"


def patch(html_path: Path) -> str:
    text = html_path.read_text(encoding="utf-8")
    if "preference.js" in text:
        return "skip"
    rel = rel_path_for(html_path)
    tag = f'<script src="{rel}" defer></script>\n'
    new_text, n = BODY_CLOSE.subn(tag + "</body>", text, count=1)
    if n == 0:
        return "no-body"
    html_path.write_text(new_text, encoding="utf-8")
    return "patched"


def main() -> int:
    if not DOCS.is_dir():
        print(f"docs dir not found: {DOCS}", file=sys.stderr)
        return 1
    counts = {"patched": 0, "skip": 0, "no-body": 0}
    for html in sorted(DOCS.rglob("*.html")):
        status = patch(html)
        counts[status] += 1
        if status != "skip":
            print(f"{status}: {html.relative_to(DOCS)}")
    print(f"\nTotals: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
