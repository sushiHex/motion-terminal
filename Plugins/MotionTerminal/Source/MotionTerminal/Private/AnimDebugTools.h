#pragma once

#include "ToolsetRegistry/ToolsetDefinition.h"
#include "AnimDebugTools.generated.h"

/** Live-PIE animation debugging: state/montage/slot weights and per-node blend weights. */
UCLASS()
class UAnimDebugTools : public UToolsetDefinition
{
	GENERATED_BODY()

public:
	/** For a PIE actor (outliner label or object name): each state machine's active states and
	    weights, plus active montages and slot weights. */
	UFUNCTION(meta = (AICallable, AIAccessMode = "Read"))
	static FString GetAnimDebugState(const FString& ActorLabel);

	/** Per-node blend weights (AbsoluteWeight tree) for a PIE actor's AnimInstance. */
	UFUNCTION(meta = (AICallable, AIAccessMode = "Read"))
	static FString GetNodeWeights(const FString& ActorLabel);

	/** Mechanism D: editor GUID-keyed per-node weights from FAnimBlueprintDebugData::UpdatedNodesThisFrame.
	    Requires the instance to be the blueprint's debug object — the first call snapshots the
	    previous debug object, attaches this one (SetObjectBeingDebugged), and returns "attached";
	    call again on a later frame for data. Side effect: overrides the editor's debug-object
	    selection until DetachNodeWeightDebug restores it. */
	UFUNCTION(meta = (AICallable, AIAccessMode = "Read"))
	static FString GetNodeWeightsByGuid(const FString& ActorLabel);

	/** Restores the debug object snapshotted by GetNodeWeightsByGuid (Mechanism D snapshot/restore). */
	UFUNCTION(meta = (AICallable))
	static FString DetachNodeWeightDebug(const FString& ActorLabel);

	/** Sets a float/double/bool/int variable on a PIE actor's AnimInstance via reflection.
	    Value: number or "true"/"false". */
	UFUNCTION(meta = (AICallable))
	static FString SetAnimVariable(const FString& ActorLabel, const FString& VariableName, const FString& Value);

	/** Reads a float/double/bool/int variable on a PIE actor's AnimInstance via reflection
	    (symmetric read-side of SetAnimVariable — proves contract resets, e.g. a trigger var
	    consumed back to its rest value, without a Python PIE-world lookup). */
	UFUNCTION(meta = (AICallable, AIAccessMode = "Read"))
	static FString GetAnimVariable(const FString& ActorLabel, const FString& VariableName);
};
