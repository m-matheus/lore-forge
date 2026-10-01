"""ElevenLabs text-to-speech with timestamps and request stitching.

A 2-hour narration is ~40 requests of <= 2,500 characters. To keep prosody continuous
across them, each request passes the ids of the previous requests
(``previous_request_ids``, max 3, valid ~2 hours) AND the neighbouring text
(``previous_text`` / ``next_text``). The text context keeps working when a single
section is re-narrated days later and the ids have expired.

Stitching requires ``eleven_multilingual_v2`` (or flash/turbo v2.5); ``eleven_v3`` does
not support it and also caps requests at 5k characters.
"""
from __future__ import annotations

import base64
import time
from dataclasses import dataclass

from loreforge.config.settings import get_settings


@dataclass
class ChunkAudio:
    audio: bytes            # mp3_44100_128
    words: list[dict]       # [{"word", "start", "end"}], relative to the chunk
    request_id: str


def _client():
    from elevenlabs.client import ElevenLabs

    return ElevenLabs(api_key=get_settings().require("elevenlabs_api_key"))


def _chars_to_words(alignment) -> list[dict]:
    """Collapse ElevenLabs per-character timings into per-word timings."""
    chars = alignment.characters
    starts = alignment.character_start_times_seconds
    ends = alignment.character_end_times_seconds
    words: list[dict] = []
    current: list[str] = []
    word_start = word_end = None
    for char, start, end in zip(chars, starts, ends):
        if char in (" ", "\n", "\t"):
            if current:
                words.append({"word": "".join(current), "start": word_start, "end": word_end})
                current, word_start = [], None
        else:
            if not current:
                word_start = start
            current.append(char)
            word_end = end
    if current:
        words.append({"word": "".join(current), "start": word_start, "end": word_end})
    return words


def convert_chunk(text: str, *, voice_id: str, model: str,
                  voice_settings: dict | None = None,
                  previous_request_ids: list[str] | None = None,
                  previous_text: str | None = None, next_text: str | None = None,
                  max_retries: int = 4) -> ChunkAudio:
    """Synthesize one chunk; return audio, word timings and the request id for stitching."""
    from elevenlabs import VoiceSettings

    client = _client()
    kwargs: dict = dict(
        voice_id=voice_id, text=text, model_id=model, output_format="mp3_44100_128",
        previous_request_ids=(previous_request_ids or [])[-3:] or None,
        previous_text=previous_text or None, next_text=next_text or None,
    )
    if voice_settings:
        kwargs["voice_settings"] = VoiceSettings(**voice_settings)
    for attempt in range(max_retries):
        try:
            raw = client.text_to_speech.with_raw_response.convert_with_timestamps(**kwargs)
            data = raw.data
            request_id = raw.headers.get("request-id") or raw.headers.get("x-request-id") or ""
            # Original-text alignment, not the normalized one: captions must show the
            # script's words ("three hundred"), not the TTS normalizer's rewrite.
            alignment = data.alignment or data.normalized_alignment
            return ChunkAudio(audio=base64.b64decode(data.audio_base_64),
                              words=_chars_to_words(alignment), request_id=request_id)
        except Exception as exc:
            # An expired previous_request_id is a 400: drop the ids, keep the text context.
            if "previous_request" in str(exc) and kwargs.get("previous_request_ids"):
                kwargs["previous_request_ids"] = None
                continue
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt * 2)
            else:
                raise
    raise RuntimeError("unreachable")
