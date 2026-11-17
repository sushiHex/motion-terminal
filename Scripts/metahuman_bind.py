# Bind the parametric MetaHuman body to the SHARED Template, place the 4-up, and
# PROVE the bind animates rather than merely resolving.
#
# WHAT THE SHARED-TEMPLATE BIND CLAIMS, AND WHAT THIS FILE HAS TO EARN. An offline comparison of the
# MetaHuman body skeleton against SK_Mannequin found the bind VIABLE — it was re-measured live at 150 shared bone names
# with `hierarchy_matches: true`, the MetaHuman missing exactly the
# mannequin's virtual/IK bones: weapon_l, weapon_r, ik_hand_root, ik_hand_gun, ik_hand_l, ik_hand_r,
# ik_foot_root, ik_foot_l, ik_foot_r, interaction, center_of_mass. VIABILITY IS NOT FUNCTION. This project once shipped a
# fully-wired foot-IK gate around a rig that wrote zero displacement, so "the class resolved and the graph
# compiled" is not evidence of anything here. The `probe` action below is the only thing in this file that
# can fail honestly, and it is the reason the file exists.
#
# ============================================================================================
# WHY THE COMPATIBILITY IS DECLARED IN MEMORY AND NEVER SAVED
#
# `USkeletalMeshComponent::NeedToSpawnAnimScriptInstance` gates the AnimBP on
#     SkelMesh->GetSkeleton()->IsCompatibleForEditor(AnimClass->GetTargetSkeleton())
# so the declaration has to live on the MESH's skeleton — the consumer — not on the AnimBP's. For this
# roster that is:
#     MetaHuman body : /MetaHumanCharacter/Female/Medium/NormalWeight/Body/metahuman_base_skel
#     Template       : /Game/Characters/Mannequins/Meshes/SK_Mannequin
# The declaration could go on SK_Mannequin or the other way round; SK_Mannequin is STOCK content and is off-limits, so the
# direction the engine needs is also the only direction allowed. But the MetaHuman body skeleton is not a
# project asset either — it resolves to
#     C:/Program Files/Epic Games/UE_5.8/Engine/Plugins/MetaHuman/MetaHumanCharacter/Content/...
# i.e. the ENGINE INSTALL, shared by every project on this machine. Saving a compatibility list into it
# would be a worse edit than the stock one the constraint forbids, and it would not travel with the repo.
#
# Re-pointing the mesh at a project-local duplicate of that skeleton was tried first and is NOT reachable:
# `USkeletalMesh::Skeleton` is VisibleAnywhere (CPF_EditConst), so Python refuses it
# ("Property 'Skeleton' ... is read-only and cannot be set") and no Python-exposed subsystem
# (SkeletalMeshEditorSubsystem, EditorSkeletalMeshLibrary) offers an assign-skeleton entry point.
#
# So `declare` calls Skeleton::AddCompatibleSkeleton on the live object and NEVER saves it. That is not a
# workaround dressed up as a design: the declaration is a one-line, re-runnable, reviewable mechanism that
# lives in this committed file, and the render happens in the same editor session that ran it.
#
# AND THE EDIT CANNOT LEAK, MEASURED RATHER THAN HOPED. The worry with an unsaved in-memory change is that
# some later save-all flushes it into the engine install. It cannot: AddCompatibleSkeleton does not mark
# its package dirty, so nothing will ever pick it up. Controlled directly in one session — duplicate the same
# skeleton into /Game (so the reader is known to cover the mount), SAVE it to reach a clean baseline, then
# call AddCompatibleSkeleton and re-read: the dirty list stayed EMPTY while `compatible_skeletons` came back
# holding SK_Mannequin. Changed but not dirty.
#
# WHAT THE `dirty_packages_game_scope` FIELD BELOW IS AND IS NOT. It is NOT a canary for the engine-install
# package, and there is deliberately no per-package boolean beside it: `get_dirty_content_packages()` is not
# known to cover the /MetaHumanCharacter engine-plugin mount, so such a flag would read False whether or not
# that package was dirty — the two are indistinguishable from here. The load-bearing evidence for
# "cannot leak" is the /Game duplicate positive control described above, which is a session action, not
# something this file re-runs. If you need the safety property re-proven, re-run that control; do not read
# a False here as proof.
#
# RUN `declare` ONCE PER EDITOR SESSION, BEFORE `place` OR ANY RENDER. The declaration is in memory only, so
# a fresh editor has it not. `place()` does not call or check it — a pawn bound without it silently gets no
# anim instance and renders in ref pose.
#
# AND RE-RUN `place` AFTER EVERY `Scripts/khai_inplace_author.py` `author`. That action deletes
# and re-duplicates the in-place ABP, so a level saved by an earlier `place` still points at the OLD, now
# dead class — and the Khaimera pawn fails the same silent way: no anim instance, ref pose, no error. The
# only symptom is `Roster_Khaimera:NO_ANIM_INSTANCE` in a probe log.
# ============================================================================================
#
# GOTCHA-MH-A: never `unreal.new_object(unreal.MetaHumanCharacter)` — it crashes the editor at
# SharedPointer.h:1133 via try_add_object_to_edit. Nothing here creates a MetaHuman asset; the body was
# already authored by Scripts/metahuman_body_author.py and this file only binds it.
#
# GOTCHA-A': run_python execs into a PERSISTENT namespace, so a surviving World/Level/Actor reference makes
# the next map change a fatal crash at EditorServer.cpp:2544. Every world/actor handle below is a local
# inside a function, and the dispatcher at the bottom keeps only strings.
#
# Usage (run in the editor through `Scripts/editor_ops.py` (`ops.run_file`)):
#     MHB_ACTION = 'declare'   # 'declare' | 'place' | 'sequence' | 'report' | 'probe'
#     exec(open(r'.../Scripts/metahuman_bind.py').read())
# Every action prints one `MHBIND <json>` line to LogPython.
import importlib
import json
import os
import sys

import unreal

# The shared driver scaffolding. Resolved off the .uproject, NOT off `__file__` — this
# module is loaded with `exec(open(...).read())` from run_python, where `__file__` is undefined (the
# same reason `_require_gitignored_level` resolves the repo root that way). Reloaded rather than
# plain-imported so an edit to mt_drive.py takes effect on the next run; see that module's header.
_MT_SCRIPTS = os.path.join(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()),
                           'Scripts')
if _MT_SCRIPTS not in sys.path:
    sys.path.insert(0, _MT_SCRIPTS)
_mt = importlib.reload(importlib.import_module('mt_drive'))

MH_MESH = '/Game/MetaHumans/MH_MT_Roster_FullBody'
MH_SKELETON = '/MetaHumanCharacter/Female/Medium/NormalWeight/Body/metahuman_base_skel'
TEMPLATE_SKELETON = '/Game/Characters/Mannequins/Meshes/SK_Mannequin'
TEMPLATE_ABP = 'ABP_MT_Locomotion_C'

MH_PAWN_CLASS = '/Game/MT/BP_MT_TestPawn.BP_MT_TestPawn_C'   # the Template pawn, mesh swapped per instance
MH_LABEL = 'Roster_MetaHuman'

# ---- Khaimera's render-time in-place swap ---------------------------------------------------
# The lineup take first shipped Khaimera STANDING because his Khai_* clips carry 1362.9 cm of travel on the
# pelvis. Non-destructive duplicates were then authored (Scripts/khai_inplace_author.py) with that travel pinned
# out, and a duplicate child ABP that consumes them. The swap is a PLACEMENT-time assignment of the
# component's `anim_class`, not an asset edit: /Game/MT/Khaimera/ABP_MT_Khaimera is never opened for
# write, so GoldenDiff.MTKhaimera and the six Verify.ClipVaries.Khai_* rows are untouched.
#
# `place(inplace=...)` is EXPLICIT in both directions — passing False actively restores the stock class
# rather than leaving whatever the last run wrote. The level is saved either way, so a half-configured
# stage cannot survive into the next render.
KHAI_LABEL = 'Roster_Khaimera'
KHAI_ABP_STOCK = '/Game/MT/Khaimera/ABP_MT_Khaimera.ABP_MT_Khaimera_C'
KHAI_ABP_INPLACE = '/Game/MT/Khaimera/InPlace/ABP_MT_Khaimera_InPlace.ABP_MT_Khaimera_InPlace_C'

# ---- the line-up, ASCENDING MEASURED HEIGHT, left to right on camera --------------------------------
# The principle is ascending height; the earlier lineup order was not
# ascending (Manny -> Quinn -> MetaHuman -> Khaimera == 180.2 / 180.5 / 158.4 / 209.9). The PRINCIPLE
# governs: the MetaHuman is the SHORT body this roster was missing, so it anchors the low end, and the
# shipped three keep their original order relative to each other.
#
# Heights are MEASURED, not nominal: boundsHeightUU straight out of Plugins/MotionTerminal/Tests/
# ScaleBaselines/*.json (metahuman.json written by this bind's own SnapshotScaleBaseline call).
#
# ONE CORRECTION, and it does not change the order. The earlier notes list "Manny 180.2 / Quinn
# 180.5"; the committed baselines say the opposite — manny.json 180.54390, quinn.json 180.16873. The two
# figures are transposed at source. Strictly ascending would therefore be
# MetaHuman -> Quinn -> Manny -> Khaimera, but the gap between the mannequin pair is 0.375 cm against a
# 51.5 cm spread (0.7% of it, well under a pixel at this framing), so swapping them would invert the original
# take's order for a difference no viewer can see. The order below is the original one; the
# heights are the ones the baselines measure. The 158.4 -> ~180.5 -> 209.9 spread is what reads.
LINEUP = [
    # (label, actor_class_path, mesh_override, y, height_cm)
    (MH_LABEL, MH_PAWN_CLASS, MH_MESH, -270.0, 158.4),
    ('Roster_Manny', '/Game/MT/BP_MT_TestPawn.BP_MT_TestPawn_C', None, -90.0, 180.5),
    ('Roster_Quinn', '/Game/MT/BP_MT_TestPawn_Quinn.BP_MT_TestPawn_Quinn_C', None, 90.0, 180.2),
    ('Roster_Khaimera', '/Game/MT/Khaimera/BP_MT_TestPawn_Khaimera.BP_MT_TestPawn_Khaimera_C',
     None, 270.0, 209.9),
]
LINEUP_X = 0.0
LINEUP_Z = 110.0        # capsule half-height 88 + the roster stage's floor clearance
LINEUP_YAW = 180.0      # face -X, which is where Scripts/pie_roster4_drive.py parks the camera


def _load(path):
    try:
        return unreal.load_asset(path)
    except Exception:
        return None


def _mesh_of(actor):
    return actor.get_component_by_class(unreal.SkeletalMeshComponent) if actor else None


def _editor_actors():
    return unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()


def _by_label(label):
    for a in _editor_actors():
        if a and a.get_actor_label() == label:
            return a
    return None


def _dirty_packages():
    """Names of every dirty content package. `UPackage::IsDirty` is not exposed on the Python Package
    object, so the list is the only reachable read — and it is the one that matters here: this is what a
    save-all would write, which is exactly the thing `declare` must be able to prove it is not adding to."""
    try:
        return sorted(str(p.get_name()) for p in
                      unreal.EditorLoadingAndSavingUtils.get_dirty_content_packages())
    except Exception as exc:
        return ['<unreadable: %s>' % exc]


# =====================================================================================================
# declare
# =====================================================================================================
def declare():
    """Declare SK_Mannequin compatible ON the MetaHuman body skeleton. IN MEMORY ONLY — there is
    deliberately no save path: MH_SKELETON resolves into the UE 5.8 engine install, and writing there is
    forbidden. Run once per editor session before `place` or any render (see the header)."""
    mh_sk = _load(MH_SKELETON)
    tmpl_sk = _load(TEMPLATE_SKELETON)
    if mh_sk is None or tmpl_sk is None:
        raise RuntimeError('skeleton missing: mh=%s template=%s' % (mh_sk, tmpl_sk))

    def listed():
        return [str(s.get_path_name()) for s in (mh_sk.get_editor_property('compatible_skeletons') or [])]

    before = listed()
    already = any(TEMPLATE_SKELETON in p for p in before)
    if not already:
        mh_sk.add_compatible_skeleton(tmpl_sk)
    after = listed()

    # /Game-scope observation only. There is deliberately no 'mh_skeleton_package_dirty' boolean here:
    # get_dirty_content_packages() is not known to cover the /MetaHumanCharacter engine-plugin mount, so
    # such a flag would read False whether or not that package was dirty — a check that cannot observe
    # what it claims to check. See the header for the positive control that actually establishes no-leak.
    out = {'action': 'declare', 'skeleton': MH_SKELETON, 'declared': TEMPLATE_SKELETON,
           'was_already_declared': already, 'before': before, 'after': after,
           'dirty_packages_game_scope': _dirty_packages(),
           'saved': False, 'save_path_exists': False, 'engine_install_asset': True}
    unreal.log('MHBIND ' + json.dumps(out))
    return out


# =====================================================================================================
# place
# =====================================================================================================
def _require_gitignored_level():
    """REFUSE to touch the current editor world unless its .umap is git-IGNORED.

    THE INCIDENT — THIS GUARD EXISTS BECAUSE THE CONVENTION IT ENFORCES WAS VIOLATED.
    `place()` operates on whatever level happens to be loaded and then SAVES it, and its docstring has
    always asserted, as a safety property, that "the level lives under the gitignored machine-local
    Content/MT/Showcase/ tree, so saving it commits nothing". Nothing in the code checked that. Called
    without loading /Game/MT/Showcase/Lvl_MT_Roster first, it spawned the four roster pawns into the
    STOCK /Game/ThirdPerson/Lvl_ThirdPerson — a git-TRACKED asset the project constraints forbid writing
    — created four Content/__ExternalActors__/ThirdPerson/... folders, and saved. It was caught by
    `git status` before any commit and reverted, but a convention with no mechanism behind it is a
    convention that will be violated again.

    THE CHECK RUNS BEFORE THE SPAWN LOOP, NOT MERELY BEFORE THE SAVE, and that placement is the point:
    it was the SPAWN that created the __ExternalActors__ folders, so a guard that only protected
    `save_current_level()` would still have left litter in a stock content tree.

    FAILS CLOSED. If git is missing, or the repo cannot be resolved, or the world has no package file on
    disk, this raises rather than assuming the level is safe — an unverifiable safety property is not a
    safety property.
    """
    import os
    import subprocess

    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    if world is None:
        raise RuntimeError('_require_gitignored_level: no editor world')
    pkg = str(world.get_path_name()).split('.')[0]          # /Game/MT/Showcase/Lvl_MT_Roster
    if not pkg.startswith('/Game/'):
        raise RuntimeError('_require_gitignored_level: refusing a non-/Game world %r' % pkg)
    rel = pkg[len('/Game/'):] + '.umap'
    umap = os.path.join(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_content_dir()),
                        *rel.split('/'))
    if not os.path.isfile(umap):
        raise RuntimeError('_require_gitignored_level: no .umap on disk for %s (looked at %s) — cannot '
                           'establish that writing it is safe, so refusing' % (pkg, umap))
    # NOT __file__: this module is loaded with `exec(open(...).read())` from run_python, where __file__
    # is undefined. (The first version used it and the guard duly refused everything — failing CLOSED on
    # its own bug, which is the designed behaviour, but not a useful guard.) The .uproject directory IS
    # the repo root for this project and is always available.
    repo = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir())
    try:
        rc = subprocess.call(['git', 'check-ignore', '--quiet', umap], cwd=repo,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except Exception as exc:
        raise RuntimeError('_require_gitignored_level: could not run `git check-ignore` (%s) — failing '
                           'CLOSED rather than assuming %s is safe to write' % (exc, pkg))
    if rc != 0:
        raise RuntimeError(
            'REFUSING TO PLACE: the loaded level %s is NOT git-ignored (git check-ignore exit %d, file '
            '%s). place() spawns actors into the CURRENT world and saves it, so running here would write '
            'tracked — possibly STOCK — content. This is the exact incident the guard exists for. Load the '
            'machine-local stage first, e.g. the level /Game/MT/Showcase/Lvl_MT_Roster.'
            % (pkg, rc, umap))
    return pkg


def place(save=True, inplace=False):
    """Idempotently put the four roster pawns on their line-up marks in the EDITOR world.

    `inplace` selects which child ABP the Khaimera pawn's mesh component carries: the stock
    `ABP_MT_Khaimera_C`, or the in-place `ABP_MT_Khaimera_InPlace_C` whose BlendSpace samples the
    travel-pinned duplicate clips. It is written on the COMPONENT (a UPROPERTY, so it survives the PIE
    duplication MRQ performs) and it is written on EVERY call in whichever direction was asked for.

    REQUIRES `declare` to have run in THIS editor session — it is in-memory only and this function neither
    calls nor checks it. Without it the MetaHuman pawn silently gets no anim instance and renders ref-pose.

    MUTATES THE ROSTER STAGE: this repositions all four pawns and saves Lvl_MT_Roster, so the level no longer
    holds its original actor layout. Seq_RosterLineup is untouched and the opening take ships from
    already-captured frames, so nothing on disk is invalidated — but "the original take keeps its provenance" is true of the
    sequence asset, not of the stage it points at.

    Spawned into the editor world BEFORE PIE on purpose: EditorActorSubsystem.spawn_actor_from_class
    refuses while PIE is live, and every knob set here (skeletal_mesh_asset,
    visibility_based_anim_tick_option) is a UPROPERTY, so it survives the PIE duplication MRQ performs.

    SAVE DEFAULTS TRUE, and it is not tidiness. MRQ's job carries `map = SoftObjectPath(level)` and the
    PIE executor LOADS that map — an unsaved editor-world spawn is not guaranteed to be in the world the
    render actually opens, so the MetaHuman could simply be absent from the frames while every log line
    here read green. The level must therefore live under a gitignored machine-local tree so that saving
    it commits nothing — and that is ENFORCED by `_require_gitignored_level()` below
    rather than merely asserted here, because the unenforced version of this sentence is exactly what
    let a stock-content write happen. See that function's docstring for the incident.
    """
    _require_gitignored_level()          # BEFORE the spawn loop — the spawn is what litters __ExternalActors__
    sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    rows = []
    for label, cls_path, mesh_override, y, height in LINEUP:
        actor = _by_label(label)
        spawned = False
        if actor is None:
            cls = unreal.load_class(None, cls_path)
            if cls is None:
                rows.append({'label': label, 'status': 'CLASS_MISSING', 'class': cls_path})
                continue
            actor = sub.spawn_actor_from_class(cls, unreal.Vector(LINEUP_X, y, LINEUP_Z),
                                               unreal.Rotator(0.0, 0.0, LINEUP_YAW))
            if actor is None:
                rows.append({'label': label, 'status': 'SPAWN_FAILED', 'class': cls_path})
                continue
            actor.set_actor_label(label)
            spawned = True
        actor.set_actor_location_and_rotation(
            unreal.Vector(LINEUP_X, y, LINEUP_Z), unreal.Rotator(0.0, 0.0, LINEUP_YAW), False, True)

        row = {'label': label, 'status': 'spawned' if spawned else 'moved', 'y': y,
               'height_cm': height, 'class': str(actor.get_class().get_name())}
        mesh = _mesh_of(actor)
        if mesh is None:
            row['status'] = 'NO_SKELETAL_MESH_COMPONENT'
        else:
            if mesh_override:
                target = _load(mesh_override)
                if target is None:
                    row['mesh_override'] = 'MISSING:' + mesh_override
                else:
                    mesh.set_skeletal_mesh_asset(target)
                    row['mesh_override'] = mesh_override
            if label == KHAI_LABEL:
                want = KHAI_ABP_INPLACE if inplace else KHAI_ABP_STOCK
                want_cls = unreal.load_class(None, want)
                if want_cls is None:
                    row['anim_class_override'] = 'MISSING:' + want
                else:
                    mesh.set_editor_property('anim_class', want_cls)
                    row['anim_class_override'] = want
            # The viewport in an automated session is headless (0x0); without this the off-screen pose tick is culled
            # and a rig can silently stop animating mid-take (found on the army stage).
            mesh.set_editor_property(
                'visibility_based_anim_tick_option',
                unreal.VisibilityBasedAnimTickOption.ALWAYS_TICK_POSE_AND_REFRESH_BONES)
            sm = mesh.get_editor_property('skeletal_mesh_asset')
            row['mesh'] = str(sm.get_path_name()) if sm else None
            row['mesh_skeleton'] = str(sm.get_editor_property('skeleton').get_path_name()) \
                if sm and sm.get_editor_property('skeleton') else None
            ac = mesh.get_editor_property('anim_class')
            row['anim_class'] = str(ac.get_name()) if ac else None
            # Reported, never assigned. The MetaHuman runs the shared Template because the Template PAWN
            # already carries it as a component default — if this script set it, "the MetaHuman runs the
            # Template" would be a statement about this script rather than about the bind.
            if mesh_override:
                row['on_shared_template'] = (row['anim_class'] == TEMPLATE_ABP)
        rows.append(row)
    saved = None
    if save:
        # Re-checked immediately before the write as well as before the spawn: a level change between the
        # two would otherwise slip past the entry guard.
        _require_gitignored_level()
        saved = bool(unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).save_current_level())
    unreal.log('MHBIND ' + json.dumps({'action': 'place', 'inplace': bool(inplace), 'rows': rows,
                                       'level_saved': saved}))
    return rows


# =====================================================================================================
# sequence — the 4-up's own render sequence
# =====================================================================================================
# An honest gap was recorded: "the STAGE is not [rebuildable], there is no stage-authoring script." This
# action closes that gap for everything this bind adds to the machine-local Content/MT/Showcase/ folder, so the whole
# delta (pawn placement + sequence) is reproducible from this committed file rather than from a one-off
# console paste. The REST of that gap — the room those pawns stand in — closed with
# `Scripts/stage_author.py` roster builds `Lvl_MT_Roster` from `new_level()`, and `place()` below is
# what puts the four rigs on their marks in it. Rebuild order is stage_author `author` -> `declare` ->
# `place`, which is how the stage's proof render was shot. Seq_RosterLineup is left untouched — it is what the
# original take was shot through, and a re-timed sequence would silently invalidate that footage's provenance.
SEQ_PATH = '/Game/MT/Showcase/Seq_Roster4Up'
SEQ_FPS = 30            # 900 frames == 30.0 s of real time; the manifest's fps_in for this shot
SEQ_FRAMES = 900


def sequence():
    """Create (or re-point) the 30 fps / 900-frame sequence with a spawnable CineCameraActor + cut.

    NO transform track: the camera is driven live by Scripts/pie_roster4_drive.py inside the MRQ PIE
    world, the proven pattern of every capture driver here. A transform track here would fight the driver.
    """
    if not unreal.EditorAssetLibrary.does_asset_exist(SEQ_PATH):
        unreal.AssetToolsHelpers.get_asset_tools().create_asset(
            asset_name=SEQ_PATH.rsplit('/', 1)[1], package_path=SEQ_PATH.rsplit('/', 1)[0],
            asset_class=unreal.LevelSequence, factory=unreal.LevelSequenceFactoryNew())
    seq = unreal.load_asset(SEQ_PATH)
    seq.set_display_rate(unreal.FrameRate(SEQ_FPS, 1))
    seq.set_playback_start(0)
    seq.set_playback_end(SEQ_FRAMES)

    cam_binding = None
    for b in seq.get_bindings():
        if 'Camera' in str(b.get_display_name()):
            cam_binding = b
            break
    if cam_binding is None:
        cam_binding = seq.add_spawnable_from_class(unreal.CineCameraActor)

    cut = None
    for t in seq.get_tracks():
        if isinstance(t, unreal.MovieSceneCameraCutTrack):
            cut = t
            break
    if cut is None:
        cut = seq.add_track(unreal.MovieSceneCameraCutTrack)
    for s in list(cut.get_sections()):
        cut.remove_section(s)
    sec = cut.add_section()
    sec.set_range(0, SEQ_FRAMES)
    binding_id = unreal.MovieSceneObjectBindingID()
    binding_id.set_editor_property('guid', cam_binding.get_id())
    sec.set_editor_property('camera_binding_id', binding_id)

    # GUARDED AND CHECKED. This was a bare `save_asset(..., only_if_is_dirty=False)`
    # with its bool return DISCARDED and no git-ignore guard — the last such writer in this file, in the
    # module whose `place()` opens with `_require_gitignored_level()` because of the stock-level incident
    # (a script that saved the stock, git-tracked /Game/ThirdPerson/Lvl_ThirdPerson). SEQ_PATH is a
    # file-scope constant under the gitignored Content/MT/Showcase/, so nothing was at risk today; a
    # safety pattern with exemptions is one people learn to leave off. `mt_drive.require_saved_gitignored`
    # is the ONE implementation, shared with `shot_seq_author`, which authors every other sequence in
    # this project.
    #
    # THE FIRST VERSION OF THIS LINE WAS A REGRESSION AND IT SHIPPED GREEN BECAUSE NOTHING RAN IT.
    # It read
    # `importlib.reload(importlib.import_module('shot_seq_author'))._require_saved_gitignored(SEQ_PATH)`,
    # copied from this file's own `mt_drive` loader at :90 — safe there because `mt_drive` has no
    # import-time side effects, fatal here because `shot_seq_author`'s module body ends in a mandatory
    # dispatcher that RAISES on an unset `SEQ_ACTION` (which it pops, so a reload always sees None) and
    # `importlib.reload` re-executes the body. `sequence()` therefore raised at its last statement and
    # Seq_Roster4Up was never saved at all — strictly worse than the unguarded `save_asset` it replaced.
    # A fix that is never run is not a fix; this one was executed against the live editor and the
    # sequence re-read field-identical afterwards.
    _mt.require_saved_gitignored(SEQ_PATH, 'metahuman_bind')
    unreal.log('MHBIND ' + json.dumps({
        'action': 'sequence', 'path': SEQ_PATH, 'fps': SEQ_FPS,
        'range': [seq.get_playback_start(), seq.get_playback_end()],
        'bindings': [str(b.get_display_name()) for b in seq.get_bindings()],
        'master_tracks': [str(t.get_class().get_name()) for t in seq.get_tracks()],
        'cut_sections': len(cut.get_sections())}))
    return SEQ_PATH


# =====================================================================================================
# report
# =====================================================================================================
def report():
    mh_sk = _load(MH_SKELETON)
    mh_mesh = _load(MH_MESH)
    out = {'action': 'report'}
    if mh_sk:
        out['compatible_skeletons'] = [
            str(s.get_path_name()) for s in (mh_sk.get_editor_property('compatible_skeletons') or [])]
        # /Game-scope only — see declare(); a False here is not evidence about the engine-plugin mount.
        out['dirty_packages_game_scope'] = _dirty_packages()
    if mh_mesh:
        b = mh_mesh.get_bounds()
        out['mh_measured_height_uu'] = round(b.box_extent.z * 2.0, 3)
    out['placed'] = []
    for label, _cls, _m, y, height in LINEUP:
        a = _by_label(label)
        rec = {'label': label, 'present': a is not None, 'height_cm': height, 'want_y': y}
        if a:
            loc = a.get_actor_location()
            rec['at'] = [round(loc.x, 1), round(loc.y, 1), round(loc.z, 1)]
            mesh = _mesh_of(a)
            if mesh:
                sm = mesh.get_editor_property('skeletal_mesh_asset')
                rec['mesh'] = str(sm.get_name()) if sm else None
                ac = mesh.get_editor_property('anim_class')
                rec['anim_class'] = str(ac.get_name()) if ac else None
        out['placed'].append(rec)
    unreal.log('MHBIND ' + json.dumps(out))
    return out


# =====================================================================================================
# probe — THE LOAD-BEARING STEP
# =====================================================================================================
#
# WHAT WOULD MAKE THIS PROBE WORTHLESS, and what it does instead.
#
# 1. "SetAnimVariable('Speed', 450) returned ok" proves nothing. `Speed` is DERIVED every tick by
#    ABP_MT_Locomotion's EventBlueprintUpdateAnimation as VectorLength(GetVelocity(pawn)), so a reflection
#    write reads back correctly and is overwritten before the state machine evaluates (see the
#    `Speed` finding in army_conductor.py). This probe drives the SOURCE: CharacterMovementComponent.Velocity, with MovementMode
#    MOVE_None so nothing re-integrates it, and set_component_tick_enabled(False) applied LAST because the
#    reflected velocity write RE-ENABLES the component tick (measured; see mt_drive.drive_cmc).
#
# 2. "The anim instance exists / the graph compiled" proves nothing — that is exactly the no-op-rig
#    failure above. So the pass condition has TWO independent halves and BOTH must hold:
#      (a) the `Locomotion` machine's dominant state is a MOVING one (not Idle) — read out of the graph's
#          own weights via AnimDebugTools::GetAnimDebugState, not restated from what this file wrote;
#      (b) a sampled bone's WORLD Z genuinely VARIES across the take (the real-output clip-varies doctrine). A
#          frozen ref-pose satisfies (a)'s state read on a graph that evaluates but never moves a bone.
#
# 3. The threshold is fixed BEFORE the run and is not tuned afterwards. hand_r's world Z swings tens of
#    centimetres in any real gait; PROBE_MIN_Z_RANGE_CM is set two orders of magnitude below that, so it
#    separates "animating" from "frozen" and nothing finer. If the probe reports FROZEN, the honest
#    outcome is a 3-up and a recorded degrade — the MetaHuman is optional by design and must never block
#    the shot sequence. Do not tune this number to make a red go green.
#
# 4. THE RULE THE FROZEN FEET EARNED, AND WHY THIS PROBE NOW SAMPLES FEET.
#    Everything above was true and both halves were honest — and NEITHER COULD FAIL ON THE DEFECT THAT
#    ACTUALLY SHIPPED. The four-rig lineup take was captioned "4 rigs jogging" while the MetaHuman's feet were
#    pinned flat to the floor by the Template's foot-IK Control Rig solving onto ik_foot_l / ik_foot_r,
#    two bones metahuman_base_skel does not have. Its ARMS swung and its state machine read `Locomotion`,
#    so `hand_r` world Z passed at 45x threshold and the state read passed outright. A rig whose legs are
#    frozen and whose arms swing satisfies both halves by construction.
#
#    THE RULE: THE CLIP-VARIES PROBE MUST SAMPLE THE BONE THE CAPTION IS ABOUT. `hand_r` proves "the bind
#    resolves and evaluates". Only a FOOT bone proves "the rig jogs". So the verdict is now split in two
#    and reported per bone per rig, and a rig can fail on legs while passing on arms instead of collapsing
#    into one ANIMATES boolean:
#        ANIMATES  = moving state  AND  hand_r  world-Z range >= PROBE_MIN_Z_RANGE_CM       (the bind lives)
#        STRIDES   = moving state  AND  BOTH feet world-Z range >= PROBE_MIN_FOOT_Z_RANGE_CM (the legs work)
#
#    WHAT THAT `AND` ACTUALLY IS — A CORRECTION. Read the two lines above as a per-sample conjunction and they promise more than
#    `verdict()` delivers. Both halves are WHOLE-TAKE AGGREGATES: `moving` is scored against the SET of
#    states seen at any point in the take, and the Z range is max-minus-min over the whole take. So one
#    moving sample plus bone motion at some other, unrelated moment satisfies it — a temporal UNION, not
#    a per-sample AND. It is not a defect in what shipped (every recorded run held a single moving state
#    on every sample: `states_seen: ["Locomotion"]`, 26 of 26 for the MetaHuman), and it is deliberately
#    NOT tightened here, because tightening a predicate at a gate would change what the shipped numbers
#    mean. It is written down because this project kept catching one fault — prose describing
#    evidence as stronger than the code that produced it — and this comment was an instance of it.
#    If you need the strict reading, score per sample and report the worst sample, not the union.
#
#    PROBE_MIN_FOOT_Z_RANGE_CM IS FIXED BEFORE THE RUN, from the mannequins' own measured swing rather
#    than from whatever this session happens to produce: a mannequin swing foot at Speed 600 clears well
#    over 15 cm, so 10 cm separates "striding" from "planted" with ~2.5x margin on the pass side. It is
#    NOT a number tuned until the MetaHuman went green — the opposite: the pre-fix Template drives this
#    probe RED for the MetaHuman, and that proven-OFF reference is what makes a green mean anything.
#    A probe that only ever goes green proves nothing.
#
#    `foot_swing_clearance_cm` (per rig, REPORTED, NOT GATED) is the stronger idea and is deliberately not
#    a threshold: over the take, how far does min(foot_l.z, foot_r.z) ever rise above its own floor? A
#    planted rig answers ~0 permanently. It is not gated because this project has no prior measurement of
#    a mannequin's flight phase at this speed to fix a threshold against BEFORE the run, and inventing one
#    afterwards from the numbers this run produced is exactly the move the header above forbids.
PROBE_BONE = 'hand_r'
PROBE_MIN_Z_RANGE_CM = 0.5
PROBE_FOOT_BONES = ('foot_l', 'foot_r')
PROBE_MIN_FOOT_Z_RANGE_CM = 10.0
# Read back per rig so the skeleton-aware gate is OBSERVED rather than assumed. Absent (pre-fix ABP, or a
# child that never got the variable) reports as None instead of failing the probe.
PROBE_GATE_VAR = 'HasMannequinFootIKRig'
PROBE_SPEED = 450.0        # jog: comfortably inside BS_Idle_Walk_Run's Locomotion state
PROBE_SAMPLE_DT = 0.25
PROBE_TOTAL = 8.0
PROBE_SETTLE = 1.5         # let the graph leave its start state before anything is scored
MOVING_STATES = ('Locomotion', 'Jump', 'Fall', 'Land')


def probe(speed=None, total=None, bone=None, tag=''):
    """Register the slate pre-tick that drives + scores every roster pawn. Call, then start PIE.

    Prints `MHBIND PROBE ...` per sample and one `MHBIND VERDICT ...` json per rig once the take ends.
    `tag` is echoed into every verdict so two runs in one session cannot be confused in a log.
    """
    speed = PROBE_SPEED if speed is None else float(speed)
    total = PROBE_TOTAL if total is None else float(total)
    bone = PROBE_BONE if bone is None else bone
    bones = (bone,) + tuple(b for b in PROBE_FOOT_BONES if b != bone)

    gen = _mt.retire_and_claim('_MT_MHPROBE')

    # Strings/floats/dicts only — no world, no actor (GOTCHA-A').
    st = {'handle': None, 'last': -999.0, 'rigs': {}, 'done': False}

    def verdict():
        for lbl in sorted(st['rigs']):
            r = st['rigs'][lbl]
            per_bone = {}
            for b in bones:
                zs = r['z'].get(b, [])
                per_bone[b] = {
                    'n': len(zs),
                    'z_min': round(min(zs), 3) if zs else None,
                    'z_max': round(max(zs), 3) if zs else None,
                    'z_range_cm': round((max(zs) - min(zs)), 3) if zs else 0.0}
            zs = r['z'].get(bone, [])
            z_range = (max(zs) - min(zs)) if zs else 0.0
            foot_ranges = [per_bone[b]['z_range_cm'] for b in PROBE_FOOT_BONES if b in per_bone]
            worst_foot = min(foot_ranges) if foot_ranges else 0.0
            # Swing clearance: how far the LOWER of the two feet ever rises above its own floor.
            mins = r['foot_min']
            clearance = (max(mins) - min(mins)) if mins else 0.0
            moving = sorted(s for s in r['states'] if s in MOVING_STATES)
            animates = bool(moving) and z_range >= PROBE_MIN_Z_RANGE_CM
            strides = bool(moving) and bool(foot_ranges) and worst_foot >= PROBE_MIN_FOOT_Z_RANGE_CM
            unreal.log('MHBIND ' + json.dumps({
                'action': 'verdict', 'tag': tag, 'rig': lbl, 'anim_class': r['anim_class'],
                'mesh': r['mesh'], 'skeleton': r['skeleton'],
                'states_seen': sorted(r['states']), 'moving_states': moving,
                'bones': per_bone,
                'bone': bone, 'n_samples': len(zs),
                'bone_world_z_min': round(min(zs), 3) if zs else None,
                'bone_world_z_max': round(max(zs), 3) if zs else None,
                'bone_world_z_range_cm': round(z_range, 3),
                'threshold_cm': PROBE_MIN_Z_RANGE_CM,
                'worst_foot_z_range_cm': round(worst_foot, 3),
                'foot_threshold_cm': PROBE_MIN_FOOT_Z_RANGE_CM,
                'foot_swing_clearance_cm': round(clearance, 3),
                'gate_var': PROBE_GATE_VAR, 'gate_read': r['gate'],
                'pelvis_drift_from_actor_max_cm': round(r['drift'], 1),
                'speed_read_last': r['speed_read'],
                'ANIMATES': animates, 'STRIDES': strides}))

    def tick(_dt):
        try:
            if int(getattr(unreal, '_MT_DRIVE_GEN', 0)) != gen:
                if st['handle'] is not None:
                    unreal.unregister_slate_pre_tick_callback(st['handle'])
                    st['handle'] = None
                return
            world = unreal.EditorLevelLibrary.get_game_world()
            if not world:
                return
            now = unreal.SystemLibrary.get_game_time_in_seconds(world)
            due = (now - st['last']) >= PROBE_SAMPLE_DT
            line = []
            for a in unreal.GameplayStatics.get_all_actors_of_class(world, unreal.Character):
                m = a.get_component_by_class(unreal.SkeletalMeshComponent)
                inst = m.get_anim_instance() if m else None
                lbl = a.get_actor_label()
                if inst is None:
                    if due:
                        line.append('%s:NO_ANIM_INSTANCE' % lbl)
                    continue
                if 'ABP_MT' not in inst.get_class().get_name():
                    continue
                cmc = a.get_component_by_class(unreal.CharacterMovementComponent)
                if cmc:
                    # ORDER IS LOAD-BEARING: mode, then velocity, then tick-disable LAST.
                    # The measurements behind it are in mt_drive.drive_cmc.
                    _mt.drive_cmc(cmc, speed)
                if not due or now < PROBE_SETTLE:
                    continue
                r = st['rigs'].setdefault(lbl, {
                    'z': {}, 'foot_min': [], 'states': set(), 'drift': 0.0,
                    'speed_read': None, 'gate': None,
                    'anim_class': str(inst.get_class().get_name()),
                    'mesh': str(m.get_editor_property('skeletal_mesh_asset').get_name())
                            if m.get_editor_property('skeletal_mesh_asset') else None,
                    'skeleton': str(m.get_editor_property('skeletal_mesh_asset')
                                    .get_editor_property('skeleton').get_path_name())
                                if m.get_editor_property('skeletal_mesh_asset') else None})
                tools = _mt.anim_debug_tools()
                state = _mt.dominant_state(
                    str(tools.call_method('GetAnimDebugState', args=(lbl,))))
                if state:
                    r['states'].add(state)
                # Every bone the verdict scores is sampled on the SAME tick, so the feet and the hand
                # describe one pose rather than two runs that happen to sit beside each other.
                zs_now = {}
                for b in bones:
                    zb = float(m.get_socket_location(b).z)
                    r['z'].setdefault(b, []).append(zb)
                    zs_now[b] = zb
                feet_now = [zs_now[b] for b in PROBE_FOOT_BONES if b in zs_now]
                if feet_now:
                    r['foot_min'].append(min(feet_now))
                pel = m.get_socket_location('pelvis')
                loc = a.get_actor_location()
                r['drift'] = max(r['drift'], ((pel.x - loc.x) ** 2 + (pel.y - loc.y) ** 2) ** 0.5)
                raw = str(tools.call_method('GetAnimVariable', args=(lbl, 'Speed')))
                r['speed_read'] = raw.split('=', 1)[1].strip() if raw.startswith('Speed =') else raw
                graw = str(tools.call_method('GetAnimVariable', args=(lbl, PROBE_GATE_VAR)))
                r['gate'] = graw.split('=', 1)[1].strip() if graw.startswith(PROBE_GATE_VAR + ' =') \
                    else ('ABSENT' if 'no property' in graw else graw)
                line.append('%s:%s %s pelvisXY=%.0f gate=%s'
                            % (lbl, state or 'NO_MACHINE',
                               ' '.join('%s.z=%.2f' % (b, zs_now[b]) for b in bones),
                               r['drift'], r['gate']))
            if due:
                st['last'] = now
                unreal.log('MHBIND PROBE t=%.2f | %s' % (now, ' | '.join(line)))
            if now >= total and not st['done']:
                st['done'] = True
                verdict()
        except Exception as exc:
            unreal.log_warning('MHBIND probe err %s' % exc)

    st['handle'] = unreal.register_slate_pre_tick_callback(tick)
    unreal._MT_MHPROBE = st
    unreal.log('MHBIND ' + json.dumps({'action': 'probe', 'gen': gen, 'speed': speed, 'tag': tag,
                                       'total_s': total, 'bone': bone, 'bones': list(bones),
                                       'threshold_cm': PROBE_MIN_Z_RANGE_CM,
                                       'foot_threshold_cm': PROBE_MIN_FOOT_Z_RANGE_CM}))
    return gen


# POP, do not get: run_python execs into a PERSISTENT namespace, so a `get` would inherit the previous
# call's action whenever this one forgot to set it (the stale-ARMY_COUNTS gotcha in army_spawn.py).
_action = globals().pop('MHB_ACTION', None)
_kwargs = globals().pop('MHB_KWARGS', None) or {}
if _action == 'declare':
    # No **_kwargs here on purpose: declare() takes none, and the parameter that used to be splatted in
    # was a save flag that would have force-written an engine-install asset. Keep it unreachable.
    declare()
elif _action == 'place':
    place(**_kwargs)
elif _action == 'sequence':
    sequence()
elif _action == 'report':
    report()
elif _action == 'probe':
    probe(**_kwargs)
elif _action is not None:
    raise RuntimeError('MHB_ACTION is %r — use declare/place/sequence/report/probe' % (_action,))
