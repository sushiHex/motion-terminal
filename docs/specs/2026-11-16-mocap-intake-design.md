# Body-mocap intake: design

Status: design only, revision 2 of this draft. Nothing here is built: no ASF/AMC fixture, reader or mocap module
exists in the tree. This revision cuts the first slice to five stages, gives acceptance explicit decision states,
and removes or marks unresolved every claim the code does not support (section 11). It extends the asset pipeline
(`docs/specs/2026-11-12-asset-pipeline-design.md`) and onboarding (`docs/specs/2026-10-29-hero-onboarding-design.md`).

## 1. Problem

Onboarding derives a hero's clips from one template (`Scripts/hero_onboard_core.py:94`) and retargets them from one
source rig (`Scripts/dyn_hero.py:30`). Production motion does not arrive that way. It comes from a vendor or a
library, on a skeleton nobody chose, in units and axes nobody wrote down, with feet that slide. Someone has to
decide whether to accept it and, if not, tell the vendor what is wrong and whose fault it probably is.

## 2. Scope

**First slice.** One delivery end to end: receive, convert and import, retarget onto two bodies, evaluate and
review, reject with evidence, take a resubmission, decide again. One CMU subject skeleton, one flat-ground walk with
clear planted contacts, two revisions. Delivered motion is preserved: no root extraction, no automatic cleanup. A
green result means the motion was measured on the target body and a person looked at it.

**Deferred** (kept in section 12): root extraction, the cleanup op stack and its damage metrics, `foot_lock`, stairs,
impacts, hand contact, asymmetry, jitter and pops, the 8-12-take oracle program, a second reader, MetaHuman
markerless capture, generative motion, Blender action layers.

**Non-goals.** No hero goes through Blender. No marker solving: the source is solved joint angles and nothing here
claims a re-solve. No face, fingers or props. No licence decision: the source is a parameter, its licence a gate.

## 3. Sources

| Source | Status | Notes |
|---|---|---|
| CMU Graphics Lab, ASF/AMC | first slice | The FAQ allows copying, modifying and redistributing; no licence is named (`research/mocap-genai-metahuman-sources-2026-11.md:7`). Converted locally, never from third-party BVH copies. |
| MetaHuman markerless | later | Engine-native sequence on the MetaHuman skeleton; Windows-only, experimental. |
| Generative text-to-motion | later, seam only | No model with permissive terms on code and weights (`research/mocap-genai-metahuman-sources-2026-11.md:17-27`). |
| AMASS, raw Mixamo, cgspeed BVH | refused | Not shown to permit redistribution in a public tree (`research/mocap-genai-metahuman-sources-2026-11.md:10-15`). |

**Frame-rate provenance is unresolved.** The research note gives 120 Hz for the CMU marker files (.c3d), not for
AMC (`research/mocap-genai-metahuman-sources-2026-11.md:8`), and AMC carries no rate. The rate is a declared fact
in the library record with the page it came from; nothing claims to recover it from frame counts. Until a CMU page
stating the AMC rate is opened and recorded, `fps_source` is 120 with `provenance: unverified`, carried onto every
later record. No code branches on the library name, as onboarding names no rig (`Scripts/hero_onboard.py:14`).

## 4. Architecture

**Hosts.** Host Python (stdlib, pytest): manifests, hashing, the licence gate, the reader and converter, contact
labels, metrics, decisions, the vendor brief. Blender headless, version pinned: canonical tracks to FBX with a proxy
mesh (one rigid capsule per bone, never shown or retargeted); it never opens a hero, nothing edited there is read
back. Unreal editor (MCP, `editor_session`): import, scale gate, IK rigs, retargeters, bake, sampling, renders.

### Five stages

One `stage_runner` run per revision, recorded at `Saved/MotionTerminalTests/mocap/<batch>/r<revision>/record.json`.
Every artifact path carries `r<revision>`; nothing a later revision makes overwrites an earlier one's evidence.

```
1 receive    host           licence gate, hash every delivered file, validate the manifest, freeze the revision
2 convert    host+Blender   ASF/AMC -> canonical -> FBX; editor: import, boundary checks, scale baseline/gate
3 retarget   editor         source IK rig + one retargeter per body (realize), explicit retarget poses, guarded bake
4 evaluate   editor+host    sample each body, labelled contacts, ground/slide, sampler parity, animator review
5 feedback   host           vendor brief on rejection, or resolution of the prior revision's issues; the decision
```

**A rejected clip is a successful stage.** `stage_runner` stops at the first stage that raises
(`Scripts/stage_runner.py:236`). `evaluate` returns a record whose decision may be `rejected`, and `feedback` runs on
it. Only infrastructure faults raise: a missing file, an editor error, a readback that differs from its spec.

**Keys and freshness.** A key covers only declared `inputs` and the keys of `needs` (`Scripts/stage_runner.py:127-129`);
the runner neither hashes outputs nor checks they exist, and keys do not cover asset contents or the engine build
(`docs/specs/2026-11-12-asset-pipeline-design.md:70-72`). So `convert` and `retarget` key on source hashes, script
digests, Blender version, engine build id, the basis matrix and the skeleton map. `evaluate` carries a
per-invocation id, as onboarding's verify does (`Scripts/hero_onboard.py:660-663`), so it re-samples on every call,
and records the hashes of the mesh, skeleton, retargeter description, baked packages and sampler settings it read.
`--resume` never keeps an evaluation. Every stage validates the artifacts it reads (present, schema, producer hash).

### Reuse, as it actually is

- `retarget_chains` (`Scripts/hero_onboard_core.py:45-62`), `ik_rig_spec` and `retargeter_spec` (`:209`, `:344`)
  with the CMU source rig passed in. Neither spec has a retarget-pose or auto-align field, and
  `retargeter_describe` reads neither back (`Scripts/hero_onboard_editor.py:251`). Section 7 adds both.
- `realize` (`Scripts/asset_pipeline.py:101-124`) with the existing `ik_rig` and `retargeter` kinds
  (`Scripts/hero_kinds.py:54`, `:64`). Imported and baked assets are not generated assets and get their own
  lifecycle (section 6).
- The bake action calls `run_batch_retarget_with_results` (`Scripts/hero_onboard_editor.py:329`) and takes its
  folder and prefix from the hero context (`:325-326`). The intake passes its own context, so outputs go to
  `/Game/MT/Mocap/<batch>/r<revision>/` (git-ignored by `Content/MT/*/`), never into onboarding's folders.
- `source_root_motion` (`Scripts/hero_onboard_core.py:364-379`) falls back to bone 0 of the source preview mesh, so
  no extra `root` bone is needed to initialise the Root Motion op; the CMU `root` segment is bone 0.
- Onboarding's preflight (`Scripts/hero_onboard.py:405-447`) checks the template's source rig and clips. It says
  nothing about a CMU source or a body's contact landmarks.
- The vary check (`Scripts/hero_onboard_editor.py:365`, constants `:45-46`) only requires a target's rotation range
  to reach 25% of the source's where the source swings over 1 degree. It stays as a static-bake guard and claims
  nothing about alignment, timing or direction (`docs/specs/2026-11-12-asset-pipeline-design.md:79-80`).

## 5. Data contracts

JSON, sorted keys. Raw takes, canonical tracks, FBX and samples live under `Saved/` or `captures/` (git-ignored,
`.gitignore:5`, `:29`). The tree tracks only motion-free manifests, contact labels, bands, licence records, briefs.

**Licence record.** `Plugins/MotionTerminal/Tests/Mocap/licenses/<library>.json`: `terms_url`, `terms_sha256` of
the text as read, `permits` (`redistribute_raw`, `derivatives`, `public_display`, `commercial`: `true|false|null`),
`attribution`, `checked_by`, `checked_on`, `status`, and `facts` (such as the frame rate), each with its URL. Stage 1
refuses unless `status` is `checked` and every permit `intended_use` needs is `true`. The gate never fetches pages.

**Delivery manifest**, one per revision:

```json
{"batch": "B001", "revision": 1, "supersedes": null, "exchange": "simulated",
 "source": {"kind": "library", "library": "<id>", "subject": "<id>"},
 "intended_use": ["public_display", "derivatives"], "skeleton_map": "maps/<library>.json",
 "clips": [{"id": "walk", "format": "asf_amc", "files": {"asf": "...", "amc": "..."}, "sha256": {...},
            "fps_source": 120, "source_frames": [1, 600], "brief": "walk 3 m on flat ground"}],
 "disclosed_defects": [{"clip": "walk", "kind": "foot_slide", "foot": "l", "source_frames": [212, 260],
                        "size_cm": 5.0, "seed": 7}]}
```

`format` other than `asf_amc` is refused as `unsupported`. `disclosed_defects` is never read by the metrics.

**Skeleton map.** Source bone to body role, explicit and authoritative. `classify_roles`
(`Scripts/hero_intake_core.py:108`) is a name-token heuristic that misses names like `lfemur`; a note only.

**Canonical tracks** (stage 2), one stated space:

```json
{"schema": 2, "units": "cm", "space": {"handedness": "left", "up": "+Z", "forward": "+X", "right": "+Y"},
 "basis": {"B": [[...], [...], [...]], "det": -1, "scale_cm_per_unit": 5.644444},
 "fps": 120, "fps_provenance": "unverified",
 "bones": [{"name": "root", "parent": null, "rest_offset": [0, 0, 0], "rest_rotation": [w, x, y, z]}, ...],
 "frames": {"source_frame": [1, 2, ...], "t_s": [0.0, ...], "pelvis_t": [[x, y, z], ...],
            "local_q": {"<bone>": [[w, x, y, z], ...]}},
 "provenance": {"source_sha256": {...}, "converter": "<digest>", "asf_units": {"length": 0.45, "angle": "deg"}}}
```

- Every bone has a rest offset and rest rotation in its parent's frame; the pelvis (CMU `root`) has an animated
  translation `pelvis_t`; every bone has an animated local rotation.
- Positions: `p_UE = s B p_src`. Rotations: `R_UE = B R_src B^-1`, computed as matrices, then made quaternions.
  `B` is a signed permutation. With `det(B) = -1` (right-handed source into Unreal's left-handed frame, per Epic's
  coordinate-system page) no single rotation of the skeleton does the conversion. `B` is chosen once from a real
  frame checked against the external viewer (section 6) and is a keyed input.
- Quaternions are normalised and sign-continuous. Any non-finite number refuses the file.
- **Time.** `source_frame` is the AMC frame number; `t_s = (source_frame - first) / fps`; a take of N samples lasts
  `(N - 1) / fps`. A trim or resample keeps a (possibly fractional) `source_frame` for every output sample, so a
  brief always names source frames.
- **Blender hop.** The exporter applies its own declared canonical-to-Blender matrix; the FBX axis settings are fixed
  and keyed. No setting is trusted: the import is checked (section 6).

**Contact labels.** `Plugins/MotionTerminal/Tests/Mocap/labels/<batch>/<clip>.json`, made by a person before the take
is measured, in source frames: `{"foot": "l", "landmark": "heel|ball|both", "source_frames": [a, b]}`, plus
`airborne` windows. Every delivered take is labelled by hand; there is no automatic contact detector.

**Body landmarks.** Per body, `heel` and `ball` points per foot in foot-bone-local coordinates, measured once on the
mesh's LOD0 in its reference pose (heel: lowest vertex dominantly skinned to the foot bone in the rear of the foot;
ball: lowest vertex dominantly skinned to the ball bone), confirmed by a person on a contact sheet, recorded with the
mesh hash. They move with the animated foot.

**Sample** (per body, clip, revision). The mesh component sits at identity on a flat ground `z = 0`, so component
space equals world. Per sample: `t_s`, the mapped `source_frame`, the target frame, and translation plus rotation of
root, pelvis, every chain's start and end, thighs, calves, feet and balls; plus each landmark's position and its
**surface clearance** above the ground. Bone heights are diagnostics, never the contact measure.

**Evaluation row.** `{measurement, body, clip, foot, landmark, window_source_frames, coverage, measured, band,
band_digest, state, fault_owner, evidence}`, plus the review row and the decision per body.

## 6. Convert and import

### ASF/AMC semantics

The format description these rules come from (a University of Wisconsin course page on ASF/AMC) has not been opened
in this revision; every rule is pinned by fixtures and the external viewer before it is trusted.

| Item | Rule |
|---|---|
| `:units` | Only `length 0.45` and `angle deg` are supported; anything else is `unsupported`. `s = 2.54 / 0.45 = 5.644444` cm per unit (`research/mocap-genai-metahuman-sources-2026-11.md:9`), applied exactly once, to segment offsets and root translations. |
| Bone `axis` vs `dof` | `axis a1 a2 a3 ORDER` is the static basis C, built in its Euler order. `dof` lists which AMC channels exist and the order they compose into the motion rotation M. Not interchangeable. |
| Root `axis` vs `order` | Root `axis` is the Euler order of the static `orientation`; root `order` names the AMC channels. Nonzero root `position` and `orientation` are kept. |
| Segment vs joint offsets | A segment's `direction` x `length` is the offset from its joint to its children's. A child's rest offset is its parent's segment vector. |
| Composition | The source states the local transform for row vectors as `L = Cinv M C B` (B the offset). The converter uses column vectors, so it uses the transposed form, derived once in its header and pinned by a fixture; never transliterated. |
| Missing or odd input | No `dof`: fixed at rest. `l` channels, bones missing between ASF and AMC, duplicate names, a broken hierarchy: refused with the reason, `unsupported`. |

### Proving the conversion: one reader, independent references

Two readers written from one ambiguous draft can share one mistake, so independence comes from the references:

1. **Analytical fixtures**, expected transforms worked out by hand: a non-XYZ `axis` order with nonzero,
   non-commuting multi-axis angles; nonzero static root `position` and `orientation`; DOFs in a reordered subset; a
   fixed bone; an asymmetric hierarchy. On every fixture the swapped composition order must give a different result;
   a fixture where it does not is rejected as non-discriminating.
2. **A pinned external viewer.** CMU's tools page lists ASF/AMC viewers with source. One is to be evaluated, pinned
   by version and hash, and used to read joint world positions on chosen frames of the real take. Until it is, the
   real take's conversion is `unmeasurable`.
3. **Signed basis fixtures.** With only the left arm raised, the left hand lands on the side `B` calls left; a
   positive turn about up turns the subject the way the viewer shows. A mirrored `B` fails both.
4. **UE boundary checks** (editor): bone local transforms and landmark-bone component positions on chosen frames
   against canonical, to 1e-2 cm. This catches the Blender and Interchange hops, not a converter error: Unreal
   imports a wrong FBX faithfully. That is what 1 to 3 are for.

Plausibility checks (human height range, head above pelvis, facing along travel, a percentile floor) are warnings
only: they reject crouches, rolls and backward walks, a mirrored walk passes them all, and they decide nothing.

### Scale gate bootstrapping

`AssertImportScale` needs a per-rig baseline and errors without one
(`Plugins/MotionTerminal/Source/MotionTerminal/Private/AnimImportTools.cpp:34-40`, `:175-185`). A new CMU skeleton
has none, and snapshotting its first unchecked import would compare the import with itself. So:

1. Import at offset 1.0; read back every bone's local length and the component-space rest positions.
2. Compare with canonical rest offsets to 1e-2 cm, and the rest bounds with the canonical extent. Bone-local lengths
   cannot see an inherited scale on bone 0, so component positions are compared too.
3. Only if both agree, run `SnapshotScaleBaseline` and record the comparison as its evidence. Later imports run
   `AssertImportScale` against it.
4. The root-only bypass (`ImportScaleCheck.cpp:434`) is narrow and baseline-dependent and is not relied on.

**Lifecycle.** Imported mesh, skeleton and sequence, and baked sequences, live under
`/Game/MT/Mocap/<batch>/r<revision>/`. Each import deletes its target, imports, reads back, and leaves nothing on
failure (realize's order). Load and delete happen in separate calls (the known silent-keep failure).

## 7. Retarget

Per body: an IK rig and a retargeter through `realize`, then the guarded bake into the intake folder. Bodies: Aurora
(`height_cm` 181.2, `HeroProfiles/ParagonAurora.json:2032`) and Grux (222.0, `HeroProfiles/ParagonGrux.json:633`).
Neither is assumed to have passed onboarding on the current engine; that is checked before the slice.

**Explicit retarget poses.** The CMU rest pose and the Paragon reference pose differ (roughly T versus A). The
retargeter spec gains a `retarget_pose` per side: the reference pose, named per-bone rotation offsets, or an
auto-align call with its settings. The build sets it, `retargeter_describe` reads back the current pose and its
per-bone offsets, `retargeter_mismatch` compares. The 5.9 API for this is unread; it is read from engine source
before building, and a pose that cannot be read back refuses the build.

**Target-pose check.** A one-frame clip holding the source retarget pose is baked onto each body. Per chain, the
start-to-end direction on the target must match the source's within the band in `thresholds.json`. This checks
alignment directly, which neither the vary check nor a raw-versus-clean comparison can.

## 8. Evaluate and review

### Decision states

| State | Meaning |
|---|---|
| `accepted` | Every required row in band with full coverage, and the animator review passed. |
| `rejected` | A row out of band, or the animator rejected it. A review rejection with passing metrics stands. |
| `unmeasurable` | A required row could not be made: coverage below 1.0, missing bone, NaN, sampler disagreement, viewer not pinned. Never zero error, never a pass. |
| `unsupported` | Input or motion outside this slice: format, `l` channels, stairs, uneven ground. |
| `waived` | Out of band and accepted by a named person with a reason; stays recorded as a known failure. |

A body's decision is its worst row; `waived` counts as accepted for sign-off only and stays visible. Sign-off needs
`accepted` or `waived` on both bodies and a passed animator review on both.

### Measurements

Bands live in `thresholds.json`, committed before revision 1 is evaluated, with their digest in every row. They are
provisional, owner-chosen bands, not tuned values.

- **Coverage.** Per labelled contact window, the share of its frames (through the time mapping) with a valid
  landmark sample. Below 1.0 the window's rows are `unmeasurable`.
- **Slide.** Per window, for the labelled landmark: maximum horizontal distance from its position at the window's
  first frame (banded), plus path length, duration and peak speed (reported). A `heel` window followed by a `ball`
  window is two windows, so heel-to-toe roll is not slide.
- **Ground.** Per window: minimum and maximum surface clearance of the labelled landmark, both banded. `airborne`
  windows are reported, not banded.
- **Source and target.** Slide and ground are measured on the canonical source (landmark approximation stated) and
  on each body, so a body-only failure is not sent to the vendor.
- **Sampler parity.** `get_bone_pose_for_frame` applies bone translation retargeting
  (`docs/verification-design.md:159`). On each body, on chosen frames, its landmark positions are compared with a
  posed component's readback; disagreement makes that body's rows `unmeasurable`. One body's result never chooses
  the sampler for the other.
- **Static-bake guard.** The vary check, as an infrastructure check.

**Fault ownership.** Every failing or unmeasurable row is tagged `source`, `conversion`, `retarget`,
`body_limitation`, `runtime` or `uncertain`: failing on the source too, `source`; passing on the source but failing
on both bodies, `conversion` or `retarget` (the boundary checks decide); failing on one body only,
`body_limitation` or `retarget`; otherwise `uncertain`. Only `source` rows become vendor issues. Any cause beyond
what was measured is written as a labelled hypothesis.

### Animator review (mandatory)

Before/after renders and a contact sheet per body (both revisions side by side on revision 2), and a review row per
body: pass or fail with a reason. A fail blocks sign-off whatever the metrics say and becomes a review issue.
Captions cite the evaluation record. `Scripts/shot_check.py` binds JSON citations, but `Scripts/review_page.py` reads
only its two fixed evidence files (`Scripts/review_page.py:31-32`, `:57`); intake records need an adapter.

### Playback contract

Accepted means accepted for **display only**: played as-is on a mesh in a level sequence or a review render, root as
delivered, no capsule, no root-motion consumption, no blending, no post-process IK. Nothing is claimed for gameplay,
deformation, LODs or compression.

## 9. Feedback and resubmission

On a rejected revision, stage 5 writes `feedback/<batch>-r<revision>.json` and a rendered page:

```json
{"batch": "B001", "revision": 1, "decision": "rejected", "exchange": "simulated",
 "issues": [{"id": "B001-r1-1", "clip": "walk", "kind": "metric", "measurement": "slide", "foot": "l",
             "landmark": "heel", "source_frames": [212, 260], "measured_cm": 5.1, "band_cm": [0, 1.5],
             "fault_owner": "source", "bodies": ["aurora", "grux"], "evidence": ["contact_sheet_walk_l.png"],
             "observed": "left heel drifts 5.1 cm forward during the plant", "hypothesis": null,
             "requested_action": "remove the drift of the left heel plant, source frames 212-260",
             "do_not_change": ["timing of every other contact", "the right foot"], "status": "open"}], "waived": []}
```

An issue without source frames, evidence, an observation, an action and `do_not_change` fails a host test.

**Resubmission route.** A new manifest (`revision + 1`, `supersedes`) with ASF/AMC files, the only supported format,
through the same five stages into its own `r<revision>` folders. Stage 5 marks each earlier issue `resolved` or
`still_open` with the new measurement, leaves the earlier record untouched, and records the approver's decision.

**The simulated exchange, labelled.** `exchange: "simulated"` is required in every manifest and brief and shown on
the review page and every caption showing the exchange.

Revision 1 is the CMU take with its own defects plus one disclosed injected defect (a left-heel slide of known size
and seed). Revision 2 is the original take's files, unmodified, labelled **controlled simulated correction**: not a
re-solve, never described as one, evaluated and reviewed for real, and it may itself be rejected.

## 10. `mt.py` and the test plan

**`mt.py`.** One row in `OPERATIONS` (`Scripts/mt.py:28-38`): `'mocap': ('mocap_intake.py', [], False, True)`. The
fourth field is metadata only; dispatch ignores it (`Scripts/mt.py:52`), so `mocap_intake.py` acquires the editor
lazily in the subcommands that need it: `ingest <manifest>` (editor for stages 2-4), `ingest <manifest> --stage
<name> --resume`, `status <batch>` and `feedback <batch>` (no editor). `/Game/...` arguments come from PowerShell
(`Scripts/mt.py:18-19`).

**Host tests** (pytest, the single relevant test first):

1. Reader and converter on the analytical fixtures: expected transforms; the order-swap mutation changes the result
   on every fixture; unsupported `:units`, `:angle` and channel cases are refused with their reason.
2. Basis: signed left/right and positive-turn fixtures, which a mirrored `B` fails. Time: `source_frame` survives
   trim and resample; duration is `(N - 1) / fps`.
3. Metrics: 2, 5 and 10 cm synthetic slides measured within 5%; a zero defect with full coverage reads 0; a missing
   sample reads `unmeasurable`, never 0; heel-then-ball windows read no slide.
4. Decisions: every state reachable; a review fail with passing metrics is `rejected`; a waiver stays visible; a
   rejected evaluation returns a record and does not raise.
5. Feedback: schema; only `source` rows become issues; resolution across revisions; revision 1's record unchanged.
6. Freshness: `evaluate`'s key changes every invocation; a changed retargeter description changes `retarget`'s.
   `test_mt.py`: the new row.

Blender rows run locally and are **skipped** without Blender; the real-take conversion row is local-only (raw motion
is never committed). A skip is recorded as `not run`, never as green, and every run lists which source, export and
editor checks actually executed.

**Editor rows**, for this take and both revisions. E1: sampler parity on both bodies. E2: boundary checks, baseline
bootstrap, then `AssertImportScale`; frame count and rate read back. E3: IK rigs and both retargeters through
`realize`, retarget poses read back, empty mismatch. E4: target-pose check and static-bake guard on both bodies.
E5: revision 1 `rejected` with the disclosed slide owned by `source`; revision 2 gets a real decision. E6: review
renders exist and their captions resolve.

## 11. Unresolved: not built, not opened, not measured

- No fixture, reader, converter or mocap module exists; the external viewer is not chosen.
- Not opened by this revision: the ASF/AMC format description, the CMU pages on frame rate and tools.
- Unmeasured: the 5.9 retarget-pose API; Interchange's import of a Blender FBX of a CMU skeleton; the sampler on
  Aurora and Grux. Unchecked: whether `SnapshotScaleBaseline`'s format holds what section 6 compares.

## 12. Later (deferred, kept so it is not lost)

**Root extraction** needs an animated pelvis translation (now in canonical) and a named consumer. To solve first:
hip-axis yaw is unstable when its horizontal projection is small and confuses twist with heading; a low-pass shifts
turns and smears starts and stops; stair height is ambiguous in double support; a jump kept in the pelvis is a
playback policy, not root motion; world-pose invariance proves neither the root trajectory nor its consumption.
Stairs stay `unsupported` until stair geometry exists. The existing in-place pin works on root and pelvis
(`Scripts/hero_onboard.py:219-224`); its bend refusal runs only above 30 cm net travel
(`Scripts/hero_onboard_editor.py:575-577`), with a separate excursion check at `:700-703`.

**Cleanup and `foot_lock`.** Pure ops on canonical tracks, raw kept. `foot_lock` needs an explicit `infeasible`
outcome (unreachable with a fixed pelvis and limb lengths, knee-pole flip, joint limit, ankle orientation) and
target-space residuals after retargeting; it never stretches or clamps silently. A toe pivot locks the ball only.

**Damage metrics**, each with the failure case it must answer before use:

| Metric | Failure case |
|---|---|
| accumulated slide | grows with contact length and noise; report displacement, duration, speed |
| jitter above 10 Hz | sees a narrow band at 30 Hz; second differences change with rate |
| robust-deviation pops | a bad clip inflates its own baseline; zero dispersion on near-constant clips |
| impact peak ratio | a spike can replace an impact; near-zero denominators; timing and direction undefined |
| silhouette deviation | sparse effectors miss elbow flips, wrist twist, penetration; equally bad raw and clean read 0 |
| hand contact | signed excess accepts intersecting hands; wrist distance is not palm contact |
| asymmetry kept | correlation undefined on constant signals; stride alignment can erase timing damage |
| airborne check | take-off and landing frames break a fixed margin; floating feet do not make a jump |

**Oracle program.** 8-12 takes, labelled corruptions, a hold-out split by original take before corrupting, never
re-tuned against. One defect may trip several metrics, so "no other metric fails" is not an assertion.

**Other seams.** A second reader, only if a disagreement with the viewer cannot otherwise be resolved. MetaHuman
markerless enters at import with its export settings recorded; its public-display licence is unconfirmed. Generated
motion: a `generation` manifest block (model, weights hash, both licences, training data, prompt, seed, sampler,
output hash), refused on any null licence, reproducibility `bitwise | within_eps | nondeterministic`, adherence
against declared assertions, not a learned judge. Blender action layers to mute one cleanup op; never read back.

## 13. Open questions for the project owner

1. **Licence.** Confirm CMU and sign its record. Public display and derivatives only, or commercial too?
2. **Frame rate.** Who confirms the AMC rate from a CMU page? Is 30 fps the bake's target rate?
3. **Bodies.** Run Aurora's and Grux's current-engine onboarding check first, or accept them as they stand?
4. **Bands.** Provisional slide, ground and target-pose bands for `thresholds.json`, before revision 1 is measured.
5. **People.** Who labels contacts and who reviews as animator? May it be the same person?
6. **Viewer.** Which CMU-listed viewer to pin, and is building it locally acceptable?
7. **Review page.** Extend `Scripts/review_page.py` with an adapter, or a separate intake page?
8. **Blender.** Which version to pin; Blender rows stay out of CI.
9. **After the slice.** Root extraction or MetaHuman markerless first?
