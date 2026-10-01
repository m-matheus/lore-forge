"""Minimal Final Cut Pro 7 XML (XMEML v4) writer, tuned for Premiere Pro import.

Why not OTIO (as in cut-forge): a 2-hour sleep video has ~700 scene clips that
reference a handful of source files, overlay loops on several tracks, still images,
opacity values and dissolves. XMEML lets a ``<file id>`` be fully described ONCE and
referenced by id afterwards (OTIO's adapter repeats the definition per clip), and we
need ``<filter>`` opacity and ``<transitionitem>``, which the adapter does not expose.

Lessons carried over from cut-forge's OTIO round-trips:
- Frame values are integers at the sequence timebase: ``round(seconds * fps)``.
- ``pathurl`` must be ``file://localhost/C:/...`` on Windows (see ``media.file_url``).
- The sequence must declare width/height, or Premiere guesses and rescales clips.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from loreforge.integrations.media import file_url


@dataclass
class MediaFile:
    """A source file. ``kind`` is "video" (may also carry audio), "audio" or "still"."""
    path: Path
    duration: int                       # frames at the sequence timebase
    kind: str = "video"
    width: int = 1920
    height: int = 1080
    has_audio: bool = False
    channels: int = 2
    sample_rate: int = 48000
    id: str = ""                        # assigned by the Sequence


@dataclass
class Marker:
    frame: int
    name: str
    comment: str = ""


@dataclass
class Clip:
    file: MediaFile
    start: int                          # timeline in (frames)
    end: int                            # timeline out (exclusive)
    src_in: int = 0                     # source in (frames)
    name: str = ""
    opacity: float | None = None        # 0..100, video only
    markers: list[Marker] = field(default_factory=list)

    @property
    def src_out(self) -> int:
        return self.src_in + (self.end - self.start)


@dataclass
class Dissolve:
    """Cross dissolve centred on the cut at ``cut`` frames, ``length`` frames long."""
    cut: int
    length: int


@dataclass
class Track:
    clips: list[Clip] = field(default_factory=list)
    dissolves: list[Dissolve] = field(default_factory=list)


@dataclass
class Sequence:
    name: str
    fps: int = 30
    width: int = 1920
    height: int = 1080
    video_tracks: list[Track] = field(default_factory=list)   # V1 first (bottom)
    audio_tracks: list[Track] = field(default_factory=list)
    markers: list[Marker] = field(default_factory=list)

    @property
    def duration(self) -> int:
        ends = [c.end for t in self.video_tracks + self.audio_tracks for c in t.clips]
        return max(ends, default=0)


# --- Serialization -------------------------------------------------------------------

def _sub(parent: ET.Element, tag: str, text=None, **attrs) -> ET.Element:
    el = ET.SubElement(parent, tag, {k: str(v) for k, v in attrs.items()})
    if text is not None:
        el.text = str(text)
    return el


def _rate(parent: ET.Element, fps: int) -> None:
    r = _sub(parent, "rate")
    _sub(r, "timebase", fps)
    _sub(r, "ntsc", "FALSE")


def _marker(parent: ET.Element, m: Marker) -> None:
    el = _sub(parent, "marker")
    _sub(el, "comment", m.comment)
    _sub(el, "name", m.name)
    _sub(el, "in", m.frame)
    _sub(el, "out", -1)


def _file_element(parent: ET.Element, f: MediaFile, seq: Sequence, defined: set[str]) -> None:
    if f.id in defined:
        _sub(parent, "file", id=f.id)
        return
    defined.add(f.id)
    el = _sub(parent, "file", id=f.id)
    _sub(el, "name", f.path.name)
    _sub(el, "pathurl", file_url(f.path))
    _rate(el, seq.fps)
    _sub(el, "duration", f.duration)
    media = _sub(el, "media")
    if f.kind in ("video", "still"):
        v = _sub(media, "video")
        sc = _sub(v, "samplecharacteristics")
        _rate(sc, seq.fps)
        _sub(sc, "width", f.width)
        _sub(sc, "height", f.height)
        _sub(sc, "pixelaspectratio", "square")
        _sub(sc, "fielddominance", "none")
    if f.kind == "audio" or f.has_audio:
        a = _sub(media, "audio")
        sc = _sub(a, "samplecharacteristics")
        _sub(sc, "depth", 16)
        _sub(sc, "samplerate", f.sample_rate)
        _sub(a, "channelcount", f.channels)


def _opacity_filter(parent: ET.Element, value: float) -> None:
    flt = _sub(parent, "filter")
    eff = _sub(flt, "effect")
    _sub(eff, "name", "Opacity")
    _sub(eff, "effectid", "opacity")
    _sub(eff, "effectcategory", "motion")
    _sub(eff, "effecttype", "motion")
    _sub(eff, "mediatype", "video")
    p = _sub(eff, "parameter", authoringApp="PremierePro")
    _sub(p, "parameterid", "opacity")
    _sub(p, "name", "Opacity")
    _sub(p, "valuemin", 0)
    _sub(p, "valuemax", 100)
    _sub(p, "value", round(value, 2))


def fill_scale(f: MediaFile, seq: Sequence) -> float:
    """Percent scale that makes ``f`` cover the sequence frame (720p source -> 150)."""
    if not f.width or not f.height:
        return 100.0
    return round(max(seq.width / f.width, seq.height / f.height) * 100, 3)


def _scale_filter(parent: ET.Element, value: float) -> None:
    """Basic Motion scale. Premiere places clips at native size; without this a 720p
    source sits as a small box in a 1080p sequence (seen in the first import test)."""
    flt = _sub(parent, "filter")
    eff = _sub(flt, "effect")
    _sub(eff, "name", "Basic Motion")
    _sub(eff, "effectid", "basic")
    _sub(eff, "effectcategory", "motion")
    _sub(eff, "effecttype", "motion")
    _sub(eff, "mediatype", "video")
    p = _sub(eff, "parameter", authoringApp="PremierePro")
    _sub(p, "parameterid", "scale")
    _sub(p, "name", "Scale")
    _sub(p, "valuemin", 0)
    _sub(p, "valuemax", 1000)
    _sub(p, "value", value)


def _sequence_format(video: ET.Element, seq: Sequence) -> None:
    """The sequence frame, in the full shape Premiere's own FCP7 export writes.

    With only width/height (first import test) Premiere ignored the block and built the
    sequence from its default preset (4K there), shrinking every 1080p layer to a
    quarter of the frame. The codec + anamorphic + colordepth fields make it honour the
    declared size.
    """
    fmt = _sub(video, "format")
    sc = _sub(fmt, "samplecharacteristics")
    _rate(sc, seq.fps)
    codec = _sub(sc, "codec")
    _sub(codec, "name", "Apple ProRes 422")
    asd = _sub(codec, "appspecificdata")
    _sub(asd, "appname", "Final Cut Pro")
    _sub(asd, "appmanufacturer", "Apple Inc.")
    _sub(asd, "appversion", "7.0")
    qt = _sub(_sub(asd, "data"), "qtcodec")
    for tag, value in (("codecname", "Apple ProRes 422"), ("codectypename", "Apple ProRes 422"),
                       ("codectypecode", "apcn"), ("codecvendorcode", "appl"),
                       ("spatialquality", 1024), ("temporalquality", 0),
                       ("keyframerate", 0), ("datarate", 0)):
        _sub(qt, tag, value)
    _sub(sc, "width", seq.width)
    _sub(sc, "height", seq.height)
    _sub(sc, "anamorphic", "FALSE")
    _sub(sc, "pixelaspectratio", "square")
    _sub(sc, "fielddominance", "none")
    _sub(sc, "colordepth", 24)


def _dissolve(parent: ET.Element, d: Dissolve, fps: int) -> None:
    half = d.length // 2
    el = _sub(parent, "transitionitem")
    _rate(el, fps)
    _sub(el, "start", d.cut - half)
    _sub(el, "end", d.cut - half + d.length)
    _sub(el, "alignment", "center")
    eff = _sub(el, "effect")
    _sub(eff, "name", "Cross Dissolve")
    _sub(eff, "effectid", "Cross Dissolve")
    _sub(eff, "effectcategory", "Dissolve")
    _sub(eff, "effecttype", "transition")
    _sub(eff, "mediatype", "video")
    _sub(eff, "wipecode", 0)
    _sub(eff, "wipeaccuracy", 100)
    _sub(eff, "startratio", 0)
    _sub(eff, "endratio", 1)
    _sub(eff, "reverse", "FALSE")


def _clipitem(parent: ET.Element, c: Clip, seq: Sequence, media: str, track_index: int,
              clip_id: str, defined: set[str]) -> None:
    el = _sub(parent, "clipitem", id=clip_id)
    _sub(el, "name", c.name or c.file.path.name)
    _sub(el, "enabled", "TRUE")
    _sub(el, "duration", c.file.duration)
    _rate(el, seq.fps)
    _sub(el, "start", c.start)
    _sub(el, "end", c.end)
    _sub(el, "in", c.src_in)
    _sub(el, "out", c.src_out)
    _file_element(el, c.file, seq, defined)
    if media == "video":
        _sub(el, "compositemode", "normal")
        scale = fill_scale(c.file, seq)
        if abs(scale - 100) > 0.01:
            _scale_filter(el, scale)
        if c.opacity is not None and c.opacity < 100:
            _opacity_filter(el, c.opacity)
    st = _sub(el, "sourcetrack")
    _sub(st, "mediatype", media)
    _sub(st, "trackindex", 1)
    for m in c.markers:
        _marker(el, m)


def to_xml(seq: Sequence) -> str:
    """Serialize the sequence to an XMEML string."""
    files: dict[Path, MediaFile] = {}
    for t in seq.video_tracks + seq.audio_tracks:
        for c in t.clips:
            files.setdefault(c.file.path.resolve(), c.file)
    for i, f in enumerate(files.values(), start=1):
        f.id = f"file-{i}"

    root = ET.Element("xmeml", version="4")
    s = _sub(root, "sequence", id="sequence-1")
    _sub(s, "name", seq.name)
    _sub(s, "duration", seq.duration)
    _rate(s, seq.fps)
    tc = _sub(s, "timecode")
    _rate(tc, seq.fps)
    _sub(tc, "string", "00:00:00:00")
    _sub(tc, "frame", 0)
    _sub(tc, "displayformat", "NDF")
    media = _sub(s, "media")

    defined: set[str] = set()
    clip_n = 0

    video = _sub(media, "video")
    _sequence_format(video, seq)
    for ti, track in enumerate(seq.video_tracks, start=1):
        t = _sub(video, "track")
        items: list[tuple[int, int, object]] = [(c.start, 1, c) for c in track.clips]
        items += [(d.cut - d.length // 2, 0, d) for d in track.dissolves]
        for _, _, item in sorted(items, key=lambda x: (x[0], x[1])):
            if isinstance(item, Dissolve):
                _dissolve(t, item, seq.fps)
            else:
                clip_n += 1
                _clipitem(t, item, seq, "video", ti, f"clipitem-{clip_n}", defined)
        _sub(t, "enabled", "TRUE")
        _sub(t, "locked", "FALSE")

    audio = _sub(media, "audio")
    _sub(audio, "numOutputChannels", 2)
    afmt = _sub(audio, "format")
    asc = _sub(afmt, "samplecharacteristics")
    _sub(asc, "depth", 16)
    _sub(asc, "samplerate", 48000)
    for ti, track in enumerate(seq.audio_tracks, start=1):
        t = _sub(audio, "track")
        for c in track.clips:
            clip_n += 1
            _clipitem(t, c, seq, "audio", ti, f"clipitem-{clip_n}", defined)
        _sub(t, "enabled", "TRUE")
        _sub(t, "locked", "FALSE")

    for m in seq.markers:
        _marker(s, m)

    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' + body + "\n"


def write(seq: Sequence, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_xml(seq), encoding="utf-8")
    return path


def loop_clips(file: MediaFile, total: int, *, opacity: float | None = None,
               name: str = "") -> list[Clip]:
    """Back-to-back copies of ``file`` covering ``total`` frames (overlay loops, stills)."""
    clips, t = [], 0
    while t < total:
        n = min(file.duration, total - t)
        clips.append(Clip(file=file, start=t, end=t + n, src_in=0, name=name, opacity=opacity))
        t += n
    return clips
