> # ⚠️ THIS ERRATUM IS ITSELF SUPERSEDED — it was a FALSE NEGATIVE. The foot IK WORKS.
>
> **Two** later confound-immune measurements proved this erratum's "no conformance" conclusion WRONG: a four-way node-state diagnostic (two runs) and **a second measurement built from scratch**, which *proved* the node-free baseline absent from the live evaluated graph (Mechanism-A `GatherDebugData`: 12 vs 13 nodes, no `AnimNode_ControlRig`), and found nothing that refuted the result. Method: build the true no-rig baseline by **structurally removing the CR node** (Output ← LBPB directly), not by an alpha-disable. Results (settled, pawnX 850.7, reproduced ×2, byte-identical across two independently-built no-rig graphs and vs stock):
> - **true no-rig (structural):** feet SPLIT on the slope — foot_l clr 6.9 (sole into ground) / foot_r clr 17.8 (sole floating). This is the raw flat-authored animation on a 20° ramp, as physics requires.
> - **rig ON (shipped ABP):** feet CONFORMED — both clr ~10.2/10.6 (soles on ground, even). node-ON vs true-no-rig = foot_l **+3.4 cm** (raised out of penetration) / foot_r **−7.2 cm** (lowered out of float). **Byte-identical to stock `ABP_Unarmed`'s rig-on output.**
> - **The foot IK conforms both feet to the ramp, matching stock exactly.**
>
> **Why THIS erratum was wrong:** its state OFF "Alpha-off" baseline measured foot_l = 141.9 — the *conformed rig-ON* pose — NOT the true no-rig 138.5. The alpha-disable did not take effect (state OFF's pose + even clearances match rig-ON, not the split no-rig), so this erratum's decisive state ON−state OFF = 0.03 cm compared rig-on to rig-on. The earlier wiring measurement fell into the same confound class. The `dL≈−0.7` "intrinsic offset" claim is also wrong: dL is ≈0 with the node truly removed and ≈−0.7 with the rig on — the rig moving the deform foot IS the effect. (The stock cross-check used a *trace-based* off state and correctly showed conformance; only our side used the confounded alpha-based off, so the two sides were not comparable.)
>
> **Consequence:** the foot-IK efficacy claim is REHABILITATED (direction + cm-to-7cm magnitude confirmed; the specific historical −6.5→−3.1 figures were a different reference and stay superseded by the airtight +3.4/−7.2). The retraction this erratum triggered across the docs, and the `FootIKAlphaGates→FootIKNodeIntegration` demotion, are reversed. The original body below is retained for the record — it documents a real, instructive false-negative (the verification discipline's OWN correction was itself confounded, caught only by the confound-immune structural method). **Meta-lesson: a "node-off" reference must PHYSICALLY remove the node, never rely on an alpha/binding disable.**

---

# ERRATUM (SUPERSEDED — see banner above) — the foot-IK "measured planting" claim is FALSIFIED

**Scope:** this erratum corrects ONE claim — that the shipped `ABP_MT_Locomotion` foot IK produces measured ground conformance ("planting") of the deform foot. It does NOT affect the rest of the foot-IK work (the slope/stairs lane, schemaVersion-2 binding capture, the golden/ClipVaries gates, the whole verification infrastructure — all real and intact). The MCP-authoring of the foot-IK *integration* (post-LBPB node insert, the Alpha gate, the exposed-pin wiring) is real and structurally present. What is false is the EFFICACY claim: that this integration visibly conforms feet to the ground.

## The claim being corrected

The `ShouldDoIKTrace` wiring work recorded — and `docs/verification-design.md` repeated — that after wiring `ShouldDoIKTrace`, foot IK was *measured working*: ramp planted-foot ball clearance IK-off→on **L −6.5→−3.1cm, R +6.1→−1.0cm**, and `foot_l != ik_foot_l` (dL≈−0.7, "was 0 when inert"). The claim passed the wiring work's own check, code review, and the follow-up gate work.

## The falsifying evidence (controlled experiment)

Method: three node states on the shipped ABP via throwaway duplicates (shipped asset never edited, GUID re-read identical), deform-foot world Z vs ground beneath (raycast), matched pawn-X on the ramp, settled + moving, reproduced in two independent PIE sessions.

- **state ON (node full-on: Alpha=1, trace on) vs state OFF (node fully off: Alpha=0):** deform `foot_l` ΔZ **+0.03cm settled** (matched-phase frames byte-identical), **≤0.011cm at all 10 moving positions**, **byte-identical across two sessions**. → The node at full weight renders the foot in the same place as bypassing it entirely. **No net deform conformance.**
- **Not an environment/session artifact:** stock `ABP_Unarmed`, same ramp, same session, ON−OFF: deform `foot_r` ΔZ **−10.2cm** (trace pulls a floating foot from 21.05→10.84cm clearance, reversibly). The trace mechanism is live in this environment; stock conforms ~10cm, ours ~0.
- **The dL≈−0.7 "proof" is intrinsic:** dL (foot_l − ik_foot_l) ≈ −0.63 to −0.69 in BOTH state ON (full-on) AND state OFF (node fully off). It is the natural bone offset, present regardless of the rig. It → 0 only in state NOTRACE (node on, trace off). The original claim read dL 0→−0.7 as "conformance began"; in fact the deform foot did not move (state ON≈state OFF). The internal rig state changed; the render did not.
- The specific −6.5→−3.1 clearance figures are not reproduced: state ON and state OFF clearances are both ~10.2–10.6cm (delta ~0.03cm, not ~3.4cm).

## Why our rig nets to zero while stock conforms (hypothesis, not yet proven)

State NOTRACE (our node running, trace OFF) drives `foot_l` ONTO `ik_foot_l` (dL→0), 3.45cm off the animation; with trace ON (state ON) the computed offset returns the deform foot to ≈the animation position (≈state OFF) instead of to the ground. Stock's trace instead pulls the foot to the ground. Leading candidate: in our post-LBPB integration the `ik_foot_l/r` bones fed to the ControlRig are not driven to track the FK feet (the retargeted/blendspace locomotion + cached-pose fan-out likely leave them at ref pose), so the rig's PBIK/trace has no coherent target and self-cancels on the deform foot. Stock's pose drives ik_foot. **Diagnosable and possibly fixable** (drive FK→IK foot before the node, or revisit the post-layering placement) — a bounded spike, uncertain payoff.

## Second finding — the standing regression test is a false-positive (fixed at the time: renamed + demoted)

`Pie.FootIKAlphaGates` (default-suite green) asserts a grounded foot-**stagger** (`dZ_feet > 5cm`) as its "rig works" discriminator. But in this experiment `dZ_feet ≈ 10.9cm` in BOTH state ON (on) and state OFF (off) — the stagger is the animation's settled foot spread on a tilted surface, NOT IK conformance. The test passes identically whether the rig runs or not. It is a structure-passes-function-doesn't false-positive — the exact class this project exists to catch, here in our own guard. Any honest re-grounding must assert on **on-vs-off deform displacement** (which the current rig would FAIL) rather than stagger.

## Claims this erratum marked known-FALSE at the time

The "measured clearance divergence / measured planting −6.5→−3.1" figure and the "functioning foot IK, measured"
statement, wherever they were repeated — including the foot-IK arc's "measured planting" resolution in
`docs/verification-design.md`.

## Lessons learned

The false claim survived its own wiring check, code review and the follow-up gate work — then was caught by an A/B measurement that demanded a varying result (the lesson of the silently-static retarget bake) and by this controlled re-measurement. That is the project's thesis ("a bake/wire that succeeds isn't one that's right") demonstrated on its own central claim. The record is corrected here, not buried.
