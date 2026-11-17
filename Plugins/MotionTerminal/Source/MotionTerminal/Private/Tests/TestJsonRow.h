// Required-field JSON readers for this directory's test manifests.
//
// ============================================================================================
// WHY THIS HEADER EXISTS, AND WHY IT IS NOT A GENERIC JSON WRAPPER
// ============================================================================================
// Three manifest loaders in this directory grew the SAME defect independently, and were then
// repaired to three different depths at three different times:
//
//   ClipVariesTests.cpp    threshold defaulted to 0.0 with the read's bool discarded, so a row that
//                          omitted it asserted nothing and reported green on `0 < 0` being false.
//                          Since repaired.
//   IKEffectorTests.cpp    requires positive thresholds, but still `continue`s past a non-object row
//                          and past an empty `clip`.
//   PieTests.cpp           LoadPieRigs checks NOTHING: `if (!Rig.IsEmpty() && !Map.IsEmpty()) Add;`
//                          silently drops any row it could not read.
//
// Each loader pairs that with a "zero usable rows" sentinel, which fires only when EVERY row is
// unusable. So dropping ONE row deletes that row's assertions from the suite while the suite still
// reports green — a rig's entire PIE coverage can vanish and nothing goes red. That is this
// project's signature defect (a check that cannot observe what it claims) reached through a loader
// rather than through an assertion.
//
// THE POINT IS NOT DRY. Three copies of a five-line validation would be a cosmetic complaint. The
// point is that the defect is mechanically "the bool return of TryGetXField was discarded", and
// UE's own method cannot be annotated [[nodiscard]] — but a local wrapper can. Routing the loaders
// through these turns a defect that needs a careful reader to notice into one the compiler refuses.
//
// WHAT IS DELIBERATELY NOT HERE: any notion of which fields a manifest requires. That policy
// legitimately differs per manifest — army_config's `mb_ceiling` is JSON null by design, `localOnly`
// is optional everywhere, `forced_lod` is absent on the hero tier. A blanket "everything required"
// helper would be wrong AND would hide which fields are optional on purpose. Each loader still
// states its own requirements; these only make stating them enforceable. The one shared preamble
// is the read-and-parse of the file plus its top-level array (LoadManifestRoot / RequireArrayField);
// every field policy below that stays with each caller.
// ============================================================================================
#pragma once

#include "CoreMinimal.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

namespace MotionTerminalTestJson
{
	/** Plugins/MotionTerminal/Tests/<FileName>, where every manifest in this directory lives. */
	inline FString TestsDataPath(const TCHAR* FileName)
	{
		return FPaths::Combine(FPaths::ProjectPluginsDir(), TEXT("MotionTerminal"), TEXT("Tests"), FileName);
	}

	/** Reads and parses Tests/<FileName> into OutRoot. Unreadable and unparseable are both a load failure. */
	[[nodiscard]] inline bool LoadManifestRoot(const TCHAR* FileName, TSharedPtr<FJsonObject>& OutRoot,
	                                           FString& OutError)
	{
		const FString Path = TestsDataPath(FileName);
		FString Text;
		if (!FFileHelper::LoadFileToString(Text, *Path))
		{
			OutError = FString::Printf(TEXT("could not read %s at '%s'"), FileName, *Path);
			return false;
		}
		const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Text);
		if (!FJsonSerializer::Deserialize(Reader, OutRoot) || !OutRoot.IsValid())
		{
			OutError = FString::Printf(TEXT("%s is not valid JSON ('%s')"), FileName, *Path);
			return false;
		}
		return true;
	}

	/** The manifest's top-level row array. Absent is a load failure, never zero rows. */
	[[nodiscard]] inline bool RequireArrayField(const TSharedPtr<FJsonObject>& Root, const TCHAR* Key,
	                                            const TCHAR* Manifest, const TArray<TSharedPtr<FJsonValue>>*& Out,
	                                            FString& OutError)
	{
		if (!Root->TryGetArrayField(Key, Out) || !Out)
		{
			OutError = FString::Printf(TEXT("%s has no '%s' array"), Manifest, Key);
			return false;
		}
		return true;
	}

	/** The row element itself must be an object. Callers used to `continue` past this, silently. */
	[[nodiscard]] inline bool RequireObject(const TSharedPtr<FJsonValue>& Value, const TCHAR* Manifest,
	                                        int32 RowIndex, const TSharedPtr<FJsonObject>*& OutObj,
	                                        FString& OutError)
	{
		if (!Value.IsValid() || !Value->TryGetObject(OutObj) || !OutObj || !OutObj->IsValid())
		{
			OutError = FString::Printf(TEXT("%s: entry %d is not a JSON object"), Manifest, RowIndex);
			return false;
		}
		return true;
	}

	/** A non-empty string field. Absent and present-but-empty are the same failure to a loader. */
	[[nodiscard]] inline bool RequireString(const TSharedPtr<FJsonObject>& Obj, const TCHAR* Key,
	                                        const TCHAR* Manifest, int32 RowIndex, FString& Out,
	                                        FString& OutError)
	{
		if (!Obj->TryGetStringField(Key, Out) || Out.IsEmpty())
		{
			OutError = FString::Printf(TEXT("%s: entry %d needs a non-empty '%s'"), Manifest, RowIndex, Key);
			return false;
		}
		return true;
	}

	/**
	 * A finite, strictly positive number field.
	 *
	 * Strictly positive rather than non-negative on purpose: every consumer of these fields is a
	 * THRESHOLD, and a zero threshold is the shape that passes everything — `MaxDelta < 0.0` is false
	 * for a completely frozen clip. A row that cannot say what "varies" or "reaches" means for it must
	 * not be allowed to assert that anything did.
	 */
	[[nodiscard]] inline bool RequirePositive(const TSharedPtr<FJsonObject>& Obj, const TCHAR* Key,
	                                          const TCHAR* Manifest, int32 RowIndex, double& Out,
	                                          FString& OutError)
	{
		if (!Obj->TryGetNumberField(Key, Out) || !FMath::IsFinite(Out) || Out <= 0.0)
		{
			OutError = FString::Printf(
				TEXT("%s: entry %d needs a positive finite '%s' (a zero or absent threshold passes everything)"),
				Manifest, RowIndex, Key);
			return false;
		}
		return true;
	}
}
