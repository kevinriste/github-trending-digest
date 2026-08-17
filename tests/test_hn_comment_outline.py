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
    monkeypatch.setattr(trending_digest, "HN_COMMENT_ANALYSIS_MAX_CHARS", 12000)
    _total, comments = trending_digest.build_hn_comment_outline(1, 99)
    # ~5011-char lines: two fit under 12000, the third whole branch is dropped
    assert len(comments) == 2


def test_outline_high_engagement_deep_comment_pulls_ancestor_chain(monkeypatch):
    # A linear chain 10<-11<-12<-13 where the DEEP comment (13) has all the
    # engagement (three replies). Engagement ranking should pick 13 and pull in
    # its whole ancestor chain for contiguity, even under a tight budget.
    long = "this is a sufficiently long comment to clear the minimum text length filter"
    items = {1: _story([10])}
    items[10] = _comment(10, kids=[11], text=long)
    items[11] = _comment(11, kids=[12], text=long)
    items[12] = _comment(12, kids=[13], text=long)
    items[13] = _comment(13, kids=[14, 15, 16], text=long)
    for cid in (14, 15, 16):
        items[cid] = _comment(cid, text=long)
    monkeypatch.setattr(
        trending_digest, "fetch_hn_item_cached",
        lambda item_id, cache, session: items.get(item_id),
    )
    # Budget fits the 4-deep chain but not the depth-5 replies.
    per_line = len(long) + len("user") + 3
    monkeypatch.setattr(
        trending_digest, "HN_COMMENT_ANALYSIS_MAX_CHARS", 4 * per_line + 4 * (1 + 2 + 3 + 4),
    )
    _total, comments = trending_digest.build_hn_comment_outline(1, 99)
    # The engaged deep comment (13) and its full ancestor chain are present.
    assert {c["depth"] for c in comments} == {1, 2, 3, 4}
    # Rendered parent-before-child.
    assert [c["depth"] for c in comments] == [1, 2, 3, 4]


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
