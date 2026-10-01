from loreforge.models.lore import Fact, LoreBible
from loreforge.models.outline import Outline, Section
from loreforge.services.duration_service import plan_expansion


def _fixture():
    facts = [Fact(id=f"f{i:04d}", text=f"fact {i}", entity_ids=["n1"], category="history")
             for i in range(1, 31)]
    bible = LoreBible(game="G", facts=facts)
    outline = Outline(game="G", format="history", sections=[
        Section(id="s01", kind="opening", title="Welcome", target_words=360),
        # s02 uses one fact and shares its entity with 27 unused ones: lots of room.
        Section(id="s02", kind="body", title="A", fact_ids=["f0001"], target_words=1000),
        Section(id="s03", kind="body", title="B", fact_ids=["f0002"], target_words=1000),
        Section(id="s04", kind="winddown", title="Rest", target_words=600)])
    return outline, bible


def test_expansion_picks_sections_with_unused_lore():
    outline, bible = _fixture()
    plan = plan_expansion(outline, bible, ledger={}, deficit_words=700, max_per_section=500)
    assert [sid for sid, _ in plan] == ["s02", "s03"]
    assert [words for _, words in plan] == [500, 200]
    assert sum(w for _, w in plan) == 700


def test_no_plan_when_every_fact_is_already_told():
    outline, bible = _fixture()
    ledger = {"s02": {"used_facts": [f.id for f in bible.facts]}}
    assert plan_expansion(outline, bible, ledger, deficit_words=500) == []
