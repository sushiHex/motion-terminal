# Control Rig Dynamics harness: two unpossessed Khaimeras run parallel lanes under real
# CharacterMovement, hard-stop, and hold, one with the dynamics post-process rig driven ON and one OFF. It
# records everything the spike measurements and the later gate need, per frame, into one JSON file.
#
# Written to be promoted, not thrown away: the gate's PIE row re-implements this in C++ against the
# same constants, and the rendered-take driver reuses it with a camera.
#
# WHERE: Lvl_MT_Army (stage_author 'army'), a flat 140 m floor. Lvl_MT_Test's flat stretch before the ramp
# is 300 cm (PieTests.cpp kBSFlatLowX/HighX), too short for a run and a stop, so the "own flat map" fallback
# (docs/specs/2026-10-06-khaimera-cr-dynamics-design.md, section 3.5) applies, and the army stage
# already is one. Lane equivalence is still measured, not assumed:
# `lane_traces` traces the floor along both lanes before the pawns spawn.
#
# WHAT IT RECORDS per sampled frame, per pawn: world delta time; CMC speed and location; the four witness
# curves read with the bool overload (UAnimInstance.get_curve_value_with_default, BlueprintPure over
# GetCurveValue(FName, float&)), so a missing curve reads as found=False, never as a silent 0; the
# component-space location of each chain tip and of each control bone outside the chain closure; the
# forced and predicted LOD.
#
# Spawn and first input are on the SAME frame for both pawns, and the file records both frame indices (a
# recorded equality, in place of "one tick apart").
#
# Usage (editor.run_python, Lvl_MT_Army loaded). Pass the same KDD_CONFIG, with the hero, to every action:
#     KDD_ACTION = 'place'; KDD_CONFIG = {'hero': '<Rig>', 'tag': 'run1', 'alphas': [1.0, 0.0]}   # editor, PIE stopped
#     (start PIE)
#     KDD_ACTION = 'drive'; KDD_CONFIG = (the same)
#     (stop PIE after KHAIDYN_DRIVE prints)
#     KDD_ACTION = 'clear'; KDD_CONFIG = (the same)
#     each followed by exec(open(r'.../Scripts/pie_dynamics_drive.py').read())
# It runs to completion inside PIE and prints `KHAIDYN_DRIVE <json summary>`; the full record lands in
# Saved/MotionTerminalTests/dyn_<rig>_<tag>.json (dyn_hero.record_path).
#
# GOTCHA-A': no World/Actor/Component is ever bound at module scope; the tick reads them off _KDD's weak list
# each frame and every handle is a local.
import importlib
import json
import os
import sys

import unreal

_SCRIPTS = os.path.join(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()), 'Scripts')
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
_dh = importlib.reload(importlib.import_module('dyn_hero'))
_mt = importlib.reload(importlib.import_module('mt_drive'))   # bone reads: _mt.bone_transform, never a socket

CONFIG = {
    'tag': 's3',
    'hero': None,                         # the rig (HeroProfiles/<rig>.json); required, on every action
    'alphas': [1.0, 0.0],                 # one pawn per entry; the alpha is never toggled during a run
    'pawn_class': None,                   # None: the hero's conventional assets (dyn_hero.resolve)
    'anim_class': None,
    'pp_class': None,
    'start_x': -2000.0,
    # lanes at +/- this. Originally 600 cm apart; a visual check found the pair too far apart and too small in frame,
    # so 170 cm apart (a ~84 cm capsule each, ~150 cm across the swinging axes).
    'lane_y': 85.0,
    'spawn_z': 110.0,
    'speed': 500.0,                       # CMC MaxWalkSpeed; the in-place ABP keys gait off velocity
    # Standing time before the run and after the release are equal, so the clip has the same standing frames at
    # the end as at the beginning; 2 s each still covers the settle check (t_stop + 1.5 s, dyn_analyze).
    'settle_s': 2.0,                      # spawned, no input: lets the anim instances and rig initialise
    'run_s': 3.0,
    'hold_s': 2.0,                        # after input release
    'braking_friction_factor': 0.0,       # pawn default 2.0; see _spawn
    'rotation_rate_yaw': 540.0,
    # Every frame advances game time by exactly 1/fixed_hz (UMTDriveLibrary), the PIE rows' FFixedTimeStepScope
    # convention. Without it an unfocused editor runs at about 3 fps and each frame is a 0.33 s step (measured).
    'fixed_hz': 60.0,         # None under MRQ, which steps the world at the sequence's display rate itself
    # Under an MRQ render (dyn_render.py) the driver also flies the sequence's spawnable camera at CAM_OFFSET /
    # CAM_LOOK below, horizontal FOV 50 degrees.
    'camera': False,
    # Focus the flown camera on the pair every frame (at the closer framing, texture and mesh quality looked low
    # close up). Measured on a still frame, Laplacian variance over the near pawn: default focus (1 km) 79.1,
    # focused at f/5.6 316.6. An 8-sample AA override did not help (62.4 alone, 220.7 with focus). The Paragon
    # textures were never the problem.
    'cam_focus': True,
    'cam_aperture': 5.6,
    'cam_exposure_locked': 1.25,          # fixed exposure, so both stands match; None = auto exposure
    'floor_extend_m': 5000.0,             # visual-only floor out to this half-span (m); 0 = the bare stage
    'cam_exposure_speed': 10.0,           # used only when cam_exposure_locked is None (engine defaults 3 / 1)
    # Console variables set for the run and restored after it, as [name, value_during, value_after]. The causal
    # control uses ['ControlRig.Dynamics.EnableStepSolver', 0, 1] (RigDynamicsSolverExecution.cpp:26): with the
    # solver not stepping, the ON/OFF difference must vanish, or it was never the dynamics.
    'cvars': [],
    # Hit reaction (docs/specs/2026-10-29-hit-reaction-design.md), both off by default so a dynamics run is
    # unchanged. reaction_alphas: the post-process ABP's HitReactionAlpha per pawn, set at spawn and held (an alpha
    # 0 -> 1 change resets the reaction's simulation and can swallow a hit).
    'reaction_alphas': None,
    # reaction_schedule: the reaction as a TRANSIENT layer, as a game uses it: alpha 0 (an exact pass-through) until
    # arm_s before the first hit, then each pawn's reaction_alphas value through the hits and hold_s after the last,
    # then a linear fade to 0 over fade_s. Arming before the impulse keeps the 0 -> 1 reset frame from swallowing it;
    # every pawn with a reaction gets the same schedule, so hit and no-hit pawns differ by the impulses alone. None:
    # alphas held.
    'reaction_schedule': None,            # {'arm_s': 0.05, 'hold_s': 1.0, 'fade_s': 0.3}
    # hits: [{'pawns': [0], 'bone', 'at_s' (seconds after spawn), 'dir': [x, y, z] (world), 'strength', 'radius',
    # 'offset_cm', 'vel_change'}, ...]: one radial impulse each on the mesh, its origin offset_cm behind the bone against
    # dir. The reaction node applies it to every simulated body whose centre of mass lies within radius (a push, not a
    # bone-targeted impact). Every value used is recorded in events['hits'], in order.
    'hits': None,
    # The mesh's own cloth (Chaos Cloth clothing assets) on each arm. ON pawns (alpha > 0) get the hero's generated
    # cloth collision bodies (hero_onboard physics stage) as a collision source; OFF pawns show NO simulation at all,
    # their cloth blended out to the skinned mesh (before this, the "no simulation" pawn still ran the stock cloth,
    # identical to the ON pawn's).
    'off_arm_cloth': False,
    'cloth_substeps': None,               # None: dyn_contact.SUBSTEPS (a sweep sets it)
    # console_at: {frame: [console commands]} run in the game world at that drive frame (a diagnostic: rhi.DumpMemory
    # mid-render, when the editor is too busy to take a command from outside)
    'console_at': None,
    # heat: the simulated pawn tinted per vertex by its distance from the unsimulated one (dyn_heat.py), the stats per
    # frame in each row's 'heat'. Its cloth runs synchronised with the game thread for it (bWaitForParallelClothTask).
    'heat': False,
    # garment: a sweep's override of the hero's garment settings (dyn_behaviours.garment), key by key; None: shipped
    'garment': None,
    # frame_height_cm: frame the hero as one this tall (None: its own height); see FRAME_SCALE
    'frame_height_cm': None,
}
# The hero FIRST: every profile-derived constant below depends on it, and this module is
# exec'd into a persistent namespace, so the caller passes it in KDD_CONFIG on every action.
_user = globals().pop('KDD_CONFIG', None) or {}
_cfg = dict(CONFIG)
_cfg.update(_user)
HERO = _dh.resolve(_cfg.get('hero'))
_cfg['hero'] = HERO['rig']
for _key, _val in (('pawn_class', HERO['pawn_class']), ('anim_class', HERO['anim_class']),
                   ('pp_class', HERO['abp_class'])):
    _cfg[_key] = _cfg.get(_key) or _val
# Framing (the earlier, wider framing left the pair far too small in frame): ~540 cm from the pair, so at 50 deg
# horizontal the frame is ~505 x 284 cm and a 210 cm hero fills ~3/4 of its height. The earlier framing was
# (900, -700, 150) at 600 cm lanes.
# Framing and lane spacing scale with the hero's height against the 209.9 cm they were set on, so every hero fills the
# frame the same way (the first hero's framing is unchanged: scale 1).
# Config 'frame_height_cm' frames every hero as one of that height instead (the lineup: one camera for every hero, so
# side by side they keep their true sizes).
FRAME_REF_HEIGHT_CM = 209.9
FRAME_SCALE = (_cfg.get('frame_height_cm') or (HERO['profile'].get('scale') or {}).get('height_cm', FRAME_REF_HEIGHT_CM)
               ) / FRAME_REF_HEIGHT_CM
CAM_OFFSET = tuple(v * FRAME_SCALE for v in (430.0, -330.0, 115.0))
CAM_LOOK = tuple(v * FRAME_SCALE for v in (0.0, 0.0, 95.0))
if 'lane_y' not in _user:
    _cfg['lane_y'] = CONFIG['lane_y'] * FRAME_SCALE
# Horizontal FOV 50 on the CineCamera's default 23.76 mm filmback: f = 23.76 / (2 tan 25 deg).
CAM_FOCAL_MM = 25.476
PROFILE = HERO['profile_path']


def _load_profile():
    try:
        with open(PROFILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# The simulated parts come from the hero profile (docs/specs/2026-10-27-secondary-motion-backends-design.md,
# section 3.6): no chain names in the harness. Tips are each part's tracked tip (the end of its unbranched run);
# CHAIN_BONES every particle bone. The reset-threshold measurement reads these: the largest particle distance from the simulation origin, and the roots' component-space speed.
PARTS = _load_profile().get('secondary', {}).get('parts', [])
CHAIN_TIPS = {p['id']: p['tip'] for p in PARTS}
CHAIN_BONES = sorted({b for p in PARTS for b in p['bones']})
# Outside every chain's closure: these must match between the ON and OFF pawn. The skeleton's own names
# for the body roles the intake resolved (profile `roles`); thighs and calves also define the leg-capsule axes for the
# leg clearance check.
_ROLES = HERO['profile'].get('roles') or {}
_spine = _ROLES.get('spine') or []
CONTROL_BONES = [b for b in (_ROLES.get('pelvis'), _spine[-1] if _spine else None, _ROLES.get('head'),
                             _ROLES.get('thigh_l'), _ROLES.get('thigh_r'), _ROLES.get('calf_l'), _ROLES.get('calf_r'),
                             _ROLES.get('foot_l'), _ROLES.get('foot_r'), _ROLES.get('hand_r')) if b]
WITNESS_CURVES = ['MT_DynRigTick', 'MT_DynTeleport', 'MT_DynPosReset', 'MT_DynKinReset', 'MT_DynFrameSpeed']
TAG = 'MT_KDD'

_prev = getattr(unreal, '_MT_KDD_HANDLE', None)
if _prev is not None:
    unreal.unregister_slate_pre_tick_callback(_prev)
    unreal._MT_KDD_HANDLE = None

_KDD = {'frame': 0, 't0': None, 'pawns': [], 'frames': [], 'events': {}, 'done': False, 'error': None}


def _v(vec):
    return [round(vec.x, 4), round(vec.y, 4), round(vec.z, 4)]


def _tm(t):
    q = t.rotation
    return _v(t.translation) + [round(q.x, 6), round(q.y, 6), round(q.z, 6), round(q.w, 6)]


def _profile_owners():
    """Every bone that owns an autofit collider in the hero profile, so validation can rebuild the colliders."""
    col = _load_profile().get('colliders', {})
    # ...and every bone carrying a chain tail: the tail's Null is a rig element, not a mesh socket, so validation
    # places it from the leaf bone's recorded transform and the tail's local offset.
    tails = {t['bone'] for c in col.get('chains', {}).values() for t in c.get('tails', [])}
    return sorted({s['owner'] for s in col.get('shapes', [])} | tails)


# Every particle bone too: the twist measure (dyn_analyze.twist) reads each strip bone's rotation, ON vs OFF.
# ...and every part's parent: the root-lift measure (dyn_analyze.root_lift) reads a chain's first simulated bone in
# its parent's frame.
_TM_BONES = sorted(set(_profile_owners()) | set(CHAIN_BONES) | {p['parent'] for p in PARTS if p.get('parent')}
                   | set(_cfg.get('tm_bones', [])))   # tm_bones: extras


def _curve(inst, name):
    if inst is None:
        return {'found': False, 'value': None, 'why': 'no post-process instance'}
    # Python folds GetCurveValueWithDefault's bool return into the out param: the value when the curve was
    # found, None when it was not (probed: a real curve gave 156.0, 'NoSuchCurve' gave None). So None, not
    # the -1.0 default, is the missing-curve signal.
    value = inst.get_curve_value_with_default(name, -1.0)
    return {'found': value is not None, 'value': value}


def _lane_traces(world):
    """Floor Z and physical material every 100 cm along each lane, before anything spawns."""
    out = []
    length = _cfg['speed'] * (_cfg['run_s'] + 1.5) + 200.0
    for i in range(len(_cfg['alphas'])):
        y = _lane_y(i)
        hits = []
        x = _cfg['start_x']
        while x <= _cfg['start_x'] + length:
            hit = unreal.SystemLibrary.line_trace_single(
                world, unreal.Vector(x, y, 500.0), unreal.Vector(x, y, -500.0),
                unreal.TraceTypeQuery.TRACE_TYPE_QUERY1, False, [], unreal.DrawDebugTrace.NONE, True)
            if hit is None:
                hits.append([x, None, None])
            else:
                t = hit.to_tuple()
                # HitResult tuple: (blocking_hit, initial_overlap, time, distance, location, impact_point, ...)
                loc = t[4]
                mat = None
                for item in t:
                    if isinstance(item, unreal.PhysicalMaterial):
                        mat = item.get_path_name()
                hits.append([x, round(loc.z, 4) if t[0] else None, mat])
            x += 100.0
        out.append({'lane_y': y, 'samples': hits})
    return out


def _lane_y(i):
    n = len(_cfg['alphas'])
    return _cfg['lane_y'] * (2.0 * i / (n - 1) - 1.0) if n > 1 else 0.0


def _editor_actors():
    return unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()


def place():
    """EDITOR world, before PIE: Python has no PIE-world spawn (no deferred spawn is exposed), so the pawns are
    placed here, like army_spawn and metahuman_bind do, and PIE duplicates them. Both therefore begin play on
    the same PIE frame. Never saved: `clear` removes them, and the stage is rebuilt by stage_author anyway."""
    # `clear` saves the level only if it was clean before this placement, so a run never saves someone's
    # unsaved edits. Recorded on the unreal module: it must survive between run_python calls.
    unreal._MT_KDD_LEVEL_WAS_CLEAN = not unreal.EditorLoadingAndSavingUtils.get_dirty_map_packages()
    clear()
    if _cfg.get('heat'):
        import dyn_heat
        dyn_heat.ensure_material(unreal)   # built and saved here: nothing can be saved during PIE
    cls = unreal.load_class(None, _cfg['pawn_class'])
    if cls is None:
        raise RuntimeError('pawn class not found: %s (create the hero\'s test pawn %s and in-place ABP %s, or pass '
                           'pawn_class/anim_class; licensed content absent?)'
                           % (_cfg['pawn_class'], HERO['pawn_path'], HERO['inplace_path']))
    for key in ('anim_class', 'pp_class'):
        if unreal.load_class(None, _cfg[key]) is None:
            raise RuntimeError('%s not found: %s (build it first: dynamics_graph.py --hero %s for the rig; the '
                               'in-place ABP %s is the hero\'s own)' % (key, _cfg[key], HERO['rig'], HERO['inplace_path']))
    sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    for i in range(len(_cfg['alphas'])):
        pawn = sub.spawn_actor_from_class(cls, unreal.Vector(_cfg['start_x'], _lane_y(i), _cfg['spawn_z']),
                                          unreal.Rotator(0.0, 0.0, 0.0))
        pawn.set_actor_label('%s%d' % (TAG, i))
        pawn.tags = [unreal.Name(TAG), unreal.Name('%s%d' % (TAG, i))]
        pawn.set_editor_property('auto_possess_ai', unreal.AutoPossessAI.DISABLED)
        pawn.set_editor_property('auto_possess_player', unreal.AutoReceiveInput.DISABLED)
        # Measured whether or not a camera sees it: a mesh not recently rendered skips its animation update, so off
        # camera the post-process rig never evaluated (its witness curves absent until the pawns ran into view, frame
        # 311, then a 15 cm snap). Hardware ray tracing had hidden it: its scene marks every mesh rendered.
        pawn.get_editor_property('mesh').set_editor_property(
            'visibility_based_anim_tick_option', unreal.VisibilityBasedAnimTickOption.ALWAYS_TICK_POSE_AND_REFRESH_BONES)
        if (_cfg['alphas'][i] <= 0.0 and not _cfg.get('off_arm_cloth')
                and HERO['profile'].get('stock', {}).get('clothing_assets')):
            # the OFF arm shows no simulation: its mesh cloth blended out to the skinned mesh (see _cloth)
            pawn.get_editor_property('mesh').set_editor_property('cloth_blend_weight', 0.0)
    if _cfg.get('floor_extend_m'):
        # The stage floor is a 140 m cube whose far edge showed as a dark band at the horizon, and after 15 m of
        # travel that edge sat a few pixels differently, so the closing stand could not loop into the opening one.
        # A visual-only extension (same cube and material, collision off, 0.5 cm under the stage floor so the two
        # never z-fight) pushes the edge out to ~floor_extend_m, a fraction of a pixel under the true horizon.
        floor = sub.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector(0.0, 0.0, -0.5 - 50.0),
                                           unreal.Rotator(0.0, 0.0, 0.0))
        smc = floor.static_mesh_component
        smc.set_static_mesh(unreal.load_asset('/Engine/BasicShapes/Cube.Cube'))
        smc.set_material(0, unreal.load_asset('/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial'))
        smc.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
        span = _cfg['floor_extend_m'] * 2.0     # metres across; the cube is 1 m, so the scale is the span
        floor.set_actor_scale3d(unreal.Vector(span, span, 1.0))
        floor.set_actor_label(TAG + '_FloorExtension')
        floor.tags = [unreal.Name(TAG)]
    unreal.log('KHAIDYN_PLACE %d pawns' % len(_cfg['alphas']))


def clear(save=False):
    """Remove the placed pawns. With save=True, also save the level so the editor can quit without a save
    prompt, but ONLY if the level was clean before `place`: then the place/clear pair is the only change and
    saving leaves the stage's content as it was. A level that was already dirty is left dirty, unsaved."""
    sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    for actor in _editor_actors():
        if unreal.Name(TAG) in actor.tags:
            sub.destroy_actor(actor)
    if save and getattr(unreal, '_MT_KDD_LEVEL_WAS_CLEAN', False):
        if not unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).save_current_level():
            raise RuntimeError('KHAIDYN clear: save_current_level failed')
    elif save:
        unreal.log_warning('KHAIDYN clear: the level was dirty before place, so it is left unsaved')


def _spawn(world):
    """PIE: find the placed pawns (in lane order) and configure them for the run."""
    anim = unreal.load_class(None, _cfg['anim_class'])
    pp = unreal.load_class(None, _cfg['pp_class'])
    if not (anim and pp):
        raise RuntimeError('class load failed: anim=%s pp=%s' % (anim, pp))
    for i in range(len(_cfg['alphas'])):
        found = unreal.GameplayStatics.get_all_actors_with_tag(world, unreal.Name('%s%d' % (TAG, i)))
        if len(found) != 1:
            raise RuntimeError('expected one PIE actor tagged %s%d, found %d (run place before PIE)'
                               % (TAG, i, len(found)))
        pawn = found[0]
        mesh = pawn.get_editor_property('mesh')
        mesh.set_anim_instance_class(anim)
        mesh.set_override_post_process_anim_bp(pp, True)
        mesh.set_forced_lod(1)            # 1 = LOD0: the cloth is skinned at LOD0-2 only (usd_chain_weights.py)
        # The alpha goes on now, before the pawn's first evaluation with the override, not at the first sample
        # (written only in _sample, the ON pawn's first evaluated frames ran at the default 0).
        pp_inst = mesh.get_post_process_instance()
        if pp_inst is None:
            raise RuntimeError('no post-process instance right after the override on %s' % pawn.get_name())
        pp_inst.set_editor_property('DynamicsAlpha', _cfg['alphas'][i])
        if _cfg.get('reaction_alphas') is not None:
            pp_inst.set_editor_property('HitReactionAlpha', _reaction_alpha(i, 0.0))
        _KDD['events'].setdefault('cloth', []).append(_cloth(mesh, _cfg['alphas'][i] > 0.0))
        pawn.set_editor_property('use_controller_rotation_yaw', False)
        cmc = pawn.get_editor_property('character_movement')
        cmc.set_editor_property('run_physics_with_no_controller', True)
        cmc.set_editor_property('orient_rotation_to_movement', True)
        cmc.set_editor_property('rotation_rate', unreal.Rotator(0.0, 0.0, _cfg['rotation_rate_yaw']))
        cmc.set_editor_property('max_walk_speed', _cfg['speed'])
        # The stop showed an abrupt one-frame snap to the standing pose at the end of the run: the pawn braked
        # with BrakingDecelerationWalking 2048 PLUS ground friction 8 x BrakingFrictionFactor 2, so 500 cm/s -> 0
        # in ~0.1 s, and the speed-keyed blend space jumped jog -> idle in ~2 frames. The start has no such friction
        # term and ramps over ~0.25 s. Braking friction off makes the stop the mirror of the start: the same
        # 2048 cm/s^2, ~0.24 s, through the same blend-space samples.
        cmc.set_editor_property('braking_friction_factor', _cfg['braking_friction_factor'])
        cmc.set_component_tick_enabled(True)
        cmc.set_movement_mode(unreal.MovementMode.MOVE_WALKING)
        _KDD['pawns'].append(pawn)
    if _cfg.get('heat'):
        _heat_begin()
    _KDD['events']['spawn_frame'] = [_KDD['frame']] * len(_KDD['pawns'])
    _KDD['events']['controllers'] = [str(p.get_controller()) for p in _KDD['pawns']]


def _cost(section, since):
    """Wall time a drive section took this frame, summed into events['cost_ms'] (what a drive frame costs, by part)."""
    import time
    cost = _KDD['events'].setdefault('cost_ms', {})
    cost[section] = round(cost.get(section, 0.0) + (time.perf_counter() - since) * 1000.0, 3)
    return time.perf_counter()


def _sample(world, now, dt):
    import time
    since = time.perf_counter()
    row = {'f': _KDD['frame'], 't': round(now, 5), 'dt': round(dt, 6), 'pawns': []}
    for i, pawn in enumerate(_KDD['pawns']):
        mesh = pawn.get_editor_property('mesh')
        pp = mesh.get_post_process_instance()
        if pp is not None:
            pp.set_editor_property('DynamicsAlpha', _cfg['alphas'][i])
            if _cfg.get('reaction_alphas') is not None:
                pp.set_editor_property('HitReactionAlpha', _reaction_alpha(i, now - (_KDD['t0'] or now)))
        cs = unreal.RelativeTransformSpace.RTS_COMPONENT
        cmc = pawn.get_editor_property('character_movement')
        row['pawns'].append({
            'speed': round(cmc.velocity.length(), 3),
            'loc': _v(pawn.get_actor_location()),
            'yaw': round(pawn.get_actor_rotation().yaw, 3),
            'pp': pp.get_class().get_name() if pp else None,
            'alpha_read': pp.get_editor_property('DynamicsAlpha') if pp else None,
            'reaction_alpha': (pp.get_editor_property('HitReactionAlpha')
                               if pp and _cfg.get('reaction_alphas') is not None else None),
            'curves': {c: _curve(pp, c) for c in WITNESS_CURVES},
            'tips': {k: _v(_mt.bone_transform(mesh, b, cs).translation) for k, b in CHAIN_TIPS.items()},
            'ctrl': {b: _v(_mt.bone_transform(mesh, b, cs).translation) for b in CONTROL_BONES},
            'chain': {b: _v(_mt.bone_transform(mesh, b, cs).translation) for b in CHAIN_BONES},
            # Collider owners' full component-space transforms (autofit validation reconstructs every collider per
            # frame from these; positions alone cannot place a capsule or a box).
            'tm': {b: _tm(_mt.bone_transform(mesh, b, cs)) for b in _TM_BONES},
            # The on-screen measurement projects tips through the shot camera, so it needs world space and the mesh's own transform.
            'tips_w': {k: _v(_mt.bone_transform(mesh, b).translation) for k, b in CHAIN_TIPS.items()},
            'mesh_w': _v(mesh.get_world_location()) + [round(mesh.get_world_rotation().yaw, 4),
                                                        round(mesh.get_world_rotation().pitch, 4),
                                                        round(mesh.get_world_rotation().roll, 4)],
            'lod': [mesh.get_forced_lod(), mesh.get_editor_property('predicted_lod_level')
                    if hasattr(mesh, 'predicted_lod_level') else None],
        })
    since = _cost('pawns', since)
    out = _mrq_frame()
    if out is not None:
        row['out'] = out[0]
    if _cfg.get('heat'):
        _heat(row)
        since = _cost('heat', since)
    _penetration(row)
    _cost('penetration', since)
    _KDD['frames'].append(row)


def _penetration(row):
    """Every pawn whose cloth simulates: this frame's cloth penetration into its body (dyn_contact), by pawn index."""
    import dyn_contact
    on = [i for i, c in enumerate(_KDD['events'].get('cloth') or []) if c.get('arm') == 'on']
    if on:
        limbs = dyn_contact.limbs(HERO['profile'])
        bones, aims = ([unreal.Name(pair[k]) for pair in limbs] for k in (0, 1))
        exclude = [unreal.Name(b) for b in dyn_contact.excluded(PARTS)]
        row['penetration'] = {str(i): json.loads(unreal.HeroDynamicsTools.measure_cloth_penetration(
            _KDD['pawns'][i].get_editor_property('mesh'), bones, aims, exclude, dyn_contact.TOL_CM)) for i in on}


def _mrq_frame():
    """Under MRQ (fixed_hz None): (output frame index, total output frames) of the render driving this world, so each
    row names the rendered frame it shaped and the drive lasts to the render's last frame (a drive that ended at its
    hold left the render's tail with a parked camera and a stale heat tint). None outside MRQ."""
    if _cfg['fixed_hz']:
        return None
    lib = unreal.MoviePipelineLibrary           # UMoviePipelineBlueprintLibrary
    if _KDD.get('mrq') is None:
        seen = [(p.get_path_name(), list(lib.get_overall_output_frames(p)))
                for p in unreal.ObjectIterator(unreal.MoviePipeline)]
        _KDD['events'].setdefault('mrq_probe', seen)
        # the running one: frames to make and not all made (the class default object has none); its index is -1 until
        # the first output frame
        live = [p for p in unreal.ObjectIterator(unreal.MoviePipeline)
                if lib.get_overall_output_frames(p)[0] < lib.get_overall_output_frames(p)[1]
                and lib.get_overall_output_frames(p)[1] > 0]
        if len(live) != 1:
            raise RuntimeError('MRQ: %d running movie pipelines, not one: %s' % (len(live), seen[:4]))
        _KDD['mrq'] = live[0]
        _KDD['events']['mrq_frames'] = lib.get_overall_output_frames(live[0])[1]
    return tuple(lib.get_overall_output_frames(_KDD['mrq']))


def _cloth(mesh, on):
    """Configure the mesh's own cloth for this arm and say what was done (recorded in events['cloth'])."""
    if not HERO['profile'].get('stock', {}).get('clothing_assets'):
        return {'clothing': False}
    if not on:
        if _cfg.get('off_arm_cloth'):
            return {'clothing': True, 'arm': 'off', 'cloth': 'stock simulation (off_arm_cloth)'}
        # Set on the PLACED pawn in the editor world (place()), never here: set_editor_property on the live PIE
        # component runs its property-change path, which re-initialised the animation (the OFF pelvis started 15 cm
        # low and eased back over 0.3 s: a 14.9 cm isolation failure).
        return {'clothing': True, 'arm': 'off', 'cloth': 'blended out (no simulation)',
                'blend_weight': mesh.get_editor_property('cloth_blend_weight')}
    try:
        pa = unreal.load_asset(HERO['cloth_collision_asset'])
    except Exception:
        pa = None
    if pa is None:
        raise RuntimeError('%s has clothing but no cloth collision bodies %s: run hero_onboard.py --stage physics'
                           % (HERO['rig'], HERO['cloth_collision_asset']))
    mesh.add_cloth_collision_source(mesh, pa)
    return {'clothing': True, 'arm': 'on', 'cloth': 'simulated with collision source', 'source': pa.get_path_name(),
            'collision': _cloth_collision(mesh)}


def _cloth_collision(mesh):
    """The cloth's collision quality (dyn_contact.SUBSTEPS, config 'cloth_substeps' to sweep it): continuous collision
    for every clothing asset (ChaosClothConfig.h:234), each keeping its own collision thickness and friction, and the
    solver's substeps per frame: UHeroDynamicsTools, since UChaosClothConfig is not readable from Python. Without it
    the dress crossed a thigh between steps at a run, stayed trapped inside it, and jittered out only after the
    stop."""
    import dyn_contact
    substeps = dyn_contact.SUBSTEPS if _cfg.get('cloth_substeps') is None else _cfg['cloth_substeps']   # 0: the assets' own
    got = json.loads(unreal.HeroDynamicsTools.set_cloth_collision(mesh, True, substeps))
    if not got.get('ok'):
        raise RuntimeError('%s: cloth collision not set: %s' % (HERO['rig'], got.get('error')))
    return dict({k: got[k] for k in ('substeps', 'assets')}, garment=_garment(mesh, list(got['assets'])))


def _garment(mesh, assets):
    """The hero's garment settings (dyn_behaviours.garment; config 'garment' overrides them for a sweep) on every
    clothing asset, through its interactors; returns what was set."""
    g = importlib.reload(importlib.import_module('dyn_behaviours')).garment(HERO['profile'], _cfg.get('garment'))
    sim = mesh.get_clothing_simulation_interactor()
    if 'iterations' in g:
        sim.set_num_iterations(int(g['iterations']))
    if 'max_iterations' in g:
        sim.set_max_num_iterations(int(g['max_iterations']))
    for name in (assets if 'damping' in g or 'linear_velocity_scale' in g else ()):   # garment() keeps groups whole
        cloth = sim.get_clothing_interactor(name)
        if cloth is None:
            raise RuntimeError('%s: no clothing interactor for %s' % (HERO['rig'], name))
        if 'damping' in g:
            cloth.set_damping(float(g['damping']), float(g['local_damping']))
        if 'linear_velocity_scale' in g:
            v = float(g['linear_velocity_scale'])
            cloth.set_velocity_scale(unreal.Vector(v, v, v), float(g['angular_velocity_scale']),
                                     float(g['fictitious_angular_scale']))
    return g


def _heat_begin():
    """Heat mode (dyn_heat): the first simulated pawn tinted by its per-vertex distance from the first unsimulated one
    (the same animation, no simulation, its cloth blended out)."""
    import dyn_heat
    alphas = _cfg['alphas']
    if _cfg.get('off_arm_cloth') or not (any(a > 0.0 for a in alphas) and any(a <= 0.0 for a in alphas)):
        raise RuntimeError('heat needs a simulated pawn and an unsimulated one without stock cloth (alphas %s, '
                           'off_arm_cloth %s)' % (alphas, _cfg.get('off_arm_cloth')))
    pick = lambda on: _KDD['pawns'][next(i for i, a in enumerate(alphas) if (a > 0.0) == on)]  # noqa: E731
    meshes = _KDD['heat_meshes'] = [pick(on).get_editor_property('mesh') for on in (True, False)]
    material = unreal.load_asset(dyn_heat.MATERIAL)        # place() built it before PIE
    if material is None:
        raise RuntimeError('heat: %s not found (place builds it)' % dyn_heat.MATERIAL)
    got = json.loads(unreal.HeroDynamicsTools.begin_displacement_heat(meshes[0], meshes[1], material))
    if not got.get('ok'):
        raise RuntimeError('heat: %s' % got.get('error'))
    _KDD['events']['heat'] = {'scale_cm': dyn_heat.SCALE_CM, 'material': dyn_heat.MATERIAL, 'begin': got}


def _heat(row):
    """One frame of the heatmap; a frame that cannot be measured records its error, never a neutral tint."""
    import dyn_heat
    shown, reference = _KDD['heat_meshes']
    row['heat'] = json.loads(unreal.HeroDynamicsTools.apply_displacement_heat(shown, reference, dyn_heat.SCALE_CM))


def _reaction_alpha(i, el):
    """Pawn i's HitReactionAlpha at el seconds after spawn (reaction_schedule; held when there is none)."""
    peak = _cfg['reaction_alphas'][i]
    sched, hits = _cfg.get('reaction_schedule'), _cfg.get('hits')
    if not sched or not hits:
        return peak
    if el - min(h['at_s'] for h in hits) < -sched['arm_s']:
        return 0.0
    t = el - max(h['at_s'] for h in hits)
    if t <= sched['hold_s']:
        return peak
    return peak * max(0.0, 1.0 - (t - sched['hold_s']) / sched['fade_s'])


def _apply_hit(hit, now):
    """Queue the radial impulse on each hit pawn's mesh (SkeletalMeshComponentPhysics.cpp:420): the post-process
    reaction node copies the queue in its pre-update and applies it to its simulated bodies this frame. The component's
    own physics bodies get it too; the record says whether any of them simulate."""
    d = unreal.Vector(*hit['dir'])
    d = d / max(d.length(), 1e-6)
    out = {'frame': _KDD['frame'], 't': round(now, 5), 'config': hit, 'pawns': []}
    for i in hit['pawns']:
        mesh = _KDD['pawns'][i].get_editor_property('mesh')
        bone_w = _mt.bone_transform(mesh, hit['bone']).translation
        centre = bone_w
        if hit.get('com'):
            # focused (hit_reaction_run.FOCUS_RADIUS_RATIO): offset behind the struck body's own centre of mass, ONE
            # bone-local point (volume-weighted as Chaos does: hit_reaction_core.body_com_local)
            (com,) = hit['com']
            tm = _mt.bone_transform(mesh, hit['bone'])
            centre = tm.transform_location(unreal.Vector(*com['local']))
        origin = centre - d * hit['offset_cm']
        mesh.add_radial_impulse(origin, hit['radius'], hit['strength'], unreal.RadialImpulseFalloff.RIF_CONSTANT,
                                hit.get('vel_change', True))
        out['pawns'].append({'pawn': i, 'bone_w': _v(bone_w), 'origin': _v(origin),
                             'ignore_radial_impulse': bool(mesh.get_editor_property('ignore_radial_impulse')),
                             'component_simulating': bool(mesh.is_simulating_physics())})
    return out


def _fly_camera(world):
    cams = unreal.GameplayStatics.get_all_actors_of_class(world, unreal.CineCameraActor)
    if len(cams) != 1:
        raise RuntimeError('expected the sequence\'s one spawnable CineCameraActor, found %d' % len(cams))
    x = _KDD['pawns'][0].get_actor_location().x
    loc = unreal.Vector(x + CAM_OFFSET[0], CAM_OFFSET[1], CAM_OFFSET[2])
    rot = unreal.MathLibrary.find_look_at_rotation(loc, unreal.Vector(x + CAM_LOOK[0], CAM_LOOK[1], CAM_LOOK[2]))
    cams[0].set_actor_location_and_rotation(loc, rot, False, True)
    cine = cams[0].get_cine_camera_component()
    cine.set_editor_property('current_focal_length', CAM_FOCAL_MM)
    if _cfg.get('cam_focus'):
        # The CineCamera's default manual focus is 100000 cm (CineCameraSettings.h:274). At 25 mm and f/2.8 the
        # hyperfocal distance is ~7.4 m, so the pair at ~5.4 m (the closer framing) sat inside the near blur limit;
        # the earlier framing at ~11 m did not. Focus on the look point every frame, at the configured aperture.
        look = unreal.Vector(x + CAM_LOOK[0], CAM_LOOK[1], CAM_LOOK[2])
        fs = cine.get_editor_property('focus_settings')
        fs.set_editor_property('focus_method', unreal.CameraFocusMethod.MANUAL)
        fs.set_editor_property('manual_focus_distance', (loc - look).length())
        cine.set_editor_property('focus_settings', fs)
        cine.set_editor_property('current_aperture', _cfg.get('cam_aperture', 5.6))
    if _cfg.get('cam_exposure_locked') is not None:
        # A LOCKED exposure (histogram min = max brightness). Faster adaptation alone left the closing
        # stand 0.7 grey levels darker in the sky than the opening one: its converged exposure differs slightly.
        # Units are this project's histogram brightness (linear here, not EV100: 8 rendered black). 1.25 was found by a
        # bisection to reproduce the opening stand's auto-exposed sky (120.1 vs 120.2 grey).
        pp = cine.get_editor_property('post_process_settings')
        for name in ('auto_exposure_min_brightness', 'auto_exposure_max_brightness'):
            pp.set_editor_property('override_' + name, True)
            pp.set_editor_property(name, float(_cfg['cam_exposure_locked']))
        cine.set_editor_property('post_process_settings', pp)
    elif _cfg.get('cam_exposure_speed'):
        # The standing loops at the head and tail must match up cleanly so the clip cycles. Auto exposure drifted
        # during the run (sky 120.2 -> 116.5 at the stop) and was still recovering at the clip's end (118.9), so the
        # closing stand could never match the opening one. Same framing at both ends means the same converged
        # exposure; adapting faster gets back there well before the hold ends.
        pp = cine.get_editor_property('post_process_settings')
        for name in ('auto_exposure_speed_up', 'auto_exposure_speed_down'):
            pp.set_editor_property('override_' + name, True)
            pp.set_editor_property(name, float(_cfg['cam_exposure_speed']))
        cine.set_editor_property('post_process_settings', pp)


def _build_manifest():
    """What the loaded rig and ABP were built from: dynamics_graph.py writes Saved/MotionTerminalTests/
    khai_dyn_build.json at the end of every build (hashing files at the end of a drive says what is on disk, not
    what was built). Read when this module is exec'd for the drive, with the engine version added."""
    path = HERO['manifest']
    try:
        with open(path) as f:
            doc = json.load(f)
    except (OSError, ValueError):
        doc = {'missing': path}
    if doc.get('hero') not in (None, HERO['rig']):
        doc['hero_mismatch'] = 'the loaded build is for %s, the drive for %s' % (doc.get('hero'), HERO['rig'])
    doc['engine'] = unreal.SystemLibrary.get_engine_version()
    return doc


# Snapshots taken when the drive starts, so the record carries what it was measured against (and dyn_gate
# compares these with the build manifest).
BUILD = _build_manifest()
COLLIDERS = _load_profile().get('colliders', {})
TAILS = {t['bone']: t['local'] for c in COLLIDERS.get('chains', {}).values() for t in c.get('tails', [])}


def _finish(world):
    _KDD['pawns'] = []
    _KDD['mrq'] = None                  # the pipeline object is not JSON and must not outlive the run
    for name, _during, after in _cfg['cvars']:
        unreal.SystemLibrary.execute_console_command(world, '%s %s' % (name, after))
    prior = _KDD['events'].get('prior_timestep')
    if prior is not None:
        unreal.MTDriveLibrary.set_fixed_time_step(bool(prior[0]), prior[1] if prior[0] else 0.0)
    unreal.unregister_slate_pre_tick_callback(unreal._MT_KDD_HANDLE)
    unreal._MT_KDD_HANDLE = None
    _KDD['done'] = True
    out_dir = os.path.join(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_saved_dir()),
                           'MotionTerminalTests')
    os.makedirs(out_dir, exist_ok=True)
    if _KDD.get('wall0') is not None:
        import time
        _KDD['events'].setdefault('cost_ms', {})['wall'] = round((time.perf_counter() - _KDD['wall0']) * 1000.0, 1)
    path = _dh.record_path(HERO, _cfg['tag'])
    with open(path, 'w') as fh:
        json.dump({'config': _cfg, 'hero': HERO['rig'], 'roles': _ROLES, 'parts': PARTS, 'colliders': COLLIDERS,
                   'tails': TAILS,
                   'build': BUILD, 'camera': {'offset': CAM_OFFSET, 'look': CAM_LOOK, 'focal_mm': CAM_FOCAL_MM},
                   'events': _KDD['events'],
                   'frames': _KDD['frames'], 'error': _KDD['error']}, fh)
    ticks = [[r['pawns'][i]['curves']['MT_DynRigTick'] for r in _KDD['frames']]
             for i in range(len(_cfg['alphas']))] if _KDD['frames'] else []
    ev = {k: v for k, v in _KDD['events'].items() if k != 'lane_traces'}
    traces = _KDD['events'].get('lane_traces') or []
    ev['lanes_equivalent'] = (len({json.dumps([s[1:] for s in lane['samples']]) for lane in traces}) == 1
                              if traces else None)
    summary = {'tag': _cfg['tag'], 'hero': HERO['rig'], 'path': path, 'frames': len(_KDD['frames']), 'events': ev,
               'error': _KDD['error'],
               'tick_found_all': [all(c['found'] for c in t) for t in ticks],
               'tick_first_last': [[t[0]['value'], t[-1]['value']] if t else None for t in ticks]}
    unreal.log('KHAIDYN_DRIVE ' + json.dumps(summary))


def _tick(dt):
    try:
        world = unreal.EditorLevelLibrary.get_game_world()
        if world is None or _KDD['done']:
            return
        now = unreal.SystemLibrary.get_game_time_in_seconds(world)
        # One row per WORLD frame. Under MRQ the Slate pre-tick fires more than once per world step (one render
        # wrote 497 rows over 421 distinct times), and a repeated row is not a new sample.
        if now == _KDD.get('last_now'):
            return
        _KDD['last_now'] = now
        if _KDD['t0'] is None:
            for name, during, _after in _cfg['cvars']:
                unreal.SystemLibrary.execute_console_command(world, '%s %s' % (name, during))
            _KDD['events']['cvars'] = _cfg['cvars']
            if _cfg['fixed_hz']:
                fixed, hz = unreal.MTDriveLibrary.get_fixed_time_step()
                _KDD['events']['prior_timestep'] = [fixed, hz]
                if not unreal.MTDriveLibrary.set_fixed_time_step(True, _cfg['fixed_hz']):
                    raise RuntimeError('set_fixed_time_step refused %r' % _cfg['fixed_hz'])
            _KDD['events']['lane_traces'] = _lane_traces(world)
            _spawn(world)
            import time
            _KDD['wall0'] = time.perf_counter()     # the drive's wall time, against which cost_ms's parts read
            _KDD['t0'] = now
            _KDD['frame'] += 1
            return
        el = now - _KDD['t0']
        driving = _cfg['settle_s'] <= el < _cfg['settle_s'] + _cfg['run_s']
        if driving:
            if 'first_input_frame' not in _KDD['events']:
                _KDD['events']['first_input_frame'] = [_KDD['frame']] * len(_KDD['pawns'])
                _KDD['events']['t_drive'] = round(now, 5)
            for pawn in _KDD['pawns']:
                pawn.add_movement_input(unreal.Vector(1.0, 0.0, 0.0), 1.0, True)
        elif el >= _cfg['settle_s'] + _cfg['run_s'] and 't_release' not in _KDD['events']:
            _KDD['events']['t_release'] = round(now, 5)
        done = _KDD['events'].setdefault('hits', []) if _cfg.get('hits') else None
        for hit in sorted(_cfg.get('hits') or [], key=lambda h: h['at_s'])[len(done or []):]:
            if el < hit['at_s']:
                break
            done.append(dict(_apply_hit(hit, now), el=round(el, 5)))     # el: seconds after spawn, as at_s
        _sample(world, now, unreal.GameplayStatics.get_world_delta_seconds(world))
        for cmd in ((_cfg.get('console_at') or {}).get(str(_KDD['frame'])) or []):
            unreal.SystemLibrary.execute_console_command(world, cmd)
        if _cfg['camera']:
            _fly_camera(world)
        _KDD['frame'] += 1
        out = _KDD['frames'][-1].get('out') if _KDD['frames'] else None
        if (el >= _cfg['settle_s'] + _cfg['run_s'] + _cfg['hold_s']
                and (out is None or out >= _KDD['events']['mrq_frames'] - 1)):    # under MRQ: its last frame too
            _finish(world)
    except Exception as exc:   # record and stop: a half-run must never read as a run
        _KDD['error'] = repr(exc)
        unreal.log_error('KHAIDYN_DRIVE error %s %r' % (json.dumps({'tag': _cfg['tag'], 'hero': _cfg['hero']}), exc))
        try:
            world = unreal.EditorLevelLibrary.get_game_world()
            _finish(world)
        except Exception as exc2:
            unreal.log_error('KHAIDYN_DRIVE finish failed %r' % exc2)


_action = globals().pop('KDD_ACTION', 'drive')
if _action == 'place':
    place()
elif _action == 'clear':
    clear(save=True)
elif _action == 'drive':
    _refused = _dh.overwrite_refusal(_dh.record_path(HERO, _cfg['tag']), _cfg)
    if _refused:
        raise RuntimeError('KHAIDYN_DRIVE refused: ' + _refused)
    unreal._MT_KDD_HANDLE = unreal.register_slate_pre_tick_callback(_tick)
    unreal.log('KHAIDYN_DRIVE registered %s' % json.dumps({k: _cfg[k] for k in ('tag', 'hero', 'alphas')}))
else:
    raise ValueError('KDD_ACTION must be place | drive | clear, not %r' % _action)
