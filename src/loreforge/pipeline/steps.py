"""Pipeline step registry: ordered steps, their "done" proof on disk and dependencies.

Each step maps to ``loreforge.services.{service}`` exposing
``run(project, params, on_log) -> dict``. Steps are idempotent: ``is_done`` checks the
output file, and per-section work inside a step skips sections whose input hash is
unchanged, so a forced re-run only redoes what changed.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable

from loreforge.models.project import VideoProject


class StepStatus(str, Enum):
    TODO = "todo"            # service not implemented yet
    LOCKED = "locked"
    PENDING = "pending"
    DONE = "done"
    RUNNING = "running"
    ERROR = "error"


@dataclass(frozen=True)
class Req:
    """One prerequisite that lives outside the step (another step's output, a brief field)."""
    label: str                                     # shown when missing: "the narration"
    check: Callable[[VideoProject], bool]


@dataclass(frozen=True)
class Step:
    """A step with no ``requires`` is always runnable: its own inputs (URLs, uploads) are
    filled inside the step, and the service raises a clear error if they are still empty."""
    id: str
    label: str
    service: str                                   # module in loreforge.services
    is_done: Callable[[VideoProject], bool]
    requires: tuple[Req, ...] = ()

    def missing(self, p: VideoProject) -> list[str]:
        return [r.label for r in self.requires if not _safe(r.check)(p)]

    def can_run(self, p: VideoProject) -> bool:
        return not self.missing(p)

    def hint(self, p: VideoProject) -> str:
        missing = self.missing(p)
        return f"Needs {_join(missing)}." if missing else ""


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _has_game(p: VideoProject) -> bool:
    return bool(p.game and p.game_slug)


def _safe(fn: Callable[[VideoProject], bool]) -> Callable[[VideoProject], bool]:
    def wrapped(p: VideoProject) -> bool:
        try:
            return fn(p)
        except ValueError:
            return False
    return wrapped


GAME = Req("the game set in the brief", _has_game)
LORE = Req("the research (lore bible)", lambda p: p.lore_bible_path.exists())
OUTLINE = Req("the outline", lambda p: p.outline_path.exists())
SCRIPT = Req("the script", lambda p: p.script_json_path.exists())
NARRATION = Req("the narration", lambda p: p.narration_wav_path.exists())
WORDS = Req("the narration word timings", lambda p: p.narration_words_path.exists())
SECTIONS = Req("the narration sections", lambda p: p.sections_path.exists())
SCENES = Req("the footage scenes", lambda p: p.scenes_path.exists())

STEPS: list[Step] = [
    Step("research", "Research: lore bible", "research_service",
         is_done=_safe(lambda p: p.lore_bible_path.exists()), requires=(GAME,)),
    Step("outline", "Outline", "outline_service",
         is_done=lambda p: p.outline_path.exists(), requires=(LORE,)),
    Step("script", "Script (section by section)", "script_service",
         is_done=lambda p: p.script_txt_path.exists(), requires=(OUTLINE,)),
    Step("narration", "Narration (TTS)", "narration_service",
         is_done=lambda p: p.narration_wav_path.exists() and p.sections_path.exists(),
         requires=(SCRIPT,)),
    Step("duration", "Duration check", "duration_service",
         is_done=lambda p: (p.audio_dir / "duration_ok.json").exists(), requires=(NARRATION,)),
    Step("captions", "Captions (SRT + Premiere)", "caption_service",
         is_done=lambda p: p.captions_srt_path.exists(), requires=(WORDS,)),
    Step("footage", "Footage: download + scenes", "footage_service",
         is_done=lambda p: p.scenes_path.exists()),
    Step("music", "Music bed", "music_service",
         is_done=lambda p: p.music_bed_path.exists(), requires=(NARRATION,)),
    Step("thumbnail", "Thumbnail", "thumbnail_service",
         is_done=lambda p: p.thumbnail_path.exists(), requires=(GAME,)),
    Step("metadata", "Metadata + chapters", "metadata_service",
         is_done=lambda p: p.metadata_path.exists(), requires=(OUTLINE, SECTIONS)),
    Step("premiere", "Premiere XML", "premiere_service",
         is_done=lambda p: p.premiere_project_path.exists(), requires=(SCENES, SECTIONS)),
]

STEP_BY_ID: dict[str, Step] = {s.id: s for s in STEPS}


def is_implemented(step: Step) -> bool:
    import importlib.util
    return importlib.util.find_spec(f"loreforge.services.{step.service}") is not None


def step_status(step: Step, project: VideoProject, running_ids: set[str] | None = None,
                error_ids: set[str] | None = None) -> StepStatus:
    if not is_implemented(step):
        return StepStatus.TODO
    if step.id in (running_ids or set()):
        return StepStatus.RUNNING
    if step.is_done(project):
        return StepStatus.DONE
    if step.id in (error_ids or set()):
        return StepStatus.ERROR
    if step.can_run(project):
        return StepStatus.PENDING
    return StepStatus.LOCKED


def wizard_state(project: VideoProject, running_ids: set[str] | None = None,
                 error_ids: set[str] | None = None) -> list[dict]:
    return [{"id": s.id, "label": s.label,
             "status": step_status(s, project, running_ids, error_ids).value,
             "requires_hint": s.hint(project)} for s in STEPS]
