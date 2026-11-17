"""Sweep dynamics settings: build the rig per candidate and run the ON/OFF drive, from a normal shell (editor up).

    python Scripts/dyn_sweep.py --hero <Rig> <candidates.json> [tag ...]

candidates.json: {"<tag>": {"behaviours": {...}, "air": {...}}, ...}. Each candidate's overrides are merged into the
behaviour library / air inputs for that build only (KDYN_BEHAVIOURS_JSON / KDYN_AIR_JSON), so a sweep never edits
Scripts/dynamics_behaviours.json. Records land in Saved/MotionTerminalTests/dyn_<Rig>_<tag>.json; compare them with
dyn_compare.py. The shipped rig is rebuilt at the end, so the editor is left on the product (an override build is never
gateable: its manifest records the override and the gate's identity row refuses it).
Workflow: docs/dynamics-tuning-workflow.md.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dyn_behaviours  # noqa: E402
import dyn_hero  # noqa: E402

ENV ={'behaviours': 'KDYN_BEHAVIOURS_JSON', 'air': 'KDYN_AIR_JSON'}


def _run(args, env):
    """(return code, the last error-looking lines for the report). Success is the return code alone: both builders exit
    non-zero on any failure; a Python traceback with exit 0 is treated as a failure too."""
    out = subprocess.run([sys.executable] + args, cwd=os.path.dirname(HERE), env=env, capture_output=True, text=True)
    lines = (out.stdout + out.stderr).splitlines()
    bad = [l for l in lines if 'Traceback' in l or 'Error' in l or 'no record' in l]
    code = out.returncode or (1 if any(l.startswith('Traceback') for l in lines) else 0)
    return code, bad


def sweep(hero, cands, only=None):
    """{tag: 'ok' | 'FAILED: ...'} plus '_restore', the shipped-rig rebuild that always runs last (in a finally, so an
    interrupted or failing sweep never leaves a candidate's rig in the editor unreported). Every candidate is checked
    before anything is built. Each candidate starts from the SHIPPED settings: overrides in the caller's environment
    are not inherited."""
    todo = [(t, c) for t, c in cands.items() if not only or t in only]
    for tag, cand in todo:
        unknown = sorted(set(cand) - set(ENV))
        if unknown:
            raise ValueError('candidate %s has unknown keys %s (known: %s)' % (tag, unknown, sorted(ENV)))
        try:
            dyn_behaviours.load(cand.get('behaviours'))          # field names, types, Radius refused
        except ValueError as exc:
            raise ValueError('candidate %s: %s' % (tag, exc))
    clean = {k: v for k, v in os.environ.items() if k not in ENV.values()}
    results = {}
    try:
        for tag, cand in todo:
            env = dict(clean, **{ENV[k]: json.dumps(v) for k, v in cand.items()})
            print('=== %s %s' % (tag, json.dumps(cand)), flush=True)
            # The hero is passed explicitly to every builder and drive, the restore included (review).
            code, bad = _run([os.path.join(HERE, 'dynamics_graph.py'), '--hero', hero], env)
            if code == 0:
                code, bad = _run([os.path.join(HERE, 'dyn_run.py'), '--hero', hero, tag, '1', '0'], env)
            results[tag] = 'ok' if code == 0 else 'FAILED: ' + ' | '.join(bad[-3:])
            print('    %s' % results[tag], flush=True)
    finally:
        try:
            code, bad = _run([os.path.join(HERE, 'dynamics_graph.py'), '--hero', hero], clean)
            results['_restore'] = 'ok' if code == 0 else 'FAILED: ' + ' | '.join(bad[-3:])
        except Exception as exc:   # noqa: BLE001 - the restore's own failure must reach the report, whatever it is
            results['_restore'] = 'FAILED: %r' % exc
        print('=== shipped rig rebuilt: %s' % results['_restore'], flush=True)
        print('SWEEP ' + json.dumps(results), flush=True)
    return results


# One sweep at a time: two sweeps share one editor and one rig asset, and a second one started during the first's
# restore deleted the AnimBP under it (2026-10-28). A killed sweep leaves the lock behind: delete it by hand once no
# sweep is running (its content names the process).
LOCK = os.path.join(dyn_hero.RECORDS, 'dyn_sweep.lock')


def _take_lock():
    os.makedirs(os.path.dirname(LOCK), exist_ok=True)
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        with open(LOCK) as f:
            raise SystemExit('another sweep holds %s (%s); wait for it, or delete the file if it is stale'
                             % (LOCK, f.read().strip()))
    with os.fdopen(fd, 'w') as f:
        f.write('pid %d' % os.getpid())


if __name__ == '__main__':
    hero, args = dyn_hero.hero_arg(sys.argv[1:])
    dyn_hero.resolve(hero)                      # a hero with no profile fails before anything is built
    with open(args[0]) as f:
        cands = json.load(f)
    _take_lock()
    try:
        res = sweep(hero, cands, set(args[1:]) or None)
    finally:
        os.remove(LOCK)
    sys.exit(0 if all(v == 'ok' for v in res.values()) else 1)
