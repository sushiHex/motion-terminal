"""hero_onboard_core: the onboarding decisions, without the editor."""
import json
import math
import os

import pytest

import hero_onboard_core as core

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _roles(rig):
    with open(os.path.join(REPO, 'HeroProfiles', rig + '.json')) as f:
        return json.load(f)['roles']


# The first hero's hand-built target rig (/Game/Spike/IK_Khaimera, read 2026-10-29).
IK_KHAIMERA = {('Spine', 'spine_01', 'spine_03'), ('Neck', 'neck_01', 'neck_02'), ('Head', 'head', 'head'),
               ('LeftClavicle', 'clavicle_l', 'clavicle_l'), ('RightClavicle', 'clavicle_r', 'clavicle_r'),
               ('LeftArm', 'upperarm_l', 'hand_l'), ('RightArm', 'upperarm_r', 'hand_r'),
               ('LeftLeg', 'thigh_l', 'ball_l'), ('RightLeg', 'thigh_r', 'ball_r')}
PARENTS = {'head': 'neck_02', 'neck_02': 'neck_01', 'neck_01': 'spine_03', 'spine_03': 'spine_02',
           'spine_02': 'spine_01', 'spine_01': 'pelvis', 'pelvis': 'root'}
for _s in 'lr':
    PARENTS.update({'clavicle_' + _s: 'spine_03', 'upperarm_' + _s: 'clavicle_' + _s,
                    'lowerarm_' + _s: 'upperarm_' + _s, 'hand_' + _s: 'lowerarm_' + _s, 'thigh_' + _s: 'pelvis',
                    'calf_' + _s: 'thigh_' + _s, 'foot_' + _s: 'calf_' + _s, 'ball_' + _s: 'foot_' + _s})


def test_chains_from_roles_reproduce_the_hand_built_rig():
    for rig in ('ParagonKhaimera', 'ParagonAurora'):
        got = core.retarget_chains(_roles(rig), PARENTS)
        assert set(got) == IK_KHAIMERA
        assert sorted(c for c, _a, _b in got) == sorted(core.CHAINS)


def test_chains_refuse_missing_roles_and_a_head_off_the_neck():
    roles = dict(_roles('ParagonAurora'), hand_l=None)
    with pytest.raises(ValueError, match='hand_l'):
        core.retarget_chains(roles, PARENTS)
    with pytest.raises(ValueError, match='does not hang'):
        core.retarget_chains(_roles('ParagonAurora'), {'head': 'spine_03'})
    no_neck = dict(_roles('ParagonAurora'), neck=None)
    assert 'Neck' not in [c for c, _a, _b in core.retarget_chains(no_neck, {})]
    # a chain whose end does not descend from its start (a role picked from a second body) is refused
    with pytest.raises(ValueError, match='LeftArm'):
        core.retarget_chains(dict(_roles('ParagonAurora'), hand_l='ghost_hand_l'),
                             dict(PARENTS, ghost_hand_l='ghost_lowerarm_l'))
    no_ball = dict(_roles('ParagonAurora'), ball_l=None)
    assert ('LeftLeg', 'thigh_l', 'foot_l') in core.retarget_chains(no_ball, PARENTS)


def test_source_clips_are_derived_once_each_in_order_forward_only():
    cdo = {'Anim_Idle': '/G/MM_Idle.MM_Idle', 'Anim_Jump': '/G/MM_Jump', 'Anim_Fall': None, 'Anim_Land': '/G/MM_Land'}
    bs = [{'animation': '/G/Walk.Walk', 'x': 0, 'y': 300}, {'animation': '/G/Jog', 'x': 0, 'y': 600},
          {'animation': '/G/Walk.Walk', 'x': 0, 'y': 150}, {'animation': '/G/Strafe', 'x': 90, 'y': 300}]
    assert core.source_clips(cdo, bs) == ['/G/MM_Idle', '/G/MM_Jump', '/G/MM_Land', '/G/Walk', '/G/Jog']
    with pytest.raises(ValueError, match='no clips'):
        core.source_clips({}, bs)
    with pytest.raises(ValueError, match='Direction = 0'):
        core.reachable_samples([{'animation': '/G/Strafe', 'x': 90, 'y': 300}])


def test_excursion_catches_a_curve_that_closes_its_endpoints():
    curve = [[0, 0, 0], [100, 50, 0], [0, 100, 0]]
    dist, d = core.travel(curve)
    pinned = core.pin_travel(curve, d)
    assert core.travel(pinned)[0] == pytest.approx(0.0)
    assert core.excursion(pinned) == pytest.approx(100.0)          # still sweeps a metre in place
    assert core.across(curve, d) == pytest.approx(100.0)
    assert core.across(curve, d) > core.EXCURSION_MAX_CM


def test_pin_removes_travel_along_its_direction_only():
    # forward travel on +Y with sway on X and bob on Z: the first hero's case, pinned to Y0 exactly
    pts = [[3 * math.sin(i), 10.0 * i, 2 * math.cos(i)] for i in range(20)]
    dist, d = core.travel(pts)
    assert dist == pytest.approx(math.hypot(pts[-1][0] - pts[0][0], 190.0))
    fwd = core.pin_travel(pts, [0.0, 1.0, 0.0])
    assert all(p[1] == pytest.approx(0.0) for p in fwd)
    assert [p[0] for p in fwd] == [p[0] for p in pts] and [p[2] for p in fwd] == [p[2] for p in pts]
    # a diagonal (strafe-forward) clip: net travel along d becomes zero, the motion across d stays
    diag = [[7.0 * i + math.sin(i), 7.0 * i - math.sin(i), 0.0] for i in range(30)]
    dist, d = core.travel(diag)
    out = core.pin_travel(diag, d)
    assert core.travel(out)[0] == pytest.approx(0.0, abs=1e-9)
    across = [-d[1], d[0], 0.0]
    for a, b in zip(diag, out):
        assert sum((a[k] - diag[0][k]) * across[k] for k in range(3)) == pytest.approx(
            sum((b[k] - out[0][k]) * across[k] for k in range(3)))


def test_one_shots_are_not_locomotion():
    jump = [[0, 0, 0], [0, 18, 50], [0, 9, 60], [0, 2, 0]]       # an 18 cm crouch-and-launch that lands back
    assert core.travel(jump)[0] <= core.TRAVEL_MIN_CM
    assert core.travel([[0, 0, 0], [0, 600, 0]])[0] > core.TRAVEL_MIN_CM
