# Rifle take render driver — the real rifle, applied AT RUNTIME, plus the off-hand rifle reach measurement.
#
# ============================================================================================
# WHY THE SWAP LIVES HERE AND NOT ON THE PAWN BLUEPRINTS (the load-bearing decision)
#
# The Soviet Assault Rifle is licensed Fab content and lives under the gitignored /Game/MT/Weapons/.
# BP_MT_TestPawn / BP_MT_TestPawn_Quinn and their test maps are COMMITTED, and their pie_rigs.json rows
# are the only `localOnly: false` rows in the project — they carry every portable PIE assertion this
# repo has. Pointing their WeaponMesh at a gitignored mesh would resolve to a null StaticMesh on any
# clone: Pie.HandReachesSocket's socket guard fires RED, and because the EventGraph derives
# WeaponIKTargetWorld from that socket unconditionally every tick, the null would also bleed Blueprint
# errors into the zero-BP-errors smoke check.
#
# So the committed pawns keep the SM_MT_Rifle cube — a fixture any clone can actually reproduce — and
# `--tier pie` keeps measuring it. This driver applies the rifle to the live PIE pawns for the render
# only, and measures the rifle's OWN reach in the driven world so the off-hand reach caption is gated by
# a number measured on the thing the footage actually shows. Two claims, two measurements, neither
# overclaimed; nothing tracked changes.
#
# WHAT IT DOES, in order, inside the MRQ PIE world:
#   1. execs Scripts/pie_aim_hand_ik_drive.py — the proven aim + off-hand reach timeline driver, unmodified. This
#      script adds to it; it does not reimplement it and writes NO animation contract variable.
#      "Writes no contract variable" is true of THIS file's own code and describes the source, not the
#      render: the exec'd beats driver writes the contract every tick. What it writes is now correct.
#      RESOLVED (this was the AIM-KNOWN-BAD site; the tag is retired). The beats driver used
#      to write AimOffsetAlpha=1 against a graph whose base was the UNARMED pose, so the rifle take rendered
#      WITH the posture defect and re-rendering could not fix it. ABP_MT_Locomotion now carries the
#      rifle-mode branch — SequenceEvaluator(MF_Rifle_Idle_ADS) -> AO_Rifle ->
#      LayeredBoneBlend(spine_01, mesh-space rotation, weight=RifleModeAlpha) -> RifleModePose — and the
#      beats driver writes RifleModeAlpha=1 alongside AimOffsetAlpha=1, so the delta lands over the base
#      it was extracted against. The rifle take renders the aim beat it always claimed to.
#   2. per pawn, once: swaps WeaponMesh's static mesh to the rifle and applies the solved port-arms
#      carry transform (below); hides Khaimera's native Paragon axe.
#   3. samples hand_l -> IK_Hand_Gun every SAMPLE_DT (0.05 s), bucketed by TIME into the OFF/ON windows
#      below, and reports distOff / distOn / gap in the same shape as the C++ FHandReachesSocketCommand.
#      It does NOT assert a gap gate, and the WeaponIKAlpha read-back is NOT what proves the OFF samples
#      are off — see _read_var's docstring for where that proof actually lives.
#
# THE CARRY TRANSFORM (hand_r bone space) is SOLVED, not guessed — derived by Scripts/solve_weapon_carry.py
# run against the live idle pose, and re-derivable by re-running it. It exists because the cube's numbers
# could not transfer: the cube's foregrip sat 15 cm straight up out of hand_r at 99% of the left arm's two-bone
# reach, and the real rifle's 40.87 cm grip->foregrip span is unreachable from that orientation. The
# solved port-arms diagonal (45 deg left, 30 deg up) lands the foregrip at 84% of Manny/Quinn's reach and
# 80% of Khaimera's. Solved per rig because Khaimera's skeleton is not Manny's.
#
# GOTCHA-A': no World/Actor/Component ever binds to a module-level name — every handle is a local inside
# _tick. Retires off the same unreal._MT_DRIVE_GEN kill-switch mrq_render bumps when the render ends.
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

# Derived, NOT one machine's absolute path. A hardcoded checkout path in a committed driver makes the
# shot un-re-renderable on any other machine — the precise repeatability defect a re-renderable shot
# must not have. A constant of exactly this shape was deleted from mrq_render.py, and
# pie_army_pullback_drive.py derives its conductor path the same way.
REPO = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()).rstrip('/')
RIFLE = '/Game/MT/Weapons/SM_SovietRifle'
AXE_BONES = ('weapon_l', 'weapon_r')       # Khaimera's Paragon axe chains; absent on Manny/Quinn

# Solved by Scripts/solve_weapon_carry.py. (RelativeLocation, RelativeRotation as (roll, pitch, yaw)
# — note unreal.Rotator's constructor order, which is NOT the (pitch, yaw, roll) print order.)
# RE-SOLVED against the RIFLE-MODE pose, by Scripts/solve_weapon_carry.py (pose-derived: barrel along the live hand_r -> hand_l).
#
# The previous values were solved against the UNARMED idle, because that is what the graph produced: this
# file's header explains that the foregrip sat at 99% of the left arm's reach there, so an honest grip was
# unreachable and the rifle was tilted to a chosen 45/30 port-arms diagonal to meet the body. With the
# rifle-mode branch built, the character is in MF_Rifle_Idle_ADS from spine_01 up and the hands are already
# placed to hold a weapon — so the barrel direction is now MEASURED (hand_r -> hand_l) instead of chosen,
# and the grip lands on hand_r to 0.0000 cm on both rigs.
#
# THE RESIDUAL, STATED BECAUSE IT IS VISIBLE. The rifle is longer than this pose's hand separation:
# grip->foregrip is 40.87 cm against 32.51 cm of hand span on Manny/Quinn and 29.66 cm on Khaimera. So the
# foregrip SOCKET overshoots hand_l by 8.36 / 11.21 cm. What that means on screen is not a floating gun —
# hand_l lies ON the barrel line between grip and foregrip, so both hands are on the weapon and the off
# hand simply sits short of the foregrip socket, along the handguard. It reads as a two-handed hold.
#
# What it DOES cost is the off-hand reach's IK target: the foregrip socket is at 107% (Manny) / 113% (Khaimera) of the
# left arm's two-bone length, so a TwoBoneIK aimed at that socket cannot reach it and will stall short.
# The off-hand reach numbers this driver measures are therefore expected to change from the cube-era figures, and
# the manifest's caption is re-derived from THIS take's measurement rather than carried over — see the
# shots_manifest row for 07.
CARRY = {
    'Khaimera': ((-13.5648, 1.0629, 7.3053), (0.7429, 4.1362, 83.2943)),
    'default':  ((-14.4431, 3.5190, 4.1850), (12.9242, 12.7375, 72.6582)),
}

# Sampling windows, in driver-timeline game seconds (see pie_aim_hand_ik_drive.py's TIMELINE header).
#
# THE OFF WINDOW MUST SIT ENTIRELY AFTER t=9.5. pie_aim_hand_ik_drive.aim_pitch() STEPS AimPitch 0 -> 0.35
# at t=9.5 (a step, not a ramp), and the ON window runs at 0.35. A window of (9.0, 10.0) straddles that
# step: its first half is a different upper-body pose from the ON window, it reads ~35.7 cm there versus
# ~26.5 cm after, and averaging the two produces an OFF baseline ~4 cm larger than the pose-matched one
# — inflating the apparent effect. That is exactly the kind of number-that-does-not-mean-what-it-says
# this project exists to catch, so the window starts at 9.6 (0.1 s of margin past the step for the aim
# offset to settle) and ends before the IK ramp begins at 10.0. SAMPLE_DT is tightened to 0.05 s so the
# narrower window still yields ~8 samples instead of 2.
#
# "Same pose family" is now CHECKED, not asserted: AimPitch is reported per window as TWO spans — the
# driven value (proves the window is placed on one side of the step) and the read-back (proves the
# AnimInstance actually holds it). Either disagreeing between windows means the comparison is invalid,
# so a future retiming that reintroduces a straddle — or a dropped AimPitch write — shows up in the log.
OFF_WINDOW = (9.6, 10.0)
ON_START = 11.6
SAMPLE_DT = 0.05

# Keep the beats driver's namespace instead of throwing it away, so this script can ask it what it
# INTENDED to write at a given time (aim_pitch/weapon_ik are pure functions of game time).
#
# WHY THAT MATTERS — CALLBACK ORDER MAKES THE READ-BACK ONE TICK STALE. Both scripts register slate
# pre-tick callbacks and this one fires FIRST, so GetAnimVariable here returns what the beats driver
# wrote on the PREVIOUS tick. Measured directly: at t=10.40 the beats driver logged `ik=0.27` while
# this callback read `a=0.05`, which is weapon_ik(10.07) — the prior tick. At the render's frame rate
# one tick is ~16 ms and the lag is immaterial; in a heavily-loaded PIE session ticking at ~3 Hz it is
# 0.33 s and will misattribute samples near a window edge. So windows are bucketed by TIME (which is
# what determines the intended value anyway), and both the intended and the read-back value are logged
# so the lag is visible in the record rather than silently shifting a number.
_BEATS = {'unreal': unreal}
exec(open(REPO + '/Scripts/pie_aim_hand_ik_drive.py').read(), _BEATS)

_S = {'handle': None, 'last': -999.0, 'dressed': set(), 'stats': {}}

# retire_previous, NOT retire_and_claim. This driver SNAPSHOTS the current generation instead of
# claiming a new one: it rides alongside pie_aim_hand_ik_drive (exec'd above) and must retire WITH that
# driver when the render ends, not retire it out from under itself on load.
GEN_AT_REG = int(getattr(unreal, '_MT_DRIVE_GEN', 0))
_mt.retire_previous('_MT_RIFLE_TAKE')


def _read_var(label, name):
    """Read a contract var back off the live AnimInstance.

    WHAT THIS DOES AND DOES NOT PROVE. It confirms the property exists and currently holds the value
    the timeline intended — useful, and it catches a misnamed/missing property. It is NOT the proof
    that the OFF samples are genuinely off: this callback and the writer are two slate pre-tick
    callbacks on the same thread one step apart, which is not real independence, and
    FHandReachesSocketCommand says so about its own equivalent read ("OFF-is-off is enforced by the
    gap gate in IKOn, not this same-tick read" — PieTests.cpp:2779). This driver has no gap gate; it
    logs `gap=` and asserts nothing.

    The proof lives in the DEPENDENT variable's structure instead, and it is stronger than a read-back:
    the reach holds ~26.7 cm flat across the whole OFF window and only collapses once the ramp starts.
    Verbatim from the shipped render's log, every 5th sample of the 0.05 s trace (t: cm @ alpha_set):
    9.98: 26.81 @ 0.00 | 10.23: 24.68 @ 0.15 | 10.48: 21.33 @ 0.32 | 10.73: 16.42 @ 0.48 |
    10.98: 11.65 @ 0.65 | 11.23: 6.42 @ 0.82 | 11.48: 1.02 @ 0.98 | 11.53: 0.13 @ 1.00.
    Monotone in the gate, with no motion before it opens. A secretly re-derived
    alpha would have pulled the hand onto the foregrip during the OFF window and flattened that
    structure. Structurally, WeaponIKAlpha also has exactly one consumer in the committed graph and was
    authored pure-external precisely so nothing re-derives it.
    """
    try:
        raw = _mt.anim_debug_tools().call_method('GetAnimVariable', args=(label, name))
        return float(str(raw).split('=')[-1])
    except Exception:
        return None


def _tick(dt):
    try:
        if int(getattr(unreal, '_MT_DRIVE_GEN', 0)) != GEN_AT_REG:
            if _S['handle'] is not None:
                unreal.unregister_slate_pre_tick_callback(_S['handle'])
                _S['handle'] = None
            return
        world = unreal.EditorLevelLibrary.get_game_world()
        if not world:
            return
        now = unreal.SystemLibrary.get_game_time_in_seconds(world)
        due = (now - _S['last']) >= SAMPLE_DT
        report = []

        for a in unreal.GameplayStatics.get_all_actors_of_class(world, unreal.Character):
            mesh = a.get_component_by_class(unreal.SkeletalMeshComponent)
            inst = mesh.get_anim_instance() if mesh else None
            if not inst or 'ABP_MT' not in inst.get_class().get_name():
                continue
            lbl = a.get_actor_label()
            wpn = None
            for c in a.get_components_by_tag(unreal.StaticMeshComponent, unreal.Name('MT_Weapon')):
                wpn = c
            if not wpn:
                report.append('%s:NO_WEAPON' % lbl)
                continue

            if lbl not in _S['dressed']:
                _S['dressed'].add(lbl)
                key = 'Khaimera' if 'Khaimera' in a.get_class().get_name() else 'default'
                loc, rot = CARRY[key]
                wpn.set_static_mesh(unreal.load_asset(RIFLE))
                wpn.set_relative_scale3d(unreal.Vector(1.0, 1.0, 1.0))
                wpn.set_relative_location(unreal.Vector(*loc), False, False)
                wpn.set_relative_rotation(unreal.Rotator(*rot), False, False)
                hidden = [b for b in AXE_BONES if mesh.get_bone_index(b) != -1]
                for b in hidden:
                    mesh.hide_bone_by_name(b, unreal.PhysBodyOp.PBO_NONE)
                unreal.log('RIFLETAKE dressed %s carry=%s mesh=%s socket_ok=%s hid_bones=%s'
                           % (lbl, key, wpn.static_mesh.get_name() if wpn.static_mesh else None,
                              wpn.does_socket_exist('IK_Hand_Gun'), hidden or 'none'))

            if not due:
                continue
            # Both fallbacks are silent: UStaticMeshComponent::GetSocketLocation returns the COMPONENT
            # ORIGIN for an unknown socket (which would coincide with the IK effector and read a false
            # ~0 cm), and USkeletalMeshComponent::GetSocketLocation returns the COMPONENT location for
            # an unknown bone. Guard BOTH ends of the measurement, not just the weapon end.
            if not wpn.does_socket_exist('IK_Hand_Gun'):
                report.append('%s:SOCKET_MISSING' % lbl)
                continue
            if mesh.get_bone_index('hand_l') == -1:
                report.append('%s:HAND_BONE_MISSING' % lbl)
                continue
            h = mesh.get_socket_location('hand_l')
            s = wpn.get_socket_location('IK_Hand_Gun')
            d = ((h.x - s.x) ** 2 + (h.y - s.y) ** 2 + (h.z - s.z) ** 2) ** 0.5
            al = _read_var(lbl, 'WeaponIKAlpha')      # read-back, ONE TICK STALE (see exec note above)
            ap = _read_var(lbl, 'AimPitch')
            al_set = _BEATS['weapon_ik'](now)          # what the beats driver drives at this instant
            ap_set = _BEATS['aim_pitch'](now)

            # BOTH AimPitch spans are aggregated, and they answer different questions:
            #   ap_set — the timeline's driven value. Its span proves the WINDOW IS PLACED correctly
            #            (entirely on one side of the t=9.5 step), which is what the OFF/ON comparison
            #            needs. It is a pure function of sample times and cannot detect a bad write.
            #   ap_read — what the AnimInstance actually holds. Its span is the only thing here that
            #            would catch AimPitch failing to reach 0.35 (a dropped/misnamed write), which
            #            would silently make the two windows different poses again.
            # Printing only the first and calling it a read-back would be mislabelled; print both.
            st = _S['stats'].setdefault(lbl, {'off': [], 'off_aim': [], 'off_aim_read': [],
                                              'on_min': 1e9, 'on_last': None, 'on_n': 0,
                                              'on_aim': [], 'on_aim_read': [],
                                              'off_alpha_bad': 0, 'on_alpha_bad': 0})
            if OFF_WINDOW[0] <= now < OFF_WINDOW[1]:
                st['off'].append(d)
                st['off_aim'].append(ap_set)
                if ap is not None:
                    st['off_aim_read'].append(ap)
                # Both the driven value and the (stale) read-back must agree the gate is off.
                if al_set != 0.0 or al is None or abs(al) > 0.01:
                    st['off_alpha_bad'] += 1
            elif now >= ON_START:
                st['on_min'] = min(st['on_min'], d)
                st['on_last'] = d
                st['on_n'] += 1
                st['on_aim'].append(ap_set)
                if ap is not None:
                    st['on_aim_read'].append(ap)
                if al_set != 1.0 or al is None or abs(al - 1.0) > 0.01:
                    st['on_alpha_bad'] += 1
            report.append('%s:%.2f(a_set=%.2f,a_read=%s,ap_set=%.2f,ap_read=%s)'
                          % (lbl, d, al_set, ('%.2f' % al) if al is not None else 'NA',
                             ap_set, ('%.2f' % ap) if ap is not None else 'NA'))

        if due:
            _S['last'] = now
            unreal.log('RIFLETAKE t=%.2f handL_to_foregrip_cm %s' % (now, ' | '.join(report)))
            if now >= ON_START:
                for lbl, st in sorted(_S['stats'].items()):
                    if not st['off'] or st['on_last'] is None:
                        continue
                    off = sum(st['off']) / len(st['off'])
                    # Report the AimPitch each window actually ran at, driven AND read back. If the two
                    # windows' spans disagree the windows are NOT pose-matched and distOff is not
                    # comparable to distOn — the exact defect a (9.0, 10.0) window introduced by
                    # straddling the t=9.5 AimPitch step.
                    def _span(vals):
                        return ('%.2f..%.2f' % (min(vals), max(vals))) if vals else 'NA'
                    unreal.log('RIFLETAKE REACH %s distOff=%.2f (n=%d, AimPitch set %s read %s) '
                               'distOn(min)=%.2f last=%.2f (n=%d, AimPitch set %s read %s) gap=%.2f '
                               'alpha_gate_violations off=%d on=%d'
                               % (lbl, off, len(st['off']), _span(st['off_aim']),
                                  _span(st['off_aim_read']), st['on_min'], st['on_last'], st['on_n'],
                                  _span(st['on_aim']), _span(st['on_aim_read']),
                                  off - st['on_min'], st['off_alpha_bad'], st['on_alpha_bad']))
    except Exception as e:
        unreal.log_warning('RIFLETAKE err %s' % e)


_S['handle'] = unreal.register_slate_pre_tick_callback(_tick)
unreal._MT_RIFLE_TAKE = _S
unreal.log('RIFLETAKE rifle driver registered gen=%d (beats driver exec\'d; swap+measure armed)' % GEN_AT_REG)
