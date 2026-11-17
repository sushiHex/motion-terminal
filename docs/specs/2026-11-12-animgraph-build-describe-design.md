# AnimGraph build and describe: design

Status: design, reviewed once (independent source review, 2026-11-12). Nothing here is built yet.
Engine: UE 5.9 Preview (build 60351404). Re-run the fixtures in section 7 on every engine update.

## 1. Goal

Author an Animation Blueprint's AnimGraph from a declarative spec with MotionTerminal's own tools, and prove
the compiled result is the spec. The first consumer is `Scripts/dynamics_graph.py`, which today places
nodes, sets properties and wires pins one call at a time.

One pattern, the one the reaction body already uses (`UHeroPhysicsTools::BuildPhysicsAsset` /
`DescribePhysicsAsset` + `hero_onboard.physics_mismatch`):

    spec (host, pure Python)  ->  Build (one call)  ->  Describe (independent read)  ->  mismatch(spec, described)

A build is accepted only when `mismatch` is empty. Build's own reply is never the evidence.

## 2. What 5.9 gives and does not give

- `UBlueprintToolset` creates nodes by localized palette text, addresses pins by index, and connects through
  the K2 schema, not `UAnimationGraphSchema`: no pose-tree rule, no space-conversion nodes. It cannot set an
  `FAnimNode_*` property or expose a Control Rig variable pin. Not used for the AnimGraph.
- GraphDsl (`Plugins/Experimental/GraphDsl`) is a public, domain-pluggable graph language with only a
  Blueprint dialect. An AnimGraph dialect is possible above the kernel in section 3, and is deferred
  (section 8): every hard correctness problem below sits in the kernel either way.
- Native and used as is: `UAnimBlueprintFactory` (skeleton, parent class), blueprint variables, compile.

## 3. Layers

1. **Kernel** `MotionTerminal::AnimGraphOps` (C++, private, no tool surface). Each operation records what it
   changed and returns a typed error; none logs-and-continues.
   - `AddNode(Graph, UClass)` through `UBlueprintNodeSpawner`, guarded by `IsCompatibleWithGraph`.
   - `Connect(A, PinA, B, PinB)` through the graph's own schema.
   - `SetProperty(Node, Path, ExportText)` and `GetProperty(Node, Path)` on the node's `Node` struct.
   - `SetPinVisible(Node, Property, bShow)`; for `UAnimGraphNode_CustomProperty`, `SetCustomPinVisibility`.
   - `AddVariableGet(Graph, Variable)`.
   - `Enumerate(Graph)`: every node, every pin, every link, every binding.
2. **Tools** `MotionTerminal.AnimGraphBuildTools` (registered toolset; called through `call_tool`, never
   through `execute_python`, which wraps a script in an undo transaction):
   - `BuildAnimBlueprint(AssetPath, SpecJson) -> json` (AIAccessMode Write)
   - `DescribeAnimBlueprint(AssetPath) -> json` (AIAccessMode Read)
3. **Host** `Scripts/animgraph_spec.py`: pure functions that make a spec and `mismatch(spec, described)`.
   Unit-tested without an editor.

## 4. Spec

    {"skeleton": "/Game/..", "parent_class": null,
     "variables": [{"name": "DynamicsAlpha", "type": "float", "default": "0.0"}],
     "nodes": [{"id": "rb", "class": "/Script/PhysicsControlUncookedOnly.AnimGraphNode_RigidBodyWithControl",
                "properties": {"OverridePhysicsAsset": "/Game/..", "CharacterSetupData.LimbSetupData": "(..)"},
                "exposed": [],            # Control Rig variables or optional pins to show
                "poses": 1}],             # LayeredBoneBlend only: number of blend poses
     "variable_gets": [{"id": "alpha", "variable": "DynamicsAlpha"}],
     "links": [["in", "Pose", "l2c", "LocalPose"], ["alpha", "DynamicsAlpha", "rig", "DynamicsAlpha"]],
     "output": "rig"}

- Node identity is the spec's `id`; the engine's node names never appear in a spec or a comparison.
- Classes, properties and pins are named by their source identifiers, never by display text.
- Property values are engine export text, imported over the node's live value (a partial struct value keeps
  its other members). `hit_reaction_core` already produces these.

## 5. Build: order and rules

Build targets only an AnimGraph that holds nothing but its root node, and refuses any other. It never
rebuilds over existing content, so it cannot delete what it does not understand. The caller deletes and
recreates the asset (the existing policy: results are reproducible, not accreted).

Order, each step failing the whole build:

1. Create the asset (factory: skeleton, parent), add variables, set their defaults.
2. Add every node.
3. Structure before values: Control Rig asset reference (the change event must name
   `ControlRigAssetReference`, or the exposed-variable table is not rebuilt); LayeredBoneBlend pose count
   through the node's own add-pose path; then pin exposure.
4. Properties. **A property with a visible pin is written to the struct and to the pin default.** The
   compiler takes an unlinked visible pin's default over the struct member (CopyBone's `bCopyTranslation`
   and `bCopyRotation` are `PinShownByDefault`: a struct-only write compiles as false). A spec that both
   sets a property and links its pin is refused.
5. Links, through the graph schema. A pose output feeds one consumer: the schema replaces an earlier link
   rather than refusing a second, so each call's success proves nothing. A spec with two consumers of one
   pose output is refused before any link is made.
6. Compare the graph's full link set with the spec's (extra links and inserted conversion nodes are
   differences), then compile. Any compiler error, and a false compile result, fail the build.
7. Build never saves. The host describes the in-memory asset, and only a description that matches the spec is
   saved (`SaveAnimBlueprint`); a graph that failed or does not match never reaches disk, and the host deletes it.

## 6. Describe: three views

**Describe shares no code with the kernel.** It is the existing independent reader (`AnimGraphTopology`,
behind `ReadAnimGraph` and the golden-diff gate) extended to carry what it lacks today: the full pin-edge
set, pin defaults, requested property values and the compiled view. The thing that checks a graph must not
be the thing that wrote it, or one bug passes on both sides; that rule predates this design
(`docs/architecture.md`) and authoring moving in-house is exactly when it starts to matter. The same
extension makes the golden baselines see wiring and settings, which they do not today.

For every node, by `id` where the build recorded it (a node-comment tag) and by class otherwise:

- **editor**: class, exposed pins, bindings, each requested property path, each visible pin's default.
- **links**: the whole edge set, by pin name.
- **compiled**: whether the node is in the generated class at all (the compiler prunes what the output does not
  reach), and the same property paths read from the class default object's anim node.

Values come back as typed JSON with every struct member present, not as export text: the engine's text export
drops members equal to their defaults, so a reader of it cannot tell a default from a missing value, and typed
values let "01" be a name or a number as the engine says. Variable defaults are read from the class default
object, where a compile leaves them.

Two limits, both reported rather than hidden:

- A member the compiler folds (`FoldProperty`, e.g. SequenceEvaluator's `Sequence`) is not in the node struct, and
  the engine's accessors for folded data are not public. Describe reports it unread with a reason starting
  "folded constant"; `mismatch` fails on it unless the caller names that node and path, and no other unread
  reason can be waived.
- A Control Rig's exposed variables compile to generated properties and copy handlers outside the node struct.
  Their links are checked in the editor graph only; whether the value arrives is a runtime check.

For a member with an unlinked visible pin, the pin default and the compiled value are the evidence (showing a pin
can move the value out of the editor struct). `mismatch` compares the spec with all three views, and also fails
on anything the spec does not declare: a node, a link, a variable, a binding, a different parent class. A value the editor holds and the compiled class does not is a
failure, which is the case a text read-back cannot see. Runtime-only failures stay outside it and keep their
own checks: a RigidBodyWithControl update naming a control the physics asset lacks only logs a warning, so
`physics_mismatch` against the built asset remains a precondition.

## 7. Acceptance (first editor session)

Fixture ABP, default test tier, no PIE: CopyBone with both flags true; SequenceEvaluator with a sequence;
LayeredBoneBlend with two layers; RigidBodyWithControl with nested and sparse values (a 1.0 multiplier and
the enable flags included); Control Rig with an exposed variable linked from a variable get.

| Gate | Passes when |
|---|---|
| Fidelity | `mismatch` is empty on editor, links and compiled views |
| Repeatable | delete + build twice gives identical descriptions |
| Refusals | non-empty graph, unknown class, unknown property, unknown pin, two consumers of one pose, property set and linked: each fails with its reason and leaves no saved asset |
| Compiled view | at least the CopyBone flags and one RigidBodyWithControl value read back from the generated class |

If the compiled view cannot be read for folded constants, that gate is replaced by a PIE probe of the same
values, and this document is corrected; the build does not ship on the editor view alone.

Then `dynamics_graph.py` moves onto it and the dynamics gate (`dyn_run --gate`) is the end-to-end
proof against the 5.8 baselines.

## 8. Deferred: a GraphDsl AnimGraph dialect

Worth a bounded spike after this ships, above the same kernel. Known obstacles from the source review:

- The service's rollback finds its transaction by context; nested under an outer transaction it reports a
  rollback it did not perform (`GraphDslTransaction.cpp:81-84`).
- Edit sessions are keyed by asset and scope only, so the Blueprint dialect and an AnimGraph dialect on one
  asset share them.
- A struct form resets omitted members to defaults, unlike export-text import.
- The canonical text describes the editor graph, not the compiled class; section 6 is still needed.
- State machines need container and rule-graph semantics that the first version would have to refuse.
