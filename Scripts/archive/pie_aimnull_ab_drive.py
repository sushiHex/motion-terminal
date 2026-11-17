# ============================================================================================
# Does the Template's AO_Rifle aim offset deform the pose AT REST (AimPitch = AimYaw = 0)?
# ============================================================================================
# THE QUESTION. The aim sweep proved the RotationOffsetBlendSpace node RESPONDS to aim: sweep AimPitch and
# the upper body rotates ~84 deg while the lower body holds (Scripts/pie_aimsweep_drive.py). Nothing has
# ever asserted the other half — that at pitch 0 the node is IDENTITY. It is not the same claim, and the
# rendered posture depends on the one nobody checked: every shot that RENDERS THE TEMPLATE runs this node at
# AimPitch 0 over
# an UNARMED locomotion pose, and the characters read head-down with the shoulders counter-rotated and
# the left arm drawn across the chest.
#
# WHY IT WAS AN OPEN QUESTION AND NOT AN OBVIOUS BUG. AO_Rifle's pitch axis is populated by three samples
# (CU +0.989 / CC 0 / CD -0.980 — measured, pie_aimsweep_drive.py header), so pitch 0 evaluates the CC
# sample, MM_Rifle_Idle_ADS_AO_CC. If Epic authored CC to BE the additive base, its mesh-space delta is
# identity and accumulating it onto ANY base pose is a no-op — in which case this node is innocent and
# the posture came from somewhere else. If CC carries a non-zero rifle-carry delta, it is added to an
# unarmed jog with nothing to cancel against. Only measurement separates those.
#
# THE ENGINE CONTRACT THIS RELIES ON (AnimNode_RotationOffsetBlendSpace.cpp, UE 5.8):
#   line 82   BasePose.Evaluate(Context);                                     <- unconditional
#   line 84   if (bIsLODEnabled && FAnimWeight::IsRelevant(ActualAlpha) && GetBlendSpace())
#   line 100  FAnimationRuntime::AccumulateMeshSpaceRotationAdditiveToLocalPose(...)
# So a NULL BlendSpace is a clean structural passthrough of the base pose — not a stale-pose hold. That
# is what makes nulling `AimOffset_Rifle` a legitimate OFF leg rather than an alpha-disable of the kind
# the foot-IK work already proved can be a confound (the foot-IK "alpha disable" never turned that node
# off, which is why the later foot-IK A/B had to remove the node structurally). Here the OFF is enforced INSIDE Evaluate by a
# null check, so it cannot be faked.
#
# ALSO NOTE line 16: `Alpha(1.f)`. At the time this probe was written the node HAD a full alpha suite and
# the Template had never bound the pin, so ActualAlpha sat at the constructor default 1.0. The alpha line
# logged below reads that straight out of GatherDebugData, so the probe records the
# unbound-at-full-weight condition as data rather than as an assertion about the .uasset. (The fix that
# came out of this run binds it to `AimOffsetAlpha`, default 0 — verified by pie_aimgate_verify_drive.py.)
#
# METHOD — ALTERNATING PAIRS, via mt_drive.alternating_pairs_deltas; see that function for why holding
# each state for seconds cannot work here and what the dAA control is for.
#
# ---------------------------------------------------------------------------------------------
# WHY THIS IS v2 — the v1 run produced a number that could not be honestly interpreted, twice over.
# ---------------------------------------------------------------------------------------------
# (1) THE LOWER-BODY GUARD FAILED. v1 inherited pie_aimsweep_drive's confound guard (foot_r must not move,
#     because an upper-body aim offset cannot touch the legs) and it came back thigh_r 37.1 deg /
#     foot_r 21.8 deg against a 0.1-0.6 deg control. Either the guard's premise is wrong for THIS graph or
#     something other than the aim node moved. Two candidates, and they are separable:
#       (a) the additive is not upper-body-only — a pelvis/root-level component would propagate to the
#           legs through the hierarchy, so `root` and `pelvis` are now SAMPLED rather than assumed;
#       (b) the foot-IK Control Rig sits DOWNSTREAM of the aim node (LayeredBoneBlend -> Aim -> ControlRig),
#           so any pelvis change makes it re-solve and move the feet. That makes foot_r an invalid
#           discriminator in the shipped graph no matter what the additive does.
#     Phase IDLE_CR0 re-runs the identical A/B with FootIKAlpha forced to 0. If the leg deltas collapse,
#     (b) owns them; if they survive, (a) does. Either way the guard stops being a coin flip.
#     RESULT: they survived unchanged (37.12 -> 37.04), so (a) owns them and the "upper-body only"
#     premise in pie_aimsweep_drive's header is false as a general statement about this node.
# (2) THE LOCOMOTION PHASE NEVER LOCOMOTED. v1 wrote cmc.Velocity directly, so CharacterMovement's own
#     tick bled it back to zero and the pawn stayed in Idle — betrayed by a thigh_r control of 0.10 deg
#     across a supposed 600 cm/s jog. The two phases then agreed to three decimals, which reads like
#     "the additive is base-independent" and is in fact "both phases were the same pose". Now driven
#     through mt_drive.drive_cmc, which is the proven injection (MOVE_None + tick disabled + every tick),
#     and the leg control is itself the evidence the phase is real.
#
# GOTCHA-A': every World/Actor/Component handle is a local inside _tick; nothing binds at module level.
# That is also why the pawn/mesh/AnimInstance are re-resolved per tick rather than cached into `_G` —
# `_G` outlives the PIE session on `unreal._MT_AIMNULL`, and a component ref surviving there is exactly
# what makes the next map change a fatal crash at EditorServer.cpp:2544.
# ============================================================================================
import importlib
import os
import sys

import unreal

_MT_SCRIPTS = os.path.join(
    unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()), 'Scripts')
if _MT_SCRIPTS not in sys.path:
    sys.path.insert(0, _MT_SCRIPTS)
_mt = importlib.reload(importlib.import_module('mt_drive'))

AIM_ASSET = '/Game/Characters/Mannequins/Anims/Rifle/AIM/AO_Rifle'
AIM_VAR = 'AimOffset_Rifle'
SLOT = '_MT_AIMNULL'
DRIVE_SPEED = 600.0                   # the lineup take's jog speed (pie_roster4_drive.DRIVE_SPEED)

# (name, start_s, end_s, foot_ik_alpha, drive_speed)
PHASES = (
    ('IDLE_CR1', 2.0, 9.0, 1.0, 0.0),      # shipped condition, at rest
    ('IDLE_CR0', 11.0, 18.0, 0.0, 0.0),    # foot-IK Control Rig off -> does it own the leg delta?
    ('LOCO_CR1', 20.0, 28.0, 1.0, DRIVE_SPEED),   # shipped condition, actually jogging
)
END_S = 28.5

# `root` and `pelvis` are sampled, not assumed: a delta at either propagates down BOTH limbs and is the
# difference between "the aim offset deforms the upper body" and "the aim offset reorients the character".
SPINE = ('root', 'pelvis', 'spine_01', 'spine_02', 'spine_03', 'neck_01', 'head')
ARMS = ('clavicle_l', 'upperarm_l', 'lowerarm_l', 'hand_l', 'clavicle_r', 'upperarm_r', 'hand_r')
LEGS = ('thigh_r', 'calf_r', 'foot_r')
BONES = SPINE + ARMS + LEGS


def _group(b):
    return 'SPINE' if b in SPINE else ('ARM' if b in ARMS else 'LEG')


_mt.retire_previous(SLOT)
_G = {'handle': None, 'label': None, 'asset': None,
      'prev_on': None, 'hist': [], 'phase': None, 'alpha': None, 'done': False}


def _report(phase, hist):
    ab, aa = _mt.alternating_pairs_deltas(hist, BONES)
    n = len(ab.get(BONES[0], []))
    unreal.log('AIMNULL ==== %s pairs=%d alpha=[%s]' % (phase, n, _G['alpha']))
    if not n:
        unreal.log_warning('AIMNULL %s: no ON/OFF/ON triples captured' % phase)
        return
    for b in BONES:
        v, c = ab[b], aa[b]
        unreal.log('AIMNULL %-9s %-11s %-5s dAB mean=%7.3f max=%7.3f | control dAA mean=%7.3f max=%7.3f'
                   % (phase, b, _group(b), sum(v) / len(v), max(v), sum(c) / len(c), max(c)))


def _tick(dt):
    try:
        if _G['done']:
            return
        w = unreal.EditorLevelLibrary.get_game_world()
        if not w:
            return
        pc = unreal.GameplayStatics.get_player_controller(w, 0)
        pawn = pc.get_controlled_pawn() if pc else None
        if not pawn:
            return
        mesh = pawn.get_component_by_class(unreal.SkeletalMeshComponent)
        inst = mesh.get_anim_instance() if mesh else None
        if not (mesh and inst):
            return
        if _G['label'] is None:
            _G['label'] = pawn.get_name()
            _G['asset'] = unreal.load_asset(AIM_ASSET)
            unreal.log('AIMNULL label=%s asset=%s' % (_G['label'], _G['asset']))

        now = unreal.SystemLibrary.get_game_time_in_seconds(w)
        cur = None
        for pname, t0, t1, fik, spd in PHASES:
            if t0 <= now <= t1:
                cur = (pname, fik, spd)
                break

        # Written EVERY tick, not once at init. These are the inputs the claim is ABOUT, and a probe
        # that sets them once trusts that nothing else in a persistent `run_python` namespace ever
        # writes them again — which is the assumption class this project keeps getting burned by. The
        # cost is a handful of reflection calls on a diagnostic run.
        _bad = _mt.set_anim_vars_checked(
            _G['label'], (('AimPitch', 0.0), ('AimYaw', 0.0), ('WeaponIKAlpha', 0.0),
                          ('FootIKAlpha', cur[1] if cur else 1.0)))
        if _bad and not _G.get('write_failed'):
            _G['write_failed'] = True
            unreal.log_error('AIMNULL SetAnimVariable FAILED for %s - this A/B is measuring a graph it '
                             'never configured, so its deltas are void, not evidence' % ', '.join(_bad))

        # drive_cmc MUST run every tick: Speed is derived as VectorLength(GetVelocity) by the AnimBP,
        # and CharacterMovement's own tick bleeds an injected velocity back toward zero. See v2 note (2).
        cmc = pawn.get_component_by_class(unreal.CharacterMovementComponent)
        if cmc:
            _mt.drive_cmc(cmc, cur[2] if cur else 0.0)

        phase = cur[0] if cur else None
        if phase != _G['phase']:
            if _G['phase'] and _G['hist']:
                _report(_G['phase'], _G['hist'])
            _G['phase'], _G['hist'] = phase, []

        # Attribute the pose we are about to read to the asset state set on the PREVIOUS tick.
        if phase and _G['prev_on'] is not None:
            _G['hist'].append((_G['prev_on'], _mt.sample_bone_rotations(mesh, BONES)))
            if _G['alpha'] is None and _G['prev_on']:
                _G['alpha'] = _mt.node_debug_line(_G['label'], 'rotationoffsetblendspace')

        nxt = not _G['prev_on']
        inst.set_editor_property(AIM_VAR, _G['asset'] if nxt else None)
        _G['prev_on'] = nxt

        if now > END_S:
            if _G['hist']:
                _report(_G['phase'], _G['hist'])
            _G['done'] = True
            unreal.log('AIMNULL DONE')
    except Exception as e:
        unreal.log_warning('AIMNULL err %s' % e)


_G['handle'] = unreal.register_slate_pre_tick_callback(_tick)
setattr(unreal, SLOT, _G)
unreal.log('AIMNULL v2 registered - phases %s' % ', '.join(p[0] for p in PHASES))
