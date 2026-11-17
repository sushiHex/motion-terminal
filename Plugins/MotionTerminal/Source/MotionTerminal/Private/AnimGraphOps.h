#pragma once

#include "CoreMinimal.h"

class UAnimBlueprint;
class UAnimGraphNode_Base;
class UEdGraph;
class UEdGraphNode;
class UEdGraphPin;
class USkeleton;

/** The AnimGraph WRITE kernel (docs/specs/2026-11-12-animgraph-build-describe-design.md, section 3). No tool surface:
    UAnimGraphBuildTools is its only caller. Every operation returns false with OutError set and never logs and
    continues. The reader that checks what this writes (AnimGraphDescribe) shares no code with it, on purpose. */
namespace MotionTerminal::AnimGraphOps
{
	/** A new Animation Blueprint at AssetPath ("/Game/Dir/Name"), which must not exist. ParentClassPath empty means
	    UAnimInstance. */
	UAnimBlueprint* CreateAnimBlueprint(const FString& AssetPath, USkeleton* Skeleton, const FString& ParentClassPath,
		FString& OutError);

	/** The blueprint's top-level AnimGraph, which must hold nothing but its root node. */
	UEdGraph* EmptyAnimGraph(UAnimBlueprint& AnimBP, FString& OutError);

	/** A member variable of TypeName "float", "bool" or "int", with DefaultValue as the editor writes it; settable on
	    each instance when bInstanceEditable (the editor's "Instance Editable"), otherwise on the class only. */
	bool AddVariable(UAnimBlueprint& AnimBP, const FString& Name, const FString& TypeName, const FString& DefaultValue,
		bool bInstanceEditable, FString& OutError);

	/** A node of NodeClassPath ("/Script/Module.AnimGraphNode_X"), spawned as the palette spawns it, tagged Id. */
	UAnimGraphNode_Base* AddNode(UEdGraph& Graph, const FString& NodeClassPath, const FString& Id, FVector2D Position,
		FString& OutError);

	/** A variable-get node for a member variable of the graph's blueprint, tagged Id. */
	UEdGraphNode* AddVariableGet(UEdGraph& Graph, const FString& Variable, const FString& Id, FVector2D Position,
		FString& OutError);

	/** A LayeredBoneBlend's blend-pose count, grown through the node's own add-pose path (weights and layers follow). */
	bool SetPoseCount(UAnimGraphNode_Base& Node, int32 Poses, FString& OutError);

	/** Import ExportText over the live value at Path ("Member", "Member.Sub", "Member[2].Sub") inside the node's
	    FAnimNode struct, notify the node as the details panel does, and, when the member has a visible pin, write the
	    pin's default too (the compiler takes an unlinked visible pin over the struct member). A nested path under a
	    member with a visible pin is refused. */
	bool SetProperty(UAnimGraphNode_Base& Node, const FString& Path, const FString& ExportText, FString& OutError);

	/** Show the pin of an optional property, or of a custom property (a Control Rig variable). */
	bool ExposePin(UAnimGraphNode_Base& Node, const FString& Property, FString& OutError);

	/** Link A.PinA (output) to B.PinB (input) through the graph's own schema, and require the direct link to exist
	    afterwards (a conversion node inserted between them is a failure: the spec did not ask for it). */
	bool Connect(UEdGraphNode& A, const FString& PinA, UEdGraphNode& B, const FString& PinB, FString& OutError);

	/** The graph's root node. */
	UEdGraphNode* Root(UEdGraph& Graph, FString& OutError);

	/** Whether a pin carries a pose (one consumer only: the schema replaces an earlier link rather than refusing). */
	bool IsPosePin(const UEdGraphPin& Pin);

	/** Every link in the graph as "NodeName.Pin->NodeName.Pin", output first. */
	TArray<FString> Links(const UEdGraph& Graph);

	/** Compile; false with every compiler error when the result is not clean. Warnings are appended to OutWarnings. */
	bool Compile(UAnimBlueprint& AnimBP, TArray<FString>& OutWarnings, FString& OutError);

	/** Save the blueprint's package to disk. */
	bool Save(UAnimBlueprint& AnimBP, FString& OutError);
}
