# HN Comment Camps Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace HN's 3-bullet Gemini comment analysis with an SSC-style two-stage OpenAI pipeline that ingests an indented whole-thread outline and emits structured JSON (framing + camps + verbatim quotes), rendered fully in both editions.

**Architecture:** A new self-contained module `hn_comment_camps.py` owns the OpenAI two-stage analysis, the JSON parse/validate, and the camps→HTML rendering (pure, no import of `trending_digest`). `trending_digest.py` gains a contiguous, char-budgeted outline builder and rewires its analysis + cache + card renderer to the new module. `morning_edition.py` renders the same camps HTML for HN, falling back to legacy bullets for old rows and for GitHub.

**Tech Stack:** Python 3.12, `openai` (Responses API, structured outputs), `psycopg`, `beautifulsoup4`, `requests`, `pytest`.

**Spec:** `docs/superpowers/specs/2026-08-17-hn-comment-camps-analysis-design.md`

## Global Constraints

- Model output is **structured JSON only** — never HTML or markdown. Upstream owns presentation; escape all model strings with `html.escape` at render time.
- New format is **HN-only**. GitHub's `comment_analysis` stays `""` and its bullet/"Insights" path is untouched.
- Renderers must tolerate **both** legacy `hn_comments_v2` bullet strings and new `hn_comments_v3` JSON, decided by `parse_comment_analysis` returning `None` for non-JSON.
- Never crash the run on analysis failure: missing `OPENAI_API_KEY` or any OpenAI error → keep prior cached analysis if present, else no reactions block.
- Output caps enforced structurally: `≤ HN_COMMENT_ANALYSIS_MAX_CAMPS` camps (default 6), `≤ HN_COMMENT_ANALYSIS_MAX_QUOTES` quotes per camp (default 3). Input capped at `HN_COMMENT_ANALYSIS_MAX_CHARS` (default 48000).
- Tests live in `tests/`, run with `uv run pytest`; `pythonpath = ["."]` so imports are top-level (`from hn_comment_camps import ...`).
- Prod sets `COMMENT_BRIEFING_MODEL=gpt-5.6-luna`; default is `gpt-5-mini`.

---

### Task 1: `parse_comment_analysis` + `render_outline` (pure helpers)

**Files:**
- Create: `hn_comment_camps.py`
- Test: `tests/test_hn_comment_camps.py`

**Interfaces:**
- Produces: `parse_comment_analysis(raw: str | None) -> dict | None` — returns a validated `{"framing": str, "camps": [{"label","description","quotes":[{"text","author"}]}]}` dict for valid v3 JSON, else `None`.
- Produces: `render_outline(comments: list[dict]) -> str` — indented outline; each dict has `depth: int` (1-based), `by: str`, `text: str`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_hn_comment_camps.py
import json

from hn_comment_camps import parse_comment_analysis, render_outline


def test_render_outline_indents_by_depth():
    comments = [
        {"depth": 1, "by": "alice", "text": "top level"},
        {"depth": 2, "by": "bob", "text": "a reply"},
        {"depth": 3, "by": "cara", "text": "deeper"},
    ]
    assert render_outline(comments) == (
        "alice: top level\n"
        "    bob: a reply\n"
        "        cara: deeper"
    )


def test_parse_valid_json_returns_dict():
    raw = json.dumps({
        "framing": "People argued.",
        "camps": [
            {"label": "Pro", "description": "liked it",
             "quotes": [{"text": "great", "author": "pg"}]},
        ],
    })
    parsed = parse_comment_analysis(raw)
    assert parsed is not None
    assert parsed["framing"] == "People argued."
    assert parsed["camps"][0]["quotes"][0]["author"] == "pg"


def test_parse_legacy_bullets_returns_none():
    assert parse_comment_analysis("- consensus\n- disagreement\n- takeaway") is None


def test_parse_blank_and_malformed_return_none():
    assert parse_comment_analysis("") is None
    assert parse_comment_analysis(None) is None
    assert parse_comment_analysis("{not json") is None


def test_parse_wrong_shape_returns_none():
    assert parse_comment_analysis(json.dumps({"framing": "x"})) is None  # no camps
    assert parse_comment_analysis(json.dumps({"camps": []})) is None      # no framing
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_hn_comment_camps.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hn_comment_camps'`

- [ ] **Step 3: Write minimal implementation**

```python
# hn_comment_camps.py
"""SSC-style two-stage comment analysis for Hacker News threads.

Ingests an indented thread outline and emits structured JSON (framing + camps
with verbatim quotes) via the OpenAI Responses API. Pure of any presentation:
the model returns data, callers render it. No import of trending_digest.
"""

import json


def render_outline(comments: list[dict]) -> str:
    """Render comments as an indented outline: a reply sits under its parent.

    Each comment dict has ``depth`` (1-based), ``by``, and ``text``.

    Returns:
        The newline-joined ``{indent}{author}: {text}`` outline.
    """
    return "\n".join(
        f"{'    ' * (int(c['depth']) - 1)}{c['by']}: {c['text']}" for c in comments
    )


def _is_quote(value: object) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("text"), str)
        and isinstance(value.get("author"), str)
    )


def _is_camp(value: object) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("label"), str)
        and isinstance(value.get("description"), str)
        and isinstance(value.get("quotes"), list)
        and all(_is_quote(q) for q in value["quotes"])
    )


def parse_comment_analysis(raw: str | None) -> dict | None:
    """Parse a stored analysis string into a validated camps dict.

    Returns:
        The dict for valid v3 JSON, or None for blank, legacy bullets, or any
        malformed / wrong-shape input (so callers fall back to legacy rendering).
    """
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    framing = data.get("framing")
    camps = data.get("camps")
    if not isinstance(framing, str) or not framing:
        return None
    if not isinstance(camps, list) or not camps:
        return None
    if not all(_is_camp(c) for c in camps):
        return None
    return {"framing": framing, "camps": camps}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_hn_comment_camps.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add hn_comment_camps.py tests/test_hn_comment_camps.py
git commit -m "feat: hn_comment_camps outline renderer + analysis JSON parser"
```

---

### Task 2: `render_camps_html` (shared camps → HTML)

**Files:**
- Modify: `hn_comment_camps.py`
- Test: `tests/test_hn_comment_camps.py`

**Interfaces:**
- Consumes: `parse_comment_analysis` output dict.
- Produces: `render_camps_html(analysis: dict) -> str` — inner HTML block (framing `<p>`, then per-camp `<div class="camp">` with label, description, `<blockquote>` quotes + `<cite>` author). All model strings HTML-escaped. Callers wrap it in their own container/heading.

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_hn_comment_camps.py
from hn_comment_camps import render_camps_html


def test_render_camps_html_escapes_and_structures():
    analysis = {
        "framing": "A <debate> ensued.",
        "camps": [
            {"label": "Skeptics", "description": "doubted it",
             "quotes": [{"text": "this is \"wrong\"", "author": "a&b"}]},
        ],
    }
    out = render_camps_html(analysis)
    assert "A &lt;debate&gt; ensued." in out
    assert "Skeptics" in out
    assert "this is &quot;wrong&quot;" in out
    assert "a&amp;b" in out
    assert "<blockquote" in out
    assert "<script" not in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_hn_comment_camps.py::test_render_camps_html_escapes_and_structures -v`
Expected: FAIL — `ImportError: cannot import name 'render_camps_html'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to hn_comment_camps.py
import html


def render_camps_html(analysis: dict) -> str:
    """Render a validated camps dict into an escaped inner HTML block.

    Returns:
        HTML for the framing paragraph followed by one block per camp. The
        caller supplies the surrounding container and heading.
    """
    parts = [f'<p class="camps-framing">{html.escape(analysis["framing"])}</p>']
    for camp in analysis["camps"]:
        quotes_html = "\n".join(
            f'<blockquote>{html.escape(q["text"])}'
            f'<cite>{html.escape(q["author"])}</cite></blockquote>'
            for q in camp["quotes"]
        )
        parts.append(
            '<div class="camp">'
            f'<p class="camp-label"><strong>{html.escape(camp["label"])}</strong> '
            f'&mdash; {html.escape(camp["description"])}</p>'
            f"{quotes_html}"
            "</div>"
        )
    return "\n".join(parts)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_hn_comment_camps.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add hn_comment_camps.py tests/test_hn_comment_camps.py
git commit -m "feat: render camps analysis to escaped HTML"
```

---

### Task 3: OpenAI two-stage `build_camps_analysis`

**Files:**
- Modify: `hn_comment_camps.py`, `pyproject.toml`
- Test: `tests/test_hn_comment_camps.py`

**Interfaces:**
- Produces: `build_camps_analysis(title: str, summary: str, outline: str, max_camps: int, max_quotes: int) -> str | None` — returns a JSON string (validated by `parse_comment_analysis`) or `None` on any failure / missing key. Uses `OPENAI_API_KEY` and `COMMENT_BRIEFING_MODEL` (default `gpt-5-mini`).
- Consumes (internally): the OpenAI Responses API.

- [ ] **Step 1: Add the `openai` dependency**

Edit `pyproject.toml` `dependencies` (after `markitdown[pdf]>=0.1.0`,):

```toml
    "openai>=1.54.0",
```

Run: `uv sync`
Expected: `openai` resolves and installs.

- [ ] **Step 2: Write the failing tests**

```python
# add to tests/test_hn_comment_camps.py
import types

import hn_comment_camps


def _fake_response(payload: dict):
    return types.SimpleNamespace(output_text=json.dumps(payload))


def test_build_camps_analysis_two_stage(monkeypatch):
    calls = []

    class FakeResponses:
        def create(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:  # stage 1: free-text spine
                return types.SimpleNamespace(output_text="FRAMING... CAMPS...")
            return _fake_response({  # stage 2: structured JSON
                "framing": "They split.",
                "camps": [{"label": "A", "description": "d",
                           "quotes": [{"text": "q", "author": "u"}]}],
            })

    class FakeClient:
        def __init__(self, *a, **k):
            self.responses = FakeResponses()

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(hn_comment_camps, "OpenAI", FakeClient)

    result = hn_comment_camps.build_camps_analysis(
        "Title", "Summary", "alice: hi\n    bob: hello", max_camps=6, max_quotes=3
    )
    assert result is not None
    assert parse_comment_analysis(result) is not None
    assert len(calls) == 2  # two stages


def test_build_camps_analysis_missing_key_returns_none(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert hn_comment_camps.build_camps_analysis("t", "s", "o", 6, 3) is None


def test_build_camps_analysis_openai_error_returns_none(monkeypatch):
    class Boom:
        def __init__(self, *a, **k):
            raise hn_comment_camps.OpenAIError("nope")

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(hn_comment_camps, "OpenAI", Boom)
    assert hn_comment_camps.build_camps_analysis("t", "s", "o", 6, 3) is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_hn_comment_camps.py -k build_camps -v`
Expected: FAIL — `AttributeError: module 'hn_comment_camps' has no attribute 'OpenAI'`

- [ ] **Step 4: Write minimal implementation**

```python
# add near top of hn_comment_camps.py
import logging
import os

from openai import OpenAI, OpenAIError

MODEL = os.environ.get("COMMENT_BRIEFING_MODEL", "gpt-5-mini")

_ANALYSIS_PROMPT = """You are analyzing the reader comment section of a Hacker News
story to prepare a "Highlights From The Comments" segment, in a fair, curatorial spirit.

You are given the story summary and the comments as an indented thread outline (each line is
"author: comment"; a reply is indented under the comment it answers).

Map how commenters reacted to the story. Output plain text with two parts:

FRAMING: 2-4 sentences on the overall shape of the reaction and the main axis/axes of disagreement.

CAMPS: a list covering EVERY substantive camp, including minority and meta-level positions. For each:
  - Name: a short label
  - Description: 1-2 sentences on the position and roughly how much support it had
  - Exemplars: the commenter names whose comments best exemplify this camp

Be honest; do not invent camps. Do NOT write quotes here.

STORY TITLE: {title}

STORY SUMMARY: {summary}

COMMENTS:
{outline}
"""

_WRITE_PROMPT = """You are turning a Hacker News comment-section analysis into structured data.

You are given (a) an ANALYSIS of the comment section (framing plus camps with exemplar
commenters) and (b) the comments as an indented thread outline.

Produce at most {max_camps} camps, each with at most {max_quotes} quotes. Every quote's "text"
must be VERBATIM from the comments below and its "author" the real commenter. Never invent or
paraphrase. Prefer the pithiest representative sentence from an exemplar. Neutral, third-person
framing.

ANALYSIS:
{analysis}

COMMENTS:
{outline}
"""

_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["framing", "camps"],
    "properties": {
        "framing": {"type": "string"},
        "camps": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["label", "description", "quotes"],
                "properties": {
                    "label": {"type": "string"},
                    "description": {"type": "string"},
                    "quotes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["text", "author"],
                            "properties": {
                                "text": {"type": "string"},
                                "author": {"type": "string"},
                            },
                        },
                    },
                },
            },
        },
    },
}


def _client() -> "OpenAI | None":
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        logging.error("OPENAI_API_KEY not set; cannot generate HN comment camps analysis")
        return None
    return OpenAI(api_key=key, max_retries=6)


def build_camps_analysis(
    title: str, summary: str, outline: str, max_camps: int, max_quotes: int
) -> str | None:
    """Run the two-stage OpenAI pipeline; return validated JSON string or None.

    Stage 1 detects the camps (free text spine); stage 2 emits structured JSON with
    verbatim quotes, constrained by a strict JSON schema. Explicit prompt-cache mode
    with no breakpoints avoids cache-write charges on these unique prompts.

    Returns:
        A JSON string that passes parse_comment_analysis, or None on any failure.
    """
    try:
        client = _client()
        if client is None:
            return None
        analysis = client.responses.create(
            model=MODEL,
            input=_ANALYSIS_PROMPT.format(title=title, summary=summary, outline=outline),
            timeout=300,
            prompt_cache_options={"mode": "explicit"},
        ).output_text.strip()
        if not analysis:
            return None
        result = client.responses.create(
            model=MODEL,
            input=_WRITE_PROMPT.format(
                analysis=analysis, outline=outline, max_camps=max_camps, max_quotes=max_quotes
            ),
            timeout=300,
            prompt_cache_options={"mode": "explicit"},
            text={
                "format": {
                    "type": "json_schema",
                    "name": "camps_analysis",
                    "strict": True,
                    "schema": _SCHEMA,
                }
            },
        ).output_text.strip()
    except OpenAIError:
        logging.exception("HN comment camps analysis request failed")
        return None
    return result if parse_comment_analysis(result) is not None else None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_hn_comment_camps.py -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add hn_comment_camps.py pyproject.toml uv.lock tests/test_hn_comment_camps.py
git commit -m "feat: two-stage OpenAI camps analysis with structured JSON output"
```

---

### Task 4: `build_hn_comment_outline` (contiguous, budget-capped)

**Files:**
- Modify: `trending_digest.py` (add config constants near line 105; add function near `build_hn_comment_nodes` ~2957)
- Test: `tests/test_hn_comment_outline.py`

**Interfaces:**
- Consumes: existing `fetch_hn_item_cached`, `clean_hn_comment_text`, `normalize_text`, and env helper `get_int_env`.
- Produces: `build_hn_comment_outline(item_id: int, total_hint: int) -> tuple[int, list[dict]]` — returns `(total_comments, comments)` where each comment dict has `depth` (1-based), `by`, `text`, in render order (top threads by position; within a thread, parent before children), truncated so the rendered outline stays under `HN_COMMENT_ANALYSIS_MAX_CHARS`.

- [ ] **Step 1: Add config constants**

In `trending_digest.py` after line 109 (`HN_COMMENT_MIN_TEXT_LEN = ...`):

```python
HN_COMMENT_ANALYSIS_MAX_CHARS = get_int_env("HN_COMMENT_ANALYSIS_MAX_CHARS", 48000)
HN_COMMENT_ANALYSIS_MAX_CAMPS = get_int_env("HN_COMMENT_ANALYSIS_MAX_CAMPS", 6)
HN_COMMENT_ANALYSIS_MAX_QUOTES = get_int_env("HN_COMMENT_ANALYSIS_MAX_QUOTES", 3)
```

And change the prompt version at line 57:

```python
HN_COMMENT_ANALYSIS_PROMPT_VERSION = "hn_comments_v3"
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_hn_comment_outline.py
import trending_digest
from hn_comment_camps import render_outline


def _story(kids):
    return {"type": "story", "descendants": 99, "kids": kids}


def _comment(cid, kids=None, text=None, by="user"):
    return {
        "id": cid, "type": "comment", "by": by,
        "text": text if text is not None else f"comment {cid} with enough length to pass the filter",
        "kids": kids or [],
    }


def test_outline_is_thread_contiguous_and_depth_ordered(monkeypatch):
    items = {
        1: _story([10, 20]),
        10: _comment(10, kids=[11]),
        11: _comment(11),
        20: _comment(20),
    }
    monkeypatch.setattr(
        trending_digest, "fetch_hn_item_cached",
        lambda item_id, cache, session: items.get(item_id),
    )
    total, comments = trending_digest.build_hn_comment_outline(1, 99)
    assert total == 99
    ids_in_order = [(c["depth"], c["text"][:9]) for c in comments]
    # branch 10 (with its reply 11) fully precedes branch 20
    assert ids_in_order == [(1, "comment 1"), (2, "comment 1"), (1, "comment 2")]
    # renders as an indent tree
    assert render_outline(comments).count("\n    ") == 1


def test_outline_respects_char_budget(monkeypatch):
    big = "x" * 5000
    items = {1: _story([10, 20, 30])}
    for cid in (10, 20, 30):
        items[cid] = _comment(cid, text=big)
    monkeypatch.setattr(
        trending_digest, "fetch_hn_item_cached",
        lambda item_id, cache, session: items.get(item_id),
    )
    monkeypatch.setattr(trending_digest, "HN_COMMENT_ANALYSIS_MAX_CHARS", 8000)
    _total, comments = trending_digest.build_hn_comment_outline(1, 99)
    # 5000-char comments: two fit under 8000, the third whole branch is dropped
    assert len(comments) == 2


def test_outline_skips_short_dead_and_deep(monkeypatch):
    items = {
        1: _story([10]),
        10: _comment(10, kids=[11]),
        11: {"id": 11, "type": "comment", "by": "x", "text": "short", "kids": []},
    }
    monkeypatch.setattr(
        trending_digest, "fetch_hn_item_cached",
        lambda item_id, cache, session: items.get(item_id),
    )
    _total, comments = trending_digest.build_hn_comment_outline(1, 99)
    assert [c["depth"] for c in comments] == [1]  # short reply 11 filtered out
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_hn_comment_outline.py -v`
Expected: FAIL — `AttributeError: module 'trending_digest' has no attribute 'build_hn_comment_outline'`

- [ ] **Step 4: Write minimal implementation**

Add to `trending_digest.py` after `build_hn_comment_nodes` (~line 3022):

```python
def build_hn_comment_outline(item_id: int, total_hint: int) -> tuple[int, list[dict]]:
    """Traverse HN threads depth-first, keeping whole branches in reading order.

    Branches (top-level replies) are visited in position order and each is
    explored parent-before-children so the result renders as an indented outline.
    Accumulation stops once the rendered outline would exceed
    HN_COMMENT_ANALYSIS_MAX_CHARS, dropping the lowest-priority whole branches.

    Returns:
        (total_comments, comments) where each comment dict has depth (1-based),
        by, and text.
    """
    session = requests.Session()
    item_cache: dict[int, dict | None] = {}

    story = fetch_hn_item_cached(item_id, item_cache, session)
    if not story:
        return total_hint, []

    total_comments = int(story.get("descendants") or total_hint or 0)
    top_kids = [int(kid) for kid in (story.get("kids") or [])]

    comments: list[dict] = []
    char_total = 0
    for kid in top_kids:
        stack = [(kid, 1)]
        while stack:
            comment_id, depth = stack.pop()
            if depth > HN_COMMENT_TRAVERSAL_MAX_DEPTH:
                continue
            if len(comments) >= HN_COMMENT_TRAVERSAL_MAX_NODES:
                return total_comments, comments

            comment = fetch_hn_item_cached(comment_id, item_cache, session)
            if not comment or comment.get("type") != "comment":
                continue
            if comment.get("dead") or comment.get("deleted"):
                continue

            kids = [int(k) for k in (comment.get("kids") or [])]
            # DFS: push children reversed so leftmost is processed first (parent already emitted).
            for child in reversed(kids):
                stack.append((child, depth + 1))

            text = clean_hn_comment_text(comment.get("text") or "")
            if len(text) < HN_COMMENT_MIN_TEXT_LEN:
                continue

            by = normalize_text(comment.get("by") or "unknown")
            line_cost = len(text) + len(by) + 4 * depth + 3
            if char_total + line_cost > HN_COMMENT_ANALYSIS_MAX_CHARS and comments:
                return total_comments, comments
            char_total += line_cost
            comments.append({"depth": depth, "by": by, "text": text})

    return total_comments, comments
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_hn_comment_outline.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add trending_digest.py tests/test_hn_comment_outline.py
git commit -m "feat: contiguous char-budgeted HN comment outline builder"
```

---

### Task 5: Rewire `get_or_generate_hn_comment_analysis` + cache key to OpenAI camps

**Files:**
- Modify: `trending_digest.py` (`get_latest_hn_comment_analysis` ~1391, `cache_hn_comment_analysis` ~1409, `get_or_generate_hn_comment_analysis` ~1478)
- Test: `tests/test_hn_comment_analysis_wiring.py`

**Interfaces:**
- Consumes: `build_hn_comment_outline`, `hn_comment_camps.render_outline`, `hn_comment_camps.build_camps_analysis`, and the config constants from Task 4.
- Produces: unchanged return shape of `get_or_generate_hn_comment_analysis` — `{"analysis_text": str, "sampled_comments": int, "total_comments": int} | None`. `analysis_text` is now a JSON string.

**Note:** cache lookup/insert currently key `model = SUMMARY_MODEL` and `sample_size = HN_COMMENT_SAMPLE_SIZE`. Switch the model dimension to `COMMENT_MODEL` (the OpenAI model) so v3 rows never collide with v2 Gemini rows, and record `sample_size = len(comments)` (the outline size actually used).

- [ ] **Step 1: Add module imports and model constant**

At the top of `trending_digest.py` imports, add:

```python
import hn_comment_camps
```

Near line 57, add:

```python
COMMENT_MODEL = os.environ.get("COMMENT_BRIEFING_MODEL", "gpt-5-mini")
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_hn_comment_analysis_wiring.py
import json
from datetime import date

import trending_digest


def test_get_or_generate_uses_openai_camps(monkeypatch):
    outline_comments = [{"depth": 1, "by": "alice", "text": "hello world this is long enough"}]
    monkeypatch.setattr(
        trending_digest, "build_hn_comment_outline",
        lambda item_id, hint: (42, outline_comments),
    )
    captured = {}

    def fake_build(title, summary, outline, max_camps, max_quotes):
        captured["outline"] = outline
        captured["caps"] = (max_camps, max_quotes)
        return json.dumps({
            "framing": "split", "camps": [
                {"label": "A", "description": "d", "quotes": [{"text": "q", "author": "u"}]}
            ],
        })

    monkeypatch.setattr(trending_digest.hn_comment_camps, "build_camps_analysis", fake_build)

    saved = {}
    monkeypatch.setattr(
        trending_digest, "get_latest_hn_comment_analysis", lambda conn, item_id: None
    )
    monkeypatch.setattr(
        trending_digest, "cache_hn_comment_analysis",
        lambda **kw: saved.update(kw),
    )

    item = {"item_id": 1, "title": "T", "summary": "S", "comment_count": 42}
    result = trending_digest.get_or_generate_hn_comment_analysis(None, item, date(2026, 8, 17))

    assert result is not None
    assert json.loads(result["analysis_text"])["framing"] == "split"
    assert result["total_comments"] == 42
    # outline was passed indented, caps threaded through
    assert "alice: hello world" in captured["outline"]
    assert captured["caps"] == (
        trending_digest.HN_COMMENT_ANALYSIS_MAX_CAMPS,
        trending_digest.HN_COMMENT_ANALYSIS_MAX_QUOTES,
    )


def test_get_or_generate_returns_none_when_model_fails(monkeypatch):
    monkeypatch.setattr(
        trending_digest, "build_hn_comment_outline",
        lambda item_id, hint: (5, [{"depth": 1, "by": "a", "text": "x" * 60}]),
    )
    monkeypatch.setattr(
        trending_digest.hn_comment_camps, "build_camps_analysis",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        trending_digest, "get_latest_hn_comment_analysis", lambda conn, item_id: None
    )
    item = {"item_id": 1, "title": "T", "summary": "S", "comment_count": 5}
    assert trending_digest.get_or_generate_hn_comment_analysis(None, item, date(2026, 8, 17)) is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_hn_comment_analysis_wiring.py -v`
Expected: FAIL — camps not called (still calls Gemini `generate_hn_comment_analysis` / `build_hn_comment_nodes`).

- [ ] **Step 4: Rewrite the three functions**

Replace the SQL model/sample_size params to key on `COMMENT_MODEL`. In `get_latest_hn_comment_analysis` change the params tuple (line ~1404) to:

```python
            (item_id, COMMENT_MODEL, HN_COMMENT_SAMPLE_SIZE),
```

In `cache_hn_comment_analysis` add a `sample_size` parameter and use `COMMENT_MODEL`:

```python
def cache_hn_comment_analysis(
    conn: psycopg.Connection,
    item_id: int,
    analysis_text: str,
    sampled_comments: int,
    total_comments: int,
    sample_size: int,
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
                COMMENT_MODEL,
                HN_COMMENT_ANALYSIS_PROMPT_VERSION,
                sample_size,
                sampled_comments,
                total_comments,
                analysis_text,
            ),
        )
```

Keep `get_latest_hn_comment_analysis` keyed on `HN_COMMENT_SAMPLE_SIZE` for the freshness lookup (stable dimension), so the lookup stays cheap while stored `sample_size` records the actual outline size. Replace the body of `get_or_generate_hn_comment_analysis` (from line ~1489 onward) with:

```python
    total_comments, comments = build_hn_comment_outline(item_id, int(item.get("comment_count") or 0))
    if not comments:
        return None

    outline = hn_comment_camps.render_outline(comments)
    analysis_text = hn_comment_camps.build_camps_analysis(
        title=str(item.get("title", "")),
        summary=str(item.get("summary", "")),
        outline=outline,
        max_camps=HN_COMMENT_ANALYSIS_MAX_CAMPS,
        max_quotes=HN_COMMENT_ANALYSIS_MAX_QUOTES,
    )
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
        sampled_comments=len(comments),
        total_comments=total_comments,
        sample_size=len(comments),
    )
    return {
        "analysis_text": analysis_text,
        "sampled_comments": len(comments),
        "total_comments": total_comments,
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_hn_comment_analysis_wiring.py tests/test_hn_comment_outline.py -v`
Expected: PASS

- [ ] **Step 6: Remove the now-dead Gemini path**

Delete `generate_hn_comment_analysis` (~1436-1475) and `select_hn_comment_sample` (~3025-3060) and `build_hn_comment_nodes` (~2957-3022) **only if** no other references remain:

Run: `grep -n "generate_hn_comment_analysis\|select_hn_comment_sample\|build_hn_comment_nodes" trending_digest.py`
Expected: no references outside the definitions → delete them. If any remain, leave the referenced function and note it.

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add trending_digest.py tests/test_hn_comment_analysis_wiring.py
git commit -m "feat: route HN comment analysis through OpenAI camps pipeline"
```

---

### Task 6: Classic digest card renderer

**Files:**
- Modify: `trending_digest.py` (`generate_hn_digest` card block ~2237-2244)
- Test: `tests/test_hn_card_render.py`

**Interfaces:**
- Consumes: `hn_comment_camps.parse_comment_analysis`, `hn_comment_camps.render_camps_html`, existing `generate_bullet_paragraph_html` (legacy fallback).
- Produces: a `render_comment_analysis_html(raw: str) -> str` helper used by the card.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_hn_card_render.py
import json

import trending_digest


def test_card_renders_camps_for_v3_json():
    raw = json.dumps({
        "framing": "They disagreed.",
        "camps": [{"label": "Pro", "description": "liked", "quotes": [{"text": "yes", "author": "pg"}]}],
    })
    out = trending_digest.render_comment_analysis_html(raw)
    assert "They disagreed." in out
    assert "Pro" in out
    assert "<blockquote" in out


def test_card_falls_back_to_bullets_for_legacy():
    out = trending_digest.render_comment_analysis_html("- one\n- two\n- three")
    assert "<blockquote" not in out
    assert "one" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_hn_card_render.py -v`
Expected: FAIL — `render_comment_analysis_html` not defined.

- [ ] **Step 3: Add the helper and rewire the card**

Add near `generate_bullet_paragraph_html` (~1750):

```python
def render_comment_analysis_html(raw: str) -> str:
    """Render stored HN comment analysis: camps HTML for v3 JSON, else legacy bullets.

    Returns:
        Inner HTML for the Comment Analysis card body.
    """
    parsed = hn_comment_camps.parse_comment_analysis(raw)
    if parsed is not None:
        return hn_comment_camps.render_camps_html(parsed)
    return generate_bullet_paragraph_html(raw)
```

Change the card block (~2238-2244) from `generate_bullet_paragraph_html(item["comment_analysis"])` to:

```python
        comment_analysis_html = ""
        if item.get("comment_analysis"):
            comment_analysis_html = f"""
                <div class="ai-summary">
                    <h4>Comment Analysis</h4>
                    {render_comment_analysis_html(item["comment_analysis"])}
                </div>
"""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_hn_card_render.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add trending_digest.py tests/test_hn_card_render.py
git commit -m "feat: render camps analysis in classic HN digest card"
```

---

### Task 7: Morning-edition renderer (both call sites, HN-only)

**Files:**
- Modify: `morning_edition.py` (`_render_analysis_drawer` ~377-399; archetype loop ~485-499)
- Test: `tests/test_morning_edition_camps.py`

**Interfaces:**
- Consumes: `hn_comment_camps.parse_comment_analysis`, `hn_comment_camps.render_camps_html`, existing `parse_bullets` (legacy/GH fallback), and `EditionConfig.id`.
- Produces: a `_render_reactions_html(config, item) -> str` helper shared by both call sites.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_morning_edition_camps.py
import json
import types

import morning_edition


def _cfg(edition_id):
    return types.SimpleNamespace(id=edition_id)


def test_reactions_renders_camps_for_hn_v3():
    raw = json.dumps({
        "framing": "Split opinions.",
        "camps": [{"label": "Boosters", "description": "keen", "quotes": [{"text": "cool", "author": "u"}]}],
    })
    out = morning_edition._render_reactions_html(_cfg("hn"), {"comment_analysis": raw})
    assert "Split opinions." in out
    assert "Boosters" in out
    assert "<blockquote" in out


def test_reactions_falls_back_to_bullets_for_legacy_hn():
    out = morning_edition._render_reactions_html(_cfg("hn"), {"comment_analysis": "- a\n- b"})
    assert "Reader Reactions" in out
    assert "<blockquote" not in out


def test_reactions_empty_for_gh_and_blank():
    assert morning_edition._render_reactions_html(_cfg("gh"), {"comment_analysis": ""}) == ""
    assert morning_edition._render_reactions_html(_cfg("hn"), {"comment_analysis": ""}) == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_morning_edition_camps.py -v`
Expected: FAIL — `_render_reactions_html` not defined.

- [ ] **Step 3: Add the shared helper and use it in both sites**

Add near `_render_analysis_drawer` in `morning_edition.py`:

```python
import hn_comment_camps


def _render_reactions_html(config, item: dict) -> str:
    """Render the reactions block: camps HTML for HN v3 JSON, else legacy bullets.

    Returns:
        A heading + body block, or "" when there is nothing to show or for GH.
    """
    raw = (item.get("comment_analysis") or "").strip()
    if not raw:
        return ""
    parsed = hn_comment_camps.parse_comment_analysis(raw)
    if config.id == "hn" and parsed is not None:
        return f"<h4>Reader Reactions</h4>\n{hn_comment_camps.render_camps_html(parsed)}"
    bullets = parse_bullets(raw)
    if not bullets:
        return ""
    reactions_label = "Reader Reactions" if config.id == "hn" else "Insights"
    body = "\n".join(f"<p>{_h(b)}</p>" for b in bullets)
    return f"<h4>{reactions_label}</h4>\n{body}"
```

In `_render_analysis_drawer` (~385-399) replace the `bullets`/`bullets_html` computation with:

```python
    bullets_html = _render_reactions_html(config, item)
```

and use `bullets_html` where it was already used. In the archetype loop (~485-499) replace the `bullets`/`reactions_html` computation with:

```python
        reactions_html = _render_reactions_html(config, item)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_morning_edition_camps.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run full suite**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add morning_edition.py tests/test_morning_edition_camps.py
git commit -m "feat: render camps analysis in both morning-edition layouts"
```

---

### Task 8: Env + docs + final verification

**Files:**
- Modify: `.env.example` (if present), `README.md`, `CLAUDE.md`

**Interfaces:** none (documentation + config surface).

- [ ] **Step 1: Document the new env vars**

Add to `.env.example` (create the lines if the file exists; otherwise add to README env section):

```bash
OPENAI_API_KEY=your-openai-key
COMMENT_BRIEFING_MODEL=gpt-5-mini
HN_COMMENT_ANALYSIS_MAX_CHARS=48000
HN_COMMENT_ANALYSIS_MAX_CAMPS=6
HN_COMMENT_ANALYSIS_MAX_QUOTES=3
```

Run: `grep -rn "OPENAI_API_KEY\|COMMENT_BRIEFING_MODEL" README.md CLAUDE.md .env.example 2>/dev/null`
Expected: entries present after editing.

- [ ] **Step 2: Note the model in CLAUDE.md**

Add one line under the HN/config docs in `CLAUDE.md`:

```markdown
- HN comment analysis uses the OpenAI Responses API (`COMMENT_BRIEFING_MODEL`, prod `gpt-5.6-luna`) via `hn_comment_camps.py`, emitting structured camps JSON; `OPENAI_API_KEY` required.
```

- [ ] **Step 3: Lint and full test**

Run: `uv run ruff check hn_comment_camps.py trending_digest.py morning_edition.py && uv run pytest -q`
Expected: no lint errors; all tests PASS.

- [ ] **Step 4: Smoke-render a page from existing DB content (optional, if DB available)**

Run: `uv run python trending_digest.py --regenerate-only`
Expected: completes; HN pages render. Legacy v2 rows show bullets; any freshly generated v3 rows show camps. (Skip if no DB configured locally.)

- [ ] **Step 5: Commit**

```bash
git add .env.example README.md CLAUDE.md
git commit -m "docs: document OpenAI comment-analysis env vars"
```

---

## Self-Review

**Spec coverage:**
- Indented, contiguous, budget-capped gathering → Task 4. ✓
- Two-stage OpenAI structured JSON (schema, caps, explicit cache) → Task 3. ✓
- Storage as JSON string in TEXT, v3 prompt version, model-keyed cache → Task 4 (version) + Task 5 (cache key/params). ✓
- Both editions render full camps, HN-only, legacy fallback → Tasks 6 (classic) + 7 (morning). ✓
- GH untouched → Task 7 branches on `config.id`; GH still `""`. ✓
- Env plumbing + never-crash fallback → Task 3 (missing key → None), Task 5 (keep latest), Task 8 (docs). ✓
- Removal of dead sampler/flat-block Gemini path → Task 5 Step 6. ✓

**Placeholder scan:** no TBD/TODO; every code step has real code; test bodies are concrete.

**Type consistency:** `build_hn_comment_outline -> (int, list[dict])` with `depth/by/text`; `render_outline` consumes those keys (Task 1) and is fed them in Task 5; `build_camps_analysis(title, summary, outline, max_camps, max_quotes) -> str | None` matches its call in Task 5; `parse_comment_analysis`/`render_camps_html` signatures match their uses in Tasks 6-7. `cache_hn_comment_analysis` gains `sample_size` — its only caller (Task 5) passes it. ✓
