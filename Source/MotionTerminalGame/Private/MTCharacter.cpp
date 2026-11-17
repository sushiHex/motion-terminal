#include "MTCharacter.h"

#include "SkeletalMeshComponentBudgeted.h"

AMTCharacter::AMTCharacter(const FObjectInitializer& ObjectInitializer)
	: Super(ObjectInitializer.SetDefaultSubobjectClass<USkeletalMeshComponentBudgeted>(ACharacter::MeshComponentName))
{
	if (USkeletalMeshComponentBudgeted* Budgeted = Cast<USkeletalMeshComponentBudgeted>(GetMesh()))
	{
		// Registering hands the mesh's tick rate to the allocator and switches its URO off
		// (AnimationBudgetAllocator.cpp RegisterComponent). That must be a per-ring decision, never a default.
		Budgeted->SetAutoRegisterWithBudgetAllocator(false);
	}
}
