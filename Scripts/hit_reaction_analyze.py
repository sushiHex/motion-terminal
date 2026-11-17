"""Hit reaction, the measures: pure Python over drive records (pie_dynamics_drive.py with a `hit`).
Spec: docs/specs/2026-10-29-hit-reaction-design.md.

    response(rec, bones, legs)         hit pawn 0 against no-hit pawn 1, same run: peak deviation (cm, deg), what the
                                       springs gave back before the fade began, the full return, the legs, the
                                       secondary parts' reaction, each impact on its target body
    fidelity(rec_on, rec_off, bones)   reaction-on vs reaction-off NO-HIT pawns, frame for frame (cm, deg): the springs
                                       must hold the animated pose (two equally sagging or twisted poses would pass
                                       `response`); body bones AND legs
    articulation(rec, window)          per side, each arm joint's peak rotation relative to its parent, and the hips,
                                       over the reaction window
    penetration(rec)                   per arm-torso pair, the hit overlap beyond the unhit pawn's at the same frame
    validate(rec, bones)               every required transform (and every one the bodies' geometry reads) present,
                                       finite and a unit quaternion, every frame
    verdict(...)                       the pass/fail rows

    python Scripts/hit_reaction_analyze.py <hit record> <reaction-off record> [<no-impulse record>]
"""
import json
import math
import sys

import hit_reaction_core

RECOVER_SHARE = 0.10        # returned when back within max(floor, this share of the peak), position AND rotation...
RECOVER_FLOOR_CM = 1.0
RECOVER_FLOOR_DEG = 2.0
RECOVER_DWELL_S = 0.25      # ...and staying there this long
RESPOND_MIN_CM = 3.0        # a hit must move the upper body at least this much (a flinch the eye sees at 1080p)
RETURN_MAX_S = 1.5          # and the pose must be the animation's again within this (springs, then the fade)
SPRING_SHARE_MAX = 0.6      # the SPRINGS must have given back at least 40% of the peak before the fade starts
LEGS_MAX_CM = 0.5           # the planted bones - the feet under leg IK, else the whole kinematic leg - must not move...
LEGS_MAX_DEG = 1.0          # ...or turn
# Under leg IK a knee bends with the hips. The hit is meant to squat, so the deeper knee must bend
# at least KNEE_SQUAT_MIN_DEG off the animation, and neither more than KNEE_MAX_DEG (past it the hit folds the legs: a
# collapse, not a flinch). Was a 30 deg ceiling alone, which broke on the squat that lets the hips give with a downward hit and puts the leg IK to work (Aurora sweep 2026-11-06, knee
# bend l / r deg: shipped 12 / 14; hip damping 1 + a chest hit tilted down 50 deg, 38 / 45, feet 0.01 cm, unhit held).
KNEE_MAX_DEG = 60.0
KNEE_SQUAT_MIN_DEG = 25.0
KNEE_SIDE_MAX = 1.0         # a knee within its own offset of the animated knee is on its side (articulation)
KNEE_SIDE_MIN_CM = 1.0      # both knees nearer their hip-foot line than this point nowhere: no side to judge
LEG_STRETCH_MAX_CM = 0.5    # a thigh or calf longer or shorter than the animation's by more: the leg IK deformed it
# The springs may lag the animation by this much, unhit. 1.0 cm until 2026-10-30, when Khaimera's arm (ten times
# Aurora's) held its unhit hand at 1.43 cm: only a stiffer shoulder brought it lower, and that put the volley back into
# the clavicles (shoulder target 1.4x: 1.16 cm, the clavicle out-turned the shoulder). Bound: 1.5 cm.
FIDELITY_MAX_CM = 1.5
FIDELITY_MAX_DEG = 3.0
SECONDARY_MIN_CM = 1.0      # a secondary part reacts when its tip moves this much more, hit against unhit...
SECONDARY_SHARE = 0.5       # ...and at least this share of the parts must
IMPACT_MIN_CM = 2.0         # each impact's target bone jumps this far along the push...
IMPACT_WINDOW_S = 0.1       # ...within this long of it
DELIVERY_LATE_S = 0.025     # an impact applied more than ~1.5 frames after its schedule is a hitch
# Where the rotation goes (a visual check found the clavicles taking the volley, the shoulders, elbows and wrists
# stayed set, the hips never moved): on EACH side, every distal arm joint turns at least this much and the clavicle no
# more than this share of that side's largest; the hips turn within the reaction, and not without an impulse.
ARTICULATE_JOINTS = ('shoulder', 'elbow', 'wrist')
# Each anatomical joint as (child role, parent role) on one side, the parent None for the spine top. Role bones, not
# bodies: a helper between them (a twist bone carrying the role below it) is spanned, never counted alone.
ARM_JOINTS = {'clavicle': ('clavicle', None), 'shoulder': ('upperarm', 'clavicle'), 'elbow': ('lowerarm', 'upperarm'),
              'wrist': ('hand', 'lowerarm')}
ARTICULATE_MIN_DEG = 5.0
CLAVICLE_SHARE_MAX = 1.0
HIPS_MIN_DEG = 2.0
# An arm-torso pair may overlap this much beyond the unhit pose's own overlap: an arm pressed against the hip, not one
# through it (the wide radial volley drove a forearm 7.8 cm into the pelvis, Aurora v4).
PENETRATION_TOL_CM = 2.0
# A pair overlapping at least this deep through the whole settled stand is anatomy (penetration), not measured.
ANATOMY_MIN_CM, STAND_S, STAND_MIN_FRAMES = 1.0, 0.5, 10


def _tm(row, pawn, bone):
    return row['pawns'][pawn]['tm'][bone]


def _rot_deg(qa, qb):
    """The angle between two rotations. Normalised: the drive records quaternions to 6 decimals, and unnormalised two
    identical poses read ~0.2 deg apart through a composed relative rotation."""
    na, nb = math.sqrt(sum(x * x for x in qa)) or 1.0, math.sqrt(sum(x * x for x in qb)) or 1.0
    d = abs(sum(x * y for x, y in zip(qa, qb))) / (na * nb)
    return math.degrees(2.0 * math.acos(min(1.0, d)))


def geometry_bones(rec):
    """The bones the reaction bodies' geometry reads: each body's own, and an older record's aimed element's aim bone
    (hit_reaction_core.body_capsules poses it from the live aim; absent, the capsule fell back to a sphere on the joint
    and every clearance carried on). A resolved element's 'from_aim' is information only:
    nothing poses from it and the drive does not record it (hit_reaction_run tm_bones: the bodies and the legs)."""
    out = []
    for body in (rec.get('config') or {}).get('reaction_bodies') or []:
        out.append(body['bone'])
        out += [e['aim'] for e in body.get('elements') or () if e.get('aim') and 'center' not in e]
    return list(dict.fromkeys(out))


def validate(rec, bones, pawns=(0, 1)):
    """[problem, ...]: a missing, non-finite or non-unit transform anywhere a measure reads (a
    NaN head once hid inside max()): bones, and every bone the bodies' geometry reads (geometry_bones)."""
    bones = list(dict.fromkeys(list(bones) + geometry_bones(rec)))
    bad = []
    for r in rec['frames']:
        for p in pawns:
            tm = r['pawns'][p].get('tm', {})
            for b in bones:
                v = tm.get(b)
                if v is None or len(v) != 7:
                    bad.append('frame %s pawn %d: %s missing' % (r['f'], p, b))
                elif not all(isinstance(x, (int, float)) and math.isfinite(x) for x in v):
                    bad.append('frame %s pawn %d: %s not finite' % (r['f'], p, b))
                elif abs(math.sqrt(sum(x * x for x in v[3:])) - 1.0) > 1e-3:
                    bad.append('frame %s pawn %d: %s rotation not unit' % (r['f'], p, b))
            if len(bad) > 20:
                return bad
    return bad


def _series(rows_a, pa, rows_b, pb, bones):
    """Per frame: (t, worst position delta cm, worst rotation delta deg) over bones, a's pawn against b's. Rows are
    paired by frame index and must agree on it (two runs at the same fixed step)."""
    if len(rows_a) != len(rows_b):      # zip would silently compare a truncated record
        raise ValueError('records of different lengths: %d vs %d frames' % (len(rows_a), len(rows_b)))
    out = []
    for ra, rb in zip(rows_a, rows_b):
        if ra['f'] != rb['f']:
            raise ValueError('frames out of step: %s vs %s' % (ra['f'], rb['f']))
        dp = max(math.dist(_tm(ra, pa, b)[:3], _tm(rb, pb, b)[:3]) for b in bones)
        dr = max(_rot_deg(_tm(ra, pa, b)[3:], _tm(rb, pb, b)[3:]) for b in bones)
        out.append((ra['t'], dp, dr))
    return out


def hits_of(rec):
    """The impacts the drive applied, in order (records before the volley carry one 'hit')."""
    ev = rec.get('events') or {}
    return ev.get('hits') or ([ev['hit']] if ev.get('hit') else [])


def _centre(body, tms, bone):
    """Where the node sees a body: its centre of mass (hit_reaction_core.body_com, volume-weighted as Chaos) where the
    record carries the body's shapes (a focused impact turns the struck body about its joint, where its bone origin
    sits still), else its bone origin. An older record's aimed element has no bone-local centre: there the mean of its
    capsules, posed from the live aim bone (an approximation)."""
    if body is None:
        return tms[bone][:3]
    try:
        return hit_reaction_core.body_com(body, tms)
    except ValueError:
        caps = body_capsules(body, tms)
        return [sum((u[i] + v[i]) / 2 for u, v, _r in caps) / len(caps) for i in range(3)] if caps else tms[bone][:3]


def recipients(row, hit, centres):
    """The bodies the impulse reached: those whose centre ({bone: component-space [x, y, z]}, the hit pawn's) lies, in
    the world (its mesh_w: the drive placed the origin in the world), within the impact's radius of its recorded
    origin (AnimNode_RigidBodyWithControl.cpp:1333-1349 -> FActorHandle::AddRadialForce, ImmediatePhysicsActorHandle_
    Chaos.cpp:540: the centre of mass, Mag > Radius skipped). None when the record cannot say (no origin, no mesh_w)."""
    origin = ((hit.get('pawns') or [{}])[0]).get('origin')
    if not origin or not row['pawns'][0].get('mesh_w'):
        return None
    return sorted(b for b, v in centres.items()
                  if math.dist(_world(row, v), origin) <= hit['config'].get('radius', 0.0))


def _world(row, v):
    mx, my, mz, yaw = row['pawns'][0]['mesh_w'][:4]
    c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    return [c * v[0] - s * v[1] + mx, s * v[0] + c * v[1] + my, v[2] + mz]


def _drive_pose(by_f, hit, f0, at):
    """The row whose pose the impulse was placed from: of the frame before the impact and its own, the one whose target
    centre lies where the drive put it (origin + push x offset_cm). The drive reads the pose on the game thread at its
    tick; which row that matches depends on the tick order, and on a body still moving fast from an earlier impact one
    frame is centimetres: the head, at 1.6 cm of margin inside the radius, read as reached by nothing (Khaimera r1)."""
    origin = ((hit.get('pawns') or [{}])[0]).get('origin')
    cands = [by_f[f] for f in (f0, f0 + 1) if f in by_f and by_f[f]['pawns'][0].get('mesh_w')]
    if not origin or not cands:
        return by_f[f0]
    d = hit['config']['dir']
    n = math.sqrt(sum(x * x for x in d)) or 1.0
    want = [o + x / n * hit['config'].get('offset_cm', 0.0) for o, x in zip(origin, d)]
    return min(cands, key=lambda r: math.dist(_world(r, at(r, 0, hit['config']['bone'])), want))


def impact(rows, hit, bodies=(), rec_bodies=None):
    """How far the impact's TARGET body (its centre: _centre) moved along the push within IMPACT_WINDOW_S BEYOND the
    motion already under way: its hit-minus-unhit offset against that offset carried on from the frames before the
    impact. An earlier impact's recovery continuing along the push does not count (the
    plain displacement passed a recovery with no second impulse); it is an estimate - the recovery is not linear -
    backed by the no-impulse control. And the bodies it reached (recipients, over the measured bones and the recorded
    reaction bodies): the strongest response over the impact's region passed a pushed neighbour while the struck body
    never moved, so the target alone is measured and a neighbour in its reach shows."""
    bone = hit['config']['bone']
    by_f = {r['f']: r for r in rows}
    f0 = hit['frame'] - 1               # the frame BEFORE (the pose sampled on the impact's own frame may carry it)
    if any(f not in by_f for f in (f0, f0 - 1, f0 - 2)):
        return {'bone': bone, 'jump_cm': None, 'recipients': None}
    # The record's transforms are COMPONENT space (the mesh is yawed, -90 deg on the Paragon heroes); the push is a
    # world direction: turn it into the component frame (Aurora v1 measured along the wrong axis).
    yaw = math.radians((by_f[f0]['pawns'][0].get('mesh_w') or [0, 0, 0, 0.0])[3])
    w = hit['config']['dir']
    n = math.sqrt(sum(x * x for x in w)) or 1.0
    c, s = math.cos(-yaw), math.sin(-yaw)
    d = [(c * w[0] - s * w[1]) / n, (s * w[0] + c * w[1]) / n, w[2] / n]
    shapes = {b['bone']: b for b in rec_bodies or ()}
    at = lambda r, p, b: _centre(shapes.get(b), r['pawns'][p]['tm'], b)   # noqa: E731
    # the simulated bodies only: a kinematic one has no inverse mass and the node skips it (...Control.cpp:1336)
    tm0 = by_f[f0]['pawns'][0]['tm']
    reach = [b for b in dict.fromkeys([bone] + list(bodies) + list(shapes))
             if b in tm0 and (shapes.get(b) or {}).get('physics_type') != 'kinematic']
    pose = _drive_pose(by_f, hit, f0, at)
    got = recipients(pose, hit, {b: at(pose, 0, b) for b in reach})
    off = lambda r: [a - x for a, x in zip(at(r, 0, bone), at(r, 1, bone))]   # noqa: E731
    win = [r for r in rows if r['f'] >= hit['frame'] and r['t'] - hit['t'] <= IMPACT_WINDOW_S]
    if not win:
        return {'bone': bone, 'jump_cm': None, 'recipients': got}
    # carried on at its pre-impact velocity AND acceleration (three frames): a constant-velocity extrapolation read a
    # recovery's own curvature as an impulse (110 exp(-3t), no second hit, 2.2 cm)
    o0, o1, o2 = off(by_f[f0]), off(by_f[f0 - 1]), off(by_f[f0 - 2])
    v = [a - x for a, x in zip(o0, o1)]
    acc = [a - 2 * x + y for a, x, y in zip(o0, o1, o2)]

    def pred(n):
        return [x + vv * n + 0.5 * aa * n * (n + 1) for x, vv, aa in zip(o0, v, acc)]
    j = max(sum((a - p) * k for a, p, k in zip(off(r), pred(r['f'] - f0), d)) for r in win)
    return {'bone': bone, 'dir': hit['config']['dir'], 'at_s': hit['config'].get('at_s'), 'jump_cm': round(j, 3),
            'recipients': got}


def delivery(rec):
    """Every scheduled impact applied once, in order, on time (a hitch queues overdue impacts together, possibly into
    the fade)."""
    planned = sorted((rec.get('config') or {}).get('hits') or [], key=lambda h: h['at_s'])
    got = hits_of(rec)
    late = [round(h['el'] - p['at_s'], 4) if 'el' in h else None for h, p in zip(got, planned)]
    # and each is the planned impact, received by the planned pawns (index pairing alone passed a wrong hit at the
    # right time)
    wrong = [i for i, (h, p) in enumerate(zip(got, planned))
             if h.get('config') != p or sorted(x.get('pawn') for x in h.get('pawns') or []) != sorted(p['pawns'])]
    return {'planned': len(planned), 'applied': len(got), 'late_s': late, 'wrong': wrong,
            'ok': bool(planned) and len(got) == len(planned) and not wrong
            and all(x is not None and 0 <= x <= DELIVERY_LATE_S for x in late)}


def response(rec, bones, legs):
    hits = hits_of(rec)
    if not hits:
        return {'error': 'no hit in the record'}
    rows = [r for r in rec['frames'] if r['f'] >= hits[0]['frame']]
    s = _series(rows, 0, rows, 1, bones)
    peak_i = max(range(len(s)), key=lambda i: s[i][1])
    peak, peak_deg = s[peak_i][1], max(x[2] for x in s)
    bound = (max(RECOVER_FLOOR_CM, RECOVER_SHARE * peak), max(RECOVER_FLOOR_DEG, RECOVER_SHARE * peak_deg))
    # The return counts from the LAST impact: from its own peak on, back within the bound and staying.
    last = hits[-1]
    i0 = next(i for i, r in enumerate(rows) if r['f'] >= last['frame'])
    last_peak = max(range(i0, len(s)), key=lambda i: s[i][1])
    back = None
    for i in range(last_peak, len(s)):
        # back within the bound and staying there to the end of the record (one dwell then a later departure passed);
        # at least a dwell of record must remain
        if s[-1][0] - s[i][0] >= RECOVER_DWELL_S and all(x[1] <= bound[0] and x[2] <= bound[1] for x in s[i:]):
            back = round(s[i][0] - s[i0][0], 3)
            break
    legs_cm = max(math.dist(_tm(r, 0, b)[:3], _tm(r, 1, b)[:3]) for r in rec['frames'] for b in legs)
    legs_deg = max(_rot_deg(_tm(r, 0, b)[3:], _tm(r, 1, b)[3:]) for r in rec['frames'] for b in legs)
    # What the SPRINGS gave back before the fade began (the fade hands the pose to the animation whatever the springs
    # did): a return that is only the fade must not read as a spring recovery.
    sched = (rec.get('config') or {}).get('reaction_schedule')
    at_fade = None
    if sched:
        fade_t = s[i0][0] + sched['hold_s']
        at_fade = round(next((x[1] for x in s if x[0] >= fade_t), s[-1][1]), 3)
    # The secondary parts (hair, cloth) must react to the hit: their tips, hit against unhit, after the impulse.
    tips = sorted(rows[0]['pawns'][0].get('tips', {}))
    moved = [k for k in tips if max(math.dist(r['pawns'][0]['tips'][k], r['pawns'][1]['tips'][k]) for r in rows)
             >= SECONDARY_MIN_CM]
    return {'peak_cm': round(peak, 3), 'peak_deg': round(peak_deg, 3), 'at_fade_cm': at_fade,
            'peak_at_s': round(s[peak_i][0] - s[0][0], 3), 'return_s': back,
            # the springs are judged against the LAST impact's own peak, not an earlier, larger one
            'last_peak_cm': round(s[last_peak][1], 3),
            'return_bound': [round(bound[0], 3), round(bound[1], 3)],
            'legs_cm': round(legs_cm, 4), 'legs_deg': round(legs_deg, 4),
            'secondary_moved': len(moved), 'secondary_parts': len(tips),
            'impacts': [impact(rec['frames'], x, bones, (rec.get('config') or {}).get('reaction_bodies'))
                        for x in hits], 'delivery': delivery(rec), 'hit': hits}


def _qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return [aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz]


def _local(row, pawn, child, parent):
    p = _tm(row, pawn, parent)[3:]
    return _qmul([-p[0], -p[1], -p[2], p[3]], _tm(row, pawn, child)[3:])


def reaction_window(rec):
    """(first frame, end s) of the reaction: from the first impact's frame to the last impact + hold_s + fade_s (the
    drive's reaction_schedule; to the record's end without one, the reaction then held). None without an impact."""
    hits = hits_of(rec)
    if not hits:
        return None
    sched = (rec.get('config') or {}).get('reaction_schedule')
    return hits[0]['frame'], (hits[-1]['t'] + sched['hold_s'] + sched['fade_s']) if sched else math.inf


def articulation(rec, window=None):
    """Where the hit's rotation went, hit pawn against unhit, over the reaction window (reaction_window(rec) unless
    given; the end is the record's own game time, so a record is read over its own window). Per side (rec['roles']), each anatomical joint
    (ARM_JOINTS: the clavicle against the spine top, the shoulder, elbow and wrist each against the role bone above),
    its peak rotation relative to its parent; the pelvis's own component-space rotation, the hips; and, for printing,
    the joints pooled by the child body's role class over the recorded constraints. A visual check found the
    clavicles taking the volley while the shoulders, elbows and wrists stayed set. Pooled over both arms and their helpers
    a right arm whose elbow never moved passed behind a left one that did, and the hips
    read over the whole record passed a turn after the reaction had faded."""
    window = window or reaction_window(rec)
    if window is None:
        return {}
    rows = [r for r in rec['frames'] if r['f'] >= window[0] and r['t'] <= window[1]]
    tm0 = rows[0]['pawns'][0]['tm'] if rows else {}

    def peak(child, parent):
        if child not in tm0 or parent not in tm0:
            return None
        return round(max(_rot_deg(_local(r, 0, child, parent), _local(r, 1, child, parent)) for r in rows), 3)
    roles = rec.get('roles') or {}
    top = (roles.get('spine') or [None])[-1]
    sides = {}
    for s in ('l', 'r'):
        bone = {k: roles.get('%s_%s' % (k, s)) for k in hit_reaction_core.ARM_ROLES}
        if any(bone.values()):
            sides[s] = {j: peak(bone[c], bone[p] if p else top) for j, (c, p) in ARM_JOINTS.items()}
    cfg = rec.get('config') or {}
    role = {b['bone']: b.get('role') or '' for b in cfg.get('reaction_bodies') or []}
    pooled = {}
    for c in cfg.get('reaction_constraints') or []:
        cls, deg = hit_reaction_core.role_class(role.get(c['child'], '')), peak(c['child'], c['parent'])
        if deg is not None:
            pooled[cls] = max(pooled.get(cls, 0.0), deg)
    pelvis = roles.get('pelvis')
    hips = hips_cm = None
    if pelvis in tm0:
        hips = round(max(_rot_deg(_tm(r, 0, pelvis)[3:], _tm(r, 1, pelvis)[3:]) for r in rows), 3)
        hips_cm = round(max(math.dist(_tm(r, 0, pelvis)[:3], _tm(r, 1, pelvis)[:3]) for r in rows), 3)
    # under leg IK each knee follows the animation by its bend (the calf against its thigh: a squat bends it) and by
    # the side it points to: its pole, the knee off the hip-foot line, hit against unhit, POSITIONALLY - |hit - unhit|
    # over the larger of the two. A knee on its own side stays within its offset (<= 1; 60 deg apart at equal size); a
    # knee turned to the other side of the leg is the sum of both (> 1). A leg turned about the hip-foot line keeps the
    # bend and reverses the knee; an angle between two NORMALISED poles read 116 deg off a
    # 0.66 cm one on a straight leg, so a frame whose knees are both nearly on the line has no side.
    def side(g):
        import collider_autofit as af
        if not all(b in tm0 for b in (g['thigh'], g['calf'], g['foot'])):
            return None, 0
        worst, none = 0.0, 0
        for r in rows:
            poles = []
            for p in (0, 1):
                h, k, f = (_tm(r, p, b)[:3] for b in (g['thigh'], g['calf'], g['foot']))
                poles.append([a - b for a, b in zip(k, af.seg_closest(k, h, f)[1])])
            size = max(math.hypot(*v) for v in poles)
            if size < KNEE_SIDE_MIN_CM:
                none += 1
                continue
            worst = max(worst, math.dist(*poles) / size)
        return (round(worst, 4) if none < len(rows) else None), none

    # ...and each leg keeps its length: a straight leg gives LegIK no bend direction, and it then SHORTENS the bones
    # toward a nearer target instead of bending (AnimNode_LegIK.cpp:363, :468), with no bend and no side to see.
    def stretch(g):
        if not all(b in tm0 for b in (g['thigh'], g['calf'], g['foot'])):
            return None
        seg = lambda r, p, a, b: math.dist(_tm(r, p, a)[:3], _tm(r, p, b)[:3])   # noqa: E731
        return round(max(abs(seg(r, 0, a, b) - seg(r, 1, a, b)) for r in rows
                         for a, b in ((g['thigh'], g['calf']), (g['calf'], g['foot']))), 4)
    # the bend itself: the knee's flexion (the shin against the thigh line, 0 straight), hit against unhit. Not the
    # calf's whole rotation against its parent, which counts its twist too.
    def flexion(r, p, g):
        h, k, f = (_tm(r, p, b)[:3] for b in (g['thigh'], g['calf'], g['foot']))
        u, v = [x - y for x, y in zip(k, h)], [x - y for x, y in zip(f, k)]
        c = sum(x * y for x, y in zip(u, v)) / max(math.hypot(*u) * math.hypot(*v), 1e-9)
        return math.degrees(math.acos(max(-1.0, min(1.0, c))))

    def bend(g):
        if not all(b in tm0 for b in (g['thigh'], g['calf'], g['foot'])):
            return None
        return round(max(abs(flexion(r, 0, g) - flexion(r, 1, g)) for r in rows), 3)
    knees = {}
    for g in cfg.get('leg_ik') or []:
        ratio, none = side(g)
        knees[g['calf']] = {'bend_deg': bend(g), 'side': ratio, 'sideless_frames': none, 'stretch_cm': stretch(g)}
    # Where the hit goes between the hips and the head (the hit whipped her head too far backwards
    # when more of the impact should be absorbed by her hip movement): the head's peak rotation against the chest (the
    # whip) and against the pelvis (the whole upper body bending over the hips), hit against unhit.
    head = roles.get('head')
    whip = peak(head, top) if head and top else None
    bend_back = peak(head, pelvis) if head and pelvis else None
    return {'sides': sides, 'joint_peak_deg': pooled, 'hips_deg': hips, 'hips_cm': hips_cm, 'knees': knees,
            'head_whip_deg': whip, 'head_over_hips_deg': bend_back,
            'window': [window[0], window[1] if math.isfinite(window[1]) else None]}


body_capsules = hit_reaction_core.body_capsules
seg_distance = hit_reaction_core.seg_distance
clearance = hit_reaction_core.clearance


def penetration(rec, pairs=None):
    """Per arm-torso pair (hit_reaction_core.collide_pairs, "a|b"), over the record (cm, positive = passing through):
    the worst overlap on the hit pawn 0 ('hit'), on the unhit pawn 1 ('unhit'), and the worst EXCESS, frame by frame
    the hit overlap less the unhit pawn's own at that same frame (none below zero: a pair the pose keeps apart gives no
    credit). The worst hit and unhit frames come from different times; their difference cancelled a hit overlap
    against an unhit one elsewhere in the record. A non-finite shape raises: min() kept
    the last finite clearance and dropped a NaN one."""
    bodies = (rec.get('config') or {}).get('reaction_bodies') or []
    by_bone = {b['bone']: b for b in bodies}
    pairs = pairs or hit_reaction_core.collide_pairs(bodies)
    used = list(dict.fromkeys(x for pair in pairs for x in pair))
    out = {'hit': {}, 'unhit': {}, 'excess': {}}
    for r in rec['frames']:
        tms = [r['pawns'][p]['tm'] for p in (0, 1)]
        for p, t in enumerate(tms):
            for x in used:
                caps = body_capsules(by_bone[x], t)
                # an absent legacy aim bone would pose its capsule as a sphere on the joint (geometry_bones)
                lost = [e['aim'] for e in by_bone[x]['elements'] if e.get('aim') and 'center' not in e
                        and e['aim'] not in t]
                if lost or not caps or not all(math.isfinite(v) for u, w, rad in caps
                                               for v in list(u) + list(w) + [rad]):
                    raise ValueError('frame %s pawn %d: body %s shape not finite (or not posed%s)' % (
                        r['f'], p, x, ': no %s' % lost if lost else ''))
        for a, b in pairs:
            k = '%s|%s' % (a, b)
            hit, unhit = (-clearance(bodies, t, a, b) for t in tms)
            for name, v in (('hit', hit), ('unhit', unhit), ('excess', hit - max(0.0, unhit))):
                out[name][k] = max(out[name].get(k, -math.inf), v)
    # A pair that already overlaps in the idle (the unhit pawn's stand before the first impact) is anatomy, not an arm
    # passing through the body: fitted capsules overlap at the shoulder, Khaimera's upper arm 8.8 cm inside his chest
    # capsule at rest, and a hit pushing it 2.8 cm deeper failed a check meant for an arm through the torso (so
    # only pairs the idle keeps apart are measured, as hit_reaction_core.colliding selects them). Anatomy
    # is an overlap ANATOMY_MIN_CM deep on EVERY frame of the settled stand (the STAND_S before the first impact): one
    # hairline touch exempted a pair that then went 10 cm in. Too short a stand: none.
    hits = hits_of(rec)
    stand = [r for r in rec['frames'] if hits and hits[0]['t'] - STAND_S <= r['t'] < hits[0]['t']]
    # ...the whole stand, without a gap: ten frames over 0.15 s passed as a settled half second
    whole = (len(stand) >= STAND_MIN_FRAMES and stand[0]['t'] <= hits[0]['t'] - 0.9 * STAND_S and
             [r['f'] for r in stand] == list(range(stand[0]['f'], stand[0]['f'] + len(stand))))
    anatomy = sorted('%s|%s' % (a, b) for a, b in pairs if whole and all(
        clearance(bodies, r['pawns'][1]['tm'], a, b) <= -ANATOMY_MIN_CM for r in stand))
    out = {name: {k: round(v, 3) for k, v in d.items()} for name, d in out.items()}
    out['excess'] = {k: v for k, v in out['excess'].items() if k not in anatomy}
    out['anatomy'] = anatomy
    return out


def fidelity(rec_on, rec_off, bones, pawn=1):
    s = _series(rec_on['frames'], pawn, rec_off['frames'], pawn, bones)
    worst = max(range(len(bones)), key=lambda i: max(
        math.dist(_tm(ra, pawn, bones[i])[:3], _tm(rb, pawn, bones[i])[:3])
        for ra, rb in zip(rec_on['frames'], rec_off['frames'])))
    return {'max_cm': round(max(x[1] for x in s), 4), 'max_deg': round(max(x[2] for x in s), 4),
            'worst_bone': bones[worst]}


def _distal(j):
    """One side's shoulder, elbow and wrist peaks (None where the record cannot measure one)."""
    return [j.get(k) for k in ARTICULATE_JOINTS]


def _clavicle_ok(j):
    """One side: the clavicle within its share of the largest of the shoulder, elbow and wrist (a visual
    check found the clavicles taking the volley while the joints below stayed set; a joint the record cannot measure fails)."""
    d = _distal(j)
    return None not in d and j.get('clavicle') is not None and j['clavicle'] <= CLAVICLE_SHARE_MAX * max(d)


def _articulates(j):
    """One side: the shoulder, elbow and wrist each turned enough."""
    d = _distal(j)
    return None not in d and min(d) >= ARTICULATE_MIN_DEG


def arms_ok(sides):
    """Every arm keeps its clavicle within share, and the hit reaches down at least one arm through every joint. One
    impact to the chest (a single hit, then the recovery) lands on one side more than the other:
    Khaimera's far arm turned 5.3 / 3.2 / 3.3 deg while his near arm turned 26 / 9 / 7. Requiring every arm suited
    the volley, which struck each arm on its own."""
    return bool(sides) and all(_clavicle_ok(j) for j in sides.values()) and any(_articulates(j) for j in sides.values())


def verdict(hit_resp, off_resp, fid, no_impulse_resp=None, problems=(), art=None, pen=None, no_impulse_art=None):
    g = lambda d, k, dflt: d.get(k) if d.get(k) is not None else dflt   # noqa: E731
    imps = hit_resp.get('impacts') or []
    rows = [
        ('records valid', not problems, '%d problem(s)%s' % (len(problems), (': ' + problems[0]) if problems else '')),
        ('responds', g(hit_resp, 'peak_cm', 0) >= RESPOND_MIN_CM, '%s >= %s cm' % (hit_resp.get('peak_cm'),
                                                                                RESPOND_MIN_CM)),
        # each target moved, and the impulse reached it alone (a neighbour in its reach is not the target's landing)
        ('every impact lands', bool(imps) and all((x['jump_cm'] or 0) >= IMPACT_MIN_CM
                                                  and x.get('recipients') == [x['bone']] for x in imps),
         '%s >= %s cm within %s s, reaching %s (the target alone)' % (
             [x['jump_cm'] for x in imps], IMPACT_MIN_CM, IMPACT_WINDOW_S, [x.get('recipients') for x in imps])),
        ('volley delivered on schedule', bool((hit_resp.get('delivery') or {}).get('ok')),
         '%(applied)s of %(planned)s impacts, late %(late_s)s s' % (hit_resp.get('delivery') or
                                                                   {'applied': 0, 'planned': '?', 'late_s': None})
         + ' <= %s s' % DELIVERY_LATE_S),
        ('springs restore', hit_resp.get('at_fade_cm') is not None and hit_resp['at_fade_cm']
         <= SPRING_SHARE_MAX * g(hit_resp, 'last_peak_cm', 0),
         '%s cm left at the fade <= %.0f%% of the last impact\'s %s cm peak' % (
             hit_resp.get('at_fade_cm'), SPRING_SHARE_MAX * 100, hit_resp.get('last_peak_cm'))),
        ('returns to the animation', hit_resp.get('return_s') is not None and hit_resp['return_s'] <= RETURN_MAX_S,
         '%s <= %s s (cm and deg)' % (hit_resp.get('return_s'), RETURN_MAX_S)),
        ('feet planted' if hit_resp.get('held') == 'feet' else 'legs untouched',
         g(hit_resp, 'legs_cm', 1e9) <= LEGS_MAX_CM and g(hit_resp, 'legs_deg', 1e9) <= LEGS_MAX_DEG,
         '%s cm / %s deg' % (hit_resp.get('legs_cm'), hit_resp.get('legs_deg'))),
        ('secondary reacts', g(hit_resp, 'secondary_moved', 0) >= SECONDARY_SHARE * max(1, g(hit_resp,
                                                                                            'secondary_parts', 0)),
         '%s of %s parts' % (hit_resp.get('secondary_moved'), hit_resp.get('secondary_parts'))),
        ('reaction off does not respond', g(off_resp, 'peak_cm', 1e9) < RESPOND_MIN_CM,
         '%s < %s cm' % (off_resp.get('peak_cm'), RESPOND_MIN_CM)),
        ('unhit pose held', fid['max_cm'] <= FIDELITY_MAX_CM and fid['max_deg'] <= FIDELITY_MAX_DEG,
         '%s cm (%s) / %s deg' % (fid['max_cm'], fid.get('worst_bone'), fid['max_deg'])),
    ]
    if art is not None:
        sides = art.get('sides') or {}
        rows.append(('arms articulate', arms_ok(sides),
                     'some arm with shoulder / elbow / wrist >= %s deg, every clavicle <= %s x its largest: %s' % (
                         ARTICULATE_MIN_DEG, CLAVICLE_SHARE_MAX, '; '.join('%s: %s, clavicle %s' % (
                             s, _distal(j), j.get('clavicle'))
                             for s, j in sorted(sides.items())) or 'no arm in the record')))
        knees = art.get('knees') or {}
        if knees:
            # a leg straight on every frame has no side to judge (side None): its length still is
            ok = all(k['bend_deg'] is not None and k['bend_deg'] <= KNEE_MAX_DEG
                     and (k['side'] is None or k['side'] <= KNEE_SIDE_MAX)
                     and k.get('stretch_cm') is not None and k['stretch_cm'] <= LEG_STRETCH_MAX_CM
                     for k in knees.values())
            rows.append(('knees follow the hips', ok, '; '.join(
                '%s bend %s <= %s deg off the animation, side %s <= %s (%d frames straight), length %s <= %s cm' % (
                    c, k['bend_deg'], KNEE_MAX_DEG, k['side'], KNEE_SIDE_MAX, k['sideless_frames'],
                    k.get('stretch_cm'), LEG_STRETCH_MAX_CM)
                for c, k in sorted(knees.items()))))
            # the hit must exercise the leg IK (a squat deep enough to show it): the
            # deeper knee bends at least this far off the animation as the hips drop
            deep = max((k['bend_deg'] for k in knees.values() if k['bend_deg'] is not None), default=None)
            rows.append(('the hit squats', deep is not None and deep >= KNEE_SQUAT_MIN_DEG,
                         'deepest knee %s >= %s deg off the animation' % (deep, KNEE_SQUAT_MIN_DEG)))
        rows.append(('hips move', (art.get('hips_deg') or 0.0) >= HIPS_MIN_DEG,
                     '%s >= %s deg within the reaction' % (art.get('hips_deg'), HIPS_MIN_DEG)))
    if no_impulse_art is not None:
        # the negative control: the same schedule without an impulse must leave the hips still, or "hips move" read
        # something other than the hit
        h = no_impulse_art.get('hips_deg')
        rows.append(('no impulse: hips stay still', h is not None and h < HIPS_MIN_DEG,
                     '%s < %s deg over its reaction window' % (h, HIPS_MIN_DEG)))
    if pen is not None:
        deeper = pen.get('excess') or {}
        k = max(deeper, key=deeper.get) if deeper else None
        rows.append(('arms stay out of the body', bool(deeper) and deeper[k] <= PENETRATION_TOL_CM,
                     'deepest beyond the unhit pose at the same frame %s cm (%s: worst hit %s, unhit %s) over %d '
                     'arm-torso pairs <= %s cm' % (k and deeper[k], k, (pen.get('hit') or {}).get(k),
                                                   (pen.get('unhit') or {}).get(k), len(deeper), PENETRATION_TOL_CM)))
    if no_impulse_resp is not None:
        rows.append(('no impulse: responds must be red', g(no_impulse_resp, 'peak_cm', 1e9) < RESPOND_MIN_CM,
                     '%s < %s cm' % (no_impulse_resp.get('peak_cm'), RESPOND_MIN_CM)))
    return {'pass': all(ok for _n, ok, _w in rows), 'rows': [{'check': n, 'ok': ok, 'why': w} for n, ok, w in rows]}


def bones_of(rec):
    """The reaction's measured bones and the legs: every body of the recorded reaction body (helpers included, so none
    escapes the gates), else the torso roles of a record from before it was recorded."""
    r = rec['roles']
    bodies = ((rec.get('config') or {}).get('reaction_bodies') or [])
    upper = [b['bone'] for b in bodies] or [b for b in (r.get('spine') or []) + [r.get('neck'), r.get('head')] if b]
    legs = [r[k] for k in ('thigh_l', 'thigh_r', 'calf_l', 'calf_r', 'foot_l', 'foot_r') if r.get(k)]
    return upper, legs


def planted(rec, legs):
    """The leg bones a hit must not move: the feet when the legs are planted by IK (the record's config leg_ik: the
    thighs and calves then follow the hips by design), else every leg bone (the thighs anchored)."""
    ik = ((rec.get('config') or {}).get('leg_ik')) or []
    return [g['foot'] for g in ik] if ik else legs


def main(paths, take=None):
    """The verdict on a matrix (paths: hit, reaction-off, no-impulse records). take: a rendered take's record, judged in
    place of the matrix's hit run for everything it shows - the response, every impact, the joints, the hips, the
    pass-through - each within the take itself (its hit pawn against its own unhit twin); the controls and the unhit
    pawn's fidelity stay the matrix's, whose drives share one fixed step (two drives' clocks differ by part of a frame,
    which a per-frame comparison read as 20 cm during the jog). The page's figures are the take's."""
    recs = [json.load(open(p)) for p in paths]
    shown = json.load(open(take)) if take else recs[0]
    upper, legs = bones_of(shown)
    measured = list(dict.fromkeys(upper + legs))      # the thighs are reaction bodies AND legs
    problems = [p for rec in recs + ([shown] if take else []) for p in validate(rec, measured)]
    if problems:
        out = {'verdict': verdict({}, {}, {'max_cm': 1e9, 'max_deg': 1e9}, None, problems), 'problems': problems[:20]}
    else:
        held = planted(shown, legs)
        hit, off = response(shown, upper, held), response(recs[1], upper, held)
        # the legs too: a lower-leg corruption shared by both reaction-on pawns holds "legs untouched", which compares
        # the two
        fid = fidelity(recs[0], recs[1], measured)
        noimp = response(recs[2], upper, held) if len(recs) > 2 else None
        hit['held'] = 'feet' if held != legs else 'legs'
        strip = lambda d: d and {k: v for k, v in d.items() if k != 'hit'}   # noqa: E731
        art = articulation(shown)
        # over its OWN window: hit_reaction_run gives the no-impulse run the same volley at zero strength, so its
        # events['hits'] mark the same schedule on its own clock. The hit record's end is absolute PIE game time, and
        # each session's t0 is its first tick, taken before the fixed step (pie_dynamics_drive._tick): a run whose
        # clock started 0.3 s later lost the last 0.3 s of its reaction to that window. No recorded hits: no window,
        # hips None, and the control fails closed.
        noimp_art = articulation(recs[2]) if len(recs) > 2 else None
        # every arm-torso pair, colliding or not: whether an arm passes through the body is the question, not whether
        # the colliding ones hold (the hit against its own unhit twin, so a pair overlapping by design cancels)
        pen = penetration(shown)
        out = {'hit': strip(hit), 'reaction_off': strip(off), 'fidelity': fid, 'no_impulse': strip(noimp),
               'articulation': art, 'no_impulse_articulation': noimp_art, 'penetration': pen,
               'verdict': verdict(hit, off, fid, noimp, (), art, pen, noimp_art), 'impulse': hit.get('hit')}
    print('HIT_REACTION ' + json.dumps(out, indent=1))
    return out


def summary(out, hz=60.0):
    """The verdict's headline figures, rounded as they are reported (the breakdown page cites these, never the raw
    analysis). Per joint, the larger side; penetration, the worst pair's excess over its unhit twin (0 when none)."""
    hit, art, fid = out['hit'], out['articulation'], out['fidelity']
    side = lambda j: round(max(s[j] for s in art['sides'].values()))   # noqa: E731
    rows = out['verdict']['rows']
    return {'peak_cm': round(hit['peak_cm'], 1), 'peak_deg': round(hit['peak_deg']),
            'impacts_cm': [round(i['jump_cm'], 1) for i in hit['impacts']],
            'applied': hit['delivery']['applied'], 'planned': hit['delivery']['planned'],
            'late_frames': round(max(hit['delivery']['late_s'] or [0]) * hz),
            'at_fade_cm': round(hit['at_fade_cm'], 1), 'last_peak_cm': round(hit['last_peak_cm'], 1),
            'return_s': round(hit['return_s'], 2), 'legs_cm': round(hit['legs_cm'], 1),
            'legs_deg': round(hit['legs_deg'], 1), 'secondary_moved': hit['secondary_moved'],
            'secondary_parts': hit['secondary_parts'], 'unhit_cm': round(fid['max_cm'], 2),
            'unhit_deg': round(fid['max_deg'], 1), 'unhit_bone': fid.get('worst_bone'),
            'off_cm': round(out['reaction_off']['peak_cm'], 1), 'noimp_cm': round(out['no_impulse']['peak_cm'], 1),
            'clavicle_deg': side('clavicle'), 'shoulder_deg': side('shoulder'), 'elbow_deg': side('elbow'),
            'wrist_deg': side('wrist'), 'hips_deg': round(art['hips_deg']),
            'penetration_cm': round(max([0.0] + list(out['penetration']['excess'].values())), 1),
            'anatomy': len(out['penetration'].get('anatomy') or []),       # pairs overlapping at rest, not measured
            'checks_passed': sum(1 for r in rows if r['ok']), 'checks': len(rows),
            'pass': out['verdict']['pass']}


if __name__ == '__main__':
    sys.exit(0 if main(sys.argv[1:])['verdict']['pass'] else 1)
