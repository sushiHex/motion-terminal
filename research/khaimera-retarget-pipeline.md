# Manny→Khaimera Retarget + Visual-Capture Pipeline — findings & recipes

**Engine:** UE 5.8 (CL 55116800) · **Context:** retargeting one anim set onto a genuinely different 198-bone Paragon rig + producing a motion visual of it. This corrects an earlier claim that the retarget "PLAYS" — **it did not; the bake was silently static.**

## TL;DR / the core lesson

**Never claim an animation clip "plays" from a single rendered frame. Verify it VARIES over time at the DATA level first.** A retarget/bake can produce N frames that are all the identical pose; a render or one screenshot cannot distinguish that from real motion. The cheap authoritative check:

```python
import unreal
for t in (0.0, 0.3, 0.6, 0.9, 1.2):
    xf = unreal.AnimationLibrary.get_bone_pose_for_time(anim, unreal.Name('foot_r'), t, False)
    print(t, xf.rotation.euler())   # if a limb bone is identical across t -> the clip is FROZEN
```

## 1. The retarget was silently static

`batch_retarget_animations` produced `Khai_MF_Unarmed_Jog_Fwd` with 53 frames — **all the same pose**. The source Manny jog varied (`foot_r` rot X `17→-6→26→-2→30`); the Khaimera output was constant (`10,-4,-6` at every t). The "rendered: Khaimera posed by the jog" evidence was **frame-0 only**. Two underlying data bugs + a tool bug.

## 2. Root-cause data bugs (both must be fixed)

**Bug A — FK Chains op had an empty `chains_to_retarget`.** The retargeter's FK op carried a `ChainMapping` (5 pairs) but zero *active per-chain settings*, so it retargeted no chains → the target held its retarget pose on every frame. `get_retarget_chain_settings` returned `fk_chains: []`; `set_retarget_chain_settings` failed with *"Target chain not found in the FK Chains op."*

```python
c = unreal.IKRetargeterController.get_controller(unreal.load_asset(RTG))
c.run_op_initial_setup(fk_op_index)   # rebuilds chains_to_retarget from the mapping (0 -> 5)
# verify: c.get_op_controller(fk_op_index).get_settings().get_editor_property('chains_to_retarget')
#         -> 5 entries, each enable_fk=True, rotation_mode=INTERPOLATED, rotation_alpha=1.0
```

**Bug B — the target IK Rig had NO retarget root.** `get_ikrig_info(IK_Khaimera)` → `pelvis_bone: "None"` (IK_Manny had `pelvis`). With no target pelvis to resolve chains against, the retarget processor null-derefs.

```python
unreal.IKRigController.get_controller(unreal.load_asset(IK_KHAIMERA)).set_retarget_root('pelvis')
```

**⚠️ Distinction that cost time:** The **Pelvis Motion OP's** `TargetPelvisBone` is a *different field* from the **rig's** retarget root (`IKRigController.set_retarget_root`). Both must be set; setting only the op's pelvis (as the original retarget setup did) leaves the rig root `None`.

## 3. Bake through Epic's native batch retarget

**Bake through Epic's own guarded API (Python):**

```python
inp = unreal.IKRetargetBatchOperationInputs()
inp.set_editor_property('assets_to_retarget', [unreal.EditorAssetLibrary.find_asset_data(ANIM)])  # Array[AssetData]
inp.set_editor_property('source_mesh', unreal.load_asset(SRC_MESH))
inp.set_editor_property('target_mesh', unreal.load_asset(TGT_MESH))
inp.set_editor_property('ik_retarget_asset', unreal.load_asset(RTG))
inp.set_editor_property('prefix', 'Khai_'); inp.set_editor_property('target_path', '/Game/Spike/Retargeted')
inp.set_editor_property('overwrite_existing_files', True)
res = unreal.IKRetargetBatchOperation.run_batch_retarget(inp)   # -> Array[AssetData]
```

After the fixes, the output varies correctly: Khaimera `foot_r` rot X `27→4→37→8→40` tracks Manny's `17→-6→26→-2→30` (same stride rhythm, retarget-pose offset). `duplicate_and_retarget` is the deprecated sibling; use `run_batch_retarget`.

## 4. Use live PIE for motion

A real PIE session advances the game loop, so a viewport frame grabbed every capture interval shows genuine per-frame motion. Discard the first 2-3 frames, which cover the skinned-mesh warm-up (often black).

## 5. Display-level recipe (the PIE capture subject)

Build a persistent level with the characters auto-playing, then capture it in live PIE.

- **SkeletalMeshActor + SingleNode auto-play:** set `animation_mode = ANIMATION_SINGLE_NODE`, then on the component's `animation_data` (FSingleAnimationPlayData) set `anim_to_play`, **`saved_looping=True`, `saved_playing=True`** (NOT `looping`/`playing` — those property names don't exist). These serialize so the mesh plays in PIE.
- **Lighting:** movable DirectionalLight (`atmosphere_sun_light=True`) + SkyLight (`real_time_capture=True`) + SkyAtmosphere + a floor. Access light components via `actor.get_component_by_class(unreal.DirectionalLightComponent)` (NOT `actor.directional_light_component`). **Exactly one** DirectionalLight or PIE prints a "Multiple directional lights competing" on-screen warning.
- **`les.new_level(path)` on an existing level RELOADS it** (doesn't wipe) → destroy leftover actors for a clean slate.
- **☠️ `unreal.Rotator(roll, pitch, yaw)`** — roll is FIRST. `Rotator(-6, 180, 0)` sets pitch=180 (camera upside-down). Use keyword args or `unreal.MathLibrary.find_look_at_rotation(cam_loc, target)`.
- `set_actor_relative_location/rotation(vec, sweep, teleport)` — the sweep/teleport bools are required positional args.

## 6. In-place jog (so a fixed camera holds the framing)

The jog is forward-locomotion (root motion), so in PIE the mesh travels and drifts out of frame. To pin it in place:

- **`force_root_lock=True` on the clip locks the ROOT bone** → stops root-driven travel. Worked for Manny.
- **But the retargeter's Pelvis Motion op also translates the PELVIS** to follow the source → Khaimera still drifted with `force_root_lock` alone. Fix: `set_retarget_root_settings(scale_horizontal=0)` (kills horizontal pelvis translation, keeps vertical bob) **and re-bake** a display clip. Restore `scale_horizontal=1` afterward so the canonical RTG keeps gameplay root motion.
- Duplicate display clips (`EditorAssetLibrary.duplicate_asset`) + set `force_root_lock=True`, `enable_root_motion=False` — keep them under a gitignored path (they reference Khaimera).

## 7. Follow-camera finding (why a true chase-cam is hard here)

A **`SkeletalMeshActor` does not move as an actor** — SingleNode root motion animates the mesh/bones in component space; the actor transform stays at spawn. So:
- Attaching a camera to the **actor** → no follow (actor never moves).
- Attaching to the mesh **`root` bone** → follows the translation BUT a child inherits the bone's **rotation**, and the retargeted root bone's rotation isn't stable → erratic framing.
- A genuine travelling chase-cam needs the characters rigged as **movement-driven `ACharacter`s** (AnimBP + CharacterMovement consuming root motion), then a SpringArm/attached cam follows cleanly. Bigger setup — deferred.
- **Delivered instead:** root-locked in-place jog + a fixed front-¾ camera → both characters held centred, loops seamlessly (a cleaner capture than a shaky chase). Output: `captures/khaimera-retarget/pie_inplace2/khaimera_manny_inplace_front.gif`.

## 8. Editor crash / relaunch recipe

If the editor dies, relaunch (launcher build) without the Epic Launcher UI:

```powershell
Start-Process 'C:\Program Files\Epic Games\UE_5.8\Engine\Binaries\Win64\UnrealEditor.exe' `
  -ArgumentList '"<repo>\MotionTerminal.uproject"'
# then wait for the engine's MCP server on :8000 to accept a TCP connection (~2-4 min load).
```

`unreal-mcp` (:8000) does not auto-reconnect after an editor death → the MCP client must reconnect it by hand. `CrashReportClientEditor` is spawned at launch as the out-of-process crash monitor — its presence ≠ a crash; don't kill it.

## 9. Framerate — PIE capture caps ~3fps; render a Level Sequence offline for 30fps

A viewport screen-grab captures at only **~3fps** (frames ~0.33s apart) **regardless of `capture_interval`** (floored at 0.05 anyway) and regardless of the Slate throttle CVars (`Slate.bAllowThrottling 0`, `t.MaxFPS 0` — no effect). Every PIE-captured GIF is therefore choppy. For true **30fps** output, render a **Level Sequence offline** — deterministic per-frame, framerate-exact, not real-time-limited:

- **Build the sequence:** `LevelSequenceFactoryNew`; `set_display_rate(unreal.FrameRate(30,1))` + `set_playback_end_seconds(loop)`; add the camera as a possessable + a `MovieSceneCameraCutTrack`, bind it via **`unreal.MovieSceneSequenceExtensions.get_binding_id(seq, binding)`** (`MovieSceneBindingProxy` exposes a `binding_id` *property*, NOT a `get_binding_id()` method; the `MovieSceneObjectBindingID()` ctor takes no args). Add a `MovieSceneSkeletalAnimationTrack` per character (`binding.add_track(...)` → `section.params.animation = clip`) so the pose is driven by sequence time.
- **Render:** `unreal.AutomatedLevelSequenceCapture` — **`settings.frame_rate` is READ-ONLY** (inherited from the sequence display rate); set `output_directory`, `output_format='frame.{frame}'`, `resolution` (`unreal.CaptureResolution`), `zero_pad_frame_numbers`, `overwrite_existing`; `cap.set_image_capture_protocol_type(unreal.load_class(None, '/Script/MovieSceneCapture.ImageSequenceProtocol_PNG'))`; then `unreal.SequencerTools.render_movie(cap, unreal.OnRenderMovieStopped(cb))` (deprecated in 5.8 but works; **async** → poll the output dir for the PNG count).
- **Assemble** the PNGs → GIF at **33ms/frame = 30fps**. One 1.77s loop ≈ 54 frames. Output `captures/khaimera-retarget/seq_30fps/khaimera_manny_30fps.gif`. Bonus: the offline render adds proper motion blur (frame 0 is sharp — no prior-frame velocity).

## Appendix — motion verified (data-level, post-fix)

| t (s) | Manny source `foot_r` rot X | Khaimera retarget `foot_r` rot X |
|------|------|------|
| 0.00 | 17 | 27 |
| 0.30 | −6 | 4 |
| 0.60 | 26 | 37 |
| 0.90 | −2 | 8 |
| 1.20 | 30 | 40 |

Same up-down-up-down stride cadence carried onto the 198-bone rig. The spine-chain-length fixup is confirmed on hardware (Manny `spine_01→05` = 5 bones vs Khaimera `spine_01→03` = 3); dodged Khaimera's embedded duplicate `ghostbeast_` rig by using un-prefixed character bones.
