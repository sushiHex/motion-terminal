# MotionTerminal — Architecture

*Architecture write-up. Companion documents: `docs/verification-design.md` (what "verified" means here),
`docs/introspection-mechanisms.md` (the four engine mechanisms behind the debug surface) and
`docs/specs/2026-11-12-animgraph-build-describe-design.md` (the AnimGraph builder). None of the three is repeated below.*

*Status, 2026-11-12: the project moved to UE 5.9 and its authoring layer was rebuilt on the project's own tools.
That layer compiles and passes its host tests; its first full editor run is still to come. Where this document
says how something is checked, it describes the mechanism, and `docs/verification-design.md` records what has
actually been witnessed.*

## What this is

MotionTerminal is a UE 5.9 project with one thesis: **an agent upgrades legacy character rigs to current-engine
features through the engine's MCP server, and every step is declared, built, read back and gated.** What it
upgrades them onto is one reusable animation system, shared across a spectrum of rigs. Everything is authored by
tools this project ships or the engine's own, and nothing an authoring tool reports about its own work counts as
proof of it.

The layers above the tools (one sequence for any generated asset, stages per hero, one entry point per operation)
are in `docs/specs/2026-11-12-asset-pipeline-design.md`.

## The line that carries the weight: what writes is not what checks

Every generated asset goes through the same four steps:

```
spec (host, pure Python)  ->  Build (one tool call)  ->  Describe (a separate read)  ->  mismatch(spec, described)
```

A build is accepted, and only then saved, when `mismatch` returns nothing. The pattern covers a hero's reaction
physics asset (`HeroPhysicsTools`), Animation Blueprint graphs (`AnimGraphBuildTools` built, `AnimGraphTools`
described), blend spaces (`AnimAssetTools`), and IK rigs and retargeters (built and described through the engine's
own controllers).

Three rules make that worth more than a round trip:

1. **The reader shares no code with the writer.** `AnimGraphDescribe` never includes `AnimGraphOps`; a helper both
   would use is written twice. One bug cannot then pass on both sides. This is the same independence the golden-diff
   gate has always had from whatever authored the graph it reads.
2. **The description reads the compiled result, not only the editor's.** An Animation Blueprint node's property can
   hold one value in the editor while the compiled class runs another: a member with a visible pin is compiled from
   the pin. So a graph is described three ways — editor node, link set, compiled class — and a value present in the
   first and absent in the third fails.
3. **A builder refuses rather than repairs.** It will not write over an existing asset, fill a non-empty graph, set
   a property that is also linked, or give one pose two consumers (the engine would silently drop the first link).
   Whatever it cannot read back is reported as unread and fails unless the caller names it.

What the comparison cannot see is stated where it is made: a constant the compiler folds out of a node, a Control
Rig variable's compiled wiring, and anything that only fails at runtime keep their own checks (a PIE assertion, a
rendered take, a person watching it).

## What comes from the engine and what is ours

The rule is: an engine-native route first; this project's own code where the engine has none.

| Need | Route |
|---|---|
| MCP server, tool registration, running Python in the editor | Engine: ModelContextProtocol, ToolsetRegistry, `execute_python` |
| IK Rig, IK Retargeter, batch retarget | Engine: the IK Rig / Retargeter controllers and the batch operation, from editor Python |
| Animation Blueprint asset, variables, compile | Engine: `UAnimBlueprintFactory`, Blueprint utilities |
| AnimGraph nodes, properties, pins, links | Ours: `AnimGraphOps` behind `AnimGraphBuildTools`. The engine's Blueprint toolset connects through the K2 schema, not the AnimGraph's, and cannot set an anim node's properties or expose a Control Rig variable |
| Reading a graph back, golden baselines | Ours: `AnimGraphTopology`, `AnimGraphDescribe` |
| Blend spaces, a skeleton's bone tree and retargeting modes | Ours: `AnimAssetTools`. The engine exposes no script-callable function for either |
| Physics asset from a spec, with collision pairs | Ours: `HeroPhysicsTools`. The engine's physics toolset takes no spec and has no collision pairs |
| Stat-group timings over a window of frames | Ours: `StatWindow` behind `EditorSessionTools`. The engine's stats toolset reads one frame and no cycle averages |
| Movie Render Queue renders | Ours, over the engine's Python API: the engine ships guidance for this, not a tool |
| Live anim introspection (states, per-node weights) | Ours: `AnimDebugTools` (`docs/introspection-mechanisms.md`) |

## The extension seam: ToolsetRegistry + AICallable

The plugin exposes tools over the engine's MCP server without touching engine files. Each toolset is a
`UToolsetDefinition` subclass whose tools are static `UFUNCTION(meta = (AICallable))` methods; `StartupModule()`
registers each class and the server exposes it as `MotionTerminal.<Class>`. The headers under
`Plugins/MotionTerminal/Source/MotionTerminal/Private/` are the authoritative tool reference.

| Toolset | Tools | Role |
|---|---|---|
| `AnimGraphTools` | `Ping`, `ReadAnimGraph`, `SnapshotBaseline`, `DiffAnimGraph`, `DescribeAnimGraph` | read and verify: structural dump, the golden-baseline pair, the three-view description |
| `AnimGraphBuildTools` | `BuildAnimBlueprint`, `SaveAnimBlueprint` | an AnimGraph from a spec; saved only once accepted |
| `AnimAssetTools` | `DescribeSkeleton`, `BuildBlendSpace`, `DescribeBlendSpace`, `SaveBuiltAsset` | the animation assets scripting cannot reach |
| `AnimDebugTools` | `GetAnimDebugState`, `GetNodeWeights`, `GetNodeWeightsByGuid` + `DetachNodeWeightDebug`, `SetAnimVariable`, `GetAnimVariable` | live PIE: machine/state/montage/slot weights, the per-node weight tree, contract-variable drive and read |
| `AnimImportTools` | `SnapshotScaleBaseline`, `CheckImportScale`, `AssertImportScale`, `FixImportScale` | FBX import-scale integrity |
| `EditorSessionTools` | `StatSampleBegin`, `StatSampleRead`, `StatSampleEnd`, `GetLogFile` | what a host-side driver needs from a running editor |

`HeroPhysicsTools`, `HeroDynamicsTools` and `RenderSessionTools` are function libraries the in-editor scripts call
from Python; they are not registered as MCP toolsets.

The tool layer is thin by design. Tools parse and marshal; the logic lives in plain namespaced C++ consumed
identically by the tools and by the automation tests, so the gate and the interactive surface cannot drift apart.

## One server, one transport

```
host script ── Scripts/editor_ops.py ── Scripts/unreal_mcp.py ── HTTP ──► 127.0.0.1:8000  (the engine's MCP server,
                                                                          inside the one UnrealEditor process)
```

Host scripts never talk to the editor any other way. `editor_ops` gives them three verbs: run a Python script in the
editor and get its final value back, call a registered tool, and wait on a condition by polling from the host.
Rules it encodes, each from a failure:

- **A tool call is not a script.** `execute_python` wraps a script in an undo transaction; builders are called as
  tools so they are not nested in one.
- **Waiting is the host's job.** Play-in-editor start and stop are requests that complete on later ticks; a script
  that waits for them blocks the thread that would complete them.
- **A log read starts at a marker,** written through the editor and found in the file, never at a byte offset.
- **Shared fate with the game thread.** A modal dialog freezes the server while the editor stays alive, so the editor
  is started unattended (`Scripts/editor_session.py`) and automation never calls anything that can open one.
- **Clean quit only.** Shutdown is a request plus a process-exit poll; a force-kill leaves a stale crash reporter that
  was once blamed for a later, unrelated failure.

## Asset and content conventions

- **`/Game/MT/` is the shipped namespace** — `ABP_MT_Locomotion` (the Template), `ALI_MT_UpperBody`, `AM_MT_Attack`,
  `Lvl_MT_Test` (the slope/stairs lane), `BP_MT_TestPawn`. `/Game/Spike/` holds the first integration assets and
  `IK_Manny`, the IK Rig for the stock mannequin that hero onboarding uses as its retarget source. All reference
  Epic's mannequin content, which is not committed.
- **Stock content is never edited.** A stock asset that needs a change is duplicated into `/Game/MT` first.
- **Generated hero assets are derived, not authored.** They are deleted and rebuilt from their specs, so a result is
  reproducible rather than accreted, and they bind licensed content, so they are git-ignored; a builder checks that
  before it writes.
- **Throwaway probe assets are `Tmp_`-prefixed and deleted after use.**
- **Large or third-party binaries stay out of git:** licensed packs, retarget output, presentation levels and renders
  under `captures/`. The only images in the repository are measurement stills under `research/fixtures/`.
- **Golden baselines are versioned source:** `Plugins/MotionTerminal/Tests/Baselines/*.json`, LF/no-BOM enforced via
  `.gitattributes`. Regeneration is a reviewed, deliberate act — see `docs/verification-design.md`.

## What the plugin deliberately does not do

- **No own MCP server.** The ToolsetRegistry seam rides the engine's.
- **No wrapper where the engine has a route.** IK rigs and retargeters are built through the engine's controllers; a
  tool of ours exists only where the table above names the gap it fills.
- **No tool that vouches for itself.** A build tool's reply says what it attempted. Acceptance is always a separate
  read compared by the caller.
- **No kitchen-sink coverage.** A tool exists because an authoring step or a verification question needs it.

## Where to go next

- **How the system is proven correct** — the two-tier runner, the golden-diff gate and its blind-spot record, the PIE
  assertion architecture: `docs/verification-design.md`.
- **How the introspection works at the engine level**: `docs/introspection-mechanisms.md`.
- **How a graph is built and accepted**: `docs/specs/2026-11-12-animgraph-build-describe-design.md`.
