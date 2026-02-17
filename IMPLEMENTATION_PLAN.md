# GitHub Trending Digest: Implementation Plan

## Scope

Implement the agreed upgrades:

1. Browser-local read-day highlighting.
2. Persistent relational data store in Docker Compose with a volume.
3. GitHub trending ingestion for daily, weekly, and monthly every run.
4. Repo history metrics (earliest seen + consecutive-day streak).
5. URL query behavior to collapse previously seen repos.
6. Daily email containing both GitHub and Hacker News links.
7. Separate Hacker News pages and calendar using official HN API.
8. Backfill historical data from existing generated pages.

No work is skipped; all items should be implemented end-to-end.

## Product Decisions (Finalized)

- Read-state tracking: per-browser only (`localStorage`), no cross-device sync.
- Summary regeneration cadence: every 7 days.
- GitHub scrape volume caps:
  - daily: 10 repos
  - weekly: up to 25 repos
  - monthly: up to 100 repos
  - (or fewer if fewer are available)
- Collapsed card behavior: keep rank + repo link/name visible in same format as expanded.
- Stats placement: show `earliest seen` and `consecutive day streak` between header row and AI summary area.
- Hacker News: separate webpage set and calendar, cross-linked with GitHub pages.
- HN source: official free Hacker News Firebase API.
- Email behavior: include both GitHub and HN daily links on first rollout.

## Architecture Plan

### 1) Data Layer

Use PostgreSQL (in Docker Compose) with named persistent volume.

- New Compose service: `postgres`.
- New env var: `DATABASE_URL`.
- New named volume: e.g. `trending_digest_pgdata`.
- App performs schema bootstrap (idempotent `CREATE TABLE IF NOT EXISTS`).

### 2) Schema (GitHub)

- `gh_repos`: canonical repo identity and metadata.
- `gh_runs`: each scrape execution by date + period (`daily|weekly|monthly`).
- `gh_entries`: rank and metrics for each repo in a run.
- `gh_summaries`: cached AI summaries with model, prompt version, and generated timestamp.

Derived metrics:
- Earliest date from first matching `gh_entries`.
- Consecutive daily streak ending on page date.

### 3) Schema (Hacker News)

- `hn_items`: canonical item data from API (id, title, url, by, score, descendants, time).
- `hn_runs`: each daily fetch run.
- `hn_entries`: rank + item for each run.
- `hn_summaries`: cached summaries (same 7-day refresh policy).

### 4) Scraping & Ingestion

GitHub:
- Fetch and store daily/weekly/monthly each run.
- Store all visible entries from each GitHub Trending period page.

Hacker News:
- Fetch top story IDs via `/v0/topstories.json`.
- Fetch item details via `/v0/item/{id}.json`.
- Store full fetched set; render selected top section in page output.

### 5) Page Generation

GitHub:
- Keep clean URL structure: `docs/YYYY-MM-DD/index.html`.
- Add per-repo stats block under header.
- Add collapse behavior with `?collapse_seen=1`:
  - Seen-before repos collapse details by default.
  - Header remains always visible and consistent.
  - Include expand/collapse controls.
- Add JS to mark current day as read in browser storage.

GitHub Index:
- Keep calendar UI.
- Add JS to color previously visited days from `localStorage`.
- Add cross-link to HN calendar.

Hacker News:
- Separate section under `docs/hn/`.
- Daily page: `docs/hn/YYYY-MM-DD/index.html`.
- Calendar: `docs/hn/index.html`.
- Add cross-links between GH and HN calendars.
- Read-day coloring via separate storage key namespace.

### 6) Email

After publish check, send one email body containing:
- GitHub daily URL with `?collapse_seen=1`.
- Hacker News daily URL.

### 7) Backfill

Add parser/ingest routine to process existing GitHub `docs/YYYY-MM-DD/index.html` files:
- recover rank/repo identity and available metadata,
- populate `gh_repos`, `gh_runs`, and `gh_entries`,
- allow immediate earliest/streak calculations for old pages.

### 8) Operations & Safety

- Keep non-destructive behavior and idempotent reruns.
- Keep current generated output compatibility where possible.
- Add a run lock (or DB-based guard) to prevent accidental overlap.

## Implementation Order

1. Add Docker Compose + Postgres volume + environment wiring.
2. Add DB bootstrap, schema, and helper query layer.
3. Integrate GitHub daily/weekly/monthly ingestion and summary cache.
4. Add GitHub page UI changes (stats + collapse + read tracking).
5. Add HN ingestion and separate HN pages/calendars.
6. Update email to include both links.
7. Add backfill routine and run once in normal flow if needed.
8. Update docs and verify dry run behavior.

## Acceptance Criteria

- Data persists across container restarts via named volume.
- GitHub daily run stores daily/weekly/monthly records.
- Summary regeneration happens only when older than 7 days.
- Daily GH pages show earliest-seen and consecutive-day streak.
- `?collapse_seen=1` collapses only previously seen entries and preserves header format.
- Index highlights browser-visited dates.
- Separate HN pages/calendar are generated and cross-linked.
- Email includes both GH and HN links.
- Historical pages are backfilled into DB for streak calculations.
