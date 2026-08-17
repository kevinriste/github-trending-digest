# HN Comment Analysis: Port the SSC "Camps" Treatment

Date: 2026-08-17
Status: Design — pending approval

## Goal

Replace the current thin 3-bullet Hacker News comment analysis with the richer
"Highlights From The Comments" treatment used for Slate Star Codex in the
`podcast-transcribe` project (`archive/comment_briefing.py`): an indented
whole-thread outline fed to a stronger model that produces a *framing* plus a set
of *camps*, each with verbatim supporting quotes.

Crucially, the model emits **structured JSON**, not HTML or prose. Upstream
renderers own all presentation. The same JSON drives both the classic digest card
and both morning-edition layouts.

## Current state

**HN today (`trending_digest.py`)**
- `build_hn_comment_nodes` — branch-diverse round-robin BFS of the HN Firebase
  tree (≤300 nodes, depth ≤6, min text 40 chars). Keeps flat node dicts with
  `depth`, `root_id`, `root_pos`, `reply_count`, `len`, `text`.
- `select_hn_comment_sample` — ranks by a hand-tuned signal score, dedupes, caps
  4 per branch, keeps **16 scattered** nodes.
- `generate_hn_comment_analysis` — flattens to `[idx] depth=N top_thread=M by=X:
  text` (no indentation) and asks **Gemini `gemini-3.1-flash-lite`** for exactly
  3 bullets. Cached in Postgres `hn_comment_analyses`, prompt version
  `hn_comments_v2`.
- Output is a bullet-line string consumed by two renderers:
  - `trending_digest.py:2242` → `generate_bullet_paragraph_html`
  - `morning_edition.py:385,485` → `parse_bullets`

**SSC reference (`podcast-transcribe/archive/comment_briefing.py`)**
- `extract_comments` — every comment, own body + nesting `depth`, **uncapped**.
- Indented outline: `{'    ' * depth}{author}: {text}`.
- Two-stage **OpenAI Responses API** pipeline (`COMMENT_BRIEFING_MODEL`, prod =
  `gpt-5.6-luna`): Stage 1 detects camps (framing + every substantive camp with
  exemplar commenters); Stage 2 writes verbatim quotes. SSC's Stage 2 emits TTS
  speaker tags — that stage is *not* ported; we emit JSON instead.

**Key constraints discovered**
- GitHub items set `comment_analysis = ""` (`ai_edition.py:77`); the non-HN
  "Insights" path in morning_edition is dormant. The new format is **HN-only**;
  the GH path is untouched.
- Renderers must tolerate **both** legacy `v2` bullet strings (already cached) and
  new `v3` JSON during rollout — decided by attempting `json.loads` and falling
  back to the existing bullet renderer.
- This repo currently wires only `GEMINI_API_KEY`. The port needs
  `OPENAI_API_KEY` + `COMMENT_BRIEFING_MODEL` in this job's environment.

## Design

### 1. Comment gathering — indented, contiguous, budget-capped

Add `build_hn_comment_outline(item_id, total_hint) -> tuple[int, list[dict]]`
that returns comments in **render order** (grouped by top-thread `root_pos`, then
tree order so a reply follows its parent) with `depth` preserved, up to a
character budget. It reuses the existing traversal primitives
(`fetch_hn_item_cached`, `clean_hn_comment_text`, depth/min-text filters) but:
- keeps **whole contiguous branches** instead of a scattered top-N sample, so
  indentation is meaningful;
- stops accumulating once the rendered outline would exceed
  `HN_COMMENT_ANALYSIS_MAX_CHARS` (new env, default ~48000), dropping the
  lowest-priority *whole* top-level branches first (never truncating mid-branch).

`select_hn_comment_sample` stays in the tree only if still referenced elsewhere;
otherwise it is removed with its test. The outline is rendered exactly like SSC:

```
{'    ' * (depth-1)}{author}: {text}
```

`total_comments` (thread `descendants`) is still reported for the "sampled N of M"
metadata.

### 2. Two-stage camps analysis → structured JSON

New module boundary: an OpenAI helper mirroring SSC's `_post_model` (explicit
prompt-cache mode, `max_retries`, 300s timeout, graceful `None` on failure),
reading `OPENAI_API_KEY` and `COMMENT_BRIEFING_MODEL`.

- **Stage 1 (spine):** free-text analysis — framing + camps with exemplar
  commenters. Same intent as SSC's ANALYSIS_PROMPT, anchored to the story summary.
- **Stage 2 (fill):** OpenAI Responses **structured output** (`text.format` =
  `json_schema`, strict) producing:

```json
{
  "framing": "2-4 sentence characterization of the reaction.",
  "camps": [
    {
      "label": "Short camp name",
      "description": "1-2 sentences; position and rough level of support.",
      "quotes": [ { "text": "verbatim comment text", "author": "pg" } ]
    }
  ]
}
```

Output caps enforced in schema + prompt: **≤6 camps**, **≤3 quotes per camp**,
quote text drawn **verbatim** from the outline. Quotes are selected (not
truncated) so verbatim integrity holds; the prompt asks for the pithiest
representative sentence(s).

Prompt version bumps to `hn_comments_v3`.

### 3. Storage

`hn_comment_analyses.analysis_text` stays `TEXT`; store the **JSON string**. No
schema migration. `model`/`prompt_version`/`sample_size` columns already exist;
`sample_size` records the char budget or node count used. Freshness gate and
cache-on-failure fallback are unchanged. (jsonb migration is possible later if we
want to query the structure — out of scope now.)

### 4. Rendering — both editions, HN-only, tolerant of legacy

A single shared parser `parse_comment_analysis(raw) -> dict | None` attempts
`json.loads` and validates the shape; returns `None` for legacy/blank/garbled
input so callers fall back to the existing bullet path.

- **Classic digest (`trending_digest.py`):** replace the
  `generate_bullet_paragraph_html` call in the Comment Analysis card with a
  renderer that emits framing `<p>`, then per camp a labelled block with its
  quotes as `<blockquote>` + attribution. Legacy rows fall back to the current
  bullet rendering.
- **Morning edition (`morning_edition.py`):** both `_render_analysis_drawer`
  (~385) and the archetype loop (~485) render the **full** camps structure for
  `config.id == "hn"` (framing + all camps + quotes). Non-HN (`gh`) keeps the
  existing `parse_bullets` path untouched. Legacy HN rows fall back to bullets.

All camp labels, descriptions, quotes, and authors are HTML-escaped at render
time. The model never sees or emits markup.

### 5. Config / env

New env, all with defaults so dev runs unchanged:
- `OPENAI_API_KEY` — required for the new analysis; if unset, log and keep the
  legacy/cached analysis (never crash the run).
- `COMMENT_BRIEFING_MODEL` — default `gpt-5-mini`; prod sets `gpt-5.6-luna`.
- `HN_COMMENT_ANALYSIS_MAX_CHARS` — default 48000 (input outline budget).
- `HN_COMMENT_ANALYSIS_MAX_CAMPS` (6), `HN_COMMENT_ANALYSIS_MAX_QUOTES` (3).

## Data flow

```
scrape_hn_topstories
  → get_or_generate_hn_comment_analysis (freshness/cache gate)
      → build_hn_comment_outline  (indented, budget-capped)
      → stage1_camps (OpenAI free text)
      → stage2_json  (OpenAI structured output)  → JSON string
      → cache in hn_comment_analyses (v3)
  → row["comment_analysis"] = JSON string
      → classic digest card renderer (parse → HTML, else legacy bullets)
      → morning_edition renderers  (parse → HTML, else legacy bullets)
```

## Error handling

- Any OpenAI failure at either stage → return prior cached analysis if present,
  else `None` (no reactions block). Matches today's behaviour; never blocks the
  article/summary path.
- Missing `OPENAI_API_KEY` → same as failure; logged once.
- Renderer receiving unparseable JSON → legacy bullet fallback, never an
  exception.

## Testing

- `build_hn_comment_outline`: thread-contiguity, depth indentation, char-budget
  truncation drops whole branches, min-text/depth filters honoured. (Mock the
  Firebase fetches, as existing tests do.)
- `parse_comment_analysis`: valid JSON → dict; legacy bullets → `None`; blank →
  `None`; malformed JSON → `None`.
- Stage-2 schema conformance: a fixture JSON renders to escaped HTML in both the
  classic card and the morning-edition drawer; quote attribution present.
- Legacy-compat: a `v2` bullet row still renders via the fallback in both
  editions.
- OpenAI helper: missing key → `None` + log, no raise.

## Out of scope

- GitHub "Insights" path (stays on bullets / empty).
- Any TTS / podcast output (that lives in `podcast-transcribe`).
- jsonb migration and structured querying of analyses.
- Changing the HN story *summary* pipeline (still Gemini).

## Settled decisions

- Model output is **structured JSON**, not HTML or markdown (upstream owns
  presentation; data is genuinely nested).
- **Full** camps analysis in **both** editions — no condensed morning variant.
- Retain "hundreds" of comments like SSC, but **char-budgeted** because HN threads
  reach thousands; cap the **output** via schema limits.
- "Sol" = `COMMENT_BRIEFING_MODEL` (prod `gpt-5.6-luna`), via the OpenAI Responses
  API, replacing Gemini flash-lite for this analysis only.
