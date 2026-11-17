# PIE capture driver for the A/B foot-IK hero capture.
# Runs inside a PIE session as a capture script. Expects a global TAKE = 'A' or 'B'
# (prepended by the caller before exec). TAKE 'A' = IK off (runtime-swap the pawn's
# AnimClass to the throwaway Tmp_MT_Locomotion_IKOff, Alpha forced 0); 'B' = IK on
# (shipped ABP_MT_Locomotion unmodified). Movement = sustained add_movement_input via a
# Slate pre-tick callback (the proven PIE movement driver). A persistent CameraActor named
# 'MT_SideCam' (placed in the editor level, cloned into the PIE world) is repositioned each
# tick to a side-tracking view of the pawn; the caller passes view_target_actor=MT_SideCam
# so captured frames render through it. BP_MT_TestPawn is never edited on disk.
import unreal

IKOFF_CLASS = '/Game/MT/Tmp_MT_Locomotion_IKOff.Tmp_MT_Locomotion_IKOff_C'
_G = {'swapped': False, 'handle': None}

try:
    _TAKE = TAKE  # noqa: F821  (injected by caller)
except NameError:
    _TAKE = 'B'


def _find_cam(w):
    for a in unreal.GameplayStatics.get_all_actors_of_class(w, unreal.CameraActor):
        try:
            if a.get_actor_label() == 'MT_SideCam':
                return a
        except Exception:
            pass
    return None


def _tick(dt):
    try:
        w = unreal.EditorLevelLibrary.get_game_world()
        # teardown: PIE world gone after having run -> unregister (GC-safe)
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

        # 1) sustained forward input FIRST (never gated by camera/setup failures)
        pawn.add_movement_input(unreal.Vector(1, 0, 0), 1.0, False)

        # optional: pitch the auto-player's own view down to frame the feet/ground.
        # (The pawn's default view is the only view that renders in this viewport-less editor.)
        ld = globals().get('LOOKDOWN')
        if ld is not None:
            pc.set_control_rotation(unreal.Rotator(float(ld), 0.0, 0.0))

        # 2) one-time AnimClass swap for take A (IK off)
        if not _G['swapped']:
            if _TAKE == 'A':
                mesh = pawn.get_component_by_class(unreal.SkeletalMeshComponent)
                if mesh:
                    mesh.set_anim_instance_class(unreal.load_class(None, IKOFF_CLASS))
                    unreal.log('PIEDRIVE take=A swapped AnimClass -> Tmp_MT_Locomotion_IKOff (IK OFF)')
            else:
                unreal.log('PIEDRIVE take=B shipped ABP_MT_Locomotion (IK ON)')
            _G['swapped'] = True

        # 3) side-tracking camera: reposition the cloned MT_SideCam to follow the pawn.
        #    Skipped when NOTRACK is set (static camera framing a fixed mark).
        if not globals().get('NOTRACK'):
            cam = _find_cam(w)
            if cam:
                p = pawn.get_actor_location()
                cam.set_actor_location(unreal.Vector(p.x - 40.0, p.y - 450.0, p.z + 30.0), False, False)
                cam.set_actor_rotation(unreal.Rotator(-8.0, 90.0, 0.0), False)
    except Exception as e:
        unreal.log_warning('PIEDRIVE err %s' % e)


_G['handle'] = unreal.register_slate_pre_tick_callback(_tick)
unreal.log('PIEDRIVE registered take=%s' % _TAKE)
