"""Thumbnail: AI key art + text composited locally.

The art is generated without any text (models mangle lettering), then the title and the
bottom badge are drawn with PIL from the channel template. That split is what keeps the
thumbnails consistent video after video: same fonts, same band, same placement, only
the art changes.
"""
from __future__ import annotations

import json
import math
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from loreforge.integrations import anthropic_client as ai
from loreforge.integrations import openai_images
from loreforge.models.project import VideoProject

WIDTH, HEIGHT = 1280, 720

ART_BASELINE = """
Create key art for a video about the video game {game}, in the style of a dark, painted
book cover. It will be used as the background of a YouTube thumbnail for a sleep channel,
so it must read as calm and heavy, not as an action poster.

COMPOSITION: one iconic location or silhouette from the game, seen at night, at a
distance, with deep empty space in the upper left and along the bottom edge where text
will be placed later. No close-up faces, no combat, no crowds.

LIGHT: a single warm light source (a lamp, a fire, a window, the moon) against cold
darkness. Most of the frame is in shadow; the eye should land on that one light.

STYLE: painterly, textured brushwork, muted and desaturated except for the warm light.
Heavy atmosphere: fog, fine rain, smoke. Think of an oil painting, not a render.

ABSOLUTELY NO TEXT, no letters, no numbers, no logos, no watermarks, no UI.
""".strip()

ART_DIRECTION_SYSTEM = """\
You are the art director of a video game lore sleep channel. Given a game and the scope
of tonight's video, choose the single most recognisable NIGHT-TIME location or silhouette
to paint, and the one warm light source in it. Prefer what a fan recognises in a
thumbnail at small size. Never choose a character's face or a fight.
"""

ART_DIRECTION_SCHEMA = ai.obj({
    "subject": ai.STR, "light": ai.STR, "mood": ai.STR, "title_text": ai.STR})


def _fit_font(draw: ImageDraw.ImageDraw, text: str, font_path: Path, max_width: float,
              max_size: int = 200) -> ImageFont.FreeTypeFont:
    lo, hi = 20, max_size
    while lo < hi:
        mid = (lo + hi + 1) // 2
        font = _load_font(font_path, mid)
        if draw.textlength(text, font=font) <= max_width:
            lo = mid
        else:
            hi = mid - 1
    return _load_font(font_path, lo)


def _load_font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    for candidate in (path, Path("C:/Windows/Fonts/georgiab.ttf"), Path("C:/Windows/Fonts/timesbd.ttf")):
        try:
            return ImageFont.truetype(str(candidate), size)
        except OSError:
            continue
    return ImageFont.load_default()


def _shadowed_text(img: Image.Image, xy: tuple[int, int], text: str, font, fill: str,
                   anchor: str = "la") -> None:
    """Draw text with a soft dark halo so it stays legible over any art."""
    halo = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(halo).text(xy, text, font=font, fill=(0, 0, 0, 235), anchor=anchor)
    img.alpha_composite(halo.filter(ImageFilter.GaussianBlur(9)))
    ImageDraw.Draw(img).text(xy, text, font=font, fill=fill, anchor=anchor)


def compose(project: VideoProject, art_path: Path, title_text: str, out: Path) -> Path:
    """Darken the art, then draw the title and the bottom badge from the channel template."""
    ch = project.channel
    tpl = ch.thumbnail
    art = Image.open(art_path).convert("RGBA")
    scale = max(WIDTH / art.width, HEIGHT / art.height)
    art = art.resize((math.ceil(art.width * scale), math.ceil(art.height * scale)), Image.LANCZOS)
    left = (art.width - WIDTH) // 2
    top = (art.height - HEIGHT) // 2
    img = art.crop((left, top, left + WIDTH, top + HEIGHT))
    img = ImageEnhance.Color(ImageEnhance.Brightness(img).enhance(0.82)).enhance(0.75)

    # Bottom band: the channel's asset when present, else a painted gradient.
    band_h = int(HEIGHT * 0.17)
    band_path = ch.path(tpl.band)
    if band_path.exists():
        band = Image.open(band_path).convert("RGBA").resize((WIDTH, band_h), Image.LANCZOS)
    else:
        band = Image.new("RGBA", (WIDTH, band_h), (0, 0, 0, 0))
        px = band.load()
        for y in range(band_h):
            a = int(235 * min(1.0, 0.25 + y / band_h))
            for x in range(WIDTH):
                px[x, y] = (8, 9, 11, a)
    img.alpha_composite(band, (0, HEIGHT - band_h))

    draw = ImageDraw.Draw(img)
    title_font = _fit_font(draw, title_text.upper(), ch.path(tpl.title_font), WIDTH * 0.88, 190)
    _shadowed_text(img, (WIDTH // 2, int(HEIGHT * 0.40)), title_text.upper(), title_font,
                   tpl.title_color, anchor="mm")

    badge = tpl.badge_template.format(game_upper=project.game.upper(),
                                      channel_upper=ch.name.upper(),
                                      game=project.game, channel=ch.name)
    badge_font = _fit_font(draw, badge, ch.path(tpl.badge_font), WIDTH * 0.9, 44)
    _shadowed_text(img, (WIDTH // 2, HEIGHT - band_h // 2), badge, badge_font,
                   tpl.badge_color, anchor="mm")

    out.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out, quality=90, optimize=True)
    return out


# First try: the references go to the image tool as a mood board (REF_REQUEST below), which
# keeps their look closest. But they are usually official key art or other channels'
# thumbnails, and the tool may refuse anything that reads as a copy of them. Blurring their
# lettering did not help (The Witcher was refused three times in a row): the art itself is what
# it objects to. So on a refusal the references are left out: Claude reads them as a mood board
# and writes a visual-direction spec plus a new subject, and the image is generated from that
# text alone. Either way the art has no lettering; the title is composited locally where the
# references had it.
REFERENCE_DIRECTION_SYSTEM = """\
You are the art director of a video game lore channel. The attached thumbnails are a mood
board: they show the visual language the channel wants, not images to copy. Describe that
language as a general design spec: composition and subject placement, camera distance,
lighting, colour relationships, atmosphere, contrast, art style, mood, and where the image
leaves calm space for a title. Describe it in general terms; do not name the references'
exact scene, logos or lettering.

Then choose the subject of a NEW scene for the given game: an original moment from its world,
of the same kind as the references' subject. If they centre a character, use that game's
canonical character by name, in a new pose and place, with a different setting and camera
angle from the references. One or two concrete sentences, enough to paint from.
"""
REFERENCE_DIRECTION_SCHEMA = ai.obj({k: ai.STR for k in (
    "composition", "camera", "lighting", "palette", "atmosphere", "contrast", "style", "mood",
    "title_space", "subject")})

MOOD_REQUEST = (
    "Create a completely new cinematic thumbnail artwork for a video about the video game "
    "{game}.\n\n"
    "VISUAL DIRECTION:\n{direction}\n\n"
    "SUBJECT: {subject}\n\n"
    "Characters from {game} appear as they canonically look, in this new scene and composition. "
    "Prioritise strong silhouettes, dramatic lighting, atmospheric depth and readability at "
    "YouTube thumbnail size. Do not reproduce any existing promotional artwork, cover art, "
    "screenshot or thumbnail.\n\n"
    "NO text, NO letters, NO logos, NO watermarks, NO UI."
)

# The default first attempt (skipped with params reference=mood).
REF_REQUEST = (
    "Create a completely new cinematic thumbnail artwork for a video about the video game "
    "{game}.\n\n"
    "The attached images are visual mood-board references only. Use them to understand the "
    "visual language, atmosphere, lighting, color relationships, character presentation and "
    "overall thumbnail readability.\n\n"
    "Create a substantially different composition and scene. Do not recreate, trace, closely "
    "imitate or reproduce any reference image. Do not reproduce the reference layout, camera "
    "angle, pose, typography, logo placement or background arrangement.\n\n"
    "The artwork should depict an original moment from the world of {game}, with its "
    "recognizable canonical characters portrayed naturally in a new pose and environment. "
    "Prioritize cinematic storytelling, strong silhouettes, dramatic lighting, atmospheric "
    "depth and readability at YouTube thumbnail size.\n\n"
    "NO text, NO letters, NO logos, NO watermarks, NO UI."
)

TITLE_ART_REQUEST = (
    'The title "{game}" in large lettering, centred on a plain pure-black background. '
    "Lettering style: {style}. Light-coloured letters. Nothing else in the image."
)

LETTERING_SYSTEM = """\
You locate lettering in thumbnail images. Return the bounding box of every piece of
lettering (titles, captions, logos, badges) as fractions of the image width and height
(0 = left/top, 1 = right/bottom), with an empty list when there is none. Also describe the
main title's lettering style in one sentence (typeface feel, weight, texture, colour, any
glow or effects) without naming a font or a brand; empty when there is no title.
"""
_NUM = {"type": "number"}
LETTERING_SCHEMA = ai.obj({
    "boxes": ai.array(ai.obj({"x0": _NUM, "y0": _NUM, "x1": _NUM, "y1": _NUM})),
    "title_style": ai.STR})


def _refs(project: VideoProject) -> list[Path]:
    d = project.thumbnail_refs_dir
    if not d.exists():
        return []
    return sorted(p for p in d.glob("*") if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"))


def _image_block(ref: Path) -> dict:
    """A reference as a PNG content block for Claude, capped at 1568 px (its useful maximum)."""
    img = Image.open(ref).convert("RGB")
    img.thumbnail((1568, 1568))
    buf = BytesIO()
    img.save(buf, "PNG")
    return ai.image_block(buf.getvalue())


def _lettering(project: VideoProject, ref: Path) -> dict:
    """Claude's read of one reference: lettering boxes + title style (cached as JSON)."""
    cache = project.thumbnail_dir / "refs_analysis" / f"{ref.stem}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    data, comp = ai.complete_json(
        LETTERING_SYSTEM, [_image_block(ref), ai.plain("Find the lettering.")],
        schema=LETTERING_SCHEMA, max_tokens=2000, effort="low")
    project.add_costs(**comp.cost_deltas())
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def _reference_direction(project: VideoProject, refs: list[Path], refresh: bool, log) -> dict:
    """Claude's visual-direction spec of the references + a new subject (cached; ``refresh``
    asks for a new one, so Redo also gets a new scene)."""
    path = project.thumbnail_dir / "ref_direction.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    user = [*(_image_block(r) for r in refs), ai.plain(
        f"Game: {project.game}\nScope: {project.scope}\n"
        "Return the design spec of these references and the subject of the new scene.")]
    data, comp = ai.complete_json(REFERENCE_DIRECTION_SYSTEM, user,
                                  schema=REFERENCE_DIRECTION_SCHEMA, max_tokens=3000, effort="low")
    project.add_costs(**comp.cost_deltas())
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    log(f"Reference direction: {data['subject']} | light: {data['lighting']}")
    return data


def _mood_prompt(game: str, d: dict) -> str:
    lines = [f"- {k.capitalize()}: {d[k]}" for k in (
        "composition", "camera", "lighting", "palette", "atmosphere", "contrast", "style", "mood")]
    lines.append(f"- Leave calm, uncluttered space {d['title_space']} for a title added later.")
    return MOOD_REQUEST.format(game=game, direction="\n".join(lines), subject=d["subject"])


def _reference_title(project: VideoProject, refs: list[Path]) -> dict | None:
    """Where the references put their title (largest lettering box) and in what style."""
    for ref in refs:
        data = _lettering(project, ref)
        if data["boxes"] and data.get("title_style"):
            b = max(data["boxes"], key=lambda b: (b["x1"] - b["x0"]) * (b["y1"] - b["y0"]))
            return {"box": [b["x0"], b["y0"], b["x1"], b["y1"]], "style": data["title_style"]}
    return None


def _title_art(project: VideoProject, style: str, log) -> Path | None:
    """The game's title as a transparent PNG, generated once per game and kept in the
    library (library/<game>/title.png). Replace that file with an official logo to use it."""
    path = project.library_dir / "title.png"
    if path.exists():
        return path
    raw = project.thumbnail_dir / "title_raw.png"
    try:
        openai_images.generate_image(TITLE_ART_REQUEST.format(game=project.game, style=style),
                                     raw, on_log=log)
    except RuntimeError as exc:
        log(f"Could not generate the title lettering ({exc}); add it in Photoshop.")
        return None
    project.add_costs(images=1)
    # Light lettering on black: brightness becomes alpha, colour is un-premultiplied.
    arr = np.asarray(Image.open(raw).convert("RGB"), np.float32)
    alpha = np.clip((arr.max(axis=2) - 40) / 120, 0, 1)
    rgb = np.clip(arr / np.maximum(alpha[..., None], 1e-3), 0, 255)
    title = Image.fromarray(np.dstack([rgb, alpha * 255]).astype(np.uint8), "RGBA")
    bbox = title.getbbox()
    if not bbox:
        log("Title lettering came back empty; add it in Photoshop.")
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    title.crop(bbox).save(path)
    log(f"Title lettering saved for every {project.game} video: {path}")
    return path


def _overlay_title(img: Image.Image, title_path: Path, box: list[float]) -> Image.Image:
    """Fit the title into the reference's title box (same centre), with a soft dark glow."""
    title = Image.open(title_path).convert("RGBA")
    x0, y0, x1, y1 = box
    max_w, max_h = (x1 - x0) * WIDTH, (y1 - y0) * HEIGHT * 1.3
    scale = min(max_w / title.width, max_h / title.height)
    title = title.resize((max(1, int(title.width * scale)), max(1, int(title.height * scale))),
                         Image.LANCZOS)
    x = int((x0 + x1) / 2 * WIDTH - title.width / 2)
    y = int((y0 + y1) / 2 * HEIGHT - title.height / 2)
    img = img.convert("RGBA")
    shadow = Image.new("RGBA", title.size, (0, 0, 0, 0))
    shadow.putalpha(title.getchannel("A").point(lambda a: int(a * 0.85)))
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    glow.alpha_composite(shadow, (x, y + 4))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(10)))
    img.alpha_composite(title, (x, y))
    return img.convert("RGB")


def _finish(project: VideoProject, src: Path, out: Path, log) -> None:
    """Reference mode: cover-crop to 1280x720; if the art came back untitled, add the title."""
    img = Image.open(src).convert("RGB")
    scale = max(WIDTH / img.width, HEIGHT / img.height)
    img = img.resize((math.ceil(img.width * scale), math.ceil(img.height * scale)), Image.LANCZOS)
    left, top = (img.width - WIDTH) // 2, (img.height - HEIGHT) // 2
    img = img.crop((left, top, left + WIDTH, top + HEIGHT))

    meta_path = src.with_suffix(".json")
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    if meta.get("title"):
        title_path = _title_art(project, meta["title"]["style"], log)
        if title_path:
            img = _overlay_title(img, title_path, meta["title"]["box"])
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "JPEG", quality=93)


def _generate(project: VideoProject, prefix: str, prompt: str, refs: list[Path],
              variants: int, log, meta: dict | None = None) -> Path:
    """Append ``variants`` new images as {prefix}_NN.png; return the first new one.
    ``meta`` is saved beside each image ({prefix}_NN.json) for ``_finish``."""
    existing = sorted(project.thumbnail_dir.glob(f"{prefix}_*.png"))
    first = None
    for i in range(len(existing), len(existing) + variants):
        out = project.thumbnail_dir / f"{prefix}_{i + 1:02d}.png"
        openai_images.generate_image(prompt, out, reference_images=refs or None, on_log=log)
        project.add_costs(images=1)
        if meta:
            out.with_suffix(".json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        first = first or out
    return first


def run(project: VideoProject, params: dict, on_log) -> dict:
    """Two modes:

    - reference thumbnails in thumbnail/refs/: sent to the image tool as a mood board; if it
      refuses (or with ``reference=mood``), Claude turns them into a visual-direction spec and
      a new subject and the art is generated from that text alone. The title is composited
      where the references had it (refthumb_NN.png);
    - no references: art direction + ART_BASELINE key art, title and band drawn locally
      from the channel template (keyart_NN.png).

    ``pick`` (a variant file name) re-finishes an existing variant without new images.
    """
    log = on_log
    variants = int(params.get("variants", 2))
    project.thumbnail_dir.mkdir(parents=True, exist_ok=True)

    if params.get("pick"):
        chosen = project.thumbnail_dir / Path(str(params["pick"])).name
        if not chosen.is_file():
            raise FileNotFoundError(f"No such variant: {chosen.name}")
    else:
        chosen = None

    refs = _refs(project)
    ref_mode = chosen.name.startswith("refthumb_") if chosen else bool(refs)

    if ref_mode:
        if chosen is None:
            existing = sorted(project.thumbnail_dir.glob("refthumb_*.png"))
            if existing and not params.get("force"):
                chosen = existing[0]
            else:
                log(f"Following {len(refs)} reference(s) for {project.game}: {[r.name for r in refs]}")
                title = _reference_title(project, refs)
                meta = {"title": title} if title else None
                if params.get("reference") != "mood":
                    try:
                        chosen = _generate(project, "refthumb", REF_REQUEST.format(game=project.game),
                                           refs, variants, log, meta=meta)
                    except RuntimeError as exc:
                        if "No image returned" not in str(exc):
                            raise
                        # Retrying with the same references gets the same answer: describe
                        # them instead and leave them out of the request.
                        log(f"Image tool refused the references ({exc}). Switching to a "
                            "description of them; the references are not sent.")
                if chosen is None:
                    direction = _reference_direction(project, refs, bool(params.get("force")), log)
                    chosen = _generate(project, "refthumb", _mood_prompt(project.game, direction),
                                       [], variants, log, meta=meta)
        _finish(project, chosen, project.thumbnail_path, log)
    else:
        direction_path = project.thumbnail_dir / "direction.json"
        # Picking an existing variant only recomposes: keep the direction (and its title_text).
        if direction_path.exists() and (not params.get("force") or chosen is not None):
            direction = json.loads(direction_path.read_text(encoding="utf-8"))
        else:
            user = (f"Game: {project.game}\nScope: {project.scope}\nFormat: {project.format}\n"
                    "Return: subject, light, mood, and title_text (the word or short game name "
                    "to print on the thumbnail: the game's name, or SLEEP when the name is long).")
            direction, comp = ai.complete_json(ART_DIRECTION_SYSTEM, user, schema=ART_DIRECTION_SCHEMA,
                                               max_tokens=4000, effort="low")
            project.add_costs(**comp.cost_deltas())
            direction_path.write_text(json.dumps(direction, indent=2), encoding="utf-8")
        log(f"Art direction: {direction['subject']} | light: {direction['light']}")

        if chosen is None:
            existing = sorted(project.thumbnail_dir.glob("keyart_*.png"))
            if existing and not params.get("force"):
                chosen = existing[0]
            else:
                prompt = (ART_BASELINE.format(game=project.game) + "\n\nSUBJECT: "
                          + direction["subject"] + "\nLIGHT: " + direction["light"]
                          + "\nMOOD: " + direction["mood"])
                chosen = _generate(project, "keyart", prompt, [], variants, log)
        title_text = params.get("title_text") or direction.get("title_text") or project.game
        compose(project, chosen, title_text, project.thumbnail_path)

    size_kb = project.thumbnail_path.stat().st_size / 1024
    log(f"Thumbnail: {project.thumbnail_path.name} from {chosen.name} ({size_kb:.0f} KB, "
        f"{'reference' if ref_mode else 'key art'} mode).")
    return {"thumbnail": str(project.thumbnail_path), "art": chosen.name,
            "mode": "reference" if ref_mode else "keyart", "kb": round(size_kb)}
