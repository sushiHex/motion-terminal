# MotionTerminal

**One reusable animation system, verified on every rig.**

A UE 5.9 project built around **one skeleton-agnostic Animation Blueprint Template** — locomotion state
machine, layered upper-body actions, runtime foot IK — driving a roster of differently-proportioned
characters with **minimal unique clips**. Compatible rigs share the system with zero retargeting; a
198-bone Paragon character joins through the IK Retargeter. Underneath, every rig's graph is **baselined,
diffed, and asserted** by two editor test tiers, run locally on every change; CI runs the editor-free host
suite on every push.

The four-rig lineup take shows a MetaHuman body (158 cm), the two UE5 mannequins (180 cm), and Paragon's
Khaimera (210 cm), all driven by the same Template at the same drive speed.

## The motion

- **Grounded locomotion** — idle / locomotion / airborne on one Template; ground-adaptive foot IK
  conforms both feet to the slope, matching stock, and is gated automatically in the air. (Stock's limits
  still apply: no foot locking, a single non-predictive trace, no toe roll.)
- **Layered actions that don't fight the gait** — upper-body montages blend from the spine up; the
  locomotion weights never flinch, and a test proves it per rig.
- **One anim set, N rigs** — the same clips drive every roster member; the Paragon retarget carries them
  onto a genuinely different skeleton.

Runtime IK brings the off-hand to the rifle's grip: 10.43 cm off, 1.34 cm on — measured in the render, not
eyeballed. An earlier take of the same hand-IK shot read 26.67 / 0.11 with a defective aim pose; the rifle-mode
branch (`docs/rifle-mode-branch-design.md`) poses the off hand toward the foregrip before IK runs, so IK has
less to close. Both sets of figures and their sampling windows are in
`Plugins/MotionTerminal/Tests/render_evidence.json`, key `hand_ik_reach`.

## The system

Clips are **instance-editable variables**, not hard-wired assets — the Template is the single source of
truth; characters override data, never structure. Per-character variation hangs off an **Animation Layer
Interface** seam. The gameplay contract is a set of **named variables**, not a graph the engineer edits.
Three of them are the *locomotion* contract — `Speed`, `IsFalling`, `GroundedFlag` — and gameplay writes
none of them: the Template derives all three from the CharacterMovementComponent every tick, so what
gameplay writes is the movement *source* and the three follow. On top of those sit the variables gameplay
does write directly — `UpperBodyState`, `TurnYawDelta`, `AimYaw`, `AimPitch`, `WeaponIKAlpha`, and the two
rifle-mode switches `RifleModeAlpha` and `AimOffsetAlpha` (both default 0; `Scripts/mt_drive.py`
`rifle_aim_pair` writes them together, because either one alone is wrong — see
`docs/rifle-mode-branch-design.md`). In the AnimGraph baseline
(`Plugins/MotionTerminal/Tests/Baselines/abp_mt_locomotion.json`, zero-based indices into the AnimGraph's
`nodes` array), node 8, the `AO_Rifle` aim offset, binds `AimYaw`, `AimPitch` and `AimOffsetAlpha` to its
pins; node 15, the off-hand Two Bone IK, binds `WeaponIKAlpha`; and `RifleModeAlpha` reaches the rifle-mode
blend weight through an in-graph variable-get link (node 21), not a binding. `Scripts/army_conductor.py`
drives `TurnYawDelta`/`AimYaw`/`AimPitch`/`WeaponIKAlpha` as "pure inputs" through `SetAnimVariable`,
because nothing in the EventGraph writes them, and holds both rifle-mode switches at 0 for the unarmed crowd.

The animator↔engineer interface is therefore ten variables: the three locomotion variables the Template
derives, and the seven inputs gameplay writes for upper-body state, turn, aim, weapon IK and rifle mode. Two
more pins are bound to variables that gameplay never writes: `FootIKAlpha` (node 1, the foot-IK Control Rig)
and the hand IK's `WeaponIKTargetWorld` effector, both recomputed by the EventGraph every tick.

The Template is **skeleton-aware, not merely skeleton-agnostic**. Its foot-IK Control Rig solves onto
`ik_foot_l`/`ik_foot_r`, so on a skeleton that has no mannequin IK rig it would otherwise solve against
static effectors and silently pin the feet. The node's alpha is gated on a bone-presence check computed once
at init, so a rig without those bones bypasses the solve and keeps its stride. That gate has its own test,
and the test has a proven-OFF.

The crowd take puts 46 characters on the same Template, in three LOD/URO tiers, driven live by
`Scripts/army_conductor.py`.

## Verified, per rig

```
$ python Scripts/run_verification.py            # topology gate: golden-diff every rig's graph vs reviewed baselines
$ python Scripts/run_verification.py --tier pie # runtime gate: PIE assertions — states reached, weights correct, IK gating
```

- **Golden-diff baselines** — each rig's authored/retargeted graph serialized to canonical JSON and diffed
  against a reviewed baseline. Regenerating a baseline is a deliberate, code-reviewed act. Break a binding
  and the gate names exactly which pin changed.
- **Live per-node introspection** — machine / state / montage / slot weights *and* the full per-node
  blend-weight tree, live over MCP, GUID-keyed back to the authored nodes. Write-up:
  [docs/introspection-mechanisms.md](docs/introspection-mechanisms.md).
- **Import integrity** — the classic Maya→FBX scale papercut is detected, auto-corrected in the
  deterministic cases, and asserted in the per-rig gate before it can masquerade as a retargeting bug. UE
  5.8 lands an FBX unit conversion at or above the root joint, so bone lengths survive intact and only the
  bounds move; the fix distinguishes that from an artist's baked root scale by the file's own declared unit,
  and refuses rather than guessing when it cannot tell.
- **Every clip that enters the roster passes a motion-varies check** — a silently-frozen bake cannot survive
  an eyeballed frame here; it fails a test.

The verification layer is the part of this project that took the most work, and the discipline is narrow:
*a test that cannot fail is not a test*. **Every gate added or reworked for the roster, import-integrity
and skeleton-aware work has a proven-OFF reference — a recorded run where it goes red for the right
reason — with one exception, named below.** They are catalogued in `docs/verification-design.md` beside
the run that produced each. That standard caught real defects in its own tooling repeatedly, including a
runtime row that had been green on the development machine while failing on every clean checkout.

The exception is the MetaHuman probe's BONE half: structurally sound, empirically unproven — no witnessed
red. The claim does not extend to "every gate" across all ~45 rows, which would outrun the evidence; the
earliest rows (the first topology, golden-diff and foot-IK gates) are documented per-row but are not
catalogued as a set.

## How it was built (and what's original)

Everything is authored **over MCP**: Unreal Engine's own MCP server, driven by tool calls, with this project's plugin
supplying the tools the engine does not have.

Where the engine has a route, the project uses it: the IK Rig and Retargeter controllers, the batch
retarget, Blueprint variables and compilation, and Python inside the editor. Where it has none, the plugin
adds one: an AnimGraph built from a declarative spec, blend spaces, a skeleton's bone tree, a physics asset
from a spec. **No builder vouches for its own work.** Every generated asset is described by a separate read,
written without shared code, and accepted only when that description matches the spec, in the compiled
result as well as the editor's (`docs/architecture.md`).

**The original work is the verification layer:** the per-rig golden-diff gate, the build-describe-accept
pattern, the four-mechanism per-node introspection surface (including the per-node weight tree), the
import-integrity tools, and the shot drivers that exercise them.

## Quickstart

*Written against a genuine clean clone. The host-side lines (the Python setup, the pytest suite and the
public gate) are what CI runs on every push. The build and editor-tier lines were run on the author's
machine before publication, where the licensed content below is installed; the SKIP counts further down
say which rows a clone without it leaves unexercised. What a fresh clone can and cannot do is stated rather
than implied.*

**1 — Clone and build.** Requires UE 5.9 and Visual Studio 2022. Keep the checkout path short: UE's build
tool refuses paths that push generated filenames past 260 characters.

In PowerShell (the `&` call operator runs a quoted path):

```
git clone <repo> MotionTerminal
cd MotionTerminal
& "<UE_5.9>/Engine/Build/BatchFiles/Build.bat" MotionTerminalEditor Win64 Development -Project="<abs>/MotionTerminal.uproject" -WaitMutex
```

**1b — Add Epic's Third Person template content.** It is not in the repository, and the editor tiers need
it. Create a UE 5.9 Third Person (Blueprint) project and copy its `Content/Characters`,
`Content/LevelPrototyping`, `Content/ThirdPerson`, `Content/Input`, `Content/__ExternalActors__` and
`Content/__ExternalObjects__` folders into this project's `Content/` (the default map is the template's
level, and the MT test levels use the Mannequins).

**1c — Look at it in the editor (optional).** Open `MotionTerminal.uproject`. The editor starts on the Third
Person template's own level, because the project's startup map, default map and default GameMode are the
template's, so pressing Play there gives you the stock Third Person character. To see this project's content,
open `/Game/MT/Lvl_MT_Test` and press Play. Its GameMode (`BP_MT_GameMode`) possesses `BP_MT_TestPawn`, the
UE5 Manny on the shared Template (`ABP_MT_Locomotion`) with the cube stand-in rifle, on the test lane: a flat
floor, a 20° ramp, a landing, four 25 cm stairs and a second landing. The pawn has no input bindings and no
camera of its own. It is the subject the PIE tier and the drivers move with `AddMovementInput`, so in Play it
stands in its idle, and the view sits at its eye height. Use Eject on the Play toolbar to leave the pawn and
orbit the viewport to watch it. `/Game/MT/Lvl_MT_Test_Quinn` is the same lane with Quinn. Moving characters, the crowd and the
rendered takes need the drivers and a running editor (step 3).

**2 — Run the gates. With steps 1 and 1b done, this needs no other content** (no licensed packs).

The host-side tooling needs Python 3.14 (the version CI pins) and three packages:

```
$ python -m pip install -r Scripts/requirements-dev.txt   # pytest, pillow, numpy
```

```
$ python Scripts/run_verification.py              # topology + golden-diff  -> Tests: 29  Failed: 0
$ python Scripts/run_verification.py --tier pie   # PIE runtime assertions  -> Tests: 24  Failed: 0
$ python -m pytest Scripts/ -q                    # host-side tooling       -> 940 passed, 5 skipped
```

The pytest line is the clean-clone result. Five tests skip there by design, each because its input is
not in the repository:

- the three `TheCommittedStamps` tests in `Scripts/test_fixture_freshness.py`
  (`test_the_committed_artifacts_are_bound_and_current`, `test_no_stamp_file_is_silently_empty`,
  `test_the_local_leg_skips_with_log_when_captures_is_absent`): no stamped artifacts
  (no `artifact_stamps.json` under the research fixtures) are committed, and no rendered `captures/` folder exists;
- `test_khaimera_ground_truth_matches_the_spike_rig`: no intake facts from the licensed Khaimera pack;
- `test_the_repository_passes_the_gate`: the optional project-local term list, kept outside the
  repository, is absent. The gate's generic rules still run, and they must pass.

The pytest suite and the public gate need no engine; they also run in CI on every push and pull request
(`.github/workflows/host-tests.yml`). The two editor tiers run locally.

The engine is located automatically (`$MT_UE_ROOT`, then the Windows registry, then Epic's
`LauncherInstalled.dat`, then the conventional install path); set `MT_UE_ROOT` if yours is somewhere
unusual, and the runner will tell you by name if it cannot find it.

`Scripts/clean_checkout_check.py` clones any commit into a fresh directory, builds it, and runs the host
suite and both tiers there. It does not perform step 1b. The template content is a precondition, not a
`localOnly` dependency, so the rows that read it (`Verify.UnarmedTopology`, `Verify.AnimGraphBuild`, `GoldenDiff.Unarmed` and the
Manny and Quinn rows) fail rather than skip in a clone that lacks it. Its editor-tier result therefore
describes a clone with step 1b done; its host-only leg (`--no-editor`) needs no engine, only git, Python 3.14 and the
packages in `Scripts/requirements-dev.txt`.

**Rows that SKIP on a clean clone, and why.** Both tiers come back green, but green is not the same as
complete: **13 of the 29** default-tier rows and **9 of the 24** pie rows SKIP-with-log (measured on a clean
clone before publication, plus `Pie.Army.OnCapsule`, added after that measurement and gated on the same
army stage), because they assert against separately-licensed content or machine-local stages this repo
does not commit. Each prints
`SKIP (localOnly … absent on this machine): <asset>`, and each run reports
`Tests: N  Failed: N  Skipped(localOnly): N  Exercised: N` so a green line can never be mistaken for full
coverage. Adding the content below turns those rows on; nothing else changes.

| To turn on | Add | Where it goes |
|---|---|---|
| `GoldenDiff.MTKhaimera`, `ClipVaries.Khai_*`, `IKEffectorDrives.Khai_*`, `ImportScale.khaimera`, the three `Pie.*.Khaimera` rows | **Paragon: Khaimera** from Fab (~2 GB, Epic-licensed) | `Content/ParagonKhaimera/`, then the retarget steps in `research/khaimera-retarget-pipeline.md` |
| `GoldenDiff.MTKhaimeraDynamics`, `GoldenDiff.MTAuroraDynamics` | the hero's Fab pack (**Paragon: Khaimera**, **Paragon: Aurora**), then its dynamics graph | built by `Scripts/dynamics_graph.py --hero <Rig>` after the hero is onboarded |
| `ImportScale.metahuman` | a **MetaHuman body**, created in-editor via the MetaHumanCharacter plugin | `Content/MetaHumans/` (`Scripts/metahuman_body_author.py`) |
| `Pie.MontageSeamless` | the Fab **Game Animation Sample** pack (animations only, retargeted to the UE5 mannequin) | import with `Scripts/clip_import_gas.py`; set `MT_GAS_FBX_ROOT` if your Fab VaultCache is elsewhere |
| the five `Pie.Army.*` rows (`Pie.Army.OnCapsule` also needs every crowd rig's pawn, so Paragon: Khaimera above, and FAILS rather than skips if a rig's `crowd_anim_class` graph does not load: Khaimera's is authored by `Scripts/khai_inplace_author.py`) | the **army stage**, a machine-local level (no licensed content, just not committed) | `/Game/MT/Army/Lvl_MT_Army`, rebuilt by running `Scripts/stage_author.py` inside the editor with `STAGE_NAMES=('army',)` and `STAGE_ACTION='author'` (the file header shows the call; needs a running editor, step 3) |

**3 — Only if you want to author or re-shoot: run the editor.**

Everything above runs without a live editor. The editor-side authoring below does not: the scripts in
`Scripts/` that author stages, sequences, heroes and shots, and the PIE drivers, run inside the editor or
call its tools, all through one transport (`Scripts/editor_ops.py`, over the engine's MCP server). Nothing
else needs installing: the tools they call are this project's plugin and the engine's own. The host tools
(`run_verification.py`, the pytest suite, `public_gate.py`, `clean_checkout_check.py` and the like) start
their own editor process or need none.

```
python Scripts/editor_session.py start    # starts the editor unattended, with its MCP server
python Scripts/wait_for_editor.py         # blocks until the editor's MCP server answers on :8000
```

`wait_for_editor.py` names the cause when it times out: the server was not started, a toolset plugin is not enabled, or the editor is still loading.

`.mcp.json` registers one MCP server for an MCP client opened in this folder: `unreal-mcp`, the editor's own
HTTP endpoint at `127.0.0.1:8000`. It fails harmlessly until the editor is running.

**4 — What a clean clone cannot reproduce, stated up front.** The rendered frames (`captures/`,
gitignored) and the machine-local stages are not committed. `Scripts/stage_author.py` and
`Scripts/shot_seq_author.py` rebuild every stage and sequence, and each shot has a committed driver — with
one exception recorded at its manifest row: **the opening shot's camera exists nowhere** and cannot be recovered.
Re-shooting any shot produces a new take, not a byte-match, because the graph has moved on since some of
them were rendered. `Plugins/MotionTerminal/Tests/shots_manifest.json` carries the details per shot.
Because git cannot protect any of that, `Scripts/backup_local.py <dir>` copies it, additively, to a
directory you name (ideally on another drive) and writes a manifest of what it took.

The pie tier runs on a real RHI and is sensitive to GPU headroom; the runner prints free VRAM before it
starts, because under memory pressure its rows fail with messages that read like animation defects.

## Operations

One entry point runs every operation, the same for any hero (`python Scripts/mt.py --help` lists them):

```
python Scripts/mt.py editor start            # the editor, unattended, with its MCP server
python Scripts/mt.py doctor                  # thirty seconds of checks on it before a long run
python Scripts/mt.py onboard <Hero>          # IK rig, retargeter, bake, locomotion, in-place, pawn, physics, verify
python Scripts/mt.py status <Hero>           # where that stands (no editor needed)
python Scripts/mt.py build <Hero>            # the dynamics rig and graph
python Scripts/mt.py run <Hero> <tag> --gate # the ON/OFF drive, judged against the hero's baseline
python Scripts/mt.py verify --tier pie       # the editor test tiers
```

Each line runs one script in `Scripts/`, whose header is its full reference. Onboarding is a list of stages with a
record file per hero: a failed run says which stage stopped and why, and `--resume` continues from it.

## Tool reference

| Tool | Entry points | What it's for |
|---|---|---|
| **AnimGraphTools** | `Ping`, `ReadAnimGraph`, `SnapshotBaseline`, `DiffAnimGraph`, `DescribeAnimGraph` | Serialize an AnimGraph to canonical JSON, snapshot a reviewed baseline, and diff a rig's current graph against it — the topology gate. `DescribeAnimGraph` reads a graph three ways (editor nodes, links, compiled class) for accepting a build. |
| **AnimGraphBuildTools** | `BuildAnimBlueprint`, `SaveAnimBlueprint` | Build an Animation Blueprint's AnimGraph from a declarative spec; saved only once its description matches. |
| **AnimAssetTools** | `DescribeSkeleton`, `BuildBlendSpace`, `DescribeBlendSpace`, `SaveBuiltAsset` | A skeleton's bones with parents and retargeting modes, and blend spaces: what editor scripting cannot reach. |
| **EditorSessionTools** | `StatSampleBegin`, `StatSampleRead`, `StatSampleEnd`, `GetLogFile` | Stat-group timings over a window of frames, and the editor's log file, for host-side drivers. |
| **AnimDebugTools** | `GetAnimDebugState`, `GetNodeWeights`, `GetNodeWeightsByGuid`, `DetachNodeWeightDebug`, `SetAnimVariable`, `GetAnimVariable` | Live introspection of a running AnimInstance: machine/state/montage/slot weights and the full per-node blend-weight tree, GUID-keyed back to authored nodes. |
| **AnimImportTools** | `SnapshotScaleBaseline`, `CheckImportScale`, `AssertImportScale`, `FixImportScale` | Detect, correct, and gate FBX import-scale drift per rig before it can masquerade as a retargeting bug. |

## Licence

This project's own work — the `MotionTerminal` plugin (C++), the code under `Scripts/`, the docs, and the
project-authored assets in `Content/MT/` and `Content/Spike/` — is **MIT**, except `Content/MT/SM_MT_Rifle`
(below). See [LICENSE](LICENSE) and [NOTICE](NOTICE). `Content/Spike/` holds the early integration-spike
assets (`ABP_Spike`, `ABP_LocomotionTemplate`, `AM_Attack_Spike`) and `IK_Manny`, an IK Rig asset authored
for the stock mannequin that the onboarding and dynamics scripts use as the retarget source. These assets
reference Epic content (the mannequin skeleton, meshes and clips), which is not included. Two assets do carry
Epic-derived data, listed in the next section.

What the MIT grant does **not** cover, because none of it is this project's to license:

* **Unreal Engine and its stock content** — the mannequin skeletons, meshes and the `MF_/MM_` animation
  sets this project retargets and drives are Epic's, under the Unreal Engine EULA. This repo reads and
  references them; it does not redistribute them and never modifies them. The one exception is the next
  item.
* **`Content/MT/SM_MT_Rifle`** — a scaled copy of the engine's basic cube shape, standing in where the
  licensed rifle is not installed. It remains subject to the Unreal Engine EULA.
* **The mannequin skeleton data inside `Content/Spike/IK_Manny`.** An IK Rig asset stores a copy of its
  skeleton's bone names, hierarchy and reference pose. In `IK_Manny` that copy is Manny's 89-bone skeleton,
  from Epic's template content, and it remains Epic's under the Unreal Engine EULA. The rig setup authored on
  top of it (chains, goals, solvers) is this project's.

The other committed assets in `Content/MT/` and `Content/Spike/` were authored in this project. They
reference Epic content by path but do not include its mesh, texture or animation data.
* **Images and renders under `research/fixtures/`** that depict Epic content (the UE mannequins, Paragon
  characters, a MetaHuman body). They are shown under Epic's terms for that content, not under MIT.
* **Rig-derived measurement data** — `HeroProfiles/`, `Plugins/MotionTerminal/Tests/ScaleBaselines/` and
  `Plugins/MotionTerminal/Tests/DynamicsBaselines/`. These are measurements of separately licensed rigs,
  kept so the gates can verify against them. The licensed content itself is not included.
* **Licensed marketplace/Fab content** — the Paragon assets, the MetaHuman body and the rifle are
  machine-local and gitignored for exactly this reason. The rows that depend on them are marked
  `localOnly` and SKIP on a clean clone rather than failing it.
