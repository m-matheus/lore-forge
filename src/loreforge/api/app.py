"""Local web UI: create runs, edit the brief, run steps with live logs, view outputs."""
from __future__ import annotations

import asyncio
import json
import queue
import threading
from pathlib import Path

from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sse_starlette.sse import EventSourceResponse

from loreforge.api import events
from loreforge.config.channels import list_channels
from loreforge.models.project import VideoProject, list_runs, slugify
from loreforge.pipeline import runner
from loreforge.pipeline.steps import wizard_state

UI_DIR = Path(__file__).resolve().parents[1] / "ui"

# Brief fields editable from the run page.
_BRIEF_FIELDS = ("game", "game_slug", "format", "scope", "target_minutes", "publisher",
                 "notes", "channel_slug")
_LIST_FIELDS = ("reference_urls", "footage_urls")


def _safe_child(base: Path, rel: str) -> Path:
    """Resolve ``rel`` under ``base``; refuse anything that escapes it."""
    path = (base / rel).resolve()
    if base.resolve() not in path.parents and path != base.resolve():
        raise ValueError("path escapes the run directory")
    return path


def _step_outputs(p: VideoProject) -> dict[str, list[str]]:
    """Existing output files per step, relative to the run dir (served by /runs/.../files)."""
    candidates = {
        "outline": [p.outline_path],
        "script": [p.script_txt_path, p.script_json_path, p.lint_path],
        "narration": [p.narration_mp3_path, p.narration_wav_path],
        "duration": [p.audio_dir / "duration_ok.json"],
        "captions": [p.captions_srt_path, p.premiere_transcript_path],
        "footage": [p.scenes_path],
        "music": [p.music_bed_path],
        "thumbnail": [p.thumbnail_path],
        "metadata": [p.metadata_path, p.description_path],
        "premiere": [p.premiere_project_path],
    }
    return {step: [str(f.relative_to(p.run_dir)).replace("\\", "/") for f in files if f.is_file()]
            for step, files in candidates.items()}


def create_app() -> FastAPI:
    app = FastAPI(title="LoreForge")
    app.mount("/static", StaticFiles(directory=UI_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=UI_DIR / "templates")
    # Cache-busting: /static/app.css?v=<mtime>, so browsers pick up CSS/JS edits right away.
    templates.env.globals["asset_v"] = lambda name: int((UI_DIR / "static" / name).stat().st_mtime)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        runs = []
        for run_id in list_runs():
            try:
                p = VideoProject.load(run_id)
                steps = wizard_state(p)
                runs.append({"run_id": run_id, "game": p.game, "format": p.format,
                             "minutes": p.target_minutes,
                             "done": sum(s["status"] == "done" for s in steps),
                             "total": len(steps)})
            except Exception:
                runs.append({"run_id": run_id, "game": "?", "format": "", "minutes": 0,
                             "done": 0, "total": 0})
        return templates.TemplateResponse(request, "index.html", {
            "runs": runs, "channels": list_channels()})

    @app.post("/runs")
    def create_run(game: str = Form(...), format: str = Form("history"),
                   target_minutes: int = Form(130), scope: str = Form("the entire game"),
                   channel_slug: str = Form(""), slug: str = Form("")):
        kwargs = {"game": game, "format": format, "target_minutes": target_minutes,
                  "scope": scope}
        if channel_slug:
            kwargs["channel_slug"] = channel_slug
        p = VideoProject.create(slug or f"{game}-{format}", **kwargs)
        return RedirectResponse(f"/runs/{p.run_id}", status_code=303)

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_page(request: Request, run_id: str):
        p = VideoProject.load(run_id)
        return templates.TemplateResponse(request, "run.html", {
            "p": p, "channels": list_channels(),
            "steps": wizard_state(p, runner.running_ids(run_id), runner.error_ids(run_id))})

    @app.get("/api/runs/{run_id}/state")
    def run_state(run_id: str):
        p = VideoProject.load(run_id)
        return {
            "project": json.loads(p.model_dump_json()),
            "target_words": p.target_words,
            "steps": wizard_state(p, runner.running_ids(run_id), runner.error_ids(run_id)),
            "sources": [str(f.relative_to(p.run_dir)) for f in p.source_files()],
            "footage": [str(f.relative_to(p.run_dir)) for f in p.footage_files()],
            "thumbnail_refs": sorted(f.name for f in p.thumbnail_refs_dir.glob("*") if f.is_file())
                if p.thumbnail_refs_dir.exists() else [],
            "thumbnail": p.thumbnail_path.exists(),
            "thumbnail_variants": sorted(f.name for pat in ("refthumb_*.png", "keyart_*.png")
                                        for f in p.thumbnail_dir.glob(pat))
                if p.thumbnail_dir.exists() else [],
            "outputs": _step_outputs(p),
            "metadata": json.loads(p.metadata_path.read_text(encoding="utf-8"))
                if p.metadata_path.exists() else None,
        }

    @app.post("/api/runs/{run_id}/brief")
    def update_brief(run_id: str, payload: dict):
        p = VideoProject.load(run_id)
        for key in _BRIEF_FIELDS:
            if key in payload:
                value = payload[key]
                setattr(p, key, int(value) if key == "target_minutes" else value)
        for key in _LIST_FIELDS:
            if key in payload:
                raw = payload[key]
                items = raw.splitlines() if isinstance(raw, str) else raw
                setattr(p, key, [u.strip() for u in items if u.strip()])
        if "game" in payload and not payload.get("game_slug"):
            p.game_slug = slugify(p.game)
        p.save()
        return {"ok": True}

    @app.post("/api/runs/{run_id}/steps/{step_id}")
    def start_step(run_id: str, step_id: str, payload: dict | None = None):
        payload = payload or {}
        force = bool(payload.pop("force", False))
        log = events.make_logger(run_id)

        def worker():
            try:
                runner.run_step(run_id, step_id, payload, force=force, log=log)
            finally:
                events.publish(run_id, events.DONE)

        threading.Thread(target=worker, daemon=True).start()
        return {"started": step_id}

    @app.post("/api/runs/{run_id}/upload/{kind}")
    async def upload(run_id: str, kind: str, files: list[UploadFile]):
        p = VideoProject.load(run_id)
        dest = {"sources": p.sources_dir, "footage": p.footage_raw_dir,
                "thumbnail-refs": p.thumbnail_refs_dir}.get(kind)
        if dest is None:
            return JSONResponse({"error": f"unknown upload kind {kind}"}, status_code=400)
        dest.mkdir(parents=True, exist_ok=True)
        saved = []
        for f in files:
            name = Path(f.filename or "file").name
            with open(dest / name, "wb") as out:
                while chunk := await f.read(1 << 20):
                    out.write(chunk)
            saved.append(name)
        return {"saved": saved}

    @app.delete("/api/runs/{run_id}/thumbnail-refs/{name}")
    def delete_thumbnail_ref(run_id: str, name: str):
        p = VideoProject.load(run_id)
        path = p.thumbnail_refs_dir / Path(name).name
        if not path.is_file():
            return JSONResponse({"error": "not found"}, status_code=404)
        path.unlink()
        return {"deleted": path.name}

    @app.get("/runs/{run_id}/files/{rel:path}")
    def run_file(run_id: str, rel: str):
        p = VideoProject.load(run_id)
        try:
            path = _safe_child(p.run_dir, rel)
        except ValueError:
            return JSONResponse({"error": "bad path"}, status_code=400)
        if not path.is_file():
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(path)

    @app.get("/api/runs/{run_id}/events")
    async def stream_events(run_id: str, request: Request):
        q = events.subscribe(run_id)

        async def gen():
            try:
                while not await request.is_disconnected():
                    try:
                        msg = await asyncio.to_thread(q.get, True, 1.0)
                    except queue.Empty:
                        continue
                    if msg == events.DONE:
                        yield {"event": "done", "data": "1"}
                    else:
                        yield {"event": "log", "data": msg}
            finally:
                events.unsubscribe(run_id, q)

        return EventSourceResponse(gen())

    return app
