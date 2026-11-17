# Topology baseline format — schemaVersion 2 (binding capture)

*Design note for the binding-capture schema bump. Closes the known gate blindness: v1 captures node presence + transition edges only, so stripping every clip binding passes both tiers.*

## Format delta (v1 → v2)

1. Root: `"schemaVersion": 2` (first field, unchanged position).
2. Each node object MAY gain one field, emitted ONLY when non-empty, placed after `"asset"`:
   `"bindings": { "<PinName>": "<PropertyPath>", ... }`
   - Source (IMPLEMENTED — a check of the 5.8 engine headers invalidated this note's original premise): in 5.8 `UAnimGraphNode_Base::PropertyBindings` is `PropertyBindings_DEPRECATED` (private, deprecated). Bindings moved to an instanced binding object: `UAnimGraphNode_Base::GetBinding()` → a `UAnimGraphNodeBinding` whose concrete `UAnimGraphNodeBinding_Base` (in a Private engine header) holds `TMap<FName, FAnimGraphNodePropertyBinding> PropertyBindings`. No public enumeration API exists, and `GetBinding()`'s return type is forward-declared-only (so `GetBinding()->GetClass()` won't compile). Shipped approach (`AnimGraphTopology.cpp` `NodeToJson`): pure UObject reflection — find the node's `Binding` `FObjectProperty`, get the object, find its `PropertyBindings` `FMapProperty` (reflection reaches the private-but-`UPROPERTY` member), guard `MapProp->ValueProp` is an `FStructProperty` over `FAnimGraphNodePropertyBinding::StaticStruct()`, then read each value as the PUBLIC `FAnimGraphNodePropertyBinding` (`PropertyName`/`PropertyPath`/`bIsBound`; filter `bIsBound`). No Internal/Private engine header needed. Emit `PinName → PropertyPath` joined with `.` if multi-segment.
   - **Canonicalization:** keys sorted lexically (case-sensitive) before emission; values verbatim.
3. Nothing else changes: node sort key remains the condensed serialization of the WHOLE node object (bindings now participate — this is what makes binding changes diff-visible), diff semantics unchanged (v2 lines are just more lines).

## Migration

- Bump is atomic with a both-baseline regen in ONE reviewed commit (SHIPPED): `abp_unarmed` (delta = version line only — confirmed no bindings in stock ABP_Unarmed players) and `abp_mt_locomotion` (delta = version line + 7 binding lines, one MORE than the original estimate: the 5 clip `Sequence` overrides `Anim_Fall/Idle/Jump/Land`, plus the BlendSpace player's `BlendSpace ← BS_Locomotion` AND `X ← Speed` — the estimate assumed 5 clips + X only; the dynamic BlendSpace asset-pin binding is real and captured honestly).
- No dual-version support: the gate compares text; old baselines simply fail with the version line in the diff — the baseline-regen discipline (`docs/verification-design.md`) is the migration path. `ParseTopology` untouched (schema-agnostic JSON).

## Non-goals (v2)

Pin-to-pin WIRING capture (pose-graph edges) stays out — cached-pose fan-out visibility comes free at node level (SaveCachedPose/UseCachedPose are nodes). Full edge capture is a future v3 if a gate gap ever demands it; the `ReadAnimGraph` audit is the compensating control for wiring **in the AnimGraph (`FunctionGraphs`) only**.

**Known blind spot (engine-verified):** interface **layer graphs** (`AnimLayerInterface` implementations, e.g. `UpperBodyLayer`) live in `FBPInterfaceDescription::Graphs`, NOT `FunctionGraphs` — which BOTH `BuildTopologyJson` (`AnimGraphTopology.cpp` iterates `FunctionGraphs`) and `ReadAnimGraph` iterate. So a layer graph's internal wiring (e.g. the `InPose → Output` passthrough) is invisible to the golden baseline, to `ReadAnimGraph`, and to every PIE assertion: a manual editor disconnect of that passthrough would leave all tests green while the layer's overlay silently ref-poses. This is currently a manual-UI-only regression vector (no shipped tool can author inside that graph), so it does not block the v2 schema — but it is a real gap. Future control: a layer-graph-authoring/-reading op added to the project's own toolset (which would also let a future capture reach `FBPInterfaceDescription::Graphs`).

## Acceptance

`TopologyRoundTrip` still green (self-diff empty); deliberate unbind of one clip pin → `GoldenDiff.MTLocomotion` fails showing the removed `bindings` line → rebind → green. That negative check is the point of the whole change — run it.
