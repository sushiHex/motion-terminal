#include "CoreMinimal.h"

#if WITH_AUTOMATION_TESTS

#include "AnimImportTools.h"
#include "FbxScaleHeader.h"
#include "ImportScaleCheck.h"
#include "Engine/SkeletalMesh.h"
#include "HAL/FileManager.h"
#include "Misc/AutomationTest.h"
#include "Misc/FileHelper.h"
#include "Misc/PackageName.h"
#include "Misc/Paths.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "UObject/Package.h"
#include "UObject/UObjectGlobals.h"
#include "UObject/UObjectIterator.h"

// The per-rig import-scale gate (detect/assert half). ONE complex test enumerates every
// Plugins/MotionTerminal/Tests/ScaleBaselines/*.json — the beautified sub-name is the rig, the test
// command is the baseline file path. Each row rebuilds the mesh's scale fingerprint and SummarizeScaleDrift's
// it against the committed baseline, reporting BOTH channels — drift as an error, an unrunnable check as a warning. Machine-local semantics: a "localOnly" baseline whose asset package is
// absent (Fab/MetaHuman content is gitignored) SKIPs so fresh clones stay green — on a machine that has the
// content, a full run must show ZERO skips. Headless-safe: SkeletalMesh asset load only, no PIE, no RHI.

// Sentinel prefix: embedded in a synthetic GetTests row's Parameters when ZERO
// baselines enumerate. RunTest checks for it and AddErrors — so an empty/missing ScaleBaselines dir
// fails LOUD (RED) instead of registering zero sub-tests and letting Verify.ImportScale report green
// with the gate absent (mirrors the ClipVaries/PieTests _ManifestLoadFailed precedent). A baseline
// path is always absolute, so it never collides with this marker.
static const FString kScaleBaselineErrorPrefix = TEXT("<no-baselines>");

IMPLEMENT_COMPLEX_AUTOMATION_TEST(FVerifyImportScaleTest,
	"MotionTerminal.Verify.ImportScale",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::ProductFilter)

void FVerifyImportScaleTest::GetTests(TArray<FString>& OutBeautifiedNames, TArray<FString>& OutTestCommands) const
{
	const FString Dir = MotionTerminal::ScaleBaselineDir();
	TArray<FString> Files;
	IFileManager::Get().FindFiles(Files, *(Dir / TEXT("*.json")), /*Files*/ true, /*Directories*/ false);
	Files.Sort();
	if (Files.Num() == 0)
	{
		// Empty/missing ScaleBaselines dir: emit ONE RED sentinel row instead of zero sub-tests, so the
		// import-scale gate can't silently vanish while the tier still reports green. (Happy path: 3
		// baselines enumerate → this branch is never taken, default stays 15/0.)
		OutBeautifiedNames.Add(TEXT("_NoBaselinesFound"));
		OutTestCommands.Add(kScaleBaselineErrorPrefix + FString::Printf(
			TEXT("no ScaleBaselines/*.json enumerated in '%s' — the import-scale gate would silently vanish"), *Dir));
		return;
	}
	for (const FString& File : Files)
	{
		OutBeautifiedNames.Add(FPaths::GetBaseFilename(File));   // rig name (e.g. "manny")
		OutTestCommands.Add(Dir / File);                          // full baseline path -> RunTest Parameters
	}
}

bool FVerifyImportScaleTest::RunTest(const FString& Parameters)
{
	if (Parameters.StartsWith(kScaleBaselineErrorPrefix))
	{
		AddError(FString::Printf(
			TEXT("Verify.ImportScale enumerated ZERO baselines — the import-scale gate has NOT run: %s"),
			*Parameters.RightChop(kScaleBaselineErrorPrefix.Len())));
		return false;
	}

	const FString RigTag = FPaths::GetBaseFilename(Parameters);

	FString Text;
	if (!FFileHelper::LoadFileToString(Text, *Parameters))
	{
		AddError(FString::Printf(TEXT("could not read baseline '%s'"), *Parameters));
		return false;
	}
	TSharedPtr<FJsonObject> Baseline;
	{
		const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Text);
		if (!FJsonSerializer::Deserialize(Reader, Baseline) || !Baseline.IsValid())
		{
			AddError(FString::Printf(TEXT("baseline '%s' is not valid JSON"), *Parameters));
			return false;
		}
	}

	FString AssetPath;
	if (!Baseline->TryGetStringField(TEXT("asset"), AssetPath) || AssetPath.IsEmpty())
	{
		AddError(FString::Printf(TEXT("baseline '%s' has no 'asset' field"), *RigTag));
		return false;
	}
	bool bLocalOnly = false;
	Baseline->TryGetBoolField(TEXT("localOnly"), bLocalOnly);

	// Machine-local skip: DoesPackageExist wants the package name (strip any .Object suffix).
	const FString PackageName = FPackageName::ObjectPathToPackageName(AssetPath);
	if (bLocalOnly && !FPackageName::DoesPackageExist(PackageName))
	{
		AddInfo(FString::Printf(TEXT("SKIP (localOnly asset absent on this machine): %s"), *AssetPath));
		return true;
	}

	USkeletalMesh* Mesh = LoadObject<USkeletalMesh>(nullptr, *AssetPath);
	if (!TestNotNull(*FString::Printf(TEXT("[%s] SkeletalMesh '%s' loads"), *RigTag, *AssetPath), Mesh))
	{
		return false;
	}

	const TSharedRef<FJsonObject> Current = MotionTerminal::BuildScaleReportJson(*Mesh);

	// Dead-reader tripwire: a reader that silently returns nothing must FAIL, not pass vacuously.
	double BoneCount = 0.0;
	Current->TryGetNumberField(TEXT("boneCount"), BoneCount);
	if (BoneCount < 10.0)
	{
		AddError(FString::Printf(TEXT("[%s] boneCount %.0f < 10 — reader produced no skeleton"), *RigTag, BoneCount));
	}
	const TSharedPtr<FJsonObject>* CurBones = nullptr;
	bool bAnyNonZero = false;
	if (Current->TryGetObjectField(TEXT("boneLengths"), CurBones) && CurBones)
	{
		for (const TPair<FString, TSharedPtr<FJsonValue>>& P : (*CurBones)->Values)
		{
			if (P.Value.IsValid() && P.Value->AsNumber() > 1e-4) { bAnyNonZero = true; break; }
		}
	}
	if (!bAnyNonZero)
	{
		AddError(FString::Printf(TEXT("[%s] every boneLengths value is 0 — dead reader"), *RigTag));
	}

	const MotionTerminal::FScaleDriftSummary Drift =
		MotionTerminal::SummarizeScaleDrift(Baseline.ToSharedRef(), Current);
	if (!Drift.IsClean())
	{
		AddError(FString::Printf(TEXT("[%s] import-scale drift vs baseline:\n%s"), *RigTag, *Drift.Diff));
	}
	// A check that could not run must be VISIBLE on a passing row too. Without this the roster gate
	// would go quietly green on a mesh whose stale-import-option audit had gone dark — silence about a
	// missing check being exactly the failure mode this suite exists to prevent. Warning, not error:
	// the field being unreadable is a known gap (Interchange imports), not a regression.
	if (!Drift.Notes.IsEmpty())
	{
		AddWarning(FString::Printf(TEXT("[%s] gate passed, but not every check could run:\n%s"), *RigTag, *Drift.Notes));
	}
	return true;   // AddError already marks failure; returning true lets every sub-check report
}

// =================================================================================================
// The import-scale FIX half. Pins the scope boundary research/fbx-scale-import-ue58.md draws
// (flag-don't-fix) — auto-fix only the
// deterministic, whole-rig unit mismatch; detect-and-FLAG baked non-uniform joint scale and
// rig-intentional root scale; ERROR rather than guess on anything unreadable.
//
// Headless-safe by construction. Coverage, stated precisely:
//   - FLAG and ERROR run UAnimImportTools::FixImportScale END TO END. They return before any import,
//     which is the point: the ordering assertions below prove no package appeared.
//   - The post-parse decision path (import at 1.0, gate, correct, reimport, re-gate, and every string
//     it emits) runs for real via MotionTerminal::ApplyScaleFix with the import injected as a fake —
//     see block (1b). That covers which offset reaches which import, which summary each branch gates
//     on, and the OK/FLAGGED/ERROR text, none of which block (1)'s isolated arithmetic can see.
//   - NOT covered: the Interchange import call itself; FixImportScale's LoadBaseline leg (no case gets
//     that far, so the on-disk baseline is never read through this row — Verify.ImportScale covers the
//     same files by another route); and the lines wiring the real importer into ApplyScaleFix (the
//     destination path and the SkeletalMesh->fingerprint step). Those were executed live in the
//     editor, where a real mis-declared FBX went in and
//     "OK: applied 0.01" + AssertImportScale "OK" came out.
//     Still not covered HERE, and by construction cannot be: this
//     row is headless. What the row proves is the DECISION; what the live import proves is that the engine
//     behaves the way the row's fixtures assume. Neither is the whole claim on its own.
// FBX fixtures are GENERATED per run under Saved/ (gitignored) — no .fbx is committed — and the
// binary walker is checked against two real FBX files that ship with the engine.
// =================================================================================================

namespace
{
	FString ImportScaleFixtureDir()
	{
		return FPaths::Combine(FPaths::ProjectSavedDir(), TEXT("MotionTerminalTests"), TEXT("ImportScale"));
	}

	FString EngineFbxSample(const TCHAR* FileName)
	{
		return FPaths::Combine(FPaths::EngineContentDir(), TEXT("FbxEditorAutomation"), FileName);
	}

	// Where every FixImportScale call in this test is pointed. Nothing else in the project writes here,
	// so any package under it can only have come from an import this test triggered.
	const TCHAR* kImportScaleDestPath = TEXT("/Game/MT/ImportScaleTest");

	// Sees UNSAVED packages, which is the whole point. FPackageName::DoesPackageExist resolves through
	// the asset registry / disk, and ImportWithScaleOffset sets bSave=false — so an import that DID run
	// leaves an in-memory package and no file. A disk check here would pass whether or not the import
	// happened, i.e. it could never fail, and the ordering property it guards would be undefended.
	// Scanning by path prefix rather than by expected asset name also avoids depending on how
	// Interchange chooses to name what it creates. Root is kImportScaleDestPath here or kImportScaleWalkDestPath below.
	bool AnyPackageUnder(const TCHAR* Root)
	{
		const FString Prefix = FString(Root) + TEXT("/");
		for (TObjectIterator<UPackage> It; It; ++It)
		{
			if (It->GetName().StartsWith(Prefix)) { return true; }
		}
		return false;
	}

	// The fixture dir must go even when a guard clause bails out early, or the next run inherits stale files.
	struct FFixtureScope
	{
		~FFixtureScope()
		{
			IFileManager::Get().DeleteDirectory(*ImportScaleFixtureDir(), /*RequireExists*/ false, /*Tree*/ true);
		}
	};

	struct FFixtureNode
	{
		FString Name;
		FVector Scale;
	};

	// A minimal ASCII FBX carrying exactly the two things the scale check reads pre-import: the GlobalSettings unit
	// declaration and each Model's Lcl Scaling. Returns the written path ("" on failure).
	FString WriteAsciiFbxFixture(const FString& FileName, TOptional<double> UnitScaleFactor,
		const TArray<FFixtureNode>& Nodes)
	{
		TArray<FString> L;
		L.Add(TEXT("; FBX 7.4.0 project file"));
		L.Add(TEXT("GlobalSettings:  {"));
		L.Add(TEXT("\tVersion: 1000"));
		L.Add(TEXT("\tProperties70:  {"));
		L.Add(TEXT("\t\tP: \"UpAxis\", \"int\", \"Integer\", \"\",2"));
		if (UnitScaleFactor.IsSet())
		{
			L.Add(FString::Printf(TEXT("\t\tP: \"UnitScaleFactor\", \"double\", \"Number\", \"\",%s"),
				*FString::SanitizeFloat(UnitScaleFactor.GetValue())));
		}
		L.Add(TEXT("\t}"));
		L.Add(TEXT("}"));
		L.Add(TEXT("Objects:  {"));
		int32 Id = 100;
		for (const FFixtureNode& Node : Nodes)
		{
			L.Add(FString::Printf(TEXT("\tModel: %d, \"Model::%s\", \"LimbNode\" {"), Id++, *Node.Name));
			L.Add(TEXT("\t\tVersion: 232"));
			L.Add(TEXT("\t\tProperties70:  {"));
			L.Add(FString::Printf(TEXT("\t\t\tP: \"Lcl Scaling\", \"Lcl Scaling\", \"\", \"A\",%s,%s,%s"),
				*FString::SanitizeFloat(Node.Scale.X), *FString::SanitizeFloat(Node.Scale.Y),
				*FString::SanitizeFloat(Node.Scale.Z)));
			L.Add(TEXT("\t\t}"));
			L.Add(TEXT("\t}"));
		}
		L.Add(TEXT("}"));

		const FString Path = FPaths::Combine(ImportScaleFixtureDir(), FileName);
		IFileManager::Get().MakeDirectory(*ImportScaleFixtureDir(), /*Tree*/ true);
		return FFileHelper::SaveStringToFile(FString::Join(L, TEXT("\n")), *Path) ? Path : FString();
	}

	// A synthetic scale fingerprint in the exact shape BuildScaleReportJson emits, uniformly scaled by
	// Scale. Two of these — one at 1x, one at 100x — are a whole-rig unit mismatch with no mesh needed.
	// What BuildScaleReportJson actually emits for an Interchange-imported mesh: the import-data class
	// name tagged "(unaudited)", because it only reads a number out of the LEGACY UFbxSkeletalMeshImportData.
	// UInterchangeAssetImportData derives from UAssetImportData, NOT from that, so every import
	// FixImportScale performs lands here. The committed baselines all carry a numeric 1.
	const TCHAR* kInterchangeImportScale = TEXT("InterchangeAssetImportData (unaudited)");

	// PerFieldScatter multiplies each measured field independently (bounds, pelvis, spine_01). A real
	// mis-unit'd rig does NOT come back with every ratio identical to the bit — an FBX round trip
	// scatters them around the true factor, and two thresholds are decided by that scatter: the 0.15
	// agreement window and the 1% re-gate tolerance. Uniform-to-the-bit reports exercise neither.
	TSharedRef<FJsonObject> MakeScaleReport(double Scale, bool bRefPoseViolation,
		bool bInterchangeImportData = false, const TArray<double>* PerFieldScatter = nullptr)
	{
		auto Field = [PerFieldScatter](int32 Index)
		{
			return (PerFieldScatter && PerFieldScatter->IsValidIndex(Index)) ? (*PerFieldScatter)[Index] : 1.0;
		};

		TSharedRef<FJsonObject> Report = MakeShared<FJsonObject>();
		Report->SetNumberField(TEXT("boundsHeightUU"), 180.0 * Scale * Field(0));
		Report->SetNumberField(TEXT("boneCount"), 3);

		TSharedRef<FJsonObject> Bones = MakeShared<FJsonObject>();
		Bones->SetNumberField(TEXT("root"), 0.0);           // zero-length: carries no scale signal
		Bones->SetNumberField(TEXT("pelvis"), 96.0 * Scale * Field(1));
		Bones->SetNumberField(TEXT("spine_01"), 12.5 * Scale * Field(2));
		Report->SetObjectField(TEXT("boneLengths"), Bones);

		TArray<TSharedPtr<FJsonValue>> Violations;
		if (bRefPoseViolation) { Violations.Add(MakeShared<FJsonValueString>(TEXT("spine_01"))); }
		Report->SetArrayField(TEXT("refPoseScaleViolations"), Violations);

		if (bInterchangeImportData)
		{
			Report->SetStringField(TEXT("importScale"), kInterchangeImportScale);
		}
		else
		{
			Report->SetNumberField(TEXT("importScale"), 1.0);
		}
		return Report;
	}

	TSharedRef<FJsonObject> MakeScaleBaseline()
	{
		TSharedRef<FJsonObject> Baseline = MakeScaleReport(1.0, /*bRefPoseViolation*/ false);
		Baseline->SetNumberField(TEXT("lengthTolerancePct"), 1.0);
		return Baseline;
	}

	// The fingerprint a scale at or above the ROOT JOINT actually produces, which is not the shape
	// MakeScaleReport models. Interchange substitutes the composed global transform for bone 0 and leaves
	// every other bone's LOCAL transform alone (InterchangeSkeletonHelper.cpp:319-342), so:
	//   * every boneLengths value is UNCHANGED — the factor is structurally invisible to them;
	//   * only boundsHeightUU moves, by the factor;
	//   * bone 0 carries a ref-pose scale violation.
	// Measured, not assumed: one FBX header patched from 1 to 100 imported as exactly this,
	// with pelvis 95.92390282538743 on both sides (research/interchange-unit-scale-root-bone.md §1c).
	// import_offset_uniform_scale composes into the SAME transform, so RootScale here is (true drift x
	// offset) and a landing factor of 1 yields a clean report — which is why the corrective reimport works.
	TSharedRef<FJsonObject> MakeRootScaleReport(double RootScale, bool bInterchangeImportData = true)
	{
		TSharedRef<FJsonObject> Report = MakeScaleReport(1.0, /*bRefPoseViolation*/ false, bInterchangeImportData);
		Report->SetNumberField(TEXT("boundsHeightUU"), 180.0 * RootScale);

		TArray<TSharedPtr<FJsonValue>> Violations;
		if (FMath::Abs(RootScale - 1.0) > 1e-4)
		{
			Violations.Add(MakeShared<FJsonValueString>(TEXT("root")));
		}
		Report->SetArrayField(TEXT("refPoseScaleViolations"), Violations);
		return Report;
	}

	// Stands in for the editor import inside ApplyScaleFix. Records the offsets it was handed, so the
	// test can assert WHICH offset reached WHICH import — the wiring that arithmetic checked in isolation
	// cannot see. Returns the fingerprint of a rig mis-unit'd by TrueDriftFactor with the offset applied.
	//
	// bInterchangeImportData defaults TRUE and must stay that way: ApplyScaleFix's real importer is
	// Interchange, so a fake that emitted a numeric importScale would be unfaithful in exactly the field
	// that decides the verdict, and would certify a path production cannot enter.
	struct FFakeImporter
	{
		double TrueDriftFactor = 1.0;
		bool bRefPoseViolation = false;
		bool bIgnoreOffset = false;      // a correction that does not land
		bool bFailFirst = false;         // an import that produces nothing gateable
		bool bInterchangeImportData = true;
		// Emit the ROOT-SCALE fingerprint instead of the bone-baked one: bone lengths untouched, only the
		// bounds moving, bone 0 flagged. This is what a real 5.8 unit conversion produces, so the
		// importer-induced lane can only be exercised through it.
		bool bRootScaleShape = false;
		TArray<double> PerFieldScatter;  // empty == uniform to the bit; see MakeScaleReport
		TArray<double> Offsets;

		TSharedPtr<FJsonObject> operator()(double OffsetUniformScale, FString& OutError)
		{
			Offsets.Add(OffsetUniformScale);
			if (bFailFirst && Offsets.Num() == 1)
			{
				OutError = TEXT("simulated: import produced no SkeletalMesh");
				return nullptr;
			}
			const double Landed = bIgnoreOffset ? TrueDriftFactor : TrueDriftFactor * OffsetUniformScale;
			if (bRootScaleShape)
			{
				return MakeRootScaleReport(Landed, bInterchangeImportData);
			}
			// The scatter is a property of the rig, so it survives the corrective reimport unchanged —
			// which is the whole reason a mean-based correction can fail the re-gate.
			return MakeScaleReport(Landed, bRefPoseViolation, bInterchangeImportData,
				PerFieldScatter.Num() > 0 ? &PerFieldScatter : nullptr);
		}
	};
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FVerifyFixImportScaleTest,
	"MotionTerminal.Verify.FixImportScale",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::ProductFilter)

bool FVerifyFixImportScaleTest::RunTest(const FString& Parameters)
{
	using namespace MotionTerminal;

	// Several sub-cases bail out early on a fixture-write failure; this survives all of them.
	const FFixtureScope FixtureCleanup;

	// --- (1) A deterministic 100x unit mismatch is CORRECTED -------------------------------------
	// The gate measures the drift; the fix cancels it; the cancelled fingerprint passes the gate.
	{
		const TSharedRef<FJsonObject> Baseline = MakeScaleBaseline();
		const FScaleDriftSummary Drift = SummarizeScaleDrift(Baseline, MakeScaleReport(100.0, false));

		TestFalse(TEXT("[100x] a 100x rig does NOT pass its baseline"), Drift.IsClean());
		TestTrue(TEXT("[100x] classified as one whole-rig uniform factor"), Drift.bUniformFactor);
		TestFalse(TEXT("[100x] carries no non-uniform evidence"), Drift.bNonUniformEvidence);
		TestTrue(TEXT("[100x] the measured factor is ~100"),
			FMath::IsNearlyEqual(Drift.UniformFactor, 100.0, 0.5));
		TestTrue(TEXT("[100x] the drift line names the unit mismatch"),
			Drift.Diff.Contains(TEXT("unit mismatch (root cause 1a/1b)")));

		const double Correction = UnitCorrectionForDrift(Drift.UniformFactor);
		TestEqual(TEXT("[100x] the correction reported on camera is 0.01"),
			FormatScaleFactor(Correction), FString(TEXT("0.01")));

		const FScaleDriftSummary AfterFix =
			SummarizeScaleDrift(Baseline, MakeScaleReport(100.0 * Correction, false));
		TestTrue(TEXT("[100x] the corrected rig passes the gate"), AfterFix.IsClean());
	}

	// --- (2) A rig-intentional scale is FLAGGED, not fixed ---------------------------------------
	// (2a) pre-import, end to end: a non-unit root scale stops the tool before anything is written.
	{
		const FString Path = WriteAsciiFbxFixture(TEXT("rig_intentional.fbx"), 1.0,
			{ { TEXT("root"), FVector(1.5, 1.5, 1.5) }, { TEXT("pelvis"), FVector::OneVector } });
		if (!TestTrue(TEXT("[flag] rig-intentional fixture written"), !Path.IsEmpty())) { return false; }

		const FString Result = UAnimImportTools::FixImportScale(Path, kImportScaleDestPath, TEXT("manny"));
		TestTrue(FString::Printf(TEXT("[flag] rig-intentional root scale is FLAGGED, got: %s"), *Result),
			Result.StartsWith(TEXT("FLAGGED:")));
		TestTrue(TEXT("[flag] the reason names the offending node"), Result.Contains(TEXT("'root'")));
		TestFalse(TEXT("[flag] nothing was silently 'applied'"), Result.Contains(TEXT("applied")));
		// No ordering assertion here on purpose: a geometry-free synthetic fixture does not translate,
		// so an import placed before the flag would create nothing and the check could not fail.
		// The ordering guard rides on the real FBX in (4b) — see the note there.
	}

	// (2b) baked NON-UNIFORM joint scale (root cause 1e) is the same refusal, reported as such.
	{
		const FString Path = WriteAsciiFbxFixture(TEXT("baked_nonuniform.fbx"), 1.0,
			{ { TEXT("root"), FVector::OneVector }, { TEXT("spine_01"), FVector(1.0, 1.25, 1.0) } });
		if (!TestTrue(TEXT("[flag] non-uniform fixture written"), !Path.IsEmpty())) { return false; }

		const FString Result = UAnimImportTools::FixImportScale(Path, kImportScaleDestPath, TEXT("manny"));
		TestTrue(FString::Printf(TEXT("[flag] baked non-uniform joint scale is FLAGGED, got: %s"), *Result),
			Result.StartsWith(TEXT("FLAGGED:")));
		TestTrue(TEXT("[flag] the reason calls it out as non-uniform"),
			Result.Contains(TEXT("NON-UNIFORM")));
	}

	// (2c) PRECEDENCE — the scope boundary's sharp edge. A file with BOTH a unit mismatch and a
	// rig-intentional scale must still be flagged: fixing the deterministic half would import a rig
	// carrying an unfixed craft problem and report success over it.
	{
		const FString Path = WriteAsciiFbxFixture(TEXT("both_problems.fbx"), 100.0,
			{ { TEXT("root"), FVector(1.5, 1.5, 1.5) } });
		if (!TestTrue(TEXT("[flag] both-problems fixture written"), !Path.IsEmpty())) { return false; }

		const FString Result = UAnimImportTools::FixImportScale(Path, kImportScaleDestPath, TEXT("manny"));
		TestTrue(FString::Printf(TEXT("[flag] rig-intentional scale outranks the unit fix, got: %s"), *Result),
			Result.StartsWith(TEXT("FLAGGED:")));
	}

	// (2d) post-import: a ref-pose scale violation is never classified as auto-correctable.
	{
		const FScaleDriftSummary Drift =
			SummarizeScaleDrift(MakeScaleBaseline(), MakeScaleReport(1.0, /*bRefPoseViolation*/ true));
		TestFalse(TEXT("[flag] a ref-pose violation is not a uniform factor"), Drift.bUniformFactor);
		TestTrue(TEXT("[flag] a ref-pose violation is non-uniform evidence"), Drift.bNonUniformEvidence);
	}

	// --- (1b) The DECISION PATH itself, with the import injected ---------------------------------
	// Case 1 proves the arithmetic in isolation; it re-derives the composition in test code, so it
	// cannot see a mis-wire inside ApplyScaleFix. These drive the real control flow with a fake import
	// and assert which offset reached which call, which summary each branch gated on, and the exact
	// strings — the parts a correct-arithmetic-but-wrong-wiring build would still pass.
	{
		const TSharedRef<FJsonObject> Baseline = MakeScaleBaseline();

		// Clean import: one call, at 1.0, and no corrective second pass.
		{
			FFakeImporter Fake{ /*TrueDriftFactor*/ 1.0 };
			const FString Result = ApplyScaleFix(TEXT("manny"), TEXT("1 (cm)"), 1.0, Baseline, FImportAndFingerprintFn(Fake));
			TestTrue(FString::Printf(TEXT("[decide] a clean import is OK, got: %s"), *Result),
				Result.StartsWith(TEXT("OK: applied 1")));
			TestEqual(TEXT("[decide] a clean import is not reimported"), Fake.Offsets.Num(), 1);
			if (Fake.Offsets.Num() >= 1)
			{
				TestTrue(TEXT("[decide] the first import runs at offset 1.0, never at the correction"),
					FMath::IsNearlyEqual(Fake.Offsets[0], 1.0, 1e-9));
			}
		}

		// 100x mis-unit: first pass at 1.0, corrective pass at 0.01, gate green, correct strings.
		{
			FFakeImporter Fake{ /*TrueDriftFactor*/ 100.0 };
			const FString Result = ApplyScaleFix(TEXT("manny"), TEXT("100 (m)"), 100.0, Baseline, FImportAndFingerprintFn(Fake));
			TestTrue(FString::Printf(TEXT("[decide] a 100x mis-unit ends OK, got: %s"), *Result),
				Result.StartsWith(TEXT("OK: applied")));
			if (TestEqual(TEXT("[decide] exactly two imports: measure, then correct"), Fake.Offsets.Num(), 2))
			{
				TestTrue(TEXT("[decide] WIRING: the FIRST import gets 1.0, not the correction"),
					FMath::IsNearlyEqual(Fake.Offsets[0], 1.0, 1e-9));
				TestTrue(TEXT("[decide] WIRING: the SECOND import gets the reciprocal 0.01"),
					FMath::IsNearlyEqual(Fake.Offsets[1], 0.01, 1e-9));
			}
			// A MeasuredText/CorrectionText swap keeps both numbers in the string but exchanges them,
			// so assert each against its own role rather than merely that both appear.
			TestTrue(TEXT("[decide] the APPLIED factor reported is the correction (0.01)"),
				Result.Contains(TEXT("applied 0.01")));
			TestTrue(TEXT("[decide] the MEASURED factor reported is the drift (100x)"),
				Result.Contains(TEXT("measured 100x")));
		}

		// A correction that does not land must ERROR on the re-gate, name what it left behind, and
		// never claim success. This is the branch that proves the second gate is real.
		{
			FFakeImporter Fake{ /*TrueDriftFactor*/ 100.0 };
			Fake.bIgnoreOffset = true;
			const FString Result = ApplyScaleFix(TEXT("manny"), TEXT("100 (m)"), 100.0, Baseline, FImportAndFingerprintFn(Fake));
			TestTrue(FString::Printf(TEXT("[decide] a correction that misses ERRORs, got: %s"), *Result),
				Result.StartsWith(TEXT("ERROR:")));
			TestFalse(TEXT("[decide] and never reports OK"), Result.StartsWith(TEXT("OK")));
			TestTrue(TEXT("[decide] the ERROR warns the bad import is sitting in the destination"),
				Result.Contains(TEXT("unsaved")));
			TestEqual(TEXT("[decide] it re-gated rather than trusting the correction"), Fake.Offsets.Num(), 2);
		}

		// Post-import non-uniform evidence FLAGs, and must not trigger a corrective reimport.
		{
			FFakeImporter Fake{ /*TrueDriftFactor*/ 1.0 };
			Fake.bRefPoseViolation = true;
			const FString Result = ApplyScaleFix(TEXT("manny"), TEXT("1 (cm)"), 1.0, Baseline, FImportAndFingerprintFn(Fake));
			TestTrue(FString::Printf(TEXT("[decide] post-import ref-pose violation FLAGs, got: %s"), *Result),
				Result.StartsWith(TEXT("FLAGGED:")));
			TestEqual(TEXT("[decide] a flagged rig is never reimported"), Fake.Offsets.Num(), 1);
		}

		// An import that yields nothing gateable ERRORs with the importer's own reason.
		{
			FFakeImporter Fake{ /*TrueDriftFactor*/ 1.0 };
			Fake.bFailFirst = true;
			const FString Result = ApplyScaleFix(TEXT("manny"), TEXT("1 (cm)"), 1.0, Baseline, FImportAndFingerprintFn(Fake));
			TestTrue(FString::Printf(TEXT("[decide] a failed import ERRORs, got: %s"), *Result),
				Result.StartsWith(TEXT("ERROR:")));
			TestTrue(TEXT("[decide] and carries the importer's reason"),
				Result.Contains(TEXT("no SkeletalMesh")));
		}

		// Degenerate factors are rejected at the decision site, not divided by. (The FLAG branch this
		// guards is unreachable while SummarizeScaleDrift refuses to set bUniformFactor for such a
		// factor — these pin the predicate itself, which is the part that could silently change.)
		TestFalse(TEXT("[decide] a ~zero factor is not usable as a correction"), IsUsableUniformFactor(0.0));
		TestFalse(TEXT("[decide] a negative factor is not usable as a correction"), IsUsableUniformFactor(-100.0));
		TestTrue(TEXT("[decide] a real factor is usable"), IsUsableUniformFactor(100.0));
	}

	// --- (1c) importScale: unmeasurable is NOT drift, but real drift still fails ------------------
	// BuildScaleReportJson can only read a number out of the LEGACY UFbxSkeletalMeshImportData, so an
	// Interchange import — every import FixImportScale performs — reports a string. Against the numeric
	// baselines this project ships, treating that as drift vetoed the corrective path entirely and made
	// FLAGGED the only reachable production verdict. It must be reported, and must not fail.
	{
		const TSharedRef<FJsonObject> NumericBaseline = MakeScaleBaseline();

		// Unreadable current value against a numeric baseline: a NOTE, not a failure, not evidence.
		{
			const FScaleDriftSummary Drift = SummarizeScaleDrift(
				NumericBaseline, MakeScaleReport(1.0, false, /*bInterchangeImportData*/ true));
			TestTrue(TEXT("[importScale] an unreadable value does not fail the gate"), Drift.IsClean());
			TestFalse(TEXT("[importScale] and is not treated as non-uniform evidence"), Drift.bNonUniformEvidence);
			TestTrue(TEXT("[importScale] but it IS reported — the reader learns the check went dark"),
				Drift.Notes.Contains(TEXT("did not run")));
		}

		// Genuine drift, direction 1: two readable numbers that differ.
		{
			TSharedRef<FJsonObject> Drifted = MakeScaleReport(1.0, false);
			Drifted->SetNumberField(TEXT("importScale"), 100.0);
			const FScaleDriftSummary Drift = SummarizeScaleDrift(NumericBaseline, Drifted);
			TestFalse(TEXT("[importScale] number-vs-different-number still FAILS"), Drift.IsClean());
			TestTrue(TEXT("[importScale] and is still non-uniform evidence"), Drift.bNonUniformEvidence);
			TestTrue(TEXT("[importScale] naming it as drift"), Drift.Diff.Contains(TEXT("importScale drift")));
		}

		// Genuine drift, direction 2: two readable strings that differ.
		{
			TSharedRef<FJsonObject> StringBaseline = MakeScaleReport(1.0, false);
			StringBaseline->SetStringField(TEXT("importScale"), TEXT("LegacyThing"));
			StringBaseline->SetNumberField(TEXT("lengthTolerancePct"), 1.0);
			TSharedRef<FJsonObject> Changed = MakeScaleReport(1.0, false);
			Changed->SetStringField(TEXT("importScale"), TEXT("SomethingElse"));

			const FScaleDriftSummary Drift = SummarizeScaleDrift(StringBaseline, Changed);
			TestFalse(TEXT("[importScale] string-vs-different-string still FAILS"), Drift.IsClean());
			TestTrue(TEXT("[importScale] and is still non-uniform evidence"), Drift.bNonUniformEvidence);
		}

		// The two remaining arms of the importScale branch.
		{
			// "none" (no import data at all) is unmeasurable, same as "(unaudited)".
			TSharedRef<FJsonObject> NoImportData = MakeScaleReport(1.0, false);
			NoImportData->SetStringField(TEXT("importScale"), TEXT("none"));
			const FScaleDriftSummary Drift = SummarizeScaleDrift(NumericBaseline, NoImportData);
			TestTrue(TEXT("[importScale] 'none' does not fail the gate"), Drift.IsClean());
			TestTrue(TEXT("[importScale] 'none' is still disclosed"), Drift.Notes.Contains(TEXT("did not run")));
		}
		{
			// Numeric baseline vs a READABLE string — the half of the branch that still counts as drift.
			// This must still FLAG: it is one measurable value replaced by a different measurable one.
			TSharedRef<FJsonObject> Readable = MakeScaleReport(1.0, false);
			Readable->SetStringField(TEXT("importScale"), TEXT("SomeReadableValue"));
			const FScaleDriftSummary Drift = SummarizeScaleDrift(NumericBaseline, Readable);
			TestFalse(TEXT("[importScale] number-vs-READABLE-string still FAILS"), Drift.IsClean());
			TestTrue(TEXT("[importScale] and is still non-uniform evidence"), Drift.bNonUniformEvidence);
		}

		// And the whole point: with the field unreadable, a 100x mis-unit still gets CORRECTED.
		{
			FFakeImporter Fake{ /*TrueDriftFactor*/ 100.0 };
			const FString Result = ApplyScaleFix(TEXT("manny"), TEXT("100 (m)"), 100.0, NumericBaseline,
				FImportAndFingerprintFn(Fake));
			TestTrue(FString::Printf(TEXT("[importScale] an Interchange import can still reach OK, got: %s"), *Result),
				Result.StartsWith(TEXT("OK: applied 0.01")));
			TestTrue(TEXT("[importScale] and the OK still discloses the dark check"),
				Result.Contains(TEXT("did not run")));
		}
	}

	// --- (1d) Realistic per-bone SCATTER — what a real FBX round trip meets ----------------------
	// Every other case feeds ratios identical to the bit, which exercises neither of the two thresholds
	// that scatter decides: the 0.15 agreement window (how far ratios may spread and still be called ONE
	// factor) and the baseline's 1% lengthTolerancePct (how close the corrected rig must land). Those
	// two differ by a factor of 15, so there is a band where the tool confidently identifies a uniform
	// factor, corrects by its mean, and then fails its own re-gate. These pin both sides of that band.
	{
		const TSharedRef<FJsonObject> Baseline = MakeScaleBaseline();

		// Tight scatter (ratios 99.4 / 100.3 / 100.1 — spread <1%): corrected rig lands inside
		// tolerance, so the fix SUCCEEDS. This is the realistic case: a unit rescale is a
		// multiplication, and an FBX round trip perturbs it far less than a percent.
		{
			FFakeImporter Fake{ /*TrueDriftFactor*/ 100.0 };
			Fake.PerFieldScatter = { 0.994, 1.003, 1.001 };
			const FString Result = ApplyScaleFix(TEXT("manny"), TEXT("100 (m)"), 100.0, Baseline,
				FImportAndFingerprintFn(Fake));
			TestTrue(FString::Printf(TEXT("[scatter] sub-1%% scatter still ends OK, got: %s"), *Result),
				Result.StartsWith(TEXT("OK: applied")));
			TestEqual(TEXT("[scatter] and it took exactly two imports"), Fake.Offsets.Num(), 2);
		}

		// Wide scatter (ratios 93 / 105 / 101 — spread ~12%, still INSIDE the 0.15 agreement window):
		// the mean is declared a uniform factor and applied, but every bone then misses the 1%
		// re-gate. The tool refuses to claim success — ERROR, not a quiet OK over a bad mesh.
		{
			FFakeImporter Fake{ /*TrueDriftFactor*/ 100.0 };
			Fake.PerFieldScatter = { 0.93, 1.05, 1.01 };
			const FString Result = ApplyScaleFix(TEXT("manny"), TEXT("100 (m)"), 100.0, Baseline,
				FImportAndFingerprintFn(Fake));
			TestTrue(FString::Printf(TEXT("[scatter] 12%% scatter is corrected then REJECTED, got: %s"), *Result),
				Result.StartsWith(TEXT("ERROR:")));
			TestFalse(TEXT("[scatter] it never claims OK over a rig that missed the re-gate"),
				Result.StartsWith(TEXT("OK")));
			TestEqual(TEXT("[scatter] it did attempt the correction before rejecting"), Fake.Offsets.Num(), 2);
		}

		AddInfo(TEXT("[scatter] band recorded: per-bone ratio spread below ~1% (lengthTolerancePct) is "
			"corrected and passes; spread between that and the 0.15 agreement window is corrected and "
			"then ERRORs on the re-gate. The FBX import step sees OK only if its FBX round trip stays in the former."));
	}

	// --- (1e) THE IMPORTER-INDUCED LANE, and its proven-OFF reference ----------------------------
	// UE 5.8 does not bake an FBX unit conversion into bone translations: it
	// deposits the whole factor at or above the root joint, so a 100x mis-declared file arrives with
	// EVERY bone length untouched and a lone ref-pose scale on bone 0 — which the gate reads as detection
	// #3 and refuses to auto-correct. That left the scale-fix lane unreachable for the exact failure it
	// was built for (mechanism at research/interchange-unit-scale-root-bone.md §2).
	//
	// The lane now opens on ONE corroborated condition: the measured factor equals the file's own
	// declared centimetres-per-unit. This block is that bypass and — the part that matters more — the
	// reference proving it stays SHUT. Every OFF case below feeds the byte-identical mesh fingerprint as
	// the ON case; the ONLY thing that differs is the number the file's header declares. A bypass whose
	// refusal path is untested is a check that cannot observe what it claims to check.
	{
		const TSharedRef<FJsonObject> Baseline = MakeScaleBaseline();

		// The shape itself: bone lengths clean, bounds off by 100, root flagged.
		{
			const FScaleDriftSummary Drift = SummarizeScaleDrift(Baseline, MakeRootScaleReport(100.0));
			TestFalse(TEXT("[rootscale] a root-scaled rig does NOT pass its baseline"), Drift.IsClean());
			TestTrue(TEXT("[rootscale] recognised as the root-only shape"), Drift.bRootScaleOnlyDrift);
			TestTrue(TEXT("[rootscale] the lone bounds ratio IS the whole-rig factor here"), Drift.bUniformFactor);
			TestTrue(TEXT("[rootscale] and the factor is ~100"),
				FMath::IsNearlyEqual(Drift.UniformFactor, 100.0, 0.5));
			TestTrue(TEXT("[rootscale] it still carries non-uniform evidence — shape alone licenses nothing"),
				Drift.bNonUniformEvidence);
			TestTrue(TEXT("[rootscale] no bone length moved, so none is reported"),
				!Drift.Diff.Contains(TEXT("bone 'pelvis'")) && !Drift.Diff.Contains(TEXT("bone 'spine_01'")));
			TestTrue(TEXT("[rootscale] the drift line refuses to attribute the cause"),
				Drift.Diff.Contains(TEXT("at or above the root joint")));

			// The predicate, exercised directly on one fixed measurement across a spread of declarations.
			TestTrue(TEXT("[rootscale] measured 100 + declared 100 (m) == the importer's own conversion"),
				IsImporterInducedUnitConversion(Drift, 100.0));
			TestFalse(TEXT("[rootscale] OFF: declared 1 (cm) — the importer never rescales such a file"),
				IsImporterInducedUnitConversion(Drift, 1.0));
			TestFalse(TEXT("[rootscale] OFF: undeclared (0) is not evidence of a conversion"),
				IsImporterInducedUnitConversion(Drift, 0.0));
			TestFalse(TEXT("[rootscale] OFF: declared 2.54 (in) does not explain a measured 100"),
				IsImporterInducedUnitConversion(Drift, 2.54));
			TestFalse(TEXT("[rootscale] OFF: declared 90 misses the 1% match window"),
				IsImporterInducedUnitConversion(Drift, 90.0));
			TestFalse(TEXT("[rootscale] OFF: a negative declaration is not a unit"),
				IsImporterInducedUnitConversion(Drift, -100.0));
		}

		// A clean rig is never dragged into the lane by a merely non-cm declaration.
		{
			const FScaleDriftSummary Clean = SummarizeScaleDrift(Baseline, MakeRootScaleReport(1.0));
			TestTrue(TEXT("[rootscale] a correctly-converted metric file is simply clean"), Clean.IsClean());
			TestFalse(TEXT("[rootscale] and a clean drift is never 'importer-induced'"),
				IsImporterInducedUnitConversion(Clean, 100.0));
		}

		// ON — the whole point of the lane: the import ends OK, corrected, re-gated.
		{
			FFakeImporter Fake{ /*TrueDriftFactor*/ 100.0 };
			Fake.bRootScaleShape = true;
			const FString Result = ApplyScaleFix(TEXT("manny"), TEXT("100 (m)"), 100.0, Baseline,
				FImportAndFingerprintFn(Fake));
			TestTrue(FString::Printf(TEXT("[rootscale] ON: a mis-declared unit is CORRECTED, got: %s"), *Result),
				Result.StartsWith(TEXT("OK: applied 0.01")));
			if (TestEqual(TEXT("[rootscale] ON: exactly two imports — measure, then correct"), Fake.Offsets.Num(), 2))
			{
				TestTrue(TEXT("[rootscale] ON: the FIRST import gets 1.0"),
					FMath::IsNearlyEqual(Fake.Offsets[0], 1.0, 1e-9));
				TestTrue(TEXT("[rootscale] ON: the SECOND gets the reciprocal 0.01"),
					FMath::IsNearlyEqual(Fake.Offsets[1], 0.01, 1e-9));
			}
			TestTrue(TEXT("[rootscale] ON: the reply names the importer's conversion, not a bone-baked drift"),
				Result.Contains(TEXT("importer's own measured 100x unit conversion")));
		}

		// OFF — THE CONFOUND. Byte-identical mesh, byte-identical drift; the file declares centimetres,
		// so the importer contributed nothing and the 100x on the root is the ARTIST'S. This is root
		// cause 1i (a rig scaled after skinning without a re-bind): its scale lives in the FbxPose /
		// skin-cluster bind matrices, which the pre-import 'Lcl Scaling' parse cannot see — which is
		// precisely why the discriminator is the declared unit and not "the parser found no node scale".
		{
			FFakeImporter Fake{ /*TrueDriftFactor*/ 100.0 };
			Fake.bRootScaleShape = true;
			const FString Result = ApplyScaleFix(TEXT("manny"), TEXT("1 (cm)"), 1.0, Baseline,
				FImportAndFingerprintFn(Fake));
			TestTrue(FString::Printf(TEXT("[rootscale] OFF: an author-scaled rig is FLAGGED, got: %s"), *Result),
				Result.StartsWith(TEXT("FLAGGED:")));
			TestFalse(TEXT("[rootscale] OFF: and nothing was 'applied' to it"), Result.Contains(TEXT("applied")));
			TestEqual(TEXT("[rootscale] OFF: it was measured once and never reimported"), Fake.Offsets.Num(), 1);
		}

		// OFF — an undeclared unit. Same mesh again. "No declaration" must not decay to "declares cm"
		// and must not decay to "declares whatever we measured".
		{
			FFakeImporter Fake{ /*TrueDriftFactor*/ 100.0 };
			Fake.bRootScaleShape = true;
			const FString Result = ApplyScaleFix(TEXT("manny"), TEXT("undeclared"), 0.0, Baseline,
				FImportAndFingerprintFn(Fake));
			TestTrue(FString::Printf(TEXT("[rootscale] OFF: an undeclared unit is FLAGGED, got: %s"), *Result),
				Result.StartsWith(TEXT("FLAGGED:")));
			TestEqual(TEXT("[rootscale] OFF: undeclared is never reimported"), Fake.Offsets.Num(), 1);
		}

		// OFF — a declared unit that does not match what was measured. The rig was scaled 3x by hand
		// inside a file that genuinely is in metres; the conversion was correct and the 3x is not ours.
		{
			FFakeImporter Fake{ /*TrueDriftFactor*/ 3.0 };
			Fake.bRootScaleShape = true;
			const FString Result = ApplyScaleFix(TEXT("manny"), TEXT("100 (m)"), 100.0, Baseline,
				FImportAndFingerprintFn(Fake));
			TestTrue(FString::Printf(TEXT("[rootscale] OFF: measured 3 under a declared 100 is FLAGGED, got: %s"),
				*Result), Result.StartsWith(TEXT("FLAGGED:")));
			TestEqual(TEXT("[rootscale] OFF: a mismatched factor is never reimported"), Fake.Offsets.Num(), 1);
		}

		// OFF — the shape guard. A root scale that ALSO moved bone lengths is not the importer's
		// conversion however well the bounds ratio matches the header, because a unit conversion
		// structurally cannot touch a bone's local translation.
		{
			FFakeImporter Fake{ /*TrueDriftFactor*/ 100.0 };   // bone-baked shape: MakeScaleReport
			Fake.bRefPoseViolation = true;
			const FString Result = ApplyScaleFix(TEXT("manny"), TEXT("100 (m)"), 100.0, Baseline,
				FImportAndFingerprintFn(Fake));
			TestTrue(FString::Printf(TEXT("[rootscale] OFF: bone-baked drift + a ref-pose scale FLAGs, got: %s"),
				*Result), Result.StartsWith(TEXT("FLAGGED:")));
			TestEqual(TEXT("[rootscale] OFF: and is never reimported"), Fake.Offsets.Num(), 1);
		}

		// OFF — the pre-import refusal is UNCHANGED. Authored 'Lcl Scaling' in a file that also declares
		// metres: the header now corroborates a conversion, and it must still change nothing. This is the
		// boundary the lane must not weaken, asserted rather than assumed. (2c) covers the same
		// precedence; this restates it against the new code path so a future edit to the discriminator
		// cannot quietly reorder the flag behind the import.
		{
			const FString Path = WriteAsciiFbxFixture(TEXT("declared_metres_and_node_scale.fbx"), 100.0,
				{ { TEXT("root"), FVector(1.5, 1.5, 1.5) }, { TEXT("pelvis"), FVector::OneVector } });
			if (TestTrue(TEXT("[rootscale] node-scale + metres fixture written"), !Path.IsEmpty()))
			{
				const FString Result = UAnimImportTools::FixImportScale(Path, kImportScaleDestPath, TEXT("manny"));
				TestTrue(FString::Printf(TEXT("[rootscale] OFF: authored node scale still hard-refuses, got: %s"),
					*Result), Result.StartsWith(TEXT("FLAGGED:")));
				TestTrue(TEXT("[rootscale] OFF: and still names the offending node"), Result.Contains(TEXT("'root'")));
				TestFalse(TEXT("[rootscale] OFF: nothing was applied"), Result.Contains(TEXT("applied")));
			}
		}
	}

	// --- (3) A missing file ERRORs ---------------------------------------------------------------
	{
		const FString Missing = FPaths::Combine(ImportScaleFixtureDir(), TEXT("does_not_exist.fbx"));
		const FString Result = UAnimImportTools::FixImportScale(Missing, kImportScaleDestPath, TEXT("manny"));
		TestTrue(FString::Printf(TEXT("[error] a missing FBX ERRORs, got: %s"), *Result),
			Result.StartsWith(TEXT("ERROR:")));
	}

	// (3b) the other refusals: a traversal-shaped rig name, and a destination that is not a package path.
	{
		const FString Path = WriteAsciiFbxFixture(TEXT("clean.fbx"), 1.0, { { TEXT("root"), FVector::OneVector } });
		if (!TestTrue(TEXT("[error] clean fixture written"), !Path.IsEmpty())) { return false; }

		TestTrue(TEXT("[error] a path-traversal rig name ERRORs"),
			UAnimImportTools::FixImportScale(Path, kImportScaleDestPath, TEXT("../evil")).StartsWith(TEXT("ERROR:")));
		TestTrue(TEXT("[error] a non-package destination ERRORs"),
			UAnimImportTools::FixImportScale(Path, TEXT("D:/somewhere"), TEXT("manny")).StartsWith(TEXT("ERROR:")));
	}

	// --- (4) The pre-import read itself ----------------------------------------------------------
	{
		const FString Path = WriteAsciiFbxFixture(TEXT("meters.fbx"), 100.0,
			{ { TEXT("root"), FVector::OneVector }, { TEXT("pelvis"), FVector::OneVector } });
		if (!TestTrue(TEXT("[parse] meters fixture written"), !Path.IsEmpty())) { return false; }

		FFbxScaleMetadata Meta;
		FString Error;
		const bool bParsed = ParseFbxScaleMetadata(Path, Meta, Error);
		if (TestTrue(FString::Printf(TEXT("[parse] ASCII FBX parses (%s)"), *Error), bParsed))
		{
			TestTrue(TEXT("[parse] the unit declaration was found"), Meta.bHasUnitScaleFactor);
			TestTrue(TEXT("[parse] UnitScaleFactor == 100"),
				FMath::IsNearlyEqual(Meta.UnitScaleFactor, 100.0, 1e-6));
			TestEqual(TEXT("[parse] the unit is described as metres"), Meta.DescribeUnit(), FString(TEXT("100 (m)")));
			TestEqual(TEXT("[parse] both Models were seen"), Meta.ModelCount, 2);
			TestEqual(TEXT("[parse] an all-identity rig reports no scaled nodes"), Meta.ScaledNodes.Num(), 0);
		}
	}

	// (4b) BINARY FBX walker, checked against real files that ship with the engine (expected values
	// derived independently, by a separate reader written from the format spec — not from this code).
	{
		const FString Character = EngineFbxSample(TEXT("AnimatedCharacter.fbx"));
		const FString BlenderCube = EngineFbxSample(TEXT("BlenderCube.fbx"));
		if (IFileManager::Get().FileExists(*Character) && IFileManager::Get().FileExists(*BlenderCube))
		{
			FFbxScaleMetadata Meta;
			FString Error;
			const bool bParsedCharacter = ParseFbxScaleMetadata(Character, Meta, Error);
			if (TestTrue(FString::Printf(TEXT("[binary] AnimatedCharacter.fbx parses (%s)"), *Error), bParsedCharacter))
			{
				TestTrue(TEXT("[binary] recognised as binary"), Meta.bBinary);
				TestEqual(TEXT("[binary] FBX version 7300"), Meta.FormatVersion, 7300);
				TestEqual(TEXT("[binary] 31 Model nodes"), Meta.ModelCount, 31);
				TestTrue(TEXT("[binary] UnitScaleFactor == 1 (cm)"),
					Meta.bHasUnitScaleFactor && FMath::IsNearlyEqual(Meta.UnitScaleFactor, 1.0, 1e-6));
				TestEqual(TEXT("[binary] a clean character has no scaled nodes"), Meta.ScaledNodes.Num(), 0);
			}

			// A real Blender export: its Mesh node carries Lcl Scaling 100 while the file still declares
			// centimetres — precisely the baked node scale the scale check refuses to silently rewrite. Its Camera
			// and Light nodes are scaled too and must NOT count: they say nothing about rig hygiene.
			FFbxScaleMetadata CubeMeta;
			FString CubeError;
			const bool bParsedCube = ParseFbxScaleMetadata(BlenderCube, CubeMeta, CubeError);
			if (TestTrue(FString::Printf(TEXT("[binary] BlenderCube.fbx parses (%s)"), *CubeError), bParsedCube))
			{
				TestEqual(TEXT("[binary] FBX version 7400"), CubeMeta.FormatVersion, 7400);
				TestEqual(TEXT("[binary] 5 Model nodes"), CubeMeta.ModelCount, 5);
				TestEqual(TEXT("[binary] only the Mesh node's baked scale counts"), CubeMeta.ScaledNodes.Num(), 1);
				if (CubeMeta.ScaledNodes.Num() == 1)
				{
					TestEqual(TEXT("[binary] the scaled node is 'Cube'"), CubeMeta.ScaledNodes[0].Name, FString(TEXT("Cube")));
					TestTrue(TEXT("[binary] its scale is 100"),
						FMath::IsNearlyEqual(CubeMeta.ScaledNodes[0].Scale.X, 100.0, 1e-3));
				}
				// THE ordering guard for the whole row, and the only one that can fail. A real,
				// translatable FBX is required: moving the import above the flag was verified to turn
				// this assertion red, while the same mutation left every synthetic-fixture
				// check green because a geometry-free fixture creates no package to detect.
				// AnyPackageUnder sees UNSAVED packages — a disk check cannot, since bSave=false.
				TestTrue(TEXT("[binary] a real baked-scale export is FLAGGED, not fixed"),
					UAnimImportTools::FixImportScale(BlenderCube, kImportScaleDestPath, TEXT("manny"))
						.StartsWith(TEXT("FLAGGED:")));
				TestFalse(TEXT("[binary] ORDERING: a real FBX was flagged without being imported"),
					AnyPackageUnder(kImportScaleDestPath));
			}
		}
		else
		{
			// Not the localOnly-asset convention: these ship with the engine, not with gitignored
			// licensed content, so their absence is unexpected. Warn rather than AddInfo, because the
			// row's ONLY falsifiable ordering guard lives in that branch — skipping quietly would leave
			// the flag-before-import property unguarded while the tier still read green.
			AddWarning(TEXT("SKIP (binary-FBX leg): Engine/Content/FbxEditorAutomation samples absent — "
				"the binary walker AND the flag-before-import ordering guard did NOT run"));
		}
	}

	return true;   // AddError already marks failure; returning true lets every sub-check report
}


// =====================================================================================================
// MotionTerminal.Verify.FbxHeaderWalkIntegrity
//
// WHY THIS ROW EXISTS. The binary FBX walker gives up on a property typecode it does not handle, then
// resyncs from the record's own declared length and carries on. That resync is correct — one exotic
// record must not cost the whole file — but it also made the abort INVISIBLE: after it, a reader that
// dropped a `P` record is byte-for-byte indistinguishable from one that read everything, and a `P`
// record that aborted at property 1 falls below the `Props.Num() >= 2` gate and never reaches
// ApplyPropertyRecord at all. A real `Lcl Scaling` went unrecorded, `ScaledNodes` came back empty, and
// `FixImportScale` IMPORTED the file instead of flagging it, with the design narrative still claiming a
// pre-import barrier.
//
// The gate for that fault has to be built out of two things, and the second is the one that matters:
//   (a) the abort must be COUNTED — FFbxScaleMetadata::AbortedPropertyRecordWalks; and
//   (b) something must READ the count. A counter nobody asserts on is the same fault in a new costume.
// So this row asserts the counter AND asserts the refusal the counter now drives.
//
// The two fixtures differ in exactly one byte position: the aborted one substitutes an unhandled
// typecode for the clean one's first `D`. Everything else — the record shape, the declared property
// count, the declared property-list length, the enclosing Model — is identical, so a difference in the
// verdict can only have come from the abort. Both are synthesised in-process from the format spec: no
// engine sample, no licensed content, no Interchange, no PIE, so this row exercises fully on a clean
// checkout (unlike the engine-sample binary leg inside Verify.FixImportScale, which warns and skips).
//
// PROVEN-OFF: deleting the `++Out.AbortedPropertyRecordWalks` increment in FbxScaleHeader.cpp turns
// [abort] the P-record abort is counted RED and, downstream, [abort] FixImportScale REFUSES RED too
// (verified by mutation, with the captured output).
//
// SECTION (4) is here rather than in a row of its own because it closes the OTHER half of the same question. Sections (1)-(3) prove an aborted PROPERTY
// walk is counted and acted on; (4) proves the same for a STRUCTURAL abort, which does not resync and
// therefore loses every record after it — a case that used to return `true` from ParseBinary with both
// property counters reading 0. Its proven-OFF is the four-byte difference between its own two
// fixtures, plus a mutation run with `++Out.AbortedStructuralWalks` deleted.
// =====================================================================================================
namespace
{
	// ---- Byte-level writers for the binary container. Little-endian, which is what the format is. ----
	void FbxPutU32(TArray<uint8>& B, uint32 V)
	{
		for (int32 i = 0; i < 4; ++i) { B.Add((uint8)((V >> (8 * i)) & 0xFF)); }
	}
	void FbxPutAnsi(TArray<uint8>& B, const ANSICHAR* S)
	{
		for (const ANSICHAR* P = S; *P; ++P) { B.Add((uint8)*P); }
	}
	// The 27-byte binary header every fixture starts with: magic, 0x00, 0x1A 0x00, then the version.
	void FbxPutPreamble(TArray<uint8>& B, uint32 Version)
	{
		FbxPutAnsi(B, "Kaydara FBX Binary  ");   // 20 bytes
		B.Add(0x00);                             // 21
		B.Add(0x1A); B.Add(0x00);                // 23 — where ParseBinary starts reading
		FbxPutU32(B, Version);                   // 27
	}
	void FbxPropString(TArray<uint8>& B, const ANSICHAR* S)
	{
		B.Add((uint8)'S');
		FbxPutU32(B, (uint32)FCStringAnsi::Strlen(S));
		FbxPutAnsi(B, S);
	}
	void FbxPropDouble(TArray<uint8>& B, double V)
	{
		B.Add((uint8)'D');
		uint64 Bits = 0;
		FMemory::Memcpy(&Bits, &V, sizeof(double));
		for (int32 i = 0; i < 8; ++i) { B.Add((uint8)((Bits >> (8 * i)) & 0xFF)); }
	}
	// A well-formed `P: "Lcl Scaling", "Lcl Scaling", "", "A", S, S, S` property list — 7 properties.
	TArray<uint8> MakeLclScalingP(double S)
	{
		TArray<uint8> P;
		FbxPropString(P, "Lcl Scaling");
		FbxPropString(P, "Lcl Scaling");
		FbxPropString(P, "");
		FbxPropString(P, "A");
		FbxPropDouble(P, S);
		FbxPropDouble(P, S);
		FbxPropDouble(P, S);
		return P;
	}
	void FbxPropInt32(TArray<uint8>& B, int32 V) { B.Add((uint8)'I'); FbxPutU32(B, (uint32)V); }

	// 'Q' is not in ReadBinaryProperty's switch and never will be — the FBX property typecodes are
	// Y C I F D L S R f d l i b. One byte is all it takes: the reader consumes the code, falls into
	// `default:`, clears bOk and returns false, and the record loop breaks with the rest of the
	// property block unread. This is the real-world shape too (a newer exporter emitting a typecode
	// this walker predates), not a corruption.
	void FbxPropUnhandledTypecode(TArray<uint8>& B) { B.Add((uint8)'Q'); }

	// The whole file is one contiguous buffer starting at 0, so B.Num() IS the absolute byte offset a
	// record's EndOffset field has to carry. Emit the header with a placeholder and patch it once the
	// record's true extent (properties + any nested children) is known.
	int32 FbxBeginNode(TArray<uint8>& B, const ANSICHAR* Name, uint32 DeclaredNumProps,
		const TArray<uint8>& Props)
	{
		const int32 EndOffsetAt = B.Num();
		FbxPutU32(B, 0);                           // EndOffset — patched by FbxEndNode
		FbxPutU32(B, DeclaredNumProps);            // what the record CLAIMS to hold: the walker's loop bound
		FbxPutU32(B, (uint32)Props.Num());         // PropertyListLen — the walker's resync anchor
		B.Add((uint8)FCStringAnsi::Strlen(Name));
		FbxPutAnsi(B, Name);
		B.Append(Props);
		return EndOffsetAt;
	}
	void FbxEndNode(TArray<uint8>& B, int32 EndOffsetAt)
	{
		const uint32 End = (uint32)B.Num();
		for (int32 i = 0; i < 4; ++i) { B[EndOffsetAt + i] = (uint8)((End >> (8 * i)) & 0xFF); }
	}

	// Objects { Model "pelvis" [LimbNode] { Properties70 { P: <PProps> } } } at FBX version 7300,
	// which keeps the record offsets 32-bit (7500 widened them to 64). No null terminator records:
	// the walker stops at each record's EndOffset and at the buffer's end.
	TArray<uint8> BuildBinaryFbxWithPRecord(const TArray<uint8>& PProps, uint32 PDeclaredNumProps)
	{
		TArray<uint8> B;
		FbxPutPreamble(B, 7300);

		const TArray<uint8> NoProps;
		TArray<uint8> ModelProps;
		FbxPropInt32(ModelProps, 100);
		FbxPropString(ModelProps, "pelvis");     // Props[1] -> the reported node name
		FbxPropString(ModelProps, "LimbNode");   // Props[2] -> the node type (must not be Camera/Light)

		const int32 ObjectsEnd = FbxBeginNode(B, "Objects", 0, NoProps);
		const int32 ModelEnd = FbxBeginNode(B, "Model", 3, ModelProps);
		const int32 P70End = FbxBeginNode(B, "Properties70", 0, NoProps);
		const int32 PEnd = FbxBeginNode(B, "P", PDeclaredNumProps, PProps);
		FbxEndNode(B, PEnd);
		FbxEndNode(B, P70End);
		FbxEndNode(B, ModelEnd);
		FbxEndNode(B, ObjectsEnd);
		return B;
	}

	// Objects { Model "pelvis" { Properties70 { } }  Model "spine" { Properties70 { P: Lcl Scaling 2,2,2 } } }
	//
	// TWO Models, and the SECOND is the one carrying the scale — that ordering is the whole point. The
	// structural fault this fixture exercises is not "the file is unreadable"; ParseBinary already
	// refuses a walk that yielded nothing. It is "the file read fine right up until it didn't", where
	// ModelCount > 0 satisfies the existing check while every record after the break — including the
	// one holding the Lcl Scaling — is never visited.
	//
	// `bCorruptSecondModelEndOffset` rewrites the second Model record's EndOffset to a value past the
	// end of the buffer. That is byte-for-byte what a file truncated inside that record looks like to
	// this walker: the record header reads, its declared extent is off the end, and there is no
	// resynchronisation point. Corrupting the offset rather than literally chopping the tail keeps the
	// ENCLOSING Objects record honest, so the break is the one being tested and not a second one.
	TArray<uint8> BuildBinaryFbxTwoModels(bool bCorruptSecondModelEndOffset)
	{
		TArray<uint8> B;
		FbxPutPreamble(B, 7300);

		const TArray<uint8> NoProps;
		TArray<uint8> Model1Props, Model2Props;
		FbxPropInt32(Model1Props, 100);
		FbxPropString(Model1Props, "pelvis");
		FbxPropString(Model1Props, "LimbNode");
		FbxPropInt32(Model2Props, 101);
		FbxPropString(Model2Props, "spine");
		FbxPropString(Model2Props, "LimbNode");

		const TArray<uint8> ScalingP = MakeLclScalingP(2.0);

		const int32 ObjectsEnd = FbxBeginNode(B, "Objects", 0, NoProps);

		const int32 Model1End = FbxBeginNode(B, "Model", 3, Model1Props);
		const int32 P70aEnd = FbxBeginNode(B, "Properties70", 0, NoProps);
		FbxEndNode(B, P70aEnd);
		FbxEndNode(B, Model1End);

		const int32 Model2End = FbxBeginNode(B, "Model", 3, Model2Props);
		const int32 P70bEnd = FbxBeginNode(B, "Properties70", 0, NoProps);
		const int32 PEnd = FbxBeginNode(B, "P", 7, ScalingP);
		FbxEndNode(B, PEnd);
		FbxEndNode(B, P70bEnd);
		FbxEndNode(B, Model2End);

		FbxEndNode(B, ObjectsEnd);

		if (bCorruptSecondModelEndOffset)
		{
			const uint32 PastEnd = (uint32)B.Num() + 4096;
			for (int32 i = 0; i < 4; ++i)
			{
				B[Model2End + i] = (uint8)((PastEnd >> (8 * i)) & 0xFF);
			}
		}
		return B;
	}

	// GlobalSettings { Properties70 { P: "UnitScaleFactor" … 1 } }  followed by  `WrapperDepth` nested
	// records ending in  Model "deep_pelvis" [LimbNode] { Properties70 { P: "Lcl Scaling" 2,2,2 } }.
	//
	// TWO top-level records, and the first one is the point. ParseBinary already refuses a walk that
	// yielded NOTHING (`ModelCount == 0 && !bHasUnitScaleFactor`), so a fixture whose only content is
	// out of reach would be refused by THAT check and the depth cap would never be the thing under
	// test. A shallow UnitScaleFactor means the walk always yields something, which is exactly the
	// shape of the fault: a file that read fine right up until it didn't.
	TArray<uint8> BuildBinaryFbxNested(int32 WrapperDepth)
	{
		TArray<uint8> B;
		FbxPutPreamble(B, 7300);

		const TArray<uint8> NoProps;

		TArray<uint8> UnitP;
		FbxPropString(UnitP, "UnitScaleFactor");
		FbxPropString(UnitP, "double");
		FbxPropString(UnitP, "");
		FbxPropString(UnitP, "");
		FbxPropDouble(UnitP, 1.0);
		const int32 GsEnd = FbxBeginNode(B, "GlobalSettings", 0, NoProps);
		const int32 Gs70End = FbxBeginNode(B, "Properties70", 0, NoProps);
		const int32 UnitEnd = FbxBeginNode(B, "P", 5, UnitP);
		FbxEndNode(B, UnitEnd);
		FbxEndNode(B, Gs70End);
		FbxEndNode(B, GsEnd);

		TArray<int32> WrapperEnds;
		for (int32 i = 0; i < WrapperDepth; ++i)
		{
			WrapperEnds.Add(FbxBeginNode(B, "Objects", 0, NoProps));
		}

		TArray<uint8> ModelProps;
		FbxPropInt32(ModelProps, 100);
		FbxPropString(ModelProps, "deep_pelvis");
		FbxPropString(ModelProps, "LimbNode");
		const TArray<uint8> ScalingP = MakeLclScalingP(2.0);

		const int32 ModelEnd = FbxBeginNode(B, "Model", 3, ModelProps);
		const int32 P70End = FbxBeginNode(B, "Properties70", 0, NoProps);
		const int32 PEnd = FbxBeginNode(B, "P", 7, ScalingP);
		FbxEndNode(B, PEnd);
		FbxEndNode(B, P70End);
		FbxEndNode(B, ModelEnd);
		for (int32 i = WrapperEnds.Num() - 1; i >= 0; --i) { FbxEndNode(B, WrapperEnds[i]); }
		return B;
	}

	FString WriteBinaryFbxFixture(const FString& FileName, const TArray<uint8>& Bytes)
	{
		const FString Path = FPaths::Combine(ImportScaleFixtureDir(), FileName);
		IFileManager::Get().MakeDirectory(*ImportScaleFixtureDir(), /*Tree*/ true);
		return FFileHelper::SaveArrayToFile(Bytes, *Path) ? Path : FString();
	}

	// A destination nothing else in the suite writes to, so "did an import happen" cannot be answered by
	// another row's leftovers. Checked with AnyPackageUnder, which sees in-memory packages for the reason
	// it documents: ImportWithScaleOffset sets bSave=false, so a disk check here could never fail.
	const TCHAR* kImportScaleWalkDestPath = TEXT("/Game/MT/ImportScaleWalkTest");
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FVerifyFbxHeaderWalkIntegrityTest,
	"MotionTerminal.Verify.FbxHeaderWalkIntegrity",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::ProductFilter)

bool FVerifyFbxHeaderWalkIntegrityTest::RunTest(const FString& Parameters)
{
	using namespace MotionTerminal;

	const FFixtureScope FixtureCleanup;

	// The identical `P` record either side of one substituted byte: "Lcl Scaling" naming the property,
	// then the value list. The clean one is 7 declared properties, the aborted one 5 — in both cases the
	// DECLARED count matches what was encoded, so the abort comes from the typecode and from nothing else
	// (a lying property count would be a different, and much easier, failure to detect).
	const TArray<uint8> CleanP = MakeLclScalingP(2.0);

	TArray<uint8> AbortedP;
	FbxPropString(AbortedP, "Lcl Scaling");
	FbxPropUnhandledTypecode(AbortedP);      // <- the ONE difference
	FbxPropDouble(AbortedP, 2.0);
	FbxPropDouble(AbortedP, 2.0);
	FbxPropDouble(AbortedP, 2.0);

	const FString CleanPath = WriteBinaryFbxFixture(TEXT("walk_clean.fbx"),
		BuildBinaryFbxWithPRecord(CleanP, 7));
	const FString AbortPath = WriteBinaryFbxFixture(TEXT("walk_aborted.fbx"),
		BuildBinaryFbxWithPRecord(AbortedP, 5));
	if (!TestTrue(TEXT("[fixtures] both binary FBX fixtures written"),
		!CleanPath.IsEmpty() && !AbortPath.IsEmpty()))
	{
		return false;
	}

	// --- (1) POSITIVE CONTROL: the walker reads the whole record, and says so -----------------------
	{
		FFbxScaleMetadata Meta;
		FString Error;
		if (TestTrue(FString::Printf(TEXT("[clean] synthetic binary FBX parses (%s)"), *Error),
			ParseFbxScaleMetadata(CleanPath, Meta, Error)))
		{
			TestTrue(TEXT("[clean] recognised as binary"), Meta.bBinary);
			TestEqual(TEXT("[clean] FBX version 7300"), Meta.FormatVersion, 7300);
			TestEqual(TEXT("[clean] one Model node"), Meta.ModelCount, 1);
			TestEqual(TEXT("[clean] the baked Lcl Scaling IS recorded"), Meta.ScaledNodes.Num(), 1);
			if (Meta.ScaledNodes.Num() == 1)
			{
				TestEqual(TEXT("[clean] reported by node name"), Meta.ScaledNodes[0].Name, FString(TEXT("pelvis")));
				TestTrue(TEXT("[clean] its scale is 2"),
					FMath::IsNearlyEqual(Meta.ScaledNodes[0].Scale.X, 2.0, 1e-9));
			}
			// The control that makes the counter meaningful: it must read zero when nothing was dropped,
			// or a nonzero reading downstream would prove nothing about this file.
			TestEqual(TEXT("[clean] no aborted property walks"), Meta.AbortedPropertyWalks, 0);
			TestEqual(TEXT("[clean] no aborted P-record walks"), Meta.AbortedPropertyRecordWalks, 0);
		}
	}

	// --- (2) THE FAULT ITSELF: one unhandled typecode silently costs the scale ----------------------
	{
		FFbxScaleMetadata Meta;
		FString Error;
		// Parsing still SUCCEEDS. That is not a bug — the resync is what keeps one exotic record from
		// costing the whole file — but it is precisely why the loss needed its own signal.
		if (TestTrue(FString::Printf(TEXT("[abort] the walk survives the unhandled typecode (%s)"), *Error),
			ParseFbxScaleMetadata(AbortPath, Meta, Error)))
		{
			TestEqual(TEXT("[abort] the structural walk stayed in sync (still one Model)"), Meta.ModelCount, 1);
			// The fault, asserted as fact rather than described in prose: the SAME Lcl Scaling that the
			// clean fixture reports is now invisible in ScaledNodes. Everything the pre-import lane
			// looks at says "clean file".
			TestEqual(TEXT("[abort] the real Lcl Scaling goes UNRECORDED (this is the fault)"),
				Meta.ScaledNodes.Num(), 0);
			// ...and this is the signal that distinguishes it from a genuinely clean file.
			TestEqual(TEXT("[abort] the P-record abort is counted"), Meta.AbortedPropertyRecordWalks, 1);
			TestEqual(TEXT("[abort] exactly one record went unread in total"), Meta.AbortedPropertyWalks, 1);
		}
	}

	// --- (3) THE COUNT IS READ, NOT MERELY KEPT ----------------------------------------------------
	// A counter nobody asserts on is the fault this row exists to close, one level up. FixImportScale
	// must reach a DIFFERENT verdict for the two fixtures, and neither verdict may import anything.
	{
		if (!TestFalse(TEXT("[dest] nothing under the walk-test destination before either call"),
			AnyPackageUnder(kImportScaleWalkDestPath)))
		{
			return false;   // a leftover package would make everything below unfalsifiable
		}

		const FString CleanResult = UAnimImportTools::FixImportScale(CleanPath, kImportScaleWalkDestPath, TEXT("manny"));
		TestTrue(FString::Printf(TEXT("[clean] a readable baked scale is FLAGGED (%s)"), *CleanResult),
			CleanResult.StartsWith(TEXT("FLAGGED:")));

		const FString AbortResult = UAnimImportTools::FixImportScale(AbortPath, kImportScaleWalkDestPath, TEXT("manny"));
		TestTrue(FString::Printf(TEXT("[abort] FixImportScale REFUSES an indeterminate read (%s)"), *AbortResult),
			AbortResult.StartsWith(TEXT("ERROR:")));
		TestTrue(TEXT("[abort] and says why, in the caller's own words"),
			AbortResult.Contains(TEXT("INDETERMINATE")));

		// The property the whole import-scale scope boundary is written around: flag/refuse BEFORE
		// importing. Without the counter check the aborted fixture falls through to a real import.
		TestFalse(TEXT("[ordering] neither verdict imported anything"), AnyPackageUnder(kImportScaleWalkDestPath));
	}

	// --- (4) THE STRUCTURAL HALF ------------------------------------------------------------------
	//
	// Sections (1)-(3) close the PROPERTY-walk abort, which resyncs and is survivable. The STRUCTURAL
	// abort does not resync: `ReadBinaryNode` clears bOk on a corrupt record end-offset, a failed
	// header/name read, or an out-of-bounds resync, and after any of those every remaining record is
	// unvisited. Nothing counted it, and `ParseBinary` returned TRUE as long as it had seen one Model
	// or a UnitScaleFactor BEFORE the break — so a binary FBX truncated partway through `Objects` was
	// observationally identical to a clean one and its rig imported unvetted.
	//
	// The two fixtures below differ in FOUR BYTES: the second Model record's declared EndOffset. The
	// intact one proves the Lcl Scaling on that second Model is readable and recorded; the corrupt one
	// proves it is lost, that the loss is counted, and that the loss is now REFUSED rather than
	// reported as a completed walk.
	{
		const FString IntactPath = WriteBinaryFbxFixture(TEXT("walk_two_models.fbx"),
			BuildBinaryFbxTwoModels(/*bCorruptSecondModelEndOffset*/ false));
		const FString TruncPath = WriteBinaryFbxFixture(TEXT("walk_truncated.fbx"),
			BuildBinaryFbxTwoModels(/*bCorruptSecondModelEndOffset*/ true));
		if (TestTrue(TEXT("[struct] both two-Model fixtures written"),
			!IntactPath.IsEmpty() && !TruncPath.IsEmpty()))
		{
			// (4a) POSITIVE CONTROL. Without this the corrupt case proves nothing: an empty
			// ScaledNodes below would be indistinguishable from a fixture that never had a scale.
			FFbxScaleMetadata Intact;
			FString IntactError;
			if (TestTrue(FString::Printf(TEXT("[struct] the intact two-Model file parses (%s)"), *IntactError),
				ParseFbxScaleMetadata(IntactPath, Intact, IntactError)))
			{
				TestEqual(TEXT("[struct] both Models are visited"), Intact.ModelCount, 2);
				TestEqual(TEXT("[struct] the SECOND Model's Lcl Scaling IS recorded"),
					Intact.ScaledNodes.Num(), 1);
				if (Intact.ScaledNodes.Num() == 1)
				{
					TestEqual(TEXT("[struct] and it is the second node, by name"),
						Intact.ScaledNodes[0].Name, FString(TEXT("spine")));
				}
				TestEqual(TEXT("[struct] no structural abort on a well-formed file"),
					Intact.AbortedStructuralWalks, 0);
				TestEqual(TEXT("[struct] and no property abort either"),
					Intact.AbortedPropertyWalks, 0);
			}

			// (4b) THE FAULT. ModelCount > 0 — so the pre-existing "yielded nothing" check passes —
			// and the scale is gone.
			FFbxScaleMetadata Trunc;
			FString TruncError;
			const bool bParsed = ParseFbxScaleMetadata(TruncPath, Trunc, TruncError);
			TestEqual(TEXT("[struct] the first Model was read before the break"), Trunc.ModelCount, 1);
			TestEqual(TEXT("[struct] the second Model's Lcl Scaling is LOST (this is the fault)"),
				Trunc.ScaledNodes.Num(), 0);
			TestEqual(TEXT("[struct] neither property counter sees it - they cannot"),
				Trunc.AbortedPropertyRecordWalks, 0);
			TestEqual(TEXT("[struct] the structural abort IS counted"), Trunc.AbortedStructuralWalks, 1);

			// (4c) AND THE COUNT IS READ. The header's contract is "a true return means the walk
			// completed"; this is the path on which that used to be false.
			TestFalse(TEXT("[struct] ParseFbxScaleMetadata REFUSES an incomplete walk"), bParsed);
			TestTrue(FString::Printf(TEXT("[struct] and says what happened (%s)"), *TruncError),
				TruncError.Contains(TEXT("structurally broken")));

			// (4d) THE VERDICT DIFFERS, AND NOTHING IS IMPORTED. Same shape as section (3): a counter
			// nobody acts on is the same defect in a new costume.
			if (TestFalse(TEXT("[struct] nothing under the walk-test destination before the calls"),
				AnyPackageUnder(kImportScaleWalkDestPath)))
			{
				const FString IntactResult =
					UAnimImportTools::FixImportScale(IntactPath, kImportScaleWalkDestPath, TEXT("manny"));
				TestTrue(FString::Printf(TEXT("[struct] the intact file is FLAGGED for its real scale (%s)"),
					*IntactResult), IntactResult.StartsWith(TEXT("FLAGGED:")));

				const FString TruncResult =
					UAnimImportTools::FixImportScale(TruncPath, kImportScaleWalkDestPath, TEXT("manny"));
				TestTrue(FString::Printf(TEXT("[struct] the truncated file is REFUSED (%s)"), *TruncResult),
					TruncResult.StartsWith(TEXT("ERROR:")));
				TestTrue(TEXT("[struct] with the structural reason, not a generic parse failure"),
					TruncResult.Contains(TEXT("structurally broken")));

				TestFalse(TEXT("[struct] neither verdict imported anything"), AnyPackageUnder(kImportScaleWalkDestPath));
			}
		}
	}

	// --- (5) THE RECURSION BACKSTOP IS AN ABORT, NOT A SHRUG ----------------------------------------
	//
	// Section (4) closed the record-grid failures. The 32-deep recursion cap was the same fault one
	// door along: it skipped a whole child subtree SILENTLY, counted nothing, and returned true — so a
	// `Model` carrying `Lcl Scaling` below that depth was unread while the walk reported as completed,
	// in the branch that exists FOR corrupt data. `FbxScaleHeader.h`'s contract ("a true return means
	// the walk completed") is load-bearing, so that door had to be closed too.
	//
	// The two fixtures differ ONLY in how many wrapper records sit above the Model, and both carry a
	// shallow UnitScaleFactor so the pre-existing "yielded nothing" refusal cannot be what fires.
	{
		const FString ShallowPath = WriteBinaryFbxFixture(TEXT("walk_nested_shallow.fbx"),
			BuildBinaryFbxNested(5));
		const FString DeepPath = WriteBinaryFbxFixture(TEXT("walk_nested_deep.fbx"),
			BuildBinaryFbxNested(40));
		if (TestTrue(TEXT("[depth] both nested fixtures written"),
			!ShallowPath.IsEmpty() && !DeepPath.IsEmpty()))
		{
			// (5a) POSITIVE CONTROL at a legal depth. Without it, an empty ScaledNodes below proves
			// nothing about the cap.
			FFbxScaleMetadata Shallow;
			FString ShallowError;
			if (TestTrue(FString::Printf(TEXT("[depth] the 5-deep fixture parses (%s)"), *ShallowError),
				ParseFbxScaleMetadata(ShallowPath, Shallow, ShallowError)))
			{
				TestTrue(TEXT("[depth] its UnitScaleFactor is read"), Shallow.bHasUnitScaleFactor);
				TestEqual(TEXT("[depth] the nested Model is reached"), Shallow.ModelCount, 1);
				TestEqual(TEXT("[depth] its Lcl Scaling IS recorded"), Shallow.ScaledNodes.Num(), 1);
				if (Shallow.ScaledNodes.Num() == 1)
				{
					TestEqual(TEXT("[depth] by name"), Shallow.ScaledNodes[0].Name,
						FString(TEXT("deep_pelvis")));
				}
				TestEqual(TEXT("[depth] no structural abort at a legal depth"),
					Shallow.AbortedStructuralWalks, 0);
			}

			// (5b) THE FAULT, past the cap.
			FFbxScaleMetadata Deep;
			FString DeepError;
			const bool bParsed = ParseFbxScaleMetadata(DeepPath, Deep, DeepError);
			TestTrue(TEXT("[depth] the shallow UnitScaleFactor is still read, so the walk yielded "
				"SOMETHING and the pre-existing empty-walk refusal is not what fires"),
				Deep.bHasUnitScaleFactor);
			TestEqual(TEXT("[depth] the Model past the cap is never visited"), Deep.ModelCount, 0);
			TestEqual(TEXT("[depth] so its Lcl Scaling is LOST (this is the fault)"),
				Deep.ScaledNodes.Num(), 0);
			TestEqual(TEXT("[depth] the skipped subtree IS counted"), Deep.AbortedStructuralWalks, 1);
			TestFalse(TEXT("[depth] and ParseFbxScaleMetadata REFUSES rather than reporting a "
				"completed walk"), bParsed);
			TestTrue(FString::Printf(TEXT("[depth] with the structural reason (%s)"), *DeepError),
				DeepError.Contains(TEXT("structurally broken")));

			// (5c) AND THE REFUSAL REACHES THE CALLER.
			const FString DeepResult =
				UAnimImportTools::FixImportScale(DeepPath, kImportScaleWalkDestPath, TEXT("manny"));
			TestTrue(FString::Printf(TEXT("[depth] FixImportScale refuses it (%s)"), *DeepResult),
				DeepResult.StartsWith(TEXT("ERROR:")) && DeepResult.Contains(TEXT("structurally broken")));
			TestFalse(TEXT("[depth] and imported nothing"), AnyPackageUnder(kImportScaleWalkDestPath));
		}
	}

	return true;
}

#endif // WITH_AUTOMATION_TESTS
