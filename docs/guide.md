# MotionTerminal technical guide

The README covers what the project is and what it shows. This guide is the reference: setup from a clean clone,
what a clone can and cannot exercise, the gameplay contract, the operations, the plugin's tools and the licence
detail.

- [Setup from a clean clone](#setup-from-a-clean-clone)
- [What skips on a clean clone](#what-skips-on-a-clean-clone)
- [Running the editor](#running-the-editor)
- [Operations](#operations)
- [The Template's contract](#the-templates-contract)
- [Verification layer](#verification-layer)
- [Plugin tools](#plugin-tools)
- [What a clone cannot reproduce](#what-a-clone-cannot-reproduce)
- [Licence detail](#licence-detail)

## Setup from a clean clone

The host-side lines (the Python setup, the pytest suite and the public gate) are what CI runs on every push. The
build and editor-tier lines were run before publication on a machine with the licensed content installed.

**1. Clone and build.** This needs UE 5.9 and Visual Studio 2022. Keep the checkout path short: UE's build tool
refuses paths that push generated filenames past 260 characters. In PowerShell:

```powershell
git clone https://github.com/sushiHex/motion-terminal.git MotionTerminal
cd MotionTerminal
& "<UE_5.9>/Engine/Build/BatchFiles/Build.bat" MotionTerminalEditor Win64 Development -Project="<abs>/MotionTerminal.uproject" -WaitMutex
```

The engine is located automatically, trying in order `$MT_UE_ROOT`, the Windows registry, Epic's
`LauncherInstalled.dat`, then the conventional install path. Set `MT_UE_ROOT` if yours is somewhere unusual; the
runner names the problem if it cannot find one.

**2. Add Epic's Third Person template content.** It is not in the repository, and the editor tiers need it.
- Create a UE 5.9 Third Person (Blueprint) project.
- Copy these folders from it into this project's `Content/`: `Content/Characters`, `Content/LevelPrototyping`,
  `Content/ThirdPerson`, `Content/Input`, `Content/__ExternalActors__` and `Content/__ExternalObjects__`.
- The project's startup map, default map and default GameMode are the template's, and the MT test levels use its
  mannequins.

**3. Look at it in the editor (optional).** Open `MotionTerminal.uproject`, then open `/Game/MT/Lvl_MT_Test` and
press Play.
- **What's there:** `BP_MT_TestPawn` is the UE5 Manny on the shared Template (`ABP_MT_Locomotion`), carrying the
  cube stand-in rifle. It stands on the test lane: a flat floor, a 20° ramp, four 25 cm stairs and two landings.
- **Why it stands still:** the pawn has no input bindings and no camera. It is the subject that the PIE tier and
  the drivers move, so use Eject to orbit the viewport.
- **Quinn:** `/Game/MT/Lvl_MT_Test_Quinn` is the same lane with Quinn.

**4. Run the gates.** With steps 1 and 2 done, this needs no other content. The host tooling needs Python 3.14 (the
version CI pins):

```
$ python -m pip install -r Scripts/requirements-dev.txt   # pytest, pillow, numpy
$ python Scripts/run_verification.py              # topology + golden-diff
$ python Scripts/run_verification.py --tier pie   # PIE runtime assertions
$ python -m pytest Scripts/ -q                    # host-side tooling
```

Five pytest tests skip on a clean clone by design, because their inputs are not in the repository:
- **Three `TheCommittedStamps` tests** in `Scripts/test_fixture_freshness.py`: no stamped artifacts are committed,
  and there is no rendered `captures/` folder.
- **`test_khaimera_ground_truth_matches_the_spike_rig`:** it needs intake facts from the licensed Khaimera pack.
- **`test_the_repository_passes_the_gate`:** the optional project-local term list is absent. The gate's generic
  rules still run and must pass.

`Scripts/clean_checkout_check.py` clones any commit into a fresh directory, builds it, and runs the host suite and
both tiers there. It does not perform step 2, which is a precondition rather than a `localOnly` dependency. So the
rows that read the template content fail rather than skip in a clone without it. Its host-only leg (`--no-editor`)
needs only git, Python 3.14 and the packages above.

## What skips on a clean clone

Both tiers come back green, but green is not the same as complete. Rows that assert against separately licensed
content, or against machine-local stages this repository does not commit, SKIP with a log line:
`SKIP (localOnly … absent on this machine): <asset>`.

Every run reports `Tests: N  Failed: N  Skipped(localOnly): N  Exercised: N`, so a green line can't be mistaken
for full coverage. Adding the content below turns those rows on; nothing else changes.

| To turn on | Add | Where it goes |
|---|---|---|
| `GoldenDiff.MTKhaimera`, `ClipVaries.Khai_*`, `IKEffectorDrives.Khai_*`, `ImportScale.khaimera`, the three `Pie.*.Khaimera` rows | **Paragon: Khaimera** from Fab (~2 GB, Epic-licensed) | `Content/ParagonKhaimera/`, then the retarget steps in `research/khaimera-retarget-pipeline.md` |
| `GoldenDiff.MTKhaimeraDynamics`, `GoldenDiff.MTAuroraDynamics` | the hero's Fab pack (**Paragon: Khaimera**, **Paragon: Aurora**), then its dynamics graph | `Scripts/dynamics_graph.py --hero <Rig>` after the hero is onboarded |
| `ImportScale.metahuman` | a **MetaHuman body**, created in-editor with the MetaHumanCharacter plugin | `Content/MetaHumans/` (`Scripts/metahuman_body_author.py`) |
| `Pie.MontageSeamless` | the Fab **Game Animation Sample** pack (animations only, retargeted to the UE5 mannequin) | `Scripts/clip_import_gas.py`; set `MT_GAS_FBX_ROOT` if your Fab VaultCache is elsewhere |
| the five `Pie.Army.*` rows | the **army stage**, a machine-local level (no licensed content, just not committed). `Pie.Army.OnCapsule` also needs every crowd rig's pawn, and fails rather than skips if a rig's `crowd_anim_class` graph does not load | `/Game/MT/Army/Lvl_MT_Army`, rebuilt by `Scripts/stage_author.py` inside the editor with `STAGE_NAMES=('army',)` and `STAGE_ACTION='author'` |

The pie tier runs on a real RHI and is sensitive to GPU headroom. The runner prints free VRAM before it starts,
because under memory pressure its rows fail with messages that read like animation defects.

## Running the editor

Everything above runs without a live editor. Authoring stages, sequences, heroes and shots, and the PIE drivers,
does need one. They all go through one transport, `Scripts/editor_ops.py`, over the engine's own MCP server.

```
python Scripts/editor_session.py start    # starts the editor unattended, with its MCP server
python Scripts/wait_for_editor.py         # blocks until the editor's MCP server answers on :8090
```

- **The launch flag:** `editor_session.py` starts the editor with `-unattended`, so no native modal can block
  automation, and passes the MCP port on the command line.
- **Why not port 8000:** Windows can reserve a port block that covers the engine's default 8000 (WinNAT's dynamic
  exclusions). See `Scripts/unreal_mcp.py`.
- **For an MCP client:** `.mcp.json` registers the editor's HTTP endpoint `127.0.0.1:8090` as `unreal-mcp`. It fails
  harmlessly until the editor is running.

## Operations

One entry point runs every operation, the same for any hero (`python Scripts/mt.py --help`):

```
python Scripts/mt.py editor start            # the editor, unattended, with its MCP server
python Scripts/mt.py doctor                  # thirty seconds of checks before a long run
python Scripts/mt.py intake /Game/<Pack>     # read a character pack into a hero profile (PowerShell)
python Scripts/mt.py onboard <Hero>          # IK rig, retargeter, bake, locomotion, in-place, pawn, physics, verify
python Scripts/mt.py status <Hero>           # where that stands (no editor needed)
python Scripts/mt.py build <Hero>            # the dynamics rig and graph
python Scripts/mt.py run <Hero> <tag> --gate # the ON/OFF drive, judged against the hero's baseline
python Scripts/mt.py render <Hero> <out_dir> # the review clip
python Scripts/mt.py verify --tier pie       # the editor test tiers
python Scripts/mt.py editor stop
```

Each line runs one script in `Scripts/`, and that script's header is its full reference. Onboarding is a list of
stages with a record file per hero. A failed run says which stage stopped and why, and `--resume` continues from
it. Run `intake` from PowerShell, because Git Bash rewrites `/Game/...` arguments into Windows paths.

## The Template's contract

Clips are instance-editable variables, not hard-wired assets. The Template is the single source of truth:
characters override data, never structure. Per-character variation hangs off an Animation Layer Interface seam.

The animator-engineer interface is ten variables:
- **Locomotion (three):** `Speed`, `IsFalling` and `GroundedFlag`, derived by the Template from the
  CharacterMovementComponent every tick. Gameplay writes the movement source, and these three follow.
- **Gameplay inputs (seven):** `UpperBodyState`, `TurnYawDelta`, `AimYaw`, `AimPitch` and `WeaponIKAlpha`, plus the
  rifle-mode switches `RifleModeAlpha` and `AimOffsetAlpha`.
  - The rifle-mode switches are written together (`Scripts/mt_drive.py` `rifle_aim_pair`), because either alone is
    wrong (`docs/rifle-mode-branch-design.md`).
- **Recomputed, never written:** `FootIKAlpha` and the hand IK's `WeaponIKTargetWorld` effector, which the
  EventGraph recomputes every tick.

The AnimGraph baseline, `Plugins/MotionTerminal/Tests/Baselines/abp_mt_locomotion.json`, records each of these
bindings by node.

**Skeleton-aware, not merely skeleton-agnostic.** The foot-IK Control Rig solves onto `ik_foot_l` and `ik_foot_r`.
On a skeleton without the mannequin's IK bones it would solve against static effectors and silently pin the feet.
So the node's alpha is gated on a bone-presence check computed at init, and a rig without those bones keeps its
stride. That gate has its own test, and the test has a proven-OFF run.

## Verification layer

- **Golden-diff baselines.** Each rig's graph is serialized to canonical JSON and diffed against a baseline that
  was checked by a person. Regenerating a baseline is a deliberate, code-reviewed act. Break a binding and the
  gate names the pin that changed.
- **Live per-node introspection.** Machine, state, montage and slot weights, plus the full per-node blend-weight
  tree, live over MCP and GUID-keyed back to the authored nodes
  ([introspection-mechanisms.md](introspection-mechanisms.md)).
- **Build, describe, accept.** No builder vouches for its own work. Every generated asset is described by a
  separate read that shares no code with the builder. It is accepted only when that description matches the spec,
  in the compiled result as well as the editor's ([architecture.md](architecture.md)).
- **Import integrity.** The Maya-to-FBX scale papercut is detected, corrected in the deterministic cases, and
  asserted per rig before it can masquerade as a retargeting bug.
  - The fix tells an FBX unit conversion apart from an artist's baked root scale by the file's own declared unit.
  - It refuses rather than guessing when it can't tell.
- **Motion-varies check.** Every clip that enters the roster must move. A silently frozen bake fails a test.
- **Proven-OFF references.** Every gate added or reworked for the roster, import-integrity and skeleton-aware work
  has a recorded run where it goes red for the right reason. They are catalogued in
  [verification-design.md](verification-design.md).
  - The one exception is the MetaHuman probe's bone half: structurally sound, but with no witnessed red.
  - The earliest rows are documented one by one but not catalogued as a set.

## Plugin tools

| Tool | Entry points | What it's for |
|---|---|---|
| **AnimGraphTools** | `Ping`, `ReadAnimGraph`, `SnapshotBaseline`, `DiffAnimGraph`, `DescribeAnimGraph` | Serialize an AnimGraph to canonical JSON, snapshot a baseline and diff a rig's graph against it. `DescribeAnimGraph` reads a graph three ways (editor nodes, links, compiled class) to accept a build. |
| **AnimGraphBuildTools** | `BuildAnimBlueprint`, `SaveAnimBlueprint` | Build an AnimGraph from a declarative spec; saved only once its description matches. |
| **AnimAssetTools** | `DescribeSkeleton`, `BuildBlendSpace`, `DescribeBlendSpace`, `SaveBuiltAsset` | A skeleton's bone tree with retargeting modes, and blend spaces: what editor scripting cannot reach. |
| **EditorSessionTools** | `StatSampleBegin`, `StatSampleRead`, `StatSampleEnd`, `GetLogFile` | Stat-group timings over a window of frames, and the editor log, for host-side drivers. |
| **AnimDebugTools** | `GetAnimDebugState`, `GetNodeWeights`, `GetNodeWeightsByGuid`, `DetachNodeWeightDebug`, `SetAnimVariable`, `GetAnimVariable` | Live introspection of a running AnimInstance, GUID-keyed back to authored nodes. |
| **AnimImportTools** | `SnapshotScaleBaseline`, `CheckImportScale`, `AssertImportScale`, `FixImportScale` | Detect, correct and gate FBX import-scale drift per rig. |

Where the engine already has a route, the project uses it: the IK Rig and Retargeter controllers, batch retarget,
Blueprint variables and compilation, and editor Python. The plugin adds only what the engine lacks.

## What a clone cannot reproduce

The rendered frames (`captures/`, gitignored) and the machine-local stages are not committed.
- **Rebuilding:** `Scripts/stage_author.py` and `Scripts/shot_seq_author.py` rebuild every stage and sequence, and
  each shot has a committed driver. The one exception is the opening shot's camera, which exists nowhere.
- **Re-shooting:** it produces a new take, not a byte-match, because the graph has moved on since some shots were
  rendered. `Plugins/MotionTerminal/Tests/shots_manifest.json` has the details per shot.
- **Backing up:** git can't protect any of this, so `Scripts/backup_local.py <dir>` copies it additively to a
  directory you name and writes a manifest of what it took.

## Licence detail

This project's own work is MIT: the `MotionTerminal` plugin (C++), the code under `Scripts/`, the docs, and the
project-authored assets in `Content/MT/` and `Content/Spike/`. See [LICENSE](../LICENSE) and [NOTICE](../NOTICE).

The MIT grant does **not** cover the following, because none of it is this project's to license:

- **Unreal Engine and its stock content.** The mannequin skeletons, meshes and the `MF_` / `MM_` animation sets are
  Epic's, under the Unreal Engine EULA. This repository references them; it does not redistribute or modify them,
  except for the two assets below.
- **`Content/MT/SM_MT_Rifle`.** A scaled copy of the engine's basic cube, standing in where the licensed rifle is
  not installed. It remains subject to the Unreal Engine EULA.
- **The mannequin skeleton data inside `Content/Spike/IK_Manny`.**
  - An IK Rig asset stores a copy of its skeleton's bone names, hierarchy and reference pose. In `IK_Manny` that copy
    is Manny's 89-bone skeleton from Epic's template content, and it remains Epic's under the Unreal Engine EULA.
  - The rig setup authored on top of it (chains, goals, solvers) is this project's.
- **Images and renders** under `research/fixtures/` that depict Epic content (the UE mannequins,
  Paragon characters, a MetaHuman body). They are shown under Epic's terms for that content.
- **Rig-derived measurement data:** `HeroProfiles/`, `Plugins/MotionTerminal/Tests/ScaleBaselines/` and
  `Plugins/MotionTerminal/Tests/DynamicsBaselines/`. These are measurements of separately licensed rigs, kept so
  the gates can verify against them.
- **Licensed Fab content.** The Paragon assets, the MetaHuman body and the rifle are machine-local and gitignored.
  The rows that depend on them are marked `localOnly` and skip on a clean clone.
- **CMU motion capture data.** It is never committed. Its licence record
  (`Plugins/MotionTerminal/Tests/Mocap/licenses/cmu.json`) carries the terms as read and the acknowledgement CMU
  asks for: the data used in this project was obtained from mocap.cs.cmu.edu, and the database was created with
  funding from NSF EIA-0196217.

Every other committed asset in `Content/MT/` and `Content/Spike/` was authored in this project. They reference
Epic content by path but don't include its mesh, texture or animation data.
