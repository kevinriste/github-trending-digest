# GitHub Trending Digest

**Live Site:** [www.kevinriste.com/github-trending-digest](https://www.kevinriste.com/github-trending-digest/)

A daily automated digest of the top 10 trending GitHub repositories, enhanced with AI-generated summaries and published to GitHub Pages.

---

## The Story: Building This in a Single Conversation

This entire project was built collaboratively between a human and Claude in one continuous conversation. Here's how it unfolded:

### The Initial Vision

It started with a simple idea: create an automated system that scrapes GitHub's trending page daily, generates AI summaries for each repository, and publishes the results to a GitHub Pages site. But there was a twist—the output needed to integrate with an existing **podcast-transcribe** pipeline that converts webpages into audio via text-to-speech.

### The Build

**First, the core script.** We built `trending_digest.py` to:
- Scrape the top trending repos from GitHub using BeautifulSoup
- Fetch each repository's README and clean it (removing badges, images, HTML cruft)
- Generate two-paragraph AI summaries using OpenAI's `gpt-5-mini`
- Create a GitHub-themed dark mode HTML page with all 10 repos
- Build a calendar index page showing all digest dates
- Commit and push to GitHub automatically
- Wait for GitHub Pages to return HTTP 200
- Send an email with the page link

**Then, the automation layer.** We borrowed the pattern from the podcast-transcribe project:
- `process.sh` - The main execution script with pyenv setup and timestamped logging
- `process-caller.sh` - A wrapper that captures failures and sends Gotify notifications
- `test-gh.sh` - A diagnostic script to verify `gh` CLI works in crontab context

**The crontab integration** worked on the first try. The `gh` CLI stores credentials in `~/.config/gh/hosts.yml`, so authentication "just works" even in non-interactive contexts.

### The Debugging Adventures

**The trafilatura problem.** The podcast-transcribe system uses trafilatura to extract main content from webpages. It was only extracting the first repository. The culprit? We had wrapped each repo in `<article class="repo">` tags, and trafilatura treats `<article>` as a content boundary.

The fix: Change `<article>` to `<section>` for individual repos, and wrap everything in a single `<article>` parent. The page looks identical, but now trafilatura sees all 10 repos as one piece of content.

**The title problem.** Trafilatura was extracting "GitHub Trending Digest" as the page title instead of including the date. The `<title>` tag had the date, but trafilatura prefers the `<h1>`.

The fix: Update the `<h1>` to include the date: "GitHub Trending Digest - February 02, 2026"

**The security review.** Before making the repo public, we noticed the shell scripts had hardcoded `/home/flog99` paths. Not a security vulnerability per se, but unnecessary exposure.

The fix: Replace hardcoded paths with `$HOME` and `$(dirname "$0")` for full portability. Now the scripts work from any location and don't leak directory structure.

### The Result

A fully automated pipeline that runs at 6am daily:
1. Scrapes GitHub trending
2. Generates AI summaries
3. Publishes to GitHub Pages
4. Waits for deployment
5. Emails the link
6. The email triggers podcast-transcribe, which converts the page to audio

All built in one conversation, including debugging the integration issues and making everything production-ready.

---

## How It Works

### Daily Flow

```
6:00 AM (cron)
    │
    ├── process-caller.sh (wrapper with error handling)
    │       │
    │       └── process.sh (pyenv setup, logging)
    │               │
    │               └── trending_digest.py
    │                       │
    │                       ├── Scrape GitHub Trending (top 10)
    │                       ├── Fetch READMEs
    │                       ├── Generate AI summaries (gpt-5-mini)
    │                       ├── Generate HTML pages
    │                       ├── git commit && git push
    │                       ├── Wait for GitHub Pages (HTTP 200)
    │                       └── Send email notification
    │
    └── On failure: Send Gotify notification with full log
```

### Project Structure

```
github-trending-digest/
├── trending_digest.py    # Main Python script
├── process.sh            # Crontab execution script
├── process-caller.sh     # Wrapper with Gotify error notifications
├── test-gh.sh            # GitHub CLI connection test
├── pyproject.toml        # Python dependencies (uv)
├── docs/                 # GitHub Pages output
│   ├── index.html        # Calendar index
│   ├── style.css         # Shared styles
│   ├── pages.json        # Date tracking
│   └── YYYY-MM-DD/       # Daily digest directories
│       └── index.html    # Daily digest page
├── README.md             # This file
├── AGENTS.md             # Instructions for AI agents
└── RECOMMENDATIONS.md    # Future improvement ideas
```

### Environment Variables

| Variable | Purpose |
|----------|---------|
| `OPENAI_API_KEY` | OpenAI API authentication |
| `GMAIL_PODCAST_ACCOUNT` | Gmail address for sending notifications |
| `GMAIL_PODCAST_ACCOUNT_APP_PASSWORD` | Gmail app password |
| `GOTIFY_SERVER` | Gotify server URL (for error notifications) |
| `GOTIFY_TOKEN` | Gotify application token |

### Dependencies

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) - Python package manager
- [pyenv](https://github.com/pyenv/pyenv) - Python version management
- [gh](https://cli.github.com/) - GitHub CLI (for authentication)
- BeautifulSoup4 - HTML parsing
- OpenAI Python SDK - AI summaries
- Requests - HTTP client

## Setup

1. Clone the repository
2. Install dependencies: `uv sync`
3. Set environment variables (see above)
4. Authenticate with GitHub: `gh auth login`
5. Add to crontab:
   ```
   0 6 * * * /path/to/process-caller.sh
   ```

## Integration with Podcast-Transcribe

This project is designed to work with the [podcast-transcribe](https://github.com/kevinriste/podcast-transcribe) system. The email sent after publishing triggers an IMAP parser that:

1. Detects the GitHub Trending Digest link
2. Fetches the page with trafilatura
3. Converts the content to audio via text-to-speech
4. Creates a podcast episode

The HTML structure uses `<section>` tags inside a single `<article>` to ensure trafilatura extracts all repository content as one cohesive piece.

## Future Improvements

See [RECOMMENDATIONS.md](RECOMMENDATIONS.md) for ideas on enhancing this project.

---

*Built collaboratively with Claude in February 2026*
