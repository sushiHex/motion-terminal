# Tiered crowd spawner (the army).
#
# Runs inside the editor over `run_python`; the caller PREPENDS an action and exec()s this file:
#     ARMY_ACTION = 'spawn'      # 'spawn' | 'despawn' | 'report' | 'assert'
#     ARMY_COUNTS = {'hero':2,'mid':2,'far':8}   # optional ramp override, else army_config.json counts
#     exec(open(r'.../Scripts/army_spawn.py').read())
# Every action prints one `ARMY <json>` line to LogPython so the caller can parse it.
#
# GOTCHA: the editor's Python globals PERSIST between run_python calls. A stale ARMY_COUNTS from an earlier
# ramp call once silently re-applied — a "full army" spawn came back as 12, with a self-consistent budget
# line that gave no hint. Documenting that was not enough, so the dispatch at the bottom now `pop`s BOTH
# names out of globals(): a call that does not set them cannot inherit them from the previous call.
# Set BOTH ARMY_ACTION and ARMY_COUNTS (use None for the config counts) on every call.
#
# GOTCHA-A′ (project rule — violating it is a HARD EDITOR CRASH, seen once while building this file):
# never change maps from inside run_python while Python holds a reference to a world/level/actor.
# `duplicate_asset`/`new_level` return a World; because run_python execs into a PERSISTENT namespace, any
# name still bound to it keeps the outgoing world alive, and the next map load fatal-errors on
# `EditorServer.cpp:2544 World Memory Leaks` rather than leaking. Consequences baked into this file:
#   * nothing here binds a World, Level or actor list to a module-level global — every collection is a local
#     that dies with the call, and the action dispatch at the bottom keeps only strings/None;
#   * map transitions belong to an editor-level load, not a Python-side LoadLevel;
#   * DISCARD SPAWNS WITH despawn_army(), NOT by reloading the level. Explicitly destroying the tagged
#     actors avoids the map change — and therefore the entire crash class — which is why that helper exists.
#
# ============================ WHY IT IS SHAPED LIKE THIS ============================
#
# SPAWN PATH. Actors are spawned into the EDITOR world BEFORE start_pie and PIE duplicates them.
# That is not a preference, it is the only path that works here:
#   * EditorActorSubsystem.spawn_actor_from_class REFUSES while PIE is live.
#   * UWorld.spawn_actor / begin_deferred_actor_spawn are not exposed to Python.
# Per-tier settings are therefore set on the EDITOR actors; they reach PIE because every knob used below
# (visibility_based_anim_tick_option, enable_update_rate_optimizations, PrimaryComponentTick.TickInterval,
# actor Tags) is a UPROPERTY and survives the PIE duplication. Anything that is NOT a UPROPERTY cannot be
# pre-set this way — which is exactly why the URO *cadence* is not set here (see below).
#
# ALWAYS_TICK_POSE_AND_REFRESH_BONES is forced on every spawned mesh. The viewport in an automated session is headless
# (0x0), so without it the off-screen instances stop ticking their pose and the crowd silently undercounts —
# you would be measuring a much smaller army than you spawned.
#
# WHAT "TIER" ACTUALLY MEANS HERE — the honest inventory of reachable knobs (live probes):
#   * `enable_update_rate_optimizations` (per component, UPROPERTY)  -> REACHABLE. hero off / mid,far on.
#   * `AnimUpdateRateShiftTag`                                       -> NOT REFLECTED TO PYTHON at all.
#   * the per-instance URO cadence (FAnimUpdateRateParameters)       -> not a UPROPERTY; unreachable.
#   * CVar `a.URO.ForceAnimRate`                                     -> reachable but GLOBAL: one value for
#     the whole world, so it cannot express mid=2 and far=4 simultaneously. It is a measurement instrument
#     (the live probes used it to price a rate), not a tiering mechanism.
#   * Animation Budget Allocator                                     -> the real per-component cadence
#     mechanism. It only drives SkeletalMeshComponentBudgeted; since 2026-10-19 the roster pawns derive from
#     AMTCharacter, whose Mesh is one, and any ring with a `budget` object in army_config.json is registered
#     by `register_budget()` in live PIE (it then owns that ring's tick rate and turns its URO off). Before
#     that date the far ring was 0/32 budgeted AND the allocator's global switch was never set — see
#     register_budget's docstring and Pie.Army.BudgetThrottle, which measures the throttling itself.
#   * `PrimaryComponentTick.TickInterval` (per component, UPROPERTY) -> REACHABLE, but held at 0 on purpose:
#     it COMPOUNDS with URO (15 Hz ticks that URO skips 3-of-4 evaluate at ~3.75 Hz), and the budget
#     allocator owns far-ring cadence.
# Foot IK is ON in every tier. A live probe proved the ControlRig `Alpha` pin does NOT gate cost (alpha 0 still
# cost 10.18 ms) and `ControlRig.DisableExecutionAll 1` is a no-op, so real IK gating needs the anim node's
# LODThreshold or a no-IK ABP. The tier shape is priced IK-ON throughout and does not depend on it.
#
# BUDGET IS ASSERTED, NOT ASSUMED — but what assert_budget checks is the MODELLED tier SHAPE. spawn refuses to
# run a shape whose summed per-instance costs exceed the ceilings in army_config.json. The guard is real (it
# runs before any spawn and it raises); its INPUTS were modelled, not validated for the shipped config — the live
# probes measured the mid/far per-instance numbers under a GLOBAL `a.URO.ForceAnimRate` (rates 2 and 4) the shipped
# army does not set, at rates the engine cannot even produce here: with ForceAnimRate 0 a rendered instance
# takes the distance-factor branch of AnimUpdateRateSetParams, max rate Num(BaseVisibleDistanceFactorThesholds)
# +1 = 3 — so mid and far both land on 3, and far never reaches 4.
#
# LIVE DRIVEN COST MEASURED (re-measured with the army_conductor RUNNING — the shipped
# speed/turn/aim waves + montage sectors). THE ORIGINAL SWEEP MEASURED A STANDING/IDLE CROWD BY MISTAKE (this
# probe path spawns + reads but never starts the conductor) and under-reported by ~1.4-1.8x; the old
# "8.6 ms worker / gt 3.33 comfortable / N=46 kept" is SUPERSEDED. DRIVEN at N=46: worker ~12.4 ms avg / 15.9 peak,
# game-thread ~3.6 ms avg / 4.21 ms PEAK. WHICH CEILING BINDS: gt_ms_ceiling is the HARD gate (serial per-frame),
# ms_ceiling ADVISORY (summed across parallel cores; ~0.8 ms wall-clock at N=46 driven). Under drive the HARD gt
# gate is UNDER on average (3.6/4.0) but OVER on PEAK (4.21/4.0) during the montage volley; driven crossings
# gt-avg@N~=52, gt-peak@N~=42, worker-avg@N~=33. DECIDED: the HARD gate is the gt
# AVERAGE and the peak is a disclosed risk signal, so N=46 STAYS (gt avg 3.6/4.0). assert_budget's per-instance
# INPUTS low-ball hard (advisory); the tier SHAPE guard is still worth running. Standing claim, updated:
#   "tiered crowd of 46 spawned and verified distinct; LIVE DRIVEN COST MEASURED — worker ~12.4/15.9 (avg/peak),
#    game-thread ~3.6/4.21 at N=46; HARD gate on the gt AVERAGE (3.6/4.0) => N=46 kept; peak disclosed as risk."
# ANIMATION MEMORY (the other half of the budget): summed GetResourceSizeBytes(EstimatedTotal) over each
# crowd SkeletalMeshComponent + its AnimInstance = 4.637 MB at N=46 (~0.1 MB/instance); mb_ceiling 6.0 MB. That
# gate reads live components (no stats thread), so unlike the ms gate it is enforced in headless CI.
# See army_config _costs_validated_via_interactive_probe / budget._ceiling_policy / budget._mb_ceiling,
# and research/fixtures/army-contact/n_vs_eval_ms.csv. Re-measure with
# archive/army_budget_probe.py (ms) and the Pie.Army.Budget log line (memory).
import json
import math
import os

import unreal

_CONFIG_REL = 'Plugins/MotionTerminal/Tests/army_config.json'
_TIERS = ('hero', 'mid', 'far')


def config_path():
    return os.path.join(unreal.Paths.project_dir(), _CONFIG_REL).replace('\\', '/')


def load_config(path=None):
    with open(path or config_path(), 'r') as handle:
        return json.load(handle)


def _budget_sums(config, counts=None):
    """Sum the MEASURED per-instance costs of the requested shape against the recorded ceilings. Never
    raises — assert_budget() and spawn_army(skip_budget_check=True) both build on this."""
    budget = config['budget']
    worker = sum(config['rings'][t]['worker_ms_per_inst'] * _count(config, counts, t) for t in _TIERS)
    game = sum(config['rings'][t]['gt_ms_per_inst'] * _count(config, counts, t) for t in _TIERS)
    return {
        'worker_ms': round(worker, 3), 'worker_ceiling': budget['ms_ceiling'],
        'gt_ms': round(game, 3), 'gt_ceiling': budget['gt_ms_ceiling'],
        'n': sum(_count(config, counts, t) for t in _TIERS),
    }


def assert_budget(config, counts=None):
    """Sum the MEASURED per-instance costs of the requested shape against the recorded ceilings.

    Raises ValueError when the shape overspends. Returns the sums so callers can log them.
    """
    budget = config['budget']
    sums = _budget_sums(config, counts)
    over = []
    if sums['worker_ms'] > budget['ms_ceiling']:
        over.append('worker %.3f > %.1f ms' % (sums['worker_ms'], budget['ms_ceiling']))
    if sums['gt_ms'] > budget['gt_ms_ceiling']:
        over.append('game-thread %.3f > %.1f ms' % (sums['gt_ms'], budget['gt_ms_ceiling']))
    if over:
        raise ValueError('army shape exceeds the measured budget: ' + '; '.join(over))
    return sums


def _count(config, counts, tier):
    if counts and tier in counts:
        return int(counts[tier])
    return int(config['rings'][tier]['count'])


def editor_world():
    return unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()


def game_world():
    # EditorLevelLibrary.get_game_world() returns null from standalone run_python; this one does not.
    return unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world()


def _resolve_roster(config):
    """Load each roster class. Returns {key: class} for the ones that load; spawn_army reports the rest.

    It used to drop a missing class with a warning, and the tier's pool then cycled the rigs that were left:
    the crowd still came back N strong, so a run that had lost a whole rig read exactly like one that had not.
    """
    resolved = {}
    for key, path in config['roster'].items():
        try:
            cls = unreal.load_class(None, path)
        except Exception:
            cls = None
        if cls is not None:
            resolved[key] = cls
    return resolved


def _mesh_of(actor):
    return actor.get_component_by_class(unreal.SkeletalMeshComponent)


def _apply_tier(mesh, config, tier):
    """Per-tier mesh settings. Returns what was actually applied, for the readback."""
    ring = config['rings'][tier]
    applied = {}
    # Headless viewport (0x0) culls off-screen instances out of the pose tick — force them all on, or the
    # crowd undercounts.
    mesh.set_editor_property(
        'visibility_based_anim_tick_option',
        unreal.VisibilityBasedAnimTickOption.ALWAYS_TICK_POSE_AND_REFRESH_BONES)
    mesh.set_editor_property('enable_update_rate_optimizations', bool(ring['uro']))
    applied['uro'] = bool(ring['uro'])
    # Forced LOD is UE's 1-BASED override (0 = auto-select, 1 = LOD0, 2 = LOD1, 3 = LOD2). SKM_Manny ships 3
    # LODs. This is the per-component knob that differentiates mid from far, since URO's own cadence is not
    # settable per instance.
    # DO NOT claim it changes URO cadence: URO consults LODToFrameSkipMap ONLY when bShouldUseLodMap is true
    # (SkinnedMeshComponent.cpp:307), that flag defaults false (EngineTypes.h:2811) and is settable only from
    # the C++-only URO customization callback, which nothing here installs. Forced LOD therefore has ZERO
    # effect on URO cadence in this project. The only surviving cost leg is the required-bone set, and it is
    # UNMEASURED — the budget probe must measure it rather than bank it as a known saving.
    # set_forced_lod() (not the ForcedLodModel property, UE_DEPRECATED(4.24) + BlueprintReadOnly) is the
    # supported setter and additionally sets ForceStreamedLodModel; both are UPROPERTYs, so the value
    # survives the PIE duplication like every other knob set here.
    mesh.set_forced_lod(int(ring.get('forced_lod', 0)))
    applied['forced_lod'] = int(mesh.get_forced_lod())
    mesh.set_component_tick_interval(0.0)
    applied['tick_interval'] = 0.0
    return applied


def spawn_army(config=None, counts=None, skip_budget_check=False):
    """Spawn the tiered crowd into the EDITOR world in concentric rings. Call BEFORE start_pie.

    Takes no `world`: EditorActorSubsystem spawns into the editor world on its own, and a World bound to a
    name in here is exactly the GOTCHA-A′ hazard this file exists to avoid.

    skip_budget_check=True spawns without raising on an over-budget shape — for archive/army_budget_probe.py's
    N-sweep, which must be able to spawn OVER budget; that is the whole point of finding the cliff.
    """
    config = config or load_config()
    sums = _budget_sums(config, counts) if skip_budget_check else assert_budget(config, counts)
    spawn = config['spawn']
    roster = _resolve_roster(config)
    # Every rig a tier that will actually spawn asks for, whether the roster lacks the key (misspelled) or the
    # class does not load. Checked BEFORE any actor exists, so a refusal never leaves a partial crowd behind.
    requested = {k for t in _TIERS if _count(config, counts, t) > 0 for k in spawn['tier_pawns'][t]}
    wanted = sorted(k for k in requested if k not in roster)
    if wanted and spawn.get('require_all_rigs', True):
        raise RuntimeError('ARMY rig(s) %s are in spawn.tier_pawns but not loadable on this machine; a crowd '
                           'without them would still count N and look complete. Add the content, or set '
                           'spawn.require_all_rigs false to spawn without them on purpose.' % wanted)
    # spawn.crowd_anim_class swaps a rig's crowd instances onto an in-place graph: a rig whose shipped graph
    # carries baked root travel needs one in the crowd, where nothing extracts that travel, and a new rig is a
    # config row, not a new branch here. Was `_use_inplace_khaimera` (Khaimera matched by class NAME) until
    # 2026-10-19, then `_apply_crowd_anim_class`.
    #
    # THE DEFECT THIS FIXES (found by watching the crowd take). The crowd spawned Khaimera with
    # ABP_MT_Khaimera_C, which drives the ORIGINAL retargeted clips. Those carry baked pelvis translation -
    # 1362.86 cm of net travel on the jog (render_evidence roster_4up khaimera_pelvis_net_travel_original_jog_cm)
    # - and every ABP here runs ROOT_MOTION_FROM_MONTAGES_ONLY, so that translation is never EXTRACTED as root
    # motion. It plays as raw animation and walks the MESH away from its capsule: 941.2 cm of drift at speed
    # 450 (khaimera_pelvis_drift_original_clips_at_speed450_cm). On screen the crowd's Khaimera read as
    # splayed, near-horizontal bodies with their axes left behind, while the mannequins beside them were fine.
    #
    # The roster take hit this first and fixed it by using the in-place graph, whose pelvis travel is
    # 0.0 cm and whose drift at 450 is 10.1 cm - 93x less. The crowd never got that fix, because nothing in the
    # army path ever compared the two: Army.NoFrozen asserts feet MOVE, Army.Budget asserts milliseconds,
    # Army.SpotCheck samples state weights. None of them asks whether a character is standing where its
    # capsule is, which is the same blind spot that hid the aim-offset defect.
    #
    # Applied here rather than by repointing army_config's roster, because the pawn Blueprint is shared with the
    # PIE fixtures whose rows are the only localOnly:false assertions in the project; changing what it
    # references would move those too. The swap is counted and logged, so a run that silently stopped swapping
    # is visible in the render's own output rather than only on screen. A class that does not load is a
    # refusal, not a warning (941.2 cm for Khaimera at 450), and checked here so no partial crowd is left.
    overrides = {}
    for rig, path in spawn.get('crowd_anim_class', {}).items():
        if rig in requested and rig in roster:
            replacement = unreal.load_class(None, path)
            if replacement is None:
                raise RuntimeError('ARMY crowd_anim_class[%s] = %s does not load; refusing to spawn a crowd %s '
                                   'that would drift off its capsule' % (rig, path, rig))
            overrides[rig] = replacement
    subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    cx, cy, _cz = spawn['center']
    z = spawn['z']
    result = {'sums': sums, 'spawned': {}, 'applied': {}, 'meshless': {}, 'missing_rigs': wanted,
              'anim_class_overrides': {}}

    for tier in _TIERS:
        ring = config['rings'][tier]
        want = _count(config, counts, tier)
        pool = [k for k in spawn['tier_pawns'][tier] if k in roster]
        if not pool:
            unreal.log_warning('ARMY tier %s has no loadable pawn class — skipped' % tier)
            result['spawned'][tier] = 0
            continue
        made = 0
        meshless = 0
        # Aggregate, not last-writer-wins: one dict per DISTINCT applied shape, so a tier where some
        # instances took different settings (or none) cannot hide behind a tidy single line.
        shapes = []
        for i in range(want):
            angle = (2.0 * math.pi * i) / max(want, 1)
            x = cx + ring['radius'] * math.cos(angle)
            y = cy + ring['radius'] * math.sin(angle)
            # Face the centre so the whole crowd reads as one formation from a camera on the ring axis.
            yaw = math.degrees(angle) + (180.0 if spawn.get('face_center') else 0.0)
            rig = pool[i % len(pool)]
            actor = subsystem.spawn_actor_from_class(
                roster[rig], unreal.Vector(x, y, z), unreal.Rotator(0.0, 0.0, yaw))
            if actor is None:
                continue
            actor.set_actor_label('Army_%s_%03d' % (tier, i))
            actor.set_editor_property(
                'tags', [unreal.Name(spawn['tag_all']), unreal.Name(spawn['tag_prefix'] + tier)])
            mesh = _mesh_of(actor)
            if mesh is None:
                # Tagged and counted toward N, but UNTIERED — a full-cost instance. Never silent.
                meshless += 1
                unreal.log_warning('ARMY %s[%d] has no SkeletalMeshComponent — tagged but UNTIERED' % (tier, i))
            else:
                if rig in overrides:
                    mesh.set_editor_property('anim_class', overrides[rig])
                    result['anim_class_overrides'][rig] = result['anim_class_overrides'].get(rig, 0) + 1
                shape = _apply_tier(mesh, config, tier)
                if shape not in shapes:
                    shapes.append(shape)
            made += 1
        result['spawned'][tier] = made
        result['applied'][tier] = shapes
        result['meshless'][tier] = meshless
    unreal.log('ARMY ' + json.dumps({'action': 'spawn', 'result': result}))
    return result


def despawn_army(config=None):
    """Destroy every actor tagged MT_Army in the editor world (ramping between counts without a reload)."""
    config = config or load_config()
    tag = unreal.Name(config['spawn']['tag_all'])
    subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    killed = 0
    for actor in subsystem.get_all_level_actors():
        if actor and tag in actor.tags:
            subsystem.destroy_actor(actor)
            killed += 1
    unreal.log('ARMY ' + json.dumps({'action': 'despawn', 'destroyed': killed}))
    return killed


# What a.Budget.Enabled read before register_budget first set it, for release_budget to restore. Module-level
# and therefore persistent across run_python calls (the exec namespace persists), which is the point here.
_BUDGET_SWITCH_BEFORE = globals().get('_BUDGET_SWITCH_BEFORE')


def register_budget(world=None, config=None):
    """Hand every ring that declares a `budget` to the Animation Budget Allocator, and REPORT what took.

    Runs in LIVE PIE (the `report` action), not at spawn: spawn_army writes the EDITOR world, PIE duplicates
    it, and only game worlds get an allocator (AnimationBudgetAllocatorModule.cpp) — registering at spawn
    would register nothing. Three things must all hold, and until 2026-10-19 none did:
      1. the mesh is a SkeletalMeshComponentBudgeted — the roster pawns now derive from AMTCharacter;
      2. it is registered with a significance and throttling allowed — registration alone sets significance 1
         and bNeverThrottle (UMTAnimBudgetLibrary.RegisterBudgetedMesh does both steps);
      3. the GLOBAL switch a.Budget.Enabled is 1 as well as the world flag — EnableAnimationBudget sets only
         the world flag, so the old UFUNCTION-only route left the allocator inert.
    Pie.Army.BudgetThrottle measures the outcome (graph-update rate per ring); this function only reports.
    """
    config = config or load_config()
    world = world or game_world()
    out = {'in_pie': world is not None, 'rings': {}, 'cvar_enabled': None, 'budget_ms_set': None}
    if world is None:
        unreal.log_warning('ARMY register_budget needs live PIE: editor worlds have no allocator — nothing registered')
        unreal.log('ARMY ' + json.dumps({'action': 'register_budget', 'budget': out}))
        return out
    budgeted_rings = [t for t in _TIERS if config['rings'][t].get('budget')]
    for tier in budgeted_rings:
        ring_budget = config['rings'][tier]['budget']
        meshes = [m for m in (_mesh_of(a) for a in _tagged(world, config, tier)) if m is not None]
        took = sum(1 for m in meshes if unreal.MTAnimBudgetLibrary.register_budgeted_mesh(
            m, float(ring_budget['significance']), bool(ring_budget.get('tick_even_if_not_rendered', False))))
        out['rings'][tier] = {'meshes': len(meshes), 'registered': took}
    if budgeted_rings:
        # BudgetInMs is the allowance for the REGISTERED components only:
        #   AvailableBudget = BudgetInMs - ExternalAnimationTimeSpent (AnimationBudgetAllocator.cpp:558),
        # and ExternalAnimationTimeSpent is fed exclusively by C++ AddExternalAnimationTimeSpentThisFrame()
        # calls, which nothing in this project makes. Handing it the whole-scene gt ceiling would therefore
        # tell the allocator that the far tier alone may spend the hero+mid budget too — ~4x over-allocated,
        # so it would never throttle. Feed the budgeted rings' OWN modelled game-thread allocation instead,
        # sized to the meshes actually registered.
        out['budget_ms_set'] = round(sum(config['rings'][t]['gt_ms_per_inst'] * out['rings'][t]['registered']
                                         for t in budgeted_rings), 3)
        # The global switch first: it is half of the allocator's run condition and nothing else sets it.
        # Its prior value is kept so release_budget restores it rather than forcing 0.
        global _BUDGET_SWITCH_BEFORE
        if _BUDGET_SWITCH_BEFORE is None:
            _BUDGET_SWITCH_BEFORE = _cvar('a.Budget.Enabled')
        unreal.SystemLibrary.execute_console_command(world, 'a.Budget.Enabled 1')
        # Parameters AFTER any a.Budget.* console write: a parameter CVar change replaces the whole per-world
        # struct with CVar values (GOnCVarParametersChanged -> SetParametersFromCVars).
        params = unreal.AnimationBudgetAllocatorParameters()
        params.set_editor_property('budget_in_ms', float(out['budget_ms_set']))
        # The default caps OFFSCREEN ticks at 4 meshes and disables the rest outright — throttling by visibility,
        # not by budget, and invisible in a rate measure (Pie.Army.BudgetThrottle's first version was fooled by
        # it). Lift it to the registered count so only the budget throttles.
        params.set_editor_property('max_ticked_offsreen_components',
                                   max(1, sum(r['registered'] for r in out['rings'].values())))
        unreal.AnimationBudget.enable_animation_budget(world, True)
        unreal.AnimationBudget.set_animation_budget_parameters(world, params)
        out['cvar_enabled'] = _cvar('a.Budget.Enabled')
    unreal.log('ARMY ' + json.dumps({'action': 'register_budget', 'budget': out}))
    return out


def release_budget(config=None):
    """Undo register_budget in live PIE: unregister the budgeted rings, disable this world's allocator, and
    put the global switch back to what register_budget found.

    Unregistering forces a mesh's URO and tick back ON (AnimationBudgetAllocator.cpp UnregisterComponent),
    which for the far ring is its configured fallback (`uro: true`), not necessarily its pre-registration
    state. PIE-only on purpose: an editor world has no allocator, and the switch is global.
    """
    global _BUDGET_SWITCH_BEFORE
    config = config or load_config()
    world = game_world()
    released = 0
    if world is not None:
        for tier in (t for t in _TIERS if config['rings'][t].get('budget')):
            for actor in _tagged(world, config, tier):
                mesh = _mesh_of(actor)
                if mesh is not None and unreal.MTAnimBudgetLibrary.unregister_budgeted_mesh(mesh):
                    released += 1
        unreal.AnimationBudget.enable_animation_budget(world, False)
        if _BUDGET_SWITCH_BEFORE is not None:
            unreal.SystemLibrary.execute_console_command(world, 'a.Budget.Enabled %s' % _BUDGET_SWITCH_BEFORE)
            _BUDGET_SWITCH_BEFORE = None
    unreal.log('ARMY ' + json.dumps({'action': 'release_budget', 'released': released, 'in_pie': world is not None}))
    return released


def _cvar(name):
    try:
        return unreal.SystemLibrary.get_console_variable_string_value(name)
    except Exception:
        return None


def _tagged(world, config, tier):
    tag = unreal.Name(config['spawn']['tag_prefix'] + tier)
    return list(unreal.GameplayStatics.get_all_actors_with_tag(world, tag))


def report_army(world=None, config=None, counts=None):
    """Per-tier readback. Run it in LIVE PIE — a tier shape that differs only in JSON is not a tiered crowd.

    EVERY actor is read, not a sample: one sampled instance standing for 32 cannot detect a partial
    application, and the meshless (untiered, full-cost) instances are invisible to a sample by construction.
    Each tier reports the SET of distinct shapes found; a tier that applied cleanly shows exactly one.

    `predicted_lod` is the honest one. forced_lod is what we asked for; get_predicted_lod_level() is the LOD
    the component is actually running, so a tier split that exists only in our own settings shows up here as
    identical predicted LODs across tiers. If it does, say so — it means the tiers are cosmetic on the cost
    axis, which is the budget probe's problem to price.

    PREDICTED LOD IS ONLY MEANINGFUL IN PIE. Run against the editor world it reads 0 for every tier
    regardless of the forced LOD, because nothing has ticked an update to compute it — a false negative, not
    a flat tier shape. `in_pie` is reported so nobody reads the editor-world run as evidence.
    """
    config = config or load_config()
    live = game_world()
    world = world or live or editor_world()
    out = {'world': str(world.get_name()) if world else None, 'in_pie': live is not None, 'tiers': {},
           'cvars': {'a.URO.ForceAnimRate': _cvar('a.URO.ForceAnimRate'),
                     'a.Budget.Enabled': _cvar('a.Budget.Enabled')}}
    for tier in _TIERS:
        actors = _tagged(world, config, tier)
        shapes = []
        predicted = {}
        meshless = []
        for actor in actors:
            mesh = _mesh_of(actor)
            if mesh is None:
                meshless.append(str(actor.get_name()))
                continue
            shape = {
                'mesh_class': str(mesh.get_class().get_name()),
                'uro': bool(mesh.get_editor_property('enable_update_rate_optimizations')),
                'forced_lod': int(mesh.get_forced_lod()),
                'num_lods': int(mesh.get_num_lods()),
                'tick_interval': round(float(mesh.get_component_tick_interval()), 5),
                'visibility_tick': str(mesh.get_editor_property('visibility_based_anim_tick_option')),
                'anim_instance': (str(mesh.get_anim_instance().get_class().get_name())
                                  if mesh.get_anim_instance() else None),
            }
            if shape not in shapes:
                shapes.append(shape)
            lod = int(mesh.get_predicted_lod_level())
            predicted[str(lod)] = predicted.get(str(lod), 0) + 1
        out['tiers'][tier] = {
            'count': len(actors),
            'expected': _count(config, counts, tier),
            'meshless': len(meshless), 'meshless_actors': meshless[:4],
            'distinct_shapes': len(shapes), 'shapes': shapes,
            'predicted_lod_hist': predicted,
        }
    tiered = {t: sorted(out['tiers'][t]['predicted_lod_hist']) for t in _TIERS if out['tiers'][t]['count']}
    out['predicted_lod_differs_across_tiers'] = (
        out['in_pie'] and len({tuple(v) for v in tiered.values()}) > 1)
    unreal.log('ARMY ' + json.dumps({'action': 'report', 'report': out}))
    return out


# POP, do not get: run_python execs into a PERSISTENT namespace, so a `get` inherits the previous call's
# ARMY_COUNTS whenever this one forgot to set it — which is how a ramp count silently became the "full
# army". Popping makes that recurrence structurally impossible: an unset name is None, never stale.
_action = globals().pop('ARMY_ACTION', None)
_counts = globals().pop('ARMY_COUNTS', None)
if _action == 'spawn':
    spawn_army(counts=_counts)
elif _action == 'despawn':
    despawn_army()
elif _action == 'report':
    register_budget()
    report_army(counts=_counts)
elif _action == 'release':
    release_budget()
elif _action == 'assert':
    unreal.log('ARMY ' + json.dumps({'action': 'assert', 'sums': assert_budget(load_config(), _counts)}))
