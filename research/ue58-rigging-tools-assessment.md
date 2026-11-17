# UE 5.8 rigging & animation tools — fit assessment for MotionTerminal

Source: Epic's Unreal Fest talk "What's new in 5.8 for rigging and animation"
(https://www.youtube.com/watch?v=yi-oDmC1nqU, captions fetched via youtube-transcript-api), checked
against the installed engine source (`Engine/Plugins` of the UE 5.8 install) and this
repo. Read-only research; no engine or project file was changed.

Every claim below is either (S) verified in engine source, (R) verified in this repo, or (T) stated in
the talk / release notes and NOT verified. Do not promote a (T) to fact without checking.

## The talk, by feature

| Feature | What it is | Lives where | Fit for runtime-verified AnimGraph work |
|---|---|---|---|
| Animator gizmos, curve-editor/Sequencer link, Simple View | Animator UX | Editor | None — not runtime, not scriptable value |
| Physics cube/ball rigs, layered physics in Sequencer, bakeable | AnimatorKit utility rigs | Sequencer | Low |
| Auto Bake (live rig <-> baked sequence toggle) | Sequencer workflow | Sequencer | Low (our renders are MRQ-over-PIE) |
| **Control Rig Dynamics** | Particle chains for secondary motion, "5x faster than CR Physics" (T), one node -> many chains, debug window works in PIE | Runtime module, Experimental | **High** |
| **Control Rig Physics in modules** (BipedPhysics/ChainPhysics/LimbPhysics), forces, convex hulls, layered rigs | Chaos inside Control Rig | Runtime module, Beta | Medium |
| Recreate Control Rig with new settings | Expose Construction event to Sequencer | Sequencer | None |
| Graph Explorer, direct reroute, pure functions w/ loops | CR editor UX | Editor | None |
| Modular rig "show only empty sockets", Shift+B | Editor UX | Editor | None |
| Direct Mesh Controls | Click-the-skin controls | **Editor-only modules** (S: DirectMeshControl.uplugin:56-63) | None at runtime |
| Morph sculpting, weight locking, quick menu | Skeletal editor | Editor, Experimental | None |
| Dataflow Transfer Attributes | Rig/shape transfer between meshes | Editor | Low |
| Morph Target Driver (shot correctives) | AnimatorKit utility rig | Sequencer | None |

## Engine-source facts (S)

**ControlRigDynamics** — `Experimental/ControlRigDynamics`
- Experimental, not enabled by default; Runtime module `ControlRigDynamics` + Editor module (uplugin L16, L30-37). Depends on ControlRig, PhysicsControl.
- `FRigUnit_SpawnDynamicsChains` (RigDynamicsExecutionHelpers.h:141): `RootBones`, `ChainTerminatorBones`, include/exclude, `ParticleProperties`, `ChainCurves` (per-chain-length multipliers for radius, mass, gravity, strength, damping, angle limit — L38-111), `Colliders`. Construction event only (L139).
- `FRigUnit_SpawnDynamicsSolver1` (RigDynamicsSolverExecution.h:67; the un-suffixed one is Deprecated 5.8, L23). `FRigUnit_StepDynamicsSolver` (L149): `Alpha` <= 0 is pass-through (L163-187).
- Substeps: `ceil(dt / MaxTimeStep)` clamped to `MaxNumSteps=4`, step = dt/NumSteps (Private/RigParticleSimulation.cpp:1271-1277) -> **frame-delta dependent**. MRQ fixed frame rate should make it repeatable; unverified.
- Teleport detection zeroes velocities, does not reset pose (L88-91). **No world collision** (RigDynamicsSolverComponent.h:76).
- Test surface: `FRigUnit_GetDynamicsSolverData` (L520-566) exposes reset/teleport flags; **no particle-position getter**; solver C++ is Private. Tests must assert on output bone transforms.
- Debug window only toggles CVars (SControlRigDynamicsDebugWidget.cpp:118-153).

**ControlRigPhysics** — Beta, EnabledByDefault (uplugin L15-17). `FixedTimeStep=0.02` default (RigPhysicsData.h:762-768). Body transform/velocity getters exist as rig units (RigPhysicsBodyExecution.h:638-734). Convex collision exists (RigPhysicsData.h:252). World collision is an async game-thread query (RigPhysicsSolver_WorldObjects.cpp:190-213).

**ControlRigModules** — `Content/Modules58/`: BipedPhysics, ChainPhysics, LimbPhysics, PhysicsSolver — all ControlRigPhysics units. No Dynamics-based module and nothing named "Fortnite" in the engine (the talk's body modules ship with the Zebra sample on Fab, T).

**Scripted authoring** — `URigVMController::AddUnitNodeFromStructPath` / `SetPinDefaultValue` / `AddLink` are BlueprintCallable (RigVMController.h:363, 919, 1041). 5.8 has two asset types: legacy `UControlRigBlueprint` and new `UControlRigRuntimeAsset` (ControlRigBlueprintFactory.h:43; ControlRigAssetFactory.h:44).

## Repo facts (R)

- **Khaimera is the only roster rig with secondary chains** (`Plugins/MotionTerminal/Tests/ScaleBaselines/khaimera.json`): `loin_cloth_bk_01..03` (+feathers), `loin_cloth_fr_01..03`, `torso_cloth_*`, `feather_01..17_l/r`, `necklace_*`, `back_moon`, `weapon_tail_l/r_01..02`. Manny/Quinn/MetaHuman have only twist/IK bones.
- AnimGraph Control Rig insertion is proven (the foot-IK Control Rig).
- Budget (`army_config.json`): game-thread avg 3.6 ms vs 4.0 ms hard gate -> ~0.4 ms headroom at N=46. Khaimera appears only in the 6-instance hero ring.
- Scope: Control Rig authoring was outside the toolset's original scope. Adopting Dynamics is a deliberate scope expansion.

## Assessment

**Control Rig Dynamics on Khaimera** is the strongest fit: it runs in the AnimBP the project already
verifies, it can be authored by script, it lands on the one rig with chains to drive, it
exercises Control Rig authoring (not just use), and `Alpha = 0` pass-through gives the
gate a built-in negative control (ON vs OFF on the same frames; non-chain bones must be identical).
Risks: Experimental status; frame-delta-dependent substeps vs MRQ; no world collision (loincloth vs
thighs needs authored colliders); cost against 0.4 ms headroom (hero ring only).

**Control Rig Physics / BipedPhysics** is Beta and more stable but bigger: needs physics assets per rig,
and a modular rig under `AnimNode_ControlRig` is unverified. Better as a second step (hit reactions).

Everything else in the talk is Editor/Sequencer tooling and does not fit runtime-verified work.

Decision: **Dynamics first (Khaimera), then Physics.**

## Design constraints for Control Rig Dynamics

Design: `docs/specs/2026-10-06-khaimera-cr-dynamics-design.md`. Each constraint below cites the engine or repo
source it rests on, and was settled before building. A citation marked "not re-read" was not re-checked in
source; a constraint with no citation rests on reasoning.

### Where the rig runs

- **`Army.Budget` cannot see Khaimera.** It spawns ONE class, read from `roster.manny` (PieTests.cpp:3555, 3574),
  so it would stay green over any dynamics cost. A Khaimera-aware cost row is required; the existing budget gate
  is not rebuilt.
- **Khaimera's ABP is a derived child with no graphs** (Baselines/abp_mt_khaimera.json: `"graphs": []`), and
  derived ABPs skip AnimGraph compilation (AnimBlueprintCompiler.cpp:1188-1192), so a node placed "last in
  Khaimera's ABPs" has nowhere to go. The rig runs in a post-process AnimBP applied per component with
  `SetOverridePostProcessAnimBP` (SkeletalMeshComponent.h:433): no edit to the licensed Khaimera mesh, template
  untouched.
- **Asset type.** The rig is the legacy asset type, and a spike proves scripted authoring first.
- **Plugins.** ControlRigDynamics is Experimental and not enabled, with PhysicsControl as a dependency (uplugin);
  both are enabled in `MotionTerminal.uproject`.
- **Content location.** The rig binds Khaimera's skeleton, and everything Khaimera-bound lives in gitignored
  `Content/MT/Khaimera/` (.gitignore:27-30). Both assets go there, authored by a tracked script so they are
  reproducible, and every Khaimera row reports SKIP with a reason where the content is absent, never PASS.
- **LOD.** With an override set, Evaluate skips the post-process LOD threshold but Update still honours it, giving
  Evaluate-without-Update at high LOD (not re-read). Hero-ring and gate pawns are forced to LOD 0, and the crowd
  does not get dynamics. Pass-through still costs something on every pawn, a second reason to keep it to the hero
  ring.
- **Cost.** The hero ring holds 2 Khaimeras, and peak game-thread time is already 4.21/4.0 ms
  (army_config.json). The cost row's ceiling uses ×2; the peak is reported and never claimed to fit.

### What the A/B compares

- **Two pawns, not a toggle.** Toggling `DynamicsAlpha` 1<->0 inside one PIE run contaminates the A/B:
  pass-through with `bTrackVelocitiesDuringPassThrough` (default true) follows the animation and drops forces, so
  re-enabling is a history-dependent resume (RigDynamicsSolver.cpp:1204, 1093; header flag checked, solver .cpp
  not re-read). The comparison uses two identically driven pawns, continuous ON against continuous OFF, at matched
  timestamps.
- **Real translation.** In-place pawns never move the component, and Component sim space takes its inertia from
  the component transform (RigDynamicsSolver.cpp:1139-1142), so on an in-place pawn the cloth reacts only to the
  animation's own bone motion and the run-then-stop swing does not exist. A velocity-injection path does exist —
  the runtime unit **`Set Dynamics Solver Space Motion`** (RigDynamicsSolverExecution.h:438) carries
  `Drag.ExternalLinearVelocity` (RigDynamicsData.h:247, "wind-like effect, typically the velocity of the object")
  — but the pawns translate for real instead of being fed injected air velocity.
- **Unpossessed pawns do not walk.** CMC runs the controlled move only when there is a controller,
  `bRunPhysicsWithNoController` is set, or root motion is playing (CharacterMovementComponent.cpp:1750-1752, 5662,
  5686), and every existing harness also disables the CMC tick, so real translation needs a new movement harness.
  A missing controller is not itself the blocker — input accumulates without one — but the pawn BP's rotation
  settings could not be read, so the harness sets them explicitly.
- **Existing shots stay as they are.** The crowd pull-back is a 46-instance shot whose caption and framing gate
  depend on its deliberately fixed camera (pie_army_pullback_drive.py:20-24); re-staging it with a follow-cam
  would break both, and re-staging the roster 4-up would walk four pawns through the camera. Both are untouched;
  the dynamics get a shot of their own, "two Khaimeras, one lane".
- **The stop is anchored.** CMC braking takes about 0.2-0.3 s, and the state machine has its own crossfade, so
  `t_stop` = the first frame with speed ≤ 1 cm/s after input release.
- **Comparison space.** Chain descendants inherit motion legitimately, so comparisons are in component space and
  exclude the chain closure. A one-tick spawn skew could hide inside an adjacent-frame allowance, so the harness
  asserts the spawn and first-input frames are equal directly; pawns spawn on the same tick with lane spacing
  ≥ 3 × capsule diameter.
- **Lanes.** The test map has ramp and landing seams (PieTests.cpp:1910), and floor extent does not prove two
  lanes equivalent, so the spike traces both lanes (Z, physical material, seams), with a dedicated map as the
  fallback.
- **Determinism is measured, not asserted.** The gate runs under PIE's variable dt while the render runs under
  MRQ's fixed dt, and a fixed output fps does not prove determinism: MRQ temporal samples change the deltas fed to
  the rig (AnimNode_ControlRigBase.cpp:105; not re-read). The delta sequence and a solver-data trace are recorded
  inside the MRQ drive as well, and two runs compared.

### Witnesses: proving the node ran

- **Presence is not evaluation.** "Non-chain bones identical" passes trivially if the node never applies (node
  alpha, LOD), and node weight is trivially 1.0 in Input->CR->Output — the project already calls that
  presence-only (PieTests.cpp:2092-2108). The gate requires a positive chain response AND a witness the rig itself
  writes, read back as an anim curve.
- **Curves fail silently.** `SetCurveValue` is a no-op for a curve the rig hierarchy cannot resolve
  (RigUnit_SetCurveValue.cpp:14-18), and the float `GetCurveValue` returns 0 for a missing curve
  (AnimInstance.cpp:2177-2182). The curve elements are declared in the rig and gated headless, `bUpdateCurves` is
  checked headless, and reads go through the bool overload.
- **No step witness.** `Get Dynamics Solver Data` returns stale data when no step ran and has no public stepped
  flag (RigDynamicsSolverExecution.h:519-566). A rig-tick witness (both arms advance) stays; the chain's motion is
  the step evidence, proven red with the `EnableStepSolver` CVar (RigDynamicsSolverExecution.cpp:237).
- **Which instance.** `TryWriteFloatVar` writes whichever instance it is given, every existing call site passes
  the main one, and writes land one tick later. The harness drives and reads `GetPostProcessInstance()` and
  settles a tick before sampling.

### Resets and teleports

- **Reset flags default off.** All three default **false** (RigDynamicsData.h:186-211), so "no unexpected reset"
  is vacuous unless they are enabled, and the position reset is vacuous at its 10,000 cm default. The position
  reset is enabled as the explosion detector, with the kinematic-speed reset; the evaluation-interval reset, which
  fires on editor hitches, stays off.
- **What the resets measure.** The position reset measures distance from the **simulation origin**, not excursion
  from the animated pose; the kinematic reset reads **sim-space target velocity**, not CMC speed
  (RigDynamicsSolver.cpp:944-956; kinematic read checked, position check not re-read). Both thresholds are derived
  from OFF-arm measurements in the spike.
- **Teleport detection defaults are all ON**, with a 100 cm position threshold and 100,000 cm/s² acceleration
  (RigDynamicsData.h:452-473), and a teleport zeroes velocities: at 600 cm/s, a 0.17 s hitch counts as a teleport.
  Thresholds are derived from the drive profile.
- **A reset is a defect, not noise.** Recovery resets can snap an excursion back before writeback
  (RigDynamicsSolver.cpp:1318; not re-read). A reset fails the run immediately; a teleport makes a run INVALID
  only with a recorded hitch; the retry budget is bounded and cannot erase an earlier FAIL. (A rule of "two
  INVALID in a row = FAIL" would never fail INVALID/valid/INVALID.)

### Bounds

- "Differs ON vs OFF" accepts lag or explosion, so motion is bounded above and below per chain, plus finite
  checks.
- **No fitted bounds.** A bound "set from the spike's measurements" is set from an ON run, so that run passes by
  construction. Every bound is instead a constant times an in-run measured noise or a physical scale, so no fitted
  number exists to tune; constants are recorded with their rationale, and a change needs a research entry. This
  makes a hash-locked bound block unnecessary: relative bounds remove the thing it would protect.
- At zero OFF noise, k × noise = 0, so gate (b) would accept identical tips. (b) is strictly greater than
  max(k_b × noise, the OFF/OFF repeatability floor, a visibility minimum fixed before the ON capture is judged).
- With gravity, healthy cloth settles at an offset from the animated pose, so "converge to OFF" would fail it;
  gate (e) measures residual frame-to-frame motion instead.
- Gate (g) measures bones, not the cloth surface, and may be unable to fail: spike step 6 must show it red first,
  or it is not adopted, and it is stated as bone-level.
- Visibility at render resolution is unproven, so spike step 7 measures projected pixel displacement before the
  ON capture is judged; the threshold is fixed in advance and also sets (b)'s visibility floor.

### What is simulated

- Chain `GravityMultiplier` defaults to **0** (RigDynamicsExecutionHelpers.h:148-151): a default-built chain would
  not hang, so it is set explicitly.
- Bone motion does not prove visible deformation, and chain bones may be unweighted, so a skin-weight audit
  (SkinWeightModifier) runs per chain before chains are chosen, on the LODs actually rendered rather than LOD0
  alone.
- v1 is the loincloth back/front plus torso cloth, with pelvis/thigh capsules; feathers and necklace come later,
  if they earn it.
- `weapon_tail` chains are left out of v1 for scope. Hidden bones and their descendants leave RequiredBones, so a
  chain under a hidden bone simulates invisibly, and a gate that does not hide them passes over a shot that shows
  nothing. That holds for any shot that hides the weapon bones: the hide is in the rifle take
  (`Scripts/pie_rifle_take_drive.py:71`); the crowd pull-back keeps the axe.

### Order of work

The spike needs the harness, probes and capture, and PIE recovery comes before spike step 3. The order is PIE
recovery, then the spike (with a minimal harness that can be promoted), the gates, and the final shot. Constants
(speed, lane, durations, stop method, map) are defined once in the harness; the camera updates after CMC; the map
is measured in the spike.

## PIE recovery and spike results

**PIE recovery is DONE.** Three pie-tier runs with unchanged binaries:
- **Run 1:** 3 red (`AimOffset.Khaimera`, `RigLocomotion.Khaimera`, `MontageSeamless`), each with the
  starvation signature (feet swing 0.00 cm, node weight 0.0). Free GPU memory fell sharply during the run.
- **Runs 2 and 3:** 22/0.
- Exit criterion, green twice, is met. The red is the known GPU-memory-pressure class, not a
  regression. Free GPU memory at the start does not predict it.
- After the budget-allocator change the new state was also green twice: 23/0, 23/0.

**Spike step 2: which candidate chains are weighted to visible mesh.**
- Source: `Scripts/usd_chain_weights.py` over UE 5.8's own USD export of the **default** Khaimera mesh;
  weights >= 0.05 counted.
- Vertices skinned per chain; the column header is that LOD's total vertex count:

| Chain | LOD0 (48539) | LOD1 (25967) | LOD2 (13805) | LOD3 (7193) | LOD4 (3987) |
|---|---|---|---|---|---|
| `loin_cloth_bk` (3 bones) | 2793 | 1475 | 825 | **0** | 0 |
| `loin_cloth_fr` (3) | 1559 | 963 | 521 | **0** | 0 |
| `torso_cloth_fr_l` / `_r` (3 each) | 1472 / 1472 | 597 / 636 | 467 / 328 | **0** | 0 |
| `torso_cloth_bk_l` / `_r` (2 each) | 303 / 298 | 193 / 188 | 149 / 145 | **0** | 0 |
| `feather_l` / `_r` (17 each) | 1143 / 1081 | 506 / 486 | **0** | 0 | 0 |
| `weapon_tail_l` / `_r` (2 each) | 1253 / 1253 | 523 / 523 | 323 / 323 | 83 / 83 | 0 |
| `necklace` (3 of 4 weighted) | 2079 | 671 | 403 | **0** | 0 |

Reading:
- **The v1 targets (both loincloths, all four torso cloths) are weighted at LOD0–2 and gone at LOD3.** The
  design's "force LOD 0 on every overridden pawn" is load-bearing, not a nicety. A pawn that drops to LOD3
  would simulate cloth that no vertex follows, and gate (b) would still pass on the bone motion. Gate
  (b) measures bones, so the forced LOD has to stay asserted.
- The feathers, cut from v1, are weighted at LOD0–1 only. `weapon_tail` survives to LOD3.
**Spike step 1: can a script author the rig? YES.** `Scripts/dynamics_rig.py` (`KDYN_ACTION='author'`)
creates `CR_MT_KhaimeraDynamics` from nothing.
- **Plugins:** it enables `ControlRigDynamics` + `PhysicsControl` in `MotionTerminal.uproject`.
- **Asset:** `ControlRigBlueprintFactory.create_new_control_rig_asset`, a legacy, non-modular rig.
- **Hierarchy:** it imports Khaimera's bones (196) and declares the four witness curves as curve elements.
- **Graph:** it wires Construction → `SpawnDynamicsSolver1` → `SpawnDynamicsChains` (six roots, gravity
  multiplier 1.0, component space) and Forwards Solve → `StepDynamicsSolver`.
- **Compile and save:** it recompiles and saves through the gitignored-path guard.
- **Read-back:** `report` re-reads the saved asset independently. All 5 units resolve by struct path, all 6
  roots and 4 curves are present, and every default took. No `LogRigVM` / `LogControlRig` warning or
  error appeared.

The calls are those of the engine's own `AnimationAssistantToolset/.../toolsets/controlrig.py`.
- **Not in the design: no data link between events.** RigVM refuses a data link from a
  Construction node to a Forwards Solve node ("You cannot combine nodes from Construction and Forwards
  Solve"). Spawn's output key therefore cannot feed Step. A rig component key is (owning element, name):
  Spawn gets an explicit owner and `SolverComponentName`, and Step gets the same key as literals, from
  one pair of constants. Spike step 3 must confirm Step finds the solver at run time; a mismatch would read as
  "dynamics inert", which is gate (b)'s red.
- **Bone count:** the hierarchy holds 196 bones against the skeleton's 198 (`ScaleBaselines/khaimera.json`).
  The two missing are presumably virtual or IK bones. Not yet checked which, and none is a v1 chain root.
- **Not yet done** (spike step 3 onward): the `DynamicsAlpha` variable, the witness-curve writes, the colliders, the
  measured thresholds, and the post-process ABP hook-up.

- **Open:** the skin used for Khaimera's shots is Sweet Dreams (`Tier1/CS_Halloween`), a different
  mesh. Its name table lists the same chain bones (`Khaimera_Halloween.uasset`), but its weights are
  unmeasured. Before a shot uses that skin, export it the same way and re-run this script.

## Spike steps 3–7

Harness: `Scripts/pie_dynamics_drive.py` (record), `Scripts/dyn_run.py` (one run end to end from a
shell), `Scripts/dyn_analyze.py` (measurements). Assets: `Scripts/dynamics_graph.py` rebuilds the rig and
the post-process ABP in dependency order. Drive profile, recorded in every file: two pawns on Lvl_MT_Army lanes
Y = ±300, start X = −2000, settle 1.0 s, run 3.0 s at 500 cm/s (CMC `MaxWalkSpeed`), release, hold 3.0 s, at a
**60 Hz fixed timestep** (`UMTDriveLibrary`, new). Records are `Saved/MotionTerminalTests/khai_dyn_<tag>.json`.

**Two harness facts found on the way.**
- Unfocused, the editor runs PIE at about 3 fps (measured: 21 frames in 7 s, each a 0.33 s step), and its
  background throttle is not reachable from Python. The fixed timestep makes every frame 1/60 s whatever the wall
  clock does, matching the C++ rows' `FFixedTimeStepScope`. It also made the runs **bit-for-bit repeatable**:
  three ON/OFF runs agree to four decimals.
- Python folds `GetCurveValueWithDefault`'s bool return into the value: a found curve returns its value, a
  missing one returns `None` (probed: 156.0 against `None` for `NoSuchCurve`). `None` is the missing-curve signal.
- Python cannot spawn into the PIE world (no deferred spawn is exposed), so the pawns are placed in the editor
  world before PIE, as `army_spawn` and `metahuman_bind` do. Both begin play on the same PIE frame.

**Step 3: PASS.** On an unpossessed pawn translating under CharacterMovement, with the override applied per component
and LOD 0 forced:
- all four witness curves read `found` on every frame, on both pawns;
- `MT_DynRigTick` advances by exactly 1 on every frame (420 frames);
- the ON pawn's chain tips differ from the OFF pawn's by 15–39 cm in the stop window, so **Step does find the
  solver through the literal key** (step 1's open question);
- the ten control bones outside the chain closure match the OFF pawn exactly (0.0 cm): no mask leak.
- Motion read: the cloth trails ~15 cm on the start, swings 19–25 cm on the hard stop, settles within ~1 s, and
  rests ~2 cm off the animated pose under gravity.
- The solver flags a **teleport on its first frame**, on both pawns. That is initialisation, so solver events
  are counted from frame 3 on.

**Step 4: lanes PASS; LOD recorded.** Floor traces every 100 cm along both lanes: Z = 0.0 and
`DefaultPhysicalMaterial` throughout, identical. `Lvl_MT_Test` was not usable: its flat stretch before the ramp is
300 cm (`PieTests.cpp` `kBSFlatLowX/HighX`), too short for a run and a stop, so the army stage (a 140 m floor)
hosts the gate, as the design's "own flat map" fallback allows. LOD: forced LOD 0 held on every frame (step 2 makes it
load-bearing).

**Step 5: PASS.** OFF/OFF: the two OFF pawns differ by **0.0 cm** on every chain, so the repeatability floor is 0 at a
fixed step. OFF-arm measurements, and the thresholds now in `dynamics_rig.py` `SOLVER_SETTINGS`:

| Quantity (OFF arm) | Measured | Threshold |
|---|---|---|
| largest particle distance from the simulation origin | 159.26 cm (a torso-cloth root, at the chest) | position reset at 159.26 + k_c × 43.96 = **225.20 cm** |
| largest kinematic (root) component-space speed | 614.7 cm/s (the jog-to-idle transition at the stop moves a root ~10 cm in one frame) | kinematic reset at 3× = **1844.1 cm/s** |
| one frame's travel | 8.33 cm | teleport position change at 3 frames = **24.99 cm** |
| peak linear acceleration | 10047 cm/s² (the hard stop) | teleport acceleration at 3× = **30141.6 cm/s²** |
| turning | none in the drive | orientation 30° and angular acceleration 1e5: engine defaults, recorded |

Substeps: `MaxTimeStep` 1/60 s, `MaxNumSteps` 4. Evaluation-interval reset off (per the design). With these set, **three
ON/OFF runs: zero teleport, position-reset or kinematic-reset events** after initialisation.

**A result that changes gate (c).** The solver reads back bone *orientations* only (`bReadBonePositions` false),
so a tip can never be further than 2 × its chain length from the OFF pose. Gate (c) with k_c ≥ 2 could never fail.
Measured ON-vs-OFF peaks reach 1.18 × chain length (the two-bone `torso_cloth_bk` chains: 16.2 cm on 13.7 cm), so
**k_c = 1.5**: above the measured response, below the physical ceiling.

**Step 6: PASS, with the bound adapted.** Capsules on `thigh_l` / `thigh_r` (radius 9 cm, hip joint to knee, 44.74 cm
long). Not on the pelvis, unlike the design's list: the chain roots sit 14–20 cm from the pelvis bone, inside any
capsule standing for the hips. The minimum distance from any simulated loincloth bone to the thigh axis over the run:

| Arm | left | right |
|---|---|---|
| OFF (animation only) | 7.75 cm | 9.94 cm |
| ON, colliders | 9.73 cm | 11.01 cm |
| ON, **colliders removed** | **1.24 cm** | 8.61 cm |

The design's absolute bound (capsule radius + particle radius = 14 cm) fails on the **authored animation itself**:
these are bones at the middle of the cloth, not the cloth surface. So gate (g) is relative: **the ON pawn's
clearance on each side ≥ the OFF pawn's − 2.5 cm** (half a particle radius of allowed interpenetration). Colliders
on: +1.98 / +1.07 cm, PASS. Colliders removed: −6.51 cm on the left, RED. Proven able to fail, so (g) is adopted.

**Step 7: visibility — the threshold, registered BEFORE the measurement and before any capture is viewed.**
- Framing (the planned shot): 1920 × 1080, horizontal FOV 50°, a camera tracking the two runners from ahead and to
  the side, at (pawn X + 900, −700, 150) looking at (pawn X, 0, 100). Both pawns sit about 24° apart in frame.
- Measure: for each chain tip, the ON pawn's tip against the OFF pawn's tip carried into the ON pawn's frame (the
  two overlaid), projected through that camera; peak pixel displacement over the stop window.
- **Pass: ≥ 12 px peak on at least 3 of the 6 chains.** 12 px is about 1.1% of the frame height at 1080p, a
  displacement a viewer sees as different motion rather than noise; three chains so one lucky chain cannot carry
  it. Then one capture is checked by eye. If it fails, the feature does not earn a render.

**Step 7 measured: PASS** (`Scripts/archive/khai_dyn_visibility.py` over `khai_dyn_s7.json`, after the rule above was
committed). Peak ON-vs-OFF tip displacement in the stop window, at depths of 906–965 cm:

| Chain | loin bk | loin fr | torso fr l | torso fr r | torso bk l | torso bk r |
|---|---|---|---|---|---|---|
| peak px | 45.4 | 46.6 | 44.3 | 48.2 | 22.1 | 19.0 |

6 of 6 chains clear 12 px. Control: an OFF/OFF run projects to **0.0 px** on every chain, so the overlay
measures the dynamics and not the projection. The capture for the visual check is an MRQ-over-PIE render at that
framing through the existing `Seq_Army` (`Scripts/dyn_render.py`), 450 frames at 60 fps, with pawn 0 (nearer
the camera) ON. The gates and the final shot wait on that visual check.

### Feather audit

Goal: dynamics on the feathery fringes — the hood fringe, the axe-tail feathers, a feather at the left wrist and two
hanging at the knees — with the backend chosen automatically and easy to swap under the hood.

`Scripts/usd_section_islands.py` over the same USD export as spike step 2, LOD0, Section3 (`MI_Global_Feathers_Khaimera`):
9008 faces in **469 separate feather pieces**. Each piece's top-weighted bone, with bind-pose height:

| Driven by | Pieces | Verts | Where | Can bone dynamics move it? |
|---|---|---|---|---|
| `feather_01..17_l/r` (single bones, weight 1.0) | 125 | ~2.4k | hood crest, z 169–208 | yes, with a tip particle per bone (the tails mechanism) |
| `weapon_tail_l/r_02` | 26 | 402 | axe tails | yes, existing 2-bone chains |
| `loin_cloth_bk_feather_l/r` | 24 | 384 | back loincloth fringe, z 56–65 (the "knee" feathers) | already simulated (branch of the loin_bk chain) |
| `loin_cloth_fr_03` | 2 | 42 | front loincloth tip, z 53 | already simulated, rides the chain end |
| `torso_cloth_fr_l/r_02` (50/50 with _03) | 22 | 348 | chest-strip feathers, z 104–111 (the "wrist" feather sits in front of the strip at belt height) | already simulated, rides the strip rigidly |
| `torso_cloth_bk_l/r_01` | 2 | 42 | back strips | already simulated |
| `back_moon` (single-bone flap) | 16 | 258 | back | yes, with a tip particle |
| **body bones**: `upperarm_twist_01` (112), `head` (68), `spine_03` (35), `clavicle` (20), `neck_01` (18) | **253** | **~4.2k** | shoulder/upper-arm fringe, hood rim, neck | **no** — skinned to animated body bones; needs a mesh-level method (Chaos Cloth on a duplicate mesh, or vertex-shader sway in a duplicate material) |

So more than half of the fringe has no bone to simulate. "Feathers that move" needs two families of backend: bone
dynamics for the 125 + 16 single-bone pieces and the chains, and a mesh-level one for the 253 body-skinned pieces.

### Hood feathers and the cloth angle limit

- **Feathers as flaps.** 33 single-bone feathers (`feather_01..17_l/r` minus `feather_14_r`, which has no weighted
  piece) became parts with a tip particle measured from each feather's own vertices (bone -> centroid direction,
  p95 length). The three necklace flaps got no tip and are dropped with a reason. Every feather responds 8–13 cm
  ON vs OFF in the stop window; 0 solver events.
- **Rig size.** One spawner and one rest copy per feather gave 39 spawners x ~70 exclusion pins; the build ran for
  over ten minutes with the editor unresponsive. Shared rest copies (one per body shape for all flaps, at the
  smallest radius needed) and one spawner per preset (the union of the members' colliders) cut it to 46 colliders
  and 7 spawners; the build takes 74 s.
- **Gotcha: `RigHierarchyController.import_bones_from_skeletal_mesh` silently left out `feather_02_r` and `jaw`**
  (2 of the mesh's 198 bones). The rig now imports from the skeleton and asserts every part root is present; the
  tail-parent assertion is what caught it.
- **Angle limit.** `AngleLimit` 30 deg at `AngleLimitStrength` 6 Hz on every cloth part (swept 30/4, 30/6, 30/10,
  40/4, 25/10 through the gate): chest-strip end bend at the stop 61.5 -> 22.7 deg (the strips had read as tucking
  behind the belt), front loincloth stop swing 5 -> 32 deg (the swing the tails had cost), strip stop swing 31–33 -> 24–26 deg.
- **Cost, measured** (`STATGROUP_Anim`, exact counter `STAT_ControlRig_Evaluate`, 12 settled frames early in the
  drive, both pawns, three runs each): six chains 1.09 / 1.09 / 1.11 ms, with the feathers 1.20 / 1.07 / 1.08 ms:
  **no measurable difference at that phase**. An earlier sampler that waited ~30 s (and could read after
  the drive ended) gave 1.08–1.11 vs 1.42–1.45 ms; that state's +0.34 ms is **unresolved**. Cost is therefore
  recorded every run and kept in the baseline, but **not gated**: the same rig read 1.07–1.45 ms across runs because
  the sampler is not locked to a phase of the drive. A phase-locked sampler is the open item.
- **A collision-model mismatch:** the rig gave every feather its group's radius and
  colliders while the analysis checked each feather's own, so the first feather baseline's "penetrations"
  (−9.4 cm) were artefacts. The profile now states each flap's effective group settings; scored on them, the worst
  feather contact is −0.07 cm with 0 frames under tolerance. The corrected tip threshold (flap radius, not the chain
  particle's 5 cm) also admits the three necklace pendants as `strap` flaps.
- **Gotcha: `stat anim` toggles, and the state outlives a PIE session**, so every other harness run had collection
  switched off (`settled: false`). The runner toggles again on an unsettled read.

### Feather flutter and hanging axe cords

Visual check: the hood feathers moved too stiffly and were over-damped, where light feathers should feel very
little gravity; the axe cords stuck out where gravity should pull them down. Native settings are now resolved
per `<preset>` then `<preset>.<kind>` (`dynamics_rig.native_for`), so a light single-bone `cloth.flap` gets
its own settings and a `strap` flap (the bead pendants) keeps its weight. New measure, `dyn_analyze.flutter`:
the RMS of the ON-minus-OFF tip offset about its own mean during the steady run (a constant lag scores 0).

- **Strength is the stiffness, gravity was the droop.** On the shipped cloth settings every feather tip sat 6.4 cm
  (median) off the sculpt at rest, nearly all of it down. Raising `Strength` to 2–3 Hz cut flutter (2.45 -> 2.45–2.89
  cm); lowering it at full gravity raised flutter (4.5 cm) but drooped 10–12 cm. Gravity 0, `Strength` 0.5,
  `DampingRatio` 0.1, 45 deg / 3 Hz soft limit: droop 0.79 cm, flutter 4.70 cm (1.9x), stop response 8.9 -> 14.6 cm.
- **Revised: the settle read as underwater** (visual check). A 0.5 Hz spring under the solver's 2.5/s drag is a ~2 s
  swing (effective damping ratio ~0.5 = 0.1 + 2.5/2ω, stationary mass-1 approximation) so each feather drifted back
  over 1.20 s (median; new measure `dyn_analyze.settle_time`: seconds from the stop until the ON-minus-OFF tip
  offset is back within max(1 cm, 15% of its post-stop peak) of its offset in the pre-run stand and stays there at
  least 0.25 s to the end of the record, else None). Swept eight Strength/DampingRatio pairs: every faster settle costs
  flutter amplitude. Shipped `Strength` 1.5 Hz, `DampingRatio` 0.1 (ζ_eff ~0.23, ~0.7 s swing): settle 0.53 s (worst
  0.72; 2.3x faster), flutter 4.70 -> 3.40 cm, stop swing 14.6 -> 12.5 cm, rest offset 0.09 cm. Lower damping
  (1.25/0.05, 1.5/0) won back flutter (4.07, 3.74 cm) but settled in ~0.85 s. The first settle measure was broken:
  it scored against the record's own last 0.25 s, so a slow drift got a settle time; the rest
  reference and the hold fix that. Not addressed: the sweep is one drive and one release phase, not a controlled
  ring-down.
- **The cord's angle limit is measured from its authored pose, which sticks straight out**, so 30 deg / 6 Hz held it
  83–91 deg from straight down. `Strength` 0.25 Hz, no limit, per-particle `Damping` 4.5 Hz (7 Hz with the solver's 2.5 Hz `AdditionalDamping`, which sums): it hangs 4–7 deg from
  down after the stop and settles in ~1.5 s with no flip over the axe; running, it swings 20–100 deg with the arm.
  Cost: brushing the legs, worst −1.30 cm on 3 frames (a 5 cm particle). Every take starts with the cord straight out
  (the authored pose) and it falls into the hang within the first ~1.5 s; MRQ renders from frame 0, so the raw take
  shows the fall (the loop cut starts later).
- **Isolation held:** the six cloth chains reproduced their baseline numbers exactly in all 12 sweep runs, and the
  pendants (swept along with the feathers under a plain `flap` key, then excluded) reproduce theirs exactly in the
  shipped build; 0 solver events throughout.
- **Native visualisation exists** (`StepDynamicsSolver.VisualizationSettings`: particles, colliders, constraints,
  angle-limit cones), drawn in PIE only with `ControlRig.EnableDrawInterfaceInGame 1` (ControlRig.cpp:70) and
  documented as expensive; the overlay (`dyn_overlay.py`) is the render-side view and now draws every part.
- **Wind (next pass, not built):** turbulence is solver-wide (`SpaceMotion.Drag.ExternalTurbulenceVelocity`, Perlin
  over absolute time, RigDynamicsSimulationSpace.cpp:249) and reaches a particle only through its drag
  (RigParticleSimulation.cpp:88), so "light parts only" needs drag moved from the global `AdditionalDamping` to
  per-preset `Damping`, and speed scaling needs the speed fed in from the AnimBP. *Corrected under "Air and motion
  scale" below:* both halves were wrong.

### Air and motion scale

Design: `docs/specs/2026-10-27-dynamics-air-and-motion-scale-design.md`.

- **The wind note above was wrong twice.**
  - The wind reaches a particle through the same drag that couples it to the body's travel, so moving drag to
    per-preset `Damping` isolates nothing: a part cannot feel no wind and still trail.
  - Speed needs no AnimBP plumbing either. The rig measures its own.
- **No per-axis motion scale exists natively.** The only per-axis conditioning is `VerticalMotionScale` (world Z). The
  drag and inertial multipliers are scalars, and the drag ones are runtime-clamped to [0, 1]
  (RigDynamicsSimulationSpace.cpp:223, 272).
- **The solver's own motion readback is one update stale.** `GetDynamicsSolverData` read before Step returns the last
  update. So correcting the native terms from it lets the full native jolt act at a hard stop, with the correction a
  frame late.
- **What was built:** the rig owns the linear frame coupling. `LinearDragMultiplier` and `LinearEulerAmount` are set
  to 0. Every frame, the rig computes the component's current v = Δp/dt and a = Δv/dt, guarded against teleports.
  - **Drag:** `ExternalLinearVelocity = wind − R·diag(S)·R⁻¹·v`.
  - **Inertia:** a component-space linear acceleration −S⊙(R⁻¹a), through `AddDynamicsParticleForceField` with 1e6 cm
    radii. The default field curves fall to 0 at the surface (RigDynamicsExecutionHelpers.h:303).
  - S = overall × (side, forward, up). The rotations are quaternions only, so turns cannot wind.
- **Parity:** at the defaults the gate passed 185/185 against the pre-change baseline with no rebaseline, with the
  native linear terms off. That is a real test that the rig reproduces the native coupling. Repeat PASS; `--nostep`
  FAIL 97/185; the rig's speed witness read 500.0 cm/s against 499.9 recorded.
- **Sweep:** the numbers are in the `AIR_VARS` comment in `dynamics_rig.py`.
  - Overall 0 / 0.5 / 2 gives a cloth trail of 10 / 22 / 41°, against 32° at the defaults.
  - Forward −0.5 makes the tails lead (−28°).
  - A 300 cm/s wind leans the standing feathers 6.1 cm downwind.
  - SpeedGust 1.0 restores the hood's run flutter to 4.50 cm (the previous flutter settings had 4.70) at a 0.55 s settle.
  - No resets anywhere.
- **Limits:**
  - the turbulence is the native time-only Perlin: coherent across parts, second-scale, every axis zero at whole
    seconds;
  - the gusts also roughly double the cloth chains' run flutter at SpeedGust 1.0;
  - the teleport guard covers position change only;
  - turn invariance of the wind is not exercised (the drive does not turn).

### Group modifiers: per-group flutter

Design: `docs/specs/2026-10-28-dynamics-group-modifiers-design.md`.

- **Per-part forcing exists natively.** `Add Dynamics Particle Force` queues a force on one particle; the solver applies
  it as a frame-long force through every substep and clears it after the step (RigDynamicsSolver.cpp:600-651,
  RigParticleSimulation.cpp:1096-1105). A chain's root is kinematic and ignores it (RigDynamicsSolver.cpp:301-307), so
  a feather's force goes on its tail Null. A key that misses is silent.
- **`PerlinNoise1D` is already (-1, 1)** (UnrealMath.cpp:3625). The noise node's `Lerp(Min, Max, P + 0.5)` therefore
  doubles it at Min/Max -1/1; use -0.5/0.5.
- **`DeltaFromPrevious` reports a delta on a zero-dt evaluation** (only its cache waits for dt > 0,
  DeltaFromPrevious.cpp:34-40). The air-and-motion-scale rig read such a frame as move/1e-4 s; v and a are now held at zero then.
- **Isolation measured:** with the hood flutter on, every non-hood part's trail, stop swing, tip delta, max step and
  clearance matched the amplitude-0 run exactly in all 12 sweep rows. SpeedGust 1.0 had moved them by up to 22.8.
- **The spring sets the flutter rate.** Faster noise is mostly filtered out and costs swing. A stiffer hood spring
  (2.5 Hz / 0.15) settles in 0.30 s (was 0.53) and flutters at ~3 Hz; the force restores the swing.
- **Chosen:** rate 6, speed gain 5 → hood flutter 4.26 cm at 2.96 Hz (previously 4.50 cm at 2.10 Hz), settle 0.30 s (worst
  0.37). Alternative: spring 3.5, gain 7 → 3.53 cm at 3.25 Hz, 0.17 s. Sweep rows in the `GROUP_MODIFIERS` comment.
- **Proof:** parity (amplitude 0) PASS 185/185 against the old baseline with no rebaseline; at the chosen values only
  the 33 feathers failed it (36 rows); rebaselined, repeat PASS 185/185, `--nostep` FAIL 97/185. Cost 1.38-1.55 ms
  against 1.26 (not gated).
- **Limits:** the 33 phases are offsets of one periodic function (repeats after 256/|rate| s), not independent noise;
  `flutter_hz` is a derivative-energy ratio, not a perceived frequency; noise phase is not reset on a solver reset;
  one particle past the reset threshold still resets every group (one solver).

### Behaviours and the necklace

Design: `docs/specs/2026-10-28-dynamics-behaviours-design.md`. Workflow: `docs/dynamics-tuning-workflow.md`.

- **Skinning decides what can be simulated.** Read-only probes over Khaimera LOD0:
  - the necklace pendant is free geometry (652 of 872 dominated vertices bound to it and its descendants only, four
    separate islands, no welds);
  - its cord is part of the 7,795-vertex body surface (one welded island, a 72-vertex seam);
  - the side pieces `necklace_l` / `necklace_r` are painted on: 0 of 121 vertices are their own;
  - every hood feather is 100% free.
  The intake fit now records `skin: {dominant, free}` per flap, fits a flap's tip to its free geometry when that is the
  majority (pendant tip 3.1 -> 8.5 cm; feathers unchanged), and drops a flap under 10% free with a reason.
- **Settings are reusable behaviours** (`Scripts/dynamics_behaviours.json`): `cloth_panel`, `feather`, `hanging_cord`,
  `pendant`, `default`, assigned by preset and kind (or a per-part profile pick) at intake and written into the
  profile. Flaps group by behaviour for their spawner, collider union, particle radius and rest copies. The switch
  left every previously covered part's settings identical (pinned by `test_dyn_behaviours.py`).
- **Global drag beats gravity on a heavy part.** The 2.5/s air drag tuned for cloth trail is ~1,250 cm/s^2 of push at
  500 cm/s. Measured on the pendant (ON minus OFF hang from straight down, standing / running; settle s):

  | Pendant setting | Stand | Run | Settle |
  |---|---|---|---|
  | spring 0.25, damping 3 (first cut) | -11.4 | -15.4 | 0.30 |
  | damping 0 | -11.4 | -15.3 | 0.33 |
  | damping 0, spring 0.1 | -11.6 | -15.7 | 0.58 |
  | mass 4, mass-scaled drag, damping 1 | -11.4 | -13.3 | 0.60 |
  | **mass 8, mass-scaled drag** (shipped) | -11.5 | -7.3 | 0.85 |
  | mass 8, mass-scaled drag, spring 0.1 | -11.7 | -7.3 | 1.13 |

  Absolute: the animation holds the pendant 11.7 deg forward along the chest standing and 2.2 deg running; the shipped
  pendant hangs 0.2 deg from straight down standing and 5 deg back running. Its own damping changes nothing; dividing
  the whole drag by mass (`bScaleDampingByInverseMass`, RigParticleSimulation.cpp:88-110) does.
- **Isolation:** every other group's gated measures were identical to the reference in every pendant row. One open
  observation: in the mass-8 rows only, the axe cords' standing hang (a new, ungated measure over the first seconds
  after spawn, while the cords still settle) moved 0.14-0.18 deg and two chest strips' 0.01 deg. No frame timing
  differed (every frame 1/60 s). Not explained; recorded rather than claimed isolated.
- **Proof:** gate against the previous baseline: only the dropped side pieces (inventory), the pendant and the feathers'
  rest-copy shift (clearance -0.37 cm, trail 0.1-0.4 deg) differed, 21 rows; rebaselined; repeat PASS 177/177; `--nostep`
  FAIL 93/177.
- **Limits:** the `assign` rules are starting points (`cloth.flap -> feather`, `strap.flap -> pendant`), reviewed per
  hero; painted chains are not detected; the engine's chain search includes every descendant of a root.
- **Editor slowdown:** twice during sweeps the editor ran at ~0.05 fps (6 frames in 2 min) and a drive recorded
  nothing within 240 s; system RAM was not exhausted. A re-run passed. `dyn_sweep` reports such candidates as FAILED
  and still restores the shipped rig. *Most likely explained during the heavy-cloth pass below:* GPU memory pressure.
  A 4K render ran at the same ~0.05 fps with GPU memory nearly exhausted and the editor spilling to system memory;
  free system RAM was not the limit. `dyn_render.py` now cancels itself under 1.5 GB
  free and takes `--res=`; check free GPU memory before a run.

### Heavy cloth and strip twist

Problem: the loincloth is heavy but floated backwards like a cape with little bounce, where heavy cloth picks up
more of the body's motion; the chest strips behaved the same and did not twist. Design:
`docs/specs/2026-10-28-dynamics-heavy-cloth-and-twist-design.md`.

- **Engine facts** (read for this pass, `Plugins/Experimental/ControlRigDynamics`):
  - no twist exists: particles are points, a bone with one particle child gets the minimal arc between its animated
    and simulated directions (`RigDynamicsSolver.cpp:849-900`); successive arcs compose a few degrees of incidental roll
    (measured 1-5 deg RMS on the strips, ~20 deg on the front loincloth's tail-aimed last bone);
  - mass reaches the dynamics only through `bScaleDampingByInverseMass` (drag) and relative-mass constraint splitting;
    gravity, the inertial forces, the acceleration-mode spring and the angle limit are mass-independent
    (`RigParticleSimulation.cpp:45-74, 1007-1037`);
  - the angle limit is a position projection with no damping term (`:250-329`).
- **Heavy-cloth sweep** (all six cloth chains; rear loincloth trail / stride bounce = run-trail std / stop swing /
  settle; worst clearance over the group in cm; 0 solver events in every row):

  | Candidate | Rear trail | Rear bounce | Rear settle | Strip bounce | Front loincloth clearance |
  |---|---|---|---|---|---|
  | previous shipped (angle limit 30/6) | 30.1 | 2.08 | 0.50 | 3.5-7.0 | -4.36 |
  | DampingRatio 0.2 | 29.8 | 2.52 | 0.92 | 4.4-8.4 | -4.22 |
  | Mass 4, mass-scaled drag | 18.2 | 6.02 | 0.55 | 6.5-8.5 | -5.41 |
  | Mass 4, DampingRatio 0.2 | 17.6 | 6.98 | 1.30 | 8.0-13.8 | -5.66 |
  | Mass 4, DampingRatio 0.2, angle limit 3 Hz | 18.7 | 7.66 | 0.98 | 7.9-14.2 | **-3.84** |
  | Mass 4, DampingRatio 0.2, TargetMode 1 | 18.6 | 5.96 | never | 7.2-14.8 | -6.81 |
  | Mass 8, DampingRatio 0.2 | 16.4 | 8.95 | 1.28 | 7.6-14.7 | -6.34 |
  | **Mass 8, DampingRatio 0.2, angle limit 3 Hz** (shipped) | **17.8** | **9.64** | 1.03 | 7.3-13.2 | **-3.78** |

  The angle limit at 3 Hz is what keeps the heavier front loincloth off the thigh (its stop swing grows 32 -> 48 deg).
  The rear loincloth sits 0.9 cm deeper in its rest box (-0.24 -> -1.16 cm). A Mass 6 / DampingRatio 0.3 row was stopped
  before it finished and has no result.
- **Twist modifier**: `cloth_strip` = the heavy values plus `twist {TwistGain, TwistMax}`. Built on 8 strip bones (the
  back strips' lower bones are not flat enough, 0.37 > 0.35). Isolation against TwistGain 0 (same values):

  | TwistGain | Strip roll RMS, run (deg) | Every other measure |
  |---|---|---|
  | 0 | 0.9-5.0 (the solver's own) | - |
  | **1** (shipped) | 12.4-19.1 | identical (max_d 0.000-0.001) |
  | 2 | 24.7-32.9 | identical |

  Parity: the old values with TwistGain 0 reproduced the previous shipped run exactly (every position measure 0.000; the
  only "change" is the new roll measure appearing on a bone the previous run did not record).
- **Limits:** the roll is a cosmetic driver (in-plane bend), not simulated torsion; skinned-edge clearance after the
  roll is not measured (particle centres do not move), so a visual check is the check; the strips' behaviour is a
  per-part profile pick (a profile override), not derived.

### Looser axe cords

Visual check after the heavy-cloth pass: the axe end straps were over-damped, with a springy, slow return. `hanging_cord` had
its own drag 4.5/s on top of the global 2.5/s (7/s in all). Swept (left / right cord: run swing = trail std, stop swing
past straight down, settle s, worst clearance cm with frames under tolerance; 0 solver events in every row; every
other group unchanged, max_d <= 0.001):

| Candidate | Run swing | Stop swing | Settle | Clearance |
|---|---|---|---|---|
| previous, Damping 4.5 | 49.5 / 44.4 | 46.5 / 5.6 | 0.82 / 0.58 | -1.30 (3) / -0.07 (0) |
| **Damping 2** (shipped) | 56.7 / 61.7 | 51.7 / 24.4 | 0.68 / 0.48 | -1.57 (4) / -0.19 (0) |
| Damping 1 | 68.3 / 60.5 | 85.1 / **172.7** (flips over the axe) | 0.48 / 0.65 | -1.73 (5) / -0.89 (0) |
| Damping 0 | 77.2 / 73.3 | 88.4 / 66.4 | 1.27 / 0.65 | -4.71 (6) / -4.72 (4) |
| Mass 4 scaled drag, Damping 1 | 83.2 / 81.0 | 126.9 / 65.3 | 1.18 / 1.03 | -2.32 (7) / -11.86 (9) |
| Mass 4 scaled drag, Damping 0 | 82.7 / 82.0 | 175.8 / 64.4 | 0.87 / never | -9.71 (6) / -9.38 (10) |
| Mass 4, Damping 1, Strength 0.1 | 87.0 / 81.9 | 115.4 / 73.0 | never / never | -2.00 (7) / -8.73 (12) |
| Mass 4, Damping 1, DampingRatio 0.2 | 84.9 / 80.9 | 176.5 / 66.9 | 1.33 / never | -8.75 (6) / -4.77 (5) |

The cords' own drag is what keeps them from flipping over the axe at the stop: half of it frees the swing and
shortens the settle; below that a cord flips or swings through its colliders. Unlike the pendant, mass-scaled drag is
wrong here: a light cord should feel the air.

### Second hero: Aurora

Onboarded by `hero_onboard.py` with no Aurora-specific code (design
`docs/specs/2026-10-29-hero-onboarding-design.md`). What the second hero taught the pipeline, each now a
universal rule:

- **Her travel is on the ROOT bone** (walk 541.7 cm, jog 1276.8 cm per loop), Khaimera's on the pelvis: the in-place
  stage pins whichever track travels, along that track's own straight-line direction (both: across-line excursion 0.0
  cm). After the pin the sway left in place is 6.3 / 7.8 cm.
- **Soft tissue is blended**: her breast bones dominate 0 of 556 weighted vertices, so the flap fit found no tip. A
  blended flap now measures its tip over the vertices it carries a 25% share of (191; tips 4.3 / 6.6 cm), and a
  `deforms_host` behaviour keeps it although it is "painted" on (that rule exists to drop rigid pieces welded to a
  host). Its behaviour turns collision off, so it gets no colliders (and no false penetration scores).
- **Hair particles must be thin**: the 5 cm cloth particle started inside her head, the 30%-of-fit rest-shape rule
  then kept the full head capsule, and the head ejected the hair on the first frame (a visual check showed the hair
  flared at a stand). Behaviours may set `particle_radius_cm` (hair 1.5), and a rest copy is skipped only when
  degenerate (< 1 cm). Worst hair contact -10.3 -> -0.6 cm, standing droop 19.3 -> 10.8 deg on that change alone.
- **Held props get a grip automatically**: a prop-named bone moving geometry under a hand (her `weapon`, 725 verts)
  makes the intake record `held_props`, `dyn_hero` derives a grip on those hands, and the ABP build finds the hero's own
  non-additive jog pose (`Jog_Fwd`). Khaimera's hand-set grip names the same hands.
- **Hair sweep** (12 chains; 0 solver events in every row): droop = ON-OFF standing hang, deg.

  | Candidate | Droop | Run trail | Flutter cm | Settle s | Worst contact |
  |---|---|---|---|---|---|
  | Strength 1 (start) | 10.8 | 47.3 | 1.25 | 0.47 | -0.57 |
  | Strength 2 | 6.2 | 41.3 | 1.17 | 0.28 | -0.70 |
  | Strength 3, DampingRatio 0.2 | 3.4 | 38.3 | 1.05 | 0.10 | -0.10 |
  | **Strength 2, gravity 0.5** (shipped, pending a visual check) | **3.6** | **46.6** | **1.67** | 0.34 | -0.98 |
  | Strength 1.5, 30 deg / 6 Hz limit | 8.9 | 43.4 | 1.13 | 0.33 | -0.49 |

- **Her dress is Chaos Cloth on the mesh** (`dress_one`, `dress_three`): a mesh-level backend the engine already
  simulates. The pawn keeps cloth simulation on (verify reports it); the render is the check.
- **Created assets are saved explicitly**: the first onboarding verified an IK rig and blendspace that
  existed only in memory (the clean-quit check caught it). Every created asset is now saved through the git-ignore
  guard, and verify reports any dirty package in the hero's folder.

### Visual check on Aurora: scalp, dress, the "no simulation" arm

Observed: her scalp poked through her hair, her right leg seemed to have no collision with the draped cloth over her
thigh, and the "no simulation" pawn in the back had identical cloth motion.

- **Scalp:** every hair chain's first simulated bone strayed 5.3-10.1 cm from its animated place in the head's frame
  while running (new measure `dyn_analyze.root_lift`, reported by `dyn_compare` as `lift`): clumps lying on the
  scalp lifted off it. Behaviours may now set `chain_curves` (FParticleChainCurves, multipliers along each chain, root
  to tip). Hair: angle limit x0.1 at the root rising to x1 at the tip, spring x6 at the root, angle-limit stiffness x3.
  Sweep (worst lift cm; run trail deg; flutter cm): none 10.07 / 47.6 / 2.92; curve a 5.61 / 44.3 / 2.56; c1 4.41;
  c2 3.90; **c3 3.58 / 43.4 / 2.39 (shipped)**; c4 (a 25 deg limit) 4.89. The scalp stays covered in the c3 render.
- **Dress:** her stock physics asset has pelvis, spine_03, hands, weapon, head and feet, no thighs or calves, and her
  Chaos Cloth collides with those only. Onboarding now builds `PA_MT_<S>_ClothCollision` (hips, thighs, calves from the
  profile's fit; the smaller side where a garment inflates one) for any hero with clothing assets, and the drive adds it
  to the ON pawn with `AddClothCollisionSource`. The stock assets are untouched.
- **The OFF arm was never "no simulation":** only the generated dynamics were off; the mesh's own cloth ran on both.
  The OFF pawn's cloth is now blended out (ClothBlendWeight 0), set on the placed pawn before PIE: setting it on the
  live PIE component with set_editor_property re-initialised its animation (a 14.9 cm isolation failure the gate caught).

### Hit reaction on Aurora

Design `docs/specs/2026-10-29-hit-reaction-design.md`. A torso reaction:
Physics Control's RigidBodyWithControl in the dynamics ABP before the Dynamics rig, bodies from the profile's fitted
capsules (a C++ toolset builds the physics asset; Python cannot see bodies), pelvis kinematic, the rest simulated with
parent-space angular springs; a radial impulse at the top of the spine; the matched matrix (reaction on/off x hit /
no-hit, plus an impulse-0 control).

- **Parity: at alpha 0 the node is a pass-through**: Aurora's dynamics run a4 matched a3 exactly on every measure.
- **The no-hit fidelity check is what caught every failure**: the first runs "responded and recovered" while both
  reaction-on pawns had folded the same way (head 53 cm / 130 deg off the animation, unhit).
- Wrong theories, tested and dropped: joint limits as the cause (widening them made it worse: they were the only
  support), control-joint collision (`bDisableCollision` changed nothing, bit-identical).
- What the source showed: the asset's angular limits sit at the REFERENCE pose and are soft, so they
  fight an animated pose; a body hanging from a non-adjacent body (head from neck_01 past neck_02) is anchored where the
  animation does not keep it. Now every bone from the pelvis to the head has a body and the joints are angularly free.
- **The springs are far softer than their nominal frequency in this articulated chain**: at "10 Hz" a hit rang at a
  ~0.9 s period with no visible decay. Strength and damping are being swept (`hit_reaction_sweep.py`).
- **Correction (volley): the softness was mostly the solver.** A physics asset runs 6 RBAN position
  iterations by default (`PhysicsAsset.h` `FPhysicsAssetSolverSettings::PositionIterations`), and an articulated chain
  of ~15 bodies does not converge at 6. With arms added, the unhit hand lagged the idle 4.4 cm and a 300 cm/s volley
  swung it 110 cm; spring multipliers saturated (6x: 2.1 cm / 82 cm). At 24 iterations: 0.57 cm / 30 cm. The
  generated asset now carries 32 (Khaimera needed it). Lesson: before tuning drive strength on RBAN, check iterations.

### Corrections to spike steps 3–7

A later check against the installed engine source broke several claims in "Spike steps 3–7". Each is corrected here;
the text above stands as written, so the record shows what was believed at the time.

| # | Claim above | What the source shows | Action and result |
|---|---|---|---|
| 1 | "The ON/OFF difference proves Step finds the solver through the literal key" | The difference alone does not: a Step that silently skips (`RigDynamicsSolverExecution.cpp:243-266`) would still let the tick witness advance | **Causal control added** (`dyn_run.py --nostep`: `ControlRig.Dynamics.EnableStepSolver 0` for the run). Result: the ON/OFF tip difference drops to **0.0 cm on all six chains**. The difference is the solver stepping, so step 3's conclusion now rests on this. Alpha 0 is confirmed pass-through in source (`RigDynamicsSolver.cpp:1204-1244`) |
| 2 | "OFF/OFF differs by 0.0 cm; bit-for-bit repeatable" | Overclaim: positions are rounded to 1e-4 cm before subtraction | Restated: **indistinguishable at the recorded precision (1e-4 cm)**. The alpha was first written in `_sample`, after the first evaluation; it is now set at spawn. Re-run: the numbers are unchanged to four decimals |
| 3 | Position-reset threshold "from the largest particle distance" | The reset tests internal simulated particles before writeback (`RigDynamicsSolver.cpp:965-975`); sockets are output bones. The OFF measurement is a baseline, not internal-state evidence | 225.20 cm is kept as a **chosen margin** over the measured bone baseline. Zero resets across the runs is evidence it does not fire on this drive, not that it bounds the internal state |
| 4 | "Rotation-only readback caps a tip at 2 × chain length" | **False for this rig**: `loin_cloth_bk_03` branches into two feather bones, and the engine writes positions at branches whatever `bReadBonePositions` says (`RigDynamicsSolver.cpp:832-844, 906-915`). SpawnChains includes the descendants (`RigDynamicsExecutionHelpers.cpp:161-179`) | **k_c = 1.5 is an empirical acceptance bound** (measured peak 1.18 ×), not a geometric one. The 2 × argument holds only for unbranched chains |
| 5 | Gate (g) as "ON whole-run minimum ≥ OFF whole-run minimum − 2.5" over the full thigh segment | Unsound three ways: whole-run minima from different frames, the full hip→knee segment rather than the capsule's core endpoints (`RigParticleSimulation.cpp:620-637`), and the right side never went red | **Rewritten** (`dyn_analyze.paired_clearance`): per frame, against the collider core, ON ≥ min(OFF, 14 cm) − 2.5 cm. A plain same-frame ON − OFF rule failed with colliders working (−5.5 cm) on frames where the cloth was 15 cm clear of the leg, hence the min(). Colliders on: margins **+0.08 cm** left, +2.13 cm right, PASS. Colliders removed: **−10.26 cm** left, **−0.92 cm** right, RED on both sides now. **The left green margin is 0.08 cm**, so as written this would be a flaky gate; the gates must settle the bound with more runs (and per-side collider removal) before adopting it |
| 6 | Step 7 overlay | Yaw convention correct (`RotationTranslationMatrix.h:72-78`); OFF/OFF exercises the transform (its own residual 0.00014 cm). Bone tips are not cloth vertices: occlusion and skin weights are absent | Accepted as a limitation of step 7, which the design asked to do on weighted vertices; the visual check of the capture is the check on what is actually visible |
| 7 | MRQ capture record | The Slate pre-tick fired more than once per world step under MRQ: 497 rows, 421 distinct times | Driver now records one row per world frame (dedupe on game time). The design's MRQ-vs-PIE determinism comparison must use the deduped record |
| 8 | `clear` saved the level | It would also save someone's unrelated unsaved edits | `clear` now saves only if the level was clean before `place`; otherwise it leaves it dirty and warns |
| 9 | 12 px as gate (b)'s visibility floor | Pixels have no single world-distance conversion: depth and direction matter | Gate (b) must evaluate the floor through the shot camera, not as a fixed cm value |

