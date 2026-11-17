"""One harness run, end to end, from a normal shell (editor up): load the stage, place the pawns, start PIE,
drive, wait for the record, stop PIE, clear. Then print the analysis.

    python Scripts/dyn_run.py --hero <Rig> <tag> [alpha ...]     e.g.  s3 1 0     or   s5_offoff 0 0
    python Scripts/dyn_run.py --hero <Rig> <tag> --gate                 ON/OFF run judged by dyn_gate
    python Scripts/dyn_run.py --hero <Rig> <tag> --rebaseline "<why>"   same run, written as the new baseline
    python Scripts/dyn_run.py --hero <Rig> <tag> --gate --set cloth_substeps=4   an experiment's drive override

The record is Saved/MotionTerminalTests/dyn_<Rig>_<tag>.json (pie_dynamics_drive.py), the analysis is
dyn_analyze.py's. Exit 1 if the driver reported an error or never finished, or, with --gate, if any gate check
failed or could not be verified.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dyn_hero  # noqa: E402
import editor_ops  # noqa: E402
import dyn_analyze  # noqa: E402
import dyn_gate  # noqa: E402

STAGE = '/Game/MT/Army/Lvl_MT_Army'
DRIVER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pie_dynamics_drive.py').replace('\\', '/')
TIMEOUT_S = 240
SUMMARY = 'KHAIDYN_DRIVE {'
FAILED = 'KHAIDYN_DRIVE error'


def _exec(action, cfg, timeout=120):
    """One driver action (place | drive | clear), with the same config every time (the driver keeps no state between
    calls but what it parks on the unreal module)."""
    return editor_ops.shared().run_file(DRIVER, timeout=timeout, KDD_ACTION=action, KDD_CONFIG=cfg)


def _summary(tag, hero):
    """True for this run's KHAIDYN_DRIVE summary line, or its error line (which ends the wait): both carry the tag
    and hero."""
    def match(line):
        if SUMMARY not in line and FAILED not in line:
            return False
        tail = line.split(FAILED, 1)[1] if FAILED in line else line.split('KHAIDYN_DRIVE', 1)[1]
        try:
            body = json.JSONDecoder().raw_decode(tail.lstrip())[0]
        except ValueError:
            return False
        return body.get('tag') == tag and body.get('hero') == hero
    return match


def _attempt(steps):
    """Run every cleanup step even when an earlier one fails; then raise the first failure, unless the run itself is
    already raising (that error is the one to see, and the cleanup failures are printed)."""
    running = sys.exc_info()[1]
    first = None
    for step in steps:
        try:
            step()
        except Exception as exc:     # noqa: BLE001 - re-raised below, after the remaining steps
            print('KHAIDYN_RUN cleanup step failed: %s' % str(exc)[:300])
            first = first or exc
    if first and running is None:
        raise first


def run(hero, tag, alphas, extra=None):
    ops = editor_ops.shared()
    cfg = {'tag': tag, 'hero': hero, 'alphas': alphas}
    cfg.update(extra or {})
    log = ops.log()                              # only a summary logged after this point counts
    ops.reset_level(STAGE)                       # whatever an interrupted earlier run left in the stage is discarded
    _exec('place', cfg)
    started = time.time()
    line, cost = None, None
    try:
        ops.start_pie()
        _draw_nothing(ops)
        _exec('drive', cfg)
        match = _summary(tag, hero)
        while line is None and time.time() - started < TIMEOUT_S:
            line = log.wait_for(match, timeout=2.0)
            if line is None and cost is None and time.time() - started > COST_AFTER_S:
                cost = _sample_cost(ops)
    finally:
        _attempt([ops.stop_pie,
                  lambda: ops.py('unreal.RenderSessionTools.set_editor_viewports_realtime(True)'),
                  lambda: _exec('clear', cfg)])
    if line is None or FAILED in line:
        print('KHAIDYN_RUN %s: %s' % (tag, editor_ops.EditorLog.message(line) if line else
                                      'no record within %d s' % TIMEOUT_S))
        return None
    summary = editor_ops.json_after(line, 'KHAIDYN_DRIVE')
    # The cost goes into the RECORD, so re-analysing the saved run reproduces the verdict.
    with open(summary['path']) as f:
        rec = json.load(f)
    rec['cost'] = cost
    with open(summary['path'], 'w') as f:
        json.dump(rec, f)
    return dyn_analyze.analyze(summary['path'])


DRAW_WAIT_S = 30.0


def _draw_nothing(ops):
    """A drive measures and renders nothing, so nothing is drawn while it runs (the editor's load on top
    of everything else running slows the whole machine): the editor's viewports stop drawing and the play
    world's game viewport draws no world (URenderSessionTools), both restored when the run ends (the game viewport
    goes with PIE). Waits for PIE's game viewport, which can open a tick after the PIE world. The tool's JSON reply
    arrives decoded (EditorOps.py)."""
    ops.py('unreal.RenderSessionTools.set_editor_viewports_realtime(False)')
    ops.wait('PIE game viewport', lambda t: ops.py('unreal.RenderSessionTools.set_play_world_rendering(False)',
                                                   timeout=t).get('viewports'), DRAW_WAIT_S)


COST_AFTER_S = 4.0          # real seconds into the drive (past spawn and initialisation; the drive can finish in ~15 s)
# The stats history holds STAT_FRAME_SLOP + 10 = 13 settled frames (StatsData.h:597): ask for 12.
COST_FRAMES = 12
COST_WAIT_S = 30.0
COST_STATS = ('ControlRig_Evaluate', 'PerformAnimEvaluation_WorkerThread')


def _sample_cost(ops):
    """Anim evaluation cost from STATGROUP_Anim over COST_FRAMES frames completed after sampling starts (the army
    budget method: summed worker-thread cycle stats, never frame time / N, which measures the editor's idle
    throttle). Cycle stats are real milliseconds whatever the fixed game step. ControlRig_Evaluate sums every Control
    Rig on every pawn, including the template's foot IK, so it compares runs; it is not one rig's cost. The stat
    window holds collection on itself (EditorSessionTools.StatSampleBegin), whatever a stat HUD toggled; 'settled' is
    false when the engine produced no frame for it."""
    try:
        reply = ops.sample_stats('Anim', COST_FRAMES, timeout=COST_WAIT_S)
    except Exception as exc:            # a stats failure must read as "not measured", never as zero cost
        return {'error': str(exc)[:300]}
    out = {'settled': bool(reply.get('collecting')) and reply.get('frames', 0) > 0, 'frames': reply.get('frames'),
           'names': {}}
    for name, st in (reply.get('stats') or {}).items():
        # EXACT name: a substring match also took ControlRig_Evaluate_Construction.
        short = name[5:] if name.startswith('STAT_') else name
        if short in COST_STATS and isinstance(st.get('avg_ms'), (int, float)):
            out[short + '_ms'] = st['avg_ms']
            out['names'][short] = name
    return out

if __name__ == '__main__':
    # --nostep: the causal control. The solver is not stepped (ControlRig.Dynamics.EnableStepSolver 0 for the run,
    # restored to 1), so an ON/OFF difference that survives it is not the dynamics.
    hero, argv = dyn_hero.hero_arg(sys.argv[1:])
    dyn_hero.resolve(hero)                              # fail here, before the editor, on a hero with no profile
    why = argv[argv.index('--rebaseline') + 1] if '--rebaseline' in argv else None
    # --set key=value (repeatable): a drive CONFIG override for an experiment (value read as JSON, else a string),
    # e.g. --set cloth_substeps=4. Never a baseline: the shipped drive is what a baseline records.
    sets = [argv[i + 1] for i, a in enumerate(argv) if a == '--set']
    if sets and why:
        raise SystemExit('--set is an experiment: it cannot be written as the baseline')
    args = [a for i, a in enumerate(argv) if not a.startswith('--') and a != why and (i == 0 or argv[i - 1] != '--set')]
    extra = {'cvars': [['ControlRig.Dynamics.EnableStepSolver', 0, 1]]} if '--nostep' in argv else {}
    for s in sets:
        k, v = s.split('=', 1)
        try:
            extra[k] = json.loads(v)
        except ValueError:
            extra[k] = v
    tag = args[0]
    alphas = [float(a) for a in args[1:]] or [1.0, 0.0]
    result = run(hero, tag, alphas, extra)
    ok = bool(result) and not result.get('error')
    if ok and ('--gate' in argv or why):
        rec = json.load(open(result['path']))
        try:
            base = json.load(open(dyn_gate.baseline_path(hero)))
        except (OSError, ValueError):
            base = None
        # Judged against the CURRENT baseline first and printed, so a rebaseline always shows what it replaces.
        rows = dyn_gate.check(result, base, rec)
        for name, subject, good, detail in rows:
            print('KHAIDYN_GATE %-4s %-10s %-22s %s' % ('PASS' if good else 'FAIL', name, subject, detail))
        ok = dyn_gate.passed(rows)
        cost = result.get('cost') or {}
        print('KHAIDYN_COST ControlRig_Evaluate %s ms over %s frames (reported, not gated; baseline %s)'
              % (cost.get('ControlRig_Evaluate_ms'), cost.get('frames'), (base or {}).get('cost_ms')))
        print('KHAIDYN_GATE %s (%d checks, %d failed)' % ('PASS' if ok else 'FAIL', len(rows),
                                                           sum(1 for r in rows if not r[2])))
        if why:
            # A new baseline only from a structurally sound run (dyn_gate.baseline_blockers); the values it
            # changes are the ones printed above as FAIL against the old one.
            dyn_gate.write_baseline(hero, result, rec, why)
            print('KHAIDYN_GATE baseline written: %s' % dyn_gate.baseline_path(hero))
            ok = dyn_gate.passed(dyn_gate.check(result, json.load(open(dyn_gate.baseline_path(hero))),
                                                          rec))
    else:
        print(json.dumps(result, indent=1))
    sys.exit(0 if ok else 1)
