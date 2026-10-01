"""Script step: write the narration section by section against the outline.

A two-hour script doesn't fit one call, and one call would drift and repeat itself.
Each section is its own call with:
- a CACHED prefix shared by every section: channel style guide, banned phrases, the
  full outline and every fact the video uses (so 15+ calls pay for it ~once);
- a per-section suffix: the section brief and its facts, the tail of the previous
  section (continuity), and the LEDGER of what has already been said: facts told,
  images and metaphors used, paragraph openings used.

After each section a deterministic lint runs (banned phrases, phrases repeated from
earlier sections, repeated openers, 8-grams copied from reference transcripts).
Flagged sentences are fixed by SURGICAL replacement (the model returns old -> new
pairs; nothing else changes). A section under 90% of its word target is expanded with
unused facts from the same part of the lore.

Idempotent: each ``sNN.md`` has a ``sNN.meta.json`` with the hash of its inputs; a
re-run only rewrites sections whose brief changed (or ``--section sNN --force``).
"""
from __future__ import annotations

import hashlib
import json
import re

from loreforge.config.settings import get_settings
from loreforge.integrations import anthropic_client as ai
from loreforge.models.lore import Fact, LoreBible
from loreforge.models.outline import Outline, Section
from loreforge.models.project import VideoProject
from loreforge.services import lint_service

MIN_RATIO = 0.90            # a section below this share of its target gets expanded
MAX_FIX_ROUNDS = 2
TAIL_WORDS = 300
_TRAILER = re.compile(r"\n\s*(USED_FACTS|SUMMARY|IMAGES)\s*:", re.I)


# --- Prompt building -----------------------------------------------------------------

WRITER_ROLE = """\
You are the narrator-writer of "{channel}", a channel of long video game lore
narrations people fall asleep to. You are writing ONE section of a {minutes}-minute
script about {game} ({format} format). Other sections are written in separate calls;
the outline below shows where this one sits.
"""

OUTPUT_RULES = """\
OUTPUT FORMAT
Write the section as plain prose paragraphs separated by blank lines: no title, no
headings, no lists, no stage directions, no quotation of these instructions. After the
prose, add exactly these three lines:
USED_FACTS: comma-separated ids of the facts you actually told
SUMMARY: two sentences on what this section told, for the writers of later sections
IMAGES: comma-separated short list of the distinctive images and metaphors you used
"""


_SPOKEN = ("zero one two three four five six seven eight nine ten eleven twelve thirteen "
           "fourteen fifteen sixteen seventeen eighteen nineteen twenty").split()


def _spoken_number(n: int) -> str:
    """Numbers are written the way they are said, so the voice never reads "4"."""
    return _SPOKEN[n] if 0 <= n < len(_SPOKEN) else str(n)


def _fact_line(f: Fact) -> str:
    tag = {"canon": "", "inferred": " (inferred)", "theory": " (THEORY: present as theory)"}[f.status]
    return f"{f.id}{tag}: {f.text}"


def cached_prefix(project: VideoProject, outline: Outline, bible: LoreBible) -> str:
    """Everything identical across the video's section calls. Deterministic order only."""
    ch = project.channel
    facts = bible.fact_map()
    outline_lines = []
    for s in outline.sections:
        outline_lines.append(f"{s.id} [{s.kind}, ~{s.target_words} words] {s.title}: {s.goal}")
    used = sorted({fid for s in outline.sections for fid in s.fact_ids})
    fact_lines = [_fact_line(facts[f]) for f in used if f in facts]
    banned = "\n".join(f"- {b}" for b in ch.banned_phrases_display())
    return "\n\n".join([
        WRITER_ROLE.format(channel=ch.name, minutes=project.target_minutes, game=project.game,
                           format=project.format),
        "STYLE GUIDE\n" + ch.style_guide.strip(),
        "BANNED PHRASES (never use these or close variants; a regex line bans a pattern)\n" + banned,
        f"HOW TO ADDRESS THE LISTENER: \"{outline.flavor.address}\" (sparingly).",
        "OUTLINE\n" + "\n".join(outline_lines),
        "FACTS OF THIS VIDEO (the only lore you may state)\n" + "\n".join(fact_lines),
        OUTPUT_RULES,
    ])


def _ledger_block(ledger: dict, upto: str) -> str:
    """What earlier sections already said, for sections before ``upto``."""
    told, images, openers, summaries = [], [], [], []
    for sid in sorted(ledger):
        if sid >= upto:
            continue
        entry = ledger[sid]
        told += entry.get("used_facts", [])
        images += entry.get("images", [])
        openers += entry.get("openers", [])
        if entry.get("summary"):
            summaries.append(f"{sid}: {entry['summary']}")
    if not summaries:
        return "LEDGER: this is the first section written; nothing has been said yet."
    return "\n".join([
        "LEDGER (already said earlier in the video; do not repeat)",
        "Section summaries:\n" + "\n".join(summaries),
        "Facts already told (refer back in half a clause at most): " + ", ".join(sorted(set(told))),
        "Images and metaphors already used (never reuse): " + "; ".join(images),
        "Paragraph openings already used (vary yours): " + "; ".join(openers[-60:]),
    ])


def chapter_number(outline: Outline, section: Section) -> int:
    """1-based position of a body section among the body sections, for "Chapter four."."""
    body = [s for s in outline.sections if s.kind == "body"]
    return body.index(section) + 1 if section in body else 0


def _midroll_block(project: VideoProject) -> str:
    return ("MID-ROLL BREAK\n"
            "This section, and only this section, opens with the break below, written as "
            "its own short paragraph BEFORE the chapter announcement. It is the one moment "
            "in two hours when the narrator speaks as themselves.\n\n"
            + project.channel.template("midroll"))


def _ritual_block(project: VideoProject, outline: Outline, section: Section) -> str:
    ch = project.channel
    name = "opening" if section.kind == "opening" else "winddown"
    first_body = next((s for s in outline.sections if s.kind == "body"), None)
    scope_line = f"{project.scope} of {project.game}"
    template = ch.template(name).format(
        channel_name=ch.name, welcome_image=outline.flavor.welcome_image or "a quiet shelter",
        scope_line=scope_line, address=outline.flavor.address,
        farewell=outline.flavor.farewell or f"Good night, {outline.flavor.address}.")
    extra = ""
    if section.kind == "opening":
        extra = (f"\n\nTONIGHT'S ENTRANCE: {outline.opening_mode or 'welcome'}. Write that "
                 "one and no other; the modes not chosen must leave no trace in the text.")
        if first_body:
            extra += f"\nThe first section after this one is \"{first_body.title}\"."
    return f"RITUAL TEMPLATE ({name})\n{template}{extra}"


def section_prompt(project: VideoProject, outline: Outline, bible: LoreBible, section: Section,
                   ledger: dict, prev_tail: str, tics: list[str] | None = None) -> str:
    facts = bible.fact_map()
    wpm = get_settings().narration_wpm
    idx = outline.sections.index(section)
    nxt = outline.sections[idx + 1] if idx + 1 < len(outline.sections) else None
    parts = [f"WRITE SECTION {section.id}: \"{section.title}\" ({section.kind})",
             f"Goal: {section.goal}",
             f"Length: about {section.target_words} words (never fewer than "
             f"{round(section.target_words * MIN_RATIO)}). At ~{wpm} words per minute this is "
             f"about {section.target_words / wpm:.0f} minutes of narration."]
    if section.kind in ("opening", "winddown"):
        parts.append(_ritual_block(project, outline, section))
    if section.kind == "body":
        if section.midroll:
            parts.append(_midroll_block(project))
        parts.append(
            "CHAPTER ANNOUNCEMENT\n"
            f"Open this section with the spoken line \"Chapter "
            f"{_spoken_number(chapter_number(outline, section))}. {section.title}.\" on its own "
            "line, exactly once, with nothing before it in the section and no flourish "
            "around it. The paragraph after it re-anchors the listener in place and time, "
            "because they may have half-woken since the last one. End the section on one "
            "or two sentences that land what it told.")
    if section.fact_ids:
        parts.append("Facts for this section (tell every one of them, in an order that flows):\n"
                     + "\n".join(_fact_line(facts[f]) for f in section.fact_ids if f in facts))
    if project.format == "facts" and section.kind == "body":
        parts.append("This is a facts video: each fact gets one to three sentences, joined by "
                     "gentle transitions, never numbered or listed.")
    parts.append(_ledger_block(ledger, section.id))
    if tics:
        parts.append("WORDS ALREADY LEANED ON in this script (find other ways to say these): "
                     + ", ".join(tics))
    if prev_tail:
        parts.append("The previous section ended like this (continue naturally from it, do not "
                     f"repeat it):\n\"\"\"{prev_tail}\"\"\"")
    if nxt:
        parts.append(f"The next section is \"{nxt.title}\": end in a way that could lead there, "
                     "without announcing it.")
    return "\n\n".join(parts)


# --- Output parsing ------------------------------------------------------------------

def parse_output(raw: str) -> tuple[str, dict]:
    """Split model output into (prose, trailer dict with used_facts/summary/images)."""
    m = _TRAILER.search(raw)
    prose = raw[:m.start()] if m else raw
    trailer = raw[m.start():] if m else ""
    meta: dict = {"used_facts": [], "summary": "", "images": []}
    for line in trailer.splitlines():
        key, _, value = line.partition(":")
        key = key.strip().upper()
        if key == "USED_FACTS":
            meta["used_facts"] = re.findall(r"f\d{3,}", value)
        elif key == "SUMMARY":
            meta["summary"] = value.strip()
        elif key == "IMAGES":
            meta["images"] = [v.strip() for v in value.split(",") if v.strip()]
    prose = re.sub(r"(?m)^\s*#+\s.*$", "", prose)          # stray headings
    prose = re.sub(r"\n{3,}", "\n\n", prose).strip()
    return prose, meta


def paragraph_openers(text: str) -> list[str]:
    """Openers fed to the ledger so later sections vary theirs.

    The chapter announcement is skipped: it is supposed to be identical every time, and
    feeding it back would have the ledger asking the writer to vary the one line that
    must not vary.
    """
    return [" ".join(p.split()[:3]) for p in lint_service.paragraphs(text)
            if not p.lstrip().lower().startswith("chapter ")]


# --- Repairs -------------------------------------------------------------------------

FIX_SYSTEM = """\
You are the line editor of a sleep-narration script. You receive one section and a
list of flagged sentences with the reason each was flagged. Rewrite ONLY those
sentences so the problem disappears, keeping meaning, facts, rhythm and the channel's
calm voice. Never introduce a banned phrase, and never reuse wording the reason says is
already used elsewhere.

Return ONE JSON object: {"replacements": [{"old": "exact flagged sentence", "new": "rewritten sentence"}]}
"""


FIX_SCHEMA = ai.obj({"replacements": ai.array(ai.obj({"old": ai.STR, "new": ai.STR}))})


def fix_findings(prefix: str, text: str, findings: list[lint_service.Finding]) -> tuple[str, ai.Completion, int]:
    flagged: dict[str, list[str]] = {}
    for f in findings:
        if f.sentence:
            flagged.setdefault(f.sentence, []).append(f"{f.kind}: {f.detail}")
    listing = "\n".join(f"- \"{s}\"\n  reason: {'; '.join(r)}" for s, r in flagged.items())
    user = f"SECTION\n\"\"\"\n{text}\n\"\"\"\n\nFLAGGED SENTENCES\n{listing}"
    data, comp = ai.complete_json([ai.cached(prefix), ai.plain(FIX_SYSTEM)], user,
                                  model=get_settings().anthropic_model_script,
                                  max_tokens=16000, effort="medium", schema=FIX_SCHEMA)
    applied = 0
    for r in data.get("replacements", []):
        old, new = (r.get("old") or "").strip(), (r.get("new") or "").strip()
        if old and new and old in text:
            text = text.replace(old, new, 1)
            applied += 1
    return text, comp, applied


EXPAND_INSTRUCTION = """\
The section below is too short: {have} words for a target of {target}. Rewrite it at
about {target} words by adding material, keeping every existing paragraph's content.
Add depth with the EXTRA FACTS listed (they have not been told anywhere in the video)
and with slower, sensory description of places already in the section. Do not pad with
summaries, repetition or rhetorical questions. Output the full section in the same
format (prose, then USED_FACTS / SUMMARY / IMAGES lines).
"""


def extra_facts(bible: LoreBible, outline: Outline, section: Section, ledger: dict,
                limit: int = 12) -> list[Fact]:
    """Unused facts closest to this section: shared entities, category and timeline."""
    assigned = {f for s in outline.sections for f in s.fact_ids}
    told = {f for e in ledger.values() for f in e.get("used_facts", [])}
    facts = bible.fact_map()
    own = [facts[f] for f in section.fact_ids if f in facts]
    ents = {e for f in own for e in f.entity_ids}
    cats = {f.category for f in own}
    chronos = [f.chrono for f in own if f.chrono is not None]
    mid = sorted(chronos)[len(chronos) // 2] if chronos else None

    def score(f: Fact) -> float:
        s = 3.0 * len(ents & set(f.entity_ids)) + (1.5 if f.category in cats else 0)
        if mid is not None and f.chrono is not None:
            s += max(0.0, 1.5 - abs(f.chrono - mid) / 10)
        return s - (1.0 if f.status == "theory" else 0)

    pool = [f for f in bible.facts if f.id not in assigned and f.id not in told]
    ranked = sorted(pool, key=score, reverse=True)
    return [f for f in ranked[:limit] if score(f) > 0]


# --- Step ----------------------------------------------------------------------------

def _hash(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


def _references(project: VideoProject) -> list[str]:
    """Transcripts the script must not echo: this project's research, plus the channel's
    own model channel. We copy the reference channel's structure on purpose, so the
    eight-gram check against it is the thing that keeps that from becoming its words."""
    dirs = [project.transcripts_dir, project.channel.path("reference")]
    return [p.read_text(encoding="utf-8", errors="ignore")
            for d in dirs if d.exists() for p in sorted(d.glob("*.txt"))]


def write_section(project: VideoProject, outline: Outline, bible: LoreBible, section: Section,
                  ledger: dict, prev_tail: str, prior_texts: dict[str, str], log) -> tuple[str, dict]:
    ch = project.channel
    prefix = cached_prefix(project, outline, bible)
    # Tics found in what is already written feed forward, so the script self-corrects
    # instead of leaning on the same word for two hours.
    tics = [f.detail.split("'")[1] for f in
            lint_service.check_overused(prior_texts, names=bible.entity_names())] if prior_texts else []
    user = section_prompt(project, outline, bible, section, ledger, prev_tail, tics)
    comp = ai.complete([ai.cached(prefix)], user, model=get_settings().anthropic_model_script,
                       max_tokens=32000, effort="high")
    project.add_costs(**comp.cost_deltas())
    text, meta = parse_output(comp.text)
    log(f"  {section.id}: {lint_service.word_count(text)} words "
        f"(cache read {comp.cache_read_tokens} / write {comp.cache_write_tokens} tokens)")

    # Expansion when short.
    words = lint_service.word_count(text)
    if words < section.target_words * MIN_RATIO:
        extras = extra_facts(bible, outline, section, ledger)
        extra_block = "\n".join(_fact_line(f) for f in extras) or "(none left: deepen description)"
        user_x = (EXPAND_INSTRUCTION.format(have=words, target=section.target_words)
                  + f"\nEXTRA FACTS\n{extra_block}\n\nSECTION\n\"\"\"\n{text}\n\"\"\"")
        comp = ai.complete([ai.cached(prefix)], user_x, model=get_settings().anthropic_model_script,
                           max_tokens=32000, effort="high")
        project.add_costs(**comp.cost_deltas())
        text2, meta2 = parse_output(comp.text)
        if lint_service.word_count(text2) > words:
            text, meta = text2, meta2
            log(f"  {section.id}: expanded to {lint_service.word_count(text)} words "
                f"with {len(extras)} extra facts available")

    # Lint + surgical fixes (checked against everything written before this section).
    references = _references(project)
    names = bible.entity_names()
    for round_ in range(MAX_FIX_ROUNDS):
        scope = {**prior_texts, section.id: text}
        report = lint_service.lint(scope, banned_patterns=ch.banned_patterns(),
                                   references=references, names=names)
        mine = [f for f in report.by_section(section.id) if f.kind != "overused"]
        if not mine:
            break
        log(f"  {section.id}: fixing {len(mine)} lint finding(s) (round {round_ + 1}): "
            f"{json.dumps(lint_service.LintReport(findings=mine).counts())}")
        text, comp, applied = fix_findings(prefix, text, mine)
        project.add_costs(**comp.cost_deltas())
        if not applied:
            break

    meta["openers"] = paragraph_openers(text)
    return text, meta


def run(project: VideoProject, params: dict, on_log) -> dict:
    log = on_log
    force = bool(params.get("force"))
    only = params.get("section")
    outline = Outline.model_validate_json(project.outline_path.read_text(encoding="utf-8"))
    bible = LoreBible.model_validate_json(project.lore_bible_path.read_text(encoding="utf-8"))
    ch = project.channel
    style_hash = _hash(ch.style_guide, "\n".join(ch.banned_phrases_display()),
                       ch.template("opening"), ch.template("winddown"))
    ledger = json.loads(project.ledger_path.read_text(encoding="utf-8")) if project.ledger_path.exists() else {}

    texts: dict[str, str] = {}
    prev_tail = ""
    written = 0
    for section in outline.sections:
        md = project.section_md_path(section.id)
        meta_path = md.with_suffix(".meta.json")
        in_hash = _hash(section.model_dump_json(), outline.flavor.model_dump_json(), style_hash)
        cached = (md.exists() and meta_path.exists()
                  and json.loads(meta_path.read_text(encoding="utf-8")).get("hash") == in_hash)
        # --section sNN rewrites just that one (missing sections are still filled in).
        redo = (only == section.id) if only else (force or not cached)
        if not redo and md.exists():
            text = md.read_text(encoding="utf-8")
        else:
            log(f"Writing {section.id} [{section.kind}] {section.title} (~{section.target_words} words)...")
            text, meta = write_section(project, outline, bible, section, ledger, prev_tail, texts, log)
            md.write_text(text + "\n", encoding="utf-8")
            meta_path.write_text(json.dumps({"hash": in_hash, **meta}, indent=2), encoding="utf-8")
            ledger[section.id] = meta
            project.ledger_path.write_text(json.dumps(ledger, indent=2, ensure_ascii=False), encoding="utf-8")
            written += 1
        texts[section.id] = text
        words = text.split()
        prev_tail = " ".join(words[-TAIL_WORDS:])

    return assemble(project, outline, bible, texts, ledger, log) | {"written": written}


def assemble(project: VideoProject, outline: Outline, bible: LoreBible, texts: dict[str, str],
             ledger: dict, log) -> dict:
    ch = project.channel
    report = lint_service.lint(texts, banned_patterns=ch.banned_patterns(),
                               references=_references(project), names=bible.entity_names())
    project.lint_path.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
                                 encoding="utf-8")
    sections = []
    for s in outline.sections:
        text = texts.get(s.id, "")
        sections.append({"id": s.id, "kind": s.kind, "title": s.title,
                         "target_words": s.target_words, "words": lint_service.word_count(text),
                         "used_facts": ledger.get(s.id, {}).get("used_facts", []), "text": text})
    total = sum(x["words"] for x in sections)
    wpm = get_settings().narration_wpm
    project.script_json_path.write_text(json.dumps(
        {"game": project.game, "format": project.format, "total_words": total,
         "est_minutes": round(total / wpm, 1), "sections": sections}, indent=2, ensure_ascii=False),
        encoding="utf-8")
    project.script_txt_path.write_text("\n\n".join(x["text"] for x in sections) + "\n", encoding="utf-8")
    assigned = {f for s in outline.sections for f in s.fact_ids}
    told = {f for x in sections for f in x["used_facts"]}
    log(f"Script: {total} words (~{total / wpm:.0f} min at {wpm} wpm), target {project.target_words}. "
        f"Facts told {len(told & assigned)}/{len(assigned)} assigned (+{len(told - assigned)} extra). "
        f"Lint: {report.counts() or 'clean'}")
    return {"words": total, "minutes": round(total / wpm, 1), "lint": report.counts()}
