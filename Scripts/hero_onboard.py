"""Onboard any hero: from its imported mesh (and intake profile) to the assets the dynamics pipeline drives.

    python Scripts/hero_onboard.py --hero <Rig>                    # every stage, in order
    python Scripts/hero_onboard.py --hero <Rig> --stage inplace    # that stage and every later one
    python Scripts/hero_onboard.py --hero <Rig> --resume           # keep the stages already done from the same inputs
    python Scripts/hero_onboard.py --hero <Rig> --check            # verify only (audits a hero onboarded by hand)
    python Scripts/hero_onboard.py --hero <Rig> --status           # where the hero stands (no editor needed)
    ... --allow-external-referencers    # delete and rebuild assets a package outside the hero's folder references
                                        # (the referencers are named in the stage record; re-point them after)

Run from a normal shell (PowerShell: Git Bash rewrites /Game paths) with the editor up. Assets land under
/Game/MT/<short>/ (git-ignored), named by dyn_hero's convention. Spec: docs/specs/2026-10-29-hero-onboarding-design.md;
the layers: docs/specs/2026-11-12-asset-pipeline-design.md.
No rig name appears here.

The stages (layer 3, stage_runner): preflight, ik_rig, retargeter, bake, locomotion, inplace, pawn, physics, verify
(`--stage retarget` is ik_rig). Each declares the stages whose records it reads and what else decides its result; the
runner keys each record by both, writes the record file (h['onboard_record'], `_check` for --check) after every stage,
and on a failure leaves it saying which stage failed and why. Stages hand each other records, never locals. Physics
and verify are never kept by --resume: their inputs carry an id made on every call of onboard(). The runner's
prepare and finish hooks (inside its lock) drop the last verify before a run that rebuilds anything and write the
summary other scripts read (summary_of: status, gaps, unsupported, has_foot_ik) under stage_runner.SUMMARY_KEY. Nothing
here writes the record file itself.

Every generated asset with a spec goes through asset_pipeline.realize (layer 2): refuse a tracked target, delete,
build, describe with a separate read, compare (hero_onboard_core, pure), save only on a match, delete on any failure.
The kinds are in hero_kinds. The bake, in-place and pawn steps (engine batch and duplicate operations) have their
outputs computed here BEFORE they run, deleted here first and again on any failure.
"""
import json
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import asset_pipeline as ap  # noqa: E402
import dyn_hero  # noqa: E402
import hero_kinds as kinds  # noqa: E402
import hero_onboard_core as core  # noqa: E402
import stage_runner as sr  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANIM_TOOLS = kinds.ANIM_TOOLS
# The leg and IK bones whose translation-retargeting mode must be Animation for the effector bake to be exact: in any
# other mode get_bone_pose_for_frame returns scaled translations and the bake is off by centimetres
# (khai_inplace_author.py IK_PAIRS header). Read, not assumed.
EXACT_MODE = 'Animation'
# What the template can do that onboarding does not carry over: stated in every record so a green onboarding is never
# read as covering them.
UNSUPPORTED = ['directional locomotion: the template binds Speed only, so only Direction = 0 samples play',
               "the template's rifle aim offset and ADS pose stay the mannequin's (rifle mode off)",
               'clips between keys and blends are not checked for effector exactness (keys only)']
# The summary's fields: kept under stage_runner.SUMMARY_KEY; a record file from before that key has them at the top.
SUMMARY_KEYS = ('hero', 'finished', 'status', 'gaps', 'unsupported', 'has_foot_ik', 'failed_stage', 'error')

physics_mismatch = core.physics_mismatch          # read by hit_reaction_run, dynamics_graph, the tests


def _ops():
    import editor_ops
    return editor_ops.shared()


# --- reads (each a separate read of what is in the editor) -------------------------------------------------------

def describe_skeleton(path, ops=None):
    """MotionTerminal.AnimAssetTools.DescribeSkeleton: every bone with its index, parent and translation-retargeting
    mode, in one read. Raises on a failed read (core.skeleton_parents)."""
    reply = (ops or _ops()).call(ANIM_TOOLS, 'DescribeSkeleton', SkeletonPath=path)
    core.skeleton_parents(reply)
    return reply


def describe_blend_space(path, ops=None):
    """MotionTerminal.AnimAssetTools.DescribeBlendSpace, refused when it says ok false."""
    reply = (ops or _ops()).call(ANIM_TOOLS, 'DescribeBlendSpace', AssetPath=path)
    if not reply.get('ok'):
        raise RuntimeError('DescribeBlendSpace %s: %s' % (path, reply.get('error')))
    return reply


def describe_physics(path):
    return kinds.describe_physics(_ops(), path)


def _skeleton(h):
    return core.skeleton_parents(describe_skeleton(h['skeleton']))


def _template_class():
    return dyn_hero.TEMPLATE_ABP + '.' + dyn_hero.TEMPLATE_ABP.rsplit('/', 1)[1] + '_C'


def _template(ops=None):
    """(the template ABP's CDO {clip var and BS_VAR: object path or None}, its blend space as DescribeBlendSpace reads
    it)."""
    tpl_cls = _template_class()
    cdo = (ops or _ops()).py(
        "import json\n"
        "c = unreal.get_default_object(unreal.load_class(None, %r))\n"
        "o = {}\n"
        "for v in %r:\n"
        "    x = c.get_editor_property(v)\n"
        "    o[v] = str(x.get_path_name()) if x else None\n"
        "json.dumps(o)\n" % (tpl_cls, core.TEMPLATE_CLIP_VARS + (core.BS_VAR,)))
    if not isinstance(cdo, dict) or not cdo.get(core.BS_VAR):
        raise RuntimeError('template %s CDO read back %r' % (tpl_cls, cdo))
    return cdo, describe_blend_space(cdo[core.BS_VAR].split('.')[0], ops)


def _ref_tms(h):
    """{bone: [x, y, z, qx, qy, qz, qw]}: the skeleton's reference pose in component space, every bone, via the editor's
    AnimPose API (UAnimPoseExtensions::GetReferencePose then GetBonePose in World space, which AnimPose.h defines as
    component space). Transforms, not positions: an aimed capsule is resolved in its owner bone's reference frame
    (hit_reaction_core.resolve_aims). The builder reads the MESH's reference skeleton; for a mesh that does not share
    its skeleton's reference pose the two would differ (the shape read-back, physics_mismatch, then says so)."""
    # No comprehension or lambda in an editor script: the server may run it with separate globals and locals, where a
    # comprehension cannot see the script's own top-level names (dynamics_graph.py, editor reads).
    got = _ops().py(
        "import json\n"
        "pose = unreal.AnimPoseExtensions.get_reference_pose(unreal.load_asset(%r))\n"
        "d = {}\n"
        "for b in unreal.AnimPoseExtensions.get_bone_names(pose):\n"
        "    t = unreal.AnimPoseExtensions.get_bone_pose(pose, b, unreal.AnimPoseSpaces.WORLD)\n"
        "    l, q = t.translation, t.rotation\n"
        "    d[str(b)] = [l.x, l.y, l.z, q.x, q.y, q.z, q.w]\n"
        "json.dumps(d)\n" % h['skeleton'])
    if not isinstance(got, dict) or not got:
        raise RuntimeError('reference pose of %s: no bones (%r)' % (h['skeleton'], str(got)[:200]))
    return got


IDLE_SAMPLES = 12


def _idle_tms(h, bones):
    """[{bone: [x, y, z, qx, qy, qz, qw]}]: component-space samples across the hero's idle, the locomotion blend
    space's speed-0 sample (the stand every take opens on), via the editor's AnimPose evaluation."""
    info = describe_blend_space(h['bs'])
    axis = 'x' if info['axis_x']['name'] == 'Speed' else 'y'
    idle = min(info['samples'], key=lambda s: abs(s[axis]))['animation'].split('.')[0]
    got = _ops().py(
        "import json\n"
        "seq = unreal.load_asset(%r)\n"
        "n, bones = %d, %r\n"
        "length = seq.get_play_length()\n"
        "opts = unreal.AnimPoseEvaluationOptions()\n"
        "out = []\n"
        "for i in range(n):\n"
        "    pose = unreal.AnimPoseExtensions.get_anim_pose_at_time(seq, length * i / n, opts)\n"
        "    d = {}\n"
        "    for b in bones:\n"
        "        t = unreal.AnimPoseExtensions.get_bone_pose(pose, b, unreal.AnimPoseSpaces.WORLD)\n"
        "        l, q = t.translation, t.rotation\n"
        "        d[b] = [l.x, l.y, l.z, q.x, q.y, q.z, q.w]\n"
        "    out.append(d)\n"
        "json.dumps({'idle': %r, 'samples': out})\n" % (idle, IDLE_SAMPLES, sorted(bones), idle), timeout=600)
    if not isinstance(got, dict) or 'samples' not in got:
        raise RuntimeError('idle %s: the sampling returned %r' % (idle, str(got)[:200]))
    if len(got['samples']) != IDLE_SAMPLES or any(set(s) != set(bones) for s in got['samples']):
        raise RuntimeError('idle %s: sampled %d poses, wanted %d of %s' % (idle, len(got['samples']), IDLE_SAMPLES,
                                                                          sorted(bones)))
    return got['samples']


def reaction_spec(h, parents, ref=None):
    """The hero's reaction body: the spec's bodies first (every shape explicit in its bone's frame, from the reference
    pose ref, read when not given), then the idle sampled at their bones, then the spec again with the arm-torso pairs
    the idle and the reference pose keep apart."""
    import hit_reaction_core
    ref = ref or _ref_tms(h)
    first = hit_reaction_core.physics_spec(h['profile'], parents, ref)
    idle = _idle_tms(h, {b['bone'] for b in first['bodies']})
    return hit_reaction_core.physics_spec(h['profile'], parents, ref, idle)


def _physics_gaps(h, parents):
    """The reaction body, and for a hero with clothing its cloth-collision bodies, against specs made now."""
    import hit_reaction_core
    if not h['profile'].get('roles', {}).get('head'):
        return []
    ref = _ref_tms(h)
    gaps = physics_mismatch(reaction_spec(h, parents, ref), describe_physics(h['physics_asset']))
    if h['profile'].get('stock', {}).get('clothing_assets'):
        gaps += ['cloth collision: %s' % g for g in physics_mismatch(
            hit_reaction_core.cloth_collision_spec(h['profile'], ref), describe_physics(h['cloth_collision_asset']))]
    return gaps


def _has_ik(h, skel):
    """Whether the template's foot-IK pass will run on this hero (it does when the skeleton has ik_foot_root:
    HasMannequinFootIKRig in PieTests.cpp), and, if so, that the effector bake will be exact. skel: the
    DescribeSkeleton reply (parents and translation-retargeting modes from the same read)."""
    parents = core.skeleton_parents(skel)
    if 'ik_foot_root' not in parents:
        return False
    roles = h['profile']['roles']
    bones = ['root', roles['pelvis'], 'ik_foot_root', 'ik_foot_l', 'ik_foot_r'] + [
        roles[k] for k in ('thigh_l', 'thigh_r', 'calf_l', 'calf_r', 'foot_l', 'foot_r')]
    # Every ancestor of both compositions (effector -> ik_foot_root -> root, foot -> ... -> pelvis -> root).
    for b in list(bones):
        p = parents.get(b)
        while p:
            if p not in bones:
                bones.append(p)
            p = parents.get(p)
    modes, missing = core.translation_modes(skel, bones)
    # A bone the read does not cover must fail, not pass as having no mode: check the set, not just the modes
    if missing or sorted(modes) != sorted(set(bones)):
        raise RuntimeError('translation-retargeting read covered %s of %s (not found %s)'
                           % (len(modes), len(set(bones)), missing))
    bad = ['%s=%s' % (b, m) for b, m in sorted(modes.items()) if m != EXACT_MODE]
    if bad:
        raise RuntimeError('effector bake would be inexact: translation retargeting %s (needs %s)' % (bad, EXACT_MODE))
    return True


def travel_bones(roles, parents):
    """The skeleton's root (the bone with no parent) and its pelvis role: where locomotion's travel can live."""
    roots = [b for b, p in parents.items() if not p]
    if len(roots) != 1:
        raise RuntimeError('skeleton has %d root bones: %s' % (len(roots), roots))
    return [roots[0], roles['pelvis']]


def _witness(roles):
    """The deform bones the in-place check compares, original against duplicate: both legs, the spine, the arms and
    the head (the pin rewrites only the root and pelvis translation; the effectors are not deform bones)."""
    keys = ('foot_l', 'foot_r', 'calf_l', 'calf_r', 'thigh_l', 'thigh_r', 'hand_l', 'hand_r', 'clavicle_l',
            'clavicle_r', 'head')
    return [roles[k] for k in keys if roles.get(k)] + list(roles.get('spine') or [])


# --- context and the steps every stage shares --------------------------------------------------------------------

class Ctx:
    """What a stage is run with: the resolved hero `h`, the editor (`ops`, opened at first use), the record file's
    path, and what onboard() sets on every call: `invocation` (the id verify's and physics' inputs carry, so neither
    is ever kept by --resume) and `pending_bake_cleanup` (a failed bake's cleanup that could not reach the editor,
    read back from the record file). `reads()`: the reads preflight is made from, once per call of onboard().
    allow_external_referencers: _clear deletes an asset a package outside the hero's folder references, and records
    the referencers, instead of refusing."""

    def __init__(self, h, record_path, ops=None, allow_external_referencers=False):
        self.h, self.record_path, self._ops, self._reads = h, record_path, ops, None
        self.allow_external_referencers = allow_external_referencers
        self.invocation = None
        self.pending_bake_cleanup = None
        self.external = {}            # {path: [referencer]} the running stage deleted past (allow_external_referencers)
        self.failed_external = {}     # the same, of a stage that then failed (finish puts it on the failure entry)

    @property
    def ops(self):
        if self._ops is None:
            self._ops = _ops()
        return self._ops

    def reads(self):
        if self._reads is None:
            cdo, bs = _template(self.ops)
            src = _editor(self, 'ik_rig_describe', {'path': dyn_hero.SOURCE_IK_RIG})
            clips = core.source_clips(cdo, bs['samples'])            # raises when the template supplies none
            self._reads = {'skeleton': describe_skeleton(self.h['skeleton'], self.ops), 'source_rig': src,
                           'source_root': _source_root(self, src),
                           'template': {'cdo': cdo, 'bs': bs,
                                        'clip_kinds': _editor(self, 'clip_kinds', {'paths': clips})['kinds']}}
        return self._reads


def _invocation(ctx):
    if not ctx.invocation:
        raise RuntimeError('no invocation id: stages run through onboard(), which sets one on every call')
    return ctx.invocation


def _source_root(ctx, src):
    """{'bone', 'from'}: the Root Motion op's source root as the op picks it (core.source_root_motion); the source
    preview mesh's skeleton is read only when the source rig names no root-motion bone."""
    skel = None
    if not src.get('root_motion') and src.get('mesh_skeleton'):
        skel = describe_skeleton(src['mesh_skeleton'], ctx.ops)
    bone, how = core.source_root_motion(src, skel)
    return {'bone': bone, 'from': how}


def _editor(ctx, action, args=None, timeout=900):
    return kinds.editor(ctx.ops, ctx.h['rig'], action, args, timeout)


def _own(h):
    return '/Game/MT/%s/' % h['short']


def referencer_refusal(own, outside):
    """The message a delete refused for its outside referencers gives: what references what, and the ways on."""
    named = '; '.join('%s <- %s' % (p, ', '.join(refs)) for p, refs in sorted(outside.items()))
    return ('referenced outside %s: %s. Deleting and rebuilding them would leave those packages holding a dead '
            'object. To proceed: delete the referencing package, or open it, remove or re-point the reference and '
            're-save it; or rerun with --allow-external-referencers, which deletes anyway, names the referencers in '
            'the stage record, and leaves re-pointing them (a placed pawn in a saved level, a GameMode) to you'
            % (own, named))


def _clear(ctx, paths):
    """Delete `paths` with the one host-side delete (asset_pipeline.delete_asset: loaded in one call, deleted in the
    next, gone from the registry AND the disk), after refusing any that something OUTSIDE the hero's folder
    references (it would be left holding a dead object; referencer_refusal says how to proceed). With
    ctx.allow_external_referencers the delete proceeds and the referencers go into ctx.external, which the stage's
    record carries (_with_referencers). The guard reads saved references (the asset registry), not live unsaved ones."""
    paths = [p for p in dict.fromkeys(paths)]
    outside = _editor(ctx, 'referenced_outside', {'paths': paths, 'own': _own(ctx.h)})['outside']
    if outside:
        if not ctx.allow_external_referencers:
            raise RuntimeError(referencer_refusal(_own(ctx.h), outside))
        for p, refs in outside.items():
            ctx.external[p] = sorted(set(ctx.external.get(p, [])) | set(refs))
        print('HERO_ONBOARD deleting past outside referencers (--allow-external-referencers): %s'
              % json.dumps(outside, sort_keys=True), flush=True)
    for p in paths:
        ap.delete_asset(ctx.ops, p)


def _with_referencers(fn):
    """A stage function whose record names the outside referencers it deleted past, when there were any."""
    def run(ctx, records):
        ctx.external, ctx.failed_external = {}, {}
        try:
            rec = fn(ctx, records)
        except Exception:
            # deleted past them and then failed: the failure record names them too (onboard's finish hook)
            ctx.failed_external = dict(ctx.external)
            raise
        if ctx.external:
            rec = dict(rec, external_referencers=dict(ctx.external))
        return rec
    run.__name__, run.__doc__ = fn.__name__, fn.__doc__
    return run


def _cleanup(ctx, paths):
    """_clear after a failure: its own failure is printed, never raised over the one being reported."""
    try:
        _clear(ctx, paths)
    except Exception as exc:          # noqa: BLE001 - the stage's own error is the one to raise
        print('HERO_ONBOARD the outputs of a failed step were not deleted (%s): %s' % (sorted(paths), str(exc)[:300]),
              flush=True)


def _realize(ctx, kind, path, spec):
    return ap.realize(ctx.ops, kind, path, spec, delete=lambda ops, p: _clear(ctx, [p]))


def _gitignored(*paths):
    """Every target of a stage, asked BEFORE the stage's first delete (asset_pipeline.require_gitignored)."""
    for p in paths:
        ap.require_gitignored(p)


def _twin(h, template_clip):
    return core.twin(h['retarget_dir'], h['clip_prefix'], template_clip)


def _locomotion_abp_spec(h, cdo, clip):
    """ABP_MT_<S>: a child of the template, its clip variables at the hero's twins (clip(template clip)), its blend
    space the hero's."""
    values = {v: clip(cdo[v]) for v in core.TEMPLATE_CLIP_VARS if cdo.get(v)}
    values[core.BS_VAR] = h['bs']
    return core.abp_spec(h['abp_main'], h['abp_main_class'], _template_class(), h['skeleton'], values)


def _inplace_abp_spec(h, cdo):
    """The in-place ABP: the same child of the template, its clip variables at the in-place twins, its blend space the
    in-place one."""
    values = {v: core.inplace_clip(h['inplace_dir'], _twin(h, cdo[v])) for v in core.TEMPLATE_CLIP_VARS if cdo.get(v)}
    values[core.BS_VAR] = h['bs_inplace']
    return core.abp_spec(h['inplace_path'], h['anim_class'], _template_class(), h['skeleton'], values)


def pawn_mismatch(h, read):
    """[difference] between the hero's test pawn as read back and what it must be: the profile mesh, the in-place ABP
    (the one verify checks), cloth simulation not disabled."""
    out = []
    if read.get('mesh') != h['mesh']:
        out.append('pawn mesh %s is not the profile mesh %s' % (read.get('mesh'), h['mesh']))
    if read.get('anim_class') != h['anim_class']:
        out.append('pawn plays %s, not the verified in-place ABP %s' % (read.get('anim_class'), h['anim_class']))
    if read.get('cloth_disabled'):
        out.append('pawn mesh has cloth simulation disabled')
    return out


# --- the stages ----------------------------------------------------------------------------------------------------

def _preflight_inputs(ctx):
    h, prof = ctx.h, ctx.h['profile']
    return {'profile': {'status': prof.get('status'), 'roles': prof.get('roles'), 'other_bodies': prof.get('other_bodies'),
                        'confirmed': ((prof.get('overrides') or {}).get('onboard.body_confirmed') or {}).get('value')},
            'assets': {'skeleton': h['skeleton'], 'mesh': h['mesh'], 'short': h['short'],
                       'source_ik_rig': dyn_hero.SOURCE_IK_RIG, 'template': dyn_hero.TEMPLATE_ABP},
            # what was read, by content: a changed skeleton, template or source rig changes every key after it
            'reads': ap.digest(ctx.reads())}


def preflight(ctx, records):
    """Everything later stages are made from, read and checked before anything is deleted: the profile is onboardable,
    the hero's folder is git-ignored, the retarget chains resolve on the skeleton, the template's (SOURCE) IK rig
    has no chain problem (errors and chain warnings refuse it; its other warnings, about goals and solvers the
    retargeter does not use, are recorded as notes), the template supplies clips and every one is a plain sequence
    (an additive one is refused here, before any stage deletes anything), and the source rig's root-motion bone (the
    Root Motion op's source root, core.source_root_motion) is recorded with where it came from."""
    h, prof = ctx.h, ctx.h['profile']
    if prof.get('status') != 'ok':
        raise RuntimeError('%s intake status %r: %s' % (h['rig'], prof.get('status'), prof.get('reasons')))
    dyn_hero.check_unique_shorts()
    # A rig with a second body (an embedded rider or ghost form) has its roles taken from the shallowest pelvis; a
    # complete, equally deep second body would be picked just as happily.
    # Onboarding such a rig needs the body confirmed by hand, as a counted profile override.
    confirmed = ((prof.get('overrides') or {}).get('onboard.body_confirmed') or {}).get('value')
    if prof.get('other_bodies') and confirmed != prof['roles'].get('pelvis'):
        # the override must NAME the chosen pelvis: a 'false' or a stale name is no confirmation
        raise RuntimeError('%s has other bodies %s: confirm the chosen body with a profile override '
                           "'onboard.body_confirmed' whose value is its pelvis %r (have %r)"
                           % (h['rig'], prof['other_bodies'], prof['roles'].get('pelvis'), confirmed))
    ap.require_gitignored(_own(h) + 'x')
    reads = ctx.reads()
    parents = core.skeleton_parents(reads['skeleton'])
    chains = core.retarget_chains(prof['roles'], parents)
    src = reads['source_rig']
    bad = core.source_rig_problems(src)
    if bad:
        raise RuntimeError('; '.join(bad))
    if not src.get('mesh'):
        raise RuntimeError('the source IK rig %s has no preview mesh' % dyn_hero.SOURCE_IK_RIG)
    tpl = reads['template']
    core.source_clips(tpl['cdo'], tpl['bs']['samples'])            # raises when the template supplies none
    additive = core.additive_problems(tpl['clip_kinds'])
    if additive:
        raise RuntimeError('; '.join(additive))
    travel = travel_bones(prof['roles'], parents)
    return {'chains': [list(c) for c in chains], 'pelvis': prof['roles']['pelvis'], 'root': travel[0],
            'travel_bones': travel, 'has_foot_ik': _has_ik(h, reads['skeleton']),
            'source_rig': {'path': dyn_hero.SOURCE_IK_RIG, 'mesh': core.package(src['mesh']), 'chains': src['chains'],
                           'notes': core.source_rig_notes(src), 'root_motion': reads['source_root']['bone'],
                           'root_motion_from': reads['source_root']['from']},
            'template': tpl}


def ik_rig(ctx, records):
    """IK_MT_<S>: the chains from roles, the retarget root (the pelvis) and the root-motion bone (the skeleton's
    root), through realize. The retargeter references the rig, so it is deleted first."""
    h, pre = ctx.h, records['preflight']
    _gitignored(h['ik_rig'], h['retargeter'])
    _clear(ctx, [h['retargeter']])
    spec = core.ik_rig_spec(h['ik_rig'], h['mesh'], pre['pelvis'], pre['chains'], pre['root'])
    return _realize(ctx, kinds.ik_rig(h['rig']), h['ik_rig'], spec)


def retargeter(ctx, records):
    """RTG_MT_<template>_to_<S>: each target chain paired with the template IK rig's same-named chain in every op that
    keeps a map, the FK op set up with every chain at the default settings, the Pelvis Motion and Root Motion ops on
    the rig's bones (the Root Motion op's source root the one preflight recorded for the source rig), validated
    against the template's mesh and the hero's, through realize."""
    h, pre, rig = ctx.h, records['preflight'], records['ik_rig']
    src = pre['source_rig']
    _gitignored(h['retargeter'])
    spec = core.retargeter_spec(h['retargeter'], dyn_hero.SOURCE_IK_RIG, rig['path'], pre['pelvis'],
                                [c[0] for c in pre['chains']], [c[0] for c in src['chains']], pre['root'], src['mesh'],
                                src.get('root_motion'), core.single_bone_chains(pre['chains'], src['chains']))
    return _realize(ctx, kinds.retargeter(h['rig']), h['retargeter'], spec)


def _disk_assets(folder, repo=ap.REPO):
    """The /Game package paths of every .uasset on disk under the Content folder matching `folder` (/Game/...)."""
    root = os.path.dirname(ap.content_file(folder.rstrip('/') + '/x', repo))
    content = os.path.join(repo, 'Content')
    out = []
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            if f.endswith('.uasset'):
                rel = os.path.relpath(os.path.join(dirpath, f[:-len('.uasset')]), content)
                out.append('/Game/' + rel.replace('\\', '/'))
    return sorted(out)


def _bake_inventory(ctx, folder):
    """The bake folder as it stands: the asset registry's listing (unsaved in-memory assets included) and the
    .uasset files on disk, together (core.bake_inventory)."""
    return core.bake_inventory(_editor(ctx, 'inventory', {'folder': folder})['assets'], _disk_assets(folder))


def _bake_cleanup(ctx, pending, after):
    """Delete what a failed bake left (core.bake_leftovers: the expected destinations, what the batch reported it made
    unasked, and everything in `after` that was not in the inventory taken before the bake), each only when inside
    the hero's folder and git-ignored NOW (_deletable, asked again here, the expected destinations included). Raises
    when it cannot. after: the inventory taken right after the failure (registry and disk); for a cleanup deferred to
    a later run, the disk listing frozen at the failure (pending['disk_after']), never a fresh inventory, which would
    take whatever was added to the folder since for the bake's."""
    expected = list(pending['expected'])
    gone = core.bake_leftovers(expected, pending['before'], after, pending.get('made') or [])
    _clear(ctx, _deletable(ctx.h, expected + [p for p in gone if p not in set(expected)]))


def bake(ctx, records):
    """The template's clips retargeted onto the hero (hero_onboard_editor.bake). Every destination is known before the
    bake runs (core.bake_destinations): deleted first, refused by the editor action while one exists, and deleted
    again on ANY failure, together with whatever the batch made that was not asked for: what its reply names, and
    what the folder holds after the failure that its inventory (registry and disk) did not hold before, which covers
    a call that died or raised while decoding its results. When that cleanup cannot reach the editor, the inventory
    and the cleanup still owed go into the stage's failure record (onboard's finish hook), and the next bake does
    them first. The editor saves the clips only once every one passed the batch check and its motion check."""
    h, pre = ctx.h, records['preflight']
    tpl = pre['template']
    clips = core.source_clips(tpl['cdo'], tpl['bs']['samples'])
    dests = core.bake_destinations(clips, h['retarget_dir'], h['clip_prefix'])
    _gitignored(*dests.values())
    if ctx.pending_bake_cleanup:
        # a failed bake's cleanup that could not run then: first, before this bake's inventory is taken, from what
        # was frozen at that failure
        _bake_cleanup(ctx, ctx.pending_bake_cleanup, ctx.pending_bake_cleanup.get('disk_after') or [])
        ctx.pending_bake_cleanup = None
    _clear(ctx, list(dests.values()))
    before = _bake_inventory(ctx, h['retarget_dir'])
    made = []
    try:
        out = _editor(ctx, 'bake', {'clips': clips, 'destinations': dests, 'source_mesh': pre['source_rig']['mesh'],
                                    'vary_pairs': core.vary_pairs(pre['source_rig']['chains'], pre['chains'])},
                      timeout=1800)
        made = core.batch_unexpected(dests, out.get('results') or [])
        # the editor's report, and the batch entries judged again here (the same pure check, on the host's own list)
        problems = list(dict.fromkeys((out.get('problems') or []) + core.batch_problems(dests, out.get('results') or [])))
        if problems or not out.get('saved'):
            raise RuntimeError('bake: %s' % (problems or ['the clips were not saved']))
    except Exception:
        pending = {'folder': h['retarget_dir'], 'before': before, 'expected': sorted(dests.values()), 'made': made}
        try:
            # the disk as the failure left it, read here without the editor: what a deferred cleanup may delete
            pending['disk_after'] = core.bake_inventory([], _disk_assets(h['retarget_dir']))
            _bake_cleanup(ctx, pending, _bake_inventory(ctx, h['retarget_dir']))
        except Exception as exc:          # noqa: BLE001 - the bake's own error is the one to raise
            print('HERO_ONBOARD the failed bake was not cleaned up (%s); the next bake does it first'
                  % str(exc)[:300], flush=True)
            ctx.pending_bake_cleanup = dict(pending, error='%s: %s' % (type(exc).__name__, str(exc)[:300]))
        raise
    return {'destinations': dests, 'clips': out['clips'], 'results': out['results']}


def _deletable(h, paths):
    """The unexpected outputs the cleanup may delete: under the hero's own folder AND git-ignored (the same guard
    every declared target has). Any other is printed for a person to look at, never deleted."""
    out = []
    for p in paths:
        try:
            if not p.startswith(_own(h)):
                raise RuntimeError('outside %s' % _own(h))
            ap.require_gitignored(p)
            out.append(p)
        except (RuntimeError, ValueError) as exc:
            print('HERO_ONBOARD the bake made %s, which is not deleted (%s)' % (p, str(exc)[:200]), flush=True)
    return out


def locomotion(ctx, records):
    """<prefix>BS_Locomotion_2D mirroring the template's blend space onto the bake's clips, then ABP_MT_<S>, a child of
    the template with its clip variables pointed at them, each through realize. The ABP references the blend space, so
    it is deleted first."""
    h, pre, made = ctx.h, records['preflight'], records['bake']['destinations']
    tpl = pre['template']
    clip = lambda p: made[core.package(p)]          # noqa: E731 - the bake's own record, not a recomputed name
    _gitignored(h['bs'], h['abp_main'])
    _clear(ctx, [h['abp_main']])
    bs = _realize(ctx, kinds.blend_space(), h['bs'],
                  core.blend_space_spec(h['skeleton'], tpl['bs'], clip, h['bs']))
    abp = _realize(ctx, kinds.abp(h['rig']), h['abp_main'], _locomotion_abp_spec(h, tpl['cdo'], clip))
    # up to date WITH warnings is accepted (the engine counts it as compiled) and said here, not hidden
    return {'bs': bs, 'abp': abp, 'abp_compile_warnings': core.abp_compiled_with_warnings(abp.get('described') or {})}


def inplace(ctx, records):
    """Every clip the hero's ABP plays duplicated into InPlace/ and pinned (hero_onboard_editor.inplace), the blend
    space and the ABP duplicated with every reference swapped. Its outputs are known before it runs
    (core.inplace_outputs): deleted first, dependents before what they reference (the pawn, which references the
    in-place ABP; the in-place ABP, which references its blend space and clips; the blend space, which references the
    clips; then the clips), and again in that order on any failure."""
    h, pre = ctx.h, records['preflight']
    tpl = pre['template']
    clips = core.inplace_outputs(tpl['cdo'], tpl['bs']['samples'], h['retarget_dir'], h['clip_prefix'],
                                 h['inplace_dir'])
    outputs = [h['inplace_path'], h['bs_inplace']] + list(clips.values())
    _gitignored(h['pawn_path'], *outputs)
    _clear(ctx, [h['pawn_path']] + outputs)
    try:
        out = _editor(ctx, 'inplace', {'has_ik': pre['has_foot_ik'], 'travel_bones': pre['travel_bones'],
                                       'outputs': clips}, timeout=1800)
    except Exception:
        _cleanup(ctx, outputs)
        raise
    return dict(out, outputs=outputs)


def pawn(ctx, records):
    """The hero's test pawn (hero_onboard_editor.pawn): deleted first, read back after, deleted again unless it holds
    the profile mesh and the in-place ABP."""
    h = ctx.h
    _gitignored(h['pawn_path'])
    _clear(ctx, [h['pawn_path']])
    try:
        out = _editor(ctx, 'pawn')
        bad = pawn_mismatch(h, out)
        if bad:
            raise RuntimeError('pawn: %s' % bad)
    except Exception:
        _cleanup(ctx, [h['pawn_path']])
        raise
    return out


def _physics_inputs(ctx):
    """Never kept by --resume: the inputs carry this call's invocation id. The reaction body's collision exclusions
    come from SAMPLING the hero's idle (reaction_spec), and no declared input stands for that animation: a locomotion
    rebuilt from the same inputs to a different idle keeps the same key. The stage must still run alone, on a hero
    whose locomotion was made by hand (hit_reaction_release runs from physics with no locomotion record), so it takes
    no need on locomotion either, and it reads nothing from the record file: it runs on every call."""
    import hit_reaction_core
    h = ctx.h
    return {'run': _invocation(ctx), 'profile': h['profile'], 'builder': hit_reaction_core.BUILDER_VERSION,
            'assets': {k: h[k] for k in ('mesh', 'skeleton', 'bs', 'physics_asset', 'cloth_collision_asset',
                                         'abp_path')}}


def physics(ctx, records):
    """The hero's reaction body, PA_MT_<S>, from its profile and skeleton (hit_reaction_core.physics_spec), and for a
    hero with clothing its cloth-collision bodies, each through realize (hero_kinds.physics: the C++ build saves, so
    the guarantee is the delete on a mismatch). The mesh and its stock physics asset are untouched. The dynamics ABP
    references the reaction body; it is derived (dynamics_graph.py rebuilds it), so it goes first, and the record
    says so ('removed')."""
    import hit_reaction_core
    h = ctx.h
    _gitignored(h['abp_path'], h['physics_asset'], h['cloth_collision_asset'])
    parents = _skeleton(h)
    ref = _ref_tms(h)
    spec = reaction_spec(h, parents, ref)
    removed = [h['abp_path']] if _editor(ctx, 'exists', {'paths': [h['abp_path']]})['exists'] else []
    _clear(ctx, [h['abp_path']])
    built = {}
    _realize(ctx, kinds.physics(h['mesh'], built), h['physics_asset'], spec)
    rec = {'spec': spec, 'built': dict(built), 'removed': removed, 'cloth_collision': None}
    # The mesh's own cloth gets bodies for the hips and legs it drapes over (the drive adds them as a cloth collision
    # source; the stock physics asset is never edited).
    if h['profile'].get('stock', {}).get('clothing_assets'):
        cspec = hit_reaction_core.cloth_collision_spec(h['profile'], ref)
        cbuilt = {}
        _realize(ctx, kinds.physics(h['mesh'], cbuilt), h['cloth_collision_asset'], cspec)
        rec['cloth_collision'] = {'spec': cspec, 'built': dict(cbuilt)}
    else:
        _clear(ctx, [h['cloth_collision_asset']])
    return rec


def _verify_inputs(ctx):
    # Verify reads what is in the editor NOW, which no recorded key can stand for: its key carries the id onboard()
    # makes on every call, so it runs on every call, resumed or not, on a reused context too.
    return {'run': _invocation(ctx)}


def _checked(gaps, name, kind, ops, path, spec):
    """Re-describe one asset and compare it with a freshly computed spec, by the same mismatch the build used.
    Returns the description ({} when it could not be read)."""
    try:
        described = kind.describe(ops, path, spec)
    except Exception as exc:          # noqa: BLE001 - an asset that does not describe is a gap, named
        gaps.append('%s %s: not readable (%s)' % (name, path, str(exc)[:300]))
        return {}
    gaps += ['%s: %s' % (name, g) for g in kind.mismatch(spec, described)]
    return described


def _on_disk(path):
    """Whether an asset's package file is on disk (asset_pipeline.content_file)."""
    return os.path.exists(ap.content_file(path))


def verified_assets(h, editor_clips, cloth=False, pawn=False):
    """Every hero asset verify reads, for the file and dirty checks after the reads: the assets described here, the
    pawn when it was read, and the clips the editor read (each played clip and, for an in-place duplicate, its
    original). The template's own clips, read only as the motion witness's reference, are not the hero's assets."""
    out = [h[k] for k in ('ik_rig', 'retargeter', 'bs', 'bs_inplace', 'abp_main', 'inplace_path', 'physics_asset')]
    out += [h['cloth_collision_asset']] if cloth else []
    out += [h['pawn_path']] if pawn else []
    for row in editor_clips or []:
        out.append(row['clip'])
        if row.get('duplicate'):
            out.append(h['retarget_dir'] + core.clip_name(row['clip'])[:-len('_InPlace')])
    return list(dict.fromkeys(out))


def verify(ctx, records):
    """Read everything back, independently of what was intended, and compare it with specs computed NOW from the
    profile, the skeleton and the template: the IK rig, the retargeter, both blend spaces (which clip at which
    coordinate) and both ABPs (which clip in which variable, compile status) by the same mismatch functions the build
    stages use; the pawn; the in-place clips (hero_onboard_editor.verify: travel, effectors, witness bones, swing);
    the reaction body. Then, after every read: each asset it read has its package file on disk, and none of their
    packages is dirty (a read that changed what it read is a gap: nothing here compiles or edits). Needs no record:
    it audits a hero onboarded by hand as well (--check). A gap is reported by name, never skipped; an ABP compiled
    with warnings is accepted and noted."""
    h, ops = ctx.h, ctx.ops
    roles = h['profile']['roles']
    gaps, notes = [], []
    skel = describe_skeleton(h['skeleton'], ops)
    parents = core.skeleton_parents(skel)
    chains = core.retarget_chains(roles, parents)
    has_ik = _has_ik(h, skel)
    travel = travel_bones(roles, parents)
    src = _editor(ctx, 'ik_rig_describe', {'path': dyn_hero.SOURCE_IK_RIG})
    gaps += core.source_rig_problems(src)
    try:
        src_root = _source_root(ctx, src)['bone']
    except RuntimeError as exc:
        src_root = None
        gaps.append(str(exc))
    cdo, tbs = _template(ops)
    clips = core.source_clips(cdo, tbs['samples'])
    sources = {}
    for c in clips:
        sources[_twin(h, c)] = c
        sources[core.inplace_clip(h['inplace_dir'], _twin(h, c))] = c
    # first, while nothing below has loaded or compiled anything: the editor's own read (unsaved packages included)
    out = _editor(ctx, 'verify', {'has_ik': has_ik, 'witness_bones': _witness(roles), 'travel_bones': travel,
                                  'vary_pairs': core.vary_pairs(src['chains'], chains), 'sources': sources},
                  timeout=1800)
    gaps += out['gaps']
    if out.get('pawn'):
        gaps += pawn_mismatch(h, out['pawn'])
    rig = h['rig']
    _checked(gaps, 'ik_rig', kinds.ik_rig(rig), ops, h['ik_rig'],
             core.ik_rig_spec(h['ik_rig'], h['mesh'], roles['pelvis'], chains, travel[0]))
    _checked(gaps, 'retargeter', kinds.retargeter(rig), ops, h['retargeter'],
             core.retargeter_spec(h['retargeter'], dyn_hero.SOURCE_IK_RIG, h['ik_rig'], roles['pelvis'],
                                  [c[0] for c in chains], [c[0] for c in src['chains']], travel[0],
                                  core.package(src.get('mesh')), src_root,
                                  core.single_bone_chains(chains, src['chains'])))
    twin = lambda p: _twin(h, p)                                                       # noqa: E731
    in_place = lambda p: core.inplace_clip(h['inplace_dir'], _twin(h, p))              # noqa: E731
    _checked(gaps, 'blend space', kinds.blend_space(), ops, h['bs'],
             core.blend_space_spec(h['skeleton'], tbs, twin, h['bs']))
    _checked(gaps, 'in-place blend space', kinds.blend_space(), ops, h['bs_inplace'],
             core.blend_space_spec(h['skeleton'], tbs, in_place, h['bs_inplace']))
    for name, path, spec in (('locomotion ABP', h['abp_main'], _locomotion_abp_spec(h, cdo, twin)),
                             ('in-place ABP', h['inplace_path'], _inplace_abp_spec(h, cdo))):
        if core.abp_compiled_with_warnings(_checked(gaps, name, kinds.abp(rig), ops, path, spec)):
            notes.append('%s %s is up to date with compiler warnings (accepted: the engine counts it as compiled)'
                         % (name, path))
    gaps += _physics_gaps(h, parents)
    # after EVERY read above: the files on disk, and no package the reads left dirty
    read = verified_assets(h, out.get('clips'), cloth=bool(h['profile'].get('stock', {}).get('clothing_assets')),
                           pawn=bool(out.get('pawn')))
    absent = {h[k] for k, there in (out.get('exists') or {}).items() if not there}     # already 'missing' gaps
    read = [p for p in read if p not in absent]
    gaps += core.missing_files(read, _on_disk)
    gaps += ['dirty after verify read it: %s' % p for p in _editor(ctx, 'dirty', {'paths': read})['dirty']]
    return {'gaps': gaps, 'notes': notes, 'has_foot_ik': has_ik, 'editor': out}


_ref = _with_referencers
STAGES = [
    sr.Stage('preflight', preflight, inputs=_preflight_inputs),
    sr.Stage('ik_rig', _ref(ik_rig), needs=['preflight'],
             inputs=lambda ctx: {k: ctx.h[k] for k in ('ik_rig', 'retargeter', 'mesh')}),
    sr.Stage('retargeter', _ref(retargeter), needs=['preflight', 'ik_rig'],
             inputs=lambda ctx: {'path': ctx.h['retargeter'], 'mesh': ctx.h['mesh'],
                                 'source_ik_rig': dyn_hero.SOURCE_IK_RIG, 'ops': list(core.DEFAULT_OPS),
                                 'fk_chain_settings': core.FK_CHAIN_SETTINGS}),
    sr.Stage('bake', _ref(bake), needs=['preflight', 'retargeter'],
             inputs=lambda ctx: {k: ctx.h[k] for k in ('retarget_dir', 'clip_prefix', 'mesh', 'retargeter')}),
    sr.Stage('locomotion', _ref(locomotion), needs=['preflight', 'bake'],
             inputs=lambda ctx: {'assets': {k: ctx.h[k] for k in ('bs', 'abp_main', 'abp_main_class', 'skeleton')},
                                 'template': dyn_hero.TEMPLATE_ABP}),
    sr.Stage('inplace', _ref(inplace), needs=['preflight', 'locomotion'],
             inputs=lambda ctx: {k: ctx.h[k] for k in ('inplace_dir', 'bs_inplace', 'inplace_path', 'anim_class',
                                                       'abp_main_class', 'pawn_path')}),
    sr.Stage('pawn', _ref(pawn), needs=['inplace'],
             inputs=lambda ctx: {'assets': {k: ctx.h[k] for k in ('pawn_path', 'mesh', 'anim_class')},
                                 'template': dyn_hero.TEMPLATE_PAWN}),
    sr.Stage('physics', _ref(physics), inputs=_physics_inputs),
    sr.Stage('verify', verify, inputs=_verify_inputs),
]
STAGE_NAMES = tuple(s.name for s in STAGES)
ALIASES = {'retarget': 'ik_rig'}            # the stage before the IK rig and retargeter were two
CHECK_STAGES = [s for s in STAGES if s.name == 'verify']


# --- the run and its summary ---------------------------------------------------------------------------------------

def summarize(recfile, rig, failed=None):
    """The summary other scripts read from the record file (under stage_runner.SUMMARY_KEY; summary_of reads it):
    status PASS (verify done, no gaps), GAPS, PARTIAL (verify not run) or FAILED (a stage raised: failed_stage and
    error say which and why), the verify gaps, what is not carried over, and whether the foot-IK pass runs on this
    hero."""
    def record(name):
        e = recfile.get(name) or {}
        return e.get('record') or {} if e.get('status') == sr.DONE else {}
    ver = record('verify')
    gaps = list(ver.get('gaps') or []) if not failed else []
    has_ik = ver.get('has_foot_ik') if 'has_foot_ik' in ver else record('preflight').get('has_foot_ik')
    out = {'hero': rig, 'finished': time.strftime('%Y-%m-%dT%H:%M:%S'), 'gaps': gaps, 'unsupported': list(UNSUPPORTED),
           'has_foot_ik': has_ik,
           'status': 'FAILED' if failed else ('PASS' if ver and not gaps else ('GAPS' if gaps else 'PARTIAL'))}
    if failed:
        out['failed_stage'] = failed.stage
        out['error'] = (recfile.get(failed.stage) or {}).get('error') or str(failed)
    return out


def summary_of(doc):
    """The hero-level summary of an onboard record file: the one under stage_runner.SUMMARY_KEY, or, in a file from
    before that key, the same fields at the top level (SUMMARY_KEYS). {} when there is none."""
    s = doc.get(sr.SUMMARY_KEY)
    if isinstance(s, dict):
        return s
    return {k: doc[k] for k in SUMMARY_KEYS if k in doc}


def _pending_cleanup(records):
    """A failed bake's cleanup still owed, as the record file holds it: on the bake's failure entry, or (should a
    runner not keep that entry's extra key) in the summary."""
    entry = records.get('bake') if isinstance(records.get('bake'), dict) else {}
    old = records.get(sr.SUMMARY_KEY) if isinstance(records.get(sr.SUMMARY_KEY), dict) else {}
    return entry.get('pending_cleanup') or old.get('pending_bake_cleanup') or None


def onboard(ctx, stages=None, start=None, resume=False, log=print):
    """Run `stages` (STAGES) through stage_runner into ctx.record_path. Every call makes a new invocation id (verify
    and physics carry it in their inputs, so --resume never keeps them, on a reused context as much as a new one) and
    drops ctx's cached reads. The record file is written by the runner only; the two changes this makes to it run
    inside the runner's lock (stage_runner.run's prepare and finish):

      prepare  the last verify is dropped when this run selects any other stage (verify needs no stage, so the runner
               would not drop it with what it rebuilds, and a run that fails before verify must not keep its PASS);
               the old summary becomes status RUNNING, still carrying a failed bake's cleanup when one is owed,
               which this run's bake picks up.
      finish   the summary (summarize) under stage_runner.SUMMARY_KEY, a bake cleanup still owed on the bake's
               failure entry (and in the summary), and the outside referencers a failed stage deleted past.

    Returns the record file as finish left it. A failing stage leaves the file saying which stage failed and why, and
    raises stage_runner.StageFailed."""
    stages = STAGES if stages is None else stages
    rig = ctx.h['rig']
    ctx.invocation = uuid.uuid4().hex
    ctx._reads = None
    names = [s.name for s in stages]
    selected = names[names.index(start):] if start in names else names
    final = {}

    def prepare(records):
        ctx.pending_bake_cleanup = _pending_cleanup(records)
        ctx.failed_external = {}
        # the old summary is replaced by a RUNNING one, which keeps a cleanup still owed on disk for the whole run:
        # rebuilding preflight drops the failed bake entry that also holds it, and a run that dies before finish
        # must not lose it
        records[sr.SUMMARY_KEY] = dict({'hero': rig, 'status': 'RUNNING'}, **(
            {'pending_bake_cleanup': ctx.pending_bake_cleanup} if ctx.pending_bake_cleanup else {}))
        if 'verify' in records and any(n != 'verify' for n in selected):
            records.pop('verify')

    def finish(records, failed):
        summary = summarize(records, rig, failed)
        if failed is not None and ctx.failed_external:
            entry = records.get(failed.stage)
            if isinstance(entry, dict):
                entry['external_referencers'] = ctx.failed_external
            summary['external_referencers'] = ctx.failed_external
        if ctx.pending_bake_cleanup:
            entry = records.get('bake')
            if isinstance(entry, dict) and entry.get('status') == sr.FAILED:
                entry['pending_cleanup'] = ctx.pending_bake_cleanup
            summary['pending_bake_cleanup'] = ctx.pending_bake_cleanup
        records[sr.SUMMARY_KEY] = summary
        final.update(json.loads(json.dumps(records)))

    try:
        recfile = sr.run(stages, ctx, ctx.record_path, start=start, resume=resume,
                         log=lambda m: log('HERO_ONBOARD %s %s' % (rig, m)), prepare=prepare, finish=finish)
    except sr.StageFailed as failed:
        summary = summary_of(final)
        log('HERO_ONBOARD %s' % json.dumps({'hero': rig, 'status': 'FAILED', 'stage': failed.stage,
                                            'error': str(summary.get('error') or failed)[:500],
                                            'record': ctx.record_path}))
        raise
    doc = final or recfile
    summary = summary_of(doc)
    removed = ((doc.get('physics') or {}).get('record') or {}).get('removed') or []
    log('HERO_ONBOARD %s' % json.dumps({'hero': rig, 'status': summary.get('status'), 'gaps': summary.get('gaps'),
                                        'record': ctx.record_path, 'rebuild': removed}))
    return doc


def record_path(h, check=False):
    return h['onboard_record'].replace('.json', '_check.json') if check else h['onboard_record']


def run(rig, first='preflight', check=False, resume=False, allow_external_referencers=False):
    """Onboard `rig` from stage `first` (ALIASES accepted) to the end, or (check) run verify alone into the _check
    record file. Returns the record file, summary included (summary_of); raises stage_runner.StageFailed on a failed
    stage. allow_external_referencers: see Ctx."""
    h = dyn_hero.resolve(rig)
    start = ALIASES.get(first, first)
    if start not in STAGE_NAMES:
        raise ValueError('no stage %r (stages: %s; aliases %s)' % (first, STAGE_NAMES, ALIASES))
    ctx = Ctx(h, record_path(h, check), allow_external_referencers=allow_external_referencers)
    if check:
        return onboard(ctx, CHECK_STAGES, resume=resume)
    return onboard(ctx, STAGES, start=None if start == STAGE_NAMES[0] else start, resume=resume)


def status_lines(path, stages=None):
    """Where a hero stands, from its record file alone (no editor): one line per stage, then the summary."""
    doc = sr.load(path)
    lines = ['%-11s %s%s' % (name, st, '  (%s)' % doc[name].get('error') if st == sr.FAILED else '')
             for name, st in sr.status(STAGES if stages is None else stages, doc)]
    status = summary_of(doc).get('status', '(no summary)')
    if not isinstance(doc.get(sr.SUMMARY_KEY), dict) and 'status' in doc:
        # A record from before stages were recorded: its PASS stands for none of the stages listed above.
        status += '  (from a record written before stages were recorded; it vouches for no stage above)'
    lines.append('status      %s' % status)
    return lines


def main(argv):
    rig, args = dyn_hero.hero_arg(argv)
    first = 'preflight'
    if '--stage' in args:
        first = args[args.index('--stage') + 1]
        if ALIASES.get(first, first) not in STAGE_NAMES:
            raise SystemExit('--stage must be one of %s (or %s)' % (STAGE_NAMES, sorted(ALIASES)))
    check = '--check' in args
    if '--status' in args:
        h = dyn_hero.resolve(rig)
        print('\n'.join(status_lines(record_path(h, check), CHECK_STAGES if check else STAGES)))
        return 0
    try:
        doc = run(rig, first, check, resume='--resume' in args,
                  allow_external_referencers='--allow-external-referencers' in args)
    except sr.StageFailed as exc:
        print('HERO_ONBOARD failed: %s' % exc, file=sys.stderr)
        return 1
    return 0 if summary_of(doc).get('status') == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
