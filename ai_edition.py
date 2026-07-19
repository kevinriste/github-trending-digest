"""AI Edition: render the ai-newsletter's select-mode digest as classic + magazine pages.

This is a third page type alongside GitHub Trending and Hacker News, but its DATA comes
from a JSON sidecar produced by the separate `ai-newsletter` project (its select pipeline
writes site/editions/<stem>.json), NOT from this project's Postgres. We do NOT re-select or
re-summarize: the same 15-20 stories the newsletter chose are rendered two ways —

  * classic:  docs/ai/<date>/classic.html  — plain headline + one-liner + analysis, hides repeats
  * magazine: docs/ai/<date>/index.html     — morning_edition's editorial spreads (source="ai")

Cross-day "seen before" is tracked in docs/ai/history.json (url -> first date seen), so the
classic page's collapse-repeats works without a database.

Run:  uv run python ai_edition.py --sidecar /path/to/latest.json [--no-publish]
"""
from __future__ import annotations

import argparse
import html
import json
import logging
from datetime import date, datetime
from pathlib import Path

from editions import EDITIONS, cross_edition_links, write_dates_manifest
from morning_edition import CONFIGS, generate_morning_edition
from trending_digest import (
    GITHUB_PAGES_URL,
    build_calendar_html,
    email_to_address,
    extract_domain,
    format_date_display,
    generate_read_days_script,
    generate_summary_html,
    get_git_sha,
    git_commit_and_push,
    notify_gotify,
    send_email,
    wait_for_pages_live,
    write_text,
)

AI_DIR = CONFIGS["ai"].output_dir            # docs/ai
HISTORY_FILE = AI_DIR / "history.json"
READ_KEY = CONFIGS["ai"].read_key
DEFAULT_SIDECAR = Path(
    "/home/flog99/dev/ai-newsletter/site/editions/latest.json"
)

log = logging.getLogger(__name__)


# ─────────────────────── Sidecar loading ───────────────────────

def load_sidecar(path: Path) -> tuple[date, list[dict]]:
    """Read the newsletter sidecar and map its stories to renderer item dicts.

    The newsletter's `deep_summary` (rich multi-paragraph) becomes each item's `summary`
    (the "Analysis" both renderers consume); the one-liner is kept as `blurb`. We prefer the
    newsletter's `headline` (its LLM-normalized cool title) over the raw source `title`, which
    for AI sources is sometimes a bare handle like "@soumithchintala"."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    day = date.fromisoformat(data["date"])
    items: list[dict] = []
    for s in data.get("stories", []):
        title = (s.get("headline") or s.get("title") or "").strip() or "(untitled)"
        items.append({
            "rank": s.get("rank"),
            "title": title,
            "url": s.get("url") or "",
            "source": s.get("source") or "",
            "published": s.get("published") or "",
            "summary": (s.get("deep_summary") or s.get("summary") or "").strip(),
            "blurb": (s.get("summary") or "").strip(),
            "comment_analysis": "",
        })
    return day, items


# ─────────────────────── Cross-day history ───────────────────────

def load_history() -> dict:
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — a corrupt history file must not sink the run
            log.warning("history.json unreadable; starting fresh")
    return {}


def apply_history(items: list[dict], day: date, history: dict) -> None:
    """Set seen_before / earliest_seen / streak_days per item from url history, and record
    today's appearance. The newsletter already cross-day-dedups, so repeats are rare — but
    when the same url resurfaces the classic page can collapse it. Mutates items + history."""
    day_str = day.isoformat()
    for it in items:
        url = it.get("url") or ""
        rec = history.get(url)
        if rec:
            first = date.fromisoformat(rec["first"])
            last = date.fromisoformat(rec["last"])
            it["seen_before"] = True
            it["earliest_seen"] = first
            it["streak_days"] = (day - first).days + 1 if (day - last).days <= 1 else 1
            rec["last"] = day_str
        else:
            it["seen_before"] = False
            it["earliest_seen"] = day
            it["streak_days"] = 1
            if url:
                history[url] = {"first": day_str, "last": day_str}


def save_history(history: dict) -> None:
    AI_DIR.mkdir(parents=True, exist_ok=True)
    tmp = HISTORY_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(history, indent=2), encoding="utf-8")
    tmp.replace(HISTORY_FILE)


# ─────────────────────── Classic page ───────────────────────

def _daily_script(day_str: str) -> str:
    """Collapse-seen + expand-all + read-day tracking for the classic AI page. Self-contained
    (mirrors the HN classic behavior) so it never depends on hn-specific paths."""
    return f"""
<script>
(() => {{
  const key = {json.dumps(READ_KEY)};
  try {{
    const days = JSON.parse(localStorage.getItem(key) || "[]");
    const set = new Set(Array.isArray(days) ? days : []);
    set.add({json.dumps(day_str)});
    localStorage.setItem(key, JSON.stringify([...set]));
  }} catch (_e) {{}}

  const collapseBtn = document.getElementById("collapse-seen-btn");
  const expandBtn = document.getElementById("expand-all-btn");
  function setCollapsed(collapsed) {{
    document.querySelectorAll('section.repo').forEach((sec) => {{
      const seen = sec.dataset.seenBefore === "1";
      const body = sec.querySelector('.repo-body');
      const toggle = sec.querySelector('.repo-toggle');
      const hide = collapsed && seen;
      if (body) body.style.display = hide ? "none" : "";
      if (toggle) {{
        toggle.textContent = hide ? "Show details" : "Hide details";
        toggle.setAttribute("aria-expanded", hide ? "false" : "true");
      }}
    }});
  }}
  if (collapseBtn) collapseBtn.addEventListener("click", () => setCollapsed(true));
  if (expandBtn) expandBtn.addEventListener("click", () => setCollapsed(false));
  document.querySelectorAll('.repo-toggle').forEach((btn) => {{
    btn.addEventListener("click", () => {{
      const body = btn.closest('section.repo').querySelector('.repo-body');
      const open = body.style.display === "none";
      body.style.display = open ? "" : "none";
      btn.textContent = open ? "Hide details" : "Show details";
      btn.setAttribute("aria-expanded", open ? "true" : "false");
    }});
  }});
}})();
</script>
"""


def generate_ai_classic_page(items: list[dict], day: date, known_dates=None) -> str:
    date_str = day.isoformat()
    date_display = format_date_display(day)

    cards = ""
    if not items:
        cards = '<p class="empty-state">No AI edition available for this day.</p>'
    for it in items:
        domain = extract_domain(it.get("url") or "") or "ainews"
        seen_badge = '<span class="seen-badge">Not new today</span>' if it.get("seen_before") else ""
        history_line = (
            f"First seen: {format_date_display(it['earliest_seen'])} | "
            f"Consecutive daily streak: {it['streak_days']} day{'s' if it['streak_days'] != 1 else ''}"
            if it.get("earliest_seen") else "History unavailable"
        )
        blurb_html = f'<p class="lead">{html.escape(it["blurb"])}</p>' if it.get("blurb") else ""
        pub = (it.get("published") or "")[:10]
        meta_bits = " | ".join(
            b for b in (html.escape(domain), html.escape(it.get("source") or ""), html.escape(pub)) if b
        )
        cards += f"""
            <section class="repo" data-seen-before="{1 if it.get('seen_before') else 0}"
                     data-share-title="{html.escape(it['title'], quote=True)}"
                     data-share-url="{html.escape(it.get('url') or '', quote=True)}">
                <div class="repo-header-row">
                    <h3>{it['rank']}. <a href="{html.escape(it.get('url') or '#')}" target="_blank" rel="noopener noreferrer">{html.escape(it['title'])}</a> {seen_badge}</h3>
                    <div class="header-buttons">
                        <button type="button" class="repo-toggle" aria-expanded="true">Hide details</button>
                    </div>
                </div>
                <div class="repo-body">
                    <p class="meta">{meta_bits}</p>
                    <p class="history">{html.escape(history_line)}</p>
                    {blurb_html}
                    <div class="ai-summary">
                        <h4>Analysis</h4>
                        {generate_summary_html(it.get("summary", ""))}
                    </div>
                </div>
            </section>
"""

    cross_html = "\n            ".join(
        f'<a href="{href}">{label}</a>'
        for href, label in cross_edition_links("ai", date_str, known_dates)
    )

    v = get_git_sha()
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI Edition (Classic) - {date_display}</title>
    <link rel="stylesheet" href="../../style.css?v={v}">
</head>
<body data-gtd-edition="ai" data-gtd-date="{date_str}">
    <header>
        <h1>AI Edition - {date_display}</h1>
        <nav>
            <a href="../">&larr; {EDITIONS["ai"].calendar_label}</a>
            <a href="./">Magazine view</a>
            {cross_html}
        </nav>
    </header>
    <main>
        <div class="repo-controls">
            <button id="collapse-seen-btn" type="button">Collapse Stories Not New Today</button>
            <button id="expand-all-btn" type="button">Expand All</button>
        </div>
        <p class="seen-help">Stories marked "Not new today" appeared on one or more previous AI editions.</p>
        <article>
            <div class="repos">
{cards}
            </div>
        </article>
    </main>
    <footer>
        <p>Generated from the AI/LLM Newsletter select digest.</p>
    </footer>
{_daily_script(date_str)}
<script src="../../preference.js?v={v}" defer></script>
</body>
</html>
"""


# ─────────────────────── Calendar index ───────────────────────

def list_ai_dates() -> list[date]:
    """Every date that has a published AI edition (a docs/ai/<date>/ dir)."""
    if not AI_DIR.exists():
        return []
    out = []
    for child in AI_DIR.iterdir():
        if child.is_dir():
            try:
                out.append(date.fromisoformat(child.name))
            except ValueError:
                continue
    return sorted(out)


def generate_ai_index_page(ai_dates: list[date]) -> str:
    v = get_git_sha()
    calendar_html = build_calendar_html(ai_dates, link_prefix="")
    cross_html = "\n            ".join(
        f'<a href="{href}">{label}</a>' for href, label in cross_edition_links("ai")
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI Edition Calendar</title>
    <link rel="stylesheet" href="../style.css?v={v}">
</head>
<body data-gtd-edition="ai">
    <header>
        <h1>AI Edition</h1>
        <nav>
            {cross_html}
        </nav>
    </header>
    <main>
        <p class="seen-help">Daily digest of the AI/LLM newsletter — the 15-20 stories that matter, as a classic list or a magazine.</p>
        <div class="calendars">
{calendar_html}
        </div>
    </main>
{generate_read_days_script(READ_KEY)}
</body>
</html>
"""


# ─────────────────────── Orchestration ───────────────────────

def build_pages(sidecar: Path, force_regenerate=True, known_dates=None) -> tuple[date, int]:
    """Render classic + magazine + calendar for the sidecar's edition. Returns (day, count)."""
    day, items = load_sidecar(sidecar)
    if not items:
        log.warning("sidecar %s has no stories; nothing to render", sidecar)
        return day, 0

    history = load_history()
    apply_history(items, day, history)

    out_dir = AI_DIR / day.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    write_text(out_dir / "classic.html", generate_ai_classic_page(items, day, known_dates))

    # Magazine (index.html). Fails open to a classic-view redirect (same contract as hn/gh).
    try:
        generate_morning_edition(day, items, source="ai", force_regenerate=force_regenerate, known_dates=known_dates)
    except Exception as exc:  # noqa: BLE001
        log.exception("AI magazine generation failed for %s: %s", day, exc)
        (out_dir / "index.html").write_text(
            '<!doctype html><meta http-equiv="refresh" content="0; url=classic.html">'
            '<link rel="canonical" href="classic.html"><title>Redirecting…</title>'
            '<p><a href="classic.html">classic view</a></p>'
        )
        notify_gotify("AI edition degraded", f"Magazine failed for {day}; classic fallback published.\n{exc}")

    save_history(history)
    write_text(AI_DIR / "index.html", generate_ai_index_page(list_ai_dates()))
    write_dates_manifest()
    return day, len(items)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Render the AI newsletter digest as classic + magazine pages.")
    ap.add_argument("--sidecar", type=Path, default=DEFAULT_SIDECAR,
                    help="path to the newsletter edition JSON (default: latest.json)")
    ap.add_argument("--no-publish", action="store_true",
                    help="render pages only; skip git push + email")
    ap.add_argument("--reuse-assignments", action="store_true",
                    help="reuse cached magazine archetype assignments (no LLM call)")
    args = ap.parse_args()

    if not args.sidecar.exists():
        log.error("sidecar not found: %s", args.sidecar)
        raise SystemExit(1)

    day, count = build_pages(args.sidecar, force_regenerate=not args.reuse_assignments)
    log.info("AI edition: rendered %d stories for %s", count, day)
    if count == 0 or args.no_publish:
        return

    if not git_commit_and_push():
        log.info("no docs changes to publish; skipping email")
        return

    page_url = f"{GITHUB_PAGES_URL}ai/{day.isoformat()}/"
    if wait_for_pages_live([page_url]):
        send_email(to_address=email_to_address, subject="links",
                   body=f"AI Edition:\n{page_url}")
    else:
        log.error("AI page did not go live; skipping email")
        notify_gotify("AI edition: page did not go live", page_url)
    log.info("Done. AI page: %s", page_url)


if __name__ == "__main__":
    main()
