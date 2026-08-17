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
