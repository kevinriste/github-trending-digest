# Agent Instructions

This file provides context for AI agents working on this codebase.

## Project Overview

GitHub Trending Digest is a Python automation that:
1. Scrapes the top 10 trending GitHub repositories daily
2. Generates AI summaries for each repository
3. Publishes results to GitHub Pages
4. Sends email notifications after deployment

## Key Files

| File | Purpose |
|------|---------|
| `trending_digest.py` | Main Python script - scraping, AI summaries, HTML generation, git operations, email |
| `process.sh` | Crontab execution script with pyenv setup and logging |
| `process-caller.sh` | Wrapper script with Gotify error notifications |
| `test-gh.sh` | Diagnostic script for testing gh CLI in crontab context |
| `docs/` | GitHub Pages output directory (auto-generated) |

## Architecture Decisions

### HTML Structure for trafilatura Compatibility
The generated HTML uses `<section class="repo">` tags inside a single `<article>` wrapper. This is intentional - trafilatura (used by the downstream podcast-transcribe system) treats `<article>` as a content boundary. Multiple `<article>` tags would cause only the first repository to be extracted.

### Clean URLs
Daily pages are stored as `docs/YYYY-MM-DD/index.html` to enable clean URLs without `.html` extensions on GitHub Pages.

### Title in H1
The `<h1>` tag includes the date (e.g., "GitHub Trending Digest - February 02, 2026") because trafilatura prefers `<h1>` over `<title>` for page title extraction.

### Portable Shell Scripts
Scripts use `$HOME` and `$(dirname "$0")` instead of hardcoded paths for portability and to avoid exposing directory structure in a public repository.

## Common Tasks

### Adding a New AI Model
Update `SUMMARY_MODEL` constant in `trending_digest.py:27`. The OpenAI client is used via `client.responses.create()`.

### Changing the Number of Repos
Update the `limit` parameter in the `main()` function call to `scrape_trending_repos()` at line 612.

### Modifying HTML Output
- Daily page template: `generate_daily_page()` function (line 190)
- Index page template: `generate_index_page()` function (line 271)
- CSS styles: `generate_css()` function (line 366)

### Testing Locally
```bash
# Dry run (will create today's page if it doesn't exist)
uv run python trending_digest.py

# Test gh CLI authentication
./test-gh.sh
```

## Environment Variables Required

- `OPENAI_API_KEY` - For AI summaries
- `GMAIL_PODCAST_ACCOUNT` - Sender email address
- `GMAIL_PODCAST_ACCOUNT_APP_PASSWORD` - Gmail app password
- `GOTIFY_SERVER` - Error notification server (optional)
- `GOTIFY_TOKEN` - Gotify app token (optional)

## Integration Points

This project integrates with:
1. **GitHub Pages** - Hosting the generated HTML
2. **GitHub CLI (gh)** - Git operations use stored credentials from `~/.config/gh/hosts.yml`
3. **podcast-transcribe** - Email triggers IMAP parser which fetches pages via trafilatura

## Code Style

- Python: Standard library where possible, minimal dependencies
- Shell: POSIX-compatible with bash extensions
- HTML: Semantic markup, dark theme CSS (GitHub-inspired)
- No type annotations unless they clarify complex signatures
