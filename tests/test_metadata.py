from loreforge.services.metadata_service import build_chapters, timestamp


def test_timestamp_format():
    assert timestamp(0) == "0:00"
    assert timestamp(75) == "1:15"
    assert timestamp(3725) == "1:02:05"


def test_chapters_start_at_zero_and_skip_short_ones():
    sections = [{"start": 0.0, "title": "Welcome"},
                {"start": 182.0, "title": "The College"},
                {"start": 185.0, "title": "Too Short"},      # 3s after the previous: dropped
                {"start": 900.0, "title": "The Church"},
                {"start": 7195.0, "title": "Rest"}]          # 5s before the end: dropped
    lines = build_chapters(sections, total=7200.0)
    assert lines[0].startswith("0:00")
    assert [l.split(" ", 1)[1] for l in lines] == ["Welcome", "The College", "The Church"]
    assert lines[1] == "3:02 The College"
