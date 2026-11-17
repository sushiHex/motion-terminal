#include "AnimGraphTools.h"

#include "AnimGraphDescribe.h"
#include "AnimGraphTopology.h"
#include "HeroToolJson.h"
#include "Misc/EngineVersion.h"
#include "Misc/FileHelper.h"
#include "HAL/FileManager.h"

#include "Animation/AnimBlueprint.h"
#include "AnimGraphNode_AssetPlayerBase.h"
#include "AnimStateNodeBase.h"
#include "AnimStateTransitionNode.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"

FString UAnimGraphTools::Ping()
{
	return FString::Printf(TEXT("pong — UE %s"), *FEngineVersion::Current().ToString());
}

namespace MotionTerminalPrivate
{
	static void DumpGraph(const UEdGraph* Graph, int32 Indent, FStringBuilderBase& Out)
	{
		const FString Pad = FString::ChrN(Indent * 2, TEXT(' '));
		for (const UEdGraphNode* Node : Graph->Nodes)
		{
			Out.Appendf(TEXT("%s- [%s] \"%s\"\n"), *Pad, *Node->GetClass()->GetName(),
				*Node->GetNodeTitle(ENodeTitleType::ListView).ToString());

			if (const UAnimGraphNode_AssetPlayerBase* Player = Cast<UAnimGraphNode_AssetPlayerBase>(Node))
			{
				if (const UObject* Asset = Player->GetAnimationAsset())
				{
					Out.Appendf(TEXT("%s    asset: %s\n"), *Pad, *Asset->GetPathName());
				}
			}

			if (const UAnimStateTransitionNode* Trans = Cast<UAnimStateTransitionNode>(Node))
			{
				const UAnimStateNodeBase* Prev = Trans->GetPreviousState();
				const UAnimStateNodeBase* Next = Trans->GetNextState();
				Out.Appendf(TEXT("%s    %s -> %s\n"), *Pad,
					Prev ? *Prev->GetStateName() : TEXT("?"),
					Next ? *Next->GetStateName() : TEXT("?"));
			}

			for (const UEdGraph* Sub : Node->GetSubGraphs())
			{
				if (Sub)
				{
					Out.Appendf(TEXT("%s  graph \"%s\":\n"), *Pad, *Sub->GetName());
					DumpGraph(Sub, Indent + 2, Out);
				}
			}
		}
	}
}

FString UAnimGraphTools::ReadAnimGraph(const FString& AnimBlueprintPath)
{
	UAnimBlueprint* AnimBP = LoadObject<UAnimBlueprint>(nullptr, *AnimBlueprintPath);
	if (!AnimBP)
	{
		return FString::Printf(TEXT("ERROR: could not load AnimBlueprint at '%s'"), *AnimBlueprintPath);
	}

	TStringBuilder<4096> Out;
	Out.Appendf(TEXT("AnimBlueprint: %s\nTargetSkeleton: %s\n"), *AnimBP->GetPathName(),
		AnimBP->TargetSkeleton ? *AnimBP->TargetSkeleton->GetPathName() : TEXT("None"));

	static const FName AnimGraphName(TEXT("AnimGraph"));
	for (const UEdGraph* Graph : AnimBP->FunctionGraphs)
	{
		if (Graph && Graph->GetFName() == AnimGraphName)
		{
			Out.Appendf(TEXT("AnimGraph:\n"));
			MotionTerminalPrivate::DumpGraph(Graph, 1, Out);
		}
	}
	return Out.ToString();
}

FString UAnimGraphTools::SnapshotBaseline(const FString& AnimBlueprintPath, const FString& BaselineName)
{
	UAnimBlueprint* AnimBP = LoadObject<UAnimBlueprint>(nullptr, *AnimBlueprintPath);
	if (!AnimBP)
	{
		return FString::Printf(TEXT("ERROR: could not load AnimBlueprint at '%s'"), *AnimBlueprintPath);
	}

	const FString Text = MotionTerminal::TopologyToString(MotionTerminal::BuildTopologyJson(*AnimBP));
	const FString Path = MotionTerminal::BaselinePath(BaselineName);
	IFileManager::Get().MakeDirectory(*MotionTerminal::BaselineDir(), /*Tree*/ true);
	// Force UTF-8 without BOM: a future non-ASCII node title must not silently flip the file
	// to UTF-16 (SaveStringToFile's default when non-ASCII), which git would treat as binary.
	if (!FFileHelper::SaveStringToFile(Text, *Path, FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
	{
		return FString::Printf(TEXT("ERROR: could not write '%s'"), *Path);
	}
	return FString::Printf(TEXT("ok: wrote %s (%d chars)"), *Path, Text.Len());
}

FString UAnimGraphTools::DiffAnimGraph(const FString& AnimBlueprintPath, const FString& BaselineName)
{
	UAnimBlueprint* AnimBP = LoadObject<UAnimBlueprint>(nullptr, *AnimBlueprintPath);
	if (!AnimBP)
	{
		return FString::Printf(TEXT("ERROR: could not load AnimBlueprint at '%s'"), *AnimBlueprintPath);
	}

	FString BaselineText;
	const FString Path = MotionTerminal::BaselinePath(BaselineName);
	if (!FFileHelper::LoadFileToString(BaselineText, *Path))
	{
		return FString::Printf(TEXT("ERROR: no baseline at '%s' (run SnapshotBaseline first)"), *Path);
	}

	const FString Current = MotionTerminal::TopologyToString(MotionTerminal::BuildTopologyJson(*AnimBP));
	const FString Diff = MotionTerminal::DiffTopologyText(BaselineText, Current);
	return Diff.IsEmpty() ? TEXT("IDENTICAL") : Diff;
}

FString UAnimGraphTools::DescribeAnimGraph(const FString& AnimBlueprintPath, const FString& PathsJson)
{
	UAnimBlueprint* AnimBP = LoadObject<UAnimBlueprint>(nullptr, *AnimBlueprintPath);
	if (!AnimBP)
	{
		return HeroToolJson::Fail(FString::Printf(TEXT("could not load AnimBlueprint at '%s'"), *AnimBlueprintPath));
	}
	TMap<FString, TArray<FString>> Paths;
	if (!PathsJson.TrimStartAndEnd().IsEmpty())
	{
		TSharedPtr<FJsonObject> Asked;
		if (!MotionTerminal::ParseTopology(PathsJson, Asked))
		{
			return HeroToolJson::Fail(TEXT("PathsJson is not a JSON object of {node id: [property path]}"));
		}
		for (const TPair<FString, TSharedPtr<FJsonValue>>& Pair : Asked->Values)
		{
			const TArray<TSharedPtr<FJsonValue>>* Items = nullptr;
			if (!Pair.Value.IsValid() || !Pair.Value->TryGetArray(Items))
			{
				return HeroToolJson::Fail(FString::Printf(TEXT("PathsJson['%s'] is not a list of paths"), *Pair.Key));
			}
			TArray<FString>& List = Paths.Add(Pair.Key);
			for (const TSharedPtr<FJsonValue>& Item : *Items)
			{
				List.Add(Item.IsValid() ? Item->AsString() : FString());
			}
		}
	}
	return HeroToolJson::ToJson(MotionTerminal::DescribeAnimGraphJson(*AnimBP, Paths));
}
