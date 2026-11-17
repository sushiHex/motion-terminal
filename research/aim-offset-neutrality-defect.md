# The aim offset was never neutral — and the rifle was never held

**Date:** 2026-08-04 · **Engine:** UE 5.8 (CL 55116800) · **Symptoms:** visible in the rendered shots.

> **Update: the footage is re-rendered.** Shots 02, 02b, 03, 05, 06, 07 and 09 were re-rendered
> against the current graph into `captures/m10-reshoot/` (`Scripts/shot_render.py`) and the manifest repointed.
> The old rifle take's frames 380–540 are kept as their own beat, `07b_aim_defect` ("Before the fix"),
> so `captures/m9-beatsde/shot7_rifle` keeps render_shot's protection. The rifle take's hand-IK figures were
> re-measured (Manny 10.43 off / 1.34 on; Khaimera 12.78 / 4.47, short of a grip at 113% of arm reach). The
> sections below describe the state before the re-render.

Two defects were visible in the footage: many poses had an inverted left elbow and a twisted upper torso, and
the rifle was not held in the character's hand. They turned out to share a root cause. This records what they were, how they were proven, why every gate stayed green through them, and
what changed.

## TL;DR

`ABP_MT_Locomotion` adopted the stock **`AO_Rifle` aim offset without the rifle pose set it belongs to**.

* **The node was fed the wrong base pose.** `AO_Rifle`'s samples are mesh-space rotation additives whose
  per-bone delta is extracted once, at bake time, as `D = S·R⁻¹` against `ref_pose_seq = MF_Rifle_Idle_ADS`,
  `ref_frame_index = 0`. Over *that* base the delta reconstructs the authored aim pose: `D·R = S`. The
  Template feeds it the **unarmed** locomotion pose, so every frame evaluated `D·(unarmed)` — the
  rifle-stance transform applied to a body that was never in the rifle stance. With the `Alpha` pin unbound
  at its constructor default of 1.0, there was no way to switch it off.
* **The centre sample is NOT identity, and is not supposed to be.** Measured off the assets by
  `Scripts/probe_ao_rifle_neutrality.py` — centre sample vs its own reference, frame 0, local rotations:
  `pelvis` 32.75°, `upperarm_l` 52.66°, `calf_r` 23.84°, `foot_r` 12.61°, worst 52.66° across 15 bones.
  A centre *coordinate* on an aim offset means "aiming forward", not "no change", so a non-identity centre
  delta is correct authoring. Reading that measurement as proof the asset is defective is the tempting
  mistake: `D` is base-dependent by construction, and the number only means something once you say which
  base it is applied to.
* The same missing rifle pose is why nothing puts a hand on the gun: the unarmed clips have open hands, and
  no attachment transform can manufacture a grip. `Scripts/solve_weapon_carry.py` compensated by bending the
  *rifle* to reach the *hand* instead.

## The posture defect

### What it looked like

Head buried forward, shoulders hunched and counter-rotated, left arm drawn across the chest with the elbow
splayed.

**Present in 7 shot rows, from 6 renders: 02, 02b, 03, 05, 06, 07, 09.** "Every shot where the Template is
in `Locomotion`" is the wrong scope, because the state name is the wrong discriminator: the aim node sits *after* the layered locomotion /
upper-body result and *before* foot IK, so it evaluates over Idle, Locomotion, montage overlays, Jump/Fall/
Land, and the rifle take's stationary pose alike. What actually decides it is the render date and whether the shot
renders the Template at all:

* **01** — `captures/m4-roster/cold_open`, rendered 2026-07-16. Predates the node; the bisect table below shows its
  posture is correct.
* **04** — `captures/m4-footik-ab/B_node_present`, rendered **2026-07-17**. The aim node was authored
  **2026-07-18**. It cannot carry the defect.
* **08** — `captures/m9-terminal/verification`, a terminal capture with no character render.

**05 and 09 are two frame ranges of the same 900-frame render** (the four-rig jog / foot-IK lineup render,
[540,900] and [300,540]), so one roster re-render covers both.

### Bisect

| capture | date | posture |
|---|---|---|
| `captures/m4-roster/cold_open` | 2026-07-16 | head up, chest open, correct arm swing |
| roster lineup render | 2026-07-16 | head up, torso open |
| four-rig jog / foot-IK lineup render | 2026-07-29 | head down, shoulders counter-rotated |
| `captures/m9-lane/lane` | 2026-07-30 | same |

Five Template changes land in that window. Four are excluded: the turn-in-place sub-SM is inside the
**Idle** state only; the off-hand `TwoBoneIK` is gated by `WeaponIKAlpha`, default `0.000000`;
`DefaultSlot` + Inertialization is a pass-through with no montage; skeleton-aware foot IK is legs-only.
That leaves **the aim offset**, the only unconditional upper-body addition in the window.

### Measurement

`Scripts/archive/pie_aimnull_ab_drive.py`. The asset is toggled on/off **every tick** and each sampled pose is
attributed to the state active one tick earlier, so compared poses are ONE CALLBACK apart (a frame only
if the callback runs per frame — this project has measured that callback as low as ~3 Hz under load);
`dAA` — the base animation's own drift across an A→A pair, with a B tick sitting *between* the two
endpoints rather than the subject being held in one state — is carried as the control. Idle,
`AimPitch = AimYaw = 0`:

| bone | additive contribution | base-drift control |
|---|---|---|
| `root` | **0.000°** | 0.000° |
| `pelvis` | 32.82° | 0.59° |
| `spine_01` | 32.85° | 0.53° |
| `head` | 18.10° | 1.36° |
| `clavicle_r` | 44.48° | 1.64° |
| `hand_l` | 24.48° | 2.29° |
| `thigh_r` | 37.12° | 0.36° |
| `foot_r` | 21.77° | 0.11° |

A 15–200× separation. Runtime alpha read back as `Alpha (100.0%)` — the unbound pin at its default.

**The control is not like-for-like, and the bias is conservative.** `ab` spans one tick (i → i+1);
`dAA` spans two (i → i+2), because the same-state pair straddling the toggle is the only one
available. `dAA` therefore *overstates* the one-tick drift, so clearing it is a stronger result than
clearing a same-duration control would be — but it is not a same-duration control and is not
described as one.

**Why these world-space numbers say the legs move — the argument, not just the table.** The probe
samples `RTS_WORLD` rotations (`Scripts/mt_drive.py:333`), and world rotation composes every parent's.
That matters: if a subtree were merely being *carried* by the pelvis, every bone in it would read the
**same** angle. Formally, if the pelvis's world rotation changes by `D` and no parent-relative rotation
below it changes, then every descendant goes `W → D·W`, and `quat_angle_deg` — which returns the angle
of the relative rotation — reports exactly `angle(D)` for all of them, independent of each bone's pose.
So deviation from the pelvis's own figure is the signal:

| bone | measured | pelvis (inheritance predicts) | deviation | its own `dAA` |
|---|---|---|---|---|
| `spine_01` | 32.85° | 32.82° | 0.03° | 0.53° |
| `thigh_r` | 37.12° | 32.82° | **4.30°** | 0.36° |
| `foot_r` | 21.77° | 32.82° | **11.05°** | 0.11° |
| `clavicle_r` | 44.48° | 32.82° | **11.66°** | 1.64° |
| `head` | 18.10° | 32.82° | **14.72°** | 1.36° |

`spine_01` is pure inheritance (0.03°, inside its own control). The legs are not: `thigh_r` deviates
12× its control and `foot_r` 100×. **Stated precisely:** this proves the net parent-relative rotation
changed *somewhere along the chain* between pelvis and that bone — not that each named bone's own local
rotation changed. A thigh rotating while calf and foot locals stay fixed would produce exactly this
signature. Either way the motion is in the leg chain, which is what "not upper-body only" claims.

The one alternative explanation is a downstream solver reacting to the changed input pose rather than
the additive driving the legs directly. That is why `FootIKAlpha = 0` was run as a control below.

**The OFF leg is structural, not an alpha-disable.** `AnimNode_RotationOffsetBlendSpace.cpp:82` evaluates the
base pose unconditionally and `:84` guards the additive on `GetBlendSpace()` being non-null, so nulling the
asset is a passthrough enforced inside `Evaluate_AnyThread`. The foot-IK work already paid for assuming an
alpha-disable equals an absent node — that assumption is what made the first foot-IK A/B a confound.

### Two corrections the measurement forced

**The confound guard failed, and it was the guard that was wrong.** `pie_aimsweep_drive.py`'s header states
*"AO_Rifle is upper-body only, so foot_r must stay ~unchanged."* The legs moved 37°/22°. Re-running with
`FootIKAlpha = 0` changed nothing (`thigh_r` 37.12 → 37.04). **That control did not work, and the conclusion
drawn from it is RETRACTED**. `FootIKAlpha` is recomputed by the EventGraph every
update — the C++ suite documents exactly this at `PieTests.cpp:4098`, and relies on it: the foot-IK OFF branch
is reached by forcing `HasMannequinFootIKRig = false`, which works *because* that bool is computed once at
init, "and `FootIKAlpha`, which IS recomputed every update, must then follow it to 0." So the probe's direct
write was overwritten before evaluation on every tick, and "the Control Rig is not the coupler" was never
tested. A delta that does not move when a control is applied is meaningless if the control never applied.

To actually settle it, force `HasMannequinFootIKRig = false` and read `FootIKAlpha` back as 0, or remove the
Control Rig node structurally. Until then, **whether the downstream foot IK contributes to the leg delta is
OPEN.** What survives independently is the asset-level measurement — the centre sample carries local rotations
of `thigh_l` 22.4°, `calf_r` 23.8°, `foot_r` 12.6° against its reference — which is upstream of any Control
Rig and does not depend on this control. The upper-body-only claim is true of the CU-vs-CC *pitch difference*,
which is all the original aim-offset test ever measured; it is false of the additive.

**v1 of the probe never locomoted.** It wrote `cmc.Velocity` directly, so CharacterMovement's tick bled it
back to zero — betrayed by a `thigh_r` control of 0.10° across a supposed 600 cm/s jog. Both phases then
agreed to three decimals, which reads like *"the additive is base-independent"* and was in fact *"both phases
were the same pose."* Fixed by driving through `mt_drive.drive_cmc`.

### The fix

The `Alpha` pin is now bound to a new `AimOffsetAlpha` float defaulting to **0**. Golden diff is one line:

```
+ "Alpha": "AimOffsetAlpha",
```

`ABP_MT_Khaimera` diffs `IDENTICAL` — the child inherits without a baseline change.

Verified both directions by `Scripts/archive/pie_aimgate_verify_drive.py`:

**BOTH LEGS OF THIS ARE OVERSTATED AND ARE WITHDRAWN AS "PROVEN"**. Kept, with the
reasons, because the failure is the same one this document is about — a witness that could not observe what it
was cited for:

* **"proven-OFF" — alpha 0 vs the node genuinely absent: worst bone 1.21°.** The 1.213° is the largest
  *mean*, not the largest value; the observed **max was 2.610°**. And "every bone below its own control" is
  false: `foot_r` read mean dAB 0.094° against a 0.093° control. The figures were reported without saying
  which statistic they were.
* **"proven-ON" — alpha 1 vs alpha 0.** The probe's own witness for the ON phase logged
  `AnimNode_RotationOffsetBlendSpace<W:100.0%> Alpha (0.0%)` — the node read **off** during the leg that was
  supposed to prove it on. `pie_aimgate_verify_drive.py` samples before configuring the new phase and carries
  `_G['prev']` across the phase boundary, so its ON numbers cannot be attributed to alpha 1.

`pie_aimgate_verify_drive.py` is also **not a gate**: `_report` has no threshold and no error path, so it can
only log. To make either claim honestly it must reset phase state, wait for evaluation, assert the runtime
alpha on *both* arms, label mean vs max with sample counts, and commit the producing log.

What is *not* affected: the default-off assertion in `FAimOffsetCommand`, which reads runtime alpha directly
and errors — that one is verified and runs green on all three rigs.

## The rifle defect

`Scripts/solve_weapon_carry.py` sets `RelativeLocation = -(R_rel · grip_local)` so the weapon's `grip_r`
socket lands on the **`hand_r` bone origin** — the wrist joint, not the palm — at a chosen 45°/30° port-arms
diagonal. Its own header says why that orientation: the real rifle's 40.87 cm grip→foregrip span is
unreachable in the earlier cube orientation, so the TwoBoneIK would stall short and the off-hand would never plant on the
weapon. The diagonal lands the foregrip at 84 % of Manny's reach and 80 % of Khaimera's.

The geometry was solved to satisfy a scalar. Nothing poses the fingers — the Template plays the unarmed set —
and the Two-Bone IK moves the left hand's *position*, not its grip. The right hand is unconstrained relative
to the weapon by construction (the weapon hangs off it).

**Not fixed.** The shape of the fix is now known, and it is *not* the one first written here. Feeding the
rifle clips through the existing `UpperBody` seam while the aim node stays downstream of the
`LayeredBlendPerBone` does not work: the node would see the already-blended pose, and its below-the-blend-root
delta (`thigh_l` 22.4°, `calf_r` 23.8°, `foot_r` 12.6°) bypasses the mask entirely and lands on the legs.

**BUILT AND MEASURED 2026-08-14.** What follows was written as a proposal; it is now the graph. See
"The rifle-mode branch, as built" below for what actually shipped, what it measured, and the defect the
first authoring of it introduced. The architecture is unchanged from this description.

The architecture puts the aim offset **inside a rifle-only branch, over its own base, before any
masking**:

```
base  : unarmed locomotion  ─────────────────────────────┐
layer : MF_Rifle_Idle_ADS ──► AO_Rifle ──► UpperBody slot ┴─► LayeredBlendPerBone (from spine_01)
                                                             ─► DefaultSlot ─► Inertialization ─► IK ─► Root
```

`AO_Rifle` then receives the base it was extracted against, so `D·R = S` reconstructs the authored aim pose,
and the layered blend — now the last word — discards the delta's leg contribution while keeping its upper
body. The whole branch gates on one `RifleModeAlpha`; **"aim on, rifle base off" must not be a reachable
state**, which is precisely what today's standalone `AimOffsetAlpha` still permits (see the warnings at
`Scripts/army_conductor.py` and `Scripts/pie_aim_hand_ik_drive.py`). Only then is the carry re-derived from that
pose's hand instead of from a reach percentage. Note the stock X/yaw axis has no samples, so this buys pitch
only.

## Why every gate stayed green

The same recurring fault, twice more: **a check that cannot observe what it claims to observe.**

* `Pie.AimOffset` asserts the node **responds** — sweep `AimPitch`, the upper body rotates ~84°. It never
  asserted the node is **identity at rest**. Different claim; only one was tested.
* `GoldenDiff.MTLocomotion` reported `IDENTICAL` throughout. An **absent** binding has no line to diff.
* `Pie.HandReachesSocket` reports `distOn` 0.04 cm — a true measurement of left-hand-to-socket distance that
  cannot see an empty right hand or a rifle inside the torso.
* The four-rig lineup render's own driver log reads back only `HasMannequinFootIKRig`; `AimYaw`, `AimPitch` and `WeaponIKAlpha`
  are never sampled in the shipped evidence.

### The new assertion

`FAimOffsetCommand`'s Settle phase now reads the node's **runtime** `Alpha (x%)` out of `GatherDebugData`
before the test opts in, and errors if it is not < 1 %. That is the assertion that would have caught this:
the `.uasset` records what a pin is *bound to*, and the defect was a pin bound to nothing. The same test then
writes `AimOffsetAlpha = 1` to exercise the beat, so it fails from both directions.

## Status of the shipped footage

**The rendered shots in `captures/` predate this fix and still contain the defect.** The graph and the gates
were fixed first and all re-shooting deferred, so the affected shots — **02, 02b, 03, 05, 06, 07 and 09** — and
the artifacts derived from them still show the pre-fix posture and the pre-fix carry. Not *every* shipped
frame: 01 and 04 predate the node and 08 has no character render, per the scope section above.
Re-rendering any shot against the current graph will produce different pixels.

---

## The rifle-mode branch, as built (2026-08-14)

Built in `/Game/MT/ABP_MT_Locomotion`, exactly the architecture proposed above:

```
UseCachedPose 'LocomotionPose' ─────────────────────────► BasePose ┐
SequenceEvaluator(MF_Rifle_Idle_ADS, t=0) ─► AO_Rifle ─► BlendPoses[0] ┴─► LayeredBoneBlend
                                                          BlendWeights[0] ◄── RifleModeAlpha
                                                                   └─► SaveCachedPose 'RifleModePose'
```

`RifleModePose` then feeds the two consumers that previously read `LocomotionPose` — the UpperBody-slot
`LayeredBoneBlend` and the `LinkedAnimLayer` — so the rifle base is upstream of the montage/slot chain
rather than competing with it. The mask is `spine_01` depth 1 with `bMeshSpaceRotationBlend = true`,
matching the project's already-proven value rather than the theoretical depth 0 in the design note,
[`docs/rifle-mode-branch-design.md`](../docs/rifle-mode-branch-design.md).

Two deliberate departures from that design note, both recorded because the note says otherwise:

* **`AO_Rifle.Alpha` stays bound to `AimOffsetAlpha`** instead of being fixed at `1.0`. With the AO inside
  the gated branch, "aim on, rifle base off" is now *inert* rather than *wrong*, so the unreachable-state
  requirement is satisfied by the topology. Keeping the binding preserves the existing default-off C++
  assertion, which is a working gate worth more than the simplification.
* **`RifleModeAlpha` is a real `K2Node_VariableGet` link, not a pin binding.** This is not a style choice —
  see below.

### The defect the first authoring introduced, and why nothing caught it

The weight was first authored as a **pin binding**: `BlendWeights_0 ← RifleModeAlpha`. That binding does not
compile. `BlendWeights` is a `TArray<float>`, the property-access path resolved against the **array** rather
than element 0, and the compiler emitted `Cannot copy property (float -> TArray)` and dropped the copy. The
pin kept its literal default of **`1.000000`**. `RifleModeAlpha` was inert and **the rifle branch was
hard-wired ON for every character in every shot** — a worse defect than the one being fixed.

The whole suite passed over that graph: **default 23/0, pie 22/0.** The signature fault again, and this time
in the gate written to catch the previous instance:

* `Pie.AimOffset` went green at **79.4°** on all three rigs. It measures the aim **response** — the delta
  between `AimPitch` 0 and 0.9 — and both of those poses sit on whatever base the branch supplies. **A test
  that differences two poses on the same base is structurally incapable of seeing the base.**
* `GoldenDiff.MTLocomotion` was green because the binding *existed* in the `.uasset`; the golden format
  records what a pin is bound *to*, not whether that binding compiles.
* Nothing in the suite looked at the layered blend's weight at all.

The only thing that noticed was a **compiler warning surfaced as a green-row coverage note** in the PIE
tier's warnings block — the mechanism this project added precisely because "a warning here usually means a
check did not RUN".

### What now proves it

* **`Verify.RifleGateWired`** (`Plugins/.../Tests/RifleGateTests.cpp`, default tier, headless). Asserts the
  pin is *linked*, that the link resolves to a get of `RifleModeAlpha`, and that no property binding competes
  with it. **Proven red**: severing the link produces
  `RIFLE BRANCH IS NOT GATED: 'BlendWeights_0' has no link ... literal default of '1.000000'`.
* **`GoldenDiff` cannot substitute for it, and this was measured rather than assumed.** Against the *same*
  severed graph, `GoldenDiff.MTLocomotion` **PASSED**. Schema v2 records node presence and bindings, not
  intra-graph pin wiring, so a severed link with the variable-get node left parked in the graph diffs
  `IDENTICAL` while the branch runs hard-on.
* **`Scripts/pie_riflegate_verify_drive.py`** — runtime corroboration, Manny / `Lvl_MT_Test`, 7 pairs/phase:

  | phase | upper worst mean dAB | lower worst mean dAB | control (dAA) |
  |---|---|---|---|
  | `GATE` (RifleModeAlpha 1↔0, aim off) | **97.72°** (hand_l) | **0.34°** (pelvis) | 0.000 upper / 0.641 lower |
  | `AIM_OVER_RIFLE` (AimOffsetAlpha 1↔0, rifle pinned on) | **44.38°** (clavicle_r) | **0.26°** | 0.000 / 0.675 |

  Every lower-body bone moved **less than its own adjacent-frame control** — the mask holds and the legs
  still come from locomotion. For contrast, the original defect moved `thigh_r` **37.1°**; it now moves
  **0.21°**.

  Read honestly: that probe's `blendLine` witness reads `(weight 1.000)` on **both** arms — it is the node's
  inherited graph weight, not `BlendWeights[0]`, so it does **not** discriminate the arms and is not cited as
  evidence the toggle took. The discrimination rests on dAB-vs-dAA, where a 0.000° control against tens of
  degrees is only possible if the sampled poses genuinely alternate. The sibling `pie_aimgate_verify_drive.py`
  was withdrawn for exactly this kind of over-reading, so this probe also clears its A/B state and holds
  sampling off for `SETTLE_TICKS` at every phase boundary, which is the flaw that voided the sibling's ON leg.

### Retracted with this change

The `AIM-KNOWN-BAD` tag is retired. All three sites are updated in the same change as the graph:
`Scripts/pie_aim_hand_ik_drive.py` (now writes `RifleModeAlpha = 1` alongside `AimOffsetAlpha = 1` — either
alone is wrong), `Scripts/pie_rifle_take_drive.py`, and `Scripts/army_conductor.py`. The army keeps **both**
alphas at 0 and now *resets and re-pins* `RifleModeAlpha`: the crowd is unarmed, and because `run_python`
execs into a persistent namespace shared with the beats driver, a stale `1` would otherwise put 46 mannequins
into a rifle idle with no gate in the army path able to see it.
