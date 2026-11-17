# PIE SceneCapture driver for the A/B foot-IK hero capture.
# This editor launch has NO rendering level viewport (get_viewport_info -> 0x0), so the
# PIE-viewport screen-grab only yields black frames.
# A SceneCaptureComponent2D renders the LIVE PIE world to a render target independent of any
# viewport, so it works headless. Runs inside a PIE session as a probe script. Caller prepends:
#   TAKE = 'A' | 'B'          # 'A' = IK off (runtime-swap AnimClass to Tmp_MT_Locomotion_IKOff)
#   OUTDIR = r'...'           # frame output dir
#   INTERVAL = 0.2            # seconds between exported frames (optional)
# Movement = sustained add_movement_input (proven driver). A persistent SceneCapture2D named
# 'MT_SceneCap' (placed in the editor level, cloned into PIE) is aimed statically at the stairs.
import os

import unreal

IKOFF_CLASS = '/Game/MT/Tmp_MT_Locomotion_IKOff.Tmp_MT_Locomotion_IKOff_C'
_G = {'swapped': False, 'handle': None, 'rt': None, 'cap': None,
      'idx': 0, 'last': -999.0, 'configured': False}

try:
    _TAKE = TAKE  # noqa: F821
except NameError:
    _TAKE = 'B'
try:
    _OUTDIR = OUTDIR  # noqa: F821
except NameError:
    # Derived from the project dir, not a hard-coded machine path.
    _OUTDIR = os.path.join(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()),
                           'captures', 'm3-footik', '_scratch').replace('\\', '/')
try:
    _INTERVAL = float(INTERVAL)  # noqa: F821
except (NameError, ValueError):
    _INTERVAL = 0.2

# static side framing of the stairs (X 1363-1723, Z 205-305)
_CAM_LOC = unreal.Vector(1543.0, -650.0, 340.0)
_CAM_ROT = unreal.Rotator(-10.0, 90.0, 0.0)
_FOV = 40.0


def _find(w, label):
    for a in unreal.GameplayStatics.get_all_actors_of_class(w, unreal.Actor):
        try:
            if a.get_actor_label() == label:
                return a
        except Exception:
            pass
    return None


def _tick(dt):
    try:
        w = unreal.EditorLevelLibrary.get_game_world()
        if _G['swapped'] and not w:
            if _G['handle'] is not None:
                unreal.unregister_slate_pre_tick_callback(_G['handle'])
                _G['handle'] = None
            _G['rt'] = None
            _G['cap'] = None
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

        if not _G['swapped']:
            if _TAKE == 'A':
                mesh = pawn.get_component_by_class(unreal.SkeletalMeshComponent)
                if mesh:
                    mesh.set_anim_instance_class(unreal.load_class(None, IKOFF_CLASS))
                    unreal.log('SCAPDRIVE take=A AnimClass -> Tmp_MT_Locomotion_IKOff (IK OFF)')
            else:
                unreal.log('SCAPDRIVE take=B shipped ABP_MT_Locomotion (IK ON)')
            _G['swapped'] = True

        if not _G['configured']:
            scap = _find(w, 'MT_SceneCap')
            if scap is None:
                return
            comp = scap.get_component_by_class(unreal.SceneCaptureComponent2D)
            scap.set_actor_location_and_rotation(_CAM_LOC, _CAM_ROT, False, False)
            comp.set_editor_property('fov_angle', _FOV)
            comp.set_editor_property('capture_source', unreal.SceneCaptureSource.SCS_FINAL_COLOR_LDR)
            rt = unreal.RenderingLibrary.create_render_target2d(w, 960, 540, unreal.TextureRenderTargetFormat.RTF_RGBA8)
            comp.set_editor_property('texture_target', rt)
            _G['cap'] = comp
            _G['rt'] = rt
            _G['configured'] = True
            unreal.log('SCAPDRIVE scenecapture configured')

        now = unreal.SystemLibrary.get_game_time_in_seconds(w)
        if _G['cap'] and _G['rt'] and (now - _G['last']) >= _INTERVAL:
            _G['cap'].capture_scene()
            name = 'f_%03d.png' % _G['idx']
            unreal.RenderingLibrary.export_render_target(w, _G['rt'], _OUTDIR, name)
            px = pawn.get_actor_location().x
            unreal.log('SCAPDRIVE exported %s pawnX=%.1f t=%.2f' % (name, px, now))
            _G['idx'] += 1
            _G['last'] = now
    except Exception as e:
        unreal.log_warning('SCAPDRIVE err %s' % e)


_G['handle'] = unreal.register_slate_pre_tick_callback(_tick)
unreal.log('SCAPDRIVE registered take=%s outdir=%s' % (_TAKE, _OUTDIR))
