from loreforge.services import caption_service as C


def _words(text: str, start: float = 0.0, step: float = 0.4) -> list[dict]:
    out, t = [], start
    for w in text.split():
        out.append({"word": w, "start": round(t, 3), "end": round(t + step * 0.9, 3)})
        t += step
    return out


def test_srt_time_format():
    assert C.seconds_to_srt_time(0) == "00:00:00,000"
    assert C.seconds_to_srt_time(3661.5) == "01:01:01,500"
    assert C.seconds_to_srt_time(1.9999) == "00:00:02,000"


def test_groups_break_on_sentence_end():
    words = _words("The bell rang once. The hunter did not move.")
    cues = C.group_words(words)
    assert [" ".join(w["word"] for w in c) for c in cues] == [
        "The bell rang once.", "The hunter did not move."]


def test_long_sentence_splits_by_length_and_time():
    words = _words(" ".join(["word"] * 60))
    cues = C.group_words(words)
    assert len(cues) > 1
    for c in cues:
        assert len(" ".join(w["word"] for w in c)) <= C.MAX_CHARS_PER_LINE * C.MAX_LINES
        assert c[-1]["end"] - c[0]["start"] <= C.MAX_SECONDS + 0.5


def test_wrap_balances_two_lines():
    lines = C.wrap_lines("the lamps of the old city burned low above the wet and empty street")
    assert len(lines) == 2
    assert all(len(l) <= C.MAX_CHARS_PER_LINE for l in lines)
    assert abs(len(lines[0]) - len(lines[1])) < 20


def test_srt_cues_do_not_overlap_and_have_minimum_length():
    words = _words("Short one. " + " ".join(["filler"] * 20) + " end.")
    srt = C.build_srt(words)
    times = [l for l in srt.splitlines() if "-->" in l]
    assert len(times) >= 2
    ends = [t.split(" --> ")[1] for t in times]
    starts = [t.split(" --> ")[0] for t in times]
    assert all(e <= s for e, s in zip(ends, starts[1:]))


def test_premiere_transcript_shape():
    t = C.build_premiere_transcript(_words("One sentence here. And another one."))
    assert t["language"] == "en-us" and t["speakers"][0]["id"] == "spk_0"
    assert len(t["segments"]) == 2
    assert t["segments"][0]["words"][-1]["eos"] is True


def test_breaks_at_clause_end_when_a_sentence_is_too_long():
    words = _words("Welcome to Channel One, and to a long and quiet night in a city "
                   "that never quite sees the morning light at all.")
    texts = [" ".join(w["word"] for w in c) for c in C.group_words(words)]
    assert texts[0].endswith(",")          # broke after "Channel One," not mid-phrase
    assert texts[1].startswith("and to a long")


def test_wrap_prefers_punctuation():
    lines = C.wrap_lines("Welcome to Channel One, and to a long and quiet night")
    assert lines[0].endswith(",")
