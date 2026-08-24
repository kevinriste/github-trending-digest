from datetime import date

import morning_edition
from morning_edition import CONFIGS, generate_morning_edition_html


def _html(**kw):
    return generate_morning_edition_html(
        CONFIGS["hn"], date(2026, 8, 24), [], [], {"gh": set(), "hn": set()}, **kw
    )


def test_take_html_injected_verbatim():
    take = ('<section class="the-take"><details open>'
            '<summary>The HN Take</summary>'
            '<h3 class="take-head">Beat</h3><p>Prose.</p>'
            '<div class="take-sources"><span>Sources</span>'
            '<ul><li><a href="https://ex/a">A</a></li></ul></div>'
            '</details></section>')
    html = _html(take_html=take)
    assert "<summary>The HN Take</summary>" in html
    assert 'class="take-sources"' in html
    assert 'href="https://ex/a"' in html


def test_take_html_takes_precedence_over_synthesis():
    html = _html(take_html="<section class='the-take'>HN</section>",
                 synthesis="# Should be ignored\nbody")
    assert "Should be ignored" not in html
    assert ">HN<" in html


def test_no_take_html_falls_back_to_synthesis():
    html = _html(synthesis="# Kept\nbody")
    assert "<summary>The Take</summary>" in html
    assert "Kept" in html


def test_take_sources_styles_present():
    # morning.css must style the sources list injected from the HN take
    assert ".take-sources" in morning_edition.CSS_TEMPLATE
