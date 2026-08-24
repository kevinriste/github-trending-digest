from datetime import date

import trending_digest
import hn_take


def test_regenerate_passes_take_md_none(monkeypatch):
    captured = {}

    def fake_render(rows, day, known, take_md=None):
        captured["take_md"] = take_md
        return "<html></html>"

    monkeypatch.setattr(trending_digest, "build_hn_view_rows",
                        lambda conn, day, allow_summary_generation=True: [])
    monkeypatch.setattr(trending_digest, "generate_hn_daily_page", fake_render)
    monkeypatch.setattr(trending_digest, "write_text", lambda *a, **k: None)
    monkeypatch.setattr(trending_digest, "generate_morning_edition", lambda *a, **k: None)
    # get_or_generate must never be reached on the regen path
    monkeypatch.setattr(hn_take, "get_or_generate",
                        lambda *a: (_ for _ in ()).throw(AssertionError("no LLM on regen")))

    trending_digest.regenerate_hn_daily_pages(None, [date(2026, 8, 24)], {"gh": set(), "hn": set()})
    assert captured["take_md"] is None
