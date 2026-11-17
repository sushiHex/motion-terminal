# Turn-in-place effect-level probe (the turn half of the rifle aim/turn take). Rendering-independent, confound-free.
# Proves the nested TurnInPlace sub-state-machine ROTATES THE PAWN IN PLACE via the root-yaw degrade
# (AnimGraphNode_RotateRootBone Yaw=+/-60 in TIP_TurnLeft/TIP_TurnRight, over the idle base pose).
#
# WHY A SETTLE->TURN DESIGN: MM_Idle carries a LARGE natural pelvis sway (+/-~22 deg world yaw,
# ~5 s cycle), so a delta-from-first-sample measurement (a) captures its baseline AFTER the turn already
# engaged and (b) is swamped by the sway — ON and OFF read identically. Instead we measure the DIFFERENCE
# between a NEUTRAL phase (inject 0) and a TURN phase (inject DELTA):
#   - PRIMARY signal = the ROOT bone component-space yaw. root has NO idle animation, so RotateRootBone is
#     the ONLY thing that moves it: neutral ~0 deg, turn ~+/-60 deg. A clean, sway-free ~60 deg sweep.
#   - CORROBORATION = the pelvis WORLD yaw CIRCULAR MEAN per phase (sin/cos accumulation cancels the sway):
#     turn_mean - settle_mean ~= +/-60 deg (the whole body follows root), while actorXYdrift stays ~0 cm
#     (rotates IN PLACE — RotateRootBone moves the pose root, not the capsule).
#   - Both channels use a CIRCULAR (sin/cos -> atan2) per-phase mean so neither wraps incorrectly near
#     +/-180 deg (root uses the same circular mean as the pelvis channel; the shipped range is small but
#     the two channels stay consistent).
#   RIGHT (DELTA=+90 > the +45 threshold): TurnInPlace leaves TIP_Neutral for TIP_TurnRight;
#       rootSweep ~ +60, pelvisWorldSweep ~ +60, drift ~ 0.
#   LEFT  (DELTA=-90 < the -45 threshold): TurnInPlace leaves TIP_Neutral for TIP_TurnLeft;
#       rootSweep ~ -60, pelvisWorldSweep ~ -60, drift ~ 0.
#   OFF   (DELTA=0, PROVEN off via the get/set read-back each tick): stays TIP_Neutral; rootSweep ~ 0.
# The turn line also logs tip=<active TurnInPlace sub-state> (GetAnimDebugState), so ONE run proves BOTH
# the signed root sweep AND which turn state the machine entered — the direct OFF/ON + direction isolation.
#
# INJECTION PATH: TurnYawDelta is instance_editable:false, so a Python set_editor_property raises
# "cannot be edited on instances". We inject through UAnimDebugTools::SetAnimVariable (raw FProperty
# SetPropertyValue_InContainer — bypasses the edit-on-instance guard) and read back via GetAnimVariable —
# the proven reflection contract. TurnYawDelta is a PURE external var (no EventGraph re-derive), so
# the write STICKS and the read-back proves the state (the confound-immunity property). The
# instance is resolved by the pawn object name (FindAnimInstanceByLabel matches GetActorLabel() OR GetName()).
#
# Runs inside a PIE session as a probe (duration >= 9 s). Caller PREPENDS the delta:
#   DELTA = 90.0   # or -90.0 for the LEFT turn
#   exec(open(r'.../Scripts/pie_turninplace_drive.py').read())
# then samples LogPython 'TIP ' lines; the LAST turn-phase line carries the final sweep numbers.
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
import math

_G = {'handle': None, 'label': None, 'last': -999.0,
      'sx': None, 'sy': None, 'maxdrift': 0.0,
      'set_ss': 0.0, 'set_cs': 0.0, 'set_n': 0, 'set_rss': 0.0, 'set_rcs': 0.0, 'set_rn': 0,
      'trn_ss': 0.0, 'trn_cs': 0.0, 'trn_n': 0, 'trn_rss': 0.0, 'trn_rcs': 0.0, 'trn_rn': 0}
INTERVAL = 0.2
SETTLE_START = 1.6  # game seconds: ignore samples before this (lets any spawn/leftover transient clear)
SETTLE_END = 3.6    # game seconds: inject 0 (neutral baseline) before this, DELTA after

try:
    _DELTA = DELTA  # noqa: F821  (prepended by the caller)
except NameError:
    _DELTA = 90.0


def _circ_mean(sin_acc, cos_acc, n):
    return math.degrees(math.atan2(sin_acc, cos_acc)) if n else 0.0


def _yaw_delta(a, b):  # signed normalized (a - b), no +/-180 wrap
    return unreal.MathLibrary.normalized_delta_rotator(
        unreal.Rotator(0, 0, a), unreal.Rotator(0, 0, b)).yaw


def _tip_state():
    # Active TurnInPlace sub-state (the heaviest, via mt_drive.dominant_state) from GetAnimDebugState.
    # Diagnostic only; any failure returns 'NA' and never breaks the tick.
    try:
        return _mt.dominant_state(str(_mt.anim_debug_tools().call_method(
            'GetAnimDebugState', args=(_G['label'],))), 'TurnInPlace') or 'NA'
    except Exception:
        return 'NA'


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
        turning = now >= SETTLE_END
        inj = _DELTA if turning else 0.0
        # Write and PROVE it stuck, in one call. TurnYawDelta is a PURE external var - the header's
        # whole confound-immunity argument rests on nothing re-deriving it - so a read-back is
        # exactly the right check, and verify=True is where that check now lives for every driver.
        # Compared against `inj`, the value driven THIS tick, not the constant _DELTA, because the
        # settle phase legitimately drives 0.
        if _mt.set_anim_vars_checked(_G['label'], (('TurnYawDelta', float(inj)),), verify=True, tol=0.5) \
                and not _G.get('delta_unproven'):
            _G['delta_unproven'] = True
            unreal.log_error('TIP TurnYawDelta DID NOT STICK at %.1f - something is re-deriving it, so '
                             'the confound-immunity property this probe rests on does not hold for this '
                             'run.' % float(inj))

        if (now - _G['last']) < INTERVAL:
            return
        _G['last'] = now
        if now < SETTLE_START:
            return   # discard spawn/leftover-callback transients before baselining

        pelvis_world_yaw = mesh.get_socket_rotation('pelvis').yaw
        root_cs = mesh.get_socket_transform('root', unreal.RelativeTransformSpace.RTS_COMPONENT)
        root_cs_yaw = root_cs.rotation.rotator().yaw
        loc = pawn.get_actor_location()
        if _G['sx'] is None:
            _G['sx'] = loc.x
            _G['sy'] = loc.y
        drift = ((loc.x - _G['sx']) ** 2 + (loc.y - _G['sy']) ** 2) ** 0.5
        if drift > _G['maxdrift']:
            _G['maxdrift'] = drift
        # Read for the setread= log field only; the assertion is in the verified write above.
        rd = _mt.parse_num(_mt.anim_debug_tools().call_method(
            'GetAnimVariable', args=(_G['label'], 'TurnYawDelta')))
        pr = math.radians(pelvis_world_yaw)
        rr = math.radians(root_cs_yaw)

        if not turning:
            _G['set_ss'] += math.sin(pr); _G['set_cs'] += math.cos(pr); _G['set_n'] += 1
            _G['set_rss'] += math.sin(rr); _G['set_rcs'] += math.cos(rr); _G['set_rn'] += 1
            unreal.log('TIP delta=%.0f setread=%s phase=settle pelvisWorldYaw=%.2f rootCSyaw=%.2f drift=%.2f n=%d'
                       % (_DELTA, ('%.0f' % rd) if rd is not None else 'NA',
                          pelvis_world_yaw, root_cs_yaw, drift, _G['set_n']))
            return

        _G['trn_ss'] += math.sin(pr); _G['trn_cs'] += math.cos(pr); _G['trn_n'] += 1
        _G['trn_rss'] += math.sin(rr); _G['trn_rcs'] += math.cos(rr); _G['trn_rn'] += 1
        set_pmean = _circ_mean(_G['set_ss'], _G['set_cs'], _G['set_n'])
        trn_pmean = _circ_mean(_G['trn_ss'], _G['trn_cs'], _G['trn_n'])
        pelvis_sweep = _yaw_delta(trn_pmean, set_pmean)
        set_rmean = _circ_mean(_G['set_rss'], _G['set_rcs'], _G['set_rn'])
        trn_rmean = _circ_mean(_G['trn_rss'], _G['trn_rcs'], _G['trn_rn'])
        root_sweep = _yaw_delta(trn_rmean, set_rmean)
        unreal.log('TIP delta=%.0f setread=%s phase=turn rootCSsweep=%.2f pelvisWorldSweep=%.2f maxDrift=%.2f tip=%s trnN=%d'
                   % (_DELTA, ('%.0f' % rd) if rd is not None else 'NA',
                      root_sweep, pelvis_sweep, _G['maxdrift'], _tip_state(), _G['trn_n']))
    except Exception as e:
        unreal.log_warning('TIP err %s' % e)


_G['handle'] = unreal.register_slate_pre_tick_callback(_tick)
unreal.log('TIP registered delta=%s (settle<%.1fs, then turn)' % (_DELTA, SETTLE_END))
