#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "MTAnimBudgetLibrary.generated.h"

class USkeletalMeshComponent;

/**
 * The Animation Budget Allocator calls Blueprint and Python cannot reach: per-mesh registration with a
 * significance, and a counter that shows whether the allocator actually throttles anything.
 *
 * Two engine facts this exists to get right:
 * - Registration alone throttles nothing. The allocator registers a component at significance 1 with
 *   bNeverThrottle = true (AnimationBudgetAllocator.cpp FAnimBudgetAllocatorComponentData), so a ring is
 *   only budgeted once its significance and policy are set after registering.
 * - The allocator runs only when the global switch a.Budget.Enabled is 1 AND its world is enabled
 *   (AnimationBudgetAllocator.cpp Update). EnableAnimationBudget sets only the second.
 */
UCLASS()
class MOTIONTERMINALGAME_API UMTAnimBudgetLibrary : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	/**
	 * Register Mesh with its world's allocator, then set its significance with throttling allowed.
	 * Returns false, changing nothing, when Mesh is not a USkeletalMeshComponentBudgeted or its world has no
	 * allocator (only game worlds get one: editor worlds never do).
	 */
	UFUNCTION(BlueprintCallable, Category = "MotionTerminal|AnimBudget")
	static bool RegisterBudgetedMesh(USkeletalMeshComponent* Mesh, float Significance, bool bTickEvenIfNotRendered);

	/** Hand Mesh back to its own tick and URO settings. Returns false if it was not a budgeted mesh in a game world. */
	UFUNCTION(BlueprintCallable, Category = "MotionTerminal|AnimBudget")
	static bool UnregisterBudgetedMesh(USkeletalMeshComponent* Mesh);

	/**
	 * How many times Mesh's anim graph has been updated: the proxy's UpdateCounter, which advances once per
	 * graph update and not on skipped or interpolated frames. Over a window, (delta / frames) is the mesh's
	 * true update rate. -1 when Mesh has no anim instance. The counter is an int16 that wraps; use
	 * AnimUpdateCountDelta for windows of any length under 32k updates.
	 */
	UFUNCTION(BlueprintPure, Category = "MotionTerminal|AnimBudget")
	static int32 GetAnimUpdateCount(const USkeletalMeshComponent* Mesh);

	/**
	 * Updates between two GetAnimUpdateCount readings, across the int16 wrap (which skips the -1 sentinel).
	 * -1 when either reading is -1 (no anim instance): nothing was measured. A counter RESET in the window
	 * (re-initialised instance) cannot be told from a wrap; keep windows short and spot-check a reinit.
	 */
	UFUNCTION(BlueprintPure, Category = "MotionTerminal|AnimBudget")
	static int32 AnimUpdateCountDelta(int32 Before, int32 After);
};
