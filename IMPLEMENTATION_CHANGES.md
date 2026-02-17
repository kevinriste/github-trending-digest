# Implementation Changes

## Overview

This document captures the concrete changes implemented from `IMPLEMENTATION_PLAN.md`.

## Data & Infrastructure

- Added PostgreSQL support and migrated state from JSON-only tracking to relational storage.
- Added Docker Compose setup for Postgres with persistent named volume:
  - `docker-compose.yml`
  - volume: `trending_digest_pgdata`
- Added `psycopg[binary]` dependency.
- Updated `process.sh` to start Postgres container before running the digest script.

## Persistence Model

Implemented Postgres schema bootstrap in `trending_digest.py` with:

- `app_meta` for one-time tasks and run metadata
- GitHub tables:
  - `gh_repos`
  - `gh_runs` (`daily|weekly|monthly`)
  - `gh_entries`
  - `gh_summaries`
- Hacker News tables:
  - `hn_items`
  - `hn_runs`
  - `hn_entries`
  - `hn_summaries`

Also added indexes for key lookup paths and summary retrieval.

## Backfill & Legacy Summary Preservation

- Added one-time backfill from existing `docs/YYYY-MM-DD/index.html` pages into Postgres.
- Backfilled historical summary content into `gh_summaries` (preserves prior AI summaries).
- Added local archive file with old summaries extracted from existing docs:
  - `data/legacy_summaries/gh_daily_summaries_from_docs.jsonl`

## GitHub Ingestion & Page Behavior

- GitHub scraping now collects and stores all visible entries for:
  - daily: max 10
  - weekly: max 25
  - monthly: max 100
  - (or fewer when the source page has fewer entries)
- Daily page rendering now includes:
  - earliest seen date
  - consecutive daily streak
  - seen-before indicator
- Added `?collapse_seen=1` behavior:
  - previously seen repos start collapsed
  - rank/name/link header remains visible
  - controls for `Collapse Seen Repos` and `Expand All`

## Read-State in Browser

- Added browser-local read tracking via `localStorage`.
- Calendar dates already visited now render with a different color.
- Separate keys for GitHub and HN calendars:
  - `gtd:read_days:gh:v1`
  - `gtd:read_days:hn:v1`

## Summary Caching Policy

- Added 7-day summary refresh policy:
  - reuse existing summary if younger than 7 days
  - regenerate only after 7 days
- Applies to both GitHub repo summaries and Hacker News story summaries.

## Hacker News Support

- Added official Hacker News API ingestion (`topstories` + item endpoint).
- Added separate HN output pages:
  - `docs/hn/index.html`
  - `docs/hn/YYYY-MM-DD/index.html`
  - `docs/hn/pages.json`
- Added cross-links between GitHub and HN calendars and daily pages.

## Email Output

- Daily email now includes both links:
  - GitHub daily page with `?collapse_seen=1`
  - Hacker News daily page

## Documentation

- Replaced README content to reflect:
  - Postgres + Compose workflow
  - new env vars
  - HN pages
  - new behavior and tuning knobs
- Added:
  - `IMPLEMENTATION_PLAN.md`
  - `IMPLEMENTATION_CHANGES.md`
