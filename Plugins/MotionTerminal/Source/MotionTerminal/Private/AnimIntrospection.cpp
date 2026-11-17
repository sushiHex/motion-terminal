#include "AnimIntrospection.h"

#include "Animation/AnimBlueprintGeneratedClass.h"
#include "Animation/AnimClassInterface.h"
#include "Animation/AnimInstance.h"
#include "Animation/AnimMontage.h"
#include "Animation/AnimNodeBase.h"
#include "Components/SkeletalMeshComponent.h"
#include "Editor.h"
#include "EngineUtils.h"
#include "GameFramework/Actor.h"

namespace MotionTerminal
{

UAnimInstance* FindAnimInstanceByLabel(const FString& ActorLabel, FString& OutError)
{
	FWorldContext* PieContext = GEditor ? GEditor->GetPIEWorldContext() : nullptr;
	UWorld* World = PieContext ? PieContext->World() : nullptr;
	if (!World) { OutError = TEXT("no PIE world (start PIE first)"); return nullptr; }

	TArray<FString> Candidates;
	for (TActorIterator<AActor> It(World); It; ++It)
	{
		USkeletalMeshComponent* Mesh = It->FindComponentByClass<USkeletalMeshComponent>();
		if (!Mesh) { continue; }
		if (It->GetActorLabel() == ActorLabel || It->GetName() == ActorLabel)
		{
			if (UAnimInstance* Inst = Mesh->GetAnimInstance()) { return Inst; }
			OutError = FString::Printf(TEXT("'%s' has a mesh but no AnimInstance"), *ActorLabel);
			return nullptr;
		}
		Candidates.Add(It->GetName());
	}
	OutError = FString::Printf(TEXT("no PIE actor '%s' with a SkeletalMeshComponent. Candidates: %s"),
		*ActorLabel, *FString::Join(Candidates, TEXT(", ")));
	return nullptr;
}

void AppendMachineStates(UAnimInstance& Inst, FStringBuilderBase& Out)
{
	// Mechanism B: baked machine descriptions + every-tick recorded weights.
	IAnimClassInterface* AnimClass = IAnimClassInterface::GetFromClass(Inst.GetClass());
	if (!AnimClass) { Out.Append(TEXT("machines: none (no IAnimClassInterface)\n")); return; }

	const TArray<FBakedAnimationStateMachine>& Machines = AnimClass->GetBakedStateMachines();
	Out.Appendf(TEXT("machines: %d\n"), Machines.Num());
	for (const FBakedAnimationStateMachine& Machine : Machines)
	{
		const int32 MachineIndex = Inst.GetStateMachineIndex(Machine.MachineName);
		const float MachineWeight = (MachineIndex != INDEX_NONE)
			? Inst.GetInstanceMachineWeight(MachineIndex) : 0.f;
		Out.Appendf(TEXT("machine '%s' (index %d, weight %.3f):\n"),
			*Machine.MachineName.ToString(), MachineIndex, MachineWeight);
		for (int32 StateIdx = 0; StateIdx < Machine.States.Num(); ++StateIdx)
		{
			const float StateWeight = (MachineIndex != INDEX_NONE)
				? Inst.GetInstanceStateWeight(MachineIndex, StateIdx) : 0.f;
			if (StateWeight > KINDA_SMALL_NUMBER)
			{
				Out.Appendf(TEXT("  state '%s' weight %.3f\n"),
					*Machine.States[StateIdx].StateName.ToString(), StateWeight);
			}
		}
	}
}

void AppendMontagesAndSlots(UAnimInstance& Inst, FStringBuilderBase& Out)
{
	// Mechanism C: public montage instances + slot weight getters.
	if (Inst.MontageInstances.Num() == 0) { Out.Append(TEXT("montages: none\n")); return; }
	Out.Appendf(TEXT("montages: %d\n"), Inst.MontageInstances.Num());
	for (const FAnimMontageInstance* MontageInst : Inst.MontageInstances)
	{
		if (!MontageInst || !MontageInst->Montage) { continue; }
		Out.Appendf(TEXT("  montage '%s' weight %.3f desired %.3f\n"),
			*MontageInst->Montage->GetName(), MontageInst->GetWeight(), MontageInst->GetDesiredWeight());
		for (const FSlotAnimationTrack& Track : MontageInst->Montage->SlotAnimTracks)
		{
			Out.Appendf(TEXT("    slot '%s' local weight %.3f\n"),
				*Track.SlotName.ToString(), Inst.GetSlotMontageLocalWeight(Track.SlotName));
		}
	}
}

TArray<FNodeDebugData::FFlattenedDebugData> GatherFlatNodeWeights(UAnimInstance& Inst)
{
	// UAnimInstance::GatherDebugData is the public entry point that internally grabs the
	// game-thread proxy and walks the node tree — no protected-access workaround needed.
	FNodeDebugData DebugData(&Inst);
	Inst.GatherDebugData(DebugData);
	return DebugData.GetFlattenedDebugData();
}

FString FormatFlatNodeWeights(const TArray<FNodeDebugData::FFlattenedDebugData>& Flat)
{
	FString Out = FString::Printf(TEXT("nodes: %d\n"), Flat.Num());
	for (const FNodeDebugData::FFlattenedDebugData& Item : Flat)
	{
		Out += FString::Printf(TEXT("%*s%s (weight %.3f)\n"),
			Item.Indent * 2, TEXT(""), *Item.DebugLine, Item.AbsoluteWeight);
	}
	return Out;
}

void AppendNodeWeights(UAnimInstance& Inst, FStringBuilderBase& Out)
{
	Out.Append(FormatFlatNodeWeights(GatherFlatNodeWeights(Inst)));
}

} // namespace MotionTerminal
