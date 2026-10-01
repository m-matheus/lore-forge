"""Build the Midnight Realm thumbnail band as Photoshop-ready layers.

The design borrows the channel's own identity rather than another channel's swipes:

  - the art sinks into the night: a soft fade to #07080A, no hard edge;
  - an engraved amber rule crowned by the four-point star of the MR monogram's ring;
  - left, a gilded label with chamfered corners (a book-spine plate) for the length;
  - right, the MR seal with the channel name stacked beside it;
  - the title in parchment Cinzel, centred between them.

The bottom-right corner is deliberately left empty: YouTube draws the video's duration
there, and the layout is symmetric around that slot.

Every PNG is a full 1280x720 transparent canvas with its element already in place, so
the Photoshop script (band.jsx) only stacks them; text stays live in the PSD.

Usage:
    python scripts/make_thumb_band.py [--channel midnight-realm] [--art path/to/art.png]
                                      [--title "BLOODBORNE LORE"]

Outputs, in channels/<slug>/assets/thumb_band/:
    fade.png, rule.png, plaque.png, seal.png   layers for Photoshop
    sample_art.png                             key art to design against (from --art)
    preview.png, preview_small.png             mock-up at full size and at feed size
    band.jsx                                   copied here: builds the editable PSD
"""
from __future__ import annotations

import argparse
import math
import shutil
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
W, H = 1280, 720

# Layout (px on the 1280x720 canvas). The text anchors are repeated in band.jsx.
FADE_TOP, FADE_SOLID = 548, 630   # art -> night gradient
RULE_Y = 634
RULE_X = (132, 1148)              # symmetric around the duration slot on the right
CY = 678                          # centre line of the band's content
PLAQUE = (132, 652, 342, 704)     # x0, y0, x1, y1 of the gilded label
CHAMFER = 11
SEAL_X, SEAL_D = 972, 66          # MR seal centre x and diameter
CHANNEL_X = 1012                  # left edge of the stacked channel name
TITLE_MAX_W = 540

BG = (7, 8, 10)                   # #07080A
PARCHMENT = (237, 230, 214)       # #EDE6D6
AMBER = (224, 170, 85)            # #E0AA55
GOLD_HI, GOLD_LO = (246, 206, 132), (168, 112, 48)


def canvas() -> Image.Image:
    return Image.new("RGBA", (W, H), (0, 0, 0, 0))


def _noise(w: int, h: int, cell: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    small = Image.fromarray((rng.random((max(2, h // cell), max(2, w // cell))) * 255).astype(np.uint8))
    return np.asarray(small.resize((w, h), Image.BICUBIC), np.float32) / 255.0


def fade_layer() -> Image.Image:
    y = np.arange(H, dtype=np.float32)
    t = np.clip((y - FADE_TOP) / (FADE_SOLID - FADE_TOP), 0, 1)
    a = t * t * (3 - 2 * t)                                   # smoothstep
    img = np.zeros((H, W, 4), np.uint8)
    img[..., :3] = BG
    img[..., 3] = (a[:, None] * 255).astype(np.uint8)
    return Image.fromarray(img, "RGBA")


def star(cx: float, cy: float, rv: float, rh: float, inner: float) -> list[tuple[float, float]]:
    """Four-point star, taller than wide, like the cardinal points on the MR ring."""
    pts = []
    for i in range(8):
        ang = -math.pi / 2 + i * math.pi / 4
        r = (rv if i % 4 == 0 else rh) if i % 2 == 0 else inner
        pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    return pts


def rule_layer() -> Image.Image:
    x0, x1 = RULE_X
    line = np.zeros((H, W), np.float32)
    xs = np.arange(W, dtype=np.float32)
    ends = np.clip(np.minimum(xs - x0, x1 - xs) / 140, 0, 1)  # taper toward both ends
    gap = np.clip((np.abs(xs - W / 2) - 22) / 6, 0, 1)        # break for the star
    line[RULE_Y] = ends * gap
    line[RULE_Y + 1] = ends * gap * 0.45
    img = canvas()
    arr = np.zeros((H, W, 4), np.uint8)
    arr[..., :3] = AMBER
    arr[..., 3] = (np.clip(line, 0, 1) * 235).astype(np.uint8)
    img = Image.fromarray(arr, "RGBA")

    mark = canvas()
    d = ImageDraw.Draw(mark)
    d.polygon(star(W / 2, RULE_Y + 0.5, 15, 10, 2.6), fill=AMBER + (255,))
    for dx in (-30, 30):                                      # small flanking diamonds
        d.polygon(star(W / 2 + dx, RULE_Y + 0.5, 3.2, 3.2, 1.2), fill=AMBER + (220,))
    glow = mark.filter(ImageFilter.GaussianBlur(6))
    img.alpha_composite(glow)
    img.alpha_composite(glow)
    img.alpha_composite(mark)
    return img


def plaque_layer() -> Image.Image:
    x0, y0, x1, y1 = PLAQUE
    w, h, c = x1 - x0, y1 - y0, CHAMFER
    shape = [(c, 0), (w - c, 0), (w, c), (w, h - c), (w - c, h), (c, h), (0, h - c), (0, c)]

    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).polygon(shape, fill=255)

    # Gilded fill: vertical gradient, brushed-metal streaks, a little wear.
    t = np.linspace(0, 1, h, dtype=np.float32)[:, None, None]
    grad = np.array(GOLD_HI, np.float32) * (1 - t) + np.array(GOLD_LO, np.float32) * t
    streak = 0.92 + 0.08 * _noise(w, h, 1, 3)[..., None] * _noise(w, h, 6, 4)[..., None]
    wear = 1 - 0.07 * np.clip((_noise(w, h, 14, 5) - 0.6) * 3, 0, 1)[..., None]
    rgb = np.clip(np.broadcast_to(grad, (h, w, 3)) * streak * wear, 0, 255).astype(np.uint8)
    plate = Image.fromarray(rgb, "RGB").convert("RGBA")
    plate.putalpha(mask)

    # Engraved inner border and a bright top bevel.
    d = ImageDraw.Draw(plate)
    inset = 4
    inner = [(c, inset), (w - c, inset), (w - inset, c), (w - inset, h - c), (w - c, h - inset),
             (c, h - inset), (inset, h - c), (inset, c)]
    d.line(inner + [inner[0]], fill=(92, 58, 20, 170), width=1)
    d.line([(c, 1), (w - c, 1)], fill=(255, 236, 190, 200), width=1)

    img = canvas()
    shadow = canvas()
    ImageDraw.Draw(shadow).polygon([(x0 + px, y0 + py + 3) for px, py in shape], fill=(0, 0, 0, 200))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(5)))
    img.alpha_composite(plate, (x0, y0))
    return img


def seal_layer(avatar: Path) -> Image.Image:
    """The MR monogram from the avatar: gold on black, so luminance becomes alpha."""
    src = Image.open(avatar).convert("RGB")
    arr = np.asarray(src, np.float32)
    lum = arr @ np.array([0.299, 0.587, 0.114], np.float32)
    alpha = np.clip((lum - 18) / 70, 0, 1)
    rgb = np.clip(arr / np.maximum(alpha[..., None], 1e-3), 0, 255)   # un-premultiply the black
    mono = Image.fromarray(np.dstack([rgb, alpha * 255]).astype(np.uint8), "RGBA")
    mono = mono.crop(mono.getbbox()).resize((SEAL_D, SEAL_D), Image.LANCZOS)
    img = canvas()
    img.alpha_composite(mono, (SEAL_X - SEAL_D // 2, CY - SEAL_D // 2))
    return img


def _font(path: Path, size: int, weight: str) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(path), size)
    font.set_variation_by_name(weight)
    return font


def _tracked_width(text: str, font, tracking: float) -> float:
    d = ImageDraw.Draw(canvas())
    return sum(d.textlength(ch, font=font) for ch in text) + tracking * font.size * (len(text) - 1)


def draw_tracked(img: Image.Image, text: str, x: float, cy: float, font, fill, tracking: float,
                 align: str = "center") -> None:
    """Draw with letter spacing (tracking in em), vertically centred on cy."""
    d = ImageDraw.Draw(img)
    width = _tracked_width(text, font, tracking)
    cx = x - width / 2 if align == "center" else x
    for ch in text:
        d.text((cx, cy), ch, font=font, fill=fill, anchor="lm")
        cx += d.textlength(ch, font=font) + tracking * font.size


def cover(img: Image.Image) -> Image.Image:
    scale = max(W / img.width, H / img.height)
    img = img.resize((math.ceil(img.width * scale), math.ceil(img.height * scale)), Image.LANCZOS)
    left, top = (img.width - W) // 2, (img.height - H) // 2
    return img.crop((left, top, left + W, top + H))


def preview(out: Path, layers: dict[str, Image.Image], art: Image.Image | None,
            font_path: Path, title: str) -> None:
    img = art.convert("RGBA") if art else Image.new("RGBA", (W, H), (40, 44, 52, 255))
    for name in ("fade", "rule", "plaque", "seal"):
        img.alpha_composite(layers[name])

    px0, _, px1, _ = PLAQUE
    draw_tracked(img, "2+ HOURS", (px0 + px1) / 2, CY + 1, _font(font_path, 24, "Black"), BG, 0.12)
    size = 42
    while size > 20 and _tracked_width(title, _font(font_path, size, "Black"), 0.06) > TITLE_MAX_W:
        size -= 1
    draw_tracked(img, title, W / 2, CY, _font(font_path, size, "Black"), PARCHMENT, 0.06)
    small = _font(font_path, 16, "Bold")
    draw_tracked(img, "MIDNIGHT", CHANNEL_X, CY - 10, small, PARCHMENT, 0.18, align="left")
    draw_tracked(img, "REALM", CHANNEL_X, CY + 11, small, AMBER, 0.18, align="left")

    img = img.convert("RGB")
    img.save(out / "preview.png")
    img.resize((360, 203), Image.LANCZOS).save(out / "preview_small.png")   # feed size


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--channel", default="midnight-realm")
    ap.add_argument("--art", type=Path, help="key art to design against (any size, cover-cropped)")
    ap.add_argument("--title", default="BLOODBORNE LORE")
    args = ap.parse_args()

    ch_dir = ROOT / "channels" / args.channel
    out = ch_dir / "assets" / "thumb_band"
    out.mkdir(parents=True, exist_ok=True)

    layers = {"fade": fade_layer(), "rule": rule_layer(), "plaque": plaque_layer(),
              "seal": seal_layer(ch_dir / "assets" / "brand" / "avatar.png")}
    for name, img in layers.items():
        img.save(out / f"{name}.png", optimize=True)

    art = cover(Image.open(args.art)) if args.art else None
    if art:
        art.convert("RGB").save(out / "sample_art.png")
    preview(out, layers, art, ch_dir / "assets" / "fonts" / "title.ttf", args.title)
    shutil.copy(Path(__file__).with_name("band.jsx"), out / "band.jsx")
    print(f"Wrote {sorted(p.name for p in out.iterdir())} to {out}")


if __name__ == "__main__":
    main()
