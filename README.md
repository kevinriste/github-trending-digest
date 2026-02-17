# GitHub Trending Digest

**Live Site:** [www.kevinriste.com/github-trending-digest](https://www.kevinriste.com/github-trending-digest/)

Daily generated digests for:
- GitHub Trending repositories (with AI summaries)
- Hacker News top stories (with AI summaries)

Pages are published to GitHub Pages with calendar navigation.

## What It Does

Each run:
1. Scrapes GitHub Trending for `daily`, `weekly`, and `monthly`.
2. Scrapes Hacker News via the official free Firebase API (`topstories`).
3. Stores all fetched data in PostgreSQL.
4. Reuses cached summaries and refreshes them every 60 days.
5. Generates static HTML pages under `docs/`.
6. Commits/pushes docs changes and emails links after pages are live.

## Key Features

- GitHub scrape caps:
  - `daily`: 10 repos
  - `weekly`: up to 25 repos
  - `monthly`: up to 100 repos
  - (or fewer if fewer are available on GitHub Trending)
- Postgres-backed history for:
  - earliest appearance date
  - consecutive daily streaks
  - summary caching
- Browser-local read-day coloring on calendar pages.
- `?collapse_seen=1` support on GitHub daily pages:
  - previously seen repos start collapsed
  - rank/name/link header remains visible in the same format
- Separate Hacker News pages under `docs/hn/` with cross-links to GitHub pages.
- Hacker News daily cards include `Comment Analysis` from sampled discussion context.

## Project Structure

```text
github-trending-digest/
├── trending_digest.py      # Main pipeline (scrape, DB, summaries, HTML, git, email)
├── docker-compose.yml      # Postgres with persistent named volume
├── process.sh              # Cron execution script
├── process-caller.sh       # Wrapper with Gotify error notifications
├── test-gh.sh              # gh CLI diagnostic
├── docs/                   # GitHub Pages output
│   ├── index.html          # GitHub calendar
│   ├── style.css           # Shared styles
│   ├── pages.json          # GitHub date index
│   ├── YYYY-MM-DD/index.html
│   └── hn/
│       ├── index.html      # Hacker News calendar
│       ├── pages.json      # Hacker News date index
│       └── YYYY-MM-DD/index.html
├── IMPLEMENTATION_PLAN.md  # Agreed roadmap and decisions
└── pyproject.toml
```

## Database (Docker Compose)

Start PostgreSQL locally:

```bash
docker compose up -d postgres
```

Data persists in named volume `trending_digest_pgdata`.

Default local connection used by the script:

```text
postgresql://trending_digest:trending_digest@localhost:5433/trending_digest
```

Override with `DATABASE_URL` if needed.

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | Postgres connection string (optional if using default compose setup) |
| `OPENAI_API_KEY` | OpenAI API authentication |
| `GMAIL_PODCAST_ACCOUNT` | Gmail sender address |
| `GMAIL_PODCAST_ACCOUNT_APP_PASSWORD` | Gmail app password |
| `DIGEST_EMAIL_TO` | Recipient for digest email (defaults to personal address in script) |
| `GOTIFY_SERVER` | Gotify server URL (optional, for wrapper script) |
| `GOTIFY_TOKEN` | Gotify token (optional, for wrapper script) |

Optional tuning:
- `GH_DAILY_RENDER_LIMIT` (`0` = all daily repos fetched)
- `HN_DAILY_RENDER_LIMIT` (default `10`)
- `HN_MAX_ITEMS` (`0` = all topstories IDs)
- `HN_FETCH_WORKERS` (default `20`)
- `HN_COMMENT_SAMPLE_SIZE` (default `16`)
- `HN_COMMENT_TRAVERSAL_MAX_NODES` (default `300`)
- `HN_COMMENT_TRAVERSAL_MAX_DEPTH` (default `6`)
- `HN_COMMENT_MAX_PER_BRANCH` (default `4`)
- `HN_COMMENT_MIN_TEXT_LEN` (default `40`)

## Setup

1. Clone the repo.
2. Start Postgres: `docker compose up -d postgres`
3. Install deps: `uv sync`
4. Set env vars.
5. Authenticate git/gh as needed.
6. Run once: `uv run python3 trending_digest.py`

Cron usage:

```cron
0 6 * * * /path/to/process-caller.sh
```

## Integration Notes

- GitHub daily pages keep repository cards as `<section class="repo">` inside a single `<article>` wrapper for downstream trafilatura extraction compatibility.
- Daily email includes both links:
  - GitHub daily page with `?collapse_seen=1`
  - Hacker News daily page

## API Sources

- GitHub Trending web pages (`daily`, `weekly`, `monthly`)
- Hacker News official API:
  - `https://hacker-news.firebaseio.com/v0/topstories.json`
  - `https://hacker-news.firebaseio.com/v0/item/<id>.json`
