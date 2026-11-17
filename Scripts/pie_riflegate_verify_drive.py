# ============================================================================================
# Does RifleModeAlpha actually GATE the rifle-mode branch, and does its mask stop at the waist?
# ============================================================================================
# Sibling of Scripts/archive/pie_aimgate_verify_drive.py, and it exists because the first authoring of this branch
# shipped a gate that did not gate — caught by a compiler warning, NOT by any test in the suite.
#
# WHAT WENT WRONG (2026-08-14, recorded because the failure mode is the point). The branch weight was
# authored as a PIN BINDING: LayeredBoneBlend.BlendWeights_0 -> property-access path "RifleModeAlpha".
# That binding does not compile — BlendWeights is a TArray<float> and the binding resolved against the
# array rather than element 0, so the compiler emitted "Cannot copy property (float -> TArray)" and
# DROPPED the copy. The pin kept its literal default of 1.000000. RifleModeAlpha was inert and the rifle
# branch was hard-wired ON for every character in every shot.
#
# The whole suite went 22/0 over that graph. Pie.AimOffset passed on all three rigs at 79.4 deg, because
# it measures the aim RESPONSE — the delta between aim=0 and aim=0.9 — and BOTH of those poses sit on
# whatever base the branch supplies. A test that differences two poses on the same base cannot see the
# base. Nothing else looked at the layered blend at all. The fix is a real K2Node_VariableGet link into
# BlendWeights_0; this probe is the detector that was missing, so the next silent un-wiring is loud.
#
# THE TWO LEGS:
#
#   GATE      Alternate RifleModeAlpha 1/0 with the AIM OFF, isolating the rifle BASE. Upper body must
#             swing hard (unarmed locomotion pose vs MF_Rifle_Idle_ADS). This is the leg that fails
#             closed on the exact defect above: an unwired weight makes both arms the same pose, so dAB
#             collapses to the dAA control and the "gate" reads as ~0 deg instead of tens of degrees.
#
#   MASK      The SAME alternation, read on the LOWER body. LayeredBoneBlend is masked from spine_01
#             with depth 1, so the legs must keep coming from locomotion: thigh/calf/foot must stay at
#             control level while the spine/arms move. A blend authored at the root — or a mask that
#             silently emptied — would put a rifle idle on the legs and drag the feet, and no existing
#             gate would see that either (Army.NoFrozen asserts feet MOVE, which a dragged foot does).
#
# Reading it the way the sibling probe is read: dAB is the signal, dAA the adjacent-frame control at
# constant state. In an alternating phase the pose swings every tick, so a stray sample can land on the
# wrong side of the toggle and inflate dAA's MAX toward the signal — the MEAN is the statistic.
#
# WHAT THE WITNESS LINE DOES AND DOES NOT SHOW (recorded because the sibling probe was withdrawn for
# exactly this kind of over-reading). `blendLine` is logged for BOTH arms, and on the 2026-08-14 run it
# read "AnimNode_LayeredBoneBlend<W:100.0%> ... (weight 1.000)" on both. That is the NODE's own inherited
# graph weight, not BlendWeights[0], so it does NOT discriminate the two arms and must not be cited as
# evidence that the toggle took. The discrimination rests entirely on the data: dAA is 0.000 deg on every
# upper bone (at a constant state the idle upper body is bit-identical frame to frame) while dAB is tens
# of degrees, which is only possible if the sampled poses genuinely alternate between two distinct
# states. The line is kept as context, labelled for what it is.
#
# FIRST GREEN RUN (2026-08-14, Manny / Lvl_MT_Test, 7 pairs per phase):
#   GATE            UPPER worst mean dAB = 97.72 deg (hand_l) against a 0.000 control;
#                   LOWER worst mean dAB =  0.34 deg (pelvis) against its OWN 0.641 control — i.e. every
#                   lower bone moved LESS than adjacent-frame noise. Mask holds.
#   AIM_OVER_RIFLE  UPPER worst mean dAB = 44.38 deg (clavicle_r); LOWER worst 0.26 deg (control 0.675).
# For contrast, the ORIGINAL defect (aim over the unarmed base) moved thigh_r 37.1 deg. It now moves
# 0.21 deg: the aim stopped deforming the legs because it is masked to the upper body AND applied over
# the base its deltas were extracted against.
#
# GOTCHA-A': every World/Actor/Component handle is a local inside _tick; nothing binds at module level
# and nothing is cached into `_G` — `_G` outlives the PIE session on `unreal._MT_RIFLEGATE`.
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

RIFLE_VAR = 'RifleModeAlpha'
AIM_ALPHA_VAR = 'AimOffsetAlpha'
SLOT = '_MT_RIFLEGATE'

# (name, start_s, end_s, rifle_a, aim_a, rifle_b, aim_b) — alternates state A and B every tick.
PHASES = (
    ('GATE', 2.0, 9.0, 1.0, 0.0, 0.0, 0.0),        # rifle base on vs off, aim out of the picture
    ('AIM_OVER_RIFLE', 11.0, 18.0, 1.0, 1.0, 1.0, 0.0),  # aim responds ON TOP of the rifle base
)
END_S = 18.5

# Split deliberately: the mask claim is about WHICH of these two groups moves. spine_01 is the mask
# root, so it belongs to UPPER; thigh/calf/foot are below it and must not follow.
UPPER = ('spine_01', 'spine_03', 'neck_01', 'head',
         'clavicle_l', 'upperarm_l', 'hand_l', 'clavicle_r', 'upperarm_r', 'hand_r')
LOWER = ('pelvis', 'thigh_l', 'calf_l', 'foot_l', 'thigh_r', 'calf_r', 'foot_r')
BONES = UPPER + LOWER

# pelvis sits ABOVE the mask root in the hierarchy but is not itself blended (mask starts at spine_01),
# so it is grouped with LOWER and expected quiet. If it ever moves with the upper body the mask root
# moved down the spine, which is a different graph than the one this project documents.

# Ticks to discard after a phase change before sampling resumes. NOT decoration: the sibling probe
# carries its A/B state across the phase boundary and samples before configuring the new phase, which is
# why its "proven-ON" leg logged the aim node at Alpha (0.0%) during the very leg meant to prove it on -
# that claim had to be withdrawn. Here the phase change clears `prev` (so the first sample of a phase is
# never labelled with the previous phase's write) and holds sampling off for these ticks so the pose has
# actually caught up with the new state before anything is attributed to it.
SETTLE_TICKS = 4

_mt.retire_previous(SLOT)
_G = {'handle': None, 'label': None, 'prev': None, 'hist': [],
      'phase': None, 'done': False, 'weight_seen': {}, 'settle': 0}


def _group_stats(means, bones):
    """(worst mean signal, worst mean control) over a bone group, from precomputed per-bone means.

    Takes means rather than the raw delta lists so that "mean" is defined in exactly ONE place. The
    per-bone log line below and these group figures are the same statistic reported at two
    granularities; computing it twice let a future change to how it is derived (trimmed mean, outlier
    rejection) land in one site and silently not the other.
    """
    sig = ctl = 0.0
    for b in bones:
        m_ab, m_aa = means[b]
        sig, ctl = max(sig, m_ab), max(ctl, m_aa)
    return sig, ctl


def _report(phase, hist):
    ab, aa = _mt.alternating_pairs_deltas(hist, BONES)
    n = len(ab.get(BONES[0], []))
    unreal.log('RIFLEGATE ==== %s pairs=%d' % (phase, n))
    for arm in (True, False):
        unreal.log('RIFLEGATE %-14s witness arm=%-5s blendLine=%s'
                   % (phase, arm, _G['weight_seen'].get((phase, arm))))
    if not n:
        unreal.log_warning('RIFLEGATE %s: no A/B/A triples captured' % phase)
        return
    # Indexed, not .get()-with-a-default: past this early return every bone in BONES has samples, so a
    # missing key is an anomaly worth raising rather than papering over. A 0.0 default would quietly
    # feed a fabricated data point into the max() below and read as "this bone did not move" - the
    # exact shape of failure this probe was written to catch.
    means = {}
    for b in BONES:
        v, c = ab[b], aa[b]
        means[b] = (sum(v) / len(v), sum(c) / len(c))
        unreal.log('RIFLEGATE %-14s %-11s dAB mean=%7.3f max=%7.3f | control dAA mean=%7.3f max=%7.3f'
                   % (phase, b, means[b][0], max(v), means[b][1], max(c)))
    usig, uctl = _group_stats(means, UPPER)
    lsig, lctl = _group_stats(means, LOWER)
    unreal.log('RIFLEGATE %-14s UPPER worst mean dAB = %.3f deg (control %.3f) | '
               'LOWER worst mean dAB = %.3f deg (control %.3f)' % (phase, usig, uctl, lsig, lctl))
    # Stated as a verdict rather than left to the reader: this probe exists because a human read a green
    # suite and concluded the branch worked. The thresholds are deliberately loose - the defect this
    # catches collapses the signal to the control, which is an order of magnitude, not a near miss.
    if phase == 'GATE':
        if usig < 5.0:
            unreal.log_error(
                'RIFLEGATE GATE FAILED: upper-body worst mean dAB is only %.3f deg. Toggling %s '
                'between 1 and 0 barely changes the pose, which is what an UNWIRED blend weight looks '
                'like - check that BlendWeights_0 on the rifle LayeredBoneBlend is LINKED to a '
                'variable-get and not merely pin-bound (a pin binding silently fails to compile and '
                'leaves the literal default in place)' % (usig, RIFLE_VAR))
        elif lsig > 5.0:
            unreal.log_error(
                'RIFLEGATE MASK FAILED: the LOWER body moved %.3f deg when %s toggled (upper %.3f). '
                'The blend is supposed to be masked from spine_01 down, leaving the legs to locomotion; '
                'a rifle idle reaching the legs drags the feet and no army gate would catch it'
                % (lsig, RIFLE_VAR, usig))
        else:
            unreal.log('RIFLEGATE GATE PROVEN: %s toggles the upper body by %.3f deg (control %.3f) '
                       'while the lower body stays at %.3f deg - the weight is wired AND the mask holds'
                       % (RIFLE_VAR, usig, uctl, lsig))


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

        now = unreal.SystemLibrary.get_game_time_in_seconds(w)
        cur = None
        for pname, t0, t1, ra, aa_, rb, ab_ in PHASES:
            if t0 <= now <= t1:
                cur = (pname, (ra, aa_), (rb, ab_))
                break

        # Pinned every tick: aim inputs held at 0 so the GATE phase measures the rifle BASE and nothing
        # else, and WeaponIKAlpha off so the hand solver cannot add motion to the arms being measured.
        _bad = _mt.set_anim_vars_checked(
            _G['label'], (('AimPitch', 0.0), ('AimYaw', 0.0), ('WeaponIKAlpha', 0.0)))
        if _bad and not _G.get('write_failed'):
            _G['write_failed'] = True
            unreal.log_error('RIFLEGATE SetAnimVariable FAILED for %s - the pose being compared is not '
                             'the pose this probe thinks it configured' % ', '.join(_bad))

        phase = cur[0] if cur else None
        if phase != _G['phase']:
            if _G['phase'] and _G['hist']:
                _report(_G['phase'], _G['hist'])
            # `prev` is cleared, not carried: a sample labelled with the outgoing phase's state is the
            # exact bug that voided the sibling probe's ON leg.
            _G['phase'], _G['hist'], _G['prev'], _G['settle'] = phase, [], None, SETTLE_TICKS

        if phase and _G['settle'] > 0:
            _G['settle'] -= 1
        elif phase and _G['prev'] is not None:
            _G['hist'].append((_G['prev'], _mt.sample_bone_rotations(mesh, BONES)))
            # Witness BOTH arms, not one. A single captured line cannot distinguish "the toggle worked"
            # from "both arms ran in the same state" - which is precisely the defect this probe exists
            # for, and precisely what a one-armed witness failed to catch last time.
            key = (phase, bool(_G['prev']))
            if key not in _G['weight_seen']:
                _G['weight_seen'][key] = _mt.node_debug_line(_G['label'], 'layeredboneblend')

        if phase:
            nxt = not _G['prev']
            rifle, aim = cur[1] if nxt else cur[2]
            # THE TWO ALPHA WRITES ARE THE WHOLE EXPERIMENT, and both are checked for the reason the
            # sibling probe learned the hard way: a dropped write leaves the var wherever it was, so an
            # arm silently measures the state it was supposed to be contrasted against. Latched, because
            # this runs every tick and an unlatched error buries the per-bone report.
            _abad = _mt.set_anim_vars_checked(
                _G['label'], ((RIFLE_VAR, float(rifle)), (AIM_ALPHA_VAR, float(aim))))
            if _abad and not _G.get('alpha_write_failed'):
                _G['alpha_write_failed'] = True
                unreal.log_error('RIFLEGATE write FAILED for %s in phase %s - this arm measures whatever '
                                 'the graph was already set to, so its deltas are void'
                                 % (', '.join(_abad), cur[0]))
            _G['prev'] = nxt

        if now > END_S:
            if _G['hist']:
                _report(_G['phase'], _G['hist'])
            _G['done'] = True
            unreal.log('RIFLEGATE DONE')
    except Exception as e:
        unreal.log_warning('RIFLEGATE err %s' % e)


_G['handle'] = unreal.register_slate_pre_tick_callback(_tick)
setattr(unreal, SLOT, _G)
unreal.log('RIFLEGATE registered - phases %s' % ', '.join(p[0] for p in PHASES))
