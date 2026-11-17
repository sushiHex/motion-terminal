#include "CoreMinimal.h"

#if WITH_AUTOMATION_TESTS

#include "AnimGraphNode_Base.h"
#include "Dom/JsonObject.h"
#include "Misc/AutomationTest.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "UObject/UnrealType.h"

// Every engine name the project's graph specs depend on resolves in THIS engine: each anim graph node class loads,
// and each property path exists inside its anim node struct. The list is Tests/engine_contract.json, generated from
// the specs themselves (Scripts/engine_contract.py), so a name a spec starts using is checked here in the same change.
//
// Why it exists: a struct value imported from text skips a member the engine no longer has, without an error. An
// engine upgrade that renames one would otherwise show up as a behaviour change in a built asset, much later.
// Headless-safe and content-free: it loads engine classes only.

namespace
{
	/** Whether `Path` ("Member", "Member.Sub", "Array[].Member") resolves inside Struct. OutWhy names the first step
	    that does not. */
	bool Resolves(const UStruct* Struct, const FString& Path, FString& OutWhy)
	{
		TArray<FString> Steps;
		Path.ParseIntoArray(Steps, TEXT("."));
		const UStruct* In = Struct;
		for (int32 i = 0; i < Steps.Num(); ++i)
		{
			FString Name = Steps[i];
			const bool bElement = Name.RemoveFromEnd(TEXT("[]"));
			const FProperty* Prop = In ? In->FindPropertyByName(FName(*Name)) : nullptr;
			if (!Prop)
			{
				OutWhy = FString::Printf(TEXT("no member '%s' on %s"), *Name,
					In ? *In->GetName() : TEXT("a value that is not a struct"));
				return false;
			}
			if (bElement)
			{
				const FArrayProperty* Array = CastField<FArrayProperty>(Prop);
				if (!Array)
				{
					OutWhy = FString::Printf(TEXT("'%s' is not an array"), *Name);
					return false;
				}
				Prop = Array->Inner;
			}
			const FStructProperty* Inner = CastField<FStructProperty>(Prop);
			In = Inner ? Inner->Struct.Get() : nullptr;
			if (!In && i < Steps.Num() - 1)
			{
				OutWhy = FString::Printf(TEXT("'%s' is not a struct, so '%s' cannot be under it"), *Steps[i], *Steps[i + 1]);
				return false;
			}
		}
		return Steps.Num() > 0;
	}
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FEngineContractTest,
	"MotionTerminal.Verify.EngineContract",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::ProductFilter)

bool FEngineContractTest::RunTest(const FString& Parameters)
{
	const FString File = FPaths::Combine(FPaths::ProjectPluginsDir(), TEXT("MotionTerminal"), TEXT("Tests"),
		TEXT("engine_contract.json"));
	FString Text;
	TSharedPtr<FJsonObject> Contract;
	if (!TestTrue(FString::Printf(TEXT("%s reads"), *File), FFileHelper::LoadFileToString(Text, *File))
		|| !TestTrue(TEXT("the contract parses"),
			FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), Contract) && Contract.IsValid()))
	{
		return false;
	}
	const TSharedPtr<FJsonObject>* Nodes = nullptr;
	if (!TestTrue(TEXT("the contract has anim_nodes"), Contract->TryGetObjectField(TEXT("anim_nodes"), Nodes)))
	{
		return false;
	}

	int32 Classes = 0;
	int32 Paths = 0;
	for (const TPair<FString, TSharedPtr<FJsonValue>>& Entry : (*Nodes)->Values)
	{
		++Classes;
		const UClass* Class = LoadObject<UClass>(nullptr, *Entry.Key);
		if (!Class || !Class->IsChildOf(UAnimGraphNode_Base::StaticClass()))
		{
			AddError(FString::Printf(TEXT("%s is not an anim graph node class in this engine"), *Entry.Key));
			continue;
		}
		const FStructProperty* NodeProp = Class->GetDefaultObject<UAnimGraphNode_Base>()->GetFNodeProperty();
		if (!NodeProp)
		{
			AddError(FString::Printf(TEXT("%s has no anim node struct"), *Entry.Key));
			continue;
		}
		const TArray<TSharedPtr<FJsonValue>>* List = nullptr;
		if (!Entry.Value.IsValid() || !Entry.Value->TryGetArray(List))
		{
			AddError(FString::Printf(TEXT("%s: its paths are not a list"), *Entry.Key));
			continue;
		}
		for (const TSharedPtr<FJsonValue>& Item : *List)
		{
			++Paths;
			FString Why;
			const FString Path = Item.IsValid() ? Item->AsString() : FString();
			if (!Resolves(NodeProp->Struct, Path, Why))
			{
				AddError(FString::Printf(TEXT("%s: '%s' does not resolve in %s (%s)"), *Class->GetName(), *Path,
					*NodeProp->Struct->GetName(), *Why));
			}
		}
	}
	// An empty or gutted file would pass the loop above without checking anything.
	TestTrue(FString::Printf(TEXT("the contract is not trivial (%d classes, %d paths)"), Classes, Paths),
		Classes >= 5 && Paths >= 20);
	return true;
}

#endif // WITH_AUTOMATION_TESTS
