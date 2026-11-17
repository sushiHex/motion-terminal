# Dynamics behaviours: reusable settings for parts, and skinning-aware parts

Date: 2026-10-28. Follows `2026-10-28-dynamics-group-modifiers-design.md`.

## Problem

On the group-flutter build, the necklace defies gravity and hugs the chest. Two things have to be established:
whether the mesh allows the necklace to move separately from the chestpiece, and how to turn these per-group
settings into generalised settings that can be reused on other characters and parts.

## Findings (measured, Khaimera LOD0, read-only probes)

- **The pendant is free geometry; the cord is not.** `necklace_center_02` dominates 872 vertices; 652 are bound to it
  alone, including four separate mesh islands (575 vertices, 0 welds to other islands). The other 663
  necklace-dominant vertices are part of the 7,795-vertex body surface (one welded island with spine_03, head,
  pelvis), joined along a 72-vertex seam.
- **The side pieces are painted on.** `necklace_l` / `necklace_r`: 121 dominant vertices each, 0 bound to them alone;
  every one is shared with spine_03. Moving them only stretches the chest.
- **Every feather is 100% rigid** (all 33); `back_moon` (no preset, not simulated) 48%.
- **The pendant's tip was 3.1 cm** because the fit took the centroid and reach of every dominated vertex, cord
  included. Its free geometry hangs 10.5 cm below the bone (p95 reach 8.1 cm).
- **The strap preset had no tuning**: the rig's global 1 Hz spring held it to the animated pose on the chest.

## Design

### 1. Behaviours: a reusable settings library (`Scripts/dynamics_behaviours.json`, `Scripts/dyn_behaviours.py`)

A behaviour is a complete, named setting: native particle properties and optional modifiers (`flutter`). The library
names no character: each entry records where and how it was tuned (`tuned`).

| Behaviour | Native | Modifier | From |
|---|---|---|---|
| default | (rig defaults) | | |
| cloth_panel | angle limit 30° / 6 Hz | | Khaimera cloth |
| feather | no gravity, 2.5 Hz / 0.15, limit 45° / 3 Hz | flutter rate 6, gain 5 | Khaimera hood |
| hanging_cord | 0.25 Hz, no limit, damping 4.5 | | Khaimera axe cords |
| pendant | (swept, see §Proof plan) | | Khaimera necklace |

`assign` maps a part to a behaviour by its profile facts, most specific first: `<preset>.<kind>`, `<preset>`, `*`. A
hero profile override `secondary.behaviours.<part id>` (a profile override, counted and attributed with its `by` and `why`) picks
another behaviour for one part. A new hero gets every behaviour its presets and kinds match with no code change; a
preset no rule names falls to `default`.

The switch is pinned by a test (`test_dyn_behaviours.py`): every part the old per-preset table covered resolves to
exactly its old values.

Flutter groups are behaviours with a `flutter` entry; their rig inputs are `<Behaviour><Input>`
(`FeatherFlutterRate`). A library behaviour no part uses adds nothing to the rig.

Experiments override the library per build (`KDYN_BEHAVIOURS_JSON`), never the file; the gate refuses an override
build (`EXPERIMENT_KEYS = ('behaviours', 'air')`).

### 2. Skinning facts in the intake fit (`hero_intake_fit.py`, `collider_autofit.py`)

- A single-bone flap's tip is fitted to its **free** vertices (all weight on the flap bone and its descendants) when
  they are the majority of what it dominates, else to its dominant vertices as before. Feathers are 100% free, so their
  tips are unchanged; the pendant's becomes 8.5 cm.
- A flap with under `PAINTED_SHARE` (10%) of its dominated vertices free is **painted onto its host** and is dropped
  from simulation with a reason, as a flap with no tip already is.

### 3. Groups are behaviours

The intake resolves each part's behaviour and writes it into the profile. Flaps of one behaviour share a spawner (one
ParticleProperties), their colliders' union, their smallest particle radius and their rest copies.

## Design decisions

Adopted (the design above is as built with them):
- **Free, not rigid.** A vertex is the flap's own when the flap bone AND ITS DESCENDANTS carry all of its weight
  (`collider_autofit.flap_vertex_sets`): a feather split 0.6/0.4 with its own twist helper was "painted" under the first
  rule. The fit records `skin: {dominant, free}`.
- **Free geometry steers the tip only as a majority.** Reproduced: a 10% free ornament on a flap whose hanging mass is
  blended turned a 20 cm downward tip into a 2 cm sideways one. `FREE_MAJORITY = 0.5`; below it the old rule stands.
- **Behaviours are resolved at intake** (`collider_autofit.build`) and written into the profile
  (`colliders.chains[*].behaviour`). Flaps group by behaviour, not preset, for their union, radius and rest copies
  (`rest_flaps_<behaviour>_<shape>`), so one group's tip no longer resizes another group's collider. The assignment is
  in the profile digest the gate checks, and the rig reads it from the profile instead of resolving again.
- **Experiments override values only**: `KDYN_BEHAVIOURS_JSON` merges into behaviour entries; assignment changes go
  through the profile (a profile override `secondary.behaviours.<id>`) and a re-run intake.
- **The profile-override path is fixed**: any `secondary.*` override other than `secondary.parts` no longer suppresses the
  derived part list (`hero_intake_core.profile`).
- **Native fields are validated** by name and type (`dyn_behaviours.NATIVE`); `Radius` is refused (the collision fit
  sizes particles, and the clearance analysis reads that radius). Every native pin is read back after authoring.
- **`dyn_compare`** groups by each record's own profile snapshot, counts flutter, hang and settle in `moved` and
  `max_d`, counts a value appearing or disappearing as a change, reports missing parts, wraps angle differences, and
  refuses a record whose pawns are not ON then OFF.
- **`hang()`** takes the forward axis from measured travel (no mesh convention), averages angles circularly, skips
  samples lying across the plane, and reports the unsigned 3D inclination beside the signed angle.
- **`dyn_sweep`** validates every candidate first, restores the shipped rig in a `finally`, and reports a failed
  restore in its result and exit code.

Not adopted, recorded:
- The `assign` rules are starting points: `cloth.flap -> feather` and `strap.flap -> pendant` will be wrong for some
  future part. That is what the per-part profile pick is for; each new hero is checked against the library before its
  visual tuning pass.
- Painted CHAINS are not detected (the fit measures skin per flap only). No such chain has been seen.
- A hero with no classified parts gets no simulation: the intake decides what is simulated, behaviours only tune it.
- A pick for a part the intake dropped is an error, by design: the drop's reason says why it cannot move.
- The engine's chain search includes every descendant of a root (RigDynamicsExecutionHelpers.cpp:173), beyond the
  profile's bone list: pre-existing, unchanged.

## Second-pass decisions

A second check confirmed every adopted fix above except two tools, and rebuilt the profile in memory from the saved measurements
exactly (33 feathers, 6 cloth panels, 2 cords, 1 pendant, 8.48 cm pendant tip). Adopted:
- `dyn_compare` wraps angle measures across runs (179.99 vs -179.99 is 0.02, not 359.98), counts a value that
  appears or disappears as an infinite change in `max_d` (not 0.000), and refuses a record without its own
  parts/colliders snapshot instead of measuring it against today's colliders.
- `dyn_sweep` validates every candidate's behaviour values (names, types, Radius) before building anything, and a
  restore that raises is still recorded in the result.
- The gate checks the build's sources (`identity sources`): every script and the library hashed into the manifest
  must match the file on disk, so a library edited without a rebuild fails.

Recorded, not acted on: the profile is read twice per build (by the author and by the manifest writer), so an edit
between them could label a rig with a later profile; the base spawner defaults (Strength, Radius, Gravity) are not read
back, only the behaviour's own fields.

## Known consequences

- Before this change all flaps shared one rest copy of each body collider, shrunk to the closest flap. The necklace's
  side pieces had set the chest copy; the feathers' own copy (`rest_flaps_feather_torso_spine_03`) is 0.37 cm larger
  (19.82 -> 20.19 cm), which moves the feathers' reported clearance by exactly that and their trail by 0.1-0.4 deg.
  Not a behaviour change; the gate is rebaselined with it. From here on a group's copies are its own.

## Proof plan

1. Unit: behaviour resolution, the old-table parity, painted-flap drop, spawner split (pytest).
2. Profile: the re-run intake changes only the necklace entries and the shared rest copies (diffed).
3. Gate against the group-flutter baseline: only the necklace and the rest-copy effect on the feathers may differ; every other
   group's max change 0.000 (`dyn_compare.py`).
4. Pendant sweep (`dyn_sweep.py`): hang from straight down standing and running (ON vs OFF), clearance, settle.
5. Rebaseline; repeat PASS; `--nostep` FAIL; close render of the chest for visual inspection.
