#include "AnimImportTools.h"

#include "FbxScaleHeader.h"
#include "ImportScaleCheck.h"

#include "AssetImportTask.h"
#include "AssetToolsModule.h"
#include "Engine/SkeletalMesh.h"
#include "HAL/FileManager.h"
#include "IAssetTools.h"
#include "InterchangeGenericAssetsPipeline.h"
#include "InterchangeManager.h"
#include "Misc/FileHelper.h"
#include "Misc/PackageName.h"
#include "Modules/ModuleManager.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "UObject/StrongObjectPtr.h"
#include "UObject/UObjectGlobals.h"

namespace
{
	USkeletalMesh* LoadSkeletalMesh(const FString& Path, FString& OutError)
	{
		USkeletalMesh* Mesh = LoadObject<USkeletalMesh>(nullptr, *Path);
		if (!Mesh)
		{
			OutError = FString::Printf(TEXT("could not load SkeletalMesh at '%s'"), *Path);
		}
		return Mesh;
	}

	// Load + parse a scale baseline JSON file into an FJsonObject. Empty return + OutError on failure.
	TSharedPtr<FJsonObject> LoadBaseline(const FString& RigName, FString& OutError)
	{
		const FString Path = MotionTerminal::ScaleBaselinePath(RigName);
		FString Text;
		if (!FFileHelper::LoadFileToString(Text, *Path))
		{
			OutError = FString::Printf(TEXT("no baseline at '%s' (run SnapshotScaleBaseline first)"), *Path);
			return nullptr;
		}
		TSharedPtr<FJsonObject> Obj;
		const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Text);
		if (!FJsonSerializer::Deserialize(Reader, Obj) || !Obj.IsValid())
		{
			OutError = FString::Printf(TEXT("baseline '%s' is not valid JSON"), *Path);
			return nullptr;
		}
		return Obj;
	}

	// Human-facing body of the pre-import FLAG. Names the offending nodes: "freeze transforms" is only
	// actionable in Maya if the animator knows WHICH node to freeze.
	FString DescribeScaledNodes(const MotionTerminal::FFbxScaleMetadata& Meta)
	{
		constexpr int32 kMaxListed = 8;
		TArray<FString> Lines;
		for (int32 i = 0; i < Meta.ScaledNodes.Num() && i < kMaxListed; ++i)
		{
			const MotionTerminal::FFbxScaledNode& Node = Meta.ScaledNodes[i];
			const FString TypeTag = Node.Type.IsEmpty() ? FString() : FString::Printf(TEXT(" [%s]"), *Node.Type);
			Lines.Add(FString::Printf(TEXT("  '%s'%s Lcl Scaling (%.4g, %.4g, %.4g)%s"),
				*Node.Name, *TypeTag, Node.Scale.X, Node.Scale.Y, Node.Scale.Z,
				Node.bNonUniform ? TEXT("  NON-UNIFORM") : TEXT("")));
		}
		if (Meta.ScaledNodes.Num() > kMaxListed)
		{
			Lines.Add(FString::Printf(TEXT("  ... and %d more"), Meta.ScaledNodes.Num() - kMaxListed));
		}
		return FString::Join(Lines, TEXT("\n"));
	}

	// Imports FbxPath through Interchange with an explicit import_offset_uniform_scale, returning the
	// SkeletalMesh it produced. The pipeline instance is a plain transient UInterchangeGenericAssetsPipeline
	// (Epic's class defaults) handed to Interchange as a stack override via UAssetImportTask::Options —
	// the same route the UE Python recipe takes. All three objects are rooted for the call: they are
	// freshly NewObject'd and nothing else references them, so a GC pass mid-import would collect them.
	USkeletalMesh* ImportWithScaleOffset(const FString& FbxPath, const FString& DestPackagePath,
		double OffsetUniformScale, FString& OutError)
	{
		TStrongObjectPtr<UInterchangeGenericAssetsPipeline> Pipeline(
			NewObject<UInterchangeGenericAssetsPipeline>(GetTransientPackage()));
		Pipeline->ImportOffsetUniformScale = static_cast<float>(OffsetUniformScale);

		TStrongObjectPtr<UInterchangePipelineStackOverride> Override(
			NewObject<UInterchangePipelineStackOverride>(GetTransientPackage()));
		Override->AddPipeline(Pipeline.Get());

		TStrongObjectPtr<UAssetImportTask> Task(NewObject<UAssetImportTask>());
		Task->Filename = FbxPath;
		Task->DestinationPath = DestPackagePath;
		Task->bAutomated = true;                 // never raise a modal dialog: this runs headless and on camera
		Task->bReplaceExisting = true;           // the corrective reimport must overwrite the first pass
		Task->bReplaceExistingSettings = true;
		Task->bAsync = false;                    // the gate has to run against the finished asset
		Task->bSave = false;                     // saving while the verifier holds packages open is an appError
		Task->Options = Override.Get();

		IAssetTools& AssetTools = FModuleManager::LoadModuleChecked<FAssetToolsModule>(TEXT("AssetTools")).Get();
		AssetTools.ImportAssetTasks({ Task.Get() });

		for (UObject* Imported : Task->GetObjects())
		{
			if (USkeletalMesh* Mesh = Cast<USkeletalMesh>(Imported)) { return Mesh; }
		}
		OutError = FString::Printf(
			TEXT("importing '%s' into '%s' produced no SkeletalMesh (%d object(s)) — FixImportScale gates a ")
			TEXT("skeletal mesh, so an animation-only or static-mesh FBX cannot be verified here"),
			*FbxPath, *DestPackagePath, Task->GetObjects().Num());
		return nullptr;
	}
}

FString UAnimImportTools::SnapshotScaleBaseline(const FString& SkeletalMeshPath, const FString& RigName, bool bLocalOnly)
{
	// Sanitize BEFORE RigName becomes a filename (write primitive; FPaths::Combine won't resolve "..").
	const FString RigErr = MotionTerminal::ValidateRigName(RigName);
	if (!RigErr.IsEmpty()) { return FString::Printf(TEXT("ERROR: %s"), *RigErr); }

	FString Error;
	USkeletalMesh* Mesh = LoadSkeletalMesh(SkeletalMeshPath, Error);
	if (!Mesh) { return FString::Printf(TEXT("ERROR: %s"), *Error); }

	TSharedRef<FJsonObject> Report = MotionTerminal::BuildScaleReportJson(*Mesh);
	// Baseline metadata (kept after the measured fields so the report reads first).
	Report->SetStringField(TEXT("rig"), RigName);
	Report->SetStringField(TEXT("asset"), SkeletalMeshPath);
	Report->SetBoolField(TEXT("localOnly"), bLocalOnly);
	Report->SetNumberField(TEXT("lengthTolerancePct"), 1.0);
	Report->SetNumberField(TEXT("schemaVersion"), 1);

	const FString Text = MotionTerminal::ScaleReportToString(Report);
	const FString Path = MotionTerminal::ScaleBaselinePath(RigName);
	IFileManager::Get().MakeDirectory(*MotionTerminal::ScaleBaselineDir(), /*Tree*/ true);
	// Force UTF-8 without BOM (git-friendly), matching SnapshotBaseline (AnimGraphTools.cpp).
	if (!FFileHelper::SaveStringToFile(Text, *Path, FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
	{
		return FString::Printf(TEXT("ERROR: could not write '%s'"), *Path);
	}
	return FString::Printf(TEXT("ok: wrote %s (%d chars)"), *Path, Text.Len());
}

FString UAnimImportTools::CheckImportScale(const FString& SkeletalMeshPath)
{
	FString Error;
	USkeletalMesh* Mesh = LoadSkeletalMesh(SkeletalMeshPath, Error);
	if (!Mesh) { return FString::Printf(TEXT("ERROR: %s"), *Error); }

	TSharedRef<FJsonObject> Report = MotionTerminal::BuildScaleReportJson(*Mesh);

	// Heuristic diagnosis (no baseline to ratio against — that is AssertImportScale's job).
	FString SuspectedCause = TEXT("none obvious");
	const TArray<TSharedPtr<FJsonValue>>* Viol = nullptr;
	double Height = 0.0;
	Report->TryGetNumberField(TEXT("boundsHeightUU"), Height);
	if (Report->TryGetArrayField(TEXT("refPoseScaleViolations"), Viol) && Viol && Viol->Num() > 0)
	{
		SuspectedCause = TEXT("ref-pose scale violation — a rig-intentional scale that silently corrupts mannequin-based retarget (scale detection #3)");
	}
	else if (Report->HasTypedField<EJson::Number>(TEXT("importScale")) &&
			 FMath::Abs(Report->GetNumberField(TEXT("importScale")) - 1.0) > 1e-3)
	{
		SuspectedCause = FString::Printf(TEXT("non-unit legacy import scale (%.4f) — meshes/anims scaled at import (root cause 1a/1b)"),
			Report->GetNumberField(TEXT("importScale")));
	}
	else if (Height > UE_KINDA_SMALL_NUMBER && (Height < 50.0 || Height > 400.0))
	{
		SuspectedCause = FString::Printf(TEXT("implausible bounds height %.1fuu for a humanoid — possible unit mismatch (root cause 1a/1b)"), Height);
	}

	return FString::Printf(TEXT("%s\nsuspectedCause: %s"), *MotionTerminal::ScaleReportToString(Report), *SuspectedCause);
}

FString UAnimImportTools::AssertImportScale(const FString& SkeletalMeshPath, const FString& RigName)
{
	// Same guard as the write path: RigName reaches ScaleBaselinePath via LoadBaseline.
	const FString RigErr = MotionTerminal::ValidateRigName(RigName);
	if (!RigErr.IsEmpty()) { return FString::Printf(TEXT("ERROR: %s"), *RigErr); }

	FString Error;
	USkeletalMesh* Mesh = LoadSkeletalMesh(SkeletalMeshPath, Error);
	if (!Mesh) { return FString::Printf(TEXT("ERROR: %s"), *Error); }

	TSharedPtr<FJsonObject> Baseline = LoadBaseline(RigName, Error);
	if (!Baseline.IsValid()) { return FString::Printf(TEXT("ERROR: %s"), *Error); }

	const TSharedRef<FJsonObject> Current = MotionTerminal::BuildScaleReportJson(*Mesh);
	const MotionTerminal::FScaleDriftSummary Drift =
		MotionTerminal::SummarizeScaleDrift(Baseline.ToSharedRef(), Current);
	if (!Drift.IsClean()) { return Drift.Diff; }
	// "OK" alone would hide a check that went dark on this mesh (e.g. an Interchange import makes the
	// stale-option audit unreadable). Report the pass and what it could not cover.
	return Drift.Notes.IsEmpty() ? TEXT("OK") : FString::Printf(TEXT("OK\n%s"), *Drift.Notes);
}

// --- Import-scale fix half ----------------------------------------------------------------------
//
// WHY THE OFFSET STARTS AT 1.0 AND THE CORRECTION IS MEASURED, NOT READ FROM THE HEADER.
//
// The original body of research/fbx-scale-import-ue58.md records that Interchange "removed"
// auto-unit-detection and exposes only a flat manual knob, so the fix is to parse the FBX header's
// UnitScaleFactor and feed it into import_offset_uniform_scale. That is NOT true of UE 5.8, and doing it
// would double-apply the unit conversion — manufacturing the exact 100x corruption this check exists to catch.
//
// THE BACKEND THAT ACTUALLY RUNS is the Autodesk FBX SDK parser, not uFBX. UfbxParser.h:98-103 is
// accurate about the uFBX backend, but it describes a path this project never enters —
// bUseUfbxParser defaults false (InterchangeFbxTranslator.h:64), is false again when translator settings
// are absent (InterchangeFbxTranslator.cpp:311), and the worker path forces FBX_SDK outright
// (InterchangeFbxTranslator.cpp:85-88). No CVar or .ini selects it. The operative 5.8 source is:
//   Engine/Plugins/Interchange/Runtime/Source/Import/Public/Fbx/InterchangeFbxTranslator.h:52
//       bool bConvertSceneUnit = true;                        (the translator-settings default)
//   .../Parsers/Fbx/Private/FbxConvert.cpp:116-122
//       if (bConvertSceneUnit) { if (Unit != FbxSystemUnit::cm) FbxSystemUnit::cm.ConvertScene(Scene); }
// — the ONLY unit scaling in the whole path. Note the inner condition: a file declaring centimetres is
// never rescaled at all, which is what makes the declared unit a usable discriminator below.
//
// AND THE CONVERSION IS A TRANSFORM, NOT BAKED DATA. Interchange substitutes the composed GLOBAL
// transform for bone 0 and leaves every other bone's local transform alone
// (InterchangeSkeletonHelper.cpp:319-342, bBakeMeshes defaulting true), so a unit conversion lands as a
// ref-pose SCALE on the root bone: every bone length is unchanged and only the bounds move.
// import_offset_uniform_scale travels that identical route (InterchangeGenericAssetsPipeline.cpp:1306-1314),
// which is why the reciprocal cancels it exactly — and why a non-1.0 offset by itself CREATES a root
// ref-pose scale. Full derivation, with the measurement: research/interchange-unit-scale-root-bone.md.
//
// import_offset_uniform_scale is a pipeline offset applied ON TOP of that normalisation. So a
// self-consistent FBX — whatever unit it declares — needs offset 1.0, and the failure that survives is a
// file whose declared unit disagrees with its geometry (root cause 1a: Maya stores cm internally regardless of
// the display unit). The correction is therefore taken from the per-rig gate's MEASURED factor, never
// read out of the header — the header is the thing that is lying. The header's number is still passed
// down, but only as a CORROBORATOR: ApplyScaleFix will treat a root-only drift as the importer's own
// conversion (rather than as an artist's baked scale) exactly when the measured factor equals the
// declared one. Measure, correct, re-verify — and never report success on an unverified mesh.

FString UAnimImportTools::FixImportScale(const FString& FbxPath, const FString& DestPackagePath, const FString& RigName)
{
	const FString RigErr = MotionTerminal::ValidateRigName(RigName);
	if (!RigErr.IsEmpty()) { return FString::Printf(TEXT("ERROR: %s"), *RigErr); }

	if (!FPackageName::IsValidPath(DestPackagePath))
	{
		return FString::Printf(
			TEXT("ERROR: destination '%s' is not a mounted package path (expected e.g. /Game/MT/Imported)"),
			*DestPackagePath);
	}

	// (1) Authoritative pre-import read of the exporter's own numbers (detection #1).
	MotionTerminal::FFbxScaleMetadata Meta;
	FString Error;
	if (!MotionTerminal::ParseFbxScaleMetadata(FbxPath, Meta, Error))
	{
		return FString::Printf(TEXT("ERROR: %s"), *Error);
	}

	// (2) Flag-don't-fix — and flag BEFORE importing, so a rig carrying craft decisions is never
	// touched at all rather than imported-then-refused.
	if (Meta.ScaledNodes.Num() > 0)
	{
		return FString::Printf(
			TEXT("FLAGGED: %d node(s) in '%s' carry a baked or rig-intentional local scale. The import-scale check detects and ")
			TEXT("flags these, never silently fixes them — auto-'freezing' rewrites artist-intended ")
			TEXT("proportions. Freeze transforms in Maya and re-export. Nothing was imported. ")
			TEXT("Declared unit: %s.\n%s"),
			Meta.ScaledNodes.Num(), *FbxPath, *Meta.DescribeUnit(), *DescribeScaledNodes(Meta));
	}

	// (2b) AN INDETERMINATE PRE-IMPORT READ IS NOT A CLEAN ONE.
	//
	// The narrative around (2) is "flag BEFORE importing, so a rig carrying craft decisions is never
	// touched." That guarantee is only as good as the parser's coverage, and the binary walker gives up
	// on a property typecode it does not handle. It resyncs and keeps going — deliberately, so one exotic
	// record cannot cost the whole file — but a `P` record that aborted at property 1 never reaches
	// `ApplyPropertyRecord` at all. `Meta.ScaledNodes.Num() == 0` then means "no scale found" and "the
	// record that would have carried the scale was unreadable" with no way to tell them apart, and (2)
	// falls through to a real import of a rig nobody vetted.
	//
	// It degraded into the POST-import lane rather than into a silent pass — ApplyScaleFix's ref-pose gate
	// still measures the landed mesh — which is why it was a lesser gap. But "flagged before anything was
	// imported" is the property the import-scale scope boundary is written around, so the honest answer when the read
	// is indeterminate is to refuse, not to import and hope the second gate catches it. ERROR, not FLAGGED:
	// FLAGGED asserts something WAS found; this says the question could not be answered.
	//
	// Ordered AFTER (2) on purpose: if scaled nodes were found anyway, naming them is strictly more useful
	// than reporting an incomplete read, and either way nothing is imported.
	if (Meta.AbortedPropertyRecordWalks > 0)
	{
		return FString::Printf(
			TEXT("ERROR: %d FBX property record(s) in '%s' could not be read to the end, so the pre-import ")
			TEXT("scale read is INDETERMINATE — a 'Lcl Scaling' may be present and unrecorded. Refusing to ")
			TEXT("import rather than treating an incomplete read as a clean one. (%d record(s) unread in ")
			TEXT("total; the walker skips property typecodes it does not handle.) Re-export from the ")
			TEXT("authoring app, or convert to ASCII FBX, and retry. Nothing was imported."),
			Meta.AbortedPropertyRecordWalks, *FbxPath, Meta.AbortedPropertyWalks);
	}

	// (3) A fix that cannot be re-verified is not a fix — the baseline is required, not optional.
	TSharedPtr<FJsonObject> Baseline = LoadBaseline(RigName, Error);
	if (!Baseline.IsValid()) { return FString::Printf(TEXT("ERROR: %s"), *Error); }

	// (4) Everything downstream — import at 1.0, gate, correct, re-gate — is ApplyScaleFix's, with the
	// real import injected here. That seam is why the decision path is testable headlessly; keep this
	// lambda as the ONLY thing this function contributes to it.
	auto ImportAndFingerprint =
		[&FbxPath, &DestPackagePath](double OffsetUniformScale, FString& OutError) -> TSharedPtr<FJsonObject>
		{
			USkeletalMesh* Mesh = ImportWithScaleOffset(FbxPath, DestPackagePath, OffsetUniformScale, OutError);
			if (!Mesh) { return nullptr; }
			return MotionTerminal::BuildScaleReportJson(*Mesh);
		};

	// The declared unit crosses the seam TWICE: rendered, for the human-facing reason lines, and as a
	// NUMBER, which is the operand ApplyScaleFix compares a measured factor against. Recovering the number
	// by parsing DescribeUnit()'s text back apart would make a safety decision depend on a display format.
	// 0 means "the file declares no unit" — never silently 1, because "undeclared" and "declared
	// centimetres" must not reach the same conclusion about who put a scale on the root bone.
	const double DeclaredUnitScaleFactor = Meta.bHasUnitScaleFactor ? Meta.UnitScaleFactor : 0.0;

	return MotionTerminal::ApplyScaleFix(RigName, Meta.DescribeUnit(), DeclaredUnitScaleFactor,
		Baseline.ToSharedRef(), ImportAndFingerprint);
}
