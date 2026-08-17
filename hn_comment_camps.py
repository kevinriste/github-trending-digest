"""SSC-style two-stage comment analysis for Hacker News threads.

Ingests an indented thread outline and emits structured JSON (framing + camps
with verbatim quotes) via the OpenAI Responses API. Pure of any presentation:
the model returns data, callers render it. No import of trending_digest.
"""

import html
import json
import logging
import os

from openai import OpenAI, OpenAIError

MODEL = os.environ.get("COMMENT_BRIEFING_MODEL", "gpt-5-mini")


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
