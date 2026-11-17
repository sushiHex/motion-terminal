#pragma once

#include "CoreMinimal.h"
#include "Dom/JsonObject.h"

class UAnimBlueprint;

namespace MotionTerminal
{
	/** What an Animation Blueprint's AnimGraph holds, read three ways
	    (docs/specs/2026-11-12-animgraph-build-describe-design.md, section 6): the editor graph (nodes, visible pins and
	    their defaults, exposed pins, bindings), its whole link set, and, for the property paths asked for, the value
	    in the editor node beside the value in the COMPILED class. A value the editor holds and the compiled class
	    does not is the failure a read of the editor graph alone cannot see.

	    PathsById: spec node id (the node's comment tag) -> property paths inside its anim node struct ("Member",
	    "Member.Sub", "Member[2].Sub").

	    This is the reading side. It shares no code with AnimGraphOps, the writing side, so one bug cannot pass on
	    both. Keep it that way: a helper both would use is written twice. */
	TSharedRef<FJsonObject> DescribeAnimGraphJson(UAnimBlueprint& AnimBP, const TMap<FString, TArray<FString>>& PathsById);
}
