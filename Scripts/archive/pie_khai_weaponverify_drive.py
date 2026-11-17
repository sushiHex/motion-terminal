# Khaimera weapon + arm-chain verify probe (Step 1 read-back + Step 2 arm-chain resolve).
# Confirms, in a live PIE session on the Khaimera pawn:
#   - the MT_Weapon-tagged StaticMeshComponent tracks hand_r (d(weaponWorld, hand_r) ~ 0),
#   - IK_Hand_Gun socket resolves on the weapon (does_socket_exist true) ~15cm out of hand_r,
#   - the off-hand IK chain bones (clavicle_l/upperarm_l/lowerarm_l/hand_l) resolve to DISTINCT,
#     non-origin world positions on Khaimera's render mesh (a collapsed/missing bone would read the
#     component origin -> the arm chain would not resolve and the TwoBoneIK could not solve).
#   - baseline off-hand reach d(hand_l, IK_Hand_Gun) with NO IK driving (the OFF reference).
import unreal

_G = {'handle': None, 'last': -999.0}
INTERVAL = 0.25
START = 1.2

if _G['handle'] is not None:
    unreal.unregister_slate_pre_tick_callback(_G['handle'])
    _G['handle'] = None


def _dist(a, b):
    dx = a.x - b.x
    dy = a.y - b.y
    dz = a.z - b.z
    return (dx * dx + dy * dy + dz * dz) ** 0.5


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
        now = unreal.SystemLibrary.get_game_time_in_seconds(w)
        if now < START:
            return
        if (now - _G['last']) < INTERVAL:
            return
        _G['last'] = now

        wpn = None
        for c in pawn.get_components_by_tag(unreal.StaticMeshComponent, unreal.Name('MT_Weapon')):
            wpn = c
            break
        if not wpn:
            unreal.log_warning('WVERIFY no MT_Weapon-tagged StaticMeshComponent on the pawn')
            return

        comp_origin = mesh.get_world_location()
        hand_r = mesh.get_socket_location('hand_r')
        hand_l = mesh.get_socket_location('hand_l')
        lower_l = mesh.get_socket_location('lowerarm_l')
        upper_l = mesh.get_socket_location('upperarm_l')
        wpn_loc = wpn.get_world_location()
        sock_exists = wpn.does_socket_exist('IK_Hand_Gun')
        sock = wpn.get_socket_location('IK_Hand_Gun')

        d_wpn_handR = _dist(wpn_loc, hand_r)
        d_handR_sock = _dist(hand_r, sock)
        d_handL_sock = _dist(hand_l, sock)
        # arm-chain resolution: each off-hand bone must be a distinct, non-origin world point
        d_upper_from_origin = _dist(upper_l, comp_origin)
        d_lower_upper = _dist(lower_l, upper_l)
        d_hand_lower = _dist(hand_l, lower_l)
        skm = mesh.get_skeletal_mesh_asset()
        unreal.log('WVERIFY mesh=%s d(wpn,handR)=%.2f IK_Hand_Gun_exists=%s d(handR,sock)=%.2f '
                   'armchain[d(upperarm_l,origin)=%.1f d(lowerarm_l,upperarm_l)=%.1f d(hand_l,lowerarm_l)=%.1f] '
                   'OFFbaseline d(hand_l,sock)=%.2f'
                   % (skm.get_name() if skm else '?', d_wpn_handR, sock_exists, d_handR_sock,
                      d_upper_from_origin, d_lower_upper, d_hand_lower, d_handL_sock))
    except Exception as e:
        unreal.log_warning('WVERIFY err %s' % e)


_G['handle'] = unreal.register_slate_pre_tick_callback(_tick)
unreal.log('WVERIFY registered (Khaimera weapon + arm-chain resolve)')
