"""J1 TTS: ElevenLabs voices through a cheaper reseller API (https://api.j1tts.com).

The API is asynchronous (submit a job, poll it, download the MP3) and takes only
``text`` + ``voice_id``: no model choice, no voice settings, no request stitching and
no timestamps. Measured on 2026-09-29, ``voice_settings.speed`` and ``speed`` are
silently ignored. So the narration service applies speed locally with ffmpeg and
estimates word timings from the audio's silences (``services/alignment.py``).

The voice must be imported into the J1 account first; ``ensure_voice`` does that once
per process from the ElevenLabs voice id, so the channel keeps its ElevenLabs id.
"""
from __future__ import annotations

import time

import requests

from loreforge.config.settings import get_settings

_imported: set[str] = set()


def _base() -> str:
    return get_settings().j1_base_url.rstrip("/")


def _headers() -> dict:
    return {"Authorization": f"Bearer {get_settings().require('j1_api_key')}",
            "Content-Type": "application/json"}


def _get(path: str, **kw) -> requests.Response:
    res = requests.get(f"{_base()}{path}", headers=_headers(), timeout=60, **kw)
    res.raise_for_status()
    return res


def usage() -> dict:
    """``{"used_today", "limit", "remaining", "reset_at", ...}``."""
    return _get("/v1/usage").json()


def ensure_voice(voice_id: str) -> None:
    """Import an ElevenLabs voice into the J1 account unless it is already there."""
    if voice_id in _imported:
        return
    mine = {v["voice_id"] for v in _get("/v1/my-voices").json().get("voices", [])}
    if voice_id not in mine:
        res = requests.post(f"{_base()}/v1/voices/import", headers=_headers(),
                            json={"voice_ids": voice_id}, timeout=60)
        res.raise_for_status()
        mine = {v["voice_id"] for v in _get("/v1/my-voices").json().get("voices", [])}
        if voice_id not in mine:
            raise RuntimeError(f"J1 could not import voice {voice_id}: {res.text[:300]}")
    _imported.add(voice_id)


def synthesize(text: str, *, voice_id: str, poll_seconds: float = 2.0,
               timeout_seconds: float = 900, max_retries: int = 4) -> tuple[bytes, str]:
    """Synthesize ``text``; return (mp3 bytes, job id). Retries the whole job on failure."""
    ensure_voice(voice_id)
    for attempt in range(max_retries):
        try:
            res = requests.post(f"{_base()}/v1/tts", headers=_headers(),
                                json={"text": text, "voice_id": voice_id}, timeout=60)
            res.raise_for_status()
            job_id = res.json()["id"]
            deadline = time.monotonic() + timeout_seconds
            while True:
                status = _get(f"/v1/tts/{job_id}").json()
                if status["status"] == "completed":
                    break
                if status["status"] == "failed":
                    raise RuntimeError(f"J1 job {job_id} failed: {status.get('error')}")
                if time.monotonic() > deadline:
                    raise TimeoutError(f"J1 job {job_id} still {status['status']} "
                                       f"after {timeout_seconds:.0f}s")
                time.sleep(poll_seconds)
            audio = _get(f"/v1/tts/{job_id}/download").content
            if not audio:
                raise RuntimeError(f"J1 job {job_id} returned an empty file")
            return audio, job_id
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt * 2)
            else:
                raise
    raise RuntimeError("unreachable")
