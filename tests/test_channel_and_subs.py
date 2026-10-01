from loreforge.config.channels import load_channel
from loreforge.config.settings import get_settings
from loreforge.integrations.youtube_dl import json3_words


def _hits(text: str) -> list[str]:
    ch = load_channel(get_settings().default_channel)
    return [p.pattern for p in ch.banned_patterns() if p.search(text)]


def test_banned_phrases_match_whole_words_and_regexes():
    assert _hits("The cathedral was specifically designed to awe.")
    assert _hits("It is not just a city, but a wound.")
    assert _hits("What a night!")
    assert not _hits("The hunter delivered the final blow.")      # 'delve' is not in 'delivered'
    assert not _hits("A quiet street under the moon.")


def test_json3_words_flattens_and_orders():
    data = {"events": [
        {"tStartMs": 0, "dDurationMs": 2000, "segs": [{"utf8": "the old"}, {"utf8": " blood", "tOffsetMs": 800}]},
        {"tStartMs": 2000, "dDurationMs": 10, "aAppend": 1, "segs": [{"utf8": "\n"}]},
        {"tStartMs": 2100, "dDurationMs": 1500, "segs": [{"utf8": "hunters"}]},
    ]}
    words = json3_words(data)
    assert [w["word"] for w in words] == ["the", "old", "blood", "hunters"]
    assert words[2]["start"] == 0.8
    assert words[2]["end"] == 2.0                          # capped at its event end
