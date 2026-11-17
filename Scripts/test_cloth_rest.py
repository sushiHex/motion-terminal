"""cloth_rest: the garment at rest against its colliders (pure math; the editor gather is exercised by the run)."""
import cloth_rest

TMS = {'thigh': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0]}
# a thigh along +Z from the bone, 10 cm near, 6 cm far, 40 cm long (centre 20 cm up)
THIGH = {'bone': 'thigh', 'elements': [{'type': 'tapered', 'center': [0.0, 0.0, 20.0],
                                         'rotation_xyzw': [0.0, 0.0, 0.0, 1.0], 'length_cm': 40.0,
                                         'radius_cm': 10.0, 'radius_end_cm': 6.0}]}


def _asset(points, maxd, th=1.0):
    return {'xyz': [c for p in points for c in p], 'max_distance': maxd, 'thickness_cm': th}


def test_a_movable_particle_deeper_than_its_thickness_inside_a_collider_is_a_push():
    rep = cloth_rest.clearance(_asset([[8.5, 0.0, 0.0], [30.0, 0.0, 0.0]], [5.0, 5.0]), [THIGH], TMS)
    assert rep == {'thigh': {'movable': 1, 'pinned': 0, 'max_cm': 2.5, 'limit_cm': 1.0}}   # 10 - 8.5 + 1 cm thickness
    assert not cloth_rest.verdict({'dress': {'added': rep}})[0]


def test_a_particle_within_its_thickness_of_the_surface_is_within_the_error_budget():
    # 10.5 cm off a 10 cm collider: Chaos holds it 1 cm off, a 0.5 cm push, as the gate allows a 1 cm sink
    rep = {'dress': {'added': cloth_rest.clearance(_asset([[10.5, 0.0, 0.0]], [5.0]), [THIGH], TMS)}}
    assert rep['dress']['added']['thigh'] == {'movable': 0, 'pinned': 0, 'max_cm': 0.5, 'limit_cm': 1.0}
    assert cloth_rest.verdict(rep) == (True, 0.5, 'dress/added/thigh')


def test_a_pinned_particle_is_counted_but_never_the_verdict():
    rep = {'dress': {'added': cloth_rest.clearance(_asset([[8.0, 0.0, 0.0]], [0.0]), [THIGH], TMS)}}
    assert rep['dress']['added']['thigh']['pinned'] == 1
    assert cloth_rest.verdict(rep)[0]


def test_the_taper_and_the_end_caps_count():
    # 8 cm off the axis at the far end (radius 6 there): clear; 1 cm beyond the near end on the axis: inside the cap
    rep = cloth_rest.clearance(_asset([[8.0, 0.0, 40.0], [0.0, 0.0, -1.0]], [1.0, 1.0], th=0.0), [THIGH], TMS)
    assert rep == {'thigh': {'movable': 1, 'pinned': 0, 'max_cm': 9.0, 'limit_cm': 0.0}}
