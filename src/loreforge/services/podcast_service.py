"""Podcast episode: the narration and the music bed mixed into one MP3 for Spotify.

The video carries the same two layers on A1 and A2, so the episode sounds like the
video. Both are already at their loudness targets (narration_service, music_service), so
they are summed as they are and a limiter only guards the peaks. The episode description
is written by the metadata step, next to the YouTube one.

It runs before the premiere step: the episode is posted first, and its link (pasted into
the brief) is what the video's QR code opens.
"""
from __future__ import annotations

import gzip
import json
import math
import xml.etree.ElementTree as ET

from loreforge.integrations.media import probe, run_ffmpeg
from loreforge.models.project import VideoProject

BITRATE = "128k"   # ~58 MB an hour: plenty for voice and a quiet bed


def run(project: VideoProject, params: dict, on_log) -> dict:
    log = on_log
    out = project.podcast_mp3_path
    inputs = ["-i", str(project.narration_wav_path)]
    if project.music_bed_path.exists():
        inputs += ["-i", str(project.music_bed_path)]
        gain = premiere_music_gain(project)
        if gain is None:
            gain = project.channel.music.podcast_gain_db
            log(f"Music bed: {gain:+.1f} dB (channel default; no edited Premiere project)")
        else:
            log(f"Music bed: {gain:+.1f} dB, as set on A2 in the Premiere project")
        graph = ("[0:a]aformat=channel_layouts=stereo[n];"
                 f"[1:a]volume={gain:.2f}dB[m];"
                 "[n][m]amix=inputs=2:duration=first:normalize=0,"
                 "alimiter=limit=0.89:level=0[out]")
    else:
        log("No music bed yet (run the music step); the episode is the narration alone.")
        graph = "[0:a]alimiter=limit=0.89:level=0[out]"
    run_ffmpeg([*inputs, "-filter_complex", graph, "-map", "[out]",
                "-c:a", "libmp3lame", "-b:a", BITRATE, str(out)])

    minutes = probe(out).duration / 60
    log(f"Podcast episode: {minutes:.1f} min, {out.stat().st_size / 1e6:.0f} MB -> {out.name}")
    for path, step in ((project.podcast_description_path, "metadata"),
                       (project.episode_art_path, "thumbnail")):
        if not path.exists():
            log(f"No {path.name} yet: run the {step} step.")

    write_upload_sheet(project, log)
    return {"minutes": round(minutes, 1), "music": project.music_bed_path.exists()}


PREMIERE_LEVEL_OFFSET_DB = 15.0   # Premiere stores clip level as linear gain 15 dB down


def premiere_music_gain(project: VideoProject) -> float | None:
    """The gain the music bed got in the edited Premiere project, in dB, or None.

    The level is set by ear per video (Essential Sound's clip volume, or the clip's own
    Volume > Level), so the episode reads it back rather than assuming one value. Both
    are summed: they are separate stages in Premiere.
    """
    projects = sorted(project.premiere_dir.glob("*.prproj"), key=lambda p: p.stat().st_mtime)
    if not projects:
        return None
    root = ET.fromstring(gzip.decompress(projects[-1].read_bytes()))
    by_id = {el.get("ObjectID") or el.get("ObjectUID"): el for el in root}

    def deref(el):
        return by_id.get(el.get("ObjectRef") or el.get("ObjectURef")) if el is not None else None

    for item in root.iter("AudioClipTrackItem"):
        sub = deref(item.find(".//SubClip"))
        # The clip the timeline names "music bed" (or the file itself, if renamed back).
        name = (sub.findtext("Name") or "").lower() if sub is not None else ""
        if name not in ("music bed", project.music_bed_path.name):
            continue
        props = item.find(".//TrackItem/Node/Properties")
        props = {p.tag: p.text for p in props} if props is not None else {}
        gain = 0.0
        if props.get("ESP.VolumeSection.1") == "1" and props.get("ESP.VolumeSection.2"):
            gain += float(props["ESP.VolumeSection.2"])
        chain = deref(item.find(".//ComponentOwner/Components"))
        components = [deref(c) for c in chain.iter("Component")] if chain is not None else []
        params = [deref(p) for c in components if c is not None for p in c.iter("Param")]
        level = next((p for p in params if p is not None and p.findtext("Name") == "Level"
                      and p.findtext("StartKeyframe")), None)
        if level is not None:
            linear = float(level.findtext("StartKeyframe").split(",")[1])
            gain += 20 * math.log10(max(linear, 1e-9)) + PREMIERE_LEVEL_OFFSET_DB
        return round(gain, 2)
    return None


def write_upload_sheet(project: VideoProject, log) -> None:
    """Every field of Spotify's episode form, filled for this episode."""
    template = project.channel.template("podcast_upload")
    if not template:
        return
    rel = lambda p: p.relative_to(project.run_dir).as_posix()
    project.podcast_upload_path.write_text(template.format(
        title=episode_title(project), audio=rel(project.podcast_mp3_path),
        description=rel(project.podcast_description_path),
        art=rel(project.episode_art_path)), encoding="utf-8")
    log(f"Upload sheet: {rel(project.podcast_upload_path)}")


def episode_title(project: VideoProject) -> str:
    """The YouTube title from the metadata step: the same search words work on Spotify."""
    if project.metadata_path.exists():
        return json.loads(project.metadata_path.read_text(encoding="utf-8"))["title"]
    return project.title or project.game
