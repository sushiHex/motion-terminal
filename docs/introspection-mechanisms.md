# Per-Node Blend-Weight Introspection in UE 5.8 — the four mechanisms behind MotionTerminal's debug surface

*A write-up on engine internals. Everything below was verified against local UE 5.8 source (CL 55116800) and exercised on hardware in live PIE; file:line citations are to that engine version.*

## The problem

When an animation graph misbehaves, the question an animator asks an engineer is almost never "what state am I in?" — the runtime answers that cheaply. It's *"how much of this pose is coming from where?"* — per-node blend weights, live, on the character that's misbehaving. The editor can show this (the AnimGraph view draws animated wires when you debug an instance), but that data path is editor-UI-internal, and the common belief — echoed by the strongest MCP tooling in this space, which explicitly defers per-node weights with "no direct public getter" — is that runtime code can't get at it cleanly.

That belief is half right. There is no *single* public getter. There are **four distinct mechanisms**, each with different access, cost, and fidelity — and all four are reachable from ordinary engine-API C++ without patching the engine or subclassing around `protected` internals. MotionTerminal ships all four behind three MCP tools.

## Mechanism A — the full per-node weight tree (`GatherDebugData`)

**What it yields:** every node in the running graph, hierarchically, each with its `AbsoluteWeight` — the fully-multiplied contribution of that node to the final pose, plus per-node debug lines (play position for players, blend alphas for blends).

**The engine path:** `UAnimInstance::GatherDebugData(FNodeDebugData&)` (`AnimInstance.h:503`, `ENGINE_API`) internally grabs the game-thread proxy and recursively visits the node tree, recording weights into an `FNodeDebugData` you supply. Flatten with `FNodeDebugData::GetFlattenedDebugData()` and you have the whole tree as `{DebugLine, Indent, AbsoluteWeight}` rows.

**The lesson we kept:** our first implementation subclassed `UAnimInstance` to unlock the `protected` `GetProxyOnGameThread()` — a trampoline. It worked, byte-for-byte identically to the final version. It was also unnecessary: `GatherDebugData` is a public wrapper that performs the same proxy grab. A simplification pass deleted the trampoline and re-verified identical output. The general rule we took from it: **before reaching around an access specifier, look one level up the call stack in engine source — Epic often already shipped the public wrapper.**

This is the mechanism the ecosystem's strongest tool defers. On hardware it returned a 10-node tree for the stock ThirdPerson character — Root → ControlRig (foot IK) → Slot → two state machines with nested states → cached pose → sequence player with play time — every node weighted, live, over MCP.

## Mechanism B — machine and state weights (the double-buffered getters)

**What it yields:** per-state-machine weight and per-state weight — the structured subset an assertion suite wants, without tree-walking.

**The engine path:** `GetInstanceMachineWeight` / `GetInstanceStateWeight` (`AnimInstance.h:1111–1116`, `ENGINE_API` UFUNCTIONs). The proxy records these **every tick, unconditionally** (`AnimInstanceProxy.cpp:3239–3277`) into double-buffered arrays — so reading them costs nothing extra and needs no debug mode.

**The trap that cost us a crash:** the runtime machine index from `GetStateMachineIndex(FName)` is **not** an index into `IAnimClassInterface::GetBakedStateMachines()`. They're different index spaces. Resolve the baked machine *by name*, take state indices from the baked description, and pass the *runtime* machine index only to the weight getters. Our verification tests encode this pairing; it was learned from an editor crash when the two index spaces were mixed.

## Mechanism C — montage and slot weights (fully public, contrary to folklore)

**What it yields:** active montage weights and per-slot local weights — the layered-action half of the debugging story.

**The engine path:** `UAnimInstance::MontageInstances` (public), `FAnimMontageInstance::GetWeight()`, and `GetSlotMontageLocalWeight` — the last one is a plain `BlueprintPure`. The "no public getter" folklore is simply wrong for slots; it was never even hidden.

## Mechanism D — editor GUID-keyed per-wire weights (what the editor itself draws)

**What it yields:** the exact data the AnimGraph editor uses to animate connection wires while debugging: `{SourceID, TargetID, Weight}` visits per frame, keyed back to editor-graph nodes — which means **stable node GUIDs**, the join key between runtime behavior and *authored structure*. Mechanisms A–C tell you what the runtime is doing; D tells you *which authored node* is doing it. That join is what makes automated graph-level assertions and diff-keyed debugging possible.

**The engine path:** `FAnimBlueprintDebugData` on the generated class (`AnimBlueprintGeneratedClass.h`; `WITH_EDITORONLY_DATA`): `UpdatedNodesThisFrame` (node visits, `SourceID/TargetID/Weight`, ~lines 133–145), plus the shipped translation maps — `NodePropertyToIndexMap` (keyed by `TWeakObjectPtr<const UAnimGraphNode_Base>`, line ~189) and, crucially, the ready-made reverse map `NodePropertyIndexToNodeMap` (`int32 → TWeakObjectPtr<const UEdGraphNode>`, line ~192). The consumer proof is the editor's own drawing policy (`AnimGraphConnectionDrawingPolicy.cpp:51–100`).

**The gate:** this data populates only for the blueprint's *debugged instance*. The unlock is public — `UBlueprint::SetObjectBeingDebugged` — but it has a side effect: it commandeers the selection a human may have made in the editor's debug dropdown. MotionTerminal's tool pair treats that as a first-class contract: **attach snapshots the previous debug object; detach restores it; a detach with no snapshot is an explicit no-op** (an earlier revision restored `nullptr` in that case — clobbering the very selection the snapshot existed to protect; a later check caught it, and the fix distinguishes "restored" from "nothing to restore" in the return value).

**A small engine-truth note:** the two maps' element types differ (`UAnimGraphNode_Base` key vs `UEdGraphNode` value). Checking the design against the real header caught the first; compiling the implementation caught the second. Both were absorbed without a workaround because `UAnimGraphNode_Base` *is* a `UEdGraphNode` — `NodeGuid` and class names come along free. Rows that can't resolve (indices with no editor node, e.g. `id:-1`) are reported as-is rather than hidden.

## The composed surface

| MCP tool | Mechanisms | Typical question it answers |
|---|---|---|
| `GetAnimDebugState(actor)` | B + C | "Which state is each machine in, at what weight — and is a montage stomping a slot?" |
| `GetNodeWeights(actor)` | A | "Show me the whole live tree — where is this 30% of pose coming from?" |
| `GetNodeWeightsByGuid(actor)` / `DetachNodeWeightDebug(actor)` | D | "Tie live weights back to the *authored* nodes — the same GUIDs my topology baselines key on." |

The same mechanisms power the automated side: the PIE assertion suite drives gameplay variables and asserts machine/state weights (B) and node presence/weights (A) inside time-budgeted latent tests, headless-runnable behind one Python command. Introspection isn't a side feature here — it's the substrate the verification gate stands on.

## Why this matters beyond this project

None of the four mechanisms required engine modification, private headers, or reflection hacks. The pattern that generalizes: *the editor's debugging affordances are usually backed by data that ordinary `ENGINE_API` surface can reach — the work is locating the recording site, its gating condition (debug object, editor-only data, tick availability), and its index spaces.* The failure modes that generalize too: trusting an index across API families (B's crash), reaching for `protected` before checking for a public wrapper (A's trampoline), and side-effecting shared editor state without a restore path (D's snapshot).
