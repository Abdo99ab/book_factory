from pipeline.catalogue import (
    REQUIRED_TITLE_KEYS,
    check_band_grammar,
    list_titles,
    load_catalogue,
    load_story_beats,
)


def test_catalogue_loads_ten_titles():
    data = load_catalogue()
    assert len(data["titles"]) == 10
    assert len(list_titles()) == 10


def test_every_title_has_required_keys():
    data = load_catalogue()
    for title_id, entry in data["titles"].items():
        for key in REQUIRED_TITLE_KEYS:
            assert key in entry, f"{title_id} missing {key}"
        assert entry["title_id"] == title_id


def test_band_grammar():
    data = load_catalogue()
    for entry in data["titles"].values():
        assert check_band_grammar(entry["band"], entry["family"]), entry["title_id"]


def test_big_kid_list_schedule_length():
    entry = load_catalogue()["titles"]["big_kid_list"]
    schedule = entry["anchor"]["state_schedule"]
    assert schedule is not None
    assert len(schedule) == 14


def test_story_beats_fourteen():
    for title_id in load_catalogue()["titles"]:
        beats = load_story_beats(title_id)
        assert len(beats["beats"]) == 14
