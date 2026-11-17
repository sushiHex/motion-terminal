"""hit_reaction_core: a hero's reaction body from its profile and skeleton, without the editor."""
import json
import math
import os

import pytest

import hit_reaction_core as core

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _profile(rig):
    with open(os.path.join(REPO, 'HeroProfiles', rig + '.json')) as f:
        return json.load(f)


def _parents(prof):
    """A biped hierarchy from the roles, with a twist helper between spine bones (a body-less bone in between)."""
    r = prof['roles']
    sp = r['spine']
    par = {r['pelvis']: 'root', sp[0]: r['pelvis'], 'spine_helper': sp[0]}
    for a, b in zip(sp, sp[1:]):
        par[b] = a
    par[sp[1]] = 'spine_helper'
    par[r['neck']] = sp[-1]
    par['neck_02'] = r['neck']
    par[r['head']] = 'neck_02'
    return par


@pytest.mark.parametrize('rig', ['ParagonAurora', 'ParagonKhaimera'])
def test_a_torso_tree_on_role_bones_parented_by_the_skeleton(rig):
    prof = _profile(rig)
    spec = core.physics_spec(prof, _parents(prof))
    r = prof['roles']
    bones = [b['bone'] for b in spec['bodies']]
    # every bone from the pelvis to the head, the in-between ones included (no reference-pose anchors across a gap),
    # then the thigh anchors
    torso = [r['pelvis'], r['spine'][0], 'spine_helper'] + r['spine'][1:] + [r['neck'], 'neck_02', r['head']]
    assert bones == torso + [r['thigh_l'], r['thigh_r']]
    assert next(b for b in spec['bodies'] if b['bone'] == 'spine_helper')['role'] == 'spine'
    assert next(b for b in spec['bodies'] if b['bone'] == 'neck_02')['role'] == 'neck'
    # the pelvis reacts; the thighs are kinematic and joined to nothing (they hold the legs to the animation)
    assert spec['kinematic'] == [r['thigh_l'], r['thigh_r']] and {r['pelvis'], r['head']} <= set(spec['simulated'])
    edges = [(c['child'], c['parent']) for c in spec['constraints']]
    assert edges == list(zip(torso[1:], torso[:-1]))                # each to its skeletal parent
    assert all(c['angular'] == 'free' for c in spec['constraints'])
    for b in spec['bodies']:
        assert b['elements'] and all(e['radius_cm'] > 0 for e in b['elements'])


def test_arms_hang_from_the_spine_top_with_their_fitted_shapes():
    prof = _profile('ParagonAurora')
    r = prof['roles']
    par = _parents(prof)
    for s in ('l', 'r'):
        par.update({r['clavicle_' + s]: r['spine'][-1], r['upperarm_' + s]: r['clavicle_' + s],
                    'lowerarm_twist_' + s: r['upperarm_' + s], r['lowerarm_' + s]: 'lowerarm_twist_' + s,
                    r['hand_' + s]: r['lowerarm_' + s]})
    spec = core.physics_spec(prof, par)
    by = {b['bone']: b for b in spec['bodies']}
    for s in ('l', 'r'):
        assert all(by[r[k + '_' + s]]['physics_type'] == 'simulated' for k in core.ARM_ROLES)
        assert by['lowerarm_twist_' + s]['role'] == 'lowerarm_' + s            # a helper takes the role below it
    assert by['upperarm_l']['elements'][0]['from'] == 'fit:upperarm_l'
    assert by['clavicle_r']['elements'][0]['aim'] == 'upperarm_r'
    assert by['hand_l']['elements'][0]['type'] == 'sphere'
    edges = {(c['child'], c['parent']) for c in spec['constraints']}
    assert ('clavicle_l', r['spine'][-1]) in edges and ('lowerarm_r', 'lowerarm_twist_r') in edges
    # the arm-torso candidates, never the arm's own chain, the clavicles or the thighs; with no idle none collides
    pairs = set(core.collide_pairs(spec['bodies']))
    assert ('lowerarm_l', r['pelvis']) in pairs and ('hand_r', r['head']) in pairs
    assert not any(a.startswith('clavicle') or b.startswith(('thigh', 'clavicle', 'upperarm')) for a, b in pairs)
    assert spec['collide'] == [] and set(spec['idle_clearance_cm']) == {'%s|%s' % p for p in pairs}


def test_a_pair_collides_only_where_the_idle_keeps_it_apart():
    ID = [0.0, 0.0, 0.0, 1.0]
    sph = lambda r: [{'type': 'sphere', 'aim': None, 'radius_cm': r}]   # noqa: E731
    bodies = [{'bone': 'pelvis', 'role': 'pelvis', 'elements': sph(10.0)},
              {'bone': 'hand_l', 'role': 'hand_l', 'elements': sph(4.0)},
              {'bone': 'hand_r', 'role': 'hand_r', 'elements': sph(4.0)}]
    idle = [{'pelvis': [0, 0, 0] + ID, 'hand_l': [20, 0, 0] + ID, 'hand_r': [-14.2, 0, 0] + ID},   # 6 / 0.2 cm apart
            {'pelvis': [0, 0, 0] + ID, 'hand_l': [16, 0, 0] + ID, 'hand_r': [-20, 0, 0] + ID}]     # 2 / 6
    ref = {'pelvis': [0, 0, 0] + ID, 'hand_l': [30, 0, 0] + ID, 'hand_r': [-30, 0, 0] + ID}       # 16 cm apart
    collide, gaps, rgaps = core.colliding(bodies, idle, ref)
    assert [(c['a'], c['b']) for c in collide] == [('hand_l', 'pelvis')]           # hand_r came within 0.2 cm
    assert gaps == {'hand_l|pelvis': 2.0, 'hand_r|pelvis': 0.2} and rgaps == {'hand_l|pelvis': 16.0,
                                                                               'hand_r|pelvis': 16.0}
    assert core.colliding(bodies, None, ref)[0] == [] and core.colliding(bodies, idle)[0] == []   # both poses decide
    # the fit's slack goes first: hand_r gives up 0.3 cm and then clears the hip by 0.5 cm, so it collides too
    assert core.slack_to_clear(bodies, idle) == {'hand_r': 0.3}
    assert bodies[2]['elements'][0]['radius_cm'] == 3.7 and bodies[2]['elements'][0]['fit_radius_cm'] == 4.0
    assert [(c['a'], c['b']) for c in core.colliding(bodies, idle, ref)[0]] == [('hand_l', 'pelvis'),
                                                                                 ('hand_r', 'pelvis')]
    # ...unless the reference pose brings the pair within COLLIDE_MIN_CLEARANCE_CM: the builder would refuse it, so the
    # spec's list (which the asset's enabled set must equal) leaves it out
    near = dict(ref, hand_l=[15.0, 0, 0] + ID)                                     # 1 cm at the reference pose
    collide, _g, rgaps = core.colliding(bodies, idle, near)
    assert [(c['a'], c['b']) for c in collide] == [('hand_r', 'pelvis')] and rgaps['hand_l|pelvis'] == 1.0
    # ...decided unrounded, as the builder decides it: 1.9996 cm records as 2.0 but stays out (the builder would fail)
    edge = dict(ref, hand_l=[15.9996, 0, 0] + ID)
    collide, _g, rgaps = core.colliding(bodies, idle, edge)
    assert [(c['a'], c['b']) for c in collide] == [('hand_r', 'pelvis')] and rgaps['hand_l|pelvis'] == 2.0
    assert [(c['a'], c['b']) for c in core.colliding(bodies, idle, dict(ref, hand_l=[16.0, 0, 0] + ID))[0]] == [
        ('hand_l', 'pelvis'), ('hand_r', 'pelvis')]                                 # exactly 2.0 cm clears
    # an overlap deeper than the slack is anatomy: no shrink, no collision
    deep = [{'pelvis': [0, 0, 0] + ID, 'hand_l': [12, 0, 0] + ID, 'hand_r': [-20, 0, 0] + ID}]        # 2 cm in
    assert core.slack_to_clear(bodies, deep) == {} and core.colliding(bodies, deep, ref)[0] == [
        {'a': 'hand_r', 'b': 'pelvis', 'idle_clearance_cm': 6.3, 'ref_clearance_cm': 16.3,
         'min_clearance_cm': core.COLLIDE_MIN_CLEARANCE_CM}]


def test_fitted_shapes_carry_over_exactly():
    prof = _profile('ParagonAurora')
    spec = core.physics_spec(prof, _parents(prof))
    s3 = next(b for b in spec['bodies'] if b['bone'] == 'spine_03')
    assert sorted(e['from'] for e in s3['elements']) == ['fit:torso_spine_03_n', 'fit:torso_spine_03_p']
    p = next(s for s in prof['colliders']['shapes'] if s['name'] == 'torso_spine_03_p')
    e = next(e for e in s3['elements'] if e['from'] == 'fit:torso_spine_03_p')
    import collider_autofit as af
    assert (e['center'], e['radius_cm'], e['length_cm']) == (
        p['local']['translation'], p['local']['radius_cm'], p['local']['length_cm'])
    # the same axis, re-rolled level so the builder's FRotator keeps it (capsule_rotation), and unit (the profile keeps
    # 6 decimals, so its quaternion is a hair off unit and scales what it rotates)
    Z = [0.0, 0.0, 1.0]
    assert _close(af.qrot(e['rotation_xyzw'], Z), af.unit(af.qrot(p['local']['rotation_xyzw'], Z)), 1e-9)
    assert abs(af.qrot(e['rotation_xyzw'], [1.0, 0.0, 0.0])[2]) < 1e-9


def test_missing_fit_falls_back_and_a_broken_tree_is_refused():
    prof = dict(_profile('ParagonAurora'), colliders={'shapes': []})
    spec = core.physics_spec(prof, _parents(prof))
    head = next(b for b in spec['bodies'] if b['role'] == 'head')
    assert head['elements'][0]['from'].startswith('default') and head['elements'][0]['type'] == 'sphere'
    with pytest.raises(ValueError, match='does not descend'):
        core.physics_spec(prof, {})


def test_cloth_collision_bodies_cover_the_hips_and_legs():
    prof = _profile('ParagonAurora')
    spec = core.cloth_collision_spec(prof)
    bones = [b['bone'] for b in spec['bodies']]
    assert bones == ['pelvis', 'thigh_l', 'thigh_r', 'calf_l', 'calf_r'] and spec['constraints'] == []
    by = {b['bone']: b['elements'][0] for b in spec['bodies']}
    # tapered; with no reference pose (no skin capsule placed), toward each aim (the pelvis toward the first spine bone)
    assert all(e['type'] == 'tapered' for e in by.values()) and by['pelvis']['aim'] == prof['roles']['spine'][0]
    assert by['calf_l']['aim'] == 'foot_l' and by['thigh_r']['from'] == 'share:thigh'
    assert all(b['physics_type'] == 'kinematic' for b in spec['bodies'])


def test_an_older_intakes_fit_is_refused_not_quietly_a_height_share():
    prof = json.loads(json.dumps(_profile('ParagonAurora')))
    prof['colliders']['fitted']['thigh_r'] = {'skin_cm': [9.0, 6.0]}           # the old intake's field, no capsule
    with pytest.raises(ValueError, match='re-run hero_intake'):
        core.cloth_collision_spec(prof)


def test_a_tapered_read_back_must_keep_its_sense_and_both_radii():
    q = core.capsule_rotation([0.0, 0.0, 1.0])
    want = {'type': 'tapered', 'center': [0, 0, 5.0], 'rotation_xyzw': q, 'radius_cm': 9.0, 'radius_end_cm': 6.0,
            'length_cm': 10.0}
    assert core.shape_matches(want, dict(want))
    assert not core.shape_matches(want, dict(want, radius_end_cm=9.0))
    flipped = core.capsule_rotation([0.0, 0.0, -1.0])
    assert not core.shape_matches(want, dict(want, rotation_xyzw=flipped))    # its radii swapped end for end
    assert core.shape_matches(dict(want, type='capsule'), dict(want, type='capsule', rotation_xyzw=flipped))


def test_an_onboarded_cloth_source_from_an_older_generator_is_stale(tmp_path):
    prof = _profile('ParagonAurora')
    r = prof['roles']
    ref = {b: [0.0, 0.0, 100.0 - 10 * i] + ID for i, b in enumerate(
        [r['pelvis'], r['spine'][0], r['thigh_l'], r['thigh_r'], r['calf_l'], r['calf_r'], r['foot_l'], r['foot_r']])}
    ref[r['spine'][0]] = [0.0, 0.0, 120.0] + ID
    now = core.cloth_collision_spec(prof, ref)
    rec = tmp_path / 'onboard.json'
    rec.write_text(json.dumps({'cloth_collision': {'spec': now}}))
    h = {'rig': 'R', 'onboard_record': str(rec)}
    core.onboarded_cloth(h, now)                                    # today's: accepted
    old = json.loads(json.dumps(now))
    old['bodies'][1]['elements'][0]['radius_end_cm'] += 3.0          # a fatter knee end: an older fit
    rec.write_text(json.dumps({'cloth_collision': {'spec': old}}))
    with pytest.raises(RuntimeError, match='cloth collision bodies are stale'):
        core.onboarded_cloth(h, now)


def test_a_cloth_collider_is_its_own_sides_skin_capsule_placed_on_the_limb():
    prof = json.loads(json.dumps(_profile('ParagonAurora')))
    r = prof['roles']
    # every bone rotated 90 deg about X, stacked down the body; the skin capsules 3 cm off the thigh bones toward +X
    s2 = math.sqrt(0.5)
    tms = {r[k] if not isinstance(r[k], list) else r[k][0]: [0.0, 0.0, 90.0 - 10 * i, s2, 0.0, 0.0, s2]
           for i, k in enumerate(('pelvis', 'spine', 'thigh_l', 'thigh_r', 'calf_l', 'calf_r', 'foot_l', 'foot_r'))}
    prof['colliders']['fitted'].update({
        'thigh_l': {'skin': {'near': [3.0, 0.0, 70.0], 'far': [3.0, 0.0, 30.0], 'radius_cm': [9.0, 6.0]}},
        'thigh_r': {'skin': {'near': [3.0, 0.0, 60.0], 'far': [3.0, 0.0, 20.0], 'radius_cm': [10.65, 7.2]}}})
    prof['colliders']['fitted']['calf_l'].pop('skin', None)             # an unmeasured segment
    bodies = {b['bone']: b['elements'][0] for b in core.cloth_collision_spec(prof, tms)['bodies']}
    t = bodies[r['thigh_r']]
    assert (t['radius_cm'], t['radius_end_cm']) == (10.65, 7.2)          # its own side: no smaller-side rule
    assert t['length_cm'] == 40.0 and t['from'] == 'skin:thigh_r'
    near, far = core.body_tapered({'bone': r['thigh_r'], 'elements': [t]}, tms)[0][:2]
    assert [round(v, 3) for v in near] == [3.0, 0.0, 60.0] and [round(v, 3) for v in far] == [3.0, 0.0, 20.0]
    c = bodies[r['calf_l']]                                              # no skin capsule fitted: the height share
    share = round(core.CLOTH_SHARE['calf'] * prof['scale']['height_cm'], 2)
    assert (c['radius_cm'], c['radius_end_cm']) == (share, share) and c['from'] == 'share:calf'


def test_arms_are_limbs_before_the_spine_with_stiffer_springs():
    prof = _profile('ParagonKhaimera')
    assert core.limbs(prof) == [('Arm_l', 'clavicle_l'), ('Arm_r', 'clavicle_r'), ('Spine', 'spine_01'),
                                ('Leg_l', 'thigh_l'), ('Leg_r', 'thigh_r'), ('Pelvis', 'pelvis')]
    text = core.initial_updates(prof)
    assert text.count('AngularStrengthMultiplier=%f' % core.ARM_STRENGTH_MULTIPLIER) == 2
    assert '"ParentSpace_Arm_l"' in text and '"ParentSpace_Arm_r"' in text and 'Spine' not in text
    assert core.physics_spec(prof, _parents(prof))['position_iterations'] == core.POSITION_ITERATIONS > 6


def test_each_arm_joint_gets_its_own_spring_after_the_set():
    prof = _profile('ParagonAurora')
    r = prof['roles']
    par = _parents(prof)
    for s in ('l', 'r'):
        par.update({r['clavicle_' + s]: r['spine'][-1], r['upperarm_' + s]: r['clavicle_' + s],
                    'lowerarm_twist_' + s: r['upperarm_' + s], r['lowerarm_' + s]: 'lowerarm_twist_' + s,
                    r['hand_' + s]: r['lowerarm_' + s]})
    text = core.initial_updates(prof, core.physics_spec(prof, par))
    m = core.ARM_JOINT_MULTIPLIER
    want = [('%s_clavicle_l' % r['spine'][-1], m['clavicle']), ('clavicle_l_upperarm_l', m['upperarm']),
            ('upperarm_l_lowerarm_twist_l', m['lowerarm']),        # a helper takes the role below it
            ('lowerarm_twist_l_lowerarm_l', m['lowerarm']), ('lowerarm_l_hand_l', m['hand'])]
    for name, k in want:
        assert '(Name="%s",Data=(AngularStrengthMultiplier=%f))' % (name, k) in text
    assert text.index('ParentSpace_Arm_r') < text.index('clavicle_l_upperarm_l')     # the sets first, then joints
    assert '_spine_' not in text and '_head"' not in text                            # the torso keeps the default


def test_a_joint_class_given_a_damping_multiplier_carries_it_and_the_others_do_not():
    prof = _profile('ParagonAurora')
    r = prof['roles']
    par = _parents(prof)
    for s in ('l', 'r'):
        par.update({r['clavicle_' + s]: r['spine'][-1], r['upperarm_' + s]: r['clavicle_' + s],
                    r['lowerarm_' + s]: r['upperarm_' + s], r['hand_' + s]: r['lowerarm_' + s]})
    text = core.initial_updates(prof, core.physics_spec(prof, par), damping={'lowerarm': 0.6})
    m = core.ARM_JOINT_MULTIPLIER
    assert ('(Name="upperarm_l_lowerarm_l",Data=(AngularStrengthMultiplier=%f,AngularDampingRatioMultiplier=%f))'
            % (m['lowerarm'], 0.6)) in text
    assert '(Name="lowerarm_l_hand_l",Data=(AngularStrengthMultiplier=%f))' % m['hand'] in text


def _legs(par, r, ik_parent='ik_foot_root'):
    """Legs under the pelvis and, when ik_parent, ik_foot_l/r under it (ik_foot_root under root, as on the heroes)."""
    for s in ('l', 'r'):
        par.update({r['thigh_' + s]: r['pelvis'], r['calf_' + s]: r['thigh_' + s], r['foot_' + s]: r['calf_' + s]})
        if ik_parent:
            par['ik_foot_' + s] = ik_parent
    if ik_parent == 'ik_foot_root':
        par['ik_foot_root'] = 'root'
    return par


def test_legs_with_ik_foot_bones_are_planted_by_ik_and_lose_their_anchors():
    prof = _profile('ParagonAurora')
    r = prof['roles']
    par = _legs(_parents(prof), r)
    legs = core.leg_ik(r, par)
    assert [(g['foot'], g['target']) for g in legs] == [(r['foot_l'], 'ik_foot_l'), (r['foot_r'], 'ik_foot_r')]
    spec = core.physics_spec(prof, par)
    assert spec['leg_ik'] == legs and not ({r['thigh_l'], r['thigh_r']} & {b['bone'] for b in spec['bodies']})
    assert not [n for n, _b in core.limbs(prof, spec) if n.startswith(core.LEG_LIMB)]
    copies, definition = core.leg_ik_nodes(legs)
    assert copies == [(r['foot_l'], 'ik_foot_l'), (r['foot_r'], 'ik_foot_r')]
    assert 'IKFootBone=(BoneName="ik_foot_r")' in definition and 'bEnableKneeTwistCorrection=False' in definition


def test_a_record_with_other_leg_ik_is_stale():
    prof = _profile('ParagonAurora')
    spec = core.physics_spec(prof, _legs(_parents(prof), prof['roles']))
    leg = lambda got: [x for x in core.spec_mismatch(spec, got) if 'leg IK' in x]     # noqa: E731
    assert spec['leg_ik'] and leg(spec) == [] and leg(dict(spec, leg_ik=[]))


def test_ik_targets_the_reaction_moves_or_lacks_keep_the_anchors():
    prof = _profile('ParagonAurora')
    r = prof['roles']
    assert core.leg_ik(r, _legs(_parents(prof), r, ik_parent=None)) == []              # no IK foot bones
    assert core.leg_ik(r, _legs(_parents(prof), r, ik_parent=r['pelvis'])) == []       # under the pelvis: moved
    beside = dict(_legs(_parents(prof), r), **{r['thigh_l']: 'root', r['thigh_r']: 'root'})
    assert core.leg_ik(r, beside) == []                     # thighs beside the pelvis: moving it moves no hip
    spec = core.physics_spec(prof, _legs(_parents(prof), r, ik_parent=None))
    assert spec['leg_ik'] == [] and {r['thigh_l'], r['thigh_r']} <= {b['bone'] for b in spec['bodies']}
    assert [n for n, _b in core.limbs(prof, spec) if n.startswith(core.LEG_LIMB)] == ['Leg_l', 'Leg_r']


def _sphere(bone, r, center=(0.0, 0.0, 0.0)):
    return {'bone': bone, 'role': bone, 'physics_type': 'simulated',
            'elements': [{'type': 'sphere', 'radius_cm': r, 'center': list(center)}]}


def test_a_body_weighs_as_the_engine_weighs_it():
    import math
    m, i = core.body_mass(_sphere('a', 10.0))
    mu = core.DENSITY_KG_CM3 * 4.0 / 3.0 * math.pi * 1000.0
    assert m == pytest.approx(mu ** core.MASS_POWER)
    assert i == pytest.approx(0.4 * m * 100.0)                 # the uniform sphere's inertia, at the raised mass


def test_a_joint_carries_its_child_and_everything_below_it_about_the_joint():
    bodies = [_sphere('p', 10.0), _sphere('c', 5.0), _sphere('g', 5.0)]
    cons = [{'parent': 'p', 'child': 'c'}, {'parent': 'c', 'child': 'g'}]
    ref = {'p': [0.0, 0.0, 0.0] + ID, 'c': [10.0, 0.0, 0.0] + ID, 'g': [30.0, 0.0, 0.0] + ID}
    core.joint_inertias(bodies, cons, ref)
    (mp, ip), (mc, ic) = core.body_mass(bodies[0]), core.body_mass(bodies[1])
    assert cons[0]['pair_inertia'] == pytest.approx(1.0 / (1.0 / ip + 1.0 / ic), rel=1e-4)
    assert cons[0]['carried_inertia'] == pytest.approx(ic + (ic + mc * 20.0 ** 2), rel=1e-4)   # c on it, g 20 out
    assert cons[1]['carried_inertia'] == pytest.approx(ic, rel=1e-4)                          # g on its own joint


def test_a_joint_holding_four_times_its_pair_gets_twice_the_target():
    c = {'pair_inertia': 10.0, 'carried_inertia': 40.0}
    s, d = core.arm_joint_tuning(c, 'lowerarm', {'lowerarm': 3.0}, {'lowerarm': 1.5})
    assert (s, d) == (pytest.approx(6.0 / core.CONTROL_STRENGTH_HZ), pytest.approx(3.0 / core.CONTROL_DAMPING_RATIO))
    assert core.arm_joint_tuning({}, 'lowerarm', {'lowerarm': 3.0}, {'lowerarm': 1.5}) is None
    assert core.arm_joint_tuning(c, 'hand', {'lowerarm': 3.0}, {'lowerarm': 1.5}) is None


def test_an_explicit_damping_override_wins_over_the_inertia_tuning():
    # with inertias present the override was silently ignored
    prof = _profile('ParagonAurora')
    r = prof['roles']
    spec = {'bodies': [{'bone': r['upperarm_l'], 'role': 'upperarm_l'}, {'bone': r['lowerarm_l'], 'role': 'lowerarm_l'}],
            'constraints': [{'parent': r['upperarm_l'], 'child': r['lowerarm_l'], 'pair_inertia': 10.0,
                             'carried_inertia': 40.0}]}
    tuned = core.initial_updates(prof, spec)
    s, d = core.arm_joint_tuning(spec['constraints'][0], 'lowerarm')
    assert 'AngularDampingRatioMultiplier=%f' % d in tuned
    over = core.initial_updates(prof, spec, damping={'lowerarm': 0.5})
    assert 'AngularStrengthMultiplier=%f,AngularDampingRatioMultiplier=%f' % (s, 0.5) in over


def test_a_record_without_the_joint_inertias_is_stale():
    bodies = [_sphere('p', 10.0), _sphere('c', 5.0)]
    want = {'bodies': bodies, 'constraints': [{'parent': 'p', 'child': 'c', 'pair_inertia': 1.0,
                                               'carried_inertia': 2.0}]}
    got = {'bodies': bodies, 'constraints': [{'parent': 'p', 'child': 'c'}]}
    assert any('carried_inertia' in x for x in core.spec_mismatch(want, got))
    assert core.spec_mismatch(want, want) == []


ID = [0.0, 0.0, 0.0, 1.0]


def _qz(deg):
    import math
    return [0.0, 0.0, math.sin(math.radians(deg) / 2), math.cos(math.radians(deg) / 2)]


def _close(u, v, tol=1e-6):
    return all(abs(a - b) <= tol for a, b in zip(u, v)) and len(u) == len(v)


def _arm_rig(prof):
    """The biped with arms (a twist helper in each forearm), a finger on the right hand only, and a reference pose:
    every bone somewhere distinct, the right clavicle turned 90 deg about Z with its upper arm 20 cm along component +Y
    (so +X in the clavicle's own frame), the finger 20 cm on along the right forearm."""
    import collider_autofit as af
    r = prof['roles']
    par = _parents(prof)
    for s in ('l', 'r'):
        par.update({r['clavicle_' + s]: r['spine'][-1], r['upperarm_' + s]: r['clavicle_' + s],
                    'lowerarm_twist_' + s: r['upperarm_' + s], r['lowerarm_' + s]: 'lowerarm_twist_' + s,
                    r['hand_' + s]: r['lowerarm_' + s]})
    par['middle_01_r'] = r['hand_r']
    ref = {b: [3.0 * i, 1.0 * i, 100.0 + 7.0 * i] + ID for i, b in enumerate(sorted(set(par) | set(par.values())))}
    P = [10.0, -20.0, 140.0]
    ref[r['clavicle_r']] = P + _qz(90.0)
    ref[r['upperarm_r']] = af.add(P, [0.0, 20.0, 0.0]) + ID
    h, f = ref[r['hand_r']][:3], ref[r['lowerarm_r']][:3]
    ref['middle_01_r'] = af.add(h, af.scale(af.unit(af.sub(h, f)), 20.0)) + ID
    return par, ref, P


def test_aimed_shapes_are_explicit_in_the_owner_bones_reference_frame():
    import collider_autofit as af
    prof = _profile('ParagonAurora')
    r = prof['roles']
    par, ref, P = _arm_rig(prof)
    spec = core.physics_spec(prof, par, ref)
    by = {b['bone']: b for b in spec['bodies']}
    clav = by[r['clavicle_r']]['elements'][0]
    # the builder's aim path, made explicit: centre L / 2 and Z along L in the clavicle's frame, cylinder |L| - 2r
    assert 'aim' not in clav and clav['from_aim'] == r['upperarm_r'] and clav['type'] == 'capsule'
    assert _close(clav['center'], [10.0, 0.0, 0.0]) and _close(af.qrot(clav['rotation_xyzw'], [0, 0, 1]), [1, 0, 0])
    assert abs(clav['length_cm'] - (20.0 - 2 * clav['radius_cm'])) < 1e-9
    hand = by[r['hand_r']]['elements'][0]           # aimed at the finger that continues the forearm
    assert hand['from_aim'] == 'middle_01_r' and abs(hand['length_cm'] - (20.0 - 2 * hand['radius_cm'])) < 1e-9
    assert by[r['hand_l']]['elements'][0]['type'] == 'sphere' and by[r['hand_l']]['elements'][0]['center'] == [0, 0, 0]
    # nothing is left for the builder to resolve: it builds each shape exactly as given (its explicit path first)
    assert all(core.element_shape(e) is not None and 'aim' not in e for b in spec['bodies'] for e in b['elements'])
    # at the reference pose the capsule spans the clavicle to the upper arm, caps inside; posed, it stays rigid in the
    # clavicle's frame (the asset's shape), however far the live upper arm has gone
    (a, b, rad), = core.body_capsules(by[r['clavicle_r']], ref)
    assert _close(a, af.add(P, [0.0, rad, 0.0])) and _close(b, af.add(P, [0.0, 20.0 - rad, 0.0]))
    moved = dict(ref, **{r['upperarm_r']: af.add(P, [30.0, 5.0, -8.0]) + ID})
    assert core.body_capsules(by[r['clavicle_r']], moved) == [(a, b, rad)]
    # an older record's aimed element still poses (from the live aim bone, an approximation)
    old = {'bone': 'c', 'elements': [{'type': 'capsule', 'aim': 'u', 'radius_cm': 1.0}]}
    assert _close(core.body_capsules(old, {'c': [0, 0, 0] + ID, 'u': [0, 0, 10] + ID})[0][1], [0, 0, 9])
    with pytest.raises(ValueError, match='no reference transform'):
        core.physics_spec(prof, par, {k: v for k, v in ref.items() if k != r['upperarm_r']})
    # a shrunk aimed capsule keeps its caps at the joints, as the builder sized it from the radius it was given
    e = dict(clav, radius_cm=clav['radius_cm'] - 1.0)
    assert abs(core.aimed_length(e) - clav['length_cm'] - 2.0) < 1e-9
    # the cloth collision bodies with no skin measurement (aimed at a height share) resolve the same way
    legs = dict(ref, thigh_l=[0, 10, 90] + ID, calf_l=[0, 10, 50] + ID, foot_l=[0, 10, 10] + ID,
                thigh_r=[0, -10, 90] + ID, calf_r=[0, -10, 50] + ID, foot_r=[0, -10, 10] + ID)
    bare = json.loads(json.dumps(prof))
    for seg in bare['colliders']['fitted'].values():
        seg.pop('skin', None)
    cloth = {b['bone']: b['elements'][0] for b in core.cloth_collision_spec(bare, legs)['bodies']}
    calf = cloth['calf_l']
    assert calf['from_aim'] == 'foot_l' and 'aim' not in calf and calf['type'] == 'tapered'
    # its end spheres on the joints: the 40 cm span from the knee to the foot, centred halfway
    assert abs(calf['length_cm'] - 40.0) < 1e-3 and _close(calf['center'], [0, 0, -20])
    assert _close(af.qrot(calf['rotation_xyzw'], [0, 0, 1]), [0, 0, -1])      # +Z toward the foot: the far end


def _ue_rotator_round_trip(q):
    """FQuat4d::Rotator() then FRotator3d::Quaternion() (UE 5.8 Core/Private/Math/UnrealMath.cpp, scalar paths): what
    the builder stores a capsule's rotation as (an FRotator) and what its describe reads back."""
    import math
    X, Y, Z, W = q
    test = Z * X - W * Y
    deg = 180.0 / math.pi
    if test < -0.4999995:
        p, y, r = -90.0, -2.0 * math.atan2(X, W) * deg, 0.0
    elif test > 0.4999995:
        p, y, r = 90.0, 2.0 * math.atan2(X, W) * deg, 0.0
    else:
        p = math.asin(2.0 * test) * deg
        y = math.atan2(2.0 * (W * Z + X * Y), 1.0 - 2.0 * (Y * Y + Z * Z)) * deg
        r = math.atan2(-2.0 * (W * X + Y * Z), 1.0 - 2.0 * (X * X + Y * Y)) * deg
    h = math.pi / 360.0
    sp, cp = math.sin(math.fmod(p, 360.0) * h), math.cos(math.fmod(p, 360.0) * h)
    sy, cy = math.sin(math.fmod(y, 360.0) * h), math.cos(math.fmod(y, 360.0) * h)
    sr, cr = math.sin(math.fmod(r, 360.0) * h), math.cos(math.fmod(r, 360.0) * h)
    return [cr * sp * sy - sr * cp * cy, -cr * sp * cy - sr * cp * sy, cr * cp * sy - sr * sp * cy,
            cr * cp * cy + sr * sp * sy]


@pytest.mark.parametrize('L', [[30.0, 0.0, 0.04], [0.0, 30.0, 0.04], [-30.0, 0.0, 0.04], [30.0, 0.0, 0.03],
                               [0.0, 0.0, 30.0], [0.0, 0.0, -30.0], [0.003, 0.0, 30.0], [12.0, -7.0, 21.0]])
def test_an_aimed_capsule_survives_the_builders_frotator(L):
    import collider_autofit as af
    # an aimed capsule just off the owning bone's X or Y: collider_autofit's q_z_to put the rotation's X on +/-Z, the
    # FRotator's pitch singularity, and the axis read back up to 1.33e-3 off (past AXIS_TOL) on a correctly built asset
    body = {'bone': 'o', 'elements': [{'type': 'capsule', 'aim': 'c', 'radius_cm': 2.0}]}
    core.resolve_aims([body], {'o': [0.0, 0.0, 0.0] + ID, 'c': list(L) + ID})
    want = core.element_shape(body['elements'][0])
    back = _ue_rotator_round_trip(want['rotation_xyzw'])
    Z = [0.0, 0.0, 1.0]
    assert _close(af.qrot(want['rotation_xyzw'], Z), af.unit(L), 1e-12)
    assert _close(af.qrot(back, Z), af.unit(L), 1e-9)
    assert core.shape_matches(want, dict(want, rotation_xyzw=back))
    if L == [30.0, 0.0, 0.04]:                         # the old roll fails this very check: the replay is faithful
        old = _ue_rotator_round_trip(af.q_z_to(L))
        assert not core.shape_matches(want, dict(want, rotation_xyzw=old))


def test_a_body_centre_of_mass_is_volume_weighted():
    import collider_autofit as af
    import math
    # a sphere r 2 at the origin (32/3 pi) and a capsule r 1, cylinder 4, at z 12 (4 pi + 4/3 pi = 16/3 pi): z 4, not
    # the centres' mean 6
    body = {'bone': 'b', 'elements': [
        {'type': 'sphere', 'center': [0.0, 0.0, 0.0], 'radius_cm': 2.0},
        {'type': 'capsule', 'center': [0.0, 0.0, 12.0], 'rotation_xyzw': _qz(30.0), 'radius_cm': 1.0,
         'length_cm': 4.0}]}
    assert _close(core.body_com_local(body), [0.0, 0.0, 4.0])
    qx = [math.sin(math.radians(45)), 0.0, 0.0, math.cos(math.radians(45))]          # 90 deg about X: +Z -> -Y
    assert _close(core.body_com(body, {'b': [1.0, 2.0, 3.0] + qx}), [1.0, -2.0, 3.0])
    assert _close(core.body_com(body, {'b': [1.0, 2.0, 3.0] + qx}), af.add([1.0, 2.0, 3.0], [0.0, -4.0, 0.0]))
    with pytest.raises(ValueError, match='unresolved'):
        core.body_com_local({'bone': 'b', 'elements': [{'type': 'capsule', 'aim': 'c', 'radius_cm': 1.0}]})


def test_the_impact_takes_the_struck_bodys_centre_of_mass():
    import hit_reaction_run
    spec = {'bodies': [{'bone': 'b', 'elements': [
        {'type': 'sphere', 'center': [0.0, 0.0, 0.0], 'radius_cm': 2.0},
        {'type': 'capsule', 'center': [0.0, 0.0, 12.0], 'rotation_xyzw': ID, 'radius_cm': 1.0, 'length_cm': 4.0}]}]}
    (com,) = hit_reaction_run.body_centre(spec, 'b')
    assert _close(com['local'], [0.0, 0.0, 4.0])


def test_two_joints_making_one_control_name_are_refused():
    prof = _profile('ParagonAurora')
    clash = {'constraints': [{'parent': 'a', 'child': 'b_c'}, {'parent': 'a_b', 'child': 'c'}]}
    with pytest.raises(ValueError, match='a -> b_c and a_b -> c'):
        core.initial_updates(prof, clash)
    with pytest.raises(ValueError, match='control name'):          # FName compares case-insensitively
        core.initial_updates(prof, {'constraints': [{'parent': 'A', 'child': 'b'}, {'parent': 'a', 'child': 'B'}]})
    with pytest.raises(ValueError, match='world-space'):           # the pelvis's world control is in the same set
        core.check_control_names({'constraints': [{'parent': 'x', 'child': 'y'}]}, ['x_y'])
    par, _ref, _P = _arm_rig(prof)
    core.initial_updates(prof, core.physics_spec(prof, par))       # a real tree: every name unique


def _pm_spec():
    import collider_autofit as af
    return {'bodies': [
        {'bone': 'pelvis', 'physics_type': 'simulated',
         'elements': [{'type': 'sphere', 'center': [0.0, 0.0, 1.0], 'radius_cm': 10.0}]},
        {'bone': 'hand_l', 'physics_type': 'simulated',
         'elements': [{'type': 'capsule', 'center': [5.0, 0.0, 0.0], 'rotation_xyzw': af.q_z_to([1.0, 0.0, 0.0]),
                       'radius_cm': 2.0, 'length_cm': 6.0, 'from_aim': 'finger'},
                      {'type': 'sphere', 'center': [0.0, 0.0, 0.0], 'radius_cm': 1.0}]}],
        'constraints': [{'child': 'hand_l', 'parent': 'pelvis', 'angular': 'free'}],
        'collide': [{'a': 'hand_l', 'b': 'pelvis'}], 'fixed_step_s': 1.0 / 60.0, 'position_iterations': 32}


def _pm_built():
    import collider_autofit as af
    # the builder's read-back: shapes in another order, the capsule's rotation another one with Z the other way along
    # the same line (and a roll), positions off by float noise
    flipped = af.qmul(af.q_z_to([-1.0, 0.0, 0.0]), _qz(40.0))
    return {'ok': True, 'fixed_step_s': 1.0 / 60.0, 'position_iterations': 32,
            'bodies': [{'bone': 'pelvis', 'physics_type': 'simulated',
                        'shapes': [{'type': 'sphere', 'center': [0.0, 0.0, 1.004], 'rotation_xyzw': _qz(70.0),
                                    'radius_cm': 10.0, 'length_cm': 0.0}]},
                       {'bone': 'hand_l', 'physics_type': 'simulated',
                        'shapes': [{'type': 'sphere', 'center': [0.0, 0.0, 0.0], 'rotation_xyzw': ID,
                                    'radius_cm': 1.0, 'length_cm': 0.0},
                                   {'type': 'capsule', 'center': [5.003, 0.0, -0.002], 'rotation_xyzw': flipped,
                                    'radius_cm': 2.004, 'length_cm': 5.996}]}],
            'constraints': [{'child': 'hand_l', 'parent': 'pelvis', 'angular_free': True}],
            'collisions': [['pelvis', 'hand_l']]}


def test_the_built_asset_must_match_the_spec_shape_for_shape_and_pair_for_pair():
    import hero_onboard
    import collider_autofit as af
    spec, built = _pm_spec(), _pm_built()
    assert hero_onboard.physics_mismatch(spec, built) == []

    def bad(edit):
        b = _pm_built()
        edit(b)
        return hero_onboard.physics_mismatch(spec, b)
    cap = lambda b: b['bodies'][1]['shapes'][1]   # noqa: E731
    assert bad(lambda b: cap(b).update(radius_cm=2.02))                             # resized past 0.01 cm
    assert bad(lambda b: cap(b).update(center=[5.02, 0.0, 0.0]))
    assert bad(lambda b: cap(b).update(rotation_xyzw=af.q_z_to([0.0, 1.0, 0.0])))   # re-aimed
    assert bad(lambda b: cap(b).update(type='sphere'))
    assert bad(lambda b: b['bodies'][1]['shapes'].pop())                            # a shape missing
    assert bad(lambda b: b['bodies'][0]['shapes'].append(dict(b['bodies'][0]['shapes'][0])))   # an extra one
    assert any('no shapes' in d for d in bad(lambda b: b['bodies'][0].pop('shapes')))    # an older describe
    # the enabled set is exactly the spec's: a missing pair fails as an extra one does
    assert any('missing' in d for d in bad(lambda b: b.update(collisions=[])))
    assert bad(lambda b: b['collisions'].append(['hand_l', 'hand_l_x']))
    assert any('collision describe' in d for d in bad(lambda b: b.pop('collisions')))
    # a spec still carrying an unresolved aim cannot be compared shape for shape
    loose = _pm_spec()
    loose['bodies'][1]['elements'][0] = {'type': 'capsule', 'aim': 'finger', 'radius_cm': 2.0}
    assert any('unresolved' in d for d in hero_onboard.physics_mismatch(loose, built))


def test_an_onboarded_spec_older_than_the_generator_is_refused(tmp_path):
    spec = dict(_pm_spec(), position_iterations=core.POSITION_ITERATIONS)
    spec['collide'][0].update(idle_clearance_cm=1.0, ref_clearance_cm=3.0)
    path = tmp_path / 'onboard.json'
    h = {'rig': 'T', 'onboard_record': str(path), 'profile': {'roles': {'pelvis': 'pelvis', 'hand_l': 'hand_l'}}}

    def onboard(s, current=None):
        path.write_text(json.dumps({'physics': {'spec': s}}))
        return core.onboarded_spec(h, current)
    assert onboard(spec) == spec and onboard(spec, json.loads(json.dumps(spec))) == spec
    now = json.loads(json.dumps(spec))
    now['bodies'][1]['elements'][0]['radius_cm'] = 2.5                    # the generator sizes it differently today
    with pytest.raises(RuntimeError, match='stale.*hand_l'):
        onboard(spec, now)
    now = dict(json.loads(json.dumps(spec)), collide=[])                 # ...or no longer lets the pair collide
    with pytest.raises(RuntimeError, match='colliding pairs'):
        onboard(spec, now)
    old = json.loads(json.dumps(spec))
    old['bodies'][1]['elements'][0] = {'type': 'capsule', 'aim': 'finger', 'radius_cm': 2.0}
    with pytest.raises(RuntimeError, match='unresolved'):
        onboard(old)
    old = json.loads(json.dumps(spec))
    del old['collide'][0]['ref_clearance_cm']
    with pytest.raises(RuntimeError, match='reference clearance'):
        onboard(old)


def test_node_text_enables_parent_space_springs_only():
    prof = _profile('ParagonAurora')
    limb = core.limb_setup(prof)
    assert 'StartBone="spine_01"' in limb and 'bCreateWorldSpaceControls=False' in limb
    assert limb.index('Arm_r') < limb.index('Spine') < limb.index('Leg_l') < limb.index('Pelvis')
    assert ('(LimbName="Pelvis",StartBone="pelvis",bIncludeParentBone=False,bCreateWorldSpaceControls=True,'
            'bCreateParentSpaceControls=False,bCreateBodyModifiers=True)') in limb
    assert ('(LimbName="Leg_r",StartBone="thigh_r",bIncludeParentBone=False,bCreateWorldSpaceControls=False,'
            'bCreateParentSpaceControls=False,bCreateBodyModifiers=False)') in limb
    world = core.world_space_control()
    assert 'LinearStrength=%f' % core.PELVIS_LINEAR_HZ in world and 'AngularStrength=%f' % core.PELVIS_ANGULAR_HZ \
        in world and 'bDisableCollision=True' in world
    text = core.parent_space_control()
    assert 'AngularStrength=%f' % core.CONTROL_STRENGTH_HZ in text and 'bDisableCollision=True' in text
    assert 'AngularStrength=10.000000' in core.parent_space_control(10.0)
    assert core.body_modifier() == '(MovementType=Simulated,GravityMultiplier=0.000000)'
