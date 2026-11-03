"""YouTube metadata: title, description, tags and chapters.

The title follows the patterns that work on this kind of channel, and the hour count
comes from the REAL audio duration, rounded down ("2+ Hours", never a promise the video
doesn't keep). Only the SEO paragraph, the "perfect for fans of" line and the coverage
list are written by Claude: the rest of the description is the channel's fixed template
(spoiler notice, fair use, about, publisher, calls to action, farewell, hashtags).

Chapters come from ``sections.json``, so they match the narration to the second.
"""
from __future__ import annotations

import json
import re
from datetime import date

from loreforge.integrations import anthropic_client as ai
from loreforge.integrations.media import probe
from loreforge.models.outline import Outline
from loreforge.models.project import VideoProject

TITLE_PATTERNS = {
    "history": "The Entire Lore of {game} Explained To Fall Asleep To",
    "facts": "{hours} Hours of {game} Facts To Fall Asleep To",
    "catalog": "Every {thing} in {game} Explained To Fall Asleep To",
}

SPOTIFY_LINE = ("Rather listen with the screen off? Every night of lore is also a podcast "
                "on Spotify: {url}")

COPY_SYSTEM = """\
You write YouTube copy for a sleep channel that narrates video game lore. You write only
the parts that change per video, in the channel's calm voice: no hype, no emoji, no
exclamation marks, no promises the video doesn't keep.
"""

COPY_SCHEMA = ai.obj({
    "seo_paragraph": ai.STR,
    "fans_of": ai.STR,
    "covers": ai.STR_LIST,
    "tags": ai.STR_LIST,
    "hashtags": ai.STR_LIST,
    "catalog_noun": ai.STR,
})


def timestamp(seconds: float) -> str:
    """YouTube chapter timestamp: H:MM:SS (or M:SS in the first hour)."""
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def build_chapters(sections: list[dict], total: float) -> list[str]:
    """Chapter lines. YouTube needs 3+, the first at 0:00, each at least 10 seconds."""
    lines, last = [], -10.0
    for sec in sections:
        start = 0.0 if not lines else float(sec["start"])
        if lines and (start - last < 10 or total - start < 10):
            continue
        lines.append(f"{timestamp(start)} {sec['title']}")
        last = start
    return lines


def _render(template: str, fields: dict) -> str:
    """Fill a channel template; an empty optional field leaves no blank gap behind."""
    return re.sub(r"\n{3,}", "\n\n", template.format(**fields)).strip() + "\n"


def run(project: VideoProject, params: dict, on_log) -> dict:
    log = on_log
    ch = project.channel
    outline = Outline.model_validate_json(project.outline_path.read_text(encoding="utf-8"))
    sections = json.loads(project.sections_path.read_text(encoding="utf-8"))
    total = probe(project.narration_wav_path).duration
    hours = int(total // 3600)

    # A catalog title needs the noun the video goes through ("Every Boss in ..."). Scopes
    # are free text ("all bosses in the game", "the entire game"), so Claude names it.
    pattern = TITLE_PATTERNS[project.format]
    title = params.get("title") or ("" if "{thing}" in pattern else pattern.format(
        game=project.game, hours=max(1, hours)))

    covers = "\n".join(f"- {s.title}: {s.goal}" for s in outline.sections if s.kind == "body")
    title_line = title or pattern.format(game=project.game, hours=max(1, hours),
                                         thing="<catalog_noun>")
    user = (f"Game: {project.game}\nScope: {project.scope}\nFormat: {project.format}\n"
            f"Title: {title_line}\nDuration: {total / 60:.0f} minutes\n"
            f"Similar games the outline suggested: {', '.join(outline.flavor.fans_of)}\n"
            f"Sections:\n{covers}\n\n"
            "Return: seo_paragraph (2-3 sentences describing this sleep documentary, using "
            "the game's name and the words listeners search for: lore, explained, sleep, "
            "relaxing narration); fans_of (one line listing similar games, comma separated); "
            "covers (6-10 short lines naming what the video covers, no timestamps); "
            "tags (13 YouTube tags, lowercase, no '#'); hashtags (exactly 3, each starting "
            "with '#'); catalog_noun (one singular noun in Title Case for what the sections "
            "go through one by one, e.g. Boss, Weapon, Demigod).")
    data, comp = ai.complete_json(COPY_SYSTEM, user, schema=COPY_SCHEMA,
                                  max_tokens=8000, effort="medium")
    project.add_costs(**comp.cost_deltas())
    title = title or pattern.format(game=project.game, hours=max(1, hours),
                                    thing=data.get("catalog_noun", "").strip() or "Boss")

    chapters = build_chapters(sections, total)
    tags = list(dict.fromkeys([*data.get("tags", []), *ch.base_tags]))[:15]
    fields = dict(
        seo_paragraph=data["seo_paragraph"].strip(),
        fans_of=data["fans_of"].strip().rstrip("."),
        covers_list="\n".join(f"- {c}" for c in data.get("covers", [])),
        chapters="\n".join(chapters),
        game=project.game,
        # The template ends the sentence itself: "FromSoftware Inc." must not become "Inc.."
        publisher=project.publisher.strip().rstrip(".") or "its publisher",
        channel_name=ch.name, channel_upper=ch.name.upper(),
        farewell=outline.flavor.farewell,
        year=date.today().year,
        hashtags=" ".join(data.get("hashtags", [])[:3]),
        spotify_line=SPOTIFY_LINE.format(url=ch.spotify_link) if ch.spotify_link else "",
        music_credit=project.music_credit())
    if ch.spotify_url and not ch.spotify_link:
        log("WARNING: spotify_url is not a public open.spotify.com link; the Spotify line "
            "was left out of the description.")
    description = _render(ch.template("description"), fields)

    meta = {"title": title, "description": description, "tags": tags,
            "chapters": chapters, "duration_minutes": round(total / 60, 1),
            "thumbnail": str(project.thumbnail_path) if project.thumbnail_path.exists() else ""}
    project.script_dir.mkdir(parents=True, exist_ok=True)
    project.metadata_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    project.description_path.write_text(description, encoding="utf-8")
    if podcast_template := ch.template("podcast_description"):
        project.podcast_description_path.write_text(_render(podcast_template, fields),
                                                    encoding="utf-8")
    project.title = title
    project.save()
    log(f"Title: {title}")
    log(f"{len(chapters)} chapters, {len(tags)} tags, description {len(description)} chars "
        f"-> {project.description_path.name}")
    if len(description) > 5000:
        log("WARNING: the description is over YouTube's 5000-character limit.")
    return {"title": title, "chapters": len(chapters), "tags": len(tags)}
