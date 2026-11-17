"""The garment at rest against every collider it meets: from a normal shell with the editor up,

    python Scripts/cloth_rest.py --hero <Rig>

A collider that reaches into the cloth's rest pose shoves the cloth off the body the moment the simulation starts,
a shelf where the painted (movable) region begins (seen on Aurora's hip). Chaos collides each clothing asset
with that asset's own physics asset AND with the source the drive adds (ChaosClothingSimulation.cpp:398;
pie_dynamics_drive._cloth), so both are checked: per collider body, how far it pushes each asset's particles at
the reference pose (Chaos holds cloth its collision thickness off a collider: the push is the depth inside the surface
plus that thickness), movable ones (max distance > 0) apart from pinned ones. The holding distance is the contact
model's, and one thickness of error past it (a particle up to its thickness inside the collider) is the budget the
dynamics gate gives the other error, the cloth sinking into the body (dyn_contact), which a smaller collider trades it
for (collider_autofit.garment_clear balances the two); a particle deeper is counted. The distance is the optimized tapered
capsule's (TaperedCapsule.h:113: the axis point at the clamped projection, the radius interpolated there), the default
cloth path.
"""
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collider_autofit as af  # noqa: E402

TOL_CM = 0.1        # pushed past the thickness by more than this counts (the rest pose is authored to the mm)


def depth(p, shape):
    """How far point p is inside tapered capsule shape (near, far, near radius, far radius), cm (< 0: outside)."""
    a, b, ra, rb = shape
    t, q = af.seg_closest(p, a, b)
    return ra + (rb - ra) * t - math.dist(p, q)


def clearance(asset, bodies, tms, tol_cm=TOL_CM):
    """{body bone: {'movable', 'pinned' (particles more than the thickness and tol_cm inside the collider: pushed
    past twice it), 'max_cm' (the largest push of a movable one, thickness included), 'limit_cm' (the thickness)}} for one
    clothing asset ({'xyz', 'max_distance', 'thickness_cm'}) against bodies (spec or described: 'bone', 'elements' or
    'shapes') posed by tms ({bone: [x, y, z, qx, qy, qz, qw]}); only bodies that push some particle."""
    import hit_reaction_core as core
    xyz = asset['xyz']
    pts = [xyz[i:i + 3] for i in range(0, len(xyz), 3)]
    th = asset['thickness_cm']
    out = {}
    for body in bodies:
        shapes = core.body_tapered(dict(body, elements=body.get('shapes') or body.get('elements') or []), tms)
        row = {'movable': 0, 'pinned': 0, 'max_cm': 0.0, 'limit_cm': th}
        for p, m in zip(pts, asset['max_distance']):
            push = max((depth(p, s) for s in shapes), default=-1e9) + th
            if push > 2.0 * th + tol_cm:
                row['movable' if m > 0.0 else 'pinned'] += 1
            if m > 0.0 and push > 0.0:
                row['max_cm'] = round(max(row['max_cm'], push), 2)
        if row['movable'] or row['pinned'] or row['max_cm'] > 0.0:
            out[body['bone']] = row
    return out


def verdict(report):
    """(ok, largest movable push cm, where): ok when no movable particle is pushed past twice its thickness."""
    rows = [(r, '%s/%s/%s' % (a, src, bone)) for a, srcs in report.items() for src, rs in srcs.items()
            for bone, r in rs.items()]
    worst = max(((r['max_cm'], w) for r, w in rows), default=(0.0, None))
    return not any(r['movable'] for r, _w in rows), worst[0], worst[1]


def gather(h):
    """The garment, its colliders and the reference pose, read in the editor: (assets, {source: bodies}, tms)."""
    import hero_onboard
    # the JSON GarmentPoints returns is the script's final expression, so it comes back as the value (no log line or
    # file in between; editor_ops.EditorOps.py)
    got = hero_onboard._ops().py("unreal.HeroDynamicsTools.garment_points(unreal.load_asset(%r))\n" % h['mesh'],
                                 timeout=600)
    if not isinstance(got, dict) or not got.get('ok'):
        raise RuntimeError('garment points: %s' % (got.get('error') if isinstance(got, dict) else str(got)[:300]))
    sources = {'added': h['cloth_collision_asset']}
    sources.update({a['name']: a['physics_asset'] for a in got['assets'] if a['physics_asset']})
    colliders = {k: hero_onboard.describe_physics(v).get('bodies') or [] for k, v in sources.items()}
    return got['assets'], colliders, hero_onboard._ref_tms(h)


def report(h):
    """{asset: {source: clearance}}: an asset meets the added source and its own physics asset only."""
    assets, colliders, tms = gather(h)
    return {a['name']: {src: clearance(a, bodies, tms) for src, bodies in colliders.items()
                        if src in ('added', a['name'])} for a in assets}


if __name__ == '__main__':
    import dyn_hero
    rig, _args = dyn_hero.hero_arg(sys.argv[1:])
    rep = report(dyn_hero.resolve(rig))
    ok, worst, where = verdict(rep)
    print('CLOTH_REST ' + json.dumps(rep))
    print('CLOTH_REST %s %s: deepest movable particle %.2f cm inside %s' % (rig, 'PASS' if ok else 'FAIL', worst, where))
    sys.exit(0 if ok else 1)
