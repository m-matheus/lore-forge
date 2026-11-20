from PIL import Image

from loreforge.config.channels import Channel
from loreforge.services import brand_service as B


def _channel(tmp_path, **kw) -> Channel:
    ch = Channel(name=kw.pop("name", "Test Channel"), slug="test", **kw)
    ch.root_dir = tmp_path
    return ch


def test_briefs_fall_back_to_the_built_in_ones(tmp_path):
    ch = _channel(tmp_path)
    assert B.banner_prompt(ch) == B.BANNER_PROMPT
    assert '"TC"' in B.avatar_prompt(ch)                     # initials filled in


def test_channel_briefs_are_read_and_filled(tmp_path):
    (tmp_path / "brand").mkdir()
    (tmp_path / "brand/avatar_prompt.txt").write_text(
        "Logo {name} / {initials} on {background} in {accent}\n", encoding="utf-8")
    ch = _channel(tmp_path, name="TheusPlays", thumbnail={"badge_color": "#FFD21F"})
    assert B.avatar_prompt(ch) == "Logo THEUSPLAYS / T on #07080A in #FFD21F"
    assert B.banner_prompt(ch) == B.BANNER_PROMPT             # no banner file: fallback


def test_accent_suffix_splits_the_name_into_two_colours(tmp_path):
    ch = _channel(tmp_path, name="TheusPlays", brand={"accent_suffix": "Plays"},
                  thumbnail={"title_color": "#FFFFFF", "badge_color": "#FFD21F"})
    assert B.name_runs(ch) == [("THEUS", "#FFFFFF"), ("PLAYS", "#FFD21F")]


def test_name_stays_one_colour_without_a_matching_suffix(tmp_path):
    plain = _channel(tmp_path, name="Midnight Realm", thumbnail={"title_color": "#EDE6D6"})
    assert B.name_runs(plain) == [("MIDNIGHT REALM", "#EDE6D6")]
    whole = _channel(tmp_path, name="Plays", brand={"accent_suffix": "Plays"})
    assert len(B.name_runs(whole)) == 1                       # never an empty head
    other = _channel(tmp_path, name="TheusPlays", brand={"accent_suffix": "Games"})
    assert len(B.name_runs(other)) == 1


def test_run_builds_the_video_watermark_and_skips_the_podcast_cover(tmp_path):
    ch = _channel(tmp_path, name="TheusPlays", brand={"accent_suffix": "Plays"})
    brand = tmp_path / "assets/brand"
    brand.mkdir(parents=True)
    for art in ("avatar_art.png", "banner_art.png"):         # present, so no API call
        Image.new("RGB", (1536, 1024), (7, 8, 10)).save(brand / art)

    made = B.run(ch)

    assert "podcast_cover" not in made                       # no spotify_url
    assert Image.open(made["avatar"]).size == (800, 800)
    assert Image.open(made["video_watermark"]).size == (150, 150)
    assert Image.open(made["banner"]).size == (2560, 1440)


def test_banner_keeps_the_art_colours_when_the_night_grade_is_off(tmp_path):
    yellow = (255, 210, 31)
    art = tmp_path / "art.png"
    Image.new("RGB", (1536, 1024), yellow).save(art)

    flat = _channel(tmp_path, brand={"banner_dim": False, "banner_veil": 0})
    B.build_banner(flat, art, tmp_path / "flat.png")
    graded = _channel(tmp_path)
    B.build_banner(graded, art, tmp_path / "graded.png")

    corner = (5, 5)                                            # far from the name
    assert Image.open(tmp_path / "flat.png").getpixel(corner) == yellow
    assert sum(Image.open(tmp_path / "graded.png").getpixel(corner)) < sum(yellow)
