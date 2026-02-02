#!/usr/bin/env python3
"""Daily GitHub Trending Digest - scrapes top 5 trending repos and publishes to GitHub Pages."""

import logging
import os
import smtplib
import subprocess
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

GITHUB_TRENDING_URL = "https://github.com/trending?since=daily"
DOCS_DIR = Path(__file__).parent / "docs"
INDEX_FILE = DOCS_DIR / "index.html"
GITHUB_PAGES_URL = "https://kevinriste.github.io/github-trending-digest/"

gmail_user = os.getenv("GMAIL_PODCAST_ACCOUNT")
gmail_password = os.getenv("GMAIL_PODCAST_ACCOUNT_APP_PASSWORD")


def scrape_trending_repos(limit: int = 5) -> list[dict]:
    """Scrape the top trending repositories from GitHub."""
    logging.info("Fetching GitHub trending page")
    headers = {"User-Agent": "Mozilla/5.0 (compatible; TrendingDigest/1.0)"}
    response = requests.get(GITHUB_TRENDING_URL, headers=headers, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    repos = []

    for article in soup.select("article.Box-row")[:limit]:
        repo_link = article.select_one("h2 a")
        if not repo_link:
            continue

        repo_path = repo_link.get("href", "").strip("/")
        repo_url = f"https://github.com/{repo_path}"

        description_elem = article.select_one("p")
        description = description_elem.get_text(strip=True) if description_elem else "No description"

        language_elem = article.select_one("[itemprop='programmingLanguage']")
        language = language_elem.get_text(strip=True) if language_elem else "Unknown"

        stars_elem = article.select_one("a[href$='/stargazers']")
        stars = stars_elem.get_text(strip=True) if stars_elem else "N/A"

        today_stars_elem = article.select_one("span.d-inline-block.float-sm-right")
        today_stars = today_stars_elem.get_text(strip=True) if today_stars_elem else ""

        repos.append({
            "name": repo_path,
            "url": repo_url,
            "description": description,
            "language": language,
            "stars": stars,
            "today_stars": today_stars,
        })
        logging.info("Found repo: %s", repo_path)

    return repos


def fetch_readme_summary(repo_path: str) -> str:
    """Fetch the first few lines of a repo's README."""
    logging.info("Fetching README for %s", repo_path)
    readme_urls = [
        f"https://raw.githubusercontent.com/{repo_path}/main/README.md",
        f"https://raw.githubusercontent.com/{repo_path}/master/README.md",
        f"https://raw.githubusercontent.com/{repo_path}/main/readme.md",
        f"https://raw.githubusercontent.com/{repo_path}/master/readme.md",
    ]

    for url in readme_urls:
        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                lines = response.text.strip().split("\n")
                summary_lines = []
                char_count = 0
                for line in lines:
                    if char_count > 500:
                        break
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#") and not stripped.startswith("!"):
                        summary_lines.append(stripped)
                        char_count += len(stripped)
                if summary_lines:
                    return " ".join(summary_lines[:5])
        except requests.RequestException:
            continue

    return "README not available"


def generate_daily_entry(repos: list[dict], date: datetime) -> str:
    """Generate HTML for a single day's entry."""
    date_str = date.strftime("%Y-%m-%d")
    date_display = date.strftime("%B %d, %Y")

    html = f"""
    <section class="daily-entry" id="{date_str}">
        <h2>{date_display}</h2>
        <div class="repos">
"""

    for i, repo in enumerate(repos, 1):
        readme_summary = fetch_readme_summary(repo["name"])
        html += f"""
            <article class="repo">
                <h3>{i}. <a href="{repo['url']}" target="_blank">{repo['name']}</a></h3>
                <p class="description">{repo['description']}</p>
                <p class="meta">
                    <span class="language">{repo['language']}</span> |
                    <span class="stars">{repo['stars']} stars</span>
                    {f'| <span class="today">{repo["today_stars"]}</span>' if repo["today_stars"] else ''}
                </p>
                <details>
                    <summary>README Preview</summary>
                    <p class="readme-preview">{readme_summary}</p>
                </details>
            </article>
"""

    html += """
        </div>
    </section>
"""
    return html


def load_existing_entries() -> str:
    """Load existing entries from index.html if it exists."""
    if not INDEX_FILE.exists():
        return ""

    with open(INDEX_FILE) as f:
        content = f.read()

    start_marker = "<!-- ENTRIES_START -->"
    end_marker = "<!-- ENTRIES_END -->"
    start_idx = content.find(start_marker)
    end_idx = content.find(end_marker)

    if start_idx != -1 and end_idx != -1:
        return content[start_idx + len(start_marker):end_idx]
    return ""


def generate_full_html(entries_content: str) -> str:
    """Generate the full HTML page with all entries."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GitHub Trending Digest</title>
    <style>
        :root {{
            --bg-color: #0d1117;
            --card-bg: #161b22;
            --text-color: #c9d1d9;
            --link-color: #58a6ff;
            --border-color: #30363d;
            --accent-color: #238636;
        }}
        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-color);
            line-height: 1.6;
            padding: 2rem;
            max-width: 900px;
            margin: 0 auto;
        }}
        h1 {{
            color: #f0f6fc;
            margin-bottom: 0.5rem;
            font-size: 2rem;
        }}
        .subtitle {{
            color: #8b949e;
            margin-bottom: 2rem;
        }}
        .daily-entry {{
            margin-bottom: 3rem;
            padding-bottom: 2rem;
            border-bottom: 1px solid var(--border-color);
        }}
        .daily-entry h2 {{
            color: #f0f6fc;
            margin-bottom: 1.5rem;
            font-size: 1.5rem;
        }}
        .repo {{
            background-color: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 6px;
            padding: 1rem;
            margin-bottom: 1rem;
        }}
        .repo h3 {{
            font-size: 1.1rem;
            margin-bottom: 0.5rem;
        }}
        .repo h3 a {{
            color: var(--link-color);
            text-decoration: none;
        }}
        .repo h3 a:hover {{
            text-decoration: underline;
        }}
        .description {{
            color: #8b949e;
            margin-bottom: 0.5rem;
        }}
        .meta {{
            font-size: 0.85rem;
            color: #8b949e;
        }}
        .language {{
            color: var(--accent-color);
        }}
        details {{
            margin-top: 0.75rem;
        }}
        summary {{
            cursor: pointer;
            color: var(--link-color);
            font-size: 0.9rem;
        }}
        .readme-preview {{
            margin-top: 0.5rem;
            padding: 0.75rem;
            background-color: var(--bg-color);
            border-radius: 4px;
            font-size: 0.85rem;
            color: #8b949e;
        }}
        nav {{
            margin-bottom: 2rem;
        }}
        nav a {{
            color: var(--link-color);
            text-decoration: none;
            margin-right: 1rem;
        }}
    </style>
</head>
<body>
    <header>
        <h1>GitHub Trending Digest</h1>
        <p class="subtitle">Daily top 5 trending repositories</p>
    </header>
    <main>
        <!-- ENTRIES_START -->{entries_content}<!-- ENTRIES_END -->
    </main>
    <footer>
        <p style="margin-top: 2rem; color: #8b949e; font-size: 0.85rem;">
            Generated automatically. Data from <a href="https://github.com/trending">GitHub Trending</a>.
        </p>
    </footer>
</body>
</html>
"""


def save_html(html: str) -> None:
    """Save the HTML file to the docs directory."""
    DOCS_DIR.mkdir(exist_ok=True)
    with open(INDEX_FILE, "w") as f:
        f.write(html)
    logging.info("Saved HTML to %s", INDEX_FILE)


def git_commit_and_push() -> None:
    """Commit and push changes to GitHub."""
    repo_dir = Path(__file__).parent
    logging.info("Committing and pushing changes")

    subprocess.run(["git", "add", "docs/"], cwd=repo_dir, check=True)
    today = datetime.now().strftime("%Y-%m-%d")
    subprocess.run(
        ["git", "commit", "-m", f"Add trending digest for {today}"],
        cwd=repo_dir,
        check=True,
    )
    subprocess.run(["git", "push"], cwd=repo_dir, check=True)
    logging.info("Pushed to GitHub")


def send_email(to_address: str, subject: str, body: str) -> None:
    """Send an email using Gmail SMTP."""
    if not gmail_user or not gmail_password:
        logging.error("Gmail credentials not set in environment variables")
        return

    logging.info("Sending email to %s", to_address)
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = gmail_user
    msg["To"] = to_address

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_user, gmail_password)
        server.sendmail(gmail_user, to_address, msg.as_string())

    logging.info("Email sent successfully")


def main() -> None:
    """Main entry point."""
    today = datetime.now()
    date_str = today.strftime("%Y-%m-%d")

    existing_entries = load_existing_entries()
    if f'id="{date_str}"' in existing_entries:
        logging.info("Entry for %s already exists, skipping", date_str)
        return

    repos = scrape_trending_repos(limit=5)
    if not repos:
        logging.error("No trending repos found")
        return

    new_entry = generate_daily_entry(repos, today)
    all_entries = new_entry + existing_entries

    full_html = generate_full_html(all_entries)
    save_html(full_html)

    git_commit_and_push()

    send_email(
        to_address="pckltpw@gmail.com",
        subject="link",
        body=GITHUB_PAGES_URL,
    )

    logging.info("Done! View at %s", GITHUB_PAGES_URL)


if __name__ == "__main__":
    main()
