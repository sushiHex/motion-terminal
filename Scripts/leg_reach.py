"""Leg reach: the least pelvis move that keeps both planted feet reachable, as ONE formula with two backends.

A hit pushes the simulated pelvis; the legs are not simulated, so a planted foot (its IK target, the incoming foot
copied before the reaction) can end up beyond the leg's reach, and LegIK then straightens the knee and drags the foot
(2026-10-30: 1.8 cm Aurora, 5.8 cm Khaimera). Between the reaction and LegIK, the pelvis (and its
whole subtree) moves by the shortest offset d with every leg within its reach:

    |hip_i + d - target_i| <= R_i,   R_i = sqrt(a^2 + c^2 + 2ac cos theta_i)

a, c the thigh and shin in the incoming pose (its own lengths, so nothing stretches), theta_i = min(the animated knee
bend, MIN_FLEX_DEG): a leg may straighten to 10 deg of bend, never lock, and one animated straighter keeps its own.
A hard "never straighten past the animation" left Khaimera no room: his pelvis dropped and the other knee bent 32 deg.
d = 0 whenever the targets are in reach (the identity without a hit); the knee keeps the
animation's plane (LegIK takes it from the incoming pose). Two legs: the constraint set is the intersection of two
balls centred c_i = target_i - hip_i, and the least offset is the origin's projection onto it, in closed form (to
TOL): the projection onto one ball when it lies in the other, else the nearest point of the circle where both
boundaries meet. Disjoint balls (no pelvis offset reaches both feet) fall back to the point between them on their axis.

`offset(m, legs)` is written against an op set `m`: `Numbers` evaluates it (the tests, the analysis), `RigOps`
emits it as RigVM nodes into a Control Rig (author()), so the rig and its tests are the same arithmetic.
"""
import math

EPS = 1e-4          # cm: a length guard against division by zero
TOL = 1e-3          # cm: the slack "inside a ball" is judged with
MIN_FLEX_DEG = 10.0  # a leg may straighten to this much knee bend (or its animated bend, if straighter), never lock
COS_MIN_FLEX = math.cos(math.radians(MIN_FLEX_DEG))


def _proj(m, c, r):
    """The point of the ball (c, r) nearest the origin: c (1 - r/|c|)+."""
    return m.vscale(c, m.fmax(0.0, m.fsub(1.0, m.fdiv(r, m.fmax(m.vlen(c), EPS)))))


def _inside(m, x, c, r):
    return m.le(m.vlen(m.vsub(x, c)), m.fadd(r, TOL))


def reach(m, hip, knee, foot):
    """R: the hip-foot distance of this leg's thigh and shin at min(its knee bend, MIN_FLEX_DEG)."""
    u, v = m.vsub(knee, hip), m.vsub(foot, knee)
    a, c = m.vlen(u), m.vlen(v)
    ac = m.fmul(a, c)
    cos = m.fmax(m.fdiv(m.vdot(u, v), m.fmax(ac, EPS)), COS_MIN_FLEX)     # cos of the bend, 1 when straight
    return m.fsqrt(m.fadd(m.fadd(m.fmul(a, a), m.fmul(c, c)), m.fmul(m.fmul(2.0, ac), cos)))


def offset(m, legs):
    """The pelvis offset for two legs, each (hip, knee, foot, target) positions in one space (component / rig
    global)."""
    (h1, k1, f1, t1), (h2, k2, f2, t2) = legs
    c1, c2 = m.vsub(t1, h1), m.vsub(t2, h2)
    r1, r2 = reach(m, h1, k1, f1), reach(m, h2, k2, f2)
    p1, p2 = _proj(m, c1, r1), _proj(m, c2, r2)
    # the circle: along the axis u at a from c1, radius h, in the plane normal to u
    e = m.vsub(c2, c1)
    d = m.fmax(m.vlen(e), EPS)
    u = m.vscale(e, m.fdiv(1.0, d))
    a = m.fdiv(m.fadd(m.fmul(d, d), m.fsub(m.fmul(r1, r1), m.fmul(r2, r2))), m.fmul(2.0, d))
    q = m.vadd(c1, m.vscale(u, a))
    h = m.fsqrt(m.fmax(0.0, m.fsub(m.fmul(r1, r1), m.fmul(a, a))))
    w = m.vsub(m.vscale(u, m.vdot(q, u)), q)                # toward the origin, within the circle's plane
    pc = m.vadd(q, m.vscale(w, m.fdiv(h, m.fmax(m.vlen(w), EPS))))
    return m.vselect(_inside(m, p1, c2, r2), p1, m.vselect(_inside(m, p2, c1, r1), p2, pc))


class Numbers:
    """The op set over floats and 3-tuples."""
    vadd = staticmethod(lambda a, b: tuple(x + y for x, y in zip(a, b)))
    vsub = staticmethod(lambda a, b: tuple(x - y for x, y in zip(a, b)))
    vscale = staticmethod(lambda v, f: tuple(x * f for x in v))
    vdot = staticmethod(lambda a, b: sum(x * y for x, y in zip(a, b)))
    vlen = staticmethod(lambda v: sum(x * x for x in v) ** 0.5)
    vselect = staticmethod(lambda c, a, b: a if c else b)
    fadd = staticmethod(lambda a, b: a + b)
    fsub = staticmethod(lambda a, b: a - b)
    fmul = staticmethod(lambda a, b: a * b)
    fdiv = staticmethod(lambda a, b: a / b)
    fmax = staticmethod(max)
    fsqrt = staticmethod(lambda a: a ** 0.5)
    le = staticmethod(lambda a, b: a <= b)


def solve(legs):
    """offset() evaluated: legs [(hip, knee, foot, target)] x2 -> (dx, dy, dz)."""
    return offset(Numbers, [tuple(tuple(float(c) for c in p) for p in leg) for leg in legs])


# --- the rig backend ---------------------------------------------------------------------------------------------

MATH = '/Script/RigVM.RigVMFunction_Math'
# op -> (unit, input pins in argument order); every one outputs .Result (RigVMFunction_MathVector.h / MathFloat.h)
RIG_OPS = {
    'vadd': ('VectorAdd', ('A', 'B')), 'vsub': ('VectorSub', ('A', 'B')), 'vscale': ('VectorScale', ('Value', 'Factor')),
    'vdot': ('VectorDot', ('A', 'B')), 'vlen': ('VectorLength', ('Value',)),
    'vselect': ('VectorSelectBool', ('Condition', 'IfTrue', 'IfFalse')),
    'fadd': ('FloatAdd', ('A', 'B')), 'fsub': ('FloatSub', ('A', 'B')), 'fmul': ('FloatMul', ('A', 'B')),
    'fdiv': ('FloatDiv', ('A', 'B')), 'fmax': ('FloatMax', ('A', 'B')), 'fsqrt': ('FloatSqrt', ('Value',)),
    'le': ('FloatLessEqual', ('A', 'B')),
}
CR_UNIT = '/Script/ControlRig.'


class Pin(str):
    """A rig pin path: an argument that is a Pin is linked, anything else is a literal default."""


def _literal(v):
    if isinstance(v, (tuple, list)):
        return '(X=%r,Y=%r,Z=%r)' % tuple(float(c) for c in v)
    return repr(float(v))


class RigOps:
    """The op set as RigVM units: each op adds one node and returns its Result pin."""

    def __init__(self, ctl, unreal, prefix='Reach'):
        self.ctl, self.unreal, self.prefix, self.nodes = ctl, unreal, prefix, []

    def node(self, struct, title):
        name = '%s%02d%s' % (self.prefix, len(self.nodes), title)
        k = len(self.nodes)
        got = self.ctl.add_unit_node_from_struct_path(struct, 'Execute', self.unreal.Vector2D(220.0 * (k % 10),
                                                                                           160.0 * (k // 10)),
                                                    name, False, False)
        if not got:
            raise RuntimeError('LEG_REACH could not add %s (%s)' % (name, struct))
        self.nodes.append(name)
        return name

    def feed(self, pin, value):
        if isinstance(value, Pin):
            if not self.ctl.add_link(value, pin, False):
                raise RuntimeError('LEG_REACH could not feed %s with %s' % (pin, value))
        else:
            self.set(pin, _literal(value))

    def set(self, pin, text):
        """A pin default, or raise: the controller refuses a missing pin or a value it cannot import."""
        if not self.ctl.set_pin_default_value(pin, text, True, False, False):
            raise RuntimeError('LEG_REACH could not set %s = %s' % (pin, text))

    def __getattr__(self, op):
        unit, pins = RIG_OPS[op]

        def emit(*args):
            n = self.node(MATH + unit, unit)
            for pin, value in zip(pins, args):
                self.feed('%s.%s' % (n, pin), value)
            return Pin(n + '.Result')
        return emit

    def bone(self, name):
        """A bone's current global (component-space) position."""
        n = self.node(CR_UNIT + 'RigUnit_GetTransform', 'Get_' + name)
        self.set(n + '.Item', '(Type=Bone,Name="%s")' % name)
        return Pin(n + '.Transform.Translation')


def rig_legs(legs):
    """The bones offset() reads, per leg (hit_reaction_core.leg_ik entries): hip = the thigh, knee = the calf, foot,
    target."""
    return [(g['thigh'], g['calf'], g['foot'], g['target']) for g in legs]


def author(unreal, cr_path, skeleton, pelvis, legs):
    """Build the reach rig at cr_path (any previous one deleted by the caller): the skeleton's hierarchy, and on
    Forwards Solve the pelvis's global translation += offset(), its children following. Returns the node count."""
    bp = unreal.ControlRigBlueprintFactory.create_new_control_rig_asset(cr_path, False)
    if bp is None or bp.get_path_name().split('.')[0] != cr_path:
        raise RuntimeError('LEG_REACH factory returned %s, not %s' % (bp and bp.get_path_name(), cr_path))
    bp.get_hierarchy_controller().import_bones(unreal.load_asset(skeleton), '', True, True, False, False)
    have = {str(k.name) for k in bp.hierarchy.get_bones()}
    missing = sorted(({pelvis} | {b for leg in rig_legs(legs) for b in leg}) - have)
    if missing:
        raise RuntimeError('LEG_REACH bones missing from %s: %s' % (skeleton, missing))
    ctl = bp.get_controller_by_name('RigVMModel')
    m = RigOps(ctl, unreal)
    begin = m.node(CR_UNIT + 'RigUnit_BeginExecution', 'Forwards')
    d = offset(m, [tuple(m.bone(b) for b in leg) for leg in rig_legs(legs)])
    moved = m.vadd(m.bone(pelvis), d)
    put = m.node(CR_UNIT + 'RigUnit_SetTranslation', 'SetPelvis')
    m.set(put + '.Item', '(Type=Bone,Name="%s")' % pelvis)
    m.feed(put + '.Value', moved)
    # read back what the move depends on: global space, its subtree following
    graph = ctl.get_graph()
    for pin, want in (('.Space', 'GlobalSpace'), ('.bPropagateToChildren', 'True'), ('.Weight', '1')):
        got = graph.find_pin(put + pin)
        value = got.get_default_value() if got is not None else None
        same = value is not None and (abs(float(value) - 1.0) < 1e-6 if pin == '.Weight' else value == want)
        if not same:
            raise RuntimeError('LEG_REACH %s%s is %s, not %s' % (put, pin, value, want))
    if not ctl.add_link(begin + '.ExecutePin', put + '.ExecutePin', False):
        raise RuntimeError('LEG_REACH could not chain %s -> %s' % (begin, put))
    bp.recompile_vm()
    return len(m.nodes)
