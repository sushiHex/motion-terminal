# ============================================================================================
# Is AO_Rifle's CENTRE sample identity against its own additive reference pose?
# ============================================================================================
# THE QUESTION. research/aim-offset-neutrality-defect.md asserts a mechanism: "the additive is
# neutral ONLY when the pose beneath it is the rifle ADS idle." An objection to that runs: it does
# not survive contact with how UE bakes additives:
#
#   For AAT_ROTATION_OFFSET_MESH_SPACE, the per-bone delta is extracted ONCE, at bake time, as
#       D_i = S_i * R_i^-1
#   where S_i is the sample's mesh-space rotation and R_i the REFERENCE pose's. It is applied as
#       O_i = D_i * B_i
#   over whatever base B_i is present.
#
# The tempting reading is "so the centre sample must be identity, and the write-up's mechanism is
# wrong." THAT INFERENCE IS THE MISTAKE. `D` is a
# fixed transform, but the RESULT `D*B` is not base-independent at all. Over its own reference the
# centre delta RECONSTRUCTS the authored aim pose — `D*R = S` — and a centre COORDINATE on an aim
# offset means "aiming forward", not "no change". A non-identity centre delta is therefore correct
# authoring, not a defect, and the original mechanism in the write-up was right: the delta is valid
# only over the base it was extracted against, and this graph supplies the UNARMED pose instead.
#
# SO WHAT THIS SCRIPT ACTUALLY MEASURES is narrower than it first claimed: the per-bone FRAME-0 LOCAL
# ROTATION DIFFERENCE between the centre sample and its reference. All-zero would prove D = identity
# (equal locals compose to equal mesh-space rotations); a nonzero MAGNITUDE is NOT the magnitude of the
# mesh-space delta, because for any bone below the first moved parent that delta composes the chain
# above it. It does NOT decide whether the asset is sound; it cannot. It reads the assets rather than the running graph, so there is no PIE,
# no world and no component refs, and hence no GOTCHA-A' stale-reference hazard on the next map
# change. Corroboration that the figure is real: pelvis reads 32.75 deg here, and the independent
# live PIE A/B (asset assigned vs nulled, AimPitch=AimYaw=0 written every tick) read 32.82 deg.
#
# Run: UnrealEditor-Cmd.exe <uproject> -run=pythonscript -script="<this file>" -unattended -nullrhi
# ============================================================================================
import math

import unreal

AO_PATH = '/Game/Characters/Mannequins/Anims/Rifle/AIM/AO_Rifle.AO_Rifle'

# The bones the defect table reports on, spanning root -> spine -> arms and root -> legs, so a
# torso-only delta and a whole-body delta are distinguishable in the output.
BONES = ('root', 'pelvis', 'spine_01', 'spine_03', 'neck_01', 'head',
         'clavicle_l', 'clavicle_r', 'upperarm_l', 'lowerarm_l', 'hand_l',
         'thigh_l', 'thigh_r', 'calf_r', 'foot_r')


def quat_angle_deg(a, b):
    """Angular distance between two rotations. abs(dot) folds q/-q (same orientation) to 0."""
    dot = a.x * b.x + a.y * b.y + a.z * b.z + a.w * b.w
    return math.degrees(2.0 * math.acos(max(-1.0, min(1.0, abs(dot)))))


def frame0_local(seq, bone):
    """Frame-0 LOCAL (parent-relative) bone transform of `seq`, or None if unreadable."""
    try:
        return unreal.AnimationLibrary.get_bone_pose_for_frame(seq, bone, 0, False)
    except Exception as e:
        unreal.log_warning('  get_bone_pose_for_frame(%s, %s) failed: %s' % (seq.get_name(), bone, e))
        return None


def main():
    ao = unreal.EditorAssetLibrary.load_asset(AO_PATH)
    if ao is None:
        unreal.log_error('AO_RIFLE_PROBE: could not load %s — ABSENT, not clean' % AO_PATH)
        return
    unreal.log('AO_RIFLE_PROBE: loaded %s (%s)' % (AO_PATH, type(ao).__name__))

    # --- the samples, and their coordinates -------------------------------------------------
    samples = None
    for prop in ('sample_data', 'SampleData'):
        try:
            samples = ao.get_editor_property(prop)
            break
        except Exception:
            continue
    if not samples:
        unreal.log_error('AO_RIFLE_PROBE: no sample_data readable on the blend space — cannot decide')
        return

    unreal.log('AO_RIFLE_PROBE: %d samples' % len(samples))
    centre_seq = None
    for s in samples:
        seq = s.get_editor_property('animation')
        val = s.get_editor_property('sample_value')
        name = seq.get_name() if seq else '<none>'
        unreal.log('AO_RIFLE_PROBE:   sample %-28s at (X=%.3f, Y=%.3f)' % (name, val.x, val.y))
        if seq is not None and abs(val.x) < 1e-4 and abs(val.y) < 1e-4:
            centre_seq = seq

    if centre_seq is None:
        unreal.log_error('AO_RIFLE_PROBE: no sample sits at (0,0) — the centre is INTERPOLATED, '
                         'which is itself the answer: AimPitch=0 does not select a single authored pose')
        return

    # --- the additive settings, read off the centre sample itself ---------------------------
    def prop(o, *names):
        for n in names:
            try:
                return o.get_editor_property(n)
            except Exception:
                continue
        return '<unreadable>'

    add_type = prop(centre_seq, 'additive_anim_type')
    ref_type = prop(centre_seq, 'ref_pose_type')
    ref_seq = prop(centre_seq, 'ref_pose_seq')
    ref_frame = prop(centre_seq, 'ref_frame_index')
    unreal.log('AO_RIFLE_PROBE: centre sample = %s' % centre_seq.get_name())
    unreal.log('AO_RIFLE_PROBE:   additive_anim_type = %s' % add_type)
    unreal.log('AO_RIFLE_PROBE:   ref_pose_type      = %s' % ref_type)
    unreal.log('AO_RIFLE_PROBE:   ref_pose_seq       = %s' % (
        ref_seq.get_name() if hasattr(ref_seq, 'get_name') else ref_seq))
    unreal.log('AO_RIFLE_PROBE:   ref_frame_index    = %s' % ref_frame)

    if not hasattr(ref_seq, 'get_name'):
        unreal.log_error('AO_RIFLE_PROBE: centre sample has no ref_pose_seq — cannot compare')
        return

    # --- THE COMPARISON ---------------------------------------------------------------------
    # Local (parent-relative) rotations are compared. If every local matches, the mesh-space
    # composition matches too, so local equality is sufficient to prove D = Identity.
    unreal.log('AO_RIFLE_PROBE: --- per-bone angle, centre sample frame 0 vs reference pose frame 0 ---')
    worst, worst_bone, n = 0.0, None, 0
    for b in BONES:
        ts = frame0_local(centre_seq, b)
        tr = frame0_local(ref_seq, b)
        if ts is None or tr is None:
            unreal.log('AO_RIFLE_PROBE:   %-12s <bone absent from one of the two sequences>' % b)
            continue
        d = quat_angle_deg(ts.rotation, tr.rotation)
        n += 1
        if d > worst:
            worst, worst_bone = d, b
        unreal.log('AO_RIFLE_PROBE:   %-12s %8.3f deg' % (b, d))

    if n == 0:
        unreal.log_error('AO_RIFLE_PROBE: compared 0 bones — result is ABSENT, not "identity"')
        return

    unreal.log('AO_RIFLE_PROBE: === worst = %.3f deg on %s, across %d bones ===' % (worst, worst_bone, n))
    # READ THIS NUMBER CORRECTLY, AND NARROWLY. It is a LOCAL rotation difference, not the magnitude of
    # `D = S*R^-1` — those coincide only for a bone whose whole parent chain is unchanged. A large value
    # is NORMAL either way: an aim offset's centre COORDINATE means "aiming forward", not "no change",
    # so the authored centre pose is expected to differ from the additive reference. `D` is not identity
    # and is not meant to be; what it IS, is base-DEPENDENT — over its own reference it reconstructs the
    # aim pose (D*R = S), over any other base it is an unrelated transform. This figure alone proves
    # nothing about whether the asset is sound.
    # (An earlier revision of this script concluded from `worst > 0.5` that the ASSET was defective. That
    # was wrong, for the reason above. Kept as a comment because the mistake is the lesson.)
    unreal.log('AO_RIFLE_PROBE: worst frame-0 LOCAL rotation difference, centre sample vs reference = '
               '%.3f deg. READ IT AS EXACTLY THAT. It is NOT the magnitude of the mesh-space delta '
               'D = S*R^-1: these are parent-relative rotations, and for any bone below the first moved '
               'parent the mesh-space delta composes the whole chain above it. All-zero here WOULD prove '
               'D = identity, since equal locals compose to equal mesh-space rotations - but a nonzero '
               'MAGNITUDE does not transfer. A large value is expected regardless: centre means '
               '"aiming forward", not "no change".' % worst)
    unreal.log('AO_RIFLE_PROBE: NOT A GATE. This script reports; it never sets a nonzero exit code, it '
               'samples %d bones rather than the whole skeleton, and it ignores translation, scale and '
               'curves. Do not cite it as a passing check.' % n)


main()
