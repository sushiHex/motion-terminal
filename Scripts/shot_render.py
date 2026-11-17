"""Render one shot of the sequence under MRQ-over-PIE from a normal shell (editor up), into a NEW directory, then gate it.

    python Scripts/shot_render.py <shot> <out_dir under captures/> [--log PATTERN ...]

Without this file a re-shoot is a hand-typed render_shot call inside the editor, with the arguments reconstructed
by hand. This file holds each shot's level, sequence, range and driver in one table, read off shots_manifest.json
and shot_seq_author.py, so a re-shoot is a single command. What it does NOT do: build stages (stage_author.py), the roster pawns (metahuman_bind.py
declare + place), or the crowd (army_spawn.py spawn) — PREREQ names them, and the caller runs them first.

Paths are absolute: render_shot resolves out_dir and driver_path against the editor's working directory.
render_shot refuses to clear a directory the manifest ships from; this file additionally refuses any directory
that already holds frames, so a re-shoot never overwrites an earlier take of any kind. After the render it runs
capture_check with the shot's frame count, and prints every editor log line matching the shot's evidence
patterns (and any --log PATTERN), which is where caption numbers come from.
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import editor_ops  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__))).replace('\\', '/')
BEATS, ROSTER, ARMY = '/Game/MT/BeatsDE/Lvl_MT_BeatsDE', '/Game/MT/Showcase/Lvl_MT_Roster', '/Game/MT/Army/Lvl_MT_Army'

SHOTS = {
    # shot: (level, sequence, start, end_exclusive, driver, job_name, log patterns, prereq)
    '02': (BEATS, '/Game/MT/BeatsDE/Seq_Lane', 0, 660, 'pie_lane_drive.py', 'Lane',
           ['LANE'], None),
    '02b': (BEATS, '/Game/MT/BeatsDE/Seq_Lane', 0, 240, 'pie_bridge_drive.py', 'Bridge',
            ['BRIDGE'], None),
    '03': (BEATS, '/Game/MT/BeatsDE/Seq_Lane', 0, 540, 'pie_montage_layer_drive.py', 'Montage',
           ['MBEAT'], None),
    # 05 and 09 are two ranges of one render (manifest: 05 [540, 900], 09 [300, 540]).
    '05': (ROSTER, '/Game/MT/Showcase/Seq_Roster4Up', 0, 900, 'pie_roster4_drive.py', 'Roster4Up',
           ['ROSTER4'], 'metahuman_bind.py declare, then place(inplace=True) with Lvl_MT_Roster loaded'),
    '06': (ARMY, '/Game/MT/Army/Seq_Army', 0, 480, 'pie_army_pullback_drive.py', 'ArmyPullback',
           ['ARMYPULL'], 'army_spawn.py spawn (ARMY_COUNTS=None) with Lvl_MT_Army loaded; despawn after'),
    '07': (BEATS, '/Game/MT/BeatsDE/Seq_BeatsDE', 0, 780, 'pie_rifle_take_drive.py', 'Rifle',
           ['RIFLETAKE'], None),
}
# Log evidence a take must contain, or it fails even when capture_check passes. A crowd-take re-shoot once
# passed capture_check with an EMPTY stage: its per-second line read army=0 because this file reloaded the
# level and dropped the unsaved crowd. An earlier 06 take also passed every green signal with two thirds of
# the crowd out of frame, so frames alone are never the evidence for this shot.
REQUIRE = {
    '06': 'army=46',
}
TIMEOUT_S = 3600
POLL_TIMEOUT_S = 60       # one render-state poll


def shoot(shot, out_dir, extra_patterns=()):
    ops = editor_ops.shared()
    level, seq, start, end, driver, job, patterns, prereq = SHOTS[shot]
    out_dir = os.path.abspath(out_dir).replace('\\', '/')
    if not out_dir.lower().startswith((REPO + '/captures/').lower()):
        raise SystemExit('out_dir must be under %s/captures' % REPO)
    if os.path.isdir(out_dir) and any(f.endswith('.png') for f in os.listdir(out_dir)):
        raise SystemExit('REFUSING: %s already holds frames; a re-shoot goes to a new directory' % out_dir)
    if prereq:
        print('PREREQ (caller must have run it): ' + prereq)
    # Load the level only if another one is open: a reload discards unsaved actors, which is exactly what the
    # prerequisites of 06 put there (army_spawn writes the editor world and does not save it). Unsaved changes in
    # another open level stop the shot rather than being lost.
    ops.load_level(level, unsaved='fail', reload=False)
    began = time.time()
    # The evidence below is THIS render's only: lines logged after this marker. The crowd take's saved log once held
    # two earlier, failed renders' army=0 lines.
    log = ops.log()
    ops.py("import importlib, sys\nsys.path.insert(0, r'%s/Scripts')\n"
           "importlib.reload(importlib.import_module('mrq_render')).render_shot(%r, %r, r'%s', %d, %d, "
           "driver_path=r'%s/Scripts/%s', job_name=%r)" % (REPO, level, seq, out_dir, start, end, REPO, driver, job),
           timeout=600)
    done = None
    while time.time() - began < TIMEOUT_S:
        time.sleep(10.0)
        try:
            state = ops.py("[getattr(unreal, '_MT_RENDER_DONE', None), getattr(unreal, '_MT_RENDER_SUCCESS', None), "
                           "getattr(unreal, '_MT_RENDER_ERROR', '')]", timeout=POLL_TIMEOUT_S)
        except editor_ops.TRANSIENT as e:      # an editor too busy to answer this poll: the next one tries again
            print(json.dumps({'render_poll_timeout': str(e)[:200]}))
            continue
        if state[0] is True:
            done = 'RENDER %s %s %s' % tuple(state)
            break
    ok = bool(done) and state[1] is True
    frames = sorted(f for f in os.listdir(out_dir) if f.endswith('.png')) if os.path.isdir(out_dir) else []
    result = {'shot': shot, 'render': done, 'ok': ok, 'frames': len(frames), 'want': end - start,
              'minutes': round((time.time() - began) / 60.0, 1), 'out_dir': out_dir}
    if ok:
        cc = subprocess.run([sys.executable, REPO + '/Scripts/capture_check.py', out_dir, '--min-frames',
                             str(end - start), '--min-motion', '0.5'], capture_output=True, text=True)
        result['capture_check'] = {'exit': cc.returncode, 'out': (cc.stdout + cc.stderr).strip()[-600:]}
    log.new_lines()
    logs = {pat: [log.message(line) for line in log.lines if pat in line][-60:]
            for pat in list(patterns) + list(extra_patterns)}
    if shot in REQUIRE:
        found = any(REQUIRE[shot] in line for lines in logs.values() for line in lines)
        result['required_log'] = {'want': REQUIRE[shot], 'found': found}
        result['ok'] = result['ok'] and found
    with open(out_dir + '/_shoot_log.json', 'w') as fh:
        json.dump({'result': result, 'logs': logs}, fh, indent=1)
    print(json.dumps(result, indent=1))
    return result


if __name__ == '__main__':
    pats = [sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == '--log' and i + 1 < len(sys.argv)]
    r = shoot(sys.argv[1], sys.argv[2], pats)
    sys.exit(0 if r['ok'] and r.get('capture_check', {}).get('exit') == 0 else 1)
