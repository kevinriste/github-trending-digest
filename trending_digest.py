#!/usr/bin/env python3
"""Daily GitHub Trending + Hacker News digest with Postgres persistence."""

import argparse
import calendar
import hashlib
import html
import json
import logging
import os
import re
import smtplib
import subprocess
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import urlparse

import psycopg
import requests
from bs4 import BeautifulSoup
from openai import OpenAI
from psycopg.rows import dict_row

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

GITHUB_TRENDING_BASE_URL = "https://github.com/trending?since={period}"
GITHUB_PERIODS = ("daily", "weekly", "monthly")
GITHUB_FETCH_LIMITS = {"daily": 10, "weekly": 25, "monthly": 100}
HN_TOPSTORIES_URL = "https://hacker-news.firebaseio.com/v0/topstories.json"
HN_ITEM_URL_TEMPLATE = "https://hacker-news.firebaseio.com/v0/item/{item_id}.json"
HN_DISCUSSION_URL_TEMPLATE = "https://news.ycombinator.com/item?id={item_id}"

DOCS_DIR = Path(__file__).parent / "docs"
HN_DOCS_DIR = DOCS_DIR / "hn"
INDEX_FILE = DOCS_DIR / "index.html"
HN_INDEX_FILE = HN_DOCS_DIR / "index.html"
PAGES_DATA_FILE = DOCS_DIR / "pages.json"
HN_PAGES_DATA_FILE = HN_DOCS_DIR / "pages.json"
STYLE_FILE = DOCS_DIR / "style.css"
GITHUB_PAGES_URL = "https://www.kevinriste.com/github-trending-digest/"

SUMMARY_MODEL = "gpt-5-mini"
GH_SUMMARY_PROMPT_VERSION = "gh_v2"
HN_SUMMARY_PROMPT_VERSION = "hn_v1"
HN_COMMENT_ANALYSIS_PROMPT_VERSION = "hn_comments_v1"
SUMMARY_REFRESH_DAYS = 60
RUN_LOCK_KEY = 348_112_907
READ_DAYS_KEY_GH = "gtd:read_days:gh:v1"
READ_DAYS_KEY_HN = "gtd:read_days:hn:v1"

DEFAULT_DATABASE_URL = "postgresql://trending_digest:trending_digest@localhost:5433/trending_digest"

gmail_user = os.getenv("GMAIL_PODCAST_ACCOUNT")
gmail_password = os.getenv("GMAIL_PODCAST_ACCOUNT_APP_PASSWORD")
email_to_address = os.getenv("DIGEST_EMAIL_TO", "kevinbobriste@gmail.com")
_openai_client = None


def parse_args() -> argparse.Namespace:
    """Parse command-line flags."""
    parser = argparse.ArgumentParser(description="Generate GitHub Trending and Hacker News digests.")
    parser.add_argument(
        "--regenerate-only",
        action="store_true",
        help="Regenerate pages from DB content only (no scrape, push, or email).",
    )
    return parser.parse_args()


def get_int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        logging.warning("Invalid integer for %s=%s, using default=%d", name, value, default)
        return default


GH_DAILY_RENDER_LIMIT = get_int_env("GH_DAILY_RENDER_LIMIT", 0)  # 0 means all fetched
HN_DAILY_RENDER_LIMIT = get_int_env("HN_DAILY_RENDER_LIMIT", 10)
HN_MAX_ITEMS = get_int_env("HN_MAX_ITEMS", 0)  # 0 means all IDs from API
HN_FETCH_WORKERS = max(1, get_int_env("HN_FETCH_WORKERS", 20))
HN_COMMENT_SAMPLE_SIZE = get_int_env("HN_COMMENT_SAMPLE_SIZE", 16)
HN_COMMENT_TRAVERSAL_MAX_NODES = get_int_env("HN_COMMENT_TRAVERSAL_MAX_NODES", 300)
HN_COMMENT_TRAVERSAL_MAX_DEPTH = get_int_env("HN_COMMENT_TRAVERSAL_MAX_DEPTH", 6)
HN_COMMENT_MAX_PER_BRANCH = get_int_env("HN_COMMENT_MAX_PER_BRANCH", 4)
HN_COMMENT_MIN_TEXT_LEN = get_int_env("HN_COMMENT_MIN_TEXT_LEN", 40)


def normalize_text(value: str) -> str:
    """Normalize whitespace in text."""
    if not value:
        return ""
    return " ".join(value.split())


def format_date_display(day: date) -> str:
    """Format a date for page titles."""
    return day.strftime("%B %d, %Y")


def extract_domain(url: str) -> str:
    """Extract hostname domain from URL."""
    if not url:
        return ""
    parsed = urlparse(url)
    host = parsed.netloc.lower().strip()
    if host.startswith("www."):
        host = host[4:]
    return host


def summary_is_fresh(generated_at: datetime, target_day: date) -> bool:
    """Return True if summary age is under the refresh threshold."""
    age_days = (target_day - generated_at.date()).days
    return age_days < SUMMARY_REFRESH_DAYS


def get_openai_client():
    """Lazy-init OpenAI client."""
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI()
    return _openai_client


def get_db_connection() -> psycopg.Connection:
    """Connect to Postgres."""
    database_url = os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
    conn = psycopg.connect(database_url)
    conn.autocommit = True
    return conn


def init_db(conn: psycopg.Connection) -> None:
    """Create schema if needed."""
    statements = [
        """
        CREATE TABLE IF NOT EXISTS app_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS gh_repos (
            id BIGSERIAL PRIMARY KEY,
            full_name TEXT NOT NULL UNIQUE,
            url TEXT NOT NULL,
            description TEXT,
            language TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS gh_runs (
            id BIGSERIAL PRIMARY KEY,
            run_date DATE NOT NULL,
            period TEXT NOT NULL CHECK (period IN ('daily', 'weekly', 'monthly')),
            source TEXT NOT NULL DEFAULT 'live',
            fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            repo_count INTEGER NOT NULL DEFAULT 0,
            UNIQUE (run_date, period)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS gh_entries (
            id BIGSERIAL PRIMARY KEY,
            run_id BIGINT NOT NULL REFERENCES gh_runs(id) ON DELETE CASCADE,
            repo_id BIGINT NOT NULL REFERENCES gh_repos(id) ON DELETE CASCADE,
            rank INTEGER NOT NULL,
            stars_text TEXT,
            period_stars_text TEXT,
            description TEXT,
            language TEXT,
            UNIQUE (run_id, repo_id),
            UNIQUE (run_id, rank)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS gh_summaries (
            id BIGSERIAL PRIMARY KEY,
            repo_id BIGINT NOT NULL REFERENCES gh_repos(id) ON DELETE CASCADE,
            model TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            summary_text TEXT NOT NULL,
            readme_hash TEXT,
            generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS hn_items (
            id BIGINT PRIMARY KEY,
            title TEXT NOT NULL,
            url TEXT,
            author TEXT,
            score INTEGER,
            comment_count INTEGER,
            item_time TIMESTAMPTZ,
            text TEXT,
            item_type TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS hn_runs (
            id BIGSERIAL PRIMARY KEY,
            run_date DATE NOT NULL,
            feed TEXT NOT NULL DEFAULT 'topstories',
            source TEXT NOT NULL DEFAULT 'live',
            fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            item_count INTEGER NOT NULL DEFAULT 0,
            UNIQUE (run_date, feed)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS hn_entries (
            id BIGSERIAL PRIMARY KEY,
            run_id BIGINT NOT NULL REFERENCES hn_runs(id) ON DELETE CASCADE,
            item_id BIGINT NOT NULL REFERENCES hn_items(id) ON DELETE CASCADE,
            rank INTEGER NOT NULL,
            UNIQUE (run_id, item_id),
            UNIQUE (run_id, rank)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS hn_summaries (
            id BIGSERIAL PRIMARY KEY,
            item_id BIGINT NOT NULL REFERENCES hn_items(id) ON DELETE CASCADE,
            model TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            summary_text TEXT NOT NULL,
            generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS hn_comment_analyses (
            id BIGSERIAL PRIMARY KEY,
            item_id BIGINT NOT NULL REFERENCES hn_items(id) ON DELETE CASCADE,
            model TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            sample_size INTEGER NOT NULL,
            sampled_comments INTEGER NOT NULL,
            total_comments INTEGER NOT NULL,
            analysis_text TEXT NOT NULL,
            generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_gh_entries_repo_id ON gh_entries(repo_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_gh_runs_period_date ON gh_runs(period, run_date)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_gh_summaries_repo_generated ON gh_summaries(repo_id, generated_at DESC)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_hn_entries_item_id ON hn_entries(item_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_hn_runs_feed_date ON hn_runs(feed, run_date)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_hn_summaries_item_generated ON hn_summaries(item_id, generated_at DESC)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_hn_comment_analyses_item_generated
            ON hn_comment_analyses(item_id, generated_at DESC)
        """,
    ]

    with conn.cursor() as cur:
        for statement in statements:
            cur.execute(statement)


def get_app_meta(conn: psycopg.Connection, key: str) -> str | None:
    """Read metadata value by key."""
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM app_meta WHERE key = %s", (key,))
        row = cur.fetchone()
    if not row:
        return None
    return row[0]


def set_app_meta(conn: psycopg.Connection, key: str, value: str) -> None:
    """Upsert metadata value."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO app_meta (key, value)
            VALUES (%s, %s)
            ON CONFLICT (key)
            DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
            """,
            (key, value),
        )


def acquire_run_lock(conn: psycopg.Connection) -> bool:
    """Prevent overlapping runs."""
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (RUN_LOCK_KEY,))
        locked = bool(cur.fetchone()[0])

    if not locked:
        logging.warning("Another digest run is already active. Exiting.")
    return locked


def release_run_lock(conn: psycopg.Connection) -> None:
    """Release advisory lock."""
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(%s)", (RUN_LOCK_KEY,))


def scrape_trending_repos(period: str) -> list[dict]:
    """Scrape GitHub Trending repositories for a period."""
    if period not in GITHUB_PERIODS:
        raise ValueError(f"Unsupported GitHub period: {period}")

    logging.info("Fetching GitHub trending page (%s)", period)
    headers = {"User-Agent": "Mozilla/5.0 (compatible; TrendingDigest/2.0)"}
    response = requests.get(GITHUB_TRENDING_BASE_URL.format(period=period), headers=headers, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    repos = []
    limit = GITHUB_FETCH_LIMITS.get(period, 10)

    for rank, article in enumerate(soup.select("article.Box-row")[:limit], start=1):
        repo_link = article.select_one("h2 a")
        if not repo_link:
            continue

        repo_path = normalize_text(repo_link.get("href", "")).strip("/")
        if not repo_path:
            continue

        description_elem = article.select_one("p")
        language_elem = article.select_one("[itemprop='programmingLanguage']")
        stars_elem = article.select_one("a[href$='/stargazers']")
        period_stars_elem = article.select_one("span.d-inline-block.float-sm-right")

        description = normalize_text(description_elem.get_text(" ", strip=True)) if description_elem else "No description"
        language = normalize_text(language_elem.get_text(" ", strip=True)) if language_elem else "Unknown"
        stars = normalize_text(stars_elem.get_text(" ", strip=True)) if stars_elem else "N/A"
        period_stars = normalize_text(period_stars_elem.get_text(" ", strip=True)) if period_stars_elem else ""

        repos.append(
            {
                "rank": rank,
                "name": repo_path,
                "url": f"https://github.com/{repo_path}",
                "description": description,
                "language": language,
                "stars": stars,
                "period_stars": period_stars,
            }
        )

    logging.info("GitHub %s scrape returned %d repositories", period, len(repos))
    return repos


def clean_readme_content(readme_text: str) -> str:
    """Strip common non-text README content before summarization."""
    lines = readme_text.split("\n")
    cleaned_lines = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("!["):
            continue
        if "shields.io" in stripped or "badge" in stripped.lower():
            continue
        if stripped.startswith("<") and ">" in stripped:
            continue
        if re.match(r"^\[.*\]\(https?://.*\)$", stripped):
            continue
        if re.match(r"^[-=_]{3,}$", stripped):
            continue
        if re.match(r"^\|[-:| ]+\|$", stripped):
            continue

        stripped = re.sub(r"!\[.*?\]\(.*?\)", "", stripped)
        stripped = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", stripped)
        stripped = re.sub(r"<[^>]+>", "", stripped)
        stripped = re.sub(r"\*\*([^*]+)\*\*", r"\1", stripped)
        stripped = re.sub(r"\*([^*]+)\*", r"\1", stripped)
        stripped = re.sub(r"__([^_]+)__", r"\1", stripped)
        stripped = re.sub(r"_([^_]+)_", r"\1", stripped)
        stripped = re.sub(r"`([^`]+)`", r"\1", stripped)

        stripped = normalize_text(stripped)
        if stripped and len(stripped) > 2:
            cleaned_lines.append(stripped)

    return "\n".join(cleaned_lines)


def fetch_readme(repo_path: str) -> str:
    """Fetch README content for a repository from likely default branches."""
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
            response = requests.get(url, timeout=12)
            if response.status_code == 200:
                return response.text
        except requests.RequestException:
            continue
    return ""


def generate_gh_summary(repo_name: str, description: str, readme_content: str) -> str:
    """Generate two-paragraph repository summary."""
    cleaned_readme = clean_readme_content(readme_content)
    if len(cleaned_readme) > 8000:
        cleaned_readme = cleaned_readme[:8000] + "..."

    prompt = f"""Analyze this GitHub repository and provide a two-paragraph summary.

Repository: {repo_name}
Description: {description}

README content:
{cleaned_readme}

Write exactly two paragraphs:
1. First paragraph: Explain what this project does, key features, and how it works technically.
2. Second paragraph: Explain who would benefit from this project and why it is trending.

Keep each paragraph concise (3-4 sentences). Use a professional, informative tone."""

    try:
        client = get_openai_client()
        response = client.responses.create(model=SUMMARY_MODEL, input=prompt)
        return response.output_text.strip()
    except Exception as exc:
        logging.exception("GitHub summary generation failed for %s: %s", repo_name, exc)
        return ""


def generate_hn_summary(item: dict) -> str:
    """Generate two-paragraph story summary for Hacker News item."""
    raw_text = item.get("text") or ""
    cleaned_text = normalize_text(BeautifulSoup(raw_text, "html.parser").get_text(" ", strip=True)) if raw_text else ""
    if len(cleaned_text) > 5000:
        cleaned_text = cleaned_text[:5000] + "..."

    title = item.get("title", "")
    url = item.get("url", "")
    author = item.get("author", "")
    score = item.get("score", 0)
    comments = item.get("comment_count", 0)

    prompt = f"""Summarize this Hacker News story in exactly two paragraphs.

Title: {title}
Source URL: {url or 'N/A'}
Author: {author}
Points: {score}
Comments: {comments}
Story text (if available):
{cleaned_text or 'N/A'}

Write exactly two paragraphs:
1. First paragraph: Explain what the story appears to be about and the key technical/business context.
2. Second paragraph: Explain why Hacker News readers might find it interesting or important.

Keep each paragraph concise (3-4 sentences) and avoid hype."""

    try:
        client = get_openai_client()
        response = client.responses.create(model=SUMMARY_MODEL, input=prompt)
        return response.output_text.strip()
    except Exception as exc:
        logging.exception("Hacker News summary generation failed for item %s: %s", item.get("item_id"), exc)
        return ""


def store_gh_period_run(conn: psycopg.Connection, run_day: date, period: str, repos: list[dict], source: str = "live") -> int:
    """Upsert one GitHub period run and replace its entries."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO gh_runs (run_date, period, source, repo_count)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (run_date, period)
            DO UPDATE SET source = EXCLUDED.source, fetched_at = NOW(), repo_count = EXCLUDED.repo_count
            RETURNING id
            """,
            (run_day, period, source, len(repos)),
        )
        run_id = cur.fetchone()[0]

        cur.execute("DELETE FROM gh_entries WHERE run_id = %s", (run_id,))

        for fallback_rank, repo in enumerate(repos, start=1):
            rank = int(repo.get("rank") or fallback_rank)
            name = normalize_text(repo.get("name") or "")
            if not name:
                continue

            url = normalize_text(repo.get("url") or f"https://github.com/{name}")
            description = normalize_text(repo.get("description") or "No description")
            language = normalize_text(repo.get("language") or "Unknown")
            stars = normalize_text(repo.get("stars") or "N/A")
            period_stars = normalize_text(repo.get("period_stars") or "")

            cur.execute(
                """
                INSERT INTO gh_repos (full_name, url, description, language)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (full_name)
                DO UPDATE SET
                    url = EXCLUDED.url,
                    description = CASE
                        WHEN EXCLUDED.description <> 'No description' THEN EXCLUDED.description
                        ELSE gh_repos.description
                    END,
                    language = CASE
                        WHEN EXCLUDED.language <> 'Unknown' THEN EXCLUDED.language
                        ELSE gh_repos.language
                    END,
                    updated_at = NOW()
                RETURNING id
                """,
                (name, url, description, language),
            )
            repo_id = cur.fetchone()[0]
            repo["repo_id"] = repo_id

            cur.execute(
                """
                INSERT INTO gh_entries
                    (run_id, repo_id, rank, stars_text, period_stars_text, description, language)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (run_id, repo_id, rank, stars, period_stars, description, language),
            )

    logging.info("Stored GitHub %s run for %s with %d repos", period, run_day.isoformat(), len(repos))
    return run_id


def store_hn_run(conn: psycopg.Connection, run_day: date, items: list[dict], source: str = "live") -> int:
    """Upsert one Hacker News run and replace its entries."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO hn_runs (run_date, feed, source, item_count)
            VALUES (%s, 'topstories', %s, %s)
            ON CONFLICT (run_date, feed)
            DO UPDATE SET source = EXCLUDED.source, fetched_at = NOW(), item_count = EXCLUDED.item_count
            RETURNING id
            """,
            (run_day, source, len(items)),
        )
        run_id = cur.fetchone()[0]

        cur.execute("DELETE FROM hn_entries WHERE run_id = %s", (run_id,))

        for fallback_rank, item in enumerate(items, start=1):
            rank = int(item.get("rank") or fallback_rank)
            item_id = int(item.get("item_id"))
            item_time_unix = item.get("item_time")
            item_time = (
                datetime.fromtimestamp(item_time_unix, tz=timezone.utc)
                if isinstance(item_time_unix, (int, float)) and item_time_unix > 0
                else None
            )

            cur.execute(
                """
                INSERT INTO hn_items
                    (id, title, url, author, score, comment_count, item_time, text, item_type)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id)
                DO UPDATE SET
                    title = EXCLUDED.title,
                    url = EXCLUDED.url,
                    author = EXCLUDED.author,
                    score = EXCLUDED.score,
                    comment_count = EXCLUDED.comment_count,
                    item_time = EXCLUDED.item_time,
                    text = EXCLUDED.text,
                    item_type = EXCLUDED.item_type,
                    updated_at = NOW()
                """,
                (
                    item_id,
                    normalize_text(item.get("title") or "Untitled"),
                    normalize_text(item.get("url") or ""),
                    normalize_text(item.get("author") or "unknown"),
                    int(item.get("score") or 0),
                    int(item.get("comment_count") or 0),
                    item_time,
                    item.get("text") or "",
                    normalize_text(item.get("item_type") or "story"),
                ),
            )

            cur.execute(
                """
                INSERT INTO hn_entries (run_id, item_id, rank)
                VALUES (%s, %s, %s)
                """,
                (run_id, item_id, rank),
            )

    logging.info("Stored Hacker News run for %s with %d stories", run_day.isoformat(), len(items))
    return run_id


def get_latest_gh_summary(conn: psycopg.Connection, repo_id: int) -> dict | None:
    """Fetch latest GitHub summary for a repo."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT summary_text, generated_at
            FROM gh_summaries
            WHERE repo_id = %s AND model = %s AND prompt_version = %s
            ORDER BY generated_at DESC
            LIMIT 1
            """,
            (repo_id, SUMMARY_MODEL, GH_SUMMARY_PROMPT_VERSION),
        )
        return cur.fetchone()


def cache_gh_summary(
    conn: psycopg.Connection,
    repo_id: int,
    summary_text: str,
    readme_hash: str,
    generated_at: datetime | None = None,
) -> None:
    """Insert GitHub summary row."""
    stamp = generated_at or datetime.now(tz=timezone.utc)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO gh_summaries (repo_id, model, prompt_version, summary_text, readme_hash, generated_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (repo_id, SUMMARY_MODEL, GH_SUMMARY_PROMPT_VERSION, summary_text, readme_hash, stamp),
        )


def cache_gh_summary_if_missing(conn: psycopg.Connection, repo_id: int, summary_text: str, summary_day: date) -> None:
    """Insert one backfilled summary per repo/day if absent."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1
            FROM gh_summaries
            WHERE repo_id = %s
              AND model = %s
              AND prompt_version = %s
              AND DATE(generated_at) = %s
            LIMIT 1
            """,
            (repo_id, SUMMARY_MODEL, GH_SUMMARY_PROMPT_VERSION, summary_day),
        )
        if cur.fetchone():
            return

    stamp = datetime.combine(summary_day, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=12)
    cache_gh_summary(conn, repo_id, summary_text, readme_hash="", generated_at=stamp)


def get_or_generate_gh_summary(conn: psycopg.Connection, repo: dict, run_day: date) -> str:
    """Return cached GitHub summary or generate a fresh one."""
    repo_id = int(repo["repo_id"])
    latest = get_latest_gh_summary(conn, repo_id)
    if latest and summary_is_fresh(latest["generated_at"], run_day):
        return latest["summary_text"]

    readme = fetch_readme(repo["name"])
    summary = generate_gh_summary(repo["name"], repo["description"], readme)
    if summary:
        readme_hash = hashlib.sha256(readme.encode("utf-8", errors="ignore")).hexdigest() if readme else ""
        cache_gh_summary(conn, repo_id, summary, readme_hash)
        return summary

    if latest:
        return latest["summary_text"]
    return ""


def get_latest_hn_summary(conn: psycopg.Connection, item_id: int) -> dict | None:
    """Fetch latest Hacker News summary for an item."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT summary_text, generated_at
            FROM hn_summaries
            WHERE item_id = %s AND model = %s AND prompt_version = %s
            ORDER BY generated_at DESC
            LIMIT 1
            """,
            (item_id, SUMMARY_MODEL, HN_SUMMARY_PROMPT_VERSION),
        )
        return cur.fetchone()


def cache_hn_summary(conn: psycopg.Connection, item_id: int, summary_text: str) -> None:
    """Insert Hacker News summary row."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO hn_summaries (item_id, model, prompt_version, summary_text, generated_at)
            VALUES (%s, %s, %s, %s, NOW())
            """,
            (item_id, SUMMARY_MODEL, HN_SUMMARY_PROMPT_VERSION, summary_text),
        )


def get_or_generate_hn_summary(conn: psycopg.Connection, item: dict, run_day: date) -> str:
    """Return cached Hacker News summary or generate a fresh one."""
    item_id = int(item["item_id"])
    latest = get_latest_hn_summary(conn, item_id)
    if latest and summary_is_fresh(latest["generated_at"], run_day):
        return latest["summary_text"]

    summary = generate_hn_summary(item)
    if summary:
        cache_hn_summary(conn, item_id, summary)
        return summary

    if latest:
        return latest["summary_text"]
    return ""


def get_latest_hn_comment_analysis(conn: psycopg.Connection, item_id: int) -> dict | None:
    """Fetch latest comment analysis for a Hacker News item."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT analysis_text, sampled_comments, total_comments, generated_at
            FROM hn_comment_analyses
            WHERE item_id = %s
              AND model = %s
              AND prompt_version = %s
              AND sample_size = %s
            ORDER BY generated_at DESC
            LIMIT 1
            """,
            (item_id, SUMMARY_MODEL, HN_COMMENT_ANALYSIS_PROMPT_VERSION, HN_COMMENT_SAMPLE_SIZE),
        )
        return cur.fetchone()


def cache_hn_comment_analysis(
    conn: psycopg.Connection,
    item_id: int,
    analysis_text: str,
    sampled_comments: int,
    total_comments: int,
) -> None:
    """Insert Hacker News comment analysis row."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO hn_comment_analyses
                (item_id, model, prompt_version, sample_size, sampled_comments, total_comments, analysis_text, generated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            """,
            (
                item_id,
                SUMMARY_MODEL,
                HN_COMMENT_ANALYSIS_PROMPT_VERSION,
                HN_COMMENT_SAMPLE_SIZE,
                sampled_comments,
                total_comments,
                analysis_text,
            ),
        )


def generate_hn_comment_analysis(item: dict, sampled_comments: list[dict], total_comments: int) -> str:
    """Generate four bullet points from sampled Hacker News comments."""
    comment_block = "\n\n".join(
        (
            f"[{idx}] depth={comment['depth']} top_thread={comment['root_pos']} "
            f"by={comment['by'] or 'unknown'}: {comment['text']}"
        )
        for idx, comment in enumerate(sampled_comments, start=1)
    )

    prompt = f"""Analyze this Hacker News discussion sample and provide exactly 4 concise bullet points.

Story title: {item.get("title", "")}
Story URL: {item.get("url") or item.get("discussion_url") or "N/A"}
Total comments in thread: {total_comments}
Sample size: {len(sampled_comments)}

Comment sample:
{comment_block}

Return exactly 4 bullet points:
- Bullet 1: Core consensus or dominant viewpoint.
- Bullet 2: Strongest disagreement or competing view.
- Bullet 3: Practical technical takeaway.
- Bullet 4: Caveat about sample bias/coverage.

Rules:
- One sentence per bullet.
- 18-35 words per bullet.
- No hype or marketing language.
- Do not quote usernames.
"""

    try:
        client = get_openai_client()
        response = client.responses.create(model=SUMMARY_MODEL, input=prompt)
        return response.output_text.strip()
    except Exception as exc:
        logging.exception("HN comment analysis generation failed for item %s: %s", item.get("item_id"), exc)
        return ""


def get_or_generate_hn_comment_analysis(conn: psycopg.Connection, item: dict, run_day: date) -> dict | None:
    """Return cached comment analysis or generate a fresh one for an HN item."""
    item_id = int(item["item_id"])
    latest = get_latest_hn_comment_analysis(conn, item_id)
    if latest and summary_is_fresh(latest["generated_at"], run_day):
        return {
            "analysis_text": latest["analysis_text"],
            "sampled_comments": int(latest["sampled_comments"]),
            "total_comments": int(latest["total_comments"]),
        }

    total_comments, nodes = build_hn_comment_nodes(item_id, int(item.get("comment_count") or 0))
    sampled = select_hn_comment_sample(nodes)
    if not sampled:
        return None

    analysis_text = generate_hn_comment_analysis(item, sampled, total_comments)
    if not analysis_text:
        if latest:
            return {
                "analysis_text": latest["analysis_text"],
                "sampled_comments": int(latest["sampled_comments"]),
                "total_comments": int(latest["total_comments"]),
            }
        return None

    cache_hn_comment_analysis(
        conn=conn,
        item_id=item_id,
        analysis_text=analysis_text,
        sampled_comments=len(sampled),
        total_comments=total_comments,
    )
    return {
        "analysis_text": analysis_text,
        "sampled_comments": len(sampled),
        "total_comments": total_comments,
    }


def get_gh_history_stats(conn: psycopg.Connection, repo_id: int, up_to_day: date) -> tuple[date | None, int, bool]:
    """Get earliest appearance, consecutive daily streak, and seen-before flag."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT gr.run_date
            FROM gh_entries ge
            JOIN gh_runs gr ON ge.run_id = gr.id
            WHERE ge.repo_id = %s
              AND gr.period = 'daily'
              AND gr.run_date <= %s
            ORDER BY gr.run_date
            """,
            (repo_id, up_to_day),
        )
        rows = cur.fetchall()

    if not rows:
        return None, 0, False

    dates = [row["run_date"] for row in rows]
    earliest = dates[0]
    seen_before = any(day < up_to_day for day in dates)

    streak = 0
    day_cursor = up_to_day
    date_set = set(dates)
    while day_cursor in date_set:
        streak += 1
        day_cursor -= timedelta(days=1)

    return earliest, streak, seen_before


def get_hn_history_stats(conn: psycopg.Connection, item_id: int, up_to_day: date) -> tuple[date | None, int, bool]:
    """Get earliest appearance, consecutive daily streak, and seen-before flag for HN item."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT hr.run_date
            FROM hn_entries he
            JOIN hn_runs hr ON he.run_id = hr.id
            WHERE he.item_id = %s
              AND hr.feed = 'topstories'
              AND hr.run_date <= %s
            ORDER BY hr.run_date
            """,
            (item_id, up_to_day),
        )
        rows = cur.fetchall()

    if not rows:
        return None, 0, False

    dates = [row["run_date"] for row in rows]
    earliest = dates[0]
    seen_before = any(day < up_to_day for day in dates)

    streak = 0
    day_cursor = up_to_day
    date_set = set(dates)
    while day_cursor in date_set:
        streak += 1
        day_cursor -= timedelta(days=1)

    return earliest, streak, seen_before


def list_gh_daily_entries(conn: psycopg.Connection, run_day: date) -> list[dict]:
    """List GitHub daily entries for one day."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT
                ge.rank,
                ge.repo_id,
                r.full_name AS name,
                r.url,
                COALESCE(ge.description, r.description, 'No description') AS description,
                COALESCE(ge.language, r.language, 'Unknown') AS language,
                COALESCE(ge.stars_text, 'N/A') AS stars,
                COALESCE(ge.period_stars_text, '') AS period_stars
            FROM gh_entries ge
            JOIN gh_runs gr ON ge.run_id = gr.id
            JOIN gh_repos r ON ge.repo_id = r.id
            WHERE gr.run_date = %s AND gr.period = 'daily'
            ORDER BY ge.rank
            """,
            (run_day,),
        )
        rows = [dict(row) for row in cur.fetchall()]

    if GH_DAILY_RENDER_LIMIT > 0:
        return rows[:GH_DAILY_RENDER_LIMIT]
    return rows


def list_hn_daily_entries(conn: psycopg.Connection, run_day: date) -> list[dict]:
    """List Hacker News daily entries for one day."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT
                he.rank,
                hi.id AS item_id,
                hi.title,
                hi.url,
                hi.author,
                hi.score,
                hi.comment_count,
                hi.item_time,
                hi.text
            FROM hn_entries he
            JOIN hn_runs hr ON he.run_id = hr.id
            JOIN hn_items hi ON he.item_id = hi.id
            WHERE hr.run_date = %s AND hr.feed = 'topstories'
            ORDER BY he.rank
            """,
            (run_day,),
        )
        rows = [dict(row) for row in cur.fetchall()]

    if HN_DAILY_RENDER_LIMIT > 0:
        return rows[:HN_DAILY_RENDER_LIMIT]
    return rows


def list_gh_daily_dates(conn: psycopg.Connection) -> list[date]:
    """List all GitHub daily run dates."""
    with conn.cursor() as cur:
        cur.execute("SELECT run_date FROM gh_runs WHERE period = 'daily' ORDER BY run_date DESC")
        return [row[0] for row in cur.fetchall()]


def list_hn_daily_dates(conn: psycopg.Connection) -> list[date]:
    """List all Hacker News run dates."""
    with conn.cursor() as cur:
        cur.execute("SELECT run_date FROM hn_runs WHERE feed = 'topstories' ORDER BY run_date DESC")
        return [row[0] for row in cur.fetchall()]


def generate_summary_html(summary_text: str) -> str:
    """Render summary text as HTML paragraphs."""
    if not summary_text:
        return "<p><em>Summary not available.</em></p>"

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", summary_text) if p.strip()]
    if not paragraphs:
        return "<p><em>Summary not available.</em></p>"

    return "\n".join(f"<p>{html.escape(paragraph)}</p>" for paragraph in paragraphs)


def generate_bullet_paragraph_html(text: str) -> str:
    """Render bullet-like text as analysis paragraphs."""
    if not text:
        return "<p><em>Comment analysis not available.</em></p>"

    bullets: list[str] = []
    for raw_line in text.splitlines():
        line = normalize_text(raw_line)
        if not line:
            continue
        if line.startswith(("-", "*", "•")):
            cleaned = normalize_text(line.lstrip("-*•").strip())
            if cleaned:
                bullets.append(cleaned)

    if not bullets:
        fallback = [normalize_text(line) for line in text.splitlines() if normalize_text(line)]
        bullets = fallback[:4]

    if not bullets:
        return "<p><em>Comment analysis not available.</em></p>"

    return "\n".join(f"<p>{html.escape(bullet)}</p>" for bullet in bullets)


def generate_month_calendar(year: int, month: int, pages_set: set[str], link_prefix: str = "") -> str:
    """Generate one month of calendar HTML."""
    cal = calendar.Calendar(firstweekday=6)
    month_name = calendar.month_name[month]

    weeks_html = ""
    for week in cal.monthdayscalendar(year, month):
        days_html = ""
        for day in week:
            if day == 0:
                days_html += '<td class="empty"></td>'
                continue

            date_str = f"{year}-{month:02d}-{day:02d}"
            if date_str in pages_set:
                days_html += (
                    f'<td class="has-page" data-date="{date_str}"><a class="day-link" href="{link_prefix}{date_str}/" '
                    f'data-date="{date_str}">{day}</a></td>'
                )
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


def build_calendar_html(page_dates: list[date], link_prefix: str = "") -> str:
    """Build full calendar markup from most recent month to oldest month."""
    today = date.today()
    pages_set = {day.isoformat() for day in page_dates}

    if page_dates:
        min_day = min(page_dates)
        max_day = max(max(page_dates), today)
    else:
        min_day = today
        max_day = today

    calendar_html = ""
    current = date(max_day.year, max_day.month, 1)
    end = date(min_day.year, min_day.month, 1)

    while current >= end:
        calendar_html += generate_month_calendar(current.year, current.month, pages_set, link_prefix=link_prefix)
        if current.month == 1:
            current = date(current.year - 1, 12, 1)
        else:
            current = date(current.year, current.month - 1, 1)

    return calendar_html


def generate_read_days_script(storage_key: str) -> str:
    """Generate JS to color calendar days already read in this browser."""
    return f"""
<script>
(() => {{
    const storageKey = {json.dumps(storage_key)};
    let stored = [];
    try {{
        stored = JSON.parse(localStorage.getItem(storageKey) || "[]");
        if (!Array.isArray(stored)) {{
            stored = [];
        }}
    }} catch (_err) {{
        stored = [];
    }}

    const readDays = new Set(stored);
    document.querySelectorAll("td.has-page[data-date]").forEach((cell) => {{
        if (readDays.has(cell.dataset.date)) {{
            cell.classList.add("read-day");
        }}
    }});
}})();
</script>
"""


def generate_gh_daily_script(day_str: str) -> str:
    """Generate JS for GH daily page behavior (mark read + collapse query)."""
    return f"""
<script>
(() => {{
    const readKey = {json.dumps(READ_DAYS_KEY_GH)};
    const dayStr = {json.dumps(day_str)};

    let stored = [];
    try {{
        stored = JSON.parse(localStorage.getItem(readKey) || "[]");
        if (!Array.isArray(stored)) {{
            stored = [];
        }}
    }} catch (_err) {{
        stored = [];
    }}

    if (!stored.includes(dayStr)) {{
        stored.push(dayStr);
        stored.sort();
        localStorage.setItem(readKey, JSON.stringify(stored));
    }}

    const collapseParam = new URLSearchParams(window.location.search).get("collapse_seen");
    const collapseSeen = collapseParam === "0" ? false : true;

    function setCollapsed(repoEl, collapsed) {{
        repoEl.classList.toggle("collapsed", collapsed);
        const button = repoEl.querySelector(".repo-toggle");
        if (button) {{
            button.textContent = collapsed ? "Show details" : "Hide details";
            button.setAttribute("aria-expanded", String(!collapsed));
        }}
    }}

    const repos = Array.from(document.querySelectorAll("section.repo[data-seen-before]"));

    repos.forEach((repoEl) => {{
        const toggle = repoEl.querySelector(".repo-toggle");
        if (!toggle) {{
            return;
        }}

        toggle.addEventListener("click", () => {{
            setCollapsed(repoEl, !repoEl.classList.contains("collapsed"));
        }});
    }});

    const collapseBtn = document.getElementById("collapse-seen-btn");
    const expandBtn = document.getElementById("expand-all-btn");

    if (collapseBtn) {{
        collapseBtn.addEventListener("click", () => {{
            repos.forEach((repoEl) => {{
                if (repoEl.dataset.seenBefore === "1") {{
                    setCollapsed(repoEl, true);
                }}
            }});
        }});
    }}

    if (expandBtn) {{
        expandBtn.addEventListener("click", () => {{
            repos.forEach((repoEl) => setCollapsed(repoEl, false));
        }});
    }}

    if (collapseSeen) {{
        repos.forEach((repoEl) => {{
            if (repoEl.dataset.seenBefore === "1") {{
                setCollapsed(repoEl, true);
            }}
        }});
    }}
}})();
</script>
"""


def generate_hn_daily_script(day_str: str) -> str:
    """Generate JS for HN daily page behavior (mark read + collapse query)."""
    return f"""
<script>
(() => {{
    const readKey = {json.dumps(READ_DAYS_KEY_HN)};
    const dayStr = {json.dumps(day_str)};

    let stored = [];
    try {{
        stored = JSON.parse(localStorage.getItem(readKey) || "[]");
        if (!Array.isArray(stored)) {{
            stored = [];
        }}
    }} catch (_err) {{
        stored = [];
    }}

    if (!stored.includes(dayStr)) {{
        stored.push(dayStr);
        stored.sort();
        localStorage.setItem(readKey, JSON.stringify(stored));
    }}

    const collapseParam = new URLSearchParams(window.location.search).get("collapse_seen");
    const collapseSeen = collapseParam === "0" ? false : true;

    function setCollapsed(repoEl, collapsed) {{
        repoEl.classList.toggle("collapsed", collapsed);
        const button = repoEl.querySelector(".repo-toggle");
        if (button) {{
            button.textContent = collapsed ? "Show details" : "Hide details";
            button.setAttribute("aria-expanded", String(!collapsed));
        }}
    }}

    const repos = Array.from(document.querySelectorAll("section.repo[data-seen-before]"));

    repos.forEach((repoEl) => {{
        const toggle = repoEl.querySelector(".repo-toggle");
        if (!toggle) {{
            return;
        }}

        toggle.addEventListener("click", () => {{
            setCollapsed(repoEl, !repoEl.classList.contains("collapsed"));
        }});
    }});

    const collapseBtn = document.getElementById("collapse-seen-btn");
    const expandBtn = document.getElementById("expand-all-btn");

    if (collapseBtn) {{
        collapseBtn.addEventListener("click", () => {{
            repos.forEach((repoEl) => {{
                if (repoEl.dataset.seenBefore === "1") {{
                    setCollapsed(repoEl, true);
                }}
            }});
        }});
    }}

    if (expandBtn) {{
        expandBtn.addEventListener("click", () => {{
            repos.forEach((repoEl) => setCollapsed(repoEl, false));
        }});
    }}

    if (collapseSeen) {{
        repos.forEach((repoEl) => {{
            if (repoEl.dataset.seenBefore === "1") {{
                setCollapsed(repoEl, true);
            }}
        }});
    }}
}})();
</script>
"""


def generate_gh_daily_page(repos: list[dict], day: date, hn_dates_set: set[str]) -> str:
    """Generate GitHub daily digest page HTML."""
    date_str = day.isoformat()
    date_display = format_date_display(day)
    hn_link = f"../hn/{date_str}/" if date_str in hn_dates_set else "../hn/"

    repo_cards = ""
    if not repos:
        repo_cards = '<p class="empty-state">No GitHub repositories available for this day.</p>'

    for repo in repos:
        summary_html = generate_summary_html(repo.get("summary", ""))
        history_line = (
            f"First seen: {format_date_display(repo['earliest_seen'])} | "
            f"Consecutive daily streak: {repo['streak_days']} day{'s' if repo['streak_days'] != 1 else ''}"
            if repo.get("earliest_seen")
            else "History unavailable"
        )
        seen_badge = '<span class="seen-badge">Seen before</span>' if repo.get("seen_before") else ""

        repo_cards += f"""
            <section class="repo" data-seen-before="{1 if repo.get('seen_before') else 0}">
                <div class="repo-header-row">
                    <h3>{repo['rank']}. <a href="{repo['url']}" target="_blank" rel="noopener noreferrer">{html.escape(repo['name'])}</a> {seen_badge}</h3>
                    <button type="button" class="repo-toggle" aria-expanded="true">Hide details</button>
                </div>
                <div class="repo-body">
                    <p class="description">{html.escape(repo['description'])}</p>
                    <p class="meta">
                        <span class="language">{html.escape(repo['language'])}</span> |
                        <span class="stars">{html.escape(repo['stars'])}</span>
                        {f'| <span class="today">{html.escape(repo["period_stars"])}</span>' if repo.get('period_stars') else ''}
                    </p>
                    <p class="history">{html.escape(history_line)}</p>
                    <div class="ai-summary">
                        <h4>Analysis</h4>
                        {summary_html}
                    </div>
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
            <a href="../">&larr; GitHub Calendar</a>
            <a href="{hn_link}">Hacker News</a>
        </nav>
    </header>
    <main>
        <div class="repo-controls">
            <button id="collapse-seen-btn" type="button">Collapse Seen Repos</button>
            <button id="expand-all-btn" type="button">Expand All</button>
        </div>
        <article>
            <div class="repos">
{repo_cards}
            </div>
        </article>
    </main>
    <footer>
        <p>Generated automatically. Data from <a href="https://github.com/trending">GitHub Trending</a>.</p>
    </footer>
{generate_gh_daily_script(date_str)}
</body>
</html>
"""


def generate_hn_daily_page(items: list[dict], day: date, gh_dates_set: set[str]) -> str:
    """Generate Hacker News daily digest page HTML."""
    date_str = day.isoformat()
    date_display = format_date_display(day)
    gh_link = f"../../{date_str}/" if date_str in gh_dates_set else "../../"

    story_cards = ""
    if not items:
        story_cards = '<p class="empty-state">No Hacker News stories available for this day.</p>'

    for item in items:
        summary_html = generate_summary_html(item.get("summary", ""))
        comment_analysis_html = ""
        if item.get("comment_analysis"):
            comment_analysis_html = f"""
                <div class="ai-summary">
                    <h4>Comment Analysis</h4>
                    {generate_bullet_paragraph_html(item["comment_analysis"])}
                </div>
"""
        history_line = (
            f"First seen: {format_date_display(item['earliest_seen'])} | "
            f"Consecutive daily streak: {item['streak_days']} day{'s' if item['streak_days'] != 1 else ''}"
            if item.get("earliest_seen")
            else "History unavailable"
        )

        title_url = item.get("url") or item.get("discussion_url")
        domain = extract_domain(item.get("url") or "") or "news.ycombinator.com"
        seen_badge = '<span class="seen-badge">Seen before</span>' if item.get("seen_before") else ""

        story_cards += f"""
            <section class="repo" data-seen-before="{1 if item.get('seen_before') else 0}">
                <div class="repo-header-row">
                    <h3>{item['rank']}. <a href="{html.escape(title_url)}" target="_blank" rel="noopener noreferrer">{html.escape(item['title'])}</a> {seen_badge}</h3>
                    <button type="button" class="repo-toggle" aria-expanded="true">Hide details</button>
                </div>
                <div class="repo-body">
                    <p class="meta">
                        <span class="language">{html.escape(domain)}</span> |
                        <span>{html.escape(item.get('author', 'unknown'))}</span> |
                        <span>{item.get('score', 0)} points</span> |
                        <span>{item.get('comment_count', 0)} comments</span> |
                        <a href="{html.escape(item['discussion_url'])}" target="_blank" rel="noopener noreferrer">discussion</a>
                    </p>
                    <p class="history">{html.escape(history_line)}</p>
                    <div class="ai-summary">
                        <h4>Analysis</h4>
                        {summary_html}
                    </div>
                    {comment_analysis_html}
                </div>
            </section>
"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Hacker News Digest - {date_display}</title>
    <link rel="stylesheet" href="../../style.css">
</head>
<body>
    <header>
        <h1>Hacker News Digest - {date_display}</h1>
        <nav>
            <a href="../">&larr; Hacker News Calendar</a>
            <a href="{gh_link}">GitHub Trending</a>
        </nav>
    </header>
    <main>
        <div class="repo-controls">
            <button id="collapse-seen-btn" type="button">Collapse Seen Repos</button>
            <button id="expand-all-btn" type="button">Expand All</button>
        </div>
        <article>
            <div class="repos">
{story_cards}
            </div>
        </article>
    </main>
    <footer>
        <p>Generated automatically. Data from <a href="https://news.ycombinator.com/">Hacker News</a>.</p>
    </footer>
{generate_hn_daily_script(date_str)}
</body>
</html>
"""


def generate_gh_index_page(gh_dates: list[date], hn_dates: list[date]) -> str:
    """Generate GitHub calendar index page."""
    calendar_html = build_calendar_html(gh_dates)
    today = date.today().isoformat()

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GitHub Trending Digest - {today}</title>
    <link rel="stylesheet" href="style.css">
</head>
<body>
    <header>
        <h1>GitHub Trending Digest</h1>
        <p class="subtitle">Daily GitHub trending repositories with AI analysis</p>
        <nav>
            <a href="hn/">Hacker News Calendar</a>
        </nav>
    </header>
    <main>
        <div class="calendar-container">
{calendar_html}
        </div>
    </main>
    <footer>
        <p>
            Generated automatically. Data from <a href="https://github.com/trending">GitHub Trending</a>.
            GitHub days tracked: {len(gh_dates)}.
        </p>
    </footer>
{generate_read_days_script(READ_DAYS_KEY_GH)}
</body>
</html>
"""


def generate_hn_index_page(hn_dates: list[date], gh_dates: list[date]) -> str:
    """Generate Hacker News calendar index page."""
    calendar_html = build_calendar_html(hn_dates)
    today = date.today().isoformat()

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Hacker News Digest - {today}</title>
    <link rel="stylesheet" href="../style.css">
</head>
<body>
    <header>
        <h1>Hacker News Digest</h1>
        <p class="subtitle">Daily Hacker News top stories with AI analysis</p>
        <nav>
            <a href="../">GitHub Trending Calendar</a>
        </nav>
    </header>
    <main>
        <div class="calendar-container">
{calendar_html}
        </div>
    </main>
    <footer>
        <p>
            Generated automatically. Data from <a href="https://news.ycombinator.com/">Hacker News</a>.
            Hacker News days tracked: {len(hn_dates)}.
        </p>
    </footer>
{generate_read_days_script(READ_DAYS_KEY_HN)}
</body>
</html>
"""


def generate_css() -> str:
    """Generate shared stylesheet."""
    return """:root {
    --bg-color: #0d1117;
    --card-bg: #161b22;
    --text-color: #c9d1d9;
    --link-color: #58a6ff;
    --border-color: #30363d;
    --accent-color: #238636;
    --highlight-bg: #1f6feb;
    --read-bg: #2d7d46;
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
    max-width: 980px;
    margin: 0 auto;
}
h1 {
    color: #f0f6fc;
    margin-bottom: 0.5rem;
    font-size: 2rem;
}
.subtitle {
    color: #8b949e;
    margin-bottom: 0.8rem;
}
nav {
    margin-bottom: 1.6rem;
    display: flex;
    gap: 1rem;
    flex-wrap: wrap;
}
nav a {
    color: var(--link-color);
    text-decoration: none;
}
nav a:hover {
    text-decoration: underline;
}
.repo-controls {
    display: flex;
    gap: 0.75rem;
    margin-bottom: 1rem;
}
.repo-controls button,
.repo-toggle {
    background-color: #21262d;
    color: #c9d1d9;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 0.35rem 0.6rem;
    font-size: 0.8rem;
    cursor: pointer;
}
.repo-controls button:hover,
.repo-toggle:hover {
    background-color: #30363d;
}
.repo {
    background-color: var(--card-bg);
    border: 1px solid var(--border-color);
    border-radius: 6px;
    padding: 1.25rem;
    margin-bottom: 1.25rem;
}
.repo-header-row {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 0.75rem;
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
.seen-badge {
    display: inline-block;
    border: 1px solid var(--border-color);
    color: #8b949e;
    font-size: 0.72rem;
    border-radius: 12px;
    padding: 0.05rem 0.45rem;
    vertical-align: middle;
}
.description {
    color: #8b949e;
    margin-bottom: 0.5rem;
}
.meta {
    font-size: 0.85rem;
    color: #8b949e;
    margin-bottom: 0.5rem;
}
.meta a {
    color: var(--link-color);
    text-decoration: none;
}
.meta a:hover {
    text-decoration: underline;
}
.language {
    color: var(--accent-color);
}
.history {
    font-size: 0.82rem;
    color: #7d8590;
    margin-bottom: 0.9rem;
}
.repo.collapsed .repo-body {
    display: none;
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
.empty-state {
    color: #8b949e;
    font-style: italic;
    margin: 1rem 0 2rem;
}
footer {
    margin-top: 2rem;
    color: #8b949e;
    font-size: 0.85rem;
}
footer a {
    color: var(--link-color);
}
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
    padding: 0;
    overflow: hidden;
}
.month-calendar td.has-page.read-day {
    background-color: var(--read-bg);
}
.month-calendar td.has-page a {
    color: #fff;
    text-decoration: none;
    display: block;
    font-weight: 500;
    padding: 0.5rem;
    width: 100%;
    height: 100%;
    box-sizing: border-box;
}
.month-calendar td.has-page:hover {
    background-color: #388bfd;
}
.month-calendar td.has-page.read-day:hover {
    background-color: #3f9f5c;
}
@media (max-width: 700px) {
    body {
        padding: 1rem;
    }
    .repo-header-row {
        flex-direction: column;
        align-items: stretch;
    }
    .repo-toggle {
        width: fit-content;
    }
    .repo-controls {
        flex-wrap: wrap;
    }
}
"""


def write_text(path: Path, content: str) -> None:
    """Write file with parent directory creation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def save_pages_json(path: Path, pages: list[date]) -> None:
    """Save a pages.json file from date list."""
    payload = {"pages": [day.isoformat() for day in sorted(pages, reverse=True)]}
    write_text(path, json.dumps(payload, indent=2) + "\n")


def save_files(
    run_day: date,
    gh_daily_html: str,
    gh_index_html: str,
    hn_daily_html: str,
    hn_index_html: str,
    css: str,
    gh_dates: list[date],
    hn_dates: list[date],
) -> None:
    """Write all generated pages and metadata files."""
    gh_daily_file = DOCS_DIR / run_day.isoformat() / "index.html"
    hn_daily_file = HN_DOCS_DIR / run_day.isoformat() / "index.html"

    write_text(gh_daily_file, gh_daily_html)
    write_text(INDEX_FILE, gh_index_html)
    write_text(hn_daily_file, hn_daily_html)
    write_text(HN_INDEX_FILE, hn_index_html)
    write_text(STYLE_FILE, css)

    save_pages_json(PAGES_DATA_FILE, gh_dates)
    save_pages_json(HN_PAGES_DATA_FILE, hn_dates)

    logging.info("Saved GitHub daily page to %s", gh_daily_file)
    logging.info("Saved Hacker News daily page to %s", hn_daily_file)
    logging.info("Saved GitHub index to %s", INDEX_FILE)
    logging.info("Saved Hacker News index to %s", HN_INDEX_FILE)
    logging.info("Saved stylesheet to %s", STYLE_FILE)


def regenerate_gh_daily_pages(conn: psycopg.Connection, gh_dates: list[date], hn_dates_set: set[str]) -> None:
    """Regenerate all GitHub daily pages from stored data."""
    if not gh_dates:
        return

    for render_day in sorted(gh_dates):
        gh_rows = build_gh_view_rows(conn, render_day, allow_summary_generation=False)
        gh_daily_html = generate_gh_daily_page(gh_rows, render_day, hn_dates_set)
        gh_daily_file = DOCS_DIR / render_day.isoformat() / "index.html"
        write_text(gh_daily_file, gh_daily_html)

    logging.info("Regenerated %d GitHub daily pages", len(gh_dates))


def regenerate_hn_daily_pages(conn: psycopg.Connection, hn_dates: list[date], gh_dates_set: set[str]) -> None:
    """Regenerate all Hacker News daily pages from stored data."""
    if not hn_dates:
        return

    for render_day in sorted(hn_dates):
        hn_rows = build_hn_view_rows(conn, render_day, allow_summary_generation=False)
        hn_daily_html = generate_hn_daily_page(hn_rows, render_day, gh_dates_set)
        hn_daily_file = HN_DOCS_DIR / render_day.isoformat() / "index.html"
        write_text(hn_daily_file, hn_daily_html)

    logging.info("Regenerated %d Hacker News daily pages", len(hn_dates))


def git_commit_and_push() -> bool:
    """Commit and push docs changes if any."""
    repo_dir = Path(__file__).parent

    subprocess.run(["git", "add", "docs/"], cwd=repo_dir, check=True)

    diff_check = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=repo_dir, check=False)
    if diff_check.returncode == 0:
        logging.info("No generated docs changes to commit")
        return False

    today_str = datetime.now().strftime("%Y-%m-%d")
    subprocess.run(["git", "commit", "-m", f"Update digests for {today_str}"], cwd=repo_dir, check=True)
    subprocess.run(["git", "push"], cwd=repo_dir, check=True)
    logging.info("Pushed generated docs changes")
    return True


def send_email(to_address: str, subject: str, body: str) -> None:
    """Send email using Gmail SMTP."""
    if not gmail_user or not gmail_password:
        logging.error("Gmail credentials not set in environment variables")
        return

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = gmail_user
    msg["To"] = to_address

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_user, gmail_password)
        server.sendmail(gmail_user, to_address, msg.as_string())

    logging.info("Email sent successfully to %s", to_address)


def wait_for_page_live(url: str, max_attempts: int = 30, delay: int = 10) -> bool:
    """Wait for URL to return HTTP 200."""
    logging.info("Waiting for page to be live: %s", url)

    for attempt in range(max_attempts):
        try:
            response = requests.head(url, timeout=10, allow_redirects=True)
            if response.status_code == 405:
                response = requests.get(url, timeout=10, allow_redirects=True)

            if response.status_code == 200:
                logging.info("Page %s is live after %d attempts", url, attempt + 1)
                return True
        except requests.RequestException as exc:
            logging.debug("Attempt %d failed for %s: %s", attempt + 1, url, exc)

        if attempt < max_attempts - 1:
            time.sleep(delay)

    logging.error("Page did not become live: %s", url)
    return False


def wait_for_pages_live(urls: list[str]) -> bool:
    """Wait for all provided URLs to become live."""
    for url in urls:
        if not wait_for_page_live(url):
            return False
    return True


def fetch_hn_item(item_id: int) -> dict | None:
    """Fetch one Hacker News item payload."""
    try:
        response = requests.get(HN_ITEM_URL_TEMPLATE.format(item_id=item_id), timeout=20)
        response.raise_for_status()
    except requests.RequestException:
        return None

    try:
        payload = response.json()
    except ValueError:
        return None

    if not isinstance(payload, dict):
        return None
    return payload


def fetch_hn_item_cached(item_id: int, cache: dict[int, dict | None], session: requests.Session) -> dict | None:
    """Fetch one Hacker News item with in-memory request cache."""
    if item_id in cache:
        return cache[item_id]

    try:
        response = session.get(HN_ITEM_URL_TEMPLATE.format(item_id=item_id), timeout=20)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        payload = None

    if payload is not None and not isinstance(payload, dict):
        payload = None

    cache[item_id] = payload
    return payload


def clean_hn_comment_text(raw_text: str) -> str:
    """Normalize Hacker News comment HTML into plain text."""
    if not raw_text:
        return ""
    return normalize_text(BeautifulSoup(raw_text, "html.parser").get_text(" ", strip=True))


def build_hn_comment_nodes(item_id: int, total_comments_hint: int) -> tuple[int, list[dict]]:
    """Traverse HN comment tree with branch-diverse round-robin strategy."""
    session = requests.Session()
    item_cache: dict[int, dict | None] = {}

    story = fetch_hn_item_cached(item_id, item_cache, session)
    if not story:
        return total_comments_hint, []

    total_comments = int(story.get("descendants") or total_comments_hint or 0)
    top_kids = [int(kid) for kid in (story.get("kids") or [])]
    if not top_kids:
        return total_comments, []

    branch_queues = [deque([(kid, 1, kid, idx + 1)]) for idx, kid in enumerate(top_kids)]

    nodes: list[dict] = []
    visited: set[int] = set()
    while len(nodes) < HN_COMMENT_TRAVERSAL_MAX_NODES:
        progressed = False
        for queue in branch_queues:
            if not queue:
                continue
            progressed = True
            comment_id, depth, root_id, root_pos = queue.popleft()
            if comment_id in visited:
                continue
            visited.add(comment_id)
            if depth > HN_COMMENT_TRAVERSAL_MAX_DEPTH:
                continue

            comment = fetch_hn_item_cached(comment_id, item_cache, session)
            if not comment or comment.get("type") != "comment":
                continue
            if comment.get("dead") or comment.get("deleted"):
                continue

            text = clean_hn_comment_text(comment.get("text") or "")
            kids = [int(kid) for kid in (comment.get("kids") or [])]

            # Continue exploring replies even when this node is too short to keep.
            for kid in kids:
                queue.append((kid, depth + 1, root_id, root_pos))

            if len(text) < HN_COMMENT_MIN_TEXT_LEN:
                continue

            nodes.append(
                {
                    "comment_id": comment_id,
                    "by": normalize_text(comment.get("by") or "unknown"),
                    "depth": depth,
                    "root_id": root_id,
                    "root_pos": root_pos,
                    "reply_count": len(kids),
                    "len": len(text),
                    "text": text,
                }
            )
            if len(nodes) >= HN_COMMENT_TRAVERSAL_MAX_NODES:
                break

        if not progressed:
            break

    return total_comments, nodes


def select_hn_comment_sample(nodes: list[dict]) -> list[dict]:
    """Select a branch-diverse high-signal subset of comments."""
    if not nodes:
        return []

    ranked = []
    for node in nodes:
        depth_bonus = 1.2 if node["depth"] == 1 else (0.7 if node["depth"] == 2 else 0.3)
        len_bonus = min(node["len"], 900) / 220
        reply_bonus = min(node["reply_count"], 10) / 4
        order_bonus = max(0, 14 - node["root_pos"]) / 14
        signal = depth_bonus + len_bonus + reply_bonus + order_bonus
        ranked.append((signal, node))

    ranked.sort(key=lambda row: (row[0], row[1]["len"]), reverse=True)

    selected: list[dict] = []
    branch_counts: dict[int, int] = {}
    text_seen: set[str] = set()
    for _, node in ranked:
        branch_id = int(node["root_id"])
        if branch_counts.get(branch_id, 0) >= HN_COMMENT_MAX_PER_BRANCH:
            continue

        dedupe_key = node["text"][:200].lower()
        if dedupe_key in text_seen:
            continue

        selected.append(node)
        branch_counts[branch_id] = branch_counts.get(branch_id, 0) + 1
        text_seen.add(dedupe_key)

        if len(selected) >= HN_COMMENT_SAMPLE_SIZE:
            break

    return selected


def scrape_hn_topstories() -> list[dict]:
    """Fetch Hacker News top stories using official API."""
    logging.info("Fetching Hacker News top stories")
    response = requests.get(HN_TOPSTORIES_URL, timeout=30)
    response.raise_for_status()
    story_ids = response.json()

    if not isinstance(story_ids, list):
        raise ValueError("Unexpected topstories response format")

    if HN_MAX_ITEMS > 0:
        story_ids = story_ids[:HN_MAX_ITEMS]

    fetched: dict[int, dict] = {}

    with ThreadPoolExecutor(max_workers=HN_FETCH_WORKERS) as executor:
        futures = {
            executor.submit(fetch_hn_item, int(item_id)): (rank, int(item_id))
            for rank, item_id in enumerate(story_ids, start=1)
        }

        for future in as_completed(futures):
            rank, item_id = futures[future]
            item = future.result()
            if not item:
                continue
            if item.get("type") != "story":
                continue
            title = normalize_text(item.get("title") or "")
            if not title:
                continue

            fetched[rank] = {
                "rank": rank,
                "item_id": item_id,
                "title": title,
                "url": normalize_text(item.get("url") or ""),
                "author": normalize_text(item.get("by") or "unknown"),
                "score": int(item.get("score") or 0),
                "comment_count": int(item.get("descendants") or 0),
                "item_time": item.get("time"),
                "text": item.get("text") or "",
                "item_type": item.get("type") or "story",
                "discussion_url": HN_DISCUSSION_URL_TEMPLATE.format(item_id=item_id),
            }

    stories = [fetched[rank] for rank in sorted(fetched)]
    logging.info("Hacker News scrape returned %d stories", len(stories))
    return stories


def parse_gh_daily_html(path: Path) -> list[dict]:
    """Parse an existing generated GitHub daily page for backfill."""
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    repos: list[dict] = []

    for section in soup.select("section.repo"):
        heading = section.select_one("h3")
        link = heading.select_one("a") if heading else None
        if not heading or not link:
            continue

        heading_text = normalize_text(heading.get_text(" ", strip=True))
        rank_match = re.match(r"^(\d+)\.", heading_text)
        rank = int(rank_match.group(1)) if rank_match else len(repos) + 1

        repo_name = normalize_text(link.get_text(" ", strip=True))
        repo_url = normalize_text(link.get("href", ""))

        description_elem = section.select_one("p.description")
        description = normalize_text(description_elem.get_text(" ", strip=True)) if description_elem else "No description"

        language_elem = section.select_one("span.language")
        stars_elem = section.select_one("span.stars")
        period_stars_elem = section.select_one("span.today")

        language = normalize_text(language_elem.get_text(" ", strip=True)) if language_elem else "Unknown"
        stars = normalize_text(stars_elem.get_text(" ", strip=True)) if stars_elem else "N/A"
        period_stars = normalize_text(period_stars_elem.get_text(" ", strip=True)) if period_stars_elem else ""

        summary_parts = []
        for paragraph in section.select("div.ai-summary p"):
            text = normalize_text(paragraph.get_text(" ", strip=True))
            if text:
                summary_parts.append(text)

        repos.append(
            {
                "rank": rank,
                "name": repo_name,
                "url": repo_url or f"https://github.com/{repo_name}",
                "description": description,
                "language": language,
                "stars": stars,
                "period_stars": period_stars,
                "summary": "\n\n".join(summary_parts),
            }
        )

    repos.sort(key=lambda repo: repo["rank"])
    return repos


def backfill_existing_gh_pages(conn: psycopg.Connection) -> None:
    """Import historical docs/YYYY-MM-DD pages into Postgres once."""
    if get_app_meta(conn, "gh_backfill_completed") == "1":
        return

    if not DOCS_DIR.exists():
        set_app_meta(conn, "gh_backfill_completed", "1")
        return

    logging.info("Backfilling existing GitHub daily pages into database")
    date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    imported_count = 0

    for child in sorted(DOCS_DIR.iterdir(), key=lambda p: p.name):
        if not child.is_dir() or not date_pattern.match(child.name):
            continue

        index_file = child / "index.html"
        if not index_file.exists():
            continue

        try:
            run_day = datetime.strptime(child.name, "%Y-%m-%d").date()
        except ValueError:
            continue

        repos = parse_gh_daily_html(index_file)
        if not repos:
            continue

        store_gh_period_run(conn, run_day, "daily", repos, source="backfill")

        for repo in repos:
            if repo.get("summary") and repo.get("repo_id"):
                cache_gh_summary_if_missing(conn, int(repo["repo_id"]), repo["summary"], run_day)

        imported_count += 1

    set_app_meta(conn, "gh_backfill_completed", "1")
    logging.info("Backfill complete. Imported %d historical daily pages", imported_count)


def build_gh_view_rows(
    conn: psycopg.Connection,
    run_day: date,
    allow_summary_generation: bool = True,
) -> list[dict]:
    """Load GitHub rows for rendering and enrich with history and summaries."""
    rows = list_gh_daily_entries(conn, run_day)
    for row in rows:
        earliest_seen, streak_days, seen_before = get_gh_history_stats(conn, int(row["repo_id"]), run_day)
        row["earliest_seen"] = earliest_seen
        row["streak_days"] = streak_days
        row["seen_before"] = seen_before
        if allow_summary_generation:
            row["summary"] = get_or_generate_gh_summary(conn, row, run_day)
        else:
            latest = get_latest_gh_summary(conn, int(row["repo_id"]))
            row["summary"] = latest["summary_text"] if latest else ""
    return rows


def build_hn_view_rows(
    conn: psycopg.Connection,
    run_day: date,
    allow_summary_generation: bool = True,
) -> list[dict]:
    """Load Hacker News rows for rendering and enrich with history + summaries."""
    rows = list_hn_daily_entries(conn, run_day)
    for row in rows:
        earliest_seen, streak_days, seen_before = get_hn_history_stats(conn, int(row["item_id"]), run_day)
        row["earliest_seen"] = earliest_seen
        row["streak_days"] = streak_days
        row["seen_before"] = seen_before
        row["discussion_url"] = HN_DISCUSSION_URL_TEMPLATE.format(item_id=row["item_id"])
        if allow_summary_generation:
            row["summary"] = get_or_generate_hn_summary(conn, row, run_day)
            comment_analysis = get_or_generate_hn_comment_analysis(conn, row, run_day)
        else:
            latest_summary = get_latest_hn_summary(conn, int(row["item_id"]))
            row["summary"] = latest_summary["summary_text"] if latest_summary else ""
            latest_comment_analysis = get_latest_hn_comment_analysis(conn, int(row["item_id"]))
            comment_analysis = (
                {
                    "analysis_text": latest_comment_analysis["analysis_text"],
                    "sampled_comments": int(latest_comment_analysis["sampled_comments"]),
                    "total_comments": int(latest_comment_analysis["total_comments"]),
                }
                if latest_comment_analysis
                else None
            )
        row["comment_analysis"] = comment_analysis["analysis_text"] if comment_analysis else ""
        row["comment_analysis_sampled_comments"] = comment_analysis["sampled_comments"] if comment_analysis else 0
        row["comment_analysis_total_comments"] = comment_analysis["total_comments"] if comment_analysis else 0
    return rows


def main() -> None:
    """Main entry point."""
    args = parse_args()
    run_day = date.today()

    try:
        conn = get_db_connection()
    except Exception as exc:
        logging.exception("Database connection failed: %s", exc)
        return

    lock_acquired = False
    try:
        init_db(conn)

        lock_acquired = acquire_run_lock(conn)
        if not lock_acquired:
            return

        backfill_existing_gh_pages(conn)

        if args.regenerate_only:
            gh_dates = list_gh_daily_dates(conn)
            hn_dates = list_hn_daily_dates(conn)
            gh_dates_set = {day.isoformat() for day in gh_dates}
            hn_dates_set = {day.isoformat() for day in hn_dates}

            regenerate_gh_daily_pages(conn, gh_dates, hn_dates_set)
            regenerate_hn_daily_pages(conn, hn_dates, gh_dates_set)

            write_text(INDEX_FILE, generate_gh_index_page(gh_dates, hn_dates))
            write_text(HN_INDEX_FILE, generate_hn_index_page(hn_dates, gh_dates))
            write_text(STYLE_FILE, generate_css())
            save_pages_json(PAGES_DATA_FILE, gh_dates)
            save_pages_json(HN_PAGES_DATA_FILE, hn_dates)

            logging.info(
                "Regenerate-only mode complete. Rebuilt GitHub days=%d, Hacker News days=%d",
                len(gh_dates),
                len(hn_dates),
            )
            return

        gh_scrape_results: dict[str, list[dict]] = {}
        for period in GITHUB_PERIODS:
            try:
                repos = scrape_trending_repos(period)
            except Exception as exc:
                logging.exception("GitHub scrape failed for %s: %s", period, exc)
                repos = []

            store_gh_period_run(conn, run_day, period, repos, source="live")
            gh_scrape_results[period] = repos

        if not gh_scrape_results.get("daily"):
            logging.error("Daily GitHub scrape returned no repos; skipping generation")
            return

        try:
            hn_items = scrape_hn_topstories()
        except Exception as exc:
            logging.exception("Hacker News scrape failed: %s", exc)
            hn_items = []

        store_hn_run(conn, run_day, hn_items, source="live")

        gh_rows = build_gh_view_rows(conn, run_day)
        hn_rows = build_hn_view_rows(conn, run_day)

        gh_dates = list_gh_daily_dates(conn)
        hn_dates = list_hn_daily_dates(conn)
        gh_dates_set = {day.isoformat() for day in gh_dates}
        hn_dates_set = {day.isoformat() for day in hn_dates}

        regenerate_gh_daily_pages(conn, gh_dates, hn_dates_set)
        regenerate_hn_daily_pages(conn, hn_dates, gh_dates_set)

        gh_daily_html = generate_gh_daily_page(gh_rows, run_day, hn_dates_set)
        gh_index_html = generate_gh_index_page(gh_dates, hn_dates)
        hn_daily_html = generate_hn_daily_page(hn_rows, run_day, gh_dates_set)
        hn_index_html = generate_hn_index_page(hn_dates, gh_dates)
        css = generate_css()

        save_files(run_day, gh_daily_html, gh_index_html, hn_daily_html, hn_index_html, css, gh_dates, hn_dates)

        changed = git_commit_and_push()

        gh_page_url = f"{GITHUB_PAGES_URL}{run_day.isoformat()}/"
        hn_page_url = f"{GITHUB_PAGES_URL}hn/{run_day.isoformat()}/"

        if changed:
            if wait_for_pages_live([gh_page_url, hn_page_url]):
                send_email(
                    to_address=email_to_address,
                    subject="links",
                    body=f"GitHub Trending Digest:\n{gh_page_url}\n\nHacker News Digest:\n{hn_page_url}",
                )
            else:
                logging.error("Skipping email because one or more pages did not go live")
        else:
            logging.info("Skipping email because there were no docs changes to publish")

        logging.info("Done. GitHub page: %s", gh_page_url)
        logging.info("Done. Hacker News page: %s", hn_page_url)
    finally:
        try:
            if lock_acquired:
                release_run_lock(conn)
        finally:
            conn.close()


if __name__ == "__main__":
    main()
