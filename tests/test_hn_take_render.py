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


def test_render_take_includes_source_links_when_provided():
    html = trending_digest.render_hn_take_html(
        "# Beat\nProse.",
        sources=[{"title": "ElevenLabs", "url": "https://example.com/a"}],
    )
    assert 'class="take-sources"' in html
    assert '<a href="https://example.com/a"' in html
    assert ">ElevenLabs</a>" in html
    # sources come after the prose
    assert html.index("Prose.") < html.index("take-sources")


def test_render_take_omits_sources_block_when_none():
    html = trending_digest.render_hn_take_html("# Beat\nProse.", sources=[])
    assert 'class="the-take"' in html
    assert 'class="take-sources"' not in html


def test_daily_page_shows_sources_for_confident_mapping():
    # a story whose article shares distinctive vocab with the take section -> confident link
    vocab = ("numbered labs naming convention startups integers domains "
             "distinctive branding sequence catalog elevenlabs twelvelabs")
    item = {
        "rank": 1, "item_id": 1, "title": "Numbered labs", "url": "https://ex/labs",
        "score": 100, "comment_count": 3, "item_type": "story", "text": "",
        "article_content": (vocab + " ") * 200,
        "discussion_url": "https://news.ycombinator.com/item?id=1",
        "summary": "s", "comment_analysis": "",
    }
    html = trending_digest.generate_hn_daily_page(
        [item], date(2026, 8, 24), _known(),
        take_md=f"# Numbered labs\n{vocab} riff.",
    )
    assert 'class="take-sources"' in html
    assert 'href="https://ex/labs"' in html
