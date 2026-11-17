# Locomotion-lane take driver — sweeps the locomotion lane (idle -> walk -> run -> sprint -> jump -> fall -> land
# -> jog -> idle) on the placed Template pawns, and records the state-machine timeline it produced.
#
# ============================================================================================
# WHY THIS DRIVER WRITES NO ANIMATION CONTRACT VARIABLE (the load-bearing decision)
#
# All three locomotion contract vars are DERIVED every tick by ABP_MT_Locomotion's
# EventBlueprintUpdateAnimation:
#
#     Speed        = VectorLength(GetVelocity(TryGetPawnOwner))
#     IsFalling    = CastToCharacter(pawn).GetCharacterMovement().IsFalling()
#     GroundedFlag = select(IsFalling, 0, 1)
#
# So a reflection write via SetAnimVariable('Speed', 400) returns `ok`, reads back as 400.0 on a
# subsequent GetAnimVariable, and is OVERWRITTEN before the state machine evaluates — a pawn that
# stands in Idle while every log line says it is running. This driver therefore
# drives the SOURCE of each derivation instead:
#
#   Speed              <- CharacterMovementComponent.Velocity, written directly.
#   IsFalling          <- CharacterMovementComponent.MovementMode (UCharacterMovementComponent::IsFalling
#                         is `MovementMode == MOVE_Falling`, nothing more), set via SetMovementMode.
#   GroundedFlag       <- follows IsFalling through the EventGraph select; never touched here.
#
# and disables the CMC's component tick so nothing re-integrates the velocity we wrote:
# APawn::GetVelocity() returns MovementComponent->Velocity verbatim, so the value survives to
# ABP-update time, and with PerformMovement never running the actor does not translate and does not
# fall. The lane is therefore played IN PLACE — deliberate, so one framing holds the subject for the
# whole 22 s and the state machine is the only thing moving.
#
# WHAT IS ACTUALLY PROVEN, AND HOW: the driver reads the Locomotion machine's live state back out of
# the AnimInstance (AnimDebugTools::GetAnimDebugState) every SAMPLE_DT and accumulates a
# state -> [first_t, last_t] timeline, dumped at the end of the run. That timeline is a read of the
# graph's own weights, not a restatement of what this file intended, so a driver that fails to drive
# shows up as a single `Idle` row rather than a green log over a frozen pawn.
#
# ONE STATE-NAMING NOTE, because it changes what "the state differs" can mean here: walking and running
# are NOT separate states in this graph. The Locomotion machine is authored as
# {Idle, Locomotion, Jump, Fall, Land}, where `Locomotion` is a single BS_Idle_Walk_Run blendspace state
# covering the whole ground gait. Gait change inside it is visible as the driven Speed (and in the
# frames), not as a state transition. The state timeline still changes five times across this take.
#
# GOTCHA-A': no World/Actor/Component ever binds to a module-level name — every handle is a local
# inside _tick. Retires off unreal._MT_DRIVE_GEN, which mrq_render bumps when the render ends.
# ============================================================================================
import importlib
import os
import sys

import unreal

# The shared driver scaffolding (mt_drive.py). Resolved off the .uproject, NOT off `__file__`:
# `mrq_render.render_shot` execs this file into a bare namespace where `__file__` does not exist,
# which is why the scaffolding used to be five copies. Reloaded rather than plain-imported so an
# edit to mt_drive.py takes effect on the next render, the way an edit to this file already does —
# see that module's header for both decisions and for why an import cannot reproduce the exec-merge
# fault that once retimed another take.
_MT_SCRIPTS = os.path.join(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()),
                           'Scripts')
if _MT_SCRIPTS not in sys.path:
    sys.path.insert(0, _MT_SCRIPTS)
_mt = importlib.reload(importlib.import_module('mt_drive'))

# (time_s, speed_cm_s) — piecewise-linear. Speed is what BS_Idle_Walk_Run is sampled at, so these
# are the gait beats: ~150 walk, ~450 run, ~600 sprint (the locomotion smoke test drove 600 as full sprint).
PHASES = [
    (0.0, 0.0),      # settle in Idle
    (1.5, 0.0),
    (2.5, 150.0),    # walk
    (5.0, 150.0),
    (6.0, 450.0),    # run
    (9.0, 450.0),
    (10.0, 600.0),   # sprint
    (12.5, 600.0),
    (13.5, 300.0),   # gather for the jump
    (16.0, 300.0),   # airborne + landing carry the same ground speed
    (18.5, 300.0),   # jog out
    (20.5, 0.0),     # decelerate to Idle
    (22.0, 0.0),
]

# Airborne window. Entering it flips MovementMode to MOVE_Falling, which makes the EventGraph derive
# IsFalling=true / GroundedFlag=0 and fires Locomotion->Jump; leaving it restores MOVE_None, which is
# what satisfies Fall->Land (`GroundedFlag >= 1`). Land->Idle is automatic.
AIRBORNE = (13.5, 16.0)

SAMPLE_DT = 0.25          # state read-back cadence; the timeline resolution of the evidence below
TOTAL = 22.0              # 660 frames at 30 fps — must match this take's shots_manifest frame_range

# ---- subject selection ----
# The stage (Lvl_MT_BeatsDE, shared with the rifle take) holds MT_Manny @ (0,-120) and MT_Khaimera @ (0,120),
# both facing +X. This shot renders MANNY ONLY, and MT_Khaimera is hidden in the render world.
#
# WHY — MEASURED, not a framing preference. Khaimera's machine-local Khai_* baked locomotion clips
# are NOT in-place, and the travel is baked onto the PELVIS rather than the root. Probed live in PIE
# on the same sweep this driver runs, sampling actor / root-socket / pelvis-socket every second:
#
#   t= 4.3  MT_Manny pelvis x=  -3   |  MT_Khaimera pelvis x=  221
#   t= 8.3  MT_Manny pelvis x=  -6   |  MT_Khaimera pelvis x=  643
#   t=10.3  MT_Manny pelvis x= -11   |  MT_Khaimera pelvis x=  986
#   t=12.3  MT_Manny pelvis x= -13   |  MT_Khaimera pelvis x= 1188   (then snaps back on clip loop)
#
# The ROOT bone reads 0 cm of drift for both rigs the whole take, which is why nothing that checks
# the root has ever caught this: the actor never moves, the capsule never moves, and the state
# machine is perfectly correct. The body simply sprints ~12 m out of frame and teleports back each
# time the clip loops. A first 660-frame take was shot before this was found and shows Khaimera
# present in some frames and absent in others, with no shadow — the give-away.
#
# This is a defect in the machine-local baked clips, NOT in the Template, and it is invisible to
# the rifle take (its aim and off-hand reach beats run at Speed 0, so the Locomotion blendspace state is never entered). Khaimera
# is still DRIVEN here — his contract-var read-backs stay in the log as a second witness that the
# Template derives identically on a non-Mannequin skeleton — he is only hidden from camera.
HIDE_FROM_CAMERA = ('MT_Khaimera',)

# The cube weapon stand-in. BP_MT_TestPawn carries a `WeaponMesh` holding SM_MT_Rifle — a project-authored
# duplicate of an engine Cube, scaled into a bar. It was right for the weapon beats and it is wrong
# here: this shot is UNARMED locomotion, no caption in it mentions a weapon, and a grey box riding the
# hip is the only thing on screen the state machine did not put there. Hidden per render on the live
# component (the proven pie_roster4_drive pattern) — NOT an asset edit; Content/MT/SM_MT_Rifle.uasset is
# tracked and is not touched. Swapping in the real Fab rifle was rejected: a rifle in an unarmed
# locomotion beat would assert something the footage does not show. Only the STAND-IN goes; Khaimera's
# axe is part of his skeletal mesh, not a StaticMeshComponent, so it is untouched by this (and he is
# hidden from camera here anyway).
HIDE_WEAPON_MESH = True

# ---- camera (world space), now centred on MT_Manny alone ----
# A slow push-in on a front-3/4: enough parallax that 22 s does not read as a still, without a cut.
# Aimed at z=105, below the weapon beats framing's 118, because a locomotion shot lives or dies on the
# foot plant and the feet must stay in frame. Sized off a 90-frame framing pass: at 28 mm on the
# CineCamera's default 23.76x13.365 mm filmback the vertical coverage is 0.4785 * distance, so a
# ~190 cm character fills 55% of frame height at the A distance (716 cm) and 68% at B (581 cm).
CAM_A = (unreal.Vector(590.0, -510.0, 215.0), unreal.Vector(0.0, -120.0, 105.0))
CAM_B = (unreal.Vector(500.0, -410.0, 165.0), unreal.Vector(0.0, -120.0, 105.0))
CAM_FOCAL = 28.0

# Retire any previous instance of THIS driver, then claim a fresh generation so any other stale
# driver in the persistent run_python namespace retires on its own next tick.
GEN = _mt.retire_and_claim('_MT_LANE')

# Strings and floats only — no world, no actor, no component (GOTCHA-A').
_S = {'handle': None, 'last': -999.0, 'focal_set': False,
      'timeline': {}, 'visits': [], 'prev': {}, 'summary_at': -999.0, 'dressed': False}


def speed_at(t):
    """Piecewise-linear interpolation of PHASES. Clamped at both ends."""
    if t <= PHASES[0][0]:
        return PHASES[0][1]
    for i in range(1, len(PHASES)):
        t0, v0 = PHASES[i - 1]
        t1, v1 = PHASES[i]
        if t < t1:
            return _mt.lerp(v0, v1, (t - t0) / (t1 - t0)) if t1 > t0 else v1
    return PHASES[-1][1]


def falling_at(t):
    return AIRBORNE[0] <= t < AIRBORNE[1]


def _read_var(label, name):
    """Read a derived contract var back off the live AnimInstance.

    This is the honest direction of proof for this driver: the driver writes CMC state, the ABP
    derives Speed/IsFalling/GroundedFlag from it, and this reads the derived result. A number that
    tracks PHASES here therefore travelled source -> engine -> graph, which is exactly what
    SetAnimVariable('Speed', ...) fails to do. It is still a same-thread read one slate tick after
    the write, so treat it as confirmation of the write, not as an independent witness — the
    independent witness is the state timeline, which is the graph's own weights."""
    try:
        raw = str(_mt.anim_debug_tools().call_method('GetAnimVariable', args=(label, name)))
    except Exception:
        return None
    # GetAnimVariable returns either "<name> = <value>" or "ERROR: <why>" (AnimDebugTools.cpp). A bare
    # split('=')[-1] turns "ERROR: no property 'Speed'" into the string "no property 'Speed'" and prints
    # it in the spd_read= field as though it were a reading — a failed read masquerading as data, which
    # is the same defect class the write side already guards against by checking for the "ok:" prefix.
    if not raw.startswith(name + ' ='):
        return 'READ_FAILED(%s)' % raw.strip()
    return raw.split('=', 1)[1].strip()


def _drive_camera(world, now):
    # Focal is written ONCE, on the first tick that actually finds a camera — hence `focal_set` is
    # latched on the return value rather than on the attempt, so a camera the MRQ PIE world spawns a
    # tick late still gets it. Both ends of CAM_A/CAM_B are lerped inside drive_ab_camera, aim point
    # included, so a future re-frame that moves the target cannot silently keep using the A end.
    cam = _mt.drive_ab_camera(world, now / TOTAL, CAM_A, CAM_B,
                              focal=None if _S['focal_set'] else CAM_FOCAL)
    if cam is not None:
        _S['focal_set'] = True


def _note(label, state, now):
    """Accumulate per-state dwell and the visit sequence.

    DWELL IS SUMMED OVER CONTIGUOUS RUNS, NOT `last_t - first_t`. Two states here are re-entered —
    the lane returns to `Locomotion` after landing and to `Idle` at the end — so a first-to-last span
    reports `Idle held 22.92s` on a 22 s take that is only briefly idle at either end. That is a
    number that looks like evidence and means nothing, so dwell is accumulated only while the state
    is actually held. It still understates by up to one SAMPLE_DT per visit (time is credited between
    samples, not from the true transition instant), which is the honest direction to be wrong in.

    `_S['visits']` records every ENTRY rather than only the first, so the dumped sequence shows the
    real path (… -> Land -> Locomotion -> Idle) instead of stopping at each state's debut."""
    tl = _S['timeline'].setdefault(label, {})
    prev, prev_t = _S['prev'].get(label, (None, None))
    d = tl.setdefault(state, [now, 0.0, 0])
    if prev == state and prev_t is not None:
        d[1] += now - prev_t
    else:
        _S['visits'].append((label, state, now))
    d[2] += 1
    _S['prev'][label] = (state, now)


def _tick(dt):
    try:
        if int(getattr(unreal, '_MT_DRIVE_GEN', 0)) != GEN:
            if _S['handle'] is not None:
                unreal.unregister_slate_pre_tick_callback(_S['handle'])
                _S['handle'] = None
            return
        world = unreal.EditorLevelLibrary.get_game_world()
        if not world:
            return
        now = unreal.SystemLibrary.get_game_time_in_seconds(world)
        _drive_camera(world, now)

        spd = speed_at(now)
        falling = falling_at(now)
        due = (now - _S['last']) >= SAMPLE_DT
        report = []
        dressed = []

        for a in unreal.GameplayStatics.get_all_actors_of_class(world, unreal.Character):
            mesh = a.get_component_by_class(unreal.SkeletalMeshComponent)
            inst = mesh.get_anim_instance() if mesh else None
            if not inst or 'ABP_MT' not in inst.get_class().get_name():
                continue
            lbl = a.get_actor_label()
            # Re-asserted every tick rather than latched: this costs nothing and means a rig that
            # streams in late, or an actor the render world re-shows, cannot leak into frame.
            if lbl in HIDE_FROM_CAMERA:
                a.set_actor_hidden_in_game(True)
            # Latched on FIRST SUCCESS, not on first attempt: `dressed` only stays empty while no
            # driven pawn has been reached yet, so a rig that streams in a tick late is still dressed.
            if HIDE_WEAPON_MESH and not _S['dressed']:
                for comp in a.get_components_by_class(unreal.StaticMeshComponent):
                    comp.set_hidden_in_game(True)
                    dressed.append('%s/%s' % (lbl, comp.get_name()))
            cmc = a.get_component_by_class(unreal.CharacterMovementComponent)
            if not cmc:
                report.append('%s:NO_CMC' % lbl)
                continue

            # ORDER IS LOAD-BEARING — mode, then velocity, then tick-disable LAST, in a try/finally.
            # The measurements behind that ordering, and the airborne beat it silently cost the first
            # take of this shot when it was wrong, are recorded once in mt_drive.drive_cmc's docstring
            # rather than five times across the drivers that all depend on it.
            _mt.drive_cmc(cmc, spd, falling)

            if not due:
                continue
            state = _mt.dominant_state(
                str(_mt.anim_debug_tools().call_method('GetAnimDebugState', args=(lbl,))))
            if state:
                _note(lbl, state, now)
            report.append('%s%s:%s spd_read=%s falling_read=%s grounded_read=%s'
                          % (lbl, '(hidden)' if lbl in HIDE_FROM_CAMERA else '',
                             state or 'NO_MACHINE', _read_var(lbl, 'Speed'),
                             _read_var(lbl, 'IsFalling'), _read_var(lbl, 'GroundedFlag')))

        if dressed:
            _S['dressed'] = True
            unreal.log('LANE dressed hidden_static_meshes=%s' % dressed)

        if due:
            _S['last'] = now
            unreal.log('LANE t=%.2f spd_set=%.0f falling_set=%s | %s'
                       % (now, spd, falling, ' | '.join(report)))
            # Re-dump the accumulated timeline once a second from the last third of the take, so the
            # shipped render's log ends with the full state sequence even though the callback is
            # torn down without warning when MRQ finishes.
            if now >= TOTAL * 0.66 and (now - _S['summary_at']) >= 1.0:
                _S['summary_at'] = now
                for lbl in sorted(_S['timeline']):
                    tl = _S['timeline'][lbl]
                    visits = [(s, t) for (l, s, t) in _S['visits'] if l == lbl]
                    unreal.log('LANE TIMELINE %s states=%d visits=%d seq=%s'
                               % (lbl, len(tl), len(visits),
                                  ' -> '.join('%s@%.2f' % (s, t) for s, t in visits)))
                    # Dwell is the rebuttal to "that state was a one-sample blip in a blend" — a state
                    # entered AND held for a measurable time is a state the graph really ran.
                    unreal.log('LANE DWELL %s %s'
                               % (lbl, ' | '.join('%s %.2fs(n=%d)' % (s, tl[s][1], tl[s][2])
                                                  for s in sorted(tl))))
    except Exception as e:
        unreal.log_warning('LANE err %s' % e)


_S['handle'] = unreal.register_slate_pre_tick_callback(_tick)
unreal._MT_LANE = _S
unreal.log('LANE driver registered gen=%d (total=%.1fs, airborne %.1f..%.1f)'
           % (GEN, TOTAL, AIRBORNE[0], AIRBORNE[1]))
