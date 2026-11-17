# usdCharacter (usdRig + usdRig_unreal): where it fits MotionTerminal

Question: where the usdCharacter repos and usdRig_unreal can be part of MotionTerminal's pipeline, where it makes sense.
Sources: shallow clones of every repo in github.com/usdCharacter (at the commits pinned below), read in two passes, with every claim below spot-checked in source. Citations are relative to each repo root.

## What the repos are

| Repo | What it is | License |
|---|---|---|
| `usdRig` ("RigExec") | OpenUSD 26.08 character-rig evaluator: FK/IK/spline IK, constraints, ordered deformers ("movers"), Hydra/usdview tools, and an experimental `.rigexec` bake with a USD-free C++17 runtime. 0.1 alpha; every layer "Prototype" or "Beta" (`STATUS.md`). | MIT (plus FlatBuffers Apache-2.0, LZMA public domain, and OpenUSD-derived TOST-1.0 code; see `NOTICE`) |
| `usdRig_unreal` | The UE 5.8 plugin "RigExec". It plays a baked `.rigexec` and draws the character as `UDynamicMeshComponent`s through a custom GPU skin. Control Rig controls feed the rig's avars, and Sequencer keys it. Pickers, hover picker, marking menu, Touch Pose, and rig-computed IK/FK match. Win64, editor only. | **No LICENSE file.** `mkdocs.yml:13` says "Copyright © Squarebit Studios", so all rights are reserved by default. |
| `usdRig_blender` | Read-only `SdfFileFormat` plugin that opens `.blend` as a native usdRig rig (armature vertex-group skinning, constraints, shape keys, Rigify picker). Supported subset. | MIT |
| `usdRig_maya` | The same for Maya ASCII `.ma`: `RigExecJoint`s and native skin movers from skinCluster (`translator.cpp:441,1568`). Also a Maya 2027 playback node. | none at root |
| `usdRig_godot` | Godot 4.7 runtime for a baked `.rigexec`. It writes posed joints into a `Skeleton3D` (`addons/rigexec/src/rigexec_player.cpp:361-373`), which is precedent for a skeletal bridge. | MIT |
| `usdGen` | OpenUSD hair/fur grooming, Hydra/usdview only. | MIT |

## Verified facts that decide the fit

1. **No skeletal-mesh path in or out, but there is one opening.**
   - **In:** the only UsdSkel type usdRig reads is `UsdSkelBlendShape`. `SkelBindingAPI` is allowed but ignored. Skin Mover reads its own `rigExec:jointIndices/jointWeights` (`usdRig/docs/nodes/skin_mover.md:24-45`). No FBX/UsdSkel importer or auto-rig was found. An existing character comes in through a DCC translator: FBX → Blender `.blend` → `usdRig_blender` (vertex-group skinning → `RigExecSkinMover`, `README.md:175`), or the Maya `.ma` translator.
   - **Out:** `export_baked` turns joints into plain `Xform` prims and meshes into point caches. It does not create a UsdSkel skinning rig (`usdRig/python/rigexec/bake.py:67`; `usdCharacter/docs/specs/python-bake-inverse.md:21-31`).
   - **In Unreal:** the plugin has no `USkeleton`, `USkeletalMesh` or `AnimSequence` code. Its generated Control Rig holds nulls, controls and channels, with no bones (`usdRig_unreal/Scripts/build_control_rig.py`). AnimBlueprints, retargeting, inertialization and our AnimGraph tooling can't reach it.
   - **The opening:** the vendored runtime implements `GetJointRestMatrices()` and `GetJointPoseMatrices()`. They are defined (not just declared) in the shipped `rigExecRuntime.lib`. They return **joint path plus asset-space matrix**, with pose = rest × skinning delta (`usdRig/libs/rigExecRuntime/exec.cpp:472,494`; `runtime.h:403-408`).
     - The public header has **no parent index**, so a bridge must map paths to the UE skeleton's own hierarchy explicitly.
     - The plugin doesn't call either function. A bridge would be new C++ on our side.
     - A joint bridge carries **joints only**: mesh correctives (blend shapes, delta mush) publish separately (`exec.cpp:647`).
2. **No runtime dynamics operator.** The movers are stateless per frame: wrinkle has no timestep or history (`usdCharacter/docs/concepts/wrinkle-deformation.md:14`), and the GPU program added in `9f31c45` covers matrix, skin, blend and wire (`libs/rigExecMath/gpuDeformProgram.h:27`). The only spring code is an **offline** example script that authors animation (`examples/2d/bust/build_bust.py:961`). usdRig doesn't compete with, or feed, the Control Rig Dynamics work.
3. **Cost: about 15 ms per biped pose, crowds unmeasured.**
   - With parallel evaluation (current), the biped takes about 5 ms for evaluation plus mesh update, and about 15 ms including all 606 gizmos (`usdRig_unreal/docs/architecture.md:251-253`). The 16–22 ms figure at `:97-98` predates parallel evaluation, so it is stale.
   - Each component keeps its own reader, its own copy of the `.rigexec` (8.4 MB for the biped) and its own open USD stage (`RigExecComponent.cpp:373-399`). Multi-character PIE is an unchecked to-do (`usdCharacter/docs/plans/touchpose-remaining.md:74-76`).
   - Crowd cost is **unmeasured, not disproven**. Nothing here is evidence it scales to the army shots.
4. **Look.** Materials are `UsdPreviewSurface` constants only (colour, roughness, metallic). No textures. UVs are read, so the README's "No UVs" line is stale. Normals are always smooth. A Paragon hero through this path would render as flat colour, so it can't carry a textured hero character.
5. **PIE works; MRQ is unproven, with specific risks.**
   - It ticks in the editor and in PIE, in `TG_PostUpdateWork` after Control Rig (`RigExecComponent.cpp:304-306, 1270-1273`). No MRQ test or mention exists.
   - **Re-evaluation:** it re-evaluates only when a control changes by more than 1e-5, or on a resync (`:1035, 1136`).
   - **Motion vectors:** "previous position" means the previous *upload*, stamped with `GFrameCounter` (`RigExecSkinComponent.cpp:268, 672`). Temporal samples, seeks and multiple evaluations per frame could produce wrong history.
   - **Euler unwrap:** this follows the last sent value, so sent avars can depend on scrub order (`RigExecComponent.cpp:1088-1093`).
   - These are risks, not demonstrated failures.
6. **Build cost.**
   - **Plugin plus the shipped biped:** no usdRig build needed. `build_plugin.bat` links the vendored prebuilt Win64 libs. `Biped.rigexec` (format 22) ships in the example. The stage `../usdRig/examples/biped/Biped_stack.usda` and all 33 of its dependencies are in the usdRig clone. `Setup_RigExecBiped.bat` deletes and rebuilds the example's map and sequence.
   - **Any new rig:** needs a full usdRig build against OpenUSD 26.08 **with OpenExec**, built from source. CI builds only on Ubuntu; MSVC isn't a CI job, and the v0.1.0 release has no binaries. This is the gating cost for options B and C.
   - **Toolchain:** the docs say the libs were built with MSVC 14.38 (`usdCharacter/docs/building.md:102-105`); building the plugin with a newer MSVC toolchain is the safe direction.
7. **Churn.**
   - The `.rigexec` format went 19→20 (Oct 9), →21 (v0.1.0, Oct 10), then 21→22 (Oct 13), with 43 commits on Oct 9 alone.
   - The reader accepts **formats 20–22** (`usdRig/libs/rigExecBinary/format.h:530-541`); the writer emits only 22 (`:651`). Bakes survive small updates and break on a reader-floor bump; not every update invalidates them.
   - The pin: usdRig `1719fa4` = usdRig HEAD = `usdRig_unreal`'s `SOURCE.txt`.
8. **Silent failure.** A mover with no runtime kernel is a no-op. It is logged in diagnostics, but `Execute()` still returns `true` (`usdRig/libs/rigExecRuntime/exec.cpp:671-676, 788-790`). The Unreal plugin checks only the boolean (`RigExecComponent.cpp:679`). Under our doctrine, a load or evaluation that "succeeds" is not evidence the character deformed. A missing kernel is a live possibility, not a hypothetical: upstream usdRig now has a Blender armature-mover kernel (`armatureMover.cpp:245-246`), but whether the **vendored** Win64 lib includes that external kernel is unverified, and this silent failure is exactly how its absence would show.
9. **Editor side effects if enabled in MotionTerminal.**
   - Adds USDCore (beta, off by default; pulls in GeometryCache, HairStrands and LiveLink).
   - Adds a new vertex factory, which means shader compiles.
   - Installs global editor input processors; with a RigExec character in the level, **P** is taken.
   - Reopens the USD stage on every PIE start.
   - No direct conflict found with the project's current plugins; load time was not measured.

## Status: PARKED

The side project stops here, and the OpenUSD 26.08 Windows build is not attempted. The ranking below
stands as written; it is not a judgement that the work is wrong. Its
one high-value item (B, the RigExec -> USkeletalMesh pose bridge) is gated on that build. The
side-project setup, kept outside this repository, is left intact so A can resume without setup.

To resume: re-read the guard rails below, re-pin the `usdRig` / `usdRig_unreal` commits, then do A.

## Where it makes sense, ranked

| # | Use | Cost | Value | Verdict |
|---|---|---|---|---|
| A | **A separate "USD rig interop" side project.** Build the plugin into its own UE 5.8 project (the shipped `RigExecBiped` example), not into MotionTerminal. Script the biped plus Sequencer over the editor-Python bridge. Gate it our way: diagnostics empty, points actually moved, and render a short capture. | Low: prebuilt libs and the prebaked biped, no OpenUSD build | OpenUSD / DCC-agnostic rig interop, without touching its gates. It also answers the MRQ and multi-instance unknowns cheaply. | **Do first** |
| B | **RigExec → USkeletalMesh pose bridge.** An AnimGraph node (or Control Rig unit) that runs the `.rigexec` runtime and writes `GetJointPoseMatrices()` into a skeletal pose. Khaimera keeps his own mesh, skin and textures; only the rig evaluation comes from usdRig. Cheapest credible path, in order: (1) prove the bridge on the shipped biped's joints mapped to a test skeleton, following Godot's `rigexec_player.cpp:361-373`; (2) author a **minimal** usdRig rig whose joints and rest frames match Khaimera's reference skeleton with `rigBuilder.h:140`, bake it, and drive Khaimera. No full deformation translation needed. | High: new C++, USD Y-up / right-handed / row-vector → UE conversion, a path→bone map, and step 2 needs the Windows OpenUSD+OpenExec build | The one integration that actually joins the two pipelines, and the most useful piece. Correctives don't come across (fact 1). | **Spike after A.** Step 2 is gated on a Windows usdRig build succeeding. |
| C | **Offline deformer lab.** Use usdRig's delta mush, wrinkle, volume-correct and RBF pose interpolator to prototype correctives, then reimplement the useful ones in Control Rig / ML Deformer. | Medium (needs the usdRig build) | Research only, no runtime dependency | Optional |
| — | Replace the AnimBP pipeline, carry the Paragon hero as drawn geometry, drive the army shots, or supply secondary dynamics | — | — | **Does not fit** (facts 1–4) |
| — | Port the pickers, marking menu or Touch Pose to Khaimera's Control Rig | Port effort | All are bound to `ARigExecActor` | Not now |
| — | usdGen grooms | — | Hydra/usdview only, no Unreal path in the repo | Not relevant |

## Guard rails if adopted

- **Hygiene:** the usdCharacter repos stay sibling checkouts outside this repo. Nothing from `usdRig_unreal` is vendored into MotionTerminal until its license is known, so MotionTerminal's own licensing is never encumbered. usdRig's own code is MIT, but its NOTICE terms travel with any binary.
- **Pin commits.** Record the usdRig / usdRig_unreal pair here whenever it changes. Today it's `1719fa4` / `6e31e7b`.
- **Gate on outcomes, not booleans** (fact 8): runtime diagnostics empty, a kernel present for every mover, every expected joint path present, and points or joints changing by more than a threshold under a known input. Prove each gate red.
- **No MotionTerminal gate may depend on RigExec** until multi-instance PIE and MRQ are proven by a red/green test of our own.

## Side project

The upstream `usdRig` (`1719fa4`) and `usdRig_unreal` (`6e31e7b`) were built outside this repository. The
shipped `RigExecBiped` example built in 2 min 24 s and its setup reported success. The probes below ran in a
separate content-only UE 5.8 project, as unattended editor startup scripts.

**Biped probe** (all green):
- Load: 11 meshes on the GPU path.
- Pose: `M_Torso.avars:ry` = 30° moves body vertices by up to 11.26 cm. The plugin's own self-test independently reports 11.58 cm over all 35,949 points.
- Rest: clearing the channels returns exactly 0.0 cm, which proves the move measure can read zero.
- Refuse: a bogus avar is refused.
- Timing: `Evaluate()` median is **7.4 ms** measured (the docs say about 5 ms).
- Self-test: opening the 8.4 MB `.rigexec` takes 3.7 s.

## Paragon → USD, measured on Khaimera

UE 5.8's own exporters were driven from Python (`SkeletalMeshExporterUsd`, `AnimSequenceExporterUSD`) in that separate project, then the output was measured with UE's bundled `pxr`.

| What | UE asset | USD result | Verdict |
|---|---|---|---|
| Mesh LOD0 | 48,539 verts | 48,539 points, 80,311 tris, normals, UV sets `st` + `st1`, `subdivisionScheme = none` | Full |
| LODs | 5 | `LOD` variant set: LOD0–LOD4 | Full |
| Skeleton | 198 bones | `UsdSkel.Skeleton` with 198 joints, plus rest and bind transforms | Full |
| Skin | — | `SkelBindingAPI` with elementSize **6**, no truncation | Full |
| Morph targets | 0 | none | n/a for Khaimera |
| Materials | 8 | 8 `UsdPreviewSurface` materials with **36 baked textures** (BaseColor, Normal, Roughness, Metallic, Specular, AO, Emissive, SubsurfaceColor). Layered Paragon materials flatten into coherent maps. Each also keeps an `unreal` shader pointing back at the `/Game/...` asset. | Partial: baked approximation, and the skin shading model is lost |
| Clips | Jog_Fwd, Idle_Relaxed_A | `SkelAnimation`, 198 joints, 30 tcps, translations in cm (`metersPerUnit` 0.01, Z-up) | Full for joints. Only morph curves carry over; material curves are dropped. |
| Physics asset, cloth, FX, sounds, montages and notifies, AnimBP, MPCs, sockets | — | no exporter (`SupportedClass` grep) | None |

Timing: the mesh with 1024² bakes took 89 s cold and 6.6 s warm. Clips take 0.1–1 s each. `Khaimera.usda` is 22 MB.

**What's left for usdRig.** usdRig ignores `SkelBindingAPI` (fact 1), so a UsdSkel character still needs converting into RigExec schema. Two routes:
- **Our own script:** a few hundred lines of pxr Python using `rigBuilder` (`AddJoint`, `AddSkinMover` + `SetJointInfluences`, blend shapes).
- **Through Blender:** Blender's own USD importer turns UsdSkel into an armature, vertex groups and shape keys (since 4.0), then usdRig_blender converts that.

Either needs the OpenUSD 26.08 build. Open questions for the script: whether `AddJoint` takes a local or an asset-space rest, choosing one LOD variant, and removing UsdSkel binding so Hydra doesn't skin the mesh twice. UE's anim export writes `blendShapes` per time code although the schema declares it `uniform` (`USDSkeletalDataConversion.cpp:3885`). It's irrelevant for Khaimera, who has no morphs.

**Gotchas found on the way (all fixed in the probe scripts):**
1. **An automated USD export bakes its textures into the engine install.** `SkeletalMeshExporterUSD.cpp:130-133` sets `TexturesDir` only for the interactive dialog, so with `AssetExportTask.automated = True` the PNG/EXR files land in the process CWD: `UE_5.8/Engine/Binaries/Win64`. The first run wrote 36 files there, and a "textures found" check passed because they resolved. They were moved out and the engine folder verified clean. Now `material_baking_options.textures_dir` is always set, and the run fails if `Engine/Binaries/Win64` gains a file or any texture resolves outside the output folder.
2. **`import pxr` inside the editor writes `__pycache__` into the engine's `USDCore/Content/Python`.** 17 folders were removed. `sys.dont_write_bytecode = True` plus `PYTHONDONTWRITEBYTECODE=1` now prevent it.
3. **USD export on frame 0 asserts** (`SharedPointer.h:1133` inside `Exporter.RunAssetExportTask`). A startup script must wait for the editor UI, the way the RigExec example's setup waits 150 ticks.
4. **USD's own upkeep.** Any USD-enabled editor launch rewrites `Engine/Binaries/ThirdParty/USD/UsdResources/.../plugInfo.json` (52 files). That is engine-managed (`USDClasses.Build.cs:31`: "read/write plugInfo.json files from UnrealUSDWrapper.cpp"), not ours, and left alone.
5. **Paragon textures build on first load in a new project.** Many are 4096², up to about 1.2 GB peak memory each, so the first launch of a new project is slow.
