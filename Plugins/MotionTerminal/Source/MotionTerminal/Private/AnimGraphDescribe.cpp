#include "AnimGraphDescribe.h"

#include "Animation/AnimBlueprint.h"
#include "Animation/AnimBlueprintGeneratedClass.h"
#include "Animation/AnimClassInterface.h"
#include "Animation/AnimNodeBase.h"
#include "Animation/AnimNodeData.h"
#include "Animation/Skeleton.h"
#include "AnimGraphNode_Base.h"
#include "AnimGraphNode_Root.h"
#include "AnimGraphTopology.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "JsonObjectConverter.h"
#include "K2Node_Variable.h"
#include "UObject/UnrealType.h"

namespace
{
	/** How a node is named in a description: its spec id (the comment tag a build leaves), "<output>" for the root,
	    its engine name otherwise. */
	FString Label(const UEdGraphNode& Node)
	{
		if (Node.IsA<UAnimGraphNode_Root>())
		{
			return TEXT("<output>");
		}
		return Node.NodeComment.IsEmpty() ? Node.GetName() : Node.NodeComment;
	}

	/** A member name without its array index: "Legs[2]" -> "Legs" and 2; false on a malformed step. */
	bool Step(const FString& Text, FString& OutName, int32& OutIndex)
	{
		OutName = Text;
		OutIndex = INDEX_NONE;
		int32 Open = INDEX_NONE;
		if (!OutName.FindChar(TEXT('['), Open))
		{
			return !OutName.IsEmpty();
		}
		const FString Digits = OutName.Mid(Open + 1, OutName.Len() - Open - 2);
		if (Open == 0 || !OutName.EndsWith(TEXT("]")) || Digits.IsEmpty() || !Digits.IsNumeric())
		{
			return false;
		}
		OutIndex = FCString::Atoi(*Digits);
		OutName.LeftInline(Open);
		return true;
	}

	/** The value Path names inside Struct at StructPtr, as typed JSON: every member of a struct (the engine's text
	    export drops members that equal their defaults, and a reader cannot tell "default" from "absent"), numbers as
	    numbers, names and enums as strings, objects as paths. MemberAt, when given, is the address of the FIRST step's
	    value, for a member the class keeps outside the struct. Null with OutWhy when the path does not resolve. */
	TSharedPtr<FJsonValue> ReadPath(const UStruct* Struct, const void* StructPtr, const void* MemberAt,
		const FString& Path, FString& OutWhy)
	{
		TArray<FString> Steps;
		Path.ParseIntoArray(Steps, TEXT("."));
		const UStruct* In = Struct;
		const void* Container = StructPtr;
		for (int32 i = 0; i < Steps.Num(); ++i)
		{
			FString Name;
			int32 Index = INDEX_NONE;
			if (!Step(Steps[i], Name, Index))
			{
				OutWhy = FString::Printf(TEXT("malformed step '%s'"), *Steps[i]);
				return nullptr;
			}
			FProperty* Prop = In ? In->FindPropertyByName(FName(*Name)) : nullptr;
			if (!Prop)
			{
				OutWhy = FString::Printf(TEXT("no property '%s'"), *Name);
				return nullptr;
			}
			const void* Value = (i == 0 && MemberAt) ? MemberAt : Prop->ContainerPtrToValuePtr<void>(Container);
			if (Index != INDEX_NONE)
			{
				const FArrayProperty* Array = CastField<FArrayProperty>(Prop);
				if (!Array)
				{
					OutWhy = FString::Printf(TEXT("'%s' is not an array"), *Name);
					return nullptr;
				}
				FScriptArrayHelper Helper(Array, Value);
				if (!Helper.IsValidIndex(Index))
				{
					OutWhy = FString::Printf(TEXT("'%s' has %d elements"), *Name, Helper.Num());
					return nullptr;
				}
				Prop = Array->Inner;
				Value = Helper.GetRawPtr(Index);
			}
			if (i == Steps.Num() - 1)
			{
				TSharedPtr<FJsonValue> Out = FJsonObjectConverter::UPropertyToJsonValue(Prop, Value, 0, 0, nullptr,
					nullptr, EJsonObjectConversionFlags::SkipStandardizeCase);
				if (!Out.IsValid())
				{
					OutWhy = FString::Printf(TEXT("'%s' has no JSON form"), *Name);
				}
				return Out;
			}
			const FStructProperty* Inner = CastField<FStructProperty>(Prop);
			if (!Inner)
			{
				OutWhy = FString::Printf(TEXT("'%s' is not a struct"), *Name);
				return nullptr;
			}
			In = Inner->Struct;
			Container = Value;
		}
		OutWhy = TEXT("empty path");
		return nullptr;
	}

	/** The compiled anim node for an editor node: its struct property on the generated class and its address in the
	    class default object. Null when the node did not compile into one (the compiler prunes what the output does
	    not reach). */
	const FStructProperty* CompiledNode(UAnimBlueprint& AnimBP, const UAnimGraphNode_Base& Node, const void*& OutPtr,
		const UObject*& OutDefaults)
	{
		UAnimBlueprintGeneratedClass* Class = Cast<UAnimBlueprintGeneratedClass>(AnimBP.GeneratedClass);
		OutDefaults = Class ? Class->GetDefaultObject(false) : nullptr;
		if (!OutDefaults)
		{
			return nullptr;
		}
		const int32* Index = Class->GetAnimBlueprintDebugData().NodePropertyToIndexMap.Find(
			TWeakObjectPtr<const UAnimGraphNode_Base>(&Node));
		const TArray<FStructProperty*>& Props = Class->GetAnimNodeProperties();
		// The map's index counts from the end of the property list (AnimBlueprintGeneratedClass.h, the debug-data
		// accessors do the same subtraction).
		const int32 At = Index ? Props.Num() - 1 - *Index : INDEX_NONE;
		if (!Props.IsValidIndex(At) || !Props[At])
		{
			return nullptr;
		}
		OutPtr = Props[At]->ContainerPtrToValuePtr<void>(OutDefaults);
		return Props[At];
	}

	/** A compiled value. A member the compiler FOLDED (meta FoldProperty) is not in the node struct: the class keeps
	    it as constant data, and the struct holds a stale default. Such a member is reported unread, never read from
	    the struct. */
	TSharedPtr<FJsonValue> ReadCompiled(const FStructProperty& NodeProp, const void* NodePtr, const FString& Path,
		FString& OutWhy)
	{
		FString First = Path;
		int32 Cut = INDEX_NONE;
		if (First.FindChar(TEXT('.'), Cut))
		{
			First.LeftInline(Cut);
		}
		FString MemberName;
		int32 Unused = INDEX_NONE;
		if (!Step(First, MemberName, Unused))
		{
			OutWhy = FString::Printf(TEXT("malformed step '%s'"), *First);
			return nullptr;
		}
		const FProperty* Member = NodeProp.Struct->FindPropertyByName(FName(*MemberName));
		if (Member && Member->HasMetaData(TEXT("FoldProperty")))
		{
			// The engine keeps its folded-data accessors to itself (FAnimNode_Base::GetData is protected,
			// IAnimClassInterface::GetNodeData too), so this member is reported unread. The reason starts with the
			// word the host's waiver looks for; no other unread reason can be waived.
			OutWhy = TEXT("folded constant: the compiled value is in the class's constant data, which is not readable here");
			return nullptr;
		}
		return ReadPath(NodeProp.Struct, NodePtr, nullptr, Path, OutWhy);
	}

	TSharedRef<FJsonObject> PinJson(const UEdGraphPin& Pin)
	{
		const TSharedRef<FJsonObject> Obj = MakeShared<FJsonObject>();
		Obj->SetStringField(TEXT("name"), Pin.PinName.ToString());
		Obj->SetStringField(TEXT("direction"), Pin.Direction == EGPD_Input ? TEXT("in") : TEXT("out"));
		Obj->SetStringField(TEXT("type"), Pin.PinType.PinCategory.ToString());
		if (Pin.Direction == EGPD_Input && Pin.LinkedTo.Num() == 0)
		{
			Obj->SetStringField(TEXT("default"), Pin.GetDefaultAsString());
		}
		return Obj;
	}

	const TCHAR* StatusName(EBlueprintStatus Status)
	{
		switch (Status)
		{
		case BS_UpToDate: return TEXT("clean");
		case BS_UpToDateWithWarnings: return TEXT("warnings");
		case BS_Error: return TEXT("error");
		case BS_Dirty: return TEXT("dirty");
		case BS_BeingCreated: return TEXT("being_created");
		default: return TEXT("unknown");
		}
	}
}

namespace MotionTerminal
{
	TSharedRef<FJsonObject> DescribeAnimGraphJson(UAnimBlueprint& AnimBP, const TMap<FString, TArray<FString>>& PathsById)
	{
		const TSharedRef<FJsonObject> Root = MakeShared<FJsonObject>();
		Root->SetBoolField(TEXT("ok"), true);
		Root->SetStringField(TEXT("path"), AnimBP.GetOutermost()->GetName());
		Root->SetStringField(TEXT("skeleton"), AnimBP.TargetSkeleton ? AnimBP.TargetSkeleton->GetOutermost()->GetName()
			: TEXT(""));
		Root->SetStringField(TEXT("parent_class"), AnimBP.ParentClass ? AnimBP.ParentClass->GetPathName() : TEXT(""));
		// "warnings" is a compiled graph; only "clean" and "warnings" have a class worth reading.
		Root->SetStringField(TEXT("compile_status"), AnimBP.GeneratedClass ? StatusName(AnimBP.Status) : TEXT("no_class"));

		// A variable's default is read where a compile leaves it: on the class default object (the blueprint's own
		// description field is emptied by a full compile).
		const UObject* ClassDefaults = AnimBP.GeneratedClass ? AnimBP.GeneratedClass->GetDefaultObject(false) : nullptr;
		TArray<TSharedPtr<FJsonValue>> Variables;
		for (const FBPVariableDescription& Var : AnimBP.NewVariables)
		{
			const TSharedRef<FJsonObject> Obj = MakeShared<FJsonObject>();
			Obj->SetStringField(TEXT("name"), Var.VarName.ToString());
			Obj->SetStringField(TEXT("type"), Var.VarType.PinCategory.ToString());
			FProperty* Prop = ClassDefaults ? ClassDefaults->GetClass()->FindPropertyByName(Var.VarName) : nullptr;
			if (Prop)
			{
				Obj->SetField(TEXT("default"), FJsonObjectConverter::UPropertyToJsonValue(Prop,
					Prop->ContainerPtrToValuePtr<void>(ClassDefaults), 0, 0, nullptr, nullptr,
					EJsonObjectConversionFlags::SkipStandardizeCase));
				// From the compiled property, the one a set on an instance meets.
				Obj->SetBoolField(TEXT("instance_editable"), Prop->HasAnyPropertyFlags(CPF_Edit)
					&& !Prop->HasAnyPropertyFlags(CPF_DisableEditOnInstance));
			}
			Variables.Add(MakeShared<FJsonValueObject>(Obj));
		}
		Root->SetArrayField(TEXT("variables"), Variables);

		static const FName AnimGraphName(TEXT("AnimGraph"));
		const UEdGraph* Graph = nullptr;
		for (const UEdGraph* Candidate : AnimBP.FunctionGraphs)
		{
			if (Candidate && Candidate->GetFName() == AnimGraphName)
			{
				Graph = Candidate;
			}
		}
		if (!Graph)
		{
			Root->SetBoolField(TEXT("ok"), false);
			Root->SetStringField(TEXT("error"), TEXT("the blueprint has no AnimGraph"));
			return Root;
		}

		TArray<TSharedPtr<FJsonValue>> Nodes;
		TArray<FString> Links;                       // "from\tpin\tto\tpin", sorted, then split
		TMap<FString, int32> LabelUses;
		for (const UEdGraphNode* Node : Graph->Nodes)
		{
			if (!Node)
			{
				continue;
			}
			const FString Id = Label(*Node);
			++LabelUses.FindOrAdd(Id);
			const TSharedRef<FJsonObject> Obj = MakeShared<FJsonObject>();
			Obj->SetStringField(TEXT("id"), Id);
			Obj->SetStringField(TEXT("name"), Node->GetName());
			Obj->SetStringField(TEXT("class"), Node->GetClass()->GetPathName());
			if (const UK2Node_Variable* VarNode = Cast<UK2Node_Variable>(Node))
			{
				Obj->SetStringField(TEXT("variable"), VarNode->GetVarNameString());
			}

			TArray<TSharedPtr<FJsonValue>> Pins;
			for (const UEdGraphPin* Pin : Node->Pins)
			{
				if (!Pin || Pin->bHidden)
				{
					continue;
				}
				Pins.Add(MakeShared<FJsonValueObject>(PinJson(*Pin)));
				if (Pin->Direction != EGPD_Output)
				{
					continue;
				}
				for (const UEdGraphPin* To : Pin->LinkedTo)
				{
					if (const UEdGraphNode* Other = To ? To->GetOwningNodeUnchecked() : nullptr)
					{
						Links.Add(FString::Join(TArray<FString>{ Id, Pin->PinName.ToString(), Label(*Other),
							To->PinName.ToString() }, TEXT("\t")));
					}
				}
			}
			Obj->SetArrayField(TEXT("pins"), Pins);

			if (const UAnimGraphNode_Base* AnimNode = Cast<UAnimGraphNode_Base>(Node))
			{
				TMap<FString, FString> Bindings;
				GetNodePropertyBindings(*AnimNode, Bindings);
				const TSharedRef<FJsonObject> BindObj = MakeShared<FJsonObject>();
				for (const TPair<FString, FString>& Pair : Bindings)
				{
					BindObj->SetStringField(Pair.Key, Pair.Value);
				}
				Obj->SetObjectField(TEXT("bindings"), BindObj);

				// Every anim node says whether it compiled into the class, asked about or not: a node the output
				// does not reach is pruned, and would otherwise look built.
				const void* CompiledPtr = nullptr;
				const UObject* Defaults = nullptr;
				const FStructProperty* CompiledProp = CompiledNode(AnimBP, *AnimNode, CompiledPtr, Defaults);
				Obj->SetBoolField(TEXT("compiled_node"), CompiledProp != nullptr);

				if (const TArray<FString>* Paths = PathsById.Find(Id))
				{
					const FStructProperty* EditorProp = AnimNode->GetFNodeProperty();
					const void* EditorPtr = EditorProp ? EditorProp->ContainerPtrToValuePtr<void>(AnimNode) : nullptr;
					const TSharedRef<FJsonObject> Editor = MakeShared<FJsonObject>();
					const TSharedRef<FJsonObject> Compiled = MakeShared<FJsonObject>();
					const TSharedRef<FJsonObject> Unread = MakeShared<FJsonObject>();
					for (const FString& Path : *Paths)
					{
						FString Why = TEXT("no anim node struct");
						TSharedPtr<FJsonValue> Value = EditorPtr
							? ReadPath(EditorProp->Struct, EditorPtr, nullptr, Path, Why) : nullptr;
						if (Value.IsValid())
						{
							Editor->SetField(Path, Value);
						}
						else
						{
							Unread->SetStringField(TEXT("editor:") + Path, Why);
						}

						Why = TEXT("the node has no compiled counterpart");
						Value = CompiledProp ? ReadCompiled(*CompiledProp, CompiledPtr, Path, Why) : nullptr;
						if (Value.IsValid())
						{
							Compiled->SetField(Path, Value);
						}
						else
						{
							Unread->SetStringField(TEXT("compiled:") + Path, Why);
						}
					}
					Obj->SetObjectField(TEXT("editor"), Editor);
					Obj->SetObjectField(TEXT("compiled"), Compiled);
					Obj->SetObjectField(TEXT("unread"), Unread);
				}
			}
			Nodes.Add(MakeShared<FJsonValueObject>(Obj));
		}
		Root->SetArrayField(TEXT("nodes"), Nodes);

		Links.Sort();
		TArray<TSharedPtr<FJsonValue>> LinkValues;
		for (const FString& Link : Links)
		{
			TArray<FString> Parts;
			Link.ParseIntoArray(Parts, TEXT("\t"), false);
			TArray<TSharedPtr<FJsonValue>> Row;
			for (const FString& Part : Parts)
			{
				Row.Add(MakeShared<FJsonValueString>(Part));
			}
			LinkValues.Add(MakeShared<FJsonValueArray>(Row));
		}
		Root->SetArrayField(TEXT("links"), LinkValues);

		// Two nodes under one label would make every comparison by id ambiguous: say so rather than pick one.
		TArray<TSharedPtr<FJsonValue>> Ambiguous;
		for (const TPair<FString, int32>& Pair : LabelUses)
		{
			if (Pair.Value > 1)
			{
				Ambiguous.Add(MakeShared<FJsonValueString>(Pair.Key));
			}
		}
		Root->SetArrayField(TEXT("ambiguous_ids"), Ambiguous);
		return Root;
	}
}
