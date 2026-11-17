#pragma once

#include "CoreMinimal.h"

namespace MotionTerminal
{
	/** An FBX Model node whose declared local scale is not identity. */
	struct FFbxScaledNode
	{
		FString Name;                        // the Model's display name ("<unnamed>" if the exporter wrote none)
		FString Type;                        // "LimbNode" / "Mesh" / "Null" / ... (property 2 of the Model record)
		FVector Scale = FVector::OneVector;  // the node's "Lcl Scaling" property
		bool bNonUniform = false;            // X/Y/Z disagree — baked non-uniform joint scale (root cause 1e)
	};

	/** Pre-import read of an FBX file's scale metadata — detection #1 in research/fbx-scale-import-ue58.md,
	    the *authoritative* pre-import signal (the exporter's own numbers, not geometry inferred after
	    the fact).

	    Reads both FBX flavours: the binary "Kaydara FBX Binary" container (what Maya and UE write by
	    default) and the ASCII text form. Only the two fields the scale check needs are extracted — the GlobalSettings
	    unit declaration and every Model's "Lcl Scaling" — so the walk stays cheap on a large rig file and
	    never materialises geometry payloads. */
	struct FFbxScaleMetadata
	{
		bool bBinary = false;
		int32 FormatVersion = 0;              // e.g. 7400 (binary only; 0 for ASCII)

		/** Centimetres per file unit, as declared by the exporter: 1 = cm, 100 = m, 2.54 = in.
		    Absent in some hand-rolled exports — hence the b* flag; a missing declaration is NOT
		    silently treated as 1.0 by callers that need a deterministic answer. */
		bool bHasUnitScaleFactor = false;
		double UnitScaleFactor = 1.0;

		int32 ModelCount = 0;                 // Models seen (a zero count means the walk found no scene)
		TArray<FFbxScaledNode> ScaledNodes;   // every Model whose Lcl Scaling != (1,1,1)

		/** ---- HOW MUCH OF THE FILE WENT UNREAD (binary flavour only) --------------------------

		    `ReadBinaryProperty` gives up on a property typecode it does not handle, and the record
		    loop then breaks. The structural walk RESYNCS from the record's own declared
		    `PropertyListLen`, so the file keeps parsing and `ParseFbxScaleMetadata` still returns
		    true — which is correct, and which is exactly why the abort was invisible. An empty
		    `ScaledNodes` was reported identically whether the walk read every property and found
		    none, or aborted before reaching the one that mattered.

		    That is not an academic gap: a `P` record's first property is the property NAME, so an
		    abort at property 1 leaves `Props.Num() < 2`, the "P" branch is skipped entirely, and a
		    real `Lcl Scaling` goes UNRECORDED. The file is then imported rather than flagged.

		    These two counters make that observable. `AbortedPropertyWalks` is every record whose
		    declared property list was not walked to the end (unhandled typecode, a short/corrupt
		    read, or a property count above the 64 sanity cap, which skips the list wholesale).
		    `AbortedPropertyRecordWalks` is the subset on a `P` record — the ONLY records that carry
		    `Lcl Scaling` / `UnitScaleFactor`, hence the only ones whose loss can change a verdict.
		    A caller that must not import an unvetted rig checks the second one; see
		    `UAnimImportTools::FixImportScale`, which refuses rather than importing.

		    Both stay 0 for the ASCII flavour: that reader is line/token based and has no partial
		    record to abandon — an unparseable token stops that one value list and nothing else. */
		int32 AbortedPropertyWalks = 0;
		int32 AbortedPropertyRecordWalks = 0;

		/** ---- THE STRUCTURAL HALF --------------------------------------------------------------
		    The two counters above cover an aborted PROPERTY walk, which is survivable: the reader
		    resyncs from the record's own declared PropertyListLen and the rest of the file is still
		    visited. A STRUCTURAL abort is not survivable. `ReadBinaryNode` clears `bOk` on a corrupt
		    record end-offset, a failed record header or name read, and a resync that lands out of
		    bounds; after any of those the reader has no idea where the next record begins, so every
		    record from that point on — including every Model that might carry an `Lcl Scaling` — is
		    never visited.

		    Without this counter none of those paths bumped anything: `ParseBinary` returned TRUE as long as it had seen one
		    Model or a UnitScaleFactor before the break, so a binary FBX truncated partway through
		    `Objects` was observationally identical to a clean one: `ScaledNodes` empty, both counters
		    0, `FixImportScale`'s `ScaledNodes.Num() > 0` and `AbortedPropertyRecordWalks > 0` guards
		    both satisfied, and an unvetted rig imported.

		    This is 0 or 1 by construction: the failure propagates straight up (every level returns
		    false and every loop tests `bOk`), so exactly one site fires. It is kept as a count rather
		    than a bool so the two abort families read the same way at a call site. `ParseBinary`
		    refuses on a non-zero value, which is what makes the "a true return means the walk
		    completed" contract below true rather than aspirational. */
		int32 AbortedStructuralWalks = 0;

		/** "1 (cm)" / "100 (m)" / "2.54 (in)" / "undeclared" — for the human-facing reason lines. */
		FString DescribeUnit() const;
	};

	/** Fills Out from the FBX at FbxPath. False + OutError on a missing/unreadable/unrecognised file,
	    and on a binary file whose record walk aborted structurally
	    (`AbortedStructuralWalks`), which is the case that used to return true while leaving most of
	    the file unread.

	    WHAT A TRUE RETURN MEANS, EXACTLY: every record the walker reached was visited and the record
	    grid was never lost. It does NOT mean the file is scale-clean. It does NOT mean every property
	    was parsed — an aborted PROPERTY walk resyncs and still returns true, which is what
	    `AbortedPropertyRecordWalks` is for. The one hole in "reached" was the 32-deep recursion
	    backstop, which once skipped a whole subtree silently and still returned true; it now
	    aborts through `FailStructural` when it would actually leave bytes
	    unread, so the sentence above is true rather than nearly true. The `EndOffset == 0` record is
	    the format's own end-of-list terminator, not an abort, and is correctly uncounted. */
	bool ParseFbxScaleMetadata(const FString& FbxPath, FFbxScaleMetadata& Out, FString& OutError);
}
