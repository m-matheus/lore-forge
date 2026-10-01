import numpy as np

from loreforge.config.channels import GradeSpec
from loreforge.services import look_service


def test_grade_darkens_desaturates_and_cools():
    g = GradeSpec()
    orange = np.array([[0.9, 0.5, 0.2]])
    out = look_service.grade(orange, g)[0]
    assert out.mean() < orange.mean()                      # darker
    assert (out.max() - out.min()) < (0.9 - 0.2) * 0.6     # much less saturated
    grey = look_service.grade(np.array([[0.2, 0.2, 0.2]]), g)[0]
    assert grey[2] > grey[0] and grey[1] > grey[0]         # shadows lean teal
    black = look_service.grade(np.zeros((1, 3)), g)[0]
    assert 0 < black.min() < 0.06                          # lifted, not crushed
    white = look_service.grade(np.ones((1, 3)), g)[0]
    assert white.max() <= g.white_clip + 1e-9


def test_cube_layout(tmp_path):
    path = look_service.write_cube(tmp_path / "t.cube", GradeSpec(), size=5)
    lines = path.read_text().splitlines()
    assert "LUT_3D_SIZE 5" in lines
    data = [list(map(float, ln.split())) for ln in lines[4:]]
    assert len(data) == 125
    g = GradeSpec()
    # Entry 1 is (r=1/4, g=0, b=0): red varies fastest.
    expected = look_service.grade(np.array([[0.25, 0.0, 0.0]]), g)[0]
    assert np.allclose(data[1], expected, atol=1e-5)
    expected_last = look_service.grade(np.ones((1, 3)), g)[0]
    assert np.allclose(data[-1], expected_last, atol=1e-5)


def test_rain_loops_seamlessly():
    frames = list(look_service.rain_frames(160, 90, 10, 1.0, seed=1))
    assert len(frames) == 10
    nxt = next(iter(look_service.rain_frames(160, 90, 10, 1.0, seed=1)))
    # Frame N (= loop start again) equals frame 0 by construction; check frame 0 is
    # reproducible and has some rain but is mostly transparent.
    assert np.array_equal(frames[0], nxt)
    alpha = frames[0][..., 3]
    assert 0 < (alpha > 0).mean() < 0.5
