# Driver for the four-rig roster lineup take, JOGGING.
#
# ============================================================================================
# THIS SHOT JOGS, AND THE THING THAT WOULD STOP IT IS FIXED AT THE ASSET LEVEL
#
# A standing lineup was the earlier fallback, and the reason was real: Khaimera's machine-local Khai_* baked
# clips carry their forward travel on the PELVIS, so driving him at a locomotion speed walked him out of
# frame every clip loop. Measured then, as max |pelvis - actor| in XY:
#
#     Speed 450   Khaimera 941.2 cm    Speed 150   Khaimera 252.6 cm    Speed 0   Khaimera 3.7 cm
#
# The fix removes the cause instead of the motion. Scripts/khai_inplace_author.py authors NON-DESTRUCTIVE
# duplicates of the two travelling clips with the pelvis forward translation pinned (X sway, Z bob and
# every rotation on every bone left byte-identical — verified across eight witness bones and every key),
# a duplicate BlendSpace that samples them, and a duplicate child ABP that carries that BlendSpace as its
# one CDO override. Scripts/metahuman_bind.py's `place(inplace=True)` assigns that ABP to the Khaimera
# pawn's mesh component at placement time. The shipped ABP_MT_Khaimera, its BlendSpace and the six
# Khai_* clips are never opened for write, so GoldenDiff.MTKhaimera and the Verify.ClipVaries.Khai_* rows
# are untouched — which an in-place edit of the originals could not manage: that approach had to re-bake
# them to get `Pie.RigLocomotion.Khaimera` green again.
#
# Re-measured on the fixed stage, same probe, same method, one full jog loop being 1.767 s so every run
# spans ~4 loops of scored samples:
#
#     driven Speed   Khaimera   Manny   Quinn   MetaHuman        (max |pelvis - actor| XY, cm)
#     150                4.2      3.9     4.0        4.1
#     450               10.1     10.3    10.6       10.8
#     600 (shipped)     15.1     17.5    18.0       18.2
#
# Khaimera is now the LEAST drifting rig in the line-up at both 450 and 600. The residual at the shipped
# speed is 15.1 cm against 180 cm of inter-rig spacing (8%), so no rig can reach a neighbour. For the frame
# edge the arithmetic is looser and is not what the claim rests on: the camera's 32 mm on a 23.76 mm
# filmback covers ~809 cm at ~1090 cm against a 540 cm line-up, leaving ~134 cm per side between the
# OUTERMOST RIG'S MARK and the edge — and the silhouettes eat into that, Khaimera's most of all. What
# actually establishes "in frame for the whole shipped range" is a direct measurement of the shipped
# frames (edge-strip chroma and luma over 60 frames across [540,900)); see render_evidence.json
# roster_4up._in_frame_criterion. The drift is also mostly along each rig's own forward axis, which at
# this camera is DEPTH rather than screen-lateral.
#
# WHY SPEED 600 AND NOT 450. 600 is the BlendSpace's jog sample outright, so the pose is the jog clip
# rather than a 50/50 walk/jog blend, and it is the same speed `Pie.RigLocomotion` characterises. The
# gait reads correspondingly: hand_r world Z sweeps 43.2 cm on Khaimera at 600 against 21.3 cm at 450.
#
# WHAT THESE FRAMES CARRY: the pose is LIVE and it is LOCOMOTION. The driver reads each rig's dominant
# `Locomotion`-machine state and a sampled bone's world Z out of the graph's own weights every SAMPLE_DT
# and dumps the accumulated timeline near the end of the take, so a rig that froze — or that quietly sat
# in `Idle` — shows up in the shipped render's own log rather than being assumed alive. capture_check's
# motion test remains NOT evidence for this shot and is not offered as such: the camera move alone would
# satisfy it.
#
# GOTCHA-A': no World/Level/Actor/Component is ever bound to a module-level name — every handle is a
# local inside _tick. Retires off unreal._MT_DRIVE_GEN, which mrq_render bumps when the render ends.
# ============================================================================================
import importlib
import os
import sys

import unreal

# The shared driver scaffolding. Resolved off the .uproject, NOT off `__file__`:
# `mrq_render.render_shot` execs this file into a bare namespace where `__file__` does not exist.
# Reloaded rather than plain-imported so an edit to mt_drive.py takes effect on the next render —
# see that module's header.
_MT_SCRIPTS = os.path.join(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()),
                           'Scripts')
if _MT_SCRIPTS not in sys.path:
    sys.path.insert(0, _MT_SCRIPTS)
_mt = importlib.reload(importlib.import_module('mt_drive'))

TOTAL = 30.0             # 900 frames at 30 fps — the full render; the manifest ships a subrange
SAMPLE_DT = 0.5

# ONLY SCORE THE FRAMES THAT SHIP. The manifest ships `frame_range [540, 900]`, i.e.
# t = 18.0 .. 30.0 s, and the reported numbers must describe THOSE frames rather than the whole take.
# This is not tidiness — it is a measured correction. Scored over the full 30 s, the take's Idle ->
# Locomotion entry transient (a one-time snap as the foot-IK Control Rig first solves onto its effectors)
# added ~10-12 cm of world-Z range to every rig whose Control Rig is RUNNING, and on Khaimera that turned
# a genuinely pinned 1.06 cm foot into a reported 10.65 cm — straight across the 10 cm stride threshold.
# The MetaHuman, whose gate now switches that Control Rig off, gained 0.02 cm from the same window, which
# is what identifies the transient as the Control Rig's rather than the clip's. The shipped subrange never
# contained the transient; only the measurement did.
SCORE_FROM_S = 18.0

# Speed is DERIVED (VectorLength(GetVelocity)) every tick by ABP_MT_Locomotion, so it is driven at its
# SOURCE — CharacterMovementComponent.Velocity — with MOVE_None and the component tick disabled. 600 is
# an assertion, not a default: it pins every rig on the BlendSpace's jog sample for the whole take
# instead of leaving the state to whatever the AI controller and gravity happen to produce, and MOVE_None
# means the CAPSULES do not translate — only the poses move, which is what makes a fixed line-up legible.
DRIVE_SPEED = 600.0

# The stand-in rifle. BP_MT_TestPawn carries a `WeaponMesh` holding SM_MT_Rifle (a project-authored
# duplicate of an engine Cube) — fine for the weapon beats, wrong for a roster line-up, where a grey box
# in four hands is the first thing the eye lands on. Hidden per-render on the live component; NOT an
# asset edit (the same per-render treatment Khaimera's axe gets).
HIDE_WEAPON_MESH = True

# WHAT THIS DRIVER SAMPLES, AND WHY IT IS NO LONGER JUST hand_r.
# An earlier version's shipped log carried `hand_r` world Z and a dominant-state read, and captioned the shot
# "4 rigs jogging". Both numbers were real and NEITHER COULD SEE THE DEFECT: the MetaHuman's feet were
# pinned flat by the Template's foot-IK Control Rig solving onto ik_foot_l / ik_foot_r, which
# metahuman_base_skel does not have — its arms swung, so hand_r read 43.63 cm against a 0.5 cm threshold
# while its legs never moved. THE RULE THAT EARNED: the shipped render's own log must carry the bone the
# CAPTION is about. So the feet are sampled here too, and the per-rig LIVE dump reports them, so a pinned
# rig shows up in the shipped render's log rather than being inferred from an arm.
PROBE_BONE = 'hand_r'
FOOT_BONES = ('foot_l', 'foot_r')
# Read back per rig so the skeleton-aware foot-IK gate is OBSERVED in the shipped take rather than
# assumed from the asset. 'ABSENT' means the variable is not on that rig's AnimInstance class.
GATE_VAR = 'HasMannequinFootIKRig'

# ---- camera (world space) --------------------------------------------------------------------------
# The line-up runs along +Y at X=0 in ascending height (MetaHuman 158.4 at Y=-270 ... Khaimera 209.9 at
# Y=+270) and faces -X, so the camera sits downrange at -X looking back at them.
#
# A slow lateral dolly from the SHORT end to the TALL end, rising as it goes. That move is what makes the
# height spread read: it walks the eye up the line instead of presenting it as a static chart. Sized off
# the CineCamera's default 23.76 x 13.365 mm filmback — horizontal coverage is (23.76 / focal) * distance,
# so at 32 mm and ~1090 cm the frame is ~809 cm wide against a 540 cm line-up plus Khaimera's silhouette.
#
# THE AIM POINT STAYS ON THE GROUP CENTRE, and that is arithmetic rather than taste. Lerping the aim
# sideways along with the camera swings the far end of the line-up out toward the frame edge: with the
# camera at y=+320 aiming at y=+60, the MetaHuman sits 17.1 deg off axis against a 20.4 deg half-FOV —
# 84% of the way to the edge, at the exact moment the move is meant to be showing the whole spread.
# Holding the aim at y=0 turns the same travel into an arc and keeps both ends at ~65-70%.
CAM_A = (unreal.Vector(-1060.0, -260.0, 104.0), unreal.Vector(0.0, 0.0, 104.0))
CAM_B = (unreal.Vector(-980.0, 260.0, 185.0), unreal.Vector(0.0, 0.0, 116.0))
CAM_FOCAL = 32.0

# Auto-exposure warm-up is a real defect, not a look. The first framing pass measured frame luma
# 168.9 -> 151.5 -> 136.6 across 45 frames — the shot visibly darkening while the eye adapts, which
# would read as a fade if a shipped subrange started inside it. The engine's default adaptation speeds
# are slow by design (cinematic eye adaptation); this take wants none of it, so both are pinned high and
# the ramp is over inside the first couple of frames. Cropping the subrange past the ramp would hide it
# rather than fix it, and would silently constrain which 12 seconds this shot is allowed to be.
AUTO_EXPOSURE_SPEED = 100.0

# (No MOVING_STATES here: this driver only records state histograms and makes no moving/idle decision.
# The moving/idle predicate lives in metahuman_bind.probe, which is what actually gates the claim.)

# Retire any previous instance of THIS driver, then claim a fresh generation so any other stale driver in
# the persistent run_python namespace retires on its own next tick.
GEN = _mt.retire_and_claim('_MT_ROSTER4')

# Strings and floats only — no world, no actor, no component (GOTCHA-A').
_S = {'handle': None, 'last': -999.0, 'focal_set': False,
      'rigs': {}, 'summary_at': -999.0, 'dressed': False}


def _drive_camera(world, now):
    # NOT mt_drive.drive_ab_camera: this shot's first-tick camera setup also pins auto-exposure (see
    # AUTO_EXPOSURE_SPEED above), which no other shot needs, so the A->B move is composed here out of
    # the shared pieces instead of the shared move growing a per-shot argument for it.
    cam = _mt.first_cine_camera(world)
    if not cam:
        return
    if not _S['focal_set']:
        comp = cam.get_cine_camera_component()
        if comp:
            comp.set_editor_property('current_focal_length', CAM_FOCAL)
            pp = comp.get_editor_property('post_process_settings')
            pp.set_editor_property('override_auto_exposure_speed_up', True)
            pp.set_editor_property('auto_exposure_speed_up', AUTO_EXPOSURE_SPEED)
            pp.set_editor_property('override_auto_exposure_speed_down', True)
            pp.set_editor_property('auto_exposure_speed_down', AUTO_EXPOSURE_SPEED)
            comp.set_editor_property('post_process_settings', pp)
        _S['focal_set'] = True
    f = now / TOTAL
    loc = _mt.vector_lerp(CAM_A[0], CAM_B[0], f)
    tgt = _mt.vector_lerp(CAM_A[1], CAM_B[1], f)
    cam.set_actor_location_and_rotation(loc, unreal.MathLibrary.find_look_at_rotation(loc, tgt),
                                        False, True)


def _tick(dt):
    try:
        if int(getattr(unreal, '_MT_DRIVE_GEN', 0)) != GEN:
            if _S['handle'] is not None:
                unreal.unregister_slate_pre_tick_callback(_S['handle'])
                _S['handle'] = None
            return
        world = unreal.EditorLevelLibrary.get_game_world()
        if not world:
            return
        now = unreal.SystemLibrary.get_game_time_in_seconds(world)
        _drive_camera(world, now)

        due = (now - _S['last']) >= SAMPLE_DT
        report = []
        dressed = []

        for a in unreal.GameplayStatics.get_all_actors_of_class(world, unreal.Character):
            mesh = a.get_component_by_class(unreal.SkeletalMeshComponent)
            inst = mesh.get_anim_instance() if mesh else None
            lbl = a.get_actor_label()
            if inst is None:
                if due:
                    report.append('%s:NO_ANIM_INSTANCE' % lbl)
                continue
            if 'ABP_MT' not in inst.get_class().get_name():
                continue

            if HIDE_WEAPON_MESH and not _S['dressed']:
                for comp in a.get_components_by_class(unreal.StaticMeshComponent):
                    comp.set_hidden_in_game(True)
                    dressed.append('%s/%s' % (lbl, comp.get_name()))

            cmc = a.get_component_by_class(unreal.CharacterMovementComponent)
            if cmc:
                # ORDER IS LOAD-BEARING — the measurements are in mt_drive.drive_cmc.
                _mt.drive_cmc(cmc, DRIVE_SPEED)

            if not due or now < SCORE_FROM_S:
                continue
            r = _S['rigs'].setdefault(lbl, {'states': {}, 'z': [], 'feet': {}, 'drift': 0.0,
                                            'gate': None})
            tools = _mt.anim_debug_tools()
            state = _mt.dominant_state(str(tools.call_method('GetAnimDebugState', args=(lbl,))))
            if state:
                r['states'][state] = r['states'].get(state, 0) + 1
            z = float(mesh.get_socket_location(PROBE_BONE).z)
            r['z'].append(z)
            for b in FOOT_BONES:
                r['feet'].setdefault(b, []).append(float(mesh.get_socket_location(b).z))
            graw = str(tools.call_method('GetAnimVariable', args=(lbl, GATE_VAR)))
            r['gate'] = graw.split('=', 1)[1].strip() if graw.startswith(GATE_VAR + ' =') \
                else ('ABSENT' if 'no property' in graw else graw)
            pel = mesh.get_socket_location('pelvis')
            loc = a.get_actor_location()
            r['drift'] = max(r['drift'], ((pel.x - loc.x) ** 2 + (pel.y - loc.y) ** 2) ** 0.5)
            report.append('%s:%s %s.z=%.2f pelvisXY=%.1f gate=%s'
                          % (lbl, state or 'NO_MACHINE', PROBE_BONE, z, r['drift'], r['gate']))

        if dressed:
            _S['dressed'] = True
            unreal.log('ROSTER4 dressed hidden_static_meshes=%s' % dressed)

        if due:
            _S['last'] = now
            unreal.log('ROSTER4 t=%.2f | %s' % (now, ' | '.join(report)))
            # Re-dump from the last third of the take, so the shipped render's log ends holding the
            # evidence even though the callback is torn down without warning when MRQ finishes.
            if now >= SCORE_FROM_S + 4.0 and (now - _S['summary_at']) >= 2.0:
                _S['summary_at'] = now
                for lbl in sorted(_S['rigs']):
                    r = _S['rigs'][lbl]
                    zs = r['z']
                    feet = ' '.join('%s_z_range=%.2fcm' % (b, (max(v) - min(v)) if v else 0.0)
                                    for b, v in sorted(r['feet'].items()))
                    unreal.log('ROSTER4 LIVE(t>=%.0fs, the shipped range) %s states=%s n=%d '
                               '%s_z=[%.2f..%.2f] range=%.2fcm %s gate=%s pelvis_drift_max=%.1fcm'
                               % (SCORE_FROM_S, lbl,
                                  ','.join('%s(%d)' % kv for kv in sorted(r['states'].items())),
                                  len(zs), PROBE_BONE, min(zs) if zs else 0.0, max(zs) if zs else 0.0,
                                  (max(zs) - min(zs)) if zs else 0.0, feet, r['gate'], r['drift']))
    except Exception as e:
        unreal.log_warning('ROSTER4 err %s' % e)


_S['handle'] = unreal.register_slate_pre_tick_callback(_tick)
unreal._MT_ROSTER4 = _S
unreal.log('ROSTER4 driver registered gen=%d total=%.1fs speed=%.0f focal=%.0f'
           % (GEN, TOTAL, DRIVE_SPEED, CAM_FOCAL))
