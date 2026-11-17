# Dynamics group modifiers: per-group flutter

Date: 2026-10-28. Follows `2026-10-27-dynamics-air-and-motion-scale-design.md`.

## Problem

On the SpeedGust 1.0 build, the hood feathers flutter too slowly and take too long to stop. Raising the shared speed
gust is not a fix: it doubled the cloth chains' flutter as a side effect. The requirement: modification nodes that let
different groups simulate differently.

## Engine facts (UE 5.8 ControlRigDynamics / RigVM source)

| Fact | Source |
|---|---|
| `Add Dynamics Particle Force` queues (Force, Space, Type) on ONE particle component; calls in a frame accumulate | RigDynamicsParticleExecution.h:911-942, .cpp:466-476 |
| The solver consumes the queue once per Step into `ExternalForce`, as a frame-long continuous force; `Acceleration` is multiplied by mass (mass-independent); particles with InvMass 0 are skipped; the queue is cleared | RigDynamicsSolver.cpp:600-651 |
| A pass-through frame (Alpha <= 0) drops queued forces | RigDynamicsSolver.cpp:1239-1243 |
| A chain's root-most particle (no parent particle) is forced kinematic, InvMass 0 | RigDynamicsSolver.cpp:301-307 |
| `SpawnDynamicsChains` puts one particle component, named `ParticleComponentName` (default), on every root and descendant, Nulls included | RigDynamicsExecutionHelpers.cpp:158-245 |
| `Noise (Vector)` (`RigVMFunction_NoiseVector2`): `Result_k = Lerp(Min, Max, Perlin1D(Value_k*Freq_k + Time_k) + 0.5)`, then `Time += Speed*dt`; per-node state | RigVMFunction_Noise.cpp:41-50 |
| Per-group spring settings already exist: `PRESET_NATIVE` keyed `preset` / `preset.kind` | Scripts/dynamics_rig.py:165-174 |

The shared turbulence (ExternalTurbulenceVelocity) is a time-only Perlin on a fixed clock: coherent across parts,
about 1 Hz. It cannot be made faster or confined to one group.

## Design

A `GROUP_MODIFIERS` table, keyed by the same group selector as `PRESET_NATIVE` (`preset` or `preset.kind`, kind on top
of preset). One modifier type for now, `flutter`, with three public rig inputs per configured group:

- `<G>FlutterRate`: noise speed (noise-time units per second; Perlin1D has ~1 lobe per unit).
- `<G>FlutterAmbient`: acceleration amplitude at rest, cm/s^2.
- `<G>FlutterSpeedGain`: extra amplitude per cm/s of horizontal character speed (1/s).

`<G>` is the selector with the dot removed and CamelCased (`cloth.flap` -> `ClothFlap`). Over-cranking and negative
values are not clamped.

Per Forwards Solve, for every NON-ROOT particle of every part in the group (a root is kinematic and would ignore it):

```
A    = Ambient + SpeedGain * |v_xy|          (|v_xy| = the existing AirSpeedH, the rig's current-frame speed)
n_i  = NoiseVector2(Value = seed_i, Frequency = 1, Speed = (Rate,Rate,Rate), Min -0.5, Max 0.5)   in (-1, 1)^3
AddDynamicsParticleForce(particle_i, Force = A * n_i, Space = World, Type = Acceleration)
```

- `seed_i` is a per-particle, per-axis offset from the particle's name, so feathers flutter out of phase.
- The noise range is Min -0.5 / Max 0.5, which passes Perlin1D's own (-1, 1) through (see design decisions).
- Unrolled: one noise node and one scale node per particle (the noise is stateful, so a loop would share one Time).
  The 33 feather tips -> ~66 nodes + 33 force units chained on Forwards Solve between AirField and SetFrameSpeed.
- Settle stays a spring setting: the `cloth.flap` Strength / DampingRatio in `PRESET_NATIVE` is re-swept now that the
  flutter amplitude no longer has to come from a soft spring.
- The shared `SpeedGust` stays 0.

## Proof plan

1. Parity: the group's flutter amplitudes at 0 -> the gate passes against the existing baseline, no rebaseline.
2. Isolation: with hood flutter on, every non-hood part's metrics (trail, flutter, settle, stop swing) match the
   amplitude-0 run.
3. A flutter-rate measure, `flutter_hz` = RMS(d offset/dt) / (2 pi RMS(offset - mean)) over the run window (exact for a
   sinusoid), next to `flutter_cm`.
4. Sweep Rate x SpeedGain x (Strength, DampingRatio) on `cloth.flap`; pick by hood flutter_hz up, flutter_cm >= the
   SpeedGust 1.0 build's 4.50, settle below 0.53 s, no resets, collider clearance unchanged.
5. Rebaseline the gate at the chosen defaults; repeat PASS and `--nostep` FAIL; render the group-flutter build for
   visual inspection.

## Design decisions

Adopted:
- **Noise range was doubled.** Perlin1D is already (-1, 1) (UnrealMath.cpp:3625); `Lerp(-1, 1, P + 0.5) = 2P`. The
  node now uses Min/Max -0.5/0.5, which passes P through. Verified in source.
- **Zero-dt evaluations misread speed.** `DeltaFromPrevious` returns `Cache - Value` on every call and only advances the
  cache when dt > 0 (DeltaFromPrevious.cpp:34-40), so a moved component on a zero-dt evaluation read as move/1e-4 s.
  This also affected the rig-owned drag and inertia (`2026-10-27-dynamics-air-and-motion-scale-design.md`). v and a are now held at zero when dt <= 1e-6. Verified in source.
- **Seeds come from the particle name** (crc32), not the enumeration index, so adding a part does not reshuffle every
  phase. They are phase offsets of one periodic function: parts decorrelate, they are not independent noise.
- **Public input names are checked for collisions** before any are added.
- **`flutter_hz` is a derivative-energy ratio, not a perceived frequency**: sampled sinusoids read
  (fs/pi) sin(pi f/fs), mixes read their power-weighted RMS rate, and frame-to-frame jitter alone tends to ~13.5 Hz at
  60 fps. It is reported beside flutter_cm and the visual check, never gated.
- **Parity freezes everything else**: SpeedGust stays 0 and the springs are unchanged for the parity run; spring
  changes come after, against a rebaselined gate.
- **Particle keys are verified by effect**: a literal key that misses is silent (RigDynamicsParticleExecution.cpp:472),
  so the sweep checks that every one of the 33 feathers' flutter moved, not just the median.

Recorded, not acted on:
- Isolation is conditional: groups share one solver, so one particle past the position threshold resets every group
  (RigDynamicsSolver.cpp:1318-1325). Particle-particle collision is opt-in (`CollisionParticles`) and not used here.
- Rate 0 holds a constant push and Ambient > 0 never stops exciting the group: both allowed (no clamps), documented.
- Noise phase is not reset on a solver reset, teleport or Alpha toggle; acceptable for an ambient flutter.
- A fresh solver with Alpha <= 0 and dt <= 0 can keep queued forces until a later evaluation
  (RigDynamicsSolver.cpp:1205-1243); the drive never evaluates that way.
- Lookup cost per force node (component-key map lookup) and 99 Perlin evaluations per frame: measured by the cost
  probe, not optimised ahead of a number.

## Implementation decisions

Adopted:
- The zero-dt hold and the dt floor are both at UE_SMALL_NUMBER (1e-8), the threshold `DeltaFromPrevious` advances its
  caches at. With 1e-6 / 1e-4, a 5e-7 s evaluation was held at v = 0 while the caches advanced, and the next frame
  read a ~30,000 cm/s^2 jolt.
- Every force's key, type and space, and every noise node's range, is read back after authoring; a mismatch fails the
  build (a missed key is silent at runtime).
- A particle reached twice (overlapping roots, or two groups) fails the build.
- `flutter_hz` returns None on a repeated or backwards timestamp, and on zero spread at any floor.

Recorded, not acted on:
- An override may add a new selector (e.g. `tail`) when it gives all three inputs: intended, experiments only.
- The gate's identity row does not compare the manifest's script hashes or engine version. Pre-existing.
- The chosen defaults' 4.26 cm is below the design's 4.50 cm amplitude target: chosen for the flutter rate (2.96 Hz);
  rate 4 / gain 7 gives 5.47 cm at 2.47 Hz. The choice between them is a visual one, made on the group-flutter render.

## Known limits

- Perlin1D repeats every 256 noise units: at Rate r the pattern repeats after 256/r s.
- The force is in world space and isotropic; no direction bias (wind direction stays `WindVelocity`).
- A pass-through frame drops the force (engine rule), as for the inertial field.
