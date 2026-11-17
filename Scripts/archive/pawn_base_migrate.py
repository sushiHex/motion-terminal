# Move the roster pawns onto AMTCharacter (game module), whose Mesh is a SkeletalMeshComponentBudgeted.
#
# WHY. The Animation Budget Allocator drives only that component class, and a Blueprint cannot change an
# inherited component's class, so the crowd's far ring was 0/32 budgeted (army_config.json far
# `_budgeted_applied`). AMTCharacter turns auto-registration off, so an unregistered pawn takes the plain
# SkeletalMeshComponent path everywhere: the swap is invisible until a ring opts in.
#
# WHAT IT REFUSES. Only a pawn whose parent is the native ACharacter is reparented. A pawn whose parent is
# another Blueprint inherits the change from it, and reparenting it would cut that inheritance.
#
# HOW IT PROVES NOTHING ELSE MOVED. reparent_blueprint refreshes and recompiles by delta serialization; it is
# not a preservation audit. So the script snapshots the properties the gates and shots depend on BEFORE,
# reparents, compiles, saves, snapshots AFTER and diffs, and writes the BEFORE snapshot to
# Saved/MotionTerminalTests/pawn_base_migrate.json. MIGRATE_ACTION='verify' (run after an editor restart,
# i.e. after a real save + reload) diffs the reloaded assets against that file.
#
# Run in the editor through `Scripts/editor_ops.py` (`ops.run_file`), with MIGRATE_ACTION = 'dry' | 'migrate' | 'verify'.
import json
import os
import re

import unreal

NEW_PARENT = '/Script/MotionTerminalGame.MTCharacter'
PAWNS = [
    '/Game/MT/BP_MT_TestPawn',
    '/Game/MT/BP_MT_TestPawn_Quinn',
    '/Game/MT/Khaimera/BP_MT_TestPawn_Khaimera',   # machine-local; absent on a clean clone
]
SNAPSHOT = os.path.join(unreal.Paths.project_saved_dir(), 'MotionTerminalTests', 'pawn_base_migrate.json')
BUDGETED_MESH = '/Script/AnimationBudgetAllocator.SkeletalMeshComponentBudgeted'
MESH_PROPS = ['skeletal_mesh_asset', 'anim_class', 'relative_location', 'relative_rotation', 'relative_scale3d',
              'visibility_based_anim_tick_option', 'enable_update_rate_optimizations', 'collision_profile_name',
              'cast_shadow', 'component_tags', 'override_materials']
CAPSULE_PROPS = ['capsule_half_height', 'capsule_radius', 'collision_profile_name']
CMC_PROPS = ['max_walk_speed', 'orient_rotation_to_movement', 'rotation_rate', 'gravity_scale',
             'max_acceleration', 'braking_deceleration_walking', 'run_physics_with_no_controller']
ACTOR_PROPS = ['tags', 'use_controller_rotation_yaw', 'auto_possess_ai', 'ai_controller_class']


_ADDRESS = re.compile(r' \(0x[0-9A-Fa-f]+\)')


def _text(value):
    if isinstance(value, unreal.Object):
        return value.get_path_name()
    if isinstance(value, (list, tuple)):
        return [_text(v) for v in value]
    # A struct's str() carries its memory address ("<Struct 'Vector' (0x...) {...}>"), which differs on every
    # read; left in, every struct would "change" and a real change would hide among the false ones.
    return _ADDRESS.sub('', str(value))


def _read(obj, names):
    out = {}
    for n in names:
        try:
            out[n] = _text(obj.get_editor_property(n))
        except Exception:
            try:  # some state (collision profile) is reflected only as a get_ function
                out[n] = _text(getattr(obj, 'get_' + n)())
            except Exception as exc:  # recorded, never skipped
                out[n] = 'UNREADABLE: %s' % exc
    return out


def _parent_of(bp):
    # UBlueprint::ParentClass is not reflected to Python; the asset registry's ParentClass tag carries it,
    # as "/Script/CoreUObject.Class'/Script/Engine.Character'" or a bare path depending on version.
    data = unreal.EditorAssetLibrary.find_asset_data(bp.get_path_name())
    tag = str(data.get_tag_value('ParentClass') or '')
    return tag.split("'")[1] if "'" in tag else tag


def snapshot(bp):
    cls = bp.generated_class()
    cdo = unreal.get_default_object(cls)
    mesh = cdo.get_editor_property('mesh')
    return {
        'parent': _parent_of(bp),
        'mesh_class': mesh.get_class().get_path_name() if mesh else None,
        'auto_register': (mesh.get_editor_property('auto_register_with_budget_allocator')
                          if mesh and mesh.get_class().get_path_name() == BUDGETED_MESH else None),
        'mesh': _read(mesh, MESH_PROPS) if mesh else None,
        'capsule': _read(cdo.get_editor_property('capsule_component'), CAPSULE_PROPS),
        'movement': _read(cdo.get_editor_property('character_movement'), CMC_PROPS),
        'actor': _read(cdo, ACTOR_PROPS),
    }


def target_problems(snap):
    """What must be TRUE after migration, asserted, not merely excluded from the diff.

    The diff skips the parent and mesh class because they are supposed to change; without this an
    unmigrated asset would also diff empty and read `verified`. Auto-registration must be OFF on the
    CDO's mesh: AMTCharacter's constructor sets it, but a Blueprint's saved subobject template is copied
    over constructor defaults, so a stray override would silently opt every pawn into the allocator."""
    problems = []
    if snap['parent'] != NEW_PARENT:
        problems.append('parent is %s, not %s' % (snap['parent'], NEW_PARENT))
    if snap['mesh_class'] != BUDGETED_MESH:
        problems.append('mesh class is %s, not %s' % (snap['mesh_class'], BUDGETED_MESH))
    if snap['auto_register'] is not False:
        problems.append('mesh auto_register_with_budget_allocator is %r, must be False' % snap['auto_register'])
    return problems


def diff(before, after):
    """Every leaf that changed, except the two that are SUPPOSED to change."""
    expected = {('parent',), ('mesh_class',), ('auto_register',)}   # asserted by target_problems instead
    out = []

    def walk(a, b, path):
        if isinstance(a, dict) and isinstance(b, dict):
            for k in sorted(set(a) | set(b)):
                walk(a.get(k), b.get(k), path + (k,))
        elif a != b and path not in expected and not (
                isinstance(a, str) and isinstance(b, str) and _ADDRESS.sub('', a) == _ADDRESS.sub('', b)):
            out.append({'path': '.'.join(path), 'before': a, 'after': b})

    walk(before, after, ())
    return out


def run(action):
    new_parent = unreal.load_class(None, NEW_PARENT)
    if new_parent is None:
        raise RuntimeError('%s not loadable — build the MotionTerminalGame module first' % NEW_PARENT)
    stored = {}
    if action == 'verify':
        with open(SNAPSHOT) as f:
            stored = json.load(f)
    report = {'action': action, 'pawns': {}}
    for path in PAWNS:
        if not unreal.EditorAssetLibrary.does_asset_exist(path):
            report['pawns'][path] = {'status': 'absent on this machine'}
            continue
        bp = unreal.load_asset(path)
        now = snapshot(bp)
        # A property read as UNREADABLE on both sides diffs equal, so a diff of [] would prove nothing about it.
        entry = {'parent': now['parent'], 'mesh_class': now['mesh_class'],
                 'unreadable': [k for part in ('mesh', 'capsule', 'movement', 'actor')
                                for k, v in (now[part] or {}).items() if str(v).startswith('UNREADABLE')]}
        if action == 'verify':
            before = stored.get(path, {}).get('before')
            entry['diff'] = diff(before, now) if before else []
            entry['target_problems'] = target_problems(now)
            entry['status'] = ('no stored snapshot' if before is None else
                               'FAILED' if entry['diff'] or entry['target_problems'] else 'verified')
        elif now['parent'] == NEW_PARENT:
            entry['status'] = 'already on AMTCharacter'
        elif now['parent'] != '/Script/Engine.Character':
            entry['status'] = 'REFUSED: parent is not the native ACharacter (inherits the change from its parent)'
        elif action == 'dry':
            entry['status'] = 'would reparent'
        else:
            unreal.BlueprintEditorLibrary.reparent_blueprint(bp, new_parent)
            unreal.BlueprintEditorLibrary.compile_blueprint(bp)
            # The return is checked: a failed save reads back correct from memory, so the AFTER snapshot
            # below would pass against an unwritten asset (mt_drive.require_saved_gitignored, habit 1). These
            # pawns are git-TRACKED on purpose, so that helper's ignored-path guard does not apply here.
            if not unreal.EditorAssetLibrary.save_loaded_asset(bp, only_if_is_dirty=False):
                raise RuntimeError('save of %s failed after reparent; the asset on disk is unchanged' % path)
            after = snapshot(bp)
            entry.update(parent=after['parent'], mesh_class=after['mesh_class'], diff=diff(now, after),
                         target_problems=target_problems(after))
            entry['status'] = 'FAILED' if entry['diff'] or entry['target_problems'] else 'reparented'
            stored[path] = {'before': now}
        report['pawns'][path] = entry
    if action == 'migrate' and stored:
        os.makedirs(os.path.dirname(SNAPSHOT), exist_ok=True)
        with open(SNAPSHOT, 'w') as f:
            json.dump(stored, f, indent=1)
    unreal.log('PAWN_MIGRATE ' + json.dumps(report))
    return report


# POP, do not get (army_spawn.py's reason): run_python execs into a persistent namespace.
_action = globals().pop('MIGRATE_ACTION', None)
if _action in ('dry', 'migrate', 'verify'):
    run(_action)
