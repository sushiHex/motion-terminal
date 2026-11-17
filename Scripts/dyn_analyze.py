"""Read a pie_dynamics_drive record and report the spike's measurements (witnesses, noise floor). Pure Python, no editor.

    python Scripts/dyn_analyze.py Saved/MotionTerminalTests/khai_dyn_s3.json [more.json ...]

Per pawn: whether every witness-curve read reported found=True, whether MT_DynRigTick advanced on every
sampled frame, how far the pawn travelled, the stop frame (first frame at CMC speed <= 1 cm/s after input
release), any solver event flags, and the hitch frames (dt > H x median dt).
Per pawn pair (pawn 0 vs pawn i): per chain, the max component-space tip delta during the stop window, and
for the control bones outside the chain closure, the max delta across the run.
Per pawn: each chain's adjacent-frame tip motion (max over the run, and in the settle window), which is
the noise floor the solver thresholds are derived from.
"""
import json
import math
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collider_autofit as af  # noqa: E402
import dyn_hero  # noqa: E402

HITCH_H = 3.0          # a hitch is a frame with dt > H x the run's median dt
STOP_W = 1.0           # stop window: [t_release, t_stop + W] seconds
SETTLE_T = 1.5         # settle check starts at t_stop + T seconds
SETTLE_SKIP = 2        # solver events on the first frames are its initialisation, not the drive
SKIP_INIT = 10         # frames excluded from the noise-floor kinematic measurements (the reference-pose snap)
# Records written before the parts list carry no 'parts': their six chains, keyed by chain name, as recorded.
_LEGACY_TIPS = {'loin_cloth_bk': 'loin_cloth_bk_03', 'loin_cloth_fr': 'loin_cloth_fr_03',
                'torso_cloth_fr_l': 'torso_cloth_fr_l_03', 'torso_cloth_fr_r': 'torso_cloth_fr_r_03',
                'torso_cloth_bk_l': 'torso_cloth_bk_l_02', 'torso_cloth_bk_r': 'torso_cloth_bk_r_02'}


def record_parts(rec):
    """The simulated parts a record measured: [{'id', 'root', 'tip', 'bones', 'parent'}]. 'id' keys the record's
    'tips' dict. A legacy record's parts are rebuilt from its own chain-bone names."""
    if 'parts' in rec:
        return [{k: p.get(k) for k in ('id', 'root', 'tip', 'bones', 'parent')} for p in rec['parts']]
    names = sorted(rec['frames'][0]['pawns'][0].get('chain', {})) if rec.get('frames') else []
    return [{'id': c, 'root': c + '_01', 'tip': t, 'bones': [b for b in names if b.startswith(c + '_')],
             'parent': 'pelvis' if c.startswith('loin') else 'spine_03'} for c, t in _LEGACY_TIPS.items()]


# Gate (g): the simulated loincloth bones (not the kinematic roots) against each leg's thigh axis, hip joint to
# knee. The bound is the thigh capsule radius + the particle radius (dynamics_rig THIGH_RADIUS, engine
# RigDynamicsDefaultParticleRadius 5 cm).
# The hero profile a record falls back to when it has no snapshot of its own: the record's hero, else MT_HERO. Records
# from before heroes were parameters name none; they always carry their collider snapshot except those from before the parts list.
_FALLBACK_HERO = {'rig': None}
# The skeleton's names for the body roles the leg checks use (the record's `roles` snapshot; the canonical names when
# a record predates it, which is what the first hero's skeleton uses).
ROLES = {}


def _set_roles(rec):
    global ROLES
    ROLES = dict(rec.get('roles') or {})


def _role(name):
    return ROLES.get(name) or name


def _record_profile(rec, parts):
    """What a record was measured against: its own collider snapshot and parts (today's profile on
    yesterday's transforms re-scores old runs, and legacy `chains` could disagree with the parts simulated). A record
    from before the snapshot falls back to the profile on disk."""
    if 'colliders' not in rec:
        return None
    return {'colliders': rec['colliders'], 'chains': [{'root': pt['root'], 'bones': pt['bones']} for pt in parts]}


def _nonfinite(p):
    """How many recorded numbers in a pawn's samples are NaN or infinite (positions, transforms, curve values, speed):
    aggregates downstream would silently drop them."""
    bad = 0

    def walk(v):
        nonlocal bad
        if isinstance(v, float) and not math.isfinite(v):
            bad += 1
        elif isinstance(v, dict):
            for w in v.values():
                walk(w)
        elif isinstance(v, (list, tuple)):
            for w in v:
                walk(w)

    for x in p:
        walk({k: x.get(k) for k in ('chain', 'tips', 'ctrl', 'tm', 'loc', 'speed', 'curves', 'mesh_w')})
    return bad


def _flap_tips(rec, parts):
    """A flap's tip bone is its own kinematic root: its motion is the tail particle, a rig Null no socket reads. Place
    each flap's tip at its tail end, rebuilt from the recorded bone transform and the tail offset the record carries,
    as a pseudo chain point '<id>.tip' (and the record's 'tips' entry), so every tip measure sees the swing."""
    tails = rec.get('tails', {})
    for pt in parts:
        if pt['tip'] != pt['root'] or pt['root'] not in tails:
            continue
        name = pt['id'] + '.tip'
        for f in rec['frames']:
            for x in f['pawns']:
                tm = x.get('tm', {}).get(pt['root'])
                if tm is not None:
                    end = [round(v, 4) for v in af.add(tm[:3], af.qrot(tm[3:], tails[pt['root']]))]
                    x['chain'][name] = end
                    x['tips'][pt['id']] = end
        pt['tip'] = name


def _spine(pt):
    """Root -> tip on the unbranched run (a flap: its bone, then its tail end)."""
    return pt['bones'][:pt['bones'].index(pt['tip']) + 1] if pt['tip'] in pt['bones'] else pt['bones'] + [pt['tip']]


def _profile():
    """The fallback hero's profile on disk, or {} when no hero is known (never a guessed one)."""
    try:
        return dyn_hero.resolve(_FALLBACK_HERO['rig'])['profile']
    except ValueError:
        return {}


# The capsule radius the rig now uses is the profile's fitted one (was a hand-set 9 cm); 9.0 only as a fallback.
def _leg_geometry(col):
    """The thigh capsule radius the rig used: the fitted one + the margin from a collider section (9.0 only when the
    section has none). Set per record from its own snapshot (today's profile re-scored old runs)."""
    global THIGH_RADIUS, CLEARANCE_BOUND
    fit = (col or {}).get('fitted', {}).get('thigh_l', {}).get('radius_cm')
    THIGH_RADIUS = fit + (col or {}).get('margin_cm', 1.0) if fit else 9.0
    CLEARANCE_BOUND = THIGH_RADIUS + PARTICLE_RADIUS


PARTICLE_RADIUS = 5.0
_leg_geometry(None)       # set per record in analyze(), from its own snapshot
PAIRED_TOL = 2.5          # gate (g): ON may come at most half a particle radius closer than OFF on the same frame


def _core(x, side):
    """The collider's CORE segment, the one the engine tests against (RigParticleSimulation.cpp:620-637): the
    thigh axis from R to L - R along hip->knee, not the whole hip->knee segment."""
    hip, knee = x['ctrl'][_role('thigh_' + side)], x['ctrl'][_role('calf_' + side)]
    length = math.dist(hip, knee)
    a, b = THIGH_RADIUS / length, (length - THIGH_RADIUS) / length
    return ([h + a * (k - h) for h, k in zip(hip, knee)], [h + b * (k - h) for h, k in zip(hip, knee)])


def leg_bones(parts):
    """The simulated points of the parts hanging from the pelvis, the ones the thigh capsules guard, as (record key,
    name): each chain's non-root bones ('chain'), and a single-bone flap's tip ('tips', its moving particle is its
    tail). Empty for a hero with nothing there (hair only): the leg checks are then not
    applicable, never vacuously passed."""
    out = []
    for pt in parts:
        if pt['parent'] != _role('pelvis'):
            continue
        moving = [b for b in pt['bones'] if b != pt['root']]
        out += [('chain', b) for b in moving] if moving else [('tips', pt['id'])]
    return out


def _has_legs(x):
    return all(_role(r) in x.get('ctrl', {}) for r in ('thigh_l', 'thigh_r', 'calf_l', 'calf_r'))


def _clearance_series(p, side, bones):
    return [min(math.dist(x[kind][name], af.seg_closest(x[kind][name], *_core(x, side))[1]) for kind, name in bones)
            for x in p]


def leg_clearance(p, bones):
    """Per side: the smallest bone-to-collider-core distance over the run, and the frames under the absolute
    capsule+particle bound. Diagnostic; the gate is paired_clearance."""
    out = {}
    for side in ('l', 'r'):
        dists = _clearance_series(p, side, bones)
        out[side] = {'min_cm': round(min(dists), 3), 'frames_below_bound': sum(1 for v in dists if v < CLEARANCE_BOUND)}
    return out


def paired_clearance(on, off, bones):
    """Gate (g), same frame against same frame (comparing two whole-run minima let a severe
    regression hide behind an OFF minimum on another frame).

    On every frame the ON pawn must keep ON >= min(OFF, CLEARANCE_BOUND) - PAIRED_TOL. The min() matters: the
    authored animation already brings the cloth bones inside the capsule+particle bound (so an absolute bound is
    red on the animation itself), and a cloth that swings 5 cm nearer than the animation while still 15 cm clear
    of the leg is not a contact (measured: plain ON - OFF went to -5.5 cm with the colliders working). So ON is
    held to the animation only where the animation is itself within contact range, and to the bound elsewhere.
    Per side: the worst margin (ON - (min(OFF, bound) - tol)) and its index; pass when it is >= 0."""
    out = {}
    for side in ('l', 'r'):
        margins = [a - (min(b, CLEARANCE_BOUND) - PAIRED_TOL)
                   for a, b in zip(_clearance_series(on, side, bones), _clearance_series(off, side, bones))]
        worst = min(range(len(margins)), key=margins.__getitem__)
        out[side] = {'worst_margin_cm': round(margins[worst], 3), 'at_index': worst, 'pass': margins[worst] >= 0.0}
    return out


def _cs_travel(a, b):
    """The pawn's travel from sample a to sample b in COMPONENT space, (x, y): its world velocity rotated by -mesh yaw
    (mesh_w[3]), so no facing convention of the mesh is assumed."""
    vx, vy = b['loc'][0] - a['loc'][0], b['loc'][1] - a['loc'][1]
    yaw = math.radians(b['mesh_w'][3])
    return vx * math.cos(yaw) + vy * math.sin(yaw), -vx * math.sin(yaw) + vy * math.cos(yaw)


def trail(p, frames, t0, t1, parts):
    """Mean trail of each `<chain>_01 -> tip` segment behind vertical, in degrees, over frames with t0 <= t <= t1
    (positive = tip behind the root, against the direction of travel). Travel is the pawn's world velocity rotated
    into component space by the mesh's yaw (mesh_w[3]), so no facing convention of the mesh is assumed."""
    out = {}
    for pt in parts:
        chain, root, tip = pt['id'], pt['root'], pt['tip']
        angles = []
        for a, b, f in zip(p, p[1:], frames[1:]):
            if not t0 <= f['t'] <= t1 or root not in b['chain']:
                continue
            cx, cy = _cs_travel(a, b)
            speed = math.hypot(cx, cy)
            if speed < 1e-3:
                continue
            fx, fy = cx / speed, cy / speed
            r, t = b['chain'][root], b['chain'][tip]
            ahead = (t[0] - r[0]) * fx + (t[1] - r[1]) * fy
            angles.append(math.degrees(math.atan2(-ahead, r[2] - t[2])))
        out[chain] = round(statistics.mean(angles), 2) if angles else None
    return out


def travel_dir(p, frames, t0, t1):
    """The mean direction of travel over t0 <= t <= t1 in COMPONENT space (unit (x, y)), the way trail() measures it:
    world velocity rotated by -mesh yaw. No facing convention of the mesh is assumed. None when the pawn did not move."""
    sx = sy = 0.0
    for a, b, f in zip(p, p[1:], frames[1:]):
        if not t0 <= f['t'] <= t1:
            continue
        cx, cy = _cs_travel(a, b)
        sx += cx
        sy += cy
    n = math.hypot(sx, sy)
    return (sx / n, sy / n) if n > 1e-6 else None


def hang(p, frames, t0, t1, parts, fwd):
    """For a necklace that read as defying gravity and hugging the chest: per part, the angle of its root -> tip from
    straight down in the plane of `fwd` (component-space travel direction, travel_dir) and up, degrees, circular mean
    over t0 <= t <= t1; positive = tip toward the travel direction. On the OFF pawn it is where the animation holds the
    part (a pendant follows the chest's lean); ON minus OFF is how far it swings. Signed for front- and back-hanging
    parts alike: for a part on the back, positive is toward the body. A part whose tip lies across the plane (a side
    flap) reads its in-plane projection, so read `incl` beside it. Returns {id: {'fwd': deg, 'incl': deg}}, where incl
    is the unsigned 3D angle from straight down."""
    out = {}
    for pt in parts:
        root, tip = pt['root'], pt['tip']
        s = c = 0.0
        n, incl = 0, []
        for x, f in zip(p, frames):
            if not (t0 <= f['t'] <= t1 and root in x['chain'] and tip in x['chain']) or fwd is None:
                continue
            d = [x['chain'][tip][k] - x['chain'][root][k] for k in range(3)]
            along = d[0] * fwd[0] + d[1] * fwd[1]
            incl.append(math.degrees(math.atan2(math.hypot(d[0], d[1]), -d[2])))
            if math.hypot(along, d[2]) >= 0.1 * math.sqrt(sum(v * v for v in d)):   # else it lies across the plane
                ang = math.atan2(along, -d[2])
                s, c, n = s + math.sin(ang), c + math.cos(ang), n + 1
        out[pt['id']] = ({'fwd': round(math.degrees(math.atan2(s, c)), 2) if n else None,
                          'incl': round(statistics.mean(incl), 2)} if incl else None)
    return out


def collider_clearance(p, frames, prof=None, tol=1.0):
    """Autofit validation: per chain, every simulated (non-root) bone against every collider the chain should respect,
    each collider rebuilt per frame from its owner's recorded transform. clearance = surface distance - particle radius
    (< 0: inside). Per chain: the minimum clearance, the frames under -tol, the worst time and collider, and how many
    checks ran vs were skipped (missing bone, shape or transform). closest_body: the nearest bone centre to any body
    proxy, without the particle radius, for choosing frames to inspect.

    What it does NOT prove: visible clearance. It tests bone centres (not the solver's internal
    particles, not mesh triangles, not the strip's width) at sampled frames only, against today's profile."""
    prof = prof if prof is not None else _profile()
    col = prof.get('colliders', {})
    shapes = {s['name']: s for s in col.get('shapes', []) if 'local' in s}
    body = [s for s in shapes.values() if s.get('role') == 'body']
    chain_bones = {c['root']: c['bones'][1:] for c in prof.get('chains', [])}
    out = {}
    if not p or 'tm' not in p[0]:
        return out
    lib = None
    for root, spec in col.get('chains', {}).items():
        if spec.get('collides_with') == []:
            # Not applicable ONLY when the part's behaviour turns collision off (soft tissue rests inside its host); an
            # empty or missing collider list on any other part is a defect, scored as such (a
            # missing key must not read as a deliberate n/a).
            import dyn_behaviours
            lib = lib or dyn_behaviours.load()
            native = (lib['behaviours'].get(spec.get('behaviour')) or {}).get('native') or {}
            if native.get('bCollideWithColliders') is False:
                out[root] = {'not_applicable': 'its behaviour %s turns collision off' % spec.get('behaviour')}
                continue
        radius = spec['particle_radius_cm']
        tails = spec.get('tails', [])
        worst, frames_bad, near, skipped, checked = (None, None, None), 0, (None, None, None), 0, 0
        for x, f in zip(p, frames):
            bad = False
            # a tail's end, from its leaf bone's recorded transform (None when that transform was not recorded)
            ends = {t['null']: (af.add(x['tm'][t['bone']][:3], af.qrot(x['tm'][t['bone']][3:], t['local']))
                                if t['bone'] in x['tm'] else None) for t in tails}
            for bone in chain_bones.get(root, []) + list(ends):
                pt = ends[bone] if bone in ends else x['chain'].get(bone)
                if pt is None:
                    skipped += len(spec['collides_with'])     # bone not recorded: these checks did not run
                    continue
                for name in spec['collides_with']:
                    s = shapes.get(name)
                    if s is None or s['owner'] not in x['tm']:
                        skipped += 1                          # shape or owner transform missing: not checked
                        continue
                    checked += 1
                    c = af.posed_distance(pt, s, x['tm'][s['owner']]) - radius   # collider placed by its owner's tm
                    if worst[0] is None or c < worst[0]:
                        worst = (c, f['t'], name)
                    bad |= c < -tol
                for s in body:
                    if s['owner'] in x['tm']:
                        d = af.posed_distance(pt, s, x['tm'][s['owner']])
                        if near[0] is None or d < near[0]:
                            near = (d, f['t'], s['name'])
            frames_bad += bad
        out[root] = {'min_clearance_cm': round(worst[0], 2) if worst[0] is not None else None,
                     'worst_t': worst[1], 'worst_collider': worst[2], 'frames_under_tol': frames_bad,
                     'closest_body_cm': round(near[0], 2) if near[0] is not None else None,
                     'closest_body_t': near[1], 'closest_body_shape': near[2],
                     'checks_run': checked, 'checks_skipped': skipped}
    return out


def liveliness(p, frames, t_run0, t_rel, parts, fwd, window=1.5):
    """For cloth that reads as too damped: per chain, how much the cloth MOVES, not where it settles.
    run_std  - spread of the trail angle during the steady run (deg): secondary bounce with the stride.
    stop_fwd - peak forward swing past vertical after the hard stop, within `window` s (deg): an over-damped chain
               barely swings through; a lively one overshoots.
    The forward axis `fwd` is fixed from the run (travel_dir over t_run0..t_rel, the mean component-space travel
    direction), so it stays defined after the pawn stops. {} when the pawn did not move (fwd None)."""
    if fwd is None:
        return {}
    fx, fy = fwd
    out = {}
    for pt in parts:
        chain, root, tip = pt['id'], pt['root'], pt['tip']
        run, stop = [], []
        for b, f in zip(p, frames):
            if root not in b['chain']:
                continue
            r, t = b['chain'][root], b['chain'][tip]
            ang = math.degrees(math.atan2(-((t[0] - r[0]) * fx + (t[1] - r[1]) * fy), r[2] - t[2]))
            if t_run0 <= f['t'] <= t_rel:
                run.append(ang)
            elif t_rel < f['t'] <= t_rel + window:
                stop.append(ang)
        out[chain] = {'run_std': round(statistics.pstdev(run), 2) if len(run) > 1 else None,
                      'stop_fwd': round(-min(stop), 2) if stop else None}
    return out


def twist_deg(q_a, q_b, axis_local):
    """Signed twist (deg) of rotation q_a relative to q_b about the bone's long axis (`axis_local`, in the bone's own
    frame, rotated by q_a): the twist part of the swing-twist decomposition of q_a * q_b^-1."""
    rel = af.qmul(q_a, af.qconj(q_b))
    if rel[3] < 0.0:
        rel = [-c for c in rel]
    ax = af.unit(af.qrot(q_a, axis_local))
    return math.degrees(2.0 * math.atan2(af.dot(rel[:3], ax), rel[3]))


def twist(frames, i, t0, t1, parts, tails=None):
    """For motion that shows no twist: per part, how far each bone ROLLS about its own long axis
    in the ON pawn against the OFF (animation-only) pawn, t0 <= t <= t1: RMS and peak of the signed twist, the bone
    with the largest RMS reported. The long axis is the bone -> next-bone direction of the OFF pose in the bone's own
    frame; a part's last bone takes its axis from its tail (`tails`: {bone: local offset}, the record's colliders
    snapshot), and is not measured without one. The solver already composes some roll from its successive minimal arcs,
    so this is not 0 with no twist modifier: compare builds.
    Needs every chain bone in `tm` (pie_dynamics_drive records them)."""
    out = {}
    tails = tails or {}
    fr = [f for f in frames if t0 <= f['t'] <= t1]
    if not fr:
        return out
    for pt in parts:
        bones = [b for b in _spine(pt) if b in pt['bones']]
        best = None
        for b, nxt in zip(bones, bones[1:] + [None]):
            tm0, tmi = fr[0]['pawns'][0].get('tm', {}), fr[0]['pawns'][i].get('tm', {})
            if b not in tm0 or b not in tmi or (nxt is None and b not in tails) or (nxt is not None and nxt not in tm0):
                continue
            vals = []
            for f in fr:
                on, off = f['pawns'][0]['tm'], f['pawns'][i]['tm']
                axis = (list(tails[b]) if nxt is None else
                        af.qrot(af.qconj(off[b][3:]), af.sub(off[nxt][:3], off[b][:3])))
                if af.norm(axis) < 1e-6:
                    continue
                vals.append(twist_deg(on[b][3:], off[b][3:], axis))
            if len(vals) < 2:
                continue
            rms = math.sqrt(sum(v * v for v in vals) / len(vals))
            if best is None or rms > best['rms_deg']:
                best = {'bone': b, 'rms_deg': round(rms, 2), 'peak_deg': round(max(abs(v) for v in vals), 2),
                        'mean_deg': round(sum(vals) / len(vals), 2)}
        if best:
            out[pt['id']] = best
    return out


def _spread(d):
    """The mean m of offsets d and their RMS distance about it, in cm: (m, rms)."""
    m = [sum(v[k] for v in d) / len(d) for k in range(3)]
    return m, math.sqrt(sum(math.dist(v, m) ** 2 for v in d) / len(d))


def flutter(frames, i, t0, t1):
    """For feathers that read as far too stiff and damped: per part, how much the ON tip OSCILLATES about
    the OFF (animation-only) tip during the steady run, t0 <= t <= t1: the RMS of the ON-minus-OFF offset about its
    own mean, in cm. A constant lag (the trail) scores 0; flutter and bounce score their amplitude. It is a spread, not
    a reversal count: a slow drift over the window scores too. Reported, not gated."""
    out = {}
    for c in frames[0]['pawns'][0]['tips']:
        d = [af.sub(f['pawns'][0]['tips'][c], f['pawns'][i]['tips'][c]) for f in frames if t0 <= f['t'] <= t1]
        if len(d) < 2:
            out[c] = None
            continue
        out[c] = round(_spread(d)[1], 3)
    return out


def flutter_hz(frames, i, t0, t1, floor=0.05):
    """For flutter that reads as too slow: per part, a RATE for the ON-minus-OFF tip offset over the run window, in Hz:
    RMS(d offset/dt) / (2 pi RMS(offset - mean)). A derivative-energy ratio, not a perceived frequency: a sinusoid at f
    sampled at fs reads (fs/pi) sin(pi f/fs) (3% low at 8 Hz / 60 fps), a mix reads its power-weighted RMS frequency, so
    small fast jitter can dominate, and sample-to-sample noise alone tends to ~13.5 Hz at 60 fps.
    Read it beside flutter_cm and the render. None when the spread is under `floor` cm.
    Reported, not gated."""
    out = {}
    for c in frames[0]['pawns'][0]['tips']:
        w = [f for f in frames if t0 <= f['t'] <= t1]
        d = [af.sub(f['pawns'][0]['tips'][c], f['pawns'][i]['tips'][c]) for f in w]
        # A repeated or backwards timestamp has no rate: no reading rather than a huge one
        if len(d) < 3 or any(g['t'] <= f['t'] for f, g in zip(w, w[1:])):
            out[c] = None
            continue
        _m, spread = _spread(d)
        if spread < floor or spread == 0.0:
            out[c] = None
            continue
        vel = [math.dist(a, b) / (g['t'] - f['t']) for a, b, f, g in zip(d, d[1:], w, w[1:])]
        rms_v = math.sqrt(sum(v * v for v in vel) / len(vel))
        out[c] = round(rms_v / (2 * math.pi * spread), 3)
    return out


def settle_time(frames, i, rest, t_stop, floor=1.0, frac=0.15, hold=0.25):
    """For a settle that reads as too long, as though underwater: per part, seconds from the stop
    until the ON-minus-OFF tip offset is back within max(floor cm, frac x its post-stop peak) of its REST offset (its
    mean over the standing window `rest` = (t0, t1) before the run, the same idle pose) and stays there to the end of
    the record. The time is the first sample of that final in-tolerance stretch. None when the stretch lasts under
    `hold` s, i.e. it has not been seen to settle (still moving, or a drift away from rest). Reported, not gated; the
    clock starts at the stop, so swing during the deceleration is not timed."""
    out = {}
    for c in frames[0]['pawns'][0]['tips']:
        off = lambda f: af.sub(f['pawns'][0]['tips'][c], f['pawns'][i]['tips'][c])
        ref = [off(f) for f in frames if rest[0] <= f['t'] < rest[1]]
        d = [(f['t'], off(f)) for f in frames if f['t'] >= t_stop]
        if len(ref) < 2 or len(d) < 2:
            out[c] = None
            continue
        m = [sum(v[k] for v in ref) / len(ref) for k in range(3)]
        dev = [(t, math.dist(v, m)) for t, v in d]
        tol = max(floor, frac * max(e for _t, e in dev))
        bad = [n for n, (_t, e) in enumerate(dev) if e > tol]
        first = bad[-1] + 1 if bad else 0
        out[c] = (round(dev[first][0] - t_stop, 3) if first < len(dev) and dev[-1][0] - dev[first][0] >= hold
                  else None)
    return out


def root_lift(frames, i, parts):
    """{part: max cm} the chain's FIRST simulated bone strays from its animated place, in its parent's frame, ON pawn 0
    against pawn i. A hair clump lying on the scalp that lifts by this much shows the scalp under it (seen at
    3-6 cm while running, 10 cm at worst, on every hair chain). None where a bone is not recorded."""
    out = {}
    for pt in parts:
        if len(pt['bones']) < 2 or not pt.get('parent'):
            continue
        b, par = pt['bones'][1], pt['parent']
        worst = None
        for f in frames:
            on, off = f['pawns'][0].get('tm', {}), f['pawns'][i].get('tm', {})
            if not all(k in d for d in (on, off) for k in (b, par)):
                worst = None
                break
            loc = lambda tm: af.qrot([-tm[par][3], -tm[par][4], -tm[par][5], tm[par][6]],   # noqa: E731
                                     [tm[b][k] - tm[par][k] for k in range(3)])
            d = math.dist(loc(on), loc(off))
            worst = d if worst is None or d > worst else worst
        out[pt['id']] = round(worst, 3) if worst is not None else None
    return out


def analyze(path):
    rec = json.load(open(path))
    frames, ev = rec['frames'], rec['events']
    _FALLBACK_HERO['rig'] = rec.get('hero') or os.environ.get(dyn_hero.ENV)
    _set_roles(rec)
    parts = record_parts(rec)
    _flap_tips(rec, parts)
    n = len(rec['config']['alphas'])
    _leg_geometry(rec['colliders'] if 'colliders' in rec else _profile().get('colliders'))
    out = {'path': path, 'alphas': rec['config']['alphas'], 'frames': len(frames),
           'error': rec['error'] if 'error' in rec else 'record has no error key',
           'cost': rec.get('cost'),
           'frame_clock_nonfinite': sum(1 for f in frames for k in ('t', 'dt')
                                        if not isinstance(f.get(k), (int, float)) or not math.isfinite(f[k])),
           'spawn_frame': ev.get('spawn_frame'), 'first_input_frame': ev.get('first_input_frame')}
    if not frames:
        return out
    dts = [f['dt'] for f in frames]
    med = statistics.median(dts)
    out['dt_median'] = med
    out['hitches'] = [f['f'] for f in frames if f['dt'] > HITCH_H * med]
    t_rel = ev.get('t_release')
    t_run = None
    pawns = []
    for i in range(n):
        p = [f['pawns'][i] for f in frames]
        ticks = [x['curves']['MT_DynRigTick'] for x in p]
        vals = [t['value'] for t in ticks if t['value'] is not None]   # an MRQ-render record reads None on some frames
        t_stop = next((f['t'] for f, x in zip(frames, p) if t_rel is not None and f['t'] >= t_rel
                       and x['speed'] <= 1.0), None)
        # The solver flags a teleport on its first step (measured: frame 1, both pawns, on the first witness run), so events are counted
        # only once the settle period has begun... after the first SETTLE_SKIP frames.
        flags = {c: [f['f'] for f, x in zip(frames, p) if f['f'] > SETTLE_SKIP and x['curves'][c]['found']
                     and (x['curves'][c]['value'] or 0) > 0.5]
                 for c in ('MT_DynTeleport', 'MT_DynPosReset', 'MT_DynKinReset')}
        s5 = {}
        # Skip the first SKIP_INIT frames: the pose snaps from the reference pose to the animation there, and the
        # finite differences read that snap as a ~600 cm/s kinematic speed (measured), which is not the drive.
        p, sframes = p[SKIP_INIT:], frames[SKIP_INIT:]
        if 'chain' in p[0]:
            s5['max_particle_dist_from_origin'] = round(max(math.dist(v, (0, 0, 0))
                                                            for x in p for v in x['chain'].values()), 3)
            roots = [pt['root'] for pt in parts if pt['root'] in p[0]['chain']]
            s5['max_root_cs_speed'] = round(max(math.dist(a['chain'][b], c['chain'][b]) / max(f['dt'], 1e-6)
                                                for a, c, f in zip(p, p[1:], sframes[1:]) for b in roots), 3)
            s5['chain_length'] = {}
            for pt in parts:
                bones = [b for b in _spine(pt) if b in p[0]['chain']]     # root -> tip, not across branches
                s5['chain_length'][pt['id']] = round(sum(math.dist(p[0]['chain'][a], p[0]['chain'][b])
                                                      for a, b in zip(bones, bones[1:])), 3)
        vel = [[(b['loc'][k] - a['loc'][k]) / max(f['dt'], 1e-6) for k in range(3)]
               for a, b, f in zip(p, p[1:], sframes[1:])]
        s5['max_frame_travel_cm'] = round(max(math.dist(a['loc'], b['loc']) for a, b in zip(p, p[1:])), 3)
        s5['peak_linear_accel'] = round(max(math.dist(u, v) / max(f['dt'], 1e-6)
                                            for u, v, f in zip(vel, vel[1:], sframes[2:])), 1)
        p = [f['pawns'][i] for f in frames]
        if 'chain' in p[0] and _has_legs(p[0]) and leg_bones(parts):
            s5['leg_clearance'] = leg_clearance(p, leg_bones(parts))
        # Steady-run window: from 1 s after the first input (the start-up acceleration is over) to the release.
        first_in = ev.get('first_input_frame')
        first_in = min(first_in) if isinstance(first_in, list) and first_in else first_in
        t_in = next((f['t'] for f in frames if f['f'] >= first_in), None) if first_in is not None else None
        if 'chain' in p[0] and 'mesh_w' in p[0] and t_in is not None and t_rel is not None:
            t_run = (t_in + 1.0, t_rel)
            s5['run_trail_deg'] = trail(p, frames, t_in + 1.0, t_rel, parts)
            fwd = travel_dir(p, frames, t_in + 1.0, t_rel)
            s5['liveliness'] = liveliness(p, frames, t_in + 1.0, t_rel, parts, fwd)
            s5['run_hang_deg'] = hang(p, frames, t_in + 1.0, t_rel, parts, fwd)
            s5['stand_hang_deg'] = hang(p, frames, t_in - 1.5, t_in - 0.5, parts, fwd)
        s5['collider_clearance'] = collider_clearance(p, frames, _record_profile(rec, parts))
        adj = {}
        for chain in p[0]['tips']:
            steps = [math.dist(a['tips'][chain], b['tips'][chain]) for a, b in zip(p, p[1:])]
            settle = [s for s, f in zip(steps, frames[1:]) if t_stop is not None and f['t'] >= t_stop + SETTLE_T]
            adj[chain] = {'max': round(max(steps), 4), 'settle_max': round(max(settle), 4) if settle else None}
        pawns.append({
            'curves_found_all': all(x['curves'][c]['found'] for x in p for c in x['curves']),
            'tick_advances_every_frame': all(b > a for a, b in zip(vals, vals[1:])),
            'tick_steps': sorted({round(b - a, 3) for a, b in zip(vals, vals[1:])}),
            'pp_class': p[0]['pp'], 'alpha_read': [p[0]['alpha_read'], p[-1]['alpha_read']],
            'alpha_reads': sorted({x['alpha_read'] for x in p}, key=str), 'nonfinite': _nonfinite(p),
            'travel_cm': round(math.dist(p[0]['loc'], p[-1]['loc']), 2), 'peak_speed': max(x['speed'] for x in p),
            'lod': p[-1]['lod'], 't_stop': t_stop, 'event_frames': flags, 'adjacent_tip_motion': adj, 's5': s5,
        })
    out['pawns'] = pawns
    pairs = []
    for i in range(1, n):
        t_stop = pawns[0]['t_stop']
        window = [f for f in frames if t_rel is not None and t_stop is not None and t_rel <= f['t'] <= t_stop + STOP_W]
        tip = {c: round(max((math.dist(f['pawns'][0]['tips'][c], f['pawns'][i]['tips'][c]) for f in window),
                            default=0), 4)
               for c in frames[0]['pawns'][0]['tips']}
        tip_run = {c: round(max(math.dist(f['pawns'][0]['tips'][c], f['pawns'][i]['tips'][c]) for f in frames), 4)
                   for c in frames[0]['pawns'][0]['tips']}
        ctrl = {b: round(max(math.dist(f['pawns'][0]['ctrl'][b], f['pawns'][i]['ctrl'][b]) for f in frames), 4)
                for b in frames[0]['pawns'][0]['ctrl']}
        pair = {'pair': [0, i], 'stop_window_tip_delta': tip, 'run_tip_delta': tip_run, 'control_bone_delta': ctrl}
        if t_run is not None:
            pair['run_flutter_cm'] = flutter(frames, i, t_run[0], t_run[1])
            pair['run_flutter_hz'] = flutter_hz(frames, i, t_run[0], t_run[1])
            tails = {t['bone']: t['local'] for c in (rec.get('colliders') or {}).get('chains', {}).values()
                     for t in c.get('tails', [])}
            pair['run_twist'] = twist(frames, i, t_run[0], t_run[1], parts, tails)
            if t_rel is not None:
                pair['stop_twist'] = twist(frames, i, t_rel, t_rel + STOP_W, parts, tails)
        if t_stop is not None and t_run is not None:
            pair['stop_settle_s'] = settle_time(frames, i, (t_run[0] - 1.5, t_run[0] - 1.0), t_stop)
        pair['root_lift_cm'] = root_lift(frames, i, parts)
        # The leg check applies when some part hangs from the pelvis; otherwise it is recorded as not applicable, so the
        # gate can tell "no legs to guard" from "the check did not run".
        pair['legs_applicable'] = bool(leg_bones(parts))
        if 'chain' in frames[0]['pawns'][0] and _has_legs(frames[0]['pawns'][0]) and pair['legs_applicable']:
            pair['paired_clearance'] = paired_clearance([f['pawns'][0] for f in frames],
                                                        [f['pawns'][i] for f in frames], leg_bones(parts))
        pairs.append(pair)
    out['pairs'] = pairs
    return out


if __name__ == '__main__':
    for p in sys.argv[1:]:
        print(json.dumps(analyze(p), indent=1))
