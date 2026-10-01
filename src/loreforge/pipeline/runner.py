"""Step runner: executes one pipeline step, streaming log lines to a callback.

A step whose output exists is skipped unless ``force=True``. Running/error state is
tracked per run so the UI reflects it live (single-process local app).
"""
from __future__ import annotations

import importlib
import threading
import traceback
from typing import Callable

from loreforge.models.project import VideoProject
from loreforge.pipeline.steps import STEP_BY_ID, is_implemented

LogFn = Callable[[str], None]

_running: dict[str, set[str]] = {}
_errors: dict[str, set[str]] = {}
_lock = threading.Lock()


def running_ids(run_id: str) -> set[str]:
    with _lock:
        return set(_running.get(run_id, set()))


def error_ids(run_id: str) -> set[str]:
    with _lock:
        return set(_errors.get(run_id, set()))


def _mark(run_id: str, step_id: str, *, running: bool | None = None,
          error: bool | None = None) -> None:
    with _lock:
        if running is True:
            _running.setdefault(run_id, set()).add(step_id)
        elif running is False:
            _running.get(run_id, set()).discard(step_id)
        if error is True:
            _errors.setdefault(run_id, set()).add(step_id)
        elif error is False:
            _errors.get(run_id, set()).discard(step_id)


def run_step(run_id: str, step_id: str, params: dict | None = None, *,
             force: bool = False, log: LogFn | None = None) -> dict:
    """Run one step synchronously. Returns {status, result?, error?}."""
    params = dict(params or {})
    log = log or (lambda _m: None)
    step = STEP_BY_ID.get(step_id)
    if step is None:
        raise ValueError(f"Unknown step id: {step_id}")
    project = VideoProject.load(run_id)
    if not is_implemented(step):
        log(f"[{step_id}] not implemented yet (upcoming phase of the build).")
        return {"status": "todo"}

    # A section-scoped request (e.g. re-generate s05) is never "already done".
    if step.is_done(project) and not force and not params.get("section"):
        log(f"[{step_id}] already done; skipping (use force to redo).")
        return {"status": "skipped"}
    if not step.can_run(project) and not force:
        hint = step.hint(project)
        log(f"[{step_id}] locked: {hint}")
        return {"status": "locked", "hint": hint}

    _mark(run_id, step_id, running=True, error=False)
    log(f"[{step_id}] starting...")
    try:
        module = importlib.import_module(f"loreforge.services.{step.service}")
        params.setdefault("force", force)
        result = module.run(project, params, log) or {}
        log(f"[{step_id}] done.")
        return {"status": "done", "result": result}
    except Exception as exc:  # noqa: BLE001 - surface any failure to the UI/CLI
        _mark(run_id, step_id, error=True)
        log(f"[{step_id}] ERROR: {exc}")
        log(traceback.format_exc())
        return {"status": "error", "error": str(exc)}
    finally:
        _mark(run_id, step_id, running=False)
