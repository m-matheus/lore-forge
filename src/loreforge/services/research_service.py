"""Research step: build the game's lore bible from sources and reference transcripts.

The lore bible is the factual backbone that keeps a 15k-word script from hallucinating.
It lives per GAME in ``library/{game}/`` and is shared by every video of that game.

Pipeline:
1. Ingest: ``sources/`` files (txt/md/html/pdf) + the automatic captions of the
   brief's ``reference_urls`` (json3). Each becomes a registered source with an id.
2. Extract (per chunk, parallel, cached by content hash in ``library/{game}/extract/``):
   atomic facts, PARAPHRASED in neutral wording, tagged canon / inferred / theory.
   Reference videos are competitors: we take knowledge, never their phrasing.
3. Merge per category: deduplicate, union the sources, keep the most precise wording.
4. Chronology: order the story events and link them to facts (drives ``history``).
5. Coverage report: facts per category and whether the bible can carry the duration.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from loreforge.config.settings import get_settings
from loreforge.integrations import anthropic_client as ai
from loreforge.integrations import youtube_dl
from loreforge.models.lore import Entity, Event, Fact, LoreBible, SourceRef, CATEGORIES
from loreforge.models.project import VideoProject

CHUNK_CHARS = 40_000
CHUNK_OVERLAP = 1_500
PARALLEL = 4

# Rough budget: how many distinct facts a video needs, per spoken minute.
FACTS_PER_MINUTE = {"history": 2.0, "facts": 2.4, "catalog": 1.8}


# --- Ingestion -----------------------------------------------------------------------

def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|nav|footer|header|aside)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>|</tr>", "\n", html)
    text = re.sub(r"<[^>]+>", " ", html)
    import html as _html
    text = _html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def _read_source(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        return "\n\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages)
    raw = path.read_text(encoding="utf-8", errors="replace")
    if ext in (".html", ".htm"):
        return _html_to_text(raw)
    return raw


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _transcript(project: VideoProject, url: str, log) -> tuple[str, str]:
    """Return (title, plain transcript) for a reference URL, cached in the run."""
    project.transcripts_dir.mkdir(parents=True, exist_ok=True)
    video_id = re.search(r"(?:v=|youtu\.be/|shorts/)([\w-]{6,})", url)
    key = video_id.group(1) if video_id else _sha(url)
    txt = project.transcripts_dir / f"{key}.txt"
    meta = project.transcripts_dir / f"{key}.meta.json"
    if txt.exists() and meta.exists():
        return json.loads(meta.read_text(encoding="utf-8")).get("title", url), txt.read_text(encoding="utf-8")
    info = youtube_dl.probe(url)
    j3 = youtube_dl.download_auto_subs_json3(url, project.transcripts_dir / key, on_log=log)
    if j3 is None:
        raise RuntimeError(f"No English captions available for {url}")
    words = youtube_dl.json3_words(json.loads(j3.read_text(encoding="utf-8")))
    (project.transcripts_dir / f"{key}.words.json").write_text(json.dumps(words), encoding="utf-8")
    text = " ".join(w["word"] for w in words)
    txt.write_text(text, encoding="utf-8")
    meta.write_text(json.dumps({"url": url, "title": info.get("title", url),
                                "duration": info.get("duration")}), encoding="utf-8")
    log(f"Transcript: {info.get('title')} ({len(words)} words)")
    return info.get("title", url), text


def gather_sources(project: VideoProject, log) -> list[dict]:
    """All sources as [{id, kind, title, origin, text, sha}] with stable ids by content."""
    items: list[dict] = []
    for path in project.source_files():
        try:
            text = _read_source(path).strip()
        except Exception as exc:  # noqa: BLE001 - one unreadable file shouldn't stop research
            log(f"Skipping {path.name}: {exc}")
            continue
        if text:
            items.append({"kind": "document", "title": path.name,
                          "origin": str(path.relative_to(project.run_dir)), "text": text})
    for url in project.reference_urls:
        title, text = _transcript(project, url, log)
        items.append({"kind": "reference_video", "title": title, "origin": url, "text": text})
    for it in items:
        it["sha"] = _sha(it["text"])
        it["id"] = f"src-{it['sha'][:8]}"
    return items


def chunk(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split on paragraph/sentence boundaries into ~``size`` char pieces with overlap."""
    if len(text) <= size:
        return [text]
    out, start = [], 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            window = text[start:end]
            cut = max(window.rfind("\n\n"), window.rfind(". "))
            if cut > size // 2:
                end = start + cut + 1
        out.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return out


# --- Schemas (structured outputs: the API guarantees valid JSON of this shape) --------

_STATUS = {"type": "string", "enum": ["canon", "inferred", "theory"]}
EXTRACT_SCHEMA = ai.obj({
    "entities": ai.array(ai.obj({"name": ai.STR, "kind": ai.STR, "aliases": ai.STR_LIST})),
    "facts": ai.array(ai.obj({"text": ai.STR, "entities": ai.STR_LIST,
                              "category": {"type": "string", "enum": CATEGORIES},
                              "status": _STATUS, "chrono": ai.NULLABLE_INT})),
})
MERGE_SCHEMA = ai.obj({"facts": ai.array(ai.obj({
    "text": ai.STR, "entities": ai.STR_LIST, "status": _STATUS,
    "chrono": ai.NULLABLE_INT, "src": ai.STR_LIST}))})
CHRONO_SCHEMA = ai.obj({"events": ai.array(ai.obj({"summary": ai.STR, "fact_ids": ai.STR_LIST}))})


# --- Extraction ----------------------------------------------------------------------

EXTRACT_SYSTEM = f"""\
You are a meticulous lore researcher building a fact database about a video game for a
long-form narration channel. You read raw source text (wiki pages, notes, or the
automatic captions of someone else's lore video) and extract ATOMIC FACTS.

Rules:
- One fact = one self-contained claim a listener could understand alone. Include the
  subject's full name in the fact, never a bare pronoun.
- PARAPHRASE every fact in plain, neutral wording of your own. Never copy a sentence,
  a metaphor or a turn of phrase from the source; we are extracting knowledge, not text.
  Reference videos are other creators' work: their phrasing must not survive.
- Keep precise details (names, places, item names, numbers, in-game quotes marked as
  quotes) exactly: precision is the point. Only verbatim in-game text may be quoted.
- status: "canon" = shown or stated in the game (item descriptions, dialogue, events);
  "inferred" = strongly implied, widely accepted reading; "theory" = speculation or fan
  interpretation. When a source presents speculation as fact, mark it "theory".
- Automatic captions contain transcription errors: fix obvious misspellings of names
  (e.g. "yarn um" -> "Yharnam") and drop anything too garbled to trust.
- Ignore channel chatter: greetings, sponsor reads, calls to subscribe, sleep cues.
- category: one of {", ".join(CATEGORIES)}.
- chrono: for story events, a rough position on the in-world timeline from 0 (deep
  past) to 100 (the player's night / the ending); null for timeless facts.

Return ONE JSON object, no commentary:
{{"entities": [{{"name": "...", "kind": "character|faction|location|item|weapon|boss|enemy|concept|event", "aliases": ["..."]}}],
 "facts": [{{"text": "...", "entities": ["exact entity names"], "category": "...", "status": "canon|inferred|theory", "chrono": 0-100 or null}}]}}
"""


def _extract_chunk(game: str, source: dict, idx: int, text: str) -> tuple[dict, ai.Completion]:
    user = (f"Game: {game}\nSource type: {source['kind']}\nSource title: {source['title']}\n"
            f"Chunk {idx + 1}\n\n<source>\n{text}\n</source>\n\n"
            "Extract every useful fact from this chunk.")
    data, comp = ai.complete_json(EXTRACT_SYSTEM, user,
                                  model=get_settings().anthropic_model_research,
                                  max_tokens=32000, effort="medium", schema=EXTRACT_SCHEMA)
    return data, comp


# --- Merge ---------------------------------------------------------------------------

MERGE_SYSTEM = """\
You deduplicate a list of lore facts about a video game that were extracted from
several sources. Facts saying the same thing (even in different words or with different
detail) must become ONE fact.

Rules:
- Keep the most precise, complete wording; you may combine details from duplicates into
  one sentence if they are the same claim. Never invent anything.
- Keep the wording neutral and your own. Keep verbatim in-game quotes as quotes.
- When duplicates disagree on status, use the most cautious (theory > inferred > canon)
  unless a document source (wiki/notes) supports it as canon.
- Drop facts that are trivial, garbled or not about the game's world/story/production.
- Preserve "src" as the UNION of the source ids of every merged fact.

Return ONE JSON object: {"facts": [{"text": "...", "entities": ["..."], "status": "...",
"chrono": number or null, "src": ["src-..."]}]}
"""


def _merge_category(game: str, category: str, facts: list[dict],
                    cache_dir: Path) -> tuple[list[dict], ai.Completion | None]:
    """Deduplicate one category. Cached by the hash of its input facts."""
    if len(facts) <= 1:
        return facts, None
    rows = [{"text": f.get("text", ""), "entities": f.get("entities") or [],
             "status": f.get("status", "inferred"), "chrono": f.get("chrono"),
             "src": f.get("src") or []} for f in facts]
    listing = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
    cache = cache_dir / f"_merge_{category}_{_sha(listing)}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8")), None
    user = (f"Game: {game}\nCategory: {category}\n{len(facts)} facts, one JSON per line:\n\n"
            f"{listing}\n\nReturn the deduplicated list.")
    data, comp = ai.complete_json(MERGE_SYSTEM, user, model=get_settings().anthropic_model_research,
                                  max_tokens=64000, effort="medium", schema=MERGE_SCHEMA)
    merged = data.get("facts", [])
    cache.write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
    return merged, comp


CHRONO_SYSTEM = """\
You order the story of a video game. Given numbered facts, produce the in-world
chronology as a list of EVENTS from the deep past to the ending. Each event is one
story beat (a founding, a discovery, a betrayal, a battle, the player's arrival...),
summarized in one neutral sentence, and cites the ids of the facts that support it.
Use only what the facts say; where order is uncertain, say so in the summary.

Aim for roughly 15-40 events: group related facts into one beat rather than one event
per fact. Do not deliberate at length over ambiguous ordering (time loops, flashbacks,
conflicting sources): pick the most plausible position, note the doubt in the summary
and move on. Not every fact needs to be cited.

Return ONE JSON object: {"events": [{"summary": "...", "fact_ids": ["f0001", ...]}]}
"""


def _chronology(game: str, facts: list[Fact]) -> tuple[list[Event], ai.Completion | None]:
    story = [f for f in facts if f.category in ("history", "character", "faction", "ending", "location")
             or f.chrono is not None]
    if not story:
        return [], None
    listing = "\n".join(f"{f.id} [{f.category}, {f.status}, chrono={f.chrono}] {f.text}" for f in story)
    data, comp = ai.complete_json(CHRONO_SYSTEM, f"Game: {game}\n\n{listing}",
                                  model=get_settings().anthropic_model_research,
                                  max_tokens=64000, effort="medium", schema=CHRONO_SCHEMA)
    known = {f.id for f in facts}
    events = []
    for i, e in enumerate(data.get("events", []), start=1):
        ids = [x for x in e.get("fact_ids", []) if x in known]
        events.append(Event(id=f"e{i:03d}", order=i, summary=e.get("summary", ""), fact_ids=ids))
    return events, comp


# --- Entities ------------------------------------------------------------------------

def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def _merge_entities(raw: list[dict]) -> tuple[list[Entity], dict[str, str]]:
    """Union entities by normalized name/alias. Returns (entities, normalized name -> id)."""
    groups: list[dict] = []
    index: dict[str, int] = {}
    for e in raw:
        names = [e.get("name", "")] + list(e.get("aliases") or [])
        keys = [_norm(n) for n in names if _norm(n)]
        if not keys:
            continue
        hit = next((index[k] for k in keys if k in index), None)
        if hit is None:
            groups.append({"name": e["name"], "kind": e.get("kind", "concept"),
                           "aliases": set(), "count": 0})
            hit = len(groups) - 1
        g = groups[hit]
        g["count"] += 1
        for n in names:
            if n and _norm(n) != _norm(g["name"]):
                g["aliases"].add(n)
        for k in keys:
            index.setdefault(k, hit)
    groups.sort(key=lambda g: (-g["count"], g["name"]))
    entities = [Entity(id=f"n{i:04d}", name=g["name"], kind=g["kind"], aliases=sorted(g["aliases"]))
                for i, g in enumerate(groups, start=1)]
    by_norm: dict[str, str] = {}
    for ent in entities:
        for n in [ent.name, *ent.aliases]:
            by_norm.setdefault(_norm(n), ent.id)
    return entities, by_norm


# --- Step ----------------------------------------------------------------------------

def run(project: VideoProject, params: dict, on_log) -> dict:
    log = on_log
    lib = project.library_dir
    extract_dir = lib / "extract"
    merge_dir = lib / "merge"
    extract_dir.mkdir(parents=True, exist_ok=True)
    merge_dir.mkdir(parents=True, exist_ok=True)
    force = bool(params.get("force"))

    sources = gather_sources(project, log)
    if not sources:
        raise RuntimeError("No sources: add files to sources/ or reference URLs to the brief.")
    registry_path = lib / "sources.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8")) if registry_path.exists() else {}
    for s in sources:
        registry[s["id"]] = {k: s[k] for k in ("kind", "title", "origin", "sha")}
    registry_path.write_text(json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8")

    # Every registered source of this GAME contributes, including ones added by other runs.
    jobs = []
    for s in sources:
        for i, piece in enumerate(chunk(s["text"])):
            out = extract_dir / f"{s['id']}_{i:02d}.json"
            if (force and params.get("reextract")) or not out.exists():
                jobs.append((s, i, piece, out))
    log(f"{len(sources)} source(s); {len(jobs)} chunk(s) to extract "
        f"(cached chunks are reused).")

    lock = threading.Lock()
    usage: list[ai.Completion] = []

    def work(job):
        s, i, piece, out = job
        data, comp = _extract_chunk(project.game, s, i, piece)
        data["_src"] = s["id"]
        out.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        with lock:
            usage.append(comp)
        return s["title"], i, len(data.get("facts", []))

    if jobs:
        with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
            for fut in as_completed([pool.submit(work, j) for j in jobs]):
                title, i, n = fut.result()
                log(f"  extracted {n} facts from {title[:50]} [chunk {i + 1}]")

    # Load every extract of this game.
    raw_entities: list[dict] = []
    raw_facts: list[dict] = []
    for f in sorted(extract_dir.glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        src_id = data.get("_src") or f.stem.rsplit("_", 1)[0]
        if src_id not in registry:
            continue
        raw_entities += data.get("entities", [])
        for fact in data.get("facts", []):
            if fact.get("text"):
                fact["src"] = [src_id]
                fact["category"] = fact.get("category") if fact.get("category") in CATEGORIES else "world"
                raw_facts.append(fact)
    log(f"Raw: {len(raw_facts)} facts, {len(raw_entities)} entity mentions. Merging...")

    by_cat: dict[str, list[dict]] = {}
    for fact in raw_facts:
        by_cat.setdefault(fact["category"], []).append(fact)

    merged: dict[str, list[dict]] = {}

    def merge_job(cat):
        facts, comp = _merge_category(project.game, cat, by_cat[cat], merge_dir)
        with lock:
            if comp:
                usage.append(comp)
        return cat, facts

    with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
        for fut in as_completed([pool.submit(merge_job, c) for c in by_cat]):
            cat, facts = fut.result()
            merged[cat] = facts
            log(f"  {cat}: {len(by_cat[cat])} -> {len(facts)}")

    entities, by_norm = _merge_entities(raw_entities)
    facts: list[Fact] = []
    for cat in CATEGORIES:
        rows = sorted(merged.get(cat, []), key=lambda r: (r.get("chrono") is None, r.get("chrono") or 0))
        for r in rows:
            ent_ids = sorted({by_norm[_norm(n)] for n in r.get("entities", []) if _norm(n) in by_norm})
            status = r.get("status") if r.get("status") in ("canon", "inferred", "theory") else "inferred"
            chrono = r.get("chrono")
            facts.append(Fact(
                id=f"f{len(facts) + 1:04d}", text=r["text"].strip(), entity_ids=ent_ids,
                category=cat, status=status,
                chrono=int(chrono) if isinstance(chrono, (int, float)) else None,
                sources=[SourceRef(src_id=s) for s in (r.get("src") or []) if s in registry]))
    log(f"Merged: {len(facts)} facts. Building chronology...")

    events, comp = _chronology(project.game, facts)
    if comp:
        usage.append(comp)

    bible = LoreBible(game=project.game, updated_at=datetime.now().isoformat(timespec="seconds"),
                      sources=registry, entities=entities, facts=facts, events=events)
    project.lore_bible_path.parent.mkdir(parents=True, exist_ok=True)
    project.lore_bible_path.write_text(bible.model_dump_json(indent=1), encoding="utf-8")

    for c in usage:
        project.add_costs(**c.cost_deltas())
    report = coverage(bible, project)
    (lib / "coverage.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    log(f"Lore bible: {len(facts)} facts, {len(entities)} entities, {len(events)} events -> "
        f"{project.lore_bible_path}")
    for warning in report["warnings"]:
        log(f"WARNING: {warning}")
    return {"facts": len(facts), "entities": len(entities), "events": len(events),
            "warnings": report["warnings"]}


def coverage(bible: LoreBible, project: VideoProject) -> dict:
    by_cat: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for f in bible.facts:
        by_cat[f.category] = by_cat.get(f.category, 0) + 1
        by_status[f.status] = by_status.get(f.status, 0) + 1
    needed = round(project.target_minutes * FACTS_PER_MINUTE.get(project.format, 2.0))
    usable = by_status.get("canon", 0) + by_status.get("inferred", 0)
    warnings = []
    if usable < needed:
        warnings.append(f"{usable} canon/inferred facts for ~{needed} needed at "
                        f"{project.target_minutes} min ({project.format}). Add sources "
                        f"(wiki pages, item descriptions) to sources/ and re-run research.")
    doc_sources = sum(1 for s in bible.sources.values() if s.get("kind") == "document")
    if doc_sources == 0:
        warnings.append("Only reference-video transcripts were used. Their claims are "
                        "unverified; add wiki/notes to sources/ to anchor canon.")
    return {"facts": len(bible.facts), "needed": needed, "usable": usable,
            "by_category": by_cat, "by_status": by_status, "warnings": warnings}
