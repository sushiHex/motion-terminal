#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Character.h"
#include "MTCharacter.generated.h"

/**
 * The roster pawns' native base: an ACharacter whose Mesh is a USkeletalMeshComponentBudgeted.
 *
 * The Animation Budget Allocator only drives components of that class, and a Blueprint cannot change an
 * inherited component's class, so the crowd's far ring could never be budgeted (army_config.json far
 * `_budgeted_applied`, 0/32). Auto-registration is OFF: until UMTAnimBudgetLibrary::RegisterBudgetedMesh
 * opts a mesh in, the budgeted component takes its plain `Super` path everywhere
 * (SkeletalMeshComponentBudgeted.cpp TickComponent / SetComponentTickEnabled), so every pawn that is not
 * in a budgeted ring behaves exactly as it did on ACharacter.
 */
UCLASS()
class MOTIONTERMINALGAME_API AMTCharacter : public ACharacter
{
	GENERATED_BODY()

public:
	AMTCharacter(const FObjectInitializer& ObjectInitializer);
};
