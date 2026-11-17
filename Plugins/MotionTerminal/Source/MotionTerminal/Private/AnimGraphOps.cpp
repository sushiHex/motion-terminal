#include "AnimGraphOps.h"

#include "Animation/AnimBlueprint.h"
#include "Animation/AnimInstance.h"
#include "Animation/Skeleton.h"
#include "AnimGraphNode_Base.h"
#include "AnimGraphNode_LayeredBoneBlend.h"
#include "AnimGraphNode_Root.h"
#include "AnimationGraphSchema.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetToolsModule.h"
#include "BlueprintNodeBinder.h"
#include "BlueprintNodeSpawner.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraphSchema_K2.h"
#include "Factories/AnimBlueprintFactory.h"
#include "IAssetTools.h"
#include "K2Node.h"
#include "K2Node_VariableGet.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Kismet2/CompilerResultsLog.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "Logging/TokenizedMessage.h"
#include "Misc/OutputDeviceNull.h"
#include "Misc/PackageName.h"
#include "Misc/StringOutputDevice.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"
#include "UObject/UnrealType.h"

namespace
{
	bool Fail(FString& OutError, FString Why)
	{
		OutError = MoveTemp(Why);
		return false;
	}

	FString Where(const UEdGraphNode& Node)
	{
		return Node.NodeComment.IsEmpty() ? Node.GetName() : Node.NodeComment;
	}

	/** One step of a property path: a member name and, for an array element, its index. */
	bool SplitStep(const FString& Step, FString& OutName, int32& OutIndex)
	{
		OutIndex = INDEX_NONE;
		int32 Open = INDEX_NONE;
		if (!Step.FindChar(TEXT('['), Open))
		{
			OutName = Step;
			return !OutName.IsEmpty();
		}
		if (!Step.EndsWith(TEXT("]")) || Open == 0)
		{
			return false;
		}
		OutName = Step.Left(Open);
		const FString Digits = Step.Mid(Open + 1, Step.Len() - Open - 2);
		if (Digits.IsEmpty() || !Digits.IsNumeric())
		{
			return false;
		}
		OutIndex = FCString::Atoi(*Digits);
		return OutIndex >= 0;
	}

	/** The property and value address a path names inside Struct at StructPtr; the first step's property is the
	    member the node is notified about. */
	bool Resolve(const UStruct* Struct, void* StructPtr, const FString& Path, FProperty*& OutLeaf, void*& OutValue,
		FProperty*& OutMember, TArray<FProperty*>& OutChain, FString& OutError)
	{
		OutChain.Reset();
		TArray<FString> Steps;
		Path.ParseIntoArray(Steps, TEXT("."));
		if (Steps.Num() == 0)
		{
			return Fail(OutError, TEXT("empty property path"));
		}
		OutMember = nullptr;
		const UStruct* Owner = Struct;
		void* Container = StructPtr;
		for (int32 i = 0; i < Steps.Num(); ++i)
		{
			FString Name;
			int32 Index = INDEX_NONE;
			if (!SplitStep(Steps[i], Name, Index))
			{
				return Fail(OutError, FString::Printf(TEXT("malformed path step '%s' in '%s'"), *Steps[i], *Path));
			}
			FProperty* Prop = Owner ? Owner->FindPropertyByName(FName(*Name)) : nullptr;
			if (!Prop)
			{
				return Fail(OutError, FString::Printf(TEXT("no property '%s' on %s (path '%s')"), *Name,
					Owner ? *Owner->GetName() : TEXT("a non-struct value"), *Path));
			}
			if (i == 0)
			{
				OutMember = Prop;
			}
			OutChain.Add(Prop);
			void* Value = Prop->ContainerPtrToValuePtr<void>(Container);
			if (Index != INDEX_NONE)
			{
				const FArrayProperty* Array = CastField<FArrayProperty>(Prop);
				if (!Array)
				{
					return Fail(OutError, FString::Printf(TEXT("'%s' is indexed but is not an array (path '%s')"),
						*Name, *Path));
				}
				FScriptArrayHelper Helper(Array, Value);
				if (!Helper.IsValidIndex(Index))
				{
					return Fail(OutError, FString::Printf(TEXT("'%s' has %d elements, index %d asked (path '%s')"),
						*Name, Helper.Num(), Index, *Path));
				}
				Prop = Array->Inner;
				Value = Helper.GetRawPtr(Index);
			}
			if (i == Steps.Num() - 1)
			{
				OutLeaf = Prop;
				OutValue = Value;
				return true;
			}
			const FStructProperty* Inner = CastField<FStructProperty>(Prop);
			if (!Inner)
			{
				return Fail(OutError, FString::Printf(TEXT("'%s' is not a struct, cannot step into it (path '%s')"),
					*Name, *Path));
			}
			Owner = Inner->Struct;
			Container = Value;
		}
		return Fail(OutError, TEXT("unreachable"));
	}

	UEdGraphPin* InputPin(UEdGraphNode& Node, const FName Name)
	{
		for (UEdGraphPin* Pin : Node.Pins)
		{
			if (Pin && Pin->Direction == EGPD_Input && Pin->PinName == Name)
			{
				return Pin;
			}
		}
		return nullptr;
	}

	/** The optional-pin table named ArrayName on the node, by reflection (both tables are protected members). */
	TArray<FOptionalPinFromProperty>* OptionalPins(UAnimGraphNode_Base& Node, const TCHAR* ArrayName)
	{
		const FArrayProperty* Array = CastField<FArrayProperty>(Node.GetClass()->FindPropertyByName(ArrayName));
		const FStructProperty* Inner = Array ? CastField<FStructProperty>(Array->Inner) : nullptr;
		if (!Inner || Inner->Struct != FOptionalPinFromProperty::StaticStruct())
		{
			return nullptr;
		}
		return Array->ContainerPtrToValuePtr<TArray<FOptionalPinFromProperty>>(&Node);
	}
}

namespace MotionTerminal::AnimGraphOps
{
	UAnimBlueprint* CreateAnimBlueprint(const FString& AssetPath, USkeleton* Skeleton, const FString& ParentClassPath,
		FString& OutError)
	{
		FString PackagePath;
		FString AssetName;
		if (!AssetPath.StartsWith(TEXT("/")) || !AssetPath.Split(TEXT("/"), &PackagePath, &AssetName,
			ESearchCase::CaseSensitive, ESearchDir::FromEnd) || AssetName.IsEmpty() || AssetName.Contains(TEXT(".")))
		{
			OutError = FString::Printf(TEXT("'%s' is not a package path like /Game/Dir/Name"), *AssetPath);
			return nullptr;
		}
		if (FindPackage(nullptr, *AssetPath) || FPackageName::DoesPackageExist(AssetPath))
		{
			OutError = FString::Printf(TEXT("%s already exists: a build never writes over an asset, delete it first"),
				*AssetPath);
			return nullptr;
		}
		if (!Skeleton)
		{
			OutError = TEXT("no skeleton");
			return nullptr;
		}
		UClass* Parent = UAnimInstance::StaticClass();
		if (!ParentClassPath.IsEmpty())
		{
			Parent = LoadObject<UClass>(nullptr, *ParentClassPath);
			if (!Parent || !Parent->IsChildOf(UAnimInstance::StaticClass()))
			{
				OutError = FString::Printf(TEXT("parent class '%s' is not an anim instance class"), *ParentClassPath);
				return nullptr;
			}
		}
		UAnimBlueprintFactory* Factory = NewObject<UAnimBlueprintFactory>();
		Factory->TargetSkeleton = Skeleton;
		Factory->ParentClass = Parent;
		IAssetTools& AssetTools = FModuleManager::LoadModuleChecked<FAssetToolsModule>(TEXT("AssetTools")).Get();
		UAnimBlueprint* AnimBP = Cast<UAnimBlueprint>(
			AssetTools.CreateAsset(AssetName, PackagePath, UAnimBlueprint::StaticClass(), Factory));
		if (!AnimBP)
		{
			OutError = FString::Printf(TEXT("the factory did not create %s"), *AssetPath);
		}
		return AnimBP;
	}

	UEdGraph* EmptyAnimGraph(UAnimBlueprint& AnimBP, FString& OutError)
	{
		static const FName AnimGraphName(TEXT("AnimGraph"));
		for (UEdGraph* Graph : AnimBP.FunctionGraphs)
		{
			if (!Graph || Graph->GetFName() != AnimGraphName)
			{
				continue;
			}
			for (const UEdGraphNode* Node : Graph->Nodes)
			{
				if (Node && !Node->IsA<UAnimGraphNode_Root>())
				{
					OutError = FString::Printf(TEXT("the AnimGraph already holds %s: a build only fills an empty graph"),
						*Node->GetClass()->GetName());
					return nullptr;
				}
			}
			return Graph;
		}
		OutError = TEXT("the blueprint has no AnimGraph");
		return nullptr;
	}

	bool AddVariable(UAnimBlueprint& AnimBP, const FString& Name, const FString& TypeName, const FString& DefaultValue,
		bool bInstanceEditable, FString& OutError)
	{
		FEdGraphPinType Type;
		if (TypeName == TEXT("float"))
		{
			Type.PinCategory = UEdGraphSchema_K2::PC_Real;
			Type.PinSubCategory = UEdGraphSchema_K2::PC_Double;
		}
		else if (TypeName == TEXT("bool"))
		{
			Type.PinCategory = UEdGraphSchema_K2::PC_Boolean;
		}
		else if (TypeName == TEXT("int"))
		{
			Type.PinCategory = UEdGraphSchema_K2::PC_Int;
		}
		else
		{
			return Fail(OutError, FString::Printf(TEXT("variable '%s': type '%s' is not float, bool or int"), *Name,
				*TypeName));
		}
		if (!FBlueprintEditorUtils::AddMemberVariable(&AnimBP, FName(*Name), Type, DefaultValue))
		{
			return Fail(OutError, FString::Printf(TEXT("variable '%s' was not added (a duplicate name?)"), *Name));
		}
		// A new member variable is class-only (CPF_DisableEditOnInstance); this is the editor's "Instance Editable".
		FBlueprintEditorUtils::SetBlueprintOnlyEditableFlag(&AnimBP, FName(*Name), !bInstanceEditable);
		return true;
	}

	UAnimGraphNode_Base* AddNode(UEdGraph& Graph, const FString& NodeClassPath, const FString& Id, FVector2D Position,
		FString& OutError)
	{
		UClass* Class = LoadObject<UClass>(nullptr, *NodeClassPath);
		if (!Class || !Class->IsChildOf(UAnimGraphNode_Base::StaticClass()) || Class->HasAnyClassFlags(CLASS_Abstract))
		{
			OutError = FString::Printf(TEXT("node '%s': '%s' is not a concrete anim graph node class"), *Id,
				*NodeClassPath);
			return nullptr;
		}
		if (!Class->GetDefaultObject<UEdGraphNode>()->IsCompatibleWithGraph(&Graph))
		{
			OutError = FString::Printf(TEXT("node '%s': %s cannot be placed in graph %s"), *Id, *Class->GetName(),
				*Graph.GetName());
			return nullptr;
		}
		UBlueprintNodeSpawner* Spawner = UBlueprintNodeSpawner::Create(Class);
		UAnimGraphNode_Base* Node = Spawner
			? Cast<UAnimGraphNode_Base>(Spawner->Invoke(&Graph, IBlueprintNodeBinder::FBindingSet(), Position))
			: nullptr;
		if (!Node)
		{
			OutError = FString::Printf(TEXT("node '%s': the spawner for %s made no node"), *Id, *Class->GetName());
			return nullptr;
		}
		Node->NodeComment = Id;
		return Node;
	}

	UEdGraphNode* AddVariableGet(UEdGraph& Graph, const FString& Variable, const FString& Id, FVector2D Position,
		FString& OutError)
	{
		UBlueprint* Blueprint = FBlueprintEditorUtils::FindBlueprintForGraph(&Graph);
		const UClass* Skeleton = Blueprint ? Blueprint->SkeletonGeneratedClass : nullptr;
		if (!Skeleton || !Skeleton->FindPropertyByName(FName(*Variable)))
		{
			OutError = FString::Printf(TEXT("variable get '%s': the blueprint has no variable '%s'"), *Id, *Variable);
			return nullptr;
		}
		FGraphNodeCreator<UK2Node_VariableGet> Creator(Graph);
		UK2Node_VariableGet* Node = Creator.CreateNode(false);
		Node->VariableReference.SetSelfMember(FName(*Variable));
		Node->NodePosX = FMath::RoundToInt32(Position.X);
		Node->NodePosY = FMath::RoundToInt32(Position.Y);
		Creator.Finalize();
		Node->NodeComment = Id;
		if (!Node->FindPin(FName(*Variable), EGPD_Output))
		{
			OutError = FString::Printf(TEXT("variable get '%s': no output pin '%s' after placement"), *Id, *Variable);
			return nullptr;
		}
		return Node;
	}

	bool SetPoseCount(UAnimGraphNode_Base& Node, int32 Poses, FString& OutError)
	{
		UAnimGraphNode_LayeredBoneBlend* Blend = Cast<UAnimGraphNode_LayeredBoneBlend>(&Node);
		if (!Blend)
		{
			return Fail(OutError, FString::Printf(TEXT("node '%s': a pose count applies to LayeredBoneBlend only, not %s"),
				*Where(Node), *Node.GetClass()->GetName()));
		}
		auto Count = [Blend]() { return Blend->Node.BlendPoses.Num(); };
		if (Poses < Count())
		{
			return Fail(OutError, FString::Printf(TEXT("node '%s': %d poses asked, a new node already has %d"),
				*Where(Node), Poses, Count()));
		}
		while (Count() < Poses)
		{
			const int32 Before = Count();
			Blend->AddPinToBlendByFilter();
			if (Count() != Before + 1)
			{
				return Fail(OutError, FString::Printf(TEXT("node '%s': adding a pose left %d poses"), *Where(Node),
					Count()));
			}
		}
		return true;
	}

	bool SetProperty(UAnimGraphNode_Base& Node, const FString& Path, const FString& ExportText, FString& OutError)
	{
		FStructProperty* NodeProp = Node.GetFNodeProperty();
		if (!NodeProp)
		{
			return Fail(OutError, FString::Printf(TEXT("node '%s' has no anim node struct"), *Where(Node)));
		}
		void* NodeStruct = NodeProp->ContainerPtrToValuePtr<void>(&Node);
		FProperty* Leaf = nullptr;
		FProperty* Member = nullptr;
		void* Value = nullptr;
		FString Why;
		TArray<FProperty*> Steps;
		if (!Resolve(NodeProp->Struct, NodeStruct, Path, Leaf, Value, Member, Steps, Why))
		{
			return Fail(OutError, FString::Printf(TEXT("node '%s': %s"), *Where(Node), *Why));
		}
		const FName MemberName = Member->GetFName();
		const UEdGraphPin* Before = InputPin(Node, MemberName);
		if (Before && Before->LinkedTo.Num() > 0)
		{
			return Fail(OutError, FString::Printf(TEXT("node '%s': '%s' is linked, a linked pin's property is not set"),
				*Where(Node), *MemberName.ToString()));
		}
		const bool bWholeMember = (Leaf == Member);
		if (Before && !Before->bHidden && !bWholeMember)
		{
			return Fail(OutError, FString::Printf(
				TEXT("node '%s': '%s' has a visible pin, so only the whole member can be set, not '%s'"),
				*Where(Node), *MemberName.ToString(), *Path));
		}

		// The details panel's own notification shape: a chain from the node's `Node` struct down to the edited
		// property, announced before and after the write. A node class keys its reactions on the edited property's
		// name (the Control Rig node rebuilds its variable table on ControlRigAssetReference,
		// AnimGraphNode_ControlRig.cpp:388; a skeletal control reacts to a nested bClampResult), so the chain names
		// the real leaf, not only the member it sits under.
		FEditPropertyChain Chain;
		Chain.AddTail(NodeProp);
		for (FProperty* StepProp : Steps)
		{
			Chain.AddTail(StepProp);
		}
		Chain.SetActiveMemberPropertyNode(NodeProp);
		Chain.SetActivePropertyNode(Steps.Last());
		static_cast<UObject&>(Node).PreEditChange(Chain);   // the chain overload, hidden by the node's own override

		FStringOutputDevice Errors;
		const TCHAR* End = Leaf->ImportText_Direct(*ExportText, Value, &Node, PPF_None, &Errors);
		if (!End || !Errors.IsEmpty())
		{
			return Fail(OutError, FString::Printf(TEXT("node '%s': '%s' refused the value %s%s%s"), *Where(Node), *Path,
				*ExportText.Left(200), Errors.IsEmpty() ? TEXT("") : TEXT(": "), *Errors));
		}
		for (; *End; ++End)
		{
			if (!FChar::IsWhitespace(*End))
			{
				return Fail(OutError, FString::Printf(TEXT("node '%s': '%s' read only part of its value, stopped at: %s"),
					*Where(Node), *Path, *FString(End).Left(80)));
			}
		}

		FPropertyChangedEvent Event(Steps.Last(), EPropertyChangeType::ValueSet);
		Event.SetActiveMemberProperty(NodeProp);
		FPropertyChangedChainEvent ChainEvent(Chain, Event);
		Node.PostEditChangeChainProperty(ChainEvent);

		// The compiler takes an unlinked visible pin's default over the struct member, so the pin gets the value too.
		// Pins are looked up again: the notification may have reconstructed the node.
		if (UEdGraphPin* Pin = InputPin(Node, MemberName); Pin && !Pin->bHidden)
		{
			const UEdGraphSchema* Schema = Node.GetSchema();
			if (!Schema)
			{
				return Fail(OutError, FString::Printf(TEXT("node '%s' has no schema"), *Where(Node)));
			}
			if (const FObjectPropertyBase* ObjectProp = CastField<FObjectPropertyBase>(Member))
			{
				UObject* Object = ObjectProp->GetObjectPropertyValue(Value);
				Schema->TrySetDefaultObject(*Pin, Object);
				if (Pin->DefaultObject != Object)
				{
					return Fail(OutError, FString::Printf(TEXT("node '%s': pin '%s' did not take the object %s"),
						*Where(Node), *MemberName.ToString(), *GetPathNameSafe(Object)));
				}
			}
			else
			{
				// The engine's own property-to-pin-default conversion: a pin's text is not export text for every type
				// (a vector pin is "x,y,z", a bool is lower case).
				FString Text;
				if (!FBlueprintEditorUtils::PropertyValueToString_Direct(Member, static_cast<const uint8*>(Value), Text,
					&Node))
				{
					return Fail(OutError, FString::Printf(TEXT("node '%s': '%s' has no pin-default form"), *Where(Node),
						*MemberName.ToString()));
				}
				Schema->TrySetDefaultValue(*Pin, Text);
				if (!Pin->GetDefaultAsString().Equals(Text, ESearchCase::IgnoreCase))
				{
					return Fail(OutError, FString::Printf(TEXT("node '%s': pin '%s' holds '%s' after being set to '%s'"),
						*Where(Node), *MemberName.ToString(), *Pin->GetDefaultAsString(), *Text));
				}
			}
		}
		return true;
	}

	bool ExposePin(UAnimGraphNode_Base& Node, const FString& Property, FString& OutError)
	{
		const FName Name(*Property);
		// Custom properties first (a Control Rig's variables), then the node's own optional pins.
		for (const TCHAR* Table : { TEXT("CustomPinProperties"), TEXT("ShowPinForProperties") })
		{
			TArray<FOptionalPinFromProperty>* Pins = OptionalPins(Node, Table);
			if (!Pins)
			{
				continue;
			}
			for (FOptionalPinFromProperty& Entry : *Pins)
			{
				if (Entry.PropertyName != Name)
				{
					continue;
				}
				if (!Entry.bShowPin)
				{
					Node.Modify();
					Entry.bShowPin = true;
					Node.ReconstructNode();
				}
				if (!InputPin(Node, Name))
				{
					return Fail(OutError, FString::Printf(TEXT("node '%s': '%s' was exposed but no pin appeared"),
						*Where(Node), *Property));
				}
				return true;
			}
		}
		return Fail(OutError, FString::Printf(TEXT("node '%s' has no exposable property '%s'"), *Where(Node),
			*Property));
	}

	bool Connect(UEdGraphNode& A, const FString& PinA, UEdGraphNode& B, const FString& PinB, FString& OutError)
	{
		UEdGraphPin* Out = A.FindPin(FName(*PinA), EGPD_Output);
		UEdGraphPin* In = B.FindPin(FName(*PinB), EGPD_Input);
		if (!Out || !In)
		{
			return Fail(OutError, FString::Printf(TEXT("link %s.%s -> %s.%s: no %s pin '%s' on '%s'"), *Where(A), *PinA,
				*Where(B), *PinB, Out ? TEXT("input") : TEXT("output"), Out ? *PinB : *PinA, Out ? *Where(B) : *Where(A)));
		}
		if (In->bHidden)
		{
			return Fail(OutError, FString::Printf(TEXT("link %s.%s -> %s.%s: the input pin is hidden (expose it first)"),
				*Where(A), *PinA, *Where(B), *PinB));
		}
		const UEdGraph* Graph = A.GetGraph();
		const UEdGraphSchema* Schema = Graph ? Graph->GetSchema() : nullptr;
		if (!Schema || Graph != B.GetGraph())
		{
			return Fail(OutError, TEXT("link: the two nodes are not in one graph with a schema"));
		}
		const bool bMade = Schema->TryCreateConnection(Out, In);
		// Looked up again: making a link can reconstruct either node.
		Out = A.FindPin(FName(*PinA), EGPD_Output);
		In = B.FindPin(FName(*PinB), EGPD_Input);
		if (!bMade || !Out || !In || !Out->LinkedTo.Contains(In))
		{
			return Fail(OutError, FString::Printf(
				TEXT("link %s.%s -> %s.%s was %s (pin types %s and %s)"), *Where(A), *PinA, *Where(B), *PinB,
				bMade ? TEXT("made through a node the spec did not name") : TEXT("refused by the schema"),
				Out ? *Out->PinType.PinCategory.ToString() : TEXT("?"), In ? *In->PinType.PinCategory.ToString() : TEXT("?")));
		}
		return true;
	}

	UEdGraphNode* Root(UEdGraph& Graph, FString& OutError)
	{
		UEdGraphNode* Found = nullptr;
		for (UEdGraphNode* Node : Graph.Nodes)
		{
			if (Node && Node->IsA<UAnimGraphNode_Root>())
			{
				if (Found)
				{
					OutError = TEXT("the AnimGraph has more than one root node");
					return nullptr;
				}
				Found = Node;
			}
		}
		if (!Found)
		{
			OutError = TEXT("the AnimGraph has no root node");
		}
		return Found;
	}

	bool IsPosePin(const UEdGraphPin& Pin)
	{
		return UAnimationGraphSchema::IsPosePin(Pin.PinType);
	}

	TArray<FString> Links(const UEdGraph& Graph)
	{
		TArray<FString> Out;
		for (const UEdGraphNode* Node : Graph.Nodes)
		{
			if (!Node)
			{
				continue;
			}
			for (const UEdGraphPin* Pin : Node->Pins)
			{
				if (!Pin || Pin->Direction != EGPD_Output)
				{
					continue;
				}
				for (const UEdGraphPin* To : Pin->LinkedTo)
				{
					if (To && To->GetOwningNodeUnchecked())
					{
						Out.Add(FString::Printf(TEXT("%s.%s->%s.%s"), *Node->GetName(), *Pin->PinName.ToString(),
							*To->GetOwningNodeUnchecked()->GetName(), *To->PinName.ToString()));
					}
				}
			}
		}
		Out.Sort();
		return Out;
	}

	bool Compile(UAnimBlueprint& AnimBP, TArray<FString>& OutWarnings, FString& OutError)
	{
		FCompilerResultsLog Log;
		Log.SetSilentMode(true);
		Log.bAnnotateMentionedNodes = false;
		FKismetEditorUtilities::CompileBlueprint(&AnimBP, EBlueprintCompileOptions::None, &Log);
		TArray<FString> Errors;
		for (const TSharedRef<FTokenizedMessage>& Message : Log.Messages)
		{
			const FString Text = Message->ToText().ToString();
			if (Message->GetSeverity() == EMessageSeverity::Error)
			{
				Errors.Add(Text);
			}
			else if (Message->GetSeverity() == EMessageSeverity::Warning
				|| Message->GetSeverity() == EMessageSeverity::PerformanceWarning)
			{
				OutWarnings.Add(Text);
			}
		}
		if (Log.NumErrors > 0 || Errors.Num() > 0 || AnimBP.Status == BS_Error || !AnimBP.GeneratedClass)
		{
			return Fail(OutError, FString::Printf(TEXT("compile failed (%d errors, status %d): %s"), Log.NumErrors,
				static_cast<int32>(AnimBP.Status), *FString::Join(Errors, TEXT(" | "))));
		}
		return true;
	}

	bool Save(UAnimBlueprint& AnimBP, FString& OutError)
	{
		UPackage* Package = AnimBP.GetOutermost();
		const FString File = FPackageName::LongPackageNameToFilename(Package->GetName(),
			FPackageName::GetAssetPackageExtension());
		FSavePackageArgs Args;
		Args.TopLevelFlags = RF_Public | RF_Standalone;
		Args.SaveFlags = SAVE_NoError;
		if (!UPackage::SavePackage(Package, &AnimBP, *File, Args))
		{
			return Fail(OutError, FString::Printf(TEXT("%s was not saved to %s"), *Package->GetName(), *File));
		}
		return true;
	}
}
