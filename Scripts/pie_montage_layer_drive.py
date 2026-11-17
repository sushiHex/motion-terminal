# Layered-montage take driver — the layered attack montage over unbroken locomotion. The pawn holds run speed
# for the whole take while UpperBodyState fires AM_MT_Attack into the 'UpperBody' slot six times,
# and the driver measures whether the locomotion branch is disturbed while the slot is live.
#
# ============================================================================================
# THE MECHANISM THIS SHOT FILMS
#
# ABP_MT_Locomotion's EventBlueprintUpdateAnimation ends with
#
#     (if (== (GetUpperBodyState) 1)
#        (Montage_Play "/Game/MT/AM_MT_Attack.AM_MT_Attack")
#        (SetUpperBodyState 0))
#
# — the montage play and the reset are BOTH the true branch, so writing UpperBodyState=1 fires one
# attack and the variable returns to 0 on the same tick. That is why ATTACKS below is a list of
# one-shot times rather than a sustained hold: a hold would re-enter Montage_Play every tick and
# restart the montage from frame 0 forever, which reads on camera as a twitching arm.
#
# AM_MT_Attack is 1.0 s long with 0.25 s blend in/out and a single slot track on 'UpperBody'.
# The montage-layer splice put `LayeredBlendPerBone(LocomotionPose, Slot 'UpperBody')` into the Template, so the
# locomotion pose is the BASE and only upper-body bones are overlaid.
#
# ---- WHAT THE CAPTION CLAIMS, AND HOW IT IS PROVEN -------------------------------------------
# The caption asserts "locomotion never flinches". That is a claim about the graph, so it is proven
# by reading the graph's own weights, not by watching the frames:
#
#   AnimDebugTools.GetNodeWeights walks UAnimInstance::GatherDebugData and prints every node's
#   AbsoluteWeight. A FULL-BODY montage drives the Locomotion state-machine node's AbsoluteWeight
#   toward 0 (whole-pose takeover); a SLOT montage leaves it at ~1.0 because the locomotion pose is
#   the LayeredBlendPerBone base. So the differentiator is:
#
#     'AnimNode_StateMachine<...> (Locomotion->Locomotion) (weight W)'   W must stay ~1.0
#     "AnimNode_Slot<...> (Slot Name: 'UpperBody' Weight:P%)"            P must rise off 0
#
#   sampled on the SAME dump, i.e. the same evaluated frame — a locomotion weight from one tick and
#   a slot weight from another would not establish that they were ever simultaneously true.
#   This is the same three-mechanism structure as MotionTerminal.Pie.MontageIsolation (PieTests.cpp),
#   run here at 30 fps inside the render that produced the shipped frames, because a figure quoted
#   about this footage has to come from the run that made it.
#
# TWO SAMPLE COUNTS, AND THEY ARE NOT INTERCHANGEABLE. `samples` counts every sample taken inside an
# attack window, blend-in and blend-out tails included. `owned` counts the subset whose slot weight
# reached SLOT_OWNED_MIN (0.5), and OWNED — not samples — is the population the two minima are taken
# over: the worst instant while the montage genuinely owned the slot. On the shipped render that was
# 138 of 288. The minimum is used rather than the mean because "never flinches" is a claim about the
# worst instant and a mean would hide a one-frame collapse inside the clean rest. Quoting `samples`
# as the population behind the minimum overstates it, which an earlier revision of this header (and
# of render_evidence.json) did.
#
# WHAT THIS WEIGHT DOES NOT SHOW — scope limit, so the number is not over-read. LayeredBoneBlend takes
# LocomotionPose as the BASE and 'UpperBody' as the layer, so the base branch's AbsoluteWeight sits at
# 1.000 BY CONSTRUCTION and can only fall if a montage lands on the upstream 'DefaultSlot' instead.
# That makes this a real falsifiable test — it rules out full-body misrouting, which is exactly the
# slot-vs-full-body differentiator — but it is NOT evidence that the layer stayed off the legs. A wrong blend mask would
# leave this weight at 1.000 while the legs visibly broke. The visual half of "never flinches" is
# closed by reading frames (see render_evidence.json montage_layer._visual_confirmation), not here.
#
# The verbatim dump taken at peak slot weight is written to disk and composited into the frames by
# Scripts/terminal_capture.py, which refuses to render anything but a file — see that module's
# header. Nothing in the transcript is authored: it is one '> command' line plus str() of what
# AnimDebugTools returned.
#
# ORDER IS LOAD-BEARING: mode -> velocity -> set_component_tick_enabled(False) LAST, every tick.
# `set_editor_property('velocity', ...)` re-enables the component tick via PostEditChangeProperty;
# disabling first therefore does nothing and PhysFalling drops the pawn back into MOVE_Walking with
# braking friction zeroing the write (an airborne beat was once lost to exactly this).
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

# THE TRANSCRIPT'S FILENAME ONLY — the directory comes from the render that armed it. This line used
# to be a fixed path to this take's shipped evidence (`REPO + '/captures/m9-montage/...'`), the same
# shape that let a stub harness overwrite the gameplay-bridge take's. This take's file survived that run intact; the
# exposure was identical and is closed the same way. See mt_drive's capture-evidence block.
TRANSCRIPT_NAME = 'montage_nodeweights.txt'

SUBJECT = 'MT_Manny'
TOTAL = 18.0              # 540 frames at 30 fps — must match this take's shots_manifest frame_range
RUN_SPEED = 450.0
SPIN_UP = 0.8             # 0 -> RUN_SPEED, so the blendspace enters the run region cleanly

# One-shot attack times, in game seconds. Spaced 2.5 s: the montage is 1.0 s + 0.25 s blend out, so
# every attack fully lands and fully releases with ~1.2 s of clean run between — enough that a
# viewer sees the legs carry on undisturbed, which is the whole point of the shot.
ATTACKS = (2.5, 5.0, 7.5, 10.0, 12.5, 15.0)
ATTACK_WINDOW = 1.6       # sample node weights for this long after each trigger (montage + blend out)

DUMP_AT = 17.2            # transcript flushed before MRQ tears the callback down at 18.0

# Same MEASURED reason as the locomotion-lane and gameplay-bridge takes: the machine-local Khai_* baked locomotion clips carry
# their travel on the PELVIS rather than the root, so at run speed the body sprints out of frame and
# snaps back on each clip loop while every root-based drift check reads 0 (found on an earlier
# locomotion pass and re-confirmed since). This shot holds the Locomotion blendspace state for its whole length, so it is
# maximally exposed. Khaimera is still DRIVEN — only hidden.
HIDE_FROM_CAMERA = ('MT_Khaimera',)

# The cube weapon stand-in, and this is the shot it mattered most in. BP_MT_TestPawn carries a `WeaponMesh`
# holding SM_MT_Rifle (a project-authored duplicate of an engine Cube scaled into a bar); at the peak of
# each attack the punch throws it across the frame right beside the terminal panel, which is the single
# most conspicuous appearance of the stand-in anywhere in the shot sequence. The montage is UNARMED — AM_MT_Attack
# is a bare-hand strike and no caption here mentions a weapon — so the box is hidden per render on the
# live component (the proven pie_roster4_drive pattern). NOT an asset edit: the tracked
# Content/MT/SM_MT_Rifle.uasset is untouched. Swapping in the real Fab rifle was rejected rather than
# deferred: a rifle in a bare-hand punch asserts something the footage does not show.
HIDE_WEAPON_MESH = True

# ---- camera (world space) ----
# Subject framed LEFT (YAW_OFF swings the view right) to clear the terminal panel composited against
# the right edge. Aimed at z=112, a little above the locomotion lane's 105: this shot lives on the shoulders and
# the arc of the punch, but the feet still have to stay in frame or "locomotion never flinches" is
# an assertion the viewer cannot check. Slower, longer push than the gameplay-bridge take over 18 s.
CAM_A = (unreal.Vector(640.0, -560.0, 210.0), unreal.Vector(0.0, -120.0, 112.0))
CAM_B = (unreal.Vector(505.0, -425.0, 178.0), unreal.Vector(0.0, -120.0, 112.0))
CAM_FOCAL = 28.0
YAW_OFF = 11.0

GEN = _mt.retire_and_claim('_MT_MONTAGE_BEAT')

# Strings and floats only — no world, no actor, no component (GOTCHA-A').
_S = {'handle': None, 'focal_set': False, 'fired': set(), 'dumped': False,
      'samples': 0, 'min_sm': 9.9, 'min_sm_at': -1.0, 'min_state': 9.9, 'max_slot': -1.0,
      'max_slot_at': -1.0, 'best_dump': None, 'min_slotapi': 9.9, 'max_slotapi': -1.0,
      'last_log': -999.0, 'unparsed': 0, 'errored': 0, 'owned': 0,
      'slot_disagree': 0, 'slot_delta_max': 0.0, 'slot_api_fail': 0, 'dressed': False}

# Tolerance for "the two slot witnesses agree". The tree prints the slot as a percentage to 1 dp
# (Weight:100.0%), so the parsed value is quantised to 0.001 while the API returns a full float —
# 0.001 is therefore the tightest threshold that is not just measuring that quantisation.
SLOT_AGREE_TOL = 0.001

# Slot weight at or above which the montage counts as genuinely OWNING the slot, and therefore the
# threshold that defines the population the locomotion minima are taken over. Named rather than
# inlined so the header's account of which population that is cannot drift away from the code that
# applies it — which is exactly how the '288 vs 138' error got into the header in the first place.
SLOT_OWNED_MIN = 0.5


def speed_at(t):
    return _mt.lerp(0.0, RUN_SPEED, t / SPIN_UP) if t < SPIN_UP else RUN_SPEED


def in_window(t):
    """True while any attack's sampling window is open."""
    return any(a <= t < a + ATTACK_WINDOW for a in ATTACKS)


def _trailing_weight(line):
    """AbsoluteWeight off a GetNodeWeights line, which ends '... (weight 1.000)'.

    Returns None rather than 0.0 when the suffix is missing or unparseable: a parse failure that
    silently reports 0.0 would make the locomotion-weight minimum collapse to zero and fail this
    shot's own gate for a text-format reason, which is the opposite of the mistake to make. Callers
    count Nones (see _S['unparsed']) so a format change is loud instead of invisible."""
    tail = line.rsplit('(weight', 1)
    if len(tail) != 2:
        return None
    try:
        return float(tail[1].strip().rstrip(')'))
    except ValueError:
        return None


def _slot_pct(line):
    """The slot's own montage weight out of "... (Slot Name: 'UpperBody' Weight:100.0%)", as 0..1.

    NOT the same number as the node's AbsoluteWeight on the same line: the Slot NODE is always
    evaluated (AbsoluteWeight 1.0 whether or not a montage is playing), and it is this inner
    GetSlotMontageLocalWeight figure that rises off 0 when the montage takes the slot."""
    if 'Weight:' not in line:
        return None
    try:
        return float(line.rsplit('Weight:', 1)[1].split('%')[0]) / 100.0
    except (IndexError, ValueError):
        return None


def _measure(now, inst):
    """One paired sample of the locomotion branch and the UpperBody slot, off ONE dump."""
    dump = str(_mt.anim_debug_tools().call_method('GetNodeWeights', args=(SUBJECT,)))
    # AnimDebugTools returns "ERROR: <why>" as a normal FString on every failure path, so an error is
    # a perfectly well-formed string that would parse to no matches, be counted as merely 'unparsed',
    # and — if it ever arrived while best_dump was still None — could reach the burned-in terminal panel as
    # though it were a node tree. Count it as its own thing and never let it become the transcript.
    if dump.startswith('ERROR:'):
        _S['errored'] += 1
        unreal.log_warning('MBEAT t=%.2f GetNodeWeights returned an error: %s' % (now, dump.strip()))
        return
    sm = state = slot = None
    for line in dump.replace('\r', '').split('\n'):
        if sm is None and '(Locomotion->' in line:
            sm = _trailing_weight(line)
        elif state is None and '(State: Locomotion)' in line:
            state = _trailing_weight(line)
        elif slot is None and "'UpperBody'" in line:
            slot = _slot_pct(line)
    if sm is None or slot is None:
        _S['unparsed'] += 1
        unreal.log_warning('MBEAT t=%.2f UNPARSED dump (sm=%s state=%s slot=%s)'
                           % (now, sm, state, slot))
        return

    # Independent second witness for the slot, straight off the AnimInstance rather than out of the
    # printed tree — so a parse bug in _slot_pct cannot by itself manufacture "the slot rose".
    try:
        slot_api = float(inst.call_method('Blueprint_GetSlotMontageLocalWeight', args=('UpperBody',)))
    except Exception:
        slot_api = -1.0

    _S['samples'] += 1
    if slot > _S['max_slot']:
        _S['max_slot'], _S['max_slot_at'] = slot, now
        _S['best_dump'] = dump                       # verbatim, at peak slot weight

    # ---- ACTUALLY COMPARE THE TWO WITNESSES, don't just collect them separately ----------------
    # Two independent readings are only corroboration if something checks that they agree; tracking
    # each one's own min/max and then ASSERTING agreement in prose is a claim nothing computed.
    # A failing API read degrades to -1.0, which must be counted rather than skipped — otherwise
    # PARTIAL failure of the second witness is invisible and the surviving samples still look like
    # a clean two-witness record.
    if slot_api < 0.0:
        _S['slot_api_fail'] += 1
    else:
        _S['max_slotapi'] = max(_S['max_slotapi'], slot_api)
        delta = abs(slot - slot_api)
        _S['slot_delta_max'] = max(_S['slot_delta_max'], delta)
        if delta > SLOT_AGREE_TOL:
            _S['slot_disagree'] += 1
            unreal.log_warning('MBEAT t=%.2f SLOT WITNESSES DISAGREE tree=%.4f api=%.4f delta=%.4f'
                               % (now, slot, slot_api, delta))

    # The claim is about instants when the slot is genuinely carrying the montage, so the locomotion
    # minimum is taken over samples with the slot actually up. Including the blend-in/out tails would
    # let a clean, slot-empty frame supply the minimum and make the gate meaningless.
    if slot >= SLOT_OWNED_MIN:
        _S['owned'] += 1          # the population the minima are actually taken over — report it,
                                  # so 'the minimum over N samples' cannot quote the wrong N
        if sm < _S['min_sm']:
            _S['min_sm'], _S['min_sm_at'] = sm, now
        if state is not None:
            _S['min_state'] = min(_S['min_state'], state)
        if slot_api >= 0.0:
            _S['min_slotapi'] = min(_S['min_slotapi'], slot_api)

    unreal.log('MBEAT t=%.2f loco_sm=%.3f loco_state=%s upper_slot=%.3f slot_api=%.3f'
               % (now, sm, ('%.3f' % state) if state is not None else 'NA', slot, slot_api))


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
        subject_inst = None
        dressed = []
        for a in unreal.GameplayStatics.get_all_actors_of_class(world, unreal.Character):
            mesh = a.get_component_by_class(unreal.SkeletalMeshComponent)
            inst = mesh.get_anim_instance() if mesh else None
            if not inst or 'ABP_MT' not in inst.get_class().get_name():
                continue
            lbl = a.get_actor_label()
            if lbl in HIDE_FROM_CAMERA:
                a.set_actor_hidden_in_game(True)
            # Latched on FIRST SUCCESS, not on first attempt — a rig that streams in a tick late is
            # still dressed, because `dressed` stays empty until some driven pawn has been reached.
            if HIDE_WEAPON_MESH and not _S['dressed']:
                for comp in a.get_components_by_class(unreal.StaticMeshComponent):
                    comp.set_hidden_in_game(True)
                    dressed.append('%s/%s' % (lbl, comp.get_name()))
            if lbl == SUBJECT:
                subject_inst = inst
            cmc = a.get_component_by_class(unreal.CharacterMovementComponent)
            if not cmc:
                continue
            # ORDER IS LOAD-BEARING — mode, velocity, tick-disable LAST, in a try/finally. The
            # measurements behind it are in mt_drive.drive_cmc.
            _mt.drive_cmc(cmc, spd)

        if dressed:
            _S['dressed'] = True
            unreal.log('MBEAT dressed hidden_static_meshes=%s' % dressed)

        # Fire each attack exactly once. Keyed on index, not on the time value, so two attacks that
        # ever share a time cannot collapse into one trigger.
        for i, t in enumerate(ATTACKS):
            if i not in _S['fired'] and now >= t:
                _S['fired'].add(i)
                # The literal '1' is preserved rather than routed through set_anim_vars_checked,
                # which would send '1.0' - UpperBodyState's property type is not float on every rig
                # and this write is the trigger the whole beat depends on. Checked in place instead.
                _r = str(_mt.anim_debug_tools().call_method(
                    'SetAnimVariable', args=(SUBJECT, 'UpperBodyState', '1')))
                unreal.log('MBEAT ATTACK %d @ t=%.2f -> %s' % (i, now, _r))
                if not _r.startswith('ok:'):
                    unreal.log_error('MBEAT attack %d FAILED TO FIRE (%s) - no montage plays, so the '
                                     'isolation window that follows measures an undisturbed graph and '
                                     'proves nothing about layering' % (i, _r))

        if subject_inst is not None and in_window(now):
            _measure(now, subject_inst)

        if not _S['dumped'] and now >= DUMP_AT:
            _S['dumped'] = True
            if _S['best_dump'] is None:
                # Never write a transcript that is not a real dump. A missing dump means the montage
                # never reached the slot, which is a failed take, not a formatting problem.
                unreal.log_warning('MBEAT NO DUMP — the UpperBody slot never carried a montage; '
                                   'this take does not show what the caption claims')
            else:
                body = _S['best_dump'].rstrip('\n').replace('\r', '')
                p = _mt.write_capture_evidence(
                    TRANSCRIPT_NAME,
                    "> AnimDebugTools.GetNodeWeights('%s')\n%s\n" % (SUBJECT, body))
                unreal.log('MBEAT TRANSCRIPT -> %s (peak slot %.3f at t=%.2f)'
                           % (p, _S['max_slot'], _S['max_slot_at']))
            # Everything the evidence entry is allowed to say has to appear on this line. `owned` is
            # the population the minima are over (NOT `samples`); `slot_disagree`/`slot_delta_max`
            # are what make "two independent witnesses agreed" a measurement instead of an assertion;
            # `slot_api_fail`/`errored` are what stop a partly-broken run from reading like a clean one.
            unreal.log('MBEAT RESULT attacks=%d/%d samples=%d owned=%d unparsed=%d errored=%d '
                       'loco_sm_min=%.4f@%.2f loco_state_min=%.4f upper_slot_max=%.4f@%.2f '
                       'slot_api_max=%.4f slot_api_min_when_owned=%.4f '
                       'slot_disagree=%d slot_delta_max=%.5f slot_api_fail=%d'
                       % (len(_S['fired']), len(ATTACKS), _S['samples'], _S['owned'],
                          _S['unparsed'], _S['errored'],
                          _S['min_sm'], _S['min_sm_at'], _S['min_state'],
                          _S['max_slot'], _S['max_slot_at'],
                          _S['max_slotapi'], _S['min_slotapi'],
                          _S['slot_disagree'], _S['slot_delta_max'], _S['slot_api_fail']))

        if (now - _S['last_log']) >= 1.0:
            _S['last_log'] = now
            unreal.log('MBEAT t=%.2f spd_set=%.0f attacks=%d/%d samples=%d'
                       % (now, spd, len(_S['fired']), len(ATTACKS), _S['samples']))
    except Exception as e:
        unreal.log_warning('MBEAT err %s' % e)


_S['handle'] = unreal.register_slate_pre_tick_callback(_tick)
unreal._MT_MONTAGE_BEAT = _S
unreal.log('MBEAT driver registered gen=%d (total=%.1fs, attacks at %s)'
           % (GEN, TOTAL, ', '.join('%.1f' % t for t in ATTACKS)))
