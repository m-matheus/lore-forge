"""Split narration text into TTS requests that end on sentence boundaries.

Chunks prefer paragraph ends, then sentence ends; a single sentence longer than the
limit (rare in this style) is split at the last comma/semicolon, then at a space.
Paragraph boundaries are kept so the narration step can insert pauses between them.
"""
from __future__ import annotations

from dataclasses import dataclass

from loreforge.services.lint_service import paragraphs, sentences


@dataclass
class Chunk:
    text: str
    paragraph_end: bool     # the chunk ends a paragraph (a pause follows)


def _split_long_sentence(sent: str, limit: int) -> list[str]:
    parts: list[str] = []
    rest = sent
    while len(rest) > limit:
        window = rest[:limit]
        cut = max(window.rfind(", "), window.rfind("; "), window.rfind(": "))
        if cut < limit // 3:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = limit
        parts.append(rest[:cut + 1].strip())
        rest = rest[cut + 1:].strip()
    if rest:
        parts.append(rest)
    return parts


def chunk_text(text: str, limit: int = 2500) -> list[Chunk]:
    """Greedy packing of sentences into chunks of at most ``limit`` characters."""
    chunks: list[Chunk] = []
    for para in paragraphs(text):
        units: list[str] = []
        for sent in sentences(para):
            units += _split_long_sentence(sent, limit) if len(sent) > limit else [sent]
        current = ""
        for unit in units:
            candidate = f"{current} {unit}".strip()
            if len(candidate) <= limit:
                current = candidate
            else:
                chunks.append(Chunk(current, paragraph_end=False))
                current = unit
        if current:
            # Merge a short paragraph into the previous chunk when it fits: fewer
            # requests, and the pause is re-inserted from the paragraph_end flags below.
            chunks.append(Chunk(current, paragraph_end=True))
    return _merge_paragraphs(chunks, limit)


def _merge_paragraphs(chunks: list[Chunk], limit: int) -> list[Chunk]:
    """Join consecutive whole paragraphs into one request, separated by a blank line.

    ElevenLabs reads a blank line as a natural paragraph pause, so merging keeps the
    request count low (~40 for two hours) without flattening the rhythm.
    """
    merged: list[Chunk] = []
    for c in chunks:
        if merged and merged[-1].paragraph_end and len(merged[-1].text) + 2 + len(c.text) <= limit:
            merged[-1] = Chunk(merged[-1].text + "\n\n" + c.text, c.paragraph_end)
        else:
            merged.append(c)
    return merged
