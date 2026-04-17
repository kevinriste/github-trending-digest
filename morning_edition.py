"""Magazine renderer for Hacker News and GitHub.

Supports "Morning Edition" (HN) and "Open Source Edition" (GitHub).
Takes ten rows and renders a magazine-style page. The classic card-style
page is maintained alongside as classic.html.
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
from typing import Literal

from google import genai

REPO_ROOT = Path(__file__).parent
MODEL = "gemini-3.1-flash-lite-preview"
NUM_STORIES = 10

# Shared LocalStorage keys (maintaining compatibility with existing read tracking)
READ_DAYS_KEY_HN = "gtd:read_days:hn:v1"
READ_DAYS_KEY_GH = "gtd:read_days:gh:v1"

@dataclass(frozen=True)
class Archetype:
    id: str
    name: str
    best_for: str

@dataclass
class EditionConfig:
    id: str
    name: str
    tagline: str
    output_dir: Path
    read_key: str
    archetypes: list[Archetype]
    prompt_voice: str
    summary_paragraphs: int = 1 # HN uses 1, GitHub uses 2

# ─────────────────────── Archetype Sets ───────────────────────

HN_ARCHETYPES = [
    Archetype("stat-hero", "Stat Hero", "stories whose headline centers on a specific number, percentage, or milestone. The layout features one huge display numeral. If picked, you must also return a `big_figure` string (e.g. \"50%\", \"10,000\", \"$1B\")"),
    Archetype("midnight", "Midnight", "AI infrastructure, privacy tech, cryptography, decentralized systems, or nocturnal and hidden themes. Dark palette with a purple glow. Tone: quiet and technical"),
    Archetype("alert-stamp", "Rose Alert", "platform failures, abuse reports, public call-outs, accountability stories. Rose background with a rotated red stamp. Tone: wry, indignant, or deadpan"),
    Archetype("academic-drop-cap", "Academic", "peer-reviewed papers, scientific research, scholarly PDFs, data-heavy studies. Ecru parchment, two-column with a drop cap. Tone: scholarly and measured"),
    Archetype("terminal", "Terminal", "dev tools, CLI releases, open-source governance, code or license policy, compiler and programming-language news. Black with green monospace. Tone: clipped, terse, prompt-like"),
    Archetype("editorial-pullquote", "Editorial Op-Ed", "op-eds, think-pieces, industry analysis, essays with a strong quotable thesis. Dark with gold accent. If picked, you must also return a `pullquote` string: one sentence phrased as display type"),
    Archetype("caution-tape", "Caution Tape", "CVEs, exploits, vulnerabilities, security alerts, breach announcements. Yellow with black diagonal stripes. Tone: clipped warning, no ornament"),
    Archetype("notebook", "Notebook", "personal essays, reflective pieces, craft and process writing, analog or paper themes, writing about thinking. Cream ruled lines. Tone: gentle and reflective"),
    Archetype("mint-pattern", "Mint Pattern", "programming folklore, classic CS, algorithm deep-dives, benchmark debates, low-level trivia. Mint background with a code-word pattern. Tone: playful-technical"),
    Archetype("pastel-playful", "Pastel Finale", "whimsical projects, art or creative software, intentionally useless things, games, curiosities. Pink pastel with an alphabet accent. Tone: light and delighted"),
    Archetype("product-plate", "Product Plate", "hardware/product/model launches and keynote-style releases. Silver keynote-slide aesthetic. Tone: clean, declarative, spec-sheet"),
    Archetype("archive", "Archive", "retro or historical pieces, vintage computing, rediscovered documents, anniversary posts. Sepia letterpress with a dated masthead. Tone: measured, archival"),
    Archetype("obituary", "Obituary", "shutdowns, EOLs, deprecations, project sunsets, service closures. Black-bordered memorial frame with a dagger ornament. Tone: somber, minimal"),
    Archetype("blueprint", "Blueprint", "Show HN posts, maker projects, personal builds, reverse-engineering, DIY engineering. Graph-paper blue with schematic accents. Tone: hands-on, build-log"),
    Archetype("observatory", "Observatory", "astronomy, space news, speculative science, cosmic discoveries. Deep indigo starfield. Tone: quiet wonder, scientific"),
]

GH_ARCHETYPES = [
    Archetype("agent-foundry", "Agent Foundry", "autonomous agents, multi-agent frameworks, agentic workflows, LLM orchestration. Blueprint aesthetic with dotted grids and industrial technical lines. Tone: procedural, architect-like"),
    Archetype("system-core", "System Core", "low-level tools, compilers, kernels, Rust/C++ performance libraries, networking stacks. Brutalist concrete aesthetic, raw monospace, heavy borders. Tone: unyielding, performance-obsessed"),
    Archetype("ui-lab", "UI Lab", "frontend frameworks, CSS libraries, design systems, animation tools, interactive web experiments. Vibrant gradients, rounded components, 'pill' buttons. Tone: enthusiastic, visual-first"),
    Archetype("data-pipeline", "Data Pipeline", "RAG systems, vector databases, ETL tools, data visualization, search engines. Flowing organic data-streams in indigo and violet. Tone: fluid, connected, high-throughput"),
    Archetype("terminal-utility", "Terminal Utility", "CLI apps, shell scripts, TUI dashboards, developer productivity tools. Classic amber-on-black CRT terminal feel with scanlines. Tone: rugged, practical, developer-ready"),
    Archetype("model-bench", "Model Bench", "new LLM weights, local inference engines (llama.cpp, MLX), fine-tuning scripts, weight conversion tools. Sleek minimalist monochrome with silver accents. Tone: technical, benchmarking-focused"),
    Archetype("privacy-shield", "Privacy Shield", "end-to-end encryption, local-first apps, self-hosted alternatives to cloud services, security auditing. Charcoal black with neon 'glitch' accents and shield motifs. Tone: protective, defiant"),
    Archetype("experimental-workshop", "The Workshop", "proof-of-concepts, experimental prototypes, 'just for fun' repos, hackathon projects. Sketchbook aesthetic, hand-drawn arrows, yellow legal-pad backgrounds. Tone: iterative, playful, raw"),
    Archetype("enterprise-engine", "Enterprise Engine", "established open-source applications, scalable backend services, cloud-native infra, Kubernetes tools. Clean professional blue, airy whitespace, corporate-but-modern. Tone: reliable, scalable"),
    Archetype("library-archive", "The Library", "curated 'awesome' lists, educational resources, computer science textbooks, interview prep, collection of research papers. Serif typography on ecru parchment. Tone: scholarly, archival"),
    # Reusing some from HN that fit well:
    Archetype("stat-hero", "Growth Stat", "repos with explosive star growth or major numeric milestones. features one huge display numeral."),
    Archetype("caution-tape", "CVE Alert", "security vulnerabilities, exploit repos, breaking changes in major libraries. Yellow/Black diagonal stripes."),
    Archetype("obituary", "Sunset notice", "project deprecations, moved repositories, or abandoned projects. memorial frame."),
    Archetype("midnight", "Night Shift", "tools for nocturnal coding, dark-mode libraries, or background/daemon services. Purple glow."),
    Archetype("blueprint", "Build Log", "step-by-step 'build your own x' tutorials or documented hardware builds. Graph paper."),
]

CONFIGS = {
    "hn": EditionConfig(
        id="hn",
        name="Morning Edition",
        tagline="HN Front Page Digest",
        output_dir=REPO_ROOT / "docs" / "hn",
        read_key=READ_DAYS_KEY_HN,
        archetypes=HN_ARCHETYPES,
        prompt_voice="scholarly for academic-drop-cap, clipped for terminal, wry for alert-stamp, quiet for midnight, declarative for product-plate, archival for archive, somber for obituary, build-log for blueprint, cosmic for observatory, etc.",
        summary_paragraphs=1,
    ),
    "gh": EditionConfig(
        id="gh",
        name="Open Source Edition",
        tagline="GitHub Daily Trending",
        output_dir=REPO_ROOT / "docs", # GH daily is at root docs/<date>
        read_key=READ_DAYS_KEY_GH,
        archetypes=GH_ARCHETYPES,
        prompt_voice="architect-like for agent-foundry, performance-obsessed for system-core, visual-first for ui-lab, fluid for data-pipeline, rugged for terminal-utility, benchmarking-focused for model-bench, protective for privacy-shield, playful for experimental-workshop, reliable for enterprise-engine, scholarly for library-archive.",
        summary_paragraphs=2,
    ),
}

ORDINAL_LABELS = ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10"]
ROMAN_LABELS = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"]

# ─────────────────────── Content helpers ───────────────────────

def first_paragraph(summary_text: str) -> str:
    """Return the summary text (stripped). Prompt now handles truncation."""
    return summary_text.strip()

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

def _build_prompt(config: EditionConfig, items: list[dict]) -> str:
    catalog_lines = "\n".join(f"- {a.id}: {a.best_for}" for a in config.archetypes)
    
    story_blocks = []
    for n, item in enumerate(items, start=1):
        title = item.get("title", "") or item.get("repo_name", "") or ""
        url = item.get("url") or item.get("discussion_url") or ""
        domain = _extract_domain(url) or "github.com"
        score_label = "pts" if config.id == "hn" else "stars"
        score = item.get("score") or item.get("stars") or 0
        comments = item.get("comment_count") or item.get("today_stars") or 0
        comments_label = "comments" if config.id == "hn" else "stars today"
        
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
            f"Stats: {score} {score_label} · {comments} {comments_label}\n"
            f"URL: {url}\n"
            f"Analysis: {summary or '(none)'}\n"
        )
        if comment_analysis:
            block += f"Reader reactions: {comment_analysis}\n"
        story_blocks.append(block)

    stories_section = "\n".join(story_blocks)

    return f"""You are the editor of a daily curated magazine called "{config.name}." Today's issue contains exactly ten stories from {config.tagline}. Your job is to assign each story to a distinct visual spread archetype and write the editorial copy for that spread.

# Spread archetype catalog

You have {len(config.archetypes)} archetypes to choose from. You must pick exactly ten for today — one per story — and all ten picks must be distinct archetype ids. Unused archetypes simply don't appear today. Choose the archetype whose "best for" description most closely matches the story's theme; if several stories could fit the same archetype, pick the best fit and send the others to their next-best archetypes.

{catalog_lines}

# Stories (in rank order)

{stories_section}

# Your task

For each of the ten stories, produce one JSON object with these fields:

- "rank": integer, 1..10, matching the story's rank above
- "archetype_id": one of the archetype ids. All ten picks must be distinct.
- "kicker": a 1-3 word department label fit to the archetype (e.g., "Infrastructure", "CVE Watch", "Keynote", "Archival Desk", "Build Log", "Observations", "Agent Foundry", "System Core"). Title Case is fine; the layout handles uppercasing.
- "headline": a rewritten magazine-voice headline, 3-12 words. Prefer active voice, present tense, concrete. It may differ from the source title if it reads better, but it must honor the facts in the Analysis. No clickbait.
- "lede": 2-3 sentences of editorial prose that sets up the story in the voice the archetype suggests ({config.prompt_voice}). Stay specific. No hype. No meta-commentary about the magazine.
- "big_figure": only when archetype_id is "stat-hero"; otherwise null. A short display string such as "50%", "10,000", "$1B".
- "pullquote": only when archetype_id is "editorial-pullquote"; otherwise null. One sentence phrased as display type. Do NOT include surrounding quotation marks.

Return a JSON array of exactly ten objects, in rank order (rank 1 first, rank 10 last). Output nothing outside the JSON array. Do not wrap the output in a code fence.
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
        raise ValueError("response is not a JSON array")
    if len(data) != NUM_STORIES:
        raise ValueError(f"expected {NUM_STORIES} objects, got {len(data)}")
    return data

def pick_editorial(config: EditionConfig, items: list[dict]) -> list[dict]:
    prompt = _build_prompt(config, items)
    last_error = ""
    for attempt in range(2):
        try:
            response = _client().models.generate_content(model=MODEL, contents=prompt)
            return _parse_response(response.text)
        except Exception as exc:
            last_error = str(exc)
            logging.warning("Morning Edition LLM response invalid (attempt %d): %s", attempt + 1, exc)
    raise RuntimeError(f"LLM did not produce a valid response after 2 attempts: {last_error}")

# ─────────────────────── Renderers ───────────────────────

def _h(text: str) -> str:
    return html_mod.escape(text)

def _extract_domain(url: str) -> str:
    if not url: return ""
    try:
        from urllib.parse import urlparse
        domain = urlparse(url).netloc
        if domain.startswith("www."):
            return domain[4:]
        return domain
    except:
        return ""

def _read_href(item: dict) -> str:
    return item.get("url") or item.get("discussion_url") or "#"

def _meta_line(config: EditionConfig, item: dict) -> str:
    domain = _extract_domain(item.get("url") or "") or ("news.ycombinator.com" if config.id == "hn" else "github.com")
    score_label = "pts" if config.id == "hn" else "stars"
    score = item.get("score") or item.get("stars") or 0
    comments = item.get("comment_count") or item.get("today_stars") or 0
    comments_label = "comments" if config.id == "hn" else "stars today"
    return f"{_h(domain)} &nbsp;·&nbsp; {score} {score_label} &nbsp;·&nbsp; {comments} {comments_label}"

def _render_analysis_drawer(config: EditionConfig, i: int, item: dict) -> str:
    """Renders a collapsible drawer containing the technical analysis."""
    raw_analysis = (item.get("summary") or "").strip()
    if config.summary_paragraphs == 1:
        analysis = raw_analysis.split("\n\n")[0].strip()
    else:
        analysis = raw_analysis
        
    bullets = parse_bullets(item.get("comment_analysis") or "")
    analysis_parts = [p.strip() for p in analysis.split("\n\n") if p.strip()]
    analysis_html = "\n".join(f'<p>{_h(p)}</p>' for p in analysis_parts)
    
    if not analysis_html:
        analysis_html = '<p class="muted"><em>Analysis not available.</em></p>'
        
    if bullets:
        reactions_label = "Reader Reactions" if config.id == "hn" else "Insights"
        bullets_html = (
            f'<h4>{reactions_label}</h4>\n'
            + "\n".join(f"<p>{_h(b)}</p>" for b in bullets)
        )
    else:
        bullets_html = ""

    return f"""
      <details class="analysis-drawer">
        <summary class="btn-dossier">[ Analysis + ]</summary>
        <div class="drawer-content">
          <h4>Technical Analysis</h4>
          {analysis_html}
          {bullets_html}
          <p class="drawer-footer"><a href="#dossier-{i}">View in Dossier ↓</a></p>
        </div>
      </details>
    """

def _links(config: EditionConfig, item: dict, n: int) -> str:
    """Primary outbound + collapsible drawer."""
    return (
        f'<div class="ctas">'
        f'<a class="btn-read" href="{_read_href(item)}" target="_blank" rel="noopener">Read →</a>'
        f'{_render_analysis_drawer(config, n, item)}'
        f'</div>'
    )

def _render_masthead(config: EditionConfig, day: date) -> str:
    date_display = day.strftime("%B %-d, %Y")
    return f"""  <header class="masthead">
    <div class="masthead-nav">
      <a href="../../">Index</a>
      <a href="../">Calendar</a>
      <a href="classic.html">Classic view</a>
    </div>
    <h1 class="frnc">Ten stories, before your coffee.</h1>
    <div class="issue-line">
      <span>Vol. I</span>
      <span>{date_display}</span>
      <span>{config.tagline}</span>
    </div>
  </header>"""

def _render_dossier(config: EditionConfig, items: list[dict], assignments: list[dict]) -> str:
    entries: list[str] = []
    arch_map = {a.id: a for a in config.archetypes}
    
    for i, (a, item) in enumerate(zip(assignments, items), start=1):
        arch = arch_map.get(a["archetype_id"])
        arch_name = arch.name if arch else a["archetype_id"]
        
        raw_analysis = (item.get("summary") or "").strip()
        if config.summary_paragraphs == 1:
            analysis = raw_analysis.split("\n\n")[0].strip()
        else:
            analysis = raw_analysis
            
        bullets = parse_bullets(item.get("comment_analysis") or "")

        analysis_parts = [p.strip() for p in analysis.split("\n\n") if p.strip()]
        analysis_html = "\n".join(f'<p>{_h(p)}</p>' for p in analysis_parts)
        if not analysis_html:
            analysis_html = '<p class="muted"><em>Analysis not available.</em></p>'
            
        if bullets:
            reactions_label = "Reader Reactions" if config.id == "hn" else "Insights"
            reactions_html = (
                f'<h4>{reactions_label}</h4>\n      '
                + "\n      ".join(f"<p>{_h(b)}</p>" for b in bullets)
            )
        else:
            reactions_html = ""

        title = item.get("title") or item.get("repo_name") or "Untitled"
        discussion_url = _h(item.get("discussion_url") or "")
        source_url = _read_href(item)
        domain = _extract_domain(item.get("url") or "") or ("news.ycombinator.com" if config.id == "hn" else "github.com")
        
        score_label = "pts" if config.id == "hn" else "stars"
        score = item.get("score") or item.get("stars") or 0
        comments = item.get("comment_count") or item.get("today_stars") or 0
        comments_label = "comments" if config.id == "hn" else "stars today"

        entries.append(f"""    <article id="dossier-{i}" class="dossier-entry">
      <div class="dossier-meta">N<sup>o</sup> {ORDINAL_LABELS[i-1]} &nbsp;·&nbsp; {arch_name} &nbsp;·&nbsp; {_h(a['kicker'])}</div>
      <h3>{_h(title)}</h3>
      <p class="dossier-source">
        <a href="{source_url}" target="_blank" rel="noopener">{domain}</a>
        &nbsp;·&nbsp; {score} {score_label} &nbsp;·&nbsp; {comments} {comments_label}
        {" &nbsp;·&nbsp; <a href='" + discussion_url + "' target='_blank' rel='noopener'>discussion</a>" if discussion_url else ""}
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
      <p class="intro">Full analysis and reactions for today's ten stories. <a href="#top">↑ Back to the edition</a></p>
    </div>
{entries_html}
  </section>"""

def _render_colophon(day: date) -> str:
    return f"""  <footer class="colophon">
    <p class="sig">— that's the edition.</p>
    <p class="meta">Set in Fraunces &amp; Inter · Compiled {day.strftime('%B %-d, %Y')}</p>
    <p class="classic-link"><a href="classic.html">Prefer the classic card view? →</a></p>
  </footer>"""

def _render_readtracker(config: EditionConfig, day: date) -> str:
    day_str = day.isoformat()
    return f"""<script>
(() => {{
  const key = {json.dumps(config.read_key)};
  const day = {json.dumps(day_str)};
  let stored = [];
  try {{
    stored = JSON.parse(localStorage.getItem(key) || "[]");
    if (!Array.isArray(stored)) stored = [];
  }} catch (e) {{}}
  if (!stored.includes(day)) {{
    stored.push(day);
    stored.sort();
    localStorage.setItem(key, JSON.stringify(stored));
  }}
}})();
</script>"""

# ─────────────────────── Spread Renderers ───────────────────────

def _arc_stat_hero(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    big = _h(a.get("big_figure") or "")
    return f"""  <section id="story-{i}" class="spread arc-stat-hero">
    <div class="numeral">{big}</div>
    <div class="body">
      <div class="kicker">N<sup>o</sup> {ORDINAL_LABELS[i-1]} · {_h(a['kicker'])}</div>
      <h2 class="frnc">{_h(a['headline'])}</h2>
      <p class="story-meta">{_meta_line(config, item)}</p>
      <p class="lede">{_h(a['lede'])}</p>
      {_links(config, item, i)}
    </div>
  </section>"""

def _arc_midnight(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    return f"""  <section id="story-{i}" class="spread arc-midnight">
    <div class="numeral">{ORDINAL_LABELS[i-1]}</div>
    <div class="body">
      <div class="glow"></div>
      <div class="kicker">// {_h(a['kicker'])}</div>
      <h2 class="frnc">{_h(a['headline'])}</h2>
      <p class="story-meta">{_meta_line(config, item)}</p>
      <p class="lede">{_h(a['lede'])}</p>
      {_links(config, item, i)}
    </div>
  </section>"""

def _arc_alert_stamp(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    return f"""  <section id="story-{i}" class="spread arc-alert-stamp">
    <div class="stamp">ALERT</div>
    <div class="numeral">{ORDINAL_LABELS[i-1]}</div>
    <div class="body">
      <div class="kicker">{_h(a['kicker'])}</div>
      <h2 class="frnc">{_h(a['headline'])}</h2>
      <p class="story-meta">{_meta_line(config, item)}</p>
      <p class="lede">{_h(a['lede'])}</p>
      {_links(config, item, i)}
    </div>
  </section>"""

def _arc_academic_drop_cap(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    lede = a['lede']
    sentences = re.split(r"(?<=[.!?])\s+", lede.strip())
    mid = max(1, len(sentences) // 2)
    left = " ".join(sentences[:mid]) or lede
    right = " ".join(sentences[mid:])
    right_html = f"<p>{_h(right)}</p>" if right.strip() else ""
    return f"""  <section id="story-{i}" class="spread arc-academic-drop-cap">
    <div class="paper-head">
      <span>{_h(a['kicker'])}</span>
      <span class="numeral-roman">{ROMAN_LABELS[i-1]}</span>
      <span>{_h(_extract_domain(item.get('url') or '') or 'news.ycombinator.com')}</span>
    </div>
    <div class="body">
      <h2 class="frnc">{_h(a['headline'])}</h2>
      <p class="story-meta">{_meta_line(config, item)}</p>
      <div class="cols">
        <div><p>{_h(left)}</p></div>
        <div>{right_html}{_links(config, item, i)}</div>
      </div>
    </div>
  </section>"""

def _arc_terminal(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    # Special version of analysis drawer for terminal (different styling)
    return f"""  <section id="story-{i}" class="spread arc-terminal">
    <div class="window">
      <div class="prompt-row">user@morning-edition:~$ cat story_{ORDINAL_LABELS[i-1]}.md</div>
      <div class="body">
        <p class="numeral-label">{ORDINAL_LABELS[i-1]}</p>
        <div class="kicker"># {_h(a['kicker'])}</div>
        <h2 class="frnc">> {_h(a['headline'])}</h2>
        <p class="story-meta">{_meta_line(config, item)}</p>
        <p class="lede">{_h(a['lede'])}<span class="cursor"></span></p>
        <div class="ctas">
          <a href="{_read_href(item)}" class="btn-read" target="_blank">RUN_PROCESS →</a>
          {_render_analysis_drawer(config, i, item)}
        </div>
      </div>
    </div>
  </section>"""

def _arc_editorial_pullquote(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    pq = a.get("pullquote") or ""
    return f"""  <section id="story-{i}" class="spread arc-editorial-pullquote">
    <div class="body">
      <div class="numeral">{ORDINAL_LABELS[i-1]}</div>
      <div class="kicker">{_h(a['kicker'])}</div>
      <h2 class="frnc">{_h(a['headline'])}</h2>
      <p class="story-meta">{_meta_line(config, item)}</p>
      <p class="lede">{_h(a['lede'])}</p>
      {_links(config, item, i)}
    </div>
    <div class="quote-side">
      <blockquote class="pullquote">{_h(pq)}</blockquote>
    </div>
  </section>"""

def _arc_caution_tape(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    return f"""  <section id="story-{i}" class="spread arc-caution-tape">
    <div class="numeral">{ORDINAL_LABELS[i-1]}</div>
    <div class="tape top">CAUTION CAUTION CAUTION CAUTION CAUTION CAUTION CAUTION CAUTION</div>
    <div class="body">
      <div class="tag">SECURITY ADVISORY</div>
      <div class="kicker">{_h(a['kicker'])}</div>
      <h2 class="frnc">{_h(a['headline'])}</h2>
      <p class="story-meta">{_meta_line(config, item)}</p>
      <p class="lede">{_h(a['lede'])}</p>
      {_links(config, item, i)}
    </div>
    <div class="tape bottom">CAUTION CAUTION CAUTION CAUTION CAUTION CAUTION CAUTION CAUTION</div>
  </section>"""

def _arc_notebook(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    return f"""  <section id="story-{i}" class="spread arc-notebook">
    <div class="numeral">{ORDINAL_LABELS[i-1]}</div>
    <div class="body">
      <div class="lines"></div>
      <div class="kicker">{_h(a['kicker'])}</div>
      <h2 class="frnc">{_h(a['headline'])}</h2>
      <p class="story-meta">{_meta_line(config, item)}</p>
      <p class="lede">{_h(a['lede'])}</p>
      {_links(config, item, i)}
    </div>
  </section>"""

def _arc_mint_pattern(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    word = a['kicker'].split()[0].upper() if a['kicker'] else "CODE"
    return f"""  <section id="story-{i}" class="spread arc-mint-pattern" style="--bg-word: '{_h(word)}'">
    <div class="numeral-block">
      <div class="numeral">{ORDINAL_LABELS[i-1]}</div>
    </div>
    <div class="body">
      <div class="kicker">{_h(a['kicker'])}</div>
      <h2 class="frnc">{_h(a['headline'])}</h2>
      <p class="story-meta">{_meta_line(config, item)}</p>
      <p class="lede">{_h(a['lede'])}</p>
      {_links(config, item, i)}
    </div>
  </section>"""

def _arc_pastel_playful(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    return f"""  <section id="story-{i}" class="spread arc-pastel-playful">
    <div class="alpha-grid">A B C D E F G H I J K L M N O P Q R S T U V W X Y Z</div>
    <div class="body">
      <div class="numeral">{ORDINAL_LABELS[i-1]}</div>
      <div class="kicker">{_h(a['kicker'])}</div>
      <h2 class="frnc">{_h(a['headline'])}</h2>
      <p class="story-meta">{_meta_line(config, item)}</p>
      <p class="lede">{_h(a['lede'])}</p>
      {_links(config, item, i)}
    </div>
  </section>"""

def _arc_product_plate(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    return f"""  <section id="story-{i}" class="spread arc-product-plate">
    <div class="plate">
      <div class="kicker">{_h(a['kicker'])}</div>
      <h2 class="frnc">{_h(a['headline'])}</h2>
      <p class="story-meta">{_meta_line(config, item)}</p>
      <p class="lede">{_h(a['lede'])}</p>
      {_links(config, item, i)}
    </div>
  </section>"""

def _arc_archive(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    return f"""  <section id="story-{i}" class="spread arc-archive">
    <div class="mast">ARCHIVE N<sup>o</sup> {ORDINAL_LABELS[i-1]}</div>
    <div class="body">
      <div class="kicker">{_h(a['kicker'])}</div>
      <h2 class="frnc">{_h(a['headline'])}</h2>
      <p class="story-meta">{_meta_line(config, item)}</p>
      <p class="lede">{_h(a['lede'])}</p>
      {_links(config, item, i)}
    </div>
  </section>"""

def _arc_obituary(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    return f"""  <section id="story-{i}" class="spread arc-obituary">
    <div class="obit-frame">
      <div class="kicker">{_h(a['kicker'])}</div>
      <h2 class="frnc">{_h(a['headline'])}</h2>
      <p class="story-meta">{_meta_line(config, item)}</p>
      <p class="lede">{_h(a['lede'])}</p>
      <div class="ornament">†</div>
      {_links(config, item, i)}
    </div>
  </section>"""

def _arc_blueprint(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    return f"""  <section id="story-{i}" class="spread arc-blueprint">
    <div class="grid"></div>
    <div class="body">
      <div class="kicker">{_h(a['kicker'])}</div>
      <h2 class="frnc">{_h(a['headline'])}</h2>
      <p class="story-meta">{_meta_line(config, item)}</p>
      <p class="lede">{_h(a['lede'])}</p>
      {_links(config, item, i)}
    </div>
  </section>"""

def _arc_observatory(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    return f"""  <section id="story-{i}" class="spread arc-observatory">
    <div class="stars"></div>
    <div class="body">
      <div class="kicker">{_h(a['kicker'])}</div>
      <h2 class="frnc">{_h(a['headline'])}</h2>
      <p class="story-meta">{_meta_line(config, item)}</p>
      <p class="lede">{_h(a['lede'])}</p>
      {_links(config, item, i)}
    </div>
  </section>"""

# GH Specific archetypes
def _arc_agent_foundry(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    return f"""  <section id="story-{i}" class="spread arc-agent-foundry">
    <div class="blueprint-bg"></div>
    <div class="body">
      <div class="kicker">{_h(a['kicker'])}</div>
      <h2 class="frnc">{_h(a['headline'])}</h2>
      <p class="story-meta">{_meta_line(config, item)}</p>
      <p class="lede">{_h(a['lede'])}</p>
      {_links(config, item, i)}
    </div>
  </section>"""

def _arc_system_core(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    return f"""  <section id="story-{i}" class="spread arc-system-core">
    <div class="concrete"></div>
    <div class="body">
      <div class="kicker">{_h(a['kicker'])}</div>
      <h2 class="frnc">{_h(a['headline'])}</h2>
      <p class="story-meta">{_meta_line(config, item)}</p>
      <p class="lede">{_h(a['lede'])}</p>
      {_links(config, item, i)}
    </div>
  </section>"""

def _arc_ui_lab(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    return f"""  <section id="story-{i}" class="spread arc-ui-lab">
    <div class="lab-gradient"></div>
    <div class="body">
      <div class="kicker">{_h(a['kicker'])}</div>
      <h2 class="frnc">{_h(a['headline'])}</h2>
      <p class="story-meta">{_meta_line(config, item)}</p>
      <p class="lede">{_h(a['lede'])}</p>
      {_links(config, item, i)}
    </div>
  </section>"""

def _arc_data_pipeline(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    return f"""  <section id="story-{i}" class="spread arc-data-pipeline">
    <div class="pipeline-streams"></div>
    <div class="body">
      <div class="kicker">{_h(a['kicker'])}</div>
      <h2 class="frnc">{_h(a['headline'])}</h2>
      <p class="story-meta">{_meta_line(config, item)}</p>
      <p class="lede">{_h(a['lede'])}</p>
      {_links(config, item, i)}
    </div>
  </section>"""

def _arc_model_bench(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    return f"""  <section id="story-{i}" class="spread arc-model-bench">
    <div class="bench-silver"></div>
    <div class="body">
      <div class="kicker">{_h(a['kicker'])}</div>
      <h2 class="frnc">{_h(a['headline'])}</h2>
      <p class="story-meta">{_meta_line(config, item)}</p>
      <p class="lede">{_h(a['lede'])}</p>
      {_links(config, item, i)}
    </div>
  </section>"""

def _arc_privacy_shield(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    return f"""  <section id="story-{i}" class="spread arc-privacy-shield">
    <div class="shield-glitch"></div>
    <div class="body">
      <div class="kicker">{_h(a['kicker'])}</div>
      <h2 class="frnc">{_h(a['headline'])}</h2>
      <p class="story-meta">{_meta_line(config, item)}</p>
      <p class="lede">{_h(a['lede'])}</p>
      {_links(config, item, i)}
    </div>
  </section>"""

def _arc_enterprise_engine(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    return f"""  <section id="story-{i}" class="spread arc-enterprise-engine">
    <div class="engine-blue"></div>
    <div class="body">
      <div class="kicker">{_h(a['kicker'])}</div>
      <h2 class="frnc">{_h(a['headline'])}</h2>
      <p class="story-meta">{_meta_line(config, item)}</p>
      <p class="lede">{_h(a['lede'])}</p>
      {_links(config, item, i)}
    </div>
  </section>"""

def _arc_terminal_utility(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    return f"""  <section id="story-{i}" class="spread arc-terminal-utility">
    <div class="crt-lines"></div>
    <div class="body">
      <div class="kicker">{_h(a['kicker'])}</div>
      <h2 class="frnc">{_h(a['headline'])}</h2>
      <p class="story-meta">{_meta_line(config, item)}</p>
      <p class="lede">{_h(a['lede'])}</p>
      {_links(config, item, i)}
    </div>
  </section>"""

def _arc_experimental_workshop(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    return f"""  <section id="story-{i}" class="spread arc-experimental-workshop">
    <div class="pad-lines"></div>
    <div class="body">
      <div class="kicker">{_h(a['kicker'])}</div>
      <h2 class="frnc">{_h(a['headline'])}</h2>
      <p class="story-meta">{_meta_line(config, item)}</p>
      <p class="lede">{_h(a['lede'])}</p>
      {_links(config, item, i)}
    </div>
  </section>"""

def _arc_library_archive(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    return f"""  <section id="story-{i}" class="spread arc-library-archive">
    <div class="parchment"></div>
    <div class="body">
      <div class="kicker">{_h(a['kicker'])}</div>
      <h2 class="frnc">{_h(a['headline'])}</h2>
      <p class="story-meta">{_meta_line(config, item)}</p>
      <p class="lede">{_h(a['lede'])}</p>
      {_links(config, item, i)}
    </div>
  </section>"""

SPREAD_RENDERERS = {
    # HN & Shared
    "stat-hero": _arc_stat_hero,
    "midnight": _arc_midnight,
    "alert-stamp": _arc_alert_stamp,
    "academic-drop-cap": _arc_academic_drop_cap,
    "terminal": _arc_terminal,
    "editorial-pullquote": _arc_editorial_pullquote,
    "caution-tape": _arc_caution_tape,
    "notebook": _arc_notebook,
    "mint-pattern": _arc_mint_pattern,
    "pastel-playful": _arc_pastel_playful,
    "product-plate": _arc_product_plate,
    "archive": _arc_archive,
    "obituary": _arc_obituary,
    "blueprint": _arc_blueprint,
    "observatory": _arc_observatory,
    # GH specific
    "agent-foundry": _arc_agent_foundry,
    "system-core": _arc_system_core,
    "ui-lab": _arc_ui_lab,
    "data-pipeline": _arc_data_pipeline,
    "model-bench": _arc_model_bench,
    "privacy-shield": _arc_privacy_shield,
    "enterprise-engine": _arc_enterprise_engine,
    "terminal-utility": _arc_terminal_utility,
    "experimental-workshop": _arc_experimental_workshop,
    "library-archive": _arc_library_archive,
}

# ─────────────────────── Styles ───────────────────────

CSS_TEMPLATE = """
  :root {
    --bg-light: #f5f1e8; --bg-dark: #121212;
    --text-dark: #121212; --text-light: #f5f1e8;
    --accent-red: #d32f2f; --accent-blue: #1976d2;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 0;
    background: var(--bg-light); color: var(--text-dark);
    font-family: 'Inter', sans-serif;
    -webkit-font-smoothing: antialiased;
  }
  .frnc { font-family: 'Fraunces', serif; font-variant-classic-ligatures: common-ligatures; }
  
  /* ───── LAYOUT ───── */
  .spread {
    position: relative; min-height: 100vh; width: 100%;
    display: flex; align-items: center; justify-content: center;
    padding: 10vh 8vw; overflow: hidden;
    scroll-snap-align: start;
  }
  .body { position: relative; z-index: 10; width: 100%; max-width: 1200px; }
  .kicker { font-weight: 700; font-size: 1rem; letter-spacing: 0.4em; text-transform: uppercase; margin-bottom: 2rem; opacity: 0.7; }
  .numeral {
    position: absolute; top: 10vh; right: 8vw; font-family: 'Fraunces', serif;
    font-size: clamp(10rem, 25vw, 22rem); font-weight: 900; line-height: 0.8;
    opacity: 0.05; pointer-events: none;
  }
  h2 { font-size: clamp(3rem, 8vw, 6.5rem); line-height: 0.95; font-weight: 400; margin: 0 0 1.5rem; letter-spacing: -0.03em; }
  .story-meta { font-weight: 600; font-size: 1.1rem; margin-bottom: 3rem; opacity: 0.6; letter-spacing: 0.02em; }
  .lede { font-size: clamp(1.4rem, 2.5vw, 2.1rem); line-height: 1.35; max-width: 800px; margin: 0 0 4rem; font-weight: 450; }
  
  .ctas { display: flex; gap: 2.5rem; align-items: flex-start; }
  .ctas a {
    text-decoration: none; font-weight: 700; font-size: 1.1rem; letter-spacing: 0.1em; text-transform: uppercase;
    padding: 1.2rem 2.4rem; transition: all 0.2s;
  }
  .btn-read { background: var(--text-dark); color: var(--bg-light); cursor: pointer; border: none; }
  .btn-dossier { 
    border-bottom: 2px solid currentColor; padding: 0.5rem 0 !important; 
    text-decoration: none; font-weight: 700; font-size: 1.1rem; letter-spacing: 0.1em; 
    text-transform: uppercase; cursor: pointer; background: transparent; color: inherit;
    display: inline-block;
  }
  
  /* ───── ANALYSIS DRAWER ───── */
  .analysis-drawer { margin-top: 0; width: 100%; max-width: 800px; }
  .analysis-drawer summary { list-style: none; outline: none; }
  .analysis-drawer summary::-webkit-details-marker { display: none; }
  .analysis-drawer[open] summary { margin-bottom: 2rem; }
  .analysis-drawer[open] summary::after { content: ""; } /* Could add JS to toggle label but we'll do CSS trick or just keep static */
  
  .drawer-content {
    background: rgba(0,0,0,0.03); padding: 3rem; border-left: 4px solid var(--text-dark);
    animation: slideDown 0.3s ease-out;
  }
  .arc-midnight .drawer-content { background: rgba(255,255,255,0.05); border-color: #fff; }
  .arc-terminal .drawer-content { background: rgba(32, 194, 14, 0.05); border-color: #20c20e; }
  
  .drawer-content h4 { text-transform: uppercase; letter-spacing: 0.2em; font-size: 0.9rem; margin-top: 0; margin-bottom: 1.5rem; opacity: 0.6; }
  .drawer-content p { font-family: 'Fraunces', serif; font-size: 1.3rem; line-height: 1.5; margin-bottom: 1.5rem; }
  .drawer-footer { margin-top: 2rem; border-top: 1px solid rgba(0,0,0,0.1); padding-top: 1.5rem; }
  .drawer-footer a { font-size: 0.9rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.1em; text-decoration: none; color: inherit; opacity: 0.5; }
  
  @keyframes slideDown {
    from { opacity: 0; transform: translateY(-10px); }
    to { opacity: 1; transform: translateY(0); }
  }

  /* ───── ARCHETYPE CSS ───── */
  
  /* Stat Hero */
  .arc-stat-hero { background: #e8e4da; }
  .arc-stat-hero .numeral { opacity: 0.15; position: absolute; right: 0; top: 10vh; font-size: clamp(20rem, 50vw, 45rem); }

  /* Midnight */
  .arc-midnight { background: #0a0a0c; color: #fff; }
  .arc-midnight .glow {
    position: absolute; top: 30%; left: 40%; width: 40vw; height: 40vw;
    background: radial-gradient(circle, rgba(110,64,255,0.15) 0%, transparent 70%);
    filter: blur(60px); pointer-events: none;
  }
  .arc-midnight .btn-read { background: #fff; color: #000; }
  .arc-midnight .numeral { color: #fff; }

  /* Alert Stamp */
  .arc-alert-stamp { background: #f9ebeb; color: #7a1e14; }
  .arc-alert-stamp .stamp {
    position: absolute; top: 15vh; right: 10vw; border: 6px solid #d32f2f;
    padding: 1rem 2rem; font-weight: 900; font-size: 4rem; transform: rotate(12deg);
    opacity: 0.2; pointer-events: none; color: #d32f2f;
  }
  .arc-alert-stamp .btn-read { background: #d32f2f; color: #fff; }

  /* Academic */
  .arc-academic-drop-cap { background: #fdfaf3; color: #2c2925; }
  .arc-academic-drop-cap .paper-head {
    position: absolute; top: 5vh; left: 8vw; right: 8vw; display: flex; justify-content: space-between;
    font-weight: 700; text-transform: uppercase; letter-spacing: 0.3em; opacity: 0.4;
    border-bottom: 1px solid rgba(0,0,0,0.1); padding-bottom: 2vh;
  }
  .arc-academic-drop-cap .numeral-roman { font-family: 'Fraunces', serif; }
  .arc-academic-drop-cap h2 { font-size: clamp(2.5rem, 5vw, 4.5rem); border-bottom: 1px solid #ddd; padding-bottom: 2rem; margin-bottom: 3rem; }
  .arc-academic-drop-cap .cols { display: grid; grid-template-columns: 1fr 1fr; gap: 4rem; align-items: start; }
  .arc-academic-drop-cap .cols p::first-letter {
    float: left; font-family: 'Fraunces', serif; font-size: 6.5rem; line-height: 0.75;
    margin: 0.8rem 1rem 0 0; font-weight: 600; color: #121212;
  }
  .arc-academic-drop-cap .btn-read { background: #2c2925; color: #fff; }

  /* Terminal */
  .arc-terminal { background: #000; color: #20c20e; font-family: 'JetBrains Mono', monospace; }
  .arc-terminal .window { border: 1px solid #20c20e; padding: 4rem; background: #050505; width: 100%; max-width: 1000px; position: relative; }
  .arc-terminal .prompt-row { position: absolute; top: -1.5rem; left: 1rem; background: #000; padding: 0 1rem; font-size: 0.9rem; opacity: 0.8; }
  .arc-terminal .numeral-label { position: absolute; top: 2rem; right: 2rem; font-size: 3rem; opacity: 0.2; font-weight: 700; }
  .arc-terminal .frnc { font-family: 'JetBrains Mono', monospace; font-weight: 700; letter-spacing: -0.05em; }
  .arc-terminal h2 { font-size: clamp(2rem, 5vw, 4rem); margin-bottom: 2rem; }
  .arc-terminal .lede { font-size: 1.4rem; max-width: 100%; }
  .arc-terminal .cursor { display: inline-block; width: 0.6em; height: 1.2em; background: #20c20e; margin-left: 0.5em; vertical-align: middle; animation: blink 1s step-end infinite; }
  @keyframes blink { 50% { opacity: 0; } }
  .arc-terminal .btn-read { background: #20c20e; color: #000; border-radius: 0; }
  .arc-terminal .btn-dossier { border-color: #20c20e; }

  /* Editorial Pullquote */
  .arc-editorial-pullquote { background: #1a1a1a; color: #f5f1e8; display: grid; grid-template-columns: 1fr 1fr; gap: 0; padding: 0; }
  .arc-editorial-pullquote .body { padding: 10vh 8vw; display: flex; flex-direction: column; justify-content: center; }
  .arc-editorial-pullquote .quote-side { background: #d4af37; color: #1a1a1a; display: flex; align-items: center; padding: 6vw; position: relative; min-height: 100vh; }
  .arc-editorial-pullquote blockquote { font-family: 'Fraunces', serif; font-size: clamp(2.5rem, 4vw, 4.5rem); line-height: 1.05; font-style: italic; font-weight: 400; margin: 0; }
  .arc-editorial-pullquote blockquote::before { content: '“'; position: absolute; top: 2vw; left: 2vw; font-size: 12rem; opacity: 0.2; font-family: 'Fraunces', serif; }
  .arc-editorial-pullquote .btn-read { background: #f5f1e8; color: #1a1a1a; }
  .arc-editorial-pullquote .numeral { color: #f5f1e8; }

  /* Caution Tape */
  .arc-caution-tape { background: #fcd116; color: #000; }
  .arc-caution-tape .tape {
    position: absolute; left: -10vw; width: 120vw; background: #000; color: #fcd116;
    font-weight: 900; font-size: 1.8rem; padding: 1rem; transform: rotate(-3deg); z-index: 5;
  }
  .arc-caution-tape .tape.top { top: 5vh; transform: rotate(2deg); }
  .arc-caution-tape .tape.bottom { bottom: 5vh; transform: rotate(-1.5deg); }
  .arc-caution-tape .tag { background: #000; color: #fff; padding: 0.6rem 1.2rem; display: inline-block; font-weight: 800; margin-bottom: 2rem; letter-spacing: 0.1em; }
  .arc-caution-tape .btn-read { background: #000; color: #fcd116; }

  /* Notebook */
  .arc-notebook { background: #fcfcf4; }
  .arc-notebook .body { position: relative; padding: 4rem; }
  .arc-notebook .lines {
    position: absolute; inset: 0; z-index: -1;
    background: repeating-linear-gradient(transparent, transparent 2.4rem, #e0e0d0 2.4rem, #e0e0d0 2.5rem);
    border-left: 2px solid #ff5252; margin-left: -2rem; padding-left: 2rem;
  }
  .arc-notebook .btn-read { background: #333; color: #fff; }

  /* Mint Pattern */
  .arc-mint-pattern { background: #d0f0e4; color: #1b4d3e; }
  .arc-mint-pattern::before {
    content: var(--bg-word); position: absolute; inset: 0;
    font-weight: 900; font-size: 16vw; line-height: 0.7; opacity: 0.05;
    display: flex; flex-wrap: wrap; word-break: break-all; overflow: hidden; font-family: 'Fraunces', serif;
  }
  .arc-mint-pattern .numeral-block { position: absolute; top: 10vh; right: 8vw; border: 4px solid #1b4d3e; padding: 2rem; }
  .arc-mint-pattern .numeral { position: static; opacity: 1; font-size: 6rem; }
  .arc-mint-pattern .btn-read { background: #1b4d3e; color: #d0f0e4; }

  /* Pastel Playful */
  .arc-pastel-playful { background: #fbe6e6; color: #8a4b4b; }
  .arc-pastel-playful .alpha-grid {
    position: absolute; right: 4vw; top: 10vh; bottom: 10vh; width: 40vw;
    display: grid; grid-template-columns: repeat(5, 1fr); gap: 1rem;
    font-weight: 900; font-size: 2.5rem; opacity: 0.1; pointer-events: none;
  }
  .arc-pastel-playful .btn-read { background: #8a4b4b; color: #fbe6e6; }

  /* Product Plate */
  .arc-product-plate { background: #eee; }
  .arc-product-plate .plate {
    background: linear-gradient(145deg, #ffffff, #dcdcdc);
    padding: 8vw; box-shadow: 30px 30px 80px #cbcbcb, -30px -30px 80px #ffffff;
    border-radius: 40px; border: 1px solid rgba(255,255,255,0.4);
  }
  .arc-product-plate .btn-read { background: #121212; color: #eee; border-radius: 50px; }

  /* Archive */
  .arc-archive { background: #f4ece1; color: #4e3620; }
  .arc-archive .mast {
    position: absolute; top: 4vh; width: 100%; text-align: center;
    font-family: 'Fraunces', serif; font-weight: 700; font-size: 1.4rem;
    letter-spacing: 0.6em; opacity: 0.4; border-bottom: 1px double #ccc;
    padding-bottom: 2vh;
  }
  .arc-archive .btn-read { background: #4e3620; color: #f4ece1; }

  /* Obituary */
  .arc-obituary { background: #fff; color: #000; }
  .arc-obituary .obit-frame { border: 25px solid #000; padding: 8vw; text-align: center; }
  .arc-obituary .lede { margin-left: auto; margin-right: auto; }
  .arc-obituary .ornament { font-size: 5rem; margin: 4rem 0; opacity: 0.2; }
  .arc-obituary .ctas { justify-content: center; }
  .arc-obituary .btn-read { background: #000; color: #fff; }

  /* Blueprint */
  .arc-blueprint { background: #0047ab; color: #fff; }
  .arc-blueprint .grid {
    position: absolute; inset: 0; opacity: 0.25;
    background-image: linear-gradient(#fff 1px, transparent 1px), linear-gradient(90deg, #fff 1px, transparent 1px);
    background-size: 50px 50px;
  }
  .arc-blueprint .btn-read { background: #fff; color: #0047ab; }

  /* Observatory */
  .arc-observatory { background: #050510; color: #e0e0ff; }
  .arc-observatory .stars {
    position: absolute; inset: 0; opacity: 0.5;
    background: radial-gradient(white, rgba(255,255,255,.2) 2px, transparent 40px);
    background-size: 150px 150px;
  }
  .arc-observatory .btn-read { background: #e0e0ff; color: #050510; border-radius: 100px; }

  /* GH Specific: Agent Foundry */
  .arc-agent-foundry { background: #f0f4f8; color: #1a365d; }
  .arc-agent-foundry .blueprint-bg {
    position: absolute; inset: 0; opacity: 0.15;
    background-image: radial-gradient(#1a365d 1px, transparent 1px);
    background-size: 25px 25px;
    background-image: linear-gradient(#1a365d 1px, transparent 1px), linear-gradient(90deg, #1a365d 1px, transparent 1px);
    background-size: 50px 50px;
  }
  .arc-agent-foundry .btn-read { background: #1a365d; color: #fff; }

  /* GH Specific: System Core */
  .arc-system-core { background: #2d3748; color: #edf2f7; }
  .arc-system-core .concrete {
    position: absolute; inset: 0; opacity: 0.08;
    background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.65' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");
  }
  .arc-system-core h2 { font-family: 'Inter', sans-serif; font-weight: 900; text-transform: uppercase; letter-spacing: -0.05em; font-size: clamp(3rem, 10vw, 8rem); }
  .arc-system-core .btn-read { background: #edf2f7; color: #2d3748; }

  /* GH Specific: UI Lab */
  .arc-ui-lab { background: #fff; color: #4c51bf; }
  .arc-ui-lab .lab-gradient {
    position: absolute; inset: 0; opacity: 0.15;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  }
  .arc-ui-lab .btn-read { background: #4c51bf; color: #fff; border-radius: 16px; box-shadow: 0 10px 20px rgba(76, 81, 191, 0.2); }

  /* GH Specific: Data Pipeline */
  .arc-data-pipeline { background: #1a202c; color: #a3bffa; }
  .arc-data-pipeline .pipeline-streams {
    position: absolute; inset: 0; opacity: 0.2;
    background: repeating-linear-gradient(45deg, #a3bffa, #a3bffa 2px, transparent 2px, transparent 24px);
  }
  .arc-data-pipeline .btn-read { background: #a3bffa; color: #1a202c; }

  /* GH Specific: Model Bench */
  .arc-model-bench { background: #f7fafc; color: #2d3748; }
  .arc-model-bench .bench-silver {
    position: absolute; right: 0; top: 0; width: 45%; height: 100%;
    background: linear-gradient(90deg, transparent, rgba(0,0,0,0.04));
    border-left: 1px solid rgba(0,0,0,0.05);
  }
  .arc-model-bench .btn-read { border: 2px solid #2d3748; background: transparent; color: #2d3748; }

  /* GH Specific: Privacy Shield */
  .arc-privacy-shield { background: #000; color: #00ff41; }
  .arc-privacy-shield .shield-glitch {
    position: absolute; inset: 0; opacity: 0.15;
    background-image: repeating-linear-gradient(0deg, rgba(0,255,65,0.1) 0, rgba(0,255,65,0.1) 1px, transparent 1px, transparent 3px);
  }
  .arc-privacy-shield .btn-read { background: #00ff41; color: #000; font-family: 'JetBrains Mono', monospace; }

  /* GH Specific: Enterprise Engine */
  .arc-enterprise-engine { background: #f0f7ff; color: #0056b3; }
  .arc-enterprise-engine .engine-blue {
    position: absolute; right: -10vw; bottom: -10vw; width: 40vw; height: 40vw;
    background: radial-gradient(circle, rgba(0,86,179,0.08) 0%, transparent 70%);
  }
  .arc-enterprise-engine .btn-read { background: #0056b3; color: #fff; }

  /* GH Specific: Terminal Utility */
  .arc-terminal-utility { background: #1a1a1a; color: #ffb000; font-family: 'JetBrains Mono', monospace; }
  .arc-terminal-utility .crt-lines {
    position: absolute; inset: 0; pointer-events: none;
    background: linear-gradient(rgba(18, 16, 16, 0) 50%, rgba(0, 0, 0, 0.3) 50%), linear-gradient(90deg, rgba(255, 0, 0, 0.08), rgba(0, 255, 0, 0.03), rgba(0, 0, 255, 0.08));
    background-size: 100% 3px, 4px 100%;
  }
  .arc-terminal-utility .btn-read { background: #ffb000; color: #1a1a1a; border-radius: 4px; }

  /* GH Specific: Experimental Workshop */
  .arc-experimental-workshop { background: #fffde7; color: #5d4037; }
  .arc-experimental-workshop .pad-lines {
    position: absolute; left: 6vw; top: 0; bottom: 0; width: 3px; background: #ff5252; opacity: 0.4;
    box-shadow: 60px 0 0 rgba(255,82,82,0.05);
  }
  .arc-experimental-workshop .btn-read { border: 3px dashed #5d4037; background: transparent; color: #5d4037; }

  /* GH Specific: Library Archive */
  .arc-library-archive { background: #fdfaf3; color: #3e2723; }
  .arc-library-archive .parchment {
    position: absolute; inset: 0; opacity: 0.05;
    background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='f'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.02' numOctaves='5'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23f)'/%3E%3C/svg%3E");
  }
  .arc-library-archive .btn-read { background: #3e2723; color: #fdfaf3; }

  /* ───── MASTHEAD ───── */
  .masthead { padding: 6vh 8vw 12vh; text-align: center; border-bottom: 1px solid #ddd; position: relative; }
  .masthead-nav { display: flex; justify-content: center; gap: 3rem; margin-bottom: 8vh; font-weight: 700; font-size: 0.95rem; text-transform: uppercase; letter-spacing: 0.3em; }
  .masthead-nav a { color: inherit; text-decoration: none; opacity: 0.5; transition: opacity 0.2s; }
  .masthead-nav a:hover { opacity: 1; }
  .masthead h1 { font-size: clamp(3.5rem, 12vw, 10rem); font-weight: 400; margin: 0 0 3rem; letter-spacing: -0.05em; line-height: 0.9; }
  .issue-line { display: flex; justify-content: center; gap: 5rem; font-weight: 800; font-size: 1.1rem; letter-spacing: 0.4em; text-transform: uppercase; opacity: 0.7; border-top: 1px solid #eee; padding-top: 4vh; }
  
  /* ───── DOSSIER ───── */
  .dossier { background: #f5f1e8; padding: 12vh 8vw; color: #121212; position: relative; border-top: 10px solid #121212; }
  .dossier-head { margin-bottom: 10vh; }
  .dossier-head h2 { font-size: clamp(4rem, 15vw, 14rem); margin-bottom: 2rem; letter-spacing: -0.05em; font-weight: 400; }
  .tagline { font-weight: 800; font-size: 1.4rem; letter-spacing: 0.6em; text-transform: uppercase; margin-bottom: 1.5rem; opacity: 0.4; }
  .intro { font-size: 1.8rem; max-width: 700px; line-height: 1.35; opacity: 0.6; font-family: 'Fraunces', serif; }
  .intro a { color: inherit; text-decoration: underline; text-underline-offset: 4px; }
  
  .dossier-entry { margin-bottom: 15vh; max-width: 1000px; border-bottom: 1px solid rgba(0,0,0,0.1); padding-bottom: 10vh; }
  .dossier-entry:last-child { border-bottom: none; }
  .dossier-meta { font-weight: 800; font-size: 1rem; letter-spacing: 0.3em; text-transform: uppercase; margin-bottom: 2rem; opacity: 0.4; }
  .dossier-entry h3 { font-family: 'Fraunces', serif; font-size: clamp(2.8rem, 6vw, 5.5rem); line-height: 1.05; margin: 0 0 2rem; font-weight: 400; letter-spacing: -0.02em; }
  .dossier-source { font-size: 1.2rem; font-weight: 600; letter-spacing: 0.03em; margin: 0 0 4rem; opacity: 0.6; }
  .dossier-source a { color: inherit; text-decoration: underline; text-underline-offset: 4px; }
  .dossier-entry h4 { font-weight: 800; font-size: 1.1rem; letter-spacing: 0.4em; text-transform: uppercase; margin: 4rem 0 2rem; opacity: 0.8; }
  .dossier-entry p { font-family: 'Fraunces', serif; font-size: 1.45rem; line-height: 1.55; margin: 0 0 2rem; font-weight: 400; }
  
  /* ───── COLOPHON ───── */
  .colophon { background: #121212; color: #f5f1e8; padding: 12vh 8vw; text-align: center; }
  .colophon .sig { font-family: 'Fraunces', serif; font-style: italic; font-size: clamp(2.5rem, 6vw, 5rem); margin-bottom: 3rem; font-weight: 400; }
  .colophon .meta { font-weight: 600; font-size: 1rem; letter-spacing: 0.4em; text-transform: uppercase; opacity: 0.5; }
  .classic-link { margin-top: 5rem; font-size: 1.1rem; letter-spacing: 0.2em; text-transform: uppercase; font-weight: 700; }
  .classic-link a { color: inherit; text-decoration: underline; text-underline-offset: 6px; }

  @media (max-width: 800px) {
    .spread { padding: 12vh 6vw; }
    .issue-line { flex-direction: column; gap: 1.5rem; align-items: center; }
    .arc-stat-hero .numeral { font-size: 15rem; top: auto; bottom: 5vh; }
    .arc-editorial-pullquote { grid-template-columns: 1fr; }
    .arc-editorial-pullquote .quote-side { padding: 15vw; min-height: 60vh; }
    .ctas { flex-direction: column; align-items: stretch; gap: 1.5rem; }
    .arc-academic-drop-cap .cols { grid-template-columns: 1fr; gap: 2rem; }
  }
"""

def generate_morning_edition_html(config: EditionConfig, day: date, items: list[dict], assignments: list[dict]) -> str:
    title = f"{config.name} — {day.strftime('%B %-d, %Y')}"
    
    spreads = []
    for i, (a, item) in enumerate(zip(assignments, items), start=1):
        arch_id = a["archetype_id"]
        renderer = SPREAD_RENDERERS.get(arch_id, _arc_stat_hero)
        spreads.append(renderer(config, i, a, item))
        
    spreads_html = "\n".join(spreads)
    
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{_h(title)}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,100..900;1,9..144,100..900&family=Inter:wght@100..900&family=JetBrains+Mono:wght@500;700&display=swap" rel="stylesheet">
  <style>{CSS_TEMPLATE}</style>
</head>
<body id="top">
{_render_masthead(config, day)}
{spreads_html}
{_render_dossier(config, items, assignments)}
{_render_colophon(day)}
{_render_readtracker(config, day)}
<script>
// Simple script to toggle the [+] and [-] labels on the drawers
document.querySelectorAll('.analysis-drawer').forEach(drawer => {{
  drawer.addEventListener('toggle', () => {{
    const summary = drawer.querySelector('summary');
    if (drawer.open) {{
      summary.textContent = '[ Analysis − ]';
    }} else {{
      summary.textContent = '[ Analysis + ]';
    }}
  }});
}});
</script>
</body>
</html>"""

def generate_morning_edition(
    day: date,
    items: list[dict],
    source: Literal["hn", "gh"] = "hn",
    force_regenerate: bool = False,
) -> str:
    config = CONFIGS[source]
    output_dir = config.output_dir / day.isoformat()
    output_dir.mkdir(parents=True, exist_ok=True)
    
    index_file = output_dir / "index.html"
    assignments_file = output_dir / "assignments.json"
    
    assignments = None
    if not force_regenerate and assignments_file.exists():
        try:
            with open(assignments_file, "r") as f:
                assignments = json.load(f)
            logging.info("%s: using cached assignments for %s", config.name, day)
        except Exception:
            pass
            
    if not assignments:
        logging.info("%s: calling Gemini for %s", config.name, day)
        assignments = pick_editorial(config, items[:NUM_STORIES])
        with open(assignments_file, "w") as f:
            json.dump(assignments, f, indent=2)
            
    html = generate_morning_edition_html(config, day, items[:NUM_STORIES], assignments)
    
    with open(index_file, "w") as f:
        f.write(html)
        
    logging.info("%s: wrote %s", config.name, index_file)
    return str(index_file)

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    
    parser = argparse.ArgumentParser()
    parser.add_argument("date", help="ISO date YYYY-MM-DD")
    parser.add_argument("--source", choices=["hn", "gh"], default="hn")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    
    day = date.fromisoformat(args.date)
    
    import psycopg
    from psycopg.rows import dict_row
    from trending_digest import build_hn_view_rows, build_gh_view_rows, get_db_connection
    
    conn = get_db_connection()
    try:
        if args.source == "hn":
            items = build_hn_view_rows(conn, day, allow_summary_generation=False)
        else:
            items = build_gh_view_rows(conn, day, allow_summary_generation=False)
    finally:
        conn.close()
        
    if not items:
        print(f"No items found for {day}")
        sys.exit(1)
        
    path = generate_morning_edition(day, items, source=args.source, force_regenerate=args.force)
    print(path)
