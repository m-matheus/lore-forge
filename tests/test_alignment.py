from loreforge.services import alignment as A


def test_speech_is_complement_of_silences():
    assert A._speech([(0.0, 0.3), (2.0, 2.5), (4.8, float("inf"))], 5.0) == [
        (0.3, 2.0), (2.5, 4.8)]


def test_advance_skips_silences():
    tl = A._Timeline([(0.0, 1.0), (2.0, 3.0)])
    assert tl.advance(0.0, 0.5) == 0.5
    assert tl.advance(0.0, 1.0) == 1.0
    assert tl.advance(0.0, 1.0, as_start=True) == 2.0
    assert tl.advance(0.0, 1.5) == 2.5
    assert tl.between(0.5, 2.5) == 1.0


def test_sentence_ends_snap_to_pauses():
    # Two sentences; the pause between them is at 2.1-2.6s, not where a purely
    # proportional split would put it.
    text = "The bell rang once over the city. Nobody answered."
    words = A.estimate_words(text, [(0.0, 0.2), (2.1, 2.6), (3.9, 4.2)], 4.2)
    assert [w["word"] for w in words] == text.split()
    once = next(w for w in words if w["word"] == "city.")
    nobody = next(w for w in words if w["word"] == "Nobody")
    assert once["end"] == 2.1
    assert nobody["start"] == 2.6
    assert words[0]["start"] == 0.2
    assert words[-1]["end"] == 3.9


def test_timings_are_monotonic_and_inside_audio():
    text = " ".join(["Word after word, slowly."] * 12)
    sil = [(i * 1.7 + 1.2, i * 1.7 + 1.6) for i in range(12)]
    words = A.estimate_words(text, sil, 20.5)
    assert len(words) == len(text.split())
    for a, b in zip(words, words[1:]):
        assert a["start"] <= a["end"] <= b["start"] + 1e-6
    assert words[-1]["end"] <= 20.5


def test_empty_text():
    assert A.estimate_words("", [], 3.0) == []
