# Control Rig Dynamics on Khaimera — Design Spec

- **Version:** 1.2 (2026-10-07)
- **Status:** Design. Implementation is split into the plans in §9.
- **Scope:** Phase 1 of 2: UE 5.8 Control Rig Dynamics secondary motion on Paragon Khaimera, gated and
  shown in a new dedicated shot. Phase 2, Control Rig Physics, gets its own spec after phase 1's gates
  are green.
- **Inputs:** `research/ue58-rigging-tools-assessment.md`. It holds the feature research and the source
  citations this spec relies on.
- **Key properties of v1.2:**
  - **Witnesses.** There is no solver-step witness, because no public stepped flag exists. The rig
    declares its curves, and the test uses the curve read that can report a missing curve.
  - **Reset thresholds.** They reference the quantities the engine actually checks.
  - **Settle check.** It measures residual motion, not convergence to the OFF pose.
  - **Bounds.** They have an absolute floor tied to on-screen visibility.
  - **Run validity.** Resets fail a run immediately; INVALID is reserved for measured hitches.
  - **Bone comparison.** It is pinned to component space and excludes the chain's descendants.
  - **Lanes.** They are proven equivalent.
  - **Plans.** They are ordered so the spike carries its own minimal harness.

---

## 1. Goal

Khaimera's loincloth and torso cloth move as secondary motion:
- They trail while he runs.
- They swing and settle when he stops.
- They stay out of his legs.

The rig is authored by script and gated by checks that are proven able to fail. It is shown in a shot
whose frame is the gate's own experiment: two Khaimeras run the same lane, one with dynamics ON and one
with it OFF.

**Why it is worth doing:** until now the project only *uses* a shipped Control Rig (the foot IK rig) and
authors none. This authors a rig from scratch, on a new 5.8 runtime system, for the one roster character
whose skeleton has secondary chains.

## 2. Design decisions

| Decision | Choice |
|---|---|
| Which 5.8 feature first | Control Rig Dynamics, then Control Rig Physics |
| How the cloth gets travel motion | **Real translation**: the gate and shot pawns move under CharacterMovement. Injected air velocity was considered and rejected |

## 3. Architecture

### 3.1 Plugins
Enable `ControlRigDynamics` (Experimental, not on by default) and its dependency `PhysicsControl` in
`MotionTerminal.uproject`.

### 3.2 Assets
The rig binds Khaimera's skeleton, so both assets are machine-local under the gitignored
`Content/MT/Khaimera/`, the same rule as every other Khaimera-bound asset. A tracked script builds them,
which makes them reproducible.

| Asset | Path | Built by |
|---|---|---|
| `CR_MT_KhaimeraDynamics` | `/Game/MT/Khaimera/` | `Scripts/dynamics_rig.py` (new, tracked) |
| `ABP_MT_KhaimeraDynamics` (post-process) | `/Game/MT/Khaimera/` | same script |

- **Asset type:** the rig uses the legacy `UControlRigBlueprint`.
- **Struct paths:** the Dynamics plugin is Experimental, so its struct paths are pinned in one place in
  the author script.

### 3.3 Rig graph

**Construction event**

**1. `Spawn Dynamics Solver`** (`FRigUnit_SpawnDynamicsSolver1`), configured explicitly:

| Setting | Value | Why |
|---|---|---|
| Simulation space | Component | Inertia comes from the moving component |
| Inertial `Amount`, `LinearDragMultiplier` | defaults (1.0, 1.0), recorded | Carry the translation into the cloth |
| `bResetFromPosition` | ON. Threshold = the largest particle distance **from the simulation origin** seen in the OFF pose over the drive, **plus** the allowed excursion (k_c × chain length). Measured in the spike | The check measures distance from the component origin, not excursion from the animated pose. A "3 × chain length" threshold would trip on a chain that sits about 100 cm above the origin |
| `bResetFromKinematicSpeed` | ON. Threshold = 3 × the largest **sim-space target velocity of the kinematic particles** seen in the OFF arm. Measured in the spike | It reads the animated-target velocity in sim space, not CMC speed (`RigDynamicsSolver.cpp:944-956`) |
| `bResetFromEvaluationInterval` | OFF | It fires on editor hitches |
| Teleport triggers | All ON. Thresholds derived from the drive profile (one frame's travel plus a hitch margin; the acceleration threshold above the stop's peak) and recorded | The 100 cm default fires on a 0.17 s hitch at 600 cm/s |
| `MaxTimeStep`, `MaxNumSteps` | explicit, recorded | Substep count depends on delta time |

**2. `Spawn Dynamics Chains`**, one node:
- **Roots:** loincloth back, loincloth front, and the torso cloth roots, finalised by the skin-weight
  audit.
- **`GravityMultiplier`:** set explicitly, because the default is 0.
- **Capsule colliders:** on `pelvis`, `thigh_l` and `thigh_r`.

**Forwards Solve**

**1. `Step Dynamics Solver`**, with `Alpha` from the rig variable `DynamicsAlpha`.

**2. Witness curves**, written with `SetCurveValue`.
- **Each curve is declared as a curve element in the rig's hierarchy.** `SetCurveValue` silently does
  nothing for a name the hierarchy cannot resolve (`RigUnit_SetCurveValue.cpp:14-18`).
- The CR node then writes the curves into its output pose (`OutputSettings.bUpdateCurves` must be on).
- Skeleton curve metadata is not needed on this path.

| Curve | Value | Meaning |
|---|---|---|
| `MT_DynRigTick` | a rig frame counter | The rig executed (both arms) |
| `MT_DynTeleport`, `MT_DynPosReset`, `MT_DynKinReset` | the flags from `Get Dynamics Solver Data` | Solver events, read every frame |

There is **no "solver stepped" witness**. `Get Dynamics Solver Data` has no stepped flag
(`RigDynamicsSolverExecution.h:519-566`). It returns the previous step's data when no step ran, and a
counter driven by the commanded alpha proves intent, not integration. The **outcome** is the evidence
that the solver stepped: the chain moves (§5.2 b). That evidence is proven red by disabling stepping
with the engine's own switch, the CVar behind `CVarControlRigDynamicsEnableStepSolver`
(`RigDynamicsSolverExecution.cpp:237`), with no change to the graph.

**Not in v1, cut for scope:** `weapon_tail`, the feathers, the necklace and `back_moon`.

### 3.4 Hook-up
`ABP_MT_KhaimeraDynamics` is a post-process AnimBP: `Input Pose → Control Rig (CR_MT_KhaimeraDynamics)
→ Output`.
- **Alpha pin:** `DynamicsAlpha` is exposed on the node and
  **linked** to an ABP float variable, never property-bound.
- **Applying it:** it is set per component with `SetOverridePostProcessAnimBP`, and **only** on the gate
  and shot pawns. The crowd, the crowd take and the roster 4-up never get it.
- **LOD:** every pawn with the override is forced to LOD 0.
- **Variables:** they are written on `GetPostProcessInstance()`. A write lands one tick later, so the
  test settles a tick before sampling.
- **Curve reads** always use `UAnimInstance::GetCurveValue(FName, float&)` and require it to return
  true. The float overload returns 0 for a missing curve (`AnimInstance.cpp:2177-2182`), which would make
  any "stays at zero" check vacuous.

### 3.5 Movement harness
Every existing harness holds pawns at `MOVE_None` with the CMC tick off. The new harness does the
following.

**Spawn**
- Both pawns spawn on the **same tick**, on parallel lanes at least 3 × the capsule diameter apart.
- Both use the in-place Khaimera ABP (pelvis travel 0).
- The harness **records** each pawn's spawn frame and first-input frame, and asserts that they are equal.
  This is used instead of a "one tick apart" proven-red, because a one-tick skew can fall inside the
  adjacent-frame allowance.

**Movement**
- CMC tick ON, `MOVE_Walking`, unpossessed, with `bRunPhysicsWithNoController = true`.
- Input comes from `AddMovementInput` on the same frame for both pawns.
- Input accumulation works without a controller. The flag enables the controlled move.
- Rotation settings (`bOrientRotationToMovement`, `bUseControllerRotationYaw`, `RotationRate`) are
  **set explicitly by the harness**, because the pawn BP's values could not be read.

**Lane equivalence**
- The test map has ramp and landing seams (`PieTests.cpp:1910`, around X 500–1064).
- The spike traces both lanes and must find:
  - the same floor Z along each lane,
  - the same physical material,
  - no seam crossing.
- Lane equivalence matters because Khaimera runs the mannequin foot IK, and the in-place clip keeps
  pelvis X/Z motion. Different ground would make §5.2(f) a false red.
- If `Lvl_MT_Test` has no equivalent lane pair, the gate uses its own flat map.

**Drive profile**
These constants are defined once and recorded:
- lane length
- drive speed
- run duration
- the stop method: input release, with braking deceleration and friction recorded
- hold duration

**Stop anchor**
- `t_stop` is the first frame on which CMC speed is ≤ 1 cm/s after input release.
- The stop window is `[t_release, t_stop + W]`. The settle check starts at `t_stop + T`.

**Hitch record**
- The harness records every frame's delta time.
- A hitch is a frame whose delta time is > H × the median delta time. H is a constant.
- §5.2's validity rule uses this record.

**Camera**
- It is owned by the driver and updated **after** CharacterMovement on the same tick.
- It is not attached to the root bone.

The harness does not touch `SpawnArmyIntoPIE` or `army_conductor.py`, so `Army.Budget` is unaffected.

## 4. Spike (Plan A)
The spike carries its own minimal harness, probes and capture. Each is written to be promoted later,
not thrown away.

| # | Question | Pass |
|---|---|---|
| Q1 | Can a script create the CR asset, declare its curve elements, and author Construction and Forwards Solve with the Dynamics units? | The asset compiles, the units resolve by struct path, and the curve elements exist |
| Q2 | Which candidate chains are weighted to visible mesh, at the LODs that render? | Per-chain, per-LOD weighted-vertex counts in research/ |
| Q3 | Does the override run the rig on an unpossessed, translating PIE Khaimera, and can the harness drive the post-process instance and read its curves? | `GetCurveValue(name, out)` returns true and `MT_DynRigTick` advances every frame |
| Q4 | Khaimera's post-process LOD threshold and per-LOD bone removal; a lane pair on the test map that passes the §3.5 equivalence traces | Recorded, or a separate map is chosen |
| Q5 | The OFF-arm measurements that the thresholds are derived from (§3.3), OFF/OFF repeatability (two OFF pawns), and whether the drive profile trips teleport or reset | Thresholds recorded; zero events across 3 runs |
| Q6 | Can the penetration check (§5.2 g) go red when the colliders are removed? | Yes. If not, (g) is not adopted |
| Q7 | **Visibility, measured before anyone judges the ON capture.** At the planned shot framing and render resolution, project the weighted cloth vertices and measure the ON-vs-OFF pixel displacement over the stop. The pixel acceptance threshold is written down **before** the ON capture is reviewed | ≥ the pre-registered pixel threshold. Then one capture is judged by eye |

If Q1 fails, the architecture's named-owner fallback ladder applies. If Q7 fails, the feature does not
earn a render, and the spike findings are the deliverable.

## 5. Verification (Plan B)

Every Khaimera-dependent row reports **SKIP with a logged reason** when the licensed content is absent.
It never reports PASS.

### 5.1 `Verify.KhaimeraDynamicsWired` (default tier, headless)

1. A consumer-backward traversal from the post-process ABP's output pose reaches a Control Rig node whose
   rig is `CR_MT_KhaimeraDynamics`.
2. Its `DynamicsAlpha` pin is linked to a variable get, with no competing property binding.
3. `OutputSettings.bUpdateCurves` is on.
4. Every witness curve is declared as a curve element in the rig hierarchy.
5. Every chain root, terminator and collider bone resolves on Khaimera's skeleton.
6. The Spawn units are in Construction, Step is in Forwards Solve, and the solver key is connected.
7. The configured values match §3.3 and the spike's recorded thresholds.

**Proven red:**
- sever the alpha link
- misspell one root bone
- turn `bUpdateCurves` off
- delete one witness curve element

### 5.2 `Pie.KhaimeraDynamics` (PIE tier)

Two pawns are driven per §3.5. One has `DynamicsAlpha = 1` throughout and the other 0 throughout. The
alpha is never toggled. Samples are taken at matched timestamps. **Every bone comparison is in component
space.**

**Run validity**
- **Any reset flag (position or kinematic) on the ON pawn fails the run immediately.** A reset is defect
  evidence: it can snap back an explosion before writeback.
- A **teleport** flag is INVALID only if the hitch record shows a hitch on that frame or the frame
  before. A teleport with no recorded hitch is a FAIL.
- INVALID runs get a bounded retry budget of 2. A FAIL earlier in the same gate invocation is never
  erased by a later valid pass.

| # | Assertion | What it catches |
|---|---|---|
| a | `MT_DynRigTick` advances on every sampled frame on both pawns, read with the bool overload | The rig never ran (LOD, a bad override, the wrong instance) |
| b | Per chain, the ON-vs-OFF tip delta in the stop window > **max**(k_b × the OFF arm's adjacent-frame delta, the OFF/OFF repeatability floor from Q5, the **visibility minimum** from Q7 converted to world units) | Dynamics inert: severed alpha, step disabled, an empty chain, zero gravity |
| c | Per chain, the ON-vs-OFF tip delta ≤ k_c × chain length across the run | Explosion or runaway |
| d | Every sampled value is finite | NaN or Inf |
| e | From `t_stop + T`, the ON pawn's **frame-to-frame** tip motion ≤ k_e × the OFF arm's adjacent-frame noise, plus the repeatability floor | Never settles, or jitters. This checks residual motion, **not** convergence to OFF: gravity-loaded cloth settles at a valid offset from the animated pose |
| f | Every bone **outside the chain closure** matches between the pawns within their adjacent-frame control. The closure is the roots plus all their descendants, which inherit chain motion legitimately | A mask leak: dynamics writing bones it should not |
| g | *(only if Q6 passed)* Each loincloth particle bone stays ≥ (capsule radius + particle radius − tolerance) from its thigh capsule axis. This is a bone-level check, not a surface check | The chain passing through the leg capsule |

**Bounds**
- Every bound is a constant times a quantity measured in the same run, a physical scale, or a floor
  measured in the spike: repeatability, or visibility.
- The visibility floor is fixed **before** the ON response is reviewed. So (b) cannot be tuned down to
  whatever the ON arm happened to produce.
- The constants are written to `research/` with the commit hash and rationale, and printed on every run.
  Changing one after a red result requires a research entry giving a physical reason.

**Proven red**

| Break | Fails |
|---|---|
| Alpha severed | (b) |
| `CVarControlRigDynamicsEnableStepSolver` = 0 | (b) |
| Colliders removed | (g) (proven in Q6) |
| An unstable setting (extreme strength, zero damping) | (c), or a reset fails the run |
| Override not applied | (a) |
| Pawns spawned or first driven on different ticks | the harness's spawn and first-input assertion |

### 5.3 `Pie.KhaimeraDynamicsCost`
`Army.Budget` spawns only Manny. The new row spawns N Khaimeras with the override and N without it, and
reports the per-instance game-thread and worker delta.

**Ceiling:** per-instance delta × 2 (the hero-ring Khaimera count) must fit within the remaining
**average** headroom under the 4.0 ms gate. Peak is already 4.21 ms against 4.0. It is reported and
never claimed to fit. Phase 1 does not put dynamics in the crowd, so this row is a measurement with a
declared ceiling.

## 6. Deliverable (Plan C): a new shot, not a re-stage

The crowd take and the roster 4-up are **not touched**. Their framing is load-bearing for existing captions and
gates.

The new shot is **"Two Khaimeras, one lane"**:
- Two Khaimeras run the §3.5 profile and hard-stop. Only one has dynamics.
- The camera tracks them, at the framing whose visibility Q7 measured.
- The shot is the before/after. The caption can say: *the frame you are watching is the experiment the
  gate runs*.
- **Determinism (recorded, not asserted):** the driver records the per-frame delta time and the
  chain-tip trace from inside the MRQ render. These are compared against two PIE runs, and the
  divergence is reported.
- Captions name the feature honestly: "UE 5.8 Control Rig Dynamics (Experimental)".
- It gets a new manifest entry and its own render guard. No existing guarded take is repointed.

## 7. Out of scope
- Crowd dynamics, and any change to the crowd take or the roster 4-up.
- `weapon_tail`, the feathers, the necklace and `back_moon` in v1.
- Chains on Manny, Quinn or MetaHuman. None exist (`ScaleBaselines/*.json`).
- Control Rig Physics (phase 2).
- World collision. Dynamics has none in 5.8.

## 8. Risks
- **Experimental API.** The Dynamics units may change in 5.9. Their struct paths are pinned in one place.
- **MRQ vs PIE timing.** §6 measures the gap rather than assuming it away.
- **Hitches.** Paging hitches are recorded and bounded by §5.2's validity rule, so a hitch cannot pass
  as a result or hide a defect.

## 9. Plans (in order)

| Plan | Contents | Depends on | Exit |
|---|---|---|---|
| **A: Spike** | Enable the plugins; write `dynamics_rig.py`; a minimal promotable harness (§3.5), probes and capture; Q1–Q7 | a green PIE tier, run with enough free GPU memory (Q3 onward needs PIE) | Q1–Q7 recorded; the Q7 capture passes visual review |
| **B: Gates** | Harden the harness; §5.1, §5.2, §5.3 with every proven-red | A | Default and PIE tiers green with the new rows; every red recorded |
| **C: Shot** | The §6 shot driver, MRQ trace, manifest entry, render guard, captions | B | Rendered take plus the determinism report |
