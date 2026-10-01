import xml.etree.ElementTree as ET
from pathlib import Path

from loreforge.services import xmeml


def _seq(tmp_path: Path) -> xmeml.Sequence:
    src = xmeml.MediaFile(path=tmp_path / "scene source.mp4", duration=9000, has_audio=True)
    rain = xmeml.MediaFile(path=tmp_path / "rain.mov", duration=300)
    narr = xmeml.MediaFile(path=tmp_path / "narration.wav", duration=6000, kind="audio", channels=1)
    v1 = xmeml.Track(clips=[
        xmeml.Clip(file=src, start=0, end=150, src_in=100),
        xmeml.Clip(file=src, start=150, end=420, src_in=3000),
        xmeml.Clip(file=src, start=420, end=700, src_in=6000),
    ], dissolves=[xmeml.Dissolve(cut=150, length=12), xmeml.Dissolve(cut=420, length=12)])
    v2 = xmeml.Track(clips=xmeml.loop_clips(rain, 700, opacity=85))
    a1 = xmeml.Track(clips=[xmeml.Clip(file=narr, start=0, end=400, src_in=0),
                            xmeml.Clip(file=narr, start=400, end=700, src_in=400)])
    return xmeml.Sequence(name="test", video_tracks=[v1, v2], audio_tracks=[a1],
                          markers=[xmeml.Marker(frame=0, name="s01"), xmeml.Marker(frame=400, name="s02")])


def test_xml_parses_and_files_are_defined_once(tmp_path):
    root = ET.fromstring(xmeml.to_xml(_seq(tmp_path)).split("\n", 2)[2])
    files = root.findall(".//file")
    full = [f for f in files if f.find("pathurl") is not None]
    assert len(full) == 3                                  # src, rain, narration
    assert len(files) == 3 + 2 + 2 + 1                     # others are id references
    ids = {f.get("id") for f in files}
    assert ids == {"file-1", "file-2", "file-3"}
    for f in full:
        assert f.find("pathurl").text.startswith("file://localhost/")
    assert "scene%20source.mp4" in full[0].find("pathurl").text


def test_clip_ranges_and_duration(tmp_path):
    seq = _seq(tmp_path)
    root = ET.fromstring(xmeml.to_xml(seq).split("\n", 2)[2])
    assert root.find("sequence/duration").text == "700"
    first = root.find(".//video/track/clipitem")
    assert [first.find(t).text for t in ("start", "end", "in", "out")] == ["0", "150", "100", "250"]
    loops = root.findall(".//video/track")[1].findall("clipitem")
    assert [int(c.find("end").text) for c in loops] == [300, 600, 700]
    assert loops[0].find(".//parameter/value").text == "85"


def test_dissolves_are_centered_on_cuts(tmp_path):
    root = ET.fromstring(xmeml.to_xml(_seq(tmp_path)).split("\n", 2)[2])
    tr = root.findall(".//video/track")[0]
    items = [c.tag for c in tr if c.tag in ("clipitem", "transitionitem")]
    assert items == ["clipitem", "transitionitem", "clipitem", "transitionitem", "clipitem"]
    t = tr.find("transitionitem")
    assert (t.find("start").text, t.find("end").text) == ("144", "156")


def test_sequence_markers(tmp_path):
    root = ET.fromstring(xmeml.to_xml(_seq(tmp_path)).split("\n", 2)[2])
    marks = root.findall("sequence/marker")
    assert [m.find("in").text for m in marks] == ["0", "400"]


def test_sequence_format_and_fill_scale(tmp_path):
    small = xmeml.MediaFile(path=tmp_path / "a720.mp4", duration=900, width=1280, height=720)
    full = xmeml.MediaFile(path=tmp_path / "rain.mov", duration=300)
    seq = xmeml.Sequence(name="s", video_tracks=[
        xmeml.Track(clips=[xmeml.Clip(file=small, start=0, end=100)]),
        xmeml.Track(clips=[xmeml.Clip(file=full, start=0, end=100)])])
    root = ET.fromstring(xmeml.to_xml(seq).split("\n", 2)[2])
    sc = root.find("sequence/media/video/format/samplecharacteristics")
    assert (sc.find("width").text, sc.find("height").text) == ("1920", "1080")
    assert sc.find("codec/name") is not None and sc.find("anamorphic").text == "FALSE"
    v1, v2 = root.findall(".//video/track")
    assert v1.find(".//effect[effectid='basic']/parameter/value").text == "150.0"
    assert v2.find(".//effect[effectid='basic']") is None      # 1080p layers stay at 100%
