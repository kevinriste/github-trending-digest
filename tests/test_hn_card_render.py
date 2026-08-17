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
