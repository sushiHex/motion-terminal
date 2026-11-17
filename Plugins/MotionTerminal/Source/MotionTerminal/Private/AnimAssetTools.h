#pragma once

#include "ToolsetRegistry/ToolsetDefinition.h"
#include "AnimAssetTools.generated.h"

/** Animation assets the engine gives editor scripting no way to read or build: a skeleton's bone tree with each bone's
    translation-retargeting mode, and blend spaces (UBlendSpace has no script-callable function at all). Same pattern
    as the other builders: a build leaves the asset in memory, a describe reads it back, the caller compares, and
    only then saves. Every tool returns JSON {"ok", ...} or {"ok": false, "error"}. */
UCLASS()
class UAnimAssetTools : public UToolsetDefinition
{
	GENERATED_BODY()

public:
	/** A skeleton's reference bones, in index order: {"path", "bones": [{"name", "index", "parent" ("" for the
	    root), "translation_retargeting": Animation|Skeleton|AnimationScaled|AnimationRelative|OrientAndScale}]}.
	    The skeleton's own reference skeleton, not a mesh's. */
	UFUNCTION(meta = (AICallable, AIAccessMode = "Read"))
	static FString DescribeSkeleton(const FString& SkeletonPath);

	/** Creates a 2D blend space at AssetPath ("/Game/Dir/Name", which must not exist) from SpecJson: {"skeleton":
	    path, "axis_x": {"name", "min", "max", "grid_divisions", "snap_to_grid", "wrap_input"}, "axis_y": {same},
	    "interpolate_using_grid" (optional), "samples": [{"animation": path, "x", "y", "rate_scale" (optional)}]}. Every sample must lie inside the axes
	    and be accepted, and the interpolation data built, or it fails. The asset is left in memory, NOT saved: describe it, compare, then SaveBuiltAsset. */
	UFUNCTION(meta = (AICallable, AIAccessMode = "Write"))
	static FString BuildBlendSpace(const FString& AssetPath, const FString& SpecJson);

	/** A blend space as built or loaded: {"path", "skeleton", "axis_x", "axis_y" (the spec's keys), "samples":
	    [{"animation", "x", "y", "valid", "rate_scale"}], "interpolate_using_grid", "triangulation_baked"}. */
	UFUNCTION(meta = (AICallable, AIAccessMode = "Read"))
	static FString DescribeBlendSpace(const FString& AssetPath);

	/** Saves an asset a Build tool left in memory, once its description was accepted. Refuses an asset that is not
	    in memory (it never loads one: an older file must not be saved over what was meant). Callable from in-editor
	    Python too: it is the scripts' one save, because it renders no thumbnail (the editor's save does, which runs an
	    Animation Blueprint's event graph in a world with no pawn). */
	UFUNCTION(BlueprintCallable, Category = "MotionTerminal|Assets", meta = (AICallable, AIAccessMode = "Write"))
	static FString SaveBuiltAsset(const FString& AssetPath);
};
