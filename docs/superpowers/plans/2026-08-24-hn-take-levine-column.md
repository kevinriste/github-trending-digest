# The HN Take — Levine-Style Daily Column Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate one daily "The HN Take" — a unified, de-attributed Levine-style column synthesizing the day's substantial Hacker News stories woven with their comment-camp reactions — and render it atop the HN daily page, gated, cached, and fail-open.

**Architecture:** A new self-contained module `hn_take.py` (mirrors `hn_comment_camps.py`: no import of `trending_digest`, swallows OpenAI errors into a module-level `API_ERRORS` list) owns the eligibility gate, context assembly, the ported prompt, the single OpenAI Responses call, and the DB cache read/write. `trending_digest.py` owns the schema (`init_db`), the render (`generate_hn_daily_page` gains an optional `take_md` arg), and one gated call in the live daily pipeline. Every path is additive: no key / disabled / no qualifying stories / generation failure all fall back to today's exact page.

**Tech Stack:** Python 3, `openai` (Responses API), `psycopg` (Postgres), `pytest`.

**Spec:** `specs/2026-08-23-hn-levine-take-design.md`

## Global Constraints

- **Fail-open everywhere.** Any OpenAI error, missing key, empty output, or zero qualifying stories → return `None` → page renders unchanged. Never crash the daily run.
- **Model:** default `HN_TAKE_MODEL = "gpt-5.6-sol"` (intentional — matches ai-newsletter's proven setup; deliberately *not* the camps model). Env-overridable.
- **Eligibility gate:** a story qualifies iff a full article `>= HN_TAKE_MIN_CHARS` (default 1500) **or** a self/text post (`item_type` in `story`/`ask`/`show`) with `text >= HN_TAKE_MIN_CHARS`. Comments are a reaction layer only — never a qualification basis.
- **Cache key:** `(run_date, model, prompt_version)`; `HN_TAKE_PROMPT_VERSION = "hn_take_v1"`.
- **De-attribution:** the ported prompt names no living author (no "Levine", "Money Stuff", "Matt") — this is what eliminated the OpenAI refusal-preamble in ai-newsletter.
- **Regeneration never calls the LLM:** `regenerate_hn_daily_pages` passes `take_md=None`. The column is produced only on the live daily path.
- **All new env-tunable constants read `os.environ` at import**, mirroring `hn_comment_camps.MODEL`.
- Run tests with `pytest` (config in `pyproject.toml`, `testpaths = ["tests"]`).

---

### Task 1: `hn_take.py` — eligibility gate + context assembly (pure, no network)

Pure logic + the ported prompt constant. No OpenAI call, no DB. This is the testable core.

**Files:**
- Create: `hn_take.py`
- Test: `tests/test_hn_take.py`

**Interfaces:**
- Consumes: enriched HN rows from `trending_digest.build_hn_view_rows` — each row is a dict with `rank`, `title`, `url`, `score`, `comment_count`, `item_type`, `text`, `article_content`, `summary`, `comment_analysis` (a JSON string parseable by `hn_comment_camps.parse_comment_analysis`).
- Produces:
  - `HN_TAKE_MIN_CHARS: int`, `HN_TAKE_BODY_CAP: int`, `HN_TAKE_MAX_STORIES: int`, `HN_TAKE_QUOTES_PER_CAMP: int` (module constants).
  - `HN_TAKE_SYSTEM: str` (the ported prompt).
  - `HN_TAKE_PROMPT_VERSION: str = "hn_take_v1"`.
  - `qualifying_body(row: dict) -> str | None` — the primary body if the row qualifies, else `None`.
  - `build_take_context(rows: list[dict]) -> str` — assembled prompt input, or `""` when no story qualifies.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_hn_take.py
import json

import hn_take
from hn_take import qualifying_body, build_take_context, HN_TAKE_MIN_CHARS


def _row(**kw):
    base = {
        "rank": 1, "title": "T", "url": "http://x", "score": 100,
        "comment_count": 5, "item_type": "story", "text": "",
        "article_content": "", "summary": "s", "comment_analysis": "",
    }
    base.update(kw)
    return base


def test_full_article_qualifies():
    assert qualifying_body(_row(article_content="a" * HN_TAKE_MIN_CHARS)) is not None


def test_self_post_qualifies():
    body = qualifying_body(_row(item_type="ask", text="b" * HN_TAKE_MIN_CHARS))
    assert body is not None and body.startswith("b")


def test_thin_article_and_no_text_excluded():
    assert qualifying_body(_row(article_content="short", text="")) is None


def test_comments_only_excluded():
    camps = json.dumps({"framing": "f", "camps": [
        {"label": "L", "description": "d", "quotes": [{"text": "q", "author": "u"}]}]})
    assert qualifying_body(_row(article_content="", text="", comment_analysis=camps)) is None


def test_boundary_exactly_min_qualifies():
    assert qualifying_body(_row(article_content="a" * HN_TAKE_MIN_CHARS)) is not None


def test_below_boundary_excluded():
    assert qualifying_body(_row(article_content="a" * (HN_TAKE_MIN_CHARS - 1))) is None


def test_link_post_text_does_not_qualify_on_type():
    # a non-self item_type with long text should NOT qualify via the self-post branch
    assert qualifying_body(_row(item_type="job", text="b" * HN_TAKE_MIN_CHARS, article_content="")) is None


def test_context_orders_by_descending_score():
    rows = [
        _row(rank=1, title="low", score=10, article_content="a" * HN_TAKE_MIN_CHARS),
        _row(rank=2, title="high", score=999, article_content="a" * HN_TAKE_MIN_CHARS),
    ]
    ctx = build_take_context(rows)
    assert ctx.index("high") < ctx.index("low")


def test_context_caps_body():
    rows = [_row(article_content="a" * (hn_take.HN_TAKE_BODY_CAP + 5000))]
    ctx = build_take_context(rows)
    assert "a" * hn_take.HN_TAKE_BODY_CAP in ctx
    assert "a" * (hn_take.HN_TAKE_BODY_CAP + 1) not in ctx


def test_context_includes_discussion_when_camps_present():
    camps = json.dumps({"framing": "people split", "camps": [
        {"label": "Pro", "description": "liked", "quotes": [{"text": "great", "author": "pg"}]}]})
    rows = [_row(article_content="a" * HN_TAKE_MIN_CHARS, comment_analysis=camps)]
    ctx = build_take_context(rows)
    assert "--- HN DISCUSSION ---" in ctx
    assert "people split" in ctx and "Pro" in ctx and "great" in ctx


def test_context_omits_discussion_when_no_camps():
    rows = [_row(article_content="a" * HN_TAKE_MIN_CHARS, comment_analysis="")]
    ctx = build_take_context(rows)
    assert "--- STORY ---" in ctx
    assert "--- HN DISCUSSION ---" not in ctx


def test_context_respects_max_stories(monkeypatch):
    monkeypatch.setattr(hn_take, "HN_TAKE_MAX_STORIES", 2)
    rows = [_row(rank=i, title=f"S{i}", score=100 - i,
                 article_content="a" * HN_TAKE_MIN_CHARS) for i in range(5)]
    ctx = build_take_context(rows)
    assert ctx.count("--- STORY ---") == 2


def test_context_empty_when_nothing_qualifies():
    assert build_take_context([_row(article_content="short", text="")]) == ""


def test_prompt_has_no_preamble_and_header_convention():
    assert "output ONLY" in hn_take.HN_TAKE_SYSTEM
    assert "# " in hn_take.HN_TAKE_SYSTEM


def test_prompt_is_de_attributed():
    for name in ("Levine", "Money Stuff", "Matt "):
        assert name not in hn_take.HN_TAKE_SYSTEM


def test_prompt_mentions_hn_discussion():
    assert "HN DISCUSSION" in hn_take.HN_TAKE_SYSTEM
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_hn_take.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hn_take'`.

- [ ] **Step 3: Port the prompt constant**

The source prompt is the triple-quoted string `OVERALL_SYNTHESIS_SYSTEM` in `../ai-newsletter/synth/prompts.py`, spanning lines **17–616** (the assignment line through the closing `"""`). Copy the **entire string body verbatim** (all five example columns) into `hn_take.py` as `HN_TAKE_SYSTEM = """..."""`. It is ~108k chars / ~27k tokens — copy it whole; do not summarize or truncate.

Then make exactly one adaptation: the **final instruction paragraph** (the last line of the string, beginning `Write the opening for today's edition...`). Replace that paragraph with the HN-adapted version below (changes: describes the new per-item shape including the discussion block, and instructs weaving reaction with substance):

Original final paragraph (to replace):
```
Write the opening for today's edition from the items you are given (each is a headline and the full article text). The items are given in the editor's consequence order, most important first. Choose your one or two sections from the top items (roughly the top five), strongly preferring the most important; pass over a top item only if there is genuinely too little to say about it. Do NOT pick a lower-ranked item just because it happens to have a longer article. Give each chosen item its own short titled section. Because you have the full article text: ground everything in it and invent nothing (no numbers, names or quotes not present); you MAY quote short verbatim phrases from the articles, the way the examples quote their sources, then react. Keep it tight, as the examples do: the news lands as a short beat and then you react, you do not recount the article at length. Rules: start each section with its short title alone on one line, prefixed with a single "# " (for example: # Emergent cyber capability), and use no other markdown anywhere; no em or en dashes; straight ASCII quotes and apostrophes only; no bullet lists; no references to prior editions; no math notation. Output prose only.
```

Adapted final paragraph:
```
Write the opening for today's edition from the items you are given. Each item is a headline followed by its full primary text (a fetched article or a Hacker News self-post) under "--- STORY ---", and, where available, an "--- HN DISCUSSION ---" block summarizing how the Hacker News comment section reacted (a framing line and the main camps, some with short verbatim quotes). The items are given in the editor's consequence order, most important first. Choose your one or two sections from the top items (roughly the top five), strongly preferring the most important; pass over a top item only if there is genuinely too little to say about it. Do NOT pick a lower-ranked item just because it happens to have more text. Give each chosen item its own short titled section. Because you have the full primary text: ground everything in it and invent nothing (no numbers, names or quotes not present); you MAY quote short verbatim phrases from the story text, the way the examples quote their sources, then react. Where the community's reaction sharpens, complicates, or deflates the point, weave it in — quote or paraphrase a camp — but do not merely summarize the thread; the story is your spine and the discussion is a reaction layer. Keep it tight, as the examples do: the news lands as a short beat and then you react, you do not recount the article at length. Rules: start each section with its short title alone on one line, prefixed with a single "# " (for example: # Emergent cyber capability), and use no other markdown anywhere; no em or en dashes; straight ASCII quotes and apostrophes only; no bullet lists; no references to prior editions; no math notation. Output prose only.
```

- [ ] **Step 4: Write the module core (constants + gate + assembly)**

Add to `hn_take.py` (above or below the prompt constant):

```python
"""Levine-style daily synthesis column over the day's substantial HN stories.

Ported (copied + adapted) from ai-newsletter's OVERALL_SYNTHESIS_SYSTEM and its
1500-char "substantial primary content" gate. Pure of presentation and of any
import of trending_digest; mirrors hn_comment_camps.py's shape and error-swallowing.
"""

import logging
import os

from openai import OpenAI, OpenAIError

import hn_comment_camps

# OpenAI request failures are swallowed so a bad key never crashes the daily run,
# and recorded here so the caller can surface a single run-level notification.
API_ERRORS: list[str] = []

HN_TAKE_MODEL = os.environ.get("HN_TAKE_MODEL", "gpt-5.6-sol")
HN_TAKE_REASONING = os.environ.get("HN_TAKE_REASONING", "medium")
HN_TAKE_PROMPT_VERSION = "hn_take_v1"

HN_TAKE_MIN_CHARS = int(os.environ.get("HN_TAKE_MIN_CHARS", "1500"))
HN_TAKE_BODY_CAP = int(os.environ.get("HN_TAKE_BODY_CAP", "12000"))
HN_TAKE_MAX_STORIES = int(os.environ.get("HN_TAKE_MAX_STORIES", "10"))
HN_TAKE_QUOTES_PER_CAMP = int(os.environ.get("HN_TAKE_QUOTES_PER_CAMP", "2"))

_SELF_POST_TYPES = {"story", "ask", "show"}


def qualifying_body(row: dict) -> str | None:
    """Return the row's substantial primary body, or None if it doesn't qualify.

    A full fetched article (>= HN_TAKE_MIN_CHARS) qualifies; otherwise a self/text
    post (item_type in story/ask/show) whose text >= HN_TAKE_MIN_CHARS qualifies.
    Comments never qualify a story on their own.
    """
    article = (row.get("article_content") or "").strip()
    if len(article) >= HN_TAKE_MIN_CHARS:
        return article
    text = (row.get("text") or "").strip()
    item_type = (row.get("item_type") or "").strip().lower()
    if item_type in _SELF_POST_TYPES and len(text) >= HN_TAKE_MIN_CHARS:
        return text
    return None


def _discussion_block(row: dict) -> str:
    """Return the '--- HN DISCUSSION ---' block for a row, or '' if no usable camps."""
    parsed = hn_comment_camps.parse_comment_analysis(row.get("comment_analysis"))
    if parsed is None:
        return ""
    lines = ["--- HN DISCUSSION ---", f"Framing: {parsed['framing']}"]
    for camp in parsed["camps"]:
        quotes = camp["quotes"][:HN_TAKE_QUOTES_PER_CAMP]
        eg = ""
        if quotes:
            joined = "; ".join(f'"{q["text"]}"' for q in quotes)
            eg = f"  [e.g. {joined}]"
        lines.append(f"- {camp['label']}: {camp['description']}{eg}")
    return "\n".join(lines)


def build_take_context(rows: list[dict]) -> str:
    """Assemble the prompt input from qualifying rows, biggest HN score first.

    Returns '' when no story qualifies (the caller then produces no column).
    """
    eligible = []
    for row in rows:
        body = qualifying_body(row)
        if body is not None:
            eligible.append((row, body))
    eligible.sort(key=lambda rb: int(rb[0].get("score") or 0), reverse=True)
    eligible = eligible[:HN_TAKE_MAX_STORIES]

    blocks = []
    for row, body in eligible:
        header = (
            f"[{row.get('rank')}] {row.get('title')}  "
            f"(score {row.get('score', 0)}, {row.get('comment_count', 0)} comments)\n"
            f"url: {row.get('url') or ''}"
        )
        parts = [header, "--- STORY ---", body[:HN_TAKE_BODY_CAP]]
        discussion = _discussion_block(row)
        if discussion:
            parts.append(discussion)
        blocks.append("\n".join(parts))
    return "\n\n".join(blocks)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_hn_take.py -v`
Expected: PASS (all 15 tests).

- [ ] **Step 6: Commit**

```bash
git add hn_take.py tests/test_hn_take.py
git commit -m "feat(hn-take): eligibility gate, context assembly, ported prompt"
```

---

### Task 2: `hn_take.py` — OpenAI generation (fail-open)

Wrap the single Responses call, mirroring `hn_comment_camps`'s `_client()` and `API_ERRORS` handling.

**Files:**
- Modify: `hn_take.py`
- Test: `tests/test_hn_take.py` (append)

**Interfaces:**
- Consumes: `build_take_context` output (Task 1), `HN_TAKE_SYSTEM`, `HN_TAKE_MODEL`, `HN_TAKE_REASONING`.
- Produces:
  - `_client() -> "OpenAI | None"`.
  - `generate_take(context: str) -> str | None` — the column markdown on success, `None` on any failure (with `API_ERRORS` appended on `OpenAIError`).

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_hn_take.py
import types
from openai import OpenAIError


class _FakeResp:
    def __init__(self, text):
        self.output_text = text


class _FakeClient:
    def __init__(self, text=None, exc=None):
        self._text, self._exc = text, exc
        self.responses = types.SimpleNamespace(create=self._create)
        self.calls = []

    def _create(self, **kw):
        self.calls.append(kw)
        if self._exc:
            raise self._exc
        return _FakeResp(self._text)


def test_generate_returns_output_text(monkeypatch):
    client = _FakeClient(text="# Section\nprose here")
    monkeypatch.setattr(hn_take, "_client", lambda: client)
    assert hn_take.generate_take("ctx") == "# Section\nprose here"
    assert client.calls[0]["model"] == hn_take.HN_TAKE_MODEL
    assert client.calls[0]["instructions"] == hn_take.HN_TAKE_SYSTEM
    assert client.calls[0]["input"] == "ctx"
    assert client.calls[0]["reasoning"] == {"effort": hn_take.HN_TAKE_REASONING}


def test_generate_none_on_empty_output(monkeypatch):
    monkeypatch.setattr(hn_take, "_client", lambda: _FakeClient(text="   "))
    assert hn_take.generate_take("ctx") is None


def test_generate_none_on_missing_client(monkeypatch):
    monkeypatch.setattr(hn_take, "_client", lambda: None)
    assert hn_take.generate_take("ctx") is None


def test_generate_none_and_records_error_on_openai_error(monkeypatch):
    hn_take.API_ERRORS.clear()
    monkeypatch.setattr(hn_take, "_client",
                        lambda: _FakeClient(exc=OpenAIError("boom")))
    assert hn_take.generate_take("ctx") is None
    assert hn_take.API_ERRORS and "boom" in hn_take.API_ERRORS[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_hn_take.py -k generate -v`
Expected: FAIL — `AttributeError: module 'hn_take' has no attribute 'generate_take'`.

- [ ] **Step 3: Implement `_client` and `generate_take`**

Add to `hn_take.py`:

```python
def _client() -> "OpenAI | None":
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        logging.error("OPENAI_API_KEY not set; cannot generate the HN Take")
        return None
    return OpenAI(api_key=key, max_retries=6)


def generate_take(context: str) -> str | None:
    """Run the one OpenAI Responses call; return the column markdown or None.

    Fail-open: missing key, empty output, or any OpenAIError -> None (the error
    is recorded in API_ERRORS so the daily run can surface one notification).
    """
    try:
        client = _client()
        if client is None:
            return None
        out = client.responses.create(
            model=HN_TAKE_MODEL,
            instructions=HN_TAKE_SYSTEM,
            input=context,
            reasoning={"effort": HN_TAKE_REASONING},
            timeout=300,
            prompt_cache_options={"mode": "explicit"},
        ).output_text.strip()
    except OpenAIError as exc:
        logging.exception("HN Take generation request failed")
        API_ERRORS.append(f"{type(exc).__name__}: {exc}")
        return None
    return out or None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_hn_take.py -k generate -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add hn_take.py tests/test_hn_take.py
git commit -m "feat(hn-take): fail-open OpenAI generation call"
```

---

### Task 3: `hn_take.py` — cache read/write + `get_or_generate`; schema in `init_db`

The cache table and the orchestration that skips the LLM on reruns.

**Files:**
- Modify: `hn_take.py`
- Modify: `trending_digest.py` (add `CREATE TABLE` to `init_db`, ~line 683 in the DDL list)
- Test: `tests/test_hn_take.py` (append)

**Interfaces:**
- Consumes: `build_take_context` (Task 1), `generate_take` (Task 2).
- Produces:
  - `load_cached_take(conn, run_day) -> str | None`.
  - `store_take(conn, run_day, take_md) -> None`.
  - `get_or_generate(conn, rows, run_day) -> str | None`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_hn_take.py
from datetime import date


def test_get_or_generate_returns_cache_without_generating(monkeypatch):
    monkeypatch.setattr(hn_take, "load_cached_take", lambda conn, day: "cached column")
    monkeypatch.setattr(hn_take, "generate_take",
                        lambda ctx: (_ for _ in ()).throw(AssertionError("should not generate")))
    assert hn_take.get_or_generate(None, [], date(2026, 8, 24)) == "cached column"


def test_get_or_generate_generates_and_stores_on_miss(monkeypatch):
    stored = {}
    monkeypatch.setattr(hn_take, "load_cached_take", lambda conn, day: None)
    monkeypatch.setattr(hn_take, "build_take_context", lambda rows: "ctx")
    monkeypatch.setattr(hn_take, "generate_take", lambda ctx: "fresh column")
    monkeypatch.setattr(hn_take, "store_take",
                        lambda conn, day, md: stored.update(day=day, md=md))
    result = hn_take.get_or_generate(None, [{"x": 1}], date(2026, 8, 24))
    assert result == "fresh column"
    assert stored == {"day": date(2026, 8, 24), "md": "fresh column"}


def test_get_or_generate_none_when_no_qualifying(monkeypatch):
    monkeypatch.setattr(hn_take, "load_cached_take", lambda conn, day: None)
    monkeypatch.setattr(hn_take, "build_take_context", lambda rows: "")
    monkeypatch.setattr(hn_take, "generate_take",
                        lambda ctx: (_ for _ in ()).throw(AssertionError("no LLM on empty ctx")))
    assert hn_take.get_or_generate(None, [], date(2026, 8, 24)) is None


def test_get_or_generate_none_on_generation_failure_no_store(monkeypatch):
    monkeypatch.setattr(hn_take, "load_cached_take", lambda conn, day: None)
    monkeypatch.setattr(hn_take, "build_take_context", lambda rows: "ctx")
    monkeypatch.setattr(hn_take, "generate_take", lambda ctx: None)
    monkeypatch.setattr(hn_take, "store_take",
                        lambda *a: (_ for _ in ()).throw(AssertionError("no store on failure")))
    assert hn_take.get_or_generate(None, [{"x": 1}], date(2026, 8, 24)) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_hn_take.py -k get_or_generate -v`
Expected: FAIL — `AttributeError: module 'hn_take' has no attribute 'get_or_generate'`.

- [ ] **Step 3: Implement cache helpers + orchestration**

Add to `hn_take.py`:

```python
def load_cached_take(conn, run_day) -> str | None:
    """Return the cached column for (run_day, model, prompt_version), or None."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT take_md FROM hn_takes "
            "WHERE run_date = %s AND model = %s AND prompt_version = %s",
            (run_day, HN_TAKE_MODEL, HN_TAKE_PROMPT_VERSION),
        )
        row = cur.fetchone()
    return row[0] if row else None


def store_take(conn, run_day, take_md) -> None:
    """Upsert the generated column keyed on (run_date, model, prompt_version)."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO hn_takes (run_date, model, prompt_version, take_md) "
            "VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (run_date, model, prompt_version) "
            "DO UPDATE SET take_md = EXCLUDED.take_md, generated_at = NOW()",
            (run_day, HN_TAKE_MODEL, HN_TAKE_PROMPT_VERSION, take_md),
        )
    conn.commit()


def get_or_generate(conn, rows, run_day) -> str | None:
    """Return the day's column: cached if present, else assemble -> generate -> store.

    Returns None (no column) when nothing qualifies or generation fails.
    """
    cached = load_cached_take(conn, run_day)
    if cached is not None:
        return cached
    context = build_take_context(rows)
    if not context:
        return None
    take_md = generate_take(context)
    if not take_md:
        return None
    store_take(conn, run_day, take_md)
    return take_md
```

- [ ] **Step 4: Add the `enabled()` gate**

Add to `hn_take.py`:

```python
def enabled() -> bool:
    """True when the column is switched on and an OpenAI key is present."""
    flag = os.environ.get("HN_TAKE_ENABLED", "1").strip().lower()
    if flag in ("0", "false", "no", "off", ""):
        return False
    return bool(os.environ.get("OPENAI_API_KEY"))
```

- [ ] **Step 5: Add the schema to `init_db`**

In `trending_digest.py`, `init_db` builds a list of DDL strings (each a `"""CREATE TABLE ..."""`). After the `hn_comment_analyses` table block (ends ~line 683), add two new entries to that list:

```python
        """
        CREATE TABLE IF NOT EXISTS hn_takes (
            id             BIGSERIAL PRIMARY KEY,
            run_date       DATE NOT NULL,
            model          TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            take_md        TEXT NOT NULL,
            generated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS hn_takes_day_ver
            ON hn_takes (run_date, model, prompt_version)
        """,
```

(Confirm the surrounding syntax: `init_db` iterates the list and executes each statement. Match the existing comma/quoting style exactly.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_hn_take.py -k get_or_generate -v`
Expected: PASS (4 tests).

- [ ] **Step 7: Commit**

```bash
git add hn_take.py trending_digest.py tests/test_hn_take.py
git commit -m "feat(hn-take): DB cache, get_or_generate orchestration, enabled() gate, schema"
```

---

### Task 4: Render — `take_md` arg on `generate_hn_daily_page`

Additive render: a `<details open>` "The HN Take" block above the story list, reusing the existing shared `.the-take` CSS.

**Files:**
- Modify: `trending_digest.py` (`render_hn_take_html` helper near `render_comment_analysis_html` ~line 1743; `generate_hn_daily_page` at line 2214)
- Test: `tests/test_hn_take_render.py`

**Interfaces:**
- Consumes: `take_md: str | None` (the column markdown from `hn_take.get_or_generate`).
- Produces: `generate_hn_daily_page(items, day, known_dates, take_md=None) -> str` — HTML with the Take block when `take_md` is truthy, byte-for-byte today's output when it's falsy.

Note: the `.the-take` / `.take-head` CSS already exists in `generate_css()` (trending_digest.py ~line 2452) and ships in the shared `style.css` the HN page loads. No CSS changes needed. The markup mirrors `ai_edition.render_the_take` (ai_edition.py:216) with the label "The HN Take".

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_hn_take_render.py
from datetime import date

import trending_digest


def _known():
    return {"gh": set(), "hn": set()}


def test_take_block_rendered_above_stories():
    html = trending_digest.generate_hn_daily_page(
        [], date(2026, 8, 24), _known(),
        take_md="# Opening beat\nDry commentary follows.",
    )
    assert 'class="the-take"' in html
    assert "<summary>The HN Take</summary>" in html
    assert '<h3 class="take-head">Opening beat</h3>' in html
    assert "Dry commentary follows." in html
    # placed above the story list container
    assert html.index('class="the-take"') < html.index('class="repos"')


def test_no_take_arg_matches_today_output():
    args = ([], date(2026, 8, 24), _known())
    assert trending_digest.generate_hn_daily_page(*args) == \
        trending_digest.generate_hn_daily_page(*args, take_md=None)


def test_empty_take_omits_block():
    html = trending_digest.generate_hn_daily_page(
        [], date(2026, 8, 24), _known(), take_md="")
    assert 'class="the-take"' not in html
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_hn_take_render.py -v`
Expected: FAIL — `generate_hn_daily_page() got an unexpected keyword argument 'take_md'`.

- [ ] **Step 3: Add the render helper**

In `trending_digest.py`, near `render_comment_analysis_html` (~line 1743), add:

```python
def render_hn_take_html(take_md: str | None) -> str:
    """Render the daily HN Take column as a collapsible-open block.

    Section titles are '# '-prefixed (the synthesis prompt's convention); all
    other non-blank lines become paragraphs. Returns '' for falsy input so the
    page renders unchanged.
    """
    text = (take_md or "").strip()
    if not text:
        return ""
    parts = []
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("# "):
            parts.append(f'<h3 class="take-head">{html.escape(line[2:].strip())}</h3>')
        else:
            parts.append(f"<p>{html.escape(line)}</p>")
    return ('<section class="the-take"><details open>'
            f'<summary>The HN Take</summary>{"".join(parts)}</details></section>')
```

- [ ] **Step 4: Thread the arg through `generate_hn_daily_page`**

Change the signature (line 2214):

```python
def generate_hn_daily_page(items: list[dict], day: date, known_dates: dict[str, set[str]], take_md: str | None = None) -> str:
```

Just after the signature/docstring, before building `story_cards`, add:

```python
    take_html = render_hn_take_html(take_md)
```

In the returned template, insert `take_html` immediately after `<main>` and before `<div class="repo-controls">`:

```python
    <main>
        {take_html}
        <div class="repo-controls">
```

(When `take_html` is `""` this collapses to a blank line inside `<main>`; to keep byte-for-byte parity with today's output when absent, verify `test_no_take_arg_matches_today_output` passes — if the blank line breaks parity, conditionally prefix a newline only when non-empty, e.g. set `take_html = render_hn_take_html(take_md)` to already include no leading/trailing whitespace and place it as `{take_html}` on its own line; the test is the arbiter.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_hn_take_render.py tests/test_hn_card_render.py -v`
Expected: PASS. `test_hn_card_render.py` (existing) still passes → confirms no regression to today's page.

- [ ] **Step 6: Commit**

```bash
git add trending_digest.py tests/test_hn_take_render.py
git commit -m "feat(hn-take): additive render of The HN Take above the story list"
```

---

### Task 5: Daily-run integration + degraded notification; regen stays LLM-free

Wire the gated call into the live pipeline; surface a single notification on failure; keep regeneration a pure re-render.

**Files:**
- Modify: `trending_digest.py` (import ~line 33; live pipeline ~line 3515–3538; degraded-notify block ~line 3517; `regenerate_hn_daily_pages` call ~line 2839)
- Test: `tests/test_hn_take_wiring.py`

**Interfaces:**
- Consumes: `hn_take.enabled()`, `hn_take.get_or_generate` (Task 3), `generate_hn_daily_page(..., take_md=...)` (Task 4).
- Produces: no new public surface — behavior only.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_hn_take_wiring.py
from datetime import date

import trending_digest
import hn_take


def test_regenerate_passes_take_md_none(monkeypatch):
    captured = {}

    def fake_render(rows, day, known, take_md=None):
        captured["take_md"] = take_md
        return "<html></html>"

    monkeypatch.setattr(trending_digest, "build_hn_view_rows",
                        lambda conn, day, allow_summary_generation=True: [])
    monkeypatch.setattr(trending_digest, "generate_hn_daily_page", fake_render)
    monkeypatch.setattr(trending_digest, "write_text", lambda *a, **k: None)
    monkeypatch.setattr(trending_digest, "generate_morning_edition", lambda *a, **k: None)
    # get_or_generate must never be reached on the regen path
    monkeypatch.setattr(hn_take, "get_or_generate",
                        lambda *a: (_ for _ in ()).throw(AssertionError("no LLM on regen")))

    trending_digest.regenerate_hn_daily_pages(None, [date(2026, 8, 24)], {"gh": set(), "hn": set()})
    assert captured["take_md"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_hn_take_wiring.py -v`
Expected: FAIL — `regenerate_hn_daily_pages` currently calls `generate_hn_daily_page` with no `take_md`, so `captured["take_md"]` is absent → `KeyError`. (After Task 4 the default is `None`, but the test asserts the call is made and reachable; making the arg explicit in Step 4 guarantees intent.)

- [ ] **Step 3: Add the import**

In `trending_digest.py`, next to `import hn_comment_camps` (line 33):

```python
import hn_take
```

- [ ] **Step 4: Make regen explicitly LLM-free**

In `regenerate_hn_daily_pages` (line 2839), change:

```python
        hn_daily_html = generate_hn_daily_page(hn_rows, render_day, known_dates)
```
to:
```python
        hn_daily_html = generate_hn_daily_page(hn_rows, render_day, known_dates, take_md=None)
```

- [ ] **Step 5: Wire the live daily path**

In the live pipeline (line 3538), replace:

```python
        hn_daily_html = generate_hn_daily_page(hn_rows, run_day, known_dates)
```
with:
```python
        take_md = None
        if hn_take.enabled():
            try:
                take_md = hn_take.get_or_generate(conn, hn_rows, run_day)
            except Exception as exc:  # defensive: the column must never sink the run
                logging.exception("HN Take generation failed for %s: %s", run_day, exc)
        hn_daily_html = generate_hn_daily_page(hn_rows, run_day, known_dates, take_md=take_md)
```

- [ ] **Step 6: Add the degraded notification**

Immediately after the existing `hn_comment_camps.API_ERRORS` notify block (lines 3517–3524), add a parallel block:

```python
        if hn_take.API_ERRORS:
            notify_gotify(
                "GitHub Trending Digest: HN Take degraded",
                f"{len(hn_take.API_ERRORS)} OpenAI request(s) failed for {run_day}; "
                f"The HN Take was skipped (page published without the column). "
                f"Run completed and published normally.\n\n"
                f"First error: {hn_take.API_ERRORS[0]}",
            )
```

Place it after the pipeline's `hn_take.get_or_generate` call so `API_ERRORS` is populated (i.e. move this notify block to just after Step 5's wiring, or keep the camps block where it is and add this one right below the `hn_daily_html = ...` assignment — the arbiter is that `hn_take.get_or_generate` has already run when this check executes).

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_hn_take_wiring.py -v`
Expected: PASS.

- [ ] **Step 8: Run the full suite**

Run: `pytest -q`
Expected: PASS (all tests, including existing `test_hn_card_render.py`, `test_hn_comment_camps.py`, `test_hn_comment_analysis_wiring.py`).

- [ ] **Step 9: Commit**

```bash
git add trending_digest.py tests/test_hn_take_wiring.py
git commit -m "feat(hn-take): gated live-pipeline wiring, degraded notification, LLM-free regen"
```

---

### Task 6: Live validation (one real generation, editorial grade)

A single real end-to-end run against a recent day, graded by eye. Not an automated test — a manual acceptance gate mirroring ai-newsletter's functional check.

**Files:** none (throwaway script or REPL session).

- [ ] **Step 1: Pick a recent day with substantial stories and run one real generation**

With `OPENAI_API_KEY` set, in a REPL (uses the live DB connection helper):

```python
import trending_digest as td, hn_take
from datetime import date
conn = td.get_db_connection()
day = date(2026, 8, 23)  # a recent published HN day
rows = td.build_hn_view_rows(conn, day, allow_summary_generation=False)
ctx = hn_take.build_take_context(rows)
print("qualifying story blocks:", ctx.count("--- STORY ---"))
take = hn_take.generate_take(ctx)   # bypasses cache to force a real call
print(take)
```

- [ ] **Step 2: Grade it against the spec's editorial standards**

Confirm by eye:
- Voice fidelity (dry, concrete, plain-then-absurd; reads like the ai-newsletter Take).
- Veracity: no invented numbers/names/quotes; claims traceable to the story bodies.
- Weaving: the HN reaction sharpens the point where present — not a thread summary.
- Clean `# ` section titles; no preamble, disclaimer, em/en dashes, or markdown beyond `# `.
- Ineligible stories (thin links, comment-only) are absent from the column.
- Any residual extraction boilerplate is handled gracefully (ignored, not amplified).

- [ ] **Step 3: Record the outcome**

If it grades well, note it in the PR/commit description and proceed. If voice or veracity is off, capture the failing column text and revisit `HN_TAKE_SYSTEM`'s adapted final paragraph or `HN_TAKE_MIN_CHARS` before shipping (these are the tunable knobs; see spec §9). No commit unless a knob changes.

---

## Self-Review

**Spec coverage:**
- §2 Eligibility gate → Task 1 (`qualifying_body`, boundary + comments-only + link-post tests).
- §3 Input assembly → Task 1 (`build_take_context`: ordering, body cap, discussion block present/absent, max-stories).
- §4 Generation → Task 2 (`generate_take`, `_client`, fail-open, `API_ERRORS`). Prompt port + version + de-attribution → Task 1 (constant + invariant tests).
- §4 Caching → Task 3 (table in `init_db`, `load_cached_take`/`store_take`/`get_or_generate`, cache-hit-skips-LLM test).
- §5 Rendering → Task 4 (`take_md` arg, `<details open>` above list, absent-falls-back-to-today).
- §6 Daily-run integration → Task 5 (`enabled()` gate, live call, `regenerate` passes `take_md=None`, degraded notification).
- §7 Testing → Tasks 1–5 unit tests; §7 live validation → Task 6.
- §8 Rollout/risks → satisfied by additive design + `HN_TAKE_ENABLED` gate (Task 3/5).
- §9 Deferred (collection spec, cross-page surfacing, min-chars tuning) → intentionally out of scope; Task 6 Step 3 flags min-chars as the tuning knob.

**Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to Task N". The only non-inlined asset is the 108k-char prompt string, which Task 1 Step 3 specifies by exact source file + line span (17–616) with the exact final-paragraph replacement given verbatim — copying a large literal is the correct instruction, not a placeholder.

**Type consistency:** `qualifying_body`, `build_take_context`, `generate_take`, `_client`, `load_cached_take`, `store_take`, `get_or_generate`, `enabled`, `render_hn_take_html` — names and signatures are used identically across the tasks that define and consume them. `HN_TAKE_MODEL`/`HN_TAKE_PROMPT_VERSION` are referenced consistently in generation (Task 2) and cache key (Task 3). The render arg is `take_md` everywhere (Tasks 4, 5).
