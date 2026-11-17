#pragma once

#include "CoreMinimal.h"
#include "Dom/JsonObject.h"

class UAnimBlueprint;
class UAnimGraphNode_Base;

namespace MotionTerminal
{
	/** Every COMPILE-TIME pin binding on an anim node, as pinName -> dotted property path.
	    Property-access bindings live on the node's instanced binding object, in a
	    private-but-UPROPERTY TMap named "PropertyBindings", so this reaches them purely by
	    reflection — never needing the Internal-only UAnimGraphNodeBinding type to be complete.
	    Only pins with bIsBound are reported; the map is empty when nothing is bound.

	    Shared rather than inlined at each caller BECAUSE OF WHAT IT GUARDS. This exact walk is
	    how the golden baseline records bindings AND how Verify.RifleGateWired detects a
	    re-introduced binding on a TArray<float> element — the miswiring that silently failed to
	    compile and left a gate stuck on. Two copies would mean a future engine change to the
	    binding layout has to be found twice. */
	void GetNodePropertyBindings(const UAnimGraphNode_Base& Node, TMap<FString, FString>& OutBindings);

	/** Canonical topology of an AnimBlueprint's AnimGraph as JSON: graphs -> nodes
	    (class/title/asset/transition edges) -> nested sub-graphs. Node arrays are sorted
	    by their serialized form, so output is deterministic and diff-stable. */
	TSharedRef<FJsonObject> BuildTopologyJson(const UAnimBlueprint& AnimBP);

	/** Deterministic pretty-printed JSON (insertion-ordered fields, sorted node arrays). */
	FString TopologyToString(const TSharedRef<FJsonObject>& Topology);

	/** Parse previously serialized topology text. Returns false on malformed JSON. */
	bool ParseTopology(const FString& JsonText, TSharedPtr<FJsonObject>& OutObject);

	/** Line-level diff of two serialized topologies.
	    Empty string == identical after EOL normalization (\r\n -> \n), so git's autocrlf
	    rewriting can never trip the gate. Otherwise, if the line multisets differ, lines
	    only in Baseline are prefixed "- " and only in Current "+ ". If the multisets are
	    equal but the (normalized) order differs — a pure reordering/move — returns the
	    single marker line "~ lines reordered/moved (same line multiset)". */
	FString DiffTopologyText(const FString& BaselineText, const FString& CurrentText);

	/** <project>/Plugins/MotionTerminal/Tests/Baselines */
	FString BaselineDir();
	/** BaselineDir()/<BaselineName>.json (".json" appended if absent). */
	FString BaselinePath(const FString& BaselineName);
}
