import json
import types

import hn_comment_camps
from hn_comment_camps import (
    parse_comment_analysis,
    render_camps_html,
    render_outline,
)


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
