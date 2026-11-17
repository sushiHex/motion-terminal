#include "CoreMinimal.h"

#if WITH_AUTOMATION_TESTS

#include "AnimGraphTopology.h"
#include "Animation/AnimBlueprint.h"
#include "Misc/AutomationTest.h"
#include "Misc/FileHelper.h"
#include "Misc/PackageName.h"

namespace
{
	// The one golden-diff row: AssetPath's current topology must byte-match the committed baseline BaselineName.
	// bLocalOnly rows SKIP (with the marker run_verification counts) when the asset package is absent.
	bool RunGoldenDiff(FAutomationTestBase& T, const TCHAR* AssetPath, const TCHAR* BaselineName, bool bLocalOnly)
	{
		if (bLocalOnly && !FPackageName::DoesPackageExist(AssetPath))
		{
			T.AddInfo(FString::Printf(TEXT("SKIP (localOnly asset absent on this machine): %s"), AssetPath));
			return true;
		}

		const FString Baseline = MotionTerminal::BaselinePath(BaselineName);
		FString BaselineText;
		if (!FFileHelper::LoadFileToString(BaselineText, *Baseline))
		{
			T.AddError(FString::Printf(TEXT("Missing baseline '%s'. Generate it: call "
				"MotionTerminal.AnimGraphTools/SnapshotBaseline over MCP with "
				"animBlueprintPath=%s, "
				"baselineName=%s — then commit the JSON."), *Baseline, AssetPath, BaselineName));
			return false;
		}

		UAnimBlueprint* AnimBP = LoadObject<UAnimBlueprint>(nullptr, AssetPath);
		if (!T.TestNotNull(FString::Printf(TEXT("%s editor asset loads"), *FPackageName::GetShortName(AssetPath)), AnimBP))
		{
			return false;
		}

		const FString Current = MotionTerminal::TopologyToString(MotionTerminal::BuildTopologyJson(*AnimBP));
		const FString Diff = MotionTerminal::DiffTopologyText(BaselineText, Current);
		if (!Diff.IsEmpty())
		{
			T.AddError(FString::Printf(TEXT("Topology drifted from baseline %s:\n%s"), BaselineName, *Diff));
			return false;
		}
		return true;
	}
}

// The per-commit golden gate (docs/verification-design.md) — current ABP_Unarmed topology
// must byte-match the committed baseline (exact equality after EOL normalization; a pure
// reordering of otherwise-identical lines is now flagged, not silently accepted). Regenerate
// deliberately via the SnapshotBaseline MCP tool; review baseline diffs like code.

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FGoldenDiffUnarmedTest,
	"MotionTerminal.GoldenDiff.Unarmed",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::ProductFilter)

bool FGoldenDiffUnarmedTest::RunTest(const FString& Parameters)
{
	return RunGoldenDiff(*this, TEXT("/Game/Characters/Mannequins/Anims/Unarmed/ABP_Unarmed"), TEXT("abp_unarmed"), false);
}

// The Template's per-commit golden gate — current ABP_MT_Locomotion topology
// must byte-match the committed baseline.

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FGoldenDiffMTLocomotionTest,
	"MotionTerminal.GoldenDiff.MTLocomotion",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::ProductFilter)

bool FGoldenDiffMTLocomotionTest::RunTest(const FString& Parameters)
{
	return RunGoldenDiff(*this, TEXT("/Game/MT/ABP_MT_Locomotion"), TEXT("abp_mt_locomotion"), false);
}

// The per-commit golden gate for the Khaimera child ABP (the asset that overrides the Template's clips).
// MACHINE-LOCAL row: /Game/MT/Khaimera/ABP_MT_Khaimera references gitignored Fab (Khaimera) content, so the
// asset is NOT committed — the baseline JSON is. When the package is absent (fresh clone) the row SKIPs so the
// tier stays green; on a machine that has the content, a full run must show ZERO skips. NOTE: the child
// INHERITS the Template's AnimGraph and adds no local graph nodes, so this baseline captures the child's
// identity + Khaimera target skeleton, not a rich topology — the clip-override proof is the runtime
// Mechanism-A probe, not this golden. This row guards that the child still loads,
// stays parented to the Template, and keeps the Khaimera skeleton.

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FGoldenDiffMTKhaimeraTest,
	"MotionTerminal.GoldenDiff.MTKhaimera",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::ProductFilter)

bool FGoldenDiffMTKhaimeraTest::RunTest(const FString& Parameters)
{
	// Machine-local: the Khaimera-referencing child ABP is gitignored — skip when absent (fresh clones stay green).
	return RunGoldenDiff(*this, TEXT("/Game/MT/Khaimera/ABP_MT_Khaimera"), TEXT("abp_mt_khaimera"), true);
}

// The golden gates for the post-process dynamics graphs (grip layer -> Control Rig -> output), one per hero that
// ships one. These graphs are BUILT, deleted and rebuilt from scratch by Scripts/dynamics_graph.py, and two
// consecutive builds serialize identically, so a diff here means the builder, or the engine under it, changed
// what it produces. MACHINE-LOCAL rows for the same reason as MTKhaimera: each asset binds a licensed hero's
// skeleton and is not committed; the baseline JSON is.

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FGoldenDiffMTKhaimeraDynamicsTest,
	"MotionTerminal.GoldenDiff.MTKhaimeraDynamics",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::ProductFilter)

bool FGoldenDiffMTKhaimeraDynamicsTest::RunTest(const FString& Parameters)
{
	return RunGoldenDiff(*this, TEXT("/Game/MT/Khaimera/ABP_MT_KhaimeraDynamics"), TEXT("abp_mt_khaimeradynamics"), true);
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FGoldenDiffMTAuroraDynamicsTest,
	"MotionTerminal.GoldenDiff.MTAuroraDynamics",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::ProductFilter)

bool FGoldenDiffMTAuroraDynamicsTest::RunTest(const FString& Parameters)
{
	return RunGoldenDiff(*this, TEXT("/Game/MT/Aurora/ABP_MT_AuroraDynamics"), TEXT("abp_mt_auroradynamics"), true);
}

#endif // WITH_AUTOMATION_TESTS
