"""The channel look: colour grade LUT, overlay assets with alpha, and an ffmpeg preview.

The look is assembled in Premiere (overlay tracks + an adjustment layer carrying the
LUT), so this module prepares channel assets ONCE (``prepare_channel``) and renders
short previews to compare against the reference frame without opening Premiere.

Design choices:
- The grade is a parametric function compiled into a 3D ``.cube`` LUT. The same file
  feeds Lumetri ("Input LUT") and ffmpeg (``lut3d``), so preview == Premiere.
- Overlays carry a real alpha channel so they composite with the *Normal* blend mode.
  A white layer with alpha = luma is mathematically identical to a Screen blend of the
  original luma overlay (1-(1-b)(1-s) = s + b(1-s)), which is how rain/dust stock is
  meant to be used. That removes any dependence on blend modes surviving FCP7 XML.
- Overlays are PNG-in-QuickTime (rgba): lossless, alpha-capable and decoded natively
  by Premiere. Measured on the 10 s rain loop: PNG 43 MB, ProRes 4444 134 MB, QuickTime
  Animation (qtrle) 202 MB. Anti-aliased streaks defeat qtrle's run-length coding.
- Film grain is NOT an overlay: per-pixel noise is incompressible (gigabytes per loop).
  It lives in the adjustment-layer preset (Premiere "Noise") and in the preview as
  ffmpeg ``noise``.
"""
from __future__ import annotations

import math
import subprocess
from pathlib import Path

import numpy as np

from loreforge.config.channels import Channel, GradeSpec

_LUMA = np.array([0.2126, 0.7152, 0.0722])


# --- Grade ----------------------------------------------------------------------------

def grade(rgb: np.ndarray, g: GradeSpec) -> np.ndarray:
    """Apply the parametric grade to float RGB in [0, 1] (any shape ending in 3)."""
    src = rgb.astype(np.float64)
    src_luma = (src @ _LUMA)[..., None]
    t = np.clip((src_luma - 0.35) / (0.80 - 0.35), 0.0, 1.0)
    sat = g.saturation + (g.highlight_saturation - g.saturation) * (t * t * (3 - 2 * t))
    x = src * g.exposure
    x = (x - g.contrast_pivot) * g.contrast + g.contrast_pivot
    luma = (x @ _LUMA)[..., None]
    x = luma + (x - luma) * sat
    luma = np.clip(x @ _LUMA, 0.0, 1.0)[..., None]
    w_shadow = np.clip(1.0 - luma / 0.5, 0.0, 1.0) ** 1.5
    w_high = np.clip((luma - 0.5) / 0.5, 0.0, 1.0)
    x = x + w_shadow * np.asarray(g.shadow_tint) + w_high * np.asarray(g.highlight_tint)
    x = np.clip(x, 0.0, 1.0)
    x = g.black_lift + x * (g.white_clip - g.black_lift)
    return np.clip(x, 0.0, 1.0)


def write_cube(path: Path, g: GradeSpec, *, size: int = 33, title: str = "look") -> Path:
    """Write the grade as an Adobe/Resolve ``.cube`` 3D LUT (red varies fastest)."""
    axis = np.linspace(0.0, 1.0, size)
    b, gg, r = np.meshgrid(axis, axis, axis, indexing="ij")
    rgb = np.stack([r, gg, b], axis=-1).reshape(-1, 3)   # r fastest, then g, then b
    out = grade(rgb, g)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'TITLE "{title}"', f"LUT_3D_SIZE {size}",
             "DOMAIN_MIN 0.0 0.0 0.0", "DOMAIN_MAX 1.0 1.0 1.0"]
    lines += [f"{v[0]:.6f} {v[1]:.6f} {v[2]:.6f}" for v in out]
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
    return path


# --- Static PNG layers ---------------------------------------------------------------

def write_vignette(path: Path, width: int, height: int, strength: float) -> Path:
    """Black RGBA vignette: transparent centre, dark oval edges."""
    from PIL import Image

    yy, xx = np.mgrid[0:height, 0:width].astype(np.float64)
    nx = (xx - width / 2) / (width / 2)
    ny = (yy - height / 2) / (height / 2)
    r = np.sqrt(nx ** 2 * 0.85 + ny ** 2 * 1.15)
    t = np.clip((r - 0.45) / (1.25 - 0.45), 0.0, 1.0)
    alpha = strength * (t * t * (3 - 2 * t))           # smoothstep
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    rgba[..., 3] = np.round(alpha * 255).astype(np.uint8)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, "RGBA").save(path)
    return path


def write_placeholder_watermark(path: Path, text: str, width: int, height: int,
                                width_frac: float, margin_frac: float) -> Path:
    """Full-frame RGBA with the channel name top-right, until a real logo exists.

    Full-frame (not a small logo) so it drops onto a track with no positioning: the XML
    never needs motion parameters, which is the least reliable part of FCP7 import.
    """
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    target_w = width * width_frac
    font = None
    size = 40
    for candidate in ("C:/Windows/Fonts/georgia.ttf", "C:/Windows/Fonts/times.ttf"):
        if Path(candidate).exists():
            for size in range(12, 120):
                f = ImageFont.truetype(candidate, size)
                if draw.textlength(text.upper(), font=f) >= target_w:
                    break
            font = ImageFont.truetype(candidate, size)
            break
    font = font or ImageFont.load_default()
    label = text.upper()
    tw = draw.textlength(label, font=font)
    margin = width * margin_frac
    draw.text((width - margin - tw, margin), label, font=font, fill=(235, 232, 225, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path


# --- Procedural overlays -------------------------------------------------------------

def _encode_rgba(frames, path: Path, width: int, height: int, fps: int, on_log=None) -> Path:
    """Pipe RGBA uint8 frames into ffmpeg -> PNG-in-QuickTime (rgba) .mov."""
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{width}x{height}", "-r", str(fps),
           "-i", "-", "-c:v", "png", "-pix_fmt", "rgba", str(path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    n = 0
    try:
        for frame in frames:
            proc.stdin.write(frame.tobytes())
            n += 1
            if on_log and n % 60 == 0:
                on_log(f"  {path.name}: {n} frames")
    finally:
        proc.stdin.close()
    err = proc.stderr.read().decode("utf-8", "replace")
    if proc.wait() != 0:
        raise RuntimeError(f"ffmpeg failed writing {path.name}: {err[-500:]}")
    return path


def rain_frames(width: int, height: int, fps: int, seconds: float, *, seed: int = 7,
                angle_deg: float = 3.0, color=(228, 232, 236)):
    """Seamlessly looping rain streaks (RGBA uint8 generator).

    Every drop travels an integer number of wraps over the loop, so frame N == frame 0.
    Three depth layers: far (short, faint, slow), mid, near (long, brighter, fast).
    """
    import cv2

    rng = np.random.default_rng(seed)
    total = int(round(seconds * fps))
    tan = math.tan(math.radians(angle_deg))
    span_y = height + 200                  # drops enter above and leave below the frame
    span_x = width + 200
    layers = [  # (count, wraps per loop, length px, thickness, alpha range)
        (300, 10, (14, 30), 1, (0.10, 0.25)),
        (160, 16, (40, 90), 1, (0.18, 0.40)),
        (40, 24, (120, 260), 1, (0.25, 0.50)),
    ]
    density = (width * height) / (1920 * 1080)   # counts above are for 1080p
    drops = []
    for count, wraps, (lmin, lmax), thick, (amin, amax) in layers:
        for _ in range(max(1, round(count * density))):
            k = wraps + int(rng.integers(-2, 3))
            drops.append((rng.uniform(0, span_x), rng.uniform(0, span_y), max(1, k),
                          rng.uniform(lmin, lmax), thick, rng.uniform(amin, amax)))
    col = np.array(color, dtype=np.uint8)
    for f in range(total):
        t = f / total
        alpha = np.zeros((height, width), dtype=np.float32)
        for x0, y0, k, length, thick, a in drops:
            dy = k * span_y * t
            y = (y0 + dy) % span_y - 100
            x = (x0 + dy * tan) % span_x - 100
            x2 = x - length * tan / math.sqrt(1 + tan * tan)
            y2 = y - length / math.sqrt(1 + tan * tan)
            cv2.line(alpha, (int(x), int(y)), (int(x2), int(y2)), float(a), thick, cv2.LINE_AA)
        alpha = cv2.GaussianBlur(alpha, (0, 0), 0.8)
        rgba = np.empty((height, width, 4), dtype=np.uint8)
        rgba[..., :3] = col
        rgba[..., 3] = np.clip(alpha * 255, 0, 255).astype(np.uint8)
        yield rgba


def film_frames(width: int, height: int, fps: int, seconds: float, *, seed: int = 11):
    """Old-film damage: dust specks, hairs, vertical scratches and a gentle flicker.

    Composited "over" on a transparent canvas; each element has its own colour, so one
    layer carries both white (scratches, bright dust) and black (dirt, hairs) marks.
    """
    import cv2

    rng = np.random.default_rng(seed)
    total = int(round(seconds * fps))
    scratches: list[dict] = []
    hair: dict | None = None
    for _ in range(total):
        # Premultiplied accumulation: colour*alpha and alpha.
        acc_c = np.zeros((height, width, 3), dtype=np.float32)
        acc_a = np.zeros((height, width), dtype=np.float32)

        def over(mask: np.ndarray, rgb: tuple[int, int, int]):
            nonlocal acc_c, acc_a
            c = np.array(rgb, dtype=np.float32) / 255.0
            acc_c = mask[..., None] * c + acc_c * (1 - mask[..., None])
            acc_a = mask + acc_a * (1 - mask)

        # Flicker: a whole-frame black veil of 0-5%.
        over(np.full((height, width), rng.uniform(0.0, 0.05), dtype=np.float32), (0, 0, 0))

        # Speckle: many tiny white dots, clustered in a few drifting columns.
        n = int(rng.poisson(45))
        if n:
            m = np.zeros((height, width), dtype=np.float32)
            centers = rng.uniform(0, width, size=3)
            xs = np.clip(rng.choice(centers, n) + rng.normal(0, width * 0.08, n), 0, width - 1)
            ys = rng.uniform(0, height, n)
            m[ys.astype(int), xs.astype(int)] = rng.uniform(0.35, 0.9, n).astype(np.float32)
            m = cv2.GaussianBlur(m, (0, 0), 0.6) * 2.2
            over(np.clip(m, 0, 1), (238, 238, 234))

        # Dust: a few larger specks per frame, each lives one frame.
        for _ in range(int(rng.poisson(2.2))):
            m = np.zeros((height, width), dtype=np.float32)
            cx, cy = int(rng.uniform(0, width)), int(rng.uniform(0, height))
            ax, ay = int(rng.uniform(1, 4)), int(rng.uniform(1, 4))
            cv2.ellipse(m, (cx, cy), (ax, ay), rng.uniform(0, 180), 0, 360,
                        float(rng.uniform(0.35, 0.8)), -1, cv2.LINE_AA)
            over(m, (10, 10, 10) if rng.random() < 0.7 else (235, 235, 230))

        # Hair: occasionally a thin dark curve for a few frames.
        if hair is None and rng.random() < 1 / (fps * 3):
            pts = [(rng.uniform(0, width), rng.uniform(0, height))]
            for _ in range(5):
                px, py = pts[-1]
                pts.append((px + rng.normal(0, 18), py + rng.normal(0, 18)))
            hair = {"pts": np.array(pts, dtype=np.int32), "ttl": int(rng.integers(2, 5))}
        if hair:
            m = np.zeros((height, width), dtype=np.float32)
            cv2.polylines(m, [hair["pts"]], False, 0.55, 1, cv2.LINE_AA)
            over(m, (12, 12, 12))
            hair["ttl"] -= 1
            if hair["ttl"] <= 0:
                hair = None

        # Vertical scratches: persist 10-40 frames with a little horizontal jitter.
        if len(scratches) < 2 and rng.random() < 1 / (fps * 2):
            scratches.append({"x": rng.uniform(0.08, 0.92) * width,
                              "ttl": int(rng.integers(10, 40)),
                              "a": float(rng.uniform(0.12, 0.3))})
        for s in scratches:
            m = np.zeros((height, width), dtype=np.float32)
            x = int(s["x"] + rng.normal(0, 1.2))
            cv2.line(m, (x, 0), (x + int(rng.normal(0, 2)), height), s["a"], 1, cv2.LINE_AA)
            over(m, (228, 228, 222))
            s["ttl"] -= 1
        scratches = [s for s in scratches if s["ttl"] > 0]

        safe_a = np.where(acc_a > 1e-6, acc_a, 1.0)
        rgb = np.clip(acc_c / safe_a[..., None], 0, 1)
        rgba = np.empty((height, width, 4), dtype=np.uint8)
        rgba[..., :3] = np.round(rgb * 255).astype(np.uint8)
        rgba[..., 3] = np.round(np.clip(acc_a, 0, 1) * 255).astype(np.uint8)
        yield rgba


def glitch_frames(width: int, height: int, fps: int, seconds: float, *, seed: int = 23,
                  gap_s: tuple[float, float] = (2.5, 5.0),
                  palette=((232, 236, 240), (90, 210, 255), (255, 60, 90))):
    """Digital glitch bursts: transparent most of the time, a few frames of torn bands,
    chroma blocks and noise strips every few seconds.

    Bursts never touch the first or last frame, so the loop is seamless (both empty).
    An overlay can't displace the footage itself; for a real RGB split / pixel shift use
    Premiere's "VR Digital Glitch" on the adjustment layer.
    """
    rng = np.random.default_rng(seed)
    total = int(round(seconds * fps))
    bursts: dict[int, int] = {}                # frame -> burst id
    f = int(fps * rng.uniform(0.8, 1.6))
    b = 0
    while True:
        length = int(rng.integers(3, 9))
        if f + length >= total - 1:
            break
        for i in range(f, f + length):
            bursts[i] = b
        b += 1
        f += length + int(fps * rng.uniform(*gap_s))
    # Each burst tears around one or two horizontal zones, so its frames read as one event.
    zones = {i: rng.uniform(0.1, 0.9, size=int(rng.integers(1, 3))) * height for i in range(b)}
    empty = np.zeros((height, width, 4), dtype=np.uint8)
    cols = [np.array(c, dtype=np.float32) / 255.0 for c in palette]

    for f in range(total):
        if f not in bursts:
            yield empty
            continue
        acc_c = np.zeros((height, width, 3), dtype=np.float32)
        acc_a = np.zeros((height, width), dtype=np.float32)

        def over(y0, y1, x0, x1, a, c):
            y0, y1 = max(0, int(y0)), min(height, int(y1))
            x0, x1 = max(0, int(x0)), min(width, int(x1))
            if y1 <= y0 or x1 <= x0:
                return
            m = np.broadcast_to(np.float32(a) if np.isscalar(a) else a[: y1 - y0, : x1 - x0],
                                (y1 - y0, x1 - x0))
            acc_c[y0:y1, x0:x1] = m[..., None] * c + acc_c[y0:y1, x0:x1] * (1 - m[..., None])
            acc_a[y0:y1, x0:x1] = m + acc_a[y0:y1, x0:x1] * (1 - m)

        for zy in zones[bursts[f]]:
            zy += rng.normal(0, height * 0.03)
            # Torn band with a chroma split: cyan and red copies a few px apart, white core.
            h = rng.uniform(6, 50)
            x0 = rng.uniform(-0.2, 0.4) * width
            x1 = x0 + rng.uniform(0.4, 1.2) * width
            split = rng.uniform(10, 36)
            over(zy, zy + h, x0 - split, x1 - split, rng.uniform(0.10, 0.22), cols[1])
            over(zy, zy + h, x0 + split, x1 + split, rng.uniform(0.10, 0.22), cols[2])
            over(zy + h * 0.3, zy + h * 0.7, x0, x1, rng.uniform(0.08, 0.18), cols[0])
            # Chroma blocks scattered near the band.
            for _ in range(int(rng.integers(4, 15))):
                by = zy + rng.normal(0, h * 2)
                bx = rng.uniform(0, width)
                over(by, by + rng.uniform(4, 30), bx, bx + rng.uniform(20, 220),
                     rng.uniform(0.15, 0.45), cols[int(rng.integers(0, len(cols)))])
            # Noise strip: thin band of per-pixel static.
            ny, nh = zy + rng.normal(0, h), int(rng.uniform(2, 8))
            noise = (rng.random((nh, width)) < 0.5).astype(np.float32) * rng.uniform(0.2, 0.4)
            over(ny, ny + nh, 0, width, noise, cols[0])
        # Scanlines across the whole frame on some burst frames.
        if rng.random() < 0.35:
            lines = np.zeros((height, width), dtype=np.float32)
            lines[::3] = 0.10
            over(0, height, 0, width, lines, np.zeros(3, dtype=np.float32))

        safe_a = np.where(acc_a > 1e-6, acc_a, 1.0)
        rgba = np.empty((height, width, 4), dtype=np.uint8)
        rgba[..., :3] = np.round(np.clip(acc_c / safe_a[..., None], 0, 1) * 255).astype(np.uint8)
        rgba[..., 3] = np.round(np.clip(acc_a, 0, 1) * 255).astype(np.uint8)
        yield rgba


def convert_stock_overlay(src: Path, dst: Path, *, fps: int, width: int, height: int) -> Path:
    """Turn a screen-blend stock overlay (light marks on black) into an alpha overlay.

    alpha = luma, colour = unpremultiplied source: composited Normal it reproduces the
    Screen blend the stock was designed for.
    """
    vf = (f"scale={width}:{height}:force_original_aspect_ratio=increase,"
          f"crop={width}:{height},fps={fps},format=rgb24,split[c][l];"
          f"[l]format=gray[a];[c][a]alphamerge,unpremultiply=inplace=1,format=rgba")
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
                    "-filter_complex", vf, "-an", "-c:v", "png", str(dst)], check=True)
    return dst


# --- Channel preparation -------------------------------------------------------------

def prepare_channel(channel: Channel, *, force: bool = False, on_log=None) -> dict:
    """Build the channel's look assets (idempotent unless ``force``)."""
    log = on_log or (lambda _m: None)
    v, look = channel.video, channel.look
    made: dict[str, str] = {}

    lut = channel.path(look.lut)
    if force or not lut.exists():
        write_cube(lut, look.grade, title=f"{channel.slug} look")
        made["lut"] = str(lut)
        log(f"LUT: {lut}")

    vig = channel.path(look.vignette)
    if force or not vig.exists():
        write_vignette(vig, v.width, v.height, look.vignette_strength)
        made["vignette"] = str(vig)
        log(f"Vignette: {vig}")

    wm = channel.path(look.watermark.file)
    if force or not wm.exists():
        write_placeholder_watermark(wm, channel.name, v.width, v.height,
                                    look.watermark.width_frac, look.watermark.margin_frac)
        made["watermark"] = str(wm)
        log(f"Watermark (placeholder text): {wm}")

    generators = {"rain": lambda: rain_frames(v.width, v.height, v.fps, 10.0),
                  "film": lambda: film_frames(v.width, v.height, v.fps, 12.0),
                  "glitch": lambda: glitch_frames(v.width, v.height, v.fps, 12.0)}
    for ov in look.overlays:
        dst = channel.path(ov.file)
        if dst.exists() and not force:
            continue
        gen = generators.get(ov.name)
        if gen is None:
            log(f"Overlay '{ov.name}' has no generator; drop a file at {dst}")
            continue
        log(f"Rendering overlay '{ov.name}' -> {dst.name} ...")
        _encode_rgba(gen(), dst, v.width, v.height, v.fps, on_log=log)
        made[ov.name] = str(dst)
    return made


# --- Preview (and bake) --------------------------------------------------------------

def look_filtergraph(channel: Channel, n_overlays: int, *, grain: float = 7.0) -> str:
    """ffmpeg filter_complex for: input 0 = footage, 1..n = overlays, n+1 = vignette,
    n+2 = watermark. Paths inside filters are channel-relative (run with cwd=channel dir)
    to dodge Windows drive-letter escaping in filter arguments."""
    v, look = channel.video, channel.look
    lut_rel = Path(look.lut).as_posix()
    parts = [f"[0:v]scale={v.width}:{v.height}:force_original_aspect_ratio=increase,"
             f"crop={v.width}:{v.height},fps={v.fps},format=rgb24,"
             f"lut3d=file='{lut_rel}',noise=alls={grain}:allf=t,format=rgba[base0]"]
    last = "base0"
    for i, ov in enumerate(look.overlays[:n_overlays], start=1):
        parts.append(f"[{i}:v]format=rgba,colorchannelmixer=aa={ov.opacity}[ov{i}]")
        parts.append(f"[{last}][ov{i}]overlay=shortest=0:format=auto[base{i}]")
        last = f"base{i}"
    vi, wi = n_overlays + 1, n_overlays + 2
    parts.append(f"[{last}][{vi}:v]overlay=format=auto[vig]")
    parts.append(f"[{wi}:v]format=rgba,colorchannelmixer=aa={look.watermark.opacity}[wm]")
    parts.append("[vig][wm]overlay=format=auto,format=yuv420p[out]")
    return ";".join(parts)


def render_preview(channel: Channel, footage: Path, out: Path, *, start: float = 0.0,
                   seconds: float = 10.0, on_log=None) -> tuple[Path, Path]:
    """Render ``seconds`` of ``footage`` with the full look. Returns (mp4, png still)."""
    look = channel.look
    overlays = [channel.path(o.file) for o in look.overlays if channel.path(o.file).exists()]
    inputs = ["-ss", f"{start:.3f}", "-t", f"{seconds:.3f}", "-i", str(footage.resolve())]
    for p in overlays:
        inputs += ["-stream_loop", "-1", "-i", str(p)]
    inputs += ["-loop", "1", "-i", str(channel.path(look.vignette)),
               "-loop", "1", "-i", str(channel.path(look.watermark.file))]
    graph = look_filtergraph(channel, len(overlays))
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = (["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + inputs +
           ["-filter_complex", graph, "-map", "[out]", "-t", f"{seconds:.3f}", "-an",
            "-c:v", "libx264", "-crf", "18", "-preset", "fast", str(out.resolve())])
    if on_log:
        on_log(f"Preview: {footage.name} @ {start:.1f}s -> {out.name}")
    res = subprocess.run(cmd, cwd=channel.root_dir, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg preview failed: {res.stderr[-800:]}")
    still = out.with_suffix(".png")
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-ss", f"{seconds / 2:.3f}", "-i", str(out.resolve()),
                    "-frames:v", "1", str(still.resolve())], check=True)
    return out, still
