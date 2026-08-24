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
    # the model input (outline) is persisted for retention/auditability
    assert saved["outline"] and "alice: hello world" in saved["outline"]
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
