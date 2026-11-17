#include "AnimDebugTools.h"

#include "AnimIntrospection.h"
#include "Animation/AnimInstance.h"
#include "Animation/AnimBlueprint.h"
#include "Animation/AnimBlueprintGeneratedClass.h"
#include "EdGraph/EdGraphNode.h"

FString UAnimDebugTools::GetAnimDebugState(const FString& ActorLabel)
{
	FString Error;
	UAnimInstance* Inst = MotionTerminal::FindAnimInstanceByLabel(ActorLabel, Error);
	if (!Inst) { return FString::Printf(TEXT("ERROR: %s"), *Error); }

	TStringBuilder<4096> Out;
	Out.Appendf(TEXT("AnimInstance: %s\n"), *Inst->GetClass()->GetPathName());
	MotionTerminal::AppendMachineStates(*Inst, Out);
	MotionTerminal::AppendMontagesAndSlots(*Inst, Out);
	return Out.ToString();
}

FString UAnimDebugTools::GetNodeWeights(const FString& ActorLabel)
{
	FString Error;
	UAnimInstance* Inst = MotionTerminal::FindAnimInstanceByLabel(ActorLabel, Error);
	if (!Inst) { return FString::Printf(TEXT("ERROR: %s"), *Error); }

	TStringBuilder<8192> Out;
	Out.Appendf(TEXT("AnimInstance: %s\n"), *Inst->GetClass()->GetPathName());
	MotionTerminal::AppendNodeWeights(*Inst, Out);
	return Out.ToString();
}

FString UAnimDebugTools::SetAnimVariable(const FString& ActorLabel, const FString& VariableName, const FString& Value)
{
	FString Error;
	UAnimInstance* Inst = MotionTerminal::FindAnimInstanceByLabel(ActorLabel, Error);
	if (!Inst) { return FString::Printf(TEXT("ERROR: %s"), *Error); }

	FProperty* Prop = Inst->GetClass()->FindPropertyByName(FName(*VariableName));
	if (!Prop) { return FString::Printf(TEXT("ERROR: no property '%s'"), *VariableName); }

	if (const FDoubleProperty* DoubleProp = CastField<FDoubleProperty>(Prop))
	{
		DoubleProp->SetPropertyValue_InContainer(Inst, FCString::Atod(*Value));
	}
	else if (const FFloatProperty* FloatProp = CastField<FFloatProperty>(Prop))
	{
		FloatProp->SetPropertyValue_InContainer(Inst, FCString::Atof(*Value));
	}
	else if (const FBoolProperty* BoolProp = CastField<FBoolProperty>(Prop))
	{
		BoolProp->SetPropertyValue_InContainer(Inst, Value.ToBool());
	}
	else if (const FIntProperty* IntProp = CastField<FIntProperty>(Prop))
	{
		IntProp->SetPropertyValue_InContainer(Inst, FCString::Atoi(*Value));
	}
	else
	{
		return FString::Printf(TEXT("ERROR: '%s' is not float/double/bool/int"), *VariableName);
	}
	return FString::Printf(TEXT("ok: %s = %s"), *VariableName, *Value);
}

FString UAnimDebugTools::GetAnimVariable(const FString& ActorLabel, const FString& VariableName)
{
	FString Error;
	UAnimInstance* Inst = MotionTerminal::FindAnimInstanceByLabel(ActorLabel, Error);
	if (!Inst) { return FString::Printf(TEXT("ERROR: %s"), *Error); }

	FProperty* Prop = Inst->GetClass()->FindPropertyByName(FName(*VariableName));
	if (!Prop) { return FString::Printf(TEXT("ERROR: no property '%s'"), *VariableName); }

	if (const FDoubleProperty* DoubleProp = CastField<FDoubleProperty>(Prop))
	{
		return FString::Printf(TEXT("%s = %f"), *VariableName, DoubleProp->GetPropertyValue_InContainer(Inst));
	}
	if (const FFloatProperty* FloatProp = CastField<FFloatProperty>(Prop))
	{
		return FString::Printf(TEXT("%s = %f"), *VariableName, FloatProp->GetPropertyValue_InContainer(Inst));
	}
	if (const FBoolProperty* BoolProp = CastField<FBoolProperty>(Prop))
	{
		return FString::Printf(TEXT("%s = %s"), *VariableName, BoolProp->GetPropertyValue_InContainer(Inst) ? TEXT("true") : TEXT("false"));
	}
	if (const FIntProperty* IntProp = CastField<FIntProperty>(Prop))
	{
		return FString::Printf(TEXT("%s = %d"), *VariableName, IntProp->GetPropertyValue_InContainer(Inst));
	}
	return FString::Printf(TEXT("ERROR: '%s' is not float/double/bool/int"), *VariableName);
}

#if WITH_EDITORONLY_DATA
namespace
{
	// Mechanism D snapshot/restore: what each AnimBlueprint's debug object was before we
	// attached, so DetachNodeWeightDebug can put it back. SetObjectBeingDebugged persists in the
	// blueprint otherwise — it would silently clobber a human's in-editor debug selection.
	TMap<TWeakObjectPtr<UAnimBlueprint>, TWeakObjectPtr<UObject>> GPreviousDebugObjects;

	UAnimBlueprint* ResolveAnimBlueprint(const FString& ActorLabel, UAnimInstance*& OutInst, FString& OutError)
	{
		OutInst = MotionTerminal::FindAnimInstanceByLabel(ActorLabel, OutError);
		if (!OutInst) { return nullptr; }
		UAnimBlueprintGeneratedClass* GenClass = Cast<UAnimBlueprintGeneratedClass>(OutInst->GetClass());
		if (!GenClass) { OutError = TEXT("instance class is not a UAnimBlueprintGeneratedClass"); return nullptr; }
		UAnimBlueprint* AnimBP = Cast<UAnimBlueprint>(GenClass->ClassGeneratedBy);
		if (!AnimBP) { OutError = TEXT("no generating AnimBlueprint (editor data stripped?)"); }
		return AnimBP;
	}
}
#endif

FString UAnimDebugTools::GetNodeWeightsByGuid(const FString& ActorLabel)
{
#if WITH_EDITORONLY_DATA
	FString Error;
	UAnimInstance* Inst = nullptr;
	UAnimBlueprint* AnimBP = ResolveAnimBlueprint(ActorLabel, Inst, Error);
	if (!AnimBP) { return FString::Printf(TEXT("ERROR: %s"), *Error); }

	if (AnimBP->GetObjectBeingDebugged() != Inst)
	{
		GPreviousDebugObjects.Add(AnimBP, AnimBP->GetObjectBeingDebugged());   // snapshot (may be null)
		AnimBP->SetObjectBeingDebugged(Inst);                                  // public unlock (Mechanism D)
		return FString::Printf(TEXT("attached: '%s' is now the debug object — call again next frame for data"),
			*ActorLabel);
	}

	UAnimBlueprintGeneratedClass* GenClass = CastChecked<UAnimBlueprintGeneratedClass>(Inst->GetClass());
	FAnimBlueprintDebugData& Debug = GenClass->GetAnimBlueprintDebugData();

	TStringBuilder<8192> Out;
	Out.Appendf(TEXT("updatedNodesThisFrame: %d\n"), Debug.UpdatedNodesThisFrame.Num());
	for (const auto& Visit : Debug.UpdatedNodesThisFrame)   // entries carry SourceID/TargetID/Weight
	{
		// 5.8 ships the reverse map directly: index -> source editor node (value type is
		// TWeakObjectPtr<const UEdGraphNode>, AnimBlueprintGeneratedClass.h:192). The concrete
		// object is a UAnimGraphNode_* so GetClass()->GetName() still yields e.g.
		// "AnimGraphNode_StateMachine"; NodeGuid is a public UEdGraphNode member (EdGraphNode.h:406).
		const TWeakObjectPtr<const UEdGraphNode>* Found = Debug.NodePropertyIndexToNodeMap.Find(Visit.SourceID);
		const UEdGraphNode* Node = Found ? Found->Get() : nullptr;
		Out.Appendf(TEXT("%s | %s | weight %.3f\n"),
			Node ? *Node->NodeGuid.ToString() : *FString::Printf(TEXT("id:%d"), Visit.SourceID),
			Node ? *Node->GetClass()->GetName() : TEXT("?"),
			Visit.Weight);
	}
	return Out.ToString();
#else
	return TEXT("ERROR: editor-only data unavailable in this build");
#endif
}

FString UAnimDebugTools::DetachNodeWeightDebug(const FString& ActorLabel)
{
#if WITH_EDITORONLY_DATA
	FString Error;
	UAnimInstance* Inst = nullptr;
	UAnimBlueprint* AnimBP = ResolveAnimBlueprint(ActorLabel, Inst, Error);
	if (!AnimBP) { return FString::Printf(TEXT("ERROR: %s"), *Error); }

	TWeakObjectPtr<UObject> Previous;
	if (GPreviousDebugObjects.RemoveAndCopyValue(AnimBP, Previous))
	{
		AnimBP->SetObjectBeingDebugged(Previous.Get());   // restore snapshot (null = no prior selection)
		return TEXT("ok: previous debug object restored");
	}
	// No snapshot (detach without attach, or double detach): leave the editor's current
	// debug selection untouched — clearing it would clobber a human's in-editor choice.
	return TEXT("ok: no snapshot to restore (not attached)");
#else
	return TEXT("ERROR: editor-only data unavailable in this build");
#endif
}
