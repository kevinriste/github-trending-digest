"""Single source of edition identity: labels, paths, read keys, date inventories.

All cross-edition navigation (classic navs, magazine mastheads, calendar indexes)
and the docs/dates.json day-nav manifest derive from this registry, so a new or
non-daily edition cannot silently inherit another edition's labels or paths.

Must stay stdlib-only: trending_digest, morning_edition, and ai_edition all
import it at module level despite their own one-way import constraints.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

DOCS_DIR = Path(__file__).parent / "docs"
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class Edition:
    id: str
    name: str            # cross-link label, e.g. "GitHub Trending"
    calendar_label: str  # e.g. "GitHub Calendar"
    root_path: str       # path under docs root: "", "hn/", "ai/"
    read_key: str        # localStorage read-day key; value must never change

    @property
    def output_dir(self) -> Path:
        import editions  # late lookup so tests can monkeypatch editions.DOCS_DIR
        return editions.DOCS_DIR / self.root_path if self.root_path else editions.DOCS_DIR


EDITIONS = {
    e.id: e
    for e in (
        Edition("gh", "GitHub Trending", "GitHub Calendar", "", "gtd:read_days:gh:v1"),
        Edition("hn", "Hacker News", "Hacker News Calendar", "hn/", "gtd:read_days:hn:v1"),
        Edition("ai", "AI News", "AI News Calendar", "ai/", "gtd:read_days:ai:v1"),
    )
}


def published_dates(edition_id):
    """ISO dates that have a published page directory for this edition."""
    root = EDITIONS[edition_id].output_dir
    if not root.is_dir():
        return set()
    return {d.name for d in root.iterdir() if d.is_dir() and _DATE_RE.match(d.name)}


def _dates_for(edition_id, known_dates):
    dates = (known_dates or {}).get(edition_id)
    return published_dates(edition_id) if dates is None else dates


def cross_edition_links(current_id, day_str=None, known_dates=None):
    """(href, label) pairs for every OTHER edition, from a page of `current_id`.

    From a dated page (day_str given): link the same-date page of each other
    edition that published that day; unpublished editions are omitted — no
    calendar fallback. From a calendar page (day_str None): always link the
    other editions' calendars.

    known_dates ({edition_id: set of ISO dates}) overrides the filesystem scan
    per edition — callers mid-run pass DB-known dates because the day being
    generated may not be on disk yet. Unlisted editions fall back to the scan.
    """
    current = EDITIONS[current_id]
    depth = current.root_path.count("/") + (1 if day_str else 0)
    to_root = "../" * depth
    links = []
    for target in EDITIONS.values():
        if target.id == current_id:
            continue
        if day_str is None:
            links.append((to_root + target.root_path, target.calendar_label))
        elif day_str in _dates_for(target.id, known_dates):
            links.append((f"{to_root}{target.root_path}{day_str}/", target.name))
    return links


def write_dates_manifest(known_dates=None):
    """Write docs/dates.json (edition id -> sorted ISO dates) for preference.js day-nav."""
    import editions
    manifest = {eid: sorted(_dates_for(eid, known_dates)) for eid in EDITIONS}
    (editions.DOCS_DIR / "dates.json").write_text(
        json.dumps(manifest, separators=(",", ":")), encoding="utf-8"
    )
