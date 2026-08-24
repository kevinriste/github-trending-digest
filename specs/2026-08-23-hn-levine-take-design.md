# The HN Take — Daily Levine-Style Column on Hacker News Stories

**Date:** 2026-08-23
**Status:** Draft for review
**Repo:** `github-trending-digest`
**Related:** ports the de-attributed "house style" Levine prompt and the
"substantial-primary-content only" gate proven in the `ai-newsletter` repo's
"The Take" (ai-newsletter `synth/prompts.py:OVERALL_SYNTHESIS_SYSTEM`,
`synth/roundup.py:overall_context`).

---

## 1. Problem & Goal

The daily HN digest renders the top ~10 Hacker News stories with a summary and
a comment-camps analysis, but there is no editorial synthesis tying the day
together. `ai-newsletter` proved that a single Levine-style ("Money Stuff")
column over the day's substantial stories reads at A− quality on `gpt-5.6-sol`.

**Goal:** generate one daily **"The HN Take"** — a unified Levine-style column
that synthesizes the day's top HN stories, **weaving each story's substance
together with the Hacker News discussion** (the comment camps), and render it
atop the HN daily page. Only stories with **substantial primary content** feed
it — a full fetched article **or** a full HN direct/self post — never a
title-only link, and never a story on the strength of comments alone.

### Non-goals
- No changes to source collection this cycle. Article extraction is treated as
  adequate (spot-checked); a "coverage/robustness" collection pass is a
  **separate, later spec**.
- No cross-repo package import. The Levine prompt and gate are **ported** (copied
  + adapted) into this repo — the two repos deploy independently.
- No per-story mini-takes and no standalone page: one unified column, rendered
  on the existing HN daily page.

---

## 2. Eligibility Gate — "substantial primary content"

A rendered HN story qualifies for the column iff **either**:

- **Full article:** `len(article_content.strip()) >= HN_TAKE_MIN_CHARS`
  (default **1500**), OR
- **Full HN direct post:** the item is a self/text post (`item_type` in
  `story`/`ask`/`show` with a non-empty `text`) and
  `len(text.strip()) >= HN_TAKE_MIN_CHARS`.

Excluded: title-only link stories with a thin/empty `article_content` and no
self `text`; any story whose only substance is its comment thread. The comment
discussion is a **reaction layer** woven in for qualifying stories, never a
qualification basis on its own.

`HN_TAKE_MIN_CHARS` mirrors ai-newsletter's `SYNTH_TAKE_MIN_CHARS=1500`, chosen
from a natural length valley (short announcement snippets below ~500–1k, real
articles a few thousand chars). Override via env.

---

## 3. Input Assembly

Consume the enriched rows `build_hn_view_rows` already produces (each row has
`title`, `url`, `score`, `item_type`, `text`, `article_content`, `summary`, and
`comment_analysis` — the camps JSON parsed by
`hn_comment_camps.parse_comment_analysis`).

For each **qualifying** story, in **descending HN score** (lead with the biggest):

```
[<rank>] <title>  (score <score>, <comment_count> comments)
url: <url>
--- STORY ---
<primary body: article_content OR self text, capped at HN_TAKE_BODY_CAP (12000) chars>
--- HN DISCUSSION ---
Framing: <camps.framing>
- <camp.label>: <camp.description>  [e.g. "<first verbatim quote>"]
  (up to HN_TAKE_QUOTES_PER_CAMP quotes, up to camps as provided)
```

If a qualifying story has no usable `comment_analysis` (missing/failed camps),
its `--- HN DISCUSSION ---` block is omitted — the article alone still carries
it. Assemble at most `HN_TAKE_MAX_STORIES` (default = the render limit, 10)
blocks; guard total prompt size with the per-body cap.

---

## 4. Generation

New module `hn_take.py` (pure of presentation, mirrors `hn_comment_camps.py`'s
shape and error-swallowing):

- **Prompt:** a repo-local constant `HN_TAKE_SYSTEM`, ported from
  ai-newsletter's `OVERALL_SYNTHESIS_SYSTEM` (the five full house-style example
  columns, the `# ` section-title convention, and the hard
  "output ONLY the prose, no preamble/disclaimer, begin at the first section
  title" instruction — the de-attribution that eliminated OpenAI refusals).
  Adapted only to note that each item now carries an `--- HN DISCUSSION ---`
  block, and to instruct: weave the story's substance with the community's
  reaction where the reaction sharpens the point; do not merely summarize the
  thread. `HN_TAKE_PROMPT_VERSION = "hn_take_v1"`.
- **Call:** one OpenAI Responses request via the existing `openai` client
  (reuse `hn_comment_camps`'s `_client()` pattern):
  `client.responses.create(model=HN_TAKE_MODEL, instructions=HN_TAKE_SYSTEM,
  input=<assembled context>, reasoning={"effort": HN_TAKE_REASONING},
  timeout=300, prompt_cache_options={"mode": "explicit"})`, reading
  `.output_text`.
  Defaults: `HN_TAKE_MODEL = "gpt-5.6-sol"`, `HN_TAKE_REASONING = "medium"`
  (both env-overridable).
- **Fail-open:** any `OpenAIError`, missing key, or empty output → return `None`
  (no column); record into a module-level `API_ERRORS` list like
  `hn_comment_camps`, so the daily run surfaces one notification and never
  crashes. A day with zero qualifying stories also returns `None`.

### Caching

New table so reruns of a day don't regenerate (and cost) the column:

```sql
CREATE TABLE IF NOT EXISTS hn_takes (
    id             BIGSERIAL PRIMARY KEY,
    run_date       DATE NOT NULL,
    model          TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    take_md        TEXT NOT NULL,
    generated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS hn_takes_day_ver
    ON hn_takes (run_date, model, prompt_version);
```

`get_or_generate(conn, rows, run_day) -> str | None`: return the cached
`take_md` for `(run_day, HN_TAKE_MODEL, HN_TAKE_PROMPT_VERSION)` if present;
otherwise assemble → generate → on success `INSERT ... ON CONFLICT DO UPDATE`
and return it; on failure return `None`.

---

## 5. Rendering

`generate_hn_daily_page` (`trending_digest.py:2214`) gains an optional
`take_md` argument. When present, render it (markdown → HTML via the same
converter the page already uses for summaries/camps) inside a styled
`<details open>` "The HN Take" block placed **above the story list**, mirroring
the AI edition's collapsible Take treatment (`ai_edition.py`). When absent
(disabled, no key, no qualifying stories, generation failed), the page renders
exactly as today — the block is simply omitted.

`regenerate_hn_daily_pages` passes `take_md=None` (regeneration is a pure
re-render of stored rows and must not trigger LLM calls); the column is produced
only on the live daily path.

---

## 6. Daily-Run Integration

In the live HN pipeline, after `build_hn_view_rows` yields the enriched rows and
before `generate_hn_daily_page`:

```python
take_md = None
if hn_take.enabled():           # HN_TAKE_ENABLED truthy AND OPENAI_API_KEY set
    take_md = hn_take.get_or_generate(conn, rows, run_day)
html = generate_hn_daily_page(rows, day, known_dates, take_md=take_md)
```

`hn_take.enabled()` gates on `HN_TAKE_ENABLED` (default on) **and** a present
`OPENAI_API_KEY`. Cost: one `gpt-5.6-sol` call per daily HN run (~a few cents),
cached against reruns — consistent with the project's existing per-day OpenAI
spend (summaries + camps).

---

## 7. Testing

**Unit (no network; mirror `hn_comment_camps` tests):**
- **Eligibility:** article ≥1500 qualifies; self/ask/show post `text` ≥1500
  qualifies; thin article + no self text is excluded; comments-only (rich camps,
  empty article & text) is excluded; boundary at exactly `HN_TAKE_MIN_CHARS`.
- **Assembly:** blocks ordered by descending score; body capped at
  `HN_TAKE_BODY_CAP`; `--- HN DISCUSSION ---` present when camps exist and
  omitted when absent; `HN_TAKE_MAX_STORIES` respected.
- **Prompt invariants:** `HN_TAKE_SYSTEM` contains the no-preamble instruction,
  the `# ` header convention, and no attributed author name (de-attribution) —
  the same class of check as ai-newsletter's `test_prompts.py`.
- **Generation (mocked client):** `output_text` returned verbatim on success;
  `None` on `OpenAIError`, on empty output, and on zero qualifying stories, with
  `API_ERRORS` populated on error.
- **Cache:** first call generates + stores; second call for the same
  `(run_day, model, version)` returns the stored row without calling the client.
- **Render:** `generate_hn_daily_page(..., take_md=...)` emits the `<details
  open>` block above the list; `take_md=None` omits it and matches today's
  output.

**Live validation (like The Take's functional test):** run one real
`get_or_generate` against a recent day, print the column, and grade it by
editorial standards (voice fidelity, veracity, weaving of article + HN reaction,
clean `#` sections, no preamble). Confirm ineligible stories are absent and that
the model handles any residual boilerplate gracefully.

---

## 8. Rollout & Risks

- **Reversibility:** entirely additive — new module, new table, one optional
  render arg, one gated pipeline call. Setting `HN_TAKE_ENABLED=0` (or removing
  the key) reverts to today's page with no code changes.
- **Cost:** one sol call/day, cached; opt-in via env. No change to summary/camps
  spend.
- **Garbage-in:** the 1500-char gate + "primary content only" rule keep stubs
  and comment-only stories out; ai-newsletter showed `gpt-5.6-sol` also ignores
  the occasional boilerplate leak, so a bad extraction degrades gracefully
  rather than producing a bad column.
- **Prompt asset size:** the five example columns (~27k tokens) are copied into
  `hn_take.py`; acceptable, matches the proven ai-newsletter setup.

---

## 9. Open Questions / Deferred

- **Collection "coverage/robustness" spec** — separate later cycle (more content
  types, retries, paywall handling, fetch depth). Not required for this column;
  current extraction is treated as adequate.
- **Surfacing beyond the HN daily page** (morning edition / index teaser) — v1
  renders on the HN daily page only; a teaser elsewhere can follow if wanted.
- **Tuning `HN_TAKE_MIN_CHARS`** once real per-day eligibility counts are in
  hand (start at 1500).
