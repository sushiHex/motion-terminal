"""Hero intake, the run: from a normal shell with the editor up, profile each content root into HeroProfiles/<rig>.json.

    python Scripts/hero_intake.py /Game/<PackA> [/Game/<PackB> ...]

Per root, over one session on the editor's MCP server (unreal_mcp): hero_intake_gather.py collects facts in the editor
(run_file; read-only); every AnimBlueprint on the primary skeleton is read with this project's own
MotionTerminal.AnimGraphTools.ReadAnimGraph for the stock audit; hero_intake_core.profile decides. Overrides already in a
profile (counted, hand-made decisions) are carried into the regenerated one. Profiles carry no timestamps, so a re-run with nothing changed
rewrites identical bytes; the script reports whether each file changed.
The rig id is the root's last path segment: no rig name lives in this code (test_hero_intake_core.py greps).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collider_autofit  # noqa: E402
import hero_intake_core as core  # noqa: E402
import unreal_mcp  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__))).replace('\\', '/')
PROFILES = os.path.join(REPO, 'HeroProfiles')
FACTS = os.path.join(REPO, 'Saved', 'MotionTerminalTests', 'intake')
GATHER = REPO + '/Scripts/hero_intake_gather.py'
FIT = REPO + '/Scripts/hero_intake_fit.py'
TIMEOUT_S = 1800                # a large pack's gather is one synchronous editor call


def _dump(doc):
    return json.dumps(doc, indent=1, sort_keys=True) + '\n'


def intake(root, client):
    rig = root.rstrip('/').rsplit('/', 1)[-1]
    facts_path = os.path.join(FACTS, rig + '_facts.json').replace('\\', '/')
    os.makedirs(FACTS, exist_ok=True)
    client.run_file(GATHER, HIG_ROOT=root, HIG_OUT=facts_path)
    with open(facts_path) as f:
        facts = json.load(f)
    mesh, _ = core.primary(facts)
    graphs = {}
    for a in facts['anim_blueprints']:
        if mesh and a['target_skeleton'] == mesh['skeleton']:
            text = client.call('MotionTerminal.AnimGraphTools', 'ReadAnimGraph', AnimBlueprintPath=a['path']).text
            graphs[a['path']] = json.loads(text).get('returnValue', '') if text.startswith('{') else text
    out_path = os.path.join(PROFILES, rig + '.json')
    old = open(out_path).read() if os.path.exists(out_path) else None
    overrides = json.loads(old).get('overrides', {}) if old else {}
    doc = core.profile(rig, facts, graphs, overrides)
    if doc['status'] == 'ok':   # collider fit (the dynamics build's input): measured on the profile's own mesh, roles and chains
        mesh = next(m for m in facts['skeletal_meshes'] if m['path'] == doc['primary']['mesh'])
        fit_path = os.path.join(FACTS, rig + '_fit.json').replace('\\', '/')
        client.run_file(FIT, HIF_MESH=mesh['path'], HIF_SPECS=collider_autofit.fit_specs(doc, mesh), HIF_OUT=fit_path)
        with open(fit_path) as f:
            collider_autofit.build(doc, json.load(f))
    new = _dump(doc)
    if new != old:   # write beside, then replace: an interrupted run leaves the previous profile intact
        with open(out_path + '.tmp', 'w', newline='\n') as f:
            f.write(new)
        os.replace(out_path + '.tmp', out_path)
    return doc, ('unchanged' if new == old else 'new' if old is None else 'changed')


def main(roots):
    os.makedirs(PROFILES, exist_ok=True)
    with unreal_mcp.Client(timeout=TIMEOUT_S) as client:
        rows = [_row(*intake(root, client)) for root in roots]
    print('HERO_INTAKE_SUMMARY %d rigs: %d ok, %d rejected, %d overrides'
          % (len(rows), sum(r['status'] == 'ok' for r in rows), sum(r['status'] == 'rejected' for r in rows),
             sum(r['overrides'] for r in rows)))
    return 0


def _row(doc, change):
    """One rig's report line, printed as soon as that rig is done."""
    row = {'rig': doc['rig'], 'status': doc['status'], 'file': change,
           'height_cm': doc.get('scale', {}).get('height_cm'),
           'unresolved': doc.get('roles_unresolved', []),
           'chains': [c['root'] + ':' + c['preset'] + ('(baked)' if c['baked'] else '') for c in doc.get('chains', [])],
           'unclassified': [c['root'] for c in doc.get('chains_unclassified', [])],
           'physics_asset': bool(doc.get('stock', {}).get('physics_asset')),
           'features': sorted(k for k, v in doc.get('stock', {}).get('features', {}).items() if v),
           'overrides': doc['override_count'], 'reasons': doc['reasons']}
    print('HERO_INTAKE ' + json.dumps(row))
    return row


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
