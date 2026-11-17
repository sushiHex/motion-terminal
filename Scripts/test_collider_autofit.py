"""collider_autofit on a synthetic biped (component space: +X left, +Y forward, +Z up), plus its quaternion helpers."""
import math

import pytest

import collider_autofit as af

ID = [0.0, 0.0, 0.0, 1.0]


def _close(a, b, tol=1e-6):
    return all(abs(x - y) < tol for x, y in zip(a, b))


def test_quaternion_helpers_round_trip():
    x, y, z = [0, 1, 0], [-1, 0, 0], [0, 0, 1]                  # 90 deg yaw
    q = af.q_from_axes(x, y, z)
    assert _close(af.qrot(q, [1, 0, 0]), x) and _close(af.qrot(q, [0, 1, 0]), y)
    d = af.unit([1, 2, 3])
    assert _close(af.qrot(af.q_z_to(d), [0, 0, 1]), d)
    owner = {'translation': [10, 0, 0], 'rotation_xyzw': q}
    pos, rot = af.to_local(owner, [10, 5, 0], q)
    assert _close(pos, [5, 0, 0]) and _close(af.qrot(rot, [1, 0, 0]), [1, 0, 0])


ROLES = {'pelvis': 'pelvis', 'spine': ['spine_01', 'spine_02'], 'neck': 'neck_01', 'head': 'head',
         'thigh_l': 'thigh_l', 'thigh_r': 'thigh_r', 'calf_l': 'calf_l', 'calf_r': 'calf_r', 'foot_l': 'foot_l',
         'foot_r': 'foot_r', 'ball_l': 'ball_l', 'ball_r': 'ball_r'}
REF = {'pelvis': [0, 0, 100], 'spine_01': [0, 0, 115], 'spine_02': [0, 0, 135], 'neck_01': [0, 0, 155],
       'head': [0, 0, 165], 'thigh_l': [10, 0, 95], 'thigh_r': [-10, 0, 95], 'calf_l': [10, 0, 50],
       'calf_r': [-10, 0, 50], 'foot_l': [10, 0, 8], 'foot_r': [-10, 0, 8], 'ball_l': [10, 15, 2],
       'ball_r': [-10, 15, 2],
       # a strip hanging down the front of the chest: 20.6 cm from the spine axis, so 4.6 cm outside a 16 cm capsule
       # (15 + margin), inside particle + rest margin (6 cm): it rests against the torso
       'strip_01': [5, 18, 150], 'strip_02': [5, 20, 138], 'strip_03': [5, 20, 126],
       # a cape far behind the body
       'cape_01': [0, -40, 150], 'cape_02': [0, -42, 130]}
CHAINS = [{'root': 'strip_01', 'bones': ['strip_01', 'strip_02', 'strip_03']},
          {'root': 'cape_01', 'bones': ['cape_01', 'cape_02']}]


def _hier():
    pairs = [('pelvis', None), ('spine_01', 'pelvis'), ('spine_02', 'spine_01'), ('neck_01', 'spine_02'),
             ('head', 'neck_01'), ('spine_02_twist', 'spine_02'), ('strip_01', 'spine_02'), ('strip_02', 'strip_01'),
             ('strip_03', 'strip_02'), ('cape_01', 'spine_02'), ('cape_02', 'cape_01')]
    for s in 'lr':
        pairs += [('thigh_' + s, 'pelvis'), ('calf_' + s, 'thigh_' + s), ('foot_' + s, 'calf_' + s),
                  ('ball_' + s, 'foot_' + s)]
    return {'bones': [b for b, _ in pairs], 'parents': dict(pairs)}


def test_body_segments_and_regions():
    segs, blobs = af.body_segments(ROLES, _hier(), CHAINS)
    names = [s['name'] for s in segs]
    assert names[:5] == ['hips', 'waist', 'torso_spine_01', 'torso_spine_02', 'neck']
    assert 'thigh_l' in names and 'calf_r' in names and [b['name'] for b in blobs] == ['head']
    region = next(s for s in segs if s['name'] == 'torso_spine_02')['region']
    assert region == ['spine_02', 'spine_02_twist']             # helper in; chains and the next role bone out


def _fit(torso_whole=24.0, torso_half=14.0, thigh=(12.0, 12.0), thigh_p50=(9.0, 9.0)):
    seg = lambda b, e, r, p50: {'bone': b, 'end': e, 'radius_cm': r, 'radius_p50_cm': p50, 'samples': 100,  # noqa
                                'length_cm': math.dist(REF[b], REF[e])}
    segs = {'hips': seg('thigh_l', 'thigh_r', 12, 10), 'neck': seg('neck_01', 'head', 6, 5),
            'thigh_l': seg('thigh_l', 'calf_l', thigh[0], thigh_p50[0]),
            'thigh_r': seg('thigh_r', 'calf_r', thigh[1], thigh_p50[1]),
            'calf_l': seg('calf_l', 'foot_l', 8, 7), 'calf_r': seg('calf_r', 'foot_r', 8, 7)}
    for a, b in (('spine_01', 'spine_02'), ('spine_02', 'neck_01')):
        segs['torso_' + a] = seg(a, b, torso_whole, torso_whole * 0.8)
        segs['torso_%s@splitx' % a] = dict(segs['torso_' + a], halves={
            '+1': {'offset_cm': 8.0, 'radius_cm': torso_half, 'samples': 50},
            '-1': {'offset_cm': -8.0, 'radius_cm': torso_half, 'samples': 50}})
    return {'segments': segs, 'blobs': {'head': {'bone': 'head', 'centre_cs': [0, 2, 172], 'radius_cm': 11.0}},
            'chains': {'strip_01': {'half_width_cm': 4.0}, 'cape_01': {'half_width_cm': 20.0}},
            'ref_cs': REF, 'ref_tm': {b: {'translation': p, 'rotation_xyzw': ID} for b, p in REF.items()}}


def _doc():
    return {'roles': ROLES, 'secondary': {'parts': [dict(c) for c in CHAINS]}, 'reasons': []}


def test_wide_torso_splits_into_left_and_right_capsules():
    split = af.build(_doc(), _fit(torso_whole=24, torso_half=14))      # 24 > 1.25 x 14: two capsules
    cape = set(split['colliders']['chains']['cape_01']['collides_with'])
    assert {'torso_spine_02_p', 'torso_spine_02_n'} <= cape and 'torso_spine_02' not in cape
    halves = {s['name']: s for s in split['colliders']['shapes'] if s['name'].startswith('torso_spine_02_')}
    assert halves['torso_spine_02_p']['a_cs'][0] == 8.0 and halves['torso_spine_02_n']['a_cs'][0] == -8.0
    whole = af.build(_doc(), _fit(torso_whole=16, torso_half=14))      # 16 < 17.5: stays one capsule
    assert 'torso_spine_02' in whole['colliders']['chains']['cape_01']['collides_with']
    assert af.build(dict(_doc(), secondary={'parts': []}), _fit())['colliders']['shapes'] == []   # no part, no shape
    stale = {'roles': ROLES, 'chains': [dict(c) for c in CHAINS], 'reasons': []}   # a pre-parts profile
    with pytest.raises(ValueError):
        af.build(stale, _fit())


def test_chest_strip_rests_on_the_torso_and_gets_a_shrunk_rest_capsule():
    doc = af.build(_doc(), _fit(torso_whole=15.0, torso_half=14.0))   # 15 > 1.25 x 14 is false: one capsule
    col = doc['colliders']
    strip = col['chains']['strip_01']['collides_with']
    assert 'torso_spine_02' not in strip                             # the capsule it rests on is excluded...
    rest = next(s for s in col['shapes'] if s['name'] == 'rest_strip_01_torso_spine_02')
    assert rest['name'] in strip and rest['for_chain'] == 'strip_01' and rest['type'] == 'capsule'
    # ...and replaced by the same capsule, shrunk until the strip's nearest rest point clears it
    assert rest['radius_cm'] == pytest.approx(math.hypot(5, 20) - af.PARTICLE_RADIUS_CM - af.REST_GAP_CM, abs=0.01)
    assert rest['a_cs'] == [0, 0, 135] and rest['shrunk_from_cm'] == 16.0
    cape = col['chains']['cape_01']['collides_with']
    assert rest['name'] not in cape                                  # another chain never collides with it
    assert 'local' in rest and 'translation' in rest['local']


def test_a_face_on_a_leaf_whose_tail_was_pruned_is_dropped():
    fit = _fit(torso_whole=15.0, torso_half=14.0)
    fit['chains']['strip_01']['tails'] = [{'bone': 'strip_03', 'length_cm': 8.0, 'end_cs': [5, 18.4, 118],
                                           'local': [0.0, -1.6, -8.0]}]
    fit['chains']['cape_01']['tails'] = [{'bone': 'cape_02', 'length_cm': 3.0, 'end_cs': [0, -42, 127],
                                          'local': [0.0, 0.0, -3.0]}]          # <= 5 cm: pruned by build
    face = {'normal_local': [0.0, 0.0, 1.0], 'flat': 0.1, 'samples': 50}
    fit['chains']['strip_01']['faces'] = {'strip_02': dict(face, next='strip_03'), 'strip_03': dict(face, next='tail')}
    fit['chains']['cape_01']['faces'] = {'cape_01': dict(face, next='cape_02'), 'cape_02': dict(face, next='tail')}
    col = af.build(_doc(), fit)['colliders']['chains']
    assert sorted(col['strip_01']['faces']) == ['strip_02', 'strip_03']     # its tail is kept: a particle child
    assert sorted(col['cape_01']['faces']) == ['cape_01']                   # cape_02's tail is gone: nothing to aim at


def test_reach_and_symmetry():
    doc = af.build(_doc(), _fit(thigh=(12.0, 24.0), thigh_p50=(9.0, 18.0)))
    names = {s['name'] for s in doc['colliders']['shapes']}
    assert 'calf_l' not in names and 'calf_r' not in names           # no chain reaches the shins
    assert any('asymmetric thigh' in r for r in doc['reasons'])


def test_chain_tail_becomes_a_point_of_the_chain():
    fit = _fit(torso_whole=15.0, torso_half=14.0)
    # the strip's mesh hangs 8 cm past strip_03, curling in to 19 cm from the spine axis; the cape's tail is 3 cm
    # end_cs and local agree: strip_03 sits at (5, 20, 126) with an identity reference transform
    fit['chains']['strip_01']['tails'] = [{'bone': 'strip_03', 'length_cm': 8.0, 'end_cs': [5, 18.4, 118],
                                           'local': [0.0, -1.6, -8.0]}]
    fit['chains']['cape_01']['tails'] = [{'bone': 'cape_02', 'length_cm': 3.0, 'end_cs': [0, -42, 127],
                                          'local': [0.0, 0.0, -3.0]}]
    col = af.build(_doc(), fit)['colliders']
    tails = col['chains']['strip_01']['tails']
    assert tails == [{'bone': 'strip_03', 'null': 'MT_tail_strip_03', 'local': [0.0, -1.6, -8.0], 'length_cm': 8.0}]
    assert af.add(REF['strip_03'], tails[0]['local']) == pytest.approx([5, 18.4, 118])   # placed where it was fitted
    assert col['chains']['cape_01']['tails'] == []                  # inside the leaf's own particle: no tail
    # the rest capsule now clears the tail's end, the strip's nearest point to the spine (spine_01 -> spine_02 here)
    rest = next(s for s in col['shapes'] if s['name'] == 'rest_strip_01_torso_spine_01')
    assert rest['radius_cm'] == pytest.approx(math.hypot(5, 18.4) - af.PARTICLE_RADIUS_CM - af.REST_GAP_CM, abs=0.01)


def test_flap_gets_a_feather_sized_particle_or_is_dropped_without_a_tip():
    REF['quill'] = [12, 0, 160]
    try:
        doc = _doc()
        doc['secondary']['parts'] += [{'id': 'quill', 'kind': 'flap', 'root': 'quill', 'bones': ['quill']},
                                      {'id': 'stub', 'kind': 'flap', 'root': 'stub', 'bones': ['stub']}]
        REF['stub'] = [-12, 0, 160]
        fit = _fit()
        fit['chains']['quill'] = {'half_width_cm': 2.0, 'tails': [{'bone': 'quill', 'length_cm': 12.0,
                                                                   'end_cs': [24, 0, 160], 'local': [12.0, 0.0, 0.0]}]}
        fit['chains']['stub'] = {'half_width_cm': 1.0, 'tails': []}
        fit['ref_tm'] = {b: {'translation': p, 'rotation_xyzw': ID} for b, p in REF.items()}
        out = af.build(doc, fit)
        quill = out['colliders']['chains']['quill']
        assert quill['particle_radius_cm'] == pytest.approx(3.0)                # a quarter of its 12 cm tip
        assert quill['tails'][0]['null'] == 'MT_tail_quill'
        assert 'stub' not in out['colliders']['chains']
        assert [p['root'] for p in out['secondary']['parts']].count('stub') == 0  # dropped from the parts, with a reason
        assert any('flap stub dropped' in r for r in out['reasons'])
    finally:
        REF.pop('quill', None)
        REF.pop('stub', None)


def test_a_flap_painted_onto_its_host_is_dropped_and_a_free_one_kept():
    REF['pend'], REF['side'] = [0, 20, 150], [8, 20, 160]
    try:
        doc = _doc()
        doc['secondary']['parts'] += [{'id': 'pend', 'kind': 'flap', 'root': 'pend', 'bones': ['pend']},
                                      {'id': 'side', 'kind': 'flap', 'root': 'side', 'bones': ['side']}]
        fit = _fit()
        tip = lambda b, p: [{'bone': b, 'length_cm': 10.0, 'end_cs': [p[0], p[1], p[2] - 10], 'local': [0, 0, -10.0]}]  # noqa
        # the pendant: 75% of its geometry its own (Khaimera's measured 652 / 872); the side piece: none (0 / 121)
        fit['chains']['pend'] = {'half_width_cm': 4.0, 'tails': tip('pend', REF['pend']),
                                 'skin': {'dominant': 872, 'free': 652}}
        fit['chains']['side'] = {'half_width_cm': 2.0, 'tails': tip('side', REF['side']),
                                 'skin': {'dominant': 121, 'free': 0}}
        fit['ref_tm'] = {b: {'translation': p, 'rotation_xyzw': ID} for b, p in REF.items()}
        out = af.build(doc, fit)
        assert 'pend' in out['colliders']['chains'] and 'side' not in out['colliders']['chains']
        assert [p['root'] for p in out['secondary']['parts']].count('side') == 0
        assert any('flap side dropped: painted onto its host (0 of 121' in r for r in out['reasons'])
        # ...unless its behaviour deforms the host by design (soft tissue): then the same skin keeps it
        doc = _doc()
        doc['secondary']['parts'] += [{'id': 'side', 'kind': 'flap', 'root': 'side', 'bones': ['side'],
                                       'preset': 'soft'}]
        out = af.build(doc, fit)
        assert out['colliders']['chains']['side']['behaviour'] == 'soft_tissue'
        assert out['colliders']['chains']['side']['collides_with'] == []          # collision off: no colliders
    finally:
        REF.pop('pend', None)
        REF.pop('side', None)


def test_flap_free_geometry_counts_its_own_helpers_and_needs_a_majority():
    # the necklace: a pendant 75% free, side pieces painted on (every vertex shared with the chest)
    pend = [{'pend': 1.0}] * 75 + [{'pend': 0.6, 'spine_03': 0.4}] * 25
    dom, free, use = af.flap_vertex_sets(pend, 'pend', {'pend'})
    assert (len(dom), len(free), use) == (100, 75, True)
    side = [{'side': 0.6, 'spine_03': 0.4}] * 50
    assert af.flap_vertex_sets(side, 'side', {'side'})[1] == []
    # counterexamples: a flap split 0.6/0.4 with its OWN twist helper is free, not painted...
    feather = [{'f': 0.6, 'f_twist': 0.4}] * 40
    assert len(af.flap_vertex_sets(feather, 'f', {'f', 'f_twist'})[1]) == 40
    # ...and a small free ornament on a flap whose hanging mass is blended does not steer the tip (no majority)
    mixed = [{'m': 1.0}] * 10 + [{'m': 0.6, 'm_twist': 0.4}] * 90
    assert af.flap_vertex_sets(mixed, 'm', {'m'})[2] is False


def test_spawners_split_flaps_by_behaviour_group():
    chains = {'f1': {'kind': 'flap', 'group': 'pendant', 'collides_with': ['neck'], 'particle_radius_cm': 2.0,
                     'behaviour': 'pendant'},
              'f2': {'kind': 'flap', 'group': 'default', 'collides_with': ['neck'], 'particle_radius_cm': 2.0,
                     'behaviour': 'default'}}
    got = {name: roots for name, roots, _spec in af.spawners(chains)}
    assert got == {'Flaps_default': ['f2'], 'Flaps_pendant': ['f1']}


def test_build_groups_flaps_by_behaviour_so_one_group_never_resizes_another():
    REF.update({'qa': [12, 0, 160], 'qb': [-12, 0, 160]})
    try:
        doc = _doc()
        doc['secondary']['parts'] += [{'id': 'qa', 'kind': 'flap', 'root': 'qa', 'bones': ['qa'], 'preset': 'cloth'},
                                      {'id': 'qb', 'kind': 'flap', 'root': 'qb', 'bones': ['qb'], 'preset': 'strap'}]
        doc['secondary']['behaviours'] = {'qb': 'pendant'}
        fit = _fit()
        for b, end in (('qa', [24, 0, 160]), ('qb', [-24, 0, 160])):
            fit['chains'][b] = {'half_width_cm': 2.0, 'tails': [{'bone': b, 'length_cm': 12.0, 'end_cs': end,
                                                                 'local': [12.0 if b == 'qa' else -12.0, 0.0, 0.0]}]}
        fit['ref_tm'] = {b: {'translation': p, 'rotation_xyzw': ID} for b, p in REF.items()}
        col = af.build(doc, fit)['colliders']['chains']
        assert col['qa']['behaviour'] == col['qa']['group'] == 'feather'
        assert col['qb']['behaviour'] == col['qb']['group'] == 'pendant'             # the profile's pick
        with pytest.raises(ValueError, match='did not find'):
            doc2 = _doc()
            doc2['secondary']['behaviours'] = {'ghost': 'pendant'}
            af.build(doc2, fit)
    finally:
        REF.pop('qa', None)
        REF.pop('qb', None)


def test_flap_groups_write_one_collision_model_that_spawners_read():
    rest = af.rest_flap_name('cloth', 'head')
    chains = {'cape_01': {'kind': 'chain', 'collides_with': ['torso'], 'particle_radius_cm': 5.0},
              'f1': {'kind': 'flap', 'collides_with': [rest, 'neck'], 'particle_radius_cm': 3.0, 'behaviour': 'cloth'},
              'f2': {'kind': 'flap', 'collides_with': ['head', 'neck'], 'particle_radius_cm': 2.0, 'behaviour': 'cloth'},
              'h1': {'kind': 'flap', 'collides_with': ['head'], 'particle_radius_cm': 1.5, 'behaviour': 'hair'}}
    af.flap_groups(chains, {'cape_01': 'cloth', 'f1': 'cloth', 'f2': 'cloth', 'h1': 'hair'})
    # every cloth flap now states the group's union (full head dropped for its rest copy) and smallest radius ...
    assert chains['f1']['collides_with'] == chains['f2']['collides_with'] == ['neck', rest]
    assert chains['f1']['particle_radius_cm'] == chains['f2']['particle_radius_cm'] == 2.0
    assert chains['cape_01']['collides_with'] == ['torso']               # chains keep their own model
    got = {name: (roots, spec) for name, roots, spec in af.spawners(chains)}
    assert got['Chain_cape_01'][0] == ['cape_01'] and got['Flaps_cloth'][0] == ['f1', 'f2']
    assert got['Flaps_hair'][1]['collides_with'] == ['head']
    chains['f2']['particle_radius_cm'] = 4.0                             # ... and spawners refuse a member that differs
    with pytest.raises(ValueError):
        af.spawners(chains)
    chains['f2']['particle_radius_cm'] = 2.0
    chains['f2']['behaviour'] = 'strap'                                  # same group, a different behaviour: stale
    with pytest.raises(ValueError):
        af.spawners(chains)


def test_posed_distance_matches_the_reference_pose():
    """A shape placed by its owner's reference transform is where the reference pose has it (validation relies on it)."""
    col = af.build(_doc(), _fit(torso_whole=15.0, torso_half=14.0))['colliders']
    s = next(s for s in col['shapes'] if s['name'] == 'torso_spine_02')
    p = [3.0, 25.0, 140.0]
    assert af.posed_distance(p, s, list(REF['spine_02']) + ID) == pytest.approx(af._shape_distance(p, s), abs=1e-6)


def test_capsule_local_transform_maps_axis():
    doc = af.build(_doc(), _fit(torso_whole=15.0, torso_half=14.0))
    caps = [s for s in doc['colliders']['shapes'] if s['type'] == 'capsule' and s['a_cs'] != s['b_cs']]
    for s in caps:
        axis = af.unit(af.sub(s['b_cs'], s['a_cs']))
        assert _close(af.qrot(s['local']['rotation_xyzw'], [0, 0, 1]), axis, 1e-4)   # identity owner transform, so local == cs
        assert s['local']['length_cm'] == pytest.approx(math.dist(s['a_cs'], s['b_cs']), abs=0.01)


def _strip(width, thick, length=20.0, n=8, tilt_deg=0.0, y0=0.0):
    """Vertices of a strip hanging down -Z from (0, y0, 0): width along X, thickness along Y (tilted about Z)."""
    t = math.radians(tilt_deg)
    pts = []
    for i in range(n):
        for j in range(n):
            for k in (-1, 1):
                x, y, z = (i / (n - 1) - 0.5) * width, k * thick / 2.0, -(j / (n - 1)) * length
                pts.append([x * math.cos(t) - y * math.sin(t), y0 + x * math.sin(t) + y * math.cos(t), z])
    return pts


def test_sym3_eigen_recovers_a_rotated_diagonal():
    lam, vec = af.sym3_eigen([[2.0, 1.0, 0.0], [1.0, 2.0, 0.0], [0.0, 0.0, 5.0]])
    assert lam == pytest.approx([1.0, 3.0, 5.0], abs=1e-9)
    assert abs(abs(af.dot(vec[0], af.unit([1, -1, 0]))) - 1.0) < 1e-9


def test_face_normal_of_a_flat_strip_points_away_from_the_host():
    pts = _strip(8.0, 0.4, tilt_deg=20.0, y0=10.0)                 # in front of the body (+Y)
    n, flat = af.face_normal(pts, [1.0] * len(pts), [0, 10, 0], [0, 10, -20], [0, 0, 0])
    assert flat < af.FACE_MAX_FLAT
    t = math.radians(20.0)
    assert _close(n, [-math.sin(t), math.cos(t), 0.0], 1e-6)       # the tilted thickness axis, signed outward (+Y)
    back = _strip(8.0, 0.4, y0=-10.0)                              # behind the body: outward is -Y
    n, _ = af.face_normal(back, [1.0] * len(back), [0, -10, 0], [0, -10, -20], [0, 0, 0])
    assert _close(n, [0, -1, 0], 1e-6)


def test_face_normal_refuses_tubes_lines_and_too_few_vertices():
    tube = _strip(4.0, 4.0)
    n, why = af.face_normal(tube, [1.0] * len(tube), [0, 0, 0], [0, 0, -20], [0, -10, 0])
    assert n is None and 'not flat' in why
    line = [[0.0, 0.0, -float(z)] for z in range(40)]
    n, why = af.face_normal(line, [1.0] * len(line), [0, 0, 0], [0, 0, -20], [0, -10, 0])
    assert n is None and 'line' in why
    few = _strip(8.0, 0.4, n=2)
    n, why = af.face_normal(few, [1.0] * len(few), [0, 0, 0], [0, 0, -20], [0, -10, 0])
    assert n is None and 'vertices' in why


def _cone(r_near, r_far, length=40.0, rings=20, around=36, offset=(0.0, 0.0)):
    """Skin points around an axis parallel to z (shifted by offset) from 0 to -length, tapering r_near -> r_far."""
    pts = []
    for i in range(rings + 1):
        z = -length * i / rings
        r = r_near + (r_far - r_near) * i / rings
        pts += [[offset[0] + r * math.cos(2 * math.pi * k / around), offset[1] + r * math.sin(2 * math.pi * k / around),
                 z] for k in range(around)]
    return pts


def _uncovered(pts, cap):
    """The most any point sticks out of a tapered capsule (cm; <= 0 when every point is inside)."""
    a, b = cap['near'], cap['far']
    rn, rf = cap['radius_cm']
    worst = -1e9
    for p in pts:
        t, q = af.seg_closest(p, a, b)
        worst = max(worst, math.dist(p, q) - (rn + (rf - rn) * t))
    return worst


def test_the_skin_capsule_covers_a_tapered_limb_end_to_end():
    skin = _cone(9.0, 5.0)
    cap = af.skin_capsule(skin, [0, 0, 0], [0, 0, -40])
    assert 8.8 < cap['radius_cm'][0] < 9.6 and 4.8 < cap['radius_cm'][1] < 5.6        # the taper, per end
    assert _uncovered(skin, cap) < 0.15                                                  # and it covers it


def test_a_limb_off_its_bone_is_centred_on_the_limb_not_the_bone():
    # the reproduction: a 10 cm limb 3 cm off its bone read 9.6 cm about the bone
    skin = _cone(10.0, 10.0, offset=(3.0, 0.0))
    cap = af.skin_capsule(skin, [0, 0, 0], [0, 0, -40])
    assert abs(cap['near'][0] - 3.0) < 0.3 and abs(cap['far'][0] - 3.0) < 0.3
    assert 9.8 < cap['radius_cm'][0] < 10.6 and _uncovered(skin, cap) < 0.15


def _oval(rx, ry, length=40.0, rings=10, around=48):
    """An elliptic cylinder along -Z from the origin: wide in x, narrow in y."""
    return [[rx * math.cos(2 * math.pi * k / around), ry * math.sin(2 * math.pi * k / around), -length * i / rings]
            for i in range(rings + 1) for k in range(around)]


def _intrusion(pts, cap, thickness):
    """The most any point sits inside a tapered capsule grown by thickness (cm; <= 0 when all clear)."""
    return max(-_dist_out for _dist_out in
               (math.dist(p, af.seg_closest(p, cap['near'], cap['far'])[1]) - thickness -
                (cap['radius_cm'][0] + (cap['radius_cm'][1] - cap['radius_cm'][0]) *
                 af.seg_closest(p, cap['near'], cap['far'])[0]) for p in pts))


def test_a_garment_resting_on_an_oval_limbs_narrow_side_is_never_pushed_off_it():
    # Aurora's dress stood off her hip in a shelf where its simulation began; the cover of an oval
    # limb overshoots its narrow side, where the dress hangs
    skin = _oval(12.0, 8.0)
    dress = [[x, 8.5, -40.0 * i / 10] for i in range(11) for x in (-3.0, 0.0, 3.0)]   # on the narrow side, 0.5 off
    plain = af.skin_capsule(skin, [0, 0, 0], [0, 0, -40])
    assert _intrusion(dress, plain, 1.0) > 4.0                    # the cover alone would shove it 4.4 cm out
    cap = af.skin_capsule(skin, [0, 0, 0], [0, 0, -40], garment=[(p, 1.0) for p in dress])
    # now no rest point is more than its thickness inside it: held off by its thickness, plus one of error
    assert _intrusion(dress, cap, 1.0) <= 2.0 + 0.01
    assert cap['garment']['points'] > 0 and cap['garment']['unmet_cm'] == 0.0 and cap['garment']['inside_cm'] <= 1.0
    # shrinking on the bone alone (to 7.5) would bare 4.5 cm of the wide sides; moved off the garment and given the
    # thickness it bares under 2, and stands no further off the skin anywhere than the cover did: never 16 cm behind
    assert cap['garment']['moved_cm'][0] > 0.5 and cap['garment']['exposed_cm'] < 2.0
    assert cap['near'][1] - cap['radius_cm'][0] > -12.1


def test_a_garment_the_skin_can_balance_is_pushed_no_more_than_the_skin_is_exposed():
    # a round limb with the dress 0.5 cm in front of it: neither error need reach the thickness, and they meet
    skin = _cone(9.0, 9.0)
    dress = [[x, 9.5, -40.0 * i / 10] for i in range(1, 10) for x in (-2.0, 0.0, 2.0)]
    cap = af.skin_capsule(skin, [0, 0, 0], [0, 0, -40], garment=[(p, 1.0) for p in dress])
    g = cap['garment']
    assert g['inside_cm'] < 1.0 and g['exposed_cm'] <= g['inside_cm'] + 0.05


def test_a_garment_far_from_the_limb_leaves_the_cover_alone():
    skin = _cone(9.0, 5.0)
    far = [[30.0, 0.0, -20.0], [0.0, -30.0, -5.0]]
    assert af.skin_capsule(skin, [0, 0, 0], [0, 0, -40], garment=[(p, 1.0) for p in far])['radius_cm'] == \
        af.skin_capsule(skin, [0, 0, 0], [0, 0, -40])['radius_cm']


def test_a_garment_inside_the_skin_never_shrinks_the_collider_below_the_skins_core():
    # the art's own overlap (cloth modelled into the skin) is the penetration measure's allowance, not the collider's
    skin = _cone(9.0, 9.0)
    sunk = [[0.0, 2.0, -20.0], [3.0, 0.0, -5.0]]
    cap = af.skin_capsule(skin, [0, 0, 0], [0, 0, -40], garment=[(p, 1.0) for p in sunk])
    assert cap['radius_cm'][0] > 8.5 and cap['radius_cm'][1] > 8.5


def test_no_limb_is_read_from_too_few_directions():
    assert af.skin_capsule([], [0, 0, 0], [0, 0, -40]) is None
    assert af.skin_capsule([[30.0, 0.0, -8.0]], [0, 0, 0], [0, 0, -40]) is None
