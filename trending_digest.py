#!/usr/bin/env python3
"""Daily GitHub Trending Digest - scrapes top 10 trending repos and publishes to GitHub Pages."""

import html
import json
import logging
import os
import re
import smtplib
import subprocess
import time
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from openai import OpenAI

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

GITHUB_TRENDING_URL = "https://github.com/trending?since=daily"
DOCS_DIR = Path(__file__).parent / "docs"
INDEX_FILE = DOCS_DIR / "index.html"
PAGES_DATA_FILE = DOCS_DIR / "pages.json"
GITHUB_PAGES_URL = "https://www.kevinriste.com/github-trending-digest/"
SUMMARY_MODEL = "gpt-5-mini"

gmail_user = os.getenv("GMAIL_PODCAST_ACCOUNT")
gmail_password = os.getenv("GMAIL_PODCAST_ACCOUNT_APP_PASSWORD")
_openai_client = None


def get_openai_client():
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI()
    return _openai_client


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


def clean_readme_content(readme_text: str) -> str:
    """Clean README content by removing images, badges, HTML, and other non-text elements."""
    lines = readme_text.split("\n")
    cleaned_lines = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # Skip image lines (markdown images)
        if stripped.startswith("!["):
            continue
        # Skip badge lines (usually contain shields.io or img.shields.io)
        if "shields.io" in stripped or "badge" in stripped.lower():
            continue
        # Skip HTML tags
        if stripped.startswith("<") and ">" in stripped:
            continue
        # Skip lines that are just links with no text
        if re.match(r"^\[.*\]\(https?://.*\)$", stripped):
            continue
        # Skip horizontal rules
        if re.match(r"^[-=_]{3,}$", stripped):
            continue
        # Skip table separators
        if re.match(r"^\|[-:| ]+\|$", stripped):
            continue
        # Remove inline images and badges
        stripped = re.sub(r"!\[.*?\]\(.*?\)", "", stripped)
        # Remove inline links but keep text: [text](url) -> text
        stripped = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", stripped)
        # Remove HTML tags
        stripped = re.sub(r"<[^>]+>", "", stripped)
        # Remove markdown emphasis but keep text
        stripped = re.sub(r"\*\*([^*]+)\*\*", r"\1", stripped)
        stripped = re.sub(r"\*([^*]+)\*", r"\1", stripped)
        stripped = re.sub(r"__([^_]+)__", r"\1", stripped)
        stripped = re.sub(r"_([^_]+)_", r"\1", stripped)
        # Remove code backticks
        stripped = re.sub(r"`([^`]+)`", r"\1", stripped)

        stripped = stripped.strip()
        if stripped and len(stripped) > 2:
            cleaned_lines.append(stripped)

    return "\n".join(cleaned_lines)


def fetch_readme(repo_path: str) -> str:
    """Fetch the README content for a repository."""
    logging.info("Fetching README for %s", repo_path)
    readme_urls = [
        f"https://raw.githubusercontent.com/{repo_path}/main/README.md",
        f"https://raw.githubusercontent.com/{repo_path}/master/README.md",
        f"https://raw.githubusercontent.com/{repo_path}/main/readme.md",
        f"https://raw.githubusercontent.com/{repo_path}/master/readme.md",
        f"https://raw.githubusercontent.com/{repo_path}/main/README.rst",
        f"https://raw.githubusercontent.com/{repo_path}/master/README.rst",
    ]

    for url in readme_urls:
        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                return response.text
        except requests.RequestException:
            continue

    return ""


def generate_ai_summary(repo_name: str, description: str, readme_content: str) -> str:
    """Generate a two-paragraph AI summary of the repository."""
    logging.info("Generating AI summary for %s", repo_name)

    cleaned_readme = clean_readme_content(readme_content)
    # Truncate to avoid token limits
    if len(cleaned_readme) > 8000:
        cleaned_readme = cleaned_readme[:8000] + "..."

    prompt = f"""Analyze this GitHub repository and provide a two-paragraph summary.

Repository: {repo_name}
Description: {description}

README content:
{cleaned_readme}

Write exactly two paragraphs:
1. First paragraph: Explain what this project does, its main features, and how it works technically.
2. Second paragraph: Discuss the potential value and use cases - who would benefit from this project and why it's trending.

Keep each paragraph concise (3-4 sentences). Write in a professional, informative tone."""

    try:
        client = get_openai_client()
        response = client.responses.create(
            model=SUMMARY_MODEL,
            input=prompt,
        )
        return response.output_text.strip()
    except Exception as exc:
        logging.exception("AI summary generation failed for %s: %s", repo_name, exc)
        return ""


def generate_daily_page(repos: list[dict], date: datetime) -> str:
    """Generate HTML for a daily digest page."""
    date_str = date.strftime("%Y-%m-%d")
    date_display = date.strftime("%B %d, %Y")

    repo_cards = ""
    for i, repo in enumerate(repos, 1):
        readme_content = fetch_readme(repo["name"])
        ai_summary = generate_ai_summary(repo["name"], repo["description"], readme_content)

        # Format AI summary into paragraphs
        summary_html = ""
        if ai_summary:
            paragraphs = ai_summary.split("\n\n")
            for p in paragraphs:
                p = p.strip()
                if p:
                    summary_html += f"<p>{html.escape(p)}</p>\n"
        else:
            summary_html = "<p><em>Summary not available.</em></p>"

        repo_cards += f"""
            <section class="repo">
                <h3>{i}. <a href="{repo['url']}" target="_blank">{html.escape(repo['name'])}</a></h3>
                <p class="description">{html.escape(repo['description'])}</p>
                <p class="meta">
                    <span class="language">{html.escape(repo['language'])}</span> |
                    <span class="stars">{html.escape(repo['stars'])} stars</span>
                    {f'| <span class="today">{html.escape(repo["today_stars"])}</span>' if repo["today_stars"] else ''}
                </p>
                <div class="ai-summary">
                    <h4>Analysis</h4>
                    {summary_html}
                </div>
            </section>
"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GitHub Trending - {date_display}</title>
    <link rel="stylesheet" href="../style.css">
</head>
<body>
    <header>
        <h1>GitHub Trending Digest - {date_display}</h1>
        <nav>
            <a href="../">&larr; Back to Calendar</a>
        </nav>
    </header>
    <main>
        <article>
            <div class="repos">
{repo_cards}
            </div>
        </article>
    </main>
    <footer>
        <p>Generated automatically. Data from <a href="https://github.com/trending">GitHub Trending</a>.</p>
    </footer>
</body>
</html>
"""


def load_pages_data() -> dict:
    """Load the pages data (dates with digest pages)."""
    if PAGES_DATA_FILE.exists():
        with open(PAGES_DATA_FILE) as f:
            return json.load(f)
    return {"pages": []}


def save_pages_data(data: dict) -> None:
    """Save the pages data."""
    with open(PAGES_DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)


def generate_index_page(pages_data: dict) -> str:
    """Generate the index page with a calendar GUI."""
    import calendar

    pages = pages_data.get("pages", [])
    pages_set = set(pages)

    today = datetime.now()

    # Find the range of months to display
    if pages:
        dates = [datetime.strptime(p, "%Y-%m-%d") for p in pages]
        min_date = min(dates)
        max_date = max(max(dates), today)
    else:
        min_date = today
        max_date = today

    # Generate calendars from most recent to oldest
    calendar_html = ""
    current = datetime(max_date.year, max_date.month, 1)
    end = datetime(min_date.year, min_date.month, 1)

    while current >= end:
        calendar_html += generate_month_calendar(current.year, current.month, pages_set)
        # Move to previous month
        if current.month == 1:
            current = datetime(current.year - 1, 12, 1)
        else:
            current = datetime(current.year, current.month - 1, 1)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GitHub Trending Digest - {today.strftime("%Y-%m-%d")}</title>
    <link rel="stylesheet" href="style.css">
</head>
<body>
    <header>
        <h1>GitHub Trending Digest</h1>
        <p class="subtitle">Daily top 10 trending repositories</p>
    </header>
    <main>
        <div class="calendar-container">
{calendar_html}
        </div>
    </main>
    <footer>
        <p>Generated automatically. Data from <a href="https://github.com/trending">GitHub Trending</a>.</p>
    </footer>
</body>
</html>
"""


def generate_month_calendar(year: int, month: int, pages_set: set) -> str:
    """Generate HTML for a single month's calendar."""
    import calendar

    cal = calendar.Calendar(firstweekday=6)  # Sunday first
    month_name = calendar.month_name[month]

    weeks_html = ""
    for week in cal.monthdayscalendar(year, month):
        days_html = ""
        for day in week:
            if day == 0:
                days_html += '<td class="empty"></td>'
            else:
                date_str = f"{year}-{month:02d}-{day:02d}"
                if date_str in pages_set:
                    days_html += f'<td class="has-page"><a href="{date_str}/">{day}</a></td>'
                else:
                    days_html += f'<td class="no-page">{day}</td>'
        weeks_html += f"<tr>{days_html}</tr>\n"

    return f"""
        <div class="month-calendar">
            <h3>{month_name} {year}</h3>
            <table>
                <thead>
                    <tr>
                        <th>Sun</th><th>Mon</th><th>Tue</th><th>Wed</th><th>Thu</th><th>Fri</th><th>Sat</th>
                    </tr>
                </thead>
                <tbody>
{weeks_html}
                </tbody>
            </table>
        </div>
"""


def generate_css() -> str:
    """Generate the shared CSS stylesheet."""
    return """:root {
    --bg-color: #0d1117;
    --card-bg: #161b22;
    --text-color: #c9d1d9;
    --link-color: #58a6ff;
    --border-color: #30363d;
    --accent-color: #238636;
    --highlight-bg: #1f6feb;
}
* {
    box-sizing: border-box;
    margin: 0;
    padding: 0;
}
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
    background-color: var(--bg-color);
    color: var(--text-color);
    line-height: 1.6;
    padding: 2rem;
    max-width: 900px;
    margin: 0 auto;
}
h1 {
    color: #f0f6fc;
    margin-bottom: 0.5rem;
    font-size: 2rem;
}
.subtitle {
    color: #8b949e;
    margin-bottom: 1rem;
}
nav {
    margin-bottom: 2rem;
}
nav a {
    color: var(--link-color);
    text-decoration: none;
}
nav a:hover {
    text-decoration: underline;
}
.repo {
    background-color: var(--card-bg);
    border: 1px solid var(--border-color);
    border-radius: 6px;
    padding: 1.25rem;
    margin-bottom: 1.25rem;
}
.repo h3 {
    font-size: 1.1rem;
    margin-bottom: 0.5rem;
}
.repo h3 a {
    color: var(--link-color);
    text-decoration: none;
}
.repo h3 a:hover {
    text-decoration: underline;
}
.description {
    color: #8b949e;
    margin-bottom: 0.5rem;
}
.meta {
    font-size: 0.85rem;
    color: #8b949e;
    margin-bottom: 1rem;
}
.language {
    color: var(--accent-color);
}
.ai-summary {
    margin-top: 1rem;
    padding-top: 1rem;
    border-top: 1px solid var(--border-color);
}
.ai-summary h4 {
    color: #f0f6fc;
    font-size: 0.95rem;
    margin-bottom: 0.75rem;
}
.ai-summary p {
    color: var(--text-color);
    margin-bottom: 0.75rem;
    font-size: 0.9rem;
}
.ai-summary p:last-child {
    margin-bottom: 0;
}
footer {
    margin-top: 2rem;
    color: #8b949e;
    font-size: 0.85rem;
}
footer a {
    color: var(--link-color);
}

/* Calendar styles */
.calendar-container {
    display: flex;
    flex-wrap: wrap;
    gap: 2rem;
    justify-content: center;
}
.month-calendar {
    background-color: var(--card-bg);
    border: 1px solid var(--border-color);
    border-radius: 6px;
    padding: 1rem;
    min-width: 280px;
}
.month-calendar h3 {
    color: #f0f6fc;
    text-align: center;
    margin-bottom: 1rem;
    font-size: 1.1rem;
}
.month-calendar table {
    width: 100%;
    border-collapse: collapse;
}
.month-calendar th {
    color: #8b949e;
    font-size: 0.75rem;
    font-weight: normal;
    padding: 0.5rem 0;
    text-align: center;
}
.month-calendar td {
    text-align: center;
    padding: 0.5rem;
    font-size: 0.9rem;
}
.month-calendar td.empty {
    background: transparent;
}
.month-calendar td.no-page {
    color: #484f58;
}
.month-calendar td.has-page {
    background-color: var(--highlight-bg);
    border-radius: 4px;
}
.month-calendar td.has-page a {
    color: #fff;
    text-decoration: none;
    display: block;
    font-weight: 500;
}
.month-calendar td.has-page:hover {
    background-color: #388bfd;
}
"""


def save_files(date: datetime, daily_html: str, index_html: str, css: str) -> None:
    """Save all generated files."""
    DOCS_DIR.mkdir(exist_ok=True)

    date_str = date.strftime("%Y-%m-%d")
    daily_dir = DOCS_DIR / date_str
    daily_dir.mkdir(exist_ok=True)
    daily_file = daily_dir / "index.html"

    with open(daily_file, "w") as f:
        f.write(daily_html)
    logging.info("Saved daily page to %s", daily_file)

    with open(INDEX_FILE, "w") as f:
        f.write(index_html)
    logging.info("Saved index to %s", INDEX_FILE)

    css_file = DOCS_DIR / "style.css"
    with open(css_file, "w") as f:
        f.write(css)
    logging.info("Saved CSS to %s", css_file)


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


def wait_for_page_live(url: str, max_attempts: int = 30, delay: int = 10) -> bool:
    """Wait for a page to return HTTP 200."""
    logging.info("Waiting for page to be live: %s", url)
    for attempt in range(max_attempts):
        try:
            response = requests.head(url, timeout=10, allow_redirects=True)
            if response.status_code == 200:
                logging.info("Page is live after %d attempts", attempt + 1)
                return True
        except requests.RequestException as e:
            logging.debug("Attempt %d failed: %s", attempt + 1, e)
        if attempt < max_attempts - 1:
            logging.info("Page not ready, waiting %ds (attempt %d/%d)", delay, attempt + 1, max_attempts)
            time.sleep(delay)
    logging.error("Page did not become live after %d attempts", max_attempts)
    return False


def main() -> None:
    """Main entry point."""
    today = datetime.now()
    date_str = today.strftime("%Y-%m-%d")

    # Check if today's page already exists
    daily_dir = DOCS_DIR / date_str
    if (daily_dir / "index.html").exists():
        logging.info("Page for %s already exists, skipping", date_str)
        return

    repos = scrape_trending_repos(limit=10)
    if not repos:
        logging.error("No trending repos found")
        return

    # Generate the daily page
    daily_html = generate_daily_page(repos, today)

    # Update pages data
    pages_data = load_pages_data()
    if date_str not in pages_data["pages"]:
        pages_data["pages"].append(date_str)
        pages_data["pages"].sort(reverse=True)
    save_pages_data(pages_data)

    # Generate index with calendar
    index_html = generate_index_page(pages_data)

    # Generate CSS
    css = generate_css()

    # Save all files
    save_files(today, daily_html, index_html, css)

    git_commit_and_push()

    # Wait for page to be live before sending email
    page_url = f"{GITHUB_PAGES_URL}{date_str}/"
    if wait_for_page_live(page_url):
        send_email(
            to_address="pckltpw@gmail.com",
            subject="link",
            body=page_url,
        )
    else:
        logging.error("Skipping email - page not live")

    logging.info("Done! View at %s", page_url)


if __name__ == "__main__":
    main()
