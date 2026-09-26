"""OpenAI Responses helper for the digest's bulk summary jobs.

These run on gpt-5.6-luna by default: it sits in OpenAI's 10M/day free-token group
(MINI), which the digest's share-project key draws on at no cost. Only the PDF or
image an HN summary may attach is sent as a file part; everything else is text.
"""

from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path

from openai import OpenAI

SUMMARY_MODEL = os.environ.get("DIGEST_SUMMARY_MODEL", "gpt-5.6-luna")
SUMMARY_REASONING = os.environ.get("DIGEST_SUMMARY_REASONING", "low")

_client: OpenAI | None = None


def client() -> OpenAI:
    """Lazy-init the OpenAI client (OPENAI_API_KEY is the share key; see process.sh)."""
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], max_retries=6)
    return _client


def _file_part(path: str) -> dict:
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    data = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    if mime.startswith("image/"):
        return {"type": "input_image", "image_url": f"data:{mime};base64,{data}"}
    return {"type": "input_file", "filename": Path(path).name, "file_data": f"data:{mime};base64,{data}"}


def _input(prompt: str, file_path: str | None) -> "str | list":
    if not file_path:
        return prompt
    return [{"role": "user", "content": [_file_part(file_path), {"type": "input_text", "text": prompt}]}]


def generate(prompt: str, *, file_path: str | None = None, model: str = SUMMARY_MODEL) -> str:
    """One Responses call; returns the stripped output text."""
    response = client().responses.create(
        model=model,
        input=_input(prompt, file_path),
        reasoning={"effort": SUMMARY_REASONING},
        timeout=300,
    )
    return (response.output_text or "").strip()


def generate_parsed(prompt: str, text_format, *, model: str = SUMMARY_MODEL):
    """One Responses call with structured output; returns the parsed pydantic object."""
    response = client().responses.parse(
        model=model,
        input=prompt,
        text_format=text_format,
        reasoning={"effort": SUMMARY_REASONING},
        timeout=300,
    )
    return response.output_parsed
