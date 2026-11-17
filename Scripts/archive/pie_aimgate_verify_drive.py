# ============================================================================================
# Does the new AimOffsetAlpha pin binding actually GATE the aim offset?
# ============================================================================================
# Sibling of pie_aimnull_ab_drive.py, which established the defect: at AimPitch 0 the AO_Rifle node is
# NOT identity — nulling its BlendSpace moved pelvis/spine ~33 deg, clavicle_r ~44 deg and thigh_r ~37 deg
# against a 0.1-2.5 deg base-drift control, because all three AO samples are mesh-space rotation additives
# whose base is frame 0 of MF_Rifle_Idle_ADS and the Template feeds them an UNARMED pose.
#
# The fix binds the node's previously-unbound Alpha pin (which sat at its constructor default 1.0) to a new
# `AimOffsetAlpha` float defaulting to 0. This probe asks the two questions that fix has to answer, and it
# is deliberately NOT the same measurement as the one that found the bug:
#
#   PROVEN-OFF  alpha 0 must equal the node genuinely absent. Measured as delta(alpha=0, BlendSpace=null).
#               An alpha that merely REDUCES the additive would still ship a deformed pose, and the foot-IK
#               work already paid for assuming an alpha-disable equals an absent node — that assumption is
#               exactly what made the first foot-IK A/B a confound. So OFF is checked against the null
#               asset, whose passthrough is enforced inside Evaluate_AnyThread by an engine null check
#               (AnimNode_RotationOffsetBlendSpace.cpp:84), not against a hope.
#
#   PROVEN-ON   alpha 1 must still reproduce the original deformation. A gate that silently disabled the
#               node at every alpha would also pass the OFF leg while destroying the aim beat. Measured as
#               delta(alpha=1, alpha=0) and compared against the ~33 deg figures the defect probe recorded.
#
# A test that can only go green is not a test: the ON leg is what makes the OFF leg mean something.
#
# METHOD: the same alternating-pairs scheme as the defect probe, via mt_drive.alternating_pairs_deltas.
# Idle only — at Speed 600 the adjacent-frame control rises to 30-45 deg on limbs and the method loses
# discrimination, which the defect probe's LOCO phase demonstrated. A mesh-space rotation additive
# composes a fixed per-bone delta independent of the base pose, so idle is representative.
#
# READING THE OUTPUT: the OFF_vs_NULL phase is the clean leg — both its states produce the same pose, so
# its control is meaningful. In ON_vs_OFF the pose swings fully every tick, so an occasional sample lands
# on the wrong side of the toggle and inflates the control's MAX to roughly the signal's max; the MEAN is
# the statistic to read there (~4 deg control against ~30 deg signal).
#
# GOTCHA-A': every World/Actor/Component handle is a local inside _tick; nothing binds at module level,
# and nothing is cached into `_G` — `_G` outlives the PIE session on `unreal._MT_AIMGATE`.
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
ALPHA_VAR = 'AimOffsetAlpha'
SLOT = '_MT_AIMGATE'

# (name, start_s, end_s, alpha_a, present_a, alpha_b, present_b) — each phase alternates state A and
# state B every tick, where a state is (AimOffsetAlpha, is the BlendSpace asset assigned).
PHASES = (
    ('OFF_vs_NULL', 2.0, 9.0, 0.0, True, 0.0, False),   # must be ~0: alpha 0 == node absent
    ('ON_vs_OFF', 11.0, 18.0, 1.0, True, 0.0, True),    # must be ~33 deg: the gate still gates
)
END_S = 18.5

BONES = ('root', 'pelvis', 'spine_01', 'spine_03', 'neck_01', 'head',
         'clavicle_l', 'upperarm_l', 'hand_l', 'clavicle_r', 'hand_r',
         'thigh_r', 'foot_r')          # legs included: the additive is NOT upper-body-only

_mt.retire_previous(SLOT)
_G = {'handle': None, 'label': None, 'asset': None,
      'prev': None, 'hist': [], 'phase': None, 'done': False, 'alpha_seen': {}}


def _report(phase, hist):
    ab, aa = _mt.alternating_pairs_deltas(hist, BONES)
    n = len(ab.get(BONES[0], []))
    unreal.log('AIMGATE ==== %s pairs=%d alphaLine=%s' % (phase, n, _G['alpha_seen'].get(phase)))
    if not n:
        unreal.log_warning('AIMGATE %s: no A/B/A triples captured' % phase)
        return
    worst = 0.0
    for b in BONES:
        v, c = ab[b], aa[b]
        worst = max(worst, sum(v) / len(v))
        unreal.log('AIMGATE %-12s %-11s dAB mean=%7.3f max=%7.3f | control dAA mean=%7.3f max=%7.3f'
                   % (phase, b, sum(v) / len(v), max(v), sum(c) / len(c), max(c)))
    unreal.log('AIMGATE %-12s WORST-BONE mean dAB = %.3f deg' % (phase, worst))


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

        now = unreal.SystemLibrary.get_game_time_in_seconds(w)
        cur = None
        for pname, t0, t1, aa_, pa, ab_, pb in PHASES:
            if t0 <= now <= t1:
                cur = (pname, (aa_, pa), (ab_, pb))
                break

        # Every tick, for the same reason as the defect probe: these are the inputs the claim is about.
        _bad = _mt.set_anim_vars_checked(
            _G['label'], (('AimPitch', 0.0), ('AimYaw', 0.0), ('WeaponIKAlpha', 0.0)))
        if _bad and not _G.get('write_failed'):
            _G['write_failed'] = True
            unreal.log_error('AIMGATE SetAnimVariable FAILED for %s - the pose being compared is not '
                             'the pose this probe thinks it configured' % ', '.join(_bad))

        phase = cur[0] if cur else None
        if phase != _G['phase']:
            if _G['phase'] and _G['hist']:
                _report(_G['phase'], _G['hist'])
            _G['phase'], _G['hist'] = phase, []

        if phase and _G['prev'] is not None:
            _G['hist'].append((_G['prev'], _mt.sample_bone_rotations(mesh, BONES)))
            if _G['prev'] and phase not in _G['alpha_seen']:
                _G['alpha_seen'][phase] = _mt.node_debug_line(
                    _G['label'], 'rotationoffsetblendspace')

        if phase:
            nxt = not _G['prev']
            alpha, present = cur[1] if nxt else cur[2]
            # THE ALPHA WRITE IS THE WHOLE EXPERIMENT. A dropped one leaves the node at its default 0
            # and the ON arm silently measures an OFF node - which is exactly what happened: the
            # 2026-08-04 run logged Alpha (0.0%) during ON_vs_OFF and the resulting "proven-ON" claim
            # had to be retracted. Checked from here on.
            # LATCHED, like the reset block above it. This runs EVERY TICK, not on phase entry, so an
            # unlatched log_error emitted thousands of identical lines across a phase window - burying
            # the per-bone report this probe exists to produce, and slowing the very tick it measures.
            _abad = _mt.set_anim_vars_checked(_G['label'], ((ALPHA_VAR, float(alpha)),))
            if _abad and not _G.get('alpha_write_failed'):
                _G['alpha_write_failed'] = True
                unreal.log_error('AIMGATE %s WRITE FAILED in phase %s - this arm measures whatever the '
                                 'node was already set to, so its deltas are void' % (ALPHA_VAR, cur[0]))
            inst.set_editor_property(AIM_VAR, _G['asset'] if present else None)
            _G['prev'] = nxt

        if now > END_S:
            if _G['hist']:
                _report(_G['phase'], _G['hist'])
            _G['done'] = True
            unreal.log('AIMGATE DONE')
    except Exception as e:
        unreal.log_warning('AIMGATE err %s' % e)


_G['handle'] = unreal.register_slate_pre_tick_callback(_tick)
setattr(unreal, SLOT, _G)
unreal.log('AIMGATE registered - phases %s' % ', '.join(p[0] for p in PHASES))
