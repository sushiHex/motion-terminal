# Rifle mode: giving the aim offset the base pose it was baked against

**Status:** designed, not built · **Basis:** a design check against the measurements in
[`research/aim-offset-neutrality-defect.md`](../research/aim-offset-neutrality-defect.md).

`AimOffsetAlpha` (default `0`) is an **off switch, not a fix**. It stops the deformation everywhere that does
not opt in, and the crowd take and the aim/hand-IK take opt in, so they still show the defect. This is the design that actually
restores the aim pose. It has not been built — every number below is either measured (and cited) or a
specification to be gated, never a claim about code that exists.

## The corrected graph

```
Locomotion SM ──► Save "LocomotionPose"
    ├─ Use ──────────────────────────────────► RifleMode LBPB . Base
    └─
MF_Rifle_Idle_ADS @ frame 0 ──► AO_Rifle ─────► RifleMode LBPB . Layer[0]
   (SequenceEvaluator, time 0)   (Alpha = 1.0)   BlendWeights[0] ◄── RifleModeAlpha
                                                 branch spine_01, depth 0, mesh-space rotation
                                                        │
                                                 Save "RifleModePose"
                                                    ├─ Use ──► Action LBPB . Base
                                                    └─ Use ──► LinkedAnimLayer ──► UpperBody slot
                                                                      └──────────► Action LBPB . Layer[0]

Action LBPB ──► DefaultSlot ──► Inertialization ──► LocalToComponent ──► IK ──► Root
```

The aim offset evaluates **over its own reference pose**, inside the branch, ahead of the mask. `D·R = S`
reconstructs the authored aim pose; the mask then discards the delta's leg contribution and keeps its upper
body.

## Five errors a simpler graph would make

1. **Do not move the `UpperBody` slot inside the rifle branch.** It is the only one, so at
   `RifleModeAlpha = 0` every upper-body montage would silently stop reaching the output. The existing graph
   deliberately fans one cached pose to both the action blend's base *and* the linked-layer/slot path; that
   fan-out must survive, downstream of the rifle blend.
2. **`RifleModeAlpha` belongs on the mode blend, not on the aim node.** Fix the AO's `Alpha` at `1.0`. That
   is what makes "aim on, rifle base off" *unreachable* rather than merely discouraged — the failure mode the
   current standalone alpha still permits.
3. **Use a `SequenceEvaluator` pinned to time 0**, not a sequence player. A freely advancing idle yields
   `D·R(t)`, and the reconstruction identity that makes this correct is `D·R(0) = S`.
4. **Mask with `spine_01`, depth 0.** Positive depth tapers the weight, which is right for the action seam
   and wrong here: partial weight means the AO is reconstructing against a partly-unarmed pose.
5. **Inertialization will not smooth this.** It does not observe a float blend weight, so `RifleModeAlpha`
   must be ramped by the caller or it pops on toggle.

## Constraints that bound the work

* Stock `AO_Rifle` is **pitch only** — every sample sits at `X = 0`. Yaw requires new samples under
  `/Game/MT/`. Do not advertise rifle yaw until they exist.
* `UpperBody` and `DefaultSlot` resolve through the same montage group, so full-body and upper-body montages
  are **mutually exclusive**. Fixing that means editing stock skeleton slot groups, which is out of bounds.
* No writes to `/Game/Characters`, `/Game/ThirdPerson`, or the engine install. New assets under `/Game/MT/`.

## Does the locomotion state machine need a rifle variant?

**Not for the first correct build.** A frozen rifle idle supplies the upper body while the existing state
machine supplies pelvis and legs. Ship that as the first correct build and the fix for the rifle aim take.

Be honest about what it does not do: the torso becomes a static ADS pose, so gait counter-rotation,
acceleration lean and turn motion are gone from the upper body, and jump/fall/land keep only their lower-body
motion. A rifle locomotion set is a later, separate decision — not a prerequisite, and not a reason to
duplicate the state machine.

## Task order, each with a gate that can go red

The shape is what matters:

1. **Make topology observable.** The golden baseline records classes, assets and property bindings but *not*
   pose edges or LBPB bone/depth/rotation-mode. This design is unverifiable until it does — and that gap is
   the same one that let an unbound pin diff as `IDENTICAL`. Red proof: on a scratch duplicate, break one
   pose edge or change `spine_01` to `pelvis`; the verifier must name the exact mismatch.
2. **Prove per-rig asset compatibility** before touching the shipping ABP. Red proof: feed the unarmed idle
   as base — the known pelvis/leg residuals must exceed tolerance.
3. **Author the graph.** Red proof: bind the mode weight to `AimPitch`, or reconnect the AO's base pose to
   locomotion; the architecture gate must fail.
4. **Prove atomicity and isolation.** Mode 0: pitch changes do nothing. Mode 1 centre: upper body matches the
   rifle reference. Mode 1 extreme: upper body moves, legs locally unchanged. Red proof: three separate
   mutations, each failing a different assertion.
5. **Preserve existing behaviour.** Red proof: put `UpperBody` back inside the branch — at mode 0 the montage
   *weight* may still rise, so the gate must assert the dependent **pose**, not the weight. A weight-only
   assertion is exactly the kind that cannot observe what it claims.
6. **Re-solve the weapon carry** from the posed hand. Measure both hands' position *and orientation*. Red
   proof: perturb the attachment 5 cm / 10°; the gate must fail even though the historical off-hand distance
   stays small — that distance is what made the old claim vacuous.
7. **Baseline and evidence last.** GoldenDiff fail-first, snapshot only after runtime gates pass.

## Biggest risk, and the cheapest way to retire it

**Not graph surgery — the assumption that the mannequin additive survives retargeting.** The existing
Khaimera coverage proves the AO *responds*; it never proved `D_target · R_target` reconstructs the retargeted
authored pose. Same response-vs-correctness confusion as the original defect.

Before editing the shipping ABP, build a throwaway `/Game/MT/Probe/Rifle/` Anim Blueprint containing only
`target-rig rifle frame 0 → candidate AO → local-transform sampler`, and compare centre and one extreme
against the retargeted authored samples on each rig. If it fails, author target-skeleton rifle base **and**
AO assets under `/Game/MT/` and override them together — never a mannequin AO over an unrelated retargeted
base.

## What becomes re-assertable once this ships

The rifle aim take can drop `KNOWN-DEFECT TAKE` and claim pitch aim over a correct reference with verified lower-body
isolation. Its hand numbers must be **re-measured**, and "grip" stays unclaimable until the orientation gate
passes — the current evidence is distance-only by construction. The crowd take's 3.6 / 4.0 ms budget must be
re-measured, because the graph gains an evaluator, a blend and a cache.
