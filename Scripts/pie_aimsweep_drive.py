# Aim-offset effect-level probe (the aim half of the rifle aim/turn take). Rendering-independent, confound-free.
# Proves the RotationOffsetBlendSpace node (AO_Rifle, spliced LayeredBoneBlend -> Aim -> ControlRig)
# CHANGES THE UPPER-BODY POSE as the aim PITCH varies, while the LOWER body is untouched.
#
# WHY DRIVE PITCH, NOT YAW (measured on the AO_Rifle asset before this probe was written): AO_Rifle's axes are
# normalized -1..1 (NOT degrees). Y=PITCH is POPULATED (samples CU +0.989 / CC 0 / CD -0.980);
# X=YAW is UNPOPULATED (zero left/right samples). Driving yaw shows NOTHING in this stock asset
# (a false "aim does nothing"). So the probe drives AimPitch in -1..1: NEUTRAL=0 vs EXTREME=+0.9
# (near the +1 pitch limit). AimPitch is bound DIRECTLY to the node's Y pin (treated as already
# normalized), so it is a PURE external contract var (no EventGraph re-derive) -> the write STICKS
# and the get/set read-back proves the OFF(0)/ON(+0.9) state (the confound-immunity property).
#
# MEASUREMENT (settle -> neutral-baseline -> extreme):
#   PRIMARY signal   = an UPPER-BODY bone's WORLD rotation (spine_03; head as corroboration). At pitch
#                      +0.9 the aim offset rotates the spine/head, so their world-space orientation
#                      differs from the neutral pose by >> 15 deg. Measured as the max angular distance
#                      (quaternion) between the extreme-phase and neutral-phase sampled rotations.
#   CONFOUND GUARD   = a LOWER-BODY bone (foot_r) world rotation delta. AO_Rifle is upper-body only, so
#                      foot_r must stay ~unchanged (< ~5 deg). This proves the upper-body delta is the
#                      aim offset, not whole-body motion / idle drift.
#   MECHANISM A      = GatherDebugData (via AnimDebugTools.GatherNodeWeights) must show a
#                      RotationOffsetBlendSpace / AO_Rifle node PRESENT with weight > 0 (the node runs).
#
# INJECTION PATH: AimPitch is instance_editable:false, so a Python set_editor_property raises
# "cannot be edited on instances". We inject through UAnimDebugTools::SetAnimVariable (raw FProperty
# SetPropertyValue_InContainer) and read back via GetAnimVariable -- the proven reflection
# contract. The instance is resolved by the pawn object name (FindAnimInstanceByLabel).
#
# Runs inside a PIE session as a probe (duration >= 8 s). Caller PREPENDS the extreme pitch:
#   PITCH = 0.9
#   exec(open(r'.../Scripts/pie_aimsweep_drive.py').read())
# then samples LogPython 'AIM ' lines; the LAST extreme-phase line carries the final deltas.
import unreal

# The shared driver scaffolding. Resolved off the .uproject, NOT off __file__: these drivers are
# exec'd into bare namespaces where __file__ does not exist. Reloaded so an edit to mt_drive.py
# takes effect on the next run - see that module's header.
import importlib
import os
import sys

_MT_SCRIPTS = os.path.join(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()),
                           'Scripts')
if _MT_SCRIPTS not in sys.path:
    sys.path.insert(0, _MT_SCRIPTS)
_mt = importlib.reload(importlib.import_module('mt_drive'))
import re

_G = {'handle': None, 'label': None, 'last': -999.0,
      'base': {}, 'ext': {}, 'dumped': False}
INTERVAL = 0.2
SETTLE_START = 1.6   # game seconds: discard spawn/leftover transients before this
SETTLE_END = 3.6     # game seconds: hold AimPitch=0 (neutral baseline) before this, PITCH after

# Bones sampled. Upper-body (aim-driven): spine_03 primary, head corroboration.
# Lower-body confound guard: foot_r (must NOT move with pitch).
UPPER_PRIMARY = 'spine_03'
UPPER_CORROB = 'head'
LOWER_GUARD = 'foot_r'

try:
    _PITCH = PITCH  # noqa: F821  (prepended by the caller)
except NameError:
    _PITCH = 0.9


def _aim_node_weight(dump_full=False):
    # Mechanism A: dump GatherDebugData node weights (via AnimDebugTools.GetNodeWeights, which walks the
    # live GatherDebugData chain) and find the RotationOffsetBlendSpace node's weight. The node's DebugLine
    # is "AnimNode_RotationOffsetBlendSpace<W:..%> Alpha (..%) PlayTime (..) (weight X.XXX)" (GetNodeName =
    # StaticStruct()->GetName(); AppendNodeWeights suffixes "(weight %.3f)" = AbsoluteWeight). Diagnostic;
    # any failure returns -1 and never breaks the tick.
    try:
        dump = str(_mt.anim_debug_tools().call_method('GetNodeWeights', args=(_G['label'],)))
        if dump_full and not _G['dumped']:
            _G['dumped'] = True
            unreal.log('AIM NODEDUMP BEGIN\n%s\nAIM NODEDUMP END' % dump)
        for raw in dump.split('\n'):
            if 'rotationoffsetblendspace' in raw.lower():
                m = re.search(r'\(weight\s+([0-9]*\.?[0-9]+)\)', raw)
                return (raw.strip(), float(m.group(1)) if m else None)
        return ('<no RotationOffsetBlendSpace node line found>', -1.0)
    except Exception as e:
        return ('<GetNodeWeights err %s>' % e, -1.0)


def _sample(mesh, name):
    # World-space bone rotation as a quaternion.
    return _mt.sample_bone_rotations(mesh, [name])[name]


def _accum_min(store, mesh, base):
    # Accumulate per-bone MAX world-rot angle vs the neutral baseline for a phase.
    for key in (UPPER_PRIMARY, UPPER_CORROB, LOWER_GUARD):
        cur = _sample(mesh, key)
        d = _mt.quat_angle_deg(base[key], cur) if key in base else 0.0
        store[key] = max(store.get(key, 0.0), d)


def _tick(dt):
    try:
        w = unreal.EditorLevelLibrary.get_game_world()
        if not w:
            return
        pc = unreal.GameplayStatics.get_player_controller(w, 0)
        pawn = pc.get_controlled_pawn() if pc else None
        if not pawn:
            return
        mesh = pawn.get_component_by_class(unreal.SkeletalMeshComponent)
        if not mesh:
            return
        if _G['label'] is None:
            _G['label'] = pawn.get_name()

        now = unreal.SystemLibrary.get_game_time_in_seconds(w)
        extreme = now >= SETTLE_END
        inj = _PITCH if extreme else 0.0
        # EVERY WRITE IS CHECKED. SetAnimVariable returns "ok: <var> = <val>" on success and
        # "ERROR: ..." otherwise, and this probe used to discard that. It matters more now than it did:
        # the aim beat is off by default, so a DROPPED write does not fail loudly - it silently turns
        # this into a sweep of a switched-OFF node, whose numbers read like "the aim offset does
        # nothing". That is the same invisible-failure class as the defect these variables gate.
        # AimYaw is pinned explicitly (its axis is unpopulated) so nothing else can drive it.
        # The aim-beat pair opts the node in over the base its deltas were extracted against; the
        # invariant and its reasoning live in mt_drive.rifle_aim_pair.
        _bad = _mt.set_anim_vars_checked(
            _G['label'], (('AimPitch', float(inj)), ('AimYaw', 0.0)) + _mt.rifle_aim_pair(True))
        if _bad and not _G.get('write_fail_logged'):
            _G['write_fail_logged'] = True
            unreal.log_error('AIMSWEEP: SetAnimVariable FAILED for %s - this sweep is measuring a node '
                             'that was never configured, so its numbers are void, not evidence'
                             % ', '.join(_bad))

        if (now - _G['last']) < INTERVAL:
            return
        _G['last'] = now
        if now < SETTLE_START:
            return

        rd = _mt.parse_num(_mt.anim_debug_tools().call_method('GetAnimVariable', args=(_G['label'], 'AimPitch')))

        if not extreme:
            # Capture / refresh the neutral baseline rotations each neutral tick (last wins before flip).
            for key in (UPPER_PRIMARY, UPPER_CORROB, LOWER_GUARD):
                _G['base'][key] = _sample(mesh, key)
            unreal.log('AIM pitch=%.2f setread=%s phase=neutral %s=set %s=set %s=set'
                       % (_PITCH, ('%.2f' % rd) if rd is not None else 'NA',
                          UPPER_PRIMARY, UPPER_CORROB, LOWER_GUARD))
            return

        _accum_min(_G['ext'], mesh, _G['base'])
        line, wt = _aim_node_weight(dump_full=True)
        unreal.log('AIM pitch=%.2f setread=%s phase=extreme upper_%s=%.2f upper_%s=%.2f lower_%s=%.2f nodeW=%s node=[%s]'
                   % (_PITCH, ('%.2f' % rd) if rd is not None else 'NA',
                      UPPER_PRIMARY, _G['ext'].get(UPPER_PRIMARY, 0.0),
                      UPPER_CORROB, _G['ext'].get(UPPER_CORROB, 0.0),
                      LOWER_GUARD, _G['ext'].get(LOWER_GUARD, 0.0),
                      ('%.3f' % wt) if wt is not None else 'NA', line))
    except Exception as e:
        unreal.log_warning('AIM err %s' % e)


_G['handle'] = unreal.register_slate_pre_tick_callback(_tick)
unreal.log('AIM registered pitch=%s (neutral<%.1fs, then extreme)' % (_PITCH, SETTLE_END))
