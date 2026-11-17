"""dyn_heat: the legend's overlay is the material's (warm -> hot over the scale, fading in from the bare texture),
clamped at both ends."""
import pytest

import dyn_heat as dh


def _rgb(c):
    return tuple(int(round(255 * v)) for v in c)


def test_the_legend_overlay_is_the_material_overlay():
    assert dh.colour(0.0) == (dh.WARM, 0.0) and dh.colour(-1.0) == (dh.WARM, 0.0)
    assert dh.colour(dh.FADE_IN)[1] == pytest.approx(dh.OPACITY)
    assert dh.colour(dh.FADE_IN / 2)[1] == pytest.approx(dh.OPACITY / 2)
    assert dh.colour(1.0)[1] == dh.colour(3.0)[1] == dh.OPACITY
    assert dh.colour(1.0)[0] == pytest.approx(dh.HOT) and dh.colour(3.0)[0] == pytest.approx(dh.HOT)


def test_no_displacement_shows_the_bare_surface():
    assert dh.over(0.0) == _rgb(dh.LEGEND_UNDER)


def test_paint_mixes_a_luminance_keeping_tint_toward_the_ramp(monkeypatch):
    luma = lambda c: sum(w * v for w, v in zip(dh.LUMA, c))                     # noqa: E731
    under = (0.2, 0.25, 0.3)                                                     # dark enough not to clip
    monkeypatch.setattr(dh, 'PAINT', 0.0)                                        # a pure tint keeps the luminance
    for t in (0.05, 0.5, 1.0):
        assert luma(dh.over(t, under)) == pytest.approx(255 * luma(under), abs=1.0)
    tint = dh.over(1.0, under)
    monkeypatch.setattr(dh, 'PAINT', 1.0)                                        # pure paint is the ramp, blended
    paint = dh.over(1.0, under)
    assert paint == _rgb([u + (h - u) * dh.OPACITY for h, u in zip(dh.HOT, under)])
    monkeypatch.setattr(dh, 'PAINT', 0.5)
    assert all(abs(m - (a + b) / 2) <= 1 for m, a, b in zip(dh.over(1.0, under), tint, paint))
    assert tint[0] > tint[1] > tint[2]                                           # red, as the ramp's top


def test_the_legend_states_the_scale(tmp_path):
    from PIL import Image
    path = dh.legend(str(tmp_path / 'legend.png'))
    img = Image.open(path)
    assert img.size == (520, 70) and img.getpixel((16, 40))[:3] == _rgb(dh.LEGEND_UNDER)


DT = 1 / 60.0


def _settle_rec(speeds_cms, stop_after=5, t_release=1.0):
    """A take after the release: the pawn slides stop_after frames at 300 cm/s, then stands; the cloth moves at the
    given speeds (cm/s) frame by frame."""
    frames, x = [], 0.0
    for i, v in enumerate(speeds_cms):
        x += 300.0 * DT if i < stop_after else 0.0
        frames.append({'t': t_release + i * DT, 'pawns': [{'loc': [x, 0.0, 0.0]}],
                       'heat': {'ok': True, 'cloth_motion_cm': v * DT}})
    return {'events': {'t_release': t_release}, 'frames': frames}


def test_a_rubbery_garment_rebounds_and_settles_late():
    import math
    # |velocity| of a damped 1.5 Hz swing: two peaks per cycle, decaying
    speeds = [60.0 * math.exp(-1.2 * i * DT) * abs(math.sin(2 * math.pi * 1.5 * i * DT)) for i in range(150)]
    got = dh.garment_settle(_settle_rec(speeds))
    assert got['rebounds'] >= 3 and got['peak_cms'] > 40 and got['settle_s'] > 1.0
    assert got['t_stop'] == pytest.approx(1.0 + 5 * DT, abs=1e-3)


def test_a_garment_that_comes_straight_to_rest_settles_without_rebounds():
    speeds = [max(0.0, 40.0 - 4.0 * i) for i in range(60)]
    got = dh.garment_settle(_settle_rec(speeds))
    assert got['settled'] and got['rebounds'] == 0 and got['settle_s'] < 0.15


def test_a_take_that_ends_still_moving_is_not_settled():
    got = dh.garment_settle(_settle_rec([20.0] * 60))
    assert not got['settled']


def test_no_release_or_no_cloth_is_no_measurement():
    assert dh.garment_settle({'events': {}, 'frames': []}) is None
    rec = _settle_rec([0.0] * 10)
    for r in rec['frames']:
        del r['heat']['cloth_motion_cm']
    assert dh.garment_settle(rec) is None
