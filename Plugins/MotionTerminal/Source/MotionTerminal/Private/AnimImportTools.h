#pragma once

#include "ToolsetRegistry/ToolsetDefinition.h"
#include "AnimImportTools.generated.h"

/** Maya->FBX import-integrity surface (failure catalogue: research/fbx-scale-import-ue58.md), complete:
    the detect/assert half (per-rig import-scale fingerprinting for the roster gate) plus the fix half
    (FixImportScale + the pre-import FBX-header parse). */
UCLASS()
class UAnimImportTools : public UToolsetDefinition
{
	GENERATED_BODY()

public:
	/** Writes a scale-fingerprint baseline for a SkeletalMesh to
	    Plugins/MotionTerminal/Tests/ScaleBaselines/<RigName>.json — the measured report plus
	    "rig", "asset", "localOnly", "lengthTolerancePct": 1.0, "schemaVersion": 1. bLocalOnly marks a
	    baseline whose asset is gitignored (Fab/MetaHuman content), so a fresh clone SKIPs the row.
	    Regenerating an existing baseline is deliberate (docs/verification-design.md) — review the diff like code. */
	UFUNCTION(meta = (AICallable, AIAccessMode = "Write"))
	static FString SnapshotScaleBaseline(const FString& SkeletalMeshPath, const FString& RigName, bool bLocalOnly);

	/** Diagnostic (no baseline): the scale report as pretty JSON plus a human "suspectedCause" line. */
	UFUNCTION(meta = (AICallable, AIAccessMode = "Read"))
	static FString CheckImportScale(const FString& SkeletalMeshPath);

	/** Compares a SkeletalMesh against its stored baseline. Returns "OK" or the drift lines
	    (the Verify.ImportScale automation test calls the same ImportScaleCheck core). */
	UFUNCTION(meta = (AICallable, AIAccessMode = "Read"))
	static FString AssertImportScale(const FString& SkeletalMeshPath, const FString& RigName);

	/** Import-scale fix half. Imports FbxPath into DestPackagePath and leaves it scale-correct, or explains why it
	    refused. Exactly one of three replies:

	      "OK: applied <factor>"   the mesh is imported AND passes the per-rig gate; <factor> is the
	                              Interchange import_offset_uniform_scale that made it pass
	      "FLAGGED: <reason>"      a scale problem outside the deterministic lane — baked non-uniform joint
	                              scale or a rig-intentional root scale. NOT fixed and, when detected
	                              pre-import, NOT imported: those are Maya-side craft decisions, and
	                              silently "correcting" one rewrites artist-intended proportions while
	                              reporting success
	      "ERROR: <reason>"        bad arguments, unreadable FBX, failed import, or a correction that did
	                              not survive the gate. Never a partial success

	    Method — measure, correct, re-verify:
	      1. Parse the FBX header pre-import (detection #1) for the declared unit and every Model's
	         Lcl Scaling. Any non-identity node scale FLAGs here, before a single asset is written.
	      2. Import at offset 1.0. UE 5.8's Interchange already normalises FBX scene units to centimetres
	         itself (FBX-SDK backend, the only one that runs by default), so feeding the header's
	         UnitScaleFactor into import_offset_uniform_scale would DOUBLE-apply the conversion and
	         manufacture the very 100x corruption this tool exists to catch. (Verified in 5.8 engine
	         source; see the note in AnimImportTools.cpp. Earlier research assumed otherwise; see the
	         correction at the top of research/fbx-scale-import-ue58.md.)
	      3. Re-run the per-rig gate. A clean pass ends it.
	      4. If the gate reports its whole-rig uniform-factor signature — measured evidence, not a
	         metadata guess — reimport at the reciprocal and gate again. Anything else FLAGs.
	    The gate re-run is not optional: a fix that alters a mesh without re-verifying it is the silent
	    corruption the flag-don't-fix rule exists to prevent.

	    THE ROOT-SCALE SHAPE, and why the header's number is still read. 5.8 does not bake a unit
	    conversion into bone translations: it lands it on bone 0, so a mis-declared file arrives with every
	    bone length intact and a ref-pose scale on 'root' — which reads as detection #3 and would FLAG
	    forever, leaving step 4 unreachable for the exact failure it was built for. It is corrected only
	    when the MEASURED factor equals the file's DECLARED centimetres-per-unit, which is what
	    distinguishes the importer's own conversion from an artist's baked root scale. A file declaring
	    centimetres is never rescaled by the importer, so a root scale on one of those still FLAGs — as
	    does any measured factor that does not match the declaration. See
	    MotionTerminal::IsImporterInducedUnitConversion and research/interchange-unit-scale-root-bone.md. */
	UFUNCTION(meta = (AICallable, AIAccessMode = "Write"))
	static FString FixImportScale(const FString& FbxPath, const FString& DestPackagePath, const FString& RigName);
};
