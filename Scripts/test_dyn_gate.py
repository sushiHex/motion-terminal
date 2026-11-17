"""dyn_gate: every check passes on a healthy ON/OFF result and goes red on the defect it exists for, including
the reproduced bypasses (a part missing from the RESULT, missing legs, wrong alphas, NaN, a
baseline written from a broken run)."""
import copy

import pytest

import dyn_gate as gate

PARTS = [{'id': 'cape_01', 'root': 'cape_01', 'bones': ['cape_01', 'cape_02'], 'backend': 'cr_dynamics'},
         {'id': 'strip_01', 'root': 'strip_01', 'bones': ['strip_01', 'strip_02'], 'backend': 'cr_dynamics'}]
COLLIDERS = {'shapes': [], 'chains': {}}


def _pawn(alpha):
    clear = {'min_clearance_cm': 1.0, 'frames_under_tol': 0, 'checks_run': 100, 'checks_skipped': 0}
    return {'alpha_read': [alpha, alpha], 'alpha_reads': [alpha], 'nonfinite': 0,
            'curves_found_all': True, 'tick_advances_every_frame': True,
            'event_frames': {e: [] for e in gate.EVENTS},
            'adjacent_tip_motion': {p['id']: {'max': 3.0, 'settle_max': 0.1} for p in PARTS},
            's5': {'collider_clearance': {p['id']: dict(clear) for p in PARTS},
                   'run_trail_deg': {p['id']: 30.0 for p in PARTS},
                   'liveliness': {p['id']: {'stop_fwd': 20.0} for p in PARTS}}}


def _result():
    return {'path': 'x/khai_dyn_t.json', 'error': None, 'frame_clock_nonfinite': 0, 'alphas': [1.0, 0.0],
            'cost': {'settled': True, 'frames': 12, 'ControlRig_Evaluate_ms': 1.1},
            'pawns': [_pawn(1.0), _pawn(0.0)],
            'pairs': [{'stop_window_tip_delta': {p['id']: 15.0 for p in PARTS}, 'control_bone_delta': {'pelvis': 0.0},
                       'paired_clearance': {'l': {'worst_margin_cm': 1.0}, 'r': {'worst_margin_cm': 2.0}},
                       'run_twist': {'strip_01': {'rms_deg': 6.0}}, 'stop_twist': {'strip_01': {'peak_deg': 20.0}}}]}


def _record():
    return {'hero': 'TestRig', 'parts': copy.deepcopy(PARTS), 'colliders': COLLIDERS,
            'config': {'pp_class': '/Game/MT/TestRig/ABP_MT_TestRigDynamics.ABP_MT_TestRigDynamics_C'},
            'build': {'hero': 'TestRig', 'abp': '/Game/MT/TestRig/ABP_MT_TestRigDynamics',
                      'parts': gate.digest(PARTS), 'colliders': gate.digest(COLLIDERS),
                      'with_colliders': True,
                      'behaviours': None, 'air': None,
                      'scripts': {n: gate.source_digest(n) for n in ('dyn_behaviours.py', 'dynamics_behaviours.json')}}}


def _failed(result, baseline=None, record=None):
    return sorted({(n, s) for n, s, ok, _d in gate.check(result, baseline or gate.measure(_result()),
                                                         record or _record()) if not ok})


def test_healthy_run_passes():
    assert gate.passed(gate.check(_result(), gate.measure(_result()), _record()))


def _no_colliders(r, pid='strip_01'):
    for pawn in r['pawns']:
        pawn['s5']['collider_clearance'][pid] = {'not_applicable': 'no colliders'}
    return r


def test_a_part_with_no_colliders_is_not_applicable_only_when_the_baseline_agrees():
    na = _no_colliders(_result())
    assert gate.passed(gate.check(na, gate.measure(_no_colliders(_result())), _record()))
    assert not [b for b in gate.baseline_blockers(na, _record()) if b[1] == 'strip_01']   # baselineable
    assert ('clearance', 'strip_01') in _failed(na)                 # the baseline still had a clearance: red
    # a baseline that merely lacks the value (an old or damaged one) is not agreement either
    lacking = gate.measure(_result())
    lacking['parts']['strip_01'].pop('min_clearance_cm')
    lacking['parts']['strip_01'].pop('clearance_na')
    assert ('clearance', 'strip_01') in _failed(na, lacking)


def test_cost_is_reported_not_gated():
    r = _result()
    r['cost'] = {'settled': True, 'frames': 12, 'ControlRig_Evaluate_ms': 9.0}     # reported...
    assert gate.passed(gate.check(r, gate.measure(_result()), _record()))     # ...never a pass/fail (unphased)
    assert gate.measure(r)['cost_ms'] == 9.0
    r['cost']['frames'] = 1
    assert gate.measure(r)['cost_ms'] is None                                   # a one-frame read is not kept


def _drop_part(r):
    for pawn in r['pawns']:
        for k in ('collider_clearance', 'run_trail_deg', 'liveliness'):
            pawn['s5'][k].pop('strip_01')
        pawn['adjacent_tip_motion'].pop('strip_01')
    r['pairs'][0]['stop_window_tip_delta'].pop('strip_01')


@pytest.mark.parametrize('mutate,expect', [
    (lambda r: r.update(error='boom'), ('setup', 'record')),
    (lambda r: r.pop('error'), ('setup', 'record')),
    (lambda r: r.update(frame_clock_nonfinite=1), ('setup', 'record')),
    (lambda r: r.update(alphas=[1.0, 1.0]), ('setup', 'alphas')),
    (lambda r: r['pawns'][1].update(alpha_read=[1.0, 1.0]), ('setup', 'alphas')),
    (lambda r: r['pawns'][1].update(alpha_reads=[0.0, 0.5]), ('setup', 'alphas')),   # a wrong alpha mid-run
    (lambda r: r['pawns'][0].update(nonfinite=1), ('integrity', 'pawn 0')),
    (lambda r: r['pawns'][0]['s5']['collider_clearance']['cape_01'].update(checks_run=float('nan')),
     ('clearance', 'cape_01')),
    (lambda r: r['pawns'][0]['s5']['collider_clearance']['cape_01'].update(frames_under_tol=float('nan')),
     ('clearance', 'cape_01')),
    (lambda r: r['pawns'][0]['event_frames']['MT_DynPosReset'].append(40), ('integrity', 'pawn 0')),
    (lambda r: r['pawns'][0].update(event_frames={}), ('integrity', 'pawn 0')),
    (lambda r: r['pawns'][1].update(tick_advances_every_frame=False), ('integrity', 'pawn 1')),
    (lambda r: r['pairs'][0]['control_bone_delta'].update(head=0.5), ('isolation', 'control bones')),
    (lambda r: r['pairs'][0].update(control_bone_delta={}), ('isolation', 'control bones')),
    (_drop_part, ('inventory', 'parts')),
    (lambda r: r['pairs'][0]['stop_window_tip_delta'].update(cape_01=0.3), ('responds', 'cape_01')),
    (lambda r: r['pairs'][0]['stop_window_tip_delta'].update(cape_01=float('nan')), ('responds', 'cape_01')),
    (lambda r: r['pawns'][0]['s5']['collider_clearance']['cape_01'].update(min_clearance_cm=0.8),
     ('clearance', 'cape_01')),
    (lambda r: r['pawns'][0]['s5']['collider_clearance']['cape_01'].update(checks_skipped=3),
     ('clearance', 'cape_01')),
    (lambda r: r['pawns'][0]['adjacent_tip_motion']['cape_01'].update(max=9.0), ('bounded', 'cape_01')),
    (lambda r: r['pawns'][0]['adjacent_tip_motion']['cape_01'].update(settle_max=1.0), ('bounded', 'cape_01')),
    (lambda r: r['pawns'][0]['s5']['run_trail_deg'].update(cape_01=34.0), ('behaviour', 'cape_01')),
    (lambda r: r['pawns'][0]['s5']['run_trail_deg'].update(cape_01=float('nan')), ('behaviour', 'cape_01')),
    # The twist moves no particle: only the roll measures can see it break
    (lambda r: r['pairs'][0]['run_twist']['strip_01'].update(rms_deg=0.5), ('behaviour', 'strip_01')),
    (lambda r: r['pairs'][0]['stop_twist'].pop('strip_01'), ('behaviour', 'strip_01')),
    (lambda r: r['pairs'][0]['run_twist'].update(cape_01={'rms_deg': 4.0}), ('behaviour', 'cape_01')),
    (lambda r: r['pairs'][0]['paired_clearance']['l'].update(worst_margin_cm=0.85), ('legs', 'l')),
    (lambda r: r['pairs'][0]['paired_clearance'].pop('r'), ('legs', 'r')),
])
def test_each_check_goes_red_on_its_defect(mutate, expect):
    r = copy.deepcopy(_result())
    mutate(r)
    assert expect in _failed(r)


def test_analysis_counts_nonfinite_raw_samples():
    import dyn_analyze as an
    sample = {'chain': {'a': [0.0, 1.0, 2.0]}, 'tips': {}, 'ctrl': {'pelvis': [0.0, float('nan'), 0.0]},
              'tm': {'b': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, float('inf')]}, 'loc': [0.0, 0.0, 0.0], 'speed': 0.0,
              'curves': {'MT_DynPosReset': {'found': True, 'value': float('nan')}}, 'mesh_w': [0.0, 0.0, 0.0, 0.0]}
    assert an._nonfinite([sample]) == 3


def test_flutter_scores_oscillation_not_a_constant_lag():
    import dyn_analyze as an

    def frames(on_x):
        return [{'t': i / 60.0, 'pawns': [{'tips': {'f': [x, 0.0, 0.0]}}, {'tips': {'f': [0.0, 0.0, 0.0]}}]}
                for i, x in enumerate(on_x)]
    assert an.flutter(frames([5.0] * 60), 1, 0.0, 1.0)['f'] == 0.0                 # trails 5 cm, never moves
    assert an.flutter(frames([5.0 + (2.0 if i % 2 else -2.0) for i in range(60)]), 1, 0.0, 1.0)['f'] == 2.0
    assert an.flutter(frames([5.0, 9.0]), 1, 0.5, 1.0)['f'] is None                # one sample in the window


def test_flutter_hz_reads_the_oscillation_rate():
    import dyn_analyze as an
    import math

    def frames(on_x):
        return [{'t': i / 60.0, 'pawns': [{'tips': {'f': [x, 1.0, 0.0]}}, {'tips': {'f': [0.0, 0.0, 0.0]}}]}
                for i, x in enumerate(on_x)]
    for hz in (1.0, 3.0, 8.0):
        sine = [5.0 + 2.0 * math.sin(2 * math.pi * hz * i / 60.0) for i in range(120)]
        assert an.flutter_hz(frames(sine), 1, 0.0, 2.0)['f'] == pytest.approx(hz, rel=0.05)
    assert an.flutter_hz(frames([5.0] * 60), 1, 0.0, 1.0)['f'] is None            # a constant lag has no rate
    assert an.flutter_hz(frames([5.0, 9.0]), 1, 0.5, 1.0)['f'] is None             # one sample in the window
    stuck = frames([5.0, 6.0, 5.0, 6.0])
    stuck[2]['t'] = stuck[1]['t']                                                 # a repeated timestamp
    assert an.flutter_hz(stuck, 1, 0.0, 1.0)['f'] is None
    assert an.flutter_hz(frames([5.0] * 60), 1, 0.0, 1.0, floor=0.0)['f'] is None  # no spread, no divide by zero


def test_compare_deltas_wrap_angles_and_count_a_lost_value():
    import dyn_compare as dc
    assert dc.delta('stand_hang', 179.99, -179.99) == pytest.approx(0.02)          # across the seam, not 359.98
    assert dc.delta('max_step_cm', 3.0, 1.0) == 2.0
    assert dc.delta('settle_s', None, 2.0) == float('inf')                         # stopped settling: a change
    assert dc.delta('settle_s', None, None) == 0.0


def test_hang_reads_the_angle_from_straight_down_toward_travel():
    import dyn_analyze as an
    parts = [{'id': 'p', 'root': 'r', 'tip': 't'}]
    pose = lambda tx, ty, tz: {'chain': {'r': [0.0, 0.0, 100.0], 't': [tx, ty, tz]}}   # noqa: E731
    frames = [{'t': 0.0}, {'t': 1.0}]
    h = lambda x, y, z, fwd=(0.0, 1.0): an.hang([pose(x, y, z)] * 2, frames, 0.0, 1.0, parts, fwd)['p']   # noqa: E731
    assert h(0, 0, 90) == {'fwd': 0.0, 'incl': 0.0}                                  # straight down
    assert h(0, 10, 90) == {'fwd': 45.0, 'incl': 45.0}                               # swung toward travel
    assert h(0, -10, 90)['fwd'] == -45.0                                              # back toward a front part's body
    assert h(10, 0, 90, fwd=(1.0, 0.0))['fwd'] == 45.0                                # travel along +X: no axis assumed
    side = h(10, 0, 100)                                                             # horizontal, sideways
    assert side['fwd'] is None and side['incl'] == 90.0                              # no in-plane angle; incl says so
    # circular mean: two samples at +179 and -179 average to 180, not 0
    up = [pose(0, 0.0175, 110), pose(0, -0.0175, 110)]
    assert abs(abs(an.hang(up, frames, 0.0, 1.0, parts, (0.0, 1.0))['p']['fwd']) - 180.0) < 0.01
    assert an.hang([pose(0, 0, 90)] * 2, frames, 2.0, 3.0, parts, (0.0, 1.0))['p'] is None
    # travel direction from motion in component space (mesh yaw 0: world = component)
    walk = [{'loc': [0.0, 5.0 * i, 0.0], 'mesh_w': [0, 0, 0, 0.0]} for i in range(3)]
    assert an.travel_dir(walk, [{'t': 0.0}, {'t': 0.5}, {'t': 1.0}], 0.0, 1.0) == (0.0, 1.0)


def test_settle_times_the_ring_down_to_the_rest_offset():
    import dyn_analyze as an

    def frames(on_x):
        return [{'t': i / 10.0, 'pawns': [{'tips': {'f': [x, 0.0, 0.0]}}, {'tips': {'f': [0.0, 0.0, 0.0]}}]}
                for i, x in enumerate(on_x)]
    # 10 Hz samples. Standing at a 3 cm rest offset for t < 1 (the reference window), stop at t=1: swings +-10 cm
    # about the rest offset through t=1.5, then rests; tol = max(1, 0.15*10) = 1.5, first settled sample t=1.6
    stand = [3.0] * 10
    rest, t_stop = (0.0, 1.0), 1.0
    ring = stand + [3.0 + (10.0 if i % 2 else -10.0) for i in range(6)] + [3.0] * 10
    assert an.settle_time(frames(ring), 1, rest, t_stop)['f'] == 0.6
    assert an.settle_time(frames(stand * 3), 1, rest, t_stop)['f'] == 0.0                   # never moved
    still_ringing = stand + [3.0 + (5.0 if i % 2 else -5.0) for i in range(16)]
    assert an.settle_time(frames(still_ringing), 1, rest, t_stop)['f'] is None
    drift = stand + [3.0 + 0.1 * i for i in range(30)]                    # 1 cm/s away from rest: leaves tol, never back
    assert an.settle_time(frames(drift), 1, rest, t_stop)['f'] is None
    brief = stand + [3.0 + (10.0 if i % 2 else -10.0) for i in range(6)] + [3.0] * 2   # 0.1 s at rest: not seen to hold
    assert an.settle_time(frames(brief), 1, rest, t_stop)['f'] is None


def test_identity_fails_when_the_record_is_not_what_was_built():
    for key, value in (('colliders', 'stale'), ('with_colliders', False),
                       ('behaviours', {'pendant': {'native': {'Strength': 0.5}}}), ('behaviours', 'DELETE'),
                       ('air', {'SpeedGust': 1.0}), ('air', 'DELETE')):
        rec = _record()
        if value == 'DELETE':
            del rec['build'][key]
        else:
            rec['build'][key] = value
        assert ('identity', 'build') in _failed(_result(), record=rec), key
    assert not gate.passed(gate.check(_result(), gate.measure(_result()), None))   # no record: not verified


def test_identity_fails_when_the_loaded_rig_is_another_heros():
    for mutate in (lambda r: r['build'].update(hero='OtherRig'), lambda r: r['build'].pop('hero'),
                   lambda r: r.pop('hero'), lambda r: r['build'].update(hero_mismatch='built for OtherRig'),
                   lambda r: r['config'].update(pp_class='/Game/MT/Other/ABP_MT_OtherDynamics.ABP_MT_OtherDynamics_C'),
                   lambda r: r['build'].pop('abp')):
        rec = _record()
        mutate(rec)
        assert ('identity', 'hero') in _failed(_result(), record=rec)


def test_a_pelvis_flap_makes_the_leg_check_apply():
    import dyn_analyze as an
    an._set_roles({})
    chain = {'id': 'cloth', 'root': 'c1', 'bones': ['c1', 'c2'], 'parent': 'pelvis'}
    flap = {'id': 'tassel', 'root': 't1', 'bones': ['t1'], 'parent': 'pelvis'}
    hair = {'id': 'hair', 'root': 'h1', 'bones': ['h1', 'h2'], 'parent': 'head'}
    assert an.leg_bones([chain, hair]) == [('chain', 'c2')]
    assert an.leg_bones([flap]) == [('tips', 'tassel')]           # a flap's moving particle is its tip
    assert an.leg_bones([hair]) == []                              # nothing at the legs: not applicable
    an._set_roles({'roles': {'pelvis': 'Hips'}})                   # the skeleton's own pelvis name
    assert an.leg_bones([dict(flap, parent='Hips')]) == [('tips', 'tassel')]
    an._set_roles({})


def test_identity_fails_when_a_heros_extras_appear_after_the_build(monkeypatch):
    real = gate.source_digest
    monkeypatch.setattr(gate, 'source_digest', lambda n: 'abc123def456' if n == 'extras:TestRig' else real(n))
    assert ('identity', 'sources') in _failed(_result(), record=_record())


def _no_legs(r):
    r['pairs'][0].pop('paired_clearance')
    r['pairs'][0]['legs_applicable'] = False


def test_legs_not_applicable_passes_only_against_a_baseline_that_agrees():
    hair = copy.deepcopy(_result())
    _no_legs(hair)
    assert gate.passed(gate.check(hair, gate.measure(hair), _record()))      # a hair-only hero against its own baseline
    assert ('legs', 'n/a') in _failed(hair)                                   # ...but not against one that had legs
    lost = copy.deepcopy(_result())
    lost['pairs'][0].pop('paired_clearance')                                  # applicable, and the check did not run
    assert ('legs', 'l') in _failed(lost)
    assert not [b for b in gate.baseline_blockers(hair, _record()) if b[1] == 'legs']


def test_identity_fails_when_a_source_changed_since_the_build():
    rec = _record()
    rec['build']['scripts']['dynamics_behaviours.json'] = 'stale0000000'           # the library edited, not rebuilt
    assert ('identity', 'sources') in _failed(_result(), record=rec)
    rec['build']['scripts'] = {}                                                    # an old manifest: not verified
    assert ('identity', 'sources') in _failed(_result(), record=rec)
    assert ('identity', 'sources') not in _failed(_result())


def test_a_part_without_a_baseline_is_not_verified_not_passed():
    base = gate.measure(_result())
    del base['parts']['cape_01']
    got = _failed(_result(), base)
    assert {('clearance', 'cape_01'), ('bounded', 'cape_01'), ('behaviour', 'cape_01')} <= set(got)


def test_a_baseline_field_that_is_missing_fails_instead_of_crashing():
    base = gate.measure(_result())
    del base['parts']['cape_01']['max_step_cm']
    del base['parts']['cape_01']['min_clearance_cm']
    got = _failed(_result(), base)
    assert ('bounded', 'cape_01') in got and ('clearance', 'cape_01') in got


def test_baseline_refuses_a_broken_run_and_a_missing_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, 'BASELINE_DIR', str(tmp_path))
    for mutate in (lambda r: r['pairs'][0]['stop_window_tip_delta'].update(cape_01=0.0),     # the rig did nothing
                   lambda r: r['pawns'][0]['s5']['collider_clearance']['cape_01'].update(checks_skipped=2),
                   lambda r: r.update(alphas=[0.0, 0.0]), _drop_part,
                   lambda r: r['pawns'][0]['s5']['run_trail_deg'].update(cape_01=float('nan')),
                   lambda r: r['pawns'][0]['adjacent_tip_motion']['cape_01'].pop('settle_max'),
                   lambda r: r['pairs'][0]['paired_clearance'].pop('l')):
        r = _result()
        mutate(r)
        with pytest.raises(RuntimeError):
            gate.write_baseline('Hero', r, _record(), 'why')
    with pytest.raises(RuntimeError):
        gate.write_baseline('Hero', _result(), _record(), ' ')
    doc = gate.write_baseline('Hero', _result(), _record(), 'first baseline')
    assert doc['why'] == 'first baseline' and doc['parts']['cape_01']['trail_deg'] == 30.0
