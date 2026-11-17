"""dyn_contact: the cloth penetration verdict passes only measured, shallow cloth on every pawn that simulates it."""
import dyn_contact as dc


def _m(max_cm, ok=True, bone='thigh_r'):
    return {'ok': ok, 'tested': 900, 'clear': 40, 'over_tol': int(max_cm > dc.TOL_CM), 'max_cm': max_cm,
            'worst': {'body_bone': bone}} if ok else {'ok': False, 'error': 'no cloth simulation data'}


def _frames(*per_frame):
    return [{'f': i, 'penetration': {'0': m}} for i, m in enumerate(per_frame)]


def test_shallow_measured_cloth_passes():
    ok, detail = dc.verdict(dc.summary(_frames(_m(0.1), _m(0.3), _m(0.0))), ['0'])
    assert ok and 'max 0.30' in detail


def test_cloth_deep_in_the_thigh_fails_and_says_where():
    ok, detail = dc.verdict(dc.summary(_frames(_m(0.1), _m(2.4), _m(0.2))), ['0'])
    assert not ok and 'max 2.40' in detail and 'frame 1, thigh_r' in detail


def test_frames_before_the_cloth_first_simulates_are_warming_and_later_failures_fail():
    ok, detail = dc.verdict(dc.summary(_frames(_m(0.0, ok=False), _m(0.1), _m(0.2))), ['0'])
    assert ok and '1 warming of 3' in detail
    assert not dc.verdict(dc.summary(_frames(_m(0.1), _m(0.0, ok=False))), ['0'])[0]
    assert not dc.verdict(dc.summary(_frames(_m(0.0, ok=False), _m(0.0, ok=False))), ['0'])[0]   # never simulated


def test_a_frame_that_tested_no_vertex_is_no_measurement():
    empty = dict(_m(0.0), tested=0)
    assert not dc.verdict(dc.summary(_frames(_m(0.1), empty)), ['0'])[0]
    assert not dc.verdict(dc.summary(_frames(empty, empty)), ['0'])[0]


def test_a_simulating_pawn_left_unmeasured_fails():
    assert not dc.verdict(dc.summary(_frames(_m(0.1))), ['0', '1'])[0]
    assert not dc.verdict({}, ['0'])[0]
    assert dc.verdict({}, []) == (True, 'no cloth simulating')


def test_the_limit_is_the_cloths_own_thickness():
    cloth = [{'arm': 'on', 'collision': {'assets': {'a': {'thickness': 1.0}, 'b': {'thickness': 0.6}}}},
             {'arm': 'off'}]
    assert dc.thickness(cloth) == 0.6 and dc.thickness([{'arm': 'off'}]) == dc.MAX_CM
    s = dc.summary(_frames(_m(0.1), _m(0.8)))
    assert dc.verdict(s, ['0'], 1.0)[0] and not dc.verdict(s, ['0'], dc.thickness(cloth))[0]


def test_the_body_is_the_collision_limbs_and_never_a_chain():
    profile = {'roles': {'pelvis': 'pelvis', 'thigh_l': 'thigh_l', 'thigh_r': 'thigh_r', 'calf_l': 'calf_l',
                         'calf_r': 'calf_r', 'spine': ['spine_01'], 'foot_l': 'foot_l', 'foot_r': 'foot_r'}}
    assert dc.limbs(profile) == [('pelvis', 'spine_01'), ('thigh_l', 'calf_l'), ('thigh_r', 'calf_r'), ('calf_l', 'foot_l'),
                                 ('calf_r', 'foot_r')]
    parts = [{'bones': ['hair_01', 'hair_02']}, {'bones': ['breast_l']}, {'bones': ['hair_02']}]
    assert dc.excluded(parts) == ['breast_l', 'hair_01', 'hair_02']
