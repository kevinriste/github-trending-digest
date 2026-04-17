"""Morning Edition magazine renderer.

Takes the ten HN rows from `build_hn_view_rows` and renders a magazine-style
page at `docs/hn/<date>/index.html` (the new default). The old card-style
page is written alongside at `docs/hn/<date>/classic.html` and cross-linked.

A single Gemini call picks a distinct spread archetype for each story (10
chosen from a catalog of 15) and writes editorial copy (kicker, headline,
lede) tuned to that archetype's voice. Assignments are cached as
`assignments.json` next to the generated HTML.

Each spread has two CTAs: "Read →" (outbound to source) and "Full analysis ↓"
(anchor to the Dossier section at the end of the page, which presents the
first analysis paragraph and three reader-reaction bullets per story).
"""

from __future__ import annotations

import argparse
import html as html_mod
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from google import genai


REPO_ROOT = Path(__file__).parent
# The magazine now lives at the canonical HN URL; the classic card page is a
# sibling at classic.html so existing calendar and GitHub-page links continue
# to land on the new default.
OUTPUT_DIR = REPO_ROOT / "docs" / "hn"
MODEL = "gemini-3.1-flash-lite-preview"
NUM_STORIES = 10
READ_DAYS_KEY_HN = "gtd:read_days:hn:v1"


@dataclass(frozen=True)
class Archetype:
    id: str
    name: str
    best_for: str


ARCHETYPES: list[Archetype] = [
    Archetype(
        "stat-hero",
        "Stat Hero",
        "stories whose headline centers on a specific number, percentage, or milestone. The layout features one huge display numeral. If picked, you must also return a `big_figure` string (e.g. \"50%\", \"10,000\", \"$1B\")",
    ),
    Archetype(
        "midnight",
        "Midnight",
        "AI infrastructure, privacy tech, cryptography, decentralized systems, or nocturnal and hidden themes. Dark palette with a purple glow. Tone: quiet and technical",
    ),
    Archetype(
        "alert-stamp",
        "Rose Alert",
        "platform failures, abuse reports, public call-outs, accountability stories. Rose background with a rotated red stamp. Tone: wry, indignant, or deadpan",
    ),
    Archetype(
        "academic-drop-cap",
        "Academic",
        "peer-reviewed papers, scientific research, scholarly PDFs, data-heavy studies. Ecru parchment, two-column with a drop cap. Tone: scholarly and measured",
    ),
    Archetype(
        "terminal",
        "Terminal",
        "dev tools, CLI releases, open-source governance, code or license policy, compiler and programming-language news. Black with green monospace. Tone: clipped, terse, prompt-like",
    ),
    Archetype(
        "editorial-pullquote",
        "Editorial Op-Ed",
        "op-eds, think-pieces, industry analysis, essays with a strong quotable thesis. Dark with gold accent. If picked, you must also return a `pullquote` string: one sentence phrased as display type (do not wrap in quotation marks — the layout adds them)",
    ),
    Archetype(
        "caution-tape",
        "Caution Tape",
        "CVEs, exploits, vulnerabilities, security alerts, breach announcements. Yellow with black diagonal stripes. Tone: clipped warning, no ornament",
    ),
    Archetype(
        "notebook",
        "Notebook",
        "personal essays, reflective pieces, craft and process writing, analog or paper themes, writing about thinking. Cream ruled lines. Tone: gentle and reflective",
    ),
    Archetype(
        "mint-pattern",
        "Mint Pattern",
        "programming folklore, classic CS, algorithm deep-dives, benchmark debates, low-level trivia. Mint background with a code-word pattern. Tone: playful-technical",
    ),
    Archetype(
        "pastel-playful",
        "Pastel Finale",
        "whimsical projects, art or creative software, intentionally useless things, games, curiosities. Pink pastel with an alphabet accent. Tone: light and delighted",
    ),
    # New archetypes —
    Archetype(
        "product-plate",
        "Product Plate",
        "hardware/product/model launches and keynote-style releases (new chips, laptops, LLM model versions, GPU announcements, official availability). Silver keynote-slide aesthetic. Tone: clean, declarative, spec-sheet",
    ),
    Archetype(
        "archive",
        "Archive",
        "retro or historical pieces, vintage computing, rediscovered documents, decades-old software, anniversary posts, (YYYY) dated blog posts from years ago. Sepia letterpress with a dated masthead. Tone: measured, archival",
    ),
    Archetype(
        "obituary",
        "Obituary",
        "shutdowns, EOLs, deprecations, project sunsets, service closures, formal departures. Black-bordered memorial frame with a dagger ornament. Tone: somber, minimal",
    ),
    Archetype(
        "blueprint",
        "Blueprint",
        "Show HN posts, maker projects, personal builds, reverse-engineering write-ups, hands-on hardware hacking, DIY engineering. Graph-paper blue with schematic accents. Tone: hands-on, build-log",
    ),
    Archetype(
        "observatory",
        "Observatory",
        "astronomy, space news, speculative science and physics, non-peer-reviewed science reporting, cosmic imagery and discoveries. Deep indigo starfield. Tone: quiet wonder, scientific",
    ),
]

ARCHETYPE_BY_ID: dict[str, Archetype] = {a.id: a for a in ARCHETYPES}

ORDINAL_LABELS = [
    "01", "02", "03", "04", "05", "06", "07", "08", "09", "10",
]
ROMAN_LABELS = [
    "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X",
]


# ─────────────────────── Content helpers ───────────────────────

# The stored HN summaries contain two paragraphs separated by a blank line.
# We want only the first paragraph on both the magazine and the classic page.
def first_paragraph(summary_text: str) -> str:
    """Return the summary text (stripped). Prompt now handles truncation."""
    return summary_text.strip()


# The stored comment analyses are now 3-bullet lists.
_BULLET_PREFIX_RE = re.compile(r"^\s*(?:Bullet\s*\d+[:.]\s*|[-*•]\s*)", re.IGNORECASE)


def parse_bullets(text: str) -> list[str]:
    if not text:
        return []
    out: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        cleaned = _BULLET_PREFIX_RE.sub("", line).strip()
        if cleaned:
            out.append(cleaned)
    return out


def limit_bullets(text: str, n: int = 3) -> list[str]:
    """Return the first n bullets. Prompt now handles truncation."""
    return parse_bullets(text)[:n]


# ─────────────────────── Gemini call ───────────────────────


_gemini_client: genai.Client | None = None


def _client() -> genai.Client:
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    return _gemini_client


def _build_prompt(items: list[dict]) -> str:
    catalog_lines = "\n".join(
        f"- {a.id} — best for: {a.best_for}" for a in ARCHETYPES
    )

    story_blocks = []
    for n, item in enumerate(items, start=1):
        title = item.get("title", "") or ""
        url = item.get("url") or item.get("discussion_url") or ""
        domain = _extract_domain(url) or "news.ycombinator.com"
        score = item.get("score", 0)
        comments = item.get("comment_count", 0)
        # The summary and comment_analysis are now short by design.
        summary = (item.get("summary") or "").strip()
        comment_analysis = (item.get("comment_analysis") or "").strip()

        if len(summary) > 1600:
            summary = summary[:1600].rsplit(" ", 1)[0] + "…"
        if len(comment_analysis) > 900:
            comment_analysis = comment_analysis[:900].rsplit(" ", 1)[0] + "…"

        block = (
            f"### Story {n}\n"
            f"Title: {title}\n"
            f"Domain: {domain}\n"
            f"Score: {score} pts · {comments} comments\n"
            f"URL: {url}\n"
            f"Analysis: {summary or '(none)'}\n"
        )
        if comment_analysis:
            block += f"Reader reactions: {comment_analysis}\n"
        story_blocks.append(block)

    stories_section = "\n".join(story_blocks)

    return f"""You are the editor of a daily curated magazine called "Morning Edition." Today's issue contains exactly ten stories from the Hacker News front page. Your job is to assign each story to a distinct visual spread archetype and write the editorial copy for that spread.

# Spread archetype catalog

You have fifteen archetypes to choose from. You must pick exactly ten for today — one per story — and all ten picks must be distinct archetype ids. Unused archetypes simply don't appear today. Choose the archetype whose "best for" description most closely matches the story's theme; if several stories could fit the same archetype, pick the best fit and send the others to their next-best archetypes.

{catalog_lines}

# Stories (in HN rank order)

{stories_section}

# Your task

For each of the ten stories, produce one JSON object with these fields:

- "rank": integer, 1..10, matching the story's HN rank above
- "archetype_id": one of the fifteen archetype ids. All ten picks must be distinct.
- "kicker": a 1-3 word department label fit to the archetype (e.g., "Infrastructure", "Op-Ed / Security", "CVE Watch", "Finale", "Keynote", "Archival Desk", "EOL Notice", "Build Log", "Observations"). Title Case is fine; the layout handles uppercasing.
- "headline": a rewritten magazine-voice headline, 3-12 words. Prefer active voice, present tense, concrete. It may differ from the source title if it reads better, but it must honor the facts in the Analysis. No clickbait.
- "lede": 2-3 sentences of editorial prose that sets up the story in the voice the archetype suggests (scholarly for academic-drop-cap, clipped for terminal, wry for alert-stamp, quiet for midnight, declarative for product-plate, archival for archive, somber for obituary, build-log for blueprint, cosmic for observatory, etc.). Stay specific. No hype. No meta-commentary about the magazine.
- "big_figure": only when archetype_id is "stat-hero"; otherwise null. A short display string such as "50%", "10,000", "$1B".
- "pullquote": only when archetype_id is "editorial-pullquote"; otherwise null. One sentence phrased as display type. Do NOT include surrounding quotation marks — the layout adds them.

Return a JSON array of exactly ten objects, in HN rank order (rank 1 first, rank 10 last). Output nothing outside the JSON array. Do not wrap the output in a code fence.
"""


_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def _parse_response(raw: str) -> list[dict]:
    text = raw.strip()
    if text.startswith("```"):
        text = _CODE_FENCE_RE.sub("", text).strip()
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON array found in response")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, list):
        raise ValueError("response root is not a JSON array")
    return data


def _validate(assignments: list[dict]) -> None:
    if len(assignments) != NUM_STORIES:
        raise ValueError(f"expected {NUM_STORIES} assignments, got {len(assignments)}")

    seen_ids: set[str] = set()
    for i, a in enumerate(assignments, start=1):
        arch = a.get("archetype_id")
        if arch not in ARCHETYPE_BY_ID:
            raise ValueError(f"entry {i}: unknown archetype_id {arch!r}")
        if arch in seen_ids:
            raise ValueError(f"entry {i}: duplicate archetype_id {arch!r}")
        seen_ids.add(arch)
        for key in ("kicker", "headline", "lede"):
            if not isinstance(a.get(key), str) or not a[key].strip():
                raise ValueError(f"entry {i}: missing/empty field {key!r}")
        if arch == "stat-hero" and not (a.get("big_figure") or "").strip():
            raise ValueError(f"entry {i}: stat-hero requires big_figure")
        if arch == "editorial-pullquote" and not (a.get("pullquote") or "").strip():
            raise ValueError(f"entry {i}: editorial-pullquote requires pullquote")


def pick_editorial(items: list[dict]) -> list[dict]:
    """Single Gemini call → assignments with archetype + editorial copy.

    Retries once on parse/validation failure with a clarifying reminder.
    """
    if len(items) < NUM_STORIES:
        raise ValueError(f"need {NUM_STORIES} stories, got {len(items)}")

    items = items[:NUM_STORIES]
    prompt = _build_prompt(items)

    client = _client()
    last_error: Exception | None = None
    for attempt in range(2):
        contents = [prompt]
        if attempt == 1 and last_error is not None:
            contents.append(
                "Your previous response could not be parsed: "
                f"{last_error}. Return ONLY the JSON array of exactly ten objects, "
                "nothing else, with all ten archetype_id values distinct and drawn "
                "from the fifteen archetypes in the catalog above."
            )
        response = client.models.generate_content(model=MODEL, contents=contents)
        raw = (response.text or "").strip()
        try:
            assignments = _parse_response(raw)
            _validate(assignments)
            assignments.sort(key=lambda a: int(a.get("rank", 0)))
            return assignments
        except Exception as exc:
            logging.warning("Morning Edition LLM response invalid (attempt %d): %s", attempt + 1, exc)
            last_error = exc

    raise RuntimeError(f"LLM did not produce a valid response after 2 attempts: {last_error}")


# ─────────────────────── Cache ───────────────────────


def _cache_path(day: date) -> Path:
    return OUTPUT_DIR / day.isoformat() / "assignments.json"


def load_cached_assignments(day: date) -> list[dict] | None:
    path = _cache_path(day)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        _validate(data)
        return data
    except Exception as exc:
        logging.warning("Morning Edition cache %s is invalid: %s", path, exc)
        return None


def save_cached_assignments(day: date, assignments: list[dict]) -> None:
    path = _cache_path(day)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(assignments, indent=2, ensure_ascii=False), encoding="utf-8")


# ─────────────────────── Rendering helpers ───────────────────────


_DOMAIN_RE = re.compile(r"^https?://([^/]+)", re.IGNORECASE)


def _extract_domain(url: str) -> str:
    if not url:
        return ""
    match = _DOMAIN_RE.match(url)
    if not match:
        return ""
    host = match.group(1).lower()
    return host[4:] if host.startswith("www.") else host


def _h(text: str | None) -> str:
    return html_mod.escape(text or "", quote=True)


def _meta_line(item: dict) -> str:
    domain = _extract_domain(item.get("url") or "") or "news.ycombinator.com"
    score = item.get("score", 0)
    comments = item.get("comment_count", 0)
    return f"{_h(domain)} &nbsp;·&nbsp; {score} pts &nbsp;·&nbsp; {comments} comments"


def _read_href(item: dict) -> str:
    return _h(item.get("url") or item.get("discussion_url") or "")


def _links(item: dict, n: int) -> str:
    """Primary outbound + secondary in-page Dossier anchor."""
    return (
        f'<div class="spread-links">'
        f'<a class="read-more" href="{_read_href(item)}" target="_blank" rel="noopener">Read →</a>'
        f'<a class="dossier-link" href="#dossier-{n}">Full analysis ↓</a>'
        f'</div>'
    )


# ─────────────────────── Archetype renderers ───────────────────────


def _render_stat_hero(n: int, a: dict, item: dict) -> str:
    big = _h(a.get("big_figure") or "")
    return f"""  <section class="spread arc-stat-hero">
    <div class="numeral">{big}</div>
    <div class="kicker">N<sup>o</sup> {ORDINAL_LABELS[n-1]} · {_h(a['kicker'])}</div>
    <h2>{_h(a['headline'])}</h2>
    <p class="story-meta">{_meta_line(item)}</p>
    <p class="lede">{_h(a['lede'])}</p>
    {_links(item, n)}
  </section>"""


def _render_midnight(n: int, a: dict, item: dict) -> str:
    return f"""  <section class="spread arc-midnight">
    <div class="numeral">{ORDINAL_LABELS[n-1]}</div>
    <div class="body">
      <div class="kicker">// {_h(a['kicker'])}</div>
      <h2>{_h(a['headline'])}</h2>
      <p class="story-meta">{_meta_line(item)}</p>
      <p class="lede">{_h(a['lede'])}</p>
      {_links(item, n)}
    </div>
  </section>"""


def _render_alert_stamp(n: int, a: dict, item: dict) -> str:
    return f"""  <section class="spread arc-alert-stamp">
    <div class="stamp">Alert</div>
    <div class="numeral">{ORDINAL_LABELS[n-1]}</div>
    <div class="kicker">{_h(a['kicker'])}</div>
    <h2>{_h(a['headline'])}</h2>
    <p class="story-meta">{_meta_line(item)}</p>
    <p class="lede">{_h(a['lede'])}</p>
    {_links(item, n)}
  </section>"""


def _render_academic(n: int, a: dict, item: dict) -> str:
    lede = a["lede"]
    sentences = re.split(r"(?<=[.!?])\s+", lede.strip())
    mid = max(1, len(sentences) // 2)
    left = " ".join(sentences[:mid]) or lede
    right = " ".join(sentences[mid:])
    right_html = f"<p>{_h(right)}</p>" if right.strip() else ""
    return f"""  <section class="spread arc-academic-drop-cap">
    <div class="paper-head">
      <span>{_h(a['kicker'])}</span>
      <span class="numeral-roman">{ROMAN_LABELS[n-1]}</span>
      <span>{_h(_extract_domain(item.get('url') or '') or 'news.ycombinator.com')}</span>
    </div>
    <h2>{_h(a['headline'])}</h2>
    <p class="story-meta">{_meta_line(item)}</p>
    <div class="cols">
      <div><p>{_h(left)}</p></div>
      <div>{right_html}{_links(item, n)}</div>
    </div>
  </section>"""


def _render_terminal(n: int, a: dict, item: dict) -> str:
    return f"""  <section class="spread arc-terminal">
    <div class="prompt-row">user@morning-edition:~$ cat story_{ORDINAL_LABELS[n-1]}.md</div>
    <p class="numeral">{ORDINAL_LABELS[n-1]}</p>
    <p class="story-meta">{_meta_line(item)}</p>
    <h2>{_h(a['headline'])}</h2>
    <p class="lede">{_h(a['lede'])}<span class="cursor"></span></p>
    {_links(item, n)}
  </section>"""


def _render_editorial_pullquote(n: int, a: dict, item: dict) -> str:
    pullquote = _h(a.get("pullquote") or "")
    return f"""  <section class="spread arc-editorial-pullquote">
    <div class="left">
      <div class="numeral">{ORDINAL_LABELS[n-1]}</div>
      <div class="kicker">{_h(a['kicker'])}</div>
      <h2>{_h(a['headline'])}</h2>
      <p class="story-meta">{_meta_line(item)}</p>
      {_links(item, n)}
    </div>
    <blockquote class="pullquote">{pullquote}</blockquote>
  </section>"""


def _render_caution_tape(n: int, a: dict, item: dict) -> str:
    return f"""  <section class="spread arc-caution-tape">
    <div class="tag">Vulnerability</div>
    <div class="numeral">{ORDINAL_LABELS[n-1]}</div>
    <div class="kicker">{_h(a['kicker'])}</div>
    <h2>{_h(a['headline'])}</h2>
    <p class="story-meta">{_meta_line(item)}</p>
    <p class="lede">{_h(a['lede'])}</p>
    {_links(item, n)}
  </section>"""


def _render_notebook(n: int, a: dict, item: dict) -> str:
    return f"""  <section class="spread arc-notebook">
    <div class="numeral">{ORDINAL_LABELS[n-1]}</div>
    <div class="kicker">{_h(a['kicker'])}</div>
    <h2>{_h(a['headline'])}</h2>
    <p class="story-meta">{_meta_line(item)}</p>
    <p class="lede">{_h(a['lede'])}</p>
    {_links(item, n)}
  </section>"""


def _render_mint_pattern(n: int, a: dict, item: dict) -> str:
    return f"""  <section class="spread arc-mint-pattern">
    <div class="numeral-block">
      <div class="numeral">{ORDINAL_LABELS[n-1]}</div>
    </div>
    <p class="kicker">// {_h(a['kicker'])}</p>
    <h2>{_h(a['headline'])}</h2>
    <p class="story-meta">{_meta_line(item)}</p>
    <p class="lede">{_h(a['lede'])}</p>
    {_links(item, n)}
  </section>"""


def _render_pastel_playful(n: int, a: dict, item: dict) -> str:
    grid = "".join(f"<span>{ch}</span>" for ch in "ABCDEFGHIJKLM")
    return f"""  <section class="spread arc-pastel-playful">
    <div class="alpha-grid">{grid}</div>
    <div class="numeral">{ORDINAL_LABELS[n-1]}</div>
    <div class="kicker">{_h(a['kicker'])}</div>
    <h2>{_h(a['headline'])}</h2>
    <p class="story-meta">{_meta_line(item)}</p>
    <p class="lede">{_h(a['lede'])}</p>
    {_links(item, n)}
  </section>"""


def _render_product_plate(n: int, a: dict, item: dict) -> str:
    return f"""  <section class="spread arc-product-plate">
    <div class="product-chip">Release &nbsp;·&nbsp; N<sup>o</sup> {ORDINAL_LABELS[n-1]}</div>
    <div class="kicker">{_h(a['kicker'])}</div>
    <h2>{_h(a['headline'])}</h2>
    <div class="product-rule"></div>
    <p class="story-meta">{_meta_line(item)}</p>
    <p class="lede">{_h(a['lede'])}</p>
    {_links(item, n)}
  </section>"""


def _render_archive(n: int, a: dict, item: dict) -> str:
    return f"""  <section class="spread arc-archive">
    <div class="archive-head">
      <span>Filed</span>
      <span>Archival Desk · N<sup>o</sup> {ROMAN_LABELS[n-1]}</span>
      <span>From the Vault</span>
    </div>
    <div class="kicker">{_h(a['kicker'])}</div>
    <h2>{_h(a['headline'])}</h2>
    <p class="story-meta">{_meta_line(item)}</p>
    <p class="lede">{_h(a['lede'])}</p>
    {_links(item, n)}
  </section>"""


def _render_obituary(n: int, a: dict, item: dict) -> str:
    return f"""  <section class="spread arc-obituary">
    <div class="obit-frame">
      <div class="ornament">†</div>
      <div class="kicker">{_h(a['kicker'])} · N<sup>o</sup> {ORDINAL_LABELS[n-1]}</div>
      <h2>{_h(a['headline'])}</h2>
      <p class="story-meta">{_meta_line(item)}</p>
      <p class="lede">{_h(a['lede'])}</p>
      {_links(item, n)}
    </div>
  </section>"""


def _render_blueprint(n: int, a: dict, item: dict) -> str:
    return f"""  <section class="spread arc-blueprint">
    <div class="corner tl">┌</div><div class="corner tr">┐</div>
    <div class="corner bl">└</div><div class="corner br">┘</div>
    <div class="numeral">FIG.&nbsp;{ORDINAL_LABELS[n-1]}</div>
    <div class="kicker">{_h(a['kicker'])}</div>
    <h2>{_h(a['headline'])}</h2>
    <p class="story-meta">{_meta_line(item)}</p>
    <p class="lede">{_h(a['lede'])}</p>
    {_links(item, n)}
  </section>"""


def _render_observatory(n: int, a: dict, item: dict) -> str:
    return f"""  <section class="spread arc-observatory">
    <div class="starfield"></div>
    <div class="numeral">Obs.&nbsp;{ORDINAL_LABELS[n-1]}</div>
    <div class="kicker">{_h(a['kicker'])}</div>
    <h2>{_h(a['headline'])}</h2>
    <p class="story-meta">{_meta_line(item)}</p>
    <p class="lede">{_h(a['lede'])}</p>
    {_links(item, n)}
  </section>"""


ARCHETYPE_RENDERERS = {
    "stat-hero": _render_stat_hero,
    "midnight": _render_midnight,
    "alert-stamp": _render_alert_stamp,
    "academic-drop-cap": _render_academic,
    "terminal": _render_terminal,
    "editorial-pullquote": _render_editorial_pullquote,
    "caution-tape": _render_caution_tape,
    "notebook": _render_notebook,
    "mint-pattern": _render_mint_pattern,
    "pastel-playful": _render_pastel_playful,
    "product-plate": _render_product_plate,
    "archive": _render_archive,
    "obituary": _render_obituary,
    "blueprint": _render_blueprint,
    "observatory": _render_observatory,
}


# ─────────────────────── Page chrome ───────────────────────


def _render_masthead(day: date) -> str:
    date_display = day.strftime("%A, %B %-d, %Y")
    return f"""  <header class="masthead">
    <div class="masthead-nav">
      <a href="../">HN Calendar</a>
      <span>·</span>
      <a href="../../{day.isoformat()}/">GitHub Trending</a>
      <span>·</span>
      <a href="classic.html">Classic view</a>
    </div>
    <div class="tagline">The Morning Edition</div>
    <h1 class="frnc">Ten stories, before your coffee.</h1>
    <div class="issue-line">
      <span>Vol. I</span>
      <span>{date_display}</span>
      <span>HN Front Page Digest</span>
    </div>
  </header>"""


def _render_dossier(items: list[dict], assignments: list[dict]) -> str:
    entries: list[str] = []
    for i, (a, item) in enumerate(zip(assignments, items), start=1):
        arch = ARCHETYPE_BY_ID.get(a["archetype_id"])
        arch_name = arch.name if arch else a["archetype_id"]
        analysis = (item.get("summary") or "").strip()
        bullets = parse_bullets(item.get("comment_analysis") or "")

        analysis_html = (
            f'<p>{_h(analysis)}</p>'
            if analysis
            else '<p class="muted"><em>Analysis not available.</em></p>'
        )
        if bullets:
            reactions_html = (
                '<h4>Reader Reactions</h4>\n      '
                + "\n      ".join(f"<p>{_h(b)}</p>" for b in bullets)
            )
        else:
            reactions_html = ""

        discussion_url = _h(item.get("discussion_url") or "")
        source_url = _read_href(item)
        domain = _extract_domain(item.get("url") or "") or "news.ycombinator.com"
        score = item.get("score", 0)
        comments = item.get("comment_count", 0)

        entries.append(f"""    <article id="dossier-{i}" class="dossier-entry">
      <div class="dossier-meta">N<sup>o</sup> {ORDINAL_LABELS[i-1]} &nbsp;·&nbsp; {_h(arch_name)} &nbsp;·&nbsp; {_h(a['kicker'])}</div>
      <h3>{_h(item.get('title') or a['headline'])}</h3>
      <p class="dossier-source">
        <a href="{source_url}" target="_blank" rel="noopener">{_h(domain)}</a>
        &nbsp;·&nbsp; {score} pts &nbsp;·&nbsp; {comments} comments
        &nbsp;·&nbsp; <a href="{discussion_url}" target="_blank" rel="noopener">discussion</a>
      </p>
      <h4>Analysis</h4>
      {analysis_html}
      {reactions_html}
    </article>""")

    entries_html = "\n".join(entries)
    return f"""  <section id="dossier" class="dossier">
    <div class="dossier-head">
      <div class="tagline">Appendix</div>
      <h2>The Dossier</h2>
      <p class="intro">Full analysis and reader reactions for today's ten stories. <a href="#top">↑ Back to the edition</a></p>
    </div>
{entries_html}
  </section>"""


def _render_colophon(day: date) -> str:
    return f"""  <footer class="colophon">
    <p class="sig">— that's the edition.</p>
    <p class="meta">Set in Fraunces &amp; Inter · Compiled {day.strftime('%B %-d, %Y')}</p>
    <p class="classic-link"><a href="classic.html">Prefer the classic card view? →</a></p>
  </footer>"""


def _render_readtracker(day: date) -> str:
    day_str = day.isoformat()
    # Marks this day as "read" in the same localStorage key used by the classic
    # page, so the HN calendar continues to recognise visited editions.
    return f"""<script>
(() => {{
  const key = {json.dumps(READ_DAYS_KEY_HN)};
  const day = {json.dumps(day_str)};
  let stored = [];
  try {{
    stored = JSON.parse(localStorage.getItem(key) || "[]");
    if (!Array.isArray(stored)) stored = [];
  }} catch (_) {{ stored = []; }}
  if (!stored.includes(day)) {{
    stored.push(day);
    stored.sort();
    localStorage.setItem(key, JSON.stringify(stored));
  }}
}})();
</script>"""


def render_page(day: date, items: list[dict], assignments: list[dict]) -> str:
    date_display = day.strftime("%A, %B %-d, %Y")
    spreads: list[str] = []
    for i, (a, item) in enumerate(zip(assignments, items), start=1):
        renderer = ARCHETYPE_RENDERERS[a["archetype_id"]]
        spreads.append(renderer(i, a, item))
    spreads_html = "\n\n".join(spreads)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Morning Edition — {date_display}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght,SOFT@0,9..144,300..900,0..100;1,9..144,300..900,0..100&family=Inter:wght@300;400;500;600;700;800;900&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
<style>
{CSS}
</style>
</head>
<body id="top">

{_render_masthead(day)}

{spreads_html}

{_render_dossier(items, assignments)}

{_render_colophon(day)}

{_render_readtracker(day)}

</body>
</html>
"""


# ─────────────────────── Entry point ───────────────────────


def generate_morning_edition(
    day: date,
    items: list[dict],
    force_regenerate: bool = False,
) -> Path | None:
    """Generate the Morning Edition page for `day`. Returns the output path, or None on failure.

    If `force_regenerate` is False and a valid `assignments.json` cache already
    exists, the page is re-rendered from cache without calling the LLM.
    """
    if len(items) < NUM_STORIES:
        logging.warning(
            "Morning Edition: need %d stories for %s, have %d — skipping",
            NUM_STORIES, day, len(items),
        )
        return None

    items = items[:NUM_STORIES]

    assignments: list[dict] | None = None
    if not force_regenerate:
        assignments = load_cached_assignments(day)
        if assignments is not None:
            logging.info("Morning Edition: using cached assignments for %s", day)

    if assignments is None:
        logging.info("Morning Edition: calling Gemini for %s", day)
        assignments = pick_editorial(items)
        save_cached_assignments(day, assignments)

    out_dir = OUTPUT_DIR / day.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "index.html"
    out_file.write_text(render_page(day, items, assignments), encoding="utf-8")
    logging.info("Morning Edition: wrote %s", out_file)
    return out_file


# ─────────────────────── CSS ───────────────────────

CSS = r"""
  :root { --ink: #121212; --paper: #f5f1e8; }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; }
  body {
    font-family: 'Inter', system-ui, sans-serif;
    font-size: 20px;
    line-height: 1.5;
    color: var(--ink);
    background: var(--paper);
    -webkit-font-smoothing: antialiased;
    text-rendering: optimizeLegibility;
  }
  a { color: inherit; }
  a:hover { opacity: 0.7; }
  .frnc { font-family: 'Fraunces', Georgia, serif; }
  .mono { font-family: 'JetBrains Mono', ui-monospace, monospace; }

  /* ───── MASTHEAD ───── */
  .masthead { padding: 2rem 6vw 2.5rem; border-bottom: 3px double #121212; background: #f5f1e8; text-align: center; }
  .masthead-nav {
    display: flex;
    justify-content: center;
    gap: 1.2rem;
    align-items: baseline;
    margin-bottom: 2rem;
    font-weight: 600;
    font-size: 1rem;
    letter-spacing: 0.2em;
    text-transform: uppercase;
  }
  .masthead-nav a { text-decoration: none; border-bottom: 2px solid transparent; padding-bottom: 2px; }
  .masthead-nav a:hover { border-bottom-color: #121212; opacity: 1; }
  .masthead-nav span { opacity: 0.4; }
  .masthead .tagline { font-weight: 600; font-size: 1.2rem; letter-spacing: 0.4em; text-transform: uppercase; margin-bottom: 1.2rem; }
  .masthead h1 {
    font-family: 'Fraunces', Georgia, serif;
    font-weight: 900;
    font-style: italic;
    font-size: clamp(4rem, 13vw, 12rem);
    line-height: 0.88;
    letter-spacing: -0.035em;
    margin: 0 0 1rem;
    font-variation-settings: "SOFT" 50;
  }
  .masthead .issue-line {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    max-width: 1000px;
    margin: 2rem auto 0;
    padding-top: 1.25rem;
    border-top: 1px solid #121212;
    font-weight: 500;
    font-size: 1.1rem;
    letter-spacing: 0.15em;
    text-transform: uppercase;
  }

  /* ───── SPREAD BASE ───── */
  .spread {
    min-height: 100vh;
    padding: 6vh 6vw;
    position: relative;
    overflow: hidden;
    display: flex;
    flex-direction: column;
    justify-content: center;
  }
  .spread .story-meta { font-weight: 600; font-size: 1rem; letter-spacing: 0.28em; text-transform: uppercase; }
  .spread-links {
    display: flex;
    flex-wrap: wrap;
    gap: 2rem 2.5rem;
    align-items: baseline;
    margin-top: 2.5rem;
  }
  .spread .read-more {
    display: inline-block;
    font-size: 1.1rem;
    font-weight: 700;
    letter-spacing: 0.15em;
    text-transform: uppercase;
    text-decoration: none;
    padding-bottom: 0.4rem;
    border-bottom: 3px solid currentColor;
  }
  .spread .dossier-link {
    font-size: 1rem;
    font-weight: 500;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    text-decoration: none;
    opacity: 0.7;
    padding-bottom: 0.3rem;
    border-bottom: 1px solid currentColor;
  }
  .spread .dossier-link:hover { opacity: 1; }

  /* ───── STAT HERO ───── */
  .arc-stat-hero { background: #eee6d3; color: #0d1a2e; padding: 8vh 6vw; }
  .arc-stat-hero .numeral {
    font-family: 'Fraunces', serif;
    font-weight: 900;
    font-style: italic;
    font-size: clamp(12rem, 36vw, 34rem);
    line-height: 0.8;
    letter-spacing: -0.06em;
    color: #0d1a2e;
    position: absolute;
    right: -2vw;
    top: 8vh;
    opacity: 0.94;
    pointer-events: none;
    white-space: nowrap;
  }
  .arc-stat-hero .kicker { font-weight: 700; font-size: 1.15rem; letter-spacing: 0.4em; text-transform: uppercase; color: #c13830; position: relative; z-index: 2; margin-bottom: 1.5rem; }
  .arc-stat-hero h2 { font-family: 'Fraunces', serif; font-weight: 800; font-size: clamp(3rem, 8vw, 7.5rem); line-height: 0.95; letter-spacing: -0.03em; max-width: 60%; margin: 0 0 2rem; position: relative; z-index: 2; }
  .arc-stat-hero .lede { font-family: 'Fraunces', serif; font-size: clamp(1.4rem, 2.2vw, 2rem); line-height: 1.45; max-width: 55%; font-weight: 400; position: relative; z-index: 2; margin: 0; }
  .arc-stat-hero .story-meta { color: #c13830; position: relative; z-index: 2; }
  .arc-stat-hero .spread-links { position: relative; z-index: 2; }

  /* ───── MIDNIGHT ───── */
  .arc-midnight { background: radial-gradient(ellipse at 70% 30%, #1a1235 0%, #05050c 65%); color: #e9e4ff; }
  .arc-midnight::before {
    content: "";
    position: absolute;
    inset: 0;
    background-image:
      radial-gradient(circle at 20% 80%, rgba(180,120,255,0.18), transparent 50%),
      radial-gradient(circle at 80% 20%, rgba(120,200,255,0.12), transparent 55%);
    pointer-events: none;
  }
  .arc-midnight .numeral {
    font-family: 'Fraunces', serif;
    font-size: clamp(8rem, 22vw, 20rem);
    font-weight: 300;
    font-style: italic;
    color: transparent;
    -webkit-text-stroke: 2px rgba(233,228,255,0.45);
    line-height: 1;
    position: absolute;
    top: 6vh;
    left: 5vw;
    letter-spacing: -0.05em;
  }
  .arc-midnight .body { position: relative; z-index: 2; margin-top: 12rem; max-width: 90%; }
  .arc-midnight .kicker { font-family: 'JetBrains Mono', monospace; font-size: 1.1rem; letter-spacing: 0.2em; text-transform: uppercase; color: #b48bff; margin-bottom: 2rem; }
  .arc-midnight h2 { font-weight: 800; font-size: clamp(3rem, 7.5vw, 6.5rem); line-height: 1; letter-spacing: -0.035em; margin: 0 0 2.5rem; }
  .arc-midnight .lede { font-size: 1.35rem; line-height: 1.55; color: #cdc6e8; max-width: 780px; margin: 0; }
  .arc-midnight .story-meta { color: #8a7cc6; margin-bottom: 1.5rem; }

  /* ───── ALERT STAMP ───── */
  .arc-alert-stamp { background: #f8e5de; color: #2b0808; background-image: repeating-linear-gradient(transparent, transparent 3rem, rgba(193,56,48,0.08) 3rem, rgba(193,56,48,0.08) calc(3rem + 1px)); }
  .arc-alert-stamp .stamp {
    position: absolute; top: 8vh; right: 5vw;
    transform: rotate(-14deg);
    border: 6px solid #c13830;
    padding: 1.2rem 2.5rem;
    font-weight: 900;
    font-size: clamp(2rem, 4vw, 3.5rem);
    letter-spacing: 0.15em;
    color: #c13830;
    background: rgba(248,229,222,0.6);
    text-transform: uppercase;
  }
  .arc-alert-stamp .numeral { font-family: 'Fraunces', serif; font-style: italic; font-weight: 900; font-size: clamp(6rem, 15vw, 12rem); line-height: 1; color: #c13830; margin: 0 0 1rem; }
  .arc-alert-stamp .kicker { font-weight: 800; font-size: 1.2rem; letter-spacing: 0.3em; text-transform: uppercase; color: #c13830; margin-bottom: 1rem; }
  .arc-alert-stamp h2 { font-family: 'Fraunces', serif; font-weight: 800; font-size: clamp(2.5rem, 6vw, 5.5rem); line-height: 1.02; letter-spacing: -0.025em; max-width: 75%; margin: 0 0 2rem; }
  .arc-alert-stamp .lede { font-size: 1.4rem; line-height: 1.55; max-width: 700px; font-weight: 400; margin: 0; }
  .arc-alert-stamp .story-meta { color: #c13830; margin-bottom: 1.5rem; }

  /* ───── ACADEMIC DROP CAP ───── */
  .arc-academic-drop-cap { background: #efe7d1; color: #1b1a14; padding: 8vh 8vw; }
  .arc-academic-drop-cap .paper-head {
    display: flex; justify-content: space-between; align-items: baseline;
    border-bottom: 1.5px solid #1b1a14;
    padding-bottom: 0.7rem; margin-bottom: 3rem;
    font-weight: 500; font-size: 1rem; letter-spacing: 0.25em; text-transform: uppercase;
  }
  .arc-academic-drop-cap .numeral-roman { font-family: 'Fraunces', serif; font-weight: 700; font-size: clamp(2.5rem, 4vw, 4rem); font-style: italic; letter-spacing: 0.08em; }
  .arc-academic-drop-cap h2 { font-family: 'Fraunces', serif; font-weight: 400; font-style: italic; font-size: clamp(2.5rem, 5.5vw, 5rem); line-height: 1.05; letter-spacing: -0.015em; margin: 0 0 1.5rem; max-width: 85%; }
  .arc-academic-drop-cap .story-meta { margin-bottom: 2rem; }
  .arc-academic-drop-cap .cols { display: grid; grid-template-columns: 1fr 1fr; gap: 3.5rem; }
  .arc-academic-drop-cap .cols p { font-family: 'Fraunces', serif; font-size: 1.35rem; line-height: 1.55; text-align: justify; hyphens: auto; margin: 0 0 1.2rem; }
  .arc-academic-drop-cap .cols > div:first-child p:first-child::first-letter {
    font-family: 'Fraunces', serif;
    font-weight: 900;
    font-size: 7em;
    float: left;
    line-height: 0.82;
    padding: 0.1em 0.15em 0 0;
    color: #7a1e14;
  }

  /* ───── TERMINAL ───── */
  .arc-terminal { background: #000; color: #8cff8c; font-family: 'JetBrains Mono', ui-monospace, monospace; padding: 8vh 6vw; }
  .arc-terminal .prompt-row { font-size: 1.15rem; font-weight: 500; color: #4ac94a; margin-bottom: 3rem; }
  .arc-terminal .numeral { font-size: clamp(6rem, 14vw, 13rem); font-weight: 700; line-height: 1; color: #8cff8c; margin: 0 0 1rem; }
  .arc-terminal .numeral::before { content: "> "; color: #4ac94a; }
  .arc-terminal h2 { font-weight: 700; font-size: clamp(2.2rem, 5vw, 4.5rem); line-height: 1.1; margin: 0 0 2rem; max-width: 90%; color: #d4ffd4; }
  .arc-terminal .lede { font-size: 1.3rem; line-height: 1.6; max-width: 780px; color: #8cff8c; margin: 0; }
  .arc-terminal .lede::before { content: "# "; color: #4ac94a; }
  .arc-terminal .story-meta { color: #4ac94a; margin-bottom: 1.5rem; }
  .arc-terminal .cursor { display: inline-block; width: 0.55em; height: 1.1em; background: #8cff8c; vertical-align: text-bottom; margin-left: 0.2em; animation: me-blink 1s steps(2, start) infinite; }
  @keyframes me-blink { to { visibility: hidden; } }
  .arc-terminal .read-more { color: #8cff8c; border-color: #4ac94a; }
  .arc-terminal .dossier-link { color: #8cff8c; border-color: rgba(140,255,140,0.4); }

  /* ───── EDITORIAL PULLQUOTE ───── */
  .arc-editorial-pullquote {
    background: #1a1a1a; color: #f5f1e8; padding: 8vh 6vw;
    display: grid; grid-template-columns: 1fr 1.1fr; gap: 5rem; align-items: center;
  }
  .arc-editorial-pullquote .left { position: relative; }
  .arc-editorial-pullquote .numeral { font-family: 'Fraunces', serif; font-weight: 900; font-style: italic; font-size: clamp(8rem, 18vw, 16rem); line-height: 0.9; color: #f5f1e8; margin: 0 0 1rem; letter-spacing: -0.05em; }
  .arc-editorial-pullquote .kicker { font-weight: 700; font-size: 1.1rem; letter-spacing: 0.35em; text-transform: uppercase; color: #e8b84a; margin-bottom: 1.5rem; }
  .arc-editorial-pullquote h2 { font-family: 'Fraunces', serif; font-weight: 700; font-size: clamp(2rem, 4vw, 3.5rem); line-height: 1.1; margin: 0 0 2rem; }
  .arc-editorial-pullquote .pullquote {
    font-family: 'Fraunces', serif; font-style: italic; font-weight: 400;
    font-size: clamp(1.8rem, 3.5vw, 3rem); line-height: 1.15;
    color: #f5f1e8; border-left: 4px solid #e8b84a; padding-left: 2rem; margin: 0;
  }
  .arc-editorial-pullquote .pullquote::before { content: "\201C"; font-size: 1.4em; line-height: 0; vertical-align: -0.4em; color: #e8b84a; margin-right: 0.1em; }
  .arc-editorial-pullquote .pullquote::after { content: "\201D"; font-size: 1.4em; line-height: 0; vertical-align: -0.6em; color: #e8b84a; margin-left: 0.05em; }
  .arc-editorial-pullquote .story-meta { color: #e8b84a; }

  /* ───── CAUTION TAPE ───── */
  .arc-caution-tape { background: #fff200; color: #0a0a0a; padding: 8vh 6vw; }
  .arc-caution-tape::before { content: ""; position: absolute; inset: 0 0 auto 0; height: 2.5rem; background: repeating-linear-gradient(135deg, #0a0a0a 0 2rem, #fff200 2rem 4rem); }
  .arc-caution-tape::after { content: ""; position: absolute; inset: auto 0 0 0; height: 2.5rem; background: repeating-linear-gradient(135deg, #0a0a0a 0 2rem, #fff200 2rem 4rem); }
  .arc-caution-tape .tag {
    position: absolute; top: 6vh; right: 6vw;
    writing-mode: vertical-rl; transform: rotate(180deg);
    font-weight: 900; font-size: 1.3rem; letter-spacing: 0.5em; text-transform: uppercase;
    background: #0a0a0a; color: #fff200; padding: 1rem 0.6rem;
  }
  .arc-caution-tape .numeral { font-weight: 900; font-size: clamp(10rem, 28vw, 24rem); line-height: 0.85; letter-spacing: -0.06em; margin: 0 0 1rem; -webkit-text-stroke: 3px #0a0a0a; color: transparent; }
  .arc-caution-tape .kicker { font-weight: 900; font-size: 1.3rem; letter-spacing: 0.35em; text-transform: uppercase; background: #0a0a0a; color: #fff200; padding: 0.5rem 1rem; display: inline-block; margin-bottom: 1.5rem; }
  .arc-caution-tape h2 { font-weight: 900; font-size: clamp(2.5rem, 6vw, 5.5rem); line-height: 0.98; letter-spacing: -0.03em; margin: 0 0 2rem; max-width: 85%; text-transform: uppercase; }
  .arc-caution-tape .lede { font-size: 1.35rem; line-height: 1.5; max-width: 680px; font-weight: 500; margin: 0; }
  .arc-caution-tape .story-meta { margin-bottom: 1.5rem; }

  /* ───── NOTEBOOK ───── */
  .arc-notebook { background: repeating-linear-gradient(#fdfaf0 0 2.2rem, rgba(120,104,60,0.22) 2.2rem 2.25rem); color: #1f1a0d; padding: 8vh 8vw; }
  .arc-notebook::before { content: ""; position: absolute; top: 0; bottom: 0; left: 8vw; width: 2px; background: #c13830; opacity: 0.5; }
  .arc-notebook .numeral { font-family: 'Fraunces', serif; font-weight: 400; font-style: italic; font-size: clamp(7rem, 17vw, 15rem); line-height: 1; letter-spacing: -0.02em; color: #1f1a0d; margin: 0 0 0.5rem; }
  .arc-notebook .kicker { font-weight: 600; font-size: 1.1rem; letter-spacing: 0.3em; text-transform: uppercase; color: #8a6b1f; margin-bottom: 1rem; }
  .arc-notebook h2 { font-family: 'Fraunces', serif; font-weight: 400; font-style: italic; font-size: clamp(2.8rem, 6.5vw, 6rem); line-height: 1; letter-spacing: -0.02em; margin: 0 0 2rem; max-width: 85%; }
  .arc-notebook .lede { font-family: 'Fraunces', serif; font-weight: 400; font-size: clamp(1.35rem, 2vw, 1.7rem); line-height: 2.2rem; max-width: 700px; margin: 0; }

  /* ───── MINT PATTERN ───── */
  .arc-mint-pattern { background: #a8e6cf; color: #0d2a1e; padding: 8vh 6vw; }
  .arc-mint-pattern::before {
    content: "XOR LISP AWK SED GREP CURL MAKE GIT VIM SSH";
    position: absolute; top: 10vh; left: 0; right: 0;
    font-family: 'JetBrains Mono', monospace;
    font-weight: 700; font-size: 2.5rem; letter-spacing: 0.3em;
    color: rgba(13,42,30,0.1); white-space: nowrap; overflow: hidden; pointer-events: none;
  }
  .arc-mint-pattern .numeral-block { position: relative; z-index: 2; }
  .arc-mint-pattern .numeral { font-family: 'Fraunces', serif; font-weight: 900; font-size: clamp(10rem, 28vw, 24rem); line-height: 0.85; letter-spacing: -0.06em; color: #0d2a1e; margin: 0; font-style: italic; }
  .arc-mint-pattern .kicker { font-family: 'JetBrains Mono', monospace; font-weight: 700; font-size: 1.1rem; letter-spacing: 0.2em; text-transform: uppercase; color: #0d2a1e; margin: 1.5rem 0; position: relative; z-index: 2; }
  .arc-mint-pattern h2 { font-family: 'Fraunces', serif; font-weight: 800; font-size: clamp(2.5rem, 6vw, 5rem); line-height: 1; letter-spacing: -0.025em; margin: 0 0 2rem; max-width: 85%; position: relative; z-index: 2; }
  .arc-mint-pattern .lede { font-family: 'Fraunces', serif; font-size: 1.4rem; line-height: 1.55; max-width: 680px; font-weight: 400; margin: 0; position: relative; z-index: 2; }
  .arc-mint-pattern .story-meta { position: relative; z-index: 2; margin-bottom: 1.5rem; }
  .arc-mint-pattern .spread-links { position: relative; z-index: 2; }

  /* ───── PASTEL PLAYFUL ───── */
  .arc-pastel-playful { background: #fde6f2; color: #2a0a2e; padding: 8vh 6vw 4vh; }
  .arc-pastel-playful .alpha-grid {
    position: absolute; top: 0; left: 0; right: 0;
    display: grid; grid-template-columns: repeat(13, 1fr);
    font-family: 'Fraunces', serif; font-style: italic;
    font-size: clamp(1.2rem, 2.5vw, 2rem); font-weight: 700;
    color: rgba(42,10,46,0.2); padding: 1rem 6vw; letter-spacing: 0.2em; pointer-events: none;
  }
  .arc-pastel-playful .alpha-grid span { text-align: center; }
  .arc-pastel-playful .numeral { font-family: 'Fraunces', serif; font-weight: 900; font-style: italic; font-size: clamp(10rem, 26vw, 22rem); line-height: 0.85; letter-spacing: -0.05em; color: #c13871; margin: 2rem 0 0; }
  .arc-pastel-playful .kicker { font-weight: 700; font-size: 1.1rem; letter-spacing: 0.35em; text-transform: uppercase; color: #c13871; margin-bottom: 1rem; }
  .arc-pastel-playful h2 { font-family: 'Fraunces', serif; font-weight: 400; font-style: italic; font-size: clamp(2.8rem, 6.5vw, 5.5rem); line-height: 1; letter-spacing: -0.02em; margin: 0 0 2rem; max-width: 85%; }
  .arc-pastel-playful .lede { font-size: 1.35rem; line-height: 1.5; max-width: 680px; margin: 0; }
  .arc-pastel-playful .story-meta { color: #c13871; margin-bottom: 1.5rem; }

  /* ───── PRODUCT PLATE ───── */
  .arc-product-plate {
    background: linear-gradient(135deg, #f5f5f3 0%, #e8e8e6 100%);
    color: #121212; padding: 10vh 8vw; text-align: center; align-items: center; justify-content: center;
  }
  .arc-product-plate .product-chip {
    font-weight: 700; font-size: 1rem; letter-spacing: 0.4em; text-transform: uppercase;
    background: #121212; color: #f5f5f3; padding: 0.5rem 1.4rem; display: inline-block; margin-bottom: 3rem;
  }
  .arc-product-plate .kicker { font-weight: 700; font-size: 1.15rem; letter-spacing: 0.5em; text-transform: uppercase; color: #666; margin-bottom: 1.5rem; }
  .arc-product-plate h2 {
    font-family: 'Inter', sans-serif;
    font-weight: 900;
    font-size: clamp(3.2rem, 9vw, 8rem);
    line-height: 0.95;
    letter-spacing: -0.045em;
    margin: 0 auto 2rem;
    max-width: 14ch;
  }
  .arc-product-plate .product-rule { width: 6rem; height: 2px; background: #121212; margin: 1.5rem auto 2rem; }
  .arc-product-plate .story-meta { color: #666; margin-bottom: 1.5rem; }
  .arc-product-plate .lede { font-family: 'Fraunces', serif; font-weight: 400; font-size: clamp(1.4rem, 2.2vw, 1.9rem); line-height: 1.45; max-width: 720px; margin: 0 auto; }
  .arc-product-plate .spread-links { justify-content: center; }

  /* ───── ARCHIVE ───── */
  .arc-archive {
    background: #eadbb8;
    background-image:
      radial-gradient(rgba(80,40,10,0.05) 1px, transparent 1px),
      radial-gradient(rgba(80,40,10,0.04) 1px, transparent 1px);
    background-size: 4px 4px, 7px 7px;
    background-position: 0 0, 2px 2px;
    color: #2a1a08; padding: 8vh 8vw;
  }
  .arc-archive .archive-head {
    display: flex; justify-content: space-between; align-items: baseline;
    padding-bottom: 1rem; margin-bottom: 3rem;
    border-top: 6px double #3a2a10; border-bottom: 6px double #3a2a10;
    padding-top: 1rem;
    font-family: 'Fraunces', serif; font-style: italic; font-weight: 500;
    font-size: 1.1rem; letter-spacing: 0.15em; text-transform: uppercase; color: #3a2a10;
  }
  .arc-archive .kicker { font-family: 'Fraunces', serif; font-style: italic; font-weight: 600; font-size: 1.2rem; letter-spacing: 0.3em; text-transform: uppercase; color: #8a3a10; margin-bottom: 1.2rem; }
  .arc-archive h2 {
    font-family: 'Fraunces', serif; font-weight: 700; font-style: normal;
    font-size: clamp(2.8rem, 6.5vw, 6rem); line-height: 1; letter-spacing: -0.02em;
    margin: 0 0 2rem; max-width: 85%;
  }
  .arc-archive .lede {
    font-family: 'Fraunces', serif; font-size: clamp(1.35rem, 2vw, 1.7rem);
    line-height: 1.55; max-width: 760px; font-weight: 400; margin: 0;
  }
  .arc-archive .lede::first-letter {
    font-family: 'Fraunces', serif; font-weight: 900; font-size: 5em;
    float: left; line-height: 0.82; padding: 0.1em 0.15em 0 0; color: #8a3a10;
  }
  .arc-archive .story-meta { color: #8a3a10; margin-bottom: 1.5rem; }

  /* ───── OBITUARY ───── */
  .arc-obituary {
    background: #f8f4ec; color: #121212;
    padding: 10vh 6vw; align-items: center; justify-content: center;
  }
  .arc-obituary .obit-frame {
    border: 3px solid #121212; padding: 6rem 5rem;
    max-width: 780px; text-align: center; background: #f8f4ec;
    position: relative; z-index: 2;
  }
  .arc-obituary .obit-frame::before,
  .arc-obituary .obit-frame::after {
    content: ""; position: absolute; left: -12px; right: -12px; height: 1px; background: #121212;
  }
  .arc-obituary .obit-frame::before { top: -12px; }
  .arc-obituary .obit-frame::after { bottom: -12px; }
  .arc-obituary .ornament { font-family: 'Fraunces', serif; font-size: 3.5rem; line-height: 1; margin-bottom: 2rem; color: #121212; }
  .arc-obituary .kicker { font-family: 'Fraunces', serif; font-style: italic; font-weight: 500; font-size: 1.1rem; letter-spacing: 0.3em; text-transform: uppercase; margin-bottom: 2rem; opacity: 0.7; }
  .arc-obituary h2 {
    font-family: 'Fraunces', serif; font-weight: 400; font-style: italic;
    font-size: clamp(2.4rem, 5vw, 4.5rem); line-height: 1.1; letter-spacing: -0.015em;
    margin: 0 0 2.5rem;
  }
  .arc-obituary .story-meta { margin-bottom: 2rem; opacity: 0.6; }
  .arc-obituary .lede { font-family: 'Fraunces', serif; font-size: clamp(1.3rem, 1.9vw, 1.6rem); line-height: 1.6; margin: 0 auto; max-width: 60ch; }
  .arc-obituary .spread-links { justify-content: center; margin-top: 3rem; }

  /* ───── BLUEPRINT ───── */
  .arc-blueprint {
    background: #0a3d62;
    background-image:
      linear-gradient(rgba(255,255,255,0.08) 1px, transparent 1px),
      linear-gradient(90deg, rgba(255,255,255,0.08) 1px, transparent 1px);
    background-size: 2.2rem 2.2rem;
    color: #eaf4ff; padding: 8vh 6vw;
  }
  .arc-blueprint .corner {
    position: absolute; font-family: 'JetBrains Mono', monospace;
    font-size: 2.5rem; color: #eaf4ff; opacity: 0.7;
  }
  .arc-blueprint .corner.tl { top: 3vh; left: 3vw; }
  .arc-blueprint .corner.tr { top: 3vh; right: 3vw; }
  .arc-blueprint .corner.bl { bottom: 3vh; left: 3vw; }
  .arc-blueprint .corner.br { bottom: 3vh; right: 3vw; }
  .arc-blueprint .numeral {
    font-family: 'JetBrains Mono', monospace;
    font-weight: 700; font-size: clamp(4rem, 9vw, 7.5rem); line-height: 1;
    letter-spacing: -0.01em; color: #eaf4ff;
    margin: 0 0 1.5rem;
    border-bottom: 2px dashed rgba(234,244,255,0.5);
    padding-bottom: 1rem;
    display: inline-block;
  }
  .arc-blueprint .kicker { font-family: 'JetBrains Mono', monospace; font-weight: 700; font-size: 1.15rem; letter-spacing: 0.3em; text-transform: uppercase; color: #9bd2ff; margin-bottom: 1.5rem; }
  .arc-blueprint h2 { font-family: 'Fraunces', serif; font-weight: 700; font-size: clamp(2.8rem, 6.5vw, 5.5rem); line-height: 1; letter-spacing: -0.025em; margin: 0 0 2rem; max-width: 85%; }
  .arc-blueprint .lede { font-family: 'JetBrains Mono', monospace; font-size: 1.2rem; line-height: 1.65; max-width: 720px; margin: 0; }
  .arc-blueprint .story-meta { color: #9bd2ff; margin-bottom: 1.5rem; font-family: 'JetBrains Mono', monospace; }

  /* ───── OBSERVATORY ───── */
  .arc-observatory {
    background: #070c2e; color: #e8ecff; padding: 8vh 6vw;
  }
  .arc-observatory .starfield {
    position: absolute; inset: 0; pointer-events: none;
    background-image:
      radial-gradient(1px 1px at 10% 20%, rgba(255,255,255,0.9), transparent),
      radial-gradient(1px 1px at 25% 70%, rgba(255,255,255,0.6), transparent),
      radial-gradient(1.5px 1.5px at 40% 30%, rgba(255,255,255,0.8), transparent),
      radial-gradient(1px 1px at 55% 85%, rgba(255,255,255,0.5), transparent),
      radial-gradient(1px 1px at 70% 15%, rgba(255,255,255,0.9), transparent),
      radial-gradient(1.5px 1.5px at 85% 55%, rgba(255,255,255,0.7), transparent),
      radial-gradient(1px 1px at 92% 25%, rgba(255,255,255,0.5), transparent),
      radial-gradient(1px 1px at 15% 90%, rgba(255,255,255,0.4), transparent),
      radial-gradient(1px 1px at 48% 50%, rgba(255,255,255,0.6), transparent),
      radial-gradient(1px 1px at 78% 78%, rgba(255,255,255,0.5), transparent);
    background-repeat: no-repeat;
  }
  .arc-observatory > * { position: relative; z-index: 2; }
  .arc-observatory .numeral {
    font-family: 'Fraunces', serif; font-style: italic; font-weight: 400;
    font-size: clamp(3rem, 7vw, 6rem); line-height: 1; letter-spacing: 0.04em;
    color: #e8ecff; margin: 0 0 2rem;
    border-bottom: 1px solid rgba(232,236,255,0.4);
    padding-bottom: 1rem; display: inline-block;
  }
  .arc-observatory .kicker { font-family: 'Fraunces', serif; font-style: italic; font-weight: 500; font-size: 1.15rem; letter-spacing: 0.3em; text-transform: uppercase; color: #9ab3ff; margin-bottom: 1.5rem; }
  .arc-observatory h2 { font-family: 'Fraunces', serif; font-weight: 400; font-style: italic; font-size: clamp(2.8rem, 6.5vw, 5.5rem); line-height: 1.05; letter-spacing: -0.015em; margin: 0 0 2rem; max-width: 85%; }
  .arc-observatory .lede { font-family: 'Fraunces', serif; font-size: clamp(1.35rem, 2vw, 1.7rem); line-height: 1.55; max-width: 720px; font-weight: 400; margin: 0; }
  .arc-observatory .story-meta { color: #9ab3ff; margin-bottom: 1.5rem; }

  /* ───── DOSSIER ───── */
  .dossier {
    background: #f5f1e8; color: #1b1a14;
    padding: 8rem 8vw 6rem;
    border-top: 6px double #121212;
  }
  .dossier .dossier-head { text-align: center; margin-bottom: 4rem; }
  .dossier .tagline { font-weight: 600; font-size: 1.1rem; letter-spacing: 0.4em; text-transform: uppercase; margin-bottom: 1rem; opacity: 0.7; }
  .dossier h2 {
    font-family: 'Fraunces', serif; font-style: italic; font-weight: 900;
    font-size: clamp(4rem, 10vw, 9rem); line-height: 0.9; letter-spacing: -0.035em;
    margin: 0 0 1.5rem;
  }
  .dossier .intro { font-family: 'Fraunces', serif; font-style: italic; font-size: 1.35rem; line-height: 1.5; max-width: 620px; margin: 0 auto; }
  .dossier .intro a { font-style: normal; font-weight: 600; text-decoration: underline; }
  .dossier-entry {
    border-top: 1.5px solid #1b1a14;
    padding: 3rem 0 2.5rem;
    max-width: 820px; margin: 0 auto;
  }
  .dossier-entry:last-child { border-bottom: 1.5px solid #1b1a14; }
  .dossier-meta { font-weight: 600; font-size: 1rem; letter-spacing: 0.28em; text-transform: uppercase; color: #7a1e14; margin-bottom: 1.2rem; }
  .dossier-entry h3 {
    font-family: 'Fraunces', serif; font-weight: 700;
    font-size: clamp(1.8rem, 3.5vw, 2.6rem); line-height: 1.1; letter-spacing: -0.015em;
    margin: 0 0 1.2rem;
  }
  .dossier-source { font-size: 1.05rem; font-weight: 500; letter-spacing: 0.08em; margin: 0 0 2rem; opacity: 0.75; }
  .dossier-source a { text-decoration: underline; }
  .dossier-entry h4 {
    font-family: 'Inter', sans-serif; font-weight: 700; font-size: 1rem;
    letter-spacing: 0.35em; text-transform: uppercase; margin: 2rem 0 1rem; color: #7a1e14;
  }
  .dossier-entry p {
    font-family: 'Fraunces', serif; font-size: 1.25rem; line-height: 1.55; margin: 0 0 1rem;
  }
  .dossier-entry p.muted { opacity: 0.6; }

  /* ───── COLOPHON ───── */
  .colophon { background: #121212; color: #f5f1e8; padding: 5rem 6vw; text-align: center; }
  .colophon .sig { font-family: 'Fraunces', serif; font-style: italic; font-weight: 400; font-size: clamp(2.5rem, 5vw, 4rem); line-height: 1.1; letter-spacing: -0.02em; margin: 0 0 1.5rem; }
  .colophon .meta { font-weight: 500; font-size: 1rem; letter-spacing: 0.3em; text-transform: uppercase; opacity: 0.7; }
  .colophon .classic-link { margin-top: 2rem; font-weight: 500; font-size: 1rem; letter-spacing: 0.2em; text-transform: uppercase; }
  .colophon .classic-link a { text-decoration: underline; opacity: 0.85; }

  /* ───── RESPONSIVE ───── */
  @media (max-width: 800px) {
    .masthead-nav { flex-wrap: wrap; gap: 0.8rem; }
    .arc-stat-hero h2, .arc-stat-hero .lede { max-width: 100%; }
    .arc-stat-hero .numeral { font-size: 14rem; opacity: 0.2; right: -2vw; top: 30vh; }
    .arc-midnight .body { margin-top: 8rem; }
    .arc-alert-stamp h2 { max-width: 100%; }
    .arc-alert-stamp .stamp { top: 4vh; right: 4vw; font-size: 1.5rem; padding: 0.8rem 1.4rem; }
    .arc-academic-drop-cap .cols { grid-template-columns: 1fr; gap: 2rem; }
    .arc-editorial-pullquote { grid-template-columns: 1fr; gap: 3rem; }
    .arc-caution-tape h2, .arc-notebook h2, .arc-pastel-playful h2, .arc-archive h2, .arc-obituary h2, .arc-blueprint h2, .arc-observatory h2 { max-width: 100%; }
    .arc-caution-tape .tag { display: none; }
    .arc-pastel-playful .alpha-grid { display: none; }
    .arc-obituary .obit-frame { padding: 3rem 2rem; }
    .arc-blueprint .corner { display: none; }
  }
"""


# ─────────────────────── CLI ───────────────────────


def _main_cli() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Generate the Morning Edition for a given day.")
    parser.add_argument("date", help="Run day in YYYY-MM-DD format")
    parser.add_argument("--force", action="store_true", help="Regenerate assignments even if cache exists")
    args = parser.parse_args()

    day = date.fromisoformat(args.date)

    from trending_digest import build_hn_view_rows, get_db_connection

    conn = get_db_connection()
    try:
        items = build_hn_view_rows(conn, day, allow_summary_generation=False)
    finally:
        conn.close()

    path = generate_morning_edition(day, items, force_regenerate=args.force)
    if path is None:
        raise SystemExit(1)
    print(path)


if __name__ == "__main__":
    _main_cli()
