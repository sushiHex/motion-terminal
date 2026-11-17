"""hit_reaction_analyze: the verdict passes a healthy matrix and goes red on each reproduced false pass (a
twisted unhit pose, a NaN transform, a return that is only the fade, no secondary reaction), and on the later
ones (an unvalidated aim bone and an absorbed NaN clearance, a pushed neighbour landing for its target, overlaps
from different frames, both arms pooled, the hips outside the reaction, the legs outside fidelity)."""
import json
import math

import pytest

import hit_reaction_analyze as a

UPPER, LEGS = ['spine_01', 'head'], ['thigh_l']
ROLES = {'spine': ['spine_01'], 'neck': None, 'head': 'head', 'thigh_l': 'thigh_l', 'calf_l': 'calf_l'}
REST = {'spine_01': [0.0, 0.0, 100.0], 'head': [0.0, 0.0, 160.0], 'thigh_l': [10.0, 0.0, 90.0],
        'calf_l': [10.0, 0.0, 50.0]}
ID = [0.0, 0.0, 0.0, 1.0]
HIT_F, DT, N = 10, 1.0 / 60, 200
OFFSET_CM, RADIUS_CM = 5.0, 10.0     # an impact's origin behind its bone, and its reach (the head alone)


def _quat_z(deg):
    h = math.radians(deg) / 2
    return [0.0, 0.0, math.sin(h), math.cos(h)]


def _rec(push=lambda t: 0.0, twist=lambda t: 0.0, tip=lambda t: 0.0, schedule=True):
    """Pawn 0 is pushed push(t) cm along x on the head (t: seconds after the hit) and twisted twist(t) deg; pawn 1 is
    the animation. Tips move tip(t) cm on pawn 0."""
    frames = []
    for f in range(N):
        t = (f - HIT_F) * DT
        pawns = []
        for p in (0, 1):
            off = push(t) if (p == 0 and t >= 0) else 0.0
            rot = twist(t) if (p == 0 and t >= 0) else 0.0
            tm = {b: list(v) + ID for b, v in REST.items()}
            tm['head'] = [off, 0.0, 160.0] + _quat_z(rot)
            dt = tip(t) if (p == 0 and t >= 0) else 0.0
            pawns.append({'tm': tm, 'tips': {'hair_01': [dt, 0.0, 150.0], 'hair_02': [dt, 0.0, 150.0]},
                          'mesh_w': [300.0 * p, 0.0, 0.0, 0.0, 0.0, 0.0]})
        frames.append({'f': f, 't': f * DT, 'pawns': pawns})
    rec = {'roles': ROLES, 'frames': frames, 'events': {'hits': []},
           'config': {'reaction_schedule': {'arm_s': 0.05, 'hold_s': 1.0, 'fade_s': 0.3}} if schedule else {}}
    _add_hit(rec, HIT_F)
    return rec


def _hit(f, origin, bone='head', d=(1.0, 0.0, 0.0), radius=RADIUS_CM):
    return {'frame': f, 't': f * DT, 'el': f * DT, 'pawns': [{'pawn': 0, 'origin': origin}],
            'config': {'bone': bone, 'dir': list(d), 'at_s': f * DT, 'pawns': [0], 'radius': radius}}


def _add_hit(rec, f, bone='head', d=(1.0, 0.0, 0.0), **kw):
    """An impact applied on frame f, on schedule (the drive records what it planned in its config), its origin
    OFFSET_CM behind the bone's world position the frame before, against the push."""
    row = rec['frames'][f - 1]['pawns'][0]
    n = math.sqrt(sum(x * x for x in d))
    origin = [m + x - OFFSET_CM * k / n for m, x, k in zip(row['mesh_w'], row['tm'][bone], d)]
    h = _hit(f, origin, bone, d, **kw)
    rec['events']['hits'].append(h)
    rec['config'].setdefault('hits', []).append(dict(h['config']))


def _flinch(t):          # out to 10 cm, springs back to 2 cm by the fade (1 s), gone by 1.3 s
    return 10.0 * math.exp(-3.0 * t) * (1.0 if t < 1.0 else max(0.0, 1.0 - (t - 1.0) / 0.3))


def _verdict(hit, off=None, noimp=None):
    hit = hit or _rec(_flinch, tip=lambda t: 3.0 if t < 1 else 0.0)
    off = off or _rec()
    noimp = noimp or _rec()
    r_hit, r_off, r_no = (a.response(x, UPPER, LEGS) for x in (hit, off, noimp))
    return a.verdict(r_hit, r_off, a.fidelity(hit, off, UPPER), r_no, a.validate(hit, UPPER + LEGS))


def _failed(v):
    return {r['check'] for r in v['rows'] if not r['ok']}


def test_a_healthy_matrix_passes():
    v = _verdict(None)
    assert v['pass'], v['rows']


def test_a_twisted_unhit_pose_fails_fidelity():
    hit = _rec(_flinch, tip=lambda t: 3.0)
    twisted_off = _rec()
    for r in hit['frames']:
        r['pawns'][1]['tm']['head'][3:] = _quat_z(180.0)     # the reaction-on UNHIT head turned around, in place
    assert 'unhit pose held' in _failed(_verdict(hit, twisted_off))


def test_a_nan_transform_fails_the_records():
    hit = _rec(_flinch, tip=lambda t: 3.0)
    for r in hit['frames'][50:]:
        r['pawns'][0]['tm']['head'] = [float('nan')] * 7
    assert 'records valid' in _failed(_verdict(hit))


def test_a_return_that_is_only_the_fade_fails_the_springs():
    held = lambda t: 10.0 * (1.0 if t < 1.0 else max(0.0, 1.0 - (t - 1.0) / 0.3))   # noqa: E731
    assert _failed(_verdict(_rec(held, tip=lambda t: 3.0))) == {'springs restore'}


def test_a_lasting_twist_fails_the_return():
    assert 'returns to the animation' in _failed(_verdict(_rec(_flinch, twist=lambda t: 30.0, tip=lambda t: 3.0)))


def test_secondary_parts_must_react():
    assert 'secondary reacts' in _failed(_verdict(_rec(_flinch)))


def test_a_volley_impact_that_does_not_land_fails_and_the_return_counts_from_the_last():
    hit = _rec(_flinch, tip=lambda t: 3.0 if t < 1 else 0.0)
    # a second impact 0.3 s later, along y on the head: nothing moves that way (the springs are recovering along x)
    _add_hit(hit, HIT_F + 18, d=(0.0, 1.0, 0.0))
    r = a.response(hit, UPPER, LEGS)
    assert [x['jump_cm'] for x in r['impacts']][1] < a.IMPACT_MIN_CM <= r['impacts'][0]['jump_cm']
    assert 'every impact lands' in _failed(_verdict(hit))
    # the recovery from the first impact (-x) is not an impact along -x: no second impulse, no landing
    # (this false pass was reproduced with a 20 cm flinch)
    back = _rec(lambda t: 20.0 * math.exp(-3.0 * t), tip=lambda t: 3.0)
    _add_hit(back, HIT_F + 18, d=(-1.0, 0.0, 0.0))
    assert a.response(back, UPPER, LEGS)['impacts'][1]['jump_cm'] < a.IMPACT_MIN_CM


def test_a_second_impulse_on_top_of_a_recovery_lands():
    second = HIT_F + 24
    push = lambda t: 10.0 * math.exp(-3.0 * t) + (8.0 * (1 - math.exp(-20.0 * (t - 0.4))) if t >= 0.4 else 0.0)  # noqa
    hit = _rec(push, tip=lambda t: 3.0)
    _add_hit(hit, second)
    assert a.response(hit, UPPER, LEGS)['impacts'][1]['jump_cm'] >= a.IMPACT_MIN_CM


def test_the_return_and_the_fade_count_from_the_last_impact():
    late = HIT_F + 30                                   # 0.5 s after the first
    lasting = lambda t: _flinch(t - 0.5) if t >= 0.5 else 10.0   # noqa: E731  held until the last impact, then a flinch
    hit = _rec(lasting, tip=lambda t: 3.0)
    _add_hit(hit, late)
    r = a.response(hit, UPPER, LEGS)
    assert r['at_fade_cm'] is not None and r['at_fade_cm'] < 3.0       # the fade is 1 s after the LAST impact
    assert r['return_s'] is not None and r['return_s'] <= a.RETURN_MAX_S


def test_a_departure_after_the_return_fails_it():
    leaves = lambda t: _flinch(t) if t < 2.0 else 5.0   # noqa: E731  back, then 5 cm off again to the end
    assert 'returns to the animation' in _failed(_verdict(_rec(leaves, tip=lambda t: 3.0)))


def test_a_late_or_missing_impact_fails_delivery():
    hit = _rec(_flinch, tip=lambda t: 3.0 if t < 1 else 0.0)
    hit['config']['hits'].append({'bone': 'head', 'dir': [1.0, 0.0, 0.0], 'at_s': 1.0})    # planned, never applied
    assert 'volley delivered on schedule' in _failed(_verdict(hit))
    late = _rec(_flinch, tip=lambda t: 3.0 if t < 1 else 0.0)
    late['events']['hits'][0]['el'] += 0.2
    assert 'volley delivered on schedule' in _failed(_verdict(late))
    other = _rec(_flinch, tip=lambda t: 3.0 if t < 1 else 0.0)
    other['events']['hits'][0]['config']['bone'] = 'spine_01'       # the right time, the wrong impact
    assert 'volley delivered on schedule' in _failed(_verdict(other))


def test_a_recovery_curve_is_not_an_impact_and_a_real_one_on_it_is():
    # constant-velocity extrapolation passed the first and missed the second
    curve = lambda t: 110.0 * math.exp(-3.0 * t)   # noqa: E731
    nohit = _rec(curve, tip=lambda t: 3.0)
    _add_hit(nohit, HIT_F + 24)
    assert a.response(nohit, UPPER, LEGS)['impacts'][1]['jump_cm'] < a.IMPACT_MIN_CM
    real = _rec(lambda t: curve(t) - (3.0 * min(1.0, (t - 0.4) / 0.1) if t >= 0.4 else 0.0), tip=lambda t: 3.0)
    _add_hit(real, HIT_F + 24, d=(-1.0, 0.0, 0.0))
    assert a.response(real, UPPER, LEGS)['impacts'][1]['jump_cm'] >= a.IMPACT_MIN_CM


def test_a_truncated_record_is_refused():
    hit, off = _rec(_flinch, tip=lambda t: 3.0), _rec()
    off['frames'] = off['frames'][:1]
    with pytest.raises(ValueError, match='lengths'):
        a.fidelity(hit, off, UPPER)


def test_segment_distance_and_body_clearance():
    assert a.seg_distance([0, 0, 0], [10, 0, 0], [5, 3, 0], [5, 3, 9]) == 3.0           # crossing, offset 3
    assert a.seg_distance([0, 0, 0], [1, 0, 0], [4, 0, 0], [4, 0, 0]) == 3.0            # a point past the end
    bodies = [{'bone': 's', 'elements': [{'type': 'capsule', 'center': [0, 0, 0], 'rotation_xyzw': ID,
                                          'radius_cm': 5.0, 'length_cm': 20.0}]},
              {'bone': 'h', 'elements': [{'type': 'sphere', 'aim': None, 'radius_cm': 2.0}]}]
    tms = {'s': [0.0, 0.0, 0.0] + ID, 'h': [6.0, 0.0, 9.0] + ID}                         # 1 cm inside the side
    assert math.isclose(a.clearance(bodies, tms, 'h', 's'), -1.0)
    tms['h'] = [0.0, 0.0, 20.0] + ID                                                     # 3 cm above the top cap
    assert math.isclose(a.clearance(bodies, tms, 'h', 's'), 3.0)


def test_articulation_reads_each_joint_relative_to_its_parent():
    rec = _rec(twist=lambda t: 20.0)              # the head turns 20 deg on pawn 0; spine_01 does not
    rec['config']['reaction_bodies'] = [{'bone': 'spine_01', 'role': 'spine'}, {'bone': 'head', 'role': 'head'}]
    rec['config']['reaction_constraints'] = [{'child': 'head', 'parent': 'spine_01'}]
    art = a.articulation(rec)
    assert math.isclose(art['joint_peak_deg']['head'], 20.0, abs_tol=0.01)
    for r in rec['frames']:                       # turn both on pawn 0: the joint itself did not move
        r['pawns'][0]['tm']['spine_01'][3:] = r['pawns'][0]['tm']['head'][3:]
    assert a.articulation(rec)['joint_peak_deg']['head'] < 0.01


def _art(clav, shoulder, elbow, wrist, hips):
    side = {'clavicle': clav, 'shoulder': shoulder, 'elbow': elbow, 'wrist': wrist}
    return {'sides': {'l': dict(side), 'r': dict(side)}, 'hips_deg': hips}


def _rows(v):
    return {r['check']: r['ok'] for r in v['rows']}


def test_the_arms_must_articulate_below_the_clavicle_and_the_hips_turn():
    hit = _rec(_flinch, tip=lambda t: 3.0 if t < 1 else 0.0)
    off = _rec()
    args = (a.response(hit, UPPER, LEGS), a.response(off, UPPER, LEGS), a.fidelity(hit, off, UPPER), None, ())
    pen = {'hit': {'x|y': 0.0}, 'unhit': {'x|y': 0.0}, 'excess': {'x|y': 0.0}}
    assert _rows(a.verdict(*args, _art(20, 42, 25, 17, 12), pen))['arms articulate']
    # the merged v4 volley: the clavicle took it, the elbow and wrist did not move, the hips were kinematic
    v4 = _rows(a.verdict(*args, _art(33, 8, 0.6, 0.25, 0.19), pen))
    assert not v4['arms articulate'] and not v4['hips move']
    assert not _rows(a.verdict(*args, _art(50, 42, 25, 17, 12), pen))['arms articulate']    # clavicle dominates


def test_an_arm_through_the_body_fails_against_its_own_unhit_overlap():
    hit = _rec(_flinch, tip=lambda t: 3.0 if t < 1 else 0.0)
    off = _rec()
    args = (a.response(hit, UPPER, LEGS), a.response(off, UPPER, LEGS), a.fidelity(hit, off, UPPER), None, ())
    art = _art(20, 42, 25, 17, 12)
    # the shoulder overlaps the chest by design (11 cm both): that cancels; a forearm 7.2 cm into the hip does not
    ok = {'hit': {'upperarm_l|spine_03': 11.0, 'lowerarm_l|pelvis': 0.4}, 'unhit': {'upperarm_l|spine_03': 11.0,
                                                                                   'lowerarm_l|pelvis': -0.5},
          'excess': {'upperarm_l|spine_03': 0.0, 'lowerarm_l|pelvis': 0.4}}
    assert _rows(a.verdict(*args, art, ok))['arms stay out of the body']
    bad = dict(ok, excess=dict(ok['excess'], **{'lowerarm_l|pelvis': 7.2}))
    assert not _rows(a.verdict(*args, art, bad))['arms stay out of the body']
    assert not _rows(a.verdict(*args, art, {'hit': {}, 'unhit': {}, 'excess': {}}))['arms stay out of the body']


def test_a_focused_impact_is_measured_at_the_struck_body_centre():
    # the head's bone origin stays put while its body (a sphere 10 cm out along x) swings 3 cm along y about it
    rec = _rec()
    body = {'bone': 'head', 'elements': [{'type': 'sphere', 'center': [10.0, 0.0, 0.0], 'radius_cm': 5.0}]}
    for r in rec['frames']:
        t = (r['f'] - HIT_F) * DT
        ang = math.atan2(3.0, 10.0) if t >= 0 else 0.0
        r['pawns'][0]['tm']['head'][3:] = _quat_z(math.degrees(ang))
    h = dict(rec['events']['hits'][0], config=dict(rec['events']['hits'][0]['config'], dir=[0.0, 1.0, 0.0]))
    assert a.impact(rec['frames'], h)['jump_cm'] < 0.1                                  # at the bone origin
    assert a.impact(rec['frames'], h, (), [body])['jump_cm'] > 2.5                       # at the body's centre


def test_measured_bones_come_from_the_recorded_reaction_body():
    rec = _rec()
    rec['config']['reaction_bodies'] = [{'bone': 'spine_01'}, {'bone': 'clavicle_helper_l'}]
    assert a.bones_of(rec)[0] == ['spine_01', 'clavicle_helper_l']


def _body(bone, role, physics_type='simulated', radius_cm=1.0):
    """A reaction body of one explicit sphere on its bone's origin."""
    return {'bone': bone, 'role': role, 'physics_type': physics_type,
            'elements': [{'type': 'sphere', 'center': [0.0, 0.0, 0.0], 'radius_cm': radius_cm}]}


NO_PEN = {'hit': {}, 'unhit': {}, 'excess': {'x|y': 0.0}}


def _gate_rows(art=None, pen=None, **kw):
    """The verdict's rows on the healthy matrix with these articulation / penetration measures."""
    hit, off = _rec(_flinch, tip=lambda t: 3.0 if t < 1 else 0.0), _rec()
    return _rows(a.verdict(a.response(hit, UPPER, LEGS), a.response(off, UPPER, LEGS), a.fidelity(hit, off, UPPER),
                           None, (), art, pen, **kw))


def _main(tmp_path, *recs):
    paths = []
    for i, rec in enumerate(recs):
        p = tmp_path / ('rec%d.json' % i)
        p.write_text(json.dumps(rec))
        paths.append(str(p))
    return a.main(paths)


def test_under_leg_ik_the_feet_are_held_and_the_knees_judged():
    legs = ['thigh_l', 'thigh_r', 'calf_l', 'calf_r', 'foot_l', 'foot_r']
    ik = [{'thigh': 'thigh_%s' % s, 'calf': 'calf_%s' % s, 'foot': 'foot_%s' % s, 'target': 'ik_foot_%s' % s}
          for s in 'lr']
    assert a.planted({'config': {'leg_ik': ik}}, legs) == ['foot_l', 'foot_r']
    assert a.planted({'config': {}}, legs) == legs                       # anchored: the whole leg
    feet = {'peak_cm': 10.0, 'legs_cm': 0.1, 'legs_deg': 0.2, 'held': 'feet'}
    names = [r['check'] for r in a.verdict(feet, {}, {'max_cm': 0, 'max_deg': 0})['rows']]
    assert 'feet planted' in names and 'legs untouched' not in names
    knee = lambda bend, side, cm=0.0: {'bend_deg': bend, 'side': side, 'sideless_frames': 0, 'stretch_cm': cm}  # noqa
    art = {'sides': {}, 'hips_deg': 5.0, 'knees': {'calf_l': knee(8.0, 0.2), 'calf_r': knee(70.0, 0.2)}}
    checks = lambda: {r['check']: r['ok'] for r in a.verdict(            # noqa: E731
        feet, {}, {'max_cm': 0, 'max_deg': 0}, art=art)['rows']}
    ok = lambda: checks()['knees follow the hips']                        # noqa: E731
    assert ok() is False                                                  # 70 deg off the animation: the legs fold
    art['knees']['calf_r'] = knee(40.0, 0.2)
    assert ok() and checks()['the hit squats']                            # a squat: what the hit is meant to show
    art['knees']['calf_r'] = knee(40.0, 1.6)
    assert ok() is False                                                  # on the other side of the leg
    art['knees']['calf_r'] = knee(9.0, 0.2)
    assert ok() and checks()['the hit squats'] is False                   # a flinch, not a squat


def test_a_knee_turned_to_the_other_side_of_the_leg_fails_though_its_bend_is_kept():
    # the whole leg turned 180 deg about the hip-foot line keeps the calf-thigh bend
    ik = [{'thigh': 'thigh_l', 'calf': 'calf_l', 'foot': 'foot_l', 'target': 'ik_foot_l'}]
    rec = _rec()
    rec['config']['leg_ik'] = ik
    for r in rec['frames']:
        for p, pawn in enumerate(r['pawns']):
            flip = p == 0 and r['f'] > HIT_F
            pawn['tm']['thigh_l'] = [0.0, 10.0, 90.0] + ID
            pawn['tm']['calf_l'] = [-8.0 if flip else 8.0, 10.0, 50.0] + ID      # the knee forward, or behind
            pawn['tm']['foot_l'] = [0.0, 10.0, 10.0] + ID
    knee = a.articulation(rec)['knees']['calf_l']
    assert knee['bend_deg'] < 1.0 and abs(knee['side'] - 2.0) < 1e-6       # the bend kept, the knee across the leg
    rows = {x['check']: x['ok'] for x in a.verdict({'held': 'feet'}, {}, {'max_cm': 0, 'max_deg': 0},
                                                    art=a.articulation(rec))['rows']}
    assert rows['knees follow the hips'] is False


def test_a_knee_straightened_onto_its_line_has_no_side_to_judge():
    # a 0.66 cm pole on a straight leg read as a 116 deg flip; nearly on the line, it points nowhere
    ik = [{'thigh': 'thigh_l', 'calf': 'calf_l', 'foot': 'foot_l', 'target': 'ik_foot_l'}]
    rec = _rec()
    rec['config']['leg_ik'] = ik
    for r in rec['frames']:
        for p, pawn in enumerate(r['pawns']):
            pawn['tm']['thigh_l'] = [0.0, 10.0, 90.0] + ID
            pawn['tm']['calf_l'] = [0.3 if p == 0 else -0.3, 10.0, 50.0] + ID
            pawn['tm']['foot_l'] = [0.0, 10.0, 10.0] + ID
    knee = a.articulation(rec)['knees']['calf_l']
    assert knee['side'] is None and knee['sideless_frames'] > 0 and knee['stretch_cm'] < 1e-6


def test_a_straight_leg_shortened_instead_of_bent_fails():
    # no bend direction, so LegIK shortens both bones (50/50 -> 45/45 cm): no bend, no side
    ik = [{'thigh': 'thigh_l', 'calf': 'calf_l', 'foot': 'foot_l', 'target': 'ik_foot_l'}]
    rec = _rec()
    rec['config']['leg_ik'] = ik
    for r in rec['frames']:
        for p, pawn in enumerate(r['pawns']):
            low = p == 0 and r['f'] > HIT_F
            pawn['tm']['thigh_l'] = [0.0, 10.0, 90.0 if low else 100.0] + ID
            pawn['tm']['calf_l'] = [0.0, 10.0, 45.0 if low else 50.0] + ID
            pawn['tm']['foot_l'] = [0.0, 10.0, 0.0] + ID
    art = a.articulation(rec)
    assert abs(art['knees']['calf_l']['stretch_cm'] - 5.0) < 1e-6 and art['knees']['calf_l']['side'] is None
    rows = {x['check']: x['ok'] for x in a.verdict({'held': 'feet'}, {}, {'max_cm': 0, 'max_deg': 0}, art=art)['rows']}
    assert rows['knees follow the hips'] is False


def test_a_take_is_judged_in_place_of_the_matrix_hit_run(tmp_path):
    # the page's figures are the shown take's; the controls stay the matrix's
    matrix_hit, take = _rec(push=lambda t: 10.0 * math.exp(-3 * t)), _rec(push=lambda t: 30.0 * math.exp(-3 * t))
    paths = []
    for i, rec in enumerate((matrix_hit, _rec(), _rec(), take)):
        p = tmp_path / ('r%d.json' % i)
        p.write_text(json.dumps(rec))
        paths.append(str(p))
    plain, shown = a.main(paths[:3]), a.main(paths[:3], take=paths[3])
    assert shown['hit']['peak_cm'] == pytest.approx(3 * plain['hit']['peak_cm'], rel=0.05)
    assert shown['fidelity'] == plain['fidelity'] and shown['reaction_off'] == plain['reaction_off']


def test_every_bone_the_geometry_reads_is_validated():
    # an older record's aimed capsule reads its aim bone (a hand's finger root); absent, it became a sphere on the
    # joint and every clearance carried on without it
    rec = _rec(_flinch, tip=lambda t: 3.0)
    rec['config']['reaction_bodies'] = [_body('spine_01', 'spine'), {
        'bone': 'head', 'role': 'head', 'elements': [{'type': 'capsule', 'aim': 'head_end', 'radius_cm': 3.0}]}]
    assert any('head_end missing' in p for p in a.validate(rec, UPPER + LEGS))
    for r in rec['frames']:
        for p in r['pawns']:
            p['tm']['head_end'] = [0.0, 0.0, 175.0] + ID
    assert a.validate(rec, UPPER + LEGS) == []
    rec['frames'][60]['pawns'][1]['tm']['head_end'][0] = float('nan')
    assert any('head_end not finite' in p for p in a.validate(rec, UPPER + LEGS))


def test_a_non_finite_clearance_is_refused():
    # min() kept the last finite overlap and dropped a NaN one
    rec = _rec()
    rec['config']['reaction_bodies'] = [_body('spine_01', 'spine'), _body('head', 'hand_l')]   # the head plays a hand
    assert a.penetration(rec)['hit']['head|spine_01'] < 0.0
    rec['frames'][60]['pawns'][0]['tm']['head'][:3] = [float('nan')] * 3
    with pytest.raises(ValueError, match='not finite'):
        a.penetration(rec)
    # and an older record's aimed capsule whose aim bone was not recorded is not posed as a sphere on the joint
    rec = _rec()
    rec['config']['reaction_bodies'] = [_body('spine_01', 'spine'), {
        'bone': 'head', 'role': 'hand_l', 'elements': [{'type': 'capsule', 'aim': 'finger_l', 'radius_cm': 1.0}]}]
    with pytest.raises(ValueError, match='finger_l'):
        a.penetration(rec)


def _retarget(rec, bone, radius):
    """rec with its one impact on bone instead, reaching radius cm."""
    rec['events']['hits'], rec['config']['hits'] = [], []
    _add_hit(rec, HIT_F, bone=bone, radius=radius)
    return rec


def test_an_impact_lands_on_its_target_alone():
    healthy = a.response(_rec(_flinch), UPPER, LEGS)['impacts'][0]
    assert healthy['recipients'] == ['head'] and healthy['jump_cm'] >= a.IMPACT_MIN_CM
    # the strongest response over the impact's region passed a pushed head while the struck spine never moved
    near = _retarget(_rec(_flinch, tip=lambda t: 3.0 if t < 1 else 0.0), 'spine_01', 70.0)
    (x,) = a.response(near, UPPER, LEGS)['impacts']
    assert x['jump_cm'] < a.IMPACT_MIN_CM and x['recipients'] == ['head', 'spine_01']
    assert 'every impact lands' in _failed(_verdict(near))
    # the struck head moved, but the reach took in the spine too: not the struck body alone
    wide = _retarget(_rec(_flinch, tip=lambda t: 3.0 if t < 1 else 0.0), 'head', 70.0)
    (x,) = a.response(wide, UPPER, LEGS)['impacts']
    assert x['jump_cm'] >= a.IMPACT_MIN_CM and x['recipients'] == ['head', 'spine_01']
    assert 'every impact lands' in _failed(_verdict(wide))
    # a kinematic body takes no impulse (AnimNode_RigidBodyWithControl.cpp:1336, inverse mass 0)
    bodies = [_body('spine_01', 'spine', 'kinematic'), _body('head', 'head')]
    assert a.impact(wide['frames'], wide['events']['hits'][0], UPPER, bodies)['recipients'] == ['head']


def test_penetration_compares_hit_and_unhit_overlaps_at_the_same_frame():
    # the hit pawn's worst frame less the unhit pawn's worst frame, from different times, cancelled a 5 cm hit
    # overlap against an unhit one earlier in the record
    rec = _rec()
    rec['config']['reaction_bodies'] = [_body('spine_01', 'spine', radius_cm=5.0), _body('head', 'hand_l')]
    for r in rec['frames']:
        for p, pawn in enumerate(r['pawns']):
            # apart through the idle stand; after the hit the unhit pawn overlaps first, then the hit one
            inside = r['f'] > HIT_F and (p == 1) == (r['f'] < 100)
            pawn['tm']['head'][:3] = [0.0, 0.0, 101.0 if inside else 120.0]
    pen = a.penetration(rec)
    assert pen['hit']['head|spine_01'] == pytest.approx(5.0) and pen['unhit']['head|spine_01'] == pytest.approx(5.0)
    assert pen['excess']['head|spine_01'] == pytest.approx(5.0) and pen['anatomy'] == []
    assert not _gate_rows(_art(20, 42, 25, 17, 12), pen)['arms stay out of the body']


STAND_HIT_F = 40        # a hit late enough that a settled stand (STAND_S) precedes it; HIT_F leaves 0.17 s


def _stand_rec():
    rec = _rec()
    rec['events']['hits'] = []
    _add_hit(rec, STAND_HIT_F)
    rec['config']['reaction_bodies'] = [_body('spine_01', 'spine', radius_cm=5.0), _body('head', 'hand_l')]
    return rec


def test_a_pair_that_overlaps_in_the_idle_is_anatomy_and_not_measured():
    # an upper arm already inside the chest capsule at rest, pushed deeper by the hit: not an arm through the body
    rec = _stand_rec()
    for r in rec['frames']:
        for p, pawn in enumerate(r['pawns']):
            pawn['tm']['head'][:3] = [0.0, 0.0, 95.0 if p == 0 and r['f'] > STAND_HIT_F else 101.0]
    pen = a.penetration(rec)
    assert pen['anatomy'] == ['head|spine_01'] and pen['excess'] == {}
    assert pen['hit']['head|spine_01'] > 0.0 and pen['unhit']['head|spine_01'] > 0.0   # still reported


def test_a_stand_with_a_gap_exempts_nothing():
    # ten frames over part of the half second passed as a settled stand
    rec = _stand_rec()
    for r in rec['frames']:
        for pawn in r['pawns']:
            pawn['tm']['head'][:3] = [0.0, 0.0, 101.0]          # overlapping throughout, as anatomy would
    assert a.penetration(rec)['anatomy'] == ['head|spine_01']
    rec['frames'] = [r for r in rec['frames'] if r['f'] != STAND_HIT_F - 4]  # one frame missing from the stand
    assert a.penetration(rec)['anatomy'] == []


def test_a_hairline_touch_in_the_stand_exempts_nothing():
    # one 0.01 cm touch before the hit exempted a pair that then went 10 cm in
    rec = _rec()
    rec['config']['reaction_bodies'] = [_body('spine_01', 'spine', radius_cm=5.0), _body('head', 'hand_l')]
    for r in rec['frames']:
        for p, pawn in enumerate(r['pawns']):
            z = 120.0
            if p == 1 and r['f'] == HIT_F - 3:
                z = 105.99                                   # a 0.01 cm overlap, once, in the stand
            if p == 0 and r['f'] > HIT_F:
                z = 101.0                                    # 5 cm in after the hit
            pawn['tm']['head'][:3] = [0.0, 0.0, z]
    pen = a.penetration(rec)
    assert pen['anatomy'] == [] and pen['excess']['head|spine_01'] > a.PENETRATION_TOL_CM


ARM = ('clavicle', 'upperarm', 'twist', 'lowerarm', 'hand')   # twist: a helper between the shoulder and the elbow


def _arm_rec(joint=lambda s, j, t: 0.0, hips=lambda t: 0.0, push=_flinch):
    """_rec with a pelvis and two arms off spine_01, every bone a body jointed to the one above it: on pawn 0, after the
    hit (t s), each arm bone j of side s turns joint(s, j, t) deg about z relative to the one above it (the twist
    helper's turn is the elbow's), and the pelvis hips(t) deg."""
    rec = _rec(push, tip=lambda t: 3.0 if t < 1 else 0.0)
    roles = dict(ROLES, pelvis='pelvis')
    bodies = [_body('pelvis', 'pelvis'), _body('spine_01', 'spine'), _body('head', 'head')]
    cons = [('spine_01', 'pelvis'), ('head', 'spine_01')]
    for s in 'lr':
        up = 'spine_01'
        for j in ARM:
            b, role = '%s_%s' % (j, s), '%s_%s' % ('lowerarm' if j == 'twist' else j, s)
            if j != 'twist':
                roles[role] = b
            bodies.append(_body(b, role))
            cons.append((b, up))
            up = b
    rec['roles'] = roles
    rec['config'].update(reaction_bodies=bodies, reaction_constraints=[{'child': c, 'parent': p} for c, p in cons])
    for r in rec['frames']:
        t = (r['f'] - HIT_F) * DT
        for p, pawn in enumerate(r['pawns']):
            on = p == 0 and t >= 0
            pawn['tm']['pelvis'] = [0.0, 0.0, 95.0] + _quat_z(hips(t) if on else 0.0)
            for s, y in (('l', 1.0), ('r', -1.0)):
                acc = 0.0
                for i, j in enumerate(ARM):
                    acc += joint(s, j, t) if on else 0.0
                    pawn['tm']['%s_%s' % (j, s)] = [0.0, y * 12.0 * (i + 1), 145.0] + _quat_z(acc)
    return rec


def test_each_arm_articulates_on_its_own_side():
    # the joints pooled over both arms passed a right arm whose elbow and wrist never moved behind a left one that
    # did
    def stiff_right(s, j, t):
        return 10.0 if j == 'clavicle' else 0.0 if (s == 'r' and j in ('twist', 'lowerarm', 'hand')) else 20.0
    art = a.articulation(_arm_rec(stiff_right))
    assert art['sides']['l'] == pytest.approx({'clavicle': 10.0, 'shoulder': 20.0, 'elbow': 40.0, 'wrist': 20.0},
                                              abs=0.01)
    assert art['sides']['r']['elbow'] < 0.01 and art['sides']['r']['wrist'] < 0.01
    # one chest impact reaches one arm more than the other: some arm through every joint suffices
    assert _gate_rows(art, NO_PEN)['arms articulate']
    stiff = lambda s, j, t: 10.0 if j == 'clavicle' else 0.0 if j in ('twist', 'lowerarm', 'hand') else 20.0  # noqa
    assert not _gate_rows(a.articulation(_arm_rec(stiff)), NO_PEN)['arms articulate']     # neither arm does
    # a helper is spanned, not counted alone: 3 deg at the twist and 3 at the elbow is a 6 deg elbow
    split = lambda s, j, t: {'clavicle': 10.0, 'twist': 3.0, 'lowerarm': 3.0}.get(j, 20.0)   # noqa: E731
    art = a.articulation(_arm_rec(split))
    assert art['sides']['r']['elbow'] == pytest.approx(6.0, abs=0.01)
    assert _gate_rows(art, NO_PEN)['arms articulate']
    # each clavicle against its own arm: the left took 30 deg against its arm's 20 (the right arm's 40 hid it)
    lop = lambda s, j, t: (30.0 if s == 'l' else 10.0) if j == 'clavicle' else (   # noqa: E731
        0.0 if j == 'twist' else 20.0 if s == 'l' else 40.0)
    assert not _gate_rows(a.articulation(_arm_rec(lop)), NO_PEN)['arms articulate']


def test_the_hips_turn_within_the_reaction_and_not_without_an_impulse(tmp_path):
    # the pelvis read over the whole record passed a turn after the reaction had faded, and nothing showed the turn
    # was the hit's
    end = 1.0 + 0.3                       # the reaction window from the (only, last) impact: hold_s + fade_s
    late = a.articulation(_arm_rec(hips=lambda t: 10.0 if t > end + 0.1 else 0.0))
    assert late['hips_deg'] < 0.01 and not _gate_rows(late, NO_PEN)['hips move']
    turned = a.articulation(_arm_rec(hips=lambda t: 10.0 if t < end else 0.0))
    assert turned['hips_deg'] == pytest.approx(10.0, abs=0.01)
    assert not _gate_rows(turned, NO_PEN, no_impulse_art=turned)['no impulse: hips stay still']
    assert _gate_rows(turned, NO_PEN, no_impulse_art=a.articulation(_arm_rec()))['no impulse: hips stay still']
    # main() measures the no-impulse record over its own window (its zero-strength volley on the same schedule)
    still = lambda t: 0.0   # noqa: E731
    out = _main(tmp_path, _arm_rec(hips=lambda t: 10.0 if t < end else 0.0), _arm_rec(push=still),
                _arm_rec(push=still, hips=lambda t: 5.0))
    rows = _rows(out['verdict'])
    assert rows['hips move'] and not rows['no impulse: hips stay still']
    # each PIE session's clock starts at its own first tick (pie_dynamics_drive._tick t0, before the fixed step):
    # a no-impulse run 0.3 s later in game time, read over the hit record's absolute end, lost the last 0.3 s of its
    # own reaction and passed a 5 deg turn there
    late_clock = _arm_rec(push=still, hips=lambda t: 5.0 if end - 0.25 < t < end else 0.0)
    for r in late_clock['frames']:
        r['t'] += 0.3
    for h in late_clock['events']['hits']:
        h['t'] += 0.3
    out = _main(tmp_path, _arm_rec(hips=lambda t: 10.0 if t < end else 0.0), _arm_rec(push=still), late_clock)
    assert out['no_impulse_articulation']['hips_deg'] == pytest.approx(5.0, abs=0.01)
    assert not _rows(out['verdict'])['no impulse: hips stay still']


def test_a_shared_lower_leg_corruption_fails_the_unhit_pose(tmp_path):
    # both reaction-on pawns' calves 5 cm off the animation ("legs untouched" compares the two, so it held) passed
    # "unhit pose held", which read the body bones alone
    hit = _rec(_flinch, tip=lambda t: 3.0 if t < 1 else 0.0)
    for r in hit['frames']:
        for p in r['pawns']:
            p['tm']['calf_l'][0] += 5.0
    out = _main(tmp_path, hit, _rec(), _rec())
    assert 'unhit pose held' in _failed(out['verdict']) and out['fidelity']['worst_bone'] == 'calf_l'


def test_identical_rounded_rotations_read_zero_degrees_apart():
    q = [round(v, 6) for v in (0.1234567, -0.4567891, 0.6543219, 0.5899999)]
    q = [round(v / math.sqrt(sum(x * x for x in q)), 6) for v in q]
    rel = a._qmul([-q[0], -q[1], -q[2], q[3]], q)          # a composed relative rotation, as articulation reads
    assert a._rot_deg(rel, [0.0, 0.0, 0.0, 1.0]) < 1e-3


def test_recipients_read_the_pose_the_impulse_was_placed_from():
    # the head moves 3 cm a frame; the drive placed the origin from frame 11's pose (centre at x=13, offset 5 along +x
    # pushes from -x: origin x=8, radius 6). Frame 10's centre (x=10) is 2 cm from the origin: inside. A neighbour that
    # was in reach on frame 10 (x=3) but not on frame 11 (x=1) is not a recipient.
    def row(f, head_x, nb_x):
        tm = {'head': [head_x, 0.0, 0.0, 0, 0, 0, 1], 'neck': [nb_x, 0.0, 0.0, 0, 0, 0, 1]}
        return {'f': f, 't': f / 60.0, 'pawns': [{'tm': tm, 'mesh_w': [0.0, 0.0, 0.0, 0.0]}]}
    by_f = {10: row(10, 10.0, 3.0), 11: row(11, 13.0, 1.0)}
    hit = {'config': {'bone': 'head', 'dir': [1.0, 0.0, 0.0], 'offset_cm': 5.0, 'radius': 6.0},
           'pawns': [{'origin': [8.0, 0.0, 0.0]}]}
    at = lambda r, p, b: r['pawns'][p]['tm'][b][:3]   # noqa: E731
    pose = a._drive_pose(by_f, hit, 10, at)
    assert pose['f'] == 11
    assert a.recipients(pose, hit, {b: at(pose, 0, b) for b in ('head', 'neck')}) == ['head']
