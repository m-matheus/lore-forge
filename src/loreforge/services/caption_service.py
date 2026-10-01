"""Captions from the narration's word timings: SRT for YouTube + Premiere transcript.

Grouped by sentence rather than word by word: a sleep video's captions should sit
still and be readable, not flicker. Cues are at most two lines of ~42 characters and
never shorter than a second.
"""
from __future__ import annotations

import json
import re

from loreforge.models.project import VideoProject

MAX_CHARS_PER_LINE = 42
MAX_LINES = 2
MAX_SECONDS = 6.0
MIN_SECONDS = 1.0
_SENTENCE_END = re.compile(r"[.!?…][\"')\]]?$")
# A clause end is the next best place to break when a sentence is too long for one cue.
_CLAUSE_END = re.compile(r"[,;:—–][\"')\]]?$")
_SPEAKER_ID = "spk_0"
_PREMIERE_LANGS = {"en": "en-us", "es": "es-es", "pt": "pt-br"}


def seconds_to_srt_time(seconds: float) -> str:
    """SRT timestamp: HH:MM:SS,mmm."""
    seconds = max(0.0, seconds)
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    ms = round((seconds - int(seconds)) * 1000)
    if ms == 1000:
        s, ms = s + 1, 0
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def group_words(words: list[dict]) -> list[list[dict]]:
    """Group timed words into caption-sized cues, breaking at sentence ends."""
    cues: list[list[dict]] = []
    current: list[dict] = []
    for w in words:
        candidate = current + [w]
        text_len = len(" ".join(x["word"] for x in candidate))
        too_long = text_len > MAX_CHARS_PER_LINE * MAX_LINES
        too_slow = candidate[-1]["end"] - candidate[0]["start"] > MAX_SECONDS
        if current and (too_long or too_slow):
            # Break at the last clause end instead of mid-phrase, as long as that keeps
            # both halves reasonable ("...Channel One, | and to a long, quiet night...").
            split = next((i + 1 for i in range(len(current) - 1, 2, -1)
                          if _CLAUSE_END.search(current[i]["word"])), None)
            if split:
                cues.append(current[:split])
                current = current[split:] + [w]
            else:
                cues.append(current)
                current = [w]
        else:
            current = candidate
        if _SENTENCE_END.search(w["word"]):
            cues.append(current)
            current = []
    if current:
        cues.append(current)
    return [c for c in cues if c]


def wrap_lines(text: str) -> list[str]:
    """Balance a cue over at most two lines of ~42 characters."""
    if len(text) <= MAX_CHARS_PER_LINE:
        return [text]
    words = text.split()
    best, best_cost = None, None
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        cost = max(len(a), len(b)) + abs(len(a) - len(b)) * 0.25
        if _CLAUSE_END.search(words[i - 1]):
            cost -= 12          # prefer wrapping right after a comma or dash
        if len(a) <= MAX_CHARS_PER_LINE and (best_cost is None or cost < best_cost):
            best, best_cost = (a, b), cost
    if best is None:
        return [text]
    return [best[0], best[1]]


def build_srt(words: list[dict]) -> str:
    blocks = []
    cues = group_words(words)
    for i, cue in enumerate(cues, start=1):
        text = " ".join(w["word"] for w in cue).strip()
        start = float(cue[0]["start"])
        end = max(float(cue[-1]["end"]), start + MIN_SECONDS)
        if i < len(cues):
            end = min(end, float(cues[i][0]["start"]))     # never overlap the next cue
        blocks.append(f"{i}\n{seconds_to_srt_time(start)} --> {seconds_to_srt_time(end)}\n"
                      + "\n".join(wrap_lines(text)))
    return "\n\n".join(blocks) + "\n"


def build_premiere_transcript(words: list[dict], *, language: str = "en",
                              speaker_name: str = "Narration") -> dict:
    """Adobe Premiere transcript (schema v1.0.0) for Text panel > Import transcript."""
    lang = _PREMIERE_LANGS.get(language, language if "-" in language else "en-us")
    segments = []
    for cue in group_words(words):
        objs = []
        for i, w in enumerate(cue):
            start = max(0.0, float(w["start"]))
            objs.append({"confidence": 1.0, "duration": round(max(0.0, float(w["end"]) - start), 3),
                         "eos": i == len(cue) - 1, "start": round(start, 3), "tags": [],
                         "text": w["word"], "type": "word"})
        seg_start = max(0.0, float(cue[0]["start"]))
        seg_end = max(seg_start, float(cue[-1]["end"]))
        segments.append({"duration": round(seg_end - seg_start, 3), "language": lang,
                         "speaker": _SPEAKER_ID, "start": round(seg_start, 3), "words": objs})
    return {"language": lang, "segments": segments,
            "speakers": [{"id": _SPEAKER_ID, "name": speaker_name}]}


def run(project: VideoProject, params: dict, on_log) -> dict:
    words = json.loads(project.narration_words_path.read_text(encoding="utf-8"))
    project.captions_srt_path.write_text(build_srt(words), encoding="utf-8")
    transcript = build_premiere_transcript(words, language=project.channel.language)
    project.premiere_transcript_path.write_text(
        json.dumps(transcript, ensure_ascii=False, indent=1), encoding="utf-8")
    cues = len(transcript["segments"])
    on_log(f"Captions: {cues} cues from {len(words)} words -> "
           f"{project.captions_srt_path.name} + {project.premiere_transcript_path.name}")
    return {"cues": cues}
