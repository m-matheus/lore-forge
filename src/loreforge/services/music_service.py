"""Music bed: one continuous, quiet ambient track as long as the narration.

Looped with crossfades (so the seam is inaudible), faded in and out, and levelled far
below the voice. Rendering it as a single file keeps the Premiere timeline simple: one
clip on A2, no volume keyframes, nothing that depends on FCP7 XML carrying audio gain.

Before any of that, the track's own fade in and fade out are cut off. Library tracks are
mastered to be played once, so they start from silence and end in silence; crossfading
that tail against that head subtracts level from both sides and digs an audible hole at
every seam. Over two hours that is a slow pulse the listener notices even if they never
work out what it is.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from loreforge.integrations.media import probe, run_ffmpeg
from loreforge.models.project import VideoProject

SAMPLE_RATE = 48000
FADE_FLOOR_DB = 3.0         # a second this far under the track's own body is still fading
PROBE_STEP = 0.1            # resolution of the fade measurement, in seconds


def measure_fades(path: Path, *, max_fade: float = 30.0) -> tuple[float, float]:
    """How many seconds of fade-in and fade-out the track carries, measured, not guessed.

    The body of the track sets the reference: anything more than ``FADE_FLOOR_DB`` below
    the median level, at the very start or the very end, is still ramping. Returns
    (head, tail) in seconds, each capped at ``max_fade`` so a genuinely quiet intro is
    never mistaken for a fade and swallowed whole.
    """
    import numpy as np

    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "8000", "-f", "f32le", "-"],
        capture_output=True, check=True).stdout
    x = np.frombuffer(raw, dtype=np.float32)
    if x.size == 0:
        return 0.0, 0.0
    win = int(8000 * PROBE_STEP)
    frames = x[:len(x) // win * win].reshape(-1, win)
    rms = 20 * np.log10(np.sqrt((frames.astype(np.float64) ** 2).mean(axis=1)) + 1e-12)
    floor = np.median(rms) - FADE_FLOOR_DB
    loud = np.flatnonzero(rms >= floor)
    if loud.size == 0:
        return 0.0, 0.0
    head = min(loud[0] * PROBE_STEP, max_fade)
    tail = min((len(rms) - 1 - loud[-1]) * PROBE_STEP, max_fade)
    return round(float(head), 1), round(float(tail), 1)


def _source(project: VideoProject, params: dict) -> Path:
    """The music file: an explicit param, a file dropped in the run, or the channel's."""
    given = params.get("file")
    if given:
        p = Path(given)
        if not p.is_absolute():
            p = project.run_dir / given
        return p
    local = sorted(p for p in project.audio_dir.glob("music.*")
                   if p.suffix.lower() in (".mp3", ".wav", ".flac", ".m4a", ".ogg"))
    if local:
        return local[0]
    ch = project.channel
    if ch.music.file:
        return ch.path(ch.music.file)
    pool = ch.path("assets/music")
    tracks = sorted(p for p in pool.glob("*") if p.suffix.lower() in
                    (".mp3", ".wav", ".flac", ".m4a", ".ogg")) if pool.exists() else []
    if tracks:
        return tracks[0]
    raise RuntimeError(
        f"No music track. Put a royalty-free ambient loop in {pool} (or set music.file in "
        f"channel.json), or drop one at {project.audio_dir / 'music.mp3'} for this video only.")


def run(project: VideoProject, params: dict, on_log) -> dict:
    log = on_log
    ch = project.channel
    m = ch.music
    src = _source(project, params)
    if not src.exists():
        raise FileNotFoundError(f"Music track not found: {src}")
    total = probe(project.narration_wav_path).duration
    raw = probe(src).duration

    # The track's own fades are cut away, so the crossfade joins two passages at full
    # level instead of two ramps heading for silence.
    head, tail = (m.trim_head, m.trim_tail)
    if head is None or tail is None:
        found = measure_fades(src)
        head = found[0] if head is None else head
        tail = found[1] if tail is None else tail
    loop = raw - head - tail
    if head or tail:
        log(f"{src.name}: trimming {head:.1f}s of fade-in and {tail:.1f}s of fade-out "
            f"({raw:.0f}s -> {loop:.0f}s of usable loop)")
    if loop < m.crossfade * 3:
        raise RuntimeError(f"{src.name} leaves only {loop:.1f}s after its fades are "
                           f"trimmed; too short to loop with a {m.crossfade:.0f}s crossfade.")

    # acrossfade needs two inputs, so the track is chained with itself: each repetition
    # overlaps the previous one by `crossfade` seconds.
    step = loop - m.crossfade
    repeats = max(1, int((total - m.crossfade) // step) + 1)
    log(f"{src.name}: {loop:.0f}s loop x{repeats} with {m.crossfade:.0f}s crossfades "
        f"-> {total / 60:.0f} min at {m.lufs} LUFS")

    # Seeking on the input side means the trimmed head and tail are never decoded, which
    # matters when the same file is opened dozens of times.
    inputs: list[str] = []
    for _ in range(repeats):
        inputs += ["-ss", f"{head:.3f}", "-t", f"{loop:.3f}", "-i", str(src)]
    filters = []
    for i in range(repeats):
        filters.append(f"[{i}:a]aformat=sample_rates={SAMPLE_RATE}:channel_layouts=stereo[a{i}]")
    last = "a0"
    for i in range(1, repeats):
        out = f"x{i}"
        # qsin, not tri: the two sides of the seam are different passages of the track, so
        # they sum as power, not as amplitude. A linear (tri) fade holds each at 0.5 in the
        # middle and loses 3 dB there; a quarter-sine holds each at 0.707 and loses nothing.
        filters.append(f"[{last}][a{i}]acrossfade=d={m.crossfade}:c1=qsin:c2=qsin[{out}]")
        last = out
    filters.append(f"[{last}]atrim=0:{total:.3f},afade=t=in:st=0:d={m.fade_in},"
                   f"afade=t=out:st={max(0.0, total - m.fade_out):.3f}:d={m.fade_out},"
                   f"loudnorm=I={m.lufs}:TP=-2:LRA=11[out]")
    run_ffmpeg(inputs + ["-filter_complex", ";".join(filters), "-map", "[out]",
                         "-ar", str(SAMPLE_RATE), "-ac", "2", "-c:a", "pcm_s16le",
                         str(project.music_bed_path)])
    made = probe(project.music_bed_path).duration
    log(f"Music bed: {made / 60:.1f} min -> {project.music_bed_path.name}")
    return {"minutes": round(made / 60, 1), "source": src.name}
