#include "CoreMinimal.h"
#include "TestJsonRow.h"

#if WITH_AUTOMATION_TESTS

#include "Animation/AnimSequence.h"
#include "Animation/AnimData/IAnimationDataModel.h"
#include "Misc/AutomationTest.h"
#include "Misc/FileHelper.h"
#include "Misc/PackageName.h"
#include "Misc/Paths.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "UObject/UObjectGlobals.h"

// The frozen-bake tripwire. ONE complex test enumerates every row of
// Plugins/MotionTerminal/Tests/clip_manifest.json — the beautified sub-name is the clip's base filename,
// the test command carries clip|bone|minRotVarianceDeg|localOnly. Each row loads the UAnimSequence, reads
// the probe bone's RAW animation-data-model track (source keys — no evaluation/RHI), samples 5 evenly-spaced
// keys, and asserts the max pairwise rotation delta (degrees) >= the row's threshold. This is exactly the
// frozen-bake lesson: a retarget/bake can emit N frames that are all the SAME pose; "it plays" is only true once the
// data VARIES over time. A committed non-local control row keeps >=1 live row on fresh clones; the Khaimera
// rows are localOnly (gitignored Fab content) and SKIP-with-log when absent, so fresh clones stay green — on a
// machine that has the content, a full run must show ZERO skips. Headless-safe: asset load + data-model read only.

namespace
{
	struct FClipRow
	{
		FString Clip;
		FString Bone;
		double MinRotVarianceDeg = 0.0;
		bool bLocalOnly = false;
	};

	// Loads clip_manifest.json into OutRows. false + OutError on read/parse/empty failure. GetTests checks
	// this AND Rows.Num(): on any failure it emits ONE synthetic "_ManifestLoadFailed" sentinel row (never
	// zero rows) whose RunTest AddErrors — a missing/malformed/empty manifest fails LOUD (RED), never a silent
	// zero-subtest vanish that lets the default tier still report green (the PieTests.cpp sentinel lesson).
	bool LoadClipManifest(TArray<FClipRow>& OutRows, FString& OutError)
	{
		TSharedPtr<FJsonObject> Root;
		if (!MotionTerminalTestJson::LoadManifestRoot(TEXT("clip_manifest.json"), Root, OutError)) { return false; }
		const TArray<TSharedPtr<FJsonValue>>* Clips = nullptr;
		if (!MotionTerminalTestJson::RequireArrayField(Root, TEXT("clips"), TEXT("clip_manifest.json"), Clips, OutError)) { return false; }
		int32 RowIndex = -1;
		for (const TSharedPtr<FJsonValue>& V : *Clips)
		{
			++RowIndex;
			// A ROW THAT CANNOT BE READ IS A RED MANIFEST, NOT A SKIPPED ROW. Every `continue` here
			// used to drop a row whose claim then never ran while the suite reported green. The three
			// Require* readers are [[nodiscard]] so that mistake cannot be made again silently.
			const TSharedPtr<FJsonObject>* Obj = nullptr;
			if (!MotionTerminalTestJson::RequireObject(V, TEXT("clip_manifest.json"), RowIndex, Obj, OutError)) { return false; }
			FClipRow Row;
			if (!MotionTerminalTestJson::RequireString(*Obj, TEXT("clip"), TEXT("clip_manifest.json"), RowIndex, Row.Clip, OutError)) { return false; }
			if (!MotionTerminalTestJson::RequireString(*Obj, TEXT("bone"), TEXT("clip_manifest.json"), RowIndex, Row.Bone, OutError)) { return false; }
			// A zero threshold passes a COMPLETELY FROZEN clip, because `MaxDeltaDeg < 0.0` is false.
			if (!MotionTerminalTestJson::RequirePositive(*Obj, TEXT("minRotVarianceDeg"), TEXT("clip_manifest.json"), RowIndex, Row.MinRotVarianceDeg, OutError)) { return false; }
			(*Obj)->TryGetBoolField(TEXT("localOnly"), Row.bLocalOnly);   // genuinely optional
			OutRows.Add(Row);
		}
		if (OutRows.Num() == 0)
		{
			OutError = TEXT("clip_manifest.json yielded zero usable clip rows");
			return false;
		}
		return true;
	}

	const FString kClipManifestErrorPrefix = TEXT("<manifest-error>");
}

IMPLEMENT_COMPLEX_AUTOMATION_TEST(FVerifyClipVariesTest,
	"MotionTerminal.Verify.ClipVaries",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::ProductFilter)

void FVerifyClipVariesTest::GetTests(TArray<FString>& OutBeautifiedNames, TArray<FString>& OutTestCommands) const
{
	TArray<FClipRow> Rows; FString Err;
	if (!LoadClipManifest(Rows, Err))
	{
		// Never zero rows — a missing/malformed/empty manifest becomes ONE RED sentinel row instead of a
		// silent vanish (the PieTests.cpp sentinel precedent).
		OutBeautifiedNames.Add(TEXT("_ManifestLoadFailed"));
		OutTestCommands.Add(kClipManifestErrorPrefix + Err);
		return;
	}
	for (const FClipRow& R : Rows)
	{
		OutBeautifiedNames.Add(FPaths::GetBaseFilename(R.Clip));   // e.g. "Khai_MM_Idle"
		// Self-contained command: clip|bone|threshold|localOnly (clip paths never contain '|').
		OutTestCommands.Add(FString::Printf(TEXT("%s|%s|%s|%d"),
			*R.Clip, *R.Bone, *FString::SanitizeFloat(R.MinRotVarianceDeg), R.bLocalOnly ? 1 : 0));
	}
}

bool FVerifyClipVariesTest::RunTest(const FString& Parameters)
{
	if (Parameters.StartsWith(kClipManifestErrorPrefix))
	{
		AddError(FString::Printf(
			TEXT("clip_manifest.json failed to load — Verify.ClipVaries rows have NOT run: %s"),
			*Parameters.RightChop(kClipManifestErrorPrefix.Len())));
		return false;
	}

	TArray<FString> Parts;
	Parameters.ParseIntoArray(Parts, TEXT("|"), /*CullEmpty*/ false);
	if (Parts.Num() != 4)
	{
		AddError(FString::Printf(TEXT("malformed ClipVaries command '%s'"), *Parameters));
		return false;
	}
	const FString ClipPath = Parts[0];
	const FString Bone = Parts[1];
	const double MinRotVarianceDeg = FCString::Atod(*Parts[2]);
	const bool bLocalOnly = (Parts[3] == TEXT("1"));
	const FString ClipTag = FPaths::GetBaseFilename(ClipPath);

	// Machine-local skip: a localOnly clip whose package is absent SKIPs (fresh clones stay green).
	if (bLocalOnly && !FPackageName::DoesPackageExist(FPackageName::ObjectPathToPackageName(ClipPath)))
	{
		AddInfo(FString::Printf(TEXT("SKIP (localOnly clip absent on this machine): %s"), *ClipPath));
		return true;
	}

	UAnimSequence* Seq = LoadObject<UAnimSequence>(nullptr, *ClipPath);
	if (!TestNotNull(*FString::Printf(TEXT("[%s] AnimSequence '%s' loads"), *ClipTag, *ClipPath), Seq))
	{
		return false;
	}

	const IAnimationDataModel* Model = Seq->GetDataModel();
	if (!Model)
	{
		AddError(FString::Printf(TEXT("[%s] no animation data model (editor-only source data missing)"), *ClipTag));
		return false;
	}
	if (!Model->IsValidBoneTrackName(FName(*Bone)))
	{
		AddError(FString::Printf(TEXT("[%s] no bone track '%s' in the clip's data model — cannot prove it varies"), *ClipTag, *Bone));
		return false;
	}

	TArray<FTransform> Keys;
	Model->GetBoneTrackTransforms(FName(*Bone), Keys);
	if (Keys.Num() < 2)
	{
		AddError(FString::Printf(TEXT("[%s] bone '%s' has %d key(s) (<2) — a frozen single-pose track"), *ClipTag, *Bone, Keys.Num()));
		return false;
	}

	// Sample 5 evenly-spaced keys across the whole clip; max pairwise rotation delta (degrees).
	const int32 N = Keys.Num();
	const int32 Sample[5] = { 0, N / 4, N / 2, (3 * N) / 4, N - 1 };
	double MaxDeltaDeg = 0.0;
	for (int32 i = 0; i < 5; ++i)
	{
		for (int32 j = i + 1; j < 5; ++j)
		{
			const double Deg = FMath::RadiansToDegrees(
				Keys[Sample[i]].GetRotation().AngularDistance(Keys[Sample[j]].GetRotation()));
			MaxDeltaDeg = FMath::Max(MaxDeltaDeg, Deg);
		}
	}

	AddInfo(FString::Printf(TEXT("[%s] bone '%s': %d keys, max pairwise rotation delta %.2f deg (threshold %.2f)"),
		*ClipTag, *Bone, N, MaxDeltaDeg, MinRotVarianceDeg));

	if (MaxDeltaDeg < MinRotVarianceDeg)
	{
		AddError(FString::Printf(
			TEXT("[%s] FROZEN BAKE: bone '%s' max rotation delta %.3f deg < threshold %.3f — the clip does not vary over time"),
			*ClipTag, *Bone, MaxDeltaDeg, MinRotVarianceDeg));
	}
	return true;   // AddError already marks failure; returning true lets every row report
}

#endif // WITH_AUTOMATION_TESTS
