# Gameplay-bridge take driver — the gameplay bridge. The pawn stands in Idle, gameplay writes the movement
# source, the three locomotion contract variables follow it, and the pawn breaks into a run. The
# driver records the whole exchange as a terminal transcript and writes it to disk DURING the render.
#
# ============================================================================================
# WHY THE TRANSCRIPT IS BUILT HERE AND NOT TYPED SOMEWHERE
#
# Scripts/terminal_capture.py composites a terminal panel over these frames, and it refuses to
# render anything but a file. This driver writes that file. Every line in it is one of exactly two
# things:
#
#   '> <command>'   — the literal call this driver issues, formatted as it is issued, and
#   <return value>  — str() of what AnimDebugTools handed back, split on newlines, unedited.
#
# EVERY VALUE LINE comes from a tool return — there is no branch that can print a reading the tool
# did not produce. That matters because the terminal panel is the one place in this shot sequence
# where a plausible-looking number ('Speed = 450.000000') could be fabricated and nobody watching could
# tell from the pixels. Writing it from inside the MRQ render means the transcript and the footage are the
# same run.
#
# THAT LAST SENTENCE WAS AN ASSERTION AND IS NOW A MECHANISM. It used to rest
# on a fixed path constant, so it held only for as long as nothing else ran this file — and something
# did. The transcript now goes through `mt_drive.write_capture_evidence`, which writes into the
# directory the RENDER armed and raises if no render armed one. "The transcript and the footage are
# the same run" is therefore true by construction: they land in the same directory, they are cleared
# together by the next render, and a run that is not a render cannot produce either.
#
# ---- EXACTLY ONE LINE IN THE TERMINAL PANEL IS AUTHORED, AND IT IS THE GAMEPLAY-SIDE WRITE ---------------
# `_do('drive')` prints `cmc.set_movement_mode(MOVE_None); cmc.velocity = (450, 0, 0)`. That line is
# a LEGEND for what _tick does to the CMC, not a transcript of a call, and it is inexact three ways:
#
#   (a) it is printed once, at RUN_AT, but `speed_at(RUN_AT)` is still 0.0 — the ramp needs RUN_RAMP
#       (0.35 s) to reach 450, so the vector written on that very frame is (0,0,0), not (450,0,0);
#   (b) the write is not a one-shot at RUN_AT at all: _tick performs it EVERY tick from t=0, with
#       speed_at(now) as the magnitude (0.0 for the whole idle phase);
#   (c) it names no actor, but _tick applies it to every driven ABP_MT character, not only SUBJECT.
#
# The causal story the line stands for is true — the run does begin at RUN_AT because that is when
# speed_at() leaves 0 — and the genuine read-back two lines later (`Speed = 450.000000`, sampled at
# t=3.60 off the live AnimInstance) is the tool-produced evidence for it. But this line is prose,
# it is the only line in the terminal panel a viewer cannot check against a tool return, and it must
# be read as prose. Anyone re-cutting this shot who wants the terminal panel fully mechanical should print
# speed_at(now) rather than RUN_SPEED, and print it every time the vector changes.
#
# WHY THE DRIVER WRITES NO CONTRACT VARIABLE FOR THE RUN. All three locomotion contract vars are
# DERIVED every tick by ABP_MT_Locomotion's EventBlueprintUpdateAnimation:
#
#     Speed        = VectorLength(GetVelocity(TryGetPawnOwner))
#     IsFalling    = CastToCharacter(pawn).GetCharacterMovement().IsFalling()
#     GroundedFlag = select(IsFalling, 0, 1)
#
# so SetAnimVariable('Speed', 450) returns `ok`, reads back as 450.0, and is overwritten before the
# state machine evaluates. The driver therefore writes the SOURCE —
# CharacterMovementComponent.Velocity — and the transcript shows the three vars picking it up. The
# one variable the bridge genuinely WRITES is UpperBodyState (an int the EventGraph consumes rather
# than derives), and the transcript ends on that call firing the attack montage — which is also the
# hand-off into the layered-montage take.
#
# ORDER IS LOAD-BEARING: mode -> velocity -> set_component_tick_enabled(False) LAST, every tick.
# `set_editor_property('velocity', ...)` routes through PostEditChangeProperty, re-registers the
# component and RE-ENABLES its tick; disabling first therefore does nothing and PhysFalling puts the
# pawn back in MOVE_Walking with braking friction zeroing the write. Getting this wrong once cost
# an entire airborne beat that logged green. The try/finally makes the invariant
# structural rather than dependent on the two calls staying adjacent.
#
# GOTCHA-A': no World/Actor/Component ever binds to a module-level name — every handle is a local
# inside _tick. Retires off unreal._MT_DRIVE_GEN, which mrq_render bumps when the render ends.
# ============================================================================================
import importlib
import os
import sys

import unreal

# The shared driver scaffolding (mt_drive.py). Resolved off the .uproject, NOT off `__file__`:
# `mrq_render.render_shot` execs this file into a bare namespace where `__file__` does not exist.
# Reloaded rather than plain-imported so an edit to mt_drive.py takes effect on the next render —
# see that module's header.
_MT_SCRIPTS = os.path.join(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()),
                           'Scripts')
if _MT_SCRIPTS not in sys.path:
    sys.path.insert(0, _MT_SCRIPTS)
_mt = importlib.reload(importlib.import_module('mt_drive'))

# THE TRANSCRIPT'S FILENAME ONLY. This driver does not know, and must not know, which directory it
# lands in — `mt_drive.write_capture_evidence` puts it inside the directory the RENDER armed, and
# raises if no render armed one. This line used to read
#
#     TRANSCRIPT = REPO + '/captures/m9-bridge/bridge_output.txt'
#
# a FIXED path to a shipped shot's evidence that any run of this file would overwrite. The driver
# equivalence harness exec'd this driver against a stubbed `unreal` for 60 simulated ticks, reached
# DUMP_AT, and wrote that path with the stub's canned returns — `IsFalling = 450.000000`, seven hours
# after the frames it backs were rendered. The fix is not a check; it is that there is no longer a
# path here to clobber. See mt_drive's capture-evidence block for the whole account.
TRANSCRIPT_NAME = 'bridge_output.txt'

SUBJECT = 'MT_Manny'
TOTAL = 8.0               # 240 frames at 30 fps — must match this take's shots_manifest frame_range
RUN_AT = 3.0              # gameplay writes the movement source here; the run starts on this frame
RUN_RAMP = 0.35           # short, so the break into a run reads as a single beat, not a fade
RUN_SPEED = 450.0         # the BS_Idle_Walk_Run run region (same figure the locomotion lane runs at)

# The exchange, in game seconds. Each entry fires exactly once, on the first tick at/after its time.
#   ('get', var)          -> AnimDebugTools.GetAnimVariable(SUBJECT, var)
#   ('drive', None)       -> records the gameplay-side write; the velocity ramp is keyed off RUN_AT
#   ('state', None)       -> AnimDebugTools.GetAnimDebugState(SUBJECT)
#   ('set', (var, value)) -> AnimDebugTools.SetAnimVariable(SUBJECT, var, value)
SCHEDULE = [
    (0.60, ('get', 'Speed')),
    (1.20, ('get', 'IsFalling')),
    (1.80, ('get', 'GroundedFlag')),
    (RUN_AT, ('drive', None)),
    (3.60, ('get', 'Speed')),
    (4.10, ('get', 'IsFalling')),
    (4.60, ('get', 'GroundedFlag')),
    (5.20, ('state', None)),
    (6.60, ('set', ('UpperBodyState', '1'))),
]
DUMP_AT = 7.40            # transcript flushed well before MRQ tears the callback down at 8.0

# Khaimera is hidden for the same MEASURED reason as the locomotion-lane take: his machine-local Khai_* baked
# locomotion clips carry their travel on the PELVIS, not the root, so at run speed the body sprints
# ~12 m out of frame and snaps back on each clip loop while every root-based check reads 0 drift
# (found on an earlier locomotion pass and re-confirmed since). This shot enters the Locomotion blendspace state,
# so it is exposed to that defect exactly as the locomotion-lane take was. He is still DRIVEN — only hidden.
HIDE_FROM_CAMERA = ('MT_Khaimera',)

# The cube weapon stand-in. BP_MT_TestPawn carries a `WeaponMesh` holding SM_MT_Rifle (a project-authored
# duplicate of an engine Cube scaled into a bar). This shot is the UNARMED gameplay bridge — the terminal
# panel is about three contract variables and nothing in it mentions a weapon — so the box is hidden per
# render on the live component (the proven pie_roster4_drive pattern). NOT an asset edit: the tracked
# Content/MT/SM_MT_Rifle.uasset is untouched. Swapping in the real Fab rifle was rejected rather than
# deferred: an armed pawn in an unarmed beat asserts something the footage does not show.
HIDE_WEAPON_MESH = True

# ---- camera (world space) ----
# Subject deliberately framed LEFT: terminal_capture composites an 820px panel against the right
# edge, and a centred subject would sit underneath it. YAW_OFF swings the view right, which pushes
# the subject left in frame; it is applied to the look-at rotation rather than by offsetting the aim
# point so the distance (and therefore the subject's size) is unaffected by the re-frame.
CAM_A = (unreal.Vector(620.0, -540.0, 205.0), unreal.Vector(0.0, -120.0, 100.0))
CAM_B = (unreal.Vector(530.0, -450.0, 180.0), unreal.Vector(0.0, -120.0, 100.0))
CAM_FOCAL = 28.0
YAW_OFF = 11.0

GEN = _mt.retire_and_claim('_MT_BRIDGE')

# Strings and floats only — no world, no actor, no component (GOTCHA-A').
_S = {'handle': None, 'focal_set': False, 'fired': set(), 'lines': [],
      'dumped': False, 'last_log': -999.0, 'errored': [], 'dressed': False}


def speed_at(t):
    if t < RUN_AT:
        return 0.0
    return _mt.lerp(0.0, RUN_SPEED, (t - RUN_AT) / RUN_RAMP)


def _rec(cmd, result=None):
    """Append one exchange to the transcript: the command as issued, then the tool's verbatim return.

    `result=None` means the command produced no return worth printing (the gameplay-side write) —
    it is NOT a placeholder for a value that was expected and did not arrive, which is why the
    caller passes it explicitly rather than this function inventing a default line."""
    # AnimDebugTools signals every failure by RETURNING the string "ERROR: <why>" — it does not
    # raise — so a failed call is indistinguishable from a successful one to everything downstream
    # and would be composited into the terminal panel as calmly as a real reading. `missed` (below) only
    # catches entries that never fired, not entries that fired and errored, so the error has to be
    # caught here, at the only point where the return value is still in hand.
    if result is not None and str(result).lstrip().startswith('ERROR:'):
        _S['errored'].append((cmd, str(result).strip()))
        unreal.log_warning('BRIDGE ERROR RETURN from %s -> %s' % (cmd, str(result).strip()))

    _S['lines'].append('> ' + cmd)
    if result is not None:
        for ln in str(result).rstrip('\n').replace('\r', '').split('\n'):
            _S['lines'].append(ln)
    unreal.log('BRIDGE > %s' % cmd)
    if result is not None:
        for ln in str(result).rstrip('\n').replace('\r', '').split('\n'):
            unreal.log('BRIDGE   %s' % ln)


def _do(kind, arg):
    if kind == 'get':
        _rec("AnimDebugTools.GetAnimVariable('%s', '%s')" % (SUBJECT, arg),
             _mt.anim_debug_tools().call_method('GetAnimVariable', args=(SUBJECT, arg)))
    elif kind == 'state':
        _rec("AnimDebugTools.GetAnimDebugState('%s')" % SUBJECT,
             _mt.anim_debug_tools().call_method('GetAnimDebugState', args=(SUBJECT,)))
    elif kind == 'set':
        _rec("AnimDebugTools.SetAnimVariable('%s', '%s', '%s')" % (SUBJECT, arg[0], arg[1]),
             _mt.anim_debug_tools().call_method('SetAnimVariable', args=(SUBJECT, arg[0], arg[1])))
    elif kind == 'drive':
        # THE ONE AUTHORED PANEL LINE. A legend for the gameplay-side write, not a transcript of a
        # call: it is printed at RUN_AT where speed_at() is still 0.0, the write actually runs every
        # tick from t=0, and it runs for every driven character. See the header block for why that is
        # tolerated here and what to change if the terminal panel must be fully mechanical.
        _rec('cmc.set_movement_mode(MOVE_None); cmc.velocity = (%d, 0, 0)' % int(RUN_SPEED))


def _drive_camera(world, now):
    # Focal written ONCE, latched on the first tick that actually finds a camera (the return value),
    # not on the first attempt — the MRQ PIE world can spawn the sequence's camera a tick late.
    cam = _mt.drive_ab_camera(world, now / TOTAL, CAM_A, CAM_B,
                              focal=None if _S['focal_set'] else CAM_FOCAL, yaw_off=YAW_OFF)
    if cam is not None:
        _S['focal_set'] = True


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
        dressed = []
        for a in unreal.GameplayStatics.get_all_actors_of_class(world, unreal.Character):
            mesh = a.get_component_by_class(unreal.SkeletalMeshComponent)
            inst = mesh.get_anim_instance() if mesh else None
            if not inst or 'ABP_MT' not in inst.get_class().get_name():
                continue
            # Re-asserted every tick rather than latched, so a rig the render world re-shows
            # cannot leak into frame.
            if a.get_actor_label() in HIDE_FROM_CAMERA:
                a.set_actor_hidden_in_game(True)
            # Latched on FIRST SUCCESS, not on first attempt — a rig that streams in a tick late is
            # still dressed, because `dressed` stays empty until some driven pawn has been reached.
            if HIDE_WEAPON_MESH and not _S['dressed']:
                for comp in a.get_components_by_class(unreal.StaticMeshComponent):
                    comp.set_hidden_in_game(True)
                    dressed.append('%s/%s' % (a.get_actor_label(), comp.get_name()))
            cmc = a.get_component_by_class(unreal.CharacterMovementComponent)
            if not cmc:
                continue
            # ORDER IS LOAD-BEARING — mode, velocity, tick-disable LAST, in a try/finally. The
            # measurements behind it are in mt_drive.drive_cmc.
            _mt.drive_cmc(cmc, spd)

        if dressed:
            _S['dressed'] = True
            unreal.log('BRIDGE dressed hidden_static_meshes=%s' % dressed)

        # The scheduled exchange. Sorted, fire-once, keyed on index so a repeated (t, kind) pair
        # cannot silently collapse into a single entry.
        for i, (t, (kind, arg)) in enumerate(SCHEDULE):
            if i not in _S['fired'] and now >= t:
                _S['fired'].add(i)
                _do(kind, arg)

        if not _S['dumped'] and now >= DUMP_AT:
            _S['dumped'] = True
            missed = [i for i in range(len(SCHEDULE)) if i not in _S['fired']]
            if missed:
                # Never write a short transcript silently: a terminal panel is 240 frames of confident-looking
                # evidence, and half an exchange looks exactly like a whole one.
                unreal.log_warning('BRIDGE INCOMPLETE — schedule entries never fired: %s' % missed)

            # An errored call must not become footage. Writing the transcript to a .REJECTED sibling
            # and REMOVING the real path is the only outcome that fails loudly all the way through:
            # terminal_capture refuses a missing file (FileNotFoundError naming it), so the composite
            # step stops instead of quietly burning "ERROR: no property 'Speed'" into 240 frames —
            # and leaving a stale transcript in place would be worse than either, because it would
            # composite cleanly and look right. The rejected text stays on disk for diagnosis.
            body = '\n'.join(_S['lines']) + '\n'
            if _S['errored']:
                unreal.log_warning('BRIDGE REJECTED — %d call(s) returned ERROR: %s'
                                   % (len(_S['errored']), _S['errored']))
                p = _mt.write_capture_evidence(TRANSCRIPT_NAME + '.REJECTED', body)
                _mt.remove_capture_evidence(TRANSCRIPT_NAME)
                unreal.log_warning('BRIDGE transcript withheld -> %s' % p)
            else:
                p = _mt.write_capture_evidence(TRANSCRIPT_NAME, body)
                unreal.log('BRIDGE TRANSCRIPT %d lines -> %s (missed=%s)'
                           % (len(_S['lines']), p, missed))

        if (now - _S['last_log']) >= 0.5:
            _S['last_log'] = now
            unreal.log('BRIDGE t=%.2f spd_set=%.0f fired=%d/%d'
                       % (now, spd, len(_S['fired']), len(SCHEDULE)))
    except Exception as e:
        unreal.log_warning('BRIDGE err %s' % e)


_S['handle'] = unreal.register_slate_pre_tick_callback(_tick)
unreal._MT_BRIDGE = _S
unreal.log('BRIDGE driver registered gen=%d (total=%.1fs, run@%.1fs)' % (GEN, TOTAL, RUN_AT))
