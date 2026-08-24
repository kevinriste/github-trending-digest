import json
import types
from datetime import date

from openai import OpenAIError

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


# --- Task 1: eligibility gate ---

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


# --- Task 1: context assembly ---

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


def test_context_excludes_comment_discussion_even_when_camps_present():
    # article-only: the comment camps are rendered elsewhere and never fed to the Take
    camps = json.dumps({"framing": "people split", "camps": [
        {"label": "Pro", "description": "liked", "quotes": [{"text": "great", "author": "pg"}]}]})
    rows = [_row(article_content="a" * HN_TAKE_MIN_CHARS, comment_analysis=camps)]
    ctx = build_take_context(rows)
    assert "--- STORY ---" in ctx
    assert "--- HN DISCUSSION ---" not in ctx
    assert "people split" not in ctx and "great" not in ctx


def test_context_respects_max_stories(monkeypatch):
    monkeypatch.setattr(hn_take, "HN_TAKE_MAX_STORIES", 2)
    rows = [_row(rank=i, title=f"S{i}", score=100 - i,
                 article_content="a" * HN_TAKE_MIN_CHARS) for i in range(5)]
    ctx = build_take_context(rows)
    assert ctx.count("--- STORY ---") == 2


def test_context_empty_when_nothing_qualifies():
    assert build_take_context([_row(article_content="short", text="")]) == ""


# --- Task 1: prompt invariants ---

def test_prompt_has_no_preamble_and_header_convention():
    assert "output ONLY" in hn_take.HN_TAKE_SYSTEM
    assert "# " in hn_take.HN_TAKE_SYSTEM


def test_prompt_is_de_attributed():
    for name in ("Levine", "Money Stuff", "Matt "):
        assert name not in hn_take.HN_TAKE_SYSTEM


def test_prompt_is_article_only():
    # article-only: no reference to a comment-discussion block in the prompt
    assert "HN DISCUSSION" not in hn_take.HN_TAKE_SYSTEM
    assert '--- STORY ---' in hn_take.HN_TAKE_SYSTEM


# --- Task 2: generation ---

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


# --- Task 3: cache orchestration ---

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
                        lambda conn, day, md, ctx: stored.update(day=day, md=md, ctx=ctx))
    result = hn_take.get_or_generate(None, [{"x": 1}], date(2026, 8, 24))
    assert result == "fresh column"
    # both output and the exact input context are persisted
    assert stored == {"day": date(2026, 8, 24), "md": "fresh column", "ctx": "ctx"}


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
