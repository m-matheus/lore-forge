"""Duration check: the real audio length is the guarantee, not the word count.

If the narration is shorter than the floor, the sections with the most unused lore left
are given a bigger word target, rewritten, and re-narrated. Everything else comes from
cache, so a fix costs a few sections rather than two hours.
"""
from __future__ import annotations

import json

from loreforge.config.settings import get_settings
from loreforge.integrations.media import probe
from loreforge.models.lore import LoreBible
from loreforge.models.outline import Outline
from loreforge.models.project import VideoProject
from loreforge.services import narration_service, script_service

MAX_ROUNDS = 3
MAX_ADDED_WORDS_PER_SECTION = 500


def _minutes(project: VideoProject) -> float:
    return probe(project.narration_wav_path).duration / 60


def plan_expansion(outline: Outline, bible: LoreBible, ledger: dict, deficit_words: int,
                   max_per_section: int = MAX_ADDED_WORDS_PER_SECTION) -> list[tuple[str, int]]:
    """Pick which sections to grow and by how much: the ones with the most unused lore
    left, so expansions add material instead of padding. Returns [(section id, words)]."""
    body = [sec for sec in outline.sections if sec.kind == "body"]
    available = {sec.id: len(script_service.extra_facts(bible, outline, sec, ledger, limit=40))
                 for sec in body}
    ranked = sorted((sec for sec in body if available[sec.id]),
                    key=lambda sec: available[sec.id], reverse=True)
    plan, added = [], 0
    for sec in ranked:
        if added >= deficit_words:
            break
        room = min(max_per_section, deficit_words - added)
        plan.append((sec.id, room))
        added += room
    return plan


def run(project: VideoProject, params: dict, on_log) -> dict:
    log = on_log
    s = get_settings()
    floor = float(params.get("min_minutes") or s.min_minutes)
    target = float(params.get("target_minutes") or project.target_minutes)
    outline = Outline.model_validate_json(project.outline_path.read_text(encoding="utf-8"))
    bible = LoreBible.model_validate_json(project.lore_bible_path.read_text(encoding="utf-8"))
    ledger = json.loads(project.ledger_path.read_text(encoding="utf-8")) if project.ledger_path.exists() else {}

    minutes = _minutes(project)
    log(f"Narration is {minutes:.1f} min (floor {floor:.0f}, brief target {target:.0f}).")
    rounds = 0
    while minutes < floor and rounds < MAX_ROUNDS:
        rounds += 1
        deficit_words = round((floor - minutes) * s.narration_wpm * 1.15)
        plan = plan_expansion(outline, bible, ledger, deficit_words)
        chosen, added = [], 0
        for sid, room in plan:
            outline.section(sid).target_words += room
            chosen.append(sid)
            added += room
        if not chosen:
            log("WARNING: no section has unused lore left; add sources and re-run research, "
                "or lower the duration floor.")
            break
        log(f"Round {rounds}: short by {floor - minutes:.1f} min. Expanding {', '.join(chosen)} "
            f"by {added} words in total.")
        project.outline_path.write_text(outline.model_dump_json(indent=2), encoding="utf-8")
        for sid in chosen:                      # drop the cached hash so the section is rewritten
            meta = project.section_md_path(sid).with_suffix(".meta.json")
            meta.unlink(missing_ok=True)
        script_service.run(project, {}, log)
        narration_service.run(project, {}, log)
        minutes = _minutes(project)
        log(f"Narration is now {minutes:.1f} min.")

    ok = minutes >= floor
    report = {"minutes": round(minutes, 2), "floor": floor, "target": target,
              "ok": ok, "rounds": rounds}
    if ok:
        (project.audio_dir / "duration_ok.json").write_text(json.dumps(report, indent=2),
                                                            encoding="utf-8")
    log(("Duration OK: " if ok else "STILL SHORT: ") + f"{minutes:.1f} min")
    return report
