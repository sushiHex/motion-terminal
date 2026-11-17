#include "AnimGraphBuildTools.h"

#include "Animation/AnimBlueprint.h"
#include "Animation/Skeleton.h"
#include "AnimGraphNode_Base.h"
#include "AnimGraphOps.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "HeroToolJson.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Serialization/JsonReader.h"

#include UE_INLINE_GENERATED_CPP_BY_NAME(AnimGraphBuildTools)

namespace
{
	namespace Ops = MotionTerminal::AnimGraphOps;

	struct FLink
	{
		FString From, FromPin, To, ToPin;
	};

	/** A JSON array of exactly N strings. */
	bool Strings(const TSharedPtr<FJsonValue>& Value, int32 N, TArray<FString>& Out)
	{
		const TArray<TSharedPtr<FJsonValue>>* Items = nullptr;
		if (!Value.IsValid() || !Value->TryGetArray(Items) || Items->Num() != N)
		{
			return false;
		}
		Out.Reset();
		for (const TSharedPtr<FJsonValue>& Item : *Items)
		{
			FString Text;
			if (!Item.IsValid() || !Item->TryGetString(Text))
			{
				return false;
			}
			Out.Add(MoveTemp(Text));
		}
		return true;
	}

	const TArray<TSharedPtr<FJsonValue>>& Array(const FJsonObject& Obj, const TCHAR* Field)
	{
		static const TArray<TSharedPtr<FJsonValue>> Empty;
		const TArray<TSharedPtr<FJsonValue>>* Items = nullptr;
		return Obj.TryGetArrayField(Field, Items) ? *Items : Empty;
	}

	/** The first step of a property path, without an index: the anim node member the path sits under. */
	FString MemberOf(const FString& Path)
	{
		FString Member = Path;
		int32 Cut = INDEX_NONE;
		if (Member.FindChar(TEXT('.'), Cut))
		{
			Member.LeftInline(Cut);
		}
		if (Member.FindChar(TEXT('['), Cut))
		{
			Member.LeftInline(Cut);
		}
		return Member;
	}

	/** The whole build once the asset exists. Returns false with OutError; OutNames maps spec ids to node names. */
	bool Fill(UAnimBlueprint& AnimBP, const FJsonObject& Spec, TMap<FString, FString>& OutNames,
		TArray<FString>& OutWarnings, FString& OutError)
	{
		// Variables first: a variable-get needs its variable on the skeleton class.
		for (const TSharedPtr<FJsonValue>& Item : Array(Spec, TEXT("variables")))
		{
			const TSharedPtr<FJsonObject>* Var = nullptr;
			FString Name, Type, Default;
			if (!Item.IsValid() || !Item->TryGetObject(Var) || !(*Var)->TryGetStringField(TEXT("name"), Name)
				|| !(*Var)->TryGetStringField(TEXT("type"), Type))
			{
				OutError = TEXT("a variable needs a name and a type");
				return false;
			}
			(*Var)->TryGetStringField(TEXT("default"), Default);
			bool bInstanceEditable = false;
			(*Var)->TryGetBoolField(TEXT("instance_editable"), bInstanceEditable);
			if (!Ops::AddVariable(AnimBP, Name, Type, Default, bInstanceEditable, OutError))
			{
				return false;
			}
		}
		FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(&AnimBP);

		UEdGraph* Graph = Ops::EmptyAnimGraph(AnimBP, OutError);
		if (!Graph)
		{
			return false;
		}

		// Links are read and checked before anything is placed: every refusal that needs no engine state comes first.
		TArray<FLink> Links;
		for (const TSharedPtr<FJsonValue>& Item : Array(Spec, TEXT("links")))
		{
			TArray<FString> Parts;
			if (!Strings(Item, 4, Parts))
			{
				OutError = TEXT("a link is [from id, output pin, to id, input pin]");
				return false;
			}
			Links.Add({ Parts[0], Parts[1], Parts[2], Parts[3] });
		}
		TArray<FString> Output;
		if (!Strings(Spec.TryGetField(TEXT("output")), 2, Output))
		{
			OutError = TEXT("the spec needs \"output\": [id, pose output pin]");
			return false;
		}
		TSet<FString> Fed;   // "id.pin" inputs
		for (const FLink& Link : Links)
		{
			bool bAlready = false;
			Fed.Add(Link.To + TEXT(".") + Link.ToPin, &bAlready);
			if (bAlready)
			{
				OutError = FString::Printf(TEXT("input %s.%s is linked twice"), *Link.To, *Link.ToPin);
				return false;
			}
		}

		TMap<FString, UEdGraphNode*> Nodes;
		int32 Column = 0;
		auto Place = [&Column]() { return FVector2D(-300.0 * (++Column), 0.0); };
		auto Claim = [&Nodes, &OutError](const FString& Id) -> bool
		{
			if (Id.IsEmpty() || Nodes.Contains(Id))
			{
				OutError = FString::Printf(TEXT("node id '%s' is empty or used twice"), *Id);
				return false;
			}
			return true;
		};

		for (const TSharedPtr<FJsonValue>& Item : Array(Spec, TEXT("variable_gets")))
		{
			const TSharedPtr<FJsonObject>* Get = nullptr;
			FString Id, Variable;
			if (!Item.IsValid() || !Item->TryGetObject(Get) || !(*Get)->TryGetStringField(TEXT("id"), Id)
				|| !(*Get)->TryGetStringField(TEXT("variable"), Variable))
			{
				OutError = TEXT("a variable get needs an id and a variable");
				return false;
			}
			if (!Claim(Id))
			{
				return false;
			}
			UEdGraphNode* Node = Ops::AddVariableGet(*Graph, Variable, Id, Place(), OutError);
			if (!Node)
			{
				return false;
			}
			Nodes.Add(Id, Node);
		}

		// Per node, structure before values: pose count, then properties in the spec's order (a Control Rig's asset
		// reference goes first there, since it decides which variables exist), then exposure.
		for (const TSharedPtr<FJsonValue>& Item : Array(Spec, TEXT("nodes")))
		{
			const TSharedPtr<FJsonObject>* Decl = nullptr;
			FString Id, Class;
			if (!Item.IsValid() || !Item->TryGetObject(Decl) || !(*Decl)->TryGetStringField(TEXT("id"), Id)
				|| !(*Decl)->TryGetStringField(TEXT("class"), Class))
			{
				OutError = TEXT("a node needs an id and a class");
				return false;
			}
			if (!Claim(Id))
			{
				return false;
			}
			UAnimGraphNode_Base* Node = Ops::AddNode(*Graph, Class, Id, Place(), OutError);
			if (!Node)
			{
				return false;
			}
			Nodes.Add(Id, Node);

			int32 Poses = 0;
			if ((*Decl)->TryGetNumberField(TEXT("poses"), Poses) && !Ops::SetPoseCount(*Node, Poses, OutError))
			{
				return false;
			}
			for (const TSharedPtr<FJsonValue>& Entry : Array(**Decl, TEXT("properties")))
			{
				TArray<FString> Pair;
				if (!Strings(Entry, 2, Pair))
				{
					OutError = FString::Printf(TEXT("node '%s': a property is [path, export text]"), *Id);
					return false;
				}
				if (Fed.Contains(Id + TEXT(".") + MemberOf(Pair[0])))
				{
					OutError = FString::Printf(TEXT("node '%s': '%s' is both set and linked"), *Id, *MemberOf(Pair[0]));
					return false;
				}
				if (!Ops::SetProperty(*Node, Pair[0], Pair[1], OutError))
				{
					return false;
				}
			}
			for (const TSharedPtr<FJsonValue>& Entry : Array(**Decl, TEXT("exposed")))
			{
				FString Property;
				if (!Entry.IsValid() || !Entry->TryGetString(Property) || !Ops::ExposePin(*Node, Property, OutError))
				{
					if (OutError.IsEmpty())
					{
						OutError = FString::Printf(TEXT("node '%s': an exposed entry is a property name"), *Id);
					}
					return false;
				}
			}
		}

		UEdGraphNode* Root = Ops::Root(*Graph, OutError);
		if (!Root)
		{
			return false;
		}
		static const FString RootId(TEXT("<output>"));
		Nodes.Add(RootId, Root);
		Links.Add({ Output[0], Output[1], RootId, TEXT("Result") });

		// A pose output feeds one consumer. The schema would not refuse the second link: it would drop the first.
		TMap<FString, int32> Consumers;
		for (const FLink& Link : Links)
		{
			UEdGraphNode** From = Nodes.Find(Link.From);
			if (!From || !Nodes.Contains(Link.To))
			{
				OutError = FString::Printf(TEXT("link %s.%s -> %s.%s names an id the spec does not declare"), *Link.From,
					*Link.FromPin, *Link.To, *Link.ToPin);
				return false;
			}
			const UEdGraphPin* Pin = (*From)->FindPin(FName(*Link.FromPin), EGPD_Output);
			if (Pin && Ops::IsPosePin(*Pin) && ++Consumers.FindOrAdd(Link.From + TEXT(".") + Link.FromPin) > 1)
			{
				OutError = FString::Printf(TEXT("pose output %s.%s has two consumers; a pose feeds one"), *Link.From,
					*Link.FromPin);
				return false;
			}
		}
		for (const FLink& Link : Links)
		{
			if (!Ops::Connect(*Nodes[Link.From], Link.FromPin, *Nodes[Link.To], Link.ToPin, OutError))
			{
				return false;
			}
		}

		// The graph's whole link set against the spec's: a link the schema replaced, or one a reconstruction dropped
		// after it was made, shows here, where each Connect's own success could not.
		TArray<FString> Want;
		for (const FLink& Link : Links)
		{
			Want.Add(FString::Printf(TEXT("%s.%s->%s.%s"), *Nodes[Link.From]->GetName(), *Link.FromPin,
				*Nodes[Link.To]->GetName(), *Link.ToPin));
		}
		Want.Sort();
		const TArray<FString> Have = Ops::Links(*Graph);
		if (Have != Want)
		{
			OutError = FString::Printf(TEXT("the graph's links are [%s], the spec's are [%s]"),
				*FString::Join(Have, TEXT(", ")), *FString::Join(Want, TEXT(", ")));
			return false;
		}

		for (const TPair<FString, UEdGraphNode*>& Pair : Nodes)
		{
			if (Pair.Key != RootId)
			{
				OutNames.Add(Pair.Key, Pair.Value->GetName());
			}
		}
		return Ops::Compile(AnimBP, OutWarnings, OutError);
	}
}

FString UAnimGraphBuildTools::BuildAnimBlueprint(const FString& AssetPath, const FString& SpecJson)
{
	TSharedPtr<FJsonObject> Spec;
	if (!FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(SpecJson), Spec) || !Spec.IsValid())
	{
		return HeroToolJson::Fail(TEXT("the spec is not a JSON object"));
	}
	FString SkeletonPath, ParentClass;
	Spec->TryGetStringField(TEXT("skeleton"), SkeletonPath);
	Spec->TryGetStringField(TEXT("parent_class"), ParentClass);
	USkeleton* Skeleton = LoadObject<USkeleton>(nullptr, *SkeletonPath);
	if (!Skeleton)
	{
		return HeroToolJson::Fail(FString::Printf(TEXT("skeleton '%s' did not load"), *SkeletonPath));
	}

	FString Error;
	UAnimBlueprint* AnimBP = MotionTerminal::AnimGraphOps::CreateAnimBlueprint(AssetPath, Skeleton, ParentClass, Error);
	if (!AnimBP)
	{
		return HeroToolJson::Fail(Error);
	}

	TMap<FString, FString> Names;
	TArray<FString> Warnings;
	const bool bBuilt = Fill(*AnimBP, *Spec, Names, Warnings, Error);

	// Never saved here: a build reaches disk only after its description was accepted (SaveAnimBlueprint), so a
	// graph that is not the spec leaves no file. Built or not, the asset is in memory, unsaved; the caller deletes
	// what it does not accept (a delete here, inside the call that made it, is the delete that leaves a file).
	const TSharedRef<FJsonObject> Reply = MakeShared<FJsonObject>();
	Reply->SetBoolField(TEXT("ok"), bBuilt);
	Reply->SetStringField(TEXT("path"), AssetPath);
	Reply->SetBoolField(TEXT("saved"), false);
	if (!bBuilt)
	{
		Reply->SetBoolField(TEXT("created"), true);
		Reply->SetStringField(TEXT("error"), Error);
		return HeroToolJson::ToJson(Reply);
	}
	const TSharedRef<FJsonObject> NodeNames = MakeShared<FJsonObject>();
	for (const TPair<FString, FString>& Pair : Names)
	{
		NodeNames->SetStringField(Pair.Key, Pair.Value);
	}
	Reply->SetObjectField(TEXT("nodes"), NodeNames);
	TArray<TSharedPtr<FJsonValue>> WarningValues;
	for (const FString& Warning : Warnings)
	{
		WarningValues.Add(MakeShared<FJsonValueString>(Warning));
	}
	Reply->SetArrayField(TEXT("warnings"), WarningValues);
	return HeroToolJson::ToJson(Reply);
}

FString UAnimGraphBuildTools::SaveAnimBlueprint(const FString& AssetPath)
{
	// Found, not loaded: this saves the object a build left in memory, and must not quietly load an older file.
	FString Name;
	AssetPath.Split(TEXT("/"), nullptr, &Name, ESearchCase::CaseSensitive, ESearchDir::FromEnd);
	UAnimBlueprint* AnimBP = FindObject<UAnimBlueprint>(nullptr, *(AssetPath + TEXT(".") + Name));
	if (!AnimBP)
	{
		return HeroToolJson::Fail(FString::Printf(TEXT("%s is not in memory: nothing was built there in this session"),
			*AssetPath));
	}
	if (AnimBP->Status != BS_UpToDate && AnimBP->Status != BS_UpToDateWithWarnings)
	{
		return HeroToolJson::Fail(FString::Printf(TEXT("%s is not compiled (status %d) and is not saved"), *AssetPath,
			static_cast<int32>(AnimBP->Status)));
	}
	FString Error;
	if (!MotionTerminal::AnimGraphOps::Save(*AnimBP, Error))
	{
		return HeroToolJson::Fail(Error);
	}
	const TSharedRef<FJsonObject> Reply = MakeShared<FJsonObject>();
	Reply->SetBoolField(TEXT("ok"), true);
	Reply->SetStringField(TEXT("path"), AssetPath);
	Reply->SetBoolField(TEXT("saved"), true);
	return HeroToolJson::ToJson(Reply);
}
