"""Cloth penetration: how deep a hero's simulated cloth goes into its body, measured every frame and gated.

The penetration heatmap showed Aurora's dress passing through her upper thigh, a defect no earlier check caught; this
module detects that class of simulation penetration. The drive (pie_dynamics_drive.py) asks, every frame and for
every pawn whose cloth simulates, UHeroDynamicsTools.MeasureClothPenetration: per simulated cloth vertex, its depth
behind the closest body triangle, less the depth the same vertex had skinned before the cloth (an overlap the art
already has there is allowed at that vertex only). The body is the limbs the cloth
collides with (limbs: the cloth collision bodies' bones, hit_reaction_core.CLOTH_ROLES, and their twist leaves),
never a Dynamics chain: a solid limb is the only surface whose back side is an inside (an open blade read 12 cm "inside"
behind its face, Aurora 2026-10-31), and behind a face is inside only nearer the limb's axis than the face (past a skin
section's open end the facing says nothing: 20 cm "inside" a calf). A vertex farther from every limb's axis than that
limb's radius (its farthest vertex) cannot be inside it and is not searched. The dynamics gate fails a run whose cloth
goes deeper than the cloth's own thickness, and one with any frame it could not measure once the cloth was simulating
(dyn_gate 'penetration').

Vertices only: a triangle interior crossing the body between two vertices is not seen; morph targets are not applied.
"""
TOL_CM = 0.2            # a vertex counts as penetrating past this (the record's over_tol)
# The gate: no frame's cloth deeper inside the body than the cloth's own collision thickness (each asset's, recorded by
# the drive; this when unrecorded). Chaos keeps its particles that far off the colliders, and the render vertices are
# rebuilt from them with small offsets: shallower is the cloth's own thickness, not a breach. Aurora at 2 substeps:
# 0.50 cm worst, one hip vertex for one frame, 99% of frames under 0.12 (2026-10-31).
MAX_CM = 1.0
# The cloth solver's substeps per frame (pie_dynamics_drive._cloth_collision). Swept on Aurora's run
# (dyn_run --set cloth_substeps=N, 2026-10-31): 1 -> 5.14 cm, 45 frames past 0.5 cm (the swinging thigh crossed the
# dress between steps, inside its collider by 3-7 cm); 2 -> 0.50, none; 4 -> 0.55, 2 (no better for twice the cost).
SUBSTEPS = 2


def thickness(cloth_events):
    """The thinnest collision thickness among the simulating pawns' clothing assets (the drive's events['cloth']), or
    MAX_CM when none is recorded."""
    got = [a['thickness'] for c in cloth_events if c.get('arm') == 'on'
           for a in ((c.get('collision') or {}).get('assets') or {}).values() if 'thickness' in a]
    return min(got) if got else MAX_CM


def limbs(profile):
    """The limbs the cloth collides with, as [(bone, aim)]: its collision bodies' (hit_reaction_core.CLOTH_ROLES), each
    from its bone toward its aim (the spine role is a chain: its first bone), the axis the limb's skin surrounds."""
    import hit_reaction_core
    roles, out = profile['roles'], []
    for role, aim_role, _seg in hit_reaction_core.CLOTH_ROLES:
        aim = roles.get(aim_role)
        aim = aim[0] if isinstance(aim, list) and aim else aim
        if roles.get(role) and aim:
            out.append((roles[role], aim))
    return out


def excluded(parts):
    """Never body: every Dynamics chain's bones (the profile's secondary parts)."""
    return sorted({b for p in parts for b in p['bones']})


def summary(frames):
    """Over a record's frames: {pawn index: {'frames', 'warming', 'failed', 'max_cm', 'over_tol_frames', 'worst'}},
    the pawns measured; {} when none was (no cloth simulating). The frames before a pawn's cloth first simulates are
    'warming' (the first sample after spawn has no cloth data yet); a failure after that is 'failed'."""
    out = {}
    for row in frames:
        for k, m in (row.get('penetration') or {}).items():
            s = out.setdefault(k, {'frames': 0, 'warming': 0, 'failed': 0, 'max_cm': 0.0, 'over_tol_frames': 0,
                                   'worst': None, 'started': False})
            s['frames'] += 1
            if not m.get('ok') or not m.get('tested'):
                # a frame that measured no cloth vertex is no measurement (never "0 cm"): failed once the cloth simulates
                s['failed' if s['started'] else 'warming'] += 1
                continue
            s['started'] = True
            s['over_tol_frames'] += 1 if m['over_tol'] else 0
            if m['max_cm'] > s['max_cm']:
                s['max_cm'] = m['max_cm']
                s['worst'] = dict(m.get('worst') or {}, f=row.get('f'))
    return out


def verdict(summary_, expected, limit_cm=MAX_CM):
    """(ok, detail) for a run: every pawn expected to simulate cloth (expected: their indices as strings) measured on
    every frame once its cloth simulated, and never deeper than limit_cm (the cloth's thickness: thickness())."""
    if sorted(summary_) != sorted(expected):
        return False, 'measured pawns %s, cloth simulating on %s' % (sorted(summary_), sorted(expected))
    bad = {k: s for k, s in summary_.items() if s['failed'] or not s['started'] or s['max_cm'] > limit_cm}
    detail = '; '.join('pawn %s: max %.2f cm (<= %.2f) at %s, %d frames over %.1f cm, %d failed, %d warming of %d'
                       % (k, s['max_cm'], limit_cm, _where(s['worst']), s['over_tol_frames'], TOL_CM, s['failed'],
                          s['warming'], s['frames']) for k, s in sorted(summary_.items()))
    return not bad, detail or 'no cloth simulating'


def _where(w):
    return 'frame %s, %s' % (w.get('f'), w.get('body_bone')) if w else 'nowhere'
