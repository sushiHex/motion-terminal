# PIE foot-clearance logger for the A/B foot-IK proof (rendering-independent, confound-free).
# Compares IK off (A) vs IK on (B) by the foot's clearance relative to the ground DIRECTLY beneath
# it (downward raycast), which removes the walk-phase / capsule-step-snap confounds that plague a
# raw matched-px foot-Z comparison. Penetration (clearance < 0) through a rising step/ramp is the
# signature of IK-off; IK-on should conform the planted foot to the surface (clearance ~0, clamped).
# Runs inside a PIE session as a probe script. Caller prepends: TAKE = 'A' | 'B'. Fine sampling (~0.05s).
import unreal

IKOFF_CLASS = '/Game/MT/Tmp_MT_Locomotion_IKOff.Tmp_MT_Locomotion_IKOff_C'
_G = {'swapped': False, 'handle': None, 'last': -999.0}
INTERVAL = 0.05

try:
    _TAKE = TAKE  # noqa: F821
except NameError:
    _TAKE = 'B'


def _ground_z(w, loc, ignore):
    start = unreal.Vector(loc.x, loc.y, loc.z + 60.0)
    end = unreal.Vector(loc.x, loc.y, loc.z - 120.0)
    hit = unreal.SystemLibrary.line_trace_single(
        w, start, end, unreal.TraceTypeQuery.TRACE_TYPE_QUERY1, False, ignore,
        unreal.DrawDebugTrace.NONE, True)
    if hit:
        try:
            return hit.to_tuple()[4].z  # impact_point.z
        except Exception:
            return hit.impact_point.z
    return None


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
                unreal.log('FOOTC take=A AnimClass -> Tmp_MT_Locomotion_IKOff (IK OFF)')
            else:
                unreal.log('FOOTC take=B shipped ABP_MT_Locomotion (IK ON)')
            _G['swapped'] = True
        now = unreal.SystemLibrary.get_game_time_in_seconds(w)
        if (now - _G['last']) < INTERVAL:
            return
        _G['last'] = now
        p = pawn.get_actor_location()
        bl = mesh.get_socket_location('ball_l')
        br = mesh.get_socket_location('ball_r')
        gl = _ground_z(w, bl, [pawn])
        gr = _ground_z(w, br, [pawn])
        cl = (bl.z - gl) if gl is not None else 999.0
        cr = (br.z - gr) if gr is not None else 999.0
        falling = pawn.get_movement_component().is_falling()
        unreal.log('FOOTC %s px=%.1f blz=%.2f gl=%.2f clrL=%.2f brz=%.2f gr=%.2f clrR=%.2f fall=%d'
                   % (_TAKE, p.x, bl.z, (gl if gl is not None else -999), cl,
                      br.z, (gr if gr is not None else -999), cr, 1 if falling else 0))
    except Exception as e:
        unreal.log_warning('FOOTC err %s' % e)


_G['handle'] = unreal.register_slate_pre_tick_callback(_tick)
unreal.log('FOOTC registered take=%s' % _TAKE)
