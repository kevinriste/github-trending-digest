#!/usr/bin/env python3
"""CLI wrapper: rebuild docs/dates.json from the editions registry.

All logic lives in editions.write_dates_manifest; run this after manually
adding/removing daily page directories.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from editions import DOCS_DIR, write_dates_manifest


def main() -> int:
    write_dates_manifest()
    manifest = json.loads((DOCS_DIR / "dates.json").read_text(encoding="utf-8"))
    print("Wrote docs/dates.json: " + ", ".join(f"{k}={len(v)}" for k, v in manifest.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
