"""Channel brand assets: avatar, banner, video watermark and thumbnail band.

Same split as the thumbnails: generated art carries no lettering (models mangle type),
and every word is drawn locally in the channel's font, so the wordmark is identical on
the banner, on the watermark burned into each video and on the thumbnail badge.

The avatar is the exception. It is a logo, not a scene, so it is briefed to the model the
way you would brief a designer — two directions, one accent colour, a legibility floor —
and the two initials are part of the drawing rather than type set over it.

Sizes follow YouTube's requirements: 800x800 avatar, 2560x1440 banner with the 1546x423
centre area that survives on every device, and a full-frame watermark matching the
video resolution.
"""
from __future__ import annotations

from pathlib import Path

import segno
from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from loreforge.config.channels import Channel
from loreforge.integrations import openai_images
from loreforge.integrations.media import run_ffmpeg

AVATAR = 800
BACKGROUND = (7, 8, 10, 255)   # #07080A, the near-black everything else sits on
BANNER = (2560, 1440)
BANNER_SAFE = (1546, 423)        # centred area visible on every device
PODCAST_COVER = 3000             # Spotify's maximum; the minimum is 1400

AVATAR_PROMPT = """
Design a professional, high-quality logo for a YouTube channel called "{name}".
The channel publishes two-hour video game lore documentaries narrated quietly for people
to fall asleep to — gothic, nocturnal, slow. Similar channels: Nightfall Lore, and the
wider "lore to fall asleep to" genre. The logo will be used as the YouTube channel icon
(square format).

STYLE DIRECTION:
Create a bold, iconic emblem logo. Near-black background. The design should feel old,
carved and premium — an inscription on a monument, a seal pressed into wax, the
frontispiece of a book nobody has opened in a century. Calm and nocturnal, never horror:
no blood, no claws, no dripping gothic cliche. Instantly recognizable at small sizes.

DESIGN CONCEPT — choose the strongest of these two directions:

Option A — Interlocked lettermark:
The letters "{initials}" treated as a single custom-drawn monogram, the two letterforms
sharing a stem or nesting into each other. Inscriptional Roman capitals in the spirit of
Cinzel or Trajan: high contrast between thick and thin strokes, sharp triangular serifs,
chiselled rather than typed. Aged gold, with a faint warm glow as if lamplight were
falling across carved stone.

Option B — Lettermark in a seal:
The same "{initials}" monogram set inside a struck medallion or wax seal: a circular
border of fine engraved line work, worn at the edges, the letters raised at the centre.
The border is restrained — a single ring and a hairline, not a crowded wreath.

TECHNICAL REQUIREMENTS:
- Square 1:1 composition, centred, generous margin, works at 800x800px minimum
- Near-black background ({background})
- Exactly one accent colour: aged amber gold ({accent}). Nothing else is coloured.
- NO text other than the "{initials}" mark — no channel name, no tagline, no wordmark
- Clean edges, high contrast — must be legible at 88x88px and still read at 24x24px
- Professional logo quality, as if drawn by a senior brand designer at a creative agency
- No cheap gradients, no bevel, no drop shadow, no lens flare; controlled transitions only
- The overall impression: quiet, old, premium, nocturnal

Output: the logo centred on its dark square background. No mockups, no phone screens,
no merchandise, no presentation boards. Just the clean logo on its dark background.
""".strip()

BANNER_PROMPT = """
A very wide, very dark night landscape for a channel banner: distant silhouettes of
towers, rooftops and bare trees along the bottom edge, under a large crescent moon in
the upper right. A single warm amber lantern light glows somewhere in the silhouettes.
Thin mist, fine rain, deep shadow.

The CENTRE of the image must stay almost empty and dark: a plain expanse of night sky
where text will be placed later. Painterly, muted, desaturated except for the amber light.

ABSOLUTELY NO TEXT, no letters, no numbers, no logos, no borders.
""".strip()


def _font(channel: Channel, size: int) -> ImageFont.FreeTypeFont:
    for candidate in (channel.path(channel.thumbnail.title_font),
                      Path("C:/Windows/Fonts/georgiab.ttf")):
        try:
            return ImageFont.truetype(str(candidate), size)
        except OSError:
            continue
    return ImageFont.load_default()


def _tracked_text(draw: ImageDraw.ImageDraw, xy, text: str, font, fill, tracking: float,
                  centre: bool = True) -> float:
    """Draw text with letter spacing (Pillow has none). Returns the width drawn."""
    widths = [draw.textlength(ch, font=font) for ch in text]
    total = sum(widths) + tracking * max(0, len(text) - 1)
    x, y = xy
    if centre:
        x -= total / 2
    for ch, w in zip(text, widths):
        draw.text((x, y), ch, font=font, fill=fill)
        x += w + tracking
    return total


def _glow(img: Image.Image, layer: Image.Image, radius: int = 18) -> None:
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(radius)))


def wordmark_layer(channel: Channel, size: tuple[int, int], *, height_frac: float,
                   centre: tuple[float, float], tagline: bool) -> Image.Image:
    """The channel name (and optionally the tagline) as its own RGBA layer."""
    w, h = size
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    font = _font(channel, max(12, int(h * height_frac)))
    cx, cy = centre[0] * w, centre[1] * h
    _tracked_text(draw, (cx, cy - font.size * 0.7), channel.name.upper(), font,
                  channel.thumbnail.title_color, font.size * 0.14)
    if tagline and channel.tagline:
        small = _font(channel, max(10, int(font.size * 0.26)))
        _tracked_text(draw, (cx, cy + font.size * 0.6), channel.tagline.upper(), small,
                      channel.thumbnail.badge_color, small.size * 0.35)
    return layer


def initials(channel: Channel) -> str:
    """The channel's initials: one capital per word of the name ("Midnight Realm" -> "MR")."""
    return "".join(w[0] for w in channel.name.split() if w[0].isalpha()).upper()[:3]


def avatar_prompt(channel: Channel) -> str:
    return AVATAR_PROMPT.format(name=channel.name.upper(), initials=initials(channel),
                                background="#07080A", accent=channel.thumbnail.badge_color)


def build_avatar(channel: Channel, art_path: Path, out: Path) -> Path:
    """Square avatar from the generated logo art.

    The image model returns 1536x1024, so the logo is centre-cropped to a square and
    resized. YouTube masks the avatar to a circle, so the brief asks for a generous
    margin and nothing is cropped into here beyond the square.
    """
    art = Image.open(art_path).convert("RGB")
    side = min(art.size)
    art = art.crop(((art.width - side) // 2, (art.height - side) // 2,
                    (art.width + side) // 2, (art.height + side) // 2))
    art.resize((AVATAR, AVATAR), Image.LANCZOS).save(out, quality=95)
    return out


def build_banner(channel: Channel, art_path: Path, out: Path) -> Path:
    """2560x1440 banner with the wordmark and tagline inside the safe area."""
    art = Image.open(art_path).convert("RGBA")
    scale = max(BANNER[0] / art.width, BANNER[1] / art.height)
    art = art.resize((round(art.width * scale), round(art.height * scale)), Image.LANCZOS)
    left, top = (art.width - BANNER[0]) // 2, (art.height - BANNER[1]) // 2
    img = art.crop((left, top, left + BANNER[0], top + BANNER[1]))
    img = ImageEnhance.Color(ImageEnhance.Brightness(img).enhance(0.72)).enhance(0.8)

    veil = Image.new("RGBA", BANNER, (0, 0, 0, 0))
    sw, sh = BANNER_SAFE
    ImageDraw.Draw(veil).rectangle(
        [(BANNER[0] - sw) // 2, (BANNER[1] - sh) // 2,
         (BANNER[0] + sw) // 2, (BANNER[1] + sh) // 2], fill=(5, 6, 9, 185))
    img.alpha_composite(veil.filter(ImageFilter.GaussianBlur(90)))

    words = wordmark_layer(channel, BANNER, height_frac=0.085, centre=(0.5, 0.47), tagline=True)
    _glow(img, words, 26)
    img.alpha_composite(words)
    img.convert("RGB").save(out, quality=92)
    return out


def _fit_font(channel: Channel, draw: ImageDraw.ImageDraw, text: str, width: float,
              tracking_frac: float) -> tuple[ImageFont.FreeTypeFont, float]:
    """Largest font (and its tracking) whose tracked ``text`` fits within ``width``."""
    size = 400
    font = _font(channel, size)
    natural = sum(draw.textlength(c, font=font) for c in text)
    total = natural + size * tracking_frac * (len(text) - 1)
    size = int(size * width / total)
    return _font(channel, size), size * tracking_frac


def build_podcast_cover(channel: Channel, logo_art: Path, scene_art: Path, out: Path) -> Path:
    """3000x3000 podcast cover: the night scene, the monogram, the name and the tagline.

    Podcast apps show the cover at thumbnail size, so the name is set as wide as the
    square allows and the monogram is the only image element that has to read.
    """
    side = PODCAST_COVER
    scene = Image.open(scene_art).convert("RGBA")
    crop = min(scene.size)
    scene = scene.crop((scene.width - crop, 0, scene.width, crop))   # moon and spires
    img = scene.resize((side, side), Image.LANCZOS).filter(ImageFilter.GaussianBlur(3))
    img = ImageEnhance.Color(ImageEnhance.Brightness(img).enhance(0.6)).enhance(0.8)

    veil = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    ImageDraw.Draw(veil).ellipse([side * 0.12, side * 0.08, side * 0.88, side * 0.92],
                                 fill=(5, 6, 9, 170))
    img.alpha_composite(veil.filter(ImageFilter.GaussianBlur(side * 0.08)))

    # The logo sits on near-black, so a screen blend drops the black and keeps the gold.
    logo = Image.open(logo_art).convert("RGB")
    sq = min(logo.size)
    logo = logo.crop(((logo.width - sq) // 2, 0, (logo.width + sq) // 2, sq))
    logo_side = int(side * 0.56)
    logo = logo.resize((logo_side, logo_side), Image.LANCZOS)
    box = ((side - logo_side) // 2, int(side * 0.07))
    region = img.crop((box[0], box[1], box[0] + logo_side, box[1] + logo_side)).convert("RGB")
    img.paste(ImageChops.screen(region, logo), box)

    words = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    draw = ImageDraw.Draw(words)
    name = channel.name.upper()
    font, tracking = _fit_font(channel, draw, name, side * 0.84, 0.12)
    _tracked_text(draw, (side / 2, side * 0.69), name, font,
                  channel.thumbnail.title_color, tracking)
    if channel.tagline:
        tag = channel.tagline.upper()
        small, small_tracking = _fit_font(channel, draw, tag, side * 0.80, 0.22)
        _tracked_text(draw, (side / 2, side * 0.69 + font.size * 1.45), tag, small,
                      channel.thumbnail.badge_color, small_tracking)
    _glow(img, words, 30)
    img.alpha_composite(words)
    img.convert("RGB").save(out, quality=95)
    return out


def spotify_qr_frame(channel: Channel, url: str) -> Image.Image:
    """Full-frame transparent still: the QR code on a dark plate, captioned in Cinzel.

    Full-frame for the same reason as the watermark: the timeline needs no motion
    parameters. The plate keeps a constant dark field behind the code, so bright
    footage passing underneath can't break the scan.
    """
    spec = channel.spotify_notice
    w, h = channel.video.width, channel.video.height
    matrix = segno.make(url, error="m").matrix
    quiet = 3                                            # modules of plate around the code
    module = max(2, int(h * spec.qr_height_frac / len(matrix)))
    code = module * len(matrix)
    caption = _font(channel, max(10, int(module * 2.6)))
    pad = quiet * module
    plate_w, plate_h = code + 2 * pad, code + 2 * pad + int(caption.size * 1.3)

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    x0 = int(spec.qr_center[0] * w - plate_w / 2)
    y0 = int(spec.qr_center[1] * h - plate_h / 2)
    cream, night = ImageColor.getrgb(channel.thumbnail.title_color), BACKGROUND[:3]
    ink, plate = (cream, night) if spec.qr_inverted else (night, cream)
    draw.rounded_rectangle([x0, y0, x0 + plate_w, y0 + plate_h], radius=module * 2,
                           fill=plate + (235,))
    for r, row in enumerate(matrix):
        for c, on in enumerate(row):
            if on:
                x, y = x0 + pad + c * module, y0 + pad + r * module
                draw.rectangle([x, y, x + module - 1, y + module - 1], fill=ink + (255,))
    _tracked_text(draw, (x0 + plate_w / 2, y0 + pad + code + 2 * module),   # clear of the code
                  "SCAN TO LISTEN", caption, ink + (255,), caption.size * 0.2)
    return img


def build_spotify_qr(channel: Channel, url: str, out: Path) -> Path:
    """The QR still as a clip with alpha, fading in and out over the notice's duration."""
    spec = channel.spotify_notice
    out.parent.mkdir(parents=True, exist_ok=True)
    still = out.with_suffix(".png")
    spotify_qr_frame(channel, url).save(still)
    fade, dur = 1.0, spec.duration
    run_ffmpeg(["-loop", "1", "-framerate", str(channel.video.fps), "-t", f"{dur}",
                "-i", str(still),
                "-vf", f"fade=t=in:st=0:d={fade}:alpha=1,"
                       f"fade=t=out:st={dur - fade}:d={fade}:alpha=1",
                "-c:v", "png", "-pix_fmt", "rgba", str(out)])
    return out


def build_watermark(channel: Channel, out: Path) -> Path:
    """Full-frame transparent PNG with the wordmark in the top-right corner.

    Full-frame so the timeline never needs motion parameters: the XML simply lays it
    over the video, which is the part of FCP7 import that always behaves.
    """
    w, h = channel.video.width, channel.video.height
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = _font(channel, max(12, int(h * 0.021)))
    right = w * (1 - channel.look.watermark.margin_frac)
    y = h * channel.look.watermark.margin_frac
    for i, line in enumerate(channel.name.upper().split()):
        width = sum(draw.textlength(c, font=font) for c in line) + font.size * 0.2 * (len(line) - 1)
        _tracked_text(draw, (right - width, y + i * font.size * 1.3), line, font,
                      channel.thumbnail.title_color, font.size * 0.2, centre=False)
    img.save(out)
    return out


def build_band(channel: Channel, out: Path, width: int = 1280, height: int = 122) -> Path:
    """Grunge bottom band for thumbnails: dark gradient with a little film texture."""
    import numpy as np

    rng = np.random.default_rng(5)
    y = np.linspace(0, 1, height)[:, None]
    alpha = np.clip(0.55 + 0.45 * y, 0, 1) * 255
    noise = rng.normal(0, 7, (height, width))
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    for c, base in enumerate((7, 8, 11)):
        rgba[..., c] = np.clip(base + noise, 0, 255).astype(np.uint8)
    rgba[..., 3] = np.clip(alpha + noise * 2, 0, 255).astype(np.uint8)
    Image.fromarray(rgba, "RGBA").filter(ImageFilter.GaussianBlur(0.6)).save(out)
    return out


def run(channel: Channel, *, force: bool = False, on_log=None) -> dict:
    """Build every brand asset. The two AI images are only regenerated with ``force``."""
    log = on_log or (lambda _m: None)
    made: dict[str, str] = {}
    brand_dir = channel.path("assets/brand")
    brand_dir.mkdir(parents=True, exist_ok=True)

    avatar_art = brand_dir / "avatar_art.png"
    if force or not avatar_art.exists():
        log("Generating the avatar logo...")
        openai_images.generate_image(avatar_prompt(channel), avatar_art, on_log=log)
    banner_art = brand_dir / "banner_art.png"
    if force or not banner_art.exists():
        log("Generating the banner art...")
        openai_images.generate_image(BANNER_PROMPT, banner_art, on_log=log)

    made["avatar"] = str(build_avatar(channel, avatar_art, brand_dir / "avatar.png"))
    made["banner"] = str(build_banner(channel, banner_art, brand_dir / "banner.png"))
    made["podcast_cover"] = str(build_podcast_cover(channel, avatar_art, banner_art,
                                                    brand_dir / "podcast_cover.jpg"))
    made["watermark"] = str(build_watermark(channel, channel.path(channel.look.watermark.file)))
    made["band"] = str(build_band(channel, channel.path(channel.thumbnail.band)))
    if channel.spotify_link:
        made["spotify_qr"] = str(build_spotify_qr(channel, channel.spotify_link,
                                                  channel.path(channel.spotify_notice.qr)))
    elif channel.spotify_url:
        log("spotify_url is not a public open.spotify.com link; no QR code built.")
    for key, value in made.items():
        log(f"  {key}: {value}")
    return made
