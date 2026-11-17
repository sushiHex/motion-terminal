"""Hero onboarding's generated-asset kinds for asset_pipeline.realize: one Kind per asset kind, each its build, its
describe (a separate read), its pure mismatch (hero_onboard_core) and its save.

    realize(ops, ik_rig(rig), path, core.ik_rig_spec(...))

    ik_rig(rig)          IK rig          hero_onboard_editor ik_rig_build / ik_rig_describe / core.ik_rig_mismatch / save
    retargeter(rig)      IK retargeter   retargeter_build + fk_setup (empty-chain guard) + retarget_chain_map /
                                         retargeter_describe / core.retargeter_mismatch / save
    blend_space()        blend space     AnimAssetTools.BuildBlendSpace / DescribeBlendSpace / core.blend_space_mismatch /
                                         SaveBuiltAsset
    abp(rig)             locomotion ABP  abp_create + abp_cdo (unsaved) / abp_describe / core.abp_mismatch / save
    physics(mesh, built) physics asset   HeroPhysicsTools.BuildPhysicsAsset (saves inside the C++ call) /
                                         DescribePhysicsAsset / core.physics_mismatch / no save

The editor actions run in hero_onboard_editor.py through editor_ops.run_file (`editor`). No rig name appears here.
Design: docs/specs/2026-11-12-asset-pipeline-design.md (layer 2).
"""
import json
import os

import asset_pipeline as ap
import hero_onboard_core as core

HERE = os.path.dirname(os.path.abspath(__file__))
EDITOR = os.path.join(HERE, 'hero_onboard_editor.py').replace('\\', '/')
ANIM_TOOLS = 'MotionTerminal.AnimAssetTools'


def editor(ops, rig, action, args=None, timeout=900):
    """One hero_onboard_editor.py action, run in the editor with HOE_HERO / HOE_ACTION / HOE_ARGS as its globals; its
    result comes back as the value the file leaves in MT_RESULT (unreal_mcp.run_file), never scraped from a log."""
    out = ops.run_file(EDITOR, timeout=timeout, HOE_HERO=rig, HOE_ACTION=action, HOE_ARGS=args or {})
    if not isinstance(out, dict) or out.get('action') != action:
        raise RuntimeError('hero_onboard_editor %s returned %r' % (action, out if out is None else str(out)[:2000]))
    return out


def _tool(ops, name, **arguments):
    reply = ops.call(ANIM_TOOLS, name, **arguments)
    if not reply.get('ok'):
        raise RuntimeError('%s.%s: %s' % (ANIM_TOOLS, name, reply.get('error')))
    return reply


def _at(path, spec):
    if spec.get('path') != path:
        raise ValueError('the spec is for %s, not %s' % (spec.get('path'), path))


def _save(rig):
    return lambda ops, path: editor(ops, rig, 'save', {'paths': [path]})


def ik_rig(rig):
    """The target IK rig (core.ik_rig_spec): made by the engine's factory and controller, left unsaved by the build."""
    def build(ops, path, spec):
        _at(path, spec)
        editor(ops, rig, 'ik_rig_build', {'spec': spec})
    return ap.Kind('ik_rig', build=build,
                   describe=lambda ops, path, spec: editor(ops, rig, 'ik_rig_describe', {'path': path}),
                   mismatch=core.ik_rig_mismatch, save=_save(rig))


def retargeter(rig):
    """The retargeter (core.retargeter_spec): three editor steps, in this order, all unsaved:

    retargeter_build  the asset, its two IK rigs (set FIRST: each default op's initial setup reads them), the
                      default op stack and the Pelvis Motion op's target pelvis;
    fk_setup          the FK op's chains rebuilt and read on their own: a mapping with zero active chains bakes every
                      frame as the retarget pose, silently, so a list that is not the spec's raises here;
    retarget_chain_map  the exact map in every op that keeps one, AFTER fk_setup (its initial setup re-runs the op's
                      fuzzy auto-map, FKChainsOp.cpp OnAddedToStack).

    describe validates against spec['source_mesh'] (the template rig's preview mesh) and the hero's mesh."""
    def build(ops, path, spec):
        _at(path, spec)
        editor(ops, rig, 'retargeter_build', {'path': path, 'source_ik_rig': spec['source_ik_rig'],
                                              'target_ik_rig': spec['target_ik_rig'], 'pelvis': spec['op_pelvis']})
        setup = editor(ops, rig, 'fk_setup', {'path': path})
        if setup.get('chains') != spec['fk_chains']:
            raise RuntimeError('FK op retargets %s, wanted %s (an empty list bakes static clips)'
                               % (setup.get('chains'), spec['fk_chains']))
        editor(ops, rig, 'retarget_chain_map', {'path': path,
                                                'source_chains': sorted(s for s in spec['chain_map'].values() if s)})
    return ap.Kind('retargeter', build=build,
                   describe=lambda ops, path, spec: editor(ops, rig, 'retargeter_describe',
                                                           {'path': path, 'source_mesh': spec['source_mesh']}),
                   mismatch=core.retargeter_mismatch, save=_save(rig))


def blend_space():
    """The locomotion blend space (core.blend_space_spec) through this repo's toolset: BuildBlendSpace leaves it in
    memory, unsaved; SaveBuiltAsset writes it once the separate DescribeBlendSpace read matches."""
    def build(ops, path, spec):
        _at(path, spec)
        _tool(ops, 'BuildBlendSpace', AssetPath=path, SpecJson=json.dumps(spec))
    return ap.Kind('blend_space', build=build,
                   describe=lambda ops, path, spec: ops.call(ANIM_TOOLS, 'DescribeBlendSpace', AssetPath=path),
                   mismatch=core.blend_space_mismatch,
                   save=lambda ops, path: _tool(ops, 'SaveBuiltAsset', AssetPath=path))


def abp(rig):
    """A locomotion ABP, a child of the template (core.abp_spec): abp_create (the engine's AnimBlueprintFactory) then
    abp_cdo (compile, set the clip variables, compile again: the build's compile, the only one), both WITHOUT saving;
    abp_describe reads parent, skeleton, compile status and the CDO values back and compiles nothing; save only on a
    match. A CDO that did not take is therefore a mismatch that realize deletes, never a saved ABP."""
    def build(ops, path, spec):
        _at(path, spec)
        editor(ops, rig, 'abp_create', {'path': path, 'parent_class': spec['parent_class'],
                                        'skeleton': spec['skeleton']})
        editor(ops, rig, 'abp_cdo', {'path': path, 'class': spec['class'], 'values': spec['cdo']})
    return ap.Kind('abp', build=build,
                   describe=lambda ops, path, spec: editor(ops, rig, 'abp_describe', {
                       'path': path, 'class': spec['class'], 'vars': sorted(spec['cdo'])}),
                   mismatch=core.abp_mismatch, save=_save(rig))


# --- physics -----------------------------------------------------------------------------------------------------

def build_physics(ops, mesh, path, spec):
    """UHeroPhysicsTools.BuildPhysicsAsset (the bodies are invisible to Python), its JSON reply decoded."""
    out = ops.py('unreal.HeroPhysicsTools.build_physics_asset(%r, %r, %r)\n' % (mesh, path, json.dumps(spec)),
                 timeout=900)
    if not isinstance(out, dict):
        raise RuntimeError('build_physics_asset %s returned %r' % (path, str(out)[:300]))
    return out


def describe_physics(ops, path):
    """UHeroPhysicsTools.DescribePhysicsAsset, decoded, plus the asset's solver position iterations (Python reads and
    writes those; the bodies it cannot see)."""
    got = ops.py(
        "import json\n"
        "d = json.loads(unreal.HeroPhysicsTools.describe_physics_asset(%r))\n"
        "pa = unreal.load_asset(%r)\n"
        "if pa:\n"
        "    d['position_iterations'] = pa.get_editor_property('solver_settings').get_editor_property("
        "'position_iterations')\n"
        "json.dumps(d)\n" % (path, path))
    if not isinstance(got, dict):
        raise RuntimeError('describe_physics_asset %s returned %r' % (path, str(got)[:300]))
    return got


def set_solver(ops, path, position_iterations):
    """Write the asset's solver position iterations and save it; a failed save raises (the describe that verifies it
    reads the resident object, which would show the value whatever reached disk). RBwC
    reads the asset's SolverSettings.PositionIterations (AnimNode_RigidBodyWithControl.cpp:1269-1275)."""
    saved = ops.py(
        "import json\n"
        "pa = unreal.load_asset(%r)\n"
        "s = pa.get_editor_property('solver_settings')\n"
        "s.set_editor_property('position_iterations', %d)\n"
        "pa.set_editor_property('solver_settings', s)\n"
        "json.dumps(bool(unreal.EditorAssetLibrary.save_asset(%r, only_if_is_dirty=False)))\n"
        % (path, position_iterations, path))
    if saved is not True:
        raise RuntimeError('physics asset %s: saving the solver iterations failed: %r' % (path, saved))


def physics(mesh, built):
    """A physics asset from a hit_reaction_core spec (the reaction body, or the cloth-collision bodies) on `mesh`.

    BuildPhysicsAsset SAVES inside the C++ call, and set_solver saves the iterations after it: neither can be held
    back until the describe matches (the C++ cannot change here). So this kind's save is a no-op, and its guarantee is
    realize's delete-on-mismatch: a build that fails, raises or does not read back as its spec is deleted again, from
    the registry and the disk, before the error is raised; nothing that is not its spec stays at the path.
    built: a dict the build fills with BuildPhysicsAsset's reply (its 'collide' report is part of the stage record)."""
    def build(ops, path, spec):
        out = build_physics(ops, mesh, path, spec)
        built.clear()
        built.update(out)
        if not out.get('ok'):
            raise RuntimeError('physics asset %s: %s' % (path, out.get('error')))
        if spec.get('position_iterations'):
            set_solver(ops, path, spec['position_iterations'])
    return ap.Kind('physics', build=build, describe=lambda ops, path, spec: describe_physics(ops, path),
                   mismatch=core.physics_mismatch, save=lambda ops, path: None)
