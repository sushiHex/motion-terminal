# Reads the LIVE CR_Mannequin_FootIK rig variables during PIE on the ramp, to determine whether
# ShouldDoIKTrace is propagating true and whether the FootTrace produces any ZOffset.
# PIE probe script; take B (shipped ABP, IK on).
import unreal

_G = {'handle': None, 'last': -999.0, 'done': 0}
INTERVAL = 0.2


def _read_rig(ai):
    # anim node struct is exposed as a property on the generated class
    for pname in ('AnimGraphNode_ControlRig', 'AnimGraphNode_ControlRig_0'):
        try:
            node = ai.get_editor_property(pname)
        except Exception:
            node = None
        if node is None:
            continue
        try:
            rig = node.get_editor_property('ControlRig')
        except Exception as e:
            rig = None
            unreal.log('RIGVAR node %s has no ControlRig prop: %s' % (pname, e))
        return node, rig
    return None, None


def _tick(dt):
    try:
        w = unreal.EditorLevelLibrary.get_game_world()
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
        p = pawn.get_actor_location()
        now = unreal.SystemLibrary.get_game_time_in_seconds(w)
        if (now - _G['last']) < INTERVAL:
            return
        _G['last'] = now
        if not (500.0 <= p.x <= 1000.0):
            return
        ai = mesh.get_anim_instance()
        node, rig = _read_rig(ai)
        if _G['done'] < 1 and rig is not None:
            # one-time dump of what the rig object is + its editor properties
            try:
                props = [str(x) for x in dir(rig)]
            except Exception:
                props = []
            unreal.log('RIGVAR rig=%s class=%s' % (rig, rig.get_class().get_name()))
            _G['done'] = 1
        vals = {}
        for vn in ('ShouldDoIKTrace', 'ZOffset_L', 'ZOffset_R', 'ZOffset_L_Target',
                   'ZOffset_R_Target', 'ZOffset_Pelvis'):
            try:
                vals[vn] = rig.get_editor_property(vn) if rig is not None else 'NORIG'
            except Exception as e:
                vals[vn] = 'ERR:%s' % e
        unreal.log('RIGVAR px=%.1f trace=%s zL=%s zR=%s zLt=%s zRt=%s zP=%s' % (
            p.x, vals.get('ShouldDoIKTrace'), vals.get('ZOffset_L'), vals.get('ZOffset_R'),
            vals.get('ZOffset_L_Target'), vals.get('ZOffset_R_Target'), vals.get('ZOffset_Pelvis')))
    except Exception as e:
        unreal.log_warning('RIGVAR err %s' % e)


_G['handle'] = unreal.register_slate_pre_tick_callback(_tick)
unreal.log('RIGVAR registered')
