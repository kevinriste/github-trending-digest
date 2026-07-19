# Cross-Edition Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Centralize all cross-edition navigation (GH / HN / AI) in one registry so links, labels, and the day-nav manifest can never silently diverge, and fix the AI edition's broken/missing nav.

**Architecture:** New stdlib-only `editions.py` module (importable by every other module without cycles) holds the edition registry and computes all cross-edition hrefs/labels with a single rule: from a dated page, link another edition's same-date page only if it is published, otherwise omit (no calendar fallback); from a calendar page, always link the other calendars. `trending_digest`, `morning_edition`, and `ai_edition` consume it; `docs/dates.json` gets a single writer derived from the registry; the daily run picks up a new ai-newsletter sidecar before generating GH/HN pages.

**Tech Stack:** Python 3.12, uv, pytest (new dev dep), vanilla JS (`docs/preference.js`).

**Spec:** `specs/2026-07-18-cross-edition-nav-design.md`

## Global Constraints

- localStorage read-key **values** must not change: `gtd:read_days:gh:v1`, `gtd:read_days:hn:v1`, `gtd:read_days:ai:v1`.
- `morning_edition.py` must NOT import `trending_digest` (or `ai_edition`) at module level; `editions.py` must import no project modules.
- Cross-link labels: "GitHub Trending" / "Hacker News" / "AI News". Calendar labels: "GitHub Calendar" / "Hacker News Calendar" / "AI News Calendar".
- Omit-if-unpublished rule applies to dated pages AND the links email. Calendar pages always cross-link calendars.
- Do not regenerate historical GH/HN daily pages as part of this work (verification may render them locally but must `git restore docs/` afterward, except the AI-day re-render in Task 8 which IS kept).
- `docs/` is the published GitHub Pages site — no internal files go there.
- Repo conventions: no type annotations unless clarifying; commit messages end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: `editions.py` registry + tests

**Files:**
- Create: `editions.py`
- Create: `tests/test_editions.py`
- Modify: `pyproject.toml` (add pytest to dev group via `uv add --group dev pytest`)

**Interfaces:**
- Produces (later tasks rely on these exact names):
  - `EDITIONS: dict[str, Edition]` — ordered gh, hn, ai; `Edition` has `id`, `name`, `calendar_label`, `root_path`, `read_key`, and property `output_dir`.
  - `published_dates(edition_id) -> set[str]`
  - `cross_edition_links(current_id, day_str=None, known_dates=None) -> list[tuple[str, str]]` — `(href, label)` for each OTHER edition.
  - `write_dates_manifest(known_dates=None) -> None` — writes `docs/dates.json`.
  - `DOCS_DIR: Path`

- [ ] **Step 1: Add pytest dev dependency**

Run: `uv add --group dev pytest`
Expected: `pyproject.toml` dev group gains `pytest`; `uv.lock` updated.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_editions.py`:

```python
from datetime import date

from editions import EDITIONS, cross_edition_links, published_dates, write_dates_manifest


def test_registry_labels_and_paths():
    assert list(EDITIONS) == ["gh", "hn", "ai"]
    assert EDITIONS["ai"].name == "AI News"
    assert EDITIONS["ai"].calendar_label == "AI News Calendar"
    assert EDITIONS["gh"].root_path == ""
    assert EDITIONS["hn"].root_path == "hn/"
    assert EDITIONS["ai"].root_path == "ai/"
    # localStorage keys must never change (users' read-day history)
    assert EDITIONS["gh"].read_key == "gtd:read_days:gh:v1"
    assert EDITIONS["hn"].read_key == "gtd:read_days:hn:v1"
    assert EDITIONS["ai"].read_key == "gtd:read_days:ai:v1"


def test_calendar_pages_always_link_other_calendars():
    assert cross_edition_links("gh") == [
        ("hn/", "Hacker News Calendar"),
        ("ai/", "AI News Calendar"),
    ]
    assert cross_edition_links("hn") == [
        ("../", "GitHub Calendar"),
        ("../ai/", "AI News Calendar"),
    ]
    assert cross_edition_links("ai") == [
        ("../", "GitHub Calendar"),
        ("../hn/", "Hacker News Calendar"),
    ]


def test_dated_page_omits_unpublished_editions():
    known = {"gh": {"2026-07-18"}, "hn": {"2026-07-18"}, "ai": set()}
    assert cross_edition_links("gh", "2026-07-18", known) == [
        ("../hn/2026-07-18/", "Hacker News"),
    ]
    assert cross_edition_links("hn", "2026-07-18", known) == [
        ("../../2026-07-18/", "GitHub Trending"),
    ]
    # nothing published that day -> no links at all, never a calendar fallback
    empty = {"gh": set(), "hn": set(), "ai": set()}
    assert cross_edition_links("gh", "2026-07-18", empty) == []


def test_dated_page_links_all_published_editions():
    known = {"gh": {"2026-07-18"}, "hn": {"2026-07-18"}, "ai": {"2026-07-18"}}
    assert cross_edition_links("gh", "2026-07-18", known) == [
        ("../hn/2026-07-18/", "Hacker News"),
        ("../ai/2026-07-18/", "AI News"),
    ]
    assert cross_edition_links("ai", "2026-07-18", known) == [
        ("../../2026-07-18/", "GitHub Trending"),
        ("../../hn/2026-07-18/", "Hacker News"),
    ]


def _fake_docs(tmp_path, monkeypatch):
    import editions
    monkeypatch.setattr(editions, "DOCS_DIR", tmp_path)
    (tmp_path / "2026-07-17").mkdir()
    (tmp_path / "2026-07-18").mkdir()
    (tmp_path / "hn" / "2026-07-18").mkdir(parents=True)
    (tmp_path / "ai" / "2026-07-18").mkdir(parents=True)
    (tmp_path / "ai" / "history.json").write_text("{}")  # non-date entry ignored
    return tmp_path


def test_published_dates_scans_docs(tmp_path, monkeypatch):
    _fake_docs(tmp_path, monkeypatch)
    assert published_dates("gh") == {"2026-07-17", "2026-07-18"}
    assert published_dates("hn") == {"2026-07-18"}
    assert published_dates("ai") == {"2026-07-18"}


def test_known_dates_partial_override_falls_back_to_scan(tmp_path, monkeypatch):
    _fake_docs(tmp_path, monkeypatch)
    # caller knows gh/hn from the DB; ai comes from the filesystem scan
    known = {"gh": {"2026-07-18"}, "hn": {"2026-07-18"}}
    assert cross_edition_links("gh", "2026-07-18", known) == [
        ("../hn/2026-07-18/", "Hacker News"),
        ("../ai/2026-07-18/", "AI News"),
    ]


def test_write_dates_manifest(tmp_path, monkeypatch):
    import json
    _fake_docs(tmp_path, monkeypatch)
    write_dates_manifest()
    manifest = json.loads((tmp_path / "dates.json").read_text())
    assert manifest == {
        "gh": ["2026-07-17", "2026-07-18"],
        "hn": ["2026-07-18"],
        "ai": ["2026-07-18"],
    }
    # DB-known override wins for listed editions
    write_dates_manifest({"gh": {"2026-07-19"}})
    manifest = json.loads((tmp_path / "dates.json").read_text())
    assert manifest["gh"] == ["2026-07-19"]
    assert manifest["ai"] == ["2026-07-18"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_editions.py -v`
Expected: FAIL / errors with `ModuleNotFoundError: No module named 'editions'`

- [ ] **Step 4: Implement `editions.py`**

```python
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
```

Note: `to_root` for a gh calendar page is `""` — links become `"hn/"`, `"ai/"` (correct: the gh calendar lives at docs root).

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_editions.py -v`
Expected: all PASS.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check editions.py tests/test_editions.py
git add editions.py tests/test_editions.py pyproject.toml uv.lock
git commit -m "feat: editions.py — single-source cross-edition nav registry

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Single dates.json writer

**Files:**
- Modify: `trending_digest.py:530-538` (delete local `write_dates_manifest`), `trending_digest.py:3390` (call site), imports near top (~line 33)
- Modify: `scripts/build_dates_manifest.py` (thin CLI)
- Regenerate: `docs/dates.json`

**Interfaces:**
- Consumes: `editions.write_dates_manifest(known_dates=None)`, `editions.published_dates`
- Produces: `docs/dates.json` now includes an `"ai"` key. `scripts/build_dates_manifest.py` stays runnable as a standalone CLI.

- [ ] **Step 1: Replace trending_digest's writer**

Delete the `write_dates_manifest` function at `trending_digest.py:530-538`. Add to the imports block (after the existing `from morning_edition import ...` at line 33):

```python
from editions import EDITIONS, cross_edition_links, write_dates_manifest
```

Change the call at line 3390 from:

```python
        write_dates_manifest(DOCS_DIR, gh_dates, hn_dates)
```

to:

```python
        write_dates_manifest({"gh": gh_dates_set, "hn": hn_dates_set})
```

(`gh_dates_set` / `hn_dates_set` already exist at lines 3378-3379; `"ai"` intentionally absent so it comes from the filesystem scan — the AI pickup in Task 6 runs earlier in `main`, so a same-day AI edition is already on disk.)

- [ ] **Step 2: Make the script a thin CLI**

Replace the entire body of `scripts/build_dates_manifest.py` with:

```python
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
```

- [ ] **Step 3: Regenerate the manifest and verify**

Run: `uv run python scripts/build_dates_manifest.py`
Expected output: `Wrote docs/dates.json: gh=N, hn=N, ai=1` (ai count ≥ 1; 2026-07-18 is published).

Run: `uv run python -c "import json;print(json.load(open('docs/dates.json'))['ai'])"`
Expected: `['2026-07-18']` (plus any newer dates).

Run: `uv run pytest tests/ -v` — all PASS. Also confirm trending_digest still imports: `uv run python -c "import trending_digest"` (exits 0; needs no DB at import time).

- [ ] **Step 4: Commit**

```bash
git add trending_digest.py scripts/build_dates_manifest.py docs/dates.json
git commit -m "refactor: one dates.json writer derived from editions registry (adds ai)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Magazine masthead consumes the registry

**Files:**
- Modify: `morning_edition.py:56-57` (read-key constants), `morning_edition.py:65-77` (EditionConfig gains `headline`), `morning_edition.py:144-176` (CONFIGS), `morning_edition.py:442-472` (`_render_masthead`)

**Interfaces:**
- Consumes: `editions.EDITIONS`, `editions.cross_edition_links`
- Produces: `EditionConfig` gains required field `headline: str` (set for all three configs). Masthead nav = own calendar · each published same-date other edition · Classic View. Tagline/headline no longer hardcode the hn/gh binary (fixes AI magazine showing "The Open Source Edition" / "Ten stories, before your coffee.").

- [ ] **Step 1: Import registry and derive constants**

Add near the top of `morning_edition.py` (with the other imports, before line 33):

```python
from editions import EDITIONS, cross_edition_links
```

Replace lines 56-57:

```python
READ_DAYS_KEY_HN = "gtd:read_days:hn:v1"
READ_DAYS_KEY_GH = "gtd:read_days:gh:v1"
```

with:

```python
READ_DAYS_KEY_HN = EDITIONS["hn"].read_key
READ_DAYS_KEY_GH = EDITIONS["gh"].read_key
```

- [ ] **Step 2: Add `headline` to EditionConfig and CONFIGS**

In the `EditionConfig` dataclass (line 65), add a required field after `prompt_voice`:

```python
    headline: str  # masthead <h1>
```

In `CONFIGS` set:
- `"ai"`: `headline="Today in AI, cover to cover.",` and change `output_dir=EDITIONS["ai"].output_dir,` and `read_key=EDITIONS["ai"].read_key,`
- `"hn"`: `headline="Ten stories, before your coffee.",` and `output_dir=EDITIONS["hn"].output_dir,` (read_key already `READ_DAYS_KEY_HN`)
- `"gh"`: `headline="Ten stories, before your coffee.",` and `output_dir=EDITIONS["gh"].output_dir,` (read_key already `READ_DAYS_KEY_GH`)

(`EDITIONS["gh"].output_dir` == `REPO_ROOT / "docs"`, same value as before.)

- [ ] **Step 3: Rewrite `_render_masthead`**

Replace the whole function (lines 442-472) with:

```python
def _render_masthead(config: EditionConfig, day: date) -> str:
    ed = EDITIONS[config.id]
    nav_parts = [f'<a href="../">{ed.calendar_label}</a>']
    for href, label in cross_edition_links(config.id, day.isoformat()):
        nav_parts.append(f'<a href="{href}">{label} &nbsp;·&nbsp; {day.strftime("%b %-d")}</a>')
    nav_parts.append('<a href="classic.html">Classic View</a>')
    nav_html = "\n      <span>&nbsp;·&nbsp;</span>\n      ".join(nav_parts)

    return f"""  <header class="masthead">
    <div class="masthead-nav">
      {nav_html}
    </div>
    <div class="tagline">The {config.name}</div>
    <h1 class="frnc">{config.headline}</h1>
    <div class="issue-line">
      <span>Vol. I</span>
      <span>{day.strftime("%B %-d, %Y")}</span>
      <span>{config.tagline}</span>
    </div>
  </header>"""
```

Known label change (approved): HN masthead "HN Calendar" → "Hacker News Calendar".

- [ ] **Step 4: Smoke-test the masthead**

Run:

```bash
uv run python - <<'EOF'
from datetime import date
from morning_edition import CONFIGS, _render_masthead
html = _render_masthead(CONFIGS["ai"], date(2026, 7, 18))
assert "AI News Calendar" in html and "GitHub Calendar" not in html
assert "../../2026-07-18/" in html          # GH published that day
assert "../../hn/2026-07-18/" in html       # HN published that day
assert "The AI Edition" in html and "Today in AI" in html
html_missing = _render_masthead(CONFIGS["ai"], date(2020, 1, 1))
assert "2020-01-01" not in html_missing      # nothing published -> no dated links
html_hn = _render_masthead(CONFIGS["hn"], date(2026, 7, 18))
assert "Hacker News Calendar" in html_hn and "../../2026-07-18/" in html_hn
print("masthead OK")
EOF
```

Expected: `masthead OK`. Then `uv run pytest tests/ -v` (still green) and `uv run ruff check morning_edition.py`.

- [ ] **Step 5: Commit**

```bash
git add morning_edition.py
git commit -m "refactor: masthead nav + edition identity from editions registry

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: GH/HN classic dailies and calendar indexes

**Files:**
- Modify: `trending_digest.py` — `generate_gh_daily_page` (2159-2226), `generate_hn_daily_page` (2229-2327), `generate_gh_index_page` (2330-2367), `generate_hn_index_page` (2370-2407), call sites 2761, 2779, 3382, 3385 (and regen helpers' signatures at 2754, 2773)

**Interfaces:**
- Consumes: `editions.EDITIONS`, `editions.cross_edition_links` (imported in Task 2)
- Produces: `generate_gh_daily_page(repos, day, known_dates, slow_burners=None)` and `generate_hn_daily_page(items, day, known_dates)` — both now take `known_dates: dict[str, set[str]]` instead of a single other-edition set. `regenerate_gh_daily_pages(conn, gh_dates, known_dates)` / `regenerate_hn_daily_pages(conn, hn_dates, known_dates)` likewise.

- [ ] **Step 1: GH daily page**

In `generate_gh_daily_page`, change the signature's third parameter from `hn_dates_set: set[str]` to `known_dates: dict[str, set[str]]`, delete line 2168 (`hn_link = ...`), and add in its place:

```python
    cross_html = "\n            ".join(
        f'<a href="{href}">{label}</a>'
        for href, label in cross_edition_links("gh", date_str, known_dates)
    )
```

Replace the nav block (2200-2203):

```html
        <nav>
            <a href="../">&larr; {EDITIONS["gh"].calendar_label}</a>
            {cross_html}
        </nav>
```

- [ ] **Step 2: HN daily page**

Same treatment in `generate_hn_daily_page`: third parameter `gh_dates_set: set[str]` → `known_dates: dict[str, set[str]]`, delete line 2233 (`gh_link = ...`), add:

```python
    cross_html = "\n            ".join(
        f'<a href="{href}">{label}</a>'
        for href, label in cross_edition_links("hn", date_str, known_dates)
    )
```

Replace the nav block (2303-2306):

```html
        <nav>
            <a href="../">&larr; {EDITIONS["hn"].calendar_label}</a>
            {cross_html}
        </nav>
```

- [ ] **Step 3: Calendar indexes**

In `generate_gh_index_page`, add before the return:

```python
    cross_html = "\n            ".join(
        f'<a href="{href}">{label}</a>' for href, label in cross_edition_links("gh")
    )
```

and replace its nav (2348-2350):

```html
        <nav>
            {cross_html}
        </nav>
```

Same in `generate_hn_index_page` with `cross_edition_links("hn")` and its nav (2388-2390). Label change (approved): HN index "GitHub Trending Calendar" → "GitHub Calendar"; both indexes gain "AI News Calendar".

- [ ] **Step 4: Update the four call sites and regen helpers**

At 3378-3379 the sets already exist; build once after them:

```python
        known_dates = {"gh": gh_dates_set, "hn": hn_dates_set}
```

- 3382: `generate_gh_daily_page(gh_rows, run_day, known_dates, slow_burners=slow_burners)`
- 3385: `generate_hn_daily_page(hn_rows, run_day, known_dates)`
- `regenerate_gh_daily_pages` (2754): parameter `hn_dates_set: set[str]` → `known_dates: dict[str, set[str]]`; call at 2761 passes `known_dates`.
- `regenerate_hn_daily_pages` (2773): parameter `gh_dates_set: set[str]` → `known_dates: dict[str, set[str]]`; call at 2779 passes `known_dates`.
- Find and update the callers of the two regenerate helpers (in the `--regenerate-only` branch of `main`, near line 3336): `grep -n "regenerate_gh_daily_pages(\|regenerate_hn_daily_pages(" trending_digest.py` — pass the same `known_dates` dict built from that branch's `gh_dates_set`/`hn_dates_set`.

- [ ] **Step 5: Smoke-test rendering**

```bash
uv run python - <<'EOF'
from datetime import date
from trending_digest import generate_gh_daily_page, generate_hn_daily_page, generate_gh_index_page, generate_hn_index_page
known = {"gh": {"2026-07-18"}, "hn": {"2026-07-18"}, "ai": {"2026-07-18"}}
gh = generate_gh_daily_page([], date(2026, 7, 18), known)
assert '<a href="../hn/2026-07-18/">Hacker News</a>' in gh
assert '<a href="../ai/2026-07-18/">AI News</a>' in gh
hn = generate_hn_daily_page([], date(2026, 7, 18), known)
assert '<a href="../../2026-07-18/">GitHub Trending</a>' in hn
assert '<a href="../../ai/2026-07-18/">AI News</a>' in hn
no_ai = {"gh": {"2026-07-17"}, "hn": {"2026-07-17"}, "ai": set()}
gh2 = generate_gh_daily_page([], date(2026, 7, 17), no_ai)
assert "ai/" not in gh2  # omitted, no calendar fallback
ghi = generate_gh_index_page([], [])
assert '<a href="hn/">Hacker News Calendar</a>' in ghi and '<a href="ai/">AI News Calendar</a>' in ghi
hni = generate_hn_index_page([], [])
assert '<a href="../">GitHub Calendar</a>' in hni and '<a href="../ai/">AI News Calendar</a>' in hni
print("classic navs OK")
EOF
```

Expected: `classic navs OK`. Then `uv run ruff check trending_digest.py`.

- [ ] **Step 6: Commit**

```bash
git add trending_digest.py
git commit -m "refactor: GH/HN classic + calendar navs from editions registry (adds AI links)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: AI edition pages

**Files:**
- Modify: `ai_edition.py` — imports (26-47), `generate_ai_classic_page` (167-245), `generate_ai_index_page` (264-292), `build_pages` (297-325), `main` argparse (328-344)

**Interfaces:**
- Consumes: `editions.EDITIONS`, `editions.cross_edition_links`, `editions.write_dates_manifest`
- Produces: `build_pages(sidecar, force_regenerate=True)` (Task 6 and the new `--reuse-assignments` flag rely on this signature). AI classic includes `preference.js`; standalone publish refreshes `dates.json`.

- [ ] **Step 1: Imports**

Add to `ai_edition.py` imports:

```python
from editions import EDITIONS, cross_edition_links, write_dates_manifest
```

(`AI_DIR`/`READ_KEY` keep coming from `CONFIGS["ai"]`, which Task 3 already pointed at the registry.)

- [ ] **Step 2: Classic page nav + preference.js**

In `generate_ai_classic_page`, add before the return:

```python
    cross_html = "\n            ".join(
        f'<a href="{href}">{label}</a>'
        for href, label in cross_edition_links("ai", date_str)
    )
```

Replace the nav block (221-225):

```html
        <nav>
            <a href="../">&larr; {EDITIONS["ai"].calendar_label}</a>
            <a href="./">Magazine view</a>
            {cross_html}
        </nav>
```

And add the preference.js include (parity with GH/HN classics) directly before `</body>`, i.e. change:

```html
{_daily_script(date_str)}
</body>
```

to:

```html
{_daily_script(date_str)}
<script src="../../preference.js?v={v}" defer></script>
</body>
```

- [ ] **Step 3: AI calendar nav**

In `generate_ai_index_page`, replace the nav (278-281) using the helper:

```python
    cross_html = "\n            ".join(
        f'<a href="{href}">{label}</a>' for href, label in cross_edition_links("ai")
    )
```

```html
        <nav>
            {cross_html}
        </nav>
```

(Labels become "GitHub Calendar" / "Hacker News Calendar".)

- [ ] **Step 4: build_pages signature + manifest**

Change `def build_pages(sidecar: Path) -> tuple[date, int]:` to `def build_pages(sidecar: Path, force_regenerate=True) -> tuple[date, int]:`, pass it through at line 313 (`generate_morning_edition(day, items, source="ai", force_regenerate=force_regenerate)`), and after `write_text(AI_DIR / "index.html", ...)` (line 324) add:

```python
    write_dates_manifest()
```

Add the CLI flag in `main` after `--no-publish`:

```python
    ap.add_argument("--reuse-assignments", action="store_true",
                    help="reuse cached magazine archetype assignments (no LLM call)")
```

and call `build_pages(args.sidecar, force_regenerate=not args.reuse_assignments)`.

- [ ] **Step 5: Smoke-test**

```bash
uv run python - <<'EOF'
from datetime import date
from ai_edition import generate_ai_classic_page
html = generate_ai_classic_page([], date(2026, 7, 18))
assert "AI News Calendar" in html
assert '<a href="../../2026-07-18/">GitHub Trending</a>' in html
assert '<a href="../../hn/2026-07-18/">Hacker News</a>' in html
assert "preference.js" in html
print("ai classic OK")
EOF
```

Expected: `ai classic OK`. Then `uv run pytest tests/ -v` and `uv run ruff check ai_edition.py`.

- [ ] **Step 6: Commit**

```bash
git add ai_edition.py
git commit -m "fix: AI edition nav — correct labels, working HN link, dated GH/HN links, preference.js

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Daily run picks up new AI newsletter; email lists only published editions

**Files:**
- Modify: `trending_digest.py` — new function near `main` (~line 3290), insertion in `main` before line 3381, email block 3412-3435

**Interfaces:**
- Consumes: `ai_edition.DEFAULT_SIDECAR`, `ai_edition.load_sidecar`, `ai_edition.build_pages(sidecar, force_regenerate=True)` (deferred import), `editions.EDITIONS`
- Produces: `maybe_render_ai_edition() -> date | None` (the AI day when a new edition was rendered this run, else None).

- [ ] **Step 1: Add the pickup function**

Above `main()` in `trending_digest.py`:

```python
def maybe_render_ai_edition():
    """Render the ai-newsletter sidecar's edition if it isn't published yet.

    Runs before GH/HN page generation so those pages (and dates.json) see the
    AI edition on disk and emit same-day AI links. Returns the AI day when a
    new edition was rendered this run, else None. Never sinks the daily run.
    """
    from ai_edition import DEFAULT_SIDECAR, build_pages, load_sidecar  # deferred: ai_edition imports this module

    try:
        if not DEFAULT_SIDECAR.exists():
            logging.info("No ai-newsletter sidecar at %s; skipping AI edition", DEFAULT_SIDECAR)
            return None
        day, _items = load_sidecar(DEFAULT_SIDECAR)
        if (EDITIONS["ai"].output_dir / day.isoformat()).is_dir():
            logging.info("AI edition for %s already published; skipping", day)
            return None
        day, count = build_pages(DEFAULT_SIDECAR)
        logging.info("AI edition: rendered %d stories for %s", count, day)
        return day if count else None
    except Exception as exc:
        logging.exception("AI edition pickup failed: %s", exc)
        notify_gotify("AI edition pickup failed", str(exc))
        return None
```

- [ ] **Step 2: Call it before page generation**

In `main`, insert immediately before `slow_burners = build_gh_slow_burner_rows(conn, run_day)` (line 3381):

```python
        ai_day = maybe_render_ai_edition()
```

(This is after `known_dates` from Task 4 Step 4 and before any page rendering, so GH/HN pages and the manifest pick up the AI dirs via the filesystem scan.)

- [ ] **Step 3: Email + liveness only for published editions**

Replace lines 3414-3430 with:

```python
        gh_page_url = f"{GITHUB_PAGES_URL}{run_day.isoformat()}/"
        hn_page_url = f"{GITHUB_PAGES_URL}hn/{run_day.isoformat()}/"
        email_sections = [
            f"GitHub Trending Digest:\n{gh_page_url}",
            f"Hacker News Digest:\n{hn_page_url}",
        ]
        live_urls = [gh_page_url, hn_page_url]
        if ai_day:
            ai_page_url = f"{GITHUB_PAGES_URL}ai/{ai_day.isoformat()}/"
            email_sections.append(f"AI Edition:\n{ai_page_url}")
            live_urls.append(ai_page_url)

        if changed:
            if wait_for_pages_live(live_urls):
                send_email(
                    to_address=email_to_address,
                    subject="links",
                    body="\n\n".join(email_sections),
                )
            else:
                logging.error("Skipping email because one or more pages did not go live")
                notify_gotify(
                    "GitHub Trending Digest: pages did not go live",
                    "One or more published pages did not return HTTP 200:\n"
                    + "\n".join(live_urls),
                )
```

(Keep the existing trailing `logging.info("Done. ...")` lines; the email body only ever lists editions that actually published this run — the omit rule, applied to email.)

- [ ] **Step 4: Verify imports/flow without a full run**

Run: `uv run python -c "import trending_digest; print(trending_digest.maybe_render_ai_edition.__doc__.splitlines()[0])"`
Expected: prints the docstring first line, no import errors (also proves no circular import at module load).

Run: `uv run pytest tests/ -v` — green.

- [ ] **Step 5: Commit**

```bash
git add trending_digest.py
git commit -m "feat: daily run picks up new ai-newsletter sidecar before GH/HN generation

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: preference.js edition-aware cross-link rewrite

**Files:**
- Modify: `docs/preference.js` — `updateCrossEditionLinks` (~lines 164-191)

**Interfaces:**
- Consumes: nothing new. Produces: style-pref rewriting works for links between any two editions (was a gh-vs-hn binary that treats AI pages as GH).

- [ ] **Step 1: Generalize edition detection**

In `updateCrossEditionLinks`, replace:

```js
    var curIsHn = /(^|\/)hn\//.test(curPath);
```

with:

```js
    // Edition ids by path prefix; extend this regex when adding an edition.
    function editionOf(path) {
      var m = path.match(/(^|\/)(hn|ai)\//);
      return m ? m[2] : "gh";
    }
    var curEdition = editionOf(curPath);
```

and replace:

```js
      var targetIsHn = /(^|\/)hn\//.test(resolved);
      if (targetIsHn === curIsHn) continue; // same edition or calendar-within-edition
```

with:

```js
      if (editionOf(resolved) === curEdition) continue; // same edition or calendar-within-edition
```

- [ ] **Step 2: Verify in a browser**

```bash
python3 -m http.server 8901 --directory docs &
```

Using playwright (or manually): open `http://localhost:8901/ai/2026-07-18/classic.html` after Task 8's re-render — confirm (a) the day-nav bar mounts (dates.json has `ai`), (b) the style toggle appears, (c) with pref set to "classic", the GitHub Trending / Hacker News links point at `.../classic.html`. Kill the server afterward.

(If Task 8 hasn't run yet, defer this browser check to Task 8 Step 4 — the code change still commits here.)

- [ ] **Step 3: Commit**

```bash
git add docs/preference.js
git commit -m "fix: preference.js cross-edition style rewrite understands the ai edition

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: End-to-end verification + republish the broken AI day

**Files:**
- Regenerated (kept): `docs/ai/2026-07-18/classic.html`, `docs/ai/2026-07-18/index.html`, `docs/ai/index.html`, `docs/dates.json`
- Regenerated (inspected then reverted): everything else under `docs/` touched by `--regenerate-only`

**Interfaces:** none new — this task verifies the whole spec.

- [ ] **Step 1: Full test + lint pass**

```bash
uv run pytest tests/ -v
uv run ruff check editions.py ai_edition.py morning_edition.py trending_digest.py scripts/build_dates_manifest.py
```

Expected: all tests pass, no lint errors.

- [ ] **Step 2: Re-render the published AI day (kept — these are the broken pages)**

```bash
uv run python ai_edition.py \
  --sidecar /home/flog99/dev/ai-newsletter/site/editions/2026-07-18-2010.json \
  --no-publish --reuse-assignments
grep -o 'AI News Calendar\|href="../../2026-07-18/"\|href="../../hn/2026-07-18/"' docs/ai/2026-07-18/classic.html | sort -u
grep -o 'AI News Calendar\|The AI Edition\|href="../../2026-07-18/"\|href="../../hn/2026-07-18/"' docs/ai/2026-07-18/index.html | sort -u
grep -c 'GitHub Calendar' docs/ai/2026-07-18/index.html || true
```

Expected: classic shows all three patterns; magazine shows all four; the last grep finds 0 occurrences of "GitHub Calendar" in the AI magazine. Also `grep '"ai"' docs/dates.json` shows the ai key.

Note: `--reuse-assignments` depends on `docs/ai/2026-07-18/assignments.json` existing from the original run. If it's missing, the magazine regenerates via an LLM call (needs `OPENAI_API_KEY` in the environment) — still correct, just slower/costed.

- [ ] **Step 3: Regenerate GH/HN locally, inspect, revert**

```bash
docker compose up -d postgres
uv run python trending_digest.py --regenerate-only
grep -o 'href="../ai/2026-07-18/">AI News' docs/2026-07-18/classic.html
grep -o 'href="../../ai/2026-07-18/">AI News' docs/hn/2026-07-18/classic.html
grep -c 'ai/2026-07-17' docs/2026-07-17/classic.html || echo "no ai link on 07-17 (correct)"
grep -o 'AI News Calendar' docs/index.html docs/hn/index.html
```

Expected: 2026-07-18 GH and HN classics link the dated AI page; 2026-07-17 has no AI link ("no ai link on 07-17 (correct)"); both calendars link "AI News Calendar". Then revert everything except the AI-day artifacts and manifest:

```bash
git add docs/ai/ docs/dates.json docs/index.html docs/hn/index.html
git restore docs/   # worktree-from-index: discards unstaged docs changes, keeps the staged ones
git status
```

(Calendar indexes are kept too — they self-heal next run anyway, but keeping them publishes the AI calendar link immediately. Historical dailies revert, per spec.)

- [ ] **Step 4: Browser spot-check**

Run the Task 7 Step 2 browser verification now if it was deferred (local server on `docs/`, check AI classic + magazine day-nav, style toggle, masthead links resolve with HTTP 200 on the local server).

- [ ] **Step 5: Final commit**

```bash
git add docs/ai/ docs/dates.json docs/index.html docs/hn/index.html
git commit -m "fix: republish AI day 2026-07-18 with corrected nav; calendars link AI edition

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

Do NOT push — publishing is the daily cron's job; surface to the user that a `git push` (or the next cron run) publishes.
