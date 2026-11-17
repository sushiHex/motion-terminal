"""Hero onboarding, the editor half: the steps with an editor Python API (the engine's IK Rig, IK Retargeter, batch
retarget and Anim Blueprint scripting API), run by hero_onboard.py through editor_ops.run_file:

    ops.run_file('.../Scripts/hero_onboard_editor.py', HOE_HERO='<Rig>', HOE_ACTION='<action>', HOE_ARGS={...})

Every action returns a dict naming itself ('action'), left in MT_RESULT, which run_file hands back as the value (no
log line to parse). Spec: docs/specs/2026-10-29-hero-onboarding-design.md.
No rig name appears here: everything comes from dyn_hero.resolve and the profile.

Actions: ik_rig_build, ik_rig_describe, retargeter_build, fk_setup, retarget_chain_map, retargeter_describe, bake,
abp_create, abp_cdo, abp_describe, inplace, pawn, verify, exists, referenced_outside, save, inventory, dirty,
clip_kinds.

Nothing here deletes. The host deletes every output before an action that makes it (asset_pipeline.delete_asset:
loaded in one editor call and deleted in the next, gone from the registry AND the disk whether or not that attempt
deleted anything), and an action that makes outputs refuses to start while one still exists (_require_absent). A
delete made inside the same call that loads the package keeps its file (ObjectTools.cpp ~2552), so an in-action
delete could not give that guarantee.
"""
import importlib
import json
import os
import sys

import unreal

_SCRIPTS = os.path.join(unreal.Paths.project_dir(), 'Scripts')
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
_dh = importlib.reload(importlib.import_module('dyn_hero'))
_core = importlib.reload(importlib.import_module('hero_onboard_core'))
_mt = importlib.reload(importlib.import_module('mt_drive'))

EAL = unreal.EditorAssetLibrary
ALIB = unreal.AnimationLibrary
SOURCE = unreal.RetargetSourceOrTarget.SOURCE
TARGET = unreal.RetargetSourceOrTarget.TARGET
WHO = 'hero_onboard'
IK_PARENT = 'ik_foot_root'
IK_PAIRS = (('ik_foot_l', 'foot_l'), ('ik_foot_r', 'foot_r'))   # the template's foot-IK effectors and the feet they carry
IK_ROUNDTRIP_TOL_CM = 1e-3      # the bake is exact by construction: this catches a write that did not round-trip
IK_MIN_SPAN_CM = 5.0            # an effector moving less than this is dead (the walk's smallest real span is 18.75 cm)
EQ_TOL = 1e-4                   # a duplicate's untouched channels must equal the original's to this
INPLACE_TOL_CM = 0.05           # a pinned clip's net horizontal travel after the pin
VARY_MIN_DEG = 1.0              # a retargeted limb whose source swings more than this must swing too...
VARY_SHARE = 0.25               # ...by at least this share of the source's swing (a static bake swings 0)


def _emit(out):
    """An action's result: returned (the dispatcher leaves it in MT_RESULT), JSON-safe."""
    return json.loads(json.dumps(out, default=str))


def _load(path):
    try:
        return unreal.load_asset(path)
    except Exception:
        return None


def _package_file(path):
    """A /Game asset path's .uasset on disk."""
    return os.path.join(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_content_dir()),
                        path[len('/Game/'):] + '.uasset')


def _require_absent(paths):
    """Refuse to start an action while any of its outputs still exists, in the registry OR on disk: the host deleted
    them first (asset_pipeline.delete_asset), and one still there is a delete that did not take. The registry is told
    of a delete before the file is (ObjectTools.cpp:2598 / :2709, the file delete's result ignored), so both are read.
    A fresh editor's registry scan may not have reached the folder yet, so it is waited out first."""
    unreal.AssetRegistryHelpers.get_asset_registry().wait_for_completion()
    left = [p for p in paths if EAL.does_asset_exist(p) or os.path.exists(_package_file(p))]
    if left:
        raise RuntimeError('outputs still exist before the build (the host deletes them first): %s' % left)


def referenced_outside(h, args):
    """{path: [referencer outside `own`]} for each existing path. Deleting does not preserve UObject identity, so
    anything OUTSIDE the hero's own generated folder that references a regenerated asset (a saved level's placed pawn,
    a GameMode, a sequence) would be left holding a dead class: the host refuses the delete when this names any (the
    silent ref-pose failure in khai_inplace_author.py's header). Reads the registry only; loads nothing."""
    unreal.AssetRegistryHelpers.get_asset_registry().wait_for_completion()
    own, out = args['own'], {}
    for p in args['paths']:
        if EAL.does_asset_exist(p):
            refs = sorted(str(r) for r in EAL.find_package_referencers_for_asset(p, False))
            outside = [r for r in refs if not r.startswith(own)]
            if outside:
                out[p] = outside
    return _emit({'action': 'referenced_outside', 'outside': out})


def _path(obj):
    return str(obj.get_path_name()).split('.')[0] if obj else None


def _keys(anim, bone):
    n = anim.data_model_interface.get_number_of_keys()
    return [ALIB.get_bone_pose_for_frame(anim, unreal.Name(bone), f, False) for f in range(n)]


def _tracks(anim):
    return {str(n) for n in anim.data_model_interface.get_bone_track_names()}


def _pts(keys):
    return [[k.translation.x, k.translation.y, k.translation.z] for k in keys]


def _rot_range_deg(keys):
    """The largest angle between any key's rotation and the first key's: how far the bone swings over the clip."""
    q0 = keys[0].rotation
    return max(unreal.MathLibrary.quat_angular_distance(q0, k.rotation) * 57.2957795 for k in keys)


# ------------------------------------------------------------------------------------------------ retarget

def _name(n):
    """An FName as a string, None for NAME_None."""
    s = str(n)
    return None if s in ('None', '') else s


def ik_rig_build(h, args):
    """The IK rig a hero_onboard_core.ik_rig_spec declares: created by the engine's factory, its preview mesh, its
    retarget root (SetRetargetRoot, the PELVIS) and one AddRetargetChain per chain. Per chain rather than one
    ApplyRetargetDefinition: an FBoneChain's StartBone / EndBone are VisibleAnywhere FBoneReferences with no Blueprint
    access (IKRigDefinition.h:154-158), so set_editor_property refuses them (IKRigController.h:466-467); only
    BoneChain.import_text could fill them, and per-chain calls each report their own failure instead.
    Left unsaved: the host saves it only once ik_rig_describe matches the spec."""
    spec = args['spec']
    _require_absent([spec['path']])
    folder, name = spec['path'].rsplit('/', 1)
    rig = unreal.IKRigDefinitionFactory.create_new_ik_rig_asset(folder, name)
    if rig is None:
        raise RuntimeError('create_new_ik_rig_asset(%s, %s) returned None' % (folder, name))
    if _path(rig) != spec['path']:
        # the factory uniquifies a taken name rather than failing (IKRigDefinitionFactory.cpp CreateNewIKRigAsset)
        raise RuntimeError('the IK rig was created as %s, not %s: an asset already held the path'
                           % (_path(rig), spec['path']))
    ctl = unreal.IKRigController.get_controller(rig)
    if not ctl.set_skeletal_mesh(_load(spec['mesh'])):
        raise RuntimeError('set_skeletal_mesh(%s) refused: the mesh lacks bones the rig needs' % spec['mesh'])
    if not ctl.set_retarget_root(spec['pelvis']):
        raise RuntimeError('set_retarget_root(%s) refused' % spec['pelvis'])
    # The root-motion bone (FRetargetDefinition::RootBone): the Root Motion op's target root comes from it
    # (RootMotionGeneratorOp.cpp OnAddedToStack), so it is set, not left to the op's fallback to bone 0.
    if spec.get('root_motion') and not ctl.set_root_motion_bone(spec['root_motion']):
        raise RuntimeError('set_root_motion_bone(%s) refused' % spec['root_motion'])
    for chain, start, end in spec['chains']:
        got = _name(ctl.add_retarget_chain(chain, start, end, ''))
        if got != chain:
            # None: a bone is not on the skeleton; another name: the chain name was taken and uniquified
            raise RuntimeError('add_retarget_chain(%s, %s, %s) made %r' % (chain, start, end, got))
    return _emit({'action': 'ik_rig_build', 'path': _path(rig)})


def ik_rig_describe(h, args):
    """Any IK rig read back through its controller, independently of what built it: path, preview mesh and that
    mesh's skeleton (where the host reads bone 0 when the rig names no root-motion bone,
    hero_onboard_core.source_root_motion), pelvis (GetRetargetRoot), root-motion bone, every chain with its start and
    end bone, and ValidateIKRig's report."""
    rig = _load(args['path'])
    if rig is None:
        raise RuntimeError('IK rig %s does not load' % args['path'])
    ctl = unreal.IKRigController.get_controller(rig)
    chains = []
    for c in ctl.get_retarget_chains():
        n = str(c.get_editor_property('chain_name'))
        chains.append([n, _name(ctl.get_retarget_chain_start_bone(n)), _name(ctl.get_retarget_chain_end_bone(n))])
    rep = ctl.validate_ik_rig()
    mesh = ctl.get_skeletal_mesh()
    return _emit({'action': 'ik_rig_describe', 'path': _path(rig), 'mesh': _path(mesh),
                  'mesh_skeleton': _path(mesh.get_editor_property('skeleton')) if mesh else None,
                  'pelvis': _name(ctl.get_retarget_root()), 'root_motion': _name(ctl.get_root_motion_bone()),
                  'chains': chains,
                  'validation': {'valid': bool(rep.get_editor_property('is_valid')),
                                 'errors': [str(e) for e in rep.get_editor_property('errors')],
                                 'warnings': [str(w) for w in rep.get_editor_property('warnings')]}})


def _rtg(h, args):
    path = args.get('path') or h['retargeter']
    rtg = _load(path)
    if rtg is None:
        raise RuntimeError('retargeter %s does not load' % path)
    return unreal.IKRetargeterController.get_controller(rtg)


def _ops_of(ctl, op_type):
    return [i for i in range(ctl.get_num_retarget_ops()) if str(ctl.get_op_type_by_name(ctl.get_op_name(i))) == op_type]


def retargeter_build(h, args):
    """The retargeter: an empty asset from the engine's IKRetargetFactory (it adds no ops), the source and target IK
    rigs set FIRST (each default op's initial setup reads them: an op added before the rigs maps no chains), then the
    default op stack, then the Pelvis Motion op's target pelvis. Left unsaved, like ik_rig_build."""
    target = args.get('path') or h['retargeter']
    _require_absent([target])
    folder, name = target.rsplit('/', 1)
    rtg = unreal.AssetToolsHelpers.get_asset_tools().create_asset(name, folder, unreal.IKRetargeter,
                                                                  unreal.IKRetargetFactory())
    if rtg is None or _path(rtg) != target:
        raise RuntimeError('create_asset made %s, not the retargeter %s' % (_path(rtg), target))
    ctl = unreal.IKRetargeterController.get_controller(rtg)
    for side, path in ((SOURCE, args['source_ik_rig']), (TARGET, args.get('target_ik_rig') or h['ik_rig'])):
        rig = _load(path)
        if rig is None:
            raise RuntimeError('IK rig %s does not load' % path)
        ctl.set_ik_rig(side, rig)
    ctl.add_default_ops()
    pelvis = _ops_of(ctl, _core.PELVIS_OP)
    if len(pelvis) != 1:
        raise RuntimeError('the default ops hold %d Pelvis Motion ops' % len(pelvis))
    ctl.get_op_controller(pelvis[0]).set_target_pelvis_bone(args['pelvis'])
    return _emit({'action': 'retargeter_build', 'path': _path(rtg), 'ops': ctl.get_num_retarget_ops()})


def fk_setup(h, args):
    """Rebuild the FK op's chains_to_retarget from the chain mapping (Bug A: a mapping with zero active chains bakes
    every frame as the retarget pose, silently) and read back how many chains it now retargets."""
    ctl = _rtg(h, args)
    fk = _ops_of(ctl, _core.FK_OP)
    if len(fk) != 1:
        raise RuntimeError('retargeter has %d FK Chains ops' % len(fk))
    ctl.run_op_initial_setup(fk[0])
    chains = ctl.get_op_controller(fk[0]).get_settings().get_editor_property('chains_to_retarget')
    names = sorted(str(c.get_editor_property('target_chain_name')) for c in chains)
    return _emit({'action': 'fk_setup', 'fk_index': fk[0], 'chains': names})


def retarget_chain_map(h, args):
    """The exact map: in EVERY op with a chain map, each target chain takes the source chain of the same name
    (SetSourceChain per op, not the EAutoMapChainType enum, a plain UENUM). Run after fk_setup, whose initial setup
    re-runs the op's fuzzy auto-map."""
    ctl = _rtg(h, args)
    have = set(args['source_chains'])
    out = {}
    for op in ctl.get_ops_with_chain_maps():
        op = str(op)
        for target in list(ctl.get_chain_map(op).keys()):
            t = str(target)
            if t in have:
                if not ctl.set_source_chain(t, t, op):
                    raise RuntimeError('set_source_chain(%s, %s, %s) changed nothing' % (t, t, op))
                out.setdefault(op, []).append(t)
    return _emit({'action': 'retarget_chain_map', 'mapped': out})


def retargeter_describe(h, args):
    """The retargeter read back: rigs, each op (name, type, enabled), EACH op's chain map (get_chain_map with no op
    name reads only the first op that has one), the FK op's chains and each chain's settings (EnableFK, rotation and
    translation mode and alpha: a disabled chain keeps its name and outputs the retarget pose), the Pelvis Motion op's
    target pelvis, the Root Motion op's source root, target root and target pelvis, and ValidateRetargeter's report
    for the source mesh and the hero's."""
    ctl = _rtg(h, args)
    ops, maps, fk, pel, fks, rm = [], {}, {}, {}, {}, {}
    for i in range(ctl.get_num_retarget_ops()):
        name = str(ctl.get_op_name(i))
        ops.append({'index': i, 'name': name, 'type': str(ctl.get_op_type_by_name(name)),
                    'enabled': bool(ctl.get_retarget_op_enabled(i))})
    for op in ctl.get_ops_with_chain_maps():
        m = {}
        for t, s in ctl.get_chain_map(str(op)).items():
            m[str(t)] = _name(s)
        maps[str(op)] = m
    for o in ops:
        if o['type'] == _core.FK_OP:
            chains = ctl.get_op_controller(o['index']).get_settings().get_editor_property('chains_to_retarget')
            fk[o['name']] = sorted(str(c.get_editor_property('target_chain_name')) for c in chains)
            per = {}
            for c in chains:
                s = {}
                for k in _core.FK_CHAIN_SETTINGS:
                    v = c.get_editor_property(k)
                    s[k] = v if isinstance(v, (bool, int, float)) else str(getattr(v, 'name', v))
                per[str(c.get_editor_property('target_chain_name'))] = s
            fks[o['name']] = per
        elif o['type'] == _core.PELVIS_OP:
            pel[o['name']] = _name(ctl.get_op_controller(o['index']).get_target_pelvis_bone())
        elif o['type'] == _core.ROOT_MOTION_OP:
            rc = ctl.get_op_controller(o['index'])
            rm[o['name']] = {'source_root': _name(rc.get_source_root_bone()),
                             'target_root': _name(rc.get_target_root_bone()),
                             'target_pelvis': _name(rc.get_target_pelvis_bone())}
    rep = ctl.validate_retargeter(_load(args['source_mesh']), _load(h['mesh']))
    rigs = {k: ctl.get_ik_rig(side) for k, side in (('source_ik_rig', SOURCE), ('target_ik_rig', TARGET))}
    return _emit({'action': 'retargeter_describe', 'path': _path(_load(args.get('path') or h['retargeter'])),
                  'source_ik_rig': _path(rigs['source_ik_rig']),
                  'target_ik_rig': _path(rigs['target_ik_rig']), 'ops': ops, 'chain_maps': maps,
                  'fk_chains': fk, 'fk_chain_settings': fks, 'op_pelvis': pel, 'op_root_motion': rm,
                  'validation': {'ready': bool(rep.get_editor_property('is_ready')),
                                 'errors': [str(e) for e in rep.get_editor_property('errors')],
                                 'warnings': [str(w) for w in rep.get_editor_property('warnings')],
                                 'cannot_run': [str(n) for n in rep.get_editor_property('ops_that_cannot_run')]}})


def bake(h, args):
    """Retarget the template's clips onto the hero with Epic's guarded batch API (the third-party batch action this
    replaced crashed the editor once chains were populated). args['destinations'] {template clip: destination} is
    every output, computed by the host BEFORE this runs: the host has deleted them (the native bake overwriting
    existing assets goes down the same crashing path), this refuses to start while one exists, and on ANY failure
    (a raise here as much as a reported problem) the host deletes them all again.

    RunBatchRetargetWithResults returns one entry per asset and can return Skipped or Failed entries without raising:
    every entry is checked (hero_onboard_core.batch_problems), and an entry that is not a requested clip is a problem.
    include_referenced_assets is OFF: on (the default, IKRetargetBatchOperation.h:166-167) the batch also duplicates
    and retargets every animation a requested asset references (HandleAnimReferenceCollection, an additive
    sequence's ref-pose clip among them, IKRetargetBatchOperation.cpp:214-235), outputs nothing here names, saves or
    deletes. The requested clips are plain sequences and need none of them; an additive one, whose ref-pose clip would
    then stay the source's, is refused by the host's preflight (clip_kinds), before anything is deleted.

    The batch only marks its outputs dirty (it saves nothing). Each output must swing its limbs (_swing) BEFORE any is
    saved: a clip is saved only once every clip passed. Once the batch has run, everything (decoding its results
    included) is inside one try, so a raise comes back as a problem with whatever results were decoded; what the
    reply still cannot name (a call that died), the host finds by its before/after inventory of the folder."""
    wanted = {src: args['destinations'][src] for src in args['clips']}
    _require_absent(list(wanted.values()))
    inp = unreal.IKRetargetBatchOperationInputs()
    inp.set_editor_property('assets_to_retarget', [EAL.find_asset_data(p) for p in args['clips']])
    inp.set_editor_property('source_mesh', _load(args['source_mesh']))
    inp.set_editor_property('target_mesh', _load(h['mesh']))
    inp.set_editor_property('ik_retarget_asset', _load(h['retargeter']))
    inp.set_editor_property('prefix', h['clip_prefix'])
    inp.set_editor_property('target_path', h['retarget_dir'].rstrip('/'))
    inp.set_editor_property('overwrite_existing_files', False)
    inp.set_editor_property('include_referenced_assets', False)
    batch = unreal.IKRetargetBatchOperation.run_batch_retarget_with_results(inp)
    results, rows, bad = [], [], []
    # From here the batch has run: a raise must not hide what it made. Decoding the results is inside the try too;
    # whatever was decoded goes back to the host either way, so it can delete the unexpected outputs (a raise is
    # reported as a problem, and nothing is saved).
    try:
        for r in batch:
            st = r.get_editor_property('status')
            results.append({'source': str(r.get_editor_property('source_asset').package_name),
                            'retargeted': str(r.get_editor_property('retargeted_asset').package_name),
                            'status': str(getattr(st, 'name', st)), 'reason': str(r.get_editor_property('reason')),
                            'requested': bool(r.get_editor_property('was_requested'))})
        bad += _core.batch_problems(wanted, results)
        for src, dst in wanted.items():
            if _load(dst) is None:
                bad.append('%s: not made' % dst)
                continue
            row, problems = _swing(src, dst, args['vary_pairs'])
            rows.append(row)
            bad += problems
        if not bad:
            for dst in wanted.values():
                _mt.require_saved_gitignored(dst, WHO)
    except Exception as exc:          # noqa: BLE001 - returned as a problem, with the results, to the host
        bad.append('the bake check raised after the batch ran: %s: %s' % (type(exc).__name__, exc))
    return _emit({'action': 'bake', 'results': results, 'clips': rows, 'problems': bad, 'saved': not bad,
                  'unexpected': _core.batch_unexpected(wanted, results)})


def _bones(anim):
    """The bone names of an animation's skeleton (its reference pose, AnimPose API): a bone with no track is at its
    reference pose only if the skeleton has it at all."""
    pose = unreal.AnimPoseExtensions.get_reference_pose(anim.get_editor_property('skeleton'))
    return {str(n) for n in unreal.AnimPoseExtensions.get_bone_names(pose)}


def _swing(src, dst, pairs):
    """A retargeted clip must swing its limbs like its source: each (source bone, target bone) pair names the SAME
    limb on each skeleton (hero_onboard_core.vary_pairs: the end bone of every chain, by chain name, and both thighs).
    A bone with no track is at its reference pose, swing 0 (the batch keys only the bones it animated), on either
    side; a bone its skeleton does not have at all is a problem, never a swing of 0. The lower bound applies only
    where the SOURCE swings more than VARY_MIN_DEG: a clip that leaves a bone at its reference pose on the source
    (tracked or not) asks nothing of the target there, and a missing target track fails only where the source moves.
    A lower bound only: what it does not see is listed at vary_pairs."""
    a, b = _load(src), _load(dst)
    row, bad = {'src': src, 'dst': dst, 'swing': {}, 'untracked': []}, []
    have_a, have_b = _tracks(a), _tracks(b)
    bones_a, bones_b = _bones(a), _bones(b)
    for sb, tb in pairs:
        if sb not in bones_a or tb not in bones_b:
            bad.append('%s: pair %s/%s names a bone its skeleton does not have' % (dst, sb, tb))
            continue
        s = _rot_range_deg(_keys(a, sb)) if sb in have_a else 0.0
        t = _rot_range_deg(_keys(b, tb)) if tb in have_b else 0.0
        row['swing'][tb] = [round(s, 2), round(t, 2)]
        row['untracked'] += ([('source', sb)] if sb not in have_a else []) + ([('target', tb)] if tb not in have_b
                                                                               else [])
        if s > VARY_MIN_DEG and t < VARY_SHARE * s:
            bad.append('%s: %s swings %.2f deg against the source %.2f (static bake?%s)'
                       % (dst, tb, t, s, '' if tb in have_b else ' no track'))
    return row, bad


# ------------------------------------------------------------------------------------------------ locomotion ABP

def _set_cdo(abp_path, cls_path, values):
    """Compile, point the CDO's clip variables at `values`, compile again (the values must SURVIVE a compile, not
    precede one). The build's compile, and the only one: abp_describe reads the status this leaves and compiles
    nothing. Saves nothing: the caller saves once a separate read accepts it. Raises when a compile fails."""
    bp = _load(abp_path)
    if bp is None:
        raise RuntimeError('ABP %s does not load' % abp_path)
    if not unreal.BlueprintEditorLibrary.compile_blueprint(bp):
        raise RuntimeError('%s did not compile (status %s)' % (abp_path, _bp_status(bp)))
    cdo = unreal.get_default_object(unreal.load_class(None, cls_path))
    for var, asset in values.items():
        cdo.set_editor_property(var, _load(asset))
    if not unreal.BlueprintEditorLibrary.compile_blueprint(bp):
        raise RuntimeError('%s did not compile after its CDO was set (status %s)' % (abp_path, _bp_status(bp)))
    cdo = unreal.get_default_object(unreal.load_class(None, cls_path))
    return {v: _path(cdo.get_editor_property(v)) for v in values}


def _bp_status(bp):
    """UBlueprint::Status (BlueprintReadOnly, Blueprint.h ~499) by name."""
    st = bp.get_editor_property('status')
    return str(getattr(st, 'name', st))


def abp_create(h, args):
    """A locomotion ABP: a child of the template ABP on the hero's skeleton, by the engine's AnimBlueprintFactory
    (ParentClass and TargetSkeleton are EditAnywhere, AnimBlueprintFactory.h:26-31). Left unsaved: abp_cdo sets its
    clip variables and abp_describe reads it back before the host saves it."""
    path = args['path']
    _require_absent([path])
    folder, name = path.rsplit('/', 1)
    parent = unreal.load_class(None, args['parent_class'])
    if parent is None:
        raise RuntimeError('template class %s does not load' % args['parent_class'])
    factory = unreal.AnimBlueprintFactory()
    factory.set_editor_property('target_skeleton', _load(args['skeleton']))
    factory.set_editor_property('parent_class', parent)
    bp = unreal.AssetToolsHelpers.get_asset_tools().create_asset(name, folder, unreal.AnimBlueprint, factory)
    if bp is None or _path(bp) != path:
        raise RuntimeError('create_asset made %s, not the ABP %s' % (_path(bp), path))
    return _emit({'action': 'abp_create', 'abp': _path(bp)})


def abp_cdo(h, args):
    """The clip variables set and compiled (_set_cdo), NOT saved: a CDO that did not take is then a mismatch the
    host deletes, never a saved ABP."""
    return _emit({'action': 'abp_cdo', 'cdo': _set_cdo(args['path'], args['class'], args['values'])})


def abp_describe(h, args):
    """Any locomotion ABP read back, independently of what built it, and READ-ONLY (it compiles nothing: a read that
    compiled would change what it certifies; the build compiles, _set_cdo): its path, parent class
    (BlueprintEditorLibrary.GetBlueprintParentClass), target skeleton, compile status as it stands, 'compiled'
    (whether that status is one the engine treats as compiled, UBlueprint::IsUpToDate: up to date, with or without
    warnings), 'compile_warnings' (up to date with warnings), and the CDO's values of args['vars']. An unknown or
    dirty status is reported as it is, and the host refuses it."""
    bp = _load(args['path'])
    if bp is None:
        raise RuntimeError('ABP %s does not load' % args['path'])
    status = _bp_status(bp)
    compiled = _core.blueprint_status(status) in _core.COMPILED_STATUSES
    parent =unreal.BlueprintEditorLibrary.get_blueprint_parent_class(bp)
    cls = unreal.load_class(None, args['class'])
    cdo = unreal.get_default_object(cls) if cls else None
    values = {}
    for v in args['vars']:
        values[v] = _path(cdo.get_editor_property(v)) if cdo else None
    return _emit({'action': 'abp_describe', 'path': _path(bp),
                  'parent': str(parent.get_path_name()) if parent else None,
                  'skeleton': _path(bp.get_editor_property('target_skeleton')),
                  'status': status, 'compiled': compiled,
                  'compile_warnings': _core.blueprint_status(status) == 'UPTODATEWITHWARNINGS', 'cdo': values})


def _plays(cls_path, bs_var):
    """(cdo {var: clip}, blendspace path, [blendspace sample clips]) of a locomotion ABP, read from the asset."""
    cdo = unreal.get_default_object(unreal.load_class(None, cls_path))
    vars_ = {v: _path(cdo.get_editor_property(v)) for v in _core.TEMPLATE_CLIP_VARS}
    bs = cdo.get_editor_property(bs_var)
    samples = [_path(s.get_editor_property('animation')) for s in bs.get_editor_property('sample_data')] if bs else []
    return vars_, _path(bs), samples


# ------------------------------------------------------------------------------------------------ in place

def _travel_tracks(anim, bones):
    """{bone: (net cm, direction)} for the skeleton's root and its pelvis role, by THIS skeleton's names (a pelvis
    called 'hips' must be pinned and checked too): whichever carries the travel. A missing
    track is an error: its travel would read as none."""
    have = _tracks(anim)
    missing = [b for b in bones if b not in have]
    if missing:
        raise RuntimeError('%s has no track for %s' % (anim.get_path_name(), missing))
    return {b: _core.travel(_pts(_keys(anim, b))) for b in bones}


def _pin(anim, bone, d):
    ks = _keys(anim, bone)
    pts = _core.pin_travel(_pts(ks), d)
    ctrl = anim.controller
    ctrl.open_bracket(unreal.Text('MT in-place pin'), False)
    try:
        ok = ctrl.set_bone_track_keys(unreal.Name(bone), [unreal.Vector(*p) for p in pts], [k.rotation for k in ks],
                                      [k.scale3d for k in ks], False)
    finally:
        ctrl.close_bracket(False)
    if not ok:
        raise RuntimeError('set_bone_track_keys returned False for %s on %s' % (bone, anim.get_path_name()))


def _component(anim, bone, cache):
    n = anim.data_model_interface.get_number_of_keys()
    chain = [str(b) for b in ALIB.find_bone_path_to_root(anim, unreal.Name(bone))]
    for b in chain:
        if b not in cache:
            cache[b] = _keys(anim, b)
    out = []
    for f in range(n):
        xf = None
        for b in chain:
            xf = cache[b][f] if xf is None else unreal.MathLibrary.compose_transforms(xf, cache[b][f])
        out.append(xf)
    return out


def _effectors(anim):
    """Per effector: its span, and its drift from the FK foot it should carry (in ik_foot_root space)."""
    cache = {}
    parent = _component(anim, IK_PARENT, cache)
    rows = {}
    for eff, foot in IK_PAIRS:
        fk = _component(anim, foot, cache)
        want = [unreal.MathLibrary.make_relative_transform(f, p) for f, p in zip(fk, parent)]
        have = _keys(anim, eff)
        drift = max((h.translation - w.translation).length() for h, w in zip(have, want))
        pts = _pts(have)
        span = max(max(p[k] for p in pts) - min(p[k] for p in pts) for k in range(3))
        rows[eff] = {'span_cm': round(span, 3), 'drift_cm': round(drift, 5), 'dead': span < IK_MIN_SPAN_CM,
                     'want': want}
    return rows


def _bake_effectors(anim):
    eff = _effectors(anim)
    ctrl = anim.controller
    ctrl.open_bracket(unreal.Text('MT ik_foot effector bake'), False)
    try:
        for name, _foot in IK_PAIRS:
            ws = eff[name]['want']
            if not ctrl.set_bone_track_keys(unreal.Name(name), [unreal.Vector(w.translation.x, w.translation.y,
                                                                              w.translation.z) for w in ws],
                                            [w.rotation for w in ws], [w.scale3d for w in ws], False):
                raise RuntimeError('set_bone_track_keys returned False for %s' % name)
    finally:
        ctrl.close_bracket(False)


def inplace(h, args):
    """Duplicate EVERY clip the hero's ABP plays into InPlace/: pin each travelling track along its own net travel
    direction, then (when the skeleton has the template's foot-IK effectors) bake them from the post-pin FK feet; the
    blendspace and the ABP are duplicated with every reference swapped. Originals are never opened for write.
    args['outputs'] {hero clip: its duplicate} is every clip output, computed by the host before this runs (it has
    deleted them, the blend space and the ABP; this refuses to start while one exists, and a clip the ABP plays that
    is not in it is refused rather than written somewhere the host does not know of)."""
    vars_, bs_path, samples = _plays(h['abp_main_class'], _core.BS_VAR)
    clips = list(dict.fromkeys([p for p in vars_.values() if p] + samples))
    outputs = args['outputs']
    unknown = [c for c in clips if c not in outputs]
    if unknown:
        raise RuntimeError('the hero ABP plays clips the host did not expect: %s (expected %s)'
                           % (unknown, sorted(outputs)))
    _require_absent(list(outputs.values()) + [h['bs_inplace'], h['inplace_path']])
    has_ik = args['has_ik']
    swap, rows = {}, []
    for src in clips:
        dst = outputs[src]
        if not EAL.duplicate_asset(src, dst):
            raise RuntimeError('duplicate_asset failed: %s -> %s' % (src, dst))
        anim = _load(dst)
        row = {'src': src, 'dst': dst, 'pinned': {}}
        for bone, (net, d) in _travel_tracks(anim, args['travel_bones']).items():
            if net > _core.TRAVEL_MIN_CM:
                off = _core.across(_pts(_keys(anim, bone)), d)
                if off > _core.EXCURSION_MAX_CM:
                    raise RuntimeError('%s: %s travels %.1f cm but bends %.1f cm off its line: not straight-line '
                                       'locomotion, unsupported (one-direction pin would leave it wandering)'
                                       % (src, bone, net, off))
                _pin(anim, bone, d)
                row['pinned'][bone] = {'net_cm': round(net, 2), 'across_cm': round(off, 2),
                                       'direction': [round(x, 4) for x in d]}
        if has_ik:
            for eff, _foot in IK_PAIRS:
                path = [str(b) for b in ALIB.find_bone_path_to_root(anim, unreal.Name(eff))]
                if len(path) < 2 or path[1] != IK_PARENT:
                    raise RuntimeError('%s hangs from %s, not %s: the bake writes effector-local keys relative to %s'
                                       % (eff, path[1:2], IK_PARENT, IK_PARENT))
            before = _effectors(anim)
            row['effector_drift_before_cm'] = {e: before[e]['drift_cm'] for e, _f in IK_PAIRS}
            dead = [e for e, _f in IK_PAIRS if before[e]['dead'] and before[e]['drift_cm'] > IK_ROUNDTRIP_TOL_CM]
            # Only DEAD tracks (the retarget never covers the effectors) are baked; a live track that already carries
            # the foot, or carries something deliberate, is kept and verify reports it.
            if dead:
                # ORDER is load-bearing: the effectors carry the post-pin feet (a pre-pin bake keys them to the
                # travelling foot, a metre ahead of the in-place rig: khai_inplace_author.py IK_PAIRS header).
                _bake_effectors(anim)
            row['effectors_baked'] = bool(dead)
        _mt.require_saved_gitignored(dst, WHO)
        swap[src] = dst
        rows.append(row)
    # the blendspace: the duplicate's sample positions are untouched, only each sample's clip is swapped. A UE struct
    # array indexed from Python is a COPY, so every element is assigned back (khai_inplace_author.py GOTCHA-BS-A).
    if not EAL.duplicate_asset(bs_path, h['bs_inplace']):
        raise RuntimeError('duplicate_asset failed: ' + bs_path)
    bs = _load(h['bs_inplace'])
    arr = bs.get_editor_property('sample_data')
    for i in range(len(arr)):
        smp = arr[i]
        smp.set_editor_property('animation', _load(swap[_path(smp.get_editor_property('animation'))]))
        arr[i] = smp
    bs.set_editor_property('sample_data', arr)
    _mt.require_saved_gitignored(h['bs_inplace'], WHO)
    if not EAL.duplicate_asset(h['abp_main'], h['inplace_path']):
        raise RuntimeError('duplicate_asset failed: ' + h['abp_main'])
    values = {v: swap[p] for v, p in vars_.items() if p}
    values[_core.BS_VAR] = h['bs_inplace']
    cdo = _set_cdo(h['inplace_path'], h['anim_class'], values)
    bad = sorted(v for v, p in values.items() if cdo.get(v) != p)
    if bad:
        raise RuntimeError('in-place ABP CDO %s did not take: %s' % (bad, cdo))
    _mt.require_saved_gitignored(h['inplace_path'], WHO)
    return _emit({'action': 'inplace', 'clips': rows, 'cdo': cdo})


# ------------------------------------------------------------------------------------------------ pawn

def pawn(h, args):
    """The hero's test pawn: the template pawn (AMTCharacter, capsule, CMC) with the hero's mesh and its IN-PLACE
    locomotion ABP, the one verify checks (a pawn on the travelling ABP plays clips whose
    foot-IK effectors are dead). The mesh is set on the CDO and NOT recompiled after (a recompile reverted it). The
    host deleted the pawn first; this refuses to start while it exists."""
    _require_absent([h['pawn_path']])
    if not EAL.duplicate_asset(_dh.TEMPLATE_PAWN, h['pawn_path']):
        raise RuntimeError('duplicate_asset failed: ' + _dh.TEMPLATE_PAWN)
    cdo = unreal.get_default_object(_load(h['pawn_path']).generated_class())
    mesh = cdo.get_editor_property('mesh')
    mesh.set_editor_property('skeletal_mesh_asset', _load(h['mesh']))
    mesh.set_editor_property('anim_class', unreal.load_class(None, h['anim_class']))
    _mt.require_saved_gitignored(h['pawn_path'], WHO)
    return _emit({'action': 'pawn', **_pawn_read(h)})


def _pawn_read(h):
    cdo = unreal.get_default_object(_load(h['pawn_path']).generated_class())
    mesh = cdo.get_editor_property('mesh')
    anim = mesh.get_editor_property('anim_class')
    return {'pawn': h['pawn_path'], 'mesh': _path(mesh.get_editor_property('skeletal_mesh_asset')),
            'anim_class': str(anim.get_path_name()) if anim else None,
            'cloth_disabled': bool(mesh.get_editor_property('disable_cloth_simulation'))}


# ------------------------------------------------------------------------------------------------ verify

def verify(h, args):
    """Read everything back, independently of what was intended. Audits a hero onboarded by hand as well (--check):
    a gap is reported by name, never skipped."""
    gaps, out = [], {'action': 'verify', 'hero': h['rig']}
    # Everything below reads in-memory objects, so an unsaved asset would verify and then vanish on the next editor
    # start: any dirty package under the hero's folder is a gap of its own.
    own = '/Game/MT/%s/' % h['short']
    dirty = sorted(str(p.get_name()) for p in unreal.EditorLoadingAndSavingUtils.get_dirty_content_packages()
                   if str(p.get_name()).startswith(own))
    out['unsaved'] = dirty
    gaps += ['unsaved: %s' % p for p in dirty]
    for key in ('ik_rig', 'retargeter', 'bs', 'abp_main', 'bs_inplace', 'inplace_path', 'pawn_path', 'physics_asset'):
        out.setdefault('exists', {})[key] = bool(EAL.does_asset_exist(h[key]))
        if not out['exists'][key]:
            gaps.append('missing %s %s' % (key, h[key]))
    if not EAL.does_asset_exist(h['inplace_path']):
        out['gaps'] = gaps
        return _emit(out)
    # the pawn read back; the host compares it (hero_onboard.pawn_mismatch, the same check its pawn stage makes)
    out['pawn'] = _pawn_read(h) if EAL.does_asset_exist(h['pawn_path']) else {}
    vars_, bs_path, samples = _plays(h['anim_class'], _core.BS_VAR)
    out['inplace_cdo'], out['inplace_bs'] = vars_, bs_path
    # Which clip sits in which variable and at which blend-space coordinate is the host's comparison (the in-place
    # ABP and blend space against freshly computed specs, hero_onboard verify). Here: every played clip must be the
    # in-place twin of a template clip (args['sources'] {played clip: template clip}) and still swing like it (a
    # static original copied to a static duplicate passes every in-place check).
    for clip in list(dict.fromkeys([p for p in vars_.values() if p] + samples)):
        src = args['sources'].get(clip)
        if src is None:
            gaps.append('%s is not the in-place retarget of any template clip' % clip)
            continue
        _row, problems = _swing(src, clip, args['vary_pairs'])
        gaps += problems
    has_ik = args['has_ik']
    witness = args['witness_bones']
    rows = []
    for clip in list(dict.fromkeys([p for p in vars_.values() if p] + samples)):
        anim = _load(clip)
        row = {'clip': clip, 'duplicate': clip.startswith(h['inplace_dir'])}
        tt = _travel_tracks(anim, args['travel_bones'])
        row['net_travel_cm'] = {b: round(v[0], 3) for b, v in tt.items()}
        # In place means the travel tracks STAY within the gait's own sway over the whole clip, not just that their
        # endpoints meet (a curve that closes still sweeps a metre).
        row['excursion_cm'] = {b: round(_core.excursion(_pts(_keys(anim, b))), 3) for b in tt}
        if any(v[0] > _core.TRAVEL_MIN_CM for v in tt.values()):
            gaps.append('%s still travels %s' % (clip, row['net_travel_cm']))
        if any(e > _core.EXCURSION_MAX_CM for e in row['excursion_cm'].values()):
            gaps.append('%s wanders %s cm in place (bound %.0f)' % (clip, row['excursion_cm'], _core.EXCURSION_MAX_CM))
        if has_ik:
            eff = _effectors(anim)
            row['effectors'] = {e: {k: v for k, v in r.items() if k != 'want'} for e, r in eff.items()}
            for e, r in eff.items():
                # The template's foot-IK pass solves each foot ONTO its effector, so an effector off its FK foot moves
                # the foot (a dead one pins it). A still effector on a still foot (an idle) is correct.
                if r['drift_cm'] > IK_ROUNDTRIP_TOL_CM:
                    gaps.append('%s: %s is %.3f cm off its FK foot (span %.3f cm): the foot-IK pass moves that foot'
                                % (clip, e, r['drift_cm'], r['span_cm']))
        if row['duplicate']:
            src = h['retarget_dir'] + _core.clip_name(clip)[:-len('_InPlace')]
            a = _load(src)
            if a is None:
                gaps.append('%s: its original %s is missing' % (clip, src))
            else:
                worst, where = 0.0, None
                pinned = {b for b, v in _travel_tracks(a, args['travel_bones']).items() if v[0] > _core.TRAVEL_MIN_CM}
                for bone in witness:
                    ka, kb = _keys(a, bone), _keys(anim, bone)
                    for x, y in zip(ka, kb):
                        dd = unreal.MathLibrary.quat_angular_distance(x.rotation, y.rotation) * 57.2957795
                        if bone not in pinned:
                            dd = max(dd, (x.translation - y.translation).length())
                        if dd > worst:
                            worst, where = dd, bone
                row['witness_max_delta'], row['witness_worst'] = round(worst, 6), where
                if worst > EQ_TOL:
                    gaps.append('%s differs from %s on %s by %.6f' % (clip, src, where, worst))
                orig = _travel_tracks(a, args['travel_bones'])
                row['original_net_travel_cm'] = {b: round(v[0], 3) for b, v in orig.items()}
                for b in pinned:
                    if tt.get(b, (0.0,))[0] > INPLACE_TOL_CM:
                        gaps.append('%s: pinned %s still nets %.3f cm' % (clip, b, tt[b][0]))
        rows.append(row)
    out['clips'], out['gaps'] = rows, gaps
    return _emit(out)


def exists(h, args):
    unreal.AssetRegistryHelpers.get_asset_registry().wait_for_completion()
    return _emit({'action': 'exists', 'exists': any(EAL.does_asset_exist(p) for p in args['paths'])})


def inventory(h, args):
    """Every asset the asset registry lists under args['folder'], recursively, in-memory (unsaved) ones included
    (include_only_on_disk_assets False): half of the bake's before/after inventory; the host lists the .uasset files
    on disk itself. Reads the registry only; loads nothing."""
    reg = unreal.AssetRegistryHelpers.get_asset_registry()
    reg.wait_for_completion()
    found = reg.get_assets_by_path(args['folder'].rstrip('/'), True, False)
    return _emit({'action': 'inventory', 'assets': sorted({str(a.package_name) for a in found})})


def dirty(h, args):
    """{'dirty': [path]}: which of args['paths'] have an unsaved (dirty) package now. Verify asks it AFTER its reads,
    so a read that changed what it read is a gap."""
    now = {str(p.get_name()) for p in unreal.EditorLoadingAndSavingUtils.get_dirty_content_packages()}
    return _emit({'action': 'dirty', 'dirty': sorted(p for p in dict.fromkeys(args['paths']) if p in now)})


def clip_kinds(h, args):
    """{clip: its additive anim type by name, None when it does not load} for args['paths']: preflight refuses an
    additive template clip before anything is deleted (hero_onboard_core.additive_problems)."""
    out = {}
    for p in args['paths']:
        seq = _load(p)
        out[p] = str(getattr(seq.get_editor_property('additive_anim_type'), 'name',
                             seq.get_editor_property('additive_anim_type'))) if seq else None
    return _emit({'action': 'clip_kinds', 'kinds': out})


def save(h, args):
    """Save assets a build left in memory once their read-back is accepted (a create/edit leaves them dirty, unsaved:
    an early run's IK rig and blendspace were only in memory, caught by the clean-quit check, and verify had read them
    from memory). Through mt_drive.require_saved_gitignored: a refused or failed save raises."""
    for p in args['paths']:
        _mt.require_saved_gitignored(p, WHO)
    return _emit({'action': 'save', 'paths': args['paths']})


ACTIONS = {'ik_rig_build': ik_rig_build, 'ik_rig_describe': ik_rig_describe, 'retargeter_build': retargeter_build,
           'fk_setup': fk_setup, 'retarget_chain_map': retarget_chain_map, 'retargeter_describe': retargeter_describe,
           'save': save, 'exists': exists, 'bake': bake, 'abp_create': abp_create, 'abp_cdo': abp_cdo,
           'abp_describe': abp_describe, 'inplace': inplace, 'pawn': pawn, 'verify': verify,
           'referenced_outside': referenced_outside, 'inventory': inventory, 'dirty': dirty, 'clip_kinds': clip_kinds}

_hero = globals().pop('HOE_HERO', None)
_action = globals().pop('HOE_ACTION', None)
_args = globals().pop('HOE_ARGS', None) or {}
if _action is not None:
    _h = _dh.resolve(_hero)
    # unreal_mcp.run_file returns the file's MT_RESULT as the call's value
    MT_RESULT = ACTIONS[_action](_h, _args)
