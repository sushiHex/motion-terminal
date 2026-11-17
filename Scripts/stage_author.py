# The FOUR photography STAGES, as a script instead of four hand-built .umap files.
#
# Runs inside the editor over `run_python`. PREFERRED form — exec into a FRESH dict, so nothing this
# file defines lands in the editor's persistent Python namespace and nothing already there can be read
# by mistake:
#     exec(open(r'.../Scripts/stage_author.py').read(),
#          {'STAGE_ACTION': 'author',    # 'author' | 'report' | 'spec'
#           'STAGE_NAMES': ('army',),    # or omit/None for all four
#           'STAGE_SUFFIX': 'Proof',     # or omit/None -> the real stage path
#           'STAGE_FORCE': False})       # True = allowed to OVERWRITE an existing level
# The older prepend-then-`exec(src)` idiom (army_spawn.py's) also works and the dispatcher POPs its
# names, but a 480-frame take was once lost to `exec(src, globals())` being a plain namespace MERGE —
# army_conductor.py's `TOTAL = 24.0` landed on top of a driver's own `TOTAL = 8.0` and silently retimed
# a shot while every gate stayed green. Hence both defences here: every module-level helper is
# `_stage_`-prefixed so a merge collides with nothing, AND the recommended call passes its own globals.
#
# Every action prints one `STAGE <json>` line to LogPython so the caller can parse it, and an
# unrecognised action RAISES rather than doing nothing quietly (the army_conductor.py lesson: a silent
# no-op is indistinguishable from an action that ran and said nothing).
#
# ============================ WHY THIS FILE EXISTS ============================
#
# `Lvl_MT_BeatsDE`, `Lvl_MT_Army`, `Lvl_MT_Roster` and `Lvl_MT_FootIK` are the four stages the shot
# sequence is photographed on. Every shot in the sequence except 08 is shot on one of them. Until this file
# they existed ONLY as machine-local .umap files on one machine, hand-built in the editor UI and
# described afterwards in prose. The army's notes recorded that honestly — "the CROWD is rebuildable
# from Scripts/army_spawn.py + army_config.json ... the STAGE is not, there is no stage-authoring
# script" — the same held for all four stages, it was the largest remaining repeatability gap in the
# shot sequence, and it is the gap that most undercuts the project's own premise: a clean checkout
# could rebuild the graph, the tests, the crowd, the sequences and the drivers, and then had nowhere
# to point them.
#
# THE STAGES STAY MACHINE-LOCAL AND GITIGNORED, AND THAT IS UNCHANGED AND DELIBERATE. They reference
# the gitignored Khaimera pawn and the gitignored MetaHuman body. What changes is that the stage is
# now REBUILDABLE rather than merely described — exactly the treatment `army_spawn.py` has always
# given the crowd (spawned at runtime from a committed script + a committed config, and discarded),
# and the treatment `shot_seq_author.py` gave the two render sequences.
#
# THE SPEC BELOW WAS READ OFF THE LIVE STAGES, NOT COPIED OUT OF PROSE NOTES. The notes record the
# recipe ("the standard stage lighting + a floor + a PlayerStart") but not the numbers: no intensity,
# no roll, no floor material, no light Z is written down anywhere in the repo. Every value in STAGES
# was dumped out of the shipped .umap files with an actor-by-actor probe, so this file
# reproduces the stages that shot the footage rather than the stages the prose implies. Where the
# three differ from each other they are LEFT differing (Roster's SkyLight does not real-time capture
# and Roster has no SkyAtmosphere; the army's sun is MOVABLE at intensity 6.0 while the other two are
# STATIONARY at 10.0) — normalising them here would have quietly re-lit two shipped shots.
#
# ============================ new_level(), NEVER duplicate_asset() ============================
#
# GOTCHA-A' (project rule; violating it is a HARD EDITOR CRASH, and the army stage paid for it once): never change
# maps from inside run_python while Python holds a reference to a world/level/actor. `duplicate_asset`
# and `new_level` both return/leave a World; because run_python execs into a PERSISTENT namespace, any
# name still bound to it keeps the outgoing world alive and the next map load fatal-errors on
# `EditorServer.cpp:2544 World Memory Leaks` rather than leaking. `Scripts/army_spawn.py`'s header
# records the incident.
#
# Roster and BeatsDE were both built by DUPLICATING the previous stage — Lvl_MT_Test ->
# Lvl_MT_Roster -> Lvl_MT_BeatsDE. The army stage tried the same recipe and crashed the editor, and the
# fix recorded then is the rule this file follows: author from `new_level()` + the standard stage-lighting
# recipe, one call, saved, no leak path. So this file does NOT reproduce how most of these stages were
# actually made. That is the point — a duplicate chain would also make each stage depend on ANOTHER
# machine-local .umap surviving on the machine, which is the very gap being closed.
#
# Consequences baked into this file, the same ones army_spawn.py carries:
#   * nothing here binds a World, Level or actor to a module-level global — every handle is a local
#     that dies with its call, and the dispatch at the bottom keeps only strings/None/bools;
#   * `author_one` calls new_level() as its FIRST statement, before any handle exists to survive it;
#   * the dispatcher gc.collect()s between stages, because authoring two stages in one call means a
#     second map change while the first stage's locals may not have been reaped yet.
#
# ============================ WHAT IT REFUSES TO DO ============================
#
# 1. IT WILL NOT OVERWRITE AN EXISTING LEVEL unless STAGE_FORCE is set. `new_level` on an existing
#    path destroys what is there, and `render_shot` clears its output directory before rendering, so
#    "rebuild the stage, then re-render" is irreversible on a machine that holds the only copy of a
#    shipped take. A 480-frame take was once lost to exactly that shape of mistake. The default is
#    therefore to refuse and name STAGE_SUFFIX in the error.
# 2. IT WILL NOT WRITE A LEVEL THAT IS NOT git-IGNORED. `_require_gitignored_target` runs BEFORE
#    new_level and fails CLOSED — the same guard, for the same reason, as
#    `metahuman_bind._require_gitignored_level`: once, a script that operated on "whatever level
#    is loaded" spawned into and saved the STOCK `Lvl_ThirdPerson`. A convention with no mechanism
#    behind it is a convention that will be violated again. (This one checks the TARGET path rather
#    than the loaded world, because here the write is the creation.)
# 3. IT WILL NOT CLAIM SUCCESS FROM THE SETTERS. After `save_current_level` it re-reads every actor it
#    placed and raises if the transform, mobility, mesh, material or light property is not what was
#    asked for — "the setter returned" is not the property this file exists to guarantee
#    (shot_seq_author.py's rule, applied to a world instead of an asset).
#
# ============================ WHAT IS *NOT* IN THE SPEC, AND WHY ============================
#
# * THE ARMY CROWD. `Lvl_MT_Army`'s 46 instances are not level actors and never were: `army_spawn.py`
#   spawns them into the editor world before PIE and `despawn_army()` destroys them. The stage is the
#   5 persistent actors below; the crowd was already rebuildable. Nothing here spawns a pawn into it.
# * THE ROSTER LINE-UP. `Lvl_MT_Roster`'s four pawns are placed by `metahuman_bind.place()`, which has
#   owns their marks, their mesh overrides and Khaimera's in-place ABP swap. Duplicating
#   that here would give the line-up two authors that could disagree. They are declared in
#   `delegated_actors` so `report` accounts for them instead of calling them strays, and the roster
#   stage is rebuilt as `author` + `metahuman_bind` declare/place — which is stated in the report and
#   was run that way to prove it.
# * CAMERAS. Every shot's camera is a SPAWNABLE in its LevelSequence (authored by shot_seq_author.py /
#   metahuman_bind.sequence) and is driven live by the shot's Python driver inside the MRQ PIE world.
#   No stage holds one, and adding one would give the driver something to fight.
# * ExponentialHeightFog / PostProcessVolume. None of the four stages has either. The 4-up's
#   auto-exposure pin lives on the sequence camera's own post-process, not in the world.
#
# ============================ THE ROTATOR TRAP ============================
#
# `unreal.Rotator(roll, pitch, yaw)` — ROLL IS FIRST. `Rotator(-6, 180, 0)` sets pitch=180 and turns
# the camera upside down. Every `rot` triple in STAGES is [roll, pitch, yaw] in that order, matching
# the constructor, and `_stage_rot()` is the only place one is built.
import gc
import json
import os

import unreal

# =====================================================================================================
# THE SPEC — every number below was read off the shipped .umap, not out of prose notes.
# =====================================================================================================
# Shared marks. The floor's top surface sits at Z=0 (a 100 uu Cube at Z=-50), which is what every
# pawn Z of 110 (capsule half-height 88 + clearance) and every camera height in every driver assumes.
_FLOOR_MESH = '/Engine/BasicShapes/Cube.Cube'
_GRID_MAT = '/Engine/EngineMaterials/WorldGridMaterial.WorldGridMaterial'
_SHAPE_MAT = '/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial'
_MT_GAMEMODE = '/Game/MT/BP_MT_GameMode.BP_MT_GameMode_C'

STAGES = {
    # ---------------------------------------------------------------------------------------------
    # The two-pawn lane stage: the locomotion lane, the gameplay bridge and the montage layer (through
    # Seq_Lane), and the `07_aim_hand_ik` take (through Seq_BeatsDE).
    # Two pawns abreast at +/-120 y, both facing +X so a camera on +X shoots the front.
    # ---------------------------------------------------------------------------------------------
    'beatsde': {
        'level': '/Game/MT/BeatsDE/Lvl_MT_BeatsDE',
        'game_mode': _MT_GAMEMODE,
        'shots': ['02_locomotion_lane', '02b_gameplay_bridge', '03_montage_layer', '07_aim_hand_ik'],
        'delegated_actors': [],
        'actors': [
            {'label': 'Floor', 'class': '/Script/Engine.StaticMeshActor',
             'loc': [0.0, 0.0, -50.0], 'rot': [0.0, 0.0, 0.0], 'scale': [120.0, 120.0, 1.0],
             'mobility': 'STATIC', 'static_mesh': _FLOOR_MESH, 'material': _GRID_MAT},
            {'label': 'DirectionalLight', 'class': '/Script/Engine.DirectionalLight',
             'loc': [0.0, 0.0, 600.0], 'rot': [0.0, -45.0, -30.0], 'scale': [2.5, 2.5, 2.5],
             'mobility': 'STATIONARY',
             'light': {'intensity': 10.0, 'atmosphere_sun_light': True}},
            {'label': 'SkyLight', 'class': '/Script/Engine.SkyLight',
             'loc': [0.0, 0.0, 300.0], 'rot': [0.0, 0.0, 0.0], 'scale': [1.0, 1.0, 1.0],
             'mobility': 'STATIONARY',
             'sky_light': {'intensity': 1.0, 'real_time_capture': True}},
            {'label': 'SkyAtmosphere', 'class': '/Script/Engine.SkyAtmosphere',
             'loc': [0.0, 0.0, 0.0], 'rot': [0.0, 0.0, 0.0], 'scale': [1.0, 1.0, 1.0],
             'mobility': 'MOVABLE'},
            # Parked 30 m off the mark so the GameMode's default pawn can never collide with the
            # placed rigs at the origin (the roster stage's arrangement, inherited).
            {'label': 'FarPlayerStart', 'class': '/Script/Engine.PlayerStart',
             'loc': [0.0, -3000.0, 200.0], 'rot': [0.0, 0.0, 0.0], 'scale': [1.0, 1.0, 1.0],
             'mobility': 'STATIC'},
            {'label': 'MT_Manny', 'class': '/Game/MT/BP_MT_TestPawn.BP_MT_TestPawn_C',
             'loc': [0.0, -120.0, 110.0], 'rot': [0.0, 0.0, 0.0], 'scale': [1.0, 1.0, 1.0],
             'mobility': 'MOVABLE', 'auto_possess_ai': 'PLACED_IN_WORLD'},
            {'label': 'MT_Khaimera',
             'class': '/Game/MT/Khaimera/BP_MT_TestPawn_Khaimera.BP_MT_TestPawn_Khaimera_C',
             'loc': [0.0, 120.0, 110.0], 'rot': [0.0, 0.0, 0.0], 'scale': [1.0, 1.0, 1.0],
             'mobility': 'MOVABLE', 'auto_possess_ai': 'PLACED_IN_WORLD',
             'optional_why': 'BP_MT_TestPawn_Khaimera lives under the gitignored Content/MT/Khaimera/ '
                             'tree (it references the Fab Paragon mesh). Absent on a clean checkout '
                             'until Khaimera is added; the stage still builds and the lane take still '
                             'renders, because pie_lane_drive hides him anyway.'},
        ],
    },
    # ---------------------------------------------------------------------------------------------
    # The army stage, for the crowd take. Five persistent actors and nothing else: the crowd is
    # spawned at runtime by army_spawn.py. Bigger floor (+/-7000 uu) and a higher, MOVABLE sun,
    # because the pull-back camera ends 72 m out and 29 m up.
    # ---------------------------------------------------------------------------------------------
    'army': {
        'level': '/Game/MT/Army/Lvl_MT_Army',
        # No game-mode override, and that is what the shipped stage holds (default_game_mode null).
        # The crowd take renders under MRQ, which overrides the game mode anyway.
        'game_mode': None,
        'shots': ['06_crowd'],
        'delegated_actors': [],
        'crowd_note': 'The 46 instances are NOT level actors — Scripts/army_spawn.py spawns them into '
                      'the editor world before PIE and despawn_army() destroys them.',
        'actors': [
            {'label': 'Floor', 'class': '/Script/Engine.StaticMeshActor',
             'loc': [0.0, 0.0, -50.0], 'rot': [0.0, 0.0, 0.0], 'scale': [140.0, 140.0, 1.0],
             'mobility': 'STATIC', 'static_mesh': _FLOOR_MESH, 'material': _SHAPE_MAT},
            {'label': 'Sun', 'class': '/Script/Engine.DirectionalLight',
             'loc': [0.0, 0.0, 2000.0], 'rot': [0.0, -45.0, 45.0], 'scale': [2.5, 2.5, 2.5],
             'mobility': 'MOVABLE',
             'light': {'intensity': 6.0, 'atmosphere_sun_light': True}},
            {'label': 'SkyLight', 'class': '/Script/Engine.SkyLight',
             'loc': [0.0, 0.0, 2000.0], 'rot': [0.0, 0.0, 0.0], 'scale': [1.0, 1.0, 1.0],
             'mobility': 'MOVABLE',
             'sky_light': {'intensity': 1.0, 'real_time_capture': True}},
            {'label': 'SkyAtmosphere', 'class': '/Script/Engine.SkyAtmosphere',
             'loc': [0.0, 0.0, 0.0], 'rot': [0.0, 0.0, 0.0], 'scale': [1.0, 1.0, 1.0],
             'mobility': 'MOVABLE'},
            {'label': 'ArmyPlayerStart', 'class': '/Script/Engine.PlayerStart',
             'loc': [0.0, 0.0, 110.0], 'rot': [0.0, 0.0, 0.0], 'scale': [1.0, 1.0, 1.0],
             'mobility': 'STATIC'},
        ],
    },
    # ---------------------------------------------------------------------------------------------
    # The roster stage: the cold open, the four-rig lineup take and the end card. The four line-up pawns are NOT here: they
    # belong to metahuman_bind.place(), which owns their marks, mesh overrides and Khaimera's
    # in-place ABP swap. This stage is the room they stand in.
    # ---------------------------------------------------------------------------------------------
    'roster': {
        'level': '/Game/MT/Showcase/Lvl_MT_Roster',
        'game_mode': _MT_GAMEMODE,
        'shots': ['01_intro', '05_roster_lineup', '09_closing_card'],
        'delegated_actors': ['Roster_MetaHuman', 'Roster_Manny', 'Roster_Quinn', 'Roster_Khaimera'],
        'delegated_to': 'Scripts/metahuman_bind.py `place` (run `declare` first, same editor session)',
        'actors': [
            {'label': 'Floor', 'class': '/Script/Engine.StaticMeshActor',
             'loc': [0.0, 0.0, -50.0], 'rot': [0.0, 0.0, 0.0], 'scale': [120.0, 120.0, 1.0],
             'mobility': 'STATIC', 'static_mesh': _FLOOR_MESH, 'material': _GRID_MAT},
            {'label': 'DirectionalLight', 'class': '/Script/Engine.DirectionalLight',
             'loc': [0.0, 0.0, 600.0], 'rot': [0.0, -45.0, -30.0], 'scale': [2.5, 2.5, 2.5],
             'mobility': 'STATIONARY',
             'light': {'intensity': 10.0, 'atmosphere_sun_light': True}},
            # NOTE the two deliberate differences from the BeatsDE stage, both read off the shipped
            # level: real_time_capture is FALSE here, and there is NO SkyAtmosphere. The lineup take's
            # exposure was tuned on this lighting (both adaptation speeds pinned to 100 on
            # the sequence camera's post-process), so "fixing" either would re-light three shipped
            # shots for tidiness.
            {'label': 'SkyLight', 'class': '/Script/Engine.SkyLight',
             'loc': [0.0, 0.0, 300.0], 'rot': [0.0, 0.0, 0.0], 'scale': [1.0, 1.0, 1.0],
             'mobility': 'STATIONARY',
             'sky_light': {'intensity': 1.0, 'real_time_capture': False}},
            {'label': 'FarPlayerStart', 'class': '/Script/Engine.PlayerStart',
             'loc': [0.0, -3000.0, 200.0], 'rot': [0.0, 0.0, 0.0], 'scale': [1.0, 1.0, 1.0],
             'mobility': 'STATIC'},
        ],
    },
    # ---------------------------------------------------------------------------------------------
    # The foot-IK A/B stage, for the foot-IK ramp A/B take.
    #
    # This was once thought to be the one stage whose SHOT could not be re-shot even with the stage and
    # the sequence rebuilt, because "the original harness was scratchpad-only and is not recoverable from
    # the record". That was read off a report's prose. It was WRONG, and looking at the files corrected
    # it: the harness was in `captures/m4-footik-ab/render_footik_ab.py`, beside the frames it
    # shot, with every camera constant in it — `captures/` is gitignored, so a clean checkout never got
    # it, but nothing was lost. It is committed now as `Scripts/pie_footik_ab_drive.py`.
    #
    # WHAT IS STILL TRUE, and it is a different claim: a re-shoot is a NEW TAKE. A re-shoot of take B
    # through this stage put the camera and the settle back exactly (pawn px=777.6 pz=193.4, both
    # soles planted) and still came out 13.5/255 from the shipped frames, because ABP_MT_Locomotion is
    # not the graph the original take filmed. Said here, at the spec, and at the manifest row.
    #
    # THE GEOMETRY IS THE POINT OF THIS STAGE, so it is specified to the same precision as everything
    # else: a 20-degree ramp the pawn walks up, a landing, four rising stairs and a second landing.
    # The A/B measures foot conformance on the ramp, so its pitch and Z are load-bearing in a way no
    # other stage's props are. The odd fractional locations are the hand-placed originals, read off
    # the live .umap and preserved rather than rounded — rounding them would move the surface the
    # +3.46 / -7.14 cm conformance figure was measured against.
    # ---------------------------------------------------------------------------------------------
    'footik': {
        'level': '/Game/MT/Showcase/Lvl_MT_FootIK',
        'game_mode': _MT_GAMEMODE,
        'shots': ['04_footik_ab'],
        'delegated_actors': [],
        'driver_note': 'The foot-IK A/B take IS re-shootable from this stage — stage + '
                       'Seq_FootIK + Scripts/pie_footik_ab_drive.py (take B is the shipped shot; take '
                       'A needs a node-removed ABP duplicate). A re-shoot is a new take, not a '
                       'reproduction of the shipped frames: the graph has changed since they were shot.',
        'actors': [
            # ±2000 uu — much smaller than the other stages, because the whole A/B happens inside
            # x in [-100, 1900].
            {'label': 'Floor', 'class': '/Script/Engine.StaticMeshActor',
             'loc': [0.0, 0.0, -50.0], 'rot': [0.0, 0.0, 0.0], 'scale': [40.0, 40.0, 1.0],
             'mobility': 'STATIC', 'static_mesh': _FLOOR_MESH, 'material': _GRID_MAT},
            {'label': 'DirectionalLight', 'class': '/Script/Engine.DirectionalLight',
             'loc': [0.0, 0.0, 600.0], 'rot': [0.0, -45.0, -30.0], 'scale': [2.5, 2.5, 2.5],
             'mobility': 'STATIONARY',
             'light': {'intensity': 10.0, 'atmosphere_sun_light': True}},
            # Like the roster stage and unlike BeatsDE/Army: no real-time capture, no SkyAtmosphere.
            {'label': 'SkyLight', 'class': '/Script/Engine.SkyLight',
             'loc': [0.0, 0.0, 300.0], 'rot': [0.0, 0.0, 0.0], 'scale': [1.0, 1.0, 1.0],
             'mobility': 'STATIONARY',
             'sky_light': {'intensity': 1.0, 'real_time_capture': False}},
            {'label': 'FarPlayerStart', 'class': '/Script/Engine.PlayerStart',
             'loc': [0.0, -3000.0, 200.0], 'rot': [0.0, 0.0, 0.0], 'scale': [1.0, 1.0, 1.0],
             'mobility': 'STATIC'},
            # The ramp: pitch 20 deg, 600x300x20 uu of Cube. Its surface runs x ~ [500, 1071] rising
            # to z ~ 205 — the numbers the foot-clearance A/B is measured against.
            {'label': 'Ramp_20deg', 'class': '/Script/Engine.StaticMeshActor',
             'loc': [785.328, 0.0, 93.2091], 'rot': [0.0, 20.0, 0.0], 'scale': [6.0, 3.0, 0.2],
             'mobility': 'STATIC', 'static_mesh': _FLOOR_MESH, 'material': _SHAPE_MAT},
            {'label': 'Landing_TopOfRamp', 'class': '/Script/Engine.StaticMeshActor',
             'loc': [1213.8156, 0.0, 195.2121], 'rot': [0.0, 0.0, 0.0], 'scale': [3.0, 3.0, 0.2],
             'mobility': 'STATIC', 'static_mesh': _FLOOR_MESH, 'material': _SHAPE_MAT},
            {'label': 'Stair_01', 'class': '/Script/Engine.StaticMeshActor',
             'loc': [1408.8156, 0.0, 217.7121], 'rot': [0.0, 0.0, 0.0], 'scale': [0.9, 3.0, 0.25],
             'mobility': 'STATIC', 'static_mesh': _FLOOR_MESH, 'material': _SHAPE_MAT},
            {'label': 'Stair_02', 'class': '/Script/Engine.StaticMeshActor',
             'loc': [1498.8156, 0.0, 230.2121], 'rot': [0.0, 0.0, 0.0], 'scale': [0.9, 3.0, 0.5],
             'mobility': 'STATIC', 'static_mesh': _FLOOR_MESH, 'material': _SHAPE_MAT},
            {'label': 'Stair_03', 'class': '/Script/Engine.StaticMeshActor',
             'loc': [1588.8156, 0.0, 242.7121], 'rot': [0.0, 0.0, 0.0], 'scale': [0.9, 3.0, 0.75],
             'mobility': 'STATIC', 'static_mesh': _FLOOR_MESH, 'material': _SHAPE_MAT},
            {'label': 'Stair_04', 'class': '/Script/Engine.StaticMeshActor',
             'loc': [1678.8156, 0.0, 255.2121], 'rot': [0.0, 0.0, 0.0], 'scale': [0.9, 3.0, 1.0],
             'mobility': 'STATIC', 'static_mesh': _FLOOR_MESH, 'material': _SHAPE_MAT},
            {'label': 'Landing_TopOfStairs', 'class': '/Script/Engine.StaticMeshActor',
             'loc': [1873.8156, 0.0, 295.2121], 'rot': [0.0, 0.0, 0.0], 'scale': [3.0, 3.0, 0.2],
             'mobility': 'STATIC', 'static_mesh': _FLOOR_MESH, 'material': _SHAPE_MAT},
            # x = -100 so the pawn has a run-up to the ramp toe at x ~ 500.
            {'label': 'FootIK_Manny', 'class': '/Game/MT/BP_MT_TestPawn.BP_MT_TestPawn_C',
             'loc': [-100.0, 0.0, 110.0], 'rot': [0.0, 0.0, 0.0], 'scale': [1.0, 1.0, 1.0],
             'mobility': 'MOVABLE', 'auto_possess_ai': 'PLACED_IN_WORLD'},
        ],
    },
}

_MOBILITY = {
    'STATIC': unreal.ComponentMobility.STATIC,
    'STATIONARY': unreal.ComponentMobility.STATIONARY,
    'MOVABLE': unreal.ComponentMobility.MOVABLE,
}
_TOL = 0.01          # uu / degrees. The setters are exact; this is float-round slack, not a fudge.


# =====================================================================================================
# small helpers — none of them retains anything
# =====================================================================================================
def _stage_vec(triple):
    return unreal.Vector(float(triple[0]), float(triple[1]), float(triple[2]))


def _stage_rot(triple):
    """[roll, pitch, yaw] -> Rotator(roll, pitch, yaw). ROLL IS FIRST; this is the only
    place in the file a Rotator is constructed, so the trap has exactly one site."""
    return unreal.Rotator(float(triple[0]), float(triple[1]), float(triple[2]))


def _stage_as_list(vec):
    return [round(float(vec.x), 4), round(float(vec.y), 4), round(float(vec.z), 4)]


def _stage_rot_as_list(rot):
    return [round(float(rot.roll), 4), round(float(rot.pitch), 4), round(float(rot.yaw), 4)]


def _stage_load_class(path):
    try:
        return unreal.load_class(None, path)
    except Exception:
        return None


def _stage_load_object(path):
    try:
        return unreal.load_asset(path)
    except Exception:
        return None


def _stage_by_label(label):
    sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    for actor in sub.get_all_level_actors():
        if actor and str(actor.get_actor_label()) == label:
            return actor
    return None


def _stage_close(a, b, tol=_TOL):
    return all(abs(float(x) - float(y)) <= tol for x, y in zip(a, b))


def resolve_level(name, suffix=None):
    """The level path this run targets. `suffix` appends to the ASSET NAME, keeping the package in the
    same gitignored folder — /Game/MT/Army/Lvl_MT_Army + 'Proof' -> /Game/MT/Army/Lvl_MT_ArmyProof."""
    path = STAGES[name]['level']
    return path + suffix if suffix else path


def _umap_on_disk(level_path):
    rel = level_path[len('/Game/'):] + '.umap'
    content = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_content_dir())
    return os.path.join(content, *rel.split('/')).replace('\\', '/')


def _require_gitignored_target(level_path):
    """REFUSE to author a level whose .umap would not be git-ignored. Runs BEFORE new_level().

    Same guard, same incident, as metahuman_bind._require_gitignored_level — once, a script that
    operated on "whatever level is loaded" spawned into and SAVED the stock, git-tracked
    /Game/ThirdPerson/Lvl_ThirdPerson. This one checks the TARGET rather than the loaded world,
    because here the write IS the creation, and `git check-ignore` answers for paths that do not
    exist yet — which is exactly the case that matters on a clean checkout.

    FAILS CLOSED: if git cannot be run, or the path is not under /Game, this raises rather than
    assuming the write is safe. An unverifiable safety property is not a safety property.
    """
    import subprocess
    if not level_path.startswith('/Game/'):
        raise RuntimeError('stage_author: refusing a non-/Game target %r' % level_path)
    umap = _umap_on_disk(level_path)
    repo = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir())
    try:
        rc = subprocess.call(['git', 'check-ignore', '--quiet', umap], cwd=repo,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except Exception as exc:
        raise RuntimeError('stage_author: could not run `git check-ignore` (%s) — failing CLOSED '
                           'rather than assuming %s is safe to write' % (exc, level_path))
    if rc != 0:
        raise RuntimeError(
            'REFUSING TO AUTHOR: %s is NOT git-ignored (git check-ignore exit %d, file %s). Every '
            'stage this file builds must live under a machine-local gitignored tree — they reference '
            'the gitignored Khaimera pawn and MetaHuman body, and a stage saved into a tracked tree would publish them.'
            % (level_path, rc, umap))
    return umap


# =====================================================================================================
# author
# =====================================================================================================
def _stage_apply(actor, spec):
    """Place one actor and set everything the spec names. Returns what was applied, for the readback."""
    applied = {'label': spec['label'], 'class': str(actor.get_class().get_path_name())}
    root = actor.root_component
    # Mobility LAST, transform FIRST: a STATIC component refuses to be moved, so the final mobility
    # is written only once the actor is on its mark. (Spawn already placed loc/rot; scale is separate.)
    if root is not None:
        root.set_mobility(unreal.ComponentMobility.MOVABLE)
    actor.set_actor_location_and_rotation(_stage_vec(spec['loc']), _stage_rot(spec['rot']), False, True)
    actor.set_actor_scale3d(_stage_vec(spec['scale']))

    if 'static_mesh' in spec:
        smc = actor.get_component_by_class(unreal.StaticMeshComponent)
        mesh = _stage_load_object(spec['static_mesh'])
        if mesh is None:
            raise RuntimeError('stage_author: floor mesh %s did not load' % spec['static_mesh'])
        smc.set_static_mesh(mesh)
        mat = _stage_load_object(spec['material'])
        if mat is None:
            raise RuntimeError('stage_author: floor material %s did not load' % spec['material'])
        smc.set_material(0, mat)
        applied['static_mesh'] = spec['static_mesh']
        applied['material'] = spec['material']

    if 'light' in spec:
        comp = actor.get_component_by_class(unreal.DirectionalLightComponent)
        for key, value in spec['light'].items():
            comp.set_editor_property(key, value)
        applied['light'] = dict(spec['light'])

    if 'sky_light' in spec:
        comp = actor.get_component_by_class(unreal.SkyLightComponent)
        for key, value in spec['sky_light'].items():
            comp.set_editor_property(key, value)
        applied['sky_light'] = dict(spec['sky_light'])

    if 'auto_possess_ai' in spec:
        # PLACED_IN_WORLD is what gives a level-placed pawn an AIController, which is what runs its
        # CharacterMovementComponent — without it the drivers' velocity writes go nowhere. Set
        # explicitly rather than inherited from the pawn CDO, so the stage does not depend on a
        # Blueprint default that a later edit could change.
        actor.set_editor_property('auto_possess_ai',
                                  getattr(unreal.AutoPossessAI, spec['auto_possess_ai']))
        applied['auto_possess_ai'] = spec['auto_possess_ai']

    if root is not None:
        root.set_mobility(_MOBILITY[spec['mobility']])
        applied['mobility'] = spec['mobility']
    return applied


def _stage_readback(spec):
    """Re-read one placed actor and return every property this file claims to have set. The caller
    compares this against the spec — `_apply` returning without raising is not the property."""
    actor = _stage_by_label(spec['label'])
    if actor is None:
        return {'label': spec['label'], 'status': 'MISSING'}
    row = {'label': spec['label'], 'status': 'ok',
           'class': str(actor.get_class().get_path_name()),
           'loc': _stage_as_list(actor.get_actor_location()),
           'rot': _stage_rot_as_list(actor.get_actor_rotation()),
           'scale': _stage_as_list(actor.get_actor_scale3d()),
           'mobility': str(actor.root_component.get_editor_property('mobility')).split('.')[-1]
                       .split(':')[0] if actor.root_component else None}
    if 'static_mesh' in spec:
        smc = actor.get_component_by_class(unreal.StaticMeshComponent)
        mesh = smc.get_editor_property('static_mesh') if smc else None
        row['static_mesh'] = str(mesh.get_path_name()) if mesh else None
        mat = smc.get_material(0) if smc else None
        row['material'] = str(mat.get_path_name()) if mat else None
    if 'light' in spec:
        comp = actor.get_component_by_class(unreal.DirectionalLightComponent)
        row['light'] = {k: comp.get_editor_property(k) for k in spec['light']}
    if 'sky_light' in spec:
        comp = actor.get_component_by_class(unreal.SkyLightComponent)
        row['sky_light'] = {k: comp.get_editor_property(k) for k in spec['sky_light']}
    if 'auto_possess_ai' in spec:
        row['auto_possess_ai'] = str(actor.get_editor_property('auto_possess_ai')).split('.')[-1] \
            .split(':')[0]
    return row


def _stage_diff(spec, row):
    """Every way the readback can fail to match the spec, named. Empty list == matches."""
    if row.get('status') == 'MISSING':
        return ['actor is absent from the saved level']
    bad = []
    if row['class'] != spec['class']:
        bad.append('class %s != %s' % (row['class'], spec['class']))
    if not _stage_close(row['loc'], spec['loc']):
        bad.append('loc %s != %s' % (row['loc'], spec['loc']))
    if not _stage_close(row['rot'], spec['rot']):
        bad.append('rot %s != %s' % (row['rot'], spec['rot']))
    if not _stage_close(row['scale'], spec['scale']):
        bad.append('scale %s != %s' % (row['scale'], spec['scale']))
    if row.get('mobility') != spec['mobility']:
        bad.append('mobility %s != %s' % (row.get('mobility'), spec['mobility']))
    for key in ('static_mesh', 'material', 'auto_possess_ai'):
        if key in spec and row.get(key) != spec[key]:
            bad.append('%s %s != %s' % (key, row.get(key), spec[key]))
    for group in ('light', 'sky_light'):
        if group in spec:
            for key, want in spec[group].items():
                got = row.get(group, {}).get(key)
                same = (abs(float(got) - float(want)) <= 1e-4
                        if isinstance(want, float) else got == want)
                if not same:
                    bad.append('%s.%s %r != %r' % (group, key, got, want))
    return bad


def author_one(name, suffix=None, force=False):
    """Build ONE stage from nothing and prove the SAVED level holds it.

    Every handle in here is a local. new_level() is the first statement that touches a world, and it
    runs before any actor handle exists — GOTCHA-A'.
    """
    spec = STAGES[name]
    level_path = resolve_level(name, suffix)
    _require_gitignored_target(level_path)                 # BEFORE new_level: the write is the creation

    existed = unreal.EditorAssetLibrary.does_asset_exist(level_path)
    if existed and not force:
        raise RuntimeError(
            'REFUSING TO OVERWRITE %s: new_level() destroys the level that is there, and locally '
            'that level may be the only thing a shipped take can be re-rendered from. Author beside '
            'it with STAGE_SUFFIX (e.g. %r -> %s), or pass STAGE_FORCE=True if replacing it really is '
            'the intent.' % (level_path, 'Proof', resolve_level(name, 'Proof')))

    les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    # is_partitioned_world=False: all four shipped stages are non-partitioned (no
    # Content/__ExternalActors__/MT/... trees exist for any of them), and a partitioned world would
    # change how MRQ streams the stage in.
    if not les.new_level(level_path, False):
        raise RuntimeError('stage_author: new_level(%s) returned False' % level_path)

    sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    placed, missing = [], []
    for actor_spec in spec['actors']:
        cls = _stage_load_class(actor_spec['class'])
        if cls is None:
            # Only ever legitimate for the gitignored-content pawns, which carry `optional_why`.
            if 'optional_why' not in actor_spec:
                raise RuntimeError('stage_author: class %s did not load and is not optional'
                                   % actor_spec['class'])
            unreal.log_warning('STAGE %s: %s (%s) is absent on this machine — skipped. %s'
                               % (name, actor_spec['label'], actor_spec['class'],
                                  actor_spec['optional_why']))
            missing.append(actor_spec['label'])
            continue
        actor = sub.spawn_actor_from_class(cls, _stage_vec(actor_spec['loc']), _stage_rot(actor_spec['rot']))
        if actor is None:
            raise RuntimeError('stage_author: spawn of %s failed' % actor_spec['label'])
        actor.set_actor_label(actor_spec['label'])
        placed.append(_stage_apply(actor, actor_spec))
        actor = None                                       # GOTCHA-A': drop it now, not at return

    ws = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world().get_world_settings()
    if spec['game_mode']:
        gm = _stage_load_class(spec['game_mode'])
        if gm is None:
            raise RuntimeError('stage_author: game mode %s did not load' % spec['game_mode'])
        ws.set_editor_property('default_game_mode', gm)
    ws = None

    if not les.save_current_level():
        raise RuntimeError('stage_author: save_current_level() returned False for %s' % level_path)

    # ---- the readback. Everything above is a claim; this is the evidence. --------------------------
    problems = {}
    rows = []
    for actor_spec in spec['actors']:
        if actor_spec['label'] in missing:
            continue
        row = _stage_readback(actor_spec)
        rows.append(row)
        bad = _stage_diff(actor_spec, row)
        if bad:
            problems[actor_spec['label']] = bad

    # EXACTLY ONE DirectionalLight, or PIE prints "Multiple directional lights competing" on
    # screen — over the frames. Counted, not assumed, because a re-run that double-placed would look
    # fine in every per-actor check above.
    suns = [a for a in sub.get_all_level_actors() if a and a.get_component_by_class(
        unreal.DirectionalLightComponent) is not None]
    if len(suns) != 1:
        problems['_directional_lights'] = ['found %d, the stage requires exactly 1' % len(suns)]
    suns = None

    strays = sorted(str(a.get_actor_label()) for a in sub.get_all_level_actors()
                    if a and str(a.get_actor_label()) not in
                    [s['label'] for s in spec['actors']] + spec['delegated_actors'])
    if strays:
        problems['_strays'] = strays

    gm_now = stage_game_mode()
    if gm_now != spec['game_mode']:
        problems['_game_mode'] = ['%s != %s' % (gm_now, spec['game_mode'])]

    out = {'action': 'author', 'stage': name, 'level': level_path, 'overwrote_existing': bool(existed),
           'placed': len(placed), 'missing_optional': missing, 'game_mode': gm_now,
           'umap': _umap_on_disk(level_path), 'readback': rows, 'problems': problems}
    unreal.log('STAGE ' + json.dumps(out))
    if problems:
        raise RuntimeError('stage_author: %s SAVED but does not match its spec: %s'
                           % (level_path, json.dumps(problems)))
    return out


def stage_game_mode():
    ws = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world().get_world_settings()
    gm = ws.get_editor_property('default_game_mode')
    return str(gm.get_path_name()) if gm else None


# =====================================================================================================
# report — diff the LOADED world against a named spec
# =====================================================================================================
def report_one(name):
    """Compare whatever level is currently loaded against STAGES[name]. This is how the shipped stage
    and a rebuilt one are shown to be the same thing: run it on each, diff the two `STAGE` lines."""
    spec = STAGES[name]
    sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    rows, problems = [], {}
    for actor_spec in spec['actors']:
        row = _stage_readback(actor_spec)
        rows.append(row)
        bad = _stage_diff(actor_spec, row)
        if bad:
            problems[actor_spec['label']] = bad
    known = [s['label'] for s in spec['actors']] + spec['delegated_actors']
    extra = sorted(str(a.get_actor_label()) for a in sub.get_all_level_actors()
                   if a and str(a.get_actor_label()) not in known)
    present_delegated = sorted(lbl for lbl in spec['delegated_actors'] if _stage_by_label(lbl) is not None)
    out = {'action': 'report', 'stage': name, 'loaded_world': str(world.get_path_name()),
           'matches_spec': not problems, 'problems': problems, 'readback': rows,
           'delegated_present': present_delegated, 'delegated_expected': spec['delegated_actors'],
           'unexpected_actors': extra, 'game_mode': stage_game_mode()}
    unreal.log('STAGE ' + json.dumps(out))
    return out


def spec_report():
    """Print the spec without touching a world — for a clean checkout that wants to know what the
    stages ARE before it has an editor pointed at one."""
    out = {'action': 'spec', 'stages': {
        n: {'level': s['level'], 'game_mode': s['game_mode'], 'shots': s['shots'],
            'actors': [a['label'] for a in s['actors']],
            'delegated_actors': s['delegated_actors']} for n, s in STAGES.items()}}
    unreal.log('STAGE ' + json.dumps(out))
    return out


# =====================================================================================================
# dispatch. POP, do not get — run_python execs into a PERSISTENT namespace, so a `get` inherits the
# previous call's STAGE_SUFFIX/STAGE_FORCE whenever this one forgot to set it. army_spawn.py learned
# that with ARMY_COUNTS (a "full army" spawn silently came back as 12); the same mistake here would be
# a FORCE flag surviving into a call that meant to refuse, which is the one flag in this file whose
# accidental persistence destroys a stage. An unset name is None, never stale.
# =====================================================================================================
_action = globals().pop('STAGE_ACTION', None)
_names = globals().pop('STAGE_NAMES', None)
_suffix = globals().pop('STAGE_SUFFIX', None)
_force = bool(globals().pop('STAGE_FORCE', False))
_selected = list(_names) if _names else list(STAGES)

if _action == 'author':
    for _n in _selected:
        author_one(_n, suffix=_suffix, force=_force)
        # A second new_level() in the same call is a second map change; make sure the first stage's
        # locals are actually reaped before it happens (GOTCHA-A').
        gc.collect()
elif _action == 'report':
    for _n in _selected:
        report_one(_n)
elif _action == 'spec':
    spec_report()
elif _action is not None:
    raise ValueError('stage_author: unknown STAGE_ACTION %r (author | report | spec)' % _action)
