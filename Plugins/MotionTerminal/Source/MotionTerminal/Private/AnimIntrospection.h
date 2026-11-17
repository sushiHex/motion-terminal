#pragma once

#include "CoreMinimal.h"
#include "Animation/AnimNodeBase.h"   // FNodeDebugData::FFlattenedDebugData (nested, so not forward-declarable)

class UAnimInstance;

namespace MotionTerminal
{
	/** Finds a PIE actor's AnimInstance by outliner label or object name.
	    On failure, OutError lists candidate actors that have skeletal meshes. */
	UAnimInstance* FindAnimInstanceByLabel(const FString& ActorLabel, FString& OutError);

	/** Mechanism B: per-machine weight + per-state weights via public UFUNCTIONs. */
	void AppendMachineStates(UAnimInstance& Inst, FStringBuilderBase& Out);

	/** Mechanism C: active montages + weights + slot weights. */
	void AppendMontagesAndSlots(UAnimInstance& Inst, FStringBuilderBase& Out);

	/** Mechanism A: the flattened per-node AbsoluteWeight tree via the public
	    UAnimInstance::GatherDebugData. Under a real RHI this walks a LIVE evaluated graph. */
	TArray<FNodeDebugData::FFlattenedDebugData> GatherFlatNodeWeights(UAnimInstance& Inst);

	/** The full indented dump of a flattened tree: "nodes: N" then one "(weight W)" line per node. */
	FString FormatFlatNodeWeights(const TArray<FNodeDebugData::FFlattenedDebugData>& Flat);

	/** Mechanism A: full per-node AbsoluteWeight tree via the public
	    UAnimInstance::GatherDebugData. BONUS — the per-node weights. */
	void AppendNodeWeights(UAnimInstance& Inst, FStringBuilderBase& Out);
}
