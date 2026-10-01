from loreforge.models.lore import Fact, LoreBible
from loreforge.models.outline import GameFlavor, Outline, Section
from loreforge.models.project import VideoProject
from loreforge.services import script_service as S


def _fixture():
    bible = LoreBible(game="Bloodborne", facts=[
        Fact(id="f0001", text="Byrgenwerth was a college by a lake.", entity_ids=["n1"], category="history", chrono=10),
        Fact(id="f0002", text="Willem led Byrgenwerth.", entity_ids=["n1", "n2"], category="character", chrono=12),
        Fact(id="f0003", text="Laurence founded the Healing Church.", entity_ids=["n3"], category="history", chrono=30),
        Fact(id="f0004", text="Willem warned: fear the old blood.", entity_ids=["n2"], category="character", chrono=14),
        Fact(id="f0005", text="The moon presence may be the true power.", category="theory", status="theory"),
    ])
    outline = Outline(game="Bloodborne", format="history", flavor=GameFlavor(address="Hunter"), sections=[
        Section(id="s01", kind="opening", title="Welcome", target_words=360),
        Section(id="s02", kind="body", title="The College", fact_ids=["f0001", "f0002"], target_words=1000),
        Section(id="s03", kind="body", title="The Church", fact_ids=["f0003"], target_words=1000),
        Section(id="s04", kind="winddown", title="Rest", target_words=600),
    ])
    project = VideoProject(run_id="t", game="Bloodborne", game_slug="bloodborne")
    return project, outline, bible


def test_parse_output_strips_trailer():
    raw = ("First paragraph.\n\n## Stray heading\nSecond paragraph.\n\n"
           "USED_FACTS: f0001, f0002\nSUMMARY: The college and its master.\nIMAGES: lake mist, lamplight")
    prose, meta = S.parse_output(raw)
    assert "USED_FACTS" not in prose and "Stray heading" not in prose
    assert prose.startswith("First paragraph.") and prose.endswith("Second paragraph.")
    assert meta == {"used_facts": ["f0001", "f0002"], "summary": "The college and its master.",
                    "images": ["lake mist", "lamplight"]}


def test_cached_prefix_is_deterministic_and_complete():
    project, outline, bible = _fixture()
    a = S.cached_prefix(project, outline, bible)
    b = S.cached_prefix(project, outline, bible)
    assert a == b
    assert "f0001: Byrgenwerth" in a and "f0003: Laurence" in a
    assert "f0004" not in a                       # unassigned facts stay out of the prefix
    assert "specifically designed" in a           # banned list from the channel
    assert '"Hunter"' in a


def test_section_prompt_has_ledger_and_tail():
    project, outline, bible = _fixture()
    ledger = {"s02": {"used_facts": ["f0001"], "summary": "The college.", "images": ["lake mist"],
                      "openers": ["By the lake"]}}
    p = S.section_prompt(project, outline, bible, outline.sections[2], ledger, "…the lake went still.")
    assert "f0003" in p and "lake mist" in p and "By the lake" in p
    assert "the lake went still" in p and '"Rest"' in p


def test_opening_prompt_uses_ritual_template():
    project, outline, bible = _fixture()
    p = S.section_prompt(project, outline, bible, outline.sections[0], {}, "")
    assert "RITUAL TEMPLATE (opening)" in p and '"The College"' in p
    assert project.channel.name in p          # the ritual greets the listener by channel


def test_extra_facts_prefers_related_and_unassigned():
    project, outline, bible = _fixture()
    extras = S.extra_facts(bible, outline, outline.sections[1], ledger={})
    assert [f.id for f in extras][0] == "f0004"   # shares Willem, same era
    assert all(f.id not in ("f0001", "f0002", "f0003") for f in extras)
