# Physical hit reaction, generated per hero

Date: 2026-10-29. One part of a universal, repeatable onboarding system (`docs/specs/2026-10-29-hero-onboarding-design.md`):
a physical reaction layer, its place in the bone-write schedule, and its verification by matched hit / no-hit trials
with the reaction on and off, and recovery within a bounded time. Backend research: a survey of the engine options,
summarised below with its engine-source citations (verified where marked).

## Goal

Any onboarded hero takes a physical hit: an impulse at a body bone pushes the upper body, which springs back to the
animation within a bounded time, while locomotion continues and the hero's cloth and hair react to the hit. Generated
from the hero's profile; no hand work per hero; proven by a matched hit / no-hit A/B with a reaction-off control.

## Backend: Physics Control's RigidBodyWithControl node, in the post-process ABP

| Option | Verdict |
|---|---|
| **RigidBodyWithControl (RBwC) anim node** (Physics Control plugin, not Beta/Experimental, already enabled) | **Chosen.** Immediate-mode physics inside anim evaluation, so it runs BEFORE the Control Rig Dynamics node in the same post-process ABP and the cloth sees the hit. Controls spring each body back to its animated target (the recovery). Takes a physics-asset override and limb setup as node properties; the mesh's queued radial impulses reach it (`SkeletalMeshComponentPhysics.cpp:420-427` -> `AnimNode_RigidBodyWithControl.cpp:1509, 1330-1349`). |
| Control Rig Physics (Beta) | Fallback: per-bone body/joint authoring in a rig, 0.02 s default step against the project's 60 Hz. |
| Physics Control component / Physical Animation component | Rejected: world-scene physics blends AFTER the post-process ABP (`SkeletalMesh.h:2209-2212`), so Dynamics would run on the pre-hit pose. |

## Design

1. **Physics asset from the hero's own body fit.** The intake already fits capsules to the body (`colliders.shapes`,
   the same ones the dynamics collide with). A pure-Python spec (`hit_reaction_core.physics_spec(profile)`) turns the
   body roles into bodies and joints:
   - bodies on the role bones only: pelvis, spine chain, neck, head, clavicles, upper/lower arms, hands, thighs, calves,
     feet. Hair, cloth, twist, finger and face bones get none (they are Dynamics' or nothing's);
   - each body's capsule from the fitted shape owned by that bone when one exists (radius, length along the bone),
     else a capsule along the bone to its role child, radius a fixed share of its length;
   - one constraint per body to its nearest body ancestor, with angular limits from a generic joint table (spine,
     neck, shoulder, elbow, wrist, hip, knee, ankle), each value with a reason.
   A new editor C++ toolset function `UHeroPhysicsTools::BuildPhysicsAsset(MeshPath, DestPath, SpecJson)` builds
   `/Game/MT/<S>/PA_MT_<S>` from that spec (`FPhysicsAssetUtils::CreateNewBody`/`CreateNewConstraint`, capsule
   geometry, limits), never touching the mesh or its stock physics asset (`SkeletalMeshEditorSubsystem.create_physics_
   asset` would overwrite the stock `<Mesh>_PhysicsAsset`, `.cpp:1096-1127`), and returns a read-back (body and
   constraint counts and names; Python cannot see bodies, `PhysicsAsset.h:212-220`).
2. **The node.** `dynamics_graph` inserts RBwC between the grip layer (or Input Pose) and the Control Rig node:
   `OverridePhysicsAsset` = the generated asset; limb setup from roles (legs, spine, arms, head); initial updates make
   the pelvis and legs kinematic (locomotion is the animation's) and the spine, neck, head and arms simulated with
   parent-space controls (the springs that recover the pose); world geometry off; fixed step 1/60 s; node `Alpha` linked
   to a `HitReactionAlpha` variable, default 0: pass-through, so every existing dynamics gate is untouched.
3. **The hit.** In PIE, `mesh.add_radial_impulse(origin, radius, strength, falloff, vel_change=True)` on the hit pawn,
   origin a short way behind the target bone along the hit direction, radius small enough to reach the upper body only.
4. **The drive.** `pie_dynamics_drive` gains an optional `hit` config `{bone, at_s, direction, strength}`: both
   pawns run the reaction (alpha 1); only pawn 0 is hit. A `--reaction-off` control runs the same hit at alpha 0.
5. **Measures** (`dyn_analyze.hit_reaction`): per simulated bone, peak hit-minus-no-hit deviation (cm, deg),
   recovery time (back within max(1 cm, 10% of peak) and staying); the legs' deviation (must stay ~0: locomotion intact);
   the Dynamics parts' response to the hit; finiteness. Proven able to fail: the reaction-off control must show ~0.
6. **Onboarding**: the physics asset is a stage of `hero_onboard.py` (after `pawn`); the node is part of the dynamics
   ABP build. Nothing names a hero.

## Proof

- Unit: `physics_spec` on both shipped heroes (bodies only on roles, every constraint parented to a body, limits from
  the table), the no-rig-name test (`test_hero_intake_core.py`) extended.
- Editor: the physics asset read-back; the ABP compiles with the node; Khaimera's dynamics gate at alpha 0 PASSES
  against his existing dynamics baseline with no rebaseline (the node is a pass-through).
- PIE: Aurora hit / no-hit: upper body deviates, recovers within the bound, legs do not; reaction-off shows nothing.
- A visual check of a render.

## Amendments to the design above

- **Smallest proof first: a torso reaction.** Bodies on the pelvis (kinematic anchor, in no limb), the spine chain,
  neck and head (simulated, one "Spine" limb, parent-space controls only). Arms and legs carry no bodies and ride the
  hierarchy. Arms/legs come only after the torso passes every check below, and on a second hero.
- **Explicit configuration, never defaults:** body `PhysicsType` written into the asset (RBwC treats only
  `PhysType_Simulated` as simulated; `CreateNewBody` leaves `Default`); `bEnableControls` true (default false); limb
  `bCreateWorldSpaceControls` false; non-zero parent-space strength (a frequency in Hz: ω = 2π·strength); the asset's
  `SolverSettings.FixedTimeStep` = 1/60 (the node copies the asset's, it has no writable one).
- **Body parents from the real skeleton** (nearest body ancestor), not role order.
- **The fitted capsules exactly:** a body takes the profile's fitted shapes' local geometry (centre, rotation, radius,
  cylinder length; Aurora's spine_03 has two offset capsules); a bone with no fitted shape gets a capsule to its aim.
- **Self-collision is off for every pair** (`bDisableCollisionsByDefault`, stated, not incidental): the reaction is the
  controls against the hit, not bodies pushing each other.
- **The hit is a radial impulse at body centres of mass** (not a bone-targeted impact): the record keeps the resolved
  origin, radius, falloff, the frame applied, `bIgnoreRadialImpulse`, and each body COM's distance. The component's own
  physics bodies also receive it, so the run records whether any of them simulate.
- **Alpha stays 1 from spawn** in reaction runs (an alpha 0 -> 1 change resets the simulation and can swallow a hit on
  that frame). Dynamics-only builds keep alpha 0 (a pass-through for the pose; the node still allocates state).
- **The matrix:** reaction on/off × hit/no-hit, Dynamics identical. Pass needs a positive response after impact, a
  bounded recovery with a dwell, AND no-hit fidelity (reaction-on no-hit against reaction-off no-hit: the springs must
  not sag the pose). Then impulse delivery disabled must turn the positive-response check red.
- **Manifest identity** gains `hit_reaction_core.py`, the C++ builder's version and the generated asset.
- Recorded, not acted on: anatomical joint frames across skeletons (limits are applied in the child's bone frame;
  hinge joints are out of the torso scope), LOD transitions and offscreen resumption (the harness forces LOD 0), moving
  -space inertia (`WorldAlpha` 0 by default), world collision (off).

## Implementation findings (and the sweeps)

- Every bone from the pelvis to the head has a body; the asset's joints are angularly free; reacting bodies feel no
  gravity (body modifier); control joints keep collision off.
- The springs act far softer than their nominal frequency in this chain and do not remove the lag behind a fast
  animation (~6 cm / 24 deg while jogging at any stiffness), so the reaction is a TRANSIENT layer: alpha 0 (an exact
  pass-through) until just before the hit, held 1 s, faded over 0.3 s; identical on hit and no-hit pawns. A reaction
  during locomotion is not claimed.

## Analysis hardening

- The matrix analyses only records its own drives just wrote (a timed-out drive no longer falls back to old files).
- The verdict gates rotation as well as position (fidelity, return, legs), validates every transform (present, finite,
  unit quaternion), separates what the springs gave back before the fade ("springs restore", <= 60% of the peak left)
  from the full return, and requires the secondary parts to react to the hit. Each reproduced false pass has a test.
- The build manifest records the physics asset's described contents and the builder's version, not its path; onboarding
  verify compares a fresh description of the asset with the spec.
- Recorded, not acted on: repeated or overlapping hits and hits during locomotion; per-body impulse COM distances in the
  record; skeletons whose bone axes put "up" off local Z (the in-place pin assumes horizontal X/Y); a chain's other
  branches beyond its tracked deepest leaf.

## Volley and arms

Requirement: a more intense reaction, from several impacts in different directions.

- **Arms join the reaction body** (the torso passed every check on two heroes, the gate set above): every bone
  from the spine top to each hand (clavicle, upper arm, lower arm, hand and any bone between) gets a simulated body
  from the fit (upper/lower arm capsules), else a capsule to its aim (clavicle) or a sphere (hand). They need no limb
  of their own: `GetLimbBones` (PhysicsControlOperatorNameGeneration.cpp:275-327) gives the "Spine" limb every body
  below spine_01, so the arms take the same parent-space springs. Legs stay kinematic. A held prop rides its hand.
- **A volley, not one hit**: `hits` is a list (one hit = a list of one). The volley is derived from roles: the spine
  top from the left, the head from the front, the lowest spine bone from the right, the middle spine from behind,
  0.4 s apart; stronger and more local than the single push (a smaller radius, so each impact moves its own region).
  The schedule arms before the first impact and holds until hold_s after the last.
- **Measures**: per impact, the target bone's hit-minus-unhit offset must jump along the impact direction within
  0.1 s (an impulse, not the springs' slower recovery from the previous one); the peak covers the volley; "springs
  restore" and "returns" count from the LAST impact; fidelity, legs and the no-impulse control cover arms too.
- **Overlay**: the render's record carries the reaction bodies; dyn_overlay draws them (capsules), the joints
  (each body's bone origin, linked to its parent) and an arrow per impact.

### Findings (Aurora/Khaimera runs v1-v4)

- **The "soft springs" were an unconverged solver.** The physics asset's default 6 position iterations: at 1x arm
  springs the unhit hand lagged the idle 4.4 cm and the volley swung it 110 cm. Iterations are part of the spec
  (32; onboarding writes and verifies them): hand lag 0.57 cm (Aurora), 0.92 cm (Khaimera; 1.18 at 24).
- **Arms are their own limbs** (listed before the spine, which would otherwise take them) with a 6x strength
  multiplier through the node's initial updates on the `ParentSpace_Arm_<s>` sets (RigidBodyWithControl.cpp:457-461,
  830-857: applied once, to the controls themselves). At 24 iterations 1x still lagged 1.8 cm.
- **Impacts scale with the hero**: origin offset and radius are shares of height (0.08 / 0.22); fixed at Aurora's
  15 / 40 cm they reached almost nothing on 210 cm Khaimera. Strength 450 cm/s.
- **Measurement corrections:** the per-impact check subtracts the motion under way (pre-impact velocity; the plain
  displacement passed a recovery with no second impulse), projects in the component frame (the record's transforms
  are component space; v1 measured along the wrong axis), and takes the most-moved reaction body within the impact's
  radius (a low spine body's origin sits on its joint); the impacts must match the schedule one-to-one and on time;
  measured bodies come from the recorded reaction body; "returned" must hold to the end of the record; the springs
  are judged against the last impact's own peak; records of different lengths are refused.
- **Further corrections:**
  - The residual extrapolates velocity AND acceleration. Constant velocity passed a 110 exp(-3t) recovery with no
    second hit (2.2 cm) and missed a real 3 cm one on it (0.8 cm); both are now tests.
  - Delivery compares each applied impact's config and receiving pawns with its plan, not just its count and time.
  - The run refuses an onboarding record whose reaction body misses a body role or has stale iterations.
  - A failed iteration save raises. RBwC does read the asset's iterations (AnimNode_RigidBodyWithControl.cpp:
    1269-1275).
  - The overlay draws aim capsules as the builder makes them.
  - The overlay's shadowed loop variable is fixed.
- **Rejected / recorded:** paired replays omitting each impact (a stricter causal test) - four more
  drives per hero for what the velocity residual plus the no-impulse control already bound; per-body COM in the record;
  hand-to-torso penetration and weapon-tip checks (left to a visual check); a coincident-helper fallback in the
  builder (no shipped skeleton has one).

## Articulation, hips and self-collision

Defects in the volley: the impact turned the clavicles and barely the shoulders, elbows and wrists; the hips took
none of the motion; and nothing stopped the arms passing through the torso.

Measured on the merged v4 volley (peak rotation of each joint relative to its parent body, hit against unhit):
Aurora clavicle 33 deg, shoulder 8, elbow 0.6, wrist 0.25, hips 0 (kinematic); Khaimera 42 / 3.8 / 0.2 / 0.2. The
volley drove Aurora's forearm 7.8 cm into the pelvis (no pair collided). Four causes, four changes, then the gates and
the geometry they read.

**Status:** the code below is in; the final Aurora and Khaimera runs with it (re-onboard `--stage physics`, rebuild the
dynamics ABP, `hit_reaction_run` and a visual check) are NOT done yet. Every run measurement here predates the
last set of changes (explicit geometry, volume-weighted centres, the pelvis impact, per-side articulation). An
onboarding record from before them is refused as stale (`hit_reaction_core.onboarded_spec`), so the runs must
re-onboard first.

- **The impact splayed the arms.** The wide radial impact (0.22 x height) gave every body within ~40 cm the same
  outward velocity from one point: both arms pushed apart, pivoting at the clavicles. Impacts are now FOCUSED
  (`hit_reaction_run.volley`): the origin 0.03 x height (`OFFSET_SHARE`) behind the struck body's centre of mass and a
  radius 1.25 x that (`FOCUS_RADIUS_RATIO`), so the struck body alone moves and the rest follow through the joints.
  The centre is `hit_reaction_core.body_com_local`: each shape's centre weighted by its volume (a capsule
  pi r^2 L + 4/3 pi r^3, L the cylinder; a sphere 4/3 pi r^3), the way Chaos computes a body's centre of mass from its
  shape collection (MassProperties.cpp `CalculateMassPropertiesFromShapeCollection`). The drive gets it as ONE
  bone-local point (`body_centre`) and poses it by the bone's world transform (`pie_dynamics_drive._apply_hit`).
  The plain mean of the shape centres sat off the real centre on a body of unequal shapes. At 0.08 x height the
  near-side clavicle and upper arm were still inside (Khaimera: clavicle 57 deg, the upper arm 3.6 cm into the neck).
  A single body needs more than the wide push: 2000 cm/s (Aurora at 450: the waist and back impacts did not land,
  0.5 / 0.7 cm; at 1800 the waist reached 2.0 cm, just at its bound).
- **The waist impact strikes the pelvis itself** (`VOLLEY`: chest from the left, head from the front, pelvis from the
  right, mid-spine from behind, 0.4 s apart). It struck the lowest spine bone, which, held between the now-simulated
  pelvis and the spine above, barely moved under a focused push (Khaimera 1.0 cm, Aurora 2.4 cm at 2000 cm/s). This
  replaces "the lowest spine bone from the right" in the volley section above.
- **The wrist had no lever.** The hand's default body was a sphere ON the wrist joint: 0.25 deg at any spring. The
  hand's capsule now aims at the descendant that best continues the forearm (`hand_aim`: reference pose, up to two
  levels down, at least 4 cm out; the middle finger root on both Paragon rigs, through a metacarpal on one side).
- **Aimed capsules are explicit bone-local geometry** (`resolve_aims`). Every aimed shape (a hand, a clavicle, a
  default capsule, the cloth-collision legs) is resolved from the reference pose into what the builder's aim path
  makes of it (HeroPhysicsTools.cpp:264-281): L = the aim bone's reference position in the owning bone's reference
  frame, centre L / 2, local Z along L, cylinder max(0, |L| - 2r); coinciding bones raise, as the builder refuses
  them. Before this, the overlay, the clearances and the impact centre rebuilt the capsule from the LIVE aim bone,
  which is not the asset's shape: the asset's shape is rigid in its bone's frame. `aim` becomes `from_aim`
  (information only); the slack shrink recomputes the cylinder so the caps stay at the joints. The rotation is
  `capsule_rotation(L)`: local +Z onto L with local X LEVEL (X.z = 0), the roll that survives the builder's FRotator
  round trip (HeroPhysicsTools.cpp:261 stores `FQuat(...).Rotator()`, the describe reads it back with
  `.Quaternion()`, :100). A spec without a reference pose is a draft (aims kept, no pair collides), and every stale
  check refuses it.
- **Per-joint springs.** Each arm control is named `<parent body>_<child>` (PhysicsControlOperatorNameGeneration.h:
  181-207); the initial updates set the `ParentSpace_Arm_<s>` sets, then each joint by its child's role class,
  later entries overwriting (RigidBodyWithControl.cpp:849-865, confirmed against the source). The clavicle is
  stiff (12x), the shoulder 1x, elbow and wrist 0.5x (`ARM_JOINT_MULTIPLIER`). A stiffer joint moves the motion to its
  neighbours: clavicle 12x took it to the shoulder; the shoulder sets how much reaches the elbow. Two joints that make
  one control name ('a' -> 'b_c' and 'a_b' -> 'c' both make "a_b_c"; UE then renames the later one "_2",
  PhysicsControlOperatorNameGeneration.h:106-128, so an update by name lands on the wrong joint or none) RAISE
  (`check_control_names`, case-insensitive as FName, the pelvis's world-space control included). The ABP verifies
  that EVERY intended multiplier entry landed in the node's export, name exact and value to 1e-4
  (`dynamics_graph.missing_multipliers`; an entry at the default 1.0 exports as `(Name="X")` with no Data, which
  reads as 1.0), and refuses to write the per-joint names unless the onboarded spec matches the built asset
  (`physics_mismatch`).
- **The hips.** The pelvis is simulated, held by a world-space control (linear 30 Hz, angular 12 Hz, ratio 3; both
  strengths explicit: a control's default is zero). The thighs are kinematic bodies with no joint: RBwC
  writes every body back (AnimNode_RigidBodyWithControl.cpp:850-866), so the legs stay the animation's ("legs
  untouched" holds at 0.0001 cm, fade included).
- **Self-collision.** Arm bodies below the clavicle collide with the torso bodies. A pair the idle animation brings
  into contact dragged the unhit arm off the animation (Aurora: upper arm 0.46 cm into the hip, colliding, unhit hand
  1.31 cm off at uniform 6x springs, against 0.45 cm without collision), while leaving such pairs out let the volley
  drive the forearms 7.2 cm into the pelvis. So onboarding samples the hero's idle (the locomotion blend space's
  speed-0 clip, 12 poses: `hero_onboard._idle_tms`), shrinks each arm body by the fit's slack (overlaps up to
  1.5 cm, floor 0.6 x the fitted radius) until it clears every torso body by 0.5 cm (`slack_to_clear`), and lists the
  pairs that then clear by 0.5 cm over every idle sample AND by 2 cm at the reference pose (`colliding`). The idle
  bound is decided on the 3-decimal value (as the shrink is sized); the reference bound UNROUNDED, as the builder
  decides it (`Gap >= MinGap`). The list is EXACT: the builder enables every pair on it and FAILS the build on one
  under its `min_clearance_cm` at the reference pose (HeroPhysicsTools.cpp:361-369), where it used to skip it
  silently and leave that arm free to pass through. Overlaps deeper than the slack are anatomy (the shoulder into the
  chest, 11-22 cm) and never collide.
- **The read-back is the asset, shape for shape and pair for pair** (`hero_onboard.physics_mismatch`). The describe
  emits every capsule and sphere of every body (HeroPhysicsTools.cpp:73-106; it read the first one only), each
  paired greedily with a spec shape (`shapes_mismatch`: positions and sizes to 0.01 cm, a capsule's axis to 1e-3 per
  component, either sense); the enabled collision set must equal the spec's `collide` set exactly, a missing pair as
  much as an extra one; plus body types, joints and their angular freedom, the fixed step and the solver iterations.
  Builder `HeroPhysicsTools/4`. And a record must be today's: `onboarded_spec` refuses unresolved aimed shapes,
  colliding pairs without a reference clearance, and any difference from the spec the generator makes now
  (`spec_mismatch`, regenerated through the editor by `hero_onboard.reaction_spec`): a record from an older generator
  passes `physics_mismatch` against the asset it built, both stale together.
- **Gates added** (`hit_reaction_analyze.verdict`):
  - "arms articulate", PER SIDE and per anatomical joint (`ARM_JOINTS`): the clavicle against the spine top, the
    shoulder, elbow and wrist each against the role bone above it, so a twist helper between two roles is spanned,
    never counted alone. On each side shoulder, elbow and wrist each >= 5 deg and the clavicle no more than the largest
    of them; a joint the record cannot measure fails. The pooled per-role-class peaks are printed only.
  - "hips move" (the pelvis's component-space rotation >= 2 deg) and a negative control, "no impulse: hips stay
    still" (< 2 deg on the no-impulse record). Both are read over the REACTION WINDOW (`reaction_window`: the first
    impact's frame to the last impact + hold_s + fade_s), each record over its own window: the no-impulse run gets the
    same volley at zero strength, so its `events['hits']` mark the schedule on its own clock. No recorded hits there
    means no window and hips None, and the control fails closed.
  - "arms stay out of the body": every arm-torso pair, colliding or not, at most 2 cm of EXCESS, taken frame by frame
    as the hit pawn's overlap less the unhit pawn's at the SAME frame (an unhit gap gives no credit). A non-finite or
    unposed shape raises.
  - "every impact lands": each impact's target body, at its volume-weighted centre (`body_com`; a focused impact turns
    a low-spine body about its joint, where its bone origin barely moves), jumps >= 2 cm along the push within 0.1 s
    beyond its extrapolated motion, AND the bodies the impulse reached (`recipients`: the simulated bodies whose
    centre lies within the radius of the recorded origin on the frame before the impact) are that body alone. A
    record that cannot say (no origin, no `mesh_w`) fails.
  - "unhit pose held" (fidelity) covers the legs too: every reaction body and every leg bone, reaction-on against
    reaction-off unhit pawns. `validate` checks every bone the bodies' geometry reads.

### False passes found and closed

1. Aimed shapes were posed from the live aim bone, not as built: `resolve_aims` makes every shape explicit
   bone-local geometry from the reference pose; `onboarded_spec` refuses a record with an unresolved one.
2. An aim bone missing from the record turned its capsule into a sphere on the joint, and `min()` absorbed a NaN
   clearance: `validate` covers every bone the geometry reads (`geometry_bones`); `penetration` raises on a
   non-finite or unposed shape.
3. The impact centre was the plain mean of the shape centres, and the strongest response over the impact's region
   passed a pushed neighbour while the struck body never moved: the centre is volume-weighted (`body_com_local`,
   one bone-local point to the drive), only the target is measured, and the gate requires the recipients to be the
   target alone.
4. The penetration excess subtracted the worst unhit overlap from the worst hit one, taken at different frames: the
   excess is per frame.
5. Articulation pooled both arms and their helpers, so a right arm whose elbow never moved passed behind the left:
   per side, per anatomical joint, helpers spanned.
6. The hips were read over the whole record (a turn after the fade passed) and nothing showed the turn was the hit's:
   windowed to the reaction, plus the no-impulse control.
7. The builder skipped a pair that did not clear; the read-back compared body names and the first shape; a stale
   record passed against its own stale asset: the exact collide list, the builder's failure, every shape and the
   enabled set in the describe, `physics_mismatch` exact, `spec_mismatch` against today's generator.
8. Two joints could make one control name, and the ABP's check passed if the arm multiplier appeared anywhere in the
   export (it kept the set entries and dropped the per-joint ones): `check_control_names` raises;
   `missing_multipliers` checks every entry.
9. Fidelity left out the legs (a lower-leg corruption shared by both reaction-on pawns held "legs untouched", which
   compares the two): fidelity covers the legs.

Tests: items 2-6 and 9 in `test_hit_reaction_analyze.py`; items 1 and 3, the spec side of item 7 and the control
names of item 8 in `test_hit_reaction_core.py`; the multiplier entries of item 8 in `test_khai_dynamics_abp_entries.py`. The builder's
refusal of a non-clearing pair (C++) has no automation test; the onboarding read-back is its check.

**A re-check of the fixes above, looking for inputs that would break each one, found three more, fixed and tested.**

- **No-impulse window clock.** Each PIE session's clock starts at its own first tick (`pie_dynamics_drive._tick`
  t0, taken before the fixed step), so reading the no-impulse record over the hit record's absolute window end lost
  the last 0.3 s of a run whose clock started 0.3 s later, and passed a 5 deg hip turn there. Each record is now read
  over its own window (`hit_reaction_analyze.main`; test
  `test_the_hips_turn_within_the_reaction_and_not_without_an_impulse`).
- **FRotator singularity.** `FQuat4d::Rotator` (UnrealMath.cpp, SINGULARITY_THRESHOLD 0.4999995) snaps pitch to
  exactly +/-90 once the rotation's X lies within ~1.41e-3 rad of +/-Z. `collider_autofit.q_z_to` puts X on -Z for an
  axis along the bone's X (the usual child direction): L = [30, 0, 0.04] read back 1.33e-3 off, past `AXIS_TOL`, on an
  asset built exactly as specified. `capsule_rotation` keeps X level, so the round trip is exact to double precision
  (test `test_an_aimed_capsule_survives_the_builders_frotator`); an older spec's rotation is refused by
  `shape_matches`, which re-onboarding fixes.
- **Unrounded reference clearance.** A true 1.9996 cm rounded to 2.0 and listed a pair the builder then refused. The
  reference bound is decided unrounded; the recorded `ref_clearance_cm` is rounded, so a 2.0 in the record can be a
  pair left out (test in `test_a_pair_collides_only_where_the_idle_keeps_it_apart`).

## Release: one command, every hero, the page from the records (2026-10-29)

The renders, overlays, posters and the breakdown page's figures had been produced by hand-typed commands and hand-edited
HTML, so a re-run could not reproduce them and the page could drift from the runs. They are now one pipeline:

- **Tuning is cross-hero.** `hit_reaction_sweep.py --hero A --hero B <candidates.json>` runs every candidate on every
  hero; a candidate wins only when it passes on all (`SWEEP_PASS_ALL`). The winner is promoted into the generator's
  constants (`hit_reaction_core`), never into a per-hero override.
- **Release.** `hit_reaction_release.py --hero A --hero B`: per hero, the reaction body (`_reaction_spec`; a refused
  record re-runs `hero_onboard --stage physics`, which raises on a body that does not read back), the dynamics ABP
  rebuilt, the matrix, and only on PASS the MRQ take, its reaction overlay cut to the reaction from the take's own
  record (`dyn_overlay --window reaction`: 0.5 s before the first impact to 1 s after the fade), and posters at
  the second impact. The verify stage's locomotion gaps are reported, not gated: the first hero predates the
  onboarding pipeline and always has them. `captures/hit-reaction/release.json` holds per hero the verdict, its
  `hit_reaction_analyze.summary`, the media and the commit; a failed hero gets no summary or media.
- **The page.** `breakdown_page.py` builds `captures/breakdown/index.html` from the tracked template
  `Scripts/breakdown_page.html`: `{{<Rig>.<key>}}` from the summary, `{{<Rig>.media.<key>}}` copies that release's
  media, `{{file:PATH}}` any other file, `{{tests}}` the README count. Any unresolved token stops the build, so a
  failed hero's figures cannot appear.

Tests: `test_hit_reaction_release.py`.

## One behaviour on every hero: inertia-normalised arms, arm impacts, a softer pelvis (2026-10-30)

The cross-hero sweep found no fixed multiplier set that passed both heroes. Aurora passed at clavicle / shoulder /
elbow / wrist 360 / 30 / 7.5 / 7.5 Hz with the pelvis at 6 Hz (p6d); Khaimera's right elbow stayed at 1-3 deg and his
unhit hand 1.3-1.7 cm off at every setting tried (sweeps 3-8).

- **Two refuted theories, recorded.** Stiffening the clavicle raised its share (a stiffer shoulder makes the arm swing
  whole and its inertia lands on the clavicle). Torso contact pinning his forearm: it is pressed 8-9% of the reaction
  window against Aurora's 19-34%, and her elbows turn freely. A contact-aware gate built on that
  premise was reverted unshipped.
- **Cause (verified against the engine source).** An acceleration drive scales stiffness AND
  damping by its PAIR inertia (PBDJointCachedSolverGaussSeidel.cpp), but a joint holds the whole chain below it.
  Khaimera's arm is ten times Aurora's and his clavicle barely heavier, so the same Hz left his elbow 1.6x stiffer
  relative to its chain and his shoulder 20% softer.
- **Rule.** Onboarding records each joint's `pair_inertia` and `carried_inertia` (`joint_inertias`: the engine's mass
  rule, (rho V)^0.75 at 1 g/cm^3, capsule tensors per Chaos, the reference pose's joint positions). Each arm joint
  gets frequency target x sqrt(R) and damping ratio target x sqrt(R), R = carried / pair (`ARM_TARGET_HZ`,
  `ARM_TARGET_ZETA`, calibrated so Aurora reproduces p6d). A heuristic in a one-coordinate model (the chain's
  modes are coupled, the solver's conditioned inertia is unread), held to the runs. `spec_mismatch`
  refuses a record without the inertias.
- **Arm impacts.** Struck only through the torso, an arm moves as much as the torso swings it: Khaimera's right
  shoulder 14 deg against Aurora's 36. The volley adds each upper arm (left from the front, right from behind:
  across the body plane, so the arm swings free rather than being driven into the torso). Six impacts, 0.4 s apart.
- **Pelvis 6 Hz.** At 30 Hz the hip impact moved the pelvis 1.0 cm and Aurora's lower back 0.9: neither landed.
- **Measurement fixes.** `_rot_deg` normalises (6-decimal quaternions read ~0.2 deg on identical poses);
  impact recipients are read on the frame whose pose the drive placed the origin from (`_drive_pose`).

With the shipped generator Khaimera's arms articulated (L 25 / 36 / 20 / 17, R 27 / 49 / 19 / 12 deg) and two
checks remained. A stiffer shoulder target traded them for the clavicle check (1.4x: unhit 1.16 cm, the clavicle
out-turned the shoulder; 1.8x broke Aurora too), so two checks were re-scoped instead (2026-10-30):

- **Unhit pose held: 1.5 cm** (was 1.0). Khaimera's unhit hand sits at 1.43 cm, an error that starts at the shoulder
  (~1 deg) and compounds down an arm ten times Aurora's.
- **Arms stay out of the body: only pairs the idle keeps apart.** A pair overlapping at least `ANATOMY_MIN_CM` (1 cm)
  on every frame of the settled stand (`STAND_S`, 0.5 s before the first impact) is anatomy and not measured
  (counted on the page): Khaimera's upper arm sits 8.8 cm inside his chest capsule at rest. A hairline touch exempts
  nothing.

**Release (2026-10-30), both PASS 15/15, one generator (ff539c3a7052)**, the page's figures from the rendered take
(`hit_reaction_analyze.main(take=)`): the take is judged in place of the matrix's hit run; the controls and the
unhit fidelity stay the matrix's (two drives' clocks differ by part of a frame, which a per-frame comparison read as
20 cm during the jog).

| | Aurora | Khaimera |
|---|---|---|
| peak | 97.4 cm / 152 deg | 86.8 cm / 87 deg |
| impacts (chest, left arm, head, hips, right arm, back) | 26.9 / 14.9 / 41.5 / 6.5 / 12.6 / 2.3 cm | 34.6 / 21.7 / 11.2 / 6.0 / 23.9 / 8.4 cm |
| clavicle / shoulder / elbow / wrist (larger side) | 20 / 46 / 29 / 20 deg | 27 / 49 / 20 / 17 deg |
| hips | 13 deg | 25 deg |
| unhit hand | 0.77 cm | 1.43 cm |
| deepest pass-through beyond the idle | 0.6 cm (2 anatomy pairs) | 0.4 cm (8 anatomy pairs) |
| back to the animation | 0.57 s | 1.17 s |

Corrected before this release: the anatomy hairline (above); page figures from a different run
than the video (the take is now judged); a damping override ignored on the inertia path; a retry that could take the
stalled attempt's late record (now its own tag); the multiplier read-back ignoring order (now checked); `round(None)`
aborting a sweep. Found while releasing: a render left running by an interrupted caller kept rendering into the next
(GPU payloads stalled > 5 s), so `dyn_render.render` now cancels its MRQ job on every exit; and a delete of an
asset carried over from the previous editor session is held for minutes after a start, so deletes retry.

A re-check of those fixes added: the take's volley must equal the matrix hit run's
(`take_problems`; a take at twice the strength passed on its own); the anatomy stand must cover the whole
`STAND_S` without a gap; the render cancels only its own MRQ executor, and a failed cancel neither masks the result
nor skips the clear; a render must deliver every requested frame; the held-delete check reads the file on disk as
well as the registry; `editor_session start` confirms the editor answering the engine's MCP server has this project loaded.
Recorded, not changed: the take's unhit fidelity is read from the matrix (by design: two drives' clocks differ); the
300 s retry window is checked only after a failed call; another project at Zen's default lifetime restarts the shared
server. The release record above predates these analysis-only edits (its generator fingerprint is of the earlier
sources); both takes were re-judged under the final code with identical verdicts and figures.

## Legs that follow the hips, colliders that hug the skin (2026-10-30)

Four defects seen on the breakdown page: the second hero's clip was an older render (hair and leg clipping); the hit
clips ran on into the jog; Aurora's thigh collider pushed the dress off her thigh in a ledge; on the hip impact the
legs stayed on the animation while the pelvis moved ("disconnected").

- **The clips.** The release renders the dynamics clip itself (`dynamics_media`: old frames and cuts removed first, a
  loop older than the release refused) after the build it shows passes its dynamics gate against the hero's baseline
  (`dynamics_gate`, as `dyn_run --gate`). The volley and its overlay are cut to the reaction
  (`dyn_overlay.reaction_cut`).
- **Cloth colliders from skin, not cloth.** The fit records `skin_cm` per segment end (`collider_autofit.skin_radii`:
  the median over 12 sectors of each sector's median distance, in bands at 10-30% and 70-90% along the bone; a band seen
  from fewer than 8 sectors is no measurement). The cloth-collision bodies are tapered capsules, each end the smaller
  side's skin radius, end spheres ON the joints so neighbouring segments overlap at the knee (inset caps met at a
  point there, and a span shorter than its radii put the waist sphere past its bone).
  Aurora's thigh: 11.2 cm constant before, 8.8 -> 6.4 cm now. The run checks the cloth asset's read-back against its
  record (`physics_mismatch`), and the take snapshots the bodies it ran with, which the overlay's cloth layer draws.
- **Legs on the hips.** RigidBodyWithControl writes back every body, kinematic ones too, so the thigh anchors held
  the legs on the animation while the pelvis moved. With IK foot bones the anchors go: each foot is copied into its
  `ik_foot_<s>` BEFORE the reaction (the incoming pose's plant, blends included), and LegIK (2 bones, no twist
  correction, full alpha) solves the legs AFTER it. A skeleton without the IK bones keeps the anchors.
- **Reach.** The first release under leg IK failed "feet planted" (1.8 cm Aurora, 5.8 Khaimera): the
  hit pushed the pelvis beyond the planted leg's reach, LegIK straightened the knee and the foot dragged
  (AnimNode_LegIK.cpp:290). Between the reaction and LegIK a generated Control Rig (`leg_reach.py`) moves the pelvis
  and its subtree by the least offset d keeping every hip within reach of its target, |hip_i + d - target_i| <= R_i,
  R_i the leg's own thigh and shin (incoming pose: nothing stretches) at min(its animated knee bend, 10 deg): a leg
  may straighten to 10 deg, never lock. The first rule, "never straighten past the animated bend", left Khaimera no
  room: his pelvis dropped and his other knee bent 32.4 deg beyond the animation; with the 10 deg
  reserve 28.1. d = 0 without a hit. Closed form for two legs (projection onto one ball if it lies in the other,
  else the nearest point of the two spheres' intersection circle). The formula is written once against an op set:
  `Numbers` evaluates it for the tests (cross-checked by Dykstra's projections) and `RigOps` emits it as RigVM math
  units, so the rig and its tests are the same arithmetic; a fake controller replays the emitted graph in the tests.
  Chain: source -> L2C -> CopyBone x2 -> RBwC -> C2L -> reach rig -> L2C -> LegIK -> C2L. Chosen over FBIK (iterative,
  approximate, "minimal" by bone mass), IK Rig Body Mover (not reach-aware; it undoes the hit) and a native node (same
  maths, new runtime modules).
- **Gates.** "feet planted": the feet only, 0.5 cm / 1 deg, under leg IK ("legs untouched" without it). "knees follow
  the hips", per knee: its geometric flexion within 30 deg of the animation's (a squat fails; the calf's whole relative
  rotation counted its twist too); its side, |pole_hit - pole_unhit| over the larger pole, <= 1 (a knee across the leg
  is ~2; a normalised pole angle read 116 deg off a 0.66 cm pole on a straight leg, so frames whose knees both sit within
  1 cm of their hip-foot line have no side, and are counted); and its length, thigh and shin within 0.5 cm of the
  animation's (a straight leg gives LegIK no bend direction and it SHORTENS the bones instead, AnimNode_LegIK.cpp:363). Leg IK also requires the thighs below the pelvis, which the reach move depends on.
- **Every foot measurement read a socket.** The recorder read bones with `get_socket_transform`, which prefers a socket
  of the same name, case-insensitively; both Paragon meshes have `Foot_L` / `Foot_R` sockets 13.3 cm down the foot. So
  every recorded "foot" was 13 cm off the bone (calf->foot 58.2 cm against the clip's 45.3), and when LegIK bent a knee
  the socket swung against the calf: a 0.72 cm "shortened shin" that was the measurement. Bone reads
  now go through `mt_drive.bone_transform`, which undoes a same-named socket on the bone and refuses one on another
  bone. Earlier foot figures (the first hit-reaction release's "legs untouched", the dynamics baselines' leg clearance)
  were measured at the sockets.
- **Spikes (the failing volley).** Aurora 16/16: feet 0.014 cm; knees bend 4.0 / 17.8 deg off the animation, side
  0.14 / 0.50. Khaimera 16/16: feet 0.008 cm; knees 15.7 / 28.1 deg, side 0.61 / 0.50, lengths 0.0002 cm.

**Release (2026-10-31), both PASS 16/16, one generator (808bc4c94317)**, each with its dynamics gate passing (Aurora
66/66; Khaimera 179/179 after a rebaseline: the inserted leg-IK chain, an identity at reaction alpha 0 up to float
round trips, moved his loin-cloth clearance 0.02-0.05 cm and 1-2 frames and his weapon tail's stop twist 2.7 deg,
reproduced exactly by the rebaseline run):

| | Aurora | Khaimera |
|---|---|---|
| feet planted (was 1.8 / 5.8 cm) | 0.016 cm | 0.009 cm |
| knees: bend off the animation L / R | 11.8 / 12.4 deg | 15.8 / 28.1 deg |
| knees: side L / R | 0.54 / 0.40 | 0.61 / 0.50 |
| shin and thigh length change | 0.0002 cm | 0.0001 cm |

Checks added in this pass: joint-centred tapered caps, skin coverage, leg-IK staleness,
knee side, cloth asset read-back and snapshot, no stale dynamics loop; for the leg reach, the length gate and the
thighs below the pelvis (recorded, not changed: tolerance-exact, VectorSelectBool
deprecated in 5.8, the dynamics gate runs without hits); and the socket reads, the 10 deg reserve, the
geometric bend.

Known, not addressed: the harness forces LOD0, so a lower LOD that strips `ik_foot_*` is unmeasured; the reach move
changes the rendered pose, not the reaction's simulated bodies; the "planted" gate compares the hit foot with the
unhit pawn's, not with a fixed plant.

## One hit, cloth CCD, breast drag, the heatmap (2026-10-31)

- **One hit, then the recovery.** The six-impact volley read as jitter, each impact interrupting the last: it is now
  one impact to the chest from the front (`IMPACTS`). "arms articulate" is re-scoped to what one impact can prove:
  every arm keeps its clavicle within share, and some arm turns through shoulder, elbow and wrist (Khaimera's far arm turned 5.3 / 3.2 /
  3.3 deg, his near arm 26 / 9 / 7). Spike: Aurora 16/16, Khaimera 16/16.
- **The dress trapped in Aurora's thigh at a run**, freed after the stop: continuous collision on every Chaos clothing
  asset, each keeping its own thickness and friction (`UHeroDynamicsTools.SetClothContinuousCollision`; the config is
  not readable from Python).
- **Breasts: zero drag**, every other part unchanged. The engine adds the solver's drag to every particle's own
  (RigParticleSimulation.cpp:88), so the solver's is 0 and the drag is each particle's own Damping: the rig's default
  is `AIR_DAMPING_HZ` and a behaviour's Damping states its whole drag (hanging_cord 4.5, pendant 2.5, soft_tissue 0;
  each the total it had before). No hard stop: drag only.
- **Heatmap of motion on Aurora's mesh.** Per LOD 0 render vertex, the distance between the ON pawn's final position
  and the OFF pawn's (same animation, no simulation, component space), over a fixed 20 cm scale, as the ON mesh's
  vertex colour, shown by an unlit translucent OVERLAY material over the mesh's own textures, so the texture detail
  stays readable under the colour: it tints rather than paints, keeping the lit texture's luminance (SceneColor, in
  scene units: MaterialTemplate.ush:3497) and taking the hue of the yellow -> red ramp, mixed halfway toward the ramp
  itself so the colour reads clearly without hiding the texture (`PAINT` 0.5, opacity 0.9), fading in over the first
  2 cm so what holds to the animation shows its bare texture (`dyn_heat.py`,
  `UHeroDynamicsTools.ApplyDisplacementHeat`; the overlay pass reuses each section's vertex factory,
  SkeletalMeshSceneProxy.cpp:839, so it reads the same colours).
  The positions are computed as the GPU skin cache does (bone skinning from the current matrices, each cloth vertex
  rebuilt from the Chaos simulation through its section's mapping and blended by the cloth weight), with the cloth
  synchronised to the game thread. Reading them through CPU skinning crashed Lumen's card capture twice (a LocalVF
  bound against the wrong buffer). A frame that cannot be measured fails the take. Spike:
  421 frames measured, 65.9 cm peak. The colour buffer is made once and rewritten in place on the render thread
  each frame: `SetVertexColorOverride` every frame flushed the render thread and rebuilt the mesh's render state
  (SkinnedMeshComponent.cpp:5131-5141, 5292), and the release that ran it took 73 min and stalled all interactive
  use while it ran. The release renders only the dynamics takes the page shows (`page_media`, from the template), not both for every hero.
  The reference mesh (cloth blended out) skips the cloth read, which also kept it waiting on its cloth task every
  frame. A render with no new frame for `STALL_S` (300 s) cancels itself (`dyn_render`), as one starved of VRAM
  does: a contended GPU otherwise ran each take to the 30-minute timeout. Its polls time out after 60 s, so a
  wedged editor cannot hold the clock.
- **Every rendered frame measured.** Under MRQ the drive records each row's output frame
  (`MoviePipelineLibrary.get_overall_output_frames`) and lasts to the render's last frame; it used to end at its hold
  and leave the render's last 30 frames with a parked camera and a stale tint. The heat check maps every rendered frame
  to a measured row. Spike (Aurora, 1080p): 452 rows, two before output frame 0 (index -1), then 0..449 one each, all
  measured; peak 73.8 cm; the whole take 36 s.
- **1080p by default** (`dyn_render.RESOLUTION`): `--res=3840x2160` supersamples a final
  look on an otherwise idle GPU.
- **The page.** Khaimera's dynamics clip first; the visualisation pair is Aurora's heatmap and Khaimera's hit-reaction
  overlay; Aurora's joints-and-capsules dynamics overlay is gone; the dynamics-gate row shows the release's own counts.

## Cloth penetration: measured, gated, fixed (2026-10-31)

The heatmap made a defect visible: the cloth passes through Aurora's upper thigh. The requirement is a validation
that detects simulation penetration of this kind. Three flaws in the first design, found before building:
a displacement threshold for "body" let more motion report less penetration; an ON/OFF-pawn
difference let one overlap's allowance pay for another; "nothing found" read as a pass.

- **The measure** (`UHeroDynamicsTools.MeasureClothPenetration`, `dyn_contact.py`), every frame of every drive, for
  every pawn whose cloth simulates: per simulated cloth vertex (LOD 0), its depth behind the closest triangle of the
  limbs the cloth collides with (the cloth collision bodies' bones and their twist leaves; never a Dynamics chain),
  less the depth the same vertex had skinned before the cloth, same frame, same body (the art's own overlap is
  allowed at that vertex only). Behind a face is inside only nearer the limb's axis than the face: an open blade read
  12 cm "inside", and past a skin section's open end a point 20 cm out read 20 cm inside a calf. The search passes any
  limb's half-thickness (20 cm), so no limb that close is clear of the body. Witness per frame: the vertex, its
  section, the body bone, raw and allowed depth, the point and the closest surface point.
- **The gate** (`dyn_gate` 'penetration', absolute): every simulating pawn measured on every frame once its cloth
  simulates, never deeper than the cloth's own collision thickness (Chaos keeps particles that far off the colliders).
- **What it found on Aurora:** 6.3 cm into the right thigh, 3 cm standing. Two causes. (1) The cloth colliders: the
  skin fit sampled the dress itself, so a left/right minimum hid it and shrank the right thigh 10.65 -> 8.82 cm; and
  the capsule sat on the bone, not the limb. Now `collider_autofit.skin_capsule` fits the body's own surface
  (`UHeroDynamicsTools.SkinPoints`: never a cloth section), per side, a circle fit per band centring it on the limb, its
  radii raised until every band is covered: standing 3 -> 0 cm, after the stop 6.3 -> 0.11. (2) The solver: at a run
  the swinging thigh crossed the dress between steps (vertices 3-7 cm inside their collider). Substeps swept with
  `dyn_run --set cloth_substeps=N`: 1 -> 5.14 cm; 2 -> 0.50; 4 -> 0.55. Shipped at 2 (`dyn_contact.SUBSTEPS`).
- Known, not addressed: vertices only (a triangle interior crossing between two is not seen); morph targets are not
  applied; the residual 0.5 cm is one vertex at the back of the hips, below the waist capsule's span.

## Keeping the editor light (2026-10-31)

An editor running a movie-pipeline render competes for the GPU with everything else on the machine, and a stalled
render looks like a hang. Measured, not guessed: a stuck render
showed the GPU idle at 0% with "Active GPU breadcrumbs" and nearly all of the card's VRAM in use (the card paging), and
`rhi.DumpResourceMemory` mid-render (the drive's new `console_at` hook) showed TWO scenes on the GPU: the movie
pipeline's play world and the editor viewport's, each with a 1 GB virtual shadow map pool, its own Lumen scene and
distance fields. The editor session now (`editor_session.py`, and the render scripts such as `mrq_render.py`):
- starts LEAN: hardware ray tracing off for the session and the engine-default 2048 shadow pages, both command-line
  ini overrides (the project untouched); `start --full` keeps the project's rendering for a final look;
- stops its viewports drawing for every render and restores them however it ends (`URenderSessionTools`);
- runs at below-normal CPU priority, inherited by everything it starts and by a release's encodes, and below-normal
  GPU scheduling (`D3DKMTSetProcessSchedulingPriorityClass`, once the editor has a device);
- refuses to start without the VRAM it needs: its measured working set plus headroom for other GPU users
  (`EDITOR_VRAM_MB`, `VRAM_HEADROOM_MB`).
The heat take, before -> after: 239-413 s -> 150 s, 4-16 GPU-breadcrumb stalls -> 0, 2 GB more VRAM left free at the
render's lowest, peak GPU 98% -> 84%. Ray tracing off exposed a harness bug
it had hidden: an off-camera mesh skips its animation update, so the gate's pawns ran without their rig until they ran
into view (ray tracing marks every mesh rendered). The drive's pawns now always tick; both heroes' gates pass in the lean
editor with behaviour identical to their ray-traced baselines (Aurora 67/67, Khaimera 180/180).

## Optimisation passes (2026-11-01)

Each measured before and after; what did not earn its place was reverted.
- **Encodes:** a render encodes only what its caller uses (the release: the loop; the hit take: none, its clips are cut
  from the frames), and the heat legend goes into the loop's one encode. Render CPU mean 26 -> 22%, peak 55 -> 35%.
- **Viewports for the whole session:** reverted: idle, pausing them saved 0.6% of the GPU (the idle editor's own
  share is ~8%). The render-scoped pause stays.
- **Render tier:** a lean session (`-MTLean`) renders at the engine's scalability, not MRQ's Cinematic tier: transient
  render memory 5.1-5.5 -> 3.5 GB, frames within run-to-run noise (mean difference 0.84/255, on the cloth).
- **The drive's cost** is recorded per run (`events['cost_ms']`: pawns, penetration, heat, wall). The penetration
  measure was 245 ms of a frame (103 of a 110 s drive): its topology is now cached per mesh and limb set, a vertex
  outside every limb's capsule is not searched (an inside needs d(P) < d(Q) <= the limb's radius), the search reaches
  2 x radius + the axis (the radius alone does not bound |P - Q|), and the allowance is read
  only for a vertex that is inside. 245 -> 29 ms a frame; a gate run 125 -> 35-43 s. The early-out changes no answer
  (the cached build with and without it: identical per-bone maxima). Open: against the build before the cache, one
  vertex at the back of the hips reads 0.12 cm instead of 0.56 (cloth deterministic across runs of one build; not the
  early-out, not the search bound, not the limb radii, each tested); both are under the gate's 1 cm.

## The garment's rest pose bounds its colliders (2026-11-05)

Aurora's dress stood off her hip in a shelf exactly where its painted (movable) region begins. Cause,
measured before any fix (`cloth_rest.py`): the cloth colliders COVER the limb's skin (`skin_capsule`), and a circle
covering an oval hip overshoots its narrow sides; 63 of her dress's movable particles sat inside the right thigh's
capsule at the reference pose, 4.67 cm deep counting the 1 cm collision thickness, so the simulation shoved them out
the frame it started. Her clothing assets have no physics asset of their own; the added source was the only collider.

- **What is measured is what Chaos simulates**: each clothing asset's LOD 0 physical-mesh
  particles with their painted max distance and the asset's own thickness and physics asset
  (`UHeroDynamicsTools.GarmentPoints`), not the render vertices mapped onto them. The distance is the optimized tapered
  capsule's (`TaperedCapsule.h:113`, the cloth default `p.ChaosCloth.UseOptimizedTaperedCapsule`).
- **The fit** (`garment_clear`) balances two errors, one each way: how far a rest particle sits inside the collider
  (pushed past the thickness Chaos holds it off) and how far the skin's outline sits outside it (where cloth sinks in
  motion). It makes the worse of the two as small as it can, each within the cloth's thickness: the contact model's
  own resolution and the dynamics gate's limit on the sink. For an allowed depth the radii of an axis are solved
  exactly (largest near + far: a concave 1D problem); the axis ends are pattern-searched across the bone, each kept
  inside the skin's core, never standing further off the skin anywhere (along each outline ray from the bone) than the
  plain cover did; the depth is then bisected to where the exposure meets it. A particle nearer the axis than the core
  (each band's nearest outline point, as a line) is the art's own overlap and bounds nothing (counted, `in_core`).
  Radii round down. `cloth_rest` judges the same budget: a push of at most twice the thickness.
- **Three wrong turns, each caught by a gate**: (1) clear every particle, radii on the bone: the left calf's knee end
  10.08 -> 5.75 cm, the dress sank 1.74 cm into the back of her calf at a run; (2) the axis free too: the calf passed,
  the right thigh (8.21 cm, 2.67 exposed) let it sink 1.55 cm; (3) a 1 cm push allowed but not balanced: the calf at
  1.08 cm. Balanced: right thigh near 13.15 -> 10.26 cm, moved 1.0, 0.9 cm inside and 0.9 exposed; rest and dynamics
  gates PASS.
- **Why the stand-off bound**: without it the search covered the oval by growing a capsule away from the garment (a
  synthetic 12 x 8 cm limb: centre 8 cm back, radius 15.8, 16 cm proud of the skin behind), which would hold any
  cloth swinging behind the leg off it in motion.
- **The gate**: the release refuses a clothed hero whose rest clearance fails (`cloth_rest_gate`), before any run.
  The dynamics gate's penetration row still judges the other direction: a smaller collider must not let the dress
  sink into the hip's wide sides in motion.
- Rejected: a per-test isolation matrix of sources (with no asset physics asset
  there is one source to blame, and the rest measure names the body); an outward-displacement row in motion (a push
  in motion is the collider working; the defect was a push at rest, which the rest gate now catches).

## A hit that squats, a garment that settles, the run overlay (2026-11-06)

Defects and requirements found on the v9 release: the dress moves rubbery, most visibly as it settles after Aurora
stops; the hit barely bends the knees, so the leg IK under the reaction is not visible (a squat needs more downward
force or less resistance in the hips); and Khaimera's overlay should show his running take with every collision and
dynamics layer drawn, not the hit take.

- **The squat**: the chest hit keeps its 2000 cm/s push back and adds as much downward (`hit_reaction_run.IMPACTS`
  45 deg down, `STRENGTH` 2828 = the whole push: a normalised direction only redirects the push, so tilting it alone
  took push away from the back). The pelvis's world control damping ratio 3 -> 1 (`PELVIS_DAMPING_RATIO`): the same
  springs, so the unhit pose is unchanged. Swept on both heroes (`hit_reaction_sweep`, which now takes a candidate's
  `impacts` and `hit_strength`): knees l / r deg off the animation, Aurora 12 / 14 -> 41 / 51, Khaimera 16 / 10 ->
  53 / 58, feet planted (0.01 cm), every check passing. A 50 deg tilt at 2000 cm/s took Khaimera's arm articulation away;
  4 Hz linear sent both past 60 deg.
- **The squat is gated both ways**: `KNEE_SQUAT_MIN_DEG` 25 (the deeper knee: the hit must visibly bend the leg IK) and
  `KNEE_MAX_DEG` 30 -> 60 (the old ceiling rejected the squat this change requires; past 60 the hit folds the legs).
- **The garment's settings** are a library entry (`dynamics_behaviours.json` `garment`, a hero override at
  `secondary.garment`, `dyn_behaviours.garment`) the drive sets on every Chaos clothing asset through its interactors
  (`_garment`: `SetNumIterations`, `SetMaxNumIterations`, `SetDamping`); runtime commands, re-applied on every drive,
  never asset edits. `UHeroDynamicsTools.ClothConfigs` reports what the asset authors (every config property, by
  reflection): Aurora's dress 2 iterations, damping 0.0498, no local damping.
- **The settle is measured** (`dyn_heat.garment_settle`) from `ApplyDisplacementHeat`'s new `cloth_motion_cm`: the RMS,
  over the vertices the cloth moves, of how far each one's offset from the animation moved since the last frame. Not a
  mean offset (panels swinging opposite ways cancel in a mean). From when the pawn has stopped, not the input
  release; settled once under 5 cm/s for 0.25 s, a take that ends first is not settled; a rebound is each new peak.
- **Swept one change at a time**: as authored, never settled in the 1.75 s stand, 5 rebounds; 6 iterations
  worse (7, never settled); damping 0.15 0.85 s / 3; local damping 0.3 0.93 s / 2, 0.5 0.73 s / 3; damping 0.1 on top
  gained nothing. Shipped: local damping 0.5 with the asset's own damping (the jiggle goes, the swing stays). The
  release gates it (`garment_gate`: settled within 1.0 s, at most 3 rebounds).
- **The run overlay**: Khaimera's dynamics take records the reaction's bodies and joints without driving them (no
  reaction alphas, no hits), and `dyn_overlay` draws chains, colliders and bodies over the take's own loop
  (`loop_cut.window`). Rendered frames are matched to rows by the row's MRQ output frame (`out`), and a frame no row
  names is refused; the fixed -2 offset remains only for older records. The reaction overlay renders only
  when the page shows it.
- Rejected: separating the pelvis's linear and angular damping (softer hips was the requirement, and both read as
  the hips; one ratio passed on both heroes); calling the overlay a complete physics debugger (it is schematic: rest
  copies collapse to the outermost, shapes rebuilt from bone transforms).

## Open (spike first)

- The project's own toolset can add `AnimGraphNode_RigidBodyWithControl` and set `CharacterSetupData` / `InitialControlAndBodyModifierUpdates`
  by text import (unverified); the node's `SolverSettings.FixedTimeStep` default (unread).
- Whether a body-less bone between two bodies (twist helpers) is handled by RBwC's parent search.
