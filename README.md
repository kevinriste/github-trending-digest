# GitHub Trending Digest

Daily digest generator for GitHub Trending. It scrapes the top repositories, generates short AI summaries, and publishes a dated HTML page plus a calendar index to GitHub Pages.

Live site: [https://www.kevinriste.com/github-trending-digest/](https://www.kevinriste.com/github-trending-digest/) (the rendered GitHub Pages site backed by `docs/`, with a calendar index and daily digests).

## What it produces
- `docs/YYYY-MM-DD/index.html` with the top 10 trending repositories and AI summaries
- `docs/index.html` with a calendar view linking to available dates
- `docs/style.css` shared styling

The site is intended to be served from the `docs/` folder via GitHub Pages.

## How it works
1. Scrape https://github.com/trending?since=daily
2. Fetch each repository README (raw GitHub URLs)
3. Generate a two-paragraph summary using OpenAI
4. Render HTML and CSS to `docs/`
5. Commit and push `docs/` changes
6. Optionally wait for the page to go live, then email the link

## Requirements
- Python 3.12+
- `uv` (dependency manager)
- Git configured with a remote that can push

## Install
```
uv sync
```

## Run
```
uv run python3 trending_digest.py
```

## Automation helpers
- `process.sh` runs the digest with logging and installs dependencies via `uv sync`.
- `process-caller.sh` wraps `process.sh` and reports failures to Gotify.

Example:
```
./process.sh
```

## Environment variables
- `OPENAI_API_KEY` (required): used by the OpenAI client
- `GMAIL_PODCAST_ACCOUNT` (optional): Gmail address used to send the page link
- `GMAIL_PODCAST_ACCOUNT_APP_PASSWORD` (optional): Gmail app password
- `GOTIFY_SERVER` (optional, used by `process-caller.sh`)
- `GOTIFY_TOKEN` (optional, used by `process-caller.sh`)

## Notes
- The scraper relies on GitHub Trending HTML structure and may break if the page changes.
- The digest is skipped if a page for today already exists in `docs/`.
- Git operations happen inside the script; ensure credentials and permissions are set up.

## Recommendations
See [RECOMMENDATIONS.md](RECOMMENDATIONS.md) for Codex-generated suggestions (not Claude).
