"""The dynamics gate: pass/fail over a dyn_analyze result (an ON pawn vs an OFF pawn) and its record, against a
committed per-hero baseline. docs/specs/2026-10-27-secondary-motion-backends-design.md §3.6: a failed or unverifiable check fails the run; nothing reads as a
pass by being absent (inventories are checked against the baseline, not taken from the result).

COST IS REPORTED, NOT GATED: summed ControlRig_Evaluate is sampled every run (dyn_run.py) and kept in the record
and the baseline, but the sampler is not locked to a phase of the drive, and the same rig read 1.07-1.45 ms across runs.
A pass/fail on it would be noise; it becomes a check once sampling is phase-locked.

Checks, each proven red by test_dyn_gate.py:
  setup      - no driver error, alphas exactly [1, 0] and read back as such, exactly one pair.
  identity   - the record's parts and colliders are the ones the loaded rig was built from (build manifest).
  integrity  - every witness curve found, the rig ticked every frame, every solver event key present and empty after
               initialisation (both pawns).
  isolation  - control bones outside every part match between the ON and OFF pawn (positions; a sample, not the
               whole pose).
  inventory  - the parts measured are the record's parts, and every baseline part is among them.
  responds   - each part's tip moves at least RESPONDS_CM from the OFF pose in the stop window.
  clearance  - each part's collider checks all ran, its minimum clearance is no worse than the baseline (beyond
               CLEAR_TOL_CM, numerical noise: runs repeat to 1e-4 cm), and no more frames under tolerance.
  bounded    - each part's largest frame-to-frame tip step and its motion after settling are no larger than the
               baseline's (+ BOUND_TOL_CM / SETTLE_TOL_CM).
  legs       - both sides' paired leg-clearance margin no worse than the baseline (CLEAR_TOL_CM).
  penetration - every pawn whose cloth simulates measured on every frame once it simulates, and its cloth never deeper
               inside the body than the cloth's own collision thickness (dyn_contact; absolute: no baseline makes cloth
               inside a thigh acceptable).
  behaviour  - each part's run trail, stop swing, run twist RMS and stop twist peak within BEHAVIOUR_TOL_DEG of the
               baseline (no twist in both runs is equal): an intended change
               needs a new baseline, written with a reason (dyn_run.py --rebaseline "<why>").
"""
import hashlib
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dyn_contact  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE_DIR = os.path.join(REPO, 'Plugins', 'MotionTerminal', 'Tests', 'DynamicsBaselines')
ALPHAS = [1.0, 0.0]
ISOLATION_CM = 1e-3
RESPONDS_CM = 2.0
CLEAR_TOL_CM = 0.1
BOUND_TOL_CM = 2.0
SETTLE_TOL_CM = 0.5
BEHAVIOUR_TOL_DEG = 1.0
TWIST_KEYS = ('twist_rms_deg', 'stop_twist_peak_deg')
COST_KEY = 'ControlRig_Evaluate_ms'
COST_MIN_FRAMES = 10      # of the 13-frame stats history; a one-frame read is not a measurement
EVENTS = ('MT_DynTeleport', 'MT_DynPosReset', 'MT_DynKinReset')
# The build manifest's experiment overrides (dynamics_graph.write_manifest): each must be recorded, and None.
EXPERIMENT_KEYS = ('behaviours', 'air')
LEG_SIDES = ('l', 'r')


def digest(obj):
    """Content hash of a JSON value (pie_dynamics_drive.py keeps the same definition: it cannot import this)."""
    return digest_bytes(json.dumps(obj, sort_keys=True).encode())


def digest_bytes(data):
    return hashlib.sha1(data).hexdigest()[:12]


def source_digest(name):
    """digest_bytes of Scripts/<name> as it is on disk now (dynamics_graph.write_manifest hashes the same way), or
    None when the file is gone. 'extras:<rig>' is the hero's HeroProfiles/dynamics/<rig>.json."""
    if name.startswith('extras:'):
        path = os.path.join(REPO, 'HeroProfiles', 'dynamics', name.split(':', 1)[1] + '.json')
    else:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
    if not os.path.exists(path):
        return None
    with open(path, 'rb') as f:
        return digest_bytes(f.read())


def baseline_path(hero):
    return os.path.join(BASELINE_DIR, hero + '.json')


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _clearance_ran(clear):
    """A part's collider clearance checks all ran: none skipped, at least one run."""
    return (bool(clear) and clear.get('checks_skipped') == 0 and _num(clear.get('checks_run'))
            and clear['checks_run'] > 0)


def measure(result):
    """The numbers a baseline keeps, from an ON/OFF analysis (pawn 0 ON, pawn 1 OFF)."""
    on = result['pawns'][0]
    pair = result['pairs'][0]
    parts = {}
    for pid in pair['stop_window_tip_delta']:
        clear = on['s5'].get('collider_clearance', {}).get(pid, {})
        live = on['s5'].get('liveliness', {}).get(pid, {})
        adj = on.get('adjacent_tip_motion', {}).get(pid, {})
        parts[pid] = {'min_clearance_cm': clear.get('min_clearance_cm'), 'frames_under_tol': clear.get('frames_under_tol'),
                      'clearance_na': bool(clear.get('not_applicable')),
                      'trail_deg': on['s5'].get('run_trail_deg', {}).get(pid), 'stop_fwd_deg': live.get('stop_fwd'),
                      'stop_window_tip_delta_cm': pair['stop_window_tip_delta'][pid],
                      'max_step_cm': adj.get('max'), 'settle_step_cm': adj.get('settle_max'),
                      # The twist modifier moves no particle, so only a rotation measure can see it:
                      # the run roll RMS and the stop roll peak, ON vs OFF.
                      'twist_rms_deg': (pair.get('run_twist', {}).get(pid) or {}).get('rms_deg'),
                      'stop_twist_peak_deg': (pair.get('stop_twist', {}).get(pid) or {}).get('peak_deg')}
    legs = {s: v['worst_margin_cm'] for s, v in pair.get('paired_clearance', {}).items()}
    # Analyses from before the flag had a leg check whenever they had legs: applicable.
    legs_applicable = pair.get('legs_applicable', True)
    cost = result.get('cost') or {}
    ok = cost.get('settled') and _num(cost.get('frames')) and cost['frames'] >= COST_MIN_FRAMES
    return {'parts': parts, 'legs': legs, 'legs_applicable': legs_applicable,
            'cost_ms': cost.get(COST_KEY) if ok else None}


def check(result, baseline, record=None):
    """[(check, subject, ok, detail)] for every check; the run passes only when every ok is True. `record` is the raw
    driver record (for setup and identity); without it those checks fail as NOT VERIFIED."""
    rows = []

    def row(name, subject, ok, detail):
        rows.append((name, subject, bool(ok), detail))

    pawns, pairs = result.get('pawns') or [], result.get('pairs') or []
    row('setup', 'record', result.get('error') is None and 'error' in result
        and result.get('frame_clock_nonfinite') == 0,
        'error=%r, frames with a non-finite t/dt %s' % (result.get('error'), result.get('frame_clock_nonfinite')))
    row('setup', 'alphas', result.get('alphas') == ALPHAS and len(pawns) == 2 and len(pairs) == 1
        and all(p.get('alpha_read') == [a, a] and p.get('alpha_reads') == [a] for p, a in zip(pawns, ALPHAS)),
        'alphas %s, read %s, %d pawns, %d pairs' % (result.get('alphas'), [p.get('alpha_read') for p in pawns],
                                                    len(pawns), len(pairs)))
    if len(pawns) != 2 or len(pairs) != 1:
        return rows
    build = (record or {}).get('build') or {}
    # The shipped build only: colliders on, no experiment override of any kind (a sweep's rig is not the product).
    # The rig in the editor was built from these sources: a library or builder edited since (and not rebuilt) is a
    # different product from the one on disk (a stale library hash passed identity).
    stale = sorted(n for n, d in (build.get('scripts') or {}).items() if d != source_digest(n))
    # ...and a hero extras file added since the build (the old manifest has no entry to compare)
    extras = 'extras:%s' % (record or {}).get('hero')
    if (record or {}).get('hero') and extras not in (build.get('scripts') or {}) and source_digest(extras):
        stale.append(extras + ' (added since the build)')
    row('identity', 'sources', record is not None and bool(build.get('scripts')) and not stale,
        'NOT VERIFIED: no record' if record is None else
        'no source digests in the manifest' if not build.get('scripts') else
        'edited since the build (rebuild): %s' % stale if stale else '%d sources match' % len(build['scripts']))
    # The loaded rig was built for this record's hero (a build left in the editor by another hero's run is refused).
    rhero = (record or {}).get('hero')
    # Labels alone are not enough: the drive must also have applied THIS build's ABP, not a
    # pp_class override pointing at another hero's.
    pp = ((record or {}).get('config') or {}).get('pp_class') or ''
    row('identity', 'hero', record is not None and rhero is not None and build.get('hero') == rhero
        and 'hero_mismatch' not in build and bool(build.get('abp')) and pp.split('.')[0] == build.get('abp'),
        'NOT VERIFIED: no record' if record is None else
        'record %s, build %s, drive ABP %s, built ABP %s%s' % (
            rhero, build.get('hero'), pp, build.get('abp'),
            ' (%s)' % build['hero_mismatch'] if 'hero_mismatch' in build else ''))
    row('identity', 'build', record is not None and build.get('parts') == digest(record.get('parts'))
        and build.get('colliders') == digest(record.get('colliders'))
        and build.get('with_colliders') is True and all(k in build and build[k] is None for k in EXPERIMENT_KEYS),
        'NOT VERIFIED: no record' if record is None else
        'manifest parts %s / record %s, colliders %s / %s, with_colliders %s, overrides %s' % (
            build.get('parts'), digest(record.get('parts')), build.get('colliders'), digest(record.get('colliders')),
            build.get('with_colliders'), {k: build.get(k, 'ABSENT') for k in EXPERIMENT_KEYS}))
    for i, p in enumerate(pawns):
        ev = p.get('event_frames', {})
        events = {c: ev[c] for c in EVENTS if ev.get(c)}
        row('integrity', 'pawn %d' % i, p.get('curves_found_all') and p.get('tick_advances_every_frame')
            and all(c in ev for c in EVENTS) and not events and p.get('nonfinite') == 0,
            'curves %s, ticks %s, events %s, non-finite samples %s' % (
                p.get('curves_found_all'), p.get('tick_advances_every_frame'),
                events or ('none' if all(c in ev for c in EVENTS) else 'MISSING KEYS'), p.get('nonfinite')))
    # Absolute, not against the baseline: cloth inside the body is a defect at any baseline (dyn_contact)
    cloth = ((record or {}).get('events') or {}).get('cloth') or []
    expected = [str(i) for i, c in enumerate(cloth) if c.get('arm') == 'on']
    ok, detail = dyn_contact.verdict(dyn_contact.summary((record or {}).get('frames') or []), expected,
                                     dyn_contact.thickness(cloth))
    row('penetration', 'cloth', record is not None and ok, 'NOT VERIFIED: no record' if record is None else detail)
    pair = pairs[0]
    ctrl = pair.get('control_bone_delta', {})
    worst = max(ctrl.items(), key=lambda kv: kv[1] if _num(kv[1]) else float('inf'), default=(None, None))
    row('isolation', 'control bones', ctrl and all(_num(v) and v <= ISOLATION_CM for v in ctrl.values()),
        'max %s cm on %s (%d bones)' % (worst[1], worst[0], len(ctrl)))
    got = measure(result)
    base = (baseline or {}).get('parts', {})
    measured = set(got['parts'])
    recorded = {p['id'] for p in (record or {}).get('parts', [])}
    row('inventory', 'parts', record is not None and measured == recorded and set(base) <= measured,
        'measured %d, recorded %d, baseline %d; missing %s, unrecorded %s' % (
            len(measured), len(recorded), len(base), sorted(set(base) - measured) or 'none',
            sorted(measured ^ recorded) or 'none'))
    for pid, m in sorted(got['parts'].items()):
        row('responds', pid, _num(m['stop_window_tip_delta_cm']) and m['stop_window_tip_delta_cm'] >= RESPONDS_CM,
            '%s cm (>= %.1f)' % (m['stop_window_tip_delta_cm'], RESPONDS_CM))
        b = base.get(pid)
        if b is None:
            for name in ('clearance', 'bounded', 'behaviour'):
                row(name, pid, False, 'NOT VERIFIED: no baseline for this part')
            continue
        b = {k: b.get(k) for k in m}     # a field the baseline lacks is None: NOT VERIFIED below, never a crash
        clear = pawns[0]['s5'].get('collider_clearance', {}).get(pid)
        if clear and clear.get('not_applicable'):
            # as the legs row: n/a passes only when the baseline agrees (it recorded no clearance either)
            # a baseline that merely LACKS the value is not agreement: it must have recorded the n/a itself
            row('clearance', pid, b.get('clearance_na') is True, 'not applicable: %s (baseline %s)'
                % (clear['not_applicable'], 'n/a' if b.get('clearance_na') is True else b['min_clearance_cm']))
        elif not _clearance_ran(clear) or not _num(m['min_clearance_cm']) or not _num(m['frames_under_tol']) \
                or not _num(b['min_clearance_cm']) or not _num(b['frames_under_tol']):
            row('clearance', pid, False, 'NOT VERIFIED: %s' % (clear and '%s checks skipped, %s run'
                                                               % (clear.get('checks_skipped'), clear.get('checks_run'))))
        else:
            row('clearance', pid, m['min_clearance_cm'] >= b['min_clearance_cm'] - CLEAR_TOL_CM
                and m['frames_under_tol'] <= b['frames_under_tol'],
                'min %.2f cm (baseline %.2f), %d frames under tol (baseline %d)'
                % (m['min_clearance_cm'], b['min_clearance_cm'], m['frames_under_tol'], b['frames_under_tol']))
        row('bounded', pid, all(_num(v) for v in (m['max_step_cm'], m['settle_step_cm'], b['max_step_cm'],
                                                    b['settle_step_cm']))
            and m['max_step_cm'] <= b['max_step_cm'] + BOUND_TOL_CM
            and m['settle_step_cm'] <= b['settle_step_cm'] + SETTLE_TOL_CM,
            'max step %s cm (baseline %s), settled step %s (baseline %s)'
            % (m['max_step_cm'], b['max_step_cm'], m['settle_step_cm'], b['settle_step_cm']))
        drift = [k for k in ('trail_deg', 'stop_fwd_deg')
                 if not (_num(m[k]) and _num(b[k]) and abs(m[k] - b[k]) <= BEHAVIOUR_TOL_DEG)]
        # A part with no measurable roll (no recorded rotations, or no axis) has none in either run: equal.
        drift += [k for k in TWIST_KEYS
                  if not ((m[k] is None and b[k] is None)
                          or (_num(m[k]) and _num(b[k]) and abs(m[k] - b[k]) <= BEHAVIOUR_TOL_DEG))]
        row('behaviour', pid, not drift, 'trail %s (baseline %s), stop swing %s (baseline %s), twist rms %s (baseline '
            '%s), stop twist peak %s (baseline %s)'
            % (m['trail_deg'], b['trail_deg'], m['stop_fwd_deg'], b['stop_fwd_deg'], m['twist_rms_deg'],
               b['twist_rms_deg'], m['stop_twist_peak_deg'], b['stop_twist_peak_deg']))
    base_applicable = (baseline or {}).get('legs_applicable', True)
    if not got['legs_applicable']:
        # No part hangs from the pelvis (a hair-only hero): there are no legs to guard. Passes only when the baseline
        # agrees, so a part that gains legs, or a check that silently stopped running, still fails.
        row('legs', 'n/a', not base_applicable and not got['legs'],
            'not applicable: no part hangs from the pelvis (baseline applicable: %s)' % base_applicable)
        return rows
    for side in LEG_SIDES:
        margin, b = got['legs'].get(side), (baseline or {}).get('legs', {}).get(side)
        row('legs', side, _num(margin) and _num(b) and margin >= b - CLEAR_TOL_CM,
            'margin %s cm (baseline %s)' % (margin, b))
    return rows


def passed(rows):
    return bool(rows) and all(ok for _n, _s, ok, _d in rows)


STRUCTURAL = ('setup', 'identity', 'integrity', 'isolation', 'responds')


def baseline_blockers(result, record):
    """Why this run cannot become a baseline: any structural check failing, or a part whose clearance did not fully
    run. Clearance and behaviour VALUES may change (that is what a new baseline records, with its reason)."""
    rows = check(result, None, record)
    bad = [r for r in rows if r[0] in STRUCTURAL and not r[2]]
    got = measure(result)
    if not (record and {p['id'] for p in record.get('parts', [])} == set(got['parts'])):
        bad.append(('inventory', 'parts', False, 'measured parts are not the recorded parts'))
    for pid, m in got['parts'].items():
        clear = result['pawns'][0]['s5'].get('collider_clearance', {}).get(pid)
        na = bool(clear and clear.get('not_applicable'))
        if not na and not _clearance_ran(clear):
            bad.append(('clearance', pid, False, 'clearance did not fully run'))
        # A roll measure is absent where no rotation is recorded (behaviour treats absent in both runs as equal); a
        # present one must still be finite. A part with no colliders has no clearance values.
        skip = set(TWIST_KEYS) | ({'min_clearance_cm', 'frames_under_tol'} if na else set())
        missing = sorted(k for k, v in m.items() if k != 'clearance_na' and not _num(v)
                         and not (k in skip and v is None))
        if missing:
            bad.append(('values', pid, False, 'not finite or missing: %s' % missing))
    if got['legs_applicable'] and (sorted(got['legs']) != list(LEG_SIDES)
                                   or not all(_num(v) for v in got['legs'].values())):
        bad.append(('values', 'legs', False, 'legs %s' % got['legs']))
    return bad


def write_baseline(hero, result, record, why):
    bad = baseline_blockers(result, record)
    if bad:
        raise RuntimeError('refusing to baseline this run: %s' % bad)
    if not why or not why.strip():
        raise RuntimeError('a baseline needs a reason')
    doc = dict(measure(result), why=why, source=os.path.basename(result['path']), build=record.get('build'))
    os.makedirs(BASELINE_DIR, exist_ok=True)
    with open(baseline_path(hero), 'w', newline='\n') as f:
        f.write(json.dumps(doc, indent=1, sort_keys=True) + '\n')
    return doc
