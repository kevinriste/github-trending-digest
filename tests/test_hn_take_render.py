from datetime import date

import trending_digest


def _known():
    return {"gh": set(), "hn": set()}


def test_take_block_rendered_above_stories():
    html = trending_digest.generate_hn_daily_page(
        [], date(2026, 8, 24), _known(),
        take_md="# Opening beat\nDry commentary follows.",
    )
    assert 'class="the-take"' in html
    assert "<summary>The HN Take</summary>" in html
    assert '<h3 class="take-head">Opening beat</h3>' in html
    assert "Dry commentary follows." in html
    # placed above the story list container
    assert html.index('class="the-take"') < html.index('class="repos"')


def test_no_take_arg_matches_today_output():
    args = ([], date(2026, 8, 24), _known())
    assert trending_digest.generate_hn_daily_page(*args) == \
        trending_digest.generate_hn_daily_page(*args, take_md=None)


def test_empty_take_omits_block():
    html = trending_digest.generate_hn_daily_page(
        [], date(2026, 8, 24), _known(), take_md="")
    assert 'class="the-take"' not in html
