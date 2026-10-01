"""Prototype (b): stress-test FCP7 XML import in Premiere before building on it.

Builds a sequence shaped like a real video: V1 hundreds of short scene clips (from one
or more sources) with cross dissolves, V2/V3 looping alpha overlays (rain, film), V4
vignette PNG, V5 watermark PNG, A1 "narration" split into section clips of one WAV,
A2 a music bed, and section markers.

Usage:
    python scripts/prototype_timeline.py --footage path/a.mp4 [path/b.mp4 ...] \
        --minutes 120 --out output/_proto/proto.xml

Check in Premiere (File > Import the .xml):
  1. every clip online (no "Media Offline")        4. dissolves present on V1
  2. rain/film show with transparency (alpha)       5. section markers visible
  3. vignette + watermark stills span the sequence  6. timeline scrubs smoothly
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from loreforge.config.channels import load_channel  # noqa: E402
from loreforge.integrations.media import probe, run_ffmpeg  # noqa: E402
from loreforge.services import xmeml  # noqa: E402


def media_file(path: Path, fps: int, kind: str = "video", frames: int | None = None) -> xmeml.MediaFile:
    info = probe(path)
    return xmeml.MediaFile(
        path=path.resolve(), kind=kind,
        duration=frames if frames is not None else max(1, int(info.duration * fps)),
        width=info.width or 1920, height=info.height or 1080,
        has_audio=info.has_audio and kind != "still",
        channels=info.channels or 2, sample_rate=info.sample_rate or 48000)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--footage", nargs="+", required=True, type=Path)
    ap.add_argument("--minutes", type=float, default=120)
    ap.add_argument("--channel", default="channel-1")
    ap.add_argument("--out", type=Path, default=Path("output/_proto/proto.xml"))
    args = ap.parse_args()

    ch = load_channel(args.channel)
    fps = ch.video.fps
    total = int(args.minutes * 60 * fps)
    out_dir = args.out.resolve().parent
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(3)

    # --- Proxy audio: quiet noise stands in for narration and music (mono, 48 kHz).
    narr = out_dir / "proto_narration.wav"
    music = out_dir / "proto_music.wav"
    secs = total / fps
    if not narr.exists():
        run_ffmpeg(["-f", "lavfi", "-i", f"anoisesrc=d={secs:.0f}:c=pink:a=0.02",
                    "-ac", "1", "-ar", "48000", str(narr)])
    if not music.exists():
        run_ffmpeg(["-f", "lavfi", "-i", f"sine=f=110:d={secs:.0f}",
                    "-af", "volume=0.02", "-ac", "2", "-ar", "48000", str(music)])

    sources = [media_file(p, fps) for p in args.footage]
    edit = ch.edit
    half = edit.dissolve_frames // 2

    # --- V1: scene clips with dissolves. Source ranges keep `half` frames of handle.
    v1 = xmeml.Track()
    t = 0
    while t < total:
        cut = edit.intro_cut_seconds if t < edit.intro_seconds * fps else rng.uniform(*edit.body_cut_seconds)
        n = min(int(cut * fps), total - t)
        src = rng.choice(sources)
        max_in = max(half, src.duration - n - half)
        src_in = rng.randint(half, max_in) if max_in > half else half
        n = min(n, src.duration - src_in - half)
        v1.clips.append(xmeml.Clip(file=src, start=t, end=t + n, src_in=src_in,
                                   name=f"scene {len(v1.clips) + 1:04d}"))
        t += n
    if edit.dissolve_frames:
        v1.dissolves = [xmeml.Dissolve(cut=c.start, length=edit.dissolve_frames)
                        for c in v1.clips[1:]]

    # --- V2..: overlays (looped), vignette, watermark (stills spanning the sequence).
    video_tracks = [v1]
    for ov in ch.look.overlays:
        p = ch.path(ov.file)
        if p.exists():
            f = media_file(p, fps)
            video_tracks.append(xmeml.Track(clips=xmeml.loop_clips(
                f, total, opacity=ov.opacity * 100, name=ov.name)))
    for still, opacity in ((ch.path(ch.look.vignette), None),
                           (ch.path(ch.look.watermark.file), ch.look.watermark.opacity * 100)):
        f = media_file(still, fps, kind="still", frames=total)
        video_tracks.append(xmeml.Track(clips=[xmeml.Clip(file=f, start=0, end=total,
                                                          opacity=opacity, name=still.stem)]))

    # --- A1: narration in ~15 section clips of one WAV; A2: music bed.
    narr_f = media_file(narr, fps, kind="audio")
    bounds = sorted(rng.sample(range(fps * 60, total - fps * 60), 14))
    edges = [0, *bounds, total]
    a1 = xmeml.Track(clips=[xmeml.Clip(file=narr_f, start=a, end=b, src_in=a, name=f"s{i + 1:02d}")
                            for i, (a, b) in enumerate(zip(edges, edges[1:]))])
    a2 = xmeml.Track(clips=[xmeml.Clip(file=media_file(music, fps, kind="audio"),
                                       start=0, end=total, name="music bed")])
    markers = [xmeml.Marker(frame=a, name=f"s{i + 1:02d} section title")
               for i, a in enumerate(edges[:-1])]

    seq = xmeml.Sequence(name=args.out.stem, fps=fps, width=ch.video.width,
                         height=ch.video.height, video_tracks=video_tracks,
                         audio_tracks=[a1, a2], markers=markers)
    xmeml.write(seq, args.out)
    print(f"{args.out}  V1={len(v1.clips)} clips, {len(v1.dissolves)} dissolves, "
          f"{len(video_tracks)} video tracks, {len(markers)} markers, {secs / 60:.0f} min")


if __name__ == "__main__":
    main()
