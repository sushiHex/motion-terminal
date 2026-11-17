#include "CoreMinimal.h"

#if WITH_AUTOMATION_TESTS

#include "AnimGraphTopology.h"
#include "Animation/AnimBlueprint.h"
#include "Misc/AutomationTest.h"

// The canonical topology model is deterministic (same asset -> same string)
// and round-trips through JSON parse. Headless-safe: asset load only, no PIE, no RHI.

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FTopologyRoundTripTest,
	"MotionTerminal.Verify.TopologyRoundTrip",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::ProductFilter)

bool FTopologyRoundTripTest::RunTest(const FString& Parameters)
{
	// Parametrized over both ABPs. ABP_Unarmed has zero
	// pin bindings, so it alone never exercises the schemaVersion-2 KeySort bindings-determinism
	// logic (AnimGraphTopology.cpp) — ABP_MT_Locomotion has real bindings and closes that gap.
	// The body is asset-agnostic; only the asset path and per-assertion tag change per iteration.
	static const TCHAR* TestAssets[] = {
		TEXT("/Game/Characters/Mannequins/Anims/Unarmed/ABP_Unarmed"),
		TEXT("/Game/MT/ABP_MT_Locomotion"),
	};

	for (const TCHAR* AssetPath : TestAssets)
	{
		UAnimBlueprint* AnimBP = LoadObject<UAnimBlueprint>(nullptr, AssetPath);
		if (!TestNotNull(FString::Printf(TEXT("%s editor asset loads"), AssetPath), AnimBP)) { continue; }

		const FString First  = MotionTerminal::TopologyToString(MotionTerminal::BuildTopologyJson(*AnimBP));
		const FString Second = MotionTerminal::TopologyToString(MotionTerminal::BuildTopologyJson(*AnimBP));
		TestTrue(FString::Printf(TEXT("[%s] serialization is deterministic"), AssetPath), First == Second);
		TestTrue(FString::Printf(TEXT("[%s] serialization is non-trivial"), AssetPath), First.Len() > 200);
		TestTrue(FString::Printf(TEXT("[%s] contains the Locomotion machine"), AssetPath), First.Contains(TEXT("Locomotion")));

		TSharedPtr<FJsonObject> Parsed;
		TestTrue(FString::Printf(TEXT("[%s] round-trips through JSON parse"), AssetPath),
			MotionTerminal::ParseTopology(First, Parsed) && Parsed.IsValid());

		TestTrue(FString::Printf(TEXT("[%s] self-diff is empty"), AssetPath),
			MotionTerminal::DiffTopologyText(First, Second).IsEmpty());

		// Sanity: a mutated baseline produces a non-empty, labeled diff.
		FString Mutated = First.Replace(TEXT("Locomotion"), TEXT("Wrong-Machine"));
		const FString Diff = MotionTerminal::DiffTopologyText(Mutated, First);
		TestTrue(FString::Printf(TEXT("[%s] mutation produces a diff"), AssetPath), !Diff.IsEmpty());
		TestTrue(FString::Printf(TEXT("[%s] diff labels baseline-only lines"), AssetPath), Diff.Contains(TEXT("- ")));
		TestTrue(FString::Printf(TEXT("[%s] diff labels current-only lines"), AssetPath), Diff.Contains(TEXT("+ ")));

		// Order-blindness: a REORDERED variant of the serialized text — same line
		// multiset, different order (as if two states' players were swapped) — must be FLAGGED,
		// not silently accepted. The old multiset-only diff was order-blind: a false-negative
		// for the golden gate. Ordered/Reordered are both Join()-of-the-same-lines so any line
		// splitting quirk cancels, isolating the reorder as the only difference.
		{
			TArray<FString> Lines;
			First.ParseIntoArrayLines(Lines, /*bCullEmpty*/ false);
			int32 SwapIdx = INDEX_NONE;
			for (int32 i = 0; i + 1 < Lines.Num(); ++i)
			{
				if (!Lines[i].Equals(Lines[i + 1], ESearchCase::CaseSensitive)) { SwapIdx = i; break; }
			}
			if (TestTrue(FString::Printf(TEXT("[%s] serialized text has two distinct adjacent lines to reorder"), AssetPath),
				SwapIdx != INDEX_NONE))
			{
				const FString Ordered = FString::Join(Lines, TEXT("\n"));
				Lines.Swap(SwapIdx, SwapIdx + 1);
				const FString Reordered = FString::Join(Lines, TEXT("\n"));
				const FString ReorderDiff = MotionTerminal::DiffTopologyText(Ordered, Reordered);
				TestTrue(FString::Printf(TEXT("[%s] reordered same-multiset text is flagged with the exact move marker"), AssetPath),
					ReorderDiff.Equals(TEXT("~ lines reordered/moved (same line multiset)"), ESearchCase::CaseSensitive));
			}
		}
		// EOL normalization: equal inputs still produce an empty diff.
		TestTrue(FString::Printf(TEXT("[%s] equal inputs produce an empty diff"), AssetPath),
			MotionTerminal::DiffTopologyText(First, First).IsEmpty());
	}
	return true;
}

#endif // WITH_AUTOMATION_TESTS
