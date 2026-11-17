# Hero intake, the facts: runs INSIDE the editor and writes what one content root holds, with no decisions in it.
# hero_intake_core.py decides; hero_intake.py (normal shell) drives both, running this file with
#
#     client.run_file('.../Scripts/hero_intake_gather.py', HIG_ROOT='/Game/<Pack>', HIG_OUT='<abs path>.json')
#
# Writes {root, skeletal_meshes[] (each with its own bone hierarchy and LOD0 skin-weight counts), skeletons{} (bone
# union per Skeleton asset, for motion sampling), anim_blueprints[], sequences_by_skeleton{}, motion{}} and prints one
# `HERO_INTAKE_GATHER <json summary>` line. Read-only: it loads assets and never saves or edits one.
import json
import math
import re
import time

import unreal

WEIGHT_CUT = 0.05           # a vertex counts toward a bone when that bone's weight on it is at least this
MOTION_SEQS = 6             # stock sequences sampled for baked chain motion
MOTION_SAMPLES = 8          # samples per sequence
LOCOMOTION = re.compile(r'(?i)(jog|run|walk|sprint|locomotion|move)')


def _class(ad):
    return str(ad.asset_class_path.asset_name)


def _tag(ad, key):
    v = ad.get_tag_value(key)
    return str(v).split("'")[1].split('.')[0] if v and "'" in str(v) else (str(v).split('.')[0] if v else None)


def _path(obj):
    return obj.get_path_name().split('.')[0] if obj else None


def _hierarchy(mesh, swm):
    """The MESH's own bones, in its reference-skeleton order, with parents. Not the Skeleton asset's list: a skeleton
    shared by several skins also holds bones only some meshes have, and get_bone_parent knows none of those."""
    names = [str(n) for n in swm.get_all_bone_names()]
    parents = {}
    for n in names:
        p = str(mesh.get_bone_parent(n))
        parents[n] = None if p in ('None', '') else p
    return {'bones': names, 'parents': parents}


def _constraints(pa):
    """All joint constraints. NOT get_constraints(False): with bIncludesTerminated false the engine returns only the
    constraints that ARE terminated (PhysicsAsset.cpp:918, `bIncludesTerminated || IsTerminated()`)."""
    return len(pa.get_constraints(True)) if pa else 0


def _weighted(mesh, swm):
    counts = {}
    for v in range(swm.get_num_vertices()):
        for bone, w in swm.get_vertex_weights(v).items():
            if w >= WEIGHT_CUT:
                b = str(bone)
                counts[b] = counts.get(b, 0) + 1
    return dict(sorted(counts.items()))


def _motion(seq_paths, bones):
    """Per bone, how far its local rotation MOVES within a stock sequence (deg): the largest angle between any sample
    and the sequence's first sample, over a few sequences, endpoints included. A constant offset from the reference
    pose is not motion and reads 0; translation-only motion is not measured."""
    out = {b: 0.0 for b in bones}
    opts = unreal.AnimPoseEvaluationOptions()
    for p in seq_paths:
        seq = unreal.load_asset(p)
        if not seq:
            continue
        length = seq.get_play_length()
        first = {}
        for i in range(MOTION_SAMPLES + 1):
            pose = unreal.AnimPoseExtensions.get_anim_pose_at_time(seq, length * i / MOTION_SAMPLES, opts)
            for b in bones:
                r = unreal.AnimPoseExtensions.get_relative_to_ref_pose_transform(pose, b).rotation
                q = (r.x, r.y, r.z, r.w)   # copy out: a kept struct wrapper can alias memory a later call reuses
                if i == 0:
                    first[b] = q
                    continue
                dot = abs(sum(a * c for a, c in zip(q, first[b])))
                ang = math.degrees(2.0 * math.acos(min(1.0, dot)))
                if ang > out[b]:
                    out[b] = ang
    return {b: round(v, 2) for b, v in out.items()}


def gather(root, out_path):
    t0 = time.time()
    reg = unreal.AssetRegistryHelpers.get_asset_registry()
    assets = reg.get_assets_by_path(root, recursive=True)
    facts = {'root': root, 'skeletal_meshes': [], 'skeletons': {}, 'anim_blueprints': [], 'sequences_by_skeleton': {}}
    seqs = {}
    for ad in assets:
        cls = _class(ad)
        path = str(ad.package_name)
        if cls == 'AnimBlueprint':
            facts['anim_blueprints'].append({'path': path, 'target_skeleton': _tag(ad, 'TargetSkeleton')})
        elif cls == 'AnimSequence':
            sk = _tag(ad, 'Skeleton')
            facts['sequences_by_skeleton'][sk] = facts['sequences_by_skeleton'].get(sk, 0) + 1
            seqs.setdefault(sk, []).append(path)
    for ad in assets:
        if _class(ad) != 'SkeletalMesh':
            continue
        mesh = unreal.load_asset(str(ad.package_name))
        if not mesh or not mesh.skeleton:
            continue
        sk = _path(mesh.skeleton)
        box = mesh.get_bounds().box_extent
        swm = unreal.SkinWeightModifier()
        if not swm.set_skeletal_mesh(mesh):
            continue
        entry = {'path': str(ad.package_name), 'skeleton': sk,
                 'physics_asset': _path(mesh.physics_asset),
                 # Body setups are not exposed to Python (5.8); the joint-constraint count is, and separates a
                 # ragdoll-ready asset (about one per joint) from a bounds-only one (few or none).
                 'physics_constraints': _constraints(mesh.physics_asset),
                 'post_process_abp': _path(mesh.post_process_anim_blueprint),
                 'clothing_assets': sorted(_path(c) for c in (mesh.mesh_clothing_assets or [])),
                 'bounds_cm': [round(2.0 * box.x, 2), round(2.0 * box.y, 2), round(2.0 * box.z, 2)],
                 'hierarchy': _hierarchy(mesh, swm), 'weighted_bones': _weighted(mesh, swm)}
        facts['skeletal_meshes'].append(entry)
        bones = facts['skeletons'].setdefault(sk, {'bones': []})['bones']   # union over the skeleton's meshes,
        bones.extend(b for b in entry['hierarchy']['bones'] if b not in bones)  # for the motion sampling only
    facts['skeletal_meshes'].sort(key=lambda m: m['path'])
    facts['anim_blueprints'].sort(key=lambda a: a['path'])
    # Baked chain motion is sampled on every skeleton that has sequences; the core reads only the primary's.
    facts['motion'] = {}
    for sk, paths in seqs.items():
        if sk not in facts['skeletons']:
            continue
        loco = sorted(p for p in paths if LOCOMOTION.search(p.rsplit('/', 1)[-1]))
        pick = (loco or sorted(paths))[:MOTION_SEQS]
        facts['motion'][sk] = {'sequences': pick, 'max_deg': _motion(pick, facts['skeletons'][sk]['bones'])}
    with open(out_path, 'w') as f:
        json.dump(facts, f, indent=1, sort_keys=True)
    unreal.log('HERO_INTAKE_GATHER ' + json.dumps({'root': root, 'out': out_path, 'meshes': len(facts['skeletal_meshes']),
                                                   'abps': len(facts['anim_blueprints']),
                                                   'seconds': round(time.time() - t0, 1)}))


_root = globals().pop('HIG_ROOT', None)
_out = globals().pop('HIG_OUT', None)
if _root and _out:
    gather(_root, _out)
