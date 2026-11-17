# Dynamics air and motion scale: design

Requirements:
- speed-based noise, so the feathers can flutter at a run without slowing their settle at the stop;
- ambient wind that can be changed at runtime;
- the simulation trick of scaling how much of the character's world motion reaches the simulation, as one overall value plus three axis values (up/down, left/right, forward/back) in the character's own space, built with quaternions/matrices so there are no winding or gimbal problems.

## Engine facts (UE 5.8 ControlRigDynamics, read from source)

| Fact | Where |
|---|---|
| Drag relaxes each particle's velocity toward the ether velocity `-LinVel*LinearDragMultiplier - WxR*AngularDragMultiplier + ExternalLinearVelocity + ExtWxR`, at rate `1-exp(-(Damping+AdditionalDamping)*dt)` | `RigParticleSimulation.cpp:88-114` |
| Linear inertial pseudo-force is `-LinearAcceleration*LinearEulerAmount*InertialForceAmount` | `RigParticleSimulation.cpp:57-73` |
| Native motion conditioning is only `VerticalMotionScale` (world Z) plus magnitude clamps; there is no per-axis scale in character space | `RigDynamicsData.h:300-365`, `RigDynamicsSimulationSpace.cpp:223-243` |
| Turbulence is `PerlinNoise1D(AbsoluteTime + 0/10/20) * ExternalTurbulenceVelocity`, time-only and the same for every particle | `RigDynamicsSimulationSpace.cpp:249-256` |
| External wind and turbulence are authored in world space and rotated into sim space | `RigDynamicsSimulationSpace.cpp:247, 264` |
| `SetDynamicsSolverSpaceMotion` replaces the whole `SpaceMotion` struct on the solver component | `RigDynamicsSolverExecution.cpp:36-45, 312` |
| `GetDynamicsSolverData` returns the sim space's world linear velocity and acceleration, raw (no VMS or clamps), from the last solver update, so one update stale when read before Step | `RigDynamicsSolverExecution.cpp:282-306`, `RigDynamicsSolver.cpp:1285-1316` |
| Native v = Δp/dt and a = Δv/dt (a only after the first update); a teleport zeroes both | `RigDynamicsSimulationSpace.cpp:133-171` |
| `AddDynamicsParticleForceField` queues a force on every particle inside an ellipsoid. Acceleration type is mass-independent. It is consumed once per StepSolver and held through every substep. Pass-through steps discard it. | `RigDynamicsSolver.cpp:600-650, 993-1082, 1239-1244` |
| `LinearDragMultiplier` and `AngularDragMultiplier` are clamped to [0, 1] at runtime; the inertial amounts are clamped only by editor metadata | `RigDynamicsSimulationSpace.cpp:267-273` |

## Design

Every addition lives in the generated rig (`Scripts/dynamics_rig.py`), so any character it builds gets the same controls. No engine edits.

### Rig inputs (public variables; defaults make the rig behave exactly as today)

| Variable | Type | Default | Meaning |
|---|---|---|---|
| `MotionScale` | float | 1 | overall share of the character's world motion that reaches the simulation |
| `MotionScaleUp` | float | 1 | up/down axis |
| `MotionScaleSide` | float | 1 | left/right axis |
| `MotionScaleForward` | float | 1 | forward/back axis |
| `WindVelocity` | vector, world, cm/s | 0 | steady ambient wind |
| `WindGust` | float, cm/s | 0 | ambient turbulence magnitude |
| `SpeedGust` | float, (cm/s) per (cm/s) | 0 | turbulence added per unit of horizontal character speed |

### Motion scale

It scales the **translational** coupling between the character's world motion and the simulation. The animated targets, the kinematic roots, the colliders and the rotational terms still drive the cloth. So S = 0 removes the drag and the jolt from travelling, not all motion. Negative S reverses that coupling, but the final constrained pose is not guaranteed to "lead".

1. Per axis in the component frame: S = MotionScale × (Side, Forward, Up), on component (X, Y, Z). Forward = component +Y was measured from the drive (mean component-space travel (0, 1), both pawns, `khai_dyn_st_rep.json`; mesh yaw −90°). That is the UE skeletal-mesh convention, kept as one constant. The scale is unsigned per axis, so the sign of X does not matter.
2. R is the component's world rotation, `RigUnit_ToWorldSpace_Rotation(identity)`. That is the same context transform the solver uses (`RigVMExecuteContext.h:466`, `RigDynamicsSolver.cpp:1129-1142`). M = R·diag(S)·R⁻¹ is applied as quaternion unrotate → per-axis multiply → rotate, never through Euler angles, so turns cannot wind.
3. **The rig owns the linear frame coupling.** The native linear terms are switched off: `LinearDragMultiplier = 0` and `LinearEulerAmount = 0`, in SpawnSolver and in the per-frame struct. The rig supplies both from **current-frame** motion.
   - p is the component's world location: `ToWorldSpace_Location(0)`.
   - v = −`DeltaFromPrevious`(p)/dt. The node returns previous − current (`RigVMFunction_DeltaFromPrevious.cpp:35`). a = −`DeltaFromPrevious`(v)/dt. Both match the native finite differences (`RigDynamicsSimulationSpace.cpp:135-146`).
   - **Teleport guard:** v and a are zeroed when |Δp| exceeds `TeleportDetection.PositionChangeThreshold`. a is also zeroed on the frame after one, and until two updates have passed (the native `UpdatesSinceReset`/`bAccelerationsValid` rule).
   - **Drag:** `ExternalLinearVelocity = WindVelocity − M·v`.
   - **Inertia:** one `AddDynamicsParticleForceField` per frame: Component space, Acceleration type, identity rotation, direction −S⊙(R⁻¹a), magnitude |S⊙(R⁻¹a)| (the direction pin is normalised, `RigDynamicsSolver.cpp:1038`). Its radii are 1e6 cm. The default multiplier curves fall linearly from 1 at the centre to 0 at the surface (`RigDynamicsExecutionHelpers.h:303`), so a particle within 2 m of the origin gets ≥ 0.9999 of the force.
   - Why not keep the native terms and add a correction: `GetDynamicsSolverData` is one solver update stale, so at a hard stop with S = 0 the full native jolt would act, and the correction would arrive a frame later.
4. Angular terms (centrifugal, Coriolis, Euler, angular drag) stay native and are not scaled per axis.
5. Over-cranking (S > 1) and negative S are always supported, by design. Nothing in the path clamps them: the external velocity is added after the native clamps (`RigDynamicsSimulationSpace.cpp:223-255`), and field magnitudes are signed and unclamped (`RigDynamicsSolver.cpp:1067-1082`). Stability is not guaranteed. The position reset (225.20 cm) can fire under strong forcing, so each mutation run reports resets.

### Wind and noise

- `ExternalTurbulenceVelocity = (WindGust + SpeedGust·|v_horizontal|) × (1,1,1)`, with v the rig's own current velocity. No AnimBP plumbing.
- `SetDynamicsSolverSpaceMotion` is written every frame and replaces the whole struct. The struct comes from one `SPACE_MOTION` table in the author script, which SpawnSolver also uses, so `air_damping` and every other field cannot drift between the two.
- What the native turbulence is (`RigDynamicsSimulationSpace.cpp:11-67, 249-255`):
  - Perlin noise on rig absolute time in seconds, with one-second lattice intervals, so gusts vary over about a second;
  - all three axes cross zero together at every whole second;
  - it repeats every 256 s, and its mean is zero only over a whole period.
  - That makes it a gust source, not a 1.5 Hz flutter source. If it reads wrong, the fallback is our own noise fed in as wind.

### Wind coupling per part

The wind reaches a particle at the same rate as the drag does (Damping + AdditionalDamping). A part cannot be wind-free and still dragged by the character's motion. Per-part response is therefore tuned with per-preset `Damping`. Moving the global drag into per-preset values is deferred: it changes no behaviour and isolates nothing.

## Proof

1. **Defaults are a real parity test.** The rig's own v and a replace the native linear terms, so `--gate` against the current baseline, with no rebaseline, tests whether the rig reproduces the native coupling (a design that only adds zero corrections would prove nothing). Then a repeat PASS, and `--nostep` FAIL as the mutation proof. A witness curve `MT_DynFrameSpeed` (|v|) is recorded and compared with the pawn's recorded travel speed.
2. **Mutations, each run under its own tag:**

| Run | Settings | Expected |
|---|---|---|
| a | `MotionScale` 0.5 | trail and stop swing drop |
| a2 | `MotionScale` 2 (over-crank) | trail and stop swing rise, no solver resets |
| a3 | `MotionScaleForward` −0.5 | the run trail flips to lead, no solver resets |
| b | `MotionScaleForward` 0 only | the forward trail goes away, the vertical bounce stays |
| c | `SpeedGust` > 0 | flutter rises while running; settle after the stop is measured, not assumed (the gusts leave the feathers moving at the stop) |
| d | `WindVelocity` set | standing feathers lean downwind, and the lean stays in the same world direction as the character turns |

3. Unit tests for the axis mapping and the M·v algebra, in Python mirroring the graph math.

## Known limits

- **Teleports:** the native code also treats an orientation change over 30° and a linear acceleration over 30141.6 cm/s² as teleports. The rig's guard uses position change only, so a snap turn passes one frame of linear motion through. The drive has no turn.
- **Conditioning:** VerticalMotionScale and the clamps no longer apply to the linear terms. The per-axis scale replaces them.
- **Hitches:** at most `MaxNumSteps × MaxTimeStep` (4/60 s) is simulated per call, while v and a use the full frame interval. A longer hitch is not tested.
- **Turbulence:** it is coherent across particles, and its spectrum is fixed.
- **First active frame:** a field added before the particles exist does nothing (`RigDynamicsSolver.cpp:1011, 1053`). At that point a = 0 anyway.

## Design rationale

Each engine fact above shaped one choice:
- `GetDynamicsSolverData` is one update stale, so the rig owns the linear coupling from current-frame motion.
- The default field curves fall off toward the ellipsoid's surface, so the field uses 1e6 cm radii.
- The field's direction pin is normalised, so direction and magnitude are set separately.
- `SetDynamicsSolverSpaceMotion` replaces the whole struct, so SpawnSolver and the per-frame write share one table.
- Strong forcing can trip the position reset, so every mutation run reports resets.
- The scale covers only the translational coupling (see Motion scale), not all motion.
- A design that only adds zero corrections would pass parity trivially, so parity replaces the native terms (see Proof).
- The settle after a speed gust is measured, not assumed.
- The inertial amounts are clamped only by editor metadata, not at runtime (unlike the drag multipliers).

Considered and left out:
- a flat-curve literal for the field. Very large radii give the same result within 1e-4 without authoring a curve.
- witnesses for every edge case (alpha transitions, variable frame time, field-edge particles). Out of scope for this design. The speed witness and the gate cover the drive.
