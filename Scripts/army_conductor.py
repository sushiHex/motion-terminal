# The conductor: choreography driver for the spawned army.
#
# Runs inside the editor over `run_python`; the caller PREPENDS an action and exec()s this file:
#     CONDUCTOR_ACTION = 'start'    # 'start' | 'stop' | 'sample' | 'status' | 'sweep'
#     CONDUCTOR_ARGS   = None       # or {'k': 12} for 'sample'
#     exec(open(r'.../Scripts/army_conductor.py').read())
# Every action prints one `COND <json>` line to LogPython so the caller can parse it, and an unrecognised
# action raises rather than doing nothing quietly. 'sweep' is assert_no_uobject_globals() in report mode —
# run it before any map change (GOTCHA-A').
#
# Both names are `globals().pop`-ed (same reason as Scripts/army_spawn.py): run_python execs into a
# PERSISTENT namespace, so a `get` would silently inherit the previous call's action. An unset name reads
# None, never stale. Set BOTH on every call. Only 'start' registers a callback or bumps the run id — the
# other actions re-exec this file to OBSERVE a live conductor and must not disturb it.
#
# GOTCHA-A' (project rule): nothing here binds a World, Level or actor to a module-level global. The roster
# cached in _CONDUCTOR_ROSTER is PLAIN DATA ONLY (str/float/int/bool) — labels and positions, never UObjects.
# Every actor handle is a local that dies with the call that fetched it. Discard spawns with despawn_army().
#
# ============================================================================================
# FINDING THAT SHAPES THIS WHOLE FILE: `Speed` IS NOT AN INJECTABLE CONTRACT VAR.
# ============================================================================================
# The obvious design drives all five contract vars through AnimDebugTools.SetAnimVariable. That is
# correct for four of them and WRONG FOR `Speed`, and the failure is silent in exactly the way this project
# keeps getting bitten by. ABP_MT_Locomotion's EventBlueprintUpdateAnimation DERIVES Speed every tick:
#
#     Speed = VectorLength(GetVelocity(TryGetPawnOwner))          (research/fixtures/abp-mt-locomotion-readback.txt)
#
# `register_slate_pre_tick_callback` fires AFTER the world tick (FSlateApplication::Tick runs after
# GEngine->Tick), so a reflection write lands after that frame's ABP update and is overwritten at the start
# of the NEXT one — before the state machine evaluates its transitions. MEASURED LIVE, both halves:
#
#     SetAnimVariable('Army_hero_000','Speed','400')  ->  GetAnimVariable ... "Speed = 400.000000"   (!)
#                                                     ->  machine 'Locomotion': state 'Idle' weight 1.000
#
# The var READS BACK AT 400 while the graph is in Idle. A conductor that logged the write's "ok:" return, or
# read the var back, would report a fully driven army that was standing still — the same shape of vacuous
# green as the AbsoluteWeight trap below. `Speed > 10` never fired because the state machine never saw 400.
#
# THE MECHANISM THAT WORKS — drive the SOURCE the ABP already reads, not the derived copy:
#     cmc.movement_mode = MOVE_NONE ; cmc.set_component_tick_enabled(False) ; cmc.Velocity = <wave>
# With the movement component not ticking, nothing damps or re-integrates Velocity, so GetVelocity() still
# returns our value when BlueprintUpdateAnimation runs, and the actor does NOT translate — the formation
# holds while the legs run. MEASURED LIVE: velocity 450 -> "Speed = 450.000000", state 'Locomotion'
# weight 1.000, actor location unchanged to 0.1 cm over the window. This is in-place locomotion, which is
# what a formation take wants anyway; it is also strictly more honest than a reflection stomp, because
# Speed, the Idle<->Locomotion transitions and the BlendSpace X pin all keep reading ONE source of truth.
#
# The BlendSpace really does respond (the premise held, it just needed the right injection point). Foot/hand
# excursion in COMPONENT space, min/max over a window, Manny hero instances, IK on:
#     Speed=120  foot_r z-range  7.41 cm   hand_r x-range  6.36 cm
#     Speed=590  foot_r z-range 48.42 cm   hand_r x-range 47.82 cm      (~6.5x / ~7.5x)
# That ratio is the pose-level proof that the speed wave reaches the pose, not just the float.
#
# The OTHER four vars (TurnYawDelta, AimYaw, AimPitch, WeaponIKAlpha) are pure inputs — nothing in the
# EventGraph writes them — so they go through SetAnimVariable exactly as the AnimDebugTools contract specifies.
#
# ============================================================================================
# MONTAGE VERIFICATION: AbsoluteWeight IS VACUOUS. Reproduced here before trusting anything.
# ============================================================================================
# GetNodeWeights prints "<DebugLine> (weight %.3f)" where the trailing number is FNodeDebugData's
# AbsoluteWeight — the INCOMING CHAIN weight. Live dump, one instance playing a montage and one not:
#
#   Army_hero_002  AnimNode_Slot<W:100.0%> (Slot Name: 'DefaultSlot' Weight:100.0%) (weight 1.000)
#                    Montage('AnimMontage_1') Anim('MT_GAS_Gesture_A') P(1.50) W(100%) (weight 1.000)
#   Army_hero_003  AnimNode_Slot<W:100.0%> (Slot Name: 'DefaultSlot' Weight:0.0%)   (weight 1.000)
#
# `(weight 1.000)` on BOTH. The trailing AbsoluteWeight cannot tell a montage from no montage at all.
# The two signals that CAN, and that this file uses (it asserts BOTH, and reports them separately):
#   (a) `Weight:X%` parsed out of the slot node's own debug text — 100.0% vs 0.0%;
#   (b) the `Montage('...') Anim('...')` CHILD ITEM, which FAnimNode_Slot::GatherDebugData emits only when a
#       montage is genuinely valid for that slot — present vs absent.
# Child items are attributed to their owning slot BY INDENT — the subtree of a slot line runs until the next
# line at an indent <= that slot's, and a Montage(...) deeper than that is the slot's own.
#
# THE INDENT RELATIONSHIP IS DYNAMIC, NOT A PROPERTY OF THIS GRAPH. FAnimNode_Base::GatherDebugData indents a
# child chain only when the node BRANCHES (AnimNodeBase.cpp:553-554):
#     bool bMultiBranch = Item.ChildNodeChain.Num() > 1;
#     int32 ChildIndent = bMultiBranch ? Indent + 1 : Indent;
# A slot with NO montage has one child, so its whole subtree emits at the SAME indent as the slot line; a slot
# WITH a montage has two, so the subtree AND the montage line both shift to Indent+1. So "UpperBody sits
# deeper than DefaultSlot" is produced by whether DefaultSlot currently holds a montage — it is not a fixed
# shape (research/fixtures/pie-introspection-idle.txt:18-27 shows the flattened, montage-free tree). Closing
# the subtree on slot lines ALONE therefore leaks: once DefaultSlot is open, any deeper Montage(...) is
# credited to it, including one under UpperBody and including cache-pose subtrees, which AnimNodeBase.cpp:568-575
# RE-EMITS from the root at indent 0. The close condition below is any line at indent <= the slot's.
#
# ============================================================================================
# ONE MONTAGE CHANNEL PER INSTANCE (hard constraint) — enforced structurally.
# ============================================================================================
# 'DefaultSlot' and 'UpperBody' both resolve to DefaultGroup, and Montage_PlayInternal calls
# StopAllMontagesByGroupName, so the interference is BIDIRECTIONAL: the Template's EventGraph auto-fires
# AM_MT_Attack whenever UpperBodyState is set to 1, which would cut a full-body DefaultSlot montage mid-blend.
# So the formation is partitioned by angular SECTOR into two sets that cannot overlap by construction:
#     MONTAGE_SECTORS   -> receive dynamic montages, and UpperBodyState is NEVER written on them
#     UPPERBODY_SECTORS -> receive UpperBodyState, and never receive a montage
# _partition() builds the two sets from disjoint constant sector tuples and each roster entry has exactly one
# sector, so the disjointness is true BY CONSTRUCTION and the raise below cannot fire on a well-formed roster.
# What the raise actually detects is a DUPLICATE ACTOR LABEL (two instances sharing a label, one per set) —
# worth keeping, but do not sell it as the thing that makes the partition safe. (Locomotion is a graph path,
# not a montage, so the speed/turn/aim waves run on every instance in both sets without touching the montage
# channel.)
#
# SKELETON GATE: the MT_GAS clips are on SK_Mannequin's skeleton. Khaimera instances (1 in 3 of the hero
# ring) cannot play them, so they are excluded from the montage set at roster-build time and recorded in
# `montage_excluded` rather than being fired at and silently returning NULL. Excluded instances belong to
# NEITHER set — they run locomotion only, and double as extra montage negative controls.
#
# ============================================================================================
# NO SEAMLESSNESS MEASUREMENT HERE — deliberately. (Scripts/archive/pie_montage_drive.py header.)
# ============================================================================================
# A pop is a DISPLACEMENT, not a velocity, so a dt-normalized cm/s pop detector discriminates as
# 1 + D/(v*dt): ~24x at a pinned 1/60, but only ~2.3x at the long frames a 46-instance crowd map produces.
# Normalizing harder is not the remedy. This conductor therefore probes LIVENESS ONLY — slot reachable,
# montage created, slot weight ramped, vars driven — none of which depends on dt. The seamlessness claim
# belongs to the committed C++ assertion MotionTerminal.Pie.MontageSeamless, which pins the clock.
#
# ============================================================================================
# EVERY set_editor_property ON A LIVE COMPONENT PASSES notify_mode=NEVER. NOT A MICRO-OPTIMISATION.
# (The three writes in _blend_settings are exempt: they build standalone UStructs with no owner object, so
# PyWrapperOwnerContext has nothing to notify.)
# ============================================================================================
# Python's set_editor_property defaults to EPropertyAccessChangeNotifyMode::Default
# (PyWrapperObject.cpp:905,1133 — "ensuring that the pre/post change notifications are called"), and
# PropertyAccessUtil.cpp:753-798 emits the pre/post change notify whenever the value actually DIFFERS. The
# velocity wave changes every pass, so it fired every pass. On a REGISTERED component that costs, per write:
#   * ActorComponent.cpp:1311-1339 — PreEditChange builds an FComponentReregisterContext (unregisters the CMC)
#     and calls FlushRenderingCommands() (GFlushRenderingCommandsOnPreEditChange = true);
#   * ActorComponent.cpp:1432-1446 — ConsolidatedPostEditChange re-registers and, since the owning actor is not a
#     template and ChangeType == ValueSet, calls MyOwner->RerunConstructionScripts(). ActorConstruction.cpp:254-294
#     does NOT gate that on world type and GIsEditor is true in PIE, so it runs in PIE too.
# i.e. 46 x (render flush + component re-register + full Blueprint actor reconstruction) EVERY drive pass.
# RerunConstructionScripts also destroys and recreates UCS-created components — the Template pawn attaches
# SM_MT_Rifle from its UserConstructionScript, so the weapon was being torn down and rebuilt under the hand-IK
# beat that reads its socket. NEVER short-circuits at PyWrapperOwnerContext.cpp:58 before any notify object is
# built. Measured effect on this army: see the frame-rate note in the tick handler below.
import json
import math
import re
import traceback

import unreal

TAU = 2.0 * math.pi

TAG_ALL = 'MT_Army'
TAG_PREFIX = 'MT_Tier_'
SLOT = 'DefaultSlot'

# See the notify-mode block above. Bound once so no call site can forget it.
NO_NOTIFY = unreal.PropertyAccessChangeNotifyMode.NEVER
CLIP_ROOT = '/Game/MT/GAS_Clips/'
CLIP_JUMP = CLIP_ROOT + 'MT_GAS_Jump_Land.MT_GAS_Jump_Land'
CLIP_GEST_A = CLIP_ROOT + 'MT_GAS_Gesture_A.MT_GAS_Gesture_A'
CLIP_GEST_B = CLIP_ROOT + 'MT_GAS_Gesture_B.MT_GAS_Gesture_B'

# ---- formation partition ----
N_SECTORS = 6
MONTAGE_SECTORS = (0, 1, 2, 3)
UPPERBODY_SECTORS = (4, 5)

# ---- wave shape ----
# The sweep direction across the formation. The spatial phase is the projection of an instance's position
# onto this axis divided by a wavelength, so the wave is a plane wave crossing the rings rather than a
# per-ring step — instances at the same radius but different angles get DIFFERENT speeds, which is what
# makes the gradient a gradient and not three flat tiers.
WAVE_ANGLE = math.radians(35.0)
SPEED_WAVELENGTH = 2600.0
SPEED_PERIOD = 7.0
SPEED_MIN = 0.0
SPEED_MAX = 600.0        # BS_Idle_Walk_Run's Speed axis is 0..600 (measured); 600 is the sprint corner.
SETTLE = 2.0             # ramp the whole wave in, so t=0 is not a step input on 46 instances at once

TURN_AT = 2.0
TURN_WAVELENGTH = 1900.0
TURN_PERIOD = 5.0
TURN_AMP = 110.0         # DEGREES. >+45 -> TIP_TurnRight, <-45 -> TIP_TurnLeft, |x|<10 -> TIP_Neutral.

AIM_AT = 3.0
AIM_RADIUS = 1500.0
AIM_PERIOD = 9.0
AIM_Z_BASE = 300.0
AIM_Z_AMP = 900.0
AIM_Z_PERIOD = 4.0
AIM_EYE_Z = 150.0        # approx eye height above the instance's feet
AIM_PITCH_FULLSCALE = math.radians(45.0)   # AimPitch is NORMALIZED -1..1; 45 deg maps to full deflection

IK_AT = 6.0
IK_RAMP = 2.0

# ---- montage sector schedule: (t, sectors, clip, blend_in, blend_out, rate, loops, inertial) ----
# The jump ripple walks the sectors 0.35 s apart so the crowd reads as a wave, not a unison hit.
# The gesture chain fires B over A on the SAME instances with inertial blends — that is the chained,
# distinct-clip case the take needs, and the case the DefaultSlot+Inertialization splice exists for.
SCHEDULE = (
    (10.00, (0,), CLIP_JUMP, 0.15, 0.25, 1.0, 1, False),
    (10.35, (1,), CLIP_JUMP, 0.15, 0.25, 1.0, 1, False),
    (10.70, (2,), CLIP_JUMP, 0.15, 0.25, 1.0, 1, False),
    (11.05, (3,), CLIP_JUMP, 0.15, 0.25, 1.0, 1, False),
    (14.00, (0, 1), CLIP_GEST_A, 0.25, 0.25, 1.0, 6, True),
    (15.50, (2, 3), CLIP_GEST_A, 0.25, 0.25, 1.0, 6, True),
    (18.00, (0, 1, 2, 3), CLIP_GEST_B, 0.25, 0.25, 1.0, 6, True),
)

UB_ON_AT = 12.0
UB_OFF_AT = 21.0

# Auto-probes: captured BY THE CONDUCTOR, because racing a run_python `sample` call against a 0.35 s ripple
# loses. Each capture records the discriminating montage signal on one positive and one negative instance
# plus the whole-formation speed gradient. PROBE_AT are fixed baselines (9.5 is BEFORE any montage fires, so
# it is the whole-formation negative control); every sector step additionally schedules its own probe
# PROBE_LAG later against a label that step actually fired on.
PROBE_AT = (9.50, 23.00)
PROBE_LAG = 0.50         # > the longest blend_in (0.25) so the ramp has completed when the probe lands

SECTOR_RETRIES = 3       # attempts per schedule step before it is abandoned (see the fire block in _tick)

TOTAL = 24.0
DRIVE_HZ = 12.0          # 46 instances x 3 SetAnimVariable + 1 velocity write per pass; each SetAnimVariable
                         # walks the world's actors, so the pass is capped rather than run every frame.
                         # WHETHER IT ENGAGES DEPENDS ON THE FRAME RATE: below 12 fps the throttle is a no-op
                         # and every tick drives (that was the whole pre-notify-fix run). Under MRQ's fixed
                         # 1/60 timestep it DOES engage, so a filmed run drives at a different cadence than a
                         # PIE run — a filmed take must not assume the two are the same.

_SLOT_RE = re.compile(r"Slot Name: '([^']+)' Weight:([0-9.]+)%")
_MONT_RE = re.compile(r"Montage\('([^']*)'\) Anim\('([^']*)'\)")
_ABSW_RE = re.compile(r"\(weight ([0-9.]+)\)\s*$")


def game_world():
    return unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world()


def _tools():
    return unreal.get_default_object(unreal.load_class(None, '/Script/MotionTerminal.AnimDebugTools'))


def _set_var(label, name, value):
    """One contract-var write. Returns True only on the plugin's literal 'ok:' prefix."""
    return str(_tools().call_method('SetAnimVariable', args=(label, name, repr(float(value))))).startswith('ok:')


def _sector(x, y):
    return int(((math.atan2(y, x) + math.pi) / TAU) * N_SECTORS) % N_SECTORS


def _phase(x, y, wavelength):
    return (x * math.cos(WAVE_ANGLE) + y * math.sin(WAVE_ANGLE)) / wavelength


def speed_at(x, y, t):
    """The speed wave, in cm/s. Public so the sampler can compare EXPECTED against MEASURED per instance."""
    u = 0.5 * (1.0 + math.sin(TAU * (t / SPEED_PERIOD - _phase(x, y, SPEED_WAVELENGTH))))
    return (SPEED_MIN + u * (SPEED_MAX - SPEED_MIN)) * min(1.0, max(0.0, t / SETTLE))


def turn_at(x, y, t):
    if t < TURN_AT:
        return 0.0
    return TURN_AMP * math.sin(TAU * ((t - TURN_AT) / TURN_PERIOD - _phase(x, y, TURN_WAVELENGTH)))


def aim_target(t):
    a = TAU * t / AIM_PERIOD
    return (AIM_RADIUS * math.cos(a), AIM_RADIUS * math.sin(a),
            AIM_Z_BASE + AIM_Z_AMP * math.sin(TAU * t / AIM_Z_PERIOD))


def aim_pitch_at(x, y, z, t):
    """Normalized -1..1 pitch onto the moving target. AimYaw stays pinned 0 (unpopulated axis in AO_Rifle)."""
    if t < AIM_AT:
        return 0.0
    tx, ty, tz = aim_target(t)
    dist = math.hypot(tx - x, ty - y)
    ang = math.atan2(tz - (z + AIM_EYE_Z), max(dist, 1.0))
    return max(-1.0, min(1.0, ang / AIM_PITCH_FULLSCALE))


def weapon_ik_at(t):
    if t < IK_AT:
        return 0.0
    return min(1.0, (t - IK_AT) / IK_RAMP)


# ---------------------------------------------------------------------------------------------------
# roster / partition
# ---------------------------------------------------------------------------------------------------

def _clip_skeleton(path):
    clip = unreal.load_asset(path)
    skel = clip.get_editor_property('skeleton') if clip else None
    return skel.get_path_name() if skel else None


def build_roster():
    """Snapshot the army as PLAIN DATA. No UObject survives this function (GOTCHA-A')."""
    world = game_world()
    if world is None:
        raise RuntimeError('no PIE world — start_pie before starting the conductor')
    want_skel = _clip_skeleton(CLIP_JUMP)
    roster = []
    for actor in unreal.GameplayStatics.get_all_actors_with_tag(world, unreal.Name(TAG_ALL)):
        mesh = actor.get_component_by_class(unreal.SkeletalMeshComponent)
        if mesh is None:
            continue
        inst = mesh.get_anim_instance()
        loc = actor.get_actor_location()
        skm = mesh.get_skeletal_mesh_asset()   # not the 'skeletal_mesh' UPROPERTY — deprecated in 5.x
        skel = skm.get_editor_property('skeleton') if skm else None
        tier = ''
        for tag in actor.tags:
            if str(tag).startswith(TAG_PREFIX):
                tier = str(tag)[len(TAG_PREFIX):]
        roster.append({
            'label': str(actor.get_actor_label()),
            'x': float(loc.x), 'y': float(loc.y), 'z': float(loc.z),
            'tier': tier,
            'sector': _sector(loc.x, loc.y),
            'abp': str(inst.get_class().get_name()) if inst else None,
            # Skeleton gate: only clips authored on this instance's own skeleton can be played into its slot.
            'can_montage': bool(skel is not None and want_skel is not None
                                and skel.get_path_name() == want_skel),
        })
    roster.sort(key=lambda r: r['label'])
    return roster


def _partition(roster):
    """Split the formation into the montage set and the UpperBodyState set, and check they are disjoint.

    The disjointness itself is true BY CONSTRUCTION — MONTAGE_SECTORS and UPPERBODY_SECTORS are disjoint
    constants and each roster entry has exactly one sector — so on a well-formed roster these raises CANNOT
    fire. Keep them anyway: what they can actually catch is two instances sharing an actor label (labels are
    the only handle the drive path has), which would put one name in both sets. Do not cite this as the
    mechanism that makes the partition safe; the sector split is that mechanism.
    """
    montage, upper, excluded = [], [], []
    for r in roster:
        if r['sector'] in MONTAGE_SECTORS:
            (montage if r['can_montage'] else excluded).append(r['label'])
        elif r['sector'] in UPPERBODY_SECTORS:
            upper.append(r['label'])
    overlap = sorted(set(montage) & set(upper))
    if overlap:
        raise RuntimeError('montage/UpperBodyState sets INTERSECT on %s — a montage sector instance would '
                           'have its finale cut by AM_MT_Attack' % overlap)
    also = sorted((set(montage) | set(excluded)) & set(upper))
    if also:
        raise RuntimeError('skeleton-excluded instances leaked into the UpperBodyState set: %s' % also)
    return {'montage': sorted(montage), 'upperbody': sorted(upper), 'montage_excluded': sorted(excluded)}


# ---------------------------------------------------------------------------------------------------
# montage-signal parsing — the DISCRIMINATING readback (see the header block)
# ---------------------------------------------------------------------------------------------------

def parse_slot_signal(tree, slot=SLOT):
    """Parse GetNodeWeights output into the two discriminating signals for `slot`, plus the vacuous one.

    Returns {'found', 'text_weight_pct', 'montages', 'abs_weight', 'line'}:
      text_weight_pct -- the slot node's OWN debug text `Weight:X%`  (discriminating)
      montages        -- the Montage('..') Anim('..') child items owned by this slot (discriminating)
      abs_weight      -- the trailing `(weight X)` AbsoluteWeight    (VACUOUS — reported to show it is)

    Child items are attributed by INDENT: the slot's subtree runs from its line until the next line at an
    indent <= the slot's, and only a Montage(...) strictly deeper than that belongs to it. Closing on ANY
    such line, not just another slot line, is load-bearing — see the indent block in the file header. Closing
    on slot lines alone leaked every deeper Montage(...) into DefaultSlot, including one under UpperBody and
    including cache-pose subtrees, which GatherDebugData re-emits from the root at indent 0.
    """
    out = {'found': False, 'text_weight_pct': None, 'montages': [], 'abs_weight': None, 'line': None}
    slot_indent = None
    for raw in str(tree).split('\n'):
        line = raw.rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(' '))
        hit = _SLOT_RE.search(line)
        if hit and hit.group(1) == slot:
            out['found'] = True
            out['text_weight_pct'] = float(hit.group(2))
            out['line'] = line.strip()
            slot_indent = indent
            aw = _ABSW_RE.search(line)
            out['abs_weight'] = float(aw.group(1)) if aw else None
            continue
        if slot_indent is not None and indent <= slot_indent:
            slot_indent = None          # ANY line at or above the slot's indent closes its subtree
            continue
        mont = _MONT_RE.search(line)
        if mont and slot_indent is not None:
            out['montages'].append({'montage': mont.group(1), 'anim': mont.group(2)})
    return out


def montage_signal(label, slot=SLOT):
    tree = str(_tools().call_method('GetNodeWeights', args=(label,)))
    if tree.startswith('ERROR:'):
        return {'label': label, 'error': tree}
    sig = parse_slot_signal(tree, slot)
    # The verdict uses ONLY the two discriminating signals. abs_weight is carried for contrast, never voted.
    #
    # Tri-state, because the two signals have DIFFERENT LIFETIMES and collapsing a disagreement to ABSENT
    # would make the negative case meaningless. They disagree because they come from different places:
    # `Weight:X%` is WeightData.SlotNodeWeight, refreshed only by FAnimNode_Slot::Update_AnyThread, while the
    # `Montage(...)` child is emitted straight off DebugData.AnimInstance->MontageInstances
    # (AnimNode_Slot.cpp:142) and is live whether or not the node updated.
    #
    # SO text_weight IS URO-DEPENDENT AND MONTAGE-CHILD IS NOT. The one PARTIAL in the T4 run was
    # `Army_far_000` at t=16.33 — a FAR-TIER (URO on) instance 0.8 s into a 6-loop inertial gesture, i.e. a
    # blend-IN window, not the blend-out tail the first draft of this comment claimed. Its node update was
    # skipped that frame so the slot weight read stale 0.0% while the montage genuinely played.
    # A FILMED TAKE MUST NOT READ `PARTIAL` ON A FAR-TIER INSTANCE AS "NOT PLAYING" — on camera it is playing.
    #
    # PARTIAL therefore carries the DIRECTION of the disagreement, because the two directions mean different
    # things: 'child-without-weight' is the URO-stale / blend-window case above (montage live, weight not yet
    # or no longer seen), 'weight-without-child' is a slot carrying weight with no montage instance behind it
    # — a genuinely suspicious state that would want investigating rather than narrating.
    has_child = bool(sig['montages'])
    has_weight = (sig['text_weight_pct'] or 0.0) > 0.0
    sig['verdict'] = ('MONTAGE' if (has_child and has_weight)
                      else 'ABSENT' if not (has_child or has_weight)
                      else 'PARTIAL')
    sig['partial_dir'] = (None if sig['verdict'] != 'PARTIAL'
                          else 'child-without-weight' if has_child else 'weight-without-child')
    sig['label'] = label
    return sig


# ---------------------------------------------------------------------------------------------------
# the conductor itself
# ---------------------------------------------------------------------------------------------------

# POP, do not get (same reason as Scripts/army_spawn.py): run_python execs into a PERSISTENT namespace, so a
# `get` would inherit the previous call's action. An unset name reads None, never stale. Popped HERE rather
# than at the dispatch below because the callback-hygiene block immediately after must know whether this exec
# is a 'start' — see why.
_action = globals().pop('CONDUCTOR_ACTION', None)
_args = globals().pop('CONDUCTOR_ARGS', None) or {}

# The handle lives under a name this file NEVER reassigns except at the two designated points (the stale
# sweep here, and the registration at the bottom). Parking it in a dict that the next exec() recreates loses
# the handle and leaks a live tick callback firing against a stale closure — one run found three concurrent
# copies interleaving and double-playing montages.
#
# BOTH BELTS ARE GATED ON 'start'. They fire only when a NEW conductor is being registered, because
# 'sample'/'status'/'stop' re-exec this same file: an ungated bump retires the run id out from under a
# conductor that is mid-timeline, so merely OBSERVING the army would silently stop it — and the observation
# would then report a frozen crowd it had itself frozen. (Caught live: a mid-run `sample` killed run 2.)
if _action == 'start':
    _stale = globals().get('_COND_HANDLE')
    if _stale is not None:
        unreal.unregister_slate_pre_tick_callback(_stale)
    _COND_HANDLE = None
    # Second belt: every registered copy of _tick resolves globals at CALL time out of the one shared
    # namespace, so a callback that survived a previous exec keeps running against the CURRENT state. Each
    # start stamps a run id, captured as a DEFAULT ARGUMENT (bound at def time, not a shared global); a tick
    # whose id is no longer current RETURNS IMMEDIATELY — it does NOT unregister itself (the early return is
    # ahead of _finish, which is where unregistration lives). It is inert, not retired. Nothing leaks today
    # because the sweep just above retires the single handle this file ever holds, but if two callbacks ever
    # coexisted only the last-registered one would be unregistered.
    _COND_RUN = globals().get('_COND_RUN', 0) + 1
else:
    _COND_HANDLE = globals().get('_COND_HANDLE')
    _COND_RUN = globals().get('_COND_RUN', 0)

_COND_ROSTER = globals().get('_COND_ROSTER', [])
_COND_SETS = globals().get('_COND_SETS', {})
_COND_STATE = globals().get('_COND_STATE', {})
_COND_PROBES = globals().get('_COND_PROBES', [])


def _prepare_movement(world, roster):
    """Per-instance one-time setup for the velocity-driven Speed wave (see the header finding).

    movement_mode = MOVE_NONE and a disabled component tick together stop the CMC from re-integrating or
    damping Velocity, so the value the conductor writes is still there when the ABP derives Speed from it,
    and the actor does not translate out of formation.
    """
    labels = {r['label'] for r in roster}
    done, failed = 0, []
    for actor in unreal.GameplayStatics.get_all_actors_with_tag(world, unreal.Name(TAG_ALL)):
        if str(actor.get_actor_label()) not in labels:
            continue
        cmc = actor.get_component_by_class(unreal.CharacterMovementComponent)
        if cmc is None:
            failed.append(str(actor.get_actor_label()))
            continue
        # notify_mode=NEVER on both: the default fires PreEditChange/PostEditChange, which on a registered
        # component means a render-command flush, a re-register and a full RerunConstructionScripts on the
        # owning Blueprint actor. See the notify block in the file header.
        cmc.set_editor_property('movement_mode', unreal.MovementMode.MOVE_NONE, NO_NOTIFY)
        cmc.set_component_tick_enabled(False)
        cmc.set_editor_property('velocity', unreal.Vector(0.0, 0.0, 0.0), NO_NOTIFY)
        done += 1
    return done, failed


def _reset_channels(world, roster):
    """Clear every montage channel and zero the contract vars before t=0.

    Without this the timeline is not deterministic: a montage left running by an earlier exec (or by a
    hand probe) would still own DefaultSlot when the first sector fires, and would read as a false
    POSITIVE on an instance the schedule never touched — corrupting the negative case the verification
    depends on. Montage_Stop with no montage argument stops ALL of them, which covers both slots — they
    resolve to the same DefaultGroup anyway.
    """
    labels = {r['label'] for r in roster}
    stopped = 0
    failures = []
    for actor in unreal.GameplayStatics.get_all_actors_with_tag(world, unreal.Name(TAG_ALL)):
        label = str(actor.get_actor_label())
        if label not in labels:
            continue
        mesh = actor.get_component_by_class(unreal.SkeletalMeshComponent)
        inst = mesh.get_anim_instance() if mesh else None
        if inst is not None:
            inst.montage_stop(0.0)   # no montage arg == stop every montage on the instance
            stopped += 1
        # THE AIM BEAT IS RESET OFF, AND THE ARMY KEEPS IT THERE. This crowd is unarmed — mannequins and
        # two axe-carrying Khaimeras — so a rifle stance on any of them is a defect, not a beat. Shot
        # 06's captions cite only the instance count and the frame cost (46 instances, 3.6/4.0 ms);
        # nothing in this shot claims an aim offset, so nothing here needs to opt in.
        #
        # Resetting it explicitly is the point of this function: pie_aim_hand_ik_drive turns the beat ON
        # every tick, run_python execs into a PERSISTENT namespace, and these drivers share a process. A
        # previous exec leaving it on would put the entire crowd into MF_Rifle_Idle_ADS from spine_01 up
        # — 46 instances holding invisible rifles — and no gate in the army path would see it: NoFrozen
        # asserts feet MOVE, Budget asserts milliseconds, SpotCheck samples state weights. The same blind
        # spot that hid the original defect.
        #
        # The two aim-beat vars are spelled out here rather than taken from mt_drive.rifle_aim_pair,
        # which is where that invariant and its full reasoning live. This file is EXEC'D into a bare
        # namespace (see namespace_merge_check.py) and carries its own _set_var rather than importing
        # the driver module, so pulling mt_drive in for a two-element tuple would add a cross-module
        # dependency to the render path to save two literals. If a THIRD gating var is ever added,
        # rifle_aim_pair is the one place that knows about it and this list has to be revisited.
        for name, val in (('TurnYawDelta', 0.0), ('AimPitch', 0.0), ('WeaponIKAlpha', 0.0),
                          ('AimYaw', 0.0), ('UpperBodyState', 0.0), ('RifleModeAlpha', 0.0),
                          ('AimOffsetAlpha', 0.0)):
            # Reported, not discarded: a failed pre-t=0 reset leaves a var at whatever the previous run left
            # it at, which is exactly the non-determinism this function exists to remove.
            if not _set_var(label, name, val):
                failures.append('%s/%s' % (label, name))
    return stopped, failures


def _actor_map(world):
    """label -> actor, built ONCE per tick from one world walk. Strictly local; never cached (GOTCHA-A')."""
    out = {}
    for actor in unreal.GameplayStatics.get_all_actors_with_tag(world, unreal.Name(TAG_ALL)):
        out[str(actor.get_actor_label())] = actor
    return out


def _blend_settings(seconds, inertial):
    s = unreal.MontageBlendSettings()
    a = unreal.AlphaBlendArgs()
    a.set_editor_property('blend_time', float(seconds))
    s.set_editor_property('blend', a)
    s.set_editor_property('blend_mode', unreal.MontageBlendMode.INERTIALIZATION if inertial
                          else unreal.MontageBlendMode.STANDARD)
    return s


def _fire_sector(actors, sectors, clip_path, blend_in, blend_out, rate, loops, inertial):
    """Fire one scheduled montage across a sector set. Returns per-label outcomes — NULL is recorded, not lost."""
    clip = unreal.load_asset(clip_path)
    if clip is None:
        # Named failure. Without this a bad/renamed path surfaces only as a full `nulls` list, which reads
        # like every instance rejected a valid clip.
        raise RuntimeError('clip did not load: %s' % clip_path)
    fired, nulls = [], []
    montage_set = set(_COND_SETS.get('montage', ()))
    for r in _COND_ROSTER:
        if r['sector'] not in sectors or r['label'] not in montage_set:
            continue
        actor = actors.get(r['label'])
        if actor is None:
            continue
        mesh = actor.get_component_by_class(unreal.SkeletalMeshComponent)
        inst = mesh.get_anim_instance() if mesh else None
        if inst is None:
            nulls.append(r['label'])
            continue
        if inertial:
            m = inst.play_slot_animation_as_dynamic_montage_with_blend_settings(
                clip, unreal.Name(SLOT), _blend_settings(blend_in, True),
                _blend_settings(blend_out, True), rate, loops)
        else:
            m = inst.play_slot_animation_as_dynamic_montage(
                clip, unreal.Name(SLOT), blend_in, blend_out, rate, loops)
        (fired if m else nulls).append(r['label'])
    return fired, nulls


def _capture_probe(t, positive=None, tag=''):
    """Discriminating montage readback on one POSITIVE and one NEGATIVE instance, plus the speed gradient.

    Captured from inside the tick because a run_python `sample` cannot reliably land inside a 0.35 s ripple.
    `positive` is passed in by the sector scheduler as a label that VERIFIABLY just received a montage —
    picking a fixed instance instead reads ABSENT whenever that instance's sector was not in the step that
    fired, which looks like a failure and is only bad probe timing. Stores plain dicts only.
    """
    pos = _COND_SETS.get('montage', [])
    neg = _COND_SETS.get('upperbody', [])
    entry = {'t': round(t, 2), 'tag': tag,
             'positive': montage_signal(positive or (pos[0] if pos else None)) if (positive or pos) else None,
             'negative': montage_signal(neg[0]) if neg else None}
    # ONE DRIVE PASS OF LAG IS EXPECTED, NOT AN ERROR. The conductor writes Velocity from a slate pre-tick,
    # i.e. AFTER the world tick; the ABP derives Speed from it on the NEXT tick. So the value readable now is
    # the wave sampled at the LAST DRIVE TIME, not at t. `expect_now` and `expect_at_last_drive` are both
    # recorded so the lag is visible instead of looking like the wave missing its target — MEASURED: at 3 fps
    # `got` matches `expect_at_last_drive` to six figures.
    prev = _COND_STATE.get('last_drive', t)
    grad = []
    for r in _COND_ROSTER[::6]:
        got = str(_tools().call_method('GetAnimVariable', args=(r['label'], 'Speed')))
        grad.append({'label': r['label'], 'x': round(r['x']), 'y': round(r['y']),
                     'expect_now': round(speed_at(r['x'], r['y'], t), 1),
                     'expect_at_last_drive': round(speed_at(r['x'], r['y'], prev), 1),
                     'got': got.split('=')[-1].strip()})
    entry['gradient'] = grad
    _COND_PROBES.append(entry)
    unreal.log('COND ' + json.dumps({'action': 'probe', 'probe': entry}))


def _finish(reason):
    _COND_STATE['done'] = True
    ticks = max(1, _COND_STATE.get('ticks', 0))
    unreal.log('COND ' + json.dumps({'action': 'finish', 'reason': reason,
                                     'ticks': _COND_STATE.get('ticks', 0),
                                     # The SLATE dt is clamped (max 0.125 s observed) and is NOT the world
                                     # delta — reporting it as the frame time would understate the crowd
                                     # cost by ~2.6x. The honest rate is world seconds per tick.
                                     'slate_mean_dt_ms': round(1000.0 * _COND_STATE.get('dt_sum', 0.0) / ticks, 1),
                                     'slate_max_dt_ms': round(1000.0 * _COND_STATE.get('dt_max', 0.0), 1),
                                     'world_ms_per_tick': round(1000.0 * _COND_STATE.get('t', 0.0) / ticks, 1),
                                     'drives': _COND_STATE.get('drives', 0),
                                     'fired': _COND_STATE.get('fired', 0),
                                     'write_failures': _COND_STATE.get('failures', [])[:8],
                                     # Error surface, so a run that threw cannot finish looking clean.
                                     'tick_errors': _COND_STATE.get('tick_errors', 0),
                                     'tick_error_last': _COND_STATE.get('tick_error_last'),
                                     'sector_errors': _COND_STATE.get('sector_errors', {}),
                                     'reset_failures': _COND_STATE.get('reset_failures', [])[:8],
                                     'probes': len(_COND_PROBES)}))
    # Self-unregister on the terminal phase. The run-id belt only makes a leaked callback INERT; it would
    # still fire every tick for the life of the editor.
    handle = globals().get('_COND_HANDLE')
    if handle is not None:
        unreal.unregister_slate_pre_tick_callback(handle)
        globals()['_COND_HANDLE'] = None


def _tick(dt, _my_run=_COND_RUN):   # default arg: bound at def time, so it is NOT a shared global
    try:
        if globals().get('_COND_RUN') != _my_run or _COND_STATE.get('done'):
            return
        world = game_world()
        if world is None:
            return
        wall = unreal.SystemLibrary.get_game_time_in_seconds(world)
        if _COND_STATE.get('t0') is None:
            _COND_STATE['t0'] = wall
        t = wall - _COND_STATE['t0']
        _COND_STATE['ticks'] = _COND_STATE.get('ticks', 0) + 1
        _COND_STATE['t'] = round(t, 2)
        # Frame rate, recorded rather than assumed. DO NOT ATTRIBUTE THE REMAINDER TO THE CROWD. The earlier
        # version of this comment claimed the driver was ~7 ms of a ~329 ms frame and "the rest is the
        # crowd's own cost"; that was wrong, because the 138-call SetAnimVariable timing did not cover the
        # velocity path, whose default notify mode was costing a render flush + re-register +
        # RerunConstructionScripts x46 per pass (see the file header). The budget probe owns the decomposition and must
        # measure PerformAnimEvaluation_WorkerThread and AnimGameThreadTime directly — frame time / N is not
        # comparable to those ceilings.
        _COND_STATE['dt_sum'] = _COND_STATE.get('dt_sum', 0.0) + float(dt)
        _COND_STATE['dt_max'] = max(_COND_STATE.get('dt_max', 0.0), float(dt))
        if t > TOTAL:
            _finish('timeline complete')
            return

        # Lazy, memoized per-tick: _actor_map(world) is a full get_all_actors_with_tag world walk + a
        # 46-entry dict build. Most ticks need it for nothing — the schedule loop below only calls
        # _fire_sector() on the handful of ticks a sector item actually fires, and the wave-drive loop
        # further down returns early on every tick the DRIVE_HZ throttle skips. Building it unconditionally
        # meant the expensive part ran on every tick regardless of the throttle.
        _actors_box = {}
        def _get_actors():
            if 'v' not in _actors_box:
                _actors_box['v'] = _actor_map(world)
            return _actors_box['v']

        # --- scheduled montage sectors: checked EVERY tick (a 0.35 s ripple step must not be throttled) ---
        for idx, item in enumerate(SCHEDULE):
            if idx in _COND_STATE.setdefault('fired_idx', []) or t < item[0]:
                continue
            # MARK AFTER, NOT BEFORE. Marking first meant a throw inside _fire_sector consumed the schedule
            # slot permanently: the step never retried, the outer handler logged one line without a
            # traceback, and `finish` looked structurally normal apart from a low `fired` count — i.e. the
            # finale silently not firing, on camera, with no error to find. A step is now retried on the next
            # tick, up to SECTOR_RETRIES, and every failure is counted into `sector_errors` so `finish`
            # reports it. The retry is bounded so a hard failure (a clip that will never load) neither spams
            # the log for the rest of the timeline nor blocks the steps behind it.
            try:
                fired, nulls = _fire_sector(_get_actors(), item[1], item[2], item[3],
                                            item[4], item[5], item[6], item[7])
            except Exception:
                errs = _COND_STATE.setdefault('sector_errors', {})
                key = str(idx)
                errs[key] = errs.get(key, 0) + 1
                unreal.log_warning('COND sector %d FAILED (attempt %d/%d) t=%.2f\n%s'
                                   % (idx, errs[key], SECTOR_RETRIES, t, traceback.format_exc()))
                if errs[key] >= SECTOR_RETRIES:
                    _COND_STATE['fired_idx'].append(idx)   # give up on this step, keep the timeline running
                continue
            _COND_STATE['fired_idx'].append(idx)
            _COND_STATE['fired'] = _COND_STATE.get('fired', 0) + len(fired)
            if fired:
                # Probe THIS step's own recipient, a blend-in later. Bound to a label we watched receive a
                # montage, so an ABSENT here is a real failure rather than a mistimed sample.
                _COND_STATE.setdefault('pending', []).append(
                    [t + PROBE_LAG, fired[0], item[2].rsplit('/', 1)[-1].split('.')[0]])
            unreal.log('COND ' + json.dumps({'action': 'sector', 't': round(t, 2),
                                             'clip': item[2].rsplit('/', 1)[-1].split('.')[0],
                                             'sectors': list(item[1]), 'inertial': item[7],
                                             'fired': len(fired), 'null': nulls}))

        # --- UpperBodyState on the DISJOINT set only (never on a montage-sector instance) ---
        for at, val, key in ((UB_ON_AT, 1.0, 'ub_on'), (UB_OFF_AT, 0.0, 'ub_off')):
            if t >= at and not _COND_STATE.get(key):
                _COND_STATE[key] = True
                bad = [lbl for lbl in _COND_SETS.get('upperbody', [])
                       if not _set_var(lbl, 'UpperBodyState', val)]
                unreal.log('COND ' + json.dumps({'action': 'upperbody', 't': round(t, 2), 'value': val,
                                                 'n': len(_COND_SETS.get('upperbody', [])), 'failed': bad}))

        # --- auto-probes: fixed baselines, plus one per sector step keyed to that step's own recipient ---
        for at in PROBE_AT:
            key = 'probe_%.2f' % at
            if t >= at and not _COND_STATE.get(key):
                _COND_STATE[key] = True
                _capture_probe(t, tag='baseline')
        pending = _COND_STATE.setdefault('pending', [])
        for entry in [p for p in pending if t >= p[0]]:
            pending.remove(entry)
            _capture_probe(t, positive=entry[1], tag=entry[2])

        # --- the waves, throttled ---
        if (t - _COND_STATE.get('last_drive', -99.0)) < (1.0 / DRIVE_HZ):
            return
        _COND_STATE['last_drive'] = t
        _COND_STATE['drives'] = _COND_STATE.get('drives', 0) + 1
        failures = []
        actors = _get_actors()
        for r in _COND_ROSTER:
            actor = actors.get(r['label'])
            if actor is None:
                continue
            # Speed: written to the SOURCE the ABP derives from, along the instance's own facing so the
            # legs read forward. Only the magnitude reaches Speed (VectorLength), so direction is cosmetic.
            spd = speed_at(r['x'], r['y'], t)
            cmc = actor.get_component_by_class(unreal.CharacterMovementComponent)
            if cmc is not None:
                fwd = actor.get_actor_forward_vector()
                # notify_mode=NEVER — the hot one. This value CHANGES every pass, so the default notify path
                # fired every pass on all 46 registered components (render flush + re-register +
                # RerunConstructionScripts). File header has the chain and the measured effect.
                cmc.set_editor_property('velocity', unreal.Vector(fwd.x * spd, fwd.y * spd, 0.0), NO_NOTIFY)
            # The three genuine contract inputs. AimYaw is written ONCE at start (pinned 0, unpopulated
            # axis) rather than every pass — 46 extra world walks per pass to rewrite a constant.
            for name, val in (('TurnYawDelta', turn_at(r['x'], r['y'], t)),
                              ('AimPitch', aim_pitch_at(r['x'], r['y'], r['z'], t)),
                              ('WeaponIKAlpha', weapon_ik_at(t))):
                if not _set_var(r['label'], name, val):
                    failures.append('%s/%s' % (r['label'], name))
        if failures:
            _COND_STATE.setdefault('failures', []).extend(failures[:8])
            unreal.log_warning('COND write failures t=%.2f n=%d %s' % (t, len(failures), failures[:6]))
    except Exception as exc:
        # Counted and TRACEBACKED. A bare one-line warning gave no file/line, and nothing about the throw
        # reached the `finish` record, so a tick that failed every frame produced a finish JSON that looked
        # structurally normal.
        _COND_STATE['tick_errors'] = _COND_STATE.get('tick_errors', 0) + 1
        _COND_STATE.setdefault('tick_error_last', str(exc))
        unreal.log_warning('COND err %s\n%s' % (exc, traceback.format_exc()))


def start_conductor():
    world = game_world()
    if world is None:
        raise RuntimeError('no PIE world — start_pie before starting the conductor')
    roster = build_roster()
    sets = _partition(roster)
    globals()['_COND_ROSTER'] = roster
    globals()['_COND_SETS'] = sets
    globals()['_COND_STATE'] = {'t0': None, 'done': False, 'fired_idx': []}
    globals()['_COND_PROBES'] = []
    prepared, no_cmc = _prepare_movement(world, roster)
    cleared, reset_failures = _reset_channels(world, roster)
    globals()['_COND_STATE']['reset_failures'] = reset_failures
    # RE-ASSERT ALL THREE CONSTANTS HERE, not just AimYaw. AimOffsetAlpha was
    # written once inside _reset_channels on the stated grounds that it is 'a constant for the whole
    # run, for the same reason AimYaw is' - but AimYaw was never only-once: it is re-pinned on this
    # very line. That left AimOffsetAlpha as the one RUN-CRITICAL var with no second assertion, and
    # it is the one whose loss is silent: an AnimInstance reinitialised mid-run (mesh re-registration,
    # a URO or forced-LOD tier change - which this conductor's own config drives) reverts it to the
    # CDO default while AimPitch keeps being driven every pass, so the aim wave sweeps a
    # switched-off node and the footage simply shows no aim response, with nothing failing anywhere.
    # RifleModeAlpha is re-pinned for the mirror-image reason (2026-08-14): its CDO default is 0, so a
    # reinitialised instance FIXES it rather than breaking it, but a stale 1 surviving from a
    # beats-driver exec in the same persistent namespace is exactly what _reset_channels clears, and a
    # clear worth doing once before t=0 is worth re-asserting at start like the other two.
    pinned = [r['label'] for r in roster
              if not _set_var(r['label'], 'AimYaw', 0.0)
              or not _set_var(r['label'], 'AimOffsetAlpha', 0.0)
              or not _set_var(r['label'], 'RifleModeAlpha', 0.0)]
    summary = {
        'action': 'start', 'run': _COND_RUN, 'n': len(roster), 'total_s': TOTAL, 'drive_hz': DRIVE_HZ,
        'tiers': {t: sum(1 for r in roster if r['tier'] == t) for t in ('hero', 'mid', 'far')},
        'sectors': {str(s): sum(1 for r in roster if r['sector'] == s) for s in range(N_SECTORS)},
        'montage_set': len(sets['montage']), 'upperbody_set': len(sets['upperbody']),
        'montage_excluded_skeleton': sets['montage_excluded'],
        'disjoint': sorted(set(sets['montage']) & set(sets['upperbody'])) == [],
        'movement_prepared': prepared, 'no_cmc': no_cmc, 'montage_channels_cleared': cleared,
        'reset_failures': reset_failures[:8], 'aimyaw_pin_failures': pinned,
    }
    unreal.log('COND ' + json.dumps(summary))
    return summary


def stop_conductor():
    globals()['_COND_RUN'] = -1
    handle = globals().get('_COND_HANDLE')
    if handle is not None:
        unreal.unregister_slate_pre_tick_callback(handle)
        globals()['_COND_HANDLE'] = None
    world = game_world()
    restored = 0
    if world is not None:
        for actor in unreal.GameplayStatics.get_all_actors_with_tag(world, unreal.Name(TAG_ALL)):
            cmc = actor.get_component_by_class(unreal.CharacterMovementComponent)
            if cmc is None:
                continue
            cmc.set_editor_property('velocity', unreal.Vector(0.0, 0.0, 0.0), NO_NOTIFY)
            cmc.set_component_tick_enabled(True)
            restored += 1
    unreal.log('COND ' + json.dumps({'action': 'stop', 'movement_restored': restored}))
    return restored


def sample_army(k=10):
    """Live readback: the spatial speed gradient across the formation + the montage signal on K instances.

    Speed is read back through GetAnimVariable, i.e. the value the ABP DERIVED this frame — which is what the
    state machine and the BlendSpace X pin actually consumed, not what we asked for.
    """
    roster = globals().get('_COND_ROSTER', [])
    if not roster:
        raise RuntimeError('no roster — start the conductor first')
    t = _COND_STATE.get('t', 0.0)
    sets = globals().get('_COND_SETS', {})
    mont = set(sets.get('montage', ()))
    step = max(1, len(roster) // max(k, 1))
    rows = []
    for r in roster[::step][:k]:
        sig = montage_signal(r['label'])
        rows.append({
            'label': r['label'], 'tier': r['tier'], 'sector': r['sector'],
            'x': round(r['x']), 'y': round(r['y']),
            'phase': round(_phase(r['x'], r['y'], SPEED_WAVELENGTH) % 1.0, 3),
            'expect_speed': round(speed_at(r['x'], r['y'], t), 1),
            'speed': str(_tools().call_method('GetAnimVariable', args=(r['label'], 'Speed'))).split('=')[-1].strip(),
            'turn': str(_tools().call_method('GetAnimVariable', args=(r['label'], 'TurnYawDelta'))).split('=')[-1].strip(),
            'aim_pitch': str(_tools().call_method('GetAnimVariable', args=(r['label'], 'AimPitch'))).split('=')[-1].strip(),
            'ik': str(_tools().call_method('GetAnimVariable', args=(r['label'], 'WeaponIKAlpha'))).split('=')[-1].strip(),
            'in_montage_set': r['label'] in mont,
            'slot_text_weight_pct': sig.get('text_weight_pct'),
            'slot_abs_weight_VACUOUS': sig.get('abs_weight'),
            'montage_children': [m['anim'] for m in sig.get('montages', [])],
            'verdict': sig.get('verdict'), 'partial_dir': sig.get('partial_dir'),
        })
    # Whole-formation speed spread — the gradient claim rests on this, not on the sampled rows.
    speeds = []
    for r in roster:
        got = str(_tools().call_method('GetAnimVariable', args=(r['label'], 'Speed')))
        try:
            speeds.append(float(got.split('=')[-1]))
        except ValueError:
            pass
    out = {'action': 'sample', 't': t, 'rows': rows,
           'formation_speed': {'n': len(speeds), 'min': round(min(speeds), 1) if speeds else None,
                               'max': round(max(speeds), 1) if speeds else None,
                               'distinct_values': len({round(s, 1) for s in speeds})},
           'disjoint_check': {
               'montage_n': len(sets.get('montage', ())), 'upperbody_n': len(sets.get('upperbody', ())),
               'intersection': sorted(set(sets.get('montage', ())) & set(sets.get('upperbody', ()))),
               'excluded_skeleton': sets.get('montage_excluded', [])},
           'probes': _COND_PROBES}
    unreal.log('COND ' + json.dumps(out))
    return out


def assert_no_uobject_globals(namespace=None, raise_on_find=True):
    """Sweep the PERSISTENT run_python namespace for names still bound to UObjects. Run BEFORE any map change.

    GOTCHA-A' is a HARD EDITOR CRASH, not a leak: run_python execs at module level, so every bare name in a
    throwaway probe (`w = ...get_game_world()`, `a = acts[0]`) becomes a module global that keeps its UObject
    alive, and the next load_level fatal-errors on `EditorServer.cpp:2544 World Memory Leaks`. The T4 run
    found 14 such names by hand. Doing it by hand is exactly the kind of step that gets skipped once, so it
    is a function.

    Returns the sorted list of offending names. With raise_on_find (the default) it RAISES instead of
    returning quietly, so it can be the last line before a map change and cannot be read past. Call
    `assert_no_uobject_globals(raise_on_find=False)` to inspect, then `del` them and `unreal.SystemLibrary`'s
    collect_garbage() before retrying.

    Skips this module's own names (all plain data by construction) and dunders. Containers are checked one
    level deep, since `acts = get_all_actors_with_tag(...)` is the common shape.
    """
    ns = namespace if namespace is not None else globals()
    mine = {'unreal', 'json', 'math', 're', 'traceback', 'NO_NOTIFY'}
    found = []
    for name, value in list(ns.items()):
        if name.startswith('__') or name in mine:
            continue
        if isinstance(value, unreal.Object):
            found.append('%s:%s' % (name, type(value).__name__))
        elif isinstance(value, (list, tuple, set)):
            for item in list(value)[:64]:
                if isinstance(item, unreal.Object):
                    found.append('%s[]:%s' % (name, type(item).__name__))
                    break
        elif isinstance(value, dict):
            for item in list(value.values())[:64]:
                if isinstance(item, unreal.Object):
                    found.append('%s{}:%s' % (name, type(item).__name__))
                    break
    found.sort()
    unreal.log('COND ' + json.dumps({'action': 'uobject_globals', 'n': len(found), 'names': found}))
    if found and raise_on_find:
        raise RuntimeError('%d globals still hold UObjects — a map change now is the EditorServer.cpp:2544 '
                           'fatal. del them and collect_garbage() first: %s' % (len(found), found))
    return found


if _action == 'start':
    start_conductor()
    _COND_HANDLE = unreal.register_slate_pre_tick_callback(_tick)
    unreal.log('COND registered run=%d total=%.1fs' % (_COND_RUN, TOTAL))
elif _action == 'stop':
    stop_conductor()
elif _action == 'sample':
    sample_army(int(_args.get('k', 10)))
elif _action == 'status':
    unreal.log('COND ' + json.dumps({'action': 'status', 'run': _COND_RUN,
                                     'registered': globals().get('_COND_HANDLE') is not None,
                                     'state': {k: v for k, v in _COND_STATE.items()
                                               if isinstance(v, (int, float, bool, str, type(None)))},
                                     'probes': len(_COND_PROBES)}))
elif _action == 'sweep':
    assert_no_uobject_globals(raise_on_find=False)
else:
    # LOUD fallback. Without it an unset or misspelled CONDUCTOR_ACTION produced zero output and zero error,
    # in a file whose whole contract with the caller is "every action prints one COND <json> line" — so the
    # caller's parse found nothing and could not tell "no action ran" from "the action ran and said nothing".
    unreal.log_error('COND ' + json.dumps({'action': 'ERROR', 'reason': 'unknown CONDUCTOR_ACTION',
                                           'got': repr(_action),
                                           'valid': ['start', 'stop', 'sample', 'status', 'sweep']}))
    raise RuntimeError('CONDUCTOR_ACTION is %r — set it to one of start/stop/sample/status/sweep on EVERY '
                       'call (both names are popped, so an unset name is None, never stale)' % (_action,))
