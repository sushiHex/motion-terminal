#include "MTAnimBudgetLibrary.h"

#include "Animation/AnimInstance.h"
#include "Components/SkeletalMeshComponent.h"
#include "IAnimationBudgetAllocator.h"
#include "SkeletalMeshComponentBudgeted.h"

namespace
{
	IAnimationBudgetAllocator* AllocatorFor(USkeletalMeshComponentBudgeted* Budgeted)
	{
		UWorld* World = Budgeted ? Budgeted->GetWorld() : nullptr;
		return World ? IAnimationBudgetAllocator::Get(World) : nullptr;
	}
}

bool UMTAnimBudgetLibrary::RegisterBudgetedMesh(USkeletalMeshComponent* Mesh, float Significance, bool bTickEvenIfNotRendered)
{
	USkeletalMeshComponentBudgeted* Budgeted = Cast<USkeletalMeshComponentBudgeted>(Mesh);
	IAnimationBudgetAllocator* Allocator = AllocatorFor(Budgeted);
	if (!Allocator)
	{
		return false;
	}
	Allocator->RegisterComponent(Budgeted);
	// The component's own setter, not the allocator's: it routes through the pointer RegisterComponent just set,
	// so a registration that did not take logs a warning instead of silently writing nothing.
	Budgeted->SetComponentSignificance(Significance, /*bNeverSkip=*/false, bTickEvenIfNotRendered,
		/*bAllowReducedWork=*/true, /*bForceInterpolate (lands in bNeverThrottle)=*/false);
	return true;
}

bool UMTAnimBudgetLibrary::UnregisterBudgetedMesh(USkeletalMeshComponent* Mesh)
{
	USkeletalMeshComponentBudgeted* Budgeted = Cast<USkeletalMeshComponentBudgeted>(Mesh);
	IAnimationBudgetAllocator* Allocator = AllocatorFor(Budgeted);
	if (!Allocator)
	{
		return false;
	}
	Allocator->UnregisterComponent(Budgeted);
	return true;
}

int32 UMTAnimBudgetLibrary::GetAnimUpdateCount(const USkeletalMeshComponent* Mesh)
{
	const UAnimInstance* Anim = Mesh ? Mesh->GetAnimInstance() : nullptr;
	return Anim ? static_cast<int32>(Anim->GetUpdateCounter().Get()) : -1;
}

int32 UMTAnimBudgetLibrary::AnimUpdateCountDelta(int32 Before, int32 After)
{
	// -1 is GetAnimUpdateCount's "no anim instance", and also the counter's own never-traversed value. A window
	// with it at either end measured nothing; returning a delta would read a vanished mesh as 65k updates.
	if (Before == INDEX_NONE || After == INDEX_NONE)
	{
		return INDEX_NONE;
	}
	// FGraphTraversalCounter::Increment wraps int16 and skips INDEX_NONE (-1): the sequence is
	// ... 32767, -32768, ..., -2, 0, 1, ... — a cycle of 65535 values. Map each to its place in that cycle.
	auto Position = [](int32 V) { return V < -1 ? V + 32768 : V + 32767; };
	constexpr int32 Cycle = 65535;
	return ((Position(After) - Position(Before)) % Cycle + Cycle) % Cycle;
}
