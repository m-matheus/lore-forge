"""Premiere export: assemble the whole video as an editable FCP7 XML timeline.

V1  scene clips cut to the channel's rhythm (fast in the opening, slower in the body)
V2+ the look's overlays (rain, film damage), looped, with alpha
Vn  vignette and watermark stills spanning the sequence
A1  the narration, one clip per section of the same WAV (so sections can be nudged)
A2  the music bed
    plus a marker at each section start

Scenes are referenced with in/out points, never re-encoded. A scene is not reused
within ``edit.no_repeat_minutes``, and each reuse takes a different part of it, so a
two-hour video built from twenty minutes of footage still doesn't feel looped.
"""
from __future__ import annotations

import json
import random

from loreforge.config.channels import Channel
from loreforge.integrations.media import probe
from loreforge.models.project import VideoProject
from loreforge.services import xmeml


def _media_file(path, fps: int, kind: str = "video", frames: int | None = None) -> xmeml.MediaFile:
    info = probe(path)
    return xmeml.MediaFile(
        path=path.resolve(), kind=kind,
        duration=frames if frames is not None else max(1, int(info.duration * fps)),
        width=info.width or 1920, height=info.height or 1080,
        has_audio=info.has_audio and kind != "still",
        channels=info.channels or 2, sample_rate=info.sample_rate or 48000)


def build_video_track(project: VideoProject, scenes: list[dict], total: int, fps: int,
                      ch: Channel, log) -> xmeml.Track:
    """Lay scene excerpts across the timeline with the channel's cut rhythm."""
    edit = ch.edit
    rng = random.Random(f"{project.run_id}")
    handle = max(edit.dissolve_frames // 2 + 1, 2)
    no_repeat = edit.no_repeat_minutes * 60 * fps
    files: dict[str, xmeml.MediaFile] = {}
    last_used: dict[int, int] = {}          # scene index -> timeline frame last used
    offsets: dict[int, int] = {}            # scene index -> next source offset to take

    track = xmeml.Track()
    t = 0
    while t < total:
        want = (edit.intro_cut_seconds if t < edit.intro_seconds * fps
                else rng.uniform(*edit.body_cut_seconds))
        length = min(int(want * fps), total - t)

        # Prefer scenes unused for the longest time, with a little randomness so the
        # order isn't identical every run.
        candidates = sorted(range(len(scenes)),
                            key=lambda i: (last_used.get(i, -no_repeat), rng.random()))
        pick = None
        for i in candidates:
            scene = scenes[i]
            frames_avail = int((scene["end"] - scene["start"]) * fps) - 2 * handle
            if frames_avail < length:
                continue
            if t - last_used.get(i, -no_repeat) < no_repeat and len(scenes) > 12:
                continue
            pick = i
            break
        if pick is None:                    # nothing long enough: take the longest scene
            pick = max(range(len(scenes)), key=lambda i: scenes[i]["end"] - scenes[i]["start"])
            length = min(length, int((scenes[pick]["end"] - scenes[pick]["start"]) * fps) - 2 * handle)
            if length < fps:
                raise RuntimeError("No scene is long enough for a single cut.")

        scene = scenes[pick]
        src_path = project.run_dir / scene["source"]
        f = files.get(scene["source"])
        if f is None:
            f = files[scene["source"]] = _media_file(src_path, fps)
        scene_in = int(scene["start"] * fps) + handle
        scene_len = int((scene["end"] - scene["start"]) * fps) - 2 * handle
        offset = offsets.get(pick, 0)
        if offset + length > scene_len:     # wrap around to reuse a different part
            offset = 0
        offsets[pick] = offset + length
        last_used[pick] = t

        track.clips.append(xmeml.Clip(file=f, start=t, end=t + length,
                                      src_in=scene_in + offset,
                                      name=f"{src_path.stem} #{scene['index']}"))
        t += length

    if edit.dissolve_frames:
        track.dissolves = [xmeml.Dissolve(cut=c.start, length=edit.dissolve_frames)
                           for c in track.clips[1:]]
    log(f"V1: {len(track.clips)} clips from {len(files)} source file(s), "
        f"{len(set(last_used))} distinct scenes, {edit.dissolve_frames}-frame dissolves")
    return track


def run(project: VideoProject, params: dict, on_log) -> dict:
    log = on_log
    ch = project.channel
    fps = ch.video.fps

    scenes = [s for s in json.loads(project.scenes_path.read_text(encoding="utf-8")) if s["keep"]]
    if not scenes:
        raise RuntimeError("No usable scenes: re-run footage, or relax the filters in "
                           "footage_service (MAX_MEAN_LUMA, MAX_MOTION, MAX_HUD_SCORE).")
    sections = json.loads(project.sections_path.read_text(encoding="utf-8"))
    narration = _media_file(project.narration_wav_path, fps, kind="audio")
    total = narration.duration
    log(f"Timeline: {total / fps / 60:.1f} min, {len(scenes)} usable scenes "
        f"({sum(s['end'] - s['start'] for s in scenes) / 60:.1f} min of footage)")

    video_tracks = [build_video_track(project, scenes, total, fps, ch, log)]
    for ov in ch.look.overlays:
        path = ch.path(ov.file)
        if not path.exists():
            log(f"Overlay '{ov.name}' missing ({path}); run: loreforge channel prepare")
            continue
        f = _media_file(path, fps)
        video_tracks.append(xmeml.Track(clips=xmeml.loop_clips(
            f, total, opacity=ov.opacity * 100, name=ov.name)))
    for still, opacity in ((ch.path(ch.look.vignette), None),
                           (ch.path(ch.look.watermark.file), ch.look.watermark.opacity * 100)):
        if still.exists():
            f = _media_file(still, fps, kind="still", frames=total)
            video_tracks.append(xmeml.Track(clips=[
                xmeml.Clip(file=f, start=0, end=total, opacity=opacity, name=still.stem)]))

    a1 = xmeml.Track()
    markers = []
    for sec in sections:
        start = int(round(sec["start"] * fps))
        end = min(total, int(round(sec["end"] * fps)))
        if end <= start:
            continue
        a1.clips.append(xmeml.Clip(file=narration, start=start, end=end, src_in=start,
                                   name=f"{sec['id']} {sec['title']}"))
        markers.append(xmeml.Marker(frame=start, name=f"{sec['id']} {sec['title']}",
                                    comment=sec["kind"]))
    audio_tracks = [a1]
    if project.music_bed_path.exists():
        bed = _media_file(project.music_bed_path, fps, kind="audio")
        audio_tracks.append(xmeml.Track(clips=[
            xmeml.Clip(file=bed, start=0, end=min(total, bed.duration), name="music bed")]))
    else:
        log("No music bed yet (run the music step); A2 will be empty.")

    seq = xmeml.Sequence(name=project.title or project.run_id, fps=fps, width=ch.video.width,
                         height=ch.video.height, video_tracks=video_tracks,
                         audio_tracks=audio_tracks, markers=markers)
    xmeml.write(seq, project.premiere_project_path)
    log(f"Premiere project: {project.premiere_project_path}")
    log(f"  {len(video_tracks)} video tracks, {len(audio_tracks)} audio tracks, "
        f"{len(markers)} section markers")
    log("  In Premiere: add an adjustment layer over V1 with the channel LUT preset "
        "(see README).")
    return {"minutes": round(total / fps / 60, 1), "clips": len(video_tracks[0].clips),
            "tracks": len(video_tracks) + len(audio_tracks), "markers": len(markers)}
