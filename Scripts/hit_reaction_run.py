"""Hit reaction: the matched matrix for one hero, end to end, from a normal shell (editor up, the hero onboarded with
its physics stage and its dynamics ABP built).

    python Scripts/hit_reaction_run.py --hero <Rig> <tag> [--strength=300] [--at=1.2] [--render=<captures dir>]

Three drives of pie_dynamics_drive.py, Dynamics on in all, pawn 0 hit / pawn 1 not:
  <tag>_hit     reaction on       the response, recovery and legs come from pawn 0 against pawn 1
  <tag>_off     reaction off      the same hit must NOT respond (the node is what reacts), and its unhit pawn is the
                                  animation the reaction-on unhit pawn must hold (fidelity)
  <tag>_noimp   reaction on, impulse 0: the response check must go red (it measures the hit, not a difference)
Then hit_reaction_analyze's verdict. Spec: docs/specs/2026-10-29-hit-reaction-design.md.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dyn_hero  # noqa: E402
import hit_reaction_analyze  # noqa: E402
import hit_reaction_core  # noqa: E402
import dyn_run  # noqa: E402

# Each impact: a velocity change (cm/s) on the struck reaction body alone (FOCUSED): its centre of mass lies within
# radius of an origin offset behind it, against the push, and its neighbours' do not. The pawns stand facing +X (they
# run along +X), so +Y pushes from the hero's left. The offset is a share of the hero's height (5.4 cm on a 181 cm
# hero), the radius a little more than it. At 0.08 x height the origin sat out among the bodies on the near side: the
# chest impact took in Khaimera's left clavicle (12.6 cm from it) and upper arm, which a velocity change flings (a
# light body takes the full 2000 cm/s): clavicle 57 deg, the upper arm 3.6 cm into the neck.
# The impact was WIDE first (radius 0.22 x height): the sphere took in nearly the whole upper body (Aurora a1: 15
# bodies for the chest impact) and, radial, pushed each outward from one point, splaying the arms apart at the
# clavicles (clavicle 34 deg, elbow 1.3, wrist 0.25, seen on the render) instead of the struck body moving and the
# rest following through the joints.
IMPACT = {'pawns': [0], 'vel_change': True}
OFFSET_SHARE = 0.03
FOCUS_RADIUS_RATIO = 1.25
# One hit, then the recovery (a volley read as jittery on each sequential hit; a single hit at the beginning
# and then the recovery reads cleanly): the chest, from the front, so the whole body rocks back and the arms, hips and legs
# follow through the joints. (role, push dir). It replaces a six-impact volley 0.4 s apart (chest, arms, head, hips,
# back), each new impact landing before the last had recovered.
# From the front and above, 45 deg down (a deeper squat puts the leg IK to work, which needs more downward
# force): the same 2000 cm/s horizontal push plus as much again downward (STRENGTH is the whole push), so the hips drop
# and the planted legs bend under them. Sweep 2026-11-06 (knee bend l / r deg, Aurora; Khaimera), hip damping ratio 1:
# level push 13 / 18; tilted 50 deg down at 2000 cm/s (less push back) 38 / 46, 48 / 51 but Khaimera's arms no longer
# articulated; 45 deg at 2828 cm/s 41 / 51, 53 / 58, every check passing on both.
IMPACTS = (('spine_top', [-1.0, 0.0, -1.0]),)    # the chest, from the front and above
IMPACT_GAP_S = 0.4                               # between impacts, when there is more than one
# A focused impact moves one body, so it takes more than the wide one did (450 cm/s). Aurora, joint peaks clavicle /
# shoulder / elbow / wrist deg: 450 cm/s 7.7 / 16 / 1.3 / 2.4, the waist and back impacts not landing (0.5, 0.7 cm);
# 1000: 21 / 41 / 4.6 / 4.2; 1800: 21 / 39 / 20 / 16, the waist 2.0 cm (just at its bound).
STRENGTH = 2828.0              # 2000 x sqrt(2): 2000 cm/s along each of back and down
FIRST_AT_S = 1.2
# The stand before the run holds the whole reaction window (the last impact, hold 1 s, fade 0.3 s) and the return
# after it. A reaction during the run lags the jog by ~6 cm / 24 deg at any spring stiffness (docs/specs/2026-10-29-hit-reaction-design.md): not
# claimed.
RETURN_MARGIN_S = 1.0


def body_centre(spec, bone):
    """The struck body's centre of mass for the drive, as ONE bone-local point: [{'local': [x, y, z]}]. Chaos weights
    each shape by its volume (hit_reaction_core.body_com_local); the drive's old mean of the shape centres sat off it on
    a body of unequal shapes."""
    body = next(b for b in spec['bodies'] if b['bone'] == bone)
    return [{'local': hit_reaction_core.body_com_local(body)}]


def volley(profile, spec, strength=STRENGTH, at_s=FIRST_AT_S, impacts=IMPACTS):
    """The impacts for a hero, in time order, each focused on its body in the reaction spec (impacts: (role, push
    dir) pairs, IMPACTS unless a sweep tries others)."""
    roles, height = profile['roles'], profile['scale']['height_cm']
    sp = roles['spine']
    bone = {'spine_top': sp[-1], 'pelvis': roles['pelvis'], 'spine_mid': sp[len(sp) // 2], 'head': roles['head'],
            'upperarm_l': roles['upperarm_l'], 'upperarm_r': roles['upperarm_r']}
    off = round(OFFSET_SHARE * height, 2)
    return [dict(IMPACT, bone=bone[k], dir=d, offset_cm=off, radius=round(off * FOCUS_RADIUS_RATIO, 2),
                 com=body_centre(spec, bone[k]), strength=strength, at_s=round(at_s + i * IMPACT_GAP_S, 3))
            for i, (k, d) in enumerate(impacts)]


def settle_s(hits):
    s = hit_reaction_core.SCHEDULE
    return max(3.0, max(x['at_s'] for x in hits) + s['hold_s'] + s['fade_s'] + RETURN_MARGIN_S)


def _reaction_spec(h):
    """The onboarded reaction body, refused unless it is the one the generator makes today (hero_onboard.reaction_spec
    reads the skeleton, its reference pose and the idle through the editor): a record from an older generator passes
    against the asset it built, both stale together. A hero with cloth: its cloth
    collision bodies too (hit_reaction_core.onboarded_cloth)."""
    import hero_onboard
    ref = hero_onboard._ref_tms(h)
    spec = hit_reaction_core.onboarded_spec(h, hero_onboard.reaction_spec(h, hero_onboard._skeleton(h), ref))
    if h['profile'].get('stock', {}).get('clothing_assets'):
        cloth = hit_reaction_core.cloth_collision_spec(h['profile'], ref)
        bodies = hit_reaction_core.onboarded_cloth(h, cloth)
        # ...and the asset the drive attaches is that record, read back
        built = hero_onboard.physics_mismatch(cloth, hero_onboard.describe_physics(h['cloth_collision_asset']))
        if built:
            raise RuntimeError('%s: the cloth collision asset is not its record: %s' % (h['rig'], '; '.join(built[:3])))
        spec = dict(spec, cloth_bodies=bodies)
    return spec


def _extra(h, spec, hits, react):
    """The drive config of one reaction run: the reaction bodies are recorded (the overlay draws them) and every body
    bone and leg bone is sampled."""
    r = h['profile']['roles']
    legs = [r[k] for k in ('thigh_l', 'thigh_r', 'calf_l', 'calf_r', 'foot_l', 'foot_r') if r.get(k)]
    return {'reaction_alphas': [react, react], 'hits': hits, 'reaction_schedule': hit_reaction_core.SCHEDULE,
            'reaction_bodies': spec['bodies'], 'reaction_constraints': spec['constraints'],
            # the arm-torso pairs the asset lets collide (the builder's report; exactly the spec's)
            'reaction_collide': hit_reaction_core.onboarded_collide(h),
            # the legs planted by IK after the reaction (the analysis then holds the feet, not the whole leg)
            'leg_ik': spec.get('leg_ik') or [],
            # the cloth collision bodies this take ran with (the overlay's cloth layer draws these, not today's)
            'cloth_bodies': spec.get('cloth_bodies') or [],
            # the bodies' bones (every shape is explicit in its own bone's frame) and the legs
            'tm_bones': list(dict.fromkeys([b['bone'] for b in spec['bodies']] + legs)),
            'settle_s': settle_s(hits)}


def run(rig, tag, strength=STRENGTH, at_s=FIRST_AT_S, impacts=IMPACTS):
    h = dyn_hero.resolve(rig)
    spec = _reaction_spec(h)
    hits = volley(h['profile'], spec, strength, at_s, impacts)
    paths = []
    for suffix, react, s in (('hit', 1.0, strength), ('off', 0.0, strength), ('noimp', 1.0, 0.0)):
        t = '%s_%s' % (tag, suffix)
        extra = _extra(h, spec, [dict(x, strength=s) for x in hits], react)
        try:
            result = dyn_run.run(rig, t, [1.0, 1.0], extra)
        except TimeoutError as e:
            # The editor's game thread has stalled for minutes right after a drive registered (one frame in 5 min,
            # 2 of ~25 drives, 2026-10-29; cause not found): the drive is retried once, fresh, under its own tag, so
            # the stalled attempt's record landing late can never be taken for the retry's
            print('HIT_REACTION_RUN %s timed out (%s); retrying once' % (t, e), flush=True)
            t += '_retry'
            result = dyn_run.run(rig, t, [1.0, 1.0], extra)
        # Only THIS run's record counts: a timed-out drive must never fall back to an older file under the same tag.
        # dyn_run returns None unless a record written after it started appeared.
        if not result or result.get('error'):
            raise RuntimeError('drive %s produced no fresh record (%s)' % (t, result and result.get('error')))
        paths.append(result['path'])
    return dict(hit_reaction_analyze.main(paths), records=paths)    # the records judged, hit / off / no-impulse


def render(rig, out_dir, tag, strength=STRENGTH, at_s=FIRST_AT_S, resolution=None, outputs=('full', 'loop')):
    """The visual check: the reaction-on hit pawn (0, nearer the camera) beside its unhit twin, under MRQ at the
    dynamics framing (dyn_render). outputs: dyn_render's (the release cuts its own clips: none)."""
    import dyn_render
    h = dyn_hero.resolve(rig)
    spec = _reaction_spec(h)
    hits = volley(h['profile'], spec, strength, at_s)
    extra = dict(_extra(h, spec, hits, 1.0), alphas=[1.0, 1.0])
    frames = int((extra['settle_s'] + 3.0 + 2.0) * 60) + 30   # stand, run, hold (the drive's defaults) and a margin
    kw = {'resolution': resolution} if resolution else {}
    return dyn_render.render(rig, os.path.abspath(out_dir).replace('\\', '/'), tag, frames=frames, extra=extra,
                                  outputs=outputs, **kw)


if __name__ == '__main__':
    rig, args = dyn_hero.hero_arg(sys.argv[1:])
    opts = {a.split('=')[0]: a.split('=', 1)[1] for a in args if a.startswith('--') and '=' in a}
    tag = next(a for a in args if not a.startswith('--'))
    if '--render' in opts:
        res = tuple(int(v) for v in opts['--res'].split('x')) if '--res' in opts else None
        render(rig, opts['--render'], tag, float(opts.get('--strength', STRENGTH)), float(opts.get('--at', FIRST_AT_S)),
               res)
        sys.exit(0)
    out = run(rig, tag, float(opts.get('--strength', STRENGTH)), float(opts.get('--at', FIRST_AT_S)))
    sys.exit(0 if out['verdict']['pass'] else 1)
