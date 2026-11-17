# Driver for the crowd pull-back reveal, over the live driven 46-instance army.
#
# ============================================================================================
# PROVENANCE: THIS IS THE ORIGINAL ARMY PULL-BACK TAKE, PROMOTED OUT OF THE SCRATCHPAD
#
# The crowd take ships from `captures/m7-army/take1_pullback`, rendered by a scratchpad harness
# (`army_take_driver.py` + `mrq_render.py`) that was never committed. So the crowd take had the same
# repeatability gap the lane take had with `Seq_Lane`, only worse: neither the camera path nor the
# invocation existed anywhere in the repo.
# This file is that harness's `pullback` branch, verbatim in geometry, with four things added:
#
#   1. THE CUBE STAND-IN IS HIDDEN (the reason this shot was re-shot at all — see below);
#   2. the conductor path is derived from unreal.Paths.project_dir() instead of being one machine's
#      absolute path (a constant of exactly that shape was removed from mrq_render.py for the same reason);
#   3. the shipped render's own log carries the crowd COUNT, the camera POSITION and the FOCAL LENGTH,
#      so "46 instances" and the framing are witnessed by the run that made the frames;
#   4. every name this file relies on at tick time is namespaced or snapshotted — see the collision
#      gotcha below, which cost one whole 480-frame take.
#
# The camera geometry is UNCHANGED ON PURPOSE. The crowd take's caption cites
# `crowd_budget.instances_spawned` and army_config's measured game-thread figures, and a check of the
# shipped frames recorded that "the crowd is countable in the pull-back". A re-frame would put that
# reading — and the visual verification of the citation — out of date for a prop fix. Same path, same
# focal, same duration, same 480 frames.
#
# WHAT IS NOT RE-MEASURED HERE, AND MUST NOT BE. The caption's numbers do not come from this render:
# `46` is hero 6 + mid 8 + far 32 out of army_config.json (recomputed from that file by
# test_shot_check.test_crowd_instance_count_matches_army_config_rings), and `3.6 / 4.0 ms` is
# army_config budget._measured / gt_ms_ceiling from the driven-crowd cost sweep. This driver spawns
# nothing and measures no cost; it films the crowd army_spawn.py already put in the world. The count it
# logs is a WITNESS that the world holds what the caption cites, not a new derivation of it.
#
# ============================================================================================
# GOTCHA — exec'ing the conductor into this namespace OVERWRITES ANY NAME THEY SHARE
#
# `_ap_bootstrap_conductor` execs Scripts/army_conductor.py into THIS file's globals (it has to: the
# conductor must land in a namespace that survives, and it registers its own slate callback from there).
# exec() with a globals dict is a plain namespace merge — so every module-level name the conductor
# defines lands on top of ours. The conductor defines `TOTAL = 24.0`.
#
# The first re-shot take of this shot named this file's take length `TOTAL` too. The conductor
# bootstrapped at t=0.03 and silently retimed the camera from an 8 s reveal to a 24 s one, so at the
# last frame the camera was at x=2439 instead of x=7200 and two thirds of the crowd was outside the
# frame. EVERY OTHER SIGNAL WAS GREEN: 480 frames, army=46 every second, 46 static meshes hidden,
# `capture_check` PASS, mrq_render's finished-render log line with success=True. It was caught only by comparing the frames
# against the shipped shot, and diagnosed only once the driver logged the camera's own read-back
# position — which is why that log line is now permanent.
#
# TWO DEFENCES, because a rename alone would only survive until the conductor gains a new constant:
#   * every name here is `_ap_`/`AP_` prefixed, and
#   * the numbers `_ap_tick` runs on are SNAPSHOTTED into `_APULL` at load, before any exec can happen,
#     so the tick reads a dict entry rather than a module global.
#
# ============================================================================================
# THE CUBE STAND-IN
#
# BP_MT_TestPawn carries a `WeaponMesh` holding SM_MT_Rifle — a project-authored duplicate of an engine
# Cube, scaled into a bar — and the whole crowd spawns from that class, so in the original take the
# entire army was carrying it. At the pull-back's far end it is near-invisible at crowd distance; in the opening
# hero-close it is a dark bar in the mannequins' hands. No caption in this shot mentions a weapon, so
# it asserts nothing — it is simply the one thing on screen the crowd system did not put there. Hidden
# per render on the live components (the proven pie_roster4_drive pattern), NOT an asset edit: the
# tracked Content/MT/SM_MT_Rifle.uasset is untouched. Swapping in the real Fab rifle was rejected: an
# armed army would make a claim about the content that the crowd system does not support.
#
# Khaimera's axe is part of his SKELETAL mesh ("its native stock mesh"), not a StaticMeshComponent,
# so the hero tier keeps it — as it did in the original crowd take and in the original roster take.
#
# ============================================================================================
# CONDUCTOR-UNDER-MRQ (the integration this file inherits from the original harness)
#
# `army_conductor.start_conductor()` needs a LIVE PIE world, and under MRQ that only exists mid-render.
# So the conductor is exec'd from inside this callback, once, as soon as the MRQ PIE world and the
# tagged army are both up (tick >= 3, army > 0). Without it the crowd stands in Idle and the shot is a
# still life of 46 statues — and a standing crowd and a driven crowd are different footage AND
# different cost.
#
# GOTCHA-A': no World, Level, Actor or Component is ever bound to a module-level name — every handle is
# a local inside _ap_tick. Retires off unreal._MT_DRIVE_GEN, which mrq_render bumps when the render
# ends.
# ============================================================================================
# (`import math` lived here and nothing in this file used it — `_ap_smooth` is plain arithmetic. It was
# also one of the two names this file shares with army_conductor.py, which it execs into its own
# globals; both sides IMPORTED it, so the overlap was benign and namespace_merge_check subtracted it.
# Removing an unused module-level name can only shrink that merge's collision surface, never grow it,
# and the conductor imports `math` for itself.)
import os

import unreal

# 8.0 s of world time. Seq_Army runs at 60 fps, so the crowd take's 480 frames ARE this take end to end;
# the rendered sequence plays them back at 30 fps (shots_manifest fps_out) as 16.0 s. The camera interpolation is keyed on
# this rather than on frame count so the path is identical whatever range is rendered out of it.
AP_TOTAL = 8.0
AP_FOCAL = 20.0          # wide — it has to fit a 3000-radius formation at the far end of the move

# The pull-back, verbatim from the original take driver: start over the front hero's shoulder, pull back and
# up to reveal the whole concentric-ring host. Smoothstep, so the reveal eases rather than ramping
# linearly into the wide.
AP_CAM_A = (unreal.Vector(760.0, 150.0, 175.0), unreal.Vector(400.0, 0.0, 140.0))
AP_CAM_B = (unreal.Vector(7200.0, 0.0, 2900.0), unreal.Vector(0.0, 0.0, 260.0))

AP_HIDE_WEAPON_MESH = True

# The army's own tag, as army_spawn.py writes it (config['spawn']['tag_all']). Iterating the TAGGED
# actors rather than every Character keeps the dressing pass off the stage props and makes the logged
# count mean "army instances", which is what the caption is about.
AP_ARMY_TAG = 'MT_Army'

AP_CONDUCTOR = os.path.join(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()),
                            'Scripts', 'army_conductor.py').replace('\\', '/')

# Retire any previous instance of THIS driver, then claim a fresh generation so any other stale driver
# in the persistent run_python namespace retires on its own next tick.
_ap_prev = getattr(unreal, '_MT_ARMYPULL', None)
if _ap_prev and _ap_prev.get('handle') is not None:
    try:
        unreal.unregister_slate_pre_tick_callback(_ap_prev['handle'])
    except Exception:
        pass
    _ap_prev['handle'] = None

AP_GEN = int(getattr(unreal, '_MT_DRIVE_GEN', 0)) + 1
unreal._MT_DRIVE_GEN = AP_GEN

# Strings, floats and counters only — no world, no actor, no component (GOTCHA-A'). The four camera
# numbers are SNAPSHOTTED here, at load, so the conductor's exec cannot retime the shot (see header).
_APULL = {'handle': None, 'cond_started': False, 'dressed': 0, 'dressed_done': False,
          'ticks': 0, 'last_log': -999.0, 'gen': AP_GEN, 'cam': 'NONE',
          'total': float(AP_TOTAL), 'focal': float(AP_FOCAL), 'a': AP_CAM_A, 'b': AP_CAM_B}


def _ap_lerp(a, b, t):
    return a + (b - a) * t


def _ap_smooth(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def _ap_v(a, b, s):
    return unreal.Vector(_ap_lerp(a.x, b.x, s), _ap_lerp(a.y, b.y, s), _ap_lerp(a.z, b.z, s))


def _ap_drive_camera(world, now):
    cam = None
    for a in unreal.GameplayStatics.get_all_actors_of_class(world, unreal.CineCameraActor):
        cam = a
        break
    if not cam:
        return
    # Written EVERY tick rather than latched on the first one. Cheap, and it means a camera the
    # sequence re-spawns or re-templates mid-render cannot quietly render on the CineCamera default.
    comp = cam.get_cine_camera_component()
    if comp:
        comp.set_editor_property('current_focal_length', _APULL['focal'])
    s = _ap_smooth(now / max(_APULL['total'], 0.01))
    loc = _ap_v(_APULL['a'][0], _APULL['b'][0], s)
    tgt = _ap_v(_APULL['a'][1], _APULL['b'][1], s)
    # ALWAYS find_look_at_rotation, never a hardcoded Rotator — the (roll,pitch,yaw)
    # constructor-order bug that aimed two capture drivers straight up cannot recur through this path.
    cam.set_actor_location_and_rotation(loc, unreal.MathLibrary.find_look_at_rotation(loc, tgt),
                                        False, True)
    # What the frame was ACTUALLY shot on, READ BACK off the actor and the component rather than
    # restated from the constants. The framing is the one property of this shot a reader cannot check
    # any other way, and a silently retimed camera is exactly what this line was added to catch.
    at = cam.get_actor_location()
    _APULL['cam'] = 'loc=(%.0f,%.0f,%.0f) f=%.3f focal=%s' % (
        at.x, at.y, at.z, s,
        ('%.1f' % comp.get_editor_property('current_focal_length')) if comp else 'NO_COMPONENT')


def _ap_bootstrap_conductor():
    """Exec the conductor's `start` into THIS driver's namespace, once the PIE world is live.

    This is the namespace merge the header warns about — everything after this line runs in a globals
    dict that also holds the conductor's constants, which is why _ap_tick reads _APULL rather than the
    module-level AP_* names."""
    g = globals()
    g['CONDUCTOR_ACTION'] = 'start'
    g['CONDUCTOR_ARGS'] = None
    exec(open(AP_CONDUCTOR).read(), g)


def _ap_tick(dt):
    try:
        if int(getattr(unreal, '_MT_DRIVE_GEN', 0)) != _APULL['gen']:
            if _APULL['handle'] is not None:
                unreal.unregister_slate_pre_tick_callback(_APULL['handle'])
                _APULL['handle'] = None
            return
        world = unreal.EditorLevelLibrary.get_game_world()
        if not world:
            return
        _APULL['ticks'] += 1
        now = unreal.SystemLibrary.get_game_time_in_seconds(world)
        _ap_drive_camera(world, now)

        army = list(unreal.GameplayStatics.get_all_actors_with_tag(world, unreal.Name(AP_ARMY_TAG)))

        # Dress once the army is actually present. Latched on FIRST SUCCESS rather than first attempt:
        # MRQ's PIE duplicates the crowd, so on the earliest ticks the tagged set can still be empty,
        # and latching on the attempt would ship 46 cubes.
        if AP_HIDE_WEAPON_MESH and not _APULL['dressed_done'] and army:
            n = 0
            for a in army:
                for comp in a.get_components_by_class(unreal.StaticMeshComponent):
                    comp.set_hidden_in_game(True)
                    n += 1
            _APULL['dressed'] = n
            _APULL['dressed_done'] = True
            unreal.log('ARMYPULL dressed hidden_static_meshes=%d over army=%d' % (n, len(army)))

        # The conductor needs the PIE world AND the duplicated army; a couple of ticks of settle first.
        if not _APULL['cond_started'] and _APULL['ticks'] >= 3 and army:
            _ap_bootstrap_conductor()
            _APULL['cond_started'] = True
            unreal.log('ARMYPULL conductor bootstrapped army=%d t=%.2f' % (len(army), now))

        if (now - _APULL['last_log']) >= 1.0:
            _APULL['last_log'] = now
            # The crowd count goes in the SHIPPED RENDER'S OWN LOG every second, because the crowd
            # take's caption cites a spawn count and the only thing that can witness the world actually held
            # it is the run that photographed it. A short army here is a failed take, not a detail.
            unreal.log('ARMYPULL t=%.2f ticks=%d army=%d dressed=%d cond=%s cam=%s'
                       % (now, _APULL['ticks'], len(army), _APULL['dressed'],
                          _APULL['cond_started'], _APULL['cam']))
    except Exception as e:
        unreal.log_warning('ARMYPULL err %s' % e)


_APULL['handle'] = unreal.register_slate_pre_tick_callback(_ap_tick)
unreal._MT_ARMYPULL = _APULL
unreal.log('ARMYPULL driver registered gen=%d total=%.1fs focal=%.0f conductor=%s'
           % (AP_GEN, _APULL['total'], _APULL['focal'], AP_CONDUCTOR))
