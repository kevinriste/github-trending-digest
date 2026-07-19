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

from editions import EDITIONS, cross_edition_links

# Import get_git_sha for cache busting
def get_git_sha():
    try:
        from trending_digest import get_git_sha as sha_fn
        return sha_fn()
    except ImportError:
        return "latest"

from google import genai
from google.genai import types
from pydantic import BaseModel

REPO_ROOT = Path(__file__).parent
MODEL = "gemini-3.1-flash-lite"
NUM_STORIES = 10


class _EditorialItem(BaseModel):
    """Response schema for one story's editorial assignment.

    Passed to Gemini as a structured-output schema so the model uses
    constrained decoding and cannot emit malformed JSON (e.g. an unquoted
    pullquote value, which previously crashed pick_editorial and left the
    HN page with no index.html -> 404).
    """

    rank: int
    archetype_id: str
    kicker: str
    headline: str
    lede: str
    big_figure: str | None = None
    pullquote: str | None = None

# Shared LocalStorage keys (maintaining compatibility with existing read tracking)
READ_DAYS_KEY_HN = EDITIONS["hn"].read_key
READ_DAYS_KEY_GH = EDITIONS["gh"].read_key

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
    headline: str  # masthead <h1>
    summary_paragraphs: int = 1 # HN uses 1, GitHub uses 2
    # Max stories on the magazine. None = use every item passed in (the AI edition
    # varies 15-20). HN/GitHub keep the historical fixed 10.
    max_stories: int | None = 10

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

# AI edition. Reuses existing archetype IDs (so every id has a spread renderer) with
# AI-news-oriented "best for" descriptions. ~20 archetypes so a 15-20 story issue can
# still land mostly-distinct spreads; the prompt allows reuse past the pool size.
AI_ARCHETYPES = [
    Archetype("product-plate", "Model Launch", "frontier model releases, new model families, major version launches. Silver keynote-slide aesthetic. Tone: clean, declarative, spec-sheet"),
    Archetype("model-bench", "Benchmark Desk", "benchmark results, intelligence-index scores, capability evaluations, price/performance analyses. Minimalist monochrome with silver accents. Tone: technical, benchmarking-focused"),
    Archetype("agent-foundry", "Agent Foundry", "autonomous agents, multi-agent frameworks, agentic workflows, tool-use and orchestration. Blueprint aesthetic. Tone: procedural, architect-like"),
    Archetype("academic-drop-cap", "Research Desk", "peer-reviewed papers, arXiv preprints, novel training methods, theoretical results. Ecru parchment with a drop cap. Tone: scholarly and measured"),
    Archetype("stat-hero", "By The Numbers", "stories whose headline centers on a specific number, parameter count, or milestone. One huge display numeral. If picked, also return a `big_figure` string (e.g. \"2.8T\", \"61%\")"),
    Archetype("caution-tape", "Security Watch", "exploits, jailbreaks, data-exfiltration attacks, model vulnerabilities, safety incidents. Yellow with black diagonal stripes. Tone: clipped warning"),
    Archetype("midnight", "Infrastructure", "inference engines, training infrastructure, quantization, decentralized/edge compute, systems work. Dark palette, purple glow. Tone: quiet and technical"),
    Archetype("system-core", "System Core", "low-level runtimes, kernels, compilers, GPU/hardware, performance libraries. Brutalist concrete, raw monospace. Tone: performance-obsessed"),
    Archetype("data-pipeline", "Data & Retrieval", "RAG, vector databases, embeddings, retrieval systems, data curation. Flowing data-streams in indigo. Tone: fluid, high-throughput"),
    Archetype("editorial-pullquote", "Analysis", "op-eds, industry analysis, essays with a strong quotable thesis, economics of AI. Dark with gold accent. If picked, also return a `pullquote` string"),
    Archetype("observatory", "Frontier Science", "AI-for-science, brain-computer interfaces, novel scientific applications, speculative research. Deep indigo starfield. Tone: quiet wonder"),
    Archetype("ui-lab", "Product Lab", "consumer AI products, app launches, hardware companions, UI/UX features, creative tools. Vibrant gradients, rounded components. Tone: enthusiastic, visual-first"),
    Archetype("alert-stamp", "Accountability", "lawsuits, policy fights, public call-outs, platform controversies, governance disputes. Rose background, rotated red stamp. Tone: wry, deadpan"),
    Archetype("terminal", "Dev Tools", "coding agents, developer tooling, CLI releases, IDE integrations, open-source governance. Black with green monospace. Tone: clipped, prompt-like"),
    Archetype("blueprint", "Build Log", "reproductions, open-weight releases, from-scratch builds, reverse-engineering, technical write-ups. Graph-paper blue. Tone: hands-on, build-log"),
    Archetype("enterprise-engine", "Enterprise", "enterprise adoption, cloud AI services, production deployments, business integrations. Clean professional blue. Tone: reliable, scalable"),
    Archetype("library-archive", "The Library", "surveys, curated resources, educational drops, collections, retrospectives. Serif on ecru parchment. Tone: scholarly, archival"),
    Archetype("privacy-shield", "Local & Private", "local-first inference, on-device models, self-hosted alternatives, privacy tech. Charcoal with neon accents. Tone: protective, defiant"),
    Archetype("obituary", "Sunset Notice", "shutdowns, deprecations, discontinued models or products, EOL announcements. Black-bordered memorial frame. Tone: somber, minimal"),
    Archetype("mint-pattern", "Deep Cut", "AI folklore, mechanistic-interpretability deep-dives, clever tricks, low-level ML trivia. Mint background. Tone: playful-technical"),
]

CONFIGS = {
    "ai": EditionConfig(
        id="ai",
        name="AI Edition",
        tagline="the AI/LLM Newsletter",
        output_dir=EDITIONS["ai"].output_dir,
        read_key=EDITIONS["ai"].read_key,
        archetypes=AI_ARCHETYPES,
        prompt_voice="declarative for product-plate/model-bench, architect-like for agent-foundry, scholarly for academic-drop-cap/library-archive, clipped-warning for caution-tape, quiet-technical for midnight/system-core, wry for alert-stamp, quiet-wonder for observatory.",
        headline="Today in AI, cover to cover.",
        summary_paragraphs=2,
        max_stories=None,
    ),
    "hn": EditionConfig(
        id="hn",
        name="Morning Edition",
        tagline="HN Front Page Digest",
        output_dir=EDITIONS["hn"].output_dir,
        read_key=READ_DAYS_KEY_HN,
        archetypes=HN_ARCHETYPES,
        prompt_voice="scholarly for academic-drop-cap, clipped for terminal, wry for alert-stamp, quiet for midnight, declarative for product-plate, archival for archive, somber for obituary, build-log for blueprint, cosmic for observatory, etc.",
        headline="Ten stories, before your coffee.",
        summary_paragraphs=1,
    ),
    "gh": EditionConfig(
        id="gh",
        name="Open Source Edition",
        tagline="GitHub Daily Trending",
        output_dir=EDITIONS["gh"].output_dir, # GH daily is at root docs/<date>
        read_key=READ_DAYS_KEY_GH,
        archetypes=GH_ARCHETYPES,
        prompt_voice="architect-like for agent-foundry, performance-obsessed for system-core, visual-first for ui-lab, fluid for data-pipeline, rugged for terminal-utility, benchmarking-focused for model-bench, protective for privacy-shield, playful for experimental-workshop, reliable for enterprise-engine, scholarly for library-archive.",
        headline="Ten stories, before your coffee.",
        summary_paragraphs=2,
    ),
}

# Sized to the largest edition (the AI edition runs up to 20 stories). HN/GitHub use
# only the first 10, so extending these is inert for them but stops an IndexError when
# a spread renderer looks up ORDINAL_LABELS[i-1] for story 11-20.
ORDINAL_LABELS = [f"{i:02d}" for i in range(1, 21)]
ROMAN_LABELS = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X",
                "XI", "XII", "XIII", "XIV", "XV", "XVI", "XVII", "XVIII", "XIX", "XX"]

# ─────────────────────── Content helpers ───────────────────────

def first_paragraph(summary_text: str) -> str:
    """Return the summary text (stripped). Prompt now handles truncation."""
    return summary_text.strip()

# Strips a single leading marker: a bullet glyph, a "Bullet 2:" / "Point 3"
# label the model sometimes echoes from the prompt, or a "1." list number.
# Applied in a loop so combinations like "- Bullet 2: " are fully removed.
_BULLET_PREFIX_RE = re.compile(
    r"^\s*(?:[-*•]|(?:bullet|point)\s*\d+\s*[:.)\-]?|\d+\s*[.)])\s*",
    re.IGNORECASE,
)

def parse_bullets(text: str) -> list[str]:
    if not text:
        return []
    out: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        cleaned = line
        while True:
            stripped = _BULLET_PREFIX_RE.sub("", cleaned).strip()
            if stripped == cleaned:
                break
            cleaned = stripped
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
        title = item.get("title") or item.get("name") or item.get("repo_name") or ""
        url = item.get("url") or item.get("discussion_url") or ""
        domain = _extract_domain(url) or "github.com"
        if config.id == "hn":
            stats = f"{item.get('score') or 0} pts · {item.get('comment_count') or 0} comments"
        elif config.id == "gh":
            period = (item.get("period_stars") or "").strip() or "no new stars today"
            stats = f"{item.get('stars') or '0'} stars · {period}"
        else:  # ai edition: no engagement metrics; source + date instead
            src = (item.get("source") or "").strip()
            pub = (item.get("published") or "").strip()
            stats = " · ".join(p for p in (src, pub[:10]) if p) or "AI newsletter"
        
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
            f"Stats: {stats}\n"
            f"URL: {url}\n"
            f"Analysis: {summary or '(none)'}\n"
        )
        if comment_analysis:
            block += f"Reader reactions: {comment_analysis}\n"
        story_blocks.append(block)

    stories_section = "\n".join(story_blocks)

    n = len(items)
    return f"""You are the editor of a daily curated magazine called "{config.name}." Today's issue contains exactly {n} stories from {config.tagline}. Your job is to assign each story to a visual spread archetype and write the editorial copy for that spread.

# Spread archetype catalog

You have {len(config.archetypes)} archetypes to choose from. Assign one archetype per story — {n} assignments total. Prefer to make every pick a distinct archetype id; only reuse an archetype when there are more stories than archetypes, or when no unused archetype fits a story well. Choose the archetype whose "best for" description most closely matches the story's theme; if several stories could fit the same archetype, pick the best fit and send the others to their next-best archetypes.

{catalog_lines}

# Stories (in rank order)

{stories_section}

# Your task

For each of the {n} stories, produce one JSON object with these fields:

- "rank": integer, 1..{n}, matching the story's rank above
- "archetype_id": one of the archetype ids, chosen per the rules above.
- "kicker": a 1-3 word department label fit to the archetype (e.g., "Infrastructure", "CVE Watch", "Keynote", "Archival Desk", "Build Log", "Observations", "Agent Foundry", "System Core"). Title Case is fine; the layout handles uppercasing.
- "headline": a rewritten magazine-voice headline, 3-12 words. Prefer active voice, present tense, concrete. It may differ from the source title if it reads better, but it must honor the facts in the Analysis. No clickbait.
- "lede": 2-3 sentences of editorial prose that sets up the story in the voice the archetype suggests ({config.prompt_voice}). Stay specific. No hype. No meta-commentary about the magazine.
- "big_figure": only when archetype_id is "stat-hero"; otherwise null. A short display string such as "50%", "10,000", "$1B".
- "pullquote": only when archetype_id is "editorial-pullquote"; otherwise null. A JSON string holding one sentence phrased as display type. Do not put literal quotation-mark characters inside the sentence text (the JSON string quotes themselves are of course required).

Return a JSON array of exactly {n} objects, in rank order (rank 1 first, rank {n} last). Output nothing outside the JSON array. Do not wrap the output in a code fence.
"""

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)

def _parse_response(raw: str, expected: int) -> list[dict]:
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
    if len(data) != expected:
        raise ValueError(f"expected {expected} objects, got {len(data)}")
    return data

_EDITORIAL_GEN_CONFIG = types.GenerateContentConfig(
    response_mime_type="application/json",
    response_schema=list[_EditorialItem],
)

def pick_editorial(config: EditionConfig, items: list[dict]) -> list[dict]:
    prompt = _build_prompt(config, items)
    last_error = ""
    for attempt in range(4):
        try:
            response = _client().models.generate_content(
                model=MODEL, contents=prompt, config=_EDITORIAL_GEN_CONFIG
            )
            return _parse_response(response.text, len(items))
        except Exception as exc:
            last_error = str(exc)
            logging.warning("Morning Edition LLM response invalid (attempt %d): %s", attempt + 1, exc)
    raise RuntimeError(f"LLM did not produce a valid response after 4 attempts: {last_error}")

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
    if config.id == "hn":
        score = item.get("score") or 0
        comments = item.get("comment_count") or 0
        return f"{_h(domain)} &nbsp;·&nbsp; {score} pts &nbsp;·&nbsp; {comments} comments"
    if config.id == "ai":  # no engagement metrics; show source feed + date
        src = (item.get("source") or "").strip()
        pub = (item.get("published") or "")[:10]
        tail = " &nbsp;·&nbsp; ".join(_h(p) for p in (src, pub) if p)
        return f"{_h(domain)}" + (f" &nbsp;·&nbsp; {tail}" if tail else "")
    stars = item.get("stars") or "0"
    period = (item.get("period_stars") or "").strip() or "no new stars today"
    return f"{_h(domain)} &nbsp;·&nbsp; {_h(str(stars))} stars &nbsp;·&nbsp; {_h(period)}"

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

    repo_link_html = ""
    if config.id == "gh":
        repo_full_name = (item.get("name") or "").strip()
        repo_url = (item.get("url") or "").strip()
        if repo_full_name and repo_url:
            repo_link_html = (
                f'<p class="drawer-repo-link">'
                f'<a href="{_h(repo_url)}" target="_blank" rel="noopener">{_h(repo_full_name)} ↗</a>'
                f'</p>'
            )

    return f"""
      <details class="analysis-drawer">
        <summary class="btn-dossier">[ Analysis + ]</summary>
        <div class="drawer-content">
          {repo_link_html}
          <h4>Technical Analysis</h4>
          <div class="drawer-analysis">
          {analysis_html}
          </div>
          {bullets_html}
          <p class="drawer-footer"><a href="#dossier-{i}">View in Dossier ↓</a></p>
        </div>
      </details>
    """

def _links(config: EditionConfig, item: dict, n: int) -> str:
    """Primary outbound + share link + collapsible drawer."""
    if config.id == "gh":
        share_title = (item.get("name") or "").strip()
    else:
        share_title = (item.get("title") or "").strip()
    share_url = (item.get("url") or item.get("discussion_url") or "").strip()
    share_html = ""
    if share_title and share_url:
        share_html = (
            f'<a class="read-more share-link" href="#"'
            f' data-share-title="{_h(share_title)}"'
            f' data-share-url="{_h(share_url)}"'
            f'>Share →</a>'
        )
    return (
        f'<div class="spread-links">'
        f'<a class="read-more" href="{_read_href(item)}" target="_blank" rel="noopener">Read →</a>'
        f'{share_html}'
        f'{_render_analysis_drawer(config, n, item)}'
        f'</div>'
    )

def _render_masthead(config: EditionConfig, day: date) -> str:
    ed = EDITIONS[config.id]
    nav_parts = [f'<a href="../">{ed.calendar_label}</a>']
    for href, label in cross_edition_links(config.id, day.isoformat()):
        nav_parts.append(f'<a href="{href}">{label} &nbsp;·&nbsp; {day.strftime("%b %-d")}</a>')
    nav_parts.append('<a href="classic.html">Classic View</a>')
    nav_html = "\n      <span>&nbsp;·&nbsp;</span>\n      ".join(nav_parts)

    return f"""  <header class="masthead">
    <div class="masthead-nav">
      {nav_html}
    </div>
    <div class="tagline">The {config.name}</div>
    <h1 class="frnc">{config.headline}</h1>
    <div class="issue-line">
      <span>Vol. I</span>
      <span>{day.strftime("%B %-d, %Y")}</span>
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

        title = item.get("title") or item.get("name") or item.get("repo_name") or "Untitled"
        discussion_url = _h(item.get("discussion_url") or "")
        source_url = _read_href(item)
        domain = _extract_domain(item.get("url") or "") or ("news.ycombinator.com" if config.id == "hn" else "github.com")

        if config.id == "hn":
            stats_line = f"{item.get('score') or 0} pts &nbsp;·&nbsp; {item.get('comment_count') or 0} comments"
        else:
            stars = _h(str(item.get("stars") or "0"))
            period = (item.get("period_stars") or "").strip() or "no new stars today"
            stats_line = f"{stars} stars &nbsp;·&nbsp; {_h(period)}"

        entries.append(f"""    <article id="dossier-{i}" class="dossier-entry">
      <div class="dossier-meta">N<sup>o</sup> {ORDINAL_LABELS[i-1]} &nbsp;·&nbsp; {arch_name} &nbsp;·&nbsp; {_h(a['kicker'])}</div>
      <h3>{_h(title)}</h3>
      <p class="dossier-source">
        <a href="{source_url}" target="_blank" rel="noopener">{domain}</a>
        &nbsp;·&nbsp; {stats_line}
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
    <div class="kicker">N<sup>o</sup> {ORDINAL_LABELS[i-1]} · {_h(a['kicker'])}</div>
    <h2>{_h(a['headline'])}</h2>
    <p class="story-meta">{_meta_line(config, item)}</p>
    <p class="lede">{_h(a['lede'])}</p>
    {_links(config, item, i)}
  </section>"""

def _arc_midnight(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    return f"""  <section id="story-{i}" class="spread arc-midnight">
    <div class="numeral">{ORDINAL_LABELS[i-1]}</div>
    <div class="body">
      <div class="kicker">// {_h(a['kicker'])}</div>
      <h2>{_h(a['headline'])}</h2>
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
      <h2>{_h(a['headline'])}</h2>
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
    <h2>{_h(a['headline'])}</h2>
    <p class="story-meta">{_meta_line(config, item)}</p>
    <div class="cols">
      <div><p>{_h(left)}</p></div>
      <div>{right_html}{_links(config, item, i)}</div>
    </div>
  </section>"""

def _arc_terminal(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    return f"""  <section id="story-{i}" class="spread arc-terminal">
    <div class="prompt-row">user@morning-edition:~$ cat story_{ORDINAL_LABELS[i-1]}.md</div>
    <div class="numeral">{ORDINAL_LABELS[i-1]}</div>
    <h2>{_h(a['headline'])}</h2>
    <p class="story-meta">{_meta_line(config, item)}</p>
    <p class="lede">{_h(a['lede'])}<span class="cursor"></span></p>
    {_links(config, item, i)}
  </section>"""

def _arc_editorial_pullquote(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    pq = a.get("pullquote") or ""
    return f"""  <section id="story-{i}" class="spread arc-editorial-pullquote">
    <div class="left">
      <div class="numeral">{ORDINAL_LABELS[i-1]}</div>
      <div class="kicker">{_h(a['kicker'])}</div>
      <h2>{_h(a['headline'])}</h2>
      <p class="story-meta">{_meta_line(config, item)}</p>
      {_links(config, item, i)}
    </div>
    <blockquote class="pullquote">{_h(pq)}</blockquote>
  </section>"""

def _arc_caution_tape(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    return f"""  <section id="story-{i}" class="spread arc-caution-tape">
    <div class="tag">SECURITY ADVISORY</div>
    <div class="numeral">{ORDINAL_LABELS[i-1]}</div>
    <div class="kicker">{_h(a['kicker'])}</div>
    <h2>{_h(a['headline'])}</h2>
    <p class="story-meta">{_meta_line(config, item)}</p>
    <p class="lede">{_h(a['lede'])}</p>
    {_links(config, item, i)}
  </section>"""

def _arc_notebook(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    return f"""  <section id="story-{i}" class="spread arc-notebook">
    <div class="numeral">{ORDINAL_LABELS[i-1]}</div>
    <div class="kicker">{_h(a['kicker'])}</div>
    <h2>{_h(a['headline'])}</h2>
    <p class="story-meta">{_meta_line(config, item)}</p>
    <p class="lede">{_h(a['lede'])}</p>
    {_links(config, item, i)}
  </section>"""

def _arc_mint_pattern(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    return f"""  <section id="story-{i}" class="spread arc-mint-pattern">
    <div class="numeral-block">
      <div class="numeral">{ORDINAL_LABELS[i-1]}</div>
    </div>
    <div class="kicker">{_h(a['kicker'])}</div>
    <h2>{_h(a['headline'])}</h2>
    <p class="story-meta">{_meta_line(config, item)}</p>
    <p class="lede">{_h(a['lede'])}</p>
    {_links(config, item, i)}
  </section>"""

def _arc_pastel_playful(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    # Build spans for A-Z grid
    alpha = "".join(f"<span>{c}</span>" for c in "ABCDEFGHIJKLM")
    alpha2 = "".join(f"<span>{c}</span>" for c in "NOPQRSTUVWXYZ")
    return f"""  <section id="story-{i}" class="spread arc-pastel-playful">
    <div class="alpha-grid">{alpha}</div>
    <div class="alpha-grid" style="top: auto; bottom: 0;">{alpha2}</div>
    <div class="numeral">{ORDINAL_LABELS[i-1]}</div>
    <div class="kicker">{_h(a['kicker'])}</div>
    <h2>{_h(a['headline'])}</h2>
    <p class="story-meta">{_meta_line(config, item)}</p>
    <p class="lede">{_h(a['lede'])}</p>
    {_links(config, item, i)}
  </section>"""

def _arc_product_plate(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    return f"""  <section id="story-{i}" class="spread arc-product-plate">
    <div class="product-chip">SPECS</div>
    <div class="kicker">{_h(a['kicker'])}</div>
    <h2>{_h(a['headline'])}</h2>
    <div class="product-rule"></div>
    <p class="story-meta">{_meta_line(config, item)}</p>
    <p class="lede">{_h(a['lede'])}</p>
    {_links(config, item, i)}
  </section>"""

def _arc_archive(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    return f"""  <section id="story-{i}" class="spread arc-archive">
    <div class="archive-head">
      <span>ARCHIVE</span>
      <span>N<sup>o</sup> {ORDINAL_LABELS[i-1]}</span>
    </div>
    <div class="kicker">{_h(a['kicker'])}</div>
    <h2>{_h(a['headline'])}</h2>
    <p class="story-meta">{_meta_line(config, item)}</p>
    <p class="lede">{_h(a['lede'])}</p>
    {_links(config, item, i)}
  </section>"""

def _arc_obituary(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    return f"""  <section id="story-{i}" class="spread arc-obituary">
    <div class="obit-frame">
      <div class="ornament">†</div>
      <div class="kicker">{_h(a['kicker'])}</div>
      <h2>{_h(a['headline'])}</h2>
      <p class="story-meta">{_meta_line(config, item)}</p>
      <p class="lede">{_h(a['lede'])}</p>
      {_links(config, item, i)}
    </div>
  </section>"""

def _arc_blueprint(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    return f"""  <section id="story-{i}" class="spread arc-blueprint">
    <div class="corner tl">+</div><div class="corner tr">+</div>
    <div class="corner bl">+</div><div class="corner br">+</div>
    <div class="numeral">{ORDINAL_LABELS[i-1]}</div>
    <div class="kicker">{_h(a['kicker'])}</div>
    <h2>{_h(a['headline'])}</h2>
    <p class="story-meta">{_meta_line(config, item)}</p>
    <p class="lede">{_h(a['lede'])}</p>
    {_links(config, item, i)}
  </section>"""

def _arc_observatory(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    return f"""  <section id="story-{i}" class="spread arc-observatory">
    <div class="starfield"></div>
    <div class="numeral">{ORDINAL_LABELS[i-1]}</div>
    <div class="kicker">{_h(a['kicker'])}</div>
    <h2>{_h(a['headline'])}</h2>
    <p class="story-meta">{_meta_line(config, item)}</p>
    <p class="lede">{_h(a['lede'])}</p>
    {_links(config, item, i)}
  </section>"""

# GH Specific archetypes - Using restored design DNA
def _arc_agent_foundry(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    return f"""  <section id="story-{i}" class="spread arc-blueprint arc-agent-foundry">
    <div class="corner tl">+</div><div class="corner tr">+</div>
    <div class="corner bl">+</div><div class="corner br">+</div>
    <div class="numeral">{ORDINAL_LABELS[i-1]}</div>
    <div class="kicker">Foundry // {_h(a['kicker'])}</div>
    <h2>{_h(a['headline'])}</h2>
    <p class="story-meta">{_meta_line(config, item)}</p>
    <p class="lede">{_h(a['lede'])}</p>
    {_links(config, item, i)}
  </section>"""

def _arc_system_core(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    return f"""  <section id="story-{i}" class="spread arc-system-core">
    <div class="concrete"></div>
    <div class="numeral">{ORDINAL_LABELS[i-1]}</div>
    <div class="kicker">Core // {_h(a['kicker'])}</div>
    <h2 class="mono">{_h(a['headline'])}</h2>
    <p class="story-meta mono">{_meta_line(config, item)}</p>
    <p class="lede mono">{_h(a['lede'])}</p>
    {_links(config, item, i)}
  </section>"""

def _arc_ui_lab(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    return f"""  <section id="story-{i}" class="spread arc-ui-lab">
    <div class="lab-gradient"></div>
    <div class="numeral">{ORDINAL_LABELS[i-1]}</div>
    <div class="kicker">UI Lab // {_h(a['kicker'])}</div>
    <h2>{_h(a['headline'])}</h2>
    <p class="story-meta">{_meta_line(config, item)}</p>
    <p class="lede">{_h(a['lede'])}</p>
    {_links(config, item, i)}
  </section>"""

def _arc_data_pipeline(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    return f"""  <section id="story-{i}" class="spread arc-data-pipeline">
    <div class="pipeline-streams"></div>
    <div class="numeral">{ORDINAL_LABELS[i-1]}</div>
    <div class="kicker">Pipeline // {_h(a['kicker'])}</div>
    <h2>{_h(a['headline'])}</h2>
    <p class="story-meta">{_meta_line(config, item)}</p>
    <p class="lede">{_h(a['lede'])}</p>
    {_links(config, item, i)}
  </section>"""

def _arc_model_bench(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    return f"""  <section id="story-{i}" class="spread arc-model-bench">
    <div class="bench-silver"></div>
    <div class="numeral">{ORDINAL_LABELS[i-1]}</div>
    <div class="kicker">Weights // {_h(a['kicker'])}</div>
    <h2 class="frnc">{_h(a['headline'])}</h2>
    <p class="story-meta">{_meta_line(config, item)}</p>
    <p class="lede">{_h(a['lede'])}</p>
    {_links(config, item, i)}
  </section>"""

def _arc_privacy_shield(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    return f"""  <section id="story-{i}" class="spread arc-privacy-shield">
    <div class="shield-glitch"></div>
    <div class="numeral">{ORDINAL_LABELS[i-1]}</div>
    <div class="kicker">Shield // {_h(a['kicker'])}</div>
    <h2 class="mono">{_h(a['headline'])}</h2>
    <p class="story-meta mono">{_meta_line(config, item)}</p>
    <p class="lede mono">{_h(a['lede'])}</p>
    {_links(config, item, i)}
  </section>"""

def _arc_enterprise_engine(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    return f"""  <section id="story-{i}" class="spread arc-enterprise-engine">
    <div class="engine-blue"></div>
    <div class="numeral">{ORDINAL_LABELS[i-1]}</div>
    <div class="kicker">Enterprise // {_h(a['kicker'])}</div>
    <h2>{_h(a['headline'])}</h2>
    <p class="story-meta">{_meta_line(config, item)}</p>
    <p class="lede">{_h(a['lede'])}</p>
    {_links(config, item, i)}
  </section>"""

def _arc_terminal_utility(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    return f"""  <section id="story-{i}" class="spread arc-terminal arc-terminal-utility">
    <div class="crt-lines"></div>
    <div class="prompt-row">dev@open-source:~$ ./bin/explore story_{ORDINAL_LABELS[i-1]}</div>
    <div class="numeral">{ORDINAL_LABELS[i-1]}</div>
    <h2 class="mono">{_h(a['headline'])}</h2>
    <p class="story-meta mono">{_meta_line(config, item)}</p>
    <p class="lede mono">{_h(a['lede'])}<span class="cursor"></span></p>
    {_links(config, item, i)}
  </section>"""

def _arc_experimental_workshop(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    return f"""  <section id="story-{i}" class="spread arc-experimental-workshop">
    <div class="pad-lines"></div>
    <div class="numeral">{ORDINAL_LABELS[i-1]}</div>
    <div class="kicker">Workshop // {_h(a['kicker'])}</div>
    <h2>{_h(a['headline'])}</h2>
    <p class="story-meta">{_meta_line(config, item)}</p>
    <p class="lede">{_h(a['lede'])}</p>
    {_links(config, item, i)}
  </section>"""

def _arc_library_archive(config: EditionConfig, i: int, a: dict, item: dict) -> str:
    return f"""  <section id="story-{i}" class="spread arc-library-archive">
    <div class="parchment"></div>
    <div class="numeral">{ORDINAL_LABELS[i-1]}</div>
    <div class="kicker">Archive // {_h(a['kicker'])}</div>
    <h2>{_h(a['headline'])}</h2>
    <p class="story-meta">{_meta_line(config, item)}</p>
    <p class="lede">{_h(a['lede'])}</p>
    {_links(config, item, i)}
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

# Restore EXACT original CSS with specific font-variation-settings and multi-line precision
CSS_TEMPLATE = r"""
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
  .masthead { padding: 2.5rem 6vw 3rem; border-bottom: 3px double #121212; background: #f5f1e8; text-align: center; }
  .masthead-nav {
    display: flex;
    justify-content: center;
    gap: 1.5rem;
    align-items: baseline;
    margin-bottom: 2.5rem;
    font-weight: 700;
    font-size: 1rem;
    letter-spacing: 0.25em;
    text-transform: uppercase;
  }
  .masthead-nav a { text-decoration: none; border-bottom: 2px solid transparent; padding-bottom: 4px; }
  .masthead-nav a:hover { border-bottom-color: #121212; opacity: 1; }
  .masthead .tagline { font-weight: 600; font-size: 1.3rem; letter-spacing: 0.4em; text-transform: uppercase; margin-bottom: 1.5rem; color: #7a1e14; }
  .masthead h1 {
    font-family: 'Fraunces', Georgia, serif;
    font-weight: 900;
    font-style: italic;
    font-size: clamp(4.5rem, 14vw, 13rem);
    line-height: 0.85;
    letter-spacing: -0.04em;
    margin: 0 0 1.5rem;
    font-variation-settings: "SOFT" 50;
  }
  .masthead .issue-line {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    max-width: 1100px;
    margin: 2.5rem auto 0;
    padding-top: 1.5rem;
    border-top: 1px solid #121212;
    font-weight: 600;
    font-size: 1.15rem;
    letter-spacing: 0.2em;
    text-transform: uppercase;
  }

  /* ───── SPREAD BASE ───── */
  .spread {
    min-height: 100vh;
    padding: 8vh 6vw;
    position: relative;
    overflow: hidden;
    display: flex;
    flex-direction: column;
    justify-content: center;
  }
  .spread .story-meta { font-weight: 600; font-size: 1.05rem; letter-spacing: 0.28em; text-transform: uppercase; margin-bottom: 2rem; opacity: 0.8; }
  .spread-links {
    display: flex;
    flex-wrap: wrap;
    gap: 2.5rem 3rem;
    align-items: flex-start;
    margin-top: 3rem;
  }
  .read-more {
    display: inline-block;
    font-size: 1.2rem;
    font-weight: 800;
    letter-spacing: 0.15em;
    text-transform: uppercase;
    text-decoration: none;
    padding-bottom: 0.5rem;
    border-bottom: 4px solid currentColor;
  }
  .btn-dossier {
    font-size: 1.1rem;
    font-weight: 700;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    text-decoration: none;
    opacity: 0.7;
    padding-bottom: 0.4rem;
    border-bottom: 2px solid currentColor;
    cursor: pointer; background: transparent; border: none; color: inherit; display: inline-block;
  }
  .btn-dossier:hover { opacity: 1; }

  /* ───── ANALYSIS DRAWER ───── */
  .analysis-drawer { margin-top: 0; width: 100%; max-width: 850px; position: relative; z-index: 50; }
  .analysis-drawer summary { list-style: none; outline: none; }
  .analysis-drawer summary::-webkit-details-marker { display: none; }
  .analysis-drawer[open] summary { margin-bottom: 2.5rem; }
  .drawer-content {
    background: rgba(0,0,0,0.04); padding: 3.5rem; border-left: 6px solid var(--ink);
    animation: slideDown 0.35s cubic-bezier(0.23, 1, 0.32, 1); margin-bottom: 2.5rem;
    text-align: left;
  }
  .drawer-content h4 { text-transform: uppercase; letter-spacing: 0.25em; font-size: 0.95rem; margin-top: 0; margin-bottom: 1.5rem; opacity: 0.6; font-weight: 800; text-align: left; }
  .drawer-repo-link { font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: 0.9rem; margin: 0 0 1.5rem; letter-spacing: 0.02em; opacity: 0.75; }
  .drawer-repo-link a { text-decoration: none; border-bottom: 1px solid currentColor; }
  .share-link { cursor: pointer; }
  .discuss-toast {
    position: fixed; bottom: 2rem; left: 50%; transform: translateX(-50%);
    background-color: #238636; color: #fff; padding: 0.75rem 1.5rem;
    border-radius: 8px; font-size: 0.9rem; z-index: 1000;
    opacity: 0; transition: opacity 0.3s; pointer-events: none;
    font-family: 'Inter', system-ui, sans-serif;
  }
  .discuss-toast.show { opacity: 1; }
  .drawer-content p { font-family: 'Fraunces', serif; font-size: 1.15rem; line-height: 1.55; margin-bottom: 1.5rem; font-weight: 400; text-align: left; }
  .drawer-footer { margin-top: 2.5rem; border-top: 1px solid rgba(0,0,0,0.1); padding-top: 1.5rem; }
  .drawer-footer a { font-size: 0.9rem; font-weight: 800; text-transform: uppercase; letter-spacing: 0.15em; text-decoration: none; color: inherit; opacity: 0.5; }
  @keyframes slideDown { from { opacity: 0; transform: translateY(-15px); } to { opacity: 1; transform: translateY(0); } }

  /* ───── STAT HERO ───── */
  .arc-stat-hero { background: #eee6d3; color: #0d1a2e; }
  .arc-stat-hero .numeral {
    font-family: 'Fraunces', serif; font-weight: 900; font-style: italic; font-size: clamp(15rem, 40vw, 38rem);
    line-height: 0.8; letter-spacing: -0.06em; color: #0d1a2e;
    position: absolute; right: -3vw; top: 5vh; opacity: 0.95; pointer-events: none;
  }
  .arc-stat-hero .kicker { font-weight: 800; font-size: 1.2rem; letter-spacing: 0.45em; color: #c13830; margin-bottom: 1.8rem; }
  .arc-stat-hero h2 { font-family: 'Fraunces', serif; font-weight: 800; font-size: clamp(3.5rem, 9vw, 8rem); line-height: 0.92; letter-spacing: -0.035em; max-width: 65%; margin: 0 0 2.5rem; }
  .arc-stat-hero .lede { font-family: 'Fraunces', serif; font-size: clamp(1.5rem, 2.4vw, 2.1rem); line-height: 1.4; max-width: 60%; font-weight: 400; margin: 0; }
  .arc-stat-hero .story-meta { color: #c13830; opacity: 1; }

  /* ───── MIDNIGHT ───── */
  .arc-midnight { background: radial-gradient(ellipse at 75% 25%, #1d1440 0%, #05050c 70%); color: #ece9ff; }
  .arc-midnight .numeral {
    font-family: 'Fraunces', serif; font-size: clamp(10rem, 25vw, 22rem); font-weight: 300; font-style: italic;
    color: transparent; -webkit-text-stroke: 2px rgba(236,233,255,0.5);
    position: absolute; top: 5vh; left: 4vw; letter-spacing: -0.05em; line-height: 1;
  }
  .arc-midnight .kicker { font-family: 'JetBrains Mono', monospace; font-size: 1.15rem; letter-spacing: 0.25em; color: #b994ff; margin-bottom: 2.2rem; }
  .arc-midnight h2 { font-weight: 900; font-size: clamp(3.2rem, 8vw, 7rem); line-height: 0.98; letter-spacing: -0.04em; margin: 12rem 0 3rem; }
  .arc-midnight .lede { font-size: 1.45rem; line-height: 1.6; color: #d0c9f0; max-width: 820px; }
  .arc-midnight .story-meta { color: #9486d3; }
  .arc-midnight .drawer-content { background: rgba(255,255,255,0.06); border-color: #ece9ff; color: #ece9ff; }

  /* ───── ALERT STAMP ───── */
  .arc-alert-stamp { background: #fbeae3; color: #2d0a0a; background-image: repeating-linear-gradient(transparent, transparent 3.5rem, rgba(193,56,48,0.1) 3.5rem, rgba(193,56,48,0.1) calc(3.5rem + 1px)); }
  .arc-alert-stamp .stamp {
    position: absolute; top: 10vh; right: 6vw; transform: rotate(-12deg); border: 8px solid #c13830;
    padding: 1.5rem 3rem; font-weight: 900; font-size: clamp(2.5rem, 5vw, 4rem); letter-spacing: 0.2em; color: #c13830; background: rgba(251,234,227,0.7); text-transform: uppercase;
  }
  .arc-alert-stamp .numeral { font-family: 'Fraunces', serif; font-style: italic; font-weight: 950; font-size: clamp(7rem, 16vw, 13rem); color: #c13830; margin: 0 0 1.2rem; }
  .arc-alert-stamp h2 { font-family: 'Fraunces', serif; font-weight: 900; font-size: clamp(2.8rem, 6.5vw, 6rem); line-height: 1; letter-spacing: -0.03em; max-width: 80%; }
  .arc-alert-stamp .story-meta { color: #c13830; }

  /* ───── ACADEMIC DROP CAP ───── */
  .arc-academic-drop-cap { background: #f2ebd5; color: #1d1c16; padding: 10vh 8vw; }
  .arc-academic-drop-cap .paper-head {
    display: flex; justify-content: space-between; align-items: baseline; border-bottom: 2px solid #1d1c16;
    padding-bottom: 1rem; margin-bottom: 4rem; font-weight: 600; font-size: 1.1rem; letter-spacing: 0.3em; text-transform: uppercase; opacity: 0.5;
  }
  .arc-academic-drop-cap .numeral-roman { font-family: 'Fraunces', serif; font-weight: 700; font-size: clamp(3rem, 4.5vw, 4.5rem); font-style: italic; }
  .arc-academic-drop-cap h2 { font-family: 'Fraunces', serif; font-weight: 400; font-style: italic; font-size: clamp(2.8rem, 6vw, 5.5rem); line-height: 1.02; margin-bottom: 2rem; }
  .arc-academic-drop-cap .cols { display: grid; grid-template-columns: 1fr 1fr; gap: 4.5rem; }
  .arc-academic-drop-cap .cols p { font-family: 'Fraunces', serif; font-size: 1.45rem; line-height: 1.6; text-align: justify; hyphens: auto; }
  .arc-academic-drop-cap .cols > div:first-child p:first-child::first-letter {
    font-family: 'Fraunces', serif; font-weight: 950; font-size: 7.5em; float: left; line-height: 0.78; padding: 0.1em 0.15em 0 0; color: #7a1e14;
  }

  /* ───── TERMINAL ───── */
  .arc-terminal { background: #000; color: #95ff95; font-family: 'JetBrains Mono', monospace; padding: 10vh 8vw; }
  .arc-terminal .prompt-row { font-size: 1.2rem; font-weight: 600; color: #51da51; margin-bottom: 4rem; }
  .arc-terminal .numeral { font-size: clamp(7rem, 15vw, 14rem); font-weight: 800; line-height: 1; color: #95ff95; margin: 0 0 1.5rem; }
  .arc-terminal .numeral::before { content: "> "; color: #51da51; }
  .arc-terminal h2 { font-weight: 800; font-size: clamp(2.5rem, 5.5vw, 5rem); line-height: 1.05; color: #e1ffe1; }
  .arc-terminal .lede { font-size: 1.4rem; line-height: 1.7; max-width: 850px; color: #95ff95; }
  .arc-terminal .lede::before { content: "# "; color: #51da51; }
  .arc-terminal .cursor { display: inline-block; width: 0.6em; height: 1.2em; background: #95ff95; vertical-align: text-bottom; margin-left: 0.3em; animation: me-blink 1.2s steps(2, start) infinite; }
  @keyframes me-blink { to { visibility: hidden; } }
  .arc-terminal .drawer-content { background: rgba(149, 255, 149, 0.06); border-color: #95ff95; color: #95ff95; }

  /* ───── EDITORIAL PULLQUOTE ───── */
  .arc-editorial-pullquote { background: #181818; color: #fcfaf5; padding: 10vh 8vw; display: grid; grid-template-columns: 1fr 1.15fr; gap: 6rem; align-items: center; }
  .arc-editorial-pullquote .numeral { font-family: 'Fraunces', serif; font-weight: 950; font-style: italic; font-size: clamp(10rem, 20vw, 18rem); line-height: 0.85; color: #fcfaf5; letter-spacing: -0.06em; margin-bottom: 1.5rem; }
  .arc-editorial-pullquote .kicker { font-weight: 800; font-size: 1.2rem; letter-spacing: 0.4em; color: #f1c40f; margin-bottom: 2rem; }
  .arc-editorial-pullquote h2 { font-family: 'Fraunces', serif; font-weight: 800; font-size: clamp(2.4rem, 4.5vw, 4rem); line-height: 1.05; }
  .arc-editorial-pullquote .pullquote {
    font-family: 'Fraunces', serif; font-style: italic; font-weight: 400; font-size: clamp(2rem, 3.8vw, 3.4rem); line-height: 1.1; color: #fcfaf5; border-left: 6px solid #f1c40f; padding-left: 2.5rem; margin: 0;
  }
  .arc-editorial-pullquote .pullquote::before { content: "\201C"; color: #f1c40f; font-size: 1.5em; vertical-align: -0.4em; line-height: 0; }

  /* ───── CAUTION TAPE ───── */
  .arc-caution-tape { background: #ffd900; color: #050505; padding: 10vh 8vw; }
  .arc-caution-tape::before, .arc-caution-tape::after { content: ""; position: absolute; left: 0; right: 0; height: 3.2rem; background: repeating-linear-gradient(135deg, #050505 0 2.5rem, #ffd900 2.5rem 5rem); }
  .arc-caution-tape::before { top: 0; } .arc-caution-tape::after { bottom: 0; }
  .arc-caution-tape .tag { position: absolute; top: 8vh; right: 6vw; writing-mode: vertical-rl; transform: rotate(180deg); font-weight: 950; font-size: 1.5rem; letter-spacing: 0.6em; background: #050505; color: #ffd900; padding: 1.2rem 0.8rem; }
  .arc-caution-tape .numeral { font-weight: 950; font-size: clamp(12rem, 32vw, 28rem); line-height: 0.8; -webkit-text-stroke: 4px #050505; color: transparent; margin-bottom: 1.5rem; }
  .arc-caution-tape .kicker { font-weight: 950; font-size: 1.4rem; letter-spacing: 0.4em; background: #050505; color: #ffd900; padding: 0.6rem 1.2rem; display: inline-block; }
  .arc-caution-tape h2 { font-weight: 950; font-size: clamp(3rem, 7vw, 6.5rem); line-height: 0.95; letter-spacing: -0.04em; text-transform: uppercase; }

  /* ───── NOTEBOOK ───── */
  .arc-notebook { background: repeating-linear-gradient(#fdfbf2 0 2.5rem, rgba(130,110,70,0.25) 2.5rem 2.55rem); color: #221c0e; padding: 10vh 10vw; }
  .arc-notebook::before { content: ""; position: absolute; top: 0; bottom: 0; left: 10vw; width: 3px; background: #d32f2f; opacity: 0.6; }
  .arc-notebook h2 { font-family: 'Fraunces', serif; font-weight: 400; font-style: italic; font-size: clamp(3.2rem, 7vw, 6.5rem); line-height: 1; letter-spacing: -0.03em; }
  .arc-notebook .lede { font-family: 'Fraunces', serif; font-size: clamp(1.5rem, 2.2vw, 1.9rem); line-height: 2.5rem; max-width: 780px; }

  /* ───── MINT PATTERN ───── */
  .arc-mint-pattern { background: #b0eed4; color: #0a3325; padding: 10vh 8vw; }
  .arc-mint-pattern::before {
    content: "LISP AWK SED GREP CURL MAKE GIT VIM SSH BASH"; font-family: 'JetBrains Mono', monospace; font-weight: 800; font-size: 3rem; letter-spacing: 0.4em;
    position: absolute; top: 12vh; left: 0; right: 0; color: rgba(10,51,37,0.12); white-space: nowrap; overflow: hidden; pointer-events: none;
  }
  .arc-mint-pattern h2 { font-family: 'Fraunces', serif; font-weight: 900; font-size: clamp(3rem, 7vw, 6rem); line-height: 0.95; }

  /* ───── PASTEL PLAYFUL ───── */
  .arc-pastel-playful { background: #fdeaf5; color: #300c35; padding: 10vh 8vw 6vh; }
  .arc-pastel-playful .alpha-grid { position: absolute; top: 0; left: 0; right: 0; display: grid; grid-template-columns: repeat(13, 1fr); font-family: 'Fraunces', serif; font-style: italic; font-size: clamp(1.4rem, 2.8vw, 2.2rem); font-weight: 800; color: rgba(48,12,53,0.22); padding: 1.2rem 8vw; letter-spacing: 0.25em; pointer-events: none; }
  .arc-pastel-playful .alpha-grid span { text-align: center; }
  .arc-pastel-playful .numeral { font-family: 'Fraunces', serif; font-weight: 950; font-style: italic; font-size: clamp(12rem, 30vw, 25rem); color: #c93d7c; margin-top: 3rem; line-height: 0.8; }

  /* ───── PRODUCT PLATE ───── */
  .arc-product-plate { background: linear-gradient(135deg, #fcfcfb 0%, #f0f0ee 100%); color: #151515; padding: 12vh 10vw; text-align: center; }
  .arc-product-plate .product-chip { font-weight: 800; font-size: 1.1rem; letter-spacing: 0.5em; background: #151515; color: #fcfcfb; padding: 0.6rem 1.6rem; display: inline-block; margin-bottom: 3.5rem; }
  .arc-product-plate h2 { font-family: 'Inter', sans-serif; font-weight: 950; font-size: clamp(3.5rem, 10vw, 9rem); line-height: 0.92; letter-spacing: -0.05em; max-width: 15ch; margin: 0 auto 2.5rem; }
  .arc-product-plate .product-rule { width: 8rem; height: 3px; background: #151515; margin: 2rem auto 2.5rem; }

  /* ───── ARCHIVE ───── */
  .arc-archive { background: #f0e2c5; background-image: radial-gradient(rgba(90,50,15,0.06) 1px, transparent 1px), radial-gradient(rgba(90,50,15,0.05) 1px, transparent 1px); background-size: 5px 5px, 9px 9px; color: #30200a; padding: 10vh 10vw; }
  .arc-archive .archive-head { display: flex; justify-content: space-between; align-items: baseline; border-top: 8px double #4a3a15; border-bottom: 8px double #4a3a15; padding: 1.2rem 0; margin-bottom: 4rem; font-family: 'Fraunces', serif; font-style: italic; font-weight: 600; font-size: 1.2rem; letter-spacing: 0.2em; color: #4a3a15; }

  /* ───── OBITUARY ───── */
  .arc-obituary { background: #faf8f2; color: #151515; padding: 12vh 8vw; align-items: center; justify-content: center; }
  .arc-obituary .obit-frame { border: 4px solid #151515; padding: 7rem 6rem; max-width: 850px; text-align: center; position: relative; }
  .arc-obituary .obit-frame::before, .arc-obituary .obit-frame::after { content: ""; position: absolute; left: -15px; right: -15px; height: 1.5px; background: #151515; }
  .arc-obituary .obit-frame::before { top: -15px; } .arc-obituary .obit-frame::after { bottom: -15px; }
  .arc-obituary .ornament { font-family: 'Fraunces', serif; font-size: 4.5rem; margin-bottom: 2.5rem; }

  /* ───── BLUEPRINT ───── */
  .arc-blueprint { background: #0c4876; background-image: linear-gradient(rgba(255,255,255,0.1) 1.5px, transparent 1.5px), linear-gradient(90deg, rgba(255,255,255,0.1) 1.5px, transparent 1.5px); background-size: 2.5rem 2.5rem; color: #f0f7ff; padding: 10vh 8vw; }
  .arc-blueprint .corner { position: absolute; font-family: 'JetBrains Mono', monospace; font-size: 3rem; color: #f0f7ff; opacity: 0.8; }
  .arc-blueprint .numeral { font-family: 'JetBrains Mono', monospace; font-weight: 800; font-size: clamp(5rem, 11vw, 9rem); border-bottom: 3px dashed rgba(240,247,255,0.6); padding-bottom: 1.2rem; display: inline-block; margin-bottom: 2rem; }

  /* ───── OBSERVATORY ───── */
  .arc-observatory { background: #080f3d; color: #f0f4ff; padding: 10vh 8vw; }
  .arc-observatory .starfield { position: absolute; inset: 0; pointer-events: none; background-image: radial-gradient(1.5px 1.5px at 10% 20%, #fff, transparent), radial-gradient(1.5px 1.5px at 25% 70%, #fff, transparent), radial-gradient(2px 2px at 40% 30%, #fff, transparent), radial-gradient(1.5px 1.5px at 70% 15%, #fff, transparent), radial-gradient(2px 2px at 85% 55%, #fff, transparent); }
  .arc-observatory .numeral { font-family: 'Fraunces', serif; font-style: italic; font-weight: 500; font-size: clamp(4rem, 9vw, 8rem); border-bottom: 2px solid rgba(240,244,255,0.5); padding-bottom: 1.2rem; display: inline-block; margin-bottom: 2.5rem; }

  /* GH Specific: Brutalist System Core */
  .arc-system-core { background: #344054; color: #f9fafb; }
  .arc-system-core .concrete { position: absolute; inset: 0; pointer-events: none; opacity: 0.12; background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.75' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E"); }
  .arc-system-core h2 { font-family: 'Inter', sans-serif; font-weight: 950; text-transform: uppercase; letter-spacing: -0.06em; font-size: clamp(3.5rem, 12vw, 9.5rem); line-height: 0.88; }
  .arc-system-core .drawer-content { background: rgba(0,0,0,0.1); border-color: #f9fafb; color: #f9fafb; }

  /* GH Specific: UI Lab */
  .arc-ui-lab { background: #fff; color: #5850ec; }
  .arc-ui-lab .lab-gradient { position: absolute; inset: 0; pointer-events: none; opacity: 0.18; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); }
  .arc-ui-lab .read-more { border-radius: 12px; border: none; background: #5850ec; color: #fff; padding: 1.2rem 2.4rem; box-shadow: 0 10px 25px rgba(88, 80, 236, 0.25); }
  .arc-ui-lab .kicker { font-family: 'Inter', sans-serif; font-weight: 800; font-size: 1.1rem; letter-spacing: 0.35em; color: #5850ec; margin-bottom: 1.8rem; text-transform: uppercase; position: relative; z-index: 2; }
  .arc-ui-lab .numeral { font-family: 'Fraunces', serif; font-style: italic; font-weight: 700; font-size: clamp(6rem, 13vw, 11rem); color: #5850ec; margin-bottom: 1.2rem; line-height: 0.95; position: relative; z-index: 2; }
  .arc-ui-lab h2 { font-family: 'Inter', sans-serif; font-weight: 900; font-size: clamp(3rem, 7vw, 6rem); line-height: 1; letter-spacing: -0.03em; position: relative; z-index: 2; max-width: 20ch; }
  .arc-ui-lab .lede { font-size: 1.4rem; line-height: 1.6; max-width: 780px; color: #312e81; position: relative; z-index: 2; }

  /* GH Specific: Agent Foundry (overlays on arc-blueprint) */
  .arc-agent-foundry .corner { font-family: 'JetBrains Mono', monospace; opacity: 0.75; }
  .arc-agent-foundry .corner.tl { top: 3.5vh; left: 4vw; }
  .arc-agent-foundry .corner.tr { top: 3.5vh; right: 4vw; }
  .arc-agent-foundry .corner.bl { bottom: 3.5vh; left: 4vw; }
  .arc-agent-foundry .corner.br { bottom: 3.5vh; right: 4vw; }
  .arc-agent-foundry .kicker { font-family: 'JetBrains Mono', monospace; font-weight: 700; font-size: 1rem; letter-spacing: 0.35em; color: #a8c7e8; margin-bottom: 2rem; text-transform: uppercase; }
  .arc-agent-foundry h2 { font-family: 'Inter', sans-serif; font-weight: 900; font-size: clamp(3rem, 7.5vw, 6.2rem); line-height: 1; max-width: 18ch; letter-spacing: -0.03em; }
  .arc-agent-foundry .lede { font-size: 1.4rem; line-height: 1.55; max-width: 760px; color: #d8e7f5; }

  /* GH Specific: Data Pipeline */
  .arc-data-pipeline { background: radial-gradient(ellipse at 20% 85%, #4c1d95 0%, #1a0a47 55%, #0a0525 100%); color: #e9e2ff; padding: 10vh 8vw; }
  .arc-data-pipeline .pipeline-streams { position: absolute; inset: 0; pointer-events: none; opacity: 0.55;
    background-image:
      radial-gradient(1px 140px at 18% 0%, #a78bfa, transparent),
      radial-gradient(1px 200px at 48% 0%, #818cf8, transparent),
      radial-gradient(1px 240px at 78% 0%, #c4b5fd, transparent),
      radial-gradient(1px 180px at 92% 0%, #6366f1, transparent); }
  .arc-data-pipeline .numeral { font-family: 'JetBrains Mono', monospace; font-weight: 300; font-size: clamp(6rem, 13vw, 11rem); letter-spacing: -0.04em; color: transparent; -webkit-text-stroke: 2px #a78bfa; margin-bottom: 2rem; line-height: 0.95; }
  .arc-data-pipeline .kicker { font-family: 'JetBrains Mono', monospace; font-weight: 700; font-size: 1.05rem; letter-spacing: 0.3em; color: #a78bfa; margin-bottom: 1.8rem; text-transform: uppercase; }
  .arc-data-pipeline h2 { font-family: 'Fraunces', serif; font-weight: 700; font-size: clamp(3rem, 7vw, 6rem); line-height: 1.02; letter-spacing: -0.03em; max-width: 20ch; }
  .arc-data-pipeline .lede { font-size: 1.4rem; line-height: 1.6; max-width: 780px; color: #dcd3ff; }
  .arc-data-pipeline .drawer-content { background: rgba(167,139,250,0.1); border-color: #a78bfa; color: #e9e2ff; }

  /* GH Specific: Terminal Utility (overlays on arc-terminal) */
  .arc-terminal-utility { box-shadow: inset 0 0 10rem rgba(0,0,0,0.75); }
  .arc-terminal-utility .crt-lines { position: absolute; inset: 0; pointer-events: none; z-index: 1;
    background: repeating-linear-gradient(transparent 0 2px, rgba(149,255,149,0.05) 2px 3px); }
  .arc-terminal-utility .prompt-row,
  .arc-terminal-utility .numeral,
  .arc-terminal-utility h2,
  .arc-terminal-utility .story-meta,
  .arc-terminal-utility .lede,
  .arc-terminal-utility .spread-links { position: relative; z-index: 2; }

  /* GH Specific: Model Bench */
  .arc-model-bench { background: linear-gradient(180deg, #fafafa 0%, #d4d4d8 100%); color: #18181b; padding: 10vh 8vw; }
  .arc-model-bench .bench-silver { position: absolute; inset: 0; pointer-events: none;
    background:
      repeating-linear-gradient(90deg, transparent 0 49px, rgba(24,24,27,0.07) 49px 50px),
      linear-gradient(135deg, rgba(255,255,255,0.55) 0%, transparent 55%); }
  .arc-model-bench .numeral { font-family: 'Fraunces', serif; font-style: italic; font-weight: 300; font-size: clamp(6rem, 14vw, 12rem); letter-spacing: -0.05em; color: #52525b; margin-bottom: 2rem; line-height: 0.88; }
  .arc-model-bench .kicker { font-family: 'JetBrains Mono', monospace; font-weight: 600; font-size: 0.95rem; letter-spacing: 0.4em; color: #52525b; margin-bottom: 1.8rem; text-transform: uppercase; }
  .arc-model-bench h2 { font-family: 'Fraunces', serif; font-weight: 400; font-size: clamp(3rem, 7vw, 6rem); line-height: 1.04; letter-spacing: -0.03em; max-width: 20ch; }
  .arc-model-bench .lede { font-size: 1.4rem; line-height: 1.6; max-width: 780px; color: #3f3f46; }

  /* GH Specific: Privacy Shield */
  .arc-privacy-shield { background: #0a0a0a; color: #fafafa; padding: 10vh 8vw; }
  .arc-privacy-shield .shield-glitch { position: absolute; inset: 0; pointer-events: none;
    background:
      linear-gradient(90deg, transparent 0 49%, rgba(239,68,68,0.1) 49% 51%, transparent 51%),
      radial-gradient(ellipse at 70% 30%, rgba(34,211,238,0.08) 0%, transparent 45%); }
  .arc-privacy-shield::before { content: ""; position: absolute; top: 5vh; right: 5vw; width: 14rem; height: 14rem;
    background: conic-gradient(from 90deg at 50% 50%, #22d3ee, transparent 60%, #ef4444);
    opacity: 0.2; border-radius: 50%; filter: blur(22px); pointer-events: none; }
  .arc-privacy-shield .numeral { font-family: 'JetBrains Mono', monospace; font-weight: 800; font-size: clamp(6rem, 13vw, 11rem); letter-spacing: -0.04em; color: #fafafa; margin-bottom: 1.5rem; line-height: 1; position: relative; z-index: 2; }
  .arc-privacy-shield .numeral::after { content: "█"; color: #22d3ee; margin-left: 0.25em; animation: me-blink 1.2s steps(2, start) infinite; }
  .arc-privacy-shield .kicker { font-family: 'JetBrains Mono', monospace; font-weight: 700; font-size: 1rem; letter-spacing: 0.35em; color: #22d3ee; margin-bottom: 2rem; text-transform: uppercase; position: relative; z-index: 2; }
  .arc-privacy-shield h2 { font-family: 'JetBrains Mono', monospace; font-weight: 700; font-size: clamp(2.6rem, 5.5vw, 4.8rem); line-height: 1.1; letter-spacing: -0.02em; position: relative; z-index: 2; }
  .arc-privacy-shield .lede { font-family: 'JetBrains Mono', monospace; font-size: 1.15rem; line-height: 1.75; max-width: 780px; color: #e4e4e7; position: relative; z-index: 2; }
  .arc-privacy-shield .drawer-content { background: rgba(34,211,238,0.06); border-color: #22d3ee; color: #fafafa; }

  /* GH Specific: Enterprise Engine */
  .arc-enterprise-engine { background: #eff6ff; color: #0c1e4e; padding: 10vh 8vw; }
  .arc-enterprise-engine .engine-blue { position: absolute; inset: 0; pointer-events: none;
    background:
      radial-gradient(ellipse at 85% 15%, rgba(29,78,216,0.14) 0%, transparent 42%),
      radial-gradient(ellipse at 15% 85%, rgba(37,99,235,0.1) 0%, transparent 42%); }
  .arc-enterprise-engine::before { content: ""; position: absolute; top: 0; left: 0; right: 0; height: 10px;
    background: linear-gradient(90deg, #1d4ed8 0%, #3b82f6 50%, #1d4ed8 100%); }
  .arc-enterprise-engine .numeral { font-family: 'Inter', sans-serif; font-weight: 100; font-size: clamp(6rem, 13vw, 11rem); color: #1d4ed8; letter-spacing: -0.06em; margin-bottom: 1.5rem; line-height: 1; }
  .arc-enterprise-engine .kicker { font-family: 'Inter', sans-serif; font-weight: 700; font-size: 1rem; letter-spacing: 0.32em; color: #1d4ed8; margin-bottom: 2rem; text-transform: uppercase; }
  .arc-enterprise-engine h2 { font-family: 'Inter', sans-serif; font-weight: 800; font-size: clamp(3rem, 7vw, 5.8rem); line-height: 1.02; letter-spacing: -0.03em; max-width: 20ch; }
  .arc-enterprise-engine .lede { font-size: 1.4rem; line-height: 1.6; max-width: 780px; color: #1e3a8a; }

  /* GH Specific: Experimental Workshop */
  .arc-experimental-workshop { background: #fef9c3; color: #422006; padding: 10vh 8vw; }
  .arc-experimental-workshop .pad-lines { position: absolute; inset: 0; pointer-events: none;
    background-image: repeating-linear-gradient(transparent 0 2.2rem, rgba(66,32,6,0.22) 2.2rem 2.25rem); }
  .arc-experimental-workshop::before { content: ""; position: absolute; top: 0; bottom: 0; left: calc(6vw + 3.5rem); width: 2px; background: #dc2626; opacity: 0.65; }
  .arc-experimental-workshop .numeral { font-family: 'Fraunces', serif; font-style: italic; font-weight: 800; font-size: clamp(7rem, 14vw, 12rem); color: #422006; margin-bottom: 1.5rem; line-height: 0.9; transform: rotate(-3deg); display: inline-block; position: relative; z-index: 2; }
  .arc-experimental-workshop .kicker { font-family: 'Fraunces', serif; font-style: italic; font-weight: 700; font-size: 1.3rem; color: #78350f; margin-bottom: 2rem; position: relative; z-index: 2; }
  .arc-experimental-workshop h2 { font-family: 'Fraunces', serif; font-weight: 800; font-style: italic; font-size: clamp(3rem, 7vw, 5.8rem); line-height: 1.04; letter-spacing: -0.02em; max-width: 22ch; position: relative; z-index: 2; }
  .arc-experimental-workshop .lede { font-family: 'Fraunces', serif; font-size: 1.45rem; line-height: 2.2rem; max-width: 780px; position: relative; z-index: 2; }

  /* GH Specific: Library Archive */
  .arc-library-archive { background: #f5ead8; color: #2a1f10; padding: 12vh 10vw; }
  .arc-library-archive .parchment { position: absolute; inset: 0; pointer-events: none; opacity: 0.45;
    background-image:
      radial-gradient(ellipse at 0% 50%, rgba(120,80,20,0.18) 0%, transparent 55%),
      radial-gradient(ellipse at 100% 50%, rgba(120,80,20,0.15) 0%, transparent 55%); }
  .arc-library-archive::before { content: ""; position: absolute; top: 6vh; left: 0; right: 0; height: 6px; border-top: 2px solid #6b4a20; border-bottom: 2px solid #6b4a20; opacity: 0.55; }
  .arc-library-archive::after { content: ""; position: absolute; bottom: 6vh; left: 0; right: 0; height: 6px; border-top: 2px solid #6b4a20; border-bottom: 2px solid #6b4a20; opacity: 0.55; }
  .arc-library-archive .numeral { font-family: 'Fraunces', serif; font-style: italic; font-weight: 500; font-size: clamp(5rem, 11vw, 9rem); color: #6b4a20; margin-bottom: 1.5rem; line-height: 0.95; position: relative; z-index: 2; }
  .arc-library-archive .kicker { font-family: 'Fraunces', serif; font-style: italic; font-weight: 700; font-size: 1.3rem; letter-spacing: 0.1em; color: #6b4a20; margin-bottom: 2rem; position: relative; z-index: 2; }
  .arc-library-archive h2 { font-family: 'Fraunces', serif; font-weight: 500; font-style: italic; font-size: clamp(3rem, 7vw, 6rem); line-height: 1.06; letter-spacing: -0.01em; max-width: 24ch; position: relative; z-index: 2; }
  .arc-library-archive .lede { font-family: 'Fraunces', serif; font-size: 1.4rem; line-height: 1.7; max-width: 780px; font-weight: 400; position: relative; z-index: 2; }

  /* ───── DOSSIER ───── */
  .dossier { background: #f5f1e8; color: #1b1a14; padding: 10rem 8vw 8rem; border-top: 10px solid #121212; }
  .dossier .dossier-head { text-align: center; margin-bottom: 6rem; }
  .dossier h2 { font-family: 'Fraunces', serif; font-style: italic; font-weight: 950; font-size: clamp(5rem, 12vw, 11rem); line-height: 0.85; margin-bottom: 2rem; font-variation-settings: "SOFT" 30; }
  .dossier .intro { font-family: 'Fraunces', serif; font-style: italic; font-size: 1.5rem; line-height: 1.45; max-width: 700px; margin: 0 auto; opacity: 0.7; }
  .dossier-entry { border-top: 2px solid #1b1a14; padding: 4rem 0 3.5rem; max-width: 900px; margin: 0 auto; }
  .dossier-meta { font-weight: 700; font-size: 1.1rem; letter-spacing: 0.35em; color: #7a1e14; margin-bottom: 1.5rem; }
  .dossier-entry h3 { font-family: 'Fraunces', serif; font-weight: 800; font-size: clamp(2rem, 4vw, 3rem); line-height: 1.1; letter-spacing: -0.02em; }
  .dossier-entry h4 { font-family: 'Inter', sans-serif; font-weight: 800; font-size: 1.1rem; letter-spacing: 0.4em; color: #7a1e14; margin: 2.5rem 0 1.5rem; }
  .dossier-entry p { font-family: 'Fraunces', serif; font-size: 1.35rem; line-height: 1.6; margin-bottom: 1.2rem; }

  /* ───── COLOPHON ───── */
  .colophon { background: #121212; color: #f5f1e8; padding: 7rem 6vw; text-align: center; }
  .colophon .sig { font-family: 'Fraunces', serif; font-style: italic; font-weight: 400; font-size: clamp(3rem, 6vw, 5rem); margin-bottom: 2rem; font-variation-settings: "SOFT" 60; }
  .colophon .classic-link { margin-top: 3rem; font-weight: 600; font-size: 1.1rem; letter-spacing: 0.25em; text-transform: uppercase; }

  /* ───── RESPONSIVE ───── */
  @media (max-width: 800px) {
    .masthead-nav { flex-wrap: wrap; gap: 1rem; }
    .masthead h1 { font-size: 4rem; line-height: 0.9; }
    .issue-line { flex-direction: column; gap: 1.5rem; align-items: center; text-align: center; }
    .arc-stat-hero h2, .arc-stat-hero .lede { max-width: 100%; }
    .arc-stat-hero .numeral { font-size: 16rem; opacity: 0.25; top: 35vh; }
    .arc-academic-drop-cap .cols { grid-template-columns: 1fr; gap: 2.5rem; }
    .arc-editorial-pullquote { grid-template-columns: 1fr; gap: 4rem; }
    .arc-caution-tape .tag { display: none; }
    .arc-pastel-playful .alpha-grid { display: none; }
    .arc-blueprint .corner { display: none; }
  }
"""

def generate_morning_edition_html(
    config: EditionConfig,
    day: date,
    items: list[dict],
    assignments: list[dict],
) -> str:
    title = f"{config.name} — {day.strftime('%B %-d, %Y')}"

    spreads = []
    for i, (a, item) in enumerate(zip(assignments, items), start=1):
        arch_id = a["archetype_id"]
        renderer = SPREAD_RENDERERS.get(arch_id, _arc_stat_hero)
        spreads.append(renderer(config, i, a, item))

    spreads_html = "\n".join(spreads)
    v = get_git_sha()
    # gh lives at docs/<date>/ (one level under docs); hn and ai at docs/<src>/<date>/ (two).
    rel_prefix = ".." if config.id == "gh" else "../.."
    pref_src = f"{rel_prefix}/preference.js?v={v}"
    css_href = f"{rel_prefix}/morning.css?v={v}"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{_h(title)}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght,SOFT@0,9..144,300..900,0..100;1,9..144,300..900,0..100&family=Inter:ital,wght@0,100..900;1,100..900&family=JetBrains+Mono:ital,wght@0,100..800;1,100..800&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="{css_href}">
</head>
<body id="top" data-gtd-edition="{config.id}" data-gtd-date="{day.isoformat()}">
{_render_masthead(config, day)}
{spreads_html}
{_render_dossier(config, items, assignments)}
{_render_colophon(day)}
{_render_readtracker(config, day)}
<script src="{pref_src}" defer></script>
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

// Share link — native share or copy markdown to clipboard
(function () {{
  const links = document.querySelectorAll('.share-link');
  if (!links.length) return;

  const toast = document.createElement('div');
  toast.className = 'discuss-toast';
  document.body.appendChild(toast);

  function showToast(msg) {{
    toast.textContent = msg;
    toast.classList.add('show');
    setTimeout(() => toast.classList.remove('show'), 2500);
  }}

  function buildMarkdown(link, spread) {{
    const title = link.dataset.shareTitle || '';
    const url = link.dataset.shareUrl || '';
    const meta = spread ? spread.querySelector('.story-meta') : null;
    const lede = spread ? spread.querySelector('.lede') : null;
    const analysis = spread ? spread.querySelector('.drawer-analysis') : null;
    let md = '# ' + title + '\\n';
    md += 'Source: ' + url;
    if (meta) md += ' | ' + meta.textContent.trim();
    md += '\\n';
    if (lede) md += '\\n' + lede.textContent.trim() + '\\n';
    if (analysis) md += '\\n## Analysis\\n' + analysis.textContent.trim() + '\\n';
    return md;
  }}

  links.forEach((link) => {{
    link.addEventListener('click', (e) => {{
      e.preventDefault();
      const spread = link.closest('section.spread');
      const md = buildMarkdown(link, spread);
      const text = "I'd like to discuss this with you. Here's a summary:\\n\\n" + md;
      const title = 'Discuss: ' + (link.dataset.shareTitle || '');
      if (navigator.share) {{
        navigator.share({{ title: title, text: text }}).catch(() => {{}});
      }} else {{
        navigator.clipboard.writeText(text).then(
          () => showToast('Copied to clipboard \\u2014 paste into your AI chat'),
          () => showToast('Failed to copy')
        );
      }}
    }});
  }});
}})();
</script>
</body>
</html>"""

def _write_shared_css() -> None:
    css_path = REPO_ROOT / "docs" / "morning.css"
    new_content = CSS_TEMPLATE.lstrip("\n")
    if css_path.exists() and css_path.read_text() == new_content:
        return
    css_path.write_text(new_content)


def _write_classic_redirect(index_file: Path) -> None:
    """Write an index.html that redirects to the classic view in the same dir."""
    index_file.write_text(
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">\n'
        '<meta http-equiv="refresh" content="0; url=classic.html">\n'
        '<link rel="canonical" href="classic.html">\n'
        "<title>Redirecting…</title></head>\n"
        '<body><p>Redirecting to the <a href="classic.html">classic view</a>.</p></body></html>\n'
    )


def generate_morning_edition(
    day: date,
    items: list[dict],
    source: Literal["hn", "gh", "ai"] = "hn",
    force_regenerate: bool = False,
) -> str:
    config = CONFIGS[source]
    # HN/GitHub cap at 10; the AI edition (max_stories=None) uses every item passed in.
    items = items[: config.max_stories] if config.max_stories else list(items)
    output_dir = config.output_dir / day.isoformat()
    output_dir.mkdir(parents=True, exist_ok=True)

    _write_shared_css()

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
        try:
            assignments = pick_editorial(config, items)
        except Exception as exc:
            # Never leave the page without an index.html. The classic view
            # (classic.html) is always written before this point, so fall back
            # to redirecting there rather than 404-ing the published URL, then
            # re-raise so the caller can alert that the page is degraded.
            logging.error(
                "%s: editorial generation failed for %s, wrote classic-view fallback: %s",
                config.name, day, exc,
            )
            _write_classic_redirect(index_file)
            raise
        with open(assignments_file, "w") as f:
            json.dump(assignments, f, indent=2)
            
    html = generate_morning_edition_html(config, day, items, assignments)
    
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
