# Dynamics: heavy cloth and strip twist

Date: 2026-10-28. Follows `2026-10-28-dynamics-behaviours-design.md`.

## Problem

On the behaviours build, the loincloth (especially the rear panel) adds too little motion of its own. It should move
like heavy cloth and pick up more of the body's motion; instead it floats backwards like a cape, with little bounce.
The chest strips show the same defect, and their motion has no visible twist.

## Findings

Measured, shipped behaviours build (`khai_dyn_bh_final`, ON pawn / OFF pawn):

| Part | Run trail, ON / OFF (deg) | Stride bounce, ON / OFF (run trail std, deg) | Stop swing past vertical (deg) |
|---|---|---|---|
| loin_cloth_bk | 30.1 / 2.4 | **2.08** / 0.84 | 21.3 |
| loin_cloth_fr | 16.2 / 5.9 | 4.72 / 0.83 | 32.0 |
| torso_cloth_bk_l / r | 34.5 / 14.2, 35.8 / 14.2 | 6.77, 4.98 | 19.9, 16.8 |
| torso_cloth_fr_l / r | 29.8 / 23.2, 35.4 / 23.2 | 7.01, 3.51 | 26.2, 24.2 |

The rear loincloth holds a steady 28° extra trail with the least bounce of any cloth part: the cape that is visible on screen.

Engine facts (UE 5.8 ControlRigDynamics, read for this design; paths under `Plugins/Experimental/ControlRigDynamics/
Source/ControlRigDynamics/`):

1. **No twist exists in the solver.** Particles are points (`RigParticleSimulation.h:104-114`); a bone with one particle
   child is rotated by `FindBetweenVectors(bone direction, particle direction)` (`RigDynamicsSolver.cpp:849-900`), a
   minimal arc: the twist about a strip's long axis is always the animation's. A branching particle's bone keeps its
   animated rotation (`:832-845`).
2. **Mass is inert by default.** Gravity, the inertial forces (`RigParticleSimulation.cpp:45-74`), the target spring in
   acceleration mode (`:1033-1037`) and the angle limit (`:1007-1012`) are all mass-independent. Mass reaches the drag
   only through `bScaleDampingByInverseMass` (`:108-109`), which divides the per-particle AND the global drag.
3. **The cloth spring is near-critical.** Strength 1 Hz (ω² ≈ 39.5/s²), the default DampingRatio 0.5, and the 2.5/s
   global drag adding ≈ 0.20 make ζ ≈ 0.7: little overshoot.
4. **The angle limit is a dissipative projection** (`:250-329`, no damping term): a part held against its cone by the
   drag loses energy on every swing that reaches it.
5. `NumConstraintSubIterations` is already 3 here, so weight travels up the chain.

## Design

### 1. Heavier cloth (behaviour values only)

Retune through the existing behaviour library and sweep tools; no code. Candidates (`dyn_sweep.py`), every cloth part:
mass-scaled drag (Mass 4 / 8 with `bScaleDampingByInverseMass`), a lower DampingRatio (0.2), a softer angle limit (3 Hz),
TargetMode 1 (a direction spring that leaves the root's bob to act as a pendulum). Whether the loincloth and the strips
end up in one behaviour or two (`cloth_panel`, `heavy_cloth`) is decided by the sweep; a split assigns by profile pick.

Measures: run trail (should fall), stride bounce = run trail std and `flutter` cm (should rise), stop swing, settle,
leg and collider clearance (gated; must not regress), solver events (0).

### 2. Strip twist: a post-solve modifier (`twist`)

A flat strip cannot bend within its own plane; pushed sideways, it twists. The solver bends a strip any way. The modifier
turns the in-plane part of each bone's simulated bend into twist about the bone:

- Before Step, read each twisted bone's and its child's animated global positions and the bone's animated rotation.
- After Step: `a` = animated direction, `s` = simulated direction, `N` = the face normal `R_anim · n_local` made
  perpendicular to `a`, `w = N × a` (the strip's width). The in-plane bend is `β = atan2(s·w, s·a)` (as built, per the
  design decisions below; an earlier form used the swing's rotation vector along `N`).
- Twist `θ = clamp(Gain · β · clamp(s·a, 0, 1), -Max, Max)`; the bone's global rotation becomes `AxisAngle(s, θ) · R_sim`, set without
  propagating to children. A rotation about `s` keeps the child's position, so no particle position changes; only
  the skin about the strip's axis turns. Children are twisted by their own `β`.
- Gain may be negative or above 1 (by design, over-crank and negative values are supported).

`n_local` is measured at intake, not set by hand: `hero_intake_fit.py` takes each non-leaf chain bone's dominant vertices
and fits a plane (covariance, smallest-eigenvalue axis = normal) in the bone's local frame, with a flatness
`sqrt(λmin / λmid)`. A bone whose geometry is not flat (flatness above `FLAT_MAX`) gets no twist, with a reason in the
profile. The modifier applies to the parts of any behaviour with a `twist` entry `{TwistGain, TwistMax}`; its rig
inputs are `<Behaviour>TwistGain` / `<Behaviour>TwistMax` (the flutter convention).

Branch bones (the rear loincloth's `loin_cloth_bk_03`) are skipped: the solver gives them the animated rotation.

### 3. Twist measure

The drive records every chain bone's component-space transform (`tm`). `dyn_analyze.twist(on, off, parts)`: per
twisted bone, the twist (swing-twist decomposition about the ON bone's own direction) of `q_on · q_off⁻¹`, its RMS and
peak during the steady run and the stop window. With the modifier at 0 it must read 0.0 against the OFF pawn's
animation-only twist, which is the parity check.

## Proof plan

1. Unit: plane fit on synthetic strips (flat, tube, tilted), the twist measure on synthetic quaternions, behaviour
   validation of the `twist` entry.
2. Parity: the twist nodes built at Gain 0, gate against the behaviours baseline with no rebaseline (PASS).
3. Heavy-cloth sweep, then a twist sweep (Gain 0.5 / 1 / 2, Max 45°), compare per behaviour with isolation.
4. Rebaseline, repeat PASS, `--nostep` FAIL, render close views of the loincloth and strips for visual inspection.

## Design decisions

Design. Adopted:
- **Driver:** `β = atan2(s·w, s·a)` with `N` made perpendicular to `a` and `w = N × a`, faded by `clamp(s·a, 0, 1)`,
  instead of `r · N` (sign-stable, defined for large bends).
- **Snapshots are stored:** the animated transforms are written into rig variables by setters chained before Step; a
  pure read wired only after Step would see the solved pose.
- **Normals are signed** away from the chain's parent bone (mirrored strips mirror), and refused for too few vertices,
  a line-like spread or a non-flat shape (`FACE_MIN_SAMPLES`, `FACE_MIN_SPREAD`, `FACE_MAX_FLAT`), weighted by the
  bone's influence (shared vertices count).
- **Eligibility follows particle topology:** a bone with exactly one particle child, a tail Null included; a branch
  gets none.
- **The parity oracle:** the solver already composes some roll, so "0 twist at gain 0" was wrong; parity is gain 0
  against the unchanged build, and the twist is measured against gain 0 with everything else equal.
- Corrected facts: mass also weights constraint corrections between particles; the angle projection can add energy
  as well as remove it; `NumIterations` (default 1) still makes the target pass child-only.

Implementation. Adopted:
- The gate and `dyn_compare` measure the roll (`twist_rms_deg`, `stop_twist_peak_deg`): the modifier moves no particle,
  so no position measure could see it break.
- The last bone of a strip is measured about its tail.
- A face on a leaf whose tail the build prunes is dropped with a reason (eligibility after pruning).
- Twist values must be finite and `TwistMax` non-negative; the rig takes `|TwistMax|` (the variable is public).

Recorded, not acted on:
- A twist-specific proven-red gate control (a run with the modifier forced off): the gain-0 / gain-1 sweep pair is
  the causal evidence; a gated control needs a runtime switch the drive does not yet set.
- Skinned-edge clearance after the roll is not measured; visual inspection is the check.
- `NumIterations` and `bAccelerationMode = false` (mass-dependent spring) were not swept.

## Open design questions (answered above)

- The driver: answered, `atan2` form adopted.
- Is a non-propagating global rotation set after Step safe against the next frame? Yes: `bReadBoneOrientations` is
  the solver-to-hierarchy readback, not an import; particles keep their own state, and the AnimGraph feeds the rig
  fresh animation every evaluation (`AnimNode_ControlRigBase.cpp:172`).
