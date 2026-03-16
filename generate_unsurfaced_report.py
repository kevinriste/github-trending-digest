#!/usr/bin/env python3
"""One-time report: all weekly/monthly repos never surfaced in any daily email."""

import logging
from datetime import date

from trending_digest import (
    DOCS_DIR,
    generate_css,
    generate_gh_daily_script,
    generate_summary_html,
    get_db_connection,
    get_or_generate_gh_summary,
    init_db,
    write_text,
    _generate_gh_repo_cards,
)
import html as html_mod
from psycopg.rows import dict_row

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def get_unsurfaced_repos(conn):
    """All weekly/monthly repos that never appeared in any daily run."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""
            WITH daily_repos AS (
                SELECT DISTINCT ge.repo_id FROM gh_entries ge
                JOIN gh_runs gr ON ge.run_id = gr.id WHERE gr.period = 'daily'
            )
            SELECT
                r.id AS repo_id,
                r.full_name AS name,
                r.url,
                COALESCE(r.description, 'No description') AS description,
                COALESCE(r.language, 'Unknown') AS language,
                COUNT(DISTINCT CASE WHEN gr.period = 'weekly' THEN gr.run_date END) AS weekly_days,
                COUNT(DISTINCT CASE WHEN gr.period = 'monthly' THEN gr.run_date END) AS monthly_days,
                MAX(ge.stars_text) AS stars,
                MAX(ge.period_stars_text) AS period_stars,
                MIN(gr.run_date) AS first_seen_date,
                MAX(gr.run_date) AS last_seen_date
            FROM gh_entries ge
            JOIN gh_runs gr ON ge.run_id = gr.id
            JOIN gh_repos r ON ge.repo_id = r.id
            WHERE gr.period IN ('weekly', 'monthly')
              AND ge.repo_id NOT IN (SELECT repo_id FROM daily_repos)
            GROUP BY r.id, r.full_name, r.url, r.description, r.language
            ORDER BY (COUNT(DISTINCT CASE WHEN gr.period = 'weekly' THEN gr.run_date END)
                    + COUNT(DISTINCT CASE WHEN gr.period = 'monthly' THEN gr.run_date END)) DESC
        """)
        return [dict(row) for row in cur.fetchall()]


def main():
    run_day = date.today()
    conn = get_db_connection()
    init_db(conn)

    rows = get_unsurfaced_repos(conn)
    logging.info("Found %d repos never surfaced in daily emails", len(rows))

    # Generate summaries
    for row in rows:
        logging.info("Generating summary for %s", row["name"])
        row["summary"] = get_or_generate_gh_summary(conn, row, run_day)
        row["earliest_seen"] = row["first_seen_date"]
        row["streak_days"] = 0
        row["seen_before"] = False

    repo_cards = _generate_gh_repo_cards(rows)

    page_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Unsurfaced Weekly/Monthly Repos</title>
    <link rel="stylesheet" href="../style.css">
</head>
<body>
    <header>
        <h1>Unsurfaced Repos</h1>
        <p class="subtitle">Repos that trended on GitHub's weekly/monthly lists but never appeared in any daily digest.</p>
        <nav>
            <a href="../">&larr; GitHub Calendar</a>
        </nav>
    </header>
    <main>
        <p class="seen-help">{len(rows)} repos found. Sorted by total appearances across weekly + monthly trending.</p>
        <article>
            <div class="repos">
{repo_cards}
            </div>
        </article>
    </main>
    <footer>
        <p>One-time report generated {run_day.isoformat()}.</p>
    </footer>
{generate_gh_daily_script(run_day.isoformat())}
</body>
</html>
"""

    out_file = DOCS_DIR / "unsurfaced" / "index.html"
    write_text(out_file, page_html)
    write_text(DOCS_DIR / "style.css", generate_css())
    logging.info("Wrote report to %s", out_file)
    conn.close()


if __name__ == "__main__":
    main()
