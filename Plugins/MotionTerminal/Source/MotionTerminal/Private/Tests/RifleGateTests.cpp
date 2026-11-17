#include "CoreMinimal.h"

#if WITH_AUTOMATION_TESTS

#include "AnimGraphNode_Base.h"
#include "AnimGraphNode_LayeredBoneBlend.h"
#include "AnimGraphNode_SaveCachedPose.h"
#include "AnimGraphTopology.h"
#include "Animation/AnimBlueprint.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphPin.h"
#include "K2Node_VariableGet.h"
#include "Misc/AutomationTest.h"

// ============================================================================================
// Is the rifle-mode branch's blend weight ACTUALLY DRIVEN BY RifleModeAlpha?
// ============================================================================================
// THE DEFECT THIS EXISTS FOR (docs/proof-test-cannot-find-defect-ste.md). The branch was first authored with the weight as a PIN
// BINDING — LayeredBoneBlend.BlendWeights_0 <- property-access path "RifleModeAlpha". That binding does
// not compile: BlendWeights is a TArray<float>, the path resolved against the ARRAY rather than element
// 0, and the compiler emitted "Cannot copy property (float -> TArray)" and dropped the copy. The pin kept
// its literal default of 1.000000, so RifleModeAlpha was inert and the rifle branch was hard-wired ON for
// every character in every shot.
//
// The entire suite passed over that graph: default 23/0, pie 22/0. Pie.AimOffset went green at 79.4 deg
// on all three rigs because it measures the aim RESPONSE — the delta between aim=0 and aim=0.9 — and both
// of those poses sit on whatever base the branch supplies. A test that differences two poses on the SAME
// base is structurally incapable of seeing the base. That is this project's signature fault once more:
// a check that cannot observe what it claims to observe.
//
// WHY A STRUCTURAL TEST AND NOT ONLY A RUNTIME ONE. Scripts/pie_riflegate_verify_drive.py measures the
// behaviour (toggling the var swings the upper body 97.7 deg against a 0.000 control while the lower body
// stays under its own noise floor) and is the richer evidence. But it needs PIE and a real RHI, so it is
// not a per-commit gate. GoldenDiff cannot cover this either: schema v2 records node class, title, asset
// and pin BINDINGS — not intra-graph pin WIRING. It would notice the variable-get node being deleted, but
// severing the LINK while leaving the node parked in the graph restores the exact 1.000-default defect and
// diffs IDENTICAL. This test closes precisely that gap, headless and in the default tier.
//
// It asserts three things, and the third is the one that would have caught the original bug:
//   1. the rifle LayeredBoneBlend's BlendWeights_0 pin is LINKED (not left on its literal default);
//   2. the link resolves to a variable-get of RifleModeAlpha (not some other float);
//   3. that pin carries NO property-access binding — because a binding there is the failure mode, and a
//      re-introduced one would silently out-live this file's other two assertions.
// ============================================================================================

namespace
{
	constexpr const TCHAR* kLocomotionABP = TEXT("/Game/MT/ABP_MT_Locomotion");
	constexpr const TCHAR* kRifleCacheName = TEXT("RifleModePose");
	constexpr const TCHAR* kRifleWeightVar = TEXT("RifleModeAlpha");
	constexpr const TCHAR* kWeightPinName = TEXT("BlendWeights_0");

	/** The SaveCachedPose with this cache name, or null. Its own function so the inner loop can return
	    the match directly — a sentinel plus a paired inner/outer break says the same thing with more
	    moving parts. AnimBP.FunctionGraphs is the same entry point AnimGraphTopology.cpp walks to build
	    the golden baseline, so this test and that gate see the same graph set by construction. */
	const UAnimGraphNode_SaveCachedPose* FindCacheByName(const UAnimBlueprint& AnimBP, const TCHAR* CacheName)
	{
		for (const UEdGraph* Graph : AnimBP.FunctionGraphs)
		{
			if (!Graph) { continue; }
			for (const UEdGraphNode* Node : Graph->Nodes)
			{
				const UAnimGraphNode_SaveCachedPose* Cache = Cast<UAnimGraphNode_SaveCachedPose>(Node);
				if (Cache && Cache->CacheName.Equals(CacheName, ESearchCase::CaseSensitive))
				{
					return Cache;
				}
			}
		}
		return nullptr;
	}

	/** The rifle blend is identified by what it FEEDS, not by node index or position: the SaveCachedPose
	    named 'RifleModePose'. Index/position would silently retarget onto the UpperBody-slot
	    LayeredBoneBlend, which also has a BlendWeights_0 pin and would pass every assertion below while
	    testing the wrong node. */
	UAnimGraphNode_LayeredBoneBlend* FindRifleBlend(const UAnimBlueprint& AnimBP, FString& OutWhy)
	{
		const UAnimGraphNode_SaveCachedPose* RifleCache = FindCacheByName(AnimBP, kRifleCacheName);
		if (!RifleCache)
		{
			OutWhy = FString::Printf(
				TEXT("no SaveCachedPose named '%s' in %s — the rifle-mode branch is gone entirely, not just unwired"),
				kRifleCacheName, kLocomotionABP);
			return nullptr;
		}

		// Walk back through the cache's pose input to whatever produces it.
		for (UEdGraphPin* Pin : RifleCache->Pins)
		{
			if (!Pin || Pin->Direction != EGPD_Input) { continue; }
			for (UEdGraphPin* Linked : Pin->LinkedTo)
			{
				if (!Linked || !Linked->GetOwningNodeUnchecked()) { continue; }
				if (UAnimGraphNode_LayeredBoneBlend* Blend =
						Cast<UAnimGraphNode_LayeredBoneBlend>(Linked->GetOwningNode()))
				{
					return Blend;
				}
			}
		}
		OutWhy = FString::Printf(
			TEXT("SaveCachedPose '%s' is not fed by a LayeredBoneBlend — the rifle pose is being produced by "
				 "something else, so the masked-upper-body architecture this project documents is not what is "
				 "actually in the graph"), kRifleCacheName);
		return nullptr;
	}
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FRifleGateWiredTest,
	"MotionTerminal.Verify.RifleGateWired",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::ProductFilter)

bool FRifleGateWiredTest::RunTest(const FString& Parameters)
{
	UAnimBlueprint* AnimBP = LoadObject<UAnimBlueprint>(nullptr, kLocomotionABP);
	if (!TestNotNull(TEXT("ABP_MT_Locomotion editor asset loads"), AnimBP)) { return false; }

	FString Why;
	UAnimGraphNode_LayeredBoneBlend* Blend = FindRifleBlend(*AnimBP, Why);
	if (!Blend)
	{
		AddError(FString::Printf(TEXT("[RifleGate] %s"), *Why));
		return false;
	}

	UEdGraphPin* WeightPin = nullptr;
	for (UEdGraphPin* Pin : Blend->Pins)
	{
		if (Pin && Pin->Direction == EGPD_Input && Pin->PinName == FName(kWeightPinName))
		{
			WeightPin = Pin;
			break;
		}
	}
	if (!WeightPin)
	{
		AddError(FString::Printf(
			TEXT("[RifleGate] the rifle LayeredBoneBlend has no '%s' input pin — it has no blend weight to "
				 "drive at all, so the branch cannot be gated"), kWeightPinName));
		return false;
	}

	// (1) LINKED, not left on a literal. This is the assertion that fails closed on the original defect:
	// the broken pin binding left this pin unlinked with DefaultValue "1.000000" — branch permanently on.
	if (WeightPin->LinkedTo.Num() == 0)
	{
		AddError(FString::Printf(
			TEXT("[RifleGate] RIFLE BRANCH IS NOT GATED: '%s' has no link, so the node reads its literal "
				 "default of '%s' on every frame and %s drives nothing. At a stuck 1.0 every character in "
				 "every shot renders with MF_Rifle_Idle_ADS blended over the upper body. Wire a variable-get "
				 "of %s into this pin — do NOT use a pin binding, which is how this broke the first time "
				 "(BlendWeights is a TArray<float>, so a property-access path resolves against the array and "
				 "the compiler drops the copy with 'Cannot copy property (float -> TArray)')"),
			kWeightPinName, *WeightPin->DefaultValue, kRifleWeightVar, kRifleWeightVar));
		return false;
	}

	// (2) The link resolves to a variable-get of the RIGHT variable. A link to some other float would
	// satisfy (1) while gating on the wrong thing.
	bool bFoundVar = false;
	FString SawInstead;
	for (const UEdGraphPin* Linked : WeightPin->LinkedTo)
	{
		if (!Linked || !Linked->GetOwningNodeUnchecked()) { continue; }
		const UEdGraphNode* Src = Linked->GetOwningNode();
		if (const UK2Node_VariableGet* VarGet = Cast<UK2Node_VariableGet>(Src))
		{
			const FName VarName = VarGet->VariableReference.GetMemberName();
			if (VarName == FName(kRifleWeightVar)) { bFoundVar = true; break; }
			SawInstead = VarName.ToString();
		}
		else
		{
			SawInstead = Src->GetClass()->GetName();
		}
	}
	if (!bFoundVar)
	{
		AddError(FString::Printf(
			TEXT("[RifleGate] '%s' is linked, but not to a get of '%s' (saw '%s'). The branch would be gated "
				 "on the wrong input, and every driver in Scripts/ writes %s expecting it to be the switch"),
			kWeightPinName, kRifleWeightVar, *SawInstead, kRifleWeightVar));
		return false;
	}

	// (3) NO property-access binding on this pin, via the SAME reflection walk that records bindings for
	// the golden baseline. Sharing it is deliberate: that walk is the mechanism whose miswiring caused
	// this defect, so it should exist once — see MotionTerminal::GetNodePropertyBindings.
	TMap<FString, FString> Bindings;
	MotionTerminal::GetNodePropertyBindings(*Blend, Bindings);
	if (const FString* BoundPath = Bindings.Find(kWeightPinName))
	{
		AddError(FString::Printf(
			TEXT("[RifleGate] '%s' carries a property-access BINDING ('%s') as well as its link. That "
				 "binding is the original defect: it cannot compile against a TArray<float> element, the "
				 "compiler drops it with 'Cannot copy property (float -> TArray)', and the pin silently "
				 "falls back to its literal. Clear the binding and keep the variable-get link"),
			kWeightPinName, **BoundPath));
		return false;
	}

	AddInfo(FString::Printf(
		TEXT("[RifleGate] '%s' is linked to a get of '%s' with no competing property binding — the rifle "
			 "branch is gated by the variable the drivers write. Runtime corroboration (upper body swings "
			 "97.7 deg on toggle, lower body stays under its own control) is Scripts/pie_riflegate_verify_drive.py"),
		kWeightPinName, kRifleWeightVar));
	return true;
}

#endif // WITH_AUTOMATION_TESTS
