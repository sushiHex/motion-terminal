# The N-vs-eval-ms sweep probe (the "found the budget cliff, shipped under it" chart).
#
# Runs inside the editor over `run_python`; the caller PREPENDS an action + target N and exec()s this file:
#     PROBE_ACTION = 'spawn'      # 'spawn' | 'despawn'
#     PROBE_N      = 46           # total instances; the tiers are scaled proportionally to the 6:8:32 shape
#     exec(open(r'.../Scripts/archive/army_budget_probe.py').read())
# It prints one `PROBE <json>` line to LogPython so the caller can parse the spawned shape.
#
# WHY A SEPARATE SPAWN PATH FROM army_spawn.py. The sweep must be able to spawn OVER budget — that is the
# whole point of finding the cliff — so it deliberately does NOT call assert_budget (army_spawn.py's pre-spawn
# guard, which raises on an over-budget shape). Everything else (per-tier URO / forced LOD / AlwaysTickPose via
# _apply_tier, the ring geometry, the tags) is REUSED from army_spawn.py so the swept crowd is the shipped
# tier config at a different count, not a different crowd.
#
# WHY THE CALLER READS THE STATS, NOT THIS SCRIPT. The anim-eval cost lives in STATGROUP_Anim on the stats
# thread (PerformAnimEvaluation_WorkerThread / AnimGameThreadTime). Python-in-editor cannot read the stats
# thread; only the project's own stats sampling can. So the sweep is orchestrated by the
# caller: for each N -> stop_pie, PROBE spawn N, start_pie, `stat anim`, a stat-group read -> the caller
# writes research/fixtures/army-contact/n_vs_eval_ms.csv from those readings. Frame-time / N is FORBIDDEN
# (it measures the ~324 ms editor idle-throttle floor, not the crowd); the summed worker-thread cycle stat is
# the honest per-frame anim-eval cost, floor-independent.
#
# GOTCHA (army_spawn.py's, inherited): run_python execs into a PERSISTENT namespace. A stale ARMY_ACTION left
# by an earlier call would make the exec(army_spawn.py) below SPAWN the full army before this probe runs, so
# both action names are popped out FIRST. GOTCHA-A' (no World/Level/actor bound to a module global) holds:
# every actor handle here is function-local and dies with the call; nothing rebinds a world.
import json
import os

import unreal

# The else-branch is the exec()-from-run_python case, where __file__ is undefined. Derived from the
# .uproject directory rather than hardcoding one machine's checkout — a hardcoded
# path in a committed script is a repeatability defect. Archived: __file__ climbs to the live Scripts/.
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) if '__file__' in globals() \
    else os.path.join(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()),
                      'Scripts').replace('\\', '/')

# Neutralise any stale army_spawn dispatch BEFORE loading its helpers, then exec it for functions only
# (with ARMY_ACTION unset its bottom dispatch is a no-op — it just defines load_config/_apply_tier/etc.).
globals().pop('ARMY_ACTION', None)
globals().pop('ARMY_COUNTS', None)
exec(open(os.path.join(_HERE, 'army_spawn.py')).read())


def scaled_counts(config, total_n):
    """Scale the shipped 6:8:32 tier shape to a total of total_n, preserving the ratio."""
    base = {t: int(config['rings'][t]['count']) for t in _TIERS}
    base_total = sum(base.values()) or 1
    f = float(total_n) / base_total
    return {t: max(0, int(round(base[t] * f))) for t in _TIERS}


def probe_spawn(total_n):
    """Despawn any existing crowd, then spawn `total_n` instances in the shipped tier config, NO budget guard.

    Delegates to army_spawn.py's spawn_army(skip_budget_check=True) — same ring-placement math, tagging,
    and per-tier URO/LOD/AlwaysTickPose application the shipped army uses, just scaled to total_n and freed
    from assert_budget()'s raise (the sweep's whole point is to spawn OVER budget and find the cliff).
    """
    config = load_config()
    despawn_army(config)
    counts = scaled_counts(config, total_n)
    result = spawn_army(config, counts=counts, skip_budget_check=True)
    made = result['spawned']
    total = sum(made.values())
    unreal.log('PROBE ' + json.dumps({'action': 'spawn', 'target_n': total_n, 'counts': counts,
                                       'made': made, 'total': total}))
    return total


_paction = globals().pop('PROBE_ACTION', None)
_pn = globals().pop('PROBE_N', None)
if _paction == 'spawn':
    probe_spawn(int(_pn))
elif _paction == 'despawn':
    despawn_army(load_config())
else:
    unreal.log_error('PROBE ' + json.dumps({'action': 'ERROR', 'reason': 'unknown PROBE_ACTION',
                                             'got': repr(_paction), 'valid': ['spawn', 'despawn']}))
    raise RuntimeError('PROBE_ACTION is %r — set it to spawn/despawn (both names are popped)' % (_paction,))
