# PIE foot-bone logger for the A/B foot-IK proof (rendering-independent).
# This editor launch is viewport-less (get_viewport_info -> 0x0) so visual capture of an
# arbitrary camera renders black. The gate's effect is a FOOT-POSE change, so we prove A vs B
# by reading foot-bone WORLD Z at matched pawn-X marks. IK off (A) = feet at raw anim pose
# (penetrate/float through the stair nosings); IK on (B) = feet conformed to the step surface.
# Runs inside a PIE session as a probe script. Caller prepends: TAKE = 'A' | 'B'.
# Movement = sustained add_movement_input (proven driver). Logs one FOOTLOG line per ~0.12s.
import unreal

IKOFF_CLASS = '/Game/MT/Tmp_MT_Locomotion_IKOff.Tmp_MT_Locomotion_IKOff_C'
_G = {'swapped': False, 'handle': None, 'last': -999.0}
INTERVAL = 0.12

try:
    _TAKE = TAKE  # noqa: F821
except NameError:
    _TAKE = 'B'


def _tick(dt):
    try:
        w = unreal.EditorLevelLibrary.get_game_world()
        if _G['swapped'] and not w:
            if _G['handle'] is not None:
                unreal.unregister_slate_pre_tick_callback(_G['handle'])
                _G['handle'] = None
            return
        if not w:
            return
        pc = unreal.GameplayStatics.get_player_controller(w, 0)
        if not pc:
            return
        pawn = pc.get_controlled_pawn()
        if not pawn:
            return
        pawn.add_movement_input(unreal.Vector(1, 0, 0), 1.0, False)
        mesh = pawn.get_component_by_class(unreal.SkeletalMeshComponent)
        if not _G['swapped']:
            if _TAKE == 'A' and mesh:
                mesh.set_anim_instance_class(unreal.load_class(None, IKOFF_CLASS))
                unreal.log('FOOTLOG take=A AnimClass -> Tmp_MT_Locomotion_IKOff (IK OFF)')
            else:
                unreal.log('FOOTLOG take=B shipped ABP_MT_Locomotion (IK ON)')
            _G['swapped'] = True
        now = unreal.SystemLibrary.get_game_time_in_seconds(w)
        if (now - _G['last']) < INTERVAL:
            return
        _G['last'] = now
        p = pawn.get_actor_location()
        fl = mesh.get_socket_location('foot_l')
        fr = mesh.get_socket_location('foot_r')
        bl = mesh.get_socket_location('ball_l')
        br = mesh.get_socket_location('ball_r')
        falling = pc.get_controlled_pawn().get_movement_component().is_falling()
        unreal.log('FOOTLOG %s px=%.1f pz=%.1f flz=%.2f frz=%.2f blz=%.2f brz=%.2f fall=%d'
                   % (_TAKE, p.x, p.z, fl.z, fr.z, bl.z, br.z, 1 if falling else 0))
    except Exception as e:
        unreal.log_warning('FOOTLOG err %s' % e)


_G['handle'] = unreal.register_slate_pre_tick_callback(_tick)
unreal.log('FOOTLOG registered take=%s' % _TAKE)
