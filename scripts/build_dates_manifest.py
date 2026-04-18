#!/usr/bin/env python3
"""Scan docs/ and write docs/dates.json with sorted GH and HN daily dates.

Run after adding new daily pages. Generators also call this at end-of-run.
preference.js fetches this file to build the prev/next day navigation.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def dates_in(folder: Path) -> list[str]:
    if not folder.is_dir():
        return []
    return sorted(d.name for d in folder.iterdir() if d.is_dir() and DATE_RE.match(d.name))


def build(out_path: Path | None = None) -> Path:
    manifest = {
        "gh": dates_in(DOCS),
        "hn": dates_in(DOCS / "hn"),
    }
    out_path = out_path or (DOCS / "dates.json")
    out_path.write_text(json.dumps(manifest, separators=(",", ":")), encoding="utf-8")
    return out_path


def main() -> int:
    out = build()
    manifest = json.loads(out.read_text(encoding="utf-8"))
    print(f"Wrote {out} (gh={len(manifest['gh'])}, hn={len(manifest['hn'])})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
