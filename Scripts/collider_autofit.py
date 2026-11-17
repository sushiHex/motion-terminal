"""Collider autofit, the decisions: pure Python over hero_intake_fit.py's measurements. Builds every dynamics collider a
rig's secondary chains can reach, fitted to the mesh, and decides per chain which ones it collides with. No editor
import; unit-tested in Scripts/test_collider_autofit.py.

Visual checks found the loincloth cutting through the thighs, then the chest strips cutting into the torso. Hand-placed
colliders cover only what someone thought of, so this derives them for every body part a chain can reach.

  body_segments(roles, hierarchy, chains)  the body as segments (hips, spine, neck, head, arms, legs) + their regions
  fit_specs(doc, mesh)                      what hero_intake_fit.py measures
  build(doc, fit)                           doc['colliders']: shapes in owner-local space, per-chain collision lists

Rules (each one a number, stated where it is defined):
  * a capsule per segment, radius = fitted 90th percentile + MARGIN; core length = the joint-to-joint length, so the
    hemispherical caps overlap the neighbours instead of leaving the joints bare
  * a torso segment whose single-capsule radius exceeds SPLIT_RATIO x its halves' becomes two capsules, left and
    right: a wide, flat chest is not a cylinder
  * paired limbs whose medians differ by more than SYMMETRY_TOL take the smaller side's fit (something non-leg is
    skinned to the larger side)
  * a shape enters the rig only if some chain can reach it (root distance <= chain length + half-width)
  * a chain whose rest pose already touches a shape (closer than particle radius + REST_MARGIN) does not collide with
    it; it gets its own REST SHAPE instead: the same capsule on the same bone, shrunk until its rest pose clears it,
    so it can swing away from the body but not into it (the hand-placed rest box, generalised and kept round)
"""
import math

import dyn_behaviours

MARGIN_CM = 1.0
SPLIT_RATIO = 1.25
SYMMETRY_TOL = 0.25
PARTICLE_RADIUS_CM = 5.0    # chain particle radius (engine default, RigDynamicsData.h:16)
REST_MARGIN_CM = 1.0
# A rest shape thinner than this is not made (reported) and the chain keeps the original. It was a FRACTION (0.3) of the
# fitted shape, which kept full-size colliders around parts that start INSIDE them: a body shape a part rests inside
# ejects it on the first frame (Aurora's hair flared at a stand, 2026-10-29). A thin rest copy still blocks the
# way to the axis; only a degenerate one is refused.
MIN_REST_RADIUS_CM = 1.0
LIMBS = ('upperarm_', 'lowerarm_', 'thigh_', 'calf_')   # segment-name prefixes of shapes that swing about a joint
TAIL_MIN_CM = PARTICLE_RADIUS_CM   # a shorter tail is already inside the leaf bone's own particle
FLAP_RADIUS_SHARE = 0.25           # a flap's particle radius: this share of its tip length...
FLAP_MIN_RADIUS_CM = 1.0           # ...at least this, at most PARTICLE_RADIUS_CM
PAINTED_SHARE = 0.1                # a flap with under this share of its dominated vertices free is painted on (build)
FLAP_REST_GROUP = 'flaps'          # flaps of one behaviour share one rest copy per body shape: rest_flaps_<b>_<shape>
# A flap's FREE geometry is used for its tip only when it is most of the flap (a pendant is 75% free). A small free
# ornament on a flap whose hanging mass is blended must not steer the tip (reproduced: a 10%
# ornament turned a 20 cm downward tip into a 2 cm sideways one).
FREE_MAJORITY = 0.5
FREE_WEIGHT = 0.999                # a vertex is the flap's own when the flap bone and its descendants carry all of it
TAIL_PREFIX = 'MT_tail_'           # the rig's Null at a tail's end, under the leaf bone
REST_GAP_CM = 2.0           # a rest box's face sits this far behind particle contact at the rest line: the runtime
#                             idle pose differs a little from the reference pose the box is fitted in (measured: front
#                             strips 2.1 cm inside their hip boxes at 0.5 cm, while standing)

# ---------------------------------------------------------------- vectors and quaternions (UE FQuat: x, y, z, w)


def sub(a, b):
    return [x - y for x, y in zip(a, b)]


def add(a, b):
    return [x + y for x, y in zip(a, b)]


def scale(a, s):
    return [x * s for x in a]


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def norm(a):
    return math.sqrt(dot(a, a))


def unit(a):
    n = norm(a)
    return scale(a, 1.0 / n) if n > 1e-9 else None


def qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return [aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz]


def qconj(q):
    return [-q[0], -q[1], -q[2], q[3]]


def qrot(q, v):
    return qmul(qmul(q, v + [0.0]), qconj(q))[:3]


def q_from_axes(x, y, z):
    """Rotation whose local X/Y/Z map to the given orthonormal axes (component space)."""
    m00, m11, m22 = x[0], y[1], z[2]
    tr = m00 + m11 + m22
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        return [(y[2] - z[1]) / s, (z[0] - x[2]) / s, (x[1] - y[0]) / s, 0.25 * s]
    if m00 > m11 and m00 > m22:
        s = math.sqrt(1.0 + m00 - m11 - m22) * 2
        return [0.25 * s, (y[0] + x[1]) / s, (z[0] + x[2]) / s, (y[2] - z[1]) / s]
    if m11 > m22:
        s = math.sqrt(1.0 + m11 - m00 - m22) * 2
        return [(y[0] + x[1]) / s, 0.25 * s, (z[1] + y[2]) / s, (z[0] - x[2]) / s]
    s = math.sqrt(1.0 + m22 - m00 - m11) * 2
    return [(z[0] + x[2]) / s, (z[1] + y[2]) / s, 0.25 * s, (x[1] - y[0]) / s]


def q_z_to(d):
    """A rotation taking local +Z onto direction d (any roll)."""
    z = unit(d)
    helper = [1.0, 0.0, 0.0] if abs(z[0]) < 0.9 else [0.0, 1.0, 0.0]
    x = unit(cross(helper, z))
    return q_from_axes(x, cross(z, x), z)


def to_local(owner_tm, pos, rot):
    """Component-space (pos, rot) -> the owning bone's local space, given that bone's reference transform."""
    qo, to = owner_tm['rotation_xyzw'], owner_tm['translation']
    return qrot(qconj(qo), sub(pos, to)), qmul(qconj(qo), rot)


def seg_closest(p, a, b):
    ab = sub(b, a)
    t = max(0.0, min(1.0, dot(sub(p, a), ab) / (dot(ab, ab) or 1e-9)))
    return t, add(a, scale(ab, t))


# The skin around a body segment, for the colliders a garment rests on (the cloth collision source): a tapered capsule
# that COVERS the limb's surface. The points are the body's own surface only (UHeroDynamicsTools.SkinPoints: never a
# cloth section), so a dress no longer inflates a side and no left/right minimum is needed (it shrank Aurora's right
# thigh 10.65 -> 8.82 cm and her dress sank 6.3 cm into it, measured by dyn_contact 2026-10-31). The limb is not centred
# on its bone (a reproduction: a 10 cm thigh 3 cm off the bone read 9.6 cm about the bone; covering it about the
# bone takes 13), so per band along the segment the outline is each angular sector's SKIN_PCT distance, the band's
# centre is the outline's mean and its radius the outline's farthest point from that centre; the capsule's axis is the
# line through the band centres, its radii a line over the bands raised until every band is covered.
SKIN_BANDS = 6
SKIN_SPAN = (0.05, 0.95)    # of bone -> end: the joints' own blend regions left out
SKIN_SECTORS = 12
SKIN_MIN_SECTORS = 8    # a band seen from fewer directions is no measurement (one stray point read as a 30 cm limb)
SKIN_PCT = 75           # of a sector's distances: its outline, past the band's taper and seam noise


def _lerp(a, b, t):
    return [x + (y - x) * t for x, y in zip(a, b)]


def _line_fit(ts, ys):
    """Least-squares (y at t = 0, y at t = 1) through (t, y); one sample: flat."""
    n = len(ts)
    mt, my = sum(ts) / n, sum(ys) / n
    st = sum((t - mt) ** 2 for t in ts)
    slope = sum((t - mt) * (y - my) for t, y in zip(ts, ys)) / st if st > 1e-9 else 0.0
    return my - slope * mt, my + slope * (1.0 - mt)


def _circle_fit(xy):
    """Least-squares circle centre (x, y) through 2D points (Kasa: x^2 + y^2 + Dx + Ey + F = 0), or None."""
    s = [[0.0] * 3 for _ in range(3)]
    r = [0.0] * 3
    for x, y in xy:
        row = (x, y, 1.0)
        z = -(x * x + y * y)
        for i in range(3):
            r[i] += row[i] * z
            for j in range(3):
                s[i][j] += row[i] * row[j]
    det = lambda m: (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
                     + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))   # noqa: E731
    d = det(s)
    if abs(d) < 1e-9:
        return None
    sol = [det([[r[k] if j == i else s[k][j] for j in range(3)] for k in range(3)]) / d for i in range(3)]   # Cramer
    return -sol[0] / 2.0, -sol[1] / 2.0


def skin_capsule(points, a, b, bands=SKIN_BANDS, sectors=SKIN_SECTORS, garment=None):
    """The tapered capsule covering the skin around segment a -> b, from component-space surface points: {'near',
    'far' (its axis ends, component space, at the joints), 'radius_cm': [near, far], 'bands': measured}, or None with
    fewer than two measured bands. garment ([(xyz, thickness)]: the movable cloth particles at rest, garment_points): it
    is then also held clear of it (garment_clear), and says by how much it was lowered ('garment')."""
    axis = unit(sub(b, a))
    ref = [1.0, 0.0, 0.0] if abs(axis[0]) < 0.9 else [0.0, 1.0, 0.0]
    u = unit(cross(axis, ref))
    v = cross(axis, u)
    lo, hi = SKIN_SPAN
    found = [dict() for _ in range(bands)]
    for p in points:
        t, q = seg_closest(p, a, b)
        if lo <= t < hi:
            d = sub(p, q)
            k = int((math.atan2(dot(d, v), dot(d, u)) + math.pi) / (2.0 * math.pi) * sectors) % sectors
            found[int((t - lo) / (hi - lo) * bands)].setdefault(k, []).append(norm(d))
    fits, skin = [], []         # skin: every band's outline, component space (what the collider should cover)
    for i, m in enumerate(found):
        if len(m) < SKIN_MIN_SECTORS:
            continue
        t = lo + (i + 0.5) / bands * (hi - lo)
        q = _lerp(a, b, t)
        outline = []                                    # (u, v) in the band's plane, about the bone
        for k, rs in m.items():
            ang = (k + 0.5) / sectors * 2.0 * math.pi - math.pi
            r = sorted(rs)[min(len(rs) - 1, int(round(SKIN_PCT / 100.0 * (len(rs) - 1))))]
            outline.append((r * math.cos(ang), r * math.sin(ang)))
        # the outline's circle centre (its mean leans to the bone's side: the sectors are even about the bone, not it)
        cu, cv = _circle_fit(outline) or (sum(o[0] for o in outline) / len(outline),
                                          sum(o[1] for o in outline) / len(outline))
        c = add(q, add(scale(u, cu), scale(v, cv)))
        spread = [math.hypot(x - cu, y - cv) for x, y in outline]
        fits.append((t, c, max(spread), min(spread)))
        skin += [(add(q, add(scale(u, x), scale(v, y))), q) for x, y in outline]
    if len(fits) < 2:
        return None
    ts = [f[0] for f in fits]
    near, far = ([_line_fit(ts, [f[1][j] for f in fits])[e] for j in range(3)] for e in (0, 1))
    # each band's radius about the fitted axis (its centre is off the line by the fit's residual)
    off = [math.dist(c, _lerp(near, far, t)) for t, c, _r, _i in fits]
    need = [r + o for (_t, _c, r, _i), o in zip(fits, off)]
    rn, rf = _line_fit(ts, need)
    lift = max(0.0, max(n - (rn + (rf - rn) * t) for t, n in zip(ts, need)))
    out = {'near': [round(x, 3) for x in near], 'far': [round(x, 3) for x in far],
           'radius_cm': [round(rn + lift, 2), round(rf + lift, 2)], 'bands': len(fits)}
    if garment:
        # the skin's core: each band's nearest outline point about the fitted axis, as a line (never above the cover)
        inner = _line_fit(ts, [max(0.0, i - o) for (_t, _c, _r, i), o in zip(fits, off)])
        out = dict(out, **garment_clear(out, inner, garment, skin))
    return out


# A collider that COVERS an oval limb overshoots its skin on the narrow sides (Aurora's hip: 13.2 cm about the thigh
# axis), and where the garment hangs inside it at rest the simulation shoves the cloth out by the overshoot the moment
# it starts: a shelf at the edge of the simulated region (cloth_rest measured 63 of her dress's
# movable particles inside the right thigh's, 4.67 cm deep). A collider that gives way bares skin the cloth then sinks
# into in motion: clearing five hem particles at her knee took the calf's knee end 10.1 -> 5.8 cm and the dress sank
# 1.7 cm into the back of her calf at a run (dynamics gate 2026-11-05). Two errors, one each way, so the fit makes the
# worse of them as small as it can: how far a rest particle is inside the collider (pushed past the thickness Chaos
# holds it off) against how far the skin's outline is outside it; each within the cloth's thickness, the contact
# model's own resolution and the dynamics gate's limit on the sink. The axis may move too, within the skin's core and
# never standing further off the skin than the cover did. A particle inside the skin's core is the art's own overlap
# and bounds nothing (counted).
GARMENT_ITERS = 40              # bisection and ternary steps for the radii (the interval < 40 cm: < 1e-5 cm)
GARMENT_BALANCE_ITERS = 8       # bisection steps balancing the two errors (within the thickness: < 0.01 cm)
GARMENT_STEP_CM = (2.0, 0.05)   # the axis search's first and last step, each end moved across the axis


def _tapered_out(p, near, far, rn, rf):
    """How far p is outside tapered capsule near -> far (radii rn, rf), cm (< 0: inside); TaperedCapsule.h:113."""
    t, q = seg_closest(p, near, far)
    return math.dist(p, q) - (rn + (rf - rn) * t)


def garment_clear(cap, inner, garment, skin=()):
    """Tapered capsule cap ({'near', 'far', 'radius_cm'}) given way to the garment at rest ([(xyz, thickness cm)], the
    movable particles) as little as the skin allows. For an allowed depth e (how far a particle outside the skin's core,
    inner: [near, far] radii about cap's axis, may sit inside the collider) the radii of an axis are solved exactly
    (radii) and the axis ends searched, each within the core, to the least skin exposed (worst, then mean) by a
    collider no further off the skin anywhere than the cover; e is then the least for which the skin is exposed by no
    more than e, at most the thinnest thickness. {'near', 'far', 'radius_cm', 'garment': {'points' (constraining it),
    'in_core' (inside the skin: constrain nothing), 'inside_cm' (e: the most a particle is inside it), 'unmet_cm' (how
    far past e any particle still is: > 0 only where the core forbids clearing it), 'exposed_cm' (the most any skin
    outline point is outside it), 'lowered_cm': [near, far], 'moved_cm': [near, far]}}. Radii round down."""
    near, far = cap['near'], cap['far']
    rn0, rf0 = cap['radius_cm']
    cn, cf = min(inner[0], rn0), min(inner[1], rf0)
    budget = min((th for _p, th in garment), default=0.0)
    fn, ff = cn, cf
    reach = max(rn0, rf0) + max(cn, cf) + budget        # the most any collider this fit can make reaches
    rows, in_core = [], 0
    for p, _th in garment:
        t, q = seg_closest(p, near, far)
        d = math.dist(p, q)
        if d >= reach:
            continue
        if d < cn + (cf - cn) * t:
            in_core += 1                                # inside the skin itself: the art's overlap, not the collider's
        else:
            rows.append(p)                              # outside the skin: every collider must give way to it
    if not any(_tapered_out(p, near, far, rn0, rf0) < 0.0 for p in rows):
        return {'garment': {'points': 0, 'in_core': in_core, 'inside_cm': 0.0, 'unmet_cm': 0.0, 'exposed_cm': 0.0,
                            'lowered_cm': [0.0, 0.0], 'moved_cm': [0.0, 0.0]}}

    def radii(nr, fr, e):
        """The largest radii (near + far) on axis nr -> fr leaving no particle more than e inside, each no higher than
        the cover about it (the cover plus how far that end moved) and no lower than the core, or None when none does.
        rn + far(rn) is concave (a min of lines) and far(rn) falls, so the feasible near radii are an interval
        [core, top]: top by bisection, the best by ternary search."""
        hn, hf = rn0 + math.dist(nr, near), rf0 + math.dist(fr, far)
        on = []
        for p in rows:
            t, q = seg_closest(p, nr, fr)
            on.append((t, math.dist(p, q) + e))

        def far_for(rn):
            rf = hf
            for t, room in on:
                if t > 1e-6:
                    rf = min(rf, (room - rn * (1.0 - t)) / t)
                elif rn > room:
                    return None
            return rf if rf >= ff else None

        if far_for(fn) is None:
            return None
        top = hn
        if far_for(top) is None:
            lo, hi = fn, hn
            for _ in range(GARMENT_ITERS):
                mid = (lo + hi) / 2.0
                lo, hi = (mid, hi) if far_for(mid) is not None else (lo, mid)
            top = lo
        a, b = fn, top
        for _ in range(GARMENT_ITERS):
            m1, m2 = a + (b - a) / 3.0, b - (b - a) / 3.0
            if m1 + far_for(m1) < m2 + far_for(m2):
                a = m1
            else:
                b = m2
        return a, far_for(a)

    def surface(nr, fr, rr):
        """Per skin outline point (s, q: the bone point it was read from), how far the collider's surface stands
        beyond it along the ray q -> s (< 0: the skin is exposed by that much)."""
        out = []
        for s, q in skin:
            n = math.dist(s, q)
            w = scale(sub(s, q), 1.0 / n)
            lo, hi = 0.0, n + reach
            if _tapered_out(q, nr, fr, *rr) >= 0.0:
                out.append(-n)                          # the bone point itself is outside: the whole ray exposed
                continue
            for _ in range(GARMENT_ITERS // 2):
                mid = (lo + hi) / 2.0
                lo, hi = (mid, hi) if _tapered_out(add(q, scale(w, mid)), nr, fr, *rr) < 0.0 else (lo, mid)
            out.append(lo - n)
        return out

    def exposure(nr, fr, rr):
        """(cost: worst and mean exposed skin, ok: no further off the skin anywhere than the cover)."""
        x = surface(nr, fr, rr)
        gaps = [max(0.0, -v) for v in x]
        return (max(gaps), sum(gaps) / len(gaps)), max(x) <= stand_off + 1e-6

    stand_off = max(surface(near, far, (rn0, rf0))) if skin else 0.0
    axis = unit(sub(far, near))
    u = unit(cross(axis, [1.0, 0.0, 0.0] if abs(axis[0]) < 0.9 else [0.0, 1.0, 0.0]))
    v = cross(axis, u)
    moves = [(k, [c * s for c in d]) for k in (0, 1) for d in (u, v) for s in (1.0, -1.0)]

    def fit(e):
        """(near, far, radii, exposure cost) for allowed depth e: the axis searched, its radii solved."""
        nr, fr = list(near), list(far)
        rr = radii(nr, fr, e)
        if rr is None:
            return nr, fr, (fn, ff), None
        if not skin:
            return nr, fr, rr, (0.0, 0.0)
        cost, step = exposure(nr, fr, rr)[0], GARMENT_STEP_CM[0]
        while step >= GARMENT_STEP_CM[1]:
            for k, d in moves:
                ends = [nr, fr]
                ends[k] = add(ends[k], scale(d, step))
                if math.dist(ends[k], (near, far)[k]) > (cn, cf)[k]:
                    continue                            # the axis stays inside the limb
                got = radii(ends[0], ends[1], e)
                c, ok = exposure(ends[0], ends[1], got) if got else (None, False)
                if ok and c < cost:
                    (nr, fr), rr, cost = ends, got, c
                    break
            else:
                step /= 2.0
        return nr, fr, rr, cost

    # the least e whose fit exposes no more than e (exposure falls as e grows): bisection over [0, budget]
    best = fit(budget)
    if best[3] is not None and best[3][0] <= budget:
        lo, hi = 0.0, budget
        for _ in range(GARMENT_BALANCE_ITERS):
            mid = (lo + hi) / 2.0
            got = fit(mid)
            if got[3] is not None and got[3][0] <= mid:
                hi, best = mid, got
            else:
                lo = mid
        e = hi
    else:
        e = budget
    nr, fr, rr, _cost = best
    near2, far2 = [round(c, 3) for c in nr], [round(c, 3) for c in fr]
    rn, rf = (math.floor(r * 100.0) / 100.0 for r in rr)
    inside = max((-_tapered_out(p, near2, far2, rn, rf) for p in rows), default=0.0)
    shown = -min(surface(near2, far2, (rn, rf)), default=0.0)
    return {'near': near2, 'far': far2, 'radius_cm': [rn, rf],
            'garment': {'points': len(rows), 'in_core': in_core, 'inside_cm': round(max(0.0, inside), 2),
                        'unmet_cm': round(max(0.0, inside - e), 2), 'exposed_cm': round(max(0.0, shown), 2),
                        'lowered_cm': [round(rn0 - rn, 2), round(rf0 - rf, 2)],
                        'moved_cm': [round(math.dist(near, near2), 2), round(math.dist(far, far2), 2)]}}

# ---------------------------------------------------------------- body model


def _descendants(parents, bones, b):
    kids = {}
    for c in bones:
        kids.setdefault(parents.get(c), []).append(c)
    out, stack = [], list(kids.get(b, []))
    while stack:
        c = stack.pop()
        out.append(c)
        stack.extend(kids.get(c, []))
    return out


def body_segments(roles, hierarchy, chains):
    """[{'name', 'owner', 'bone', 'end', 'region', 'torso'}] and [{'name', 'owner', 'bone', 'region'}] (blobs).
    A segment's region is its bone plus the helper bones under it (twist, corrective...) that are neither a body role
    nor part of a chain nor under another role bone: the vertices the body itself moves with that bone."""
    parents, bones = hierarchy['parents'], hierarchy['bones']
    body = {v for v in roles.values() if isinstance(v, str)} | set(roles.get('spine') or [])
    chain_bones = {b for c in chains for b in c['bones']}

    def helpers(b):
        out = [b]
        for d in _descendants(parents, bones, b):
            a = parents.get(d)
            while a not in body and a is not None:
                a = parents.get(a)
            if d not in body and d not in chain_bones and a == b:
                out.append(d)
        return out

    segs, blobs = [], []
    r = roles
    if r.get('pelvis') and r.get('thigh_l') and r.get('thigh_r'):
        segs.append({'name': 'hips', 'owner': r['pelvis'], 'bone': r['thigh_l'], 'end': r['thigh_r'],
                     'region': helpers(r['pelvis']), 'torso': False})
    spine = list(r.get('spine') or [])
    # The WAIST, pelvis -> first spine bone (a chest strip's end was seen sliding behind the belt at the stop). The
    # hips capsule runs across the hip joints, so its radius is the skirt below, and nothing was fitted at belt height;
    # measured on the pelvis's own vertices between the two joints, this one is the belt.
    if r.get('pelvis') and spine:
        segs.append({'name': 'waist', 'owner': r['pelvis'], 'bone': r['pelvis'], 'end': spine[0],
                     'region': helpers(r['pelvis']), 'torso': True})
    column = spine + ([r['neck']] if r.get('neck') else [])
    for a, b in zip(column, column[1:]):
        segs.append({'name': 'torso_' + a, 'owner': a, 'bone': a, 'end': b, 'region': helpers(a), 'torso': True})
    if r.get('neck') and r.get('head'):
        segs.append({'name': 'neck', 'owner': r['neck'], 'bone': r['neck'], 'end': r['head'],
                     'region': helpers(r['neck']), 'torso': False})
        blobs.append({'name': 'head', 'owner': r['head'], 'bone': r['head'], 'region': helpers(r['head'])})
    for s in 'lr':
        for a, b in (('upperarm', 'lowerarm'), ('lowerarm', 'hand'), ('thigh', 'calf'), ('calf', 'foot')):
            ba, bb = r.get(a + '_' + s), r.get(b + '_' + s)
            if ba and bb:
                segs.append({'name': '%s_%s' % (a, s), 'owner': ba, 'bone': ba, 'end': bb, 'region': helpers(ba),
                             'torso': False})
    return segs, blobs


def frame(ref, roles):
    """(left-right, forward, up) from the reference pose, or None: left-right = horizontal thigh_l - thigh_r,
    forward = horizontal foot_l -> ball_l orthogonalised, up = +Z."""
    try:
        lr = unit([ref[roles['thigh_l']][0] - ref[roles['thigh_r']][0],
                   ref[roles['thigh_l']][1] - ref[roles['thigh_r']][1], 0.0])
        fw = [ref[roles['ball_l']][0] - ref[roles['foot_l']][0], ref[roles['ball_l']][1] - ref[roles['foot_l']][1], 0.0]
    except (KeyError, TypeError):
        return None
    if lr is None:
        return None
    fw = unit(sub(fw, scale(lr, dot(fw, lr))))
    return (lr, fw, [0.0, 0.0, 1.0]) if fw else None


def sim_parts(doc):
    """The parts a backend simulates (docs/specs/2026-10-27-secondary-motion-backends-design.md §3.1), each with 'root' and 'bones'. A profile from before the
    parts list (chains but no `secondary`) is an error, not zero parts: re-run hero_intake.py on it."""
    if 'secondary' not in doc:
        if doc.get('chains'):
            raise ValueError('profile %s has chains but no secondary.parts: re-run hero_intake.py' % doc.get('rig'))
        return []
    return doc['secondary']['parts']


def fit_specs(doc, mesh):
    """What hero_intake_fit.py measures. The torso's split axis should be the body's left-right, but that frame comes
    from reference positions the fit itself returns, so torso segments are measured split along component X and Y
    both and build() keeps the one nearer the body's left-right."""
    roles = doc.get('roles', {})
    segs, blobs = body_segments(roles, mesh['hierarchy'], sim_parts(doc))
    specs = {'segments': [], 'blobs': [], 'chains': [{'root': c['root'], 'bones': c['bones']}
                                                      for c in sim_parts(doc)]}
    for s in segs:
        spec = {k: s[k] for k in ('name', 'bone', 'end', 'region')}
        specs['segments'].append(spec)
        if s['torso']:
            for axis_name, axis in (('x', [1.0, 0.0, 0.0]), ('y', [0.0, 1.0, 0.0])):
                specs['segments'].append(dict(spec, name='%s@split%s' % (s['name'], axis_name), split_axis=axis))
    specs['blobs'] = [{k: b[k] for k in ('name', 'bone', 'region')} for b in blobs]
    ref = [v for v in roles.values() if isinstance(v, str)] + list(roles.get('spine') or [])
    ref += [b for c in sim_parts(doc) for b in c['bones']]
    specs['ref_bones'] = sorted(set(ref))
    return specs

# ---------------------------------------------------------------- build


def _capsule(name, owner, a, b, radius, role):
    return {'name': name, 'owner': owner, 'type': 'capsule', 'a_cs': [round(v, 3) for v in a],
            'b_cs': [round(v, 3) for v in b], 'radius_cm': round(radius, 2), 'role': role}


def _shape_distance(p, s):
    """Distance from point p to the shape's surface (negative inside), component space, reference pose."""
    if s['type'] == 'capsule':
        _, q = seg_closest(p, s['a_cs'], s['b_cs'])
        return math.dist(p, q) - s['radius_cm']
    c, ax = s['centre_cs'], s['axes_cs']
    loc = [dot(sub(p, c), a) for a in ax]
    half = [e / 2.0 for e in s['extents_cm']]
    out = [max(abs(l) - h, 0.0) for l, h in zip(loc, half)]
    inside = min(h - abs(l) for l, h in zip(loc, half))
    return norm(out) if any(out) else -inside


def posed_distance(p, s, tm):
    """Distance from p to a localized shape (negative inside) with its owner at component-space transform
    tm = [tx, ty, tz, qx, qy, qz, qw]: the shape TM is applied relative to its owner (RigParticleSimulation.cpp:620)."""
    t, q, loc = tm[:3], tm[3:], s['local']
    centre, rot = add(t, qrot(q, loc['translation'])), qmul(q, loc['rotation_xyzw'])
    if s['type'] == 'capsule':
        half = scale(qrot(rot, [0.0, 0.0, 1.0]), loc['length_cm'] / 2.0)
        _, c = seg_closest(p, sub(centre, half), add(centre, half))
        return math.dist(p, c) - loc['radius_cm']
    lp = qrot(qconj(rot), sub(p, centre))
    half = [e / 2.0 for e in loc['extents_cm']]
    out = [max(abs(l) - h, 0.0) for l, h in zip(lp, half)]
    return norm(out) if any(out) else -min(h - abs(l) for l, h in zip(lp, half))


def _localize(s, tms):
    """The shape's transform relative to its owner in the reference pose: what the rig spawns, and what places the
    shape on a recorded pose."""
    tm = tms.get(s['owner'])
    if tm is None:
        return
    if s['type'] == 'capsule':
        a, b = s['a_cs'], s['b_cs']
        rot = q_z_to(sub(b, a)) if norm(sub(b, a)) > 1e-6 else [0.0, 0.0, 0.0, 1.0]
        pos, q = to_local(tm, scale(add(a, b), 0.5), rot)
        s['local'] = {'translation': [round(v, 4) for v in pos], 'rotation_xyzw': [round(v, 6) for v in q],
                      'radius_cm': s['radius_cm'], 'length_cm': round(norm(sub(b, a)), 2)}
    else:
        pos, q = to_local(tm, s['centre_cs'], q_from_axes(*s['axes_cs']))
        s['local'] = {'translation': [round(v, 4) for v in pos], 'rotation_xyzw': [round(v, 6) for v in q],
                      'extents_cm': s['extents_cm']}


def _rest_shape(chain, shape, nearest, reasons, particle_radius=PARTICLE_RADIUS_CM):
    """The chain's own copy of a body shape it already rests against, shrunk until the chain's rest pose (its closest
    approach to the shape's axis, `nearest`) clears it by the particle radius + REST_GAP_CM. It stays round: a flat
    rest box riding a thigh acted as a paddle and flung the front loincloth to -73 deg (measured), where a capsule lets
    the cloth slide around the limb."""
    a, b = shape['a_cs'], shape['b_cs']
    radius = nearest - particle_radius - REST_GAP_CM
    if radius < MIN_REST_RADIUS_CM:
        reasons.append('rest shape skipped for %s on %s (kept the original): it rests %.1f cm from the axis (fitted %.1f)'
                       % (chain['root'], shape['name'], nearest, shape['radius_cm']))
        return None
    rest = _capsule('rest_%s_%s' % (chain['root'], shape['name']), shape['owner'], a, b, radius, 'rest')
    rest['for_chain'], rest['shrunk_from_cm'] = chain['root'], shape['radius_cm']
    return rest


def flap_vertex_sets(vweights, leaf, own_bones, dominant=0.5, min_samples=5):
    """(dominated, free, use_free) vertex indices of a single-bone flap. Dominated: the flap bone carries >= `dominant`
    of the weight. Free: dominated, and the flap bone plus its own descendants (`own_bones`: twist helpers, tails)
    carry all of it, i.e. nothing outside the flap moves it. use_free: the free set is the flap's mass (a majority, and
    enough samples), so its tip should follow that set rather than everything the bone dominates."""
    dom = [i for i, w in enumerate(vweights) if w.get(leaf, 0.0) >= dominant]
    free = [i for i in dom if sum(w for b, w in vweights[i].items() if b in own_bones) >= FREE_WEIGHT]
    return dom, free, len(free) >= min_samples and len(free) >= FREE_MAJORITY * len(dom)


FACE_MIN_SAMPLES = 20      # a strip bone's face normal needs this many weighted vertices...
FACE_MIN_SPREAD = 0.05     # ...spread in two directions (lambda_mid / lambda_max at least this: not a line)...
FACE_MAX_FLAT = 0.35       # ...and flat (sqrt(lambda_min / lambda_mid) at most this: not a tube or a block)


def sym3_eigen(m, sweeps=50):
    """Eigenvalues (ascending) and unit eigenvectors of a symmetric 3x3 matrix (cyclic Jacobi)."""
    a = [row[:] for row in m]
    v = [[1.0 if i == j else 0.0 for j in range(3)] for i in range(3)]
    for _ in range(sweeps):
        off = a[0][1] ** 2 + a[0][2] ** 2 + a[1][2] ** 2
        if off < 1e-18:
            break
        for p, q in ((0, 1), (0, 2), (1, 2)):
            if abs(a[p][q]) < 1e-30:
                continue
            th = (a[q][q] - a[p][p]) / (2.0 * a[p][q])
            t = (1.0 if th >= 0 else -1.0) / (abs(th) + math.sqrt(th * th + 1.0))
            c = 1.0 / math.sqrt(t * t + 1.0)
            s = t * c
            for k in range(3):
                akp, akq = a[k][p], a[k][q]
                a[k][p], a[k][q] = c * akp - s * akq, s * akp + c * akq
            for k in range(3):
                apk, aqk = a[p][k], a[q][k]
                a[p][k], a[q][k] = c * apk - s * aqk, s * apk + c * aqk
            for k in range(3):
                vkp, vkq = v[k][p], v[k][q]
                v[k][p], v[k][q] = c * vkp - s * vkq, s * vkp + c * vkq
    pairs = sorted(((a[i][i], [v[0][i], v[1][i], v[2][i]]) for i in range(3)), key=lambda e: e[0])
    return [e[0] for e in pairs], [e[1] for e in pairs]


def face_normal(points, weights, bone, nxt, host):
    """A strip bone's face normal, component space, from the vertices it moves (`points`, influence `weights`), or
    (None, reason). The normal is the smallest-spread axis of the weighted covariance, taken perpendicular to the bone
    (bone -> nxt) and signed to point AWAY from the host body (`host`: a point inside it, e.g. the chain's parent
    bone), so mirrored strips get mirrored normals. Not flat enough, too few vertices, or spread along a line only:
    no normal (the part gets no twist)."""
    wsum = sum(weights)
    if len(points) < FACE_MIN_SAMPLES or wsum <= 0.0:
        return None, '%d vertices (need %d)' % (len(points), FACE_MIN_SAMPLES)
    c = [sum(w * p[k] for p, w in zip(points, weights)) / wsum for k in range(3)]
    cov = [[sum(w * (p[i] - c[i]) * (p[j] - c[j]) for p, w in zip(points, weights)) / wsum for j in range(3)]
           for i in range(3)]
    lam, vec = sym3_eigen(cov)
    if lam[2] <= 0.0 or lam[1] / lam[2] < FACE_MIN_SPREAD:
        return None, 'spread along a line (mid/max %.3f, need %.2f)' % (lam[1] / lam[2] if lam[2] > 0 else 0.0,
                                                                         FACE_MIN_SPREAD)
    flat = math.sqrt(max(lam[0], 0.0) / lam[1])
    if flat > FACE_MAX_FLAT:
        return None, 'not flat (thickness/width %.2f, max %.2f)' % (flat, FACE_MAX_FLAT)
    axis = unit(sub(nxt, bone))
    n = sub(vec[0], scale(axis, dot(vec[0], axis)))
    if norm(n) < 1e-6:
        return None, 'face normal along the bone'
    n = unit(n)
    out = sub(c, host)
    if dot(n, out) < 0.0:
        n = scale(n, -1.0)
    return n, round(flat, 3)


def build(doc, fit):
    """doc['colliders'] from a fit: {'shapes': [...], 'chains': {root: {'particle_radius_cm', 'collides_with'}}}."""
    roles = doc.get('roles', {})
    ref, tms = fit.get('ref_cs', {}), fit.get('ref_tm', {})
    reasons = doc.setdefault('reasons', [])
    fr = frame(ref, roles)
    segs, blobs = body_segments(roles, {'parents': {}, 'bones': []}, sim_parts(doc))   # names/owners only
    owners = {s['name']: s['owner'] for s in segs + blobs}
    meas = fit.get('segments', {})
    # symmetry on paired limbs
    radius = {k: v['radius_cm'] for k, v in meas.items() if '@' not in k and v.get('radius_cm') is not None}
    for name in sorted(radius):
        if name.endswith('_l') and name[:-2] + '_r' in radius:
            l, r = meas[name], meas[name[:-2] + '_r']
            if abs(l['radius_p50_cm'] - r['radius_p50_cm']) > SYMMETRY_TOL * min(l['radius_p50_cm'], r['radius_p50_cm']):
                small, big = (name, name[:-2] + '_r') if l['radius_p50_cm'] < r['radius_p50_cm'] else \
                             (name[:-2] + '_r', name)
                radius[big] = radius[small]
                reasons.append('asymmetric %s fit (median %.1f vs %.1f cm): %s mirrored from %s'
                               % (name[:-2], meas[small]['radius_p50_cm'], meas[big]['radius_p50_cm'], big, small))
    shapes = []
    for s in segs:
        m = meas.get(s['name'])
        if not m or s['name'] not in radius:
            continue
        a, b = ref.get(m['bone']), ref.get(m['end'])
        if a is None or b is None:
            continue
        whole = radius[s['name']] + MARGIN_CM
        split = None
        if s['torso'] and fr:
            axis_name = 'x' if abs(fr[0][0]) >= abs(fr[0][1]) else 'y'
            sm = meas.get('%s@split%s' % (s['name'], axis_name), {}).get('halves', {})
            if len(sm) == 2 and all(h.get('radius_cm') for h in sm.values()):
                if radius[s['name']] > SPLIT_RATIO * max(h['radius_cm'] for h in sm.values()):
                    split = (axis_name, sm)
        if split:
            axis = [1.0, 0.0, 0.0] if split[0] == 'x' else [0.0, 1.0, 0.0]
            for key, h in sorted(split[1].items()):
                off = scale(axis, h['offset_cm'])
                shapes.append(_capsule('%s_%s' % (s['name'], 'p' if key == '+1' else 'n'), s['owner'], add(a, off),
                                       add(b, off), h['radius_cm'] + MARGIN_CM, 'body'))
        else:
            shapes.append(_capsule(s['name'], s['owner'], a, b, whole, 'body'))
    for bl in blobs:
        m = fit.get('blobs', {}).get(bl['name'])
        if m and m.get('radius_cm'):
            c = m['centre_cs']
            shapes.append(_capsule(bl['name'], bl['owner'], c, c, m['radius_cm'] + MARGIN_CM, 'body'))
    for s in shapes:
        _localize(s, tms)
    widths = {k: v['half_width_cm'] for k, v in fit.get('chains', {}).items()}
    # Each part's behaviour (dynamics_behaviours.json, or the profile's pick in secondary.behaviours), resolved HERE so
    # the collision groups below, the rig's spawners and the gate's profile digest all see one assignment.
    lib = dyn_behaviours.load()
    chosen = doc.get('secondary', {}).get('behaviours', {})
    known = {c.get('id', c['root']) for c in sim_parts(doc)}
    stray = sorted(set(chosen) - known)
    if stray:
        raise ValueError('secondary.behaviours names parts the intake did not find: %s' % ', '.join(stray))
    behaviour = {c['root']: dyn_behaviours.resolve(dict(c, id=c.get('id', c['root'])), lib, chosen)
                 for c in sim_parts(doc)}
    chains_out, used = {}, set()
    rest_shapes = []
    dropped = []
    for c in sim_parts(doc):
        # TAILS (a chest strip's fringe and feather were seen sinking into the belt at the stop). The mesh hangs past
        # a chain's leaf bone, and nothing there collides: the leaf's particle sits on the bone. A tail longer than the
        # particle radius gets its own particle at the measured end (the rig adds a Null there, which the chain search
        # picks up, RigDynamicsExecutionHelpers.cpp:173), and counts as one of the chain's points below.
        # A flap's tip only has to clear the smallest flap particle; a chain's leaf, the chain particle (the
        # chain threshold dropped short feathers as "no tip").
        # PAINTED flaps (2026-10-28, a necklace's side pieces; research/ue58-rigging-tools-assessment.md, 'Behaviours and the necklace'): a flap with almost no FREE geometry (vertices only
        # it and its own descendants move, flap_vertex_sets) is skinned INTO its host (every vertex shared with the
        # chest). It cannot move without stretching the host, so it is not simulated: the motion is the host's.
        # Measured: those pieces 0% free, feathers 100%. Chains are not tested (the fit measures skin per flap only).
        skin = fit.get('chains', {}).get(c['root'], {}).get('skin')
        if c.get('kind') == 'flap' and skin and skin['free'] < PAINTED_SHARE * max(skin['dominant'], 1) \
                and not lib['behaviours'][behaviour[c['root']]].get('deforms_host'):
            dropped.append(c['root'])
            reasons.append('flap %s dropped: painted onto its host (%d of %d vertices its own)'
                           % (c['root'], skin['free'], skin['dominant']))
            continue
        # A tail counts when it reaches past the leaf's OWN particle: a flap's smallest, or the chain's behaviour radius
        # (hair 1.5 cm kept 1.6-2.3 cm tails the 5 cm cloth threshold pruned).
        floor = FLAP_MIN_RADIUS_CM if c.get('kind') == 'flap' else \
            lib['behaviours'][behaviour[c['root']]].get('particle_radius_cm', TAIL_MIN_CM)
        tails = [t for t in fit.get('chains', {}).get(c['root'], {}).get('tails', [])
                 if t['length_cm'] > floor and t['bone'] in ref]
        pts = [ref[b] for b in c['bones'][1:] if b in ref]            # the root particle is kinematic
        pts += [t['end_cs'] for t in tails]
        root = ref.get(c['root'])
        if root is None or not pts:
            if c.get('kind') == 'flap':
                # A flap's only moving particle is its tip: without one it would be a kinematic point (no motion).
                dropped.append(c['root'])
                reasons.append('flap %s dropped: no tip measured on its mesh' % c['root'])
            continue
        # A flap is a thin piece (a feather): its particle is a quarter of its length, not the cloth chains' 5 cm,
        # or the head it grows from would push it away at rest.
        prad = (min(PARTICLE_RADIUS_CM, max(FLAP_MIN_RADIUS_CM, FLAP_RADIUS_SHARE * tails[0]['length_cm']))
                if c.get('kind') == 'flap' else
                lib['behaviours'][behaviour[c['root']]].get('particle_radius_cm', PARTICLE_RADIUS_CM))
        length = sum(math.dist(ref[x], ref[y]) for x, y in zip(c['bones'], c['bones'][1:]) if x in ref and y in ref)
        length += max([t['length_cm'] for t in tails] or [0.0])
        # Reach: the chain's length plus whichever is wider, the strip or the particle (a 1 cm-wide
        # chain's 5 cm particle still touches what its half-width says it cannot).
        reach = length + max(widths.get(c['root']) or 0.0, prad)
        collide = []
        # A behaviour that turns collision off (native bCollideWithColliders false: soft tissue rests inside its host)
        # gets no colliders, so the rig spawns none for it and the clearance analysis scores none against it.
        solid = (lib['behaviours'][behaviour[c['root']]].get('native') or {}).get('bCollideWithColliders', True)
        for s in (shapes if solid else []):
            # A limb sweeps its own length about its joint: an arm out of reach in the reference pose can still swing
            # into the cloth, so a limb shape counts as reachable by that much more.
            sweep = norm(sub(s['b_cs'], s['a_cs'])) if s['name'].startswith(LIMBS) else 0.0
            if _shape_distance(root, s) - sweep > reach:
                continue                                              # out of this chain's reach
            rest = min(_shape_distance(p, s) for p in pts)
            if rest < prad + REST_MARGIN_CM:              # it rests against this part of the body
                rest = _rest_shape(c, s, rest + s['radius_cm'], reasons, prad)
                if rest and c.get('kind') == 'flap':
                    # Flaps of one behaviour SHARE one rest copy per body shape, as thin as the closest of them needs: a
                    # copy per feather (31 on Khaimera) made every part exclude every copy, and the rig build crawled
                    # (2026-10-27). Per behaviour, not across all flaps: one group's tip must not resize another's
                    # collider.
                    shared = rest_flap_name(behaviour[c['root']], s['name'])
                    have = next((r for r in rest_shapes if r['name'] == shared), None)
                    if have is None:
                        rest['name'], rest['for_chain'] = shared, '%s_%s' % (FLAP_REST_GROUP, behaviour[c['root']])
                        rest_shapes.append(rest)
                    else:
                        have['radius_cm'] = min(have['radius_cm'], rest['radius_cm'])
                    collide.append(shared)
                elif rest:
                    rest_shapes.append(rest)
                    collide.append(rest['name'])
                else:
                    # Too thin to shrink: keep the original rather than leave a hole the chain can pass through.
                    # The chain is pushed out of it at rest, and the reason above says so.
                    collide.append(s['name'])
            else:
                collide.append(s['name'])
        chains_out[c['root']] = {'kind': c.get('kind', 'chain'), 'behaviour': behaviour[c['root']],
                                 'particle_radius_cm': round(prad, 2),
                                 'reach_cm': round(reach, 2),
                                 'collides_with': sorted(collide),
                                 'tails': [{'bone': t['bone'], 'null': TAIL_PREFIX + t['bone'], 'local': t['local'],
                                            'length_cm': t['length_cm']} for t in tails]}
        # A strip's face normals (hero_intake_fit, face_normal), for a behaviour's twist modifier: only the bones the
        # solver rotates (one particle child), with a reason for each bone that got none.
        faces = dict(fit.get('chains', {}).get(c['root'], {}).get('faces') or {})
        # Eligibility follows the FINAL particle topology: a leaf whose tail was pruned above (too short) has no particle
        # child, so the rig could not twist it.
        kept = {t['bone'] for t in tails}
        for b, f in list(faces.items()):
            if 'normal_local' in f and f.get('next') == 'tail' and b not in kept:
                faces[b] = {'reason': 'its tail was pruned (no next particle)'}
        if faces:
            chains_out[c['root']]['faces'] = {b: {'normal_local': f['normal_local'], 'flat': f['flat']}
                                              for b, f in sorted(faces.items()) if 'normal_local' in f}
            if lib['behaviours'][behaviour[c['root']]].get('twist'):     # only where a twist would use it
                reasons.extend('no face normal for %s on %s: %s' % (b, c['root'], f['reason'])
                               for b, f in sorted(faces.items()) if 'normal_local' not in f)
        used.update(collide)
    flap_groups(chains_out, behaviour)
    if dropped:
        doc['secondary']['parts'] = [p for p in sim_parts(doc) if p['root'] not in dropped]
    final = [s for s in shapes + rest_shapes if s['name'] in used]
    for s in rest_shapes:
        _localize(s, tms)
    doc['colliders'] = {'shapes': final, 'chains': chains_out, 'margin_cm': MARGIN_CM,
                        'chain_half_width_cm': dict(sorted(widths.items())),
                        'fitted': {k: {'radius_cm': v.get('radius_cm'), 'radius_p50_cm': v.get('radius_p50_cm'),
                                       'skin': v.get('skin'), 'samples': v.get('samples')}
                                   for k, v in sorted(meas.items()) if '@' not in k}}
    return doc


def rest_flap_name(behaviour, shape):
    """The rest copy of body `shape` shared by the flaps of one behaviour."""
    return 'rest_%s_%s_%s' % (FLAP_REST_GROUP, behaviour, shape)


def flap_groups(chains_out, groups_of):
    """docs/specs/2026-10-27-secondary-motion-backends-design.md §3.7 step 2 (the rig and the analysis must share one collision model). Flaps of one group
    (their behaviour) share one spawner, so they share its properties: the group's smallest particle radius and the
    UNION of its members' colliders (reach only prunes what a flap cannot touch; where the group's rest copy of a shape
    is in the union, the full shape is dropped, so a flap resting on it is not pushed off). Written into every member's
    entry, so the profile states what the rig simulates and what the clearance check tests."""
    groups = {}
    for root, spec in chains_out.items():
        if spec.get('kind') == 'flap':
            groups.setdefault(groups_of.get(root), []).append(root)
    for group, roots in groups.items():
        union = {n for r in roots for n in chains_out[r]['collides_with']}
        union = sorted(n for n in union if rest_flap_name(group, n) not in union)
        radius = min(chains_out[r]['particle_radius_cm'] for r in roots)
        for r in roots:
            chains_out[r].update(collides_with=union, particle_radius_cm=radius, group=group)


def spawners(chains):
    """[(node name, root bones, spec)]: one SpawnDynamicsChains per chain (its own radius and exclusions), and one per
    flap group (flap_groups already gave every member the group's settings; a member that disagrees is an error).
    One spawner per feather made the rig 39 spawners x ~70 exclusion pins, and its build ran for many minutes
    (2026-10-27). A flap group is its behaviour (flap_groups), so its one ParticleProperties is that behaviour's."""
    out, groups = [], {}
    for root, spec in sorted(chains.items()):
        if spec.get('kind') == 'flap':
            groups.setdefault(spec.get('group'), []).append((root, spec))
        else:
            out.append(('Chain_%s' % root, [root], spec))
    for group, members in sorted(groups.items(), key=lambda kv: str(kv[0])):
        first = members[0][1]
        # The group's spawner carries ONE ParticleProperties, so its members must share the behaviour it comes from: a
        # flap whose behaviour changed without a refit would otherwise get its group's settings.
        key = lambda s: (s['collides_with'], s['particle_radius_cm'], s.get('behaviour'))
        for root, spec in members:
            if key(spec) != key(first):
                raise ValueError('flap %s disagrees with its group %s: re-run the intake fit' % (root, group))
        out.append(('Flaps_%s' % group, [m[0] for m in members], first))
    return out
