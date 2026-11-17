# Secondary motion with switchable backends — Design (draft v0.2)

Status: draft v0.2, not built. The source-checked constraints behind v0.2 are in §7.

## 1. Goal and requirements

Goal, following the feather audit (`research/ue58-rigging-tools-assessment.md`, section "Feather audit"): every
one of the solutions listed there should be implementable, and changing the dynamics system under the hood should be automatic and
easy with the toolset.

- R1 **All of the above**, eventually: Control Rig Dynamics (today), Control Rig Physics, AnimDynamics,
  RigidBody + physics asset, Chaos Cloth, material WPO; Deformer Graph as an additional candidate (§3.3).
- R2 **Automatic**: intake finds the parts and fills defaults without per-hero hand authoring.
- R3 **Switchable**: which backend simulates a part is a recorded setting, applied at **build time** (rebuild), not
  live during play (live switching is out of scope: velocity transfer and reset semantics differ per backend).
- Binding project rules: no stock/engine edits (duplicates under gitignored `Content/MT/<Hero>/`); assets rebuilt
  by tracked scripts; every claim measured; a gate must be proven able to fail; a missing measurement is
  FAIL or NOT VERIFIED, never PASS.

## 2. What exists today (checked against source)

- **Intake**: `hero_intake.py` writes `HeroProfiles/<Hero>.json` (built in `hero_intake_core.py:364-381`);
  `hero_intake_fit.py` writes intermediate fit data (chain tails, radii); `collider_autofit.py` builds colliders
  for `doc['chains']` only (`:345`). Single-bone flaps are listed but get **no tip and no colliders**
  (`hero_intake_fit.py:135` iterates `bones[1:]`). Weights are LOD0 only.
- **Intake exclusions that block wanted parts**: `weapon` is a deny token and anything under a hand is excluded
  (`hero_intake_core.py:191, 223-235`), so axe tails are not parts; bones under the head role are excluded unless
  tagged hair, so `feather_01/02` are missing (29 feather flaps listed, not 31).
- **One backend**: `dynamics_rig.py` (CR Dynamics rig: one solver, a spawner per `colliders.chains` entry,
  tails as Nulls, witness curves, `DynamicsAlpha`; air damping and reset thresholds are **solver-wide**);
  `dynamics_graph.py` (post-process ABP: Input -> grip -> CR -> Output, applied by the harness only).
- **Harness**: `pie_dynamics_drive.py` hard-codes chain bones/tips (`:84-97`); `dyn_analyze.py`
  derives chains by name patterns (`:262, :267`); `dyn_run.py` exits 0 whenever analysis exists — it is
  **not a gate** (`:71-73`); clearance measures bone centres and tail ends, not the cloth surface; the visibility
  script still uses the old spike visibility camera. No frame-cost measurement exists anywhere.
- **Audit tools** need a hand-made USD export; islands keep only three aggregate weights and no stable identity.
- **Not yet in the baseline** when this was written: tails, waist collider, grip layer, overlay tooling. The baseline must land before step 1 can
  claim "no behaviour change".

## 3. Design

### 3.1 Parts (intake, automatic)

| kind | definition | topology attribute | notes |
|---|---|---|---|
| `chain` | rooted bone subgraph skinned to visible mesh | `linear` or `branching` | branching (loin_cloth_bk_03 -> two feathers) must be declared, some backends only take linear |
| `flap` | one bone with weighted mesh and no chain child | `single` | needs an inferred tip/hinge per piece (attachment inference, §3.5) |
| `region` | mesh pieces whose deformation is owned by animated body bones | `mesh` | identified per **vertex** with full influences, not by a piece's top bone |

Every part records: stable id, bones or vertex set, per-LOD skinned vertex counts, preset, and a provenance hash
(mesh, skin, UV, topology) so overrides survive reimport or fail loudly when they cannot be remapped.

### 3.2 Description (profile) — native first, neutral later

`secondary.parts[]`: `{id, kind, topology, bones|vertices, preset, backend, native{<backend>: {...}}}`.
- Each backend's **native settings are stored losslessly**. Step 1 moves today's CR Dynamics values (strength,
  radius, gravity multiplier, solver-wide damping and thresholds, tails, exclusions) into `native.cr_dynamics`
  with no translation.
- The shared, backend-neutral contract is **measured response targets**, not physical constants: trail angle at
  run speed, stop swing, settle time, clearance. A backend is tuned to hit them; the numbers are compared across
  backends. (A neutral physical set {Hz, Hz, gravity, angle, radius} is not honest: damping has three distinct
  meanings in CR Dynamics alone, angle limits need a strength, cloth and WPO have no equivalents — §7.)
- Translators from one backend's native settings to another's are optional helpers, each declaring what it cannot
  map. `supports(part)` returns capabilities, unmet prerequisites and unsupported settings, never a bare bool.
- `backend` is set through the profile's existing override mechanism (with `by` and `why`,
  `hero_intake_core.py:314-331`); the profile is the single source of truth and every run record stamps the
  backend, the rig build identity and the engine/plugin versions.

### 3.3 Backends

| backend | parts | maturity (installed 5.8) | authoring path to probe first |
|---|---|---|---|
| `cr_dynamics` | chain, flap | Experimental | existing |
| `cr_physics` | chain, flap | Beta | Control Rig graph (as today) |
| `anim_dynamics` | linear chain, flap | engine | can the project's own toolset create the node and set its nested arrays? |
| `rigid_body` | chain, flap | engine | duplicate physics asset; `bFreezeIncomingPoseOnStart` must be false |
| `chaos_cloth` | region, chain-as-cloth | Chaos Cloth Asset (disabled by default) + Dataflow | Cloth Asset built from the **read-only** source mesh + Cloth Component on the pawn; the original pieces must be hidden or replaced (section-level visibility only) |
| `wpo` | region | engine | derived master material/function + instances (a material instance alone cannot add WPO); per-piece mask by UV region, only where UVs do not overlap; sway direction from the skinned tangent frame |
| `deformer` | region | Deformer Graph, Beta | per-component mesh deformer that reads skinning and writes positions; custom backend |

Interface extracted when backend 2 lands, from two real implementations (`author`, `report`, `remove`,
`supports`).

### 3.4 Composition and ownership

- The resolver computes each backend's **write set**, not just its declared bones: CR Dynamics re-aims the
  parent of each particle and expands descendants; a Control Rig node's default pose-adapter path copies its
  **whole local pose** back (`ControlRigHierarchyMappings.cpp:366`), so a downstream rig can overwrite upstream
  results; CR Physics propagates to children; RigidBody simulates whatever the physics asset marks simulated.
  Overlapping write sets are a build error.
- **Vertex ownership**: a vertex belongs to the part with its dominant influence over the full skin; a mesh backend
  may only displace vertices it owns (prevents WPO/cloth on top of bone motion). **Material ownership**: a mesh
  backend that works per material slot owns the whole slot unless it masks per vertex/UV; the resolver checks it.
- **Solver grouping** is a resolver output: solver-wide settings (damping, timestep, space, reset thresholds) mean
  parts with incompatible policies need separate solvers. Adding parts to a solver re-derives its thresholds and
  re-runs every gate on it.
- Hero rules (the grip layer) run before all backends and are declared, because they write the roots of
  hand-attached chains (axe tails).
- Collision between backends is not shared: each translates the profile's collider shapes into its own model,
  and declares what it cannot represent.
- Stock dynamics already in a hero's AnimBP (e.g. Revenant's AnimDynamics/RigidBody nodes) are
  detected at intake and either owned or excluded.

### 3.5 Intake upgrades

- Flaps get tips and colliders (autofit and the tail fitter extended past `doc['chains']`).
- Head-subtree feathers and hand-attached `weapon_tail` chains admitted by explicit rules (with the grip as a
  declared dependency).
- Islands computed **in-editor** (the Geometry Script path the fit already uses), with full influences, UVs,
  stable ids; no manual USD export.
- Attachment inference per piece: pinned root vertices and hinge/tip direction, with a confidence and a reject.

### 3.6 Verification

- The harness reads parts from the profile; no chain names in code. The runner exits non-zero on any failed or
  unverifiable gate.
- Each backend **declares which gates apply**; each applicable gate is proven red once per backend by a mutation
  (backend disabled, mask on the wrong region, roots unpinned, collider removed, amplitude exaggerated).
- Bone backends: responds (ON vs OFF above a floor), bounded (no explosion; backend-specific reset witnesses),
  clears colliders (relative, per frame), settles, and a **frame-cost gate (new)**.
- Mesh backends: deformed-geometry ON/OFF on a per-LOD vertex membership manifest (responds on tip samples, roots
  stay attached, non-owned samples unchanged, stretch/jump bounds); where geometry readback cannot see the
  displacement (WPO), the result is labelled **look-verified only**, never PASS.
- Composition test (from step 4): upstream motion injected into unrelated bones survives every downstream backend
  at alpha 0 / partial / 1, LOD exit/re-entry, teleport and hitch.

### 3.7 Build order (each step lands and is gated on its own)

0. **Baseline**: land the pending work (tails, waist, grip, overlay), commit the current measured numbers as a
   tracked baseline; harness reads parts from the profile; runner becomes a real gate; records stamped.
1. **Parts + `cr_dynamics` behind them**, the six chains migrated with native settings preserved; reproduces the
   baseline exactly.
2. **Hood feathers + back_moon** as flaps with tips (first new visible result); frame-cost gate lands here.
3. **Axe tails** (intake rule + grip dependency).
4. **Second bone backend on one chain**, chosen by an authoring probe of the project's own toolset; proves the switch and the
   composition test.
5. **In-editor regions + first mesh backend** (WPO or cloth, chosen by the UV-overlap and hide-original probes).
6. Remaining backends one at a time.

## 4. Open questions

- Q1 resolved: migrate the six chains in step 1, natively (§7).
- Q2 resolved: no neutral physical set; native settings + measured response targets (§3.2).
- Q3 partly resolved: coexistence is possible only with write-set ownership and an order rule; to be measured in
  step 4.
- Q4 open: cost of ~30 flaps on one solver — measured in step 2 (collider scans dominate, self-collision opt-in,
  one bad particle resets the whole solver).
- Q5 open: WPO masking depends on feather UV overlap (unmeasured); cloth needs the original pieces hidden.
- Q6 open: which mesh backend first.

## 5. Out of scope

Live (in-play) backend switching; shared collision/force exchange between solvers; army-scale LOD policy beyond
"dynamics off past LOD N" (to be set in step 2 from the per-LOD weights).

## 6. Risks

Experimental/Beta plugins; the project's own toolset may not author AnimDynamics/RigidBody/cloth nodes (probe before promising);
existing clearance gate margin 0.08 cm; gitignored Khaimera content means a missing asset must SKIP with reason.

## 7. Design rationale (v0.2)

The constraints that shaped v0.2, each load-bearing one checked in source:
- The intake blocks wanted parts (the weapon deny token, the under-hand and under-head exclusions)
  (`hero_intake_core.py:191, 223-235`), so §3.5 admits them by explicit rules.
- Flaps get no tips or colliders (`hero_intake_fit.py:135`, `collider_autofit.py:345`), so §3.5 extends the fit
  past `doc['chains']`.
- A neutral set of physical parameters would be dishonest: an AngleLimit at its default strength 0 is disabled
  (`RigDynamicsData.h:612-618`), and damping alone has three meanings in CR Dynamics. Hence native settings first,
  with measured response targets as the shared contract (§3.2).
- A Control Rig node copies its whole local pose back on the default path (`ControlRigHierarchyMappings.cpp:366`),
  so ownership is by computed write set, not declared bones (§3.4).
- Mesh backends need ownership per vertex and per material slot, not per bone, and solver grouping has to be a
  resolver output because damping, timestep, space and reset thresholds are solver-wide (§3.4).
- The current runner is not a gate, clearance is measured at bone centres only, there is no cost gate, and the
  visibility camera is stale (§2), so §3.6 and step 0 make the runner a gate and add the frame-cost gate.
- The Chaos Cloth Asset imports from a read-only mesh (`SkeletalMeshImportNode.h:26`), so the original pieces
  must be hidden; the RigidBody freeze flag exists (`AnimNode_RigidBody.h:308`) and must be false (§3.3).
- Step 1 stays small (no parameter layer, no backend interface until a second backend exists); step 0 lands a
  baseline; the harness is driven by parts; records are stamped; flaps come first and mesh backends last (§3.7).
- Every gate is proven red by a mutation, a missing measurement is FAIL or NOT VERIFIED and never PASS, parts carry
  stable ids and provenance, attachment is inferred per piece, Deformer Graph is a mesh option, and backends switch
  at build time only.

Not verified in source, carried as claims: UV overlap on the feather section; the project's own toolset's ability to author
AnimDynamics/RigidBody nodes; Revenant's stock dynamics nodes.
Deferred: live-switch transition tests (out of scope with build-time switching).
