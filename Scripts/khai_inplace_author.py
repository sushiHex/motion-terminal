# Author IN-PLACE DUPLICATES of Khaimera's two travelling locomotion clips, plus the
# duplicate BlendSpace and duplicate child ABP that consume them, so the lineup take can show him JOGGING.
# Then BAKE REAL ik_foot_l / ik_foot_r EFFECTOR TRACKS into those duplicates, so the shared
# Template's foot-IK Control Rig pass stops pinning his feet. See the IK_PAIRS header block below for the
# mechanism, the live proof, and the measured space convention.
#
# ============================================================================================
# WHY DUPLICATES AND NOT AN IN-PLACE EDIT — this is the whole constraint, not a style preference.
#
# "Khaimera jogs in place" was solved once before, by zeroing the pelvis forward translation ON THE
# SHIPPED CLIPS. It worked for the shots and then broke the tiers: `Pie.RigLocomotion.Khaimera` went red
# (foot_l relZ 0.83 against an 8.0 floor) and the two clips had to be re-baked back to their retargeted state
# with `IKRetargetBatchOperation.run_batch_retarget`. So the edited
# originals are known-hostile to the verification suite.
#
# Everything this file writes therefore lands in a SEPARATE FOLDER and the originals are never opened for
# write. These rows must stay green and are re-run after every author:
#     GoldenDiff.MTKhaimera
#     Verify.ClipVaries.Khai_MF_Unarmed_Jog_Fwd     Verify.ClipVaries.Khai_MF_Unarmed_Walk_Fwd
#     Verify.ClipVaries.Khai_MM_{Idle,Jump,Fall_Loop,Land}
# `verify()` re-reads the originals afterwards and fails loudly if their pelvis travel changed.
#
# ============================================================================================
# WHERE THE TRAVEL ACTUALLY IS — measured on this machine before anything was authored, not assumed.
#
# `AnimationLibrary.get_bone_pose_for_frame` over every key of all six Khai_* clips, root and pelvis:
#
#     clip                       root dx/dy/dz        pelvis dy (forward)   pelvis dx / dz
#     Khai_MF_Unarmed_Jog_Fwd    0.00 / 0.00 / 0.00   1362.86 cm            6.22 / 11.34
#     Khai_MF_Unarmed_Walk_Fwd   0.00 / 0.00 / 0.00    578.57 cm            4.91 / 10.51
#     Khai_MM_Idle               0.00 / 0.00 / 0.00      1.01 cm            1.06 / 0.48
#     Khai_MM_Jump               0.00 / 0.00 / 0.00     18.11 cm            7.89 / 59.65
#     Khai_MM_Fall_Loop          0.00 / 0.00 / 0.00      6.40 cm            1.75 / 3.83
#     Khai_MM_Land               0.00 / 0.00 / 0.00     14.65 cm            7.11 / 34.31
#
# The root track is IDENTICALLY ZERO in all six. That is the decisive read: `force_root_lock` locks the
# root, and the root is already still, so the obvious route ("force_root_lock=True on the clip") cannot
# move this needle at all — it is not that the flag is insufficient, it is that there is nothing on that
# track to lock. All the travel is pelvis-local +Y.
#
# Do Idle/Jump/Fall/Land need treatment? They do NOT, and the reason is stronger than
# "the number is small": Jump's 18.1 cm and Land's 14.7 cm of pelvis Y sit alongside 59.7 cm and 34.3 cm
# of pelvis Z, i.e. they are the crouch-and-launch and the absorb, authored content of a one-shot pose,
# not net travel — and neither clip loops. Only the two locomotion clips ramp monotonically.
#
# ============================================================================================
# WHY THE PELVIS Y IS PINNED TO A CONSTANT RATHER THAN LINEARLY DE-RAMPED.
#
# The obvious minimal edit is to subtract a straight line so that y[0] == y[last] — it removes exactly the
# net travel and preserves whatever fore-aft surge the gait authored. Measured, that residual is:
#
#     jog  : peak-to-peak 60.94 cm    walk : peak-to-peak 9.82 cm
#
# and the jog's residual is NOT a per-stride surge. Printed frame by frame it is one smooth bowl across
# the whole loop (0.0 -> -35.7 -> -60.9 at frame 28 -> -13.3 -> 0.0): the clip ACCELERATES through its
# 1.767 s, so a linear fit lags it by 61 cm at mid-loop. De-ramping linearly would therefore leave
# Khaimera sliding 61 cm backwards and forwards once per loop against three neighbours that do not — a
# defect the eye reads as him losing his footing, dressed up as "preserving authored content".
#
# So: pelvis translation Y is pinned to its FIRST key value. X sway (6.2 cm), Z bob (11.3 cm), and every
# rotation and scale key on every bone are untouched. Net fore-aft travel becomes 0.00 cm by construction
# and the loop is seamless at the join for free.
#
# Nothing but the pelvis TRANSLATION Y changes: `verify()` re-samples eight bones across every key of both
# the original and the duplicate and requires them equal, so "only the pelvis moved" is measured rather
# than asserted from the shape of the code.
#
# ============================================================================================
# GOTCHA-A': run_python execs into a PERSISTENT namespace. Every function below keeps strings and floats
# at module scope only; no World/Level/Actor is ever bound to a module-level name.
#
# ☠️ RE-RUN `metahuman_bind.place(inplace=True)` AFTER EVERY `author`. `author` DELETES and re-duplicates
# the ABP (so the result is rebuildable rather than accreted), which leaves any saved level holding the
# OLD, now-dead class object. The pawn then silently gets no anim instance and renders in ref pose with no
# error — the identical silent-failure signature to skipping `metahuman_bind.declare`. It shows up only as
# `Roster_Khaimera:NO_ANIM_INSTANCE` in a probe log, and it costs a full probe run to find.
#
# Usage (run in the editor through `Scripts/editor_ops.py` (`ops.run_file`)):
#     KIP_ACTION = 'author'    # 'author' | 'verify' | 'report'
#     exec(open(r'.../Scripts/khai_inplace_author.py').read())
# Every action prints one `KHAIINPLACE <json>` line to LogPython.
import json

import unreal

SRC_DIR = '/Game/MT/Khaimera/Retargeted/'
DST_DIR = '/Game/MT/Khaimera/InPlace/'          # gitignored via Content/MT/Khaimera/ (checked first)

# (source clip, duplicate name). Only the two that travel.
CLIPS = [
    ('Khai_MF_Unarmed_Jog_Fwd', 'Khai_MF_Unarmed_Jog_Fwd_InPlace'),
    ('Khai_MF_Unarmed_Walk_Fwd', 'Khai_MF_Unarmed_Walk_Fwd_InPlace'),
]
# Confirmed above to need no treatment; re-measured by report()/verify() rather than taken on trust.
UNTREATED = ['Khai_MM_Idle', 'Khai_MM_Jump', 'Khai_MM_Fall_Loop', 'Khai_MM_Land']

SRC_BS = '/Game/MT/Khaimera/Khai_BS_Locomotion_2D'
DST_BS = DST_DIR + 'Khai_BS_Locomotion_2D_InPlace'
SRC_ABP = '/Game/MT/Khaimera/ABP_MT_Khaimera'
DST_ABP = DST_DIR + 'ABP_MT_Khaimera_InPlace'
DST_ABP_CLASS = DST_ABP + '.' + DST_ABP.rsplit('/', 1)[1] + '_C'

PELVIS = 'pelvis'

# =====================================================================================================
# THE IK EFFECTOR BAKE. Why the pelvis pin alone was not enough to make him jog on camera.
#
# The pelvis pin got Khaimera jogging IN PLACE, and a foot-motion probe proved he still was not STRIDING: at the shipped
# Speed 600 his feet measured foot_l 1.62 cm / foot_r 0.37 cm of world-Z range against a 10 cm threshold,
# while his hand_r swung 43.2 cm. Arms moving, legs frozen — the same signature the MetaHuman had, for a
# DIFFERENT reason.
#
# THE CAUSE. ABP_MT_Locomotion runs CR_Mannequin_FootIK, which solves the deform foot ONTO the ik_foot
# effector rather than offsetting it (a measured correction to this project's
# foot-IK notes: the solve drives foot_l ONTO ik_foot_l, dL->0). Every one of the six baked Khai_* retarget clips carries ik_foot_l and ik_foot_r translation
# spans of EXACTLY 0.000 / 0.000 / 0.000 — the IK Rig retarget chains never covered those bones. He HAS
# ik_foot_root/l/r natively (198-bone Paragon skeleton), so the skeleton-presence gate correctly
# reads true and correctly leaves the pass running. A CONSTANT EFFECTOR PINS A FOOT EXACTLY AS A MISSING
# ONE DOES. The clip's own foot_l carries 60.7 cm of vertical excursion; the solve threw all of it away.
#
# PROVEN, NOT ARGUED, before a single key was written. Forcing HasMannequinFootIKRig=false on the LIVE
# AnimInstance in PIE — no asset touched, no clip changed, the Control Rig pass simply bypassed — took his
# feet from 1.62 / 0.37 cm to 71.66 / 79.39 cm, while Manny/Quinn/MetaHuman moved by the ordinary amount.
# That isolates the effector-consuming Control Rig pass as the pinner and nothing else.
#
# THE SPACE CONVENTION, MEASURED OFF THE WORKING MANNEQUIN CLIPS RATHER THAN ASSUMED.
# ik_foot_l's LOCAL transform (relative to its parent ik_foot_root) carries the FK foot's transform
# expressed in ik_foot_root space. Parent chains are identical on both skeletons:
#     ik_foot_l -> ik_foot_root -> root        foot_l -> calf_l -> thigh_l -> pelvis -> root
# Over EVERY key of MF_Unarmed_Jog_Fwd and MF_Unarmed_Walk_Fwd, both feet, measured against the RAW
# animation data model by Verify.IKEffectorDrives and reproduced across two runs:
#   - ROTATION agrees to a max of 0.00103 deg.
#   - TRANSLATION agrees to a max of 0.0001 cm. One micron. The relationship is EXACT, not approximate.
# The rival "component space including the root bone" reading is excluded by orders of magnitude: under
# it the residual reaches -1059.86 cm by the end of the same clip.
#
# ☠️ A CORRECTION, AND THE GOTCHA UNDERNEATH IT. This header originally reported a
# "mirrored ~1.2 cm outboard offset, max 2.96 cm" on the mannequin and described the bake as deliberately
# NOT reproducing it. There was no such offset and therefore no such choice. The figure came from a
# throwaway probe using `AnimationLibrary.get_bone_pose_for_frame`, which APPLIES THE SKELETON'S BONE
# TRANSLATION RETARGETING MODE. On SK_Mannequin the FK leg chain (pelvis/thigh_l/calf_l/foot_l) is
# `AnimationScaled` while ik_foot_root/ik_foot_l/ik_foot_r are `Animation`, so the probe composed a
# SCALED chain and differenced it against an UNSCALED effector. Translation only, rotation untouched —
# which is exactly why the rotation numbers were right and the translation numbers were not.
#
# WHY THAT DOES NOT MAKE THIS BAKE WRONG, and the condition under which it would. Every bone this bake
# reads on Khaimera_Skeleton — root, pelvis, thigh_l, calf_l, foot_l, ik_foot_root, ik_foot_l/r — is
# `Animation` mode, so get_bone_pose_for_frame returns the authored data unmodified and the composition
# is exact. Verify.IKEffectorDrives independently confirms it: reading the RAW data model, it measures
# this bake's result at 0.0000 cm from the FK foot. POINTED AT A RIG WHOSE FK CHAIN USES AnimationScaled
# (SK_Mannequin itself, for instance) THIS BAKE WOULD BE SILENTLY WRONG BY A COUPLE OF CENTIMETRES.
# USkeleton's retargeting mode is not exposed to Python, so this file cannot assert the precondition —
# the guard that actually catches it is Verify.IKEffectorDrives, which reads the authoritative source and
# now bounds the result at 0.05 cm. If this script is ever aimed at a new rig, read that row's output
# rather than trusting this one.
#
# WHAT IS WRITTEN: the exact FK foot transform in ik_foot_root space, which makes the solve an IDENTITY
# on the deform foot so the authored stride survives the pass intact.
#
# ORDER IS LOAD-BEARING: the bake runs AFTER _pin_pelvis, never before. The pelvis is in foot_l's parent
# chain, so pinning it CHANGES the component-space foot path — the jog's foot_l Y excursion drops from
# 1363.4 cm to 128.6 cm. Baking first would key the effectors to the travelling foot and put them a
# metre in front of the in-place rig.
IK_PARENT = 'ik_foot_root'
IK_PAIRS = (('ik_foot_l', 'foot_l'), ('ik_foot_r', 'foot_r'))
# Round-trip bound for the write. The bake is exact by construction, so this catches a broken
# set_bone_track_keys round-trip (or a silent no-op write), not an authored tolerance.
IK_ROUNDTRIP_TOL_CM = 1e-3
# What "not degenerate" means, fixed from the WORKING reference before this ran: the smallest real
# vertical effector span measured on any mannequin locomotion clip is the walk's 18.75 cm, and the defect
# is 0.000. 5.0 sits 3.75x below the smallest real value.
IK_MIN_SPAN_CM = 5.0

# Bones sampled by verify() to prove the edit touched nothing but the pelvis translation. Chosen to cover
# both legs (the thing the earlier in-place edit was accused of breaking), the spine, an arm and the head.
WITNESS_BONES = ['foot_l', 'foot_r', 'calf_l', 'thigh_r', 'spine_01', 'hand_r', 'head', 'clavicle_l']
# The pelvis is the ONE bone this script rewrites, and it was once the one bone
# excluded from the per-key equality check: X sway and Z bob were claimed "unchanged" out of two aggregate
# numbers a human read off a log line. It is now compared per key like every witness bone, with Y — the
# axis that is DELIBERATELY changed — excluded, so the claim is a gate instead of a reading.
#
# THIS PAIR IS ALSO WHERE THE TRAVEL AXIS IS RECORDED, and it is the only place it is. The
# measured fact is that pelvis-local +Y is FORWARD on this rig, X is sway and Z is bob; the complement
# {x, z} is what must not move. A second constant `TRAVEL_AXIS = 'y'` used to state the same fact beside
# this one and was read by nothing — an ungated duplicate of a measurement, which is the shape that lets
# two statements of one fact drift apart. Deleted; the fact lives here, where a gate enforces it.
PELVIS_EQ_AXES = ('x', 'z')
EQ_TOL = 1e-4
# The bones this script DELIBERATELY rewrites on the duplicate, kept as an explicit list so the witness
# comparison's meaning stays honest as that list grows. `pelvis` (Y only, the pin) and the two ik_foot
# effectors (the bake) are edits; every OTHER bone must come back byte-identical. Note the effectors are
# NOT deform bones — nothing is skinned to them — so "the deform pose is unchanged" remains exactly true
# of the duplicate, which is why the witness set does not need to grow.
REWRITTEN_BONES = ('pelvis', 'ik_foot_l', 'ik_foot_r')


def _load(path):
    try:
        return unreal.load_asset(path)
    except Exception:
        return None


def _require_saved(path):
    """`EditorAssetLibrary.save_asset` returns bool and every call site here used to DISCARD it — in the one file whose headline gotcha is a write that succeeded silently at reporting and
    not at writing. A save that quietly failed would still read back correct from the in-memory object."""
    if not unreal.EditorAssetLibrary.save_asset(path, only_if_is_dirty=False):
        raise RuntimeError('save_asset returned False for ' + path)
    return True


def _assert_tracked(anim, bones, label):
    """UE 5.8's GetBonePosesForTimeInternal (AnimationBlueprintLibrary.cpp:98-106) returns the REFERENCE
    pose with only a Warning when the requested bone has no track. Original and duplicate would then both
    return the identical ref pose and the per-key delta would be 0.0 BY CONSTRUCTION — indistinguishable
    from "proven identical". So every bone the equality check relies on is asserted to
    have a real track first, and an untracked one fails loudly instead of contributing a vacuous zero."""
    have = {str(n) for n in anim.data_model_interface.get_bone_track_names()}
    missing = [b for b in bones if b not in have]
    if missing:
        raise RuntimeError('%s: no animation track for %s — a per-key equality check on these bones '
                           'would compare ref pose to ref pose and read 0.0 vacuously' % (label, missing))
    return sorted(bones)


def _keys(anim, bone):
    """Every local-space key of `bone`, in key order. `extract_root_motion=False` — the root track is
    identically zero on these clips, so this is the raw authored local transform."""
    n = anim.data_model_interface.get_number_of_keys()
    lib = unreal.AnimationLibrary
    return [lib.get_bone_pose_for_frame(anim, unreal.Name(bone), f, False) for f in range(n)]


def _pos_span(anim, bone):
    ks = _keys(anim, bone)
    xs = [k.translation.x for k in ks]
    ys = [k.translation.y for k in ks]
    zs = [k.translation.z for k in ks]
    return {'n': len(ks),
            'dx': round(max(xs) - min(xs), 3), 'dy': round(max(ys) - min(ys), 3),
            'dz': round(max(zs) - min(zs), 3),
            'first_y': round(ys[0], 3), 'last_y': round(ys[-1], 3),
            'net_travel_y': round(ys[-1] - ys[0], 3)}


# =====================================================================================================
# Effector geometry
# =====================================================================================================
def _chain_to_root(anim, bone):
    """[bone, parent, ..., root] for `bone` on this clip's skeleton."""
    return [str(n) for n in unreal.AnimationLibrary.find_bone_path_to_root(anim, unreal.Name(bone))]


def _component_keys(anim, bone, cache):
    """Component-space transform of `bone` at EVERY key, composed up the parent chain.
    UE bone convention: ComponentSpace = Local * ParentComponentSpace, which is what
    MathLibrary.compose_transforms(child, parent) computes."""
    n = anim.data_model_interface.get_number_of_keys()

    def local(b):
        if b not in cache:
            cache[b] = _keys(anim, b)
        return cache[b]

    out = [None] * n
    chain = _chain_to_root(anim, bone)
    for f in range(n):
        xf = None
        for b in chain:
            l = local(b)[f]
            xf = l if xf is None else unreal.MathLibrary.compose_transforms(xf, l)
        out[f] = xf
    return out


def _effector_targets(anim, cache=None):
    """For each (effector, foot) pair, the track the Control Rig expects: the FK foot expressed in
    ik_foot_root space, per key. Written in the general relative-transform form on purpose — on every
    rig measured `ik_foot_root` is keyed to an identity local transform under `root`, which makes this
    equal to root space, but nothing here depends on that staying true."""
    cache = {} if cache is None else cache
    n = anim.data_model_interface.get_number_of_keys()
    parent = _component_keys(anim, IK_PARENT, cache)
    out = {}
    for eff, foot in IK_PAIRS:
        fk = _component_keys(anim, foot, cache)
        out[eff] = [unreal.MathLibrary.make_relative_transform(fk[f], parent[f]) for f in range(n)]
    return out


def _effector_report(anim):
    """Per-effector health: the span actually stored, and how far it is from the FK foot it is supposed
    to carry. This is the same measurement Verify.IKEffectorDrives makes in C++, so the script and the
    automation row cannot drift apart silently."""
    cache = {}
    want = _effector_targets(anim, cache)
    rows = {}
    for eff, foot in IK_PAIRS:
        have = _keys(anim, eff)
        w = want[eff]
        drift = max(((h.translation.x - t.translation.x) ** 2
                     + (h.translation.y - t.translation.y) ** 2
                     + (h.translation.z - t.translation.z) ** 2) ** 0.5
                    for h, t in zip(have, w))
        rot = max(unreal.MathLibrary.quat_angular_distance(h.rotation, t.rotation) * 57.2957795
                  for h, t in zip(have, w))
        span = _pos_span(anim, eff)
        rows[eff] = {'span_dx': span['dx'], 'span_dy': span['dy'], 'span_dz': span['dz'],
                     'max_drift_from_%s_cm' % foot: round(drift, 5),
                     'max_rot_delta_deg': round(rot, 5),
                     'degenerate': max(span['dx'], span['dy'], span['dz']) < IK_MIN_SPAN_CM}
    return rows


# =====================================================================================================
# author
# =====================================================================================================
def _pin_pelvis(dup_path):
    """Rewrite the duplicate's pelvis track with Y pinned to key 0. Returns the before/after spans."""
    anim = _load(dup_path)
    if anim is None:
        raise RuntimeError('duplicate missing: ' + dup_path)
    before = _pos_span(anim, PELVIS)

    ks = _keys(anim, PELVIS)
    y0 = ks[0].translation.y
    pos = [unreal.Vector(k.translation.x, y0, k.translation.z) for k in ks]
    rot = [k.rotation for k in ks]
    scl = [k.scale3d for k in ks]

    ctrl = anim.controller
    ctrl.open_bracket(unreal.Text('MT in-place pelvis pin'), False)
    try:
        ok = ctrl.set_bone_track_keys(unreal.Name(PELVIS), pos, rot, scl, False)
    finally:
        ctrl.close_bracket(False)
    if not ok:
        raise RuntimeError('set_bone_track_keys returned False for ' + dup_path)

    _require_saved(dup_path)
    after = _pos_span(_load(dup_path), PELVIS)
    # I4, half one: the axes that must NOT move are asserted here at the aggregate level as well as per
    # key in verify(), so a broken round-trip fails at the point of the write rather than one step later.
    for axis in PELVIS_EQ_AXES:
        if abs(before['d' + axis] - after['d' + axis]) > EQ_TOL:
            raise RuntimeError('pelvis d%s changed %s -> %s in %s — only Y may move'
                               % (axis, before['d' + axis], after['d' + axis], dup_path))
    return before, after


def _bake_effectors(dup_path):
    """Rewrite the duplicate's ik_foot_l / ik_foot_r tracks so they carry the FK feet, which
    is what CR_Mannequin_FootIK solves onto. MUST run AFTER _pin_pelvis — see the IK_PAIRS header.

    Nothing is written to the ORIGINAL: this only ever opens `dup_path`, which lives under the gitignored
    Content/MT/Khaimera/InPlace/."""
    anim = _load(dup_path)
    if anim is None:
        raise RuntimeError('duplicate missing: ' + dup_path)

    # I3, applied to the effector bake: a bone with no track reads back as the REFERENCE pose with only a
    # Warning, so a missing ik_foot_l would be silently "baked" into nothing and every check below would
    # compare ref pose to ref pose. Assert the tracks exist first.
    _assert_tracked(anim, [IK_PARENT] + [b for pair in IK_PAIRS for b in pair], dup_path)

    before = _effector_report(anim)
    # The precondition this bake exists to fix. If the source is ALREADY driving its effectors then this
    # script is being pointed at the wrong content and would silently overwrite good data with its own
    # arithmetic — so say so rather than proceed.
    if not all(before[eff]['degenerate'] for eff, _f in IK_PAIRS):
        raise RuntimeError('%s already drives its ik_foot effectors (%s) — refusing to overwrite tracks '
                           'this bake was only ever meant to REPLACE when dead' % (dup_path, before))

    targets = _effector_targets(anim)
    ctrl = anim.controller
    ctrl.open_bracket(unreal.Text('MT in-place ik_foot effector bake'), False)
    try:
        for eff, _foot in IK_PAIRS:
            ts = targets[eff]
            ok = ctrl.set_bone_track_keys(unreal.Name(eff),
                                          [unreal.Vector(t.translation.x, t.translation.y, t.translation.z)
                                           for t in ts],
                                          [t.rotation for t in ts],
                                          [t.scale3d for t in ts], False)
            if not ok:
                raise RuntimeError('set_bone_track_keys returned False for %s on %s' % (eff, dup_path))
    finally:
        ctrl.close_bracket(False)

    _require_saved(dup_path)

    # Re-read the effectors FRESH off the asset rather than out of the list just written — the same
    # discipline GOTCHA-BS-A earned, where a struct-array edit reported success out of its own copy.
    after = _effector_report(_load(dup_path))
    for eff, foot in IK_PAIRS:
        d = after[eff]['max_drift_from_%s_cm' % foot]
        if d > IK_ROUNDTRIP_TOL_CM:
            raise RuntimeError('%s: %s is still %.5f cm from %s after the bake (bound %.5f) — the write '
                               'did not round-trip' % (dup_path, eff, d, foot, IK_ROUNDTRIP_TOL_CM))
        if after[eff]['degenerate']:
            raise RuntimeError('%s: %s is STILL degenerate after the bake (spans %.3f/%.3f/%.3f) — the '
                               'FK foot this was baked from does not move either'
                               % (dup_path, eff, after[eff]['span_dx'], after[eff]['span_dy'],
                                  after[eff]['span_dz']))
    return before, after


def author():
    out = {'action': 'author', 'clips': [], 'blendspace': None, 'abp': None}

    for src_name, dst_name in CLIPS:
        src, dst = SRC_DIR + src_name, DST_DIR + dst_name
        if unreal.EditorAssetLibrary.does_asset_exist(dst):
            unreal.EditorAssetLibrary.delete_asset(dst)      # rebuildable, not accreted
        if not unreal.EditorAssetLibrary.duplicate_asset(src, dst):
            raise RuntimeError('duplicate_asset failed: %s -> %s' % (src, dst))
        before, after = _pin_pelvis(dst)
        # ORDER IS LOAD-BEARING: the pelvis is in the foot's parent chain, so the effectors must be baked
        # from the POST-PIN component-space feet. See the IK_PAIRS header.
        ik_before, ik_after = _bake_effectors(dst)
        out['clips'].append({'src': src, 'dst': dst, 'pelvis_before': before, 'pelvis_after': after,
                             'effectors_before': ik_before, 'effectors_after': ik_after})

    # ---- blendspace ---------------------------------------------------------------------------------
    # Only the two travelling samples are re-pointed. Sample POSITIONS are untouched, so the serialised
    # triangulation the duplicate inherits stays valid (it indexes samples, not animations) — which is why
    # this needs no re-bake, unlike a from-scratch `create_blend_space` + `bake_blend_space`. The claim
    # is not left on that reasoning: the PIE probe reads the played BlendSpace out of the graph's own
    # weights, so an unbaked/empty triangulation would show up as an A-pose there.
    if unreal.EditorAssetLibrary.does_asset_exist(DST_BS):
        unreal.EditorAssetLibrary.delete_asset(DST_BS)
    if not unreal.EditorAssetLibrary.duplicate_asset(SRC_BS, DST_BS):
        raise RuntimeError('duplicate_asset failed: ' + SRC_BS)
    #
    # GOTCHA-BS-A (cost a whole probe run to find): INDEXING A UE STRUCT ARRAY FROM PYTHON YIELDS A COPY.
    # `for smp in samples: smp.set_editor_property('animation', ...)` mutates a throwaway and reports
    # success — the array is unchanged, `set_editor_property('sample_data', ...)` writes the originals
    # back, and nothing anywhere errors. The element must be assigned BACK into the array
    # (`samples[i] = smp`) before the array is written to the asset. Proven live:
    #     after struct set   -> arr[4] = Khai_MF_Unarmed_Jog_Fwd           (unchanged)
    #     after arr[4] = s   -> arr[4] = Khai_MF_Unarmed_Jog_Fwd_InPlace   (took)
    # The first version of this file had the bug AND reported the swap out of the Python list it had just
    # mutated, so its own log line said the swap had worked. What actually caught it is that the readback
    # below performs a FRESH `get_editor_property('sample_data')` on the UObject rather than re-reading the
    # Python list. It does NOT re-read the file: `unreal.load_asset` on an already-loaded package returns
    # the IN-MEMORY UObject and never hits disk. Reading the object property afresh is
    # sufficient for the copy-semantics bug; a save that silently failed is caught separately, by
    # `_require_saved`.
    bs = _load(DST_BS)
    swap = {SRC_DIR + s: DST_DIR + d for s, d in CLIPS}
    samples = bs.get_editor_property('sample_data')
    for i in range(len(samples)):
        smp = samples[i]
        cur = smp.get_editor_property('animation')
        cur_path = str(cur.get_path_name()).split('.')[0] if cur else None
        if cur_path in swap:
            smp.set_editor_property('animation', _load(swap[cur_path]))
            samples[i] = smp
    bs.set_editor_property('sample_data', samples)
    _require_saved(DST_BS)
    out['blendspace'] = {'path': DST_BS, 'samples_reloaded_from_disk': _bs_samples()}

    # ---- child ABP ----------------------------------------------------------------------------------
    # ABP_MT_Khaimera is a CHILD of the shared Template and holds its overrides as CDO variables, not as
    # graph nodes (`get_abp_variables` on it returns zero OWN variables). So the duplicate
    # needs exactly one CDO write. The four one-shot clip vars keep pointing at the untouched originals.
    if unreal.EditorAssetLibrary.does_asset_exist(DST_ABP):
        unreal.EditorAssetLibrary.delete_asset(DST_ABP)
    if not unreal.EditorAssetLibrary.duplicate_asset(SRC_ABP, DST_ABP):
        raise RuntimeError('duplicate_asset failed: ' + SRC_ABP)
    dup_bp = _load(DST_ABP)
    unreal.BlueprintEditorLibrary.compile_blueprint(dup_bp)
    cls = unreal.load_class(None, DST_ABP_CLASS)
    unreal.get_default_object(cls).set_editor_property('BS_Locomotion', _load(DST_BS))
    unreal.BlueprintEditorLibrary.compile_blueprint(dup_bp)      # must SURVIVE a compile, not precede one
    _require_saved(DST_ABP)
    out['abp'] = {'path': DST_ABP, 'class': DST_ABP_CLASS, 'cdo_after_compile': _cdo_read()}

    unreal.log('KHAIINPLACE ' + json.dumps(out))
    return out


def _bs_samples(path=None):
    """Sample -> animation, RE-LOADED from the asset. Never report a struct-array edit out of the Python
    list that was edited (GOTCHA-BS-A) — that list is a copy and will happily agree with itself."""
    bs = _load(path or DST_BS)
    if bs is None:
        return 'MISSING'
    rows = []
    for s in bs.get_editor_property('sample_data'):
        a = s.get_editor_property('animation')
        v = s.get_editor_property('sample_value')
        rows.append({'anim': str(a.get_path_name()).split('.')[0] if a else None,
                     'x': v.x, 'y': v.y})
    return rows


def _cdo_read():
    cls = unreal.load_class(None, DST_ABP_CLASS)
    if cls is None:
        return 'CLASS_MISSING'
    cdo = unreal.get_default_object(cls)
    o = {}
    for v in ('BS_Locomotion', 'Anim_Idle', 'Anim_Jump', 'Anim_Fall', 'Anim_Land'):
        try:
            val = cdo.get_editor_property(v)
            o[v] = str(val.get_path_name()).split('.')[0] if val else None
        except Exception as exc:
            o[v] = 'ERR:%s' % exc
    return o


# =====================================================================================================
# verify — the non-destructive claim and the "only the pelvis moved" claim, both measured
# =====================================================================================================
def verify():
    # RENAMED: the flag is set solely from abs(net_travel_y) > 100 on the two source
    # clips, so it is a coarse "this script did not eat the asset" canary and NOT a byte-integrity proof —
    # a source clip could lose every rotation key and it would still read True. The real integrity proof is
    # GoldenDiff.MTKhaimera plus the six Verify.ClipVaries.Khai_* tier rows. The name now says only what
    # the measurement supports.
    out = {'action': 'verify', 'originals_still_travel': True,
           '_originals_still_travel_scope': 'coarse canary: |net pelvis Y travel| > 100 cm on the two '
                                            'SOURCE clips only. Byte integrity is proven by '
                                            'GoldenDiff.MTKhaimera + Verify.ClipVaries.Khai_*, not here.',
           # RENAMED from `only_pelvis_changed`: the pelvis is no longer the only thing this
           # script rewrites — the two ik_foot effector tracks are baked too. The CLAIM is unchanged and
           # is still measured over the same eight witness bones plus the pelvis's two untouched axes:
           # every DEFORM bone comes back byte-identical. The effectors are not deform bones (nothing is
           # skinned to them), so the duplicate's visible pose is still the original's, key for key.
           'deform_pose_unchanged': True,
           'rewritten_bones': list(REWRITTEN_BONES),
           # The two halves of the effector claim. The duplicates must now DRIVE their effectors
           # (the fix), and the ORIGINALS must still be dead on exactly those tracks (the proof that the
           # fix was written non-destructively, asserted on the very bones the bake touches rather than
           # inferred from the fact that a different file path was opened).
           'duplicates_drive_effectors': True,
           'originals_effectors_still_degenerate': True,
           'clips': [], 'untreated': [], 'cdo': _cdo_read(),
           'blendspace_dup': _bs_samples(), 'blendspace_src': _bs_samples(SRC_BS)}
    # The duplicate must CONSUME the in-place clips and the original must NOT — GOTCHA-BS-A made both of
    # those silently false once, so both are asserted rather than eyeballed.
    dup_anims = [r['anim'] for r in out['blendspace_dup']] if isinstance(out['blendspace_dup'], list) else []
    src_anims = [r['anim'] for r in out['blendspace_src']] if isinstance(out['blendspace_src'], list) else []
    out['dup_bs_uses_inplace'] = all((DST_DIR + d) in dup_anims for _s, d in CLIPS)
    out['src_bs_unchanged'] = all((SRC_DIR + s) in src_anims for s, _d in CLIPS)

    for src_name, dst_name in CLIPS:
        src, dst = _load(SRC_DIR + src_name), _load(DST_DIR + dst_name)
        rec = {'src': src_name, 'dst': dst_name}
        if src is None or dst is None:
            rec['status'] = 'MISSING'
            out['clips'].append(rec)
            out['originals_still_travel'] = False
            continue
        rec['src_pelvis'] = _pos_span(src, PELVIS)
        rec['dst_pelvis'] = _pos_span(dst, PELVIS)
        # The original must STILL travel — if it does not, this script ate the asset the tiers depend on.
        rec['original_still_travels'] = abs(rec['src_pelvis']['net_travel_y']) > 100.0
        rec['duplicate_is_in_place'] = abs(rec['dst_pelvis']['dy']) < 0.01
        if not rec['original_still_travels']:
            out['originals_still_travel'] = False

        # I3: prove every compared bone HAS a track before comparing, or a 0.0 delta is vacuous.
        # I4: the pelvis — the one bone actually rewritten — is now in the per-key comparison too, on the
        # two axes that must not move. Its Y is excluded because Y is the edit.
        compare = WITNESS_BONES + [PELVIS]
        rec['tracked_bones_asserted'] = _assert_tracked(src, compare, src_name)
        _assert_tracked(dst, compare, dst_name)

        worst = 0.0
        worst_bone = None
        for bone in compare:
            a, b = _keys(src, bone), _keys(dst, bone)
            if len(a) != len(b):
                worst, worst_bone = 9e9, bone + ':KEYCOUNT'
                break
            axes = PELVIS_EQ_AXES if bone == PELVIS else ('x', 'y', 'z')
            for ka, kb in zip(a, b):
                d = max([abs(getattr(ka.translation, ax) - getattr(kb.translation, ax)) for ax in axes]
                        + [abs(ka.rotation.x - kb.rotation.x), abs(ka.rotation.y - kb.rotation.y),
                           abs(ka.rotation.z - kb.rotation.z), abs(ka.rotation.w - kb.rotation.w)])
                if d > worst:
                    worst, worst_bone = d, bone
        rec['witness_bones'] = compare
        rec['pelvis_axes_compared'] = list(PELVIS_EQ_AXES)
        rec['max_witness_delta'] = round(worst, 8)
        rec['witness_worst_bone'] = worst_bone
        rec['witness_identical'] = worst <= EQ_TOL
        if not rec['witness_identical']:
            out['deform_pose_unchanged'] = False

        # ---- The effector claim, both halves, measured on the tracks the bake writes -----
        rec['src_effectors'] = _effector_report(src)
        rec['dst_effectors'] = _effector_report(dst)
        # The duplicate must DRIVE its effectors and they must TRACK the FK feet. A non-degenerate
        # effector baked in the wrong space would sail through a variance check and then move the feet
        # confidently to the wrong places, so the drift bound is asserted too, not just the span.
        rec['duplicate_drives_effectors'] = all(
            (not rec['dst_effectors'][eff]['degenerate'])
            and rec['dst_effectors'][eff]['max_drift_from_%s_cm' % foot] <= IK_ROUNDTRIP_TOL_CM
            for eff, foot in IK_PAIRS)
        # The ORIGINAL must still be dead on exactly those tracks — this is the non-destructiveness proof
        # aimed at the bones the bake rewrites, rather than at the pelvis the pin rewrote.
        rec['original_effectors_still_degenerate'] = all(
            rec['src_effectors'][eff]['degenerate'] for eff, _f in IK_PAIRS)
        if not rec['duplicate_drives_effectors']:
            out['duplicates_drive_effectors'] = False
        if not rec['original_effectors_still_degenerate']:
            out['originals_effectors_still_degenerate'] = False
        out['clips'].append(rec)

    for name in UNTREATED:
        a = _load(SRC_DIR + name)
        out['untreated'].append({'clip': name, 'pelvis': _pos_span(a, PELVIS) if a else 'MISSING'})

    out['PASS'] = bool(out['originals_still_travel'] and out['deform_pose_unchanged']
                       and out['duplicates_drive_effectors']
                       and out['originals_effectors_still_degenerate']
                       and out['dup_bs_uses_inplace'] and out['src_bs_unchanged']
                       and out['cdo'].get('BS_Locomotion') == DST_BS
                       and all(c.get('duplicate_is_in_place') for c in out['clips']))
    unreal.log('KHAIINPLACE ' + json.dumps(out))
    return out


def report():
    o = {'action': 'report', 'exists': {}, 'cdo': _cdo_read(), 'effectors': {}}
    for p in [DST_DIR + d for _s, d in CLIPS] + [DST_BS, DST_ABP]:
        o['exists'][p] = bool(unreal.EditorAssetLibrary.does_asset_exist(p))
    # Effector health for BOTH sides of every pair, so one call shows the fix and its non-destructiveness.
    for p in [SRC_DIR + s for s, _d in CLIPS] + [DST_DIR + d for _s, d in CLIPS]:
        a = _load(p)
        o['effectors'][p] = _effector_report(a) if a is not None else 'MISSING'
    o['blendspace_dup'] = _bs_samples()
    o['blendspace_src'] = _bs_samples(SRC_BS)
    unreal.log('KHAIINPLACE ' + json.dumps(o))
    return o


# POP, do not get — run_python's namespace is persistent and a `get` would inherit the previous call's
# action whenever this one forgot to set it (the stale-ARMY_COUNTS gotcha in army_spawn.py).
_action = globals().pop('KIP_ACTION', None)
if _action == 'author':
    author()
elif _action == 'verify':
    verify()
elif _action == 'report':
    report()
elif _action is not None:
    raise RuntimeError('KIP_ACTION is %r — use author/verify/report' % (_action,))
