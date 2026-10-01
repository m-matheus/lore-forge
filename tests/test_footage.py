import numpy as np

from loreforge.services import footage_service as F


def _clip(base: float, *, motion: float = 0.0, frames: int = 20, hud: bool = False,
          spike: float | None = None) -> np.ndarray:
    rng = np.random.default_rng(0)
    out = np.full((frames, 180, 320), base, dtype=np.float32)
    # Camera motion moves the whole frame, not only its middle.
    out += rng.normal(0, motion, out.shape).astype(np.float32)
    if spike is not None:
        out[frames // 2] = np.clip(out[frames // 2] + spike, 0, 1)
    if hud:
        out[:, :14, :90] = 0.85          # a frozen bar in the top-left corner
    return np.clip(out, 0, 1)


def test_keeps_a_dark_slow_scene():
    luma, motion, peak, hud = F.measure(_clip(0.08, motion=0.004))
    keep, reason = F.judge(luma, motion, peak, hud)
    assert keep and reason == ""


def test_rejects_bright_and_black():
    assert not F.judge(*F.measure(_clip(0.75, motion=0.004)))[0]
    assert F.judge(*F.measure(_clip(0.002)))[1].startswith("black screen")


def test_rejects_motion_and_sudden_movement():
    assert F.judge(*F.measure(_clip(0.1, motion=0.12)))[1].startswith("too much motion")
    peaky = F.measure(_clip(0.1, motion=0.004, spike=0.5))
    assert not F.judge(*peaky)[0]


def test_detects_hud_as_frozen_border_pixels():
    luma, motion, peak, hud = F.measure(_clip(0.1, motion=0.02, hud=True))
    assert hud > F.MAX_HUD_SCORE
    assert F.judge(luma, motion, peak, hud)[1].startswith("HUD")
    # The same scene without the bar is kept.
    assert F.judge(*F.measure(_clip(0.1, motion=0.02)))[0]
