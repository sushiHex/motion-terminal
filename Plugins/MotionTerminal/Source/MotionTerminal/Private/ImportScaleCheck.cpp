#include "ImportScaleCheck.h"

#include "AnimGraphTopology.h"
#include "Engine/SkeletalMesh.h"
#include "ReferenceSkeleton.h"
#include "Factories/FbxSkeletalMeshImportData.h"   // UnrealEd: legacy import-scale surface (ImportUniformScale)

#include "Misc/Paths.h"

namespace
{
	// A ref-pose bone counts as a "scale violation" if its local scale is non-uniform, or uniform but
	// != 1.0 — either flags a rig-intentional scale that silently corrupts a mannequin-based retarget.
	constexpr double kScaleTol = 1e-4;

	bool IsScaleViolation(const FVector& Scale)
	{
		const double MaxComp = FMath::Max3(Scale.X, Scale.Y, Scale.Z);
		const double MinComp = FMath::Min3(Scale.X, Scale.Y, Scale.Z);
		if ((MaxComp - MinComp) > kScaleTol) { return true; }              // non-uniform
		return FMath::Abs(MaxComp - 1.0) > kScaleTol;                       // uniform but scaled
	}

	// A string importScale means BuildScaleReportJson could not read a number out of the mesh's import
	// data: "none" (no import data at all) or "<Class> (unaudited)" (import data it cannot introspect —
	// which is EVERY Interchange import, since only the legacy UFbxSkeletalMeshImportData exposes a flat
	// scale). Such a value reports nothing about whether the import options drifted.
	bool IsUnmeasurableImportScale(const FString& Value)
	{
		return Value == TEXT("none") || Value.EndsWith(TEXT("(unaudited)"));
	}
}

namespace MotionTerminal
{
	TSharedRef<FJsonObject> BuildScaleReportJson(const USkeletalMesh& Mesh)
	{
		TSharedRef<FJsonObject> Report = MakeShared<FJsonObject>();

		// (1) Bounds height. Imported bounds are the authored, pre-render CPU bounds (headless-safe);
		// fall back to runtime bounds only if the imported extent is degenerate. Full Z height = 2*extent.
		FBoxSphereBounds Bounds = Mesh.GetImportedBounds();
		if (Bounds.BoxExtent.Z <= UE_KINDA_SMALL_NUMBER)
		{
			Bounds = Mesh.GetBounds();
		}
		Report->SetNumberField(TEXT("boundsHeightUU"), Bounds.BoxExtent.Z * 2.0);

		// (2)/(3) Ref-pose bone lengths + scale violations, walked in bone-index order (deterministic).
		const FReferenceSkeleton& RefSkel = Mesh.GetRefSkeleton();
		const TArray<FTransform>& RefPose = RefSkel.GetRefBonePose();
		const int32 NumBones = RefSkel.GetNum();
		Report->SetNumberField(TEXT("boneCount"), NumBones);

		TSharedRef<FJsonObject> BoneLengths = MakeShared<FJsonObject>();
		TArray<TSharedPtr<FJsonValue>> Violations;
		for (int32 i = 0; i < NumBones && i < RefPose.Num(); ++i)
		{
			const FName BoneName = RefSkel.GetBoneName(i);
			const FTransform& T = RefPose[i];
			BoneLengths->SetNumberField(BoneName.ToString(), T.GetTranslation().Size());
			if (IsScaleViolation(T.GetScale3D()))
			{
				Violations.Add(MakeShared<FJsonValueString>(BoneName.ToString()));
			}
		}
		Report->SetObjectField(TEXT("boneLengths"), BoneLengths);
		Report->SetArrayField(TEXT("refPoseScaleViolations"), Violations);

		// (4) Import scale. Legacy FBX path exposes a flat ImportUniformScale float; the Interchange path
		// stores UInterchangeAssetImportData, whose scale lives in pipeline settings. Reading those
		// settings back is NOT IMPLEMENTED, and that is not free: it means the stale-import-option
		// check (root cause 1c) is live ONLY on the legacy path. Every Interchange-imported mesh reports
		// "unaudited" here, and SummarizeScaleDrift has to treat that as an unmeasured field rather than
		// as drift — see its importScale section, which learned this the hard way. Building the audit
		// would restore 1c for Interchange imports; until then that detection is simply absent for them.
		const UAssetImportData* ImportData = Mesh.GetAssetImportData();
		if (!ImportData)
		{
			Report->SetStringField(TEXT("importScale"), TEXT("none"));
		}
		else if (const UFbxSkeletalMeshImportData* FbxData = Cast<UFbxSkeletalMeshImportData>(ImportData))
		{
			Report->SetNumberField(TEXT("importScale"), FbxData->ImportUniformScale);
		}
		else
		{
			Report->SetStringField(TEXT("importScale"),
				FString::Printf(TEXT("%s (unaudited)"), *ImportData->GetClass()->GetName()));
		}

		return Report;
	}

	FScaleDriftSummary SummarizeScaleDrift(const TSharedRef<FJsonObject>& Baseline, const TSharedRef<FJsonObject>& Current)
	{
		FScaleDriftSummary Summary;
		double TolPct = 1.0;
		Baseline->TryGetNumberField(TEXT("lengthTolerancePct"), TolPct);
		auto WithinTol = [TolPct](double Ratio) { return FMath::Abs(Ratio - 1.0) * 100.0 <= TolPct; };

		TArray<FString> Fails;
		TArray<FString> Notes;            // observations that are not drift (see Summary.Notes)
		TArray<double> ViolatingRatios;   // for the common-factor summary

		// Evidence bookkeeping for the root-scale-only shape (FScaleDriftSummary::bRootScaleOnlyDrift).
		// bNonUniformEvidence cannot answer "was a ref-pose scale the ONLY evidence?", because every
		// source ORs into that one bool and none of them can be told apart afterwards. These record the
		// sources separately, and nothing else reads them.
		bool bEvidenceOtherThanRefPose = false;
		bool bHaveRefPoseViolation = false;
		bool bBoundsRatioViolated = false;
		double BoundsRatio = 1.0;

		// --- bounds height ratio ---
		double BaseH = 0.0, CurH = 0.0;
		const bool bHaveBase = Baseline->TryGetNumberField(TEXT("boundsHeightUU"), BaseH);
		const bool bHaveCur  = Current->TryGetNumberField(TEXT("boundsHeightUU"), CurH);
		if (bHaveBase && bHaveCur && BaseH > UE_KINDA_SMALL_NUMBER)
		{
			const double Ratio = CurH / BaseH;
			if (!WithinTol(Ratio))
			{
				Fails.Add(FString::Printf(TEXT("boundsHeightUU %.2f vs baseline %.2f (ratio %s, tol %.1f%%)"),
					CurH, BaseH, *FormatScaleFactor(Ratio), TolPct));
				ViolatingRatios.Add(Ratio);
				bBoundsRatioViolated = true;
				BoundsRatio = Ratio;
			}
		}

		// --- bone count (structural) ---
		// The per-bone loop below iterates BASELINE keys only, so a current mesh that GAINED bones would
		// pass silently (every baseline bone still matches). An exact-count check catches that direction —
		// a mismatch beyond exact equality is a structural change, not a scale drift.
		double BaseBoneCount = 0.0, CurBoneCount = 0.0;
		const bool bHaveBaseBC = Baseline->TryGetNumberField(TEXT("boneCount"), BaseBoneCount);
		const bool bHaveCurBC  = Current->TryGetNumberField(TEXT("boneCount"), CurBoneCount);
		if (bHaveBaseBC && bHaveCurBC && BaseBoneCount != CurBoneCount)
		{
			Fails.Add(FString::Printf(TEXT("boneCount %.0f vs baseline %.0f (mismatch)"), CurBoneCount, BaseBoneCount));
			Summary.bNonUniformEvidence = true;   // structural, not a scale factor
			bEvidenceOtherThanRefPose = true;
		}

		// --- per-bone length ratios ---
		const TSharedPtr<FJsonObject>* BaseBonesPtr = nullptr;
		const TSharedPtr<FJsonObject>* CurBonesPtr  = nullptr;
		if (Baseline->TryGetObjectField(TEXT("boneLengths"), BaseBonesPtr) &&
			Current->TryGetObjectField(TEXT("boneLengths"), CurBonesPtr))
		{
			// Deterministic per-bone report ordering: sort baseline bone names before comparing.
			// (FJsonObject::Values is keyed by an internal shared-string type in 5.8, so we collect
			// FString keys via the temp-pair range-for idiom rather than GetKeys<FString>.)
			TArray<FString> BoneNames;
			for (const TPair<FString, TSharedPtr<FJsonValue>>& P : (*BaseBonesPtr)->Values)
			{
				BoneNames.Add(P.Key);
			}
			BoneNames.Sort();
			for (const FString& Bone : BoneNames)
			{
				const double BaseLen = (*BaseBonesPtr)->GetNumberField(Bone);
				double CurLen = 0.0;
				if (!(*CurBonesPtr)->TryGetNumberField(Bone, CurLen))
				{
					Fails.Add(FString::Printf(TEXT("bone '%s' missing in current mesh (baseline length %.3f)"), *Bone, BaseLen));
					Summary.bNonUniformEvidence = true;   // structural, not a scale factor
					bEvidenceOtherThanRefPose = true;
					continue;
				}
				if (BaseLen <= UE_KINDA_SMALL_NUMBER)
				{
					// A zero-length baseline bone (e.g. root) carries no scale signal — only flag if the
					// current value became non-zero, which is itself a structural change.
					if (CurLen > kScaleTol)
					{
						Fails.Add(FString::Printf(TEXT("bone '%s' length %.3f vs baseline ~0"), *Bone, CurLen));
						Summary.bNonUniformEvidence = true;
						bEvidenceOtherThanRefPose = true;
					}
					continue;
				}
				const double Ratio = CurLen / BaseLen;
				if (!WithinTol(Ratio))
				{
					Fails.Add(FString::Printf(TEXT("bone '%s' length %.3f vs baseline %.3f (ratio %s)"),
						*Bone, CurLen, BaseLen, *FormatScaleFactor(Ratio)));
					ViolatingRatios.Add(Ratio);
				}
			}
		}

		// --- ref-pose scale violations (current) ---
		const TArray<TSharedPtr<FJsonValue>>* CurViol = nullptr;
		if (Current->TryGetArrayField(TEXT("refPoseScaleViolations"), CurViol) && CurViol && CurViol->Num() > 0)
		{
			TArray<FString> Names;
			for (const TSharedPtr<FJsonValue>& V : *CurViol) { if (V.IsValid()) { Names.Add(V->AsString()); } }
			Fails.Add(FString::Printf(TEXT("ref-pose scale violation(s) on: %s (scale detection #3)"),
				*FString::Join(Names, TEXT(", "))));
			// Baked non-uniform / rig-intentional scale — the never-silently-fix case.
			Summary.bNonUniformEvidence = true;
			bHaveRefPoseViolation = true;
		}

		// --- importScale drift (root cause 1c, stale import options) ---
		// This field is only MEASURABLE on the legacy FBX path. BuildScaleReportJson emits a number when
		// the import data casts to UFbxSkeletalMeshImportData and a "<Class> (unaudited)" string
		// otherwise — and UInterchangeAssetImportData is a sibling of that class, not a subclass, so
		// every Interchange import (i.e. everything FixImportScale performs on 5.8) lands in the string
		// branch. Treating "unreadable" as "changed" made a numeric baseline veto EVERY corrected
		// import, including a perfectly clean one: absence of evidence is not evidence of change. It is
		// recorded as a NOTE instead — visible, so a reader knows the check went dark — while genuine
		// drift between two READABLE values still fails exactly as before.
		{
			const bool bBaseNum = Baseline->HasTypedField<EJson::Number>(TEXT("importScale"));
			const bool bCurNum  = Current->HasTypedField<EJson::Number>(TEXT("importScale"));

			FString BaseStr, CurStr;
			const bool bHaveBaseScale = bBaseNum || Baseline->TryGetStringField(TEXT("importScale"), BaseStr);
			const bool bHaveCurScale  = bCurNum  || Current->TryGetStringField(TEXT("importScale"), CurStr);

			if (bHaveBaseScale && bHaveCurScale)
			{
				const bool bBaseReadable = bBaseNum || !IsUnmeasurableImportScale(BaseStr);
				const bool bCurReadable  = bCurNum  || !IsUnmeasurableImportScale(CurStr);

				const FString BaseText = bBaseNum
					? FString::Printf(TEXT("%.4f"), Baseline->GetNumberField(TEXT("importScale"))) : BaseStr;
				const FString CurText = bCurNum
					? FString::Printf(TEXT("%.4f"), Current->GetNumberField(TEXT("importScale"))) : CurStr;

				if (!bBaseReadable || !bCurReadable)
				{
					Notes.Add(FString::Printf(
						TEXT("importScale not comparable: current '%s' vs baseline '%s' — the stale-import-option ")
						TEXT("check (root cause 1c) did not run on this mesh"), *CurText, *BaseText));
				}
				else if (bBaseNum && bCurNum)
				{
					const double B = Baseline->GetNumberField(TEXT("importScale"));
					const double C = Current->GetNumberField(TEXT("importScale"));
					if (FMath::Abs(B - C) > 1e-3)
					{
						Fails.Add(FString::Printf(TEXT("importScale drift: %.4f vs baseline %.4f"), C, B));
						Summary.bNonUniformEvidence = true;   // stale options, not a file-unit problem
						bEvidenceOtherThanRefPose = true;
					}
				}
				else if (bBaseNum != bCurNum || !BaseText.Equals(CurText))
				{
					Fails.Add(FString::Printf(TEXT("importScale changed: '%s' vs baseline '%s'"), *CurText, *BaseText));
					Summary.bNonUniformEvidence = true;
					bEvidenceOtherThanRefPose = true;
				}
			}
		}

		// Notes are set regardless of the verdict — a check that went dark must be reported on a PASS too.
		Summary.Notes = FString::Join(Notes, TEXT("\n"));

		if (Fails.Num() == 0) { return Summary; }

		// Common-factor summary: when every violating ratio clusters around a single factor, NAME it —
		// a whole-rig unit mismatch (root cause 1a/1b), the signature the per-bone lines would otherwise bury.
		// This is also the ONLY classification FixImportScale is allowed to auto-correct, so a lone
		// violating ratio does not qualify: one data point is a measurement, not a whole-rig factor.
		if (ViolatingRatios.Num() >= 2)
		{
			double Sum = 0.0;
			for (double R : ViolatingRatios) { Sum += R; }
			const double Mean = Sum / ViolatingRatios.Num();
			bool bAllAgree = Mean > UE_KINDA_SMALL_NUMBER;
			// ---- THE SCATTER BAND. DECIDED, NOT OVERLOOKED ----------------------------------------
			//
			// This 0.15 agreement window and the baselines' lengthTolerancePct of 1 % differ by 15x, so
			// there is a band — spread between ~1 % and ~15 % — where ApplyScaleFix confidently declares
			// one uniform factor, corrects by its mean, and then fails its own re-gate. Pinned on both
			// sides by ImportScaleTests' [scatter] cases.
			//
			// LEFT OPEN, DELIBERATELY, for two reasons:
			//  1. The tool is HONEST inside the band. It returns ERROR, not a quiet OK over a mesh that
			//     missed tolerance. The failure mode is a wasted correction attempt and a red, which is
			//     the direction a safety tool is supposed to fail in.
			//  2. The principled fix is to drive this window from the baseline's own lengthTolerancePct,
			//     and that number is SHARED roster-gate semantics: `TolPct` above is the violation
			//     predicate every Verify.ImportScale row is gated on, and it is written into all four
			//     committed baseline JSONs. Tying the classifier to it would change which drifts are
			//     called "one uniform factor" — i.e. bUniformFactor / bNonUniformEvidence, and therefore
			//     the reported classification of the shipped roster rows — as a side effect of closing a
			//     band nothing has ever landed in. Changing shared gate semantics to fix a hazard the
			//     tool already refuses safely is the wrong trade.
			//
			// What would change this: a real FBX round trip landing in the band (the measured one did not
			// — it scattered well under 1 %), or the classifier being needed OUTSIDE the auto-correct lane.
			for (double R : ViolatingRatios)
			{
				if (FMath::Abs(R - Mean) > 0.15 * FMath::Abs(Mean)) { bAllAgree = false; break; }
			}
			if (bAllAgree)
			{
				Summary.bUniformFactor = true;
				Summary.UniformFactor = Mean;
				Fails.Insert(FString::Printf(
					TEXT("uniform factor ~%s -- unit mismatch (root cause 1a/1b)"), *FormatScaleFactor(Mean)), 0);
			}
			else
			{
				Summary.bNonUniformEvidence = true;   // ratios disagree: not one factor, so not auto-fixable
				bEvidenceOtherThanRefPose = true;
			}
		}
		else if (ViolatingRatios.Num() == 1)
		{
			// One violating ratio is normally a measurement, not a whole-rig factor — with ONE exception,
			// where it is the complete evidence rather than a lone sample.
			//
			// A scale sitting at or above the root joint is structurally invisible to every per-bone
			// ref-pose length: Interchange substitutes the composed GLOBAL transform for bone 0 alone and
			// leaves every other bone's LOCAL transform untouched (InterchangeSkeletonHelper.cpp:319-342,
			// research/interchange-unit-scale-root-bone.md §2). Bounds height is the only field that can
			// register it. Demanding a second agreeing ratio in that shape demands evidence the mesh
			// cannot physically produce — which is what left the auto-fix lane unreachable for the exact
			// failure it was built for (measured on a real mis-declared FBX import).
			//
			// Recognising the factor is NOT permission to cancel it: an artist's baked root scale is
			// indistinguishable here, so bNonUniformEvidence stays set by the ref-pose block above and
			// only ApplyScaleFix's declared-unit discriminator can get past it.
			// NAMING CAVEAT: this does not verify the violating bone IS bone 0, nor that there is exactly
			// one — a mid-chain baked scale that moves no bone length fits the same shape and sets this
			// flag. That is survivable only because the flag is a shape and never a licence: such a file
			// fails (c) in IsImporterInducedUnitConversion, and were it ever to pass, ApplyScaleFix's
			// post-correction re-gate returns ERROR rather than rewriting it. Tighten this to bone 0 if
			// the flag ever gains a second reader that treats it as permission on its own.
			if (bBoundsRatioViolated && bHaveRefPoseViolation && !bEvidenceOtherThanRefPose
				&& IsUsableUniformFactor(BoundsRatio))
			{
				Summary.bRootScaleOnlyDrift = true;
				Summary.bUniformFactor = true;
				Summary.UniformFactor = BoundsRatio;
				Fails.Insert(FString::Printf(
					TEXT("whole-rig factor ~%s at or above the root joint -- either a unit mismatch (root cause 1a/1b) ")
					TEXT("or a baked root scale (root cause 1e/1i); ref-pose bone lengths cannot tell those apart"),
					*FormatScaleFactor(BoundsRatio)), 0);
			}
			else
			{
				Summary.bNonUniformEvidence = true;
			}
		}

		Summary.Diff = FString::Join(Fails, TEXT("\n"));
		return Summary;
	}


	bool IsUsableUniformFactor(double MeasuredUniformFactor)
	{
		// Positive, not merely non-zero: the factor is a ratio of measured lengths, so a negative one is
		// impossible upstream and inverting it would silently mirror the rig. Matches the > 0 test
		// SummarizeScaleDrift applies before it will call a factor uniform at all.
		return FMath::IsFinite(MeasuredUniformFactor) && MeasuredUniformFactor > UE_KINDA_SMALL_NUMBER;
	}

	double UnitCorrectionForDrift(double MeasuredUniformFactor)
	{
		if (!IsUsableUniformFactor(MeasuredUniformFactor)) { return 1.0; }
		return 1.0 / MeasuredUniformFactor;
	}

	bool IsImporterInducedUnitConversion(const FScaleDriftSummary& Drift, double DeclaredUnitScaleFactor)
	{
		// How close a MEASURED bounds ratio must sit to an EXACT declared centimetres-per-unit before the
		// two count as the same number. Relative, and set to the same 1% the per-rig baselines use as
		// lengthTolerancePct — a factor further out than the gate's own pass band is not "the declared
		// conversion", whatever else it may be.
		constexpr double kDeclaredUnitMatchRelTol = 0.01;

		// (a) Shape. Anything past a scale at or above the root joint — a bone that changed length, a
		// structural change, importScale drift, ratios that disagree — is out of scope no matter what the
		// header declares. bRootScaleOnlyDrift is set only when a ref-pose violation was the sole evidence.
		if (!Drift.bRootScaleOnlyDrift || !Drift.bUniformFactor) { return false; }
		if (!IsUsableUniformFactor(Drift.UniformFactor)) { return false; }

		// (b) The importer must have had something to convert at all. FbxConvert.cpp:116-122 rescales the
		// scene ONLY when the file's declared unit differs from centimetres, so on a file declaring cm —
		// or declaring nothing, which callers pass as 0 — the importer contributed exactly zero scale and
		// every root scale present is the author's.
		//
		// (b) is defence-in-depth, NOT the condition that keeps out root cause 1i (a rig scaled after
		// skinning without a re-bind, whose scale lives in the FbxPose / skin-cluster bind matrices and is
		// invisible to the pre-import 'Lcl Scaling' parse). At every shipped baseline's lengthTolerancePct
		// of 1, (c) below is what excludes 1i: the gate's violation predicate and (c)'s match window are
		// exactly complementary, so a 1i scale large enough to be flagged is necessarily too far from the
		// declared conversion to match. (b) earns its keep only if a baseline tightens below 1%, and by
		// guarding (c)'s relative tolerance against degenerating at Declared <= 0.
		// DO NOT relax (c) on the assumption that (b) is holding the line — it is not.
		if (!FMath::IsFinite(DeclaredUnitScaleFactor) || DeclaredUnitScaleFactor <= UE_KINDA_SMALL_NUMBER)
		{
			return false;
		}
		if (FMath::Abs(DeclaredUnitScaleFactor - 1.0) <= kDeclaredUnitMatchRelTol) { return false; }

		// (c) And the measured factor must BE that declared conversion, not merely coexist with it. A rig
		// an artist scaled by 3 inside a file declaring metres measures 3, not 100, and stays flagged.
		return FMath::Abs(Drift.UniformFactor - DeclaredUnitScaleFactor)
			<= kDeclaredUnitMatchRelTol * DeclaredUnitScaleFactor;
	}

	FString ApplyScaleFix(const FString& RigName, const FString& UnitText, double DeclaredUnitScaleFactor,
		const TSharedRef<FJsonObject>& Baseline, FImportAndFingerprintFn ImportAndFingerprint)
	{
		// (1) Import with no correction, then let the per-rig gate measure what actually landed.
		FString Error;
		TSharedPtr<FJsonObject> First = ImportAndFingerprint(1.0, Error);
		if (!First.IsValid()) { return FString::Printf(TEXT("ERROR: %s"), *Error); }

		// A note is not a failure, but it must reach the caller: "gate green" is only honest alongside
		// the list of checks that could not run.
		auto WithNotes = [](const FString& Reply, const FString& Notes)
		{
			return Notes.IsEmpty() ? Reply : FString::Printf(TEXT("%s\n%s"), *Reply, *Notes);
		};

		const FScaleDriftSummary Drift = SummarizeScaleDrift(Baseline, First.ToSharedRef());
		if (Drift.IsClean())
		{
			return WithNotes(FString::Printf(
				TEXT("OK: applied 1 (no correction needed; declared unit %s; '%s' gate green)"),
				*UnitText, *RigName), Drift.Notes);
		}

		// The one bypass past bNonUniformEvidence, and it is deliberately narrow. UE 5.8 does not bake an
		// FBX unit conversion into bone translations or vertices: it deposits the whole factor at or above
		// the root joint, where the gate reads it back as a ref-pose scale violation — the SAME shape an
		// artist's baked root scale produces. The two are separated by the file's own declaration: an
		// importer-induced factor equals the declared centimetres-per-unit, an author-induced one does not
		// (IsImporterInducedUnitConversion, and research/interchange-unit-scale-root-bone.md §2/§4).
		//
		// Nothing else is relaxed. A file carrying authored 'Lcl Scaling' never reaches this function —
		// UAnimImportTools::FixImportScale refuses it before importing — and any drift that also moved a
		// bone length, the bone count, or the import metadata still FLAGs below.
		const bool bImporterUnitConversion = IsImporterInducedUnitConversion(Drift, DeclaredUnitScaleFactor);

		if (!Drift.bUniformFactor || (Drift.bNonUniformEvidence && !bImporterUnitConversion))
		{
			return FString::Printf(
				TEXT("FLAGGED: the imported mesh fails the '%s' gate, but the drift is not a clean whole-rig unit ")
				TEXT("mismatch — an import option cannot correct it without rewriting the rig. Left as imported:\n%s"),
				*RigName, *Drift.Diff);
		}

		// A factor that cannot be inverted is not correctable. SummarizeScaleDrift already refuses to set
		// bUniformFactor for a ~zero mean, so this is belt-and-braces — but the reciprocal is taken HERE,
		// so the guard belongs here too rather than resting on a distant invariant.
		if (!IsUsableUniformFactor(Drift.UniformFactor))
		{
			return FString::Printf(
				TEXT("FLAGGED: the '%s' gate reports a degenerate uniform factor (%s) — not invertible, so no ")
				TEXT("import offset can cancel it. Left as imported:\n%s"),
				*RigName, *FormatScaleFactor(Drift.UniformFactor), *Drift.Diff);
		}

		// (2) One measured factor across the whole rig: deterministic and lossless to cancel.
		const double Correction = UnitCorrectionForDrift(Drift.UniformFactor);
		const FString MeasuredText = FormatScaleFactor(Drift.UniformFactor);
		const FString CorrectionText = FormatScaleFactor(Correction);

		TSharedPtr<FJsonObject> Second = ImportAndFingerprint(Correction, Error);
		if (!Second.IsValid()) { return FString::Printf(TEXT("ERROR: %s"), *Error); }

		const FScaleDriftSummary After = SummarizeScaleDrift(Baseline, Second.ToSharedRef());

		// This re-gate is load-bearing, not a formality: it is what contains the residual hole in
		// IsImporterInducedUnitConversion. A file that BOTH mis-declares its unit AND carries an author-baked
		// root scale equal to that same declared factor satisfies (b) and (c) and reaches here — but the
		// correction only cancels one of the two, so the second import still misses the gate and returns
		// ERROR below. A silent rewrite would require the file's geometry to have already been centimetres,
		// which is the honest case. Do not turn this into a warning.
		if (!After.IsClean())
		{
			// The corrected reimport overwrote the destination (bReplaceExisting), so a wrongly-scaled
			// asset is sitting there unsaved. Say so — otherwise a later "Save All" persists it silently.
			return FString::Printf(
				TEXT("ERROR: applied %s to cancel a measured %sx drift, but the '%s' gate still fails — not ")
				TEXT("reporting success on an unverified mesh. The destination package now holds this ")
				TEXT("wrongly-scaled import, unsaved: discard it rather than saving.\n%s"),
				*CorrectionText, *MeasuredText, *RigName, *After.Diff);
		}

		// Name WHICH lane succeeded. The two are different findings about the file — one says its geometry
		// disagrees with the unit it declares, the other says its lengths drifted — and a single sentence
		// covering both would tell the reader neither.
		if (bImporterUnitConversion)
		{
			return WithNotes(FString::Printf(
				TEXT("OK: applied %s (cancelled the importer's own measured %sx unit conversion: the file declares ")
				TEXT("%s but its geometry is already centimetres, root cause 1a. The factor matches that declaration, so ")
				TEXT("it is the importer's and not a baked rig scale; '%s' gate green)"),
				*CorrectionText, *MeasuredText, *UnitText, *RigName), After.Notes);
		}

		return WithNotes(FString::Printf(
			TEXT("OK: applied %s (cancelled a measured %sx whole-rig unit mismatch, root cause 1a/1b; declared unit %s; ")
			TEXT("'%s' gate green)"),
			*CorrectionText, *MeasuredText, *UnitText, *RigName), After.Notes);
	}

	FString FormatScaleFactor(double Factor)
	{
		return FString::Printf(TEXT("%.4g"), Factor);
	}

	FString ScaleReportToString(const TSharedRef<FJsonObject>& Report)
	{
		return TopologyToString(Report);
	}

	FString ScaleBaselineDir()
	{
		return FPaths::Combine(FPaths::ProjectPluginsDir(), TEXT("MotionTerminal"), TEXT("Tests"), TEXT("ScaleBaselines"));
	}

	FString ScaleBaselinePath(const FString& RigName)
	{
		const FString File = RigName.EndsWith(TEXT(".json")) ? RigName : RigName + TEXT(".json");
		return FPaths::Combine(ScaleBaselineDir(), File);
	}

	FString ValidateRigName(const FString& RigName)
	{
		if (RigName.IsEmpty())
		{
			return TEXT("RigName is empty");
		}
		if (RigName.Contains(TEXT("/")) || RigName.Contains(TEXT("\\")))
		{
			return FString::Printf(TEXT("RigName '%s' contains a path separator"), *RigName);
		}
		if (RigName.Contains(TEXT("..")))
		{
			return FString::Printf(TEXT("RigName '%s' contains '..' (path traversal)"), *RigName);
		}
		return FString();
	}
}
