"""Footage step: gather sources, cut them into scenes, and keep only the usable ones.

Sources are YouTube URLs from the brief (ambience / no-commentary / cinematic captures)
and the user's own captures dropped in ``footage/raw/``. Scenes are detected with
PySceneDetect and then measured on a downscaled decode, because a sleep video needs a
specific kind of shot:

- dark, but not black (an unlit scene reads as a dead screen)
- slow: no whip pans, no combat, no hard camera shake
- no HUD: a health bar or item counter pinned in a corner betrays itself as pixels that
  never change while the middle of the frame moves
- no menus, title cards or fades to black

Nothing is re-encoded: the timeline references the source file with in/out points, so
this step only writes ``scenes.json`` plus one contact-sheet frame per scene.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from loreforge.integrations import youtube_dl
from loreforge.integrations.media import probe, run_ffmpeg
from loreforge.models.project import VideoProject

ANALYSIS_WIDTH = 320
ANALYSIS_FPS = 5
MIN_SCENE_SECONDS = 4.0
MAX_SCENE_SECONDS = 90.0

# Thresholds tuned against the reference channel's own frames (mean luma ~0.02-0.07).
MAX_MEAN_LUMA = 0.42          # brighter than this is daylight/menu, not a night video
MIN_MEAN_LUMA = 0.012         # darker than this is a black screen
MAX_MOTION = 0.075            # mean absolute frame difference
MAX_MOTION_PEAK = 0.16        # a single hard cut/whip pan inside the scene
MAX_HUD_SCORE = 0.015        # share of border pixels that are static AND bright (a HUD bar)
MIN_USABLE_MINUTES = 25.0     # below this the timeline repeats itself too often


@dataclass
class Scene:
    source: str               # path relative to the run dir
    index: int
    start: float
    end: float
    luma: float
    motion: float
    motion_peak: float
    hud: float
    keep: bool
    reason: str = ""
    frame: str = ""           # contact-sheet image, relative to the run dir

    @property
    def duration(self) -> float:
        return self.end - self.start


def _decode_gray(path: Path, start: float, end: float) -> np.ndarray:
    """Decode a scene as a small grayscale array [frames, h, w] in 0..1."""
    import subprocess

    height = 180
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}",
           "-i", str(path), "-vf", f"scale={ANALYSIS_WIDTH}:{height},fps={ANALYSIS_FPS},format=gray",
           "-f", "rawvideo", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    frame_size = ANALYSIS_WIDTH * height
    n = len(raw) // frame_size
    if n == 0:
        return np.zeros((0, height, ANALYSIS_WIDTH), dtype=np.float32)
    return (np.frombuffer(raw[:n * frame_size], dtype=np.uint8)
            .reshape(n, height, ANALYSIS_WIDTH).astype(np.float32) / 255.0)


def measure(frames: np.ndarray) -> tuple[float, float, float, float]:
    """Return (mean luma, mean motion, peak motion, HUD score)."""
    if len(frames) < 2:
        return (float(frames.mean()) if len(frames) else 0.0, 0.0, 0.0, 0.0)
    diffs = np.abs(np.diff(frames, axis=0))
    motion = diffs.mean(axis=(1, 2))
    h, w = frames.shape[1:]
    centre = diffs[:, h // 4:3 * h // 4, w // 4:3 * w // 4].mean()

    # HUD: pixels in the border band that stay frozen while the centre moves AND are
    # brighter than the scene around them. Staying frozen is not enough on its own —
    # letterbox bars and dark still corners do that too, and they are fine to keep.
    mean_frame = frames.mean(axis=0)
    border = np.ones((h, w), dtype=bool)
    border[h // 5:4 * h // 5, w // 5:4 * w // 5] = False
    static = diffs.mean(axis=0) < 0.004
    bright = mean_frame > frames.mean() + 0.12
    hud = float((static & bright & border).sum() / border.sum()) if centre > 0.004 else 0.0
    return float(frames.mean()), float(motion.mean()), float(motion.max()), hud


def judge(luma: float, motion: float, peak: float, hud: float) -> tuple[bool, str]:
    if luma > MAX_MEAN_LUMA:
        return False, f"too bright ({luma:.2f})"
    if luma < MIN_MEAN_LUMA:
        return False, f"black screen ({luma:.3f})"
    if motion > MAX_MOTION:
        return False, f"too much motion ({motion:.3f})"
    if peak > MAX_MOTION_PEAK:
        return False, f"sudden movement ({peak:.3f})"
    if hud > MAX_HUD_SCORE:
        return False, f"HUD detected ({hud:.2f})"
    return True, ""


def detect_scenes(path: Path, on_log) -> list[tuple[float, float]]:
    """Scene boundaries via PySceneDetect, split to at most MAX_SCENE_SECONDS."""
    from scenedetect import AdaptiveDetector, SceneManager, open_video

    video = open_video(str(path))
    manager = SceneManager()
    manager.add_detector(AdaptiveDetector(adaptive_threshold=3.0, min_scene_len=15))
    manager.auto_downscale = True
    manager.detect_scenes(video, show_progress=False)
    cuts = [(s.get_seconds(), e.get_seconds()) for s, e in manager.get_scene_list()]
    if not cuts:
        cuts = [(0.0, probe(path).duration)]
    out: list[tuple[float, float]] = []
    for start, end in cuts:
        if end - start < MIN_SCENE_SECONDS:
            continue
        while end - start > MAX_SCENE_SECONDS:
            out.append((start, start + MAX_SCENE_SECONDS))
            start += MAX_SCENE_SECONDS
        if end - start >= MIN_SCENE_SECONDS:
            out.append((start, end))
    return out


def run(project: VideoProject, params: dict, on_log) -> dict:
    log = on_log
    force = bool(params.get("force"))

    # 1. Download any URL not fetched yet.
    project.footage_yt_dir.mkdir(parents=True, exist_ok=True)
    for i, url in enumerate(project.footage_urls):
        dest = project.footage_yt_path(i)
        if dest.exists() and not params.get("redownload"):
            continue
        log(f"Downloading footage {i + 1}/{len(project.footage_urls)}: {url}")
        youtube_dl.download(url, dest, on_log=log)

    files = project.footage_files()
    if not files:
        raise RuntimeError(f"No footage: add URLs to the brief or drop videos in "
                           f"{project.footage_raw_dir}")

    cache_path = project.footage_dir / "scenes_cache.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() and not force else {}
    project.contact_dir.mkdir(parents=True, exist_ok=True)

    scenes: list[Scene] = []
    for path in files:
        rel = str(path.relative_to(project.run_dir))
        info = probe(path)
        key = f"{rel}:{path.stat().st_size}"
        if key in cache:
            cuts = [tuple(c) for c in cache[key]]
            log(f"{path.name}: {len(cuts)} scenes (cached)")
        else:
            log(f"{path.name}: {info.duration / 60:.0f} min, detecting scenes...")
            cuts = detect_scenes(path, log)
            cache[key] = cuts
            cache_path.write_text(json.dumps(cache), encoding="utf-8")
            log(f"{path.name}: {len(cuts)} scenes of at least {MIN_SCENE_SECONDS:.0f}s")

        for i, (start, end) in enumerate(cuts):
            luma, motion, peak, hud = measure(_decode_gray(path, start, end))
            keep, reason = judge(luma, motion, peak, hud)
            scene = Scene(source=rel, index=i, start=round(start, 3), end=round(end, 3),
                          luma=round(luma, 4), motion=round(motion, 4),
                          motion_peak=round(peak, 4), hud=round(hud, 3), keep=keep, reason=reason)
            if keep:
                frame = project.contact_dir / f"{path.stem}_{i:04d}.jpg"
                if not frame.exists():
                    run_ffmpeg(["-ss", f"{(start + end) / 2:.3f}", "-i", str(path),
                                "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "4", str(frame)])
                scene.frame = str(frame.relative_to(project.run_dir))
            scenes.append(scene)

    kept = [s for s in scenes if s.keep]
    total = sum(s.duration for s in kept)

    # A walkthrough recording is in motion almost all the time, so fixed thresholds can
    # leave too little footage. Rather than fail, take back the calmest rejects (motion
    # only; a bright, black or HUD-covered scene is never acceptable) until there is
    # enough material, and say so.
    floor = float(params.get("min_usable_minutes") or MIN_USABLE_MINUTES) * 60
    if total < floor:
        spare = sorted((s for s in scenes if not s.keep and s.reason.startswith(("too much", "sudden"))),
                       key=lambda s: s.motion)
        for scene in spare:
            if total >= floor:
                break
            scene.keep = True
            scene.reason = f"relaxed ({scene.reason})"
            total += scene.duration
        if spare:
            log(f"Only {sum(s.duration for s in scenes if s.keep and not s.reason) / 60:.1f} min "
                f"passed the filters; accepted the calmest rejects up to {total / 60:.1f} min.")

    for scene in scenes:
        if scene.keep and not scene.frame:
            src = project.run_dir / scene.source
            frame = project.contact_dir / f"{src.stem}_{scene.index:04d}.jpg"
            if not frame.exists():
                run_ffmpeg(["-ss", f"{(scene.start + scene.end) / 2:.3f}", "-i", str(src),
                            "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "4", str(frame)])
            scene.frame = str(frame.relative_to(project.run_dir))

    kept = [s for s in scenes if s.keep]
    project.scenes_path.write_text(json.dumps([asdict(s) for s in scenes], indent=1),
                                    encoding="utf-8")
    reasons: dict[str, int] = {}
    for s in scenes:
        if not s.keep:
            reasons[s.reason.split(" (")[0]] = reasons.get(s.reason.split(" (")[0], 0) + 1
    log(f"Scenes: {len(kept)} kept of {len(scenes)} ({total / 60:.1f} min of usable footage). "
        f"Rejected: {reasons or 'none'}")
    if total < floor:
        log(f"WARNING: {total / 60:.1f} min of usable footage (want {floor / 60:.0f}+); scenes "
            f"will repeat often. Add more footage URLs or captures.")
    return {"scenes": len(scenes), "kept": len(kept), "usable_minutes": round(total / 60, 1),
            "rejected": reasons}
