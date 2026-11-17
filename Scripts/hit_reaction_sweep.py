"""Sweep the hit reaction's tuning across heroes: per candidate and hero, rebuild the dynamics ABP with the override
and run the hit_reaction_run matrix. A candidate is a setting for the generator, so it ships only when it passes on
EVERY hero swept (no per-hero tuning). From a normal shell, editor up.

    python Scripts/hit_reaction_sweep.py --hero <Rig> [--hero <Rig> ...] <candidates.json> [--strength=<cm/s>]

candidates.json: {"<tag>": {<KDYN_REACTION_JSON keys: dynamics_graph._add_reaction>}, ...}, e.g. {"c24":
{"arm_joints": {"clavicle": 24, "upperarm": 2, "lowerarm": 0.5, "hand": 0.5}}}. Each hero's shipped ABP is rebuilt at
the end. Prints one row per candidate and hero, then the candidates that pass on all.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dyn_hero  # noqa: E402
import hit_reaction_run  # noqa: E402


def build(rig, override=None):
    """Rebuild the hero's dynamics ABP: the shipped one, or with a reaction override. Every experiment override in the
    environment (KDYN_*_JSON: reaction, behaviours, air) is cleared first, so 'shipped' means shipped."""
    env = {k: v for k, v in os.environ.items() if not (k.startswith('KDYN_') and k.endswith('_JSON'))}
    if override:
        env['KDYN_REACTION_JSON'] = json.dumps(override)
    out = subprocess.run([sys.executable, os.path.join(HERE, 'dynamics_graph.py'), '--hero', rig], env=env,
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError('build failed: %s' % (out.stdout + out.stderr)[-1500:])


def row(out):
    """The figures a sweep compares, from one hit_reaction_run verdict (one whose records failed validation carries no
    figures: a failed row, not an aborted sweep)."""
    return {'hit': out.get('hit') or {}, 'fidelity': out.get('fidelity') or {},
            'articulation': out.get('articulation') or {}, 'pass': out['verdict']['pass'],
            'failed': [x['check'] for x in out['verdict']['rows'] if not x['ok']] + out.get('problems', [])[:3]}


def print_row(tag, rig, over, r):
    """One line per candidate and hero: what the verdict's checks read (each impact's jump, each side's joints). A value
    the record could not measure prints as None (round(None) once aborted the sweep)."""
    rnd = lambda v, n=0: None if v is None else round(v, n)     # noqa: E731
    sides = {s: [rnd(v.get(j)) for j in ('clavicle', 'shoulder', 'elbow', 'wrist')]
             for s, v in (r['articulation'].get('sides') or {}).items()}
    knees = {c: rnd(k.get('bend_deg'), 1) for c, k in sorted((r['articulation'].get('knees') or {}).items())}
    print('SWEEP %-6s %-16s %s\n      peak %s cm  return %s  unhit %s cm  impacts %s cm  clav/shoulder/elbow/wrist %s'
          '  hips %s deg %s cm  head whip %s over hips %s  knees %s  %s' % (
              tag, rig, json.dumps(over), r['hit'].get('peak_cm'), r['hit'].get('return_s'), r['fidelity'].get('max_cm'),
              [rnd(i.get('jump_cm'), 1) for i in r['hit'].get('impacts', [])], sides, r['articulation'].get('hips_deg'),
              r['articulation'].get('hips_cm'), r['articulation'].get('head_whip_deg'),
              r['articulation'].get('head_over_hips_deg'), knees,
                             'PASS' if r['pass'] else 'fail %s' % r['failed']), flush=True)


def main(rigs, cands, strength):
    rows = {}
    for rig in rigs:
        try:
            for tag, over in cands.items():
                # A stalled or failed drive is that candidate's failed row, printed at once: a sweep never loses the
                # rows before it.
                try:
                    over = dict(over)
                    # the volley's own keys (impacts, hit_strength: cm/s) go to the drive, the rest to the ABP build
                    # ('strength' there is the joints' spring, Hz)
                    impacts = [tuple(i) for i in over.pop('impacts', hit_reaction_run.IMPACTS)]
                    hit = over.pop('hit_strength', strength)
                    build(rig, over)
                    rows[tag, rig] = row(hit_reaction_run.run(rig, tag, hit, impacts=impacts))
                except Exception as e:      # noqa: BLE001
                    rows[tag, rig] = row({'verdict': {'pass': False, 'rows': []}, 'problems': [repr(e)[:300]]})
                print_row(tag, rig, cands[tag], rows[tag, rig])
        finally:
            build(rig)
    winners = [t for t in cands if all(rows[t, rig]['pass'] for rig in rigs)]
    print('SWEEP_PASS_ALL %s' % json.dumps(winners))
    return rows, winners


if __name__ == '__main__':
    rigs, args = dyn_hero.heroes_arg(sys.argv[1:])
    opts = {a.split('=')[0]: a.split('=', 1)[1] for a in args if a.startswith('--') and '=' in a}
    path = next(a for a in args if not a.startswith('--'))
    sys.exit(0 if main(rigs, json.load(open(path)), float(opts.get('--strength', hit_reaction_run.STRENGTH)))[1]
             else 1)
