# MotionTerminal — Verification Design

*Verification write-up. Companions: `docs/architecture.md` (why the system is shaped this way), `docs/introspection-mechanisms.md` (the engine mechanisms the assertions are built on), `docs/topology-schema-v2.md` (the baseline format's own design note).*

## What "verified" means here

A feature is verified when three independent things hold:

1. **Structure matches a reviewed baseline** — the golden-diff gate.
2. **The runtime contract holds in PIE within a time budget** — the assertion suite.
3. **The feature's *effect* is measured present when on and absent when off** — an A/B probe with an instrument that does not share code with the thing under test.

The third clause is the project's actual thesis, and it was earned, not designed in. Twice, a feature that satisfied clauses 1 and 2 completely was doing nothing: a retarget bake that "played" on every eyeballed frame but had frozen all 53 frames to one pose (the silent-static bake), and a foot-IK rig that was correctly wired, structurally provable, and covered by a green PIE test while producing zero foot displacement (the foot-IK arc below). "Compiles clean," "is bound," and even "test passes" are necessary signals; none of them is *sufficient*, and this document exists largely to record how each insufficiency was discovered and what compensates for it.

## The two-tier runner

One command runs everything: `Scripts/run_verification.py` (stdlib-only Python; its pure helpers, such as the low-headroom advisory and its per-row windowing, have host tests in `Scripts/test_repo_integrity.py`).

| Tier | Invocation | RHI | Suites | Cadence |
|---|---|---|---|---|
| `default` | `python Scripts/run_verification.py` | headless `-NullRHI` | `MotionTerminal.Verify.*` + `MotionTerminal.GoldenDiff.*` | every change, locally (<2 min) |
| `pie` | `python Scripts/run_verification.py --tier pie` | real RHI, local | `MotionTerminal.Pie.*` | before a merge / on demand |

The split is forced by measured engine behavior, not preference: under `-NullRHI` a skinned ThirdPerson pawn stalls at "0/1 ready" and CommonUI's viewport check auto-fails the test — so topology and golden-diff work runs headless, and anything that needs a ticking animated character runs against a real RHI. The runner encodes the automation-harness traps directly:

- **Exit codes are ignored** — `UnrealEditor-Cmd` exits ~0 regardless; `index.json` in the report directory is the only source of truth (parsed with `utf-8-sig`; UE writes a BOM).
- **UE 5.8 renamed `-ReportOutputPath` to `-ReportExportPath`** — the old flag *silently* produces no report, which would read as success to a naive runner. Missing `index.json` is a hard error (exit 2), and zero-tests-found fails rather than passes.
- **`-TestExit="Automation Test Queue Empty"`** prevents the `;Quit` race.

Before a merge the tiers are additionally backed by a **live smoke over MCP** — editor launched, `AnimGraphTools.Ping`, `DiffAnimGraph` returning `IDENTICAL` against every baseline, clean `quit_editor()` with a process-exit poll. This proves the *tool path* end-to-end (registration, marshaling, asset load), not just the in-process test path. A feature is done when its verification increment is green; a red blocks the next increment.

Both editor tiers run locally; they need an Unreal Editor install and, for `pie`, a GPU. What runs in CI is the editor-free half: `.github/workflows/host-tests.yml` runs the public gate (`Scripts/public_gate.py --generic`) and the host-side pytest suite (`python -m pytest Scripts -q`) on every push and pull request.

The first inventory (after the foot-IK work; `Pie.FootIKAlphaGates` was later **renamed `Pie.FootIKNodeIntegration` and demoted to structural-only** — see the arc below): `Verify.UnarmedTopology`, `Verify.TopologyRoundTrip` (looped over both baselined assets), `GoldenDiff.Unarmed`, `GoldenDiff.MTLocomotion`; `Pie.UnarmedIdleWeight`, `Pie.TemplateSpeedToLocomotion`, `Pie.MontageIsolation`, `Pie.FootIKNodeIntegration`. The suite accretes per feature — the roster work added per-rig baselines, the import-scale assertion, and the clip-varies test.

## The golden-diff gate

`SnapshotBaseline` serializes an AnimBlueprint's AnimGraph to a canonical JSON (`AnimGraphTopology.cpp`: graphs → nodes with class/title/asset/transition edges → nested sub-graphs; node arrays sorted by their serialized form for determinism) into `Plugins/MotionTerminal/Tests/Baselines/<name>.json`. `DiffAnimGraph` and the `GoldenDiff.*` tests re-serialize the live asset and compare. The diff is line-multiset-based with EOL normalization (git autocrlf can never trip the gate), emits `- `/`+ ` lines, and — after a review caught the order-blind false-negative — reports a pure reordering as its own marker line instead of `IDENTICAL`.

### schemaVersion 1 → 2: what each version can and cannot see

**v1** captured node presence and transition edges. Its blindness was identified in review and demonstrated concretely while the clip-variable work was being planned: a regression stripping every clip/variable *pin binding* from the Template — functionally catastrophic — passed **both** tiers under v1.

**v2** closed that hole: each node may emit a `"bindings"` map, read via pure UObject reflection (the 5.8 binding data moved behind a private, forward-declared-only accessor — the reflection route and its type guard are documented in `docs/topology-schema-v2.md`). The bump was atomic with a both-baseline regen in one reviewed commit, and the closure was proven by a negative check, not asserted: unbind one pin → the diff shows exactly `- "X":"Speed"` → rebind → `IDENTICAL`. The capture also proved generic beyond its design case when the foot-IK node was spliced in: the ControlRig node's `Alpha ← GroundedFlag` binding surfaced with zero code change.

### The honest blind-spot record

What the baseline **still cannot see**, discovered the hard way and recorded rather than papered over:

| Blind spot | Found | Compensating control |
|---|---|---|
| Pin-to-pin **link edges** in the AnimGraph (e.g. the `GroundedFlag → Conv_IntToBool → ShouldDoIKTrace` wire) and **exposed-pin state** on ControlRig nodes | During the foot-IK diagnosis — the shipped foot-IK gate's drive is invisible to v2; only the two K2 nodes appear as presence lines | `ReadAnimGraph` + `get_anim_node_pin_bindings` dump committed as a fixture (`research/fixtures/abp-mt-locomotion-readback.txt`). The compensating claim originally made here — that `Pie.FootIKAlphaGates` "decisively" asserts the wire's effect (below) — is **RETRACTED 2026-07-16**: that test measured the animation's foot geometry, not the wire's effect; it is renamed `FootIKNodeIntegration` and demoted to an observation, not a compensating control. See `research/foot-ik-erratum.md` |
| **Interface layer graphs** (`FBPInterfaceDescription::Graphs`) — a manual disconnect inside the `UpperBodyLayer` graph would leave every test green while the layer silently ref-poses | In review of the layer work, engine-verified | Currently a manual-UI-only regression vector — no shipped tool can author inside that graph; a layer-graph op is a candidate extension of the project's own toolset, which would also let the capture reach it. Recorded in `docs/topology-schema-v2.md` |
| **Node properties and literal pin defaults** (e.g. `AlphaInputType`, which silently selects which alpha pin is honored) | During the foot-IK diagnosis | Effect-level PIE assertions; direct struct reads recorded in fixtures when a property is load-bearing |

The rule this table encodes: a blind spot is acceptable only while (a) it is written down, (b) something else covers the failure it would hide, and (c) closing it is assigned. Baselines diff *structure*; the PIE tier owns *function* — which is why neither tier is optional.

## The PIE assertion architecture

The `Pie.*` tests are raw latent commands (`IAutomationLatentCommand` in `PieTests.cpp`) — per-frame polls with explicit phase machines, driving real gameplay and asserting through the same introspection core the MCP tools use (mechanisms A–C of `docs/introspection-mechanisms.md`; the tests and the debug tools literally share `AnimIntrospection.cpp`).

Patterns that are load-bearing, each traceable to a failure that mandated it:

- **Lazy-init the clock.** A latent command's constructor runs at *enqueue*, before the map loads; timing from there spends the budget on map load. The clock starts on first poll.
- **Per-phase budgets, reset at each transition.** E.g. `Pie.TemplateSpeedToLocomotion`: Settle (Idle weight > 0.99, 20 s) → Drive (real `AddMovementInput` until Locomotion weight > 0.9 at Speed ≈ 600, fresh 20 s) → an observation-only JumpProbe tail (8 s, `AddInfo`, can never fail the test). `Pie.FootIKAlphaGates` runs five phases (Settle/Drive/GroundSettle/Airborne/Recover) the same way. Timeouts name the phase and the unmet condition.
- **Pin the possessed pawn.** Scanning all pawns produced first-eligible-pawn ambiguity; driven tests resolve `PlayerController → Pawn`.
- **Resolve baked state machines by name, never by runtime index.** The runtime machine index is not an index into `GetBakedStateMachines()` — indexing it that way crashed the editor — and the pairing is factored into one helper (`ResolveLocomotionBaked`) so it cannot be re-learned per test.
- **Drive the real contract, not the reflection override.** Forcing airborne uses `Character->Jump()`, because the EventGraph re-derives `GroundedFlag` from `IsFalling` every tick and stomps a synthetic `SetAnimVariable` within a frame. The same silent-overwrite class of bug later confounded a foot-IK experiment (below) — live bindings overwrite defaults every tick.
- **Save/restore mutated global state.** The CommonUI viewport CVar is saved and restored around each test; the restore was proven live (the prior value differed — the bleed was real).

## The effect-level-probe convention — origin story

This is the document's spine: the foot-IK arc, told in order, because the convention it produced is only credible with the failures attached.

**The setup (all green).** The stock `CR_Mannequin_FootIK` ControlRig node was spliced into the Template (`LBPB → ControlRig → Output`) with a float `Alpha ← GroundedFlag` property binding. Structural proof exceeded the usual bar: not just `is_bound:true` but the compiled T3D `PropertyAccess` copy record showing the actual per-tick copy op. The baseline regen captured the binding. A new PIE test, `Pie.FootIKAlphaGates`, measured foot clearance against the analytic ramp height and cleanly separated grounded / airborne / recovered — and its review even strengthened a weak threshold into a real assertion. Wiring verified, baseline green, PIE green.

**The catch.** The foot-IK capture ran the same A/B discipline that exposed the silent-static bake: raycast ball-of-foot ground clearance at matched pawn positions, IK-on versus an IK-off duplicate. **A ≡ B everywhere** — identical on the flat, both penetrating ~6.5 cm on the ramp, and `foot_l == ik_foot_l` at every sample. The rig was wired correctly and doing *nothing*. Every structural check had been satisfied by a functionally inert mechanism, and the green test was — in hindsight — validating the Alpha *binding path*, with feet tracking the body 1:1 in both gate states.

**The diagnosis, three layers deep, with a wrong turn kept on the record.**
- **First layer:** the suspected kill-switch (`ShouldDoIKTrace`, an internal rig bool defaulting false) was forced three independent ways — still byte-identical clearances — and the conclusion drawn was that the rig/environment was broken. That conclusion was *wrong*, and is preserved as such; everything was reverted, nothing shipped.
- **Second layer:** the cheap discriminator that should have come first: stock `ABP_Unarmed` on the *same pawn, ramp, and instrument* — and it worked (nonzero grounded foot delta, ~0 airborne). The defect was ours. Root cause, verified: stock exposes `ShouldDoIKTrace` as a driven input *pin*; **a property binding to a non-exposed rig input is silently ignored at runtime** (the node fills it with type-default `false`), which also explains why all three forcings did nothing.- **Third layer:** with the pin exposed, one more silent failure turned up on the way: an int→bool property binding on the now-exposed pin still measured dL = 0; only the in-graph link (`GroundedFlag → Conv_IntToBool → pin` — exactly stock's shape) drove it. **Then the measurement recorded at the time:** planted-foot ramp clearance reportedly moved from −6.5 cm penetration to −3.1 cm (left) and from +6.1 cm float to −1.0 cm (right); `foot_l` diverged from `ik_foot_l` (dL ≈ −0.7, previously exactly 0) — logged as "Foot IK plants, measured". **These specific −6.5→−3.1 figures are SUPERSEDED** (a different reference + a confounded alpha-gated method); a later controlled re-measurement did not reproduce them — but the *conclusion* they supported (the foot IK plants) is **re-confirmed** by the corrected capstone below, with an airtight **+3.46/−7.14 cm** from a confound-immune baseline.

**The correction that ran the other way.** After the pin-exposure fix, the original Alpha binding looked like inert dead weight: the engine path it seemed to rely on was the bool alpha mode, and an experiment appeared to show the binding had no effect. Before removing it, `AlphaInputType` was read directly off the serialized node struct: **Float** — the bool-mode engine path is the branch the node never takes, and the experiment was confounded (the live binding overwrote the `Alpha=0` default back to 1.0 every tick). The binding is load-bearing: the true mechanism is **two gates, both fed by `GroundedFlag`** — Alpha as the whole-node airborne blend gate, `ShouldDoIKTrace` as the trace-conformance gate inside the running rig. The falsified claim was annotated where it was made, with a forward pointer, rather than edited away.

**Re-grounding the test.** Thresholds were re-derived from working-rig data — including refuting the first instinct to tighten it with measurement (the working rig conforms the trailing foot *upward*, shrinking the first-tick release delta; the margin was loosened 3.0 → 2.0 cm because the data said the old value sat 0.3 cm from the real signal). The originally-planned foot-vs-foot method, dead on the inert rig (ΔZ 0.32 cm), was read at the time as now working: 10.81 cm of slope stagger. **This dZ_feet reading is RETRACTED as a discriminator** — the raw foot-Z spread is present whether the node runs or not (the animation's foot spread on the tilted ramp, not an IK effect). The corrected metric is *clearance evenness* (slope normalized out), which the confound-immune measurement below shows the rig genuinely produces.

**The last specimen (pre-merge review).** The review replayed the re-grounded test's four assertions against the *historical inert-rig dataset* — and all four passed. Nothing guarded the stuck-OFF direction. The pre-merge fix added the stagger discriminator (`GroundedDZfeet > 5 cm`, grounded and recovered): inert rig 0.34, working rig 10.81 — 32× separation, from the branch's own logged data — believed at the time to close the gap. Only then did the foot-IK work merge. **This discriminator is RETRACTED — wrong SIGN:** it asserts a *large* raw-Z spread, which the split inert stance also passes. The corrected discriminator (below) asserts *clearance evenness* + a grounded band — the state only a conforming rig produces — now a HARD assertion.

**What the arc bought beyond the fix:** the convention now applied to every later feature:

> **Every feature increment ships with an effect-level probe:** measure the feature's effect ON vs OFF with an independent instrument at matched conditions, before the gate line is written. Structural verification and a green weight assertion are entry criteria, never acceptance.

**The capstone the arc delivered during the roster work — the convention caught its own "fix"** (⚠ **this capstone and the paragraph after it are themselves SUPERSEDED — read "The capstone, corrected" below; the re-measurement described here was ALSO confounded, and the foot IK does in fact conform**). Everything above this line is left in place, uncorrected in situ, because what happened next is the point. The roster work re-ran the same A/B before trusting the foot-IK shot on the merged, already-shipped result — and got byte-identical again. A controlled re-measurement followed: shipped node full-on vs. fully-bypassed, position-matched, two independent PIE sessions, stock `ABP_Unarmed` run on the identical ramp and session as a live control. It falsified "measured planting" outright. The deform foot moves ΔZ ≈ 0.03 cm whether the node runs or not (≤0.011 cm across 10 moving samples, byte-identical across the two sessions), while stock moves the foot ~10 cm on the same lane in the same session — proof the null result is the integration, not the environment or the instrument. The −6.5→−3.1 cm figures above do not reproduce; the dL≈−0.7 "divergence" turns out to be an intrinsic bone offset present with the node off too; and the `GroundedDZfeet` 0.34-vs-10.81 "32× stagger" discriminator — the test written to *harden* this exact result — is itself the same class of false-positive this document is about: it measures the animation's settled foot spread on a tilted floor, identical whether the IK node runs or not, not IK conformance. `Pie.FootIKAlphaGates` is renamed `FootIKNodeIntegration` and demoted from a pass/fail gate to an observation.

Read the two effect-level-probe failures this document opened with again: a bake that "played" on every eyeballed frame but had frozen every sampled pose (the silent-static bake), and a rig that was "wired correctly, structurally provable, and covered by a green PIE test while producing zero foot displacement" (the first catch, above). This is a **third** instance of the identical failure mode — structure, and even a first-pass measurement, said yes, and only a second, independent, controlled measurement said no — except this time the false claim had already survived the fix's own implementation, a code review, the pre-destructive decision gate, and the pre-merge review before it merged. The effect-level-probe convention this document is *about* did not just catch the two failures that motivated writing it down. It caught the fix, after it shipped.

**The capstone, corrected (2026-07-17) — the discipline caught the error in its own correction.** The re-measurement above was *itself* confounded, in the exact class it was hunting. Its "node fully-bypassed" baseline disabled the rig by clearing the Alpha binding and forcing the weight to 0 — but `Alpha ← GroundedFlag`, a live property binding, overwrote that 0 every tick, so on the grounded ramp the ControlRig node ran at **W:100% in both states.** It measured rig-on against rig-on (ΔZ ≈ 0.03 cm) and concluded "inert." A confound-immune re-measurement settled it: the node was **structurally removed** and **proven absent from the live evaluated graph** — a Mechanism-A `GatherDebugData` node-count walk showing **13 nodes with the rig, 12 without, no `AnimNode_ControlRig` in the stripped tree** (not an alpha/binding disable, which the confound hides). Against that baseline the shipped rig moves the deform feet **+3.46 / −7.14 cm**, evening a split ramp stance (one sole penetrating, one floating) to level soles — identical at three ramp positions, byte-stable across two sessions, byte-identical to stock's own output. The strongest refutation — that the bypass merely *broke* the graph — held: an independently node-free stock graph rendered the same split to **<0.003 cm**, so the split is the genuine flat-authored animation on the 20° slope. **The foot IK conforms.** And the lesson is deeper than the first capstone claimed: **a disable-based reference is not a control** — the same confound produced the original over-claim *and* its false retraction — and the effect-level-probe discipline caught it **in its own correction**, because the only measurement trusted was one whose "off" state was *proven* off. This is the first instance where the discipline audited itself.

**The convention applied to turn-in-place, aim offset and hand IK — three more mechanisms, three more effect-level probes, two more bugs caught before green.** Each mechanism shipped with a same-change measured ON-vs-proven-OFF probe, and each probe earned its keep: `Pie.TurnInPlace` (+ `Scripts/pie_turninplace_drive.py`) caught that a `ModifyBone`-on-root turn was **structurally inert** — component-space modify does not propagate on the geometry-less root, so OFF ≡ ON — before any green claim, forcing the switch to local-space `RotateRootBone`; and it gates on **sway-free ROOT yaw** with signed sweeps in *both* directions (an early version gated only the right turn and would have missed a broken left turn). `Pie.AimOffset` (+ `pie_aimsweep_drive.py`) caught nothing broken but is confound-immune by construction — it separates upper-body Δ (head ~84°) from the lower body (~0.2°), so a whole-skeleton rotation can't masquerade as an aim offset. `Pie.HandReachesSocket` (+ `pie_handreach_drive.py`) caught a `WeaponIKTargetWorld` effector that was **gated inside a montage branch** — in idle the effector sat at world origin and yanked the hand to the floor — and its final form carries a proven-discriminating **socket-existence + bone-index Settle guard** (present → both green; a bogus socket name → RED), closing the shared-`GetSocketLocation`-fallback confound where an unknown socket silently returns component origin in *both* the effector and the measurement so they coincide and the test passes with the hand nowhere near the weapon. The three tests are `Pie.TurnInPlace.{Manny,Quinn,Khaimera}`, `Pie.AimOffset.{Manny,Quinn,Khaimera}`, and `Pie.HandReachesSocket.{Manny,Quinn}` (Khaimera's row is honestly absent — the hand-IK degrade, `handIK:false`). Pie tier 8 → 16; default unchanged at 15 (the new nodes live in the Template baseline, regenerated to `IDENTICAL`).

## The crowd-scale tier — a budget with three separately-classified gates, and the effect-level discipline applied to a measurement

The army stage puts 46 tiered instances (hero 6 / mid 8 URO+LOD1 / far 32 URO+LOD2; `army_config.json` stores these as `forced_lod` 2 and 3, because `SetForcedLOD` counts from 1 = LOD0 and 0 = not forced) on the one shared Template, driven live by the conductor (spatial speed/turn/aim waves + full-body `DefaultSlot`+`Inertialization` montage sectors). It adds four PIE rows — `Pie.Army.Budget`, `Pie.Army.SpotCheck`, `Pie.Army.NoFrozen`, and `Pie.MontageSeamless` — taking the pie tier 16 → 20 with the default unchanged at 15 (the two new AnimGraph nodes live in the Template golden, regenerated to `IDENTICAL`).

**The budget is not one "under budget" number — it is three metrics, deliberately classified, and the honest state is two hard gates green and one advisory-and-over.** This is a methodology decision, not a rounding of an awkward result, and it is recorded in `army_config.json budget._ceiling_policy` rather than paraphrased:

| Metric | Class | At N=46 driven | Verdict |
|---|---|---|---|
| `AnimGameThreadTime` (serial, per-frame) | **HARD gate, on the AVERAGE** | 3.6 ms avg / 4.0 ceiling; **4.21 ms peak** on the montage volley | GREEN on avg; peak **disclosed as a risk, not gated** |
| Animation memory (`GetResourceSizeBytes`, summed) | **HARD gate, headless-CI-provable** | 4.637 MB / 6.0 ceiling | GREEN |
| `PerformAnimEvaluation_WorkerThread` (summed cycles) | **ADVISORY** | 12.4 ms avg / 15.9 peak / 8.0 ceiling | over — by design |

The game-thread gate is on the *average over a settled window* because the 4.0 ms ceiling is itself derived as a *sustained* 24%-of-a-16.67 ms-frame allocation: a lone 4.21 ms peak is still ~25% of the frame with ~12 ms of headroom and does not by itself drop a frame, and a 14-frame-window max is too noisy (one GC/PSO hitch) to gate CI on. The summed-worker metric is advisory because `PerformAnimEvaluation_WorkerThread` sums cycles across parallel worker threads (on a multi-core desktop CPU → ~0.8 ms wall-clock at N=46 driven), so its raw ms is a conservative headroom signal, not a frame-binding wall-clock cost. `Pie.Army.Budget` hard-fails on the gt average and `AddInfo`-reports the peak; N=46 is kept (driven-safe on gt-avg to N≈52).

**Where each row genuinely runs, stated precisely — the three `Pie.Army.*` do NOT degrade the same way in headless CI:**

- `Pie.Army.Budget` hard-fails on the gt **average** and on **animation memory** — both genuinely execute in `-unattended` (memory reads live components with no stats thread; the gt read settles when a frame does). But its live anim-eval-**ms** read cannot settle a `STATGROUP_Anim` frame headless — over 24 s, collection is enabled and group metadata flows, yet `GetLatestValidFrame()` stays **−1** the entire time, because the stat-frame pipeline needs the viewport-driven frame advance a 0×0 `-unattended` editor lacks. For that one piece the test **degrades honestly** to a structural crowd-shape assertion and a pointer to the committed interactive sweep (`research/fixtures/army-contact/n_vs_eval_ms.csv`) — never a faked green stat. A green pie tier therefore enforces the *memory* budget headless but not the live *ms* budget; the ms gate's authority is the interactive sweep.
- `Pie.Army.SpotCheck` (K=10 of N=46, evenly-spaced: locomotion weight ≈ 1 at speed, aim node present, slot weight sane) and `Pie.Army.NoFrozen` (K=10 driven instances' moving bone Z varies over a window; a deliberately-unticked control must read frozen — the control proves the varies-check can fire) are **fully headless-capable** — bone/weight introspection through the shared `AnimIntrospection.cpp`, no stats thread.
- `Pie.Army.OnCapsule` (a later addition, not one of the original three) spawns one pawn per crowd rig, driven in place at 450 under a pinned 60 Hz clock, and asserts the pelvis's horizontal distance from the capsule axis stays under a **provisional 30 cm** bound (in-place Khaimera 10.1 cm, baked original 941.2 cm). It reads bone positions and the anim update counter, no stats thread, so it is headless-capable. **Not yet run, red or green:** the bound is to be calibrated from its first measured run, and the negative control (remove `crowd_anim_class.khaimera`) is still to be done.

**`Pie.MontageSeamless` is an effect-level probe with a built-in confound trip.** It drives BOTH arms in one PIE session — same graph, same two clips, same slot — differing only in blend mode (inertialization vs a single-frame hard cut), reads 89 bones in component space per tick under a **pinned 60 Hz clock** (an unpinned clock made "cm/frame" meaningless — a hand at 10 m/s covers ~3 m in a 0.3 s hitch, swamping any pop; the pin is held by an RAII `FFixedTimeStepScope` so even an external `StopTests` `DequeueAllCommands` cannot leak the pinned clock into later tests). Measured: inertial transient **21.9 cm/frame** vs hard-cut **244.9** (11.16× discrimination); the threshold is **derived**, `3.0 × max(run-floor 19.81, walk-floor 10.75, 1.0) = 59.43`, floored by the *faster* clip so a run→slow-idle chain (the crowd's real montage schedule) can't produce a bound a correct blend fails; and the test errors out with a `CONFOUND` if the control ever comes in under threshold — a green that cannot be defended is unreachable. The slot-weight check is a residual guard only: `GetSlotMontageLocalWeight` sums-and-clamps every montage valid for the slot, so it reads 1.0 for both a cross-blend and a cut and is vacuous for the discrimination it was first claimed to make — recorded rather than papered over.

**The effect-level-probe discipline this document is about caught a budget measurement, not just a feature.** The shipped crowd numbers are a *driven* re-measurement. The original sweep priced a **standing/idle** crowd by mistake — `army_budget_probe.py` spawns the crowd and reads stats but never starts the conductor — and concluded "worker 8.6/8.0, gt 3.33/4.0 comfortable, keep N=46" off idle-graph cost, ~1.4–1.8× below the shipped driven load. Nothing in the crowd work's own checks questioned it; it was caught by accident during a HUD fix when a live *driven* read gave wildly different figures; the sweep was reopened, re-measured with the conductor running, and the average-vs-peak gate methodology was settled. This is the sixth instance of the project's recurring failure mode — measuring something adjacent to the real condition (after the retarget bake that "played" but was frozen, the foot-IK that was wired-but-inert, the ControlRig alpha priced as a cost gate it isn't, the vacuous `AbsoluteWeight`, and `Speed` silently re-derived past a reflection write) — and the **first** that the checks on the change itself missed, caught instead by an unrelated measurement. The lesson generalizes the convention to measurements as much as features: *a budget read is only trustworthy when the crowd it measured was in the condition it ships in* — here, driven.

The honesty anchor for foot IK is: **measured ground conformance on this integration — +3.46/−7.14 cm against a proven-node-free baseline, byte-identical to stock** — with the stock rig's limits still applying (no foot locking, single non-predictive trace, no toe roll), so the caption is "conforms both feet to the slope, matching stock," never "production foot IK." The historical −6.5→−3.1 cm and 0.03-cm figures stay superseded (confounded / different reference). The two superseded flips: `research/foot-ik-erratum.md`.

## Import integrity, skeleton-aware IK and the rendered shots — a fourth verification surface, and one pattern found five times in one change

The import-integrity and skeleton-aware work took the suite **default 15 → 23, pie 20 → 22, with zero `SKIP (localOnly` on either tier on a machine with the licensed content installed** (a clean clone skips the licensed rows; see the clean-clone figures below). **Eight** new default rows: `ArmyBudgetZeroGuard` (1), `FixImportScale` (1), `ImportScale.metahuman` (1), `IKEffectorDrives` (4) and `FbxHeaderWalkIntegrity` (1), plus gates that live outside the automation suite entirely (listed further down). The floor these numbers come from is `TIER_MIN_ROWS`; prose row counts drift while a floor checked by code does not, which is why `test_repo_integrity.py` checks the pinned figures against `tier_counts.json`.

| Row(s) | What it asserts | Why it exists |
|---|---|---|
| `Verify.FixImportScale` | the fix half of the import-scale tool: flag-don't-fix runs **pre-import** (nothing is imported when a file carries authored node scale), the correction is **measured from what landed** and never guessed from metadata, and the post-fix re-gate turns a bad factor into `ERROR` rather than a quiet `OK` | the catch→fix promise was previously unbacked by any test |
| `Verify.ImportScale.metahuman` | the 4th roster scale row, closing the documented MetaHuman degrade | the roster gate was 3-of-4 until then |
| `Verify.IKEffectorDrives` ×4 | that each clip's `ik_foot_l/r` effector track actually follows its FK foot — max drift **0.0001 cm** / **≤0.00103°** against thresholds of 0.05 | a *constant* effector pins a foot exactly as a *missing* one does; this is the only row that can tell them apart |
| `Pie.SkeletonAwareFootIK` ×2 | the Template's foot-IK pass is gated on a **computed** skeleton property: class-default `false`, live instance `true` | a gate rewired to constant `true` passes a naive presence check on every rig that has the bone |
| `Verify.ArmyBudgetZeroGuard` | the crowd budget's per-tier readings are not silently zero — an explicit zero-reading count, which is anchor-independent where the old scale-vs-anchor inequality was not | a tier whose anim cost collapses to 0 passed the scale check; the inequality cannot see a middle-weight tier collapsing whichever anchor is chosen |
| `Verify.FbxHeaderWalkIntegrity` | an aborted FBX **property** walk is counted and the count is *read* (`FixImportScale` reaches a different verdict for two fixtures differing by one byte); and that an aborted **structural** walk is counted, refused, and likewise reaches a different verdict for two fixtures differing by four bytes | a `P` record that aborts at property 1 loses a real `Lcl Scaling` and looks exactly like a clean file; a structural abort loses every record after it and used to look exactly like a completed walk |

**The rendered shot sequence is a gated surface.** `Scripts/shot_check.py` is not an automation row and does not run in either tier, but it is the same discipline pointed at the rendered output: every caption figure is bound **positionally** to a dotted key in committed evidence (a transposed up/down pair fails), a shot's declared runtime must equal `frame_range / fps_out`, and in its strict mode the burned-in `caption_text` must contain its own claim and may not quote a number no citation covers. It has refused the cut for real — corrupt the crowd take's citation and it exits 1 naming the key. It also has a blind spot worth stating: **`shot_check` cannot see pixels.** Changing a caption without re-assembling used to leave the MP4 displaying the old text while every check stayed green, and the only control was procedural (captions are burned at assemble time, so a caption edit *requires* a re-assemble).

> **The procedural control was replaced.** A procedural control is the same species as the naming convention that failed to stop the crowd-take namespace merge (below). `shot_assemble` now writes an `.assembly.json` stamp beside the MP4, carrying a SHA-256 fingerprint of the manifest fields that determine the pixels (resolution, `fps_out`, and per shipped shot its id, order, `source_dir`, `frame_range`, `caption_text`), and `shot_check` fails on a mismatch or on an artifact with no stamp at all. What survives is the honest half: **it still cannot see pixels.** It can prove the file was produced by an assembler run over *this* manifest state; it cannot prove ffmpeg drew what PIL handed it.

**The pattern the import-scale work found five times, in one change, all the same fault: a check that cannot observe what it claims to check.**

1. An **assertion that could not fail** — the ordering guard used `DoesPackageExist` (a *disk* check) while the import deliberately never saves, so it passed under the correct build, under the stub, and would have passed under a broken one. Fixed with a loaded-`UPackage` scan and **demonstrated red** against a deliberately reordered build.
2. **The fix repeated the sin.** Four ordering assertions were written; only one went red. The three on geometry-free ASCII fixtures could not fail either — an early import creates nothing to detect. They were deleted rather than kept as guards that cannot guard. *Only the mutation exposed this; reasoning did not.*
3. A **proof that re-derived instead of invoking** — the arithmetic was verified in test code rather than by calling the composition, so three concrete mis-wires were invisible at green.
4. A **fake unfaithful in exactly the deciding field** — the test double emitted `importScale` as a *number* while production (Interchange's `UInterchangeAssetImportData`) emits a *string*, so the green coverage certified a path production could never enter. The lesson generalises: *derive a test double from the branches the code under test reads, not from what looks reasonable.*
5. A **channel computed and then discarded** — the "this check went dark" notes were surfaced in two callers and dropped by the one that runs every tier. The wrapper that discarded them was deleted rather than patched, because a convenience API that silently drops half a two-channel result **is** the defect.

That fifth one is now visibly live rather than latent: `Verify.ImportScale.metahuman` currently passes as **`succeededWithWarnings`**, reporting `importScale not comparable … the stale-import-option check … did not run on this mesh`. Before the fix that row would have been red for the wrong reason; after a naive fix it would have been green and silent. It is green **and loud**, which is the correct third option.

**Two green rows whose green means less than their name suggests — stated because a suite that hides this is worse than one that has fewer rows.**

- **`Pie.RigLocomotion.Khaimera` is green for a degenerate reason, and no threshold can fix it.** Its foot-swing band was retrofitted with a ceiling (`kFootSwingMaxCm = 75.0`) after a historical artefact was found to sail under a floor-only check. But harvested in the quantity the assertion actually compares — pawn-relative Z — that row reads **33–35 cm, *below* the healthy mannequins' 46–51**, so neither a floor nor a ceiling separates dragged legs from working ones. The clips it would need to discriminate against are off-limits to write. **The real gate for that failure is the headless `Verify.IKEffectorDrives` row**, and the code says so at the assertion rather than implying a coverage it does not have. The ceiling's high side rests on a **single** historical observation, and the code says that too.
- **`GoldenDiff.MTLocomotion` cannot see most of the skeleton-aware change.** Baseline schema v2 captures the AnimGraph, so the new bool, its init event and the AND are invisible; only the `Alpha` binding string moved, which is why that whole feature's golden diff is one line. An edit keeping the name `FootIKAlpha` while gutting the logic would pass. `Pie.SkeletonAwareFootIK` is the compensating control, and it is a PIE row, not a golden one.

**One more measurement hazard, banked.** `get_bone_pose_for_frame` **applies bone translation retargeting**. On `SK_Mannequin` the FK leg chain is `AnimationScaled` while the `ik_foot_*` chain is `Animation`, so a probe that composes one and differences it against the other is wrong by centimetres — in translation only, which is exactly why a phantom "residual" once matched digit-for-digit on rotation and not at all on translation. `USkeleton`'s retargeting mode is not exposed to Python, so a Python baking script **cannot assert its own precondition**; `Verify.IKEffectorDrives` is the guard.

## Closing the carried items, and two fault classes no gate could see

This section records what happened to each open item on the list, because a carried item that is neither closed nor re-argued becomes an omission the next time somebody reads the list.

**The one new automation row: `Verify.FbxHeaderWalkIntegrity`** (default tier 22 → **23**; pie unchanged at 22).

`FbxScaleHeader`'s binary walker abandons a property record on a typecode it does not handle, then resyncs
from the record's own declared length and carries on — correct, and the reason the loss was invisible: after
the resync the reader is indistinguishable from one that read everything. A `P` record's property 0 is the
property *name*, so an abort at property 1 drops it below the `Props.Num() >= 2` gate and a real `Lcl Scaling`
went **unrecorded**, `ScaledNodes` came back empty, and `FixImportScale` **imported** the file instead of
flagging it — while the design narrative claimed a pre-import barrier. `FFbxScaleMetadata` now counts aborted
walks (`AbortedPropertyWalks`, and the `P`-record subset that can change a verdict), and `FixImportScale`
**refuses** on a nonzero P-record count rather than treating an incomplete read as a clean one. The row
asserts both halves — the counter *and* the refusal it drives — because a counter nobody reads is the same
fault wearing a new hat. Its two fixtures are synthesised in-process from the format spec and differ in
exactly one byte, so a difference in verdict can only have come from the abort; they need no engine sample,
no licensed content and no Interchange, so unlike the engine-sample binary leg this row **exercises on a
clean checkout**.

**Four checks that are not automation rows, added where the fault actually lives:**

- **`Scripts/namespace_merge_check.py`** — static (`ast`, stdlib) detection of the exec-namespace-**merge**
  class. `exec(src, globals())` is a plain merge; `army_conductor.py`'s `TOTAL = 24.0` landing on a driver's
  own `TOTAL = 8.0` retimed the crowd take so two thirds of the crowd left frame, with frame count,
  `army=46`, `capture_check` **and** `success=True` all green — caught only by comparing the frames against an earlier render of the same take. The
  only defence afterwards was a naming convention recorded in comments. This resolves both real merge sites
  (following the path constants, since neither passes a literal), intersects module-level bindings, and
  discounts import/import overlaps. Its proven-OFF is **baked in**, not just transcribed:
  `test_namespace_merge.py` reproduces the crowd-take collision as a fixture on every pytest run, and a run
  that resolves *no* merge site is reported as an error rather than a pass.
- **The assembled MP4 is bound to the manifest** — see "The procedural control was replaced" above.
- **`run_verification.TIER_MIN_ROWS`** — the whole-module-vanishes floor; see the Interchange note in the last section.
- **Warnings are printed, and skips are counted.** `Verify.FixImportScale`'s engine-samples leg `AddWarning`s
  rather than `AddError`s so the suite stays portable to an engine install without
  `Engine/Content/FbxEditorAutomation` — still the right trade, but the warning went **nowhere**, so "the
  flag-before-import ordering guard did NOT run" could be true while the screen said `Failed: 0`. The runner
  now prints Warning entries from green rows, labels localOnly-skipped rows `SKIP` instead of `PASS`, and
  reports `Skipped(localOnly)` and `Exercised` beside `Tests`.

**`Tests: 22  Failed: 0` never meant "22 things were checked."** On a machine without the licensed content a
localOnly row is registered, green, and asserts nothing — so a clean clone registers substantially more rows
than it exercises. That is the correct behaviour and is itself asserted (absence must SKIP, not fail), but it
means the registered count was never the coverage. Both the runner and `clean_checkout_check.py` now print
the **exercised** count beside the registered one, so the only reading of those summaries that was ever wrong
is no longer available. The first clean-clone measurement of it: **45 rows registered across the two tiers, 30
exercised** (default 23/12 — 11 of its rows are Khaimera or MetaHuman content; pie 22/18). The split for any
given commit is printed by the run that produces it, because a figure
asserted here would go stale silently, which is the failure this whole document is about.

> **Correction.** That pie figure was itself overstated by three. The `Pie.Army.*` rows logged
> `SKIP (army map absent on this machine)`, without the `localOnly` marker the runner counts, so on a clone
> without the army stage three rows that asserted nothing were reported as exercised; pie was really 22/15.
> The message now carries the marker, and `test_repo_integrity` fails on any whole-row SKIP message that
> lacks it. A clean-clone measurement taken before publication then read default **25/14** (11 SKIP), pie
> ~~**23/15** (8 SKIP, the four army rows among them)~~ (pie figure superseded below).

> **One row added since.** `Pie.Army.OnCapsule` adds one pie row. It is gated like the other army
> rows (the army stage, plus every crowd rig's roster pawn), so on a clean clone it SKIPs with the counted
> marker and the exercised count does not move. Derived from the `clean_checkout_check` clone measurement
> taken before publication plus that one row, NOT re-measured on a clean clone, and the two dynamics golden rows added later, which SKIP there, and the two build-and-describe rows added after that, which run: default **29/16** (13 SKIP), pie **24/15** (9 SKIP, the five army rows among them).

**`clean_checkout_check.py` stays a command — decided, with the reason.** It clones, builds and runs both
tiers (~200 s). Wiring it into a tier would make the iterate loop unusable and it would be learned around; a
pre-push hook fires on commits that cannot have broken it and gets `--no-verify`'d. Where it would genuinely
belong is CI on push, but it needs an Unreal Editor install and a GPU, which the hosted CI runner
(`.github/workflows/host-tests.yml`) does not have — that runner covers only the editor-free half (the public
gate and the host pytest suite) — so the editor half stays open (see "Scheduled, not yet shipped" below)
rather than being simulated by a local hook. So it is the **release gate**: run it before any claim that the
project works from a clean checkout. What did change is that its cheap leg got cheaper to trust — it now
collects the whole `Scripts/` pytest directory instead of two hard-coded filenames (a test file added later
was simply not being run while this script still said "pytest OK") and carries the namespace-merge gate.

**Re-recorded as decisions, not closed:**

- **The import-scale scatter band.** `lengthTolerancePct` (1 %) and the agreement window (0.15) differ by 15×, so a
  spread between them yields a confident correction followed by a self-rejection. Left open because the tool
  is **honest** there — `ERROR`, not a quiet `OK` — and because the principled fix drives the classifier from
  `lengthTolerancePct`, which is the **shared roster-gate** semantics written into all four committed
  baselines; changing which drifts count as "one uniform factor" as a side effect of closing a band nothing
  has landed in is the wrong trade. The reasoning now lives at the threshold in
  `ImportScaleCheck.cpp`, not only in a report.
- **The army zero-guard's two limits** — it cannot catch a tier degrading to *small-but-nonzero*, and its zero
  check would false-positive on a legitimately zero-anim-cost tier (unreachable at the shipped 6/8/32 shape,
  where every tier runs the same ABP). Both are now argued at `CheckArmyMemoryReading` with what would have to
  change to reopen them.

**Two smaller carries, closed:** the citation layer's count-mismatch fallback to set-membership now emits a
`NOTE:` instead of downgrading in silence (latent by construction today — asserted, rather than asserted *in
prose*, by a test over the shipped manifest); and `mrq_render`'s `_err` now sets `_MT_RENDER_DONE` /
`_MT_RENDER_SUCCESS = False` on a **fatal**, so a fatal without a `Finished` delegate ends the caller's poll
with the right answer instead of hanging it with the reason buried in the Output Log.

## Baseline-regen discipline

The phrase is "reviewed like code," and the mechanics make that literal:

- **Regeneration is a deliberate act** — `SnapshotBaseline`'s own docstring says so — landing as a single reviewed commit whose diff is the review artifact.
- **Confirm the failure shape *before* regenerating.** When the foot-IK pin link was added, the red gate was run and diffed first: only `+` additions (the two new K2 nodes), zero `-` removals — proving the regen would not quietly absorb a regression alongside the intended change. Then regenerated, then re-diffed to `IDENTICAL`, then checked the git diff of the baseline itself (+8 lines, the retained `"Alpha":"GroundedFlag"` line untouched, the other baseline 0 lines).
- **Prove closure with a negative check** when the format itself changes (the v2 unbind/rebind check above). A schema bump that only ever goes green has proven nothing.
- **A red gate is information, not noise:** when the foot-IK node was spliced in, the gate began at the *expected* 4/1 and the change was scoped to turning exactly that one red green — an unexpected red anywhere else blocks.

## Scheduled, not yet shipped (honesty ledger)

> **Status.** The list below records what was intended before it was built; five of its six entries are now delivered, and the closures are listed here so the measured-vs-intended boundary stays visible.
> **CLOSED:** per-rig baselines + per-rig PIE assertions (four rigs); the clip-varies test (7 `Verify.ClipVaries` rows); the import-scale assertion (`AssertImportScale`, then `FixImportScale` — `AnimImportTools` source exists and is gated); the foot-IK A/B photography (rendered as the foot-IK A/B take).
> **PARTLY CLOSED:** **CI runner.** `.github/workflows/host-tests.yml` runs the public gate (`--generic`) and the host pytest suite on every push and pull request. The editor tiers (`default` and `pie`) and `clean_checkout_check.py` still run locally only; for those, the visible signal remains the green local run.
> **OPEN RISK:** the plugin **hard-requires the Interchange engine plugin** (`.uplugin` + `Build.cs`). If it were ever absent the whole `MotionTerminal` module fails to load, taking **every default-tier row** with it (25 at the last recorded count, `Plugins/MotionTerminal/Tests/tier_counts.json`), not just the import-scale row — very low probability (engine-shipped, default-on since 5.4) and there is no cheaper way to express the dependency, but it is a single point of failure for the entire gate. Also: `FixImportScale` imports through a **fresh** `UInterchangeGenericAssetsPipeline` with Epic class defaults, not the project's configured pipeline stack, so if that stack is ever customised the tool and a Content Browser drag-drop will diverge.

> **The Interchange risk is detectable.** It remains a single point of failure and is recorded as one, but its symptom can no longer be mistaken for a pass. `run_verification.TIER_MIN_ROWS` is a per-tier row **floor** checked outside the module (23 default / 22 pie when it was added); below it the runner prints `THIS IS NOT A PASS — rows that never ran cannot fail`, names the whole-module-vanishes shape explicitly, and exits 2. `clean_checkout_check.py` reads the same floor out of the clone's own runner rather than copying the numbers. The floor is a floor: adding rows is bookkeeping, **lowering** it is the claim that has to be argued.

Claims this list originally declined to make, each now closed or partly closed (see the status note above):

- **Per-rig baselines and per-rig PIE assertions** — one baseline existed per *asset* at the time (`abp_unarmed`, `abp_mt_locomotion`); the roster expansion was next.
- **The clip-varies test** — the discipline was practiced manually (`get_bone_pose_for_time` probes, data-verified before any "plays" claim) and was to land as a suite test with the roster.
- **Import-scale assertion in the gate** (`AssertImportScale`) — detect/assert first, then the fix; no `AnimImportTools` source existed yet.
- **CI runner** — the editor tiers still have none; the hosted runner covers the editor-free checks only.
- **The foot-IK A/B *photography*** — deferred to a render-capable session; the quantitative A/B evidence at `research/fixtures/foot-ik-ab-contact/` stands in.

The pattern in this section is the same as everywhere else in the suite: the boundary between *measured* and *intended* is always written down, because the two failure stories at the top of this document were both, at bottom, that boundary going unmarked.
