"""ffprobe/ffmpeg helpers shared by audio, footage and timeline services."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class MediaInfo:
    duration: float                 # seconds (container)
    width: int = 0
    height: int = 0
    fps: float = 0.0
    has_video: bool = False
    has_audio: bool = False
    sample_rate: int = 0
    channels: int = 0
    vcodec: str = ""


def _fps(rate: str) -> float:
    try:
        num, den = rate.split("/")
        return float(num) / float(den) if float(den) else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


@lru_cache(maxsize=512)
def _probe_cached(path: str, mtime: float) -> MediaInfo:
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
        capture_output=True, text=True, encoding="utf-8", check=True)
    data = json.loads(res.stdout)
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    duration = float(data.get("format", {}).get("duration") or 0.0)
    if not duration and v and v.get("duration"):
        duration = float(v["duration"])
    return MediaInfo(
        duration=duration,
        width=int(v.get("width", 0)) if v else 0,
        height=int(v.get("height", 0)) if v else 0,
        fps=_fps(v.get("avg_frame_rate") or v.get("r_frame_rate") or "0/1") if v else 0.0,
        has_video=v is not None,
        has_audio=a is not None,
        sample_rate=int(a.get("sample_rate", 0)) if a else 0,
        channels=int(a.get("channels", 0)) if a else 0,
        vcodec=v.get("codec_name", "") if v else "",
    )


def probe(path: Path) -> MediaInfo:
    """ffprobe ``path`` (cached per path+mtime)."""
    p = Path(path).resolve()
    return _probe_cached(str(p), p.stat().st_mtime)


def run_ffmpeg(args: list[str], *, cwd: Path | None = None) -> None:
    """Run ffmpeg quietly; raise with the tail of stderr on failure."""
    res = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args],
                         cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                         errors="replace")
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {res.stderr[-800:]}")


def file_url(path: Path) -> str:
    """``file://localhost/C:/...`` URL for FCP7 XML.

    Premiere's importer needs the explicit ``localhost`` host on Windows: the host-less
    ``file:///C:/...`` from ``Path.as_uri()`` is mis-parsed into ``\\\\\\C:\\...`` and
    every clip shows as Media Offline. (Learned in cut-forge.)
    """
    return Path(path).resolve().as_uri().replace("file:///", "file://localhost/", 1)
