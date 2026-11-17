# Full-body dynamic-montage seamlessness effect probe.
# The Python-side twin of MotionTerminal.Pie.MontageSeamless (PieTests.cpp): same graph, same two clips,
# same slot, same two signals — a live diagnostic you can run without a rebuild.
#
# MECHANISM: the montage-slot wiring splices `LayeredBlendPerBone -> Slot 'DefaultSlot' -> Inertialization -> AimOffset`
# into the shared Template ABP_MT_Locomotion. EMontageBlendMode::Inertialization is documented to REQUIRE an
# inertialization node downstream of the slot the montage plays into, so a walk->run swap through DefaultSlot
# with BlendMode=Inertialization exercises exactly that wiring.
#
# TWO SIGNALS, sampled EVERY tick (no interval throttle — frame-to-frame continuity is the whole point):
#   (a) DefaultSlot local weight. RESIDUAL GUARD ONLY, not evidence of a cross-blend: GetSlotMontageLocalWeight
#       SUMS the weights of every montage valid for the slot and clamps to 1.0, so a same-slot cross-blend and
#       a hard cut BOTH read ~1.0. It cannot distinguish them. What it still catches is a montage expiring or
#       being stopped before the swap lands, which would drop the slot back to locomotion.
#   (b) POP DETECTOR: max |delta position| over a probe bone set, in COMPONENT space (so pawn/capsule motion
#       cannot leak into the signal), NORMALIZED BY THE FRAME'S dt AND REPORTED IN cm/s. A smooth blend
#       spreads the pose difference across the blend window; a hard cut delivers all of it at once.
#
# ****  cm/s, NOT cm/frame — AND NORMALIZATION IS ONLY HALF A FIX  ****
# "cm per frame" is only meaningful if a frame is a fixed amount of time, and under automation/editor PIE it is
# not: frames hitch to ~0.3 s, and a hand travelling ~10 m/s covers ~3 m in one such frame. The C++ test's
# FIRST build measured raw per-frame deltas and did NOT discriminate — inertial 174.4 vs hard-cut control 159.5
# (ratio 0.91x), with a walk-only floor of 303.8 LARGER than both arms. That was frame-time noise, not blending.
# The C++ test fixed it by PINNING FApp fixed-time-step at 60 Hz. This probe runs on the live unpinned clock,
# so it divides by the measured dt instead. Anything that reports a raw per-frame delta here is measuring the
# frame rate.
#
# BUT: dividing by dt removes the VARIANCE between frames, not the effect of dt's MAGNITUDE. A pop is a
# DISPLACEMENT, not a velocity. A pose jump D is delivered in ONE frame no matter how long that frame is, so
# normalized it reads D/dt and SHRINKS as frames lengthen — while natural motion v is dt-invariant. The two
# come back as v vs (v + D/dt), i.e. discrimination goes as:
#
#     ratio = 1 + D / (v * dt)
#
# At the C++ test's pinned 1/60 that is the ~24x separation it reports. At the ~0.3 s frames THIS probe can
# see, the same D and v give only ~2.3x — the identical wiring, the identical blend, an almost useless probe.
# Long frames degrade a normalized pop detector toward blindness; they do not break it the way raw cm/frame
# was broken, which is worse, because it still returns a plausible-looking number.
#
# ---- CONSEQUENCE FOR Scripts/army_conductor.py (the army crowd driver) ----
# The army runs on a CROWD map with dozens of skeletal meshes: frames there are LONG, which is exactly the
# regime the formula above says a normalized probe cannot discriminate in. Do NOT read "normalize to cm/s"
# as the remedy there — it is necessary but NOT sufficient, and inheriting it as a sufficient fix would mean
# reporting a ~2x ratio as evidence of anything. The two honest options for the army:
#   (1) PIN THE CLOCK for the measurement window (FApp fixed-time-step, as MontageSeamless does), or
#   (2) accept that the probe cannot discriminate at crowd frame rates and rely on the C++ assertion
#       (MotionTerminal.Pie.MontageSeamless) for the seamlessness claim, using the probe only for liveness
#       (slot reachable, montage created, weight ramped) — which does not depend on dt at all.
# Anything else is measuring the crowd's frame rate and calling it a blend.
#
# CONFOUND-IMMUNITY: run it TWICE, changing only the blend mechanism.
#   MODE = 'inertial'  -> BlendMode=Inertialization, BlendTime=0.25   (the claim)
#   MODE = 'hardcut'   -> BlendMode=Standard,        BlendTime=0.0    (the control that MUST spike)
# A detector that reports the same number for both cannot tell smooth from popping, and a green from it would
# be worthless. Compare the two runs' final `MSEAM ... phase=done` lines — the `maxpop_cms` field, in cm/s.
# The `floor_cms` on the same line is the walk-only natural-motion floor, i.e. how fast bones move with NO
# blend happening at all; a maxpop that does not clear it is not a pop. Expect the hard-cut run to land an
# order of magnitude above the inertial run. If the two come back comparable, check that dt is sane before
# concluding anything (a `dt=` of 0 or a stalled clock makes every figure garbage).
#
# Runs inside a PIE session as a probe (duration >= 10 s). Caller PREPENDS:
#   MODE = 'inertial'   # or 'hardcut'
#   exec(open(r'.../Scripts/archive/pie_montage_drive.py').read())
# then samples LogPython 'MSEAM ' lines.
import unreal

WALK = '/Game/MT/GAS_Clips/MT_GAS_Walk_Fwd.MT_GAS_Walk_Fwd'
RUN = '/Game/MT/GAS_Clips/MT_GAS_Run_Fwd.MT_GAS_Run_Fwd'
SLOT = 'DefaultSlot'

# Phase schedule in game seconds. WALK_AT gives the pawn time to settle in Idle; SWAP_AT is far enough past
# WALK_AT to leave a usable steady-state window. The ramp itself is excluded by the SLOT_OWNED gate below,
# not by this spacing — timing alone would not guarantee it.
WALK_AT = 1.5
SWAP_AT = 3.0
END_AT = 3.9
BLEND_SEC = 0.25

# Probe bones: extremities travel fastest, so they carry the largest honest speed AND the largest pop.
# (The C++ test walks every bone; this subset keeps a per-tick Python probe cheap.)
BONES = ['pelvis', 'spine_03', 'head', 'hand_l', 'hand_r', 'foot_l', 'foot_r']

# Only count a frame toward the walk-only floor once the walk montage actually owns the slot — otherwise the
# floor absorbs the walk montage's own 0.25 s blend-in ramp, which is not steady-state motion. (Same gate the
# C++ test uses: SlotW > 0.95.)
SLOT_OWNED = 0.95

try:
    _MODE = MODE  # noqa: F821  (prepended by the caller)
except NameError:
    _MODE = 'inertial'

# Unregister a callback left over from an EARLIER exec of this file FIRST. The handle must live under a
# name this file never reassigns at import time — parking it inside a dict that the next exec recreates
# loses the handle and leaks a live tick callback that keeps firing against a stale closure.
_stale = globals().get('_MSEAM_HANDLE')
if _stale is not None:
    unreal.unregister_slate_pre_tick_callback(_stale)
_MSEAM_HANDLE = None

# Second belt: every registered copy of _tick resolves globals at CALL time out of the one shared
# interpreter namespace, so a callback that survived a previous exec keeps running against the CURRENT
# _G — double-playing montages and double-counting samples. Each exec stamps a run id; a tick whose
# captured id is no longer current does nothing. (Observed live: three concurrent copies interleaving.)
_MSEAM_RUN = globals().get('_MSEAM_RUN', 0) + 1

_G = {'prev': None, 'prevnow': None, 'walked': False, 'swapped': False, 'done': False,
      'floor': 0.0, 'pop': 0.0, 'popbone': '', 'minslot': 1.0, 'n': 0}


def _blend_settings(inertial):
    s = unreal.MontageBlendSettings()
    a = unreal.AlphaBlendArgs()
    a.set_editor_property('blend_time', BLEND_SEC if inertial else 0.0)
    s.set_editor_property('blend', a)
    s.set_editor_property(
        'blend_mode',
        unreal.MontageBlendMode.INERTIALIZATION if inertial else unreal.MontageBlendMode.STANDARD)
    return s


def _comp_positions(mesh):
    out = {}
    for b in BONES:
        t = mesh.get_socket_transform(unreal.Name(b), unreal.RelativeTransformSpace.RTS_COMPONENT)
        out[b] = t.translation
    return out


def _max_delta(prev, cur):
    best, bone = 0.0, ''
    for b, p in prev.items():
        c = cur.get(b)
        if c is None:
            continue
        dx, dy, dz = c.x - p.x, c.y - p.y, c.z - p.z
        d = (dx * dx + dy * dy + dz * dz) ** 0.5
        if d > best:
            best, bone = d, b
    return best, bone


def _tick(dt, _my_run=_MSEAM_RUN):   # default arg: bound at def time, so it is NOT a shared global
    try:
        if globals().get('_MSEAM_RUN') != _my_run or _G['done']:
            return
        w = unreal.EditorLevelLibrary.get_game_world()
        if not w:
            return
        pc = unreal.GameplayStatics.get_player_controller(w, 0)
        pawn = pc.get_controlled_pawn() if pc else None
        mesh = pawn.get_component_by_class(unreal.SkeletalMeshComponent) if pawn else None
        inst = mesh.get_anim_instance() if mesh else None
        if not inst:
            return

        now = unreal.SystemLibrary.get_game_time_in_seconds(w)
        cur = _comp_positions(mesh)
        delta, bone = (_max_delta(_G['prev'], cur) if _G['prev'] else (-1.0, ''))
        # Normalize to cm/s against the GAME clock the pose was evaluated on (not the slate dt, which can
        # differ). A non-positive gap means the sim did not advance between ticks — that frame carries no
        # information about speed, so it is dropped rather than dividing by ~0 and inventing a spike.
        gap = (now - _G['prevnow']) if _G['prevnow'] is not None else 0.0
        speed = (delta / gap) if (delta >= 0.0 and gap > 1e-6) else -1.0
        _G['prev'] = cur
        _G['prevnow'] = now
        # GetSlotMontageLocalWeight is not a UFUNCTION; Blueprint_GetSlotMontageLocalWeight is its
        # script-exposed twin (AnimInstance.h — UFUNCTION(BlueprintPure, DisplayName="GetSlotLocalWeight")).
        slot = float(inst.call_method('Blueprint_GetSlotMontageLocalWeight', args=(SLOT,)))

        if not _G['walked'] and now >= WALK_AT:
            _G['walked'] = True
            m = inst.play_slot_animation_as_dynamic_montage(
                unreal.load_asset(WALK), unreal.Name(SLOT), 0.1, 0.25, 1.0, 20)
            unreal.log('MSEAM mode=%s phase=walk montage=%s (null => DefaultSlot unreachable in the graph)'
                       % (_MODE, m.get_name() if m else 'NULL'))
            return

        if _G['walked'] and not _G['swapped']:
            if speed > 0.0 and slot > SLOT_OWNED:
                _G['floor'] = max(_G['floor'], speed)   # walk-only natural-motion floor, cm/s
            if now >= SWAP_AT:
                _G['swapped'] = True
                inertial = (_MODE == 'inertial')
                m = inst.play_slot_animation_as_dynamic_montage_with_blend_settings(
                    unreal.load_asset(RUN), unreal.Name(SLOT),
                    _blend_settings(inertial), _blend_settings(True), 1.0, 20)
                unreal.log('MSEAM mode=%s phase=swap blend_mode=%s blend_time=%.2f slotW=%.3f floor_cms=%.1f montage=%s'
                           % (_MODE, 'Inertialization' if inertial else 'Standard',
                              BLEND_SEC if inertial else 0.0, slot, _G['floor'],
                              m.get_name() if m else 'NULL'))
            else:
                unreal.log('MSEAM mode=%s phase=ramp t=%.2f dt=%.4f slotW=%.3f speed_cms=%.1f floor_cms=%.1f'
                           % (_MODE, now, gap, slot, speed, _G['floor']))
            return

        if _G['swapped'] and now < END_AT:
            _G['n'] += 1
            if speed > _G['pop']:
                _G['pop'], _G['popbone'] = speed, bone
            _G['minslot'] = min(_G['minslot'], slot)
            unreal.log('MSEAM mode=%s phase=measure t=%.2f dt=%.4f slotW=%.3f speed_cms=%.1f maxpop_cms=%.1f(%s) minslot=%.3f n=%d'
                       % (_MODE, now, gap, slot, speed, _G['pop'], _G['popbone'], _G['minslot'], _G['n']))
            return

        if _G['swapped']:
            _G['done'] = True
            unreal.log('MSEAM mode=%s phase=done maxpop_cms=%.1f bone=%s floor_cms=%.1f minslot=%.3f samples=%d'
                       % (_MODE, _G['pop'], _G['popbone'], _G['floor'], _G['minslot'], _G['n']))
            # Self-unregister on the terminal phase. The run-id belt above only makes a leaked callback
            # inert; it still fires every tick for the life of the editor. This file's own gotcha is
            # leaked slate callbacks, so it must not leak its own.
            _h = globals().get('_MSEAM_HANDLE')
            if _h is not None:
                unreal.unregister_slate_pre_tick_callback(_h)
                globals()['_MSEAM_HANDLE'] = None
    except Exception as e:
        unreal.log_warning('MSEAM err %s' % e)


_MSEAM_HANDLE = unreal.register_slate_pre_tick_callback(_tick)
unreal.log('MSEAM registered mode=%s slot=%s walk@%.1fs swap@%.1fs end@%.1fs' % (_MODE, SLOT, WALK_AT, SWAP_AT, END_AT))
