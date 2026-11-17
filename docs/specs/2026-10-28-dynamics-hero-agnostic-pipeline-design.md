# Dynamics: one pipeline for any hero

Date: 2026-10-28. The simulation on the first hero
is tuned and gated; before a second character can use it, the tooling has to stop naming the first. Proving it on a
second hero (Aurora) follows in `2026-10-29-hero-onboarding-design.md`.

## Problem

The behaviour library, intake, analysis measures, gate and sweep/compare tools are already hero-agnostic. The eight
`khai_*` scripts that build, drive, gate and render the rig still name Khaimera:

| Script | Hard-coded |
|---|---|
| `dynamics_rig.py` | CR path, mesh path, profile path, solver component name, log tag |
| `dynamics_graph.py` | ABP path, CR class, skeleton, grip pose + hands, manifest, profile path |
| `pie_dynamics_drive.py` | test pawn class, in-place anim class, post-process ABP class, profile path |
| `dyn_run.py` | hero name for the baseline, driver path, record name |
| `dyn_analyze.py` / `dyn_overlay.py` | profile path fallback |
| `dyn_render.py` | driver path, output names |
| `archive/khai_dyn_visibility.py` | (one-off shot-framing visibility check, archived; no hero values) |

`SOLVER_SETTINGS` reset/teleport thresholds were measured on Khaimera's OFF arm.

## Design

1. **`Scripts/dyn_hero.py`** (pure Python, also importable in the editor) resolves everything hero-specific from the
   rig name, so a new hero needs no code edit:
   - name: `--hero <Rig>` on every CLI, passed to editor-side scripts as a global and to subprocesses as `MT_HERO`; no
     default (a missing hero is an error, not Khaimera).
   - from the profile (`HeroProfiles/<Rig>.json`): mesh, skeleton, parts, colliders.
   - by convention, `short` = the rig name without a `Paragon` prefix: `/Game/MT/<short>/CR_MT_<short>Dynamics`,
     `ABP_MT_<short>Dynamics`, `BP_MT_TestPawn_<short>`, `InPlace/ABP_MT_<short>_InPlace`, solver component
     `MT_<short>DynamicsSolver`. Khaimera's existing assets already follow it, so nothing is renamed in Content.
   - optional hand-set extras in `HeroProfiles/dynamics/<Rig>.json`, each with a `why`: `grip` (pose + hand roots,
     Khaimera's open-hand fix), and later `solver_settings` overrides once a hero's OFF arm is measured. Unknown keys
     are refused.
2. *(Superseded by the design decisions below: no rename.)* **Rename** `khai_*` to `dyn_*` (`dyn_author`, `dyn_abp`, `dyn_run`, `dyn_analyze`, `dyn_gate`, `dyn_render`,
   `dyn_overlay`, `pie_dyn_drive`, tests), log tags `KHAIDYN*` to `DYN*`. Records become
   `Saved/MotionTerminalTests/dyn_<short>_<tag>.json`, the manifest `dyn_<short>_build.json`; the baseline stays
   `Tests/DynamicsBaselines/<Rig>.json`. `dyn_sweep` / `dyn_compare` take `--hero`.
3. **The no-rig-name test** (`test_no_rig_name_in_generator_code`) extends from `hero_intake*.py` to every dynamics script, so
   a hero name can no longer creep back into the shared code (the per-hero config files are data, not code).
4. **Out of scope** (left to the onboarding design and the second hero, Aurora): creating a hero's test pawn and in-place ABP, calibrating its solver thresholds,
   a hair behaviour. A missing pawn/ABP is reported by name with what to create.

## Design decisions

Adopted (the design above is amended by these):
- **No rename.** Renaming is migration, not generalisation: it breaks imports, the sweep's subprocess paths, manifest
  source keys, log parsing, pytest ids and every historical record. The `khai_*` scripts keep their names and take
  `--hero`; records become `dyn_<Rig>_<tag>.json` and readers fall back to the pre-hero `khai_dyn_<tag>.json`.
- **Identity by the full rig id** (records, manifest); the asset namespace (`short`) is checked for collisions across
  every profile (`Aurora` and `ParagonAurora` would share a folder).
- **An execution contract:** the hero is consumed before any profile-derived constant, passed explicitly on every
  editor exec (`KDYN_HERO`, `KDD_CONFIG['hero']`; an environment variable does not reach the running editor), carried
  through every sweep build, drive and the restore, and drive summaries match on tag AND hero.
- **Skeleton names from the profile:** control bones and the leg checks use the profile's `roles`; the solver owner is
  the imported hierarchy's top bone; the record keeps a `roles` snapshot.
- **The leg check has explicit applicability:** a hero with nothing hanging from the pelvis records
  `legs_applicable: false`; the gate passes the legs row only when the baseline agrees, and a baseline needs legs only
  where they apply (reproduced: hair-only parts raised in `min([])`).
- **No-grip ABP branch** (Input Pose -> Control Rig); the drive preflights the pawn, in-place and dynamics classes and
  names what to create.
- **Identity proves the hero:** the manifest records hero, mesh, CR and ABP; a new `identity hero` row refuses a build
  made for another hero; the manifest hashes `dyn_hero.py` and the hero's extras file.
- **Framing scales with the profile height**, the record keeps its camera, and the overlay reads it.
- **The no-rig-name check reads executable code** (comments and docstrings dropped, embedded names caught), proven against the
  pre-change scripts; `TRAIL_DEG` (unused) removed.

Recorded, not acted on (for the second hero or later):
- Aurora's checked-in profile predates `secondary.parts` (re-run the intake); its two presetless flaps need a
  classification decision; hair falls to `default`.
- The drive's speed, timing and spawn height are universal defaults; a hero whose locomotion differs overrides them
  in `KDD_CONFIG`.
- The fixed 2 cm `responds` floor may be too high for very small parts.
- `_LEGACY_TIPS` and the archived visibility-check constants describe the first hero's history and stay as such.
- Editor access stays serialized across heroes (one sweep lock; the drive's callback handle and actor tags are global).

## Implementation decisions

All adopted:
- `identity hero` also requires the drive's post-process class to be this build's ABP (a `pp_class` override
  pointing at another hero's passed on labels alone).
- A hero extras file added after a build is a stale source (the old manifest had no entry to compare).
- A single-bone flap hanging from the pelvis makes the leg check apply, measured at its tip (it had read as "no
  legs").
- `dyn_compare --hero` reads only that hero's records; the pre-hero records only without a hero, never a mix.
- `--hero` with no value is an error, never a fall-through to `MT_HERO`.
- The no-rig-name check matches case-insensitively (`KHAIMERA_MESH`, `CR_MT_khaimeraDynamics`).

Re-verified offline: the parity repeat record re-gated with the new gate code passes every row except the sources row
(dyn_hero.py edited since that build, as intended; the next build refreshes it).

## Proof

- Unit: `dyn_hero` resolution (convention, profile fields, extras validation, missing hero), the extended
  no-rig-name test.
- Parity: rebuild the first hero through the parameterised pipeline and gate against its existing dynamics baseline
  (built before this change) with NO rebaseline: PASS (179 rows with the new identity hero row).
  Repeat PASS, `--nostep` FAIL.
- Docs: workflow doc, README commands.
