# Hands-to-weapon effect-level probe (the off-hand rifle reach). Rendering-independent, confound-free.
# THE load-bearing off-hand reach probe: does the off-hand bone (hand_l) actually REACH the weapon's
# IK_Hand_Gun socket when the Two-Bone IK is ON (WeaponIKAlpha=1) vs the free-swinging off-hand when
# it is proven-OFF (WeaponIKAlpha=0)?
#
# MECHANISM: the ABP_MT_Locomotion TwoBoneIK node (ik_bone=hand_l, effector_space=World, spliced
# Aim -> TwoBoneIK -> ControlRig) drives hand_l to WeaponIKTargetWorld — the weapon component's
# IK_Hand_Gun socket world position, which the EventGraph re-reads each tick (so the target follows the
# socket when it moves). Gated by WeaponIKAlpha (a PURE external var, no EventGraph re-derive -> the OFF
# state is PROVEN off via the get/set read-back — the confound-immunity property). The weapon is a
# StaticMeshComponent tagged MT_Weapon attached to the hand_r bone of the render mesh.
#
# MEASUREMENT (settle -> measure): the distance from the HAND bone (hand_l) world position to the
# weapon SOCK (IK_Hand_Gun) world position, accumulated (min/mean/last) over a stable window.
#   ON  (ALPHA=1): hand_l snaps onto the foregrip -> handToSock below tolerance (steadily small).
#   OFF (ALPHA=0, PROVEN via read-back): the animated off-hand swings free -> handToSock large (tens
#       of cm). The GAP between the two runs IS the measured effect (on reaches, off does not, off
#       proven off).
#
# INJECTION PATH: WeaponIKAlpha is instance_editable:false, so a Python set_editor_property raises
# "cannot be edited on instances". We inject through UAnimDebugTools::SetAnimVariable (raw FProperty
# SetPropertyValue_InContainer — bypasses the edit-on-instance guard) and read back via
# GetAnimVariable — the proven reflection contract. The instance is resolved by the pawn object
# name (FindAnimInstanceByLabel matches GetActorLabel() OR GetName()).
#
# Runs inside a PIE session as a probe (duration >= 8 s). Caller PREPENDS:
#   ALPHA = 1.0        # or 0.0 for the proven-off reference
#   HAND = 'hand_l'
#   SOCK = 'IK_Hand_Gun'
#   exec(open(r'.../Scripts/pie_handreach_drive.py').read())
# then samples LogPython 'HREACH ' lines; the LAST measure-phase line carries the final distance stats.
import unreal

# The shared driver scaffolding. Resolved off the .uproject, NOT off __file__: these drivers are
# exec'd into bare namespaces where __file__ does not exist. Reloaded so an edit to mt_drive.py
# takes effect on the next run - see that module's header.
import importlib
import os
import sys

_MT_SCRIPTS = os.path.join(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()),
                           'Scripts')
if _MT_SCRIPTS not in sys.path:
    sys.path.insert(0, _MT_SCRIPTS)
_mt = importlib.reload(importlib.import_module('mt_drive'))

_G = {'handle': None, 'label': None, 'last': -999.0,
      'min': 1.0e9, 'sum': 0.0, 'n': 0, 'sockchecked': False}
INTERVAL = 0.1
SETTLE_START = 1.6   # game seconds: discard spawn/leftover transients + let the IK blend in before this

try:
    _ALPHA = ALPHA  # noqa: F821  (prepended by the caller)
except NameError:
    _ALPHA = 1.0
try:
    _HAND = HAND    # noqa: F821
except NameError:
    _HAND = 'hand_l'
try:
    _SOCK = SOCK    # noqa: F821
except NameError:
    _SOCK = 'IK_Hand_Gun'


def _dist(a, b):
    # Manual component distance (no reliance on Vector operator overloads across UE Python versions).
    dx = a.x - b.x
    dy = a.y - b.y
    dz = a.z - b.z
    return (dx * dx + dy * dy + dz * dz) ** 0.5


def _weapon_comp(pawn):
    # Resolve the weapon by component tag (robust to name drift — the same tag the EventGraph uses).
    for c in pawn.get_components_by_tag(unreal.StaticMeshComponent, unreal.Name('MT_Weapon')):
        return c
    return None


def _tick(dt):
    try:
        w = unreal.EditorLevelLibrary.get_game_world()
        if not w:
            return
        pc = unreal.GameplayStatics.get_player_controller(w, 0)
        pawn = pc.get_controlled_pawn() if pc else None
        if not pawn:
            return
        mesh = pawn.get_component_by_class(unreal.SkeletalMeshComponent)
        if not mesh:
            return
        if _G['label'] is None:
            _G['label'] = pawn.get_name()

        # Inject the alpha every tick, and PROVE it stuck. verify=True makes one call do write +
        # read-back + compare, which is what this file used to hand-roll in two places 35 lines
        # apart; WeaponIKAlpha is a pure external var, so a read-back is meaningful for it.
        if _mt.set_anim_vars_checked(_G['label'], (('WeaponIKAlpha', float(_ALPHA)),), verify=True) \
                and not _G.get('alpha_unproven'):
            _G['alpha_unproven'] = True
            unreal.log_error('HREACH WeaponIKAlpha DID NOT STICK at %.3f - the OFF/ON isolation this '
                             'probe claims is unproven for this run, so every handToSock number below '
                             'is void, not evidence.' % float(_ALPHA))

        wpn = _weapon_comp(pawn)
        if not wpn:
            unreal.log_warning('HREACH no MT_Weapon-tagged StaticMeshComponent on the pawn')
            return

        # Socket-existence guard (mirrors the C++ FHandReachesSocketCommand Settle guard). get_socket_location
        # returns the COMPONENT ORIGIN — silently — for an unknown socket name, so a regressed IK_Hand_Gun
        # would make this diagnostic measure the weapon-origin fallback (a false ~0cm reach that coincides
        # with the EventGraph effector). Log the present-vs-bogus discrimination once, then refuse to measure
        # if the socket is missing so the instrument can't silently read the fallback either.
        if not _G['sockchecked']:
            _G['sockchecked'] = True
            unreal.log('HREACH socket-guard does_socket_exist(%s)=%s does_socket_exist(__bogus__)=%s (guard discriminates)'
                       % (_SOCK, wpn.does_socket_exist(_SOCK), wpn.does_socket_exist('__bogus__')))
        if not wpn.does_socket_exist(_SOCK):
            unreal.log_warning('HREACH SOCKET-MISSING %s does not exist on the weapon component — '
                               'get_socket_location returns the COMPONENT ORIGIN silently; refusing to measure '
                               'the fallback (would read a false ~0cm reach)' % _SOCK)
            return

        now = unreal.SystemLibrary.get_game_time_in_seconds(w)
        if (now - _G['last']) < INTERVAL:
            return
        _G['last'] = now

        hand = mesh.get_socket_location(_HAND)   # off-hand (hand_l) bone world position
        sock = wpn.get_socket_location(_SOCK)     # weapon foregrip (IK_Hand_Gun) world position
        dist = _dist(hand, sock)
        # Still read it for the log line's setread= field. The ASSERTION now lives in the verified
        # write above, so this read is reporting, not proving - and parse_num rejects NaN/inf, so a
        # non-finite read shows as NA here rather than sneaking through a comparison.
        rd = _mt.parse_num(_mt.anim_debug_tools().call_method(
            'GetAnimVariable', args=(_G['label'], 'WeaponIKAlpha')))

        if now < SETTLE_START:
            unreal.log('HREACH alpha=%.1f setread=%s phase=settle handToSock=%.2f'
                       % (_ALPHA, ('%.2f' % rd) if rd is not None else 'NA', dist))
            return

        _G['min'] = min(_G['min'], dist)
        _G['sum'] += dist
        _G['n'] += 1
        mean = _G['sum'] / _G['n'] if _G['n'] else dist
        unreal.log('HREACH alpha=%.1f setread=%s phase=measure handToSock=%.2f min=%.2f mean=%.2f n=%d'
                   % (_ALPHA, ('%.2f' % rd) if rd is not None else 'NA',
                      dist, _G['min'], mean, _G['n']))
    except Exception as e:
        unreal.log_warning('HREACH err %s' % e)


_G['handle'] = unreal.register_slate_pre_tick_callback(_tick)
unreal.log('HREACH registered alpha=%s hand=%s sock=%s (settle<%.1fs, then measure)'
           % (_ALPHA, _HAND, _SOCK, SETTLE_START))
