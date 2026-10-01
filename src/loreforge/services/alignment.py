"""Word timings for TTS audio that comes without them (J1 returns only an MP3).

No Whisper: the text is known exactly, only the timing is missing. Each chunk's audio
is scanned for silences, and the pauses are matched to the text's punctuation by a
global alignment (``_anchor``) that keeps every stretch between two anchors near the
chunk's average speaking rate. Words between anchors are spread over the SPEECH time
(silences skipped) by length.

Captions are grouped by sentence, so sentence-level anchoring is what keeps them in
sync; a single word can be a few hundred milliseconds off, a cue should not be.
"""
from __future__ import annotations

import math
import re
import subprocess
from pathlib import Path

_SENTENCE_END = re.compile(r"[.!?…][\"'”’)\]]*$")
_CLAUSE_END = re.compile(r"[,;:—–][\"'”’)\]]*$")
_WORD_OVERHEAD = 1.5        # per-word cost in "letters": short words still take time
# Alignment costs (units: squared standard deviations of the speaking rate).
_RATE_SIGMA = 0.18          # sentence-to-sentence spread of the speaking rate (log)
_RATE_SIGMA_SHORT = 0.8     # extra spread for short segments (divided by sqrt(letters))
_MISS_COST = {1: 0.4, 2: 4.0}   # punctuation with no pause: comma / sentence end
_DROP_BASE, _DROP_PER_SEC = 1.0, 8.0   # a pause matched to no punctuation
_MAX_PAUSE_SKIP, _MAX_GAP_SKIP = 5, 12


def detect_silences(wav: Path, *, noise_db: float = -40.0,
                    min_seconds: float = 0.18) -> list[tuple[float, float]]:
    """``[(start, end)]`` of every silence in ``wav`` (ffmpeg silencedetect)."""
    res = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(wav), "-af",
         f"silencedetect=noise={noise_db}dB:d={min_seconds}", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    out: list[tuple[float, float]] = []
    start: float | None = None
    for line in res.stderr.splitlines():
        if m := re.search(r"silence_start: (-?[\d.]+)", line):
            start = max(0.0, float(m.group(1)))
        elif (m := re.search(r"silence_end: ([\d.]+)", line)) and start is not None:
            out.append((start, float(m.group(1))))
            start = None
    if start is not None:
        out.append((start, float("inf")))     # trailing silence; clipped by the caller
    return out


def _weight(word: str) -> float:
    return len(re.sub(r"\W", "", word)) + _WORD_OVERHEAD


def _speech(silences: list[tuple[float, float]], duration: float) -> list[tuple[float, float]]:
    """Complement of the silences inside [0, duration]."""
    out, t = [], 0.0
    for a, b in sorted(silences):
        a, b = max(0.0, a), min(duration, b)
        if a > t:
            out.append((t, a))
        t = max(t, b)
    if t < duration:
        out.append((t, duration))
    return out or [(0.0, duration)]


class _Timeline:
    def __init__(self, speech: list[tuple[float, float]]):
        self.speech = speech

    def between(self, t0: float, t1: float) -> float:
        return sum(max(0.0, min(b, t1) - max(a, t0)) for a, b in self.speech)

    def advance(self, t0: float, seconds: float, *, as_start: bool = False) -> float:
        """Real time after ``seconds`` of speech from ``t0``, skipping silences.

        ``as_start``: a result that lands exactly on the end of a speech interval moves
        to the start of the next one (a word starts after a pause, not before it).
        """
        left = seconds
        last = t0
        for a, b in self.speech:
            if b <= t0:
                continue
            a = max(a, t0)
            span = b - a
            if left < span or (left == span and not as_start):
                return a + left
            left -= span
            last = b
        return last


def _pause_kind(word: str) -> int:
    """0 = no punctuation, 1 = clause end, 2 = sentence end."""
    if _SENTENCE_END.search(word):
        return 2
    return 1 if _CLAUSE_END.search(word) else 0


def _anchor(words: list[str], weights: list[float], tl: _Timeline,
            pauses: list[tuple[float, float]]) -> list[tuple[int, float, float]]:
    """Match detected pauses to punctuation gaps: ``[(word_index, pause_start, pause_end)]``.

    Dynamic programming over (pause, gap) pairs. A segment between two anchors costs how
    far its speaking rate is from the chunk's average; an unmatched sentence end costs a
    lot, an unmatched comma little, a discarded pause more the longer it is. The global
    optimum cannot drift the way a greedy nearest-pause snap does.
    """
    gaps = [i for i, w in enumerate(words[:-1]) if _pause_kind(w)]
    kinds = [_pause_kind(words[i]) for i in gaps]
    wp = [0.0]
    for w in weights:
        wp.append(wp[-1] + w)
    speech_start, speech_end = tl.speech[0][0], tl.speech[-1][1]
    rate = tl.between(speech_start, speech_end) / wp[-1]
    # Virtual anchors at both ends: gap -1 / "pause" ending at speech start, and the last
    # word / "pause" starting at speech end.
    G = [-1, *gaps, len(words) - 1]
    K = [0, *kinds, 0]
    P = [(speech_start, speech_start), *pauses, (speech_end, speech_end)]
    cum_speech = [tl.between(speech_start, a) for a, _ in P]   # speech before pause start
    after = [tl.between(speech_start, b) for _, b in P]         # ... before pause end
    unmatched = [0.0, *[_MISS_COST[k] for k in K[1:-1]], 0.0]
    miss_prefix = [0.0]
    for c in unmatched:
        miss_prefix.append(miss_prefix[-1] + c)
    drop = [0.0, *[_DROP_BASE + _DROP_PER_SEC * (b - a) for a, b in pauses], 0.0]
    drop_prefix = [0.0]
    for c in drop:
        drop_prefix.append(drop_prefix[-1] + c)

    n_g, n_p = len(G), len(P)
    best: dict[tuple[int, int], tuple[float, tuple[int, int] | None]] = {(0, 0): (0.0, None)}
    end = (n_p - 1, n_g - 1)
    for k in range(n_p - 1):
        for j in range(n_g - 1):
            cur = best.get((k, j))
            if cur is None:
                continue
            # Inner pauses/gaps (the end anchor excluded), plus the end from anywhere so
            # the search window can never make the end unreachable.
            nexts = [(k2, j2) for k2 in range(k + 1, min(n_p - 1, k + 1 + _MAX_PAUSE_SKIP))
                     for j2 in range(j + 1, min(n_g - 1, j + 1 + _MAX_GAP_SKIP))]
            for k2, j2 in [*nexts, end]:
                d = cum_speech[k2] - after[k]
                w = wp[G[j2] + 1] - wp[G[j] + 1]
                if d <= 0 or w <= 0:
                    continue
                sigma = _RATE_SIGMA + _RATE_SIGMA_SHORT / math.sqrt(w)
                cost = (cur[0] + (math.log(d / (w * rate)) / sigma) ** 2
                        + drop_prefix[k2] - drop_prefix[k + 1]
                        + miss_prefix[j2] - miss_prefix[j + 1])
                prev = best.get((k2, j2))
                if prev is None or cost < prev[0]:
                    best[(k2, j2)] = (cost, (k, j))
    if end not in best:
        return []
    out: list[tuple[int, float, float]] = []
    node = best[end][1]
    while node != (0, 0):
        k, j = node
        out.append((G[j], *P[k]))
        node = best[node][1]
    return out[::-1]


def estimate_words(text: str, silences: list[tuple[float, float]],
                   duration: float) -> list[dict]:
    """``[{"word", "start", "end"}]`` for ``text`` spoken over ``duration`` seconds."""
    words = text.split()
    if not words or duration <= 0:
        return []
    tl = _Timeline(_speech(silences, duration))
    speech_start, speech_end = tl.speech[0][0], tl.speech[-1][1]
    pauses = [(a, min(b, duration)) for a, b in sorted(silences)
              if speech_start < a < speech_end]
    weights = [_weight(w) for w in words]

    # Segments between anchors: (first word, last word, start time, end time).
    bounds = [(-1, speech_start, speech_start), *_anchor(words, weights, tl, pauses),
              (len(words) - 1, speech_end, speech_end)]
    out: list[dict] = []
    for (i0, _, t0), (i1, t1, _) in zip(bounds, bounds[1:]):
        seg = range(i0 + 1, i1 + 1)
        w_seg = sum(weights[i] for i in seg)
        span = tl.between(t0, t1)
        cum = 0.0
        for i in seg:
            start = tl.advance(t0, span * cum / w_seg, as_start=True)
            cum += weights[i]
            end = tl.advance(t0, span * cum / w_seg)
            out.append({"word": words[i], "start": round(start, 3),
                        "end": round(max(end, start), 3)})
    return out
