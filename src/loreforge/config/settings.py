"""Typed application settings, loaded once from .env."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

# Project root = three levels up from src/loreforge/config/settings.py.
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Environment-backed configuration.

    API keys are optional at import time so the app can boot before keys are set;
    services raise a clear error (``require``) when a key is actually needed.
    """

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ):
        # .env wins over the process environment: the app is often launched from a shell
        # that exports ANTHROPIC_* for a local proxy (e.g. Claude Code).
        return (init_settings, dotenv_settings, env_settings, file_secret_settings)

    # --- API keys ---
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    elevenlabs_api_key: str | None = None
    j1_api_key: str | None = None

    # --- Models ---
    # Research/outline/metadata are extraction and planning; the script is the product.
    anthropic_model_research: str = "claude-sonnet-5"
    anthropic_model_script: str = "claude-opus-5"
    # flash/turbo v2.5 cost 0.5 credits per character against multilingual_v2's 1.0,
    # and all three support request stitching, timestamps and voice speed.
    elevenlabs_model: str = "eleven_flash_v2_5"
    voice_id: str | None = None  # overrides the channel voice when set
    # "j1" = ElevenLabs voices via api.j1tts.com (cheaper; no timestamps, stitching or
    # voice settings, so speed and word timings are done locally). "elevenlabs" = direct.
    tts_provider: str = "j1"
    j1_base_url: str = "https://api.j1tts.com"

    # --- Script / duration ---
    narration_wpm: int = 126          # measured on the reference channel (see channels/*/reference)
    min_minutes: int = 105            # hard floor for the real audio duration
    default_target_minutes: int = 130
    word_slack: float = 0.10          # script target = minutes * wpm * (1 + slack)

    # --- Paths ---
    output_base_dir: str = "output"
    default_channel: str = "midnight-realm"

    # --- YouTube ---
    youtube_cookies_file: str | None = None
    youtube_quality: str = "edit"
    youtube_max_height: int | None = None

    @property
    def project_root(self) -> Path:
        return PROJECT_ROOT

    @property
    def output_dir(self) -> Path:
        base = Path(self.output_base_dir)
        return base if base.is_absolute() else PROJECT_ROOT / base

    @property
    def channels_dir(self) -> Path:
        return PROJECT_ROOT / "channels"

    @property
    def library_dir(self) -> Path:
        return PROJECT_ROOT / "library"

    def require(self, key: str) -> str:
        """Return an API key or raise a clear, actionable error if it's missing."""
        value = getattr(self, key, None)
        if not value:
            raise RuntimeError(
                f"{key.upper()} is not set. Add it to your .env file "
                f"(copy .env.example) before running this step."
            )
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide Settings singleton."""
    return Settings()
