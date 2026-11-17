#include "AnimGraphTopology.h"

#include "Animation/AnimBlueprint.h"
#include "AnimGraphNode_Base.h"
#include "AnimGraphNode_AssetPlayerBase.h"
#include "AnimStateNodeBase.h"
#include "AnimStateTransitionNode.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "Misc/Paths.h"
#include "Policies/PrettyJsonPrintPolicy.h"
#include "Policies/CondensedJsonPrintPolicy.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

namespace
{
	using namespace MotionTerminal;

	TSharedRef<FJsonObject> GraphToJson(const UEdGraph& Graph);

	TSharedRef<FJsonObject> NodeToJson(const UEdGraphNode& Node)
	{
		TSharedRef<FJsonObject> Obj = MakeShared<FJsonObject>();
		Obj->SetStringField(TEXT("class"), Node.GetClass()->GetName());
		Obj->SetStringField(TEXT("title"), Node.GetNodeTitle(ENodeTitleType::ListView).ToString());

		if (const UAnimGraphNode_AssetPlayerBase* Player = Cast<UAnimGraphNode_AssetPlayerBase>(&Node))
		{
			if (const UObject* Asset = Player->GetAnimationAsset())
			{
				Obj->SetStringField(TEXT("asset"), Asset->GetPathName());
			}
		}
		// Pin bindings (property-access wiring, e.g. BlendSpace X <- Speed) are the schemaVersion-2
		// addition: they live on the anim node's instanced binding object, in a private-but-UPROPERTY
		// TMap<FName, FAnimGraphNodePropertyBinding> named "PropertyBindings". We reach it purely by
		// reflection, so we never need the forward-declared-only UAnimGraphNodeBinding type to be
		// complete nor any Internal/Private engine header: fetch the binding object through the node's
		// own "Binding" FObjectProperty (yielding a complete UObject*), then walk its map, reading each
		// value as the PUBLIC FAnimGraphNodePropertyBinding struct. Emitted only when >=1 pin is bound.
		if (const UAnimGraphNode_Base* AnimNode = Cast<UAnimGraphNode_Base>(&Node))
		{
			TMap<FString, FString> Bindings;   // pinName -> dotted property path
			MotionTerminal::GetNodePropertyBindings(*AnimNode, Bindings);
			if (Bindings.Num() > 0)
			{
				Bindings.KeySort([](const FString& A, const FString& B)
					{ return A.Compare(B, ESearchCase::CaseSensitive) < 0; });
				TSharedRef<FJsonObject> BindObj = MakeShared<FJsonObject>();
				for (const TPair<FString, FString>& P : Bindings) { BindObj->SetStringField(P.Key, P.Value); }
				Obj->SetObjectField(TEXT("bindings"), BindObj);
			}
		}
		if (const UAnimStateTransitionNode* Trans = Cast<UAnimStateTransitionNode>(&Node))
		{
			const UAnimStateNodeBase* Prev = Trans->GetPreviousState();
			const UAnimStateNodeBase* Next = Trans->GetNextState();
			Obj->SetStringField(TEXT("from"), Prev ? Prev->GetStateName() : TEXT("?"));
			Obj->SetStringField(TEXT("to"),   Next ? Next->GetStateName() : TEXT("?"));
		}

		TArray<TSharedPtr<FJsonValue>> SubGraphs;
		for (const UEdGraph* Sub : Node.GetSubGraphs())
		{
			if (Sub) { SubGraphs.Add(MakeShared<FJsonValueObject>(GraphToJson(*Sub))); }
		}
		if (SubGraphs.Num() > 0) { Obj->SetArrayField(TEXT("graphs"), SubGraphs); }
		return Obj;
	}

	FString CondensedValueString(const TSharedRef<FJsonObject>& Obj)
	{
		FString Out;
		TSharedRef<TJsonWriter<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>> Writer =
			TJsonWriterFactory<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>::Create(&Out);
		FJsonSerializer::Serialize(Obj, Writer);
		return Out;
	}

	TSharedRef<FJsonObject> GraphToJson(const UEdGraph& Graph)
	{
		TSharedRef<FJsonObject> Obj = MakeShared<FJsonObject>();
		Obj->SetStringField(TEXT("name"), Graph.GetName());

		// Sort nodes by their condensed serialization: deterministic output regardless of
		// authoring/GUID order, which is what makes the golden diff stable.
		TArray<TPair<FString, TSharedRef<FJsonObject>>> Sorted;
		for (const UEdGraphNode* Node : Graph.Nodes)
		{
			if (!Node) { continue; }
			TSharedRef<FJsonObject> NodeObj = NodeToJson(*Node);
			Sorted.Emplace(CondensedValueString(NodeObj), NodeObj);
		}
		Sorted.Sort([](const TPair<FString, TSharedRef<FJsonObject>>& A,
		               const TPair<FString, TSharedRef<FJsonObject>>& B)
		               { return A.Key.Compare(B.Key, ESearchCase::CaseSensitive) < 0; });

		TArray<TSharedPtr<FJsonValue>> Nodes;
		for (const TPair<FString, TSharedRef<FJsonObject>>& Pair : Sorted)
		{
			Nodes.Add(MakeShared<FJsonValueObject>(Pair.Value));
		}
		Obj->SetArrayField(TEXT("nodes"), Nodes);
		return Obj;
	}
}

namespace MotionTerminal
{
	// Pin bindings (property-access wiring, e.g. BlendSpace X <- Speed) are the schemaVersion-2
	// addition: they live on the anim node's instanced binding object, in a private-but-UPROPERTY
	// TMap<FName, FAnimGraphNodePropertyBinding> named "PropertyBindings". We reach it purely by
	// reflection, so we never need the forward-declared-only UAnimGraphNodeBinding type to be
	// complete nor any Internal/Private engine header: fetch the binding object through the node's
	// own "Binding" FObjectProperty (yielding a complete UObject*), then walk its map, reading each
	// value as the PUBLIC FAnimGraphNodePropertyBinding struct.
	//
	// Guard-clause style on purpose: each reflection step is a place the engine's layout could move.
	// A moved layout is an ERROR, not an empty result: returning silently would serialize every node
	// with no bindings, and a regenerated baseline would then pin that loss as correct. The error log
	// fails any automation test that reaches it, and names the step that no longer matches.
	// A node with no binding object at all is legitimately unbound and stays silent.
	void LayoutMoved(const UAnimGraphNode_Base& Node, const TCHAR* Step)
	{
		UE_LOG(LogTemp, Error, TEXT("MotionTerminal topology: engine binding layout changed (%s) on %s; bindings not read."),
			Step, *Node.GetClass()->GetName());
	}

	void GetNodePropertyBindings(const UAnimGraphNode_Base& Node, TMap<FString, FString>& OutBindings)
	{
		const FObjectPropertyBase* BindingProp = CastField<FObjectPropertyBase>(
			Node.GetClass()->FindPropertyByName(TEXT("Binding")));
		if (!BindingProp) { return LayoutMoved(Node, TEXT("no 'Binding' object property")); }

		const UObject* BindingObj = BindingProp->GetObjectPropertyValue_InContainer(&Node);
		if (!BindingObj) { return; }

		const FMapProperty* MapProp = CastField<FMapProperty>(
			BindingObj->GetClass()->FindPropertyByName(TEXT("PropertyBindings")));
		if (!MapProp) { return LayoutMoved(Node, TEXT("no 'PropertyBindings' map on the binding object")); }

		// Only reinterpret values as FAnimGraphNodePropertyBinding when the map's value type
		// actually IS that struct (a raw reinterpret of any other layout would be UB).
		const FStructProperty* ValStructProp = CastField<FStructProperty>(MapProp->ValueProp);
		if (!ValStructProp || ValStructProp->Struct != FAnimGraphNodePropertyBinding::StaticStruct())
		{
			return LayoutMoved(Node, TEXT("'PropertyBindings' values are not FAnimGraphNodePropertyBinding"));
		}

		FScriptMapHelper MapHelper(MapProp, MapProp->ContainerPtrToValuePtr<void>(BindingObj));
		for (int32 i = 0; i < MapHelper.GetMaxIndex(); ++i)
		{
			if (!MapHelper.IsValidIndex(i)) { continue; }
			// The value memory IS a FAnimGraphNodePropertyBinding (public struct) — safe cast.
			const FAnimGraphNodePropertyBinding* Val =
				reinterpret_cast<const FAnimGraphNodePropertyBinding*>(MapHelper.GetValuePtr(i));
			if (!Val || !Val->bIsBound) { continue; }   // only actually-bound pins
			OutBindings.Add(Val->PropertyName.ToString(), FString::Join(Val->PropertyPath, TEXT(".")));
		}
	}

	TSharedRef<FJsonObject> BuildTopologyJson(const UAnimBlueprint& AnimBP)
	{
		TSharedRef<FJsonObject> Root = MakeShared<FJsonObject>();
		// First field: format version, so a future baseline reader can detect/migrate schema
		// changes. Bump when the serialized topology shape changes.
		Root->SetNumberField(TEXT("schemaVersion"), 2);
		Root->SetStringField(TEXT("animBlueprint"), AnimBP.GetPathName());
		Root->SetStringField(TEXT("targetSkeleton"),
			AnimBP.TargetSkeleton ? AnimBP.TargetSkeleton->GetPathName() : TEXT("None"));

		static const FName AnimGraphName(TEXT("AnimGraph"));
		TArray<TSharedPtr<FJsonValue>> Graphs;
		for (const UEdGraph* Graph : AnimBP.FunctionGraphs)
		{
			if (Graph && Graph->GetFName() == AnimGraphName)
			{
				Graphs.Add(MakeShared<FJsonValueObject>(GraphToJson(*Graph)));
			}
		}
		Root->SetArrayField(TEXT("graphs"), Graphs);
		return Root;
	}

	FString TopologyToString(const TSharedRef<FJsonObject>& Topology)
	{
		FString Out;
		TSharedRef<TJsonWriter<TCHAR, TPrettyJsonPrintPolicy<TCHAR>>> Writer =
			TJsonWriterFactory<TCHAR, TPrettyJsonPrintPolicy<TCHAR>>::Create(&Out);
		FJsonSerializer::Serialize(Topology, Writer);
		return Out;
	}

	bool ParseTopology(const FString& JsonText, TSharedPtr<FJsonObject>& OutObject)
	{
		const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(JsonText);
		return FJsonSerializer::Deserialize(Reader, OutObject) && OutObject.IsValid();
	}

	FString DiffTopologyText(const FString& BaselineText, const FString& CurrentText)
	{
		// (a) EOL-normalize (\r\n -> \n) and require EXACT equality for an empty diff. git's
		// autocrlf may rewrite line endings on checkout; that alone must never fail the gate.
		const FString NormBaseline = BaselineText.Replace(TEXT("\r\n"), TEXT("\n"));
		const FString NormCurrent  = CurrentText.Replace(TEXT("\r\n"), TEXT("\n"));
		if (NormBaseline.Equals(NormCurrent, ESearchCase::CaseSensitive)) { return FString(); }

		auto ToLines = [](const FString& Text)
		{
			TArray<FString> Lines;
			Text.ParseIntoArrayLines(Lines, /*bCullEmpty*/ false);
			return Lines;
		};
		const TArray<FString> A = ToLines(NormBaseline);
		const TArray<FString> B = ToLines(NormCurrent);

		TMap<FString, int32> Counts;
		for (const FString& L : A) { Counts.FindOrAdd(L)++; }
		for (const FString& L : B) { Counts.FindOrAdd(L)--; }

		TArray<FString> Removed, Added;
		for (const TPair<FString, int32>& P : Counts)
		{
			for (int32 i = 0; i <  P.Value; ++i) { Removed.Add(FString::Printf(TEXT("- %s"), *P.Key)); }
			for (int32 i = 0; i > P.Value; --i) { Added.Add(FString::Printf(TEXT("+ %s"), *P.Key)); }
		}
		// (b) content changed: report the multiset diff exactly as before.
		if (Removed.Num() > 0 || Added.Num() > 0)
		{
			Removed.Sort();
			Added.Sort();
			return FString::Join(Removed, TEXT("\n")) + (Removed.Num() && Added.Num() ? TEXT("\n") : TEXT(""))
				+ FString::Join(Added, TEXT("\n"));
		}
		// (c) same line multiset but the normalized texts differ => a pure reordering/move
		// (e.g. two states' players swapped). Flag it — the old multiset diff was order-blind.
		return TEXT("~ lines reordered/moved (same line multiset)");
	}

	FString BaselineDir()
	{
		return FPaths::Combine(FPaths::ProjectPluginsDir(), TEXT("MotionTerminal"), TEXT("Tests"), TEXT("Baselines"));
	}

	FString BaselinePath(const FString& BaselineName)
	{
		const FString File = BaselineName.EndsWith(TEXT(".json")) ? BaselineName : BaselineName + TEXT(".json");
		return FPaths::Combine(BaselineDir(), File);
	}
}
