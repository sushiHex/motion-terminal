#pragma once

#include "ToolsetRegistry/ToolsetDefinition.h"
#include "AnimGraphTools.generated.h"

/** Read/verify tools for Animation Blueprint AnimGraph state machines. */
UCLASS()
class UAnimGraphTools : public UToolsetDefinition
{
	GENERATED_BODY()

public:
	/** Connectivity check. Returns "pong" plus the running engine version. */
	UFUNCTION(meta = (AICallable, AIAccessMode = "Read"))
	static FString Ping();

	/** Dumps the structure of an Animation Blueprint's AnimGraph: state machines, states,
	    transitions, asset players, layering/slot/IK nodes, and wiring. Path e.g. "/Game/Folder/ABP_Name". */
	UFUNCTION(meta = (AICallable, AIAccessMode = "Read"))
	static FString ReadAnimGraph(const FString& AnimBlueprintPath);

	/** Serializes the AnimGraph topology to Plugins/MotionTerminal/Tests/Baselines/<baselineName>.json.
	    Regenerating an existing baseline is deliberate (docs/verification-design.md) — review the diff like code. */
	UFUNCTION(meta = (AICallable, AIAccessMode = "Write"))
	static FString SnapshotBaseline(const FString& AnimBlueprintPath, const FString& BaselineName);

	/** Diffs the AnimBlueprint's current AnimGraph topology against a stored baseline.
	    Returns "IDENTICAL" or a line diff ("- " = baseline-only, "+ " = current-only). */
	UFUNCTION(meta = (AICallable, AIAccessMode = "Read"))
	static FString DiffAnimGraph(const FString& AnimBlueprintPath, const FString& BaselineName);

	/** Describes an Animation Blueprint's AnimGraph as JSON: every node (id = its comment tag, class, visible pins
	    with their defaults, bindings), the whole link set as [from, pin, to, pin], the variables, and whether the
	    last compile was clean. PathsJson, optional, is {"node id": ["Member", "Member.Sub", "Member[2].Sub"]}: for
	    those property paths each node also reports "editor" (the graph node's value) beside "compiled" (the value in
	    the compiled class), and "unread" names any it could not read. This is the read that proves a build; it
	    shares no code with the tool that builds. */
	UFUNCTION(meta = (AICallable, AIAccessMode = "Read"))
	static FString DescribeAnimGraph(const FString& AnimBlueprintPath, const FString& PathsJson);
};
