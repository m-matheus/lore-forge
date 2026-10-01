"""Channel configuration model + loader.

Each channel lives in ``channels/{slug}/`` with ``channel.json`` (brand, voice, look,
edit rhythm, thumbnail template) plus text files the script prompts read verbatim:
``style_guide.md``, ``banned_phrases.txt`` and ``templates/*.md``. Adding a channel is
dropping a folder, never touching code.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

from loreforge.config.settings import get_settings


class VideoSpec(BaseModel):
    width: int = 1920
    height: int = 1080
    fps: int = 30


class VoiceSpec(BaseModel):
    voice_id: str = ""
    model: str | None = None            # None = settings.elevenlabs_model
    stability: float = 0.55
    similarity_boost: float = 0.75
    style: float = 0.0
    # Pace is per model, not just per voice: on the same chunk this voice reads at
    # 129 wpm on turbo v2.5 at speed 1.0 but only ~99 wpm on flash v2.5 at 0.87.
    # 0.95 on flash lands on the reference channel's 120 wpm. Re-measure when changing
    # model or voice: the narration log prints each chunk's length and word count.
    speed: float = 0.95
    winddown_speed: float = 0.88        # the last section slows down


class NarrationSpec(BaseModel):
    chunk_chars: int = 2500             # ElevenLabs request size, cut at sentence end
    paragraph_pause: float = 0.6        # seconds of silence between paragraphs
    section_pause: float = 1.6          # between sections
    winddown_paragraph_pause: float = 1.4
    target_lufs: float = -16.0


class ScriptSpec(BaseModel):
    opening_minutes: float = 3.0
    winddown_minutes: float = 5.0
    default_address: str = "traveler"   # used when a game has no natural address term


class GradeSpec(BaseModel):
    """Parametric colour grade, compiled into a 3D LUT (``look.cube``).

    Applied in order: exposure -> contrast -> saturation -> split tone -> black lift.
    Saturation blends from ``saturation`` (darks/mids) to ``highlight_saturation`` for
    pixels that were bright in the SOURCE, so lamps and fire keep their warmth while the
    rest of the frame goes nearly monochrome (measured on the reference channel).
    Tints are RGB offsets added in shadows / highlights (weighted by luma).
    """
    exposure: float = 0.48
    contrast: float = 1.15
    contrast_pivot: float = 0.35
    saturation: float = 0.42
    highlight_saturation: float = 0.85
    shadow_tint: list[float] = Field(default_factory=lambda: [-0.004, 0.004, 0.018])
    highlight_tint: list[float] = Field(default_factory=lambda: [0.010, -0.004, -0.012])
    black_lift: float = 0.008
    white_clip: float = 0.78


class OverlaySpec(BaseModel):
    """One full-frame overlay layer placed on its own video track, looped."""
    name: str                           # "rain", "dust", "grain"...
    file: str                           # relative to the channel dir
    opacity: float = 1.0


class WatermarkSpec(BaseModel):
    file: str = "assets/watermark.png"
    width_frac: float = 0.075           # watermark width relative to frame width
    margin_frac: float = 0.025
    opacity: float = 0.55


class LookSpec(BaseModel):
    mode: str = "premiere"              # "premiere" (overlay tracks) | "bake" (ffmpeg)
    grade: GradeSpec = Field(default_factory=GradeSpec)
    lut: str = "assets/look.cube"
    overlays: list[OverlaySpec] = Field(default_factory=list)
    vignette: str = "assets/vignette.png"
    vignette_strength: float = 0.78
    watermark: WatermarkSpec = Field(default_factory=WatermarkSpec)


class EditSpec(BaseModel):
    intro_seconds: float = 180.0        # the opening cuts faster
    intro_cut_seconds: float = 5.0
    body_cut_seconds: list[float] = Field(default_factory=lambda: [8.0, 14.0])
    dissolve_frames: int = 12           # 0 = hard cuts
    no_repeat_minutes: float = 20.0     # a scene is not reused within this window


class MusicSpec(BaseModel):
    file: str = ""                      # relative to the channel dir; per-run override allowed
    lufs: float = -30.0
    crossfade: float = 6.0
    fade_in: float = 4.0
    fade_out: float = 12.0
    # Library tracks nearly always fade in and out. Looping those fades against each
    # other digs a hole in the level at every seam, so they are cut off before the
    # crossfade. None measures the track; a number pins it (0 disables the trim).
    trim_head: float | None = None
    trim_tail: float | None = None


class ThumbnailSpec(BaseModel):
    title_font: str = "assets/fonts/title.ttf"
    badge_font: str = "assets/fonts/badge.ttf"
    band: str = "assets/band.png"
    title_color: str = "#E9E4D8"
    badge_color: str = "#D8D2C4"
    badge_template: str = "2+ HOUR | {game_upper} LORE | {channel_upper}"


class Channel(BaseModel):
    name: str
    slug: str
    language: str = "en"
    tagline: str = ""
    video: VideoSpec = Field(default_factory=VideoSpec)
    voice: VoiceSpec = Field(default_factory=VoiceSpec)
    narration: NarrationSpec = Field(default_factory=NarrationSpec)
    script: ScriptSpec = Field(default_factory=ScriptSpec)
    look: LookSpec = Field(default_factory=LookSpec)
    edit: EditSpec = Field(default_factory=EditSpec)
    music: MusicSpec = Field(default_factory=MusicSpec)
    thumbnail: ThumbnailSpec = Field(default_factory=ThumbnailSpec)
    base_tags: list[str] = Field(default_factory=list)

    # Populated by the loader: absolute path to this channel's directory.
    root_dir: Path | None = None

    def path(self, rel: str) -> Path:
        """Resolve a channel-relative path (asset, template...) to an absolute path."""
        if self.root_dir is None:
            raise RuntimeError(f"Channel {self.slug} was not loaded from disk")
        return (self.root_dir / rel).resolve()

    def read_text(self, rel: str, default: str = "") -> str:
        p = self.path(rel)
        return p.read_text(encoding="utf-8") if p.exists() else default

    @property
    def style_guide(self) -> str:
        return self.read_text("style_guide.md")

    def template(self, name: str) -> str:
        return self.read_text(f"templates/{name}.md")

    def banned_patterns(self) -> list[re.Pattern]:
        """Banned phrases, one per line. Plain lines match as whole words, case-insensitive;
        lines starting with ``re:`` are raw regexes. ``#`` starts a comment."""
        patterns: list[re.Pattern] = []
        for raw in self.read_text("banned_phrases.txt").splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            if line.startswith("re:"):
                patterns.append(re.compile(line[3:].strip(), re.IGNORECASE))
            else:
                patterns.append(re.compile(rf"\b{re.escape(line)}\b", re.IGNORECASE))
        return patterns

    def banned_phrases_display(self) -> list[str]:
        """Human-readable banned list for prompts (regex lines shown as-is)."""
        out = []
        for raw in self.read_text("banned_phrases.txt").splitlines():
            line = raw.split("#", 1)[0].strip()
            if line:
                out.append(line[3:].strip() if line.startswith("re:") else line)
        return out


def _channels_dir() -> Path:
    return get_settings().channels_dir


@lru_cache(maxsize=None)
def load_channel(slug: str) -> Channel:
    """Load and validate ``channels/{slug}/channel.json``."""
    path = _channels_dir() / slug / "channel.json"
    if not path.exists():
        raise FileNotFoundError(f"Channel config not found: {path}")
    channel = Channel(**json.loads(path.read_text(encoding="utf-8")))
    channel.root_dir = path.parent
    return channel


def list_channels() -> list[Channel]:
    base = _channels_dir()
    if not base.exists():
        return []
    out: list[Channel] = []
    for child in sorted(base.iterdir()):
        if (child / "channel.json").exists():
            try:
                out.append(load_channel(child.name))
            except Exception:
                continue
    return out
