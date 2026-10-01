import re

from loreforge.services import lint_service as L


def test_sentences_and_paragraphs():
    text = "The bell rang. Nobody came!\n\nRain fell on Yharnam. It did not stop."
    assert L.sentences(text) == ["The bell rang.", "Nobody came!", "Rain fell on Yharnam.", "It did not stop."]
    assert len(L.paragraphs(text)) == 2


def test_banned():
    pats = [re.compile(r"\bspecifically designed\b", re.I)]
    f = L.check_banned({"s01": "It was specifically designed for hunters. Fine."}, pats)
    assert [x.detail for x in f] == ["specifically designed"]


def test_repeated_ngrams_flag_later_use_only():
    secs = {"s01": "The lamps burned low over the wet stone streets.",
            "s02": "Much later, the lamps burned low over the cathedral."}
    f = L.check_repeated_ngrams(secs)
    assert len(f) == 1 and f[0].section == "s02"


def test_grammar_ngrams_are_ignored():
    secs = {"s01": "He went to the end of the road.", "s02": "She came to the end of the hall."}
    assert L.check_repeated_ngrams(secs) == []


def test_openers():
    text = ("The hunt began. The hunt spread. The hunt ended. Quiet returned.\n\n"
            "The streets waited.")
    kinds = [f.detail for f in L.check_openers({"s01": text})]
    assert any("opens 3 sentences" in d for d in kinds)
    assert any("consecutive paragraphs" in d for d in kinds)


def test_overused_skips_names():  # threshold: 2.5 per 1k words
    body = " ".join(["Gehrman watched the moonlight and the moonlight answered."] * 10)
    f = L.check_overused({"s01": body}, names={"gehrman"})
    details = " ".join(x.detail for x in f)
    assert "moonlight" in details and "gehrman" not in details.lower()


def test_copied():
    ref = ["and so the hunter walked into the fog of the old cathedral ward at night"]
    secs = {"s01": "Then the hunter walked into the fog of the old cathedral ward alone.",
            "s02": "A different sentence entirely about the moon."}
    f = L.check_copied(secs, ref)
    assert [x.section for x in f] == ["s01"]
