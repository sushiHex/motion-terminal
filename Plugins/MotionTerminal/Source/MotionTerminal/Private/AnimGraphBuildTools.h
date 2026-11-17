#pragma once

#include "ToolsetRegistry/ToolsetDefinition.h"
#include "AnimGraphBuildTools.generated.h"

/** Builds an Animation Blueprint's AnimGraph from a declarative spec
    (docs/specs/2026-11-12-animgraph-build-describe-design.md). What it built is checked by a different tool with
    different code: AnimGraphTools.DescribeAnimGraph. */
UCLASS()
class UAnimGraphBuildTools : public UToolsetDefinition
{
	GENERATED_BODY()

public:
	/** Creates the Animation Blueprint at AssetPath ("/Game/Dir/Name", which must not exist) from SpecJson and
	    compiles it. It is NOT saved: describe it, compare, then SaveAnimBlueprint. Spec: {"skeleton": path, "parent_class": path or "", "variables": [{"name", "type":
	    float|bool|int, "default"}], "nodes": [{"id", "class": "/Script/Module.AnimGraphNode_X", "poses": n
	    (LayeredBoneBlend), "properties": [[path, export text], ...] in the order to apply, "exposed": [property]}],
	    "variable_gets": [{"id", "variable"}], "links": [[from id, output pin, to id, input pin]], "output": [id,
	    pose output pin]}. Returns JSON {"ok", "path", "saved": false, "nodes": {id: engine node name}, "warnings"}
	    or {"ok": false, "error", "created"}. Either way the asset is in memory and unsaved, for the caller to save
	    or delete. A reply of ok is not the proof of the build; the description is. */
	UFUNCTION(meta = (AICallable, AIAccessMode = "Write"))
	static FString BuildAnimBlueprint(const FString& AssetPath, const FString& SpecJson);

	/** Saves an Animation Blueprint that BuildAnimBlueprint left in memory, once its description was accepted.
	    Refuses one that is not in memory or not compiled. Returns JSON {"ok", "path", "saved"}. */
	UFUNCTION(meta = (AICallable, AIAccessMode = "Write"))
	static FString SaveAnimBlueprint(const FString& AssetPath);
};
