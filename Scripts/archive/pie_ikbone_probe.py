# One-off probe: does CR_Mannequin_FootIK (Alpha=1, take B) move the DEFORM foot bones (foot_l/_r),
# the IK bones (ik_foot_l/_r), or nothing, vs take A (Alpha=0)? Sampled on the ramp where the flat-walk
# anim foot sits above the rising slope (max opportunity for a ground-conforming IK to act).
# PIE probe script; caller prepends TAKE = 'A' | 'B'.
import unreal

IKOFF_CLASS = '/Game/MT/Tmp_MT_Locomotion_IKOff.Tmp_MT_Locomotion_IKOff_C'
_G = {'swapped': False, 'handle': None, 'last': -999.0}
INTERVAL = 0.15
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
            _G['swapped'] = True
        now = unreal.SystemLibrary.get_game_time_in_seconds(w)
        if (now - _G['last']) < INTERVAL:
            return
        _G['last'] = now
        p = pawn.get_actor_location()
        if not (500.0 <= p.x <= 1050.0):
            return

        def bz(n):
            try:
                return mesh.get_socket_location(n).z
            except Exception:
                return -999.0
        unreal.log('IKBONE %s px=%.1f foot_l=%.2f ik_foot_l=%.2f foot_r=%.2f ik_foot_r=%.2f'
                   % (_TAKE, p.x, bz('foot_l'), bz('ik_foot_l'), bz('foot_r'), bz('ik_foot_r')))
    except Exception as e:
        unreal.log_warning('IKBONE err %s' % e)


_G['handle'] = unreal.register_slate_pre_tick_callback(_tick)
unreal.log('IKBONE registered take=%s' % _TAKE)
