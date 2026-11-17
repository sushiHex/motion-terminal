# Driver for the foot-IK A/B take, node REMOVED vs node PRESENT, on the ramp.
#
# ============================================================================================
# PROVENANCE: THIS IS THE ORIGINAL FOOT-IK A/B CAPTURE HARNESS, PROMOTED OUT OF THE CAPTURE DIRECTORY
#
# The shipped foot-IK take comes from `captures/m4-footik-ab/B_node_present`, rendered by
# `render_footik_ab.py`. Describing the drive in prose alone, without a camera coordinate, makes that
# harness look unreconstructible. It is not: the harness sits in
# `captures/m4-footik-ab/` next to the frames it shot, with every constant in it —
#
#     CAMDX, CAMY, CAMDZ, LOOKDX, LOOKDZ, FOCAL = 55.0, -300.0, -55.0, 0.0, -78.0, 45.0
#     SETTLE_X = 760.0
#
# `captures/` is gitignored, so a clean checkout genuinely never got that file: the gap was real, it
# just was not the unrecoverable kind. This file is that harness's DRIVE half, committed, so the
# geometry lives in the repo instead of in one machine's ignored output folder. Same treatment
# `pie_army_pullback_drive.py` gives the army capture harness.
#
# The MRQ half is deliberately NOT carried over: the original set its own job up, and
# `mrq_render.render_shot` owns that now. Invoke as
#
#     import mrq_render                                  # in-editor, over run_python
#     mrq_render.render_shot('/Game/MT/Showcase/Lvl_MT_FootIK', '/Game/MT/Showcase/Seq_FootIK',
#                             out_dir, 0, 240, driver_path='.../Scripts/pie_footik_ab_drive.py')
#
# ============================================================================================
# THE TWO TAKES, AND WHICH ONE IS THE SHIPPED SHOT
#
# Take B — the shipped `ABP_MT_Locomotion`, ControlRig node PRESENT — is the shipped foot-IK take. Take A
# swaps the pawn's anim instance to a duplicate with the CR node STRUCTURALLY REMOVED (never an `Alpha=0`
# disable: the live `Alpha <- GroundedFlag` binding overwrites the literal every tick, which is the
# confound that made the first A/B rig-on-vs-rig-on and forced a re-shoot). Take A feeds the committed
# contact sheet and the +3.46 / -7.14 cm conformance figure, not the rendered shot sequence.
#
# The original node-removed duplicate `Tmp3_MT_Loco_NoRig` no longer exists. The equivalent survivor is
# the measurement control `ABP_MT_Tmp_NoCR` — the same construct, an `ABP_MT_Locomotion` duplicate with
# the CR node structurally removed — so that is the default.
#
# THAT ASSET IS A DEPENDENCY, NOT A THROWAWAY. Both `*_NoCR` assets would look like disposable
# measurement controls to anyone tidying `Content/MT/Showcase/`; they are load-bearing, because take A
# is the node-OFF half of the committed still
# `research/fixtures/roster-contact/03_footik_ab_split_vs_plant.jpg`. The one-line recipe to
# recreate one: duplicate `ABP_MT_Locomotion`, then
# `set_output_pose_source(source_node=AnimGraphNode_LayeredBoneBlend_0)` so the CR node is bypassed
# STRUCTURALLY, compile, save.
#
# The dependency is on the SHAPE, not on that asset name: `unreal._MT_FOOTIK_NORIG` points take A at any
# node-removed duplicate. Take A alone RAISES when its class will not load rather than silently
# rendering a second take B, which would reproduce the exact confound this shot exists to have escaped.
#
# ============================================================================================
# ONE DELIBERATE DIFFERENCE FROM THE ORIGINAL RENDER: THE CUBE STAND-IN IS HIDDEN
#
# `SM_MT_Rifle` — the project-authored engine-Cube stand-in on `BP_MT_TestPawn`'s `WeaponMesh` — did
# not exist when these frames were shot; it came with the aim/weapon beats. A re-shoot on today's pawn
# therefore puts a dark bar in frame that the original take does not have, in a shot whose entire subject
# is the feet. Hidden per render on the live component (the same pattern the army and lineup drivers
# use), NOT an asset edit: the tracked Content/MT/SM_MT_Rifle.uasset is untouched.
#
# THIS DOES NOT MAKE A RE-SHOOT FRAME-IDENTICAL TO THE SHIPPED TAKE and nothing here claims it does.
# `ABP_MT_Locomotion` has been edited many times since that take (the aim/hand-IK beats, the
# skeleton-aware Template). What this file restores is the FRAMING AND THE DRIVE — the part that was
# genuinely lost — so a rebuilder gets the shipped take's camera rather than a guess at it.
#
# ============================================================================================
# GOTCHA-A': no World, Level, Actor or Component is bound to a module-level name — every handle is a
# local inside `_fk_tick`. The numbers the tick runs on are SNAPSHOTTED into `_FKAB` at load, so a
# later exec into these globals (the `army_conductor.TOTAL` incident) cannot retime the shot.
import importlib
import os
import sys

import unreal

# The shared driver scaffolding. Resolved off the .uproject, NOT off `__file__`:
# `mrq_render.render_shot` execs this file into a bare namespace where `__file__` does not exist.
# Reloaded rather than plain-imported so an edit to mt_drive.py takes effect on the next render —
# see that module's header.
_MT_SCRIPTS = os.path.join(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()),
                           'Scripts')
if _MT_SCRIPTS not in sys.path:
    sys.path.insert(0, _MT_SCRIPTS)
_mt = importlib.reload(importlib.import_module('mt_drive'))

FK_PAWN_LABEL = 'FootIK_Manny'

# Drive +X until the pawn is past the ramp toe and onto the slope, then RELEASE — the settle into a
# grounded idle stance on the incline is where the conformance is measured. Verbatim original value.
FK_SETTLE_X = 760.0

# The camera, verbatim from the original harness: low side / three-quarter on the feet, riding the pawn.
#   loc  = (pawnX + CAMDX,  CAMY,  pawnZ + CAMDZ)
#   look = (pawnX + LOOKDX, 0.0,   pawnZ + LOOKDZ)
FK_CAMDX, FK_CAMY, FK_CAMDZ = 55.0, -300.0, -55.0
FK_LOOKDX, FK_LOOKDZ = 0.0, -78.0
FK_FOCAL = 45.0

# 'B' = shipped ABP (CR node present) = the shipped take. 'A' = node-removed duplicate.
FK_TAKE = str(getattr(unreal, '_MT_FOOTIK_TAKE', 'B')).upper()
FK_NORIG = str(getattr(unreal, '_MT_FOOTIK_NORIG',
                       '/Game/MT/Showcase/ABP_MT_Tmp_NoCR.ABP_MT_Tmp_NoCR_C'))

FK_HIDE_WEAPON_MESH = True

# Foot-clearance sampling. `clr = footZ - groundZ` under each deform foot, from a downward line trace
# — the same measurement whose take-A/take-B difference IS the caption's +3.46 / -7.14 cm.
FK_TRACE_UP, FK_TRACE_DOWN = 60.0, 200.0
FK_SETTLED_SPEED = 8.0
FK_FOOT_EVERY = 10

FK_GEN = _mt.retire_and_claim('_MT_FOOTIKAB')

# (`dressed` — the hidden-mesh COUNT — was carried here and never read back: unlike
# pie_army_pullback_drive, which reports its count in a per-second log line, this driver logs `n`
# directly at the moment it dresses. Dropped; `dressed_done` is the latch and is read.)
_FKAB = {'handle': None, 'ticks': 0, 'swapped': False, 'dressed_done': False,
         'settled_x': -1.0, 'gen': FK_GEN, 'take': FK_TAKE, 'norig': FK_NORIG,
         'settle_x': float(FK_SETTLE_X), 'focal': float(FK_FOCAL),
         'camdx': float(FK_CAMDX), 'camy': float(FK_CAMY), 'camdz': float(FK_CAMDZ),
         'lookdx': float(FK_LOOKDX), 'lookdz': float(FK_LOOKDZ),
         'cam': 'NONE', 'inst': '?'}


def _fk_ground_z(world, loc, ignore):
    hit = unreal.SystemLibrary.line_trace_single(
        world,
        unreal.Vector(loc.x, loc.y, loc.z + FK_TRACE_UP),
        unreal.Vector(loc.x, loc.y, loc.z - FK_TRACE_DOWN),
        unreal.TraceTypeQuery.TRACE_TYPE_QUERY1, False, ignore,
        unreal.DrawDebugTrace.NONE, True)
    if not hit:
        return None
    try:
        return hit.to_tuple()[4].z
    except Exception:
        return hit.impact_point.z


def _fk_drive_camera(world, pawn_loc):
    # NOT mt_drive.drive_ab_camera: this camera RIDES THE PAWN (loc and aim are both offsets from the
    # live pawn position), so there is no A->B path to lerp. Only the find is shared.
    cam = _mt.first_cine_camera(world)
    if not cam:
        return
    loc = unreal.Vector(pawn_loc.x + _FKAB['camdx'], _FKAB['camy'], pawn_loc.z + _FKAB['camdz'])
    look = unreal.Vector(pawn_loc.x + _FKAB['lookdx'], 0.0, pawn_loc.z + _FKAB['lookdz'])
    # ALWAYS find_look_at_rotation, never a hand-built Rotator — the (roll,pitch,yaw)
    # constructor-order bug that aimed two capture drivers at the sky cannot recur through this path.
    cam.set_actor_location_and_rotation(
        loc, unreal.MathLibrary.find_look_at_rotation(loc, look), False, True)
    comp = cam.get_cine_camera_component()
    if comp:
        comp.set_editor_property('current_focal_length', _FKAB['focal'])
    # READ BACK off the actor, not restated from the constants. The framing is the property of this
    # shot that was lost once already; the render that makes the frames now witnesses it.
    at = cam.get_actor_location()
    _FKAB['cam'] = 'loc=(%.0f,%.0f,%.0f) focal=%s' % (
        at.x, at.y, at.z,
        ('%.1f' % comp.get_editor_property('current_focal_length')) if comp else 'NO_COMPONENT')


def _fk_tick(dt):
    try:
        if int(getattr(unreal, '_MT_DRIVE_GEN', 0)) != _FKAB['gen']:
            if _FKAB['handle'] is not None:
                unreal.unregister_slate_pre_tick_callback(_FKAB['handle'])
                _FKAB['handle'] = None
            return
        world = unreal.EditorLevelLibrary.get_game_world()
        if not world:
            return
        pawn = None
        for a in unreal.GameplayStatics.get_all_actors_of_class(world, unreal.Character):
            if a.get_actor_label() == FK_PAWN_LABEL:
                pawn = a
                break
        if pawn is None:
            return
        mesh = pawn.get_component_by_class(unreal.SkeletalMeshComponent)
        _FKAB['ticks'] += 1

        if not _FKAB['swapped']:
            if _FKAB['take'] == 'A' and mesh:
                cls = unreal.load_class(None, _FKAB['norig'])
                if cls is None:
                    # Take A with no node-removed class is take B wearing take A's label — exactly
                    # the confound the re-shoot exists to have escaped. Fail loudly.
                    raise RuntimeError(
                        'FOOTIKAB take A: %s did not load. Take A needs an ABP_MT_Locomotion '
                        'duplicate with the ControlRig node STRUCTURALLY removed (never Alpha=0); '
                        'set unreal._MT_FOOTIK_NORIG to one.' % _FKAB['norig'])
                mesh.set_anim_instance_class(cls)
            if mesh and mesh.get_anim_instance():
                _FKAB['inst'] = str(mesh.get_anim_instance().get_class().get_name())
            _FKAB['swapped'] = True
            unreal.log('FOOTIKAB take=%s anim_instance=%s' % (_FKAB['take'], _FKAB['inst']))

        if FK_HIDE_WEAPON_MESH and not _FKAB['dressed_done']:
            n = 0
            for comp in pawn.get_components_by_class(unreal.StaticMeshComponent):
                comp.set_hidden_in_game(True)
                n += 1
            _FKAB['dressed_done'] = True
            unreal.log('FOOTIKAB dressed hidden_static_meshes=%d' % n)

        loc = pawn.get_actor_location()
        if loc.x < _FKAB['settle_x']:
            pawn.add_movement_input(unreal.Vector(1, 0, 0), 1.0, False)
        elif _FKAB['settled_x'] < 0:
            _FKAB['settled_x'] = loc.x
            unreal.log('FOOTIKAB SETTLE px=%.1f tick=%d' % (loc.x, _FKAB['ticks']))

        _fk_drive_camera(world, loc)

        spd = pawn.get_velocity().length()
        try:
            falling = 1 if pawn.get_movement_component().is_falling() else 0
        except Exception:
            falling = -1
        if _FKAB['ticks'] % 20 == 1:
            unreal.log('FOOTIKAB n=%d px=%.1f pz=%.1f spd=%.0f falling=%d cam=%s'
                       % (_FKAB['ticks'], loc.x, loc.z, spd, falling, _FKAB['cam']))

        if (_FKAB['settled_x'] > 0 and spd < FK_SETTLED_SPEED and mesh
                and _FKAB['ticks'] % FK_FOOT_EVERY == 0):
            fl = mesh.get_socket_location('foot_l')
            fr = mesh.get_socket_location('foot_r')
            gl = _fk_ground_z(world, fl, [pawn])
            gr = _fk_ground_z(world, fr, [pawn])
            unreal.log('FOOTIKAB FOOT n=%d falling=%d foot_l=%.2f clrL=%s foot_r=%.2f clrR=%s'
                       % (_FKAB['ticks'], falling, fl.z,
                          ('%.2f' % (fl.z - gl)) if gl is not None else 'NO_HIT', fr.z,
                          ('%.2f' % (fr.z - gr)) if gr is not None else 'NO_HIT'))
    except Exception as e:
        unreal.log_warning('FOOTIKAB err %s' % e)


_FKAB['handle'] = unreal.register_slate_pre_tick_callback(_fk_tick)
unreal._MT_FOOTIKAB = _FKAB
unreal.log('FOOTIKAB driver registered gen=%d take=%s settle_x=%.0f focal=%.0f cam=(+%.0f,%.0f,%.0f) '
           'look=(+%.0f,0,%.0f)' % (FK_GEN, FK_TAKE, FK_SETTLE_X, FK_FOCAL,
                                    FK_CAMDX, FK_CAMY, FK_CAMDZ, FK_LOOKDX, FK_LOOKDZ))
