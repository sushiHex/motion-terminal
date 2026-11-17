"""Compare dynamics runs per behaviour: one table row per (run, behaviour group), against a reference run.

    python Scripts/dyn_compare.py --hero <Rig> <reference tag> <tag> [tag ...]

Reads Saved/MotionTerminalTests/dyn_<Rig>_<tag>.json (dyn_run / dyn_sweep records); without --hero (and no
MT_HERO) it reads the pre-hero khai_dyn_<tag>.json records instead, never a mix. Parts are grouped by the
behaviour the record's own profile snapshot assigns (colliders.chains[*].behaviour); a record from before behaviours
falls back to the current library and its group is marked with '?'. Per group, medians over its parts of:
  trail       run trail behind vertical (deg)              hang s/r   ON minus OFF hang from straight down, standing /
  flut cm/Hz  run flutter amplitude / rate                            running (deg; + = forward, free of the body)
  settle      seconds to settle after the stop (worst)     clear      the group's worst collider clearance (cm)
  moved       parts whose measures (all of the above, and the gate's) differ from the reference by more than TOL, or
              that gained or lost a value (a part that stopped settling counts)
  max_d       the group's largest change in any measure from the reference: 0.000 on a group you did not touch is
              the isolation proof. A part missing from either run is reported, never skipped.
Reported, never gated (the gate is dyn_run --gate). Workflow: docs/dynamics-tuning-workflow.md.
"""
import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dyn_behaviours  # noqa: E402
import dyn_hero  # noqa: E402
import dyn_analyze as an  # noqa: E402
import dyn_gate as gate  # noqa: E402

TOL = 0.05
MEASURES = ('trail_deg', 'stop_fwd_deg', 'stop_window_tip_delta_cm', 'max_step_cm', 'min_clearance_cm',
            'flutter_cm', 'flutter_hz', 'settle_s', 'stand_hang', 'run_hang', 'twist_rms_deg', 'stop_twist_peak_deg', 'root_lift')
ANGLES = {'trail_deg', 'stop_fwd_deg', 'stand_hang', 'run_hang'}   # compared across runs on the circle


def delta(k, a, b):
    """|a - b| for measure k; an angle wraps (179.99 vs -179.99 is 0.02); a value present in one run and not the other
    is an infinite change (a part that stopped settling is never 'unchanged')."""
    if _num(a) != _num(b):
        return float('inf')
    if not _num(a):
        return 0.0
    return abs(_wrap(a - b)) if k in ANGLES else abs(a - b)


def _num(v):
    return isinstance(v, (int, float))


def _wrap(deg):
    """An angle difference in (-180, 180]: +179 minus -179 is -2, not 358."""
    return (deg + 180.0) % 360.0 - 180.0 if deg != 180.0 else 180.0


def _med(vals):
    vals = [v for v in vals if _num(v)]
    return statistics.median(vals) if vals else None


def record_path(tag, hero=None):
    """With a hero, its record dyn_<hero>_<tag>.json and nothing else: a missing record must never be replaced by
    another hero's history. Without one, the pre-hero khai_dyn_<tag>.json records."""
    return dyn_hero.record_path({'rig': hero}, tag) if hero else dyn_hero.legacy_record_path(tag)


def load(tag, lib, hero=None):
    path = record_path(tag, hero)
    r = an.analyze(path)
    with open(path) as f:
        rec = json.load(f)
    if r.get('alphas') != [1.0, 0.0]:
        raise ValueError('%s: alphas %s, not [1, 0] (ON pawn first): ON - OFF would be meaningless' % (tag, r['alphas']))
    if 'parts' not in rec or 'colliders' not in rec:
        # Without its own snapshot the clearance would be measured against TODAY's colliders: refuse, do not guess
        raise ValueError('%s: record has no parts/colliders snapshot (written before 2026-10-27); re-run it' % tag)
    snap = (rec.get('colliders') or {}).get('chains', {})
    group = {p['id']: snap.get(p['id'], {}).get('behaviour') or dyn_behaviours.resolve(p, lib) + '?'
             for p in rec['parts']}
    pair = r['pairs'][0]
    s5 = [p['s5'] for p in r['pawns']]
    rows = {}
    for pid, m in gate.measure(r)['parts'].items():
        hang = lambda k: tuple((s.get(k, {}).get(pid) or {}).get('fwd') for s in s5[:2])   # noqa: E731
        sh, rh = hang('stand_hang_deg'), hang('run_hang_deg')
        rows[pid] = dict(m, flutter_cm=pair.get('run_flutter_cm', {}).get(pid),
                         flutter_hz=pair.get('run_flutter_hz', {}).get(pid),
                         settle_s=pair.get('stop_settle_s', {}).get(pid),
                         root_lift=pair.get('root_lift_cm', {}).get(pid),
                         stand_hang=_wrap(sh[0] - sh[1]) if all(_num(v) for v in sh) else None,
                         run_hang=_wrap(rh[0] - rh[1]) if all(_num(v) for v in rh) else None)
    events = {k: v for k, v in r['pawns'][0]['event_frames'].items() if v}
    return rows, group, events, (r.get('cost') or {}).get('ControlRig_Evaluate_ms')


def table(ref_tag, tags, lib=None, hero=None):
    lib = lib or dyn_behaviours.load()
    ref, _g, _e, _c = load(ref_tag, lib, hero)
    lines = ['%-14s %-12s %3s %7s %6s %6s %6s %5s %6s %6s %7s %6s %7s %7s' % (
        'run', 'behaviour', 'n', 'trail', 'hang_s', 'hang_r', 'flutcm', 'flHz', 'settle', 'worst', 'clear', 'lift',
        'moved', 'max_d')]
    for tag in [ref_tag] + list(tags):
        rows, group, events, cost = load(tag, lib, hero)
        missing = sorted(set(ref) - set(rows)), sorted(set(rows) - set(ref))
        for b in sorted(set(group.values())):
            ids = [p for p in rows if group.get(p) == b]
            deltas = [delta(k, rows[p].get(k), ref[p].get(k)) for p in ids if p in ref for k in MEASURES]
            moved = sum(1 for p in ids if p not in ref or
                        any(delta(k, rows[p].get(k), ref[p].get(k)) > TOL for k in MEASURES))
            f = lambda k: _med([rows[p].get(k) for p in ids])   # noqa: E731
            fmt = lambda v, w=6, d=2: ('%*.*f' % (w, d, v)) if _num(v) else '%*s' % (w, '-')   # noqa: E731
            settle = [rows[p]['settle_s'] for p in ids if _num(rows[p]['settle_s'])]
            clear = [rows[p]['min_clearance_cm'] for p in ids if _num(rows[p].get('min_clearance_cm'))]
            lift = [rows[p]['root_lift'] for p in ids if _num(rows[p].get('root_lift'))]
            lines.append('%-14s %-12s %3d %s %s %s %s %s %s %s %s %s %4d/%-2d %s' % (
                tag, b, len(ids), fmt(f('trail_deg'), 7), fmt(f('stand_hang')), fmt(f('run_hang')),
                fmt(f('flutter_cm')), fmt(f('flutter_hz'), 5), fmt(f('settle_s')),
                fmt(max(settle) if settle else None), fmt(min(clear) if clear else None, 7),
                fmt(max(lift) if lift else None), moved, len(ids),
                fmt(max(deltas) if deltas else None, 7, 3)))
        lines.append('%-14s events %s, cost %s ms%s' % (
            tag, events or 'none', cost, '' if not any(missing) else
            '; parts only in the reference %s, only here %s' % missing))
    return '\n'.join(lines)


if __name__ == '__main__':
    if any(a.startswith('--hero') for a in sys.argv[1:]) or os.environ.get(dyn_hero.ENV):
        hero, args = dyn_hero.hero_arg(sys.argv[1:])     # a malformed --hero raises
    else:
        hero, args = None, sys.argv[1:]                  # no hero: legacy records only
    print(table(args[0], args[1:], hero=hero))
