"""
Curated Game Animation Sample clip import onto SK_Mannequin.

Run in the editor through `Scripts/editor_ops.py` (`ops.run_file`).
Uses the proven import path from the seed clips (MT_GAS_Idle_Loop, MT_GAS_Run_Fwd):
AssetImportTask + FbxImportUI{import_animations=True, import_mesh=False, skeleton=<SK_Mannequin skeleton>,
mesh_type_to_import=FBXIT_ANIMATION}.

Not meant to be run as a standalone python.exe script — it calls into the `unreal` module, which only
exists inside the editor's embedded interpreter.
"""

import unreal
import json
import os

DEST_PATH = "/Game/MT/GAS_Clips"

# ============================ WHERE THE FBX PACK LIVES ============================
#
# This used to be a literal `C:/ProgramData/Epic/EpicGamesLauncher/VaultCache/...` constant. Epic's
# launcher lets the vault cache be relocated, so on another machine the import would have failed on a
# path that exists nowhere — the same family of defect as five hardcoded checkout roots in other scripts
# and a hardcoded ENGINE path in run_verification.py. Resolved in two steps:
#
#   1. $MT_GAS_FBX_ROOT              explicit override
#   2. Plugins/MotionTerminal/Tests/GAS_Clips_manifest.json -> "fbxSourceRoot"
#
# Step 2 is why the manifest's `fbxSourceRoot` stays a literal absolute path: it is the PROVENANCE
# record for where these clips actually came from — and now it is also the single place to edit.
_FBX_ENV = "MT_GAS_FBX_ROOT"


def _manifest_fbx_root():
    p = os.path.join(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()),
                     'Plugins', 'MotionTerminal', 'Tests', 'GAS_Clips_manifest.json')
    try:
        with open(p, encoding='utf-8-sig') as f:
            return str(json.load(f).get('fbxSourceRoot') or '')
    except Exception:
        return ''


def resolve_fbx_root():
    """The pack root, or raise naming the override. Never returns a directory that is not there —
    otherwise every one of the eight imports fails separately on a path nobody printed."""
    tried = [c for c in (os.environ.get(_FBX_ENV), _manifest_fbx_root()) if c]
    for c in tried:
        if os.path.isdir(c):
            return c.replace('\\', '/').rstrip('/')
    raise RuntimeError(
        "clip_import_gas: the Game Animation Sample FBX pack was not found. Set %s to the folder that "
        "contains Idle/, Walk/, Run/ ... (or fix fbxSourceRoot in "
        "Plugins/MotionTerminal/Tests/GAS_Clips_manifest.json). Searched, none is a directory: %s"
        % (_FBX_ENV, tried))


FBX_ROOT = resolve_fbx_root()

# Asset name -> (FBX relative path, category)
CURATED = [
    ("MT_GAS_Idle_Loop", "Idle/M_Neutral_Stand_Idle_Loop.FBX", "idle"),
    ("MT_GAS_Walk_Fwd", "Walk/M_Neutral_Walk_Loop_F.FBX", "locomotion"),
    ("MT_GAS_Run_Fwd", "Run/M_Neutral_Run_Loop_F.FBX", "locomotion"),
    ("MT_GAS_Sprint_Fwd", "Sprint/M_Neutral_Sprint_Loop_F.FBX", "locomotion"),
    ("MT_GAS_Jump_Land", "Jump/M_Neutral_Jump_F_Land_Run_Light_Lfoot.FBX", "jump"),
    ("MT_GAS_Gesture_A", "Avoidance/M_Neutral_avoid_idle_move_0_6m_F_Rfoot.FBX", "gesture"),
    ("MT_GAS_Gesture_B", "Interactions/Bench/M_interaction_bench_idle_loop.FBX", "gesture"),
    ("MT_GAS_IdleBreak", "Idle/M_Relaxed_Stand_Idle_Loop.FBX", "idle variety"),
]


def resolve_skeleton():
    obj = unreal.load_asset('/Game/Characters/Mannequins/Meshes/SK_Mannequin')
    return obj if isinstance(obj, unreal.Skeleton) else obj.get_editor_property('skeleton')


def imp(fbx, name, skel):
    t = unreal.AssetImportTask()
    t.set_editor_property('filename', fbx)
    t.set_editor_property('destination_path', DEST_PATH)
    t.set_editor_property('destination_name', name)
    t.set_editor_property('automated', True)
    t.set_editor_property('save', True)
    t.set_editor_property('replace_existing', True)
    ui = unreal.FbxImportUI()
    ui.set_editor_property('import_animations', True)
    ui.set_editor_property('import_mesh', False)
    ui.set_editor_property('import_materials', False)
    ui.set_editor_property('import_textures', False)
    ui.set_editor_property('skeleton', skel)
    ui.set_editor_property('mesh_type_to_import', unreal.FBXImportType.FBXIT_ANIMATION)
    ui.set_editor_property('original_import_type', unreal.FBXImportType.FBXIT_ANIMATION)
    t.set_editor_property('options', ui)
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([t])
    return list(t.get_editor_property('imported_object_paths'))


def run():
    skel = resolve_skeleton()
    results = {}
    for name, rel, category in CURATED:
        fbx = f"{FBX_ROOT}/{rel}"
        try:
            paths = imp(fbx, name, skel)
            results[name] = {'ok': bool(paths), 'paths': paths, 'category': category, 'source': rel}
            print(f"IMP_{name}={paths}")
        except Exception as e:
            results[name] = {'ok': False, 'error': str(e), 'category': category, 'source': rel}
            print(f"IMP_FAIL_{name}={e}")

    for name, info in results.items():
        if info.get('paths'):
            asset = unreal.load_asset(info['paths'][0])
            cls = asset.get_class().get_name() if asset else 'NONE'
            skel_name = asset.get_editor_property('skeleton').get_name() if asset and hasattr(asset, 'get_editor_property') else '?'
            length = asset.get_editor_property('sequence_length') if asset else -1
            print(f"VERIFY_{name} class={cls} skel={skel_name} length={length}")

    print("RESULTS_JSON=" + json.dumps(results))


run()
