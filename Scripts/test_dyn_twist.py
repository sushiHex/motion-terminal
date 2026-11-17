"""dyn_analyze.twist_deg / twist on synthetic bone rotations (quaternions xyzw)."""
import math

import dyn_analyze as A


def _q(axis, deg):
    s = math.sin(math.radians(deg) / 2.0)
    n = math.sqrt(sum(a * a for a in axis))
    return [axis[0] / n * s, axis[1] / n * s, axis[2] / n * s, math.cos(math.radians(deg) / 2.0)]


def test_pure_roll_about_the_long_axis_is_the_twist():
    base = _q([0, 0, 1], 30.0)                                    # an animated bone, yawed
    roll = A.af.qmul(base, _q([0, 1, 0], 20.0))                   # +20 deg about its own local Y (the long axis)
    assert abs(A.twist_deg(roll, base, [0, 1, 0]) - 20.0) < 1e-6
    assert abs(A.twist_deg(base, roll, [0, 1, 0]) + 20.0) < 1e-3


def test_a_pure_swing_has_no_twist():
    base = _q([1, 0, 0], 10.0)
    swung = A.af.qmul(base, _q([1, 0, 0], 35.0))                  # bend about local X, the long axis is Y
    assert abs(A.twist_deg(swung, base, [0, 1, 0])) < 1e-6


def test_twist_survives_a_swing_and_the_quaternion_sign():
    base = _q([0, 0, 1], -40.0)
    q = A.af.qmul(base, A.af.qmul(_q([1, 0, 0], 25.0), _q([0, 1, 0], -15.0)))   # swing then roll, local frame
    assert abs(A.twist_deg(q, base, [0, 1, 0]) + 15.0) < 0.5
    assert abs(A.twist_deg([-c for c in q], base, [0, 1, 0]) + 15.0) < 0.5


def _frame(t, on, off):
    pos = {'a': [0.0, 0.0, 0.0], 'b': [0.0, 10.0, 0.0]}           # 'a' -> 'b' along local Y at the identity
    def tm(q):
        return {'a': pos['a'] + q, 'b': pos['b'] + [0.0, 0.0, 0.0, 1.0]}
    return {'t': t, 'pawns': [{'tm': tm(on)}, {'tm': tm(off)}]}


def test_twist_reports_rms_and_peak_per_part_inside_the_window():
    ident = [0.0, 0.0, 0.0, 1.0]
    frames = [_frame(0.0, _q([0, 1, 0], 50.0), ident),            # outside the window
              _frame(1.0, _q([0, 1, 0], 10.0), ident),
              _frame(2.0, _q([0, 1, 0], -10.0), ident)]
    part = {'id': 'strip', 'root': 'a', 'tip': 'b', 'bones': ['a', 'b']}
    out = A.twist(frames, 1, 0.5, 2.5, [part])
    assert out['strip']['bone'] == 'a'
    assert abs(out['strip']['rms_deg'] - 10.0) < 0.01 and abs(out['strip']['peak_deg'] - 10.0) < 0.01
    assert abs(out['strip']['mean_deg']) < 0.01


def test_the_last_bone_is_measured_about_its_tail():
    ident = [0.0, 0.0, 0.0, 1.0]

    def frame(t, roll):
        tm = {'a': [0.0, 0.0, 0.0] + ident, 'b': [0.0, 10.0, 0.0] + _q([0, 1, 0], roll)}
        off = {'a': [0.0, 0.0, 0.0] + ident, 'b': [0.0, 10.0, 0.0] + ident}
        return {'t': t, 'pawns': [{'tm': tm}, {'tm': off}]}
    part = {'id': 'strip', 'root': 'a', 'tip': 'b', 'bones': ['a', 'b']}
    frames = [frame(1.0, 40.0), frame(2.0, 40.0)]
    assert A.twist(frames, 1, 0.5, 2.5, [part]) == {'strip': {'bone': 'a', 'rms_deg': 0.0, 'peak_deg': 0.0,
                                                              'mean_deg': 0.0}}
    out = A.twist(frames, 1, 0.5, 2.5, [part], tails={'b': [0.0, 12.0, 0.0]})
    assert out['strip']['bone'] == 'b' and abs(out['strip']['rms_deg'] - 40.0) < 0.01
