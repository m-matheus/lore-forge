"""VideoProject: the central handle for one run.

A run is one video living under ``output/{YYYYMMDD}-{slug}/``. It holds the brief
(game, format, scope, target duration, reference and footage URLs), persisted to
``project.json``, and resolves every file path so services never hard-code layout.

The lore bible is per GAME, not per run: it lives in ``library/{game_slug}/`` so a
facts video reuses the research done for a history video of the same game.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from loreforge.config.channels import Channel, load_channel
from loreforge.config.settings import get_settings

PROJECT_FILE = "project.json"

VideoFormat = Literal["history", "facts", "catalog"]


class Costs(BaseModel):
    """Running cost counters, updated by the services that spend money."""
    anthropic_input_tokens: int = 0
    anthropic_output_tokens: int = 0
    anthropic_cache_read_tokens: int = 0
    anthropic_cache_write_tokens: int = 0
    elevenlabs_chars: int = 0
    images: int = 0


class VideoProject(BaseModel):
    """Persisted brief + path resolver."""

    run_id: str
    channel_slug: str = Field(default_factory=lambda: get_settings().default_channel)

    # --- Brief ---
    game: str = ""
    game_slug: str = ""
    format: VideoFormat = "history"
    scope: str = "the entire game"      # "the entire game" | "The Old Hunters DLC" | "Lady Maria"
    target_minutes: int = Field(default_factory=lambda: get_settings().default_target_minutes)
    publisher: str = ""
    reference_urls: list[str] = Field(default_factory=list)
    footage_urls: list[str] = Field(default_factory=list)
    notes: str = ""                     # free-text direction for outline/script

    # --- Filled by later steps ---
    title: str = ""
    costs: Costs = Field(default_factory=Costs)

    # --- Channel (not serialized; resolved on demand) ---
    @property
    def channel(self) -> Channel:
        return load_channel(self.channel_slug)

    # --- Derived targets ---
    @property
    def target_words(self) -> int:
        s = get_settings()
        return round(self.target_minutes * s.narration_wpm * (1 + s.word_slack))

    # --- Run paths ---
    @property
    def run_dir(self) -> Path:
        return get_settings().output_dir / self.run_id

    @property
    def sources_dir(self) -> Path:
        return self.run_dir / "sources"

    @property
    def research_dir(self) -> Path:
        return self.run_dir / "research"

    @property
    def transcripts_dir(self) -> Path:
        return self.research_dir / "transcripts"

    @property
    def script_dir(self) -> Path:
        return self.run_dir / "script"

    @property
    def outline_path(self) -> Path:
        return self.script_dir / "outline.json"

    def section_md_path(self, section_id: str) -> Path:
        return self.script_dir / f"{section_id}.md"

    @property
    def ledger_path(self) -> Path:
        return self.script_dir / "ledger.json"

    @property
    def lint_path(self) -> Path:
        return self.script_dir / "lint.json"

    @property
    def script_json_path(self) -> Path:
        return self.script_dir / "script.json"

    @property
    def script_txt_path(self) -> Path:
        return self.script_dir / "script.txt"

    @property
    def metadata_path(self) -> Path:
        return self.script_dir / "metadata.json"

    @property
    def description_path(self) -> Path:
        return self.script_dir / "description.txt"

    @property
    def audio_dir(self) -> Path:
        return self.run_dir / "audio"

    def section_audio_dir(self, section_id: str) -> Path:
        return self.audio_dir / section_id

    def section_wav_path(self, section_id: str) -> Path:
        return self.audio_dir / f"{section_id}.wav"

    @property
    def narration_wav_path(self) -> Path:
        return self.audio_dir / "narration.wav"

    @property
    def narration_mp3_path(self) -> Path:
        return self.audio_dir / "narration.mp3"

    @property
    def narration_words_path(self) -> Path:
        return self.audio_dir / "narration_words.json"

    @property
    def sections_path(self) -> Path:
        return self.audio_dir / "sections.json"

    @property
    def captions_srt_path(self) -> Path:
        return self.audio_dir / "captions.srt"

    @property
    def premiere_transcript_path(self) -> Path:
        return self.audio_dir / "premiere_transcript.json"

    @property
    def music_bed_path(self) -> Path:
        return self.audio_dir / "music_bed.wav"

    @property
    def footage_dir(self) -> Path:
        return self.run_dir / "footage"

    @property
    def footage_raw_dir(self) -> Path:
        # Own captures dropped here by the user (any container ffmpeg reads).
        return self.footage_dir / "raw"

    @property
    def footage_yt_dir(self) -> Path:
        return self.footage_dir / "yt"

    def footage_yt_path(self, index: int) -> Path:
        return self.footage_yt_dir / f"yt_{index + 1:02d}.mp4"

    @property
    def scenes_path(self) -> Path:
        return self.footage_dir / "scenes.json"

    @property
    def contact_dir(self) -> Path:
        return self.footage_dir / "contact"

    @property
    def thumbnail_dir(self) -> Path:
        return self.run_dir / "thumbnail"

    @property
    def thumbnail_refs_dir(self) -> Path:
        return self.thumbnail_dir / "refs"

    @property
    def thumbnail_path(self) -> Path:
        return self.thumbnail_dir / "thumbnail.jpg"

    @property
    def premiere_dir(self) -> Path:
        return self.run_dir / "premiere"

    @property
    def premiere_project_path(self) -> Path:
        return self.premiere_dir / f"{self.run_id}.xml"

    # --- Library (per game) ---
    @property
    def library_dir(self) -> Path:
        if not self.game_slug:
            raise ValueError("game_slug is empty: set the game in the brief first.")
        return get_settings().library_dir / self.game_slug

    @property
    def lore_bible_path(self) -> Path:
        return self.library_dir / "lore_bible.json"

    # --- Footage helpers ---
    def footage_files(self) -> list[Path]:
        """Every source video for the run: YouTube downloads first, then own captures."""
        exts = {".mp4", ".mov", ".mkv", ".m4v", ".webm"}
        files: list[Path] = []
        for d in (self.footage_yt_dir, self.footage_raw_dir):
            if d.exists():
                files += sorted(p for p in d.iterdir()
                                if p.is_file() and p.suffix.lower() in exts)
        return files

    def source_files(self) -> list[Path]:
        if not self.sources_dir.exists():
            return []
        return sorted(p for p in self.sources_dir.rglob("*") if p.is_file())

    # --- Persistence ---
    def save(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / PROJECT_FILE).write_text(
            self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load(cls, run_id: str) -> "VideoProject":
        path = get_settings().output_dir / run_id / PROJECT_FILE
        if not path.exists():
            raise FileNotFoundError(f"No project.json for run '{run_id}' at {path}")
        return cls(**json.loads(path.read_text(encoding="utf-8")))

    @classmethod
    def create(cls, slug: str, **kwargs) -> "VideoProject":
        run_id = f"{datetime.now():%Y%m%d}-{slugify(slug)}"
        if not kwargs.get("game_slug") and kwargs.get("game"):
            kwargs["game_slug"] = slugify(kwargs["game"])
        project = cls(run_id=run_id, **kwargs)
        project.save()
        for d in (project.sources_dir, project.footage_raw_dir, project.thumbnail_refs_dir):
            d.mkdir(parents=True, exist_ok=True)
        return project

    def add_costs(self, **deltas: int) -> None:
        """Increment cost counters and persist (reloads first to avoid clobbering)."""
        fresh = VideoProject.load(self.run_id) if (self.run_dir / PROJECT_FILE).exists() else self
        for key, value in deltas.items():
            setattr(fresh.costs, key, getattr(fresh.costs, key) + int(value))
        fresh.save()
        self.costs = fresh.costs


def slugify(text: str) -> str:
    import re
    import unicodedata
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "run"


def list_runs() -> list[str]:
    base = get_settings().output_dir
    if not base.exists():
        return []
    return sorted((c.name for c in base.iterdir() if (c / PROJECT_FILE).exists()), reverse=True)
