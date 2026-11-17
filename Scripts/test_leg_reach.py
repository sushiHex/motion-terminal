import math
import random
import re

import pytest

import leg_reach as lr


def _norm(v):
    return math.sqrt(sum(x * x for x in v))


def _sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def _leg(hip, foot, target, bend=20.0, thigh=45.0):
    """(hip, knee, foot, target): a thigh of `thigh` cm and the shin that reaches the foot with the knee bent `bend`
    deg (0 = straight), the knee forward (+y) of the hip-foot line."""
    hip, foot = tuple(float(c) for c in hip), tuple(float(c) for c in foot)
    span = _sub(foot, hip)
    d = _norm(span)
    th = math.radians(bend)
    shin = -thigh * math.cos(th) + math.sqrt(d * d - (thigh * math.sin(th)) ** 2)
    alpha = math.acos(max(-1.0, min(1.0, (thigh * thigh + d * d - shin * shin) / (2 * thigh * d))))
    u = tuple(x / d for x in span)
    fwd = _sub((0.0, 1.0, 0.0), tuple(x * u[1] for x in u))
    n = tuple(x / _norm(fwd) for x in fwd)
    knee = tuple(h + thigh * (math.cos(alpha) * a + math.sin(alpha) * b) for h, a, b in zip(hip, u, n))
    return (hip, knee, foot, tuple(float(c) for c in target))


def _r(leg):
    return lr.reach(lr.Numbers, *leg[:3])


def _reach_after(leg, d):
    return _norm(_sub(tuple(h + x for h, x in zip(leg[0], d)), leg[3])), _r(leg)


def test_a_leg_may_straighten_to_ten_degrees_and_no_further():
    bent = _leg((0, 0, 90), (0, 0, 2), (0, 0, 2), bend=20.0)
    a, c = _norm(_sub(bent[1], bent[0])), _norm(_sub(bent[2], bent[1]))
    assert abs(_r(bent) - math.sqrt(a * a + c * c + 2 * a * c * math.cos(math.radians(10)))) < 1e-9
    straight = _leg((0, 0, 90), (0, 0, 2), (0, 0, 2), bend=4.0)          # animated straighter than 10: its own chord
    assert abs(_r(straight) - 88.0) < 1e-9


def _dykstra(legs, n=20000):
    """The least-norm point of the two balls' intersection by Dykstra's alternating projections (slow, independent)."""
    balls = [(_sub(leg[3], leg[0]), _r(leg)) for leg in legs]
    x, p, q = (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)

    def onto(v, c, r):
        e = _sub(v, c)
        n_ = _norm(e)
        return v if n_ <= r else tuple(ci + ei * r / n_ for ci, ei in zip(c, e))
    for _ in range(n):
        y = onto(tuple(a + b for a, b in zip(x, p)), *balls[0])
        p = tuple(a + b - c for a, b, c in zip(x, p, y))
        x = onto(tuple(a + b for a, b in zip(y, q)), *balls[1])
        q = tuple(a + b - c for a, b, c in zip(y, q, x))
    return x


STAND = [_leg((10, 0, 90), (10, 2, 2), (10, 2, 2)), _leg((-10, 0, 90), (-10, 2, 2), (-10, 2, 2))]


def test_no_hit_no_move():
    assert _norm(lr.solve(STAND)) < 1e-9


def test_a_pelvis_pushed_toward_the_feet_stays_put():
    legs = [_leg((10, 0, 80), (10, 2, -8), (10, 2, 2)), _leg((-10, 0, 80), (-10, 2, -8), (-10, 2, 2))]
    assert _norm(lr.solve(legs)) < 1e-9


def test_one_leg_out_of_reach_moves_back_exactly_onto_its_reach():
    # the pelvis shoved 6 cm away from the left foot (+x, up), the right leg with slack to spare
    legs = [_leg((16, 0, 93), (16, 2, 5), (10, 2, 2)), _leg((-7, 0, 85), (-7, 2, -5), (-10, 2, 2))]
    assert _reach_after(legs[0], (0, 0, 0))[0] > _r(legs[0])
    d = lr.solve(legs)
    got, reach = _reach_after(legs[0], d)
    assert abs(got - reach) < 1e-6
    assert _reach_after(legs[1], d)[0] <= _reach_after(legs[1], d)[1] + lr.TOL
    assert _norm(_sub(d, _dykstra(legs))) < 1e-3


def test_the_least_offset_matches_an_independent_solver():
    rnd = random.Random(7)
    for _ in range(300):
        legs = []
        for side in (1, -1):
            hip = (side * 10 + rnd.uniform(-6, 6), rnd.uniform(-6, 6), 90 + rnd.uniform(-8, 8))
            target = (side * 10 + rnd.uniform(-3, 3), rnd.uniform(-3, 3), rnd.uniform(0, 4))
            # the leg in the incoming pose: a foot 82..89 cm from the hip, the knee bent 0..40 deg
            r = rnd.uniform(82, 89)
            foot = (hip[0], hip[1], hip[2] - r)
            legs.append(_leg(hip, foot, target, bend=rnd.uniform(0.0, 40.0)))
        d = lr.solve(legs)
        ref = _dykstra(legs, 4000)
        for leg in legs:
            got, reach = _reach_after(leg, d)
            assert got <= reach + 2 * lr.TOL
        assert _norm(d) <= _norm(ref) + 1e-3 and _norm(_sub(d, ref)) < 5e-3, (legs, d, ref)


def test_no_offset_reaches_both_feet_then_the_point_between_them():
    legs = [_leg((0, 0, 90), (0, 0, 10), (300, 0, 0)), _leg((0, 0, 90), (0, 0, 10), (-300, 0, 0))]
    d = lr.solve(legs)
    assert all(math.isfinite(x) for x in d) and abs(d[0]) < 1e-6


class _Ctl:
    """A RigVM controller that records what the rig backend asks for."""

    def __init__(self):
        self.nodes, self.defaults, self.links = {}, {}, {}

    def add_unit_node_from_struct_path(self, struct, method, pos, name, undo, python):
        self.nodes[name] = struct
        return name

    def set_pin_default_value(self, pin, text, *a):
        self.defaults[pin] = text
        return True

    def add_link(self, src, dst, undo):
        self.links[dst] = src
        return True


class _Unreal:
    Vector2D = staticmethod(lambda x, y: (x, y))


class _RefusingCtl(_Ctl):
    """A controller that refuses every pin default, as RigVM does for a pin an engine rename removed."""

    def set_pin_default_value(self, pin, text, *a):
        return False


def test_a_refused_pin_default_raises():
    m = lr.RigOps(_RefusingCtl(), _Unreal)
    with pytest.raises(RuntimeError, match='could not set'):
        m.bone('pelvis')
    with pytest.raises(RuntimeError, match='could not set'):
        m.vadd((1.0, 2.0, 3.0), (0.0, 0.0, 0.0))


def _evaluate(ctl, pin, bones):
    """Interpret the recorded graph: the value at a .Result (or a bone's .Transform.Translation) pin."""
    node, _, out = pin.partition('.')
    struct = ctl.nodes[node]
    if struct.endswith('RigUnit_GetTransform'):
        assert out == 'Transform.Translation'
        return bones[re.search(r'Name="([^"]+)"', ctl.defaults[node + '.Item']).group(1)]
    unit = struct[len(lr.MATH):]
    op, pins = next((k, v[1]) for k, v in lr.RIG_OPS.items() if v[0] == unit)
    args = []
    for p in pins:
        key = '%s.%s' % (node, p)
        if key in ctl.links:
            args.append(_evaluate(ctl, ctl.links[key], bones))
        else:
            text = ctl.defaults[key]
            nums = [float(x) for x in re.findall(r'-?[\d.]+(?:e-?\d+)?', text)]
            args.append(tuple(nums) if text.startswith('(') else nums[0])
    return getattr(lr.Numbers, op)(*args)


def test_the_rig_computes_the_same_offset():
    legs = [_leg((16, 0, 93), (16, 2, 5), (10, 2, 2)), _leg((-7, 1, 88), (-7, 1, 3), (-11, 2, 2))]
    names = lr.rig_legs(SPEC_LEGS)
    bones = {n: p for leg, ns in zip(legs, names) for n, p in zip(ns, leg)}
    ctl = _Ctl()
    m = lr.RigOps(ctl, _Unreal)
    out = lr.offset(m, [tuple(m.bone(b) for b in ns) for ns in names])
    got = _evaluate(ctl, out, bones)
    assert _norm(_sub(got, lr.solve(legs))) < 1e-9 and _norm(got) > 1.0


class _Pin:
    def __init__(self, value):
        self.value = value

    def get_default_value(self):
        return self.value


class _Bp:
    """A Control Rig blueprint over a _Ctl, with a skeleton's bones."""

    def __init__(self, path, bones):
        self.path, self.ctl, self.compiled = path, _Ctl(), False
        self.bones = [type('K', (), {'name': b})() for b in bones]
        self.hierarchy = type('H', (), {'get_bones': lambda _s: self.bones})()
        self.ctl.get_graph = lambda: type('G', (), {'find_pin': lambda _s, p: _Pin(
            self.ctl.defaults.get(p, {'.Space': 'GlobalSpace', '.bPropagateToChildren': 'True',
                                      '.Weight': '1.000000'}['.' + p.rsplit('.', 1)[1]]))})()

    def get_path_name(self):
        return self.path + '.' + self.path.rsplit('/', 1)[1]

    def get_hierarchy_controller(self):
        return type('HC', (), {'import_bones': lambda *a: None})()

    def get_controller_by_name(self, name):
        return self.ctl

    def recompile_vm(self):
        self.compiled = True


def _editor(bones):
    made = {}

    class Factory:
        @staticmethod
        def create_new_control_rig_asset(path, modular):
            made['bp'] = _Bp(path, bones)
            return made['bp']
    return type('U', (), {'ControlRigBlueprintFactory': Factory, 'load_asset': staticmethod(lambda p: p),
                          'Vector2D': staticmethod(lambda x, y: (x, y))}), made


SPEC_LEGS = [{'thigh': 'thigh_%s' % s, 'calf': 'calf_%s' % s, 'foot': 'foot_%s' % s, 'target': 'ik_foot_%s' % s}
             for s in 'lr']


def test_author_builds_a_rig_that_moves_the_pelvis_by_the_offset():
    bones = ['root', 'pelvis', 'thigh_l', 'calf_l', 'foot_l', 'thigh_r', 'calf_r', 'foot_r', 'ik_foot_l', 'ik_foot_r']
    ue, made = _editor(bones)
    lr.author(ue, '/Game/MT/X/CR_MT_XLegReach', '/Game/X/Skel', 'pelvis', SPEC_LEGS)
    ctl = made['bp'].ctl
    put = next(n for n in ctl.nodes if n.endswith('SetPelvis'))
    assert made['bp'].compiled and ctl.defaults[put + '.Item'] == '(Type=Bone,Name="pelvis")'
    assert ctl.links[put + '.ExecutePin'].endswith('Forwards.ExecutePin')
    legs = [_leg((16, 0, 93), (16, 2, 5), (10, 2, 2)), _leg((-7, 1, 88), (-7, 1, -4), (-11, 2, 2))]
    pos = {n: p for leg, ns in zip(legs, lr.rig_legs(SPEC_LEGS)) for n, p in zip(ns, leg)}
    assert _norm(lr.solve(legs)) > 1.0
    pos['pelvis'] = (0.0, 0.0, 95.0)
    moved = _evaluate(ctl, ctl.links[put + '.Value'], pos)
    assert _norm(_sub(_sub(moved, pos['pelvis']), lr.solve(legs))) < 1e-9


def test_author_refuses_a_skeleton_without_the_bones():
    ue, _made = _editor(['root', 'pelvis', 'thigh_l', 'calf_l', 'foot_l', 'thigh_r', 'calf_r', 'foot_r'])
    try:
        lr.author(ue, '/Game/MT/X/CR', '/Game/X/Skel', 'pelvis', SPEC_LEGS)
        raise AssertionError('no refusal')
    except RuntimeError as e:
        assert "['ik_foot_l', 'ik_foot_r']" in str(e)


def test_rig_legs_reads_the_spec_entries():
    spec = [{'thigh': 'thigh_l', 'calf': 'calf_l', 'foot': 'foot_l', 'target': 'ik_foot_l'}]
    assert lr.rig_legs(spec) == [('thigh_l', 'calf_l', 'foot_l', 'ik_foot_l')]
