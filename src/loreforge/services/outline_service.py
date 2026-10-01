"""Outline step: plan the sections of the video against the lore bible.

Claude proposes the structure (format-specific), then deterministic validation fixes
what a model can't be trusted to count: word budgets are rescaled to the target, every
fact is assigned to at most one section, unknown ids are dropped, and history outlines
are checked for chronological order. The outline is plain JSON the user can edit in the
UI before the script is written.
"""
from __future__ import annotations

import json
import statistics

from loreforge.config.settings import get_settings
from loreforge.integrations import anthropic_client as ai
from loreforge.models.lore import LoreBible
from loreforge.models.outline import GameFlavor, Outline, Section
from loreforge.models.project import VideoProject

OPENING_MODES = ("welcome", "question", "cold open", "arrival")

WORDS_PER_SECTION = 1100          # body sections land around 700-1,400 words
WORDS_PER_FACT = {"history": 55, "facts": 42, "catalog": 60}

FORMAT_BRIEFS = {
    "history": """\
FORMAT: history, "The Entire Lore of {game} Explained". A chronological narrative from
the deepest past to the ending(s). Each body section is one era or episode of the story,
in order. Assign each section the facts of its era; weave supporting facts (items,
weapons, locations) into the section where they belong in time. Timeless world facts go
where they first matter. Endings come last, and the final body section is a reflection:
no new lore, only what the story says about ambition, grief, loyalty or whatever this
game is actually about. It sits between the endings and the wind-down.""",
    "facts": """\
FORMAT: facts, "N Hours of {game} Facts". Hundreds of independent facts, each told in
one to three sentences, grouped into sections by category in this order where the
material exists: world and setting -> factions -> characters -> weapons and items ->
bosses and enemies -> sound, art and production -> secrets and cut content -> endings.
Split a large category into several sections (e.g. "Characters of the Hunter's Dream").
Aim for about {facts_per_section} facts per body section.""",
    "catalog": """\
FORMAT: catalog, "Every {scope} in {game} Explained". One block per entity of the kind
named in the scope, in the order the player meets them. Each body section covers one
major entity, or several minor ones grouped. Assign each section the facts about its
entities.""",
}

OUTLINE_SYSTEM = """\
You are the head writer of a sleep channel that narrates video game lore in videos of
two hours or more. You plan the structure of one video from a fact database. You never
invent lore: sections are built only from the fact ids you are given.

Return ONE JSON object, no commentary:
{
  "working_title": "...",
  "flavor": {
    "address": "how the narrator addresses the listener, from the game's own vocabulary (e.g. Hunter, Tarnished, Ashen One); a neutral word if the game has none",
    "welcome_image": "one sentence: a safe, quiet place inside this game's world for the opening to settle the listener into",
    "farewell": "the final line of the video: a gentle good night that uses the address term and an image from the game (original wording, not a famous quote)",
    "fans_of": ["3-5 similar games or series"]
  },
  "sections": [
    {"title": "...", "goal": "what this section covers and why it comes here, 1-2 sentences",
     "fact_ids": ["f0001", ...], "visual_tags": ["2-4 visual keywords for matching footage: cathedral, moon, streets, forest, sea..."]}
  ]
}

List ONLY the body sections: the opening and wind-down are added automatically.
Titles are short and evocative, never clickbait. Every fact id appears at most once.
Use as many of the canon and inferred facts as the length allows; theory facts only
where the story genuinely invites speculation.
"""


OUTLINE_SCHEMA = ai.obj({
    "working_title": ai.STR,
    "flavor": ai.obj({"address": ai.STR, "welcome_image": ai.STR, "farewell": ai.STR,
                      "fans_of": ai.STR_LIST}),
    "sections": ai.array(ai.obj({"title": ai.STR, "goal": ai.STR, "fact_ids": ai.STR_LIST,
                                 "visual_tags": ai.STR_LIST})),
})


def recent_opening_modes(project: VideoProject, limit: int = 2) -> list[str]:
    """The entrances used by this channel's most recent videos, newest first.

    Run ids start with the date, so sorting the directory names sorts by recency.
    """
    out: list[str] = []
    root = get_settings().output_dir
    if not root.exists():
        return out
    for run in sorted((d for d in root.iterdir() if d.is_dir()), reverse=True):
        if run.name == project.run_id:
            continue
        path = run / "script" / "outline.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        mode = data.get("opening_mode")
        if mode:
            out.append(mode)
        if len(out) >= limit:
            break
    return out


def pick_opening_mode(project: VideoProject) -> str:
    """The first mode this channel has not used in its last couple of videos.

    Deterministic, so a re-run keeps the same entrance, and editable afterwards: the
    producer can change ``opening_mode`` in outline.json before the script is written.
    """
    recent = recent_opening_modes(project)
    return next((m for m in OPENING_MODES if m not in recent), OPENING_MODES[0])


def _fact_index(bible: LoreBible) -> str:
    lines = []
    for f in bible.facts:
        chrono = "" if f.chrono is None else f" t={f.chrono}"
        lines.append(f"{f.id} [{f.category}|{f.status}{chrono}] {f.text}")
    return "\n".join(lines)


def _events_index(bible: LoreBible) -> str:
    return "\n".join(f"{e.order}. {e.summary} ({', '.join(e.fact_ids[:8])})" for e in bible.events)


def build_prompt(project: VideoProject, bible: LoreBible, body_words: int, n_sections: int) -> str:
    wpf = WORDS_PER_FACT.get(project.format, 50)
    facts_per_section = max(8, round(WORDS_PER_SECTION / wpf))
    brief = FORMAT_BRIEFS[project.format].format(game=project.game, scope=project.scope,
                                                 facts_per_section=facts_per_section)
    parts = [
        f"Game: {project.game}",
        f"Scope: {project.scope}",
        brief,
        f"Length: about {body_words} words of body narration, so plan about {n_sections} "
        f"body sections, and assign roughly {round(body_words / wpf)} facts in total.",
    ]
    if project.notes:
        parts.append(f"Direction from the producer: {project.notes}")
    if bible.events and project.format == "history":
        parts.append(f"Chronology of events (with supporting fact ids):\n{_events_index(bible)}")
    parts.append(f"Facts ({len(bible.facts)}):\n{_fact_index(bible)}")
    return "\n\n".join(parts)


def validate(outline: Outline, bible: LoreBible, project: VideoProject) -> Outline:
    """Deterministic fixes and warnings. Returns the same outline, mutated."""
    ch = project.channel
    s = get_settings()
    known = bible.fact_map()
    seen: set[str] = set()
    warnings: list[str] = []

    body = [sec for sec in outline.sections if sec.kind == "body"]
    for sec in body:
        clean = []
        for fid in sec.fact_ids:
            if fid in known and fid not in seen:
                clean.append(fid)
                seen.add(fid)
        dropped = len(sec.fact_ids) - len(clean)
        if dropped:
            warnings.append(f"{sec.title}: dropped {dropped} unknown or duplicate fact ids")
        sec.fact_ids = clean
        if not clean:
            warnings.append(f"{sec.title}: no facts assigned")

    # Word budget: opening + wind-down are fixed; body sections share the rest in
    # proportion to how many facts they carry (floor 600, so no section is a stub).
    opening_words = round(ch.script.opening_minutes * s.narration_wpm)
    winddown_words = round(ch.script.winddown_minutes * s.narration_wpm)
    body_total = max(1, project.target_words - opening_words - winddown_words)
    weights = [max(1, len(sec.fact_ids)) for sec in body]
    wsum = sum(weights) or 1
    for sec, w in zip(body, weights):
        sec.target_words = max(600, round(body_total * w / wsum))
    scale = body_total / max(1, sum(sec.target_words for sec in body))
    for sec in body:
        sec.target_words = round(sec.target_words * scale)

    # The mid-roll break goes at the chapter boundary nearest the middle of the body, so
    # it lands on a section start rather than interrupting one. The reference channel
    # puts it a little before halfway, which is where the drop-off actually happens.
    if body:
        cumulative, running = [], 0
        for sec in body:
            cumulative.append(running)
            running += sec.target_words
        middle = running * 0.45
        nearest = min(range(len(body)), key=lambda i: abs(cumulative[i] - middle))
        for sec in body:
            sec.midroll = False
        body[nearest].midroll = True

    opening = Section(id="s00", kind="opening", title="Welcome",
                      goal="Opening ritual: welcome, settle the listener, name tonight's story.",
                      target_words=opening_words, visual_tags=["calm", "establishing"])
    winddown = Section(id="s99", kind="winddown", title="Rest",
                       goal="Wind-down ritual: slow the pace, let the story go, say good night.",
                       target_words=winddown_words, visual_tags=["calm", "night"])
    outline.sections = [opening, *body, winddown]
    for i, sec in enumerate(outline.sections, start=1):
        sec.id = f"s{i:02d}"

    if project.format == "history":
        chronos = []
        for sec in body:
            vals = [known[f].chrono for f in sec.fact_ids if known[f].chrono is not None]
            chronos.append(statistics.median(vals) if vals else None)
        for (a, ca), (b, cb) in zip(zip(body, chronos), list(zip(body, chronos))[1:]):
            if ca is not None and cb is not None and cb + 15 < ca:
                warnings.append(f"Chronology: '{b.title}' (t~{cb}) comes after '{a.title}' (t~{ca})")

    usable = [f for f in bible.facts if f.status != "theory"]
    unused = [f for f in usable if f.id not in seen]
    if usable and len(unused) / len(usable) > 0.35:
        warnings.append(f"{len(unused)} of {len(usable)} canon/inferred facts are unassigned; "
                        "they remain available for expansion.")
    outline.warnings = warnings
    return outline


def run(project: VideoProject, params: dict, on_log) -> dict:
    log = on_log
    bible = LoreBible.model_validate_json(project.lore_bible_path.read_text(encoding="utf-8"))
    ch = project.channel
    s = get_settings()
    body_words = project.target_words - round(
        (ch.script.opening_minutes + ch.script.winddown_minutes) * s.narration_wpm)
    n_sections = max(6, round(body_words / WORDS_PER_SECTION))
    log(f"Planning ~{n_sections} body sections for ~{body_words} body words "
        f"from {len(bible.facts)} facts...")

    data, comp = ai.complete_json(OUTLINE_SYSTEM, build_prompt(project, bible, body_words, n_sections),
                                  model=s.anthropic_model_script, max_tokens=64000, effort="medium",
                                  schema=OUTLINE_SCHEMA)
    project.add_costs(**comp.cost_deltas())

    flavor = GameFlavor(**{k: v for k, v in (data.get("flavor") or {}).items()
                           if k in GameFlavor.model_fields})
    if not flavor.address:
        flavor.address = ch.script.default_address
    sections = [Section(id=f"b{i:02d}", kind="body", title=sec.get("title", f"Part {i}"),
                        goal=sec.get("goal", ""), fact_ids=list(sec.get("fact_ids") or []),
                        visual_tags=list(sec.get("visual_tags") or []))
                for i, sec in enumerate(data.get("sections") or [], start=1)]
    if not sections:
        raise RuntimeError("The outline came back without sections.")
    outline = validate(Outline(game=project.game, format=project.format,
                               working_title=data.get("working_title", ""), flavor=flavor,
                               opening_mode=pick_opening_mode(project),
                               sections=sections), bible, project)

    project.script_dir.mkdir(parents=True, exist_ok=True)
    project.outline_path.write_text(outline.model_dump_json(indent=2), encoding="utf-8")
    for sec in outline.sections:
        log(f"  {sec.id} {sec.kind:<8} {sec.target_words:>5}w {len(sec.fact_ids):>3} facts  {sec.title}")
    for w in outline.warnings:
        log(f"WARNING: {w}")
    log(f"Outline: {len(outline.sections)} sections, {outline.total_words} words "
        f"(target {project.target_words}). Opening: {outline.opening_mode}. "
        f"Address: {flavor.address}. Farewell: {flavor.farewell}")
    return {"sections": len(outline.sections), "words": outline.total_words,
            "warnings": outline.warnings}
