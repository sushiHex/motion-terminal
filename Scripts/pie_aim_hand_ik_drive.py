# Photography driver — the deterministic aim/turn + off-hand reach "director" for the rifle take
# (pie_rifle_take_drive execs it).
#
# WHY A PYTHON SLATE PRE-TICK CALLBACK, NOT A DIRECTOR BLUEPRINT:
#   A director Blueprint in the style of a "BP_MT_LaneDirector" would be the obvious model. That asset
#   does NOT exist (does_asset_exist('/Game/MT/BP_MT_LaneDirector') == False); the lane take drove its
#   live pawns + camera with a Python register_slate_pre_tick_callback fired INSIDE the MRQ PIE session,
#   with no director Blueprint. Decisively, the injection path
#   UAnimDebugTools::SetAnimVariable is UFUNCTION(meta=(AICallable)) — NOT BlueprintCallable — so a
#   director-BP graph physically cannot call it. This driver IS the director: the proven slate
#   pre-tick harness, calling SetAnimVariable via /Script reflection (the exact authorized path, the
#   same one pie_aimsweep/turninplace/handreach use).
#
# WHAT IT PROVES (honesty about the subject): the subjects are the LIVE placed Template/child pawns (MT_Manny =
#   BP_MT_TestPawn_C, MT_Khaimera = BP_MT_TestPawn_Khaimera_C) in a simulating (PIE) world. Their pose
#   comes from the live AnimBP driven by these contract-var writes — NOT from any MovieSceneSkeletalAnimation
#   track. The pawns are UNPOSSESSED and stationary (Speed velocity-derived == 0 -> Idle state), so the
#   nested TurnInPlace SM (inside Idle), the aim offset, and the hand IK all apply on the idle base pose.
#
# VAR RANGES (measured when the aim / turn-in-place / hand-IK nodes were authored):
#   AimPitch : NORMALIZED -1..1 (AO_Rifle Y axis). +0.9 = look up, -0.9 = look down. (AimYaw's X axis is
#              UNPOPULATED in the stock asset -> pinned 0; driving it shows nothing, by design.)
#   TurnYawDelta : DEGREES. >+45 -> TIP_TurnRight (RotateRootBone +60), <-45 -> TIP_TurnLeft (-60),
#              |x|<10 -> TIP_Neutral. +-90 gives a clean ~57-60 deg in-place root yaw.
#   WeaponIKAlpha : 0..1. 0 = off-hand free, 1 = off-hand TwoBoneIK'd onto the weapon's IK_Hand_Gun
#              foregrip (Manny seats ~0.1 cm; Khaimera ~5-6 cm = the honest hand-size delta).
#
# TIMELINE (game seconds; keyed off SystemLibrary.get_game_time_in_seconds so it maps to MRQ frames):
#   0.0-2.0   SETTLE            (all vars 0; camera A close on Manny)
#   2.0-5.0   AIM SWEEP         (AimPitch 0 -> +0.9 -> -0.9 -> 0;      camera A)
#   5.0-9.0   TURN-IN-PLACE     (TurnYawDelta 0 -> +90(hold) -> 0 -> -90(hold) -> 0; camera A)
#   9.0       CUT to camera B (two-up)
#   9.0-13.0  OFF-HAND IK       (WeaponIKAlpha 0 -> 1(hold) on BOTH; slight AimPitch lift; camera B)
#
# Registered before MRQ render; a generation kill-switch (unreal._MT_DRIVE_GEN) retires stale callbacks.
import importlib
import math
import os
import sys

import unreal

# The shared driver scaffolding. Resolved off the .uproject rather than __file__, because both
# mrq_render.render_shot and pie_rifle_take_drive exec this file into a bare namespace where
# __file__ does not exist. Reloaded so an edit to mt_drive.py takes effect on the next render.
_MT_SCRIPTS = os.path.join(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()),
                           'Scripts')
if _MT_SCRIPTS not in sys.path:
    sys.path.insert(0, _MT_SCRIPTS)
_mt = importlib.reload(importlib.import_module('mt_drive'))

TOTAL = 13.0

# retire any previous instance of THIS driver cleanly
_prev = getattr(unreal, '_MT_AIM_HAND_IK', None)
if _prev and _prev.get('handle') is not None:
    try:
        unreal.unregister_slate_pre_tick_callback(_prev['handle'])
    except Exception:
        pass
    _prev['handle'] = None

GEN = int(getattr(unreal, '_MT_DRIVE_GEN', 0)) + 1
unreal._MT_DRIVE_GEN = GEN

_G = {'handle': None, 'last_log': -999.0, 'focal_set': False}


def _lerp(a, b, t):
    t = max(0.0, min(1.0, t))
    return a + (b - a) * t


def aim_pitch(t):
    if t < 2.0:
        return 0.0
    if t < 3.0:
        return _lerp(0.0, 0.9, (t - 2.0) / 1.0)   # look UP
    if t < 4.0:
        return _lerp(0.9, -0.9, (t - 3.0) / 1.0)  # sweep DOWN
    if t < 5.0:
        return _lerp(-0.9, 0.0, (t - 4.0) / 1.0)  # back to center
    if t >= 9.5:
        return 0.35                                # gentle lift so the shouldered weapon reads
    return 0.0


def turn_yaw(t):
    if t < 5.0:
        return 0.0
    if t < 5.5:
        return _lerp(0.0, 90.0, (t - 5.0) / 0.5)   # turn RIGHT
    if t < 6.5:
        return 90.0
    if t < 7.0:
        return _lerp(90.0, 0.0, (t - 6.5) / 0.5)
    if t < 7.5:
        return _lerp(0.0, -90.0, (t - 7.0) / 0.5)  # turn LEFT
    if t < 8.5:
        return -90.0
    if t < 9.0:
        return _lerp(-90.0, 0.0, (t - 8.5) / 0.5)
    return 0.0


def weapon_ik(t):
    if t < 10.0:
        return 0.0
    if t < 11.5:
        return _lerp(0.0, 1.0, (t - 10.0) / 1.5)   # off-hand finds the foregrip
    return 1.0


# ---- camera framings (world space). Manny @ (0,-120), Khaimera @ (0,120), facing +X. ----
CAM_A_LOC = unreal.Vector(235.0, -300.0, 158.0)   # front-3/4 close on Manny (turn + aim)
CAM_A_TGT = unreal.Vector(0.0, -120.0, 128.0)
CAM_B_LOC = unreal.Vector(560.0, 0.0, 172.0)      # two-up, both off-hands to the foregrip
CAM_B_TGT = unreal.Vector(0.0, 0.0, 118.0)


def _find_camera(world):
    for a in unreal.GameplayStatics.get_all_actors_of_class(world, unreal.CineCameraActor):
        return a
    return None


def _drive_camera(world, now):
    cam = _find_camera(world)
    if not cam:
        return
    if not _G['focal_set']:
        comp = cam.get_cine_camera_component()
        if comp:
            comp.set_editor_property('current_focal_length', 28.0)  # wide-ish; fits both framings
        _G['focal_set'] = True
    if now < 9.0:
        loc, tgt = CAM_A_LOC, CAM_A_TGT
    else:
        loc, tgt = CAM_B_LOC, CAM_B_TGT
    rot = unreal.MathLibrary.find_look_at_rotation(loc, tgt)
    cam.set_actor_location_and_rotation(loc, rot, False, True)


def _target_pawns(world):
    out = []
    for a in unreal.GameplayStatics.get_all_actors_of_class(world, unreal.Character):
        mesh = a.get_component_by_class(unreal.SkeletalMeshComponent)
        if not mesh:
            continue
        inst = mesh.get_anim_instance()
        if not inst:
            continue
        if 'ABP_MT' in inst.get_class().get_name():
            out.append(a)
    return out


def _tick(dt):
    try:
        if getattr(unreal, '_MT_DRIVE_GEN', None) != GEN:
            if _G['handle'] is not None:
                unreal.unregister_slate_pre_tick_callback(_G['handle'])
                _G['handle'] = None
            return
        world = unreal.EditorLevelLibrary.get_game_world()
        if not world:
            return
        now = unreal.SystemLibrary.get_game_time_in_seconds(world)
        _drive_camera(world, now)

        ap = aim_pitch(now)
        ty = turn_yaw(now)
        ik = weapon_ik(now)
        pawns = _target_pawns(world)
        results = []
        for p in pawns:
            lbl = p.get_actor_label()
            # Write ALL FIVE contract vars and check EVERY return, not just AimPitch's:
            # SetAnimVariable returns "ok: <var> = <val>" on success, "ERROR: ..." otherwise.
            # Logging one write's return over-claims — the honest log names
            # any that failed to stick, so a silent write regression can't read green.
            #
            # The aim sweep is ABOUT the aim, so it opts INTO the aim beat, which is off by default. Both of
            # its variables move together and mt_drive.rifle_aim_pair is where that invariant and its
            # reasoning live (AIM-KNOWN-BAD retracted here and in
            # docs/rifle-mode-branch-design.md). Without this the aim sweep would drive a node whose
            # output is gated out of the pose entirely.
            writes = (('AimPitch', ap), ('AimYaw', 0.0), ('TurnYawDelta', ty),
                      ('WeaponIKAlpha', ik)) + _mt.rifle_aim_pair(True)
            # Through the shared helper: the "ok:" success sentinel is the plugin's contract, and it
            # was open-coded in five files. One owner means a change to that contract - or the
            # addition of a read-back check - lands everywhere at once instead of in four of five.
            bad = _mt.set_anim_vars_checked(lbl, writes)
            # Count derived from `writes`, not spelled out: the literal read 'ok5' while six vars were
            # being written, which is the same class of drift as a check that names its own subject.
            results.append('%s:%s' % (lbl, 'ok%d' % len(writes) if not bad
                                      else 'FAIL[%s]' % ','.join(bad)))

        if (now - _G['last_log']) >= 1.0:
            _G['last_log'] = now
            unreal.log('AIMIK t=%.2f ap=%.2f ty=%.1f ik=%.2f npawns=%d %s'
                       % (now, ap, ty, ik, len(pawns), ' | '.join(results)))
    except Exception as e:
        unreal.log_warning('AIMIK err %s' % e)


_G['handle'] = unreal.register_slate_pre_tick_callback(_tick)
unreal._MT_AIM_HAND_IK = _G
unreal.log('AIMIK driver registered gen=%d (total=%.1fs)' % (GEN, TOTAL))
