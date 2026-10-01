"""Narration step: turn the script into audio with word-level timing.

Two hours of speech is ~40 ElevenLabs requests. Prosody is kept continuous with
request stitching (``previous_request_ids``) plus text context, and the timing comes
from ``convert_with_timestamps``, so there is no Whisper pass anywhere.

With ``TTS_PROVIDER=j1`` (the default, cheaper) none of that exists: J1 returns a bare
MP3, so speed is applied with ffmpeg and word timings are estimated from each chunk's
silences (``services/alignment.py``). The sample-count timeline below is unchanged.

Timing is built from DECODED SAMPLE COUNTS, never from the last word's end time: over
40 chunks the gap between "when the last word ends" and "when the audio file ends"
accumulates into seconds of drift, which would desynchronise captions by the end.

Everything is cached per chunk (``audio/{section}/cNN.*`` + a hash of text and voice
settings), so re-narrating one expanded section costs one section, not two hours.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from loreforge.config.settings import get_settings
from loreforge.integrations import elevenlabs_client as el
from loreforge.integrations import j1_client
from loreforge.integrations.media import probe, run_ffmpeg
from loreforge.models.project import VideoProject
from loreforge.services import alignment, lint_service
from loreforge.services.chunking import Chunk, chunk_text

SAMPLE_RATE = 48000
CHANNELS = 1


@dataclass
class RenderedChunk:
    wav: Path
    words: list[dict]
    samples: int
    pause_after: float


def _hash(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


def _voice_settings(project: VideoProject, kind: str) -> dict:
    v = project.channel.voice
    speed = v.winddown_speed if kind == "winddown" else v.speed
    return {"stability": v.stability, "similarity_boost": v.similarity_boost,
            "style": v.style, "speed": speed}


def _to_wav(mp3: Path, wav: Path, tempo: float = 1.0) -> int:
    """Decode to canonical PCM and return the sample count (the unit of all timing).

    ``tempo`` != 1 time-stretches without changing pitch, for providers that ignore
    the voice's speed setting (J1).
    """
    af = ["-af", f"atempo={tempo:.4f}"] if abs(tempo - 1.0) > 1e-3 else []
    run_ffmpeg(["-i", str(mp3), *af, "-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE),
                "-c:a", "pcm_s16le", str(wav)])
    return round(probe(wav).duration * SAMPLE_RATE)


def _silence(path: Path, seconds: float) -> None:
    if path.exists():
        return
    run_ffmpeg(["-f", "lavfi", "-i",
                f"anullsrc=r={SAMPLE_RATE}:cl={'mono' if CHANNELS == 1 else 'stereo'}",
                "-t", f"{seconds:.3f}", "-c:a", "pcm_s16le", str(path)])


def _concat(parts: list[Path], out: Path, tmp_dir: Path) -> None:
    """Concatenate identical-format WAVs without re-encoding (sample-exact)."""
    listing = tmp_dir / "concat.txt"
    listing.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
    run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(out)])


def _loudnorm(src: Path, dst: Path, target_lufs: float, log) -> None:
    """Two-pass loudnorm in linear mode: level changes, timing does not."""
    measure = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(src), "-af",
         f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    tail = measure.stderr[measure.stderr.rfind("{"):measure.stderr.rfind("}") + 1]
    try:
        m = json.loads(tail)
        af = (f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11:measured_I={m['input_i']}:"
              f"measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}:"
              f"measured_thresh={m['input_thresh']}:offset={m['target_offset']}:"
              f"linear=true:print_format=summary")
        log(f"Loudness before: {m['input_i']} LUFS -> target {target_lufs}")
    except Exception:
        af = f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11"
        log("Loudness: single-pass fallback (could not parse the measurement)")
    run_ffmpeg(["-i", str(src), "-af", af, "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS),
                "-c:a", "pcm_s16le", str(dst)])


def render_chunk(project: VideoProject, section_id: str, index: int, chunk: Chunk,
                 kind: str, context: dict, log) -> RenderedChunk:
    """Synthesize (or reuse) one chunk. ``context`` carries stitching state."""
    settings = get_settings()
    ch = project.channel
    voice_id = settings.voice_id or ch.voice.voice_id
    if not voice_id:
        raise RuntimeError("No ElevenLabs voice: set VOICE_ID in .env or voice.voice_id "
                           "in channel.json.")
    j1 = settings.tts_provider == "j1"
    # J1 has no model choice and honours no voice setting; only speed survives, locally.
    model = "j1" if j1 else (ch.voice.model or settings.elevenlabs_model)
    vs = _voice_settings(project, kind)
    if j1:
        vs = {"speed": vs["speed"]}

    out_dir = project.section_audio_dir(section_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / f"c{index:02d}"
    mp3, wav, meta_path = stem.with_suffix(".mp3"), stem.with_suffix(".wav"), stem.with_suffix(".json")
    want = _hash(chunk.text, voice_id, model, json.dumps(vs, sort_keys=True))

    if meta_path.exists() and wav.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("hash") == want:
            if meta.get("request_id"):
                context["ids"].append(meta["request_id"])
            return RenderedChunk(wav, meta["words"], meta["samples"], 0.0)

    if j1:
        audio, job_id = j1_client.synthesize(chunk.text, voice_id=voice_id)
        mp3.write_bytes(audio)
        samples = _to_wav(mp3, wav, tempo=vs["speed"])
        words = alignment.estimate_words(chunk.text, alignment.detect_silences(wav),
                                         samples / SAMPLE_RATE)
        meta_path.write_text(json.dumps({"hash": want, "job_id": job_id, "samples": samples,
                                         "chars": len(chunk.text), "words": words,
                                         "timing": "estimated"}, ensure_ascii=False),
                             encoding="utf-8")
        project.add_costs(elevenlabs_chars=len(chunk.text))
        secs = samples / SAMPLE_RATE
        log(f"    {section_id} chunk {index}: {len(chunk.text)} chars, {secs:.1f}s, "
            f"{len(words)} words, {len(words) / secs * 60:.0f} wpm (J1)")
        return RenderedChunk(wav, words, samples, 0.0)

    result = el.convert_chunk(chunk.text, voice_id=voice_id, model=model, voice_settings=vs,
                              previous_request_ids=context["ids"],
                              previous_text=context.get("prev_text"),
                              next_text=context.get("next_text"))
    mp3.write_bytes(result.audio)
    samples = _to_wav(mp3, wav)
    meta_path.write_text(json.dumps({"hash": want, "request_id": result.request_id,
                                     "samples": samples, "chars": len(chunk.text),
                                     "words": result.words}, ensure_ascii=False), encoding="utf-8")
    project.add_costs(elevenlabs_chars=len(chunk.text))
    if result.request_id:
        context["ids"].append(result.request_id)
    log(f"    {section_id} chunk {index}: {len(chunk.text)} chars, "
        f"{samples / SAMPLE_RATE:.1f}s, {len(result.words)} words")
    return RenderedChunk(wav, result.words, samples, 0.0)


def dry_run(project: VideoProject, params: dict, log) -> dict:
    """Estimate the timeline without calling the API: silent audio, estimated timings.

    Lets the rest of the pipeline (captions, chapters, the Premiere timeline) be built
    and reviewed before spending TTS credits. Word timings are spread evenly at the
    channel's words-per-minute, so they are plausible but NOT frame-accurate.
    """
    ch = project.channel
    wpm = get_settings().narration_wpm
    script = json.loads(project.script_json_path.read_text(encoding="utf-8"))
    words_out: list[dict] = []
    sections_out: list[dict] = []
    t = 0.0
    for sec in script["sections"]:
        start = t
        for para in lint_service.paragraphs(sec["text"]):
            for w in para.split():
                dur = 60.0 / wpm
                words_out.append({"word": w, "start": round(t, 3), "end": round(t + dur * 0.9, 3)})
                t += dur
            t += ch.narration.paragraph_pause
        sections_out.append({"id": sec["id"], "kind": sec["kind"], "title": sec["title"],
                             "start": round(start, 3), "end": round(t, 3)})
        t += ch.narration.section_pause
    project.audio_dir.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(["-f", "lavfi", "-i",
                f"anullsrc=r={SAMPLE_RATE}:cl={'mono' if CHANNELS == 1 else 'stereo'}",
                "-t", f"{t:.3f}", "-c:a", "pcm_s16le", str(project.narration_wav_path)])
    project.narration_words_path.write_text(json.dumps(words_out, ensure_ascii=False), encoding="utf-8")
    project.sections_path.write_text(json.dumps(sections_out, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"DRY RUN: {t / 60:.1f} min estimated at {wpm} wpm, {len(words_out)} words, "
        f"silent narration.wav written. No credits spent; re-run without dry to narrate.")
    return {"minutes": round(t / 60, 1), "words": len(words_out), "dry": True}


def run(project: VideoProject, params: dict, on_log) -> dict:
    log = on_log
    if params.get("dry"):
        return dry_run(project, params, log)
    ch = project.channel
    nar = ch.narration
    script = json.loads(project.script_json_path.read_text(encoding="utf-8"))
    sections = script["sections"]
    only = params.get("section")

    project.audio_dir.mkdir(parents=True, exist_ok=True)
    silence_dir = project.audio_dir / "_silence"
    silence_dir.mkdir(exist_ok=True)

    # Plan every chunk first so each request can see its neighbouring text.
    plan: list[tuple[dict, int, Chunk]] = []
    for sec in sections:
        for i, c in enumerate(chunk_text(sec["text"], nar.chunk_chars), start=1):
            plan.append((sec, i, c))
    log(f"{len(sections)} sections -> {len(plan)} chunks "
        f"({sum(len(c.text) for _, _, c in plan):,} characters)")

    context: dict = {"ids": []}
    parts: list[Path] = []
    cursor = 0                      # samples written so far
    words_out: list[dict] = []
    sections_out: list[dict] = []
    section_start = 0

    for n, (sec, i, c) in enumerate(plan):
        context["ids"] = context["ids"][-3:]
        context["prev_text"] = plan[n - 1][2].text[-400:] if n else None
        context["next_text"] = plan[n + 1][2].text[:400] if n + 1 < len(plan) else None
        # Unchanged chunks come back from cache without an API call, so a re-narration
        # after expanding one section only pays for that section.
        rc = render_chunk(project, sec["id"], i, c, sec["kind"], context, log)
        parts.append(rc.wav)
        for w in rc.words:
            words_out.append({"word": w["word"],
                              "start": round(cursor / SAMPLE_RATE + float(w["start"]), 3),
                              "end": round(cursor / SAMPLE_RATE + float(w["end"]), 3)})
        cursor += rc.samples

        last_of_section = (n + 1 == len(plan)) or plan[n + 1][0]["id"] != sec["id"]
        if last_of_section:
            sections_out.append({"id": sec["id"], "kind": sec["kind"], "title": sec["title"],
                                 "start": round(section_start / SAMPLE_RATE, 3),
                                 "end": round(cursor / SAMPLE_RATE, 3)})
            pause = nar.section_pause if n + 1 < len(plan) else 0.0
        else:
            pause = (nar.winddown_paragraph_pause if sec["kind"] == "winddown"
                     else nar.paragraph_pause) if c.paragraph_end else 0.0
        if pause:
            sil = silence_dir / f"{pause:.3f}.wav"
            _silence(sil, pause)
            parts.append(sil)
            cursor += round(pause * SAMPLE_RATE)
        if last_of_section:
            section_start = cursor

    raw = project.audio_dir / "narration_raw.wav"
    _concat(parts, raw, project.audio_dir)
    _loudnorm(raw, project.narration_wav_path, nar.target_lufs, log)
    run_ffmpeg(["-i", str(project.narration_wav_path), "-b:a", "192k",
                str(project.narration_mp3_path)])
    raw.unlink(missing_ok=True)

    duration = probe(project.narration_wav_path).duration
    project.narration_words_path.write_text(json.dumps(words_out, ensure_ascii=False),
                                            encoding="utf-8")
    project.sections_path.write_text(json.dumps(sections_out, indent=2, ensure_ascii=False),
                                     encoding="utf-8")
    log(f"Narration: {duration / 60:.1f} min, {len(words_out)} timed words, "
        f"{len(sections_out)} sections -> {project.narration_wav_path.name}")
    if abs(duration - cursor / SAMPLE_RATE) > 0.5:
        log(f"WARNING: timeline drift {duration - cursor / SAMPLE_RATE:+.2f}s between the "
            f"file and the computed timing.")
    return {"minutes": round(duration / 60, 1), "words": len(words_out),
            "chars": project.costs.elevenlabs_chars}
