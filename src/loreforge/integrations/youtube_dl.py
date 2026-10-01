"""yt-dlp footage downloader.

Ported from the old ``fetch_amv.py`` but WITHOUT scene splitting — LoreForge downloads
the source clip whole and the user cuts it in Premiere. Uses the yt-dlp module via the
current interpreter (no external binary assumption beyond ffmpeg for stream merge, which
yt-dlp locates on PATH if present).

Authentication: set YOUTUBE_COOKIES_FILE in .env pointing to a Netscape-format cookies
file exported from a logged-in browser (e.g. via "Get cookies.txt LOCALLY" extension).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections import deque
from functools import lru_cache
from pathlib import Path

YT_DLP = [sys.executable, "-m", "yt_dlp"]

# Piped on Windows, the yt-dlp child writes stderr in the ANSI codepage (cp1252). Its
# "Sign in to confirm you're not a bot" error uses a curly apostrophe (0x92), which is
# invalid UTF-8: a strict decode killed subprocess's reader thread and the real error
# surfaced as "(no stderr)". Forcing UTF-8 in the child keeps the message intact.
_YTDLP_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}

# --- Minimum yt-dlp -------------------------------------------------------------------
# YouTube enforces a GVS PO Token on the player clients older yt-dlp builds default to
# (android_vr, mweb, web). Without that token the metadata and the first ~10 MB download
# normally and every byte after is answered with HTTP 403 - a download that looks healthy
# until it dies at 10%. 2026.08.19 moved the default client set to visionos, which serves
# the full stream untokenised, so older builds are refused up front rather than dying
# halfway through a 90 MB clip.
MIN_YTDLP_VERSION = (2026, 8, 19)

# EJS solver: downloaded once from GitHub and cached locally. Required to solve
# YouTube's n-challenge (without it, all https formats are blocked).
_EJS = ["--remote-components", "ejs:github"]

# web_embedded lists manual subtitle tracks without needing video formats or cookies.
_SUB_CLIENT_ARGS = ["--extractor-args", "youtube:player_client=web_embedded"]

# Disable bgutil pip plugin if installed (avoids Deno startup overhead on every call).
_NO_BGUTIL = ["--extractor-args", "youtubepot-bgutilscript:server_home=/nonexistent"]

# Long clips routinely hit a transient CDN hiccup; retry instead of failing the pipeline
# step. exp=1:20 backs off 1s, 2s, 4s ... capped at 20s. --retries is ONE budget for the
# whole file, not per chunk: a 1.35 GiB clip burned 9 of 10 on dropped connections
# ("N bytes read, M more expected") by 6%, so it is sized for hour-long 1080p60 sources.
_RETRIES = [
    "--retries", "50",
    "--fragment-retries", "10",
    "--extractor-retries", "3",
    "--file-access-retries", "5",
    "--retry-sleep", "http:exp=1:20",
]

# --- Quality modes --------------------------------------------------------------------
QUALITY_EDIT = "edit"  # best H.264 rendition - imports into Premiere with no re-encode
QUALITY_MAX = "max"    # best resolution at any codec, transcoded to H.264 when needed

# Prefer AAC so the merged file stays a clean Premiere-native mp4.
# Parenthesised: without the group the "/" alternatives would detach from the "+"
# and yt-dlp could fall back to an audio-only download.
_AUDIO_SELECTOR = "(bestaudio[acodec^=mp4a]/bestaudio[ext=m4a]/bestaudio)"

# YouTube publishes each rendition twice: as DASH (protocol https) and as HLS
# (m3u8_native). The HLS manifest advertises a peak BANDWIDTH near double the real
# average - 1080p60 avc1 lists at 4907k against DASH's 2515k - yet downloading both
# yields the same ~94 MiB, 2512 kbps encode. Restricting to https keeps the bitrate sort
# honest and gives exact sizes plus resumable byte ranges.
_HTTPS_ONLY = "[protocol^=http]"


@lru_cache(maxsize=1)
def _ytdlp_version() -> tuple[int, ...]:
    """Return the installed yt-dlp version as a numeric tuple, e.g. ``(2026, 8, 19)``."""
    from yt_dlp.version import __version__ as raw
    parts: list[int] = []
    for chunk in raw.split(".")[:3]:
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def _require_ytdlp_version() -> None:
    """Refuse to run against a yt-dlp too old for YouTube's current PO Token enforcement."""
    if _ytdlp_version() >= MIN_YTDLP_VERSION:
        return
    have = ".".join(str(p) for p in _ytdlp_version())
    want = ".".join(str(p) for p in MIN_YTDLP_VERSION)
    raise RuntimeError(
        f"yt-dlp {have} is too old for YouTube (needs >= {want}). Older builds pick a "
        f"player client YouTube now blocks with HTTP 403 after about 10 MB, so downloads "
        f"die around 10%. Fix: pip install -U yt-dlp"
    )


def _cookies_args() -> list[str]:
    """Return --cookies <path> from settings, or raise if not configured."""
    try:
        from loreforge.config.settings import get_settings
        path = get_settings().youtube_cookies_file
        if path:
            return ["--cookies", path]
    except Exception:
        pass
    raise RuntimeError(
        "YOUTUBE_COOKIES_FILE is not set. Add it to your .env file pointing to a "
        "Netscape cookies file exported from a YouTube-logged-in browser."
    )


def _dl_args() -> list[str]:
    """Base args for all video/audio download commands."""
    _require_ytdlp_version()
    return _EJS + _NO_BGUTIL + _RETRIES + _cookies_args()


def _run_ytdlp(cmd: list[str]) -> subprocess.CompletedProcess:
    """Run a yt-dlp command, re-raising failures with yt-dlp's actual error and a hint."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", check=True, env=_YTDLP_ENV)
    except subprocess.CalledProcessError as exc:
        tail = [ln for ln in (exc.stderr or "").splitlines() if ln.strip()][-40:]
        url = cmd[-1] if cmd else ""
        raise RuntimeError(_failure_message("run", url, exc.returncode, tail)) from None


# Failure signatures worth translating: yt-dlp reports the symptom, these name the cause
# and the fix so a pipeline log is actionable without re-running the command by hand.
_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("http error 403", "forbidden"),
     "YouTube cut the video stream off mid-download - the PO Token block. Run "
     "'pip install -U yt-dlp' and retry."),
    (("sign in to confirm", "not a bot", "cookies are no longer valid", "login required"),
     "YouTube wants an authenticated session. Re-export YOUTUBE_COOKIES_FILE from a "
     "logged-in browser; those cookies expire every few weeks."),
    (("video unavailable", "private video", "members-only", "age-restricted"),
     "The video is not available to this account: private, removed, members-only or "
     "age-gated."),
    (("requested format is not available",),
     "No rendition matched the quality filter. Raise or clear YOUTUBE_MAX_HEIGHT, or set "
     "YOUTUBE_QUALITY=max to allow VP9/AV1 renditions."),
)


def _failure_message(action: str, url: str, code: int, tail: list[str]) -> str:
    """Build an error message carrying yt-dlp's own last error line plus a likely cause."""
    errors = [ln for ln in tail if "ERROR" in ln]
    detail = errors[-1] if errors else (tail[-1] if tail else "(no output captured)")
    blob = " ".join(tail).lower()
    hint = next((h for needles, h in _HINTS if any(n in blob for n in needles)), "")
    message = f"yt-dlp {action} failed (exit {code}) for {url}\n  {detail}"
    return f"{message}\n  Hint: {hint}" if hint else message


# "[download]  12.4% of    1.35GiB at ..." - yt-dlp's --newline progress line.
_DL_PROGRESS = re.compile(r"^\[download\]\s+(\d+(?:\.\d+)?)% of")
_PROGRESS_STEP = 5


def _stream_ytdlp(cmd: list[str], *, action: str, url: str, on_log=None) -> None:
    """Run yt-dlp, streaming output to ``on_log``, raising with the real error on failure.

    yt-dlp writes errors to stderr, merged into stdout here for live progress; keeping a
    tail of that stream is what lets the raised exception name the actual failure instead
    of only an exit code. Progress lines arrive several times a second, so only one per
    5% step reaches ``on_log``; every other line (retries, errors, merges) passes through.
    """
    process = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", env=_YTDLP_ENV,
    )
    tail: deque[str] = deque(maxlen=40)
    last_step: int | None = None
    for line in process.stdout:
        line = line.rstrip()
        if not line:
            continue
        tail.append(line)
        progress = _DL_PROGRESS.match(line)
        if progress:
            step = int(float(progress.group(1)) // _PROGRESS_STEP)
            if last_step is not None and step <= last_step:
                continue
            last_step = step
        elif "Destination:" in line:
            # New stream (video, then audio) or a retry reopening the file.
            last_step = None
        if on_log:
            on_log(line)
    process.wait()
    if process.returncode != 0:
        raise RuntimeError(_failure_message(action, url, process.returncode, list(tail)))


def probe(url: str) -> dict:
    """Fetch video metadata without downloading."""
    result = _run_ytdlp(YT_DLP + _dl_args() + ["--dump-json", "--no-playlist", url])
    info = json.loads(result.stdout)
    return {
        "url": url,
        "title": info.get("title", ""),
        "channel": info.get("channel", info.get("uploader", "")),
        "duration": info.get("duration", 0),
        "upload_date": info.get("upload_date", ""),
    }


def list_manual_subtitles(url: str) -> list[dict]:
    """Return the MANUAL (channel-authored) subtitle languages available for ``url``.

    Each entry is ``{"code": "<yt-dlp lang code>", "name": "<human label>"}``. These are
    the exact codes to pass to ``download_subtitles`` (e.g. ``pt``, ``pt-BR``, ``en``,
    ``ja``) — YouTube's real codes, not the human names shown in the player. Automatic
    (ASR) captions under ``automatic_captions`` are deliberately ignored.

    ``live_chat`` is filtered out: it is the live-stream chat replay (JSON, not lyric
    text) that YouTube exposes under ``subtitles``, never an actual caption track. Tracks
    that offer no text format (vtt/srt/ttml) are dropped for the same reason.
    """
    result = _run_ytdlp(
        YT_DLP + _SUB_CLIENT_ARGS + _cookies_args()
        + ["--dump-json", "--ignore-no-formats-error", "--no-playlist", url]
    )
    info = json.loads(result.stdout)
    subs = info.get("subtitles") or {}
    text_exts = {"vtt", "srt", "ttml", "srv1", "srv2", "srv3"}
    out = []
    for code, tracks in subs.items():
        if code == "live_chat":
            continue
        name = ""
        has_text = False
        if isinstance(tracks, list):
            for t in tracks:
                if t.get("ext") in text_exts:
                    has_text = True
                if not name:
                    name = t.get("name", "") or ""
        if not has_text:
            continue
        out.append({"code": code, "name": name})
    out.sort(key=lambda s: s["code"])
    return out


def _quality_defaults(quality: str | None, max_height: int | None) -> tuple[str, int | None]:
    """Resolve the quality mode and height cap, falling back to settings then defaults."""
    if quality is None or max_height is None:
        try:
            from loreforge.config.settings import get_settings
            settings = get_settings()
            if quality is None:
                quality = getattr(settings, "youtube_quality", None)
            if max_height is None:
                max_height = getattr(settings, "youtube_max_height", None)
        except Exception:
            pass
    resolved = (quality or QUALITY_EDIT).strip().lower()
    if resolved not in (QUALITY_EDIT, QUALITY_MAX):
        raise ValueError(
            f"Unknown quality '{quality}'. Use '{QUALITY_EDIT}' (native H.264) or "
            f"'{QUALITY_MAX}' (highest resolution, transcoded)."
        )
    return resolved, max_height


def _format_args(quality: str, max_height: int | None) -> list[str]:
    """Build the yt-dlp ``-f``/``-S`` pair for a quality mode.

    ``QUALITY_EDIT`` keeps the download Premiere-native: the best H.264 (avc1) rendition
    with AAC audio, no re-encode. On most YouTube uploads that caps out at 1080p, because
    YouTube only publishes 1440p and 2160p as VP9 and AV1.

    ``QUALITY_MAX`` takes the highest resolution on offer regardless of codec, which the
    caller then transcodes to H.264. Sorting puts resolution and fps first, then prefers
    h264 so an equal-resolution rendition that needs no transcode wins, and only then
    falls back to bitrate.
    """
    cap = f"[height<={max_height}]" if max_height else ""
    if quality == QUALITY_MAX:
        chain = (
            f"bestvideo{cap}{_HTTPS_ONLY}+{_AUDIO_SELECTOR}/"
            f"bestvideo{cap}+{_AUDIO_SELECTOR}/"
            f"best{cap}/best"
        )
        sort = "res,fps,vcodec:h264,tbr"
    else:
        chain = (
            f"bestvideo[vcodec^=avc1]{cap}{_HTTPS_ONLY}+{_AUDIO_SELECTOR}/"
            f"bestvideo[vcodec^=avc1]{cap}+{_AUDIO_SELECTOR}/"
            f"best[vcodec^=avc1]{cap}/best[ext=mp4]{cap}/best{cap}/best"
        )
        sort = "res,fps,tbr"
    return ["-f", chain, "-S", sort]


def _video_codec(path: Path) -> str:
    """Return the video codec of ``path`` ("h264", "vp9", "av1", ...), "" if unknown."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, encoding="utf-8", check=True,
        )
    except Exception:
        return ""
    lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
    return lines[0] if lines else ""


@lru_cache(maxsize=1)
def _has_nvenc() -> bool:
    """True when ffmpeg exposes NVIDIA's H.264 encoder, which is far faster than libx264."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, encoding="utf-8", check=True,
        )
    except Exception:
        return False
    return "h264_nvenc" in result.stdout


# Keys of ffmpeg's -progress stream, e.g. "out_time_us=12345" or "progress=continue".
_FFMPEG_PROGRESS_KEY = re.compile(r"^[a-z_0-9]+=")


def _media_duration(path: Path) -> float:
    """Return the duration of ``path`` in seconds, or 0.0 when ffprobe cannot tell."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, encoding="utf-8", check=True,
        )
        return float(result.stdout.strip().splitlines()[0])
    except Exception:
        return 0.0


def _run_ffmpeg(cmd: list[str], *, duration: float, on_log=None) -> tuple[int, str]:
    """Run an ffmpeg command with ``-progress pipe:1``, logging percent as it encodes.

    A silent multi-minute step reads as a hung pipeline, so progress is reported every 5%.
    ffmpeg's own ``-stats`` output is carriage-return separated and does not survive line
    iteration; the ``-progress`` stream is newline-delimited ``key=value`` blocks, which
    does. Returns ``(exit code, last error line)``.
    """
    log = on_log or (lambda _m: None)
    process = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    tail: deque[str] = deque(maxlen=20)
    next_mark = 5
    for line in process.stdout:
        line = line.strip()
        if not line:
            continue
        if line.startswith("out_time_us=") and duration > 0:
            raw = line.split("=", 1)[1]
            if raw.isdigit():
                percent = min(100.0, (int(raw) / 1_000_000) / duration * 100)
                if percent >= next_mark:
                    log(f"[transcode] {percent:.0f}%")
                    next_mark = int(percent // 5) * 5 + 5
        elif not _FFMPEG_PROGRESS_KEY.match(line):
            # Not part of the progress stream: an actual ffmpeg diagnostic. Matching the
            # key shape rather than testing for "=" keeps error lines that contain one.
            tail.append(line)
    process.wait()
    return process.returncode, (tail[-1] if tail else f"exit {process.returncode}")


def _transcode_to_h264(path: Path, *, on_log=None) -> None:
    """Re-encode ``path`` in place to high-bitrate H.264/AAC so Premiere can decode it.

    Only reached on ``QUALITY_MAX`` downloads that came back VP9 or AV1 - the codecs
    YouTube uses above 1080p and the ones Premiere imports as "Media offline". Settings
    are near visually lossless (NVENC cq 18, x264 crf 16) so the extra resolution stays
    worth having despite the generational re-encode. NVENC is tried first and libx264 is
    the fallback, since a driver mismatch makes NVENC fail at encoder-open time.
    """
    log = on_log or (lambda _m: None)
    codec = _video_codec(path)
    if codec in ("h264", ""):
        return

    tmp = path.with_name(f"{path.stem}.h264{path.suffix}")
    attempts: list[list[str]] = []
    if _has_nvenc():
        attempts.append([
            "-c:v", "h264_nvenc", "-preset", "p7", "-tune", "hq",
            "-rc", "vbr", "-cq", "18", "-b:v", "0",
            "-profile:v", "high", "-pix_fmt", "yuv420p",
        ])
    attempts.append([
        "-c:v", "libx264", "-crf", "16", "-preset", "slow", "-pix_fmt", "yuv420p",
    ])

    duration = _media_duration(path)
    last_error = ""
    for video_args in attempts:
        encoder = video_args[1]
        log(f"Transcoding {codec} -> H.264 ({encoder}); a 1440p60 clip takes a few minutes.")
        cmd = (
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-nostats",
             "-progress", "pipe:1", "-i", str(path)]
            + video_args
            + ["-c:a", "aac", "-b:a", "320k", "-movflags", "+faststart", str(tmp)]
        )
        code, last_error = _run_ffmpeg(cmd, duration=duration, on_log=log)
        if code == 0 and tmp.exists() and tmp.stat().st_size > 0:
            tmp.replace(path)
            log(f"Transcode complete: {path.name} is now H.264.")
            return
        log(f"{encoder} failed: {last_error}")
        tmp.unlink(missing_ok=True)

    raise RuntimeError(
        f"ffmpeg could not transcode {path.name} from {codec} to H.264: {last_error}. "
        f"The {codec} file is still on disk; set YOUTUBE_QUALITY=edit to download a "
        f"native H.264 rendition instead."
    )


def download(url: str, dest: Path, *, quality: str | None = None,
             max_height: int | None = None, on_log=None) -> dict:
    """Download ``url`` to ``dest`` as a Premiere-ready H.264 mp4 and return metadata.

    ``quality`` is ``"edit"`` (default: best H.264 rendition, no re-encode) or ``"max"``
    (best resolution at any codec, transcoded to H.264 when YouTube publishes the top
    renditions only as VP9/AV1). ``max_height`` caps resolution. Both fall back to
    ``YOUTUBE_QUALITY`` / ``YOUTUBE_MAX_HEIGHT`` in settings when omitted. ``on_log``
    receives yt-dlp output lines for live progress.
    """
    log = on_log or (lambda _m: None)
    quality, max_height = _quality_defaults(quality, max_height)
    dest.parent.mkdir(parents=True, exist_ok=True)
    meta = probe(url)
    cap = f", <= {max_height}p" if max_height else ""
    log(f"Downloading: {meta['title']} ({meta['duration']}s) [quality={quality}{cap}]")

    cmd = YT_DLP + _dl_args() + _format_args(quality, max_height) + [
        "--merge-output-format", "mp4",
        "-o", str(dest),
        "--no-playlist",
        "--newline",
        url,
    ]
    _stream_ytdlp(cmd, action="download", url=url, on_log=on_log)

    if quality == QUALITY_MAX:
        _transcode_to_h264(dest, on_log=on_log)

    meta["local_path"] = str(dest)
    meta["quality"] = quality
    codec = _video_codec(dest)
    if codec:
        meta["vcodec"] = codec
    return meta


# --- Manual (channel-authored) subtitle download -------------------------------------

_VTT_TIMING = re.compile(r"^\d{2}:\d{2}:\d{2}[.,]\d{3}\s*-->")
_VTT_CUE_INDEX = re.compile(r"^\d+$")
# Any inline/markup tag: <c>, </c>, <c.colorXXXX>, <b>, </b>, karaoke <00:00:01.234>, etc.
_VTT_ANY_TAG = re.compile(r"<[^>]*>")
# Zero-width characters that animated captions inject between glyphs. Written as escapes
# on purpose: a literal invisible char here once degraded into a plain space, which
# stripped every word separator. Non-breaking spaces are normalised by ``_WS_RUN`` instead.
_VTT_ZERO_WIDTH = re.compile("[​‌‍﻿]")
_WS_RUN = re.compile(r"\s+")


def _vtt_to_text(vtt: str) -> str:
    """Strip a WebVTT subtitle file down to plain lyric text.

    Handles heavily-animated karaoke captions (the common case for lyric videos): a giant
    ``STYLE``/``::cue`` colour table in the header, every line wrapped in ``<c.colorXXXX>``
    and ``<b>`` tags with ``​`` zero-width spaces sprinkled between glyphs, and each
    line repeated across dozens of near-identical cues as the colour sweep animates.

    Removes the header block, ``NOTE``/``STYLE``/``::cue`` styling, numeric cue indices,
    ``HH:MM:SS.mmm --> …`` timing lines, ALL markup tags and zero-width spacing, then
    normalises whitespace. Consecutive identical lines are collapsed — which, once the
    tags/zero-width noise is gone, folds the karaoke sweep of a line down to one copy while
    still preserving a chorus line that genuinely recurs later in the song.
    """
    # Drop the leading header block (``WEBVTT`` line plus ``Kind:``/``Language:`` metadata
    # and any inline ``Style:``/``::cue`` colour table) up to the first blank line.
    raw_lines = vtt.splitlines()
    if raw_lines and raw_lines[0].lstrip().startswith("WEBVTT"):
        i = 0
        while i < len(raw_lines) and raw_lines[i].strip():
            i += 1
        raw_lines = raw_lines[i:]

    lines: list[str] = []
    # text -> end time (s) of the cue in which it was last on screen.
    last_end: dict[str, float] = {}
    cue_start = cue_end = None
    for raw in raw_lines:
        line = raw.strip()
        if not line:
            continue
        # Skip styling / structural noise (defensive — most is dropped with the header).
        if (line.startswith("WEBVTT") or line.startswith("NOTE") or line.startswith("STYLE")
                or line.startswith("::cue") or line.startswith("Style:") or line == "}"
                or line == "##"):
            continue
        if "-->" in line and _VTT_TIMING.match(line):
            cue_start, cue_end = _vtt_cue_times(line)
            continue
        if _VTT_CUE_INDEX.match(line):
            continue
        line = _VTT_ANY_TAG.sub("", line)
        line = _VTT_ZERO_WIDTH.sub("", line)
        line = _WS_RUN.sub(" ", line).strip()
        if not line:
            continue
        # Collapse consecutive exact repeats (animated/karaoke-caption artifact).
        if lines and lines[-1] == line:
            if cue_end is not None:
                last_end[line] = cue_end
            continue
        # Cross-fading captions keep the outgoing line on screen while the next one fades
        # in, so the two interleave (A, B, A, B…). Drop a line that reappears while its
        # previous occurrence is still (practically) visible; a chorus that genuinely
        # recurs later starts well after the last one ended and is kept.
        prev_end = last_end.get(line)
        if (prev_end is not None and cue_start is not None
                and cue_start - prev_end <= _VTT_OVERLAP_GAP_S):
            last_end[line] = max(prev_end, cue_end or prev_end)
            continue
        if cue_end is not None:
            last_end[line] = cue_end
        lines.append(line)
    return "\n".join(lines)


# Max gap (s) between a line's last cue and its reappearance for the two to count as the
# same on-screen occurrence (animated captions re-emit cues every ~1-3 video frames).
_VTT_OVERLAP_GAP_S = 0.25


def _vtt_timestamp(ts: str) -> float:
    h, m, s = ts.replace(",", ".").split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def _vtt_cue_times(timing_line: str) -> tuple[float, float]:
    start, _, rest = timing_line.partition("-->")
    end = rest.split()[0]
    return _vtt_timestamp(start.strip()), _vtt_timestamp(end)


def download_subtitles(url: str, out_dir: Path, *, lang: str = "en", on_log=None) -> str:
    """Download ONLY the channel's manual subtitles for ``url`` in ``lang``; return plain text.

    Deliberately passes ``--write-subs`` WITHOUT ``--write-auto-subs``: YouTube's automatic
    captions are ASR-quality (the same problem Whisper has), whereas manual subs are the
    channel's own — usually the correct lyrics. Downloads no media (``--skip-download``).

    Returns the cleaned, dedup'd transcript, or ``""`` when the video has no manual
    subtitles in the requested language.
    """
    log = on_log or (lambda _m: None)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_template = str(out_dir / "%(id)s.%(ext)s")
    cmd = YT_DLP + _SUB_CLIENT_ARGS + _cookies_args() + [
        "--write-subs",
        "--skip-download",
        "--ignore-no-formats-error",
        "--no-playlist",
        "--sub-langs", lang,
        "--sub-format", "vtt",
        "-o", out_template,
        "--newline",
        url,
    ]
    log(f"Fetching manual subtitles ({lang})…")
    _stream_ytdlp(cmd, action="subtitle download", url=url, on_log=log)
    # A missing manual sub is not an error here — yt-dlp just writes nothing.
    vtt_files = sorted(out_dir.glob("*.vtt"))
    if not vtt_files:
        log(f"No manual subtitles found for language '{lang}'.")
        return ""
    text = _vtt_to_text(vtt_files[0].read_text(encoding="utf-8", errors="replace"))
    log(f"Manual subtitles parsed: {len(text.splitlines())} lines.")
    return text


def download_audio(url: str, dest: Path, *, audio_format: str = "mp3", on_log=None) -> dict:
    """Download audio-only from ``url``, transcode to ``audio_format``, save to ``dest``.

    Uses yt-dlp's extract-audio postprocessor (requires ffmpeg on PATH, same implicit
    dependency as the mp4 merge in ``download``). Returns metadata with ``local_path``.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    meta = probe(url)
    if on_log:
        on_log(f"Downloading audio: {meta['title']} ({meta['duration']}s)")

    # -x rewrites the output extension, so template with %(ext)s and point at the stem.
    out_template = str(dest.with_suffix(f".%(ext)s"))
    cmd = YT_DLP + _dl_args() + [
        "-x",
        "--audio-format", audio_format,
        "--audio-quality", "0",
        "-f", "bestaudio/best",
        "-o", out_template,
        "--no-playlist",
        "--newline",
        url,
    ]
    _stream_ytdlp(cmd, action="audio download", url=url, on_log=on_log)

    meta["local_path"] = str(dest)
    return meta


# --- Automatic captions (json3) for reference transcripts ------------------------------
# Reference videos are mined for KNOWLEDGE, never wording (research extracts paraphrased
# facts; the script lint flags any 8-gram shared with these transcripts). YouTube's ASR
# captions are good enough for that and arrive instantly with per-word timing.

def download_auto_subs_json3(url: str, out_dir: Path, *, lang: str = "en", on_log=None) -> Path | None:
    """Download the automatic captions of ``url`` as json3; return the file or None."""
    log = on_log or (lambda _m: None)
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = YT_DLP + _SUB_CLIENT_ARGS + _cookies_args() + [
        "--write-auto-subs", "--write-subs",
        "--skip-download", "--ignore-no-formats-error", "--no-playlist",
        "--sub-langs", f"{lang}.*,{lang}", "--sub-format", "json3",
        "-o", str(out_dir / "%(id)s.%(ext)s"), "--newline", url,
    ]
    log(f"Fetching captions (json3, {lang}) for {url}")
    _stream_ytdlp(cmd, action="caption download", url=url, on_log=log)
    files = sorted(out_dir.glob("*.json3"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def json3_words(data: dict) -> list[dict]:
    """Flatten a json3 caption file into [{"word", "start", "end"}] (seconds).

    json3 events carry ``tStartMs`` and ``segs`` with ``utf8`` text and ``tOffsetMs``.
    Auto captions repeat lines across rolling events; only events with ``segs`` that are
    not pure newlines contribute, so each word appears once. A word ends where the next
    one starts (capped at the event end).
    """
    words: list[dict] = []
    for ev in data.get("events", []):
        segs = ev.get("segs") or []
        if not segs or ev.get("aAppend"):
            continue
        t0 = ev.get("tStartMs", 0)
        t_end = t0 + ev.get("dDurationMs", 0)
        for seg in segs:
            text = (seg.get("utf8") or "").strip()
            if not text or text == "\n":
                continue
            start = (t0 + seg.get("tOffsetMs", 0)) / 1000.0
            for piece in text.split():
                words.append({"word": piece, "start": start, "end": t_end / 1000.0})
    for a, b in zip(words, words[1:]):
        a["end"] = min(a["end"], b["start"]) if b["start"] >= a["start"] else a["end"]
    return words


def json3_text(data: dict) -> str:
    """Plain transcript text from a json3 caption file."""
    return " ".join(w["word"] for w in json3_words(data))
