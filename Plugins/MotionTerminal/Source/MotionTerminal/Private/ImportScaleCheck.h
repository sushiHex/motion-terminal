#pragma once

#include "CoreMinimal.h"
#include "Dom/JsonObject.h"

class USkeletalMesh;

namespace MotionTerminal
{
	/** Import-scale detect half (failure catalogue: research/fbx-scale-import-ue58.md). Measures a per-rig scale fingerprint of a USkeletalMesh as JSON:
	    - "boundsHeightUU"        : imported-bounds Z height in Unreal units (full extent, BoxExtent.Z*2)
	    - "boneCount"             : reference-skeleton bone count
	    - "boneLengths"           : { boneName -> ref-pose LOCAL translation length } (inserted in bone-index
	                                order so a regenerated baseline stays diff-stable)
	    - "refPoseScaleViolations": [ boneName ] for any ref-pose bone whose local scale is non-uniform or
	                                != 1.0 (tolerance 1e-4) — detection #3 (a rig-intentional root scale
	                                that silently corrupts retargeting)
	    - "importScale"           : the legacy FBX uniform-scale multiplier when GetAssetImportData() casts to
	                                UFbxSkeletalMeshImportData (a number); otherwise the import-data class name
	                                tagged "unaudited"; "none" when there is no import data (engine-template
	                                assets). The Interchange pipeline-settings audit that would make this
	                                readable for Interchange imports is NOT IMPLEMENTED — a real gap.
	                                Consequence: the stale-import-option check (root cause 1c)
	                                runs only on legacy-imported meshes; for Interchange imports the field is
	                                unmeasurable, and SummarizeScaleDrift reports that as a note rather than
	                                as drift (treating it as drift vetoed every corrective import). */
	TSharedRef<FJsonObject> BuildScaleReportJson(const USkeletalMesh& Mesh);

	/** The gate verdict, with the drift *classified* rather than only rendered. FixImportScale needs the
	    classification: only a whole-rig uniform factor is safe to auto-correct, and anything else must be
	    flagged for a human. */
	struct FScaleDriftSummary
	{
		/** Human-readable failure lines; empty == the mesh matches its baseline. */
		FString Diff;

		/** Observations that are NOT drift: fields the gate could not measure on this mesh. Rendered so a
		    reader knows a check went dark, but deliberately kept out of Diff — absence of evidence must
		    neither fail the gate nor veto a correction. */
		FString Notes;

		/** True when EVERY violating length ratio agreed on one common factor — a whole-rig unit
		    mismatch (root cause 1a/1b), the one deterministic, losslessly correctable case. */
		bool bUniformFactor = false;
		double UniformFactor = 1.0;      // current / baseline

		/** True when the drift carries evidence that is NOT a clean unit mismatch: ref-pose scale
		    violations (root cause 1e/1i), a structural bone-count/name change, importScale metadata drift, or
		    violating ratios that disagree with each other. Never auto-correct while this is set —
		    except through the one narrow, header-corroborated door IsImporterInducedUnitConversion opens. */
		bool bNonUniformEvidence = false;

		/** True when the drift's ONLY evidence is a scale sitting at or above the root joint: at least one
		    ref-pose scale violation, the bounds-height ratio as the sole violating ratio, every per-bone
		    ref-pose length inside tolerance, and no structural or importScale evidence at all.

		    That is the exact observable signature of an FBX unit conversion on UE 5.8 — Interchange writes
		    the composed GLOBAL transform into bone 0 and leaves every other bone's local transform alone,
		    so the factor cannot register in any bone length and shows up only in the bounds
		    (research/interchange-unit-scale-root-bone.md §2, InterchangeSkeletonHelper.cpp:319-342).

		    It is equally the signature of an artist's baked root scale, so on its own it is a SHAPE, never
		    a licence: bNonUniformEvidence stays set, and only ApplyScaleFix's declared-unit discriminator
		    can clear the way past it. */
		bool bRootScaleOnlyDrift = false;

		bool IsClean() const { return Diff.IsEmpty(); }
	};

	/** Compares a stored baseline report against a freshly-built current report and classifies the drift:
	    - bounds-height ratio outside the baseline's "lengthTolerancePct"
	    - any per-bone length ratio outside tolerance (per-bone supersedes a per-chain ratio: finer
	      granularity catches baked non-uniform scale on a single bone)
	    - any ref-pose scale violation present in the current report
	    - importScale drift between two READABLE values (numeric ratio, or one readable value replaced by
	      a different readable one). An unreadable current value is NOT drift — it lands in Notes.
	    When the violating ratios all agree on one common factor, a leading summary line NAMES it
	    (e.g. "uniform factor ~100.0 -- unit mismatch", tagged with its root-cause id) and bUniformFactor is set.

	    Callers must render BOTH channels. There is deliberately no Diff-only convenience wrapper: the
	    one that existed silently dropped Notes, and the single caller that used it lost the disclosure
	    this type exists to provide. */
	FScaleDriftSummary SummarizeScaleDrift(const TSharedRef<FJsonObject>& Baseline, const TSharedRef<FJsonObject>& Current);

	/** Imports at OffsetUniformScale and returns the resulting mesh's scale fingerprint. Null +
	    OutError when the import produced nothing gateable. Injected into ApplyScaleFix so the fix's
	    control flow is exercisable without an editor import (the real one lives in AnimImportTools.cpp). */
	using FImportAndFingerprintFn = TFunctionRef<TSharedPtr<FJsonObject>(double OffsetUniformScale, FString& OutError)>;

	/** The import-scale fix decision, everything after the pre-import header read: import at 1.0, gate, and — only
	    for a measured whole-rig uniform factor — reimport at the reciprocal and gate again. Returns the
	    caller-facing "OK: applied <factor>" / "FLAGGED: <reason>" / "ERROR: <reason>" string.

	    The import is a parameter rather than a call so that every branch here (the clean path, the
	    corrective reimport and the offset it is handed, the post-fix re-gate, and each failure mode) is
	    reachable from a headless test.

	    UnitText is the FBX's declared unit already RENDERED, for the human-facing reason lines.
	    DeclaredUnitScaleFactor is that same declaration as a NUMBER — centimetres per file unit, or 0
	    when the file declares none. Both, deliberately: the number is what
	    IsImporterInducedUnitConversion compares against, and recovering it by parsing DescribeUnit()'s
	    text back apart would make a safety decision depend on a display format. Two scalars still keep
	    this header independent of the FBX reader, which is why FFbxScaleMetadata itself does not cross. */
	FString ApplyScaleFix(const FString& RigName, const FString& UnitText, double DeclaredUnitScaleFactor,
		const TSharedRef<FJsonObject>& Baseline, FImportAndFingerprintFn ImportAndFingerprint);

	/** The ONE door past bNonUniformEvidence, and the whole of the importer-induced lane.

	    True only when ALL of these hold:
	      (a) the drift's only evidence is a scale at or above the root joint (bRootScaleOnlyDrift), with
	          a usable measured factor;
	      (b) the file declares a unit that is NOT centimetres. FbxConvert.cpp:116-122 rescales the scene
	          only when the declared unit differs from cm, so on a file declaring cm — or declaring
	          nothing, which callers pass as 0 — the importer contributed no scale whatsoever and every
	          root scale present is the author's;
	      (c) the MEASURED factor equals that declared conversion within 1%.

	    (b)+(c) are what make this sound where "the pre-import parse found no Lcl Scaling" is not. That
	    weaker test passes for a rig scaled after skinning without a re-bind (root cause 1i): the scale
	    lives in the FbxPose / skin-cluster bind matrices, which the pre-import parser never reads, and the
	    file still declares centimetres. Such a rig fails (b) outright, and fails (c) unless the artist's
	    factor happens to equal the declared unit conversion of a file that also mis-declares its unit.

	    Of the two, (c) is the one carrying the weight: at every shipped baseline's lengthTolerancePct of
	    1, the gate's violation predicate and (c)'s match window are exactly complementary, so a 1i scale
	    big enough to be flagged is necessarily too far from the declared conversion to match. (b) bites
	    only if a baseline tightens below 1%, and guards (c)'s relative tolerance at Declared <= 0. Do not
	    relax (c) on the assumption that (b) covers it.

	    This never widens the pre-import refusal: a file carrying authored 'Lcl Scaling' is flagged by
	    UAnimImportTools::FixImportScale before any import and never reaches ApplyScaleFix at all. */
	bool IsImporterInducedUnitConversion(const FScaleDriftSummary& Drift, double DeclaredUnitScaleFactor);

	/** The Interchange import_offset_uniform_scale that cancels a measured whole-rig drift — its
	    reciprocal. Returns 1.0 for a degenerate factor rather than dividing by ~zero; ApplyScaleFix
	    rejects such a factor before ever getting here (see IsUsableUniformFactor). */
	double UnitCorrectionForDrift(double MeasuredUniformFactor);

	/** A measured factor is usable as a correction only if it is finite and not ~zero. Guards the
	    reciprocal at the decision site instead of relying on SummarizeScaleDrift's classification to
	    have already excluded it. */
	bool IsUsableUniformFactor(double MeasuredUniformFactor);

	/** A scale factor as it appears in tool output: "100", "0.01", "1.0002" — 4 significant digits, no
	    trailing zeros. Same formatting the drift lines use, so a reported factor reads identically
	    whether it came from the gate or from the fix. */
	FString FormatScaleFactor(double Factor);

	/** Deterministic pretty-printed JSON for a scale report (used by CheckImportScale + the snapshot writer). */
	FString ScaleReportToString(const TSharedRef<FJsonObject>& Report);

	/** <project>/Plugins/MotionTerminal/Tests/ScaleBaselines */
	FString ScaleBaselineDir();
	/** ScaleBaselineDir()/<RigName>.json (".json" appended if absent). */
	FString ScaleBaselinePath(const FString& RigName);

	/** Guards a RigName before ScaleBaselinePath turns it into a baseline filename. Empty return == safe;
	    otherwise a reason. Rejects empty, path separators ('/' '\\'), and ".." — SnapshotScaleBaseline is an
	    MCP write primitive and FPaths::Combine does NOT resolve "..", so a name like "../evil" would escape
	    the ScaleBaselines dir. Callers wrap the reason in the module's "ERROR: " reply idiom. */
	FString ValidateRigName(const FString& RigName);
}
