# Hero onboarding: any hero from its imported mesh to a driven, dynamics-ready pawn

Date: 2026-10-29. Scope: a universal, repeatable system with no one-off work and no simulatable part held back. This
document covers onboarding; the physical hit reaction built on it is in `docs/specs/2026-10-29-hit-reaction-design.md`.

## Problem

`dyn_hero.resolve` (`docs/specs/2026-10-28-dynamics-hero-agnostic-pipeline-design.md`) expects, per hero `S` (the rig without `Paragon`), a test pawn
`/Game/MT/<S>/BP_MT_TestPawn_<S>` and an in-place locomotion ABP `/Game/MT/<S>/InPlace/ABP_MT_<S>_InPlace`. Only
Khaimera has them, and only two of the steps that made them exist as scripts: `khai_inplace_author.py` (hard-coded to
him) and `pawn_base_migrate.py` (a fixed pawn list; now `Scripts/archive/`). The IK rigs, retargeter, bake, blendspace, child ABP and pawn were
one-off MCP calls.

## Design

One shell-side tool, `Scripts/hero_onboard.py --hero <Rig> [--stage <name>] [--check]`, in the established pattern of
`dynamics_graph.py`: the project's own toolsets (`ops.call`) for what has no editor Python API, `ops.py` for the rest,
every stage delete-and-regenerate (never patched in place), every stage read back independently of what it intended,
one `Saved/MotionTerminalTests/onboard_<Rig>.json` record. Everything per hero comes from the profile (`roles`, mesh,
skeleton) and the `dyn_hero` convention; no rig name in the code (the no-rig-name test covers the new scripts).

Stages, in dependency order (a stage invalidates every later one, so `--stage X` re-runs X and what follows):

1. **preflight** — profile `status: ok`, every role the retargeter needs resolved, the asset namespace unique
   (`dyn_hero.check_unique_shorts`), and `Content/MT/<S>/` git-ignored (a generic `Content/MT/*/` rule; every tracked
   `Content/MT` asset sits at the top level, verified with `git ls-files`).
2. **retarget** — `IK_MT_<S>` under `/Game/MT/<S>/Retarget/` (create_ik_rig with `pelvis_bone`, then
   `IKRigController.set_retarget_root`, both fields: `research/khaimera-retarget-pipeline.md` §2), with the source rig's nine chains
   (Spine, Neck, Head, Left/RightClavicle, Left/RightArm, Left/RightLeg) built from `roles`: Spine = `spine[0]..spine[-1]`,
   Neck = `neck`..the head's parent, Head = `head`, clavicles, arm = upperarm..hand, leg = thigh..ball (or foot). The
   source is the template's own IK rig (`/Game/Spike/IK_Manny`, the mannequin the template plays). Then
   `RTG_MT_Manny_to_<S>` (exact auto-map), `run_op_initial_setup` on the FK op, and a read-back that the FK op retargets
   all nine chains (Bug A: an empty `chains_to_retarget` bakes static clips silently).
3. **bake** — the source clips are DERIVED from the template, never listed: its CDO clip variables (`Anim_Idle`,
   `Anim_Jump`, `Anim_Fall`, `Anim_Land`) plus every sample of its `BS_Locomotion`. The output folder
   `/Game/MT/<S>/Retargeted/` is deleted first (the native bake over existing files crashes),
   then `IKRetargetBatchOperation.run_batch_retarget`. Clip names are `<prefix><source name>`, prefix `<S>_` by convention. Every output must VARY: a
   limb's local rotation range over the clip within a bound of its source's (the TL;DR of `research/khaimera-retarget-pipeline.md`).
4. **locomotion** — `<prefix>BS_Locomotion_2D` mirrors the template's blendspace: same axes, same sample positions,
   each sample's clip swapped for its retargeted twin, then baked. `ABP_MT_<S>` is a child of the template
   (`create_anim_blueprint` with the template as parent) with the five CDO variables pointed at the hero's clips and
   blendspace, re-read after a compile.
5. **inplace** — `khai_inplace_author.py` generalised by hero (keeps its name, per the no-rename decision in
   `docs/specs/2026-10-28-dynamics-hero-agnostic-pipeline-design.md`):
   - EVERY clip the ABP plays is duplicated into `InPlace/` (Khaimera did only the two locomotion clips, which left
     his idle/jump/fall/land feet pinned by dead IK effectors: `ik_effector_manifest.json` `_not_listed_on_purpose`);
   - the pelvis is pinned along each clip's measured NET travel direction d (`p' = p - ((p - p0)·d)d`), only on clips
     whose net travel exceeds a threshold; sway across d, bob, rotations and every other bone are unchanged. On a clip
     travelling along +Y this is exactly Khaimera's Y pin;
   - when the skeleton has `ik_foot_root`, the effectors are baked from the post-pin FK feet; the bake is exact only
     when the leg and IK bones use `Animation` translation retargeting, now READ and asserted instead of documented as unassertable;
   - `<prefix>BS_Locomotion_2D_InPlace` and `ABP_MT_<S>_InPlace` with all five CDO references swapped.
   The existing per-key witness checks, round-trip bounds and originals-untouched checks stay.
6. **pawn** — `BP_MT_TestPawn_<S>` duplicated from `BP_MT_TestPawn` (AMTCharacter parent, capsule 88/34, mesh offset
   (0,0,-90) yaw -90, MaxWalkSpeed 600; Khaimera's pawn reads the same), CDO mesh = profile mesh and anim class =
   `ABP_MT_<S>`, no recompile after the CDO mesh edit.
7. **verify** — read back every asset, CDO reference, clip variance, in-place travel (|net| < 0.01 cm on every pinned
   clip), effector drift (< 1e-3 cm) and the originals' travel, into the record. `--check` runs only this stage, so it
   audits a hero onboarded by hand: Khaimera, whose legacy names come from his extras (`clip_prefix: "Khai_"`).

`dyn_hero` gains `clip_prefix` (extras key, with a `why`), and the onboarding paths (`ik_rig`, `retargeter`,
`retarget_dir`, `bs`, `abp_main`, `inplace_dir`), all from the same convention.

Verification-suite rows (`clip_manifest.json`, `ik_effector_manifest.json`) are data: `verify` prints the rows a hero
would add; adding them is a separate, reviewed edit. `GoldenDiff.MT<S>` stays C++ and out of scope.

## Out of scope / recorded

- Khaimera is NOT re-onboarded: re-baking his clips would invalidate `GoldenDiff.MTKhaimera`, the six ClipVaries rows
  and the current dynamics baseline (`Plugins/MotionTerminal/Tests/DynamicsBaselines/<Rig>.json`). `--check` audits him and reports his known legacy gaps instead.
- The retargeted clips leave hands open (the mannequin's unarmed set); a hero whose clips hold a prop takes a `grip`
  extra, as Khaimera does. Aurora holds nothing.
- Wrist-twist limits and Foot Placement are later modules.

## Design and implementation decisions — adopted

- The pawn plays the verified in-place ABP. Forward locomotion only (the template binds Speed, not Direction): only
  Direction = 0 samples are mirrored. The in-place pin refuses curved travel and verify bounds the whole clip's
  excursion, not its endpoints. Effectors are checked by drift from the FK foot (a still idle is correct); the
  effector-parent hierarchy and every ancestor's translation-retargeting mode are asserted. A rig with a second body
  needs a profile override (with its `by` and `why`) naming the confirmed pelvis. A delete is refused while assets outside the hero's folder
  reference it; every asset the onboarding creates is saved and verify reports any unsaved one.
- Verify compares with an inventory derived from the TEMPLATE (every clip variable, the reachable sample count) and
  re-checks each played clip's limb swing against its template source, with each skeleton's own bone names (the
  source IK rig's chains); travel is read on the skeleton's own root and pelvis names.
- Replacing the reaction body removes the dynamics ABP that referenced it (recorded as `rebuild`).
- Recorded, not acted on: rifle aim offset / ADS remain the mannequin's; effector exactness between keys and in blends.

## Proof

- Unit: role-to-chain mapping (Khaimera's and Aurora's roles give the chains of `IK_Khaimera` exactly), travel-pin
  arithmetic, the derived clip list, convention paths, the no-rig-name test extended to the new scripts.
- Editor: onboard Aurora end to end from nothing; a second run re-creates the same assets (record diff empty apart from
  timings); `--check` on Khaimera passes on everything his assets have.
- PIE: the dynamics drive runs Aurora's pawn with no Aurora-specific code.
