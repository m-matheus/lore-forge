from PIL import Image

from loreforge.models.project import VideoProject
from loreforge.services import thumbnail_service as T


def _project(tmp_path, monkeypatch):
    monkeypatch.setattr(VideoProject, "run_dir", property(lambda self: tmp_path))
    monkeypatch.setattr(VideoProject, "add_costs", lambda self, **kw: None)
    return VideoProject(run_id="t", game="Elden Ring", game_slug="elden-ring")


def _fake_images(monkeypatch, calls):
    def generate(prompt, out, reference_images=None, on_log=None):
        calls.append({"prompt": prompt, "refs": reference_images, "out": out.name})
        Image.new("RGB", (1536, 1024), (40, 30, 20)).save(out)
    monkeypatch.setattr(T.openai_images, "generate_image", generate)


class _Comp:
    def cost_deltas(self):
        return {}


DIRECTION = {k: k + " spec" for k in (
    "composition", "camera", "lighting", "palette", "atmosphere", "contrast", "style", "mood")}
DIRECTION.update(title_space="across the top third", subject="Geralt on a ruined bridge at night")


def _fake_claude(monkeypatch, lettering, asked):
    def complete_json(system, user, schema=None, **kw):
        asked.append(schema)
        if schema is T.LETTERING_SCHEMA:
            return dict(lettering), _Comp()
        assert schema is T.REFERENCE_DIRECTION_SCHEMA
        return dict(DIRECTION), _Comp()
    monkeypatch.setattr(T.ai, "complete_json", complete_json)


def test_reference_mood_describes_the_references_instead_of_sending_them(tmp_path, monkeypatch):
    p = _project(tmp_path, monkeypatch)
    calls, asked = [], []
    _fake_images(monkeypatch, calls)
    _fake_claude(monkeypatch, {"boxes": [], "title_style": ""}, asked)
    monkeypatch.setattr(T, "compose", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no compositing")))
    p.thumbnail_refs_dir.mkdir(parents=True)
    Image.new("RGB", (640, 360)).save(p.thumbnail_refs_dir / "ref.png")

    result = T.run(p, {"variants": 2, "reference": "mood"}, lambda m: None)

    assert [c["out"] for c in calls] == ["refthumb_01.png", "refthumb_02.png"]
    assert calls[0]["refs"] is None                    # the references never reach the image tool
    prompt = calls[0]["prompt"]
    assert "video game Elden Ring" in prompt and "SUBJECT: Geralt on a ruined bridge" in prompt
    assert "- Lighting: lighting spec" in prompt and "space across the top third" in prompt
    assert "NO text" in prompt
    assert result["mode"] == "reference" and result["art"] == "refthumb_01.png"
    assert Image.open(p.thumbnail_path).size == (1280, 720)

    # Redo appends new variants with a fresh direction; pick re-finishes with no new images.
    assert T.run(p, {"force": True, "variants": 1, "reference": "mood"}, lambda m: None)["art"] == "refthumb_03.png"
    assert asked.count(T.REFERENCE_DIRECTION_SCHEMA) == 2
    assert T.run(p, {"force": True, "pick": "refthumb_02.png"}, lambda m: None)["art"] == "refthumb_02.png"
    assert len(calls) == 3


def test_without_references_uses_key_art_and_compositing(tmp_path, monkeypatch):
    p = _project(tmp_path, monkeypatch)
    calls, composed = [], []
    _fake_images(monkeypatch, calls)

    class Comp:
        def cost_deltas(self):
            return {}
    direction = {"subject": "castle", "light": "lamp", "mood": "calm", "title_text": "ELDEN RING"}
    monkeypatch.setattr(T.ai, "complete_json", lambda *a, **k: (dict(direction), Comp()))
    def compose(proj, art, title, out):
        composed.append((art.name, title))
        out.write_bytes(b"jpg")
    monkeypatch.setattr(T, "compose", compose)

    result = T.run(p, {"variants": 1}, lambda m: None)

    assert calls[0]["refs"] is None and calls[0]["out"] == "keyart_01.png"
    assert composed == [("keyart_01.png", "ELDEN RING")]
    assert result["mode"] == "keyart"


def test_reference_mode_sends_the_references_first(tmp_path, monkeypatch):
    p = _project(tmp_path, monkeypatch)
    calls, asked = [], []
    _fake_images(monkeypatch, calls)
    _fake_claude(monkeypatch, {"boxes": [], "title_style": ""}, asked)
    p.thumbnail_refs_dir.mkdir(parents=True)
    Image.new("RGB", (640, 360)).save(p.thumbnail_refs_dir / "ref.png")

    result = T.run(p, {"variants": 1}, lambda m: None)

    assert calls[0]["refs"] == [p.thumbnail_refs_dir / "ref.png"]
    assert "mood-board references only" in calls[0]["prompt"] and "NO text" in calls[0]["prompt"]
    assert T.REFERENCE_DIRECTION_SCHEMA not in asked     # no description when the image passes
    assert result["mode"] == "reference" and result["art"] == "refthumb_01.png"


def test_refused_reference_image_falls_back_to_the_description_and_overlays_the_title(
        tmp_path, monkeypatch):
    p = _project(tmp_path, monkeypatch)
    monkeypatch.setattr(VideoProject, "library_dir", property(lambda self: tmp_path / "library"))
    calls = []

    def generate(prompt, out, reference_images=None, on_log=None):
        calls.append({"prompt": prompt, "refs": reference_images})
        if len(calls) == 1:                           # first try: the image tool refuses
            raise RuntimeError("No image returned by the Responses API")
        img = Image.new("RGB", (1536, 1024))          # art is black; the title art gets a
        if prompt.startswith("The title"):            # white word on black
            img.paste((255, 255, 255), (400, 450, 1100, 580))
        img.save(out)
    monkeypatch.setattr(T.openai_images, "generate_image", generate)

    lettering = {"boxes": [{"x0": 0.1, "y0": 0.2, "x1": 0.9, "y1": 0.5}],
                 "title_style": "weathered white gothic serif"}
    _fake_claude(monkeypatch, lettering, [])
    p.thumbnail_refs_dir.mkdir(parents=True)
    Image.new("RGB", (640, 360), (200, 200, 200)).save(p.thumbnail_refs_dir / "ref.png")

    result = T.run(p, {"variants": 1}, lambda m: None)

    assert calls[0]["refs"] == [p.thumbnail_refs_dir / "ref.png"]
    assert "mood-board references only" in calls[0]["prompt"]
    assert calls[1]["refs"] is None and "SUBJECT: Geralt" in calls[1]["prompt"]
    assert calls[2]["prompt"].startswith('The title "Elden Ring"') and "weathered white gothic" in calls[2]["prompt"]
    assert result["art"] == "refthumb_01.png"
    assert (tmp_path / "library" / "title.png").exists()
    # The title is composited inside the reference's box: white pixels at its centre.
    assert Image.open(p.thumbnail_path).getpixel((640, 252))[0] > 200

    # Picking the variant again reuses the cached title: no new image call.
    T.run(p, {"force": True, "pick": "refthumb_01.png"}, lambda m: None)
    assert len(calls) == 3

