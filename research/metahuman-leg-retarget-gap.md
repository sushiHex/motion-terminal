# MetaHuman legs don't stride in the 4-up jog take — diagnosis

> ## STATUS: CONFIRMED LIVE AND ACTED ON
>
> Everything below was written without an editor. It has now been run. **The diagnosis is correct** and the
> §6 recommended fix has shipped. Two things below are wrong; they are corrected here, next to the original
> reasoning, because how they went wrong is the useful part.
>
> **1. §5's decisive check passed, with a bigger margin than predicted.** `AnimGraphNode_ControlRig`
> structurally removed from a throwaway duplicate, assigned to the MetaHuman pawn's component only, probed
> at Speed 600 on `foot_l`/`foot_r`: foot world-Z range **1.23 cm → 39.54 / 46.21 cm**, swing clearance
> **2.32 cm → 18.41 cm**. Predicted 20–30 cm. The shipped gate (§6) reproduces those numbers
> **bit-identically**, which is what proves `Alpha = 0` on this node is equivalent to the node's absence
> rather than merely similar to it.
>
> **2. §4's "one asymmetry that could not be explained offline" is resolved, and its assumption was wrong.**
> The doc assumed Khaimera was fine and that the rig no-ops on him. **He is pinned exactly as the MetaHuman
> was.** Measured in PIE: `foot_l` moves dx 0.21 / dy 0.09 / dz 0.63 cm over a 7 s take while his `calf_l`
> moves 12.66 and his `hand_r` 72.25 — the same frozen-foot-under-a-moving-leg signature. Confirmed on the
> shipped frames too: tight crops of his boots across a stride cycle show both feet flat and static, only
> the shins and the loincloth moving. §2.1's claim that "on Manny, Quinn **and Khaimera** the legs are
> bright blur all the way to the ground" is **false for Khaimera** — that blur is his loincloth, his
> feathers and his moving cast shadow, and it fooled a temporal-standard-deviation map exactly the way
> `hand_r` fooled the probe.
>
> **Why he is pinned is different, and it is the sharper finding.** Khaimera *has* `ik_foot_root/l/r` —
> they are native to the 198-bone Paragon skeleton — so the §6 gate reads **true** for him and correctly
> leaves his foot-IK pass running. But his baked retarget clips carry those tracks **dead**. Measured over
> every key of `Khai_MF_Unarmed_Jog_Fwd_InPlace`, `ik_foot_l` translation spans **0.000 / 0.000 / 0.000**,
> against **14.163 / 116.384 / 50.635** on the mannequin source `MF_Unarmed_Jog_Fwd`. **A constant effector
> pins a foot exactly as a missing one does.** So bone presence is *necessary but not sufficient*: the
> precondition this pass really needs is "the animation drives the IK effectors", a property of the
> **clips**, not of the **skeleton**. The gate shipped as specified; fixing Khaimera is a
> clip-bake job (write live `ik_foot_l/r` tracks into the gitignored `InPlace` duplicates from the FK feet)
> and was left out of scope. The 4-up jog take therefore ships with **three of four rigs striding** and a caption that
> asserts no gait.
>
> **One structural correction to §2.3.** The Control Rig node is **not** bracketed inside
> `LocalToComponentSpace → ControlRig → ComponentToLocalSpace`. The live graph reads
> `… → LocalToComponentSpace → TwoBoneIK(hand_l) → ComponentToLocalSpace → ControlRig → Output Pose`: the
> node sits *after* the bracket and takes a local-space pose. This does not affect the diagnosis (the
> anim node does its own space conversion), but the baseline was being read too eagerly.


**Scope:** diagnosis + recommendation only. Nothing was changed. No live editor was available when this was
written, so every conclusion below is **source- or frame-derived**; the one live check that would close the
last gap is named in §5.

**Filename note:** this is *not* a retargeting gap. The compatible-skeleton bind and the pose remap work.
The legs are being overwritten *after* they animate. The filename is kept so existing links stay valid.

---

## 1. Verdict

The MetaHuman body's feet are **pinned by the shared Template's own foot-IK Control Rig pass**, which runs
at full weight on the MetaHuman and solves each leg onto `ik_foot_l` / `ik_foot_r` — two of the nine bones
`metahuman_base_skel` does not have. Those two effectors therefore never move, and the solver drags the
MetaHuman's feet onto them every frame while the animated pose above the solve plays normally.

The graph, the bind, the clip sampling and the skeleton remap are all fine. The stride is authored, reaches
the legs, and is then destroyed by the last node in the chain.

The project already reasoned about this and reached the **opposite** conclusion. `render_evidence.json`
`roster_4up._scope_limit_foot_ik` says:

> "The foot-IK ControlRig drives ik_foot_l / ik_foot_r, so it **cannot reach this rig** — 'foot IK stays
> mannequin-only' in the caption is that absence, not a preference."

and `_what_the_shipped_frames_do_NOT_show` (b) repeats "cannot reach the MetaHuman at all." Both are false.
The pass reaches it; what it cannot do is *succeed*. The caption's disclaimer is not describing an absent
feature, it is describing an active defect. This is a known error shape — any "rig X cannot do Y" claim
must name the mechanism it traced, not the symbol it failed to find — and here the claim was inferred from
a missing symbol instead of traced.

---

## 2. The measured evidence

### 2.1 The frames: feet static, body moving (this is the kinematic clincher)

Measured over frames 0596..0616 of the gitignored 4-up jog capture under `captures/` (≈ one stride cycle at
30 fps), luminance threshold 205, per-rig column band, no editor required:

| rig | lowest body-pixel row across the cycle | range |
|---|---|---|
| **MetaHuman** | 809, 810, 811, 811, 811, 811, 811, 811, 813, 813, 810 | **4 px** |
| Manny | 795 … 827 | **32 px** |

At this framing the MetaHuman spans ~343 px for 158.37 cm, i.e. **0.46 cm/px**. Its 4 px is
anti-aliasing/shadow noise (≈1.8 cm); Manny's 32 px is a real swing foot, and that measure *understates*
his swing because it only tracks the lowest pixel (the planted foot).

A per-pixel temporal standard-deviation map over frames 560–615 shows it directly: on the MetaHuman the
shins and both feet are a **dark, zero-variance silhouette** standing inside a bright moving shadow, while
the torso and arms are bright. On Manny, Quinn and Khaimera the legs are bright blur all the way to the
ground. Reproduce with:

```python
# read-only, from repo root; FRAMES is the 4-up jog capture's frame pattern under captures/
import numpy as np
from PIL import Image
FRAMES = 'captures/.../frame_%04d.png'
fr = range(560, 616); acc = acc2 = None
for i in fr:
    a = np.asarray(Image.open(FRAMES % i).convert('L'), float)
    acc  = a  if acc  is None else acc  + a
    acc2 = a*a if acc2 is None else acc2 + a*a
n = len(list(fr)); mean = acc/n
Image.fromarray(np.clip(np.sqrt(np.maximum(acc2/n - mean*mean, 0))*4, 0, 255).astype('uint8')).save('sd.png')
```

Tight 5x crops of the MetaHuman's feet (`box=(215,755,375,835)`) across frames 596→616 show **both feet
flat on the ground in identical positions in every frame**; only the cast shadow changes. The same crop on
Manny (`box=(660,715,830,800)`) shows one leg planted and one clearly airborne, alternating.

**Why this rules out a pose-level cause.** The MetaHuman's pelvis, hips and torso translate visibly across
the cycle while the feet do not move at all. A frozen or mis-blended *local-space* leg pose cannot produce
that — the feet would ride with the pelvis. Feet fixed in component space while the pelvis moves is only
achievable by a **component-space IK solve with a static effector**. The Template has exactly one such
node, and the baseline shows it bracketed in `LocalToComponentSpace → ControlRig → ComponentToLocalSpace`.

### 2.2 The bone inventory (committed baselines, `Plugins/MotionTerminal/Tests/ScaleBaselines/`)

| rig | bones | `ik_foot_root/l/r` | `thigh/calf/foot/ball _l/_r` |
|---|---|---|---|
| Manny (`SKM_Manny_Simple`) | 89 | **present** | present |
| Quinn (`SKM_Quinn_Simple`) | 89 | **present** | present |
| Khaimera (198-bone Paragon) | 198 | **present** | present |
| **MetaHuman (`MH_MT_Roster_FullBody`)** | 342 | **ABSENT** | present |

The MetaHuman is the **only rig in the roster without the mannequin IK rig**, and it is the only rig whose
legs don't stride. Its deform leg chain is fully present under identical names, which is why "bone-name
mismatch in the leg chain" is not the answer.

### 2.3 The graph (committed GoldenDiff baseline `Tests/Baselines/abp_mt_locomotion.json`)

`ABP_MT_Locomotion`'s top-level AnimGraph contains, among others:

```
AnimGraphNode_LayeredBoneBlend      | Layered blend per bone
AnimGraphNode_LocalToComponentSpace | Local To Component
AnimGraphNode_ControlRig            | Control Rig          | {'Alpha': 'GroundedFlag'}
AnimGraphNode_ComponentToLocalSpace | Component To Local
AnimGraphNode_TwoBoneIK             | Bone: hand_l         | {'Alpha': 'WeaponIKAlpha', ...}
AnimGraphNode_Root                  | Output Pose
```

- The Control Rig is `CR_Mannequin_FootIK_C` (`/Game/Characters/Mannequins/Rigs/CR_Mannequin_FootIK`),
  confirmed at `AnimNode_ControlRig<W:100.0%>` in four independent live node-weight captures.
- Its **Alpha is bound to `GroundedFlag`**. Every rig in the 4-up jog take is grounded for the whole take
  (`MOVE_None`, velocity forced, no jump), so **Alpha = 1 on the MetaHuman for all 900 frames**. There is
  no gate that excludes it.
- Node order `SM → LBPB → CR → Output` is from the live node-weight captures, not from the baseline (schema v2 records node presence and pin bindings, not link edges —
  `docs/verification-design.md` says so explicitly).
- `ABP_MT_Khaimera` is a child with `"graphs": []` — it owns no graph nodes, so Khaimera runs this same
  Control Rig pass.

### 2.4 What `CR_Mannequin_FootIK` does to the feet — the project's own measurement

`research/foot-ik-erratum.md`, in its node-running, ground-trace-OFF state:

> "drives `foot_l` **ONTO** `ik_foot_l` (dL→0), 3.45 cm off the animation"

That is direct, committed, in-project evidence that the rig's leg effector **is** the `ik_foot_l` /
`ik_foot_r` bone transform (the ground trace then adds a Z offset on top). On the mannequin family this is
invisible on flat ground because the source clips key `ik_foot_l/r` to track the FK feet — the effector
strides, so the solve reproduces the stride (and on a ramp it conforms, +3.46 / −7.14 cm).

---

## 3. The mechanism, stated once

1. `BS_Idle_Walk_Run` at Speed 600 samples the jog. The clip is remapped from `SK_Mannequin` into
   `metahuman_base_skel` by the compatible-skeleton path; every deform bone the clip drives exists under
   the same name, so the **legs receive a correct striding pose**. (This is why the upper body looks right
   — it is the same remap.)
2. `LayeredBoneBlend` layers the upper body; legs pass through from the base.
3. `LocalToComponentSpace → AnimNode_ControlRig(CR_Mannequin_FootIK_C)`, Alpha = `GroundedFlag` = 1.
4. The Control Rig's **own** hierarchy was imported from `SK_Mannequin` and therefore *does* contain
   `ik_foot_root/l/r`. The anim node maps rig bones to mesh bones **by name against the mesh's required
   bones**. On the MetaHuman those three names do not resolve, so those three elements receive no input
   from the pose and hold a **static transform** for the entire take.
5. The rig solves `foot_l`/`foot_r` onto those static targets. Result: two fixed, flat, ground-level foot
   placements, held every frame. The shorter MetaHuman legs (femur+tibia 74.2 cm vs the mannequin's
   85.6 cm; pelvis 83.5 cm vs 95.9 cm) have to splay and straighten to reach mannequin-ref-pose foot
   positions — which is exactly the wide, bowed, flat-footed stance in the frames.
6. Nothing above the pelvis is in a leg chain, so the arms and torso play the jog untouched. Hence
   `hand_r` sweeping 43.63 cm and the state machine reading `Locomotion`: **both true, both irrelevant.**

**Why the standing take shipped clean and the jog take didn't.** The earlier 4-up was a *standing*
line-up. A planted idle looks identical whether or not the feet are pinned. The defect only became visible
when the same stage was re-shot as a jog.

---

## 4. Candidates ruled out

| candidate | verdict | why |
|---|---|---|
| Bone-name mismatch in the leg chain | **ruled out** | `thigh/calf/foot/ball_l/_r` + twists all present on the MetaHuman under identical names (`ScaleBaselines/metahuman.json`). |
| Compatible-skeleton remap silently ref-posing unmapped bones | **ruled out** | The mapping is by name and the leg names all map. Also, ref-posed legs would be *straight and static relative to the pelvis*; the frames show the pelvis moving while the feet stay — impossible from a local-space pose. |
| Translation-retargeting mode on the leg bones | **ruled out as the cause** | Translation modes change proportions and pelvis height, not whether the legs swing. A wrong mode cannot freeze a foot in component space. Worth a look *after* the fix if the corrected stride sits at the wrong height. |
| Root-motion / pelvis handling | **ruled out** | All four capsules are `MOVE_None` with pose-only motion; the other three behave. |
| Bind-pose orientation difference | **ruled out** | Would produce a *wrong* pose, not a *static* one, and would corrupt the upper body identically — it doesn't. |
| `LayeredBoneBlend` mask miscomputed on a 342-bone hierarchy | **ruled out** | Same kinematic argument: a local-space blend cannot hold the feet still while the pelvis translates. It would also have to leave the upper body correct, which is the half it actually controls. |
| The `TwoBoneIK` on `hand_l` | **not involved** | Arm chain; `WeaponIKAlpha` = 0 in the unarmed jog take; and `hand_l/lowerarm_l/upperarm_l` were already verified to exist on the MetaHuman. |
| Missing `ik_hand_*` bones | **not involved** | Same reason. The weapon beat uses a socket on `SM_MT_Rifle`, not the skeleton bone. |

**One asymmetry that could not be explained offline.** Khaimera *also* runs this Control Rig (his child ABP
owns no graph), *also* has `ik_foot_root/l/r`, and his baked `Khai_*` clips most likely carry those bones as
static retarget-pose values — yet his feet swing correctly in the frames, and an earlier Khaimera foot-IK
check measured the rig as a **quiet no-op** on him (zero ControlRig warnings, `footIK:false`). The most
likely reading is that the Control Rig's bone mapping fails broadly enough on `Khaimera_Skeleton` that the
node does nothing, whereas `metahuman_base_skel` is *mannequin-name-compatible enough for the rig to run*
and incompatible in exactly the three bones the solve depends on — the worst possible middle ground. This
does not change the diagnosis (the frames settle what happens) and does not change the fix (see §6, the
gate keys on a bone Khaimera has, so he is left exactly as-is), but it is an open sub-question and should be
recorded as one.

---

## 5. Confidence, and the one live check that would settle it

**Confidence: high (≈90%).** Steps 1–3 and 6 of §3 and every row of §2 are source- or pixel-derived and do
not need an editor. Step 4 — *that the rig's `ik_foot` elements go static specifically because the anim
node's name mapping doesn't resolve them* — is inferred; `FAnimNode_ControlRigBase` in the 5.8 install was
not read and the editor could not be run. The observable consequence (a static effector) is however forced
by the frames regardless of which internal path produces it.

**The decisive check — the project's confound-immune structural-removal method, one PIE session:**

1. Duplicate `ABP_MT_Locomotion` to a throwaway (`Tmp_MT_Locomotion_NoCR`), **structurally remove** the
   `AnimGraphNode_ControlRig` (never an alpha disable — the lesson of `research/foot-ik-erratum.md`; a
   live `Alpha ← GroundedFlag` binding overwrites a forced 0). Prove removal via `GatherDebugData` node
   count, as the foot-IK re-measurement did.
2. Assign it to the **MetaHuman pawn's component only** (`mesh.set_editor_property('anim_class', ...)`,
   the exact mechanism `metahuman_bind.place()` already uses for Khaimera).
3. Re-run the probe at Speed 600 sampling **`foot_l` / `foot_r` world Z**, not `hand_r`.

**Prediction:** foot Z range goes from ~0 to 20–30 cm and the shipped rig starts striding. If it does not,
the diagnosis is wrong and the next suspect is the remap after all — but that hypothesis then owes an
explanation of how the feet stay fixed in component space while the pelvis moves.

**Cheap pre-check in the same session:** confirm the CR node reads `W:100.0%` on the MetaHuman's anim
instance. Caveat: do **not** try to read `ik_foot_l` via `mesh.get_socket_location('ik_foot_l')` — the mesh
has no such bone, so that returns the component location, not the rig's internal value. It reads the mesh,
not the hierarchy.

---

## 6. Is it fixable inside the constraints? Yes — and cheaply

### Recommended — gate the Control Rig on the presence of the mannequin IK rig, inside the shared Template

Make the CR node's Alpha `GroundedFlag AND HasMannequinFootIKRig`, where `HasMannequinFootIKRig` is a bool
computed **once** in `BlueprintInitializeAnimation` as
`TryGetPawnOwner → Mesh → GetBoneIndex("ik_foot_root") != -1`
(`USkinnedMeshComponent::GetBoneIndex` is BlueprintCallable, so no new C++ is required; if a thread-safe
pure node is preferred, the `MotionTerminal` plugin already hosts helper statics).

- **Constraint-clean.** Touches `/Game/MT/ABP_MT_Locomotion.uasset` only — a tracked, project-owned asset.
  No stock content, nothing under `C:/Program Files/Epic Games/UE_5.8/`.
- **No behaviour change for Manny, Quinn or Khaimera** — all three have `ik_foot_root`, so the gate reads
  true and the node runs exactly as today. Prove it rather than assert it: re-run
  `Pie.FootIKNodeIntegration` (the ramp conformance assertion, currently green on Manny Δ0.45 /
  Quinn Δ0.72) after the change.
- **The MetaHuman gets its stride back**, on the unmodified-in-spirit shared Template — and the claim
  *improves*: the one graph is now genuinely skeleton-aware, skipping a mannequin-specific IK pass on a rig
  that has no mannequin IK rig. That is a better result than the current one.
- **Blast radius:** `Tests/Baselines/abp_mt_locomotion.json` must be regenerated (one new K2 node, changed
  Alpha binding); `GoldenDiff.MTLocomotion` and the topology round-trip go red until it is, which is the
  expected, reviewable pattern this repo already uses.

**What the change involves:** the ABP edit (`set_anim_node_pin_binding` is the proven-working path;
`set_transition_rule negate` is the known-broken sibling — different code path); a baseline regen; a four-rig
re-probe with foot metrics; and a re-render of the 900 frames, re-assembly of the shot sequence and a
caption/citation update, which is the largest part.

### Fallback if the re-baseline is unacceptable — a MetaHuman-only duplicate ABP

Author a gitignored `Content/MT/Showcase/` duplicate with the CR node structurally removed and assign it at
placement time, exactly as Khaimera's in-place ABP is assigned today. **Zero** tracked-asset or
test churn.

**But the MetaHuman would no longer run the shared Template.** `roster_4up.rigs_on_shared_template_unmodified`
drops 3 → 2, so this is the weaker option and only worth taking if the re-baseline is ruled out.

### Not viable — give the MetaHuman the missing bones

Adding `ik_foot_root/l/r` (real or virtual) means writing `metahuman_base_skel`, which resolves into
`C:/Program Files/Epic Games/UE_5.8/Engine/Plugins/MetaHuman/MetaHumanCharacter/Content/…` — the engine
install, shared by every project that uses it. **Say it plainly: this is forbidden and there is no
in-memory dodge.** The `AddCompatibleSkeleton` precedent does not transfer: the no-leak property was
established for that one call specifically (changed-but-not-dirty, positive-controlled against a /Game
duplicate), and `AddNewVirtualBone` has no such control. Worth one line for the record: a virtual
`ik_foot_l` sourced from `foot_l` *would* make the solve a near-identity and fix this with one bone —
if the skeleton were ever project-local. It isn't.

### Rejected — retarget + bake onto the MetaHuman skeleton

Does not address the cause. A baked clip on `metahuman_base_skel` still has no `ik_foot` tracks, because
the target skeleton has no such bones — the rig would pin the feet exactly as it does now. It also gives up
the zero-retarget bind, which is what the 4-up take demonstrates.

### Rejected for now — author a MetaHuman-specific foot IK

An IK Rig / Control Rig on `thigh/calf/foot` with no `ik_foot` dependency is the *correct* long-term
answer, and delivers **nothing visible on a flat floor**. Revisit only if the roster ever goes on
the foot-IK ramp.

---

## 7. The metric gap — the more valuable finding

`Scripts/metahuman_bind.py` sets `PROBE_BONE = 'hand_r'`, `PROBE_MIN_Z_RANGE_CM = 0.5`, and pairs it with a
state-machine read. Its own header argues at length about not tuning the threshold and about needing two
independent halves. Both halves are honest. **Neither can fail on this defect.** A rig whose legs are
pinned and whose arms swing passes both, by 45x.

This is a second instance of the exact class `Pie.FootIKAlphaGates` was demoted for — a
structure-passes-function-doesn't guard, this time inside the verification layer itself.

**The rule this earns:** *the clip-varies probe must sample the bone the caption is about.* `hand_r`
proves "the bind animates." Only a foot bone proves "the rig jogs."

Concretely, for `probe()`:

- Keep `hand_r` — it correctly proves the bind resolves and evaluates.
- **Add `foot_l` and `foot_r` world-Z range**, thresholded from the mannequins' own measured swing at
  Speed 600 (Manny's swing foot clears well over 15 cm; a 10 cm threshold separates stride from planted
  with ~2.5x margin — fix it before the run, as the existing header insists).
- **Strongest single test:** swing clearance — over the take, does `min(foot_l.z, foot_r.z)` ever rise
  meaningfully above the ground plane? A planted rig answers no, permanently.
- Report per-bone, per-rig, so a rig can fail on legs while passing on arms instead of collapsing to one
  `ANIMATES: true`.

---

## 8. The honest caption

**Shipped today** (`Plugins/MotionTerminal/Tests/shots_manifest.json`):

> "4 rigs jogging, one Template family - the MetaHuman runs it with zero retarget. Foot IK stays
> mannequin-only."

Two faults. "4 rigs jogging" is false — three jog. And "Foot IK stays mannequin-only" reads as a benign
absence when it is in fact the active cause of the fourth rig's failure; the caption unknowingly names its
own defect.

**If the existing frames ship unfixed**, short version:

> "4 rigs, one Template. 3 jog — the MetaHuman's feet are pinned by a mannequin-only foot-IK rig solving
> to bones its skeleton doesn't have."

Longer form for the evidence file:

> The MetaHuman body binds to the shared graph with zero retarget and plays the jog from the pelvis up.
> Its feet don't move: the Template's foot-IK Control Rig runs on it at full weight and solves to
> `ik_foot_l`/`ik_foot_r`, which `metahuman_base_skel` does not have, so the effectors are static and the
> solver plants the feet every frame. Caught by a foot-bone probe after a hand-bone probe read green at
> 45x threshold.

**If §6's recommended fix lands:**

> "4 rigs jogging, one Template. The shared graph skips its mannequin foot-IK pass on a skeleton that has
> no mannequin IK rig."

### Files carrying statements now known false

| file | statement |
|---|---|
| `Plugins/MotionTerminal/Tests/shots_manifest.json` | `caption_text` — "4 rigs jogging" |
| `Plugins/MotionTerminal/Tests/render_evidence.json` | `roster_4up._claim` — that the 4-up jog take shows FOUR rigs JOGGING |
| `Plugins/MotionTerminal/Tests/render_evidence.json` | `roster_4up._scope_limit_foot_ik` — "so it cannot reach this rig … that absence, not a preference" |
| `Plugins/MotionTerminal/Tests/render_evidence.json` | `roster_4up._what_the_shipped_frames_do_NOT_show` (b) — "cannot reach the MetaHuman at all" |

The `{"claim": "4 rigs", keys: ["roster_4up.rigs_in_frame"]}` citation itself stays valid either way — four
rigs are in frame. It is the prose around it that over-claims.

---

## 9. One-paragraph summary

The 4-up jog take's MetaHuman does not stride because `ABP_MT_Locomotion`'s `CR_Mannequin_FootIK` Control
Rig pass — alpha-gated on `GroundedFlag`, therefore at full weight for the whole grounded take — solves the
deform feet onto `ik_foot_l`/`ik_foot_r`, two of the nine bones `metahuman_base_skel` lacks; those
effectors never move, so the feet are planted every frame while the correctly-remapped jog plays above the
pelvis. The bind, the remap and the graph are all working. It is fixable inside the constraints by gating the Control Rig node on `GetBoneIndex("ik_foot_root") != -1`, which changes nothing
for the three rigs that have that bone. The costlier lesson is the probe: `hand_r` world-Z variance and a
`Locomotion` state read are both true of a rig with frozen legs, so the guard that was written to prevent
exactly a "structure passes, function doesn't" claim was itself one.
