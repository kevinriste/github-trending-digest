from editions import EDITIONS, cross_edition_links, published_dates, write_dates_manifest


def test_registry_labels_and_paths():
    assert list(EDITIONS) == ["gh", "hn", "ai"]
    assert EDITIONS["ai"].name == "AI News"
    assert EDITIONS["ai"].calendar_label == "AI News Calendar"
    assert EDITIONS["gh"].root_path == ""
    assert EDITIONS["hn"].root_path == "hn/"
    assert EDITIONS["ai"].root_path == "ai/"
    # localStorage keys must never change (users' read-day history)
    assert EDITIONS["gh"].read_key == "gtd:read_days:gh:v1"
    assert EDITIONS["hn"].read_key == "gtd:read_days:hn:v1"
    assert EDITIONS["ai"].read_key == "gtd:read_days:ai:v1"


def test_calendar_pages_always_link_other_calendars():
    assert cross_edition_links("gh") == [
        ("hn/", "Hacker News Calendar"),
        ("ai/", "AI News Calendar"),
    ]
    assert cross_edition_links("hn") == [
        ("../", "GitHub Calendar"),
        ("../ai/", "AI News Calendar"),
    ]
    assert cross_edition_links("ai") == [
        ("../", "GitHub Calendar"),
        ("../hn/", "Hacker News Calendar"),
    ]


def test_dated_page_omits_unpublished_editions():
    known = {"gh": {"2026-07-18"}, "hn": {"2026-07-18"}, "ai": set()}
    assert cross_edition_links("gh", "2026-07-18", known) == [
        ("../hn/2026-07-18/", "Hacker News"),
    ]
    assert cross_edition_links("hn", "2026-07-18", known) == [
        ("../../2026-07-18/", "GitHub Trending"),
    ]
    # nothing published that day -> no links at all, never a calendar fallback
    empty = {"gh": set(), "hn": set(), "ai": set()}
    assert cross_edition_links("gh", "2026-07-18", empty) == []


def test_dated_page_links_all_published_editions():
    known = {"gh": {"2026-07-18"}, "hn": {"2026-07-18"}, "ai": {"2026-07-18"}}
    assert cross_edition_links("gh", "2026-07-18", known) == [
        ("../hn/2026-07-18/", "Hacker News"),
        ("../ai/2026-07-18/", "AI News"),
    ]
    assert cross_edition_links("ai", "2026-07-18", known) == [
        ("../../2026-07-18/", "GitHub Trending"),
        ("../../hn/2026-07-18/", "Hacker News"),
    ]


def _fake_docs(tmp_path, monkeypatch):
    import editions
    monkeypatch.setattr(editions, "DOCS_DIR", tmp_path)
    (tmp_path / "2026-07-17").mkdir()
    (tmp_path / "2026-07-18").mkdir()
    (tmp_path / "hn" / "2026-07-18").mkdir(parents=True)
    (tmp_path / "ai" / "2026-07-18").mkdir(parents=True)
    (tmp_path / "ai" / "history.json").write_text("{}")  # non-date entry ignored
    return tmp_path


def test_published_dates_scans_docs(tmp_path, monkeypatch):
    _fake_docs(tmp_path, monkeypatch)
    assert published_dates("gh") == {"2026-07-17", "2026-07-18"}
    assert published_dates("hn") == {"2026-07-18"}
    assert published_dates("ai") == {"2026-07-18"}


def test_known_dates_partial_override_falls_back_to_scan(tmp_path, monkeypatch):
    _fake_docs(tmp_path, monkeypatch)
    # caller knows gh/hn from the DB; ai comes from the filesystem scan
    known = {"gh": {"2026-07-18"}, "hn": {"2026-07-18"}}
    assert cross_edition_links("gh", "2026-07-18", known) == [
        ("../hn/2026-07-18/", "Hacker News"),
        ("../ai/2026-07-18/", "AI News"),
    ]


def test_write_dates_manifest(tmp_path, monkeypatch):
    import json
    _fake_docs(tmp_path, monkeypatch)
    write_dates_manifest()
    manifest = json.loads((tmp_path / "dates.json").read_text())
    assert manifest == {
        "gh": ["2026-07-17", "2026-07-18"],
        "hn": ["2026-07-18"],
        "ai": ["2026-07-18"],
    }
    # DB-known override wins for listed editions
    write_dates_manifest({"gh": {"2026-07-19"}})
    manifest = json.loads((tmp_path / "dates.json").read_text())
    assert manifest["gh"] == ["2026-07-19"]
    assert manifest["ai"] == ["2026-07-18"]
