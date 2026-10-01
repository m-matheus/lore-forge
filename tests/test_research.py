from loreforge.models.lore import Fact, LoreBible
from loreforge.models.project import VideoProject
from loreforge.services import research_service as R


def test_chunk_overlaps_and_covers_text():
    text = ("The Healing Church rose over Yharnam. " * 3000).strip()
    parts = R.chunk(text, size=10_000, overlap=500)
    assert len(parts) > 5
    assert all(len(p) <= 10_000 for p in parts)
    assert parts[0].endswith(".")
    assert parts[-1].endswith("Yharnam.")


def test_html_to_text_drops_chrome():
    html = "<nav>menu</nav><h1>Ludwig</h1><p>The first hunter &amp; knight.</p><script>x()</script>"
    text = R._html_to_text(html)
    assert "menu" not in text and "x()" not in text
    assert "Ludwig" in text and "hunter & knight" in text


def test_merge_entities_unions_aliases():
    raw = [{"name": "Gehrman", "kind": "character", "aliases": ["The First Hunter"]},
           {"name": "the first hunter", "kind": "character"},
           {"name": "Maria", "kind": "character", "aliases": ["Lady Maria of the Astral Clocktower"]}]
    ents, by_norm = R._merge_entities(raw)
    assert len(ents) == 2
    assert by_norm["gehrman"] == by_norm["the first hunter"]
    assert by_norm["maria"] != by_norm["gehrman"]


def test_coverage_warns_when_thin():
    p = VideoProject(run_id="x", game="Bloodborne", game_slug="bloodborne",
                     format="history", target_minutes=130)
    bible = LoreBible(game="Bloodborne", sources={"src-1": {"kind": "reference_video"}},
                      facts=[Fact(id=f"f{i}", text="t") for i in range(50)])
    rep = R.coverage(bible, p)
    assert rep["needed"] == 260
    assert len(rep["warnings"]) == 2
