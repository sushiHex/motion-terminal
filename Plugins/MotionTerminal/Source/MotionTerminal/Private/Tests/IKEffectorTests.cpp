#include "CoreMinimal.h"
#include "TestJsonRow.h"

#if WITH_AUTOMATION_TESTS

#include "Animation/AnimSequence.h"
#include "Animation/AnimData/IAnimationDataModel.h"
#include "Animation/Skeleton.h"
#include "Misc/AutomationTest.h"
#include "Misc/FileHelper.h"
#include "Misc/PackageName.h"
#include "Misc/Paths.h"
#include "ReferenceSkeleton.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "UObject/UObjectGlobals.h"

// ================================================================================================
// The IK-EFFECTOR tripwire. The sibling of Verify.ClipVaries, one level up the causal chain.
//
// WHY THIS EXISTS. Verify.ClipVaries asks "does the DEFORM pose vary over time" — the frozen-bake
// question. It cannot see the defect this test was written for, because that defect leaves every deform
// bone perfectly animated and kills a bone the deform pose does not contain.
//
// ABP_MT_Locomotion runs CR_Mannequin_FootIK, and that rig solves the deform foot ONTO the ik_foot
// effector rather than offsetting it (research/foot-ik-erratum.md: with the node running and the trace
// off it drives foot_l ONTO ik_foot_l, dL->0). So the effector track IS the foot's intended transform. A
// clip whose ik_foot_l / ik_foot_r tracks hold a CONSTANT value therefore PINS the foot every frame — and
// it pins it exactly as hard as a skeleton with no ik_foot bones at all does. A skeleton-presence gate
// (GetBoneIndex("ik_foot_root") != -1) is NECESSARY BUT NOT SUFFICIENT: Khaimera HAS those bones
// natively, so the gate correctly reads true and correctly leaves the pass running, while his baked
// retarget clips carried ik_foot_l spans of 0.000 / 0.000 / 0.000 against the mannequin source's
// 14.163 / 116.384 / 50.635. A rendered take showed him planted with his arms swinging at 43 cm.
//
// The precondition the pass actually needs is "the animation DRIVES the effectors" — a property of the
// CLIP, not of the SKELETON, and therefore not computable once at init. That makes it an authoring-time
// assertion, which is what this file is. It is deliberately NOT a per-frame runtime check: the cost would
// be paid every tick by every rig to detect an asset defect that cannot change at runtime.
//
// WHAT EACH ROW ASSERTS, and why the third assertion is the one that matters most.
//   (1) NOT DEGENERATE  — the effector's translation VARIES (max-min per axis), vertically and overall.
//                         This is the assertion the shipped defect fails, at 0.000 against a 5.0 floor.
//   (2) ROTATION AGREES — the effector's rotation matches the FK foot's, to maxEffectorRotDeg.
//   (3) TRACKS THE FOOT — the effector's TRANSLATION matches the FK foot's, to maxEffectorDriftCm.
//
// (1) alone is not enough and saying why is the point: an effector track that VARIES but was baked in the
// WRONG SPACE would sail through a variance check and then move the feet confidently to the wrong places
// — which looks like success in any bone-range probe and fails on camera. (2) and (3) are what make this a
// gate on CORRECTNESS rather than on liveness.
//
// THE CONVENTION, MEASURED OFF THE WORKING MANNEQUIN CLIPS RATHER THAN ASSUMED.
// ik_foot_l's LOCAL transform (i.e. relative to its parent ik_foot_root) carries the FK foot's transform
// expressed in ik_foot_root space, EXACTLY. The parent chains are ik_foot_l -> ik_foot_root -> root and
// foot_l -> calf_l -> thigh_l -> pelvis -> root, on BOTH SK_Mannequin and Khaimera_Skeleton.
// Measured by THIS TEST over every key of MF_Unarmed_Jog_Fwd and MF_Unarmed_Walk_Fwd, both feet, twice:
//   - rotation agrees to a max of 0.00103 deg;
//   - translation agrees to a max of 0.0001 cm — one micron.
// The rival "component space including the root bone" reading is excluded by orders of magnitude: under
// it the residual reaches -1059.86 cm by the end of the same clip.
//
// A CORRECTION WORTH KEEPING. An earlier measurement reported this residual as a
// "mirrored ~1.2 cm outboard offset, max 2.96 cm" and set this test's drift bound at 5.0 cm to
// accommodate it. That residual is not in the data. It came from a throwaway probe that read poses via
// AnimationLibrary::GetBonePoseForFrame, which APPLIES the skeleton's bone translation retargeting mode,
// whereas this test reads the RAW animation data model. On SK_Mannequin the FK leg chain
// (pelvis/thigh_l/calf_l/foot_l) is AnimationScaled while ik_foot_root/ik_foot_l/ik_foot_r are Animation
// — so that probe composed a SCALED chain and differenced it against an UNSCALED effector. Translation
// only; rotation untouched. Which is precisely why the rotation figures were right and the translation
// figures were not, and why the bogus residual looked "mirrored" (both legs scale identically). On
// Khaimera_Skeleton every bone in both chains is Animation, which is why the Python bake and this test
// agree to 0.0000 cm there. THE LESSON: for authored-data questions read the data model, not the pose
// API — the pose API answers "what will evaluation produce", which is a different question.
//
// Headless-safe (asset load + data-model read; no RHI, no PIE), so it runs in the DEFAULT tier beside
// Verify.ClipVaries. localOnly rows SKIP-with-log when their package is absent, so fresh clones without
// the machine-local Khaimera content stay green; the committed mannequin rows keep >= 1 live row always.
// ================================================================================================

namespace
{
	struct FIKEffectorRow
	{
		FString Clip;
		double MinEffectorSpanCm = 0.0;
		double MaxEffectorDriftCm = 0.0;
		double MaxEffectorRotDeg = 0.0;
		bool bLocalOnly = false;
	};

	// Loads ik_effector_manifest.json into OutRows. false + OutError on read/parse/empty failure.
	// GetTests checks this AND Rows.Num(): on any failure it emits ONE synthetic "_ManifestLoadFailed"
	// sentinel row (never zero rows) whose RunTest AddErrors — a missing/malformed/empty manifest fails
	// LOUD (RED), never a silent zero-subtest vanish that lets the default tier still report green
	// (the PieTests.cpp / ClipVariesTests.cpp sentinel precedent).
	bool LoadIKEffectorManifest(TArray<FIKEffectorRow>& OutRows, FString& OutError)
	{
		TSharedPtr<FJsonObject> Root;
		if (!MotionTerminalTestJson::LoadManifestRoot(TEXT("ik_effector_manifest.json"), Root, OutError)) { return false; }
		const TArray<TSharedPtr<FJsonValue>>* Clips = nullptr;
		if (!MotionTerminalTestJson::RequireArrayField(Root, TEXT("clips"), TEXT("ik_effector_manifest.json"), Clips, OutError)) { return false; }
		int32 RowIndex = -1;
		for (const TSharedPtr<FJsonValue>& V : *Clips)
		{
			++RowIndex;
			// A ROW THAT CANNOT BE READ IS A MANIFEST DEFECT, NOT A SKIPPED ROW. Both `continue`s here
			// dropped a row whose assertions then never ran, while the zero-rows sentinel below fires only
			// when EVERY row is unusable - so losing one clip's coverage stayed green. The thresholds were
			// already required (correctly); the row identity was not.
			const TSharedPtr<FJsonObject>* Obj = nullptr;
			if (!MotionTerminalTestJson::RequireObject(V, TEXT("ik_effector_manifest.json"), RowIndex, Obj, OutError)) { return false; }
			FIKEffectorRow Row;
			if (!MotionTerminalTestJson::RequireString(*Obj, TEXT("clip"), TEXT("ik_effector_manifest.json"), RowIndex, Row.Clip, OutError)) { return false; }
			if (!MotionTerminalTestJson::RequirePositive(*Obj, TEXT("minEffectorSpanCm"), TEXT("ik_effector_manifest.json"), RowIndex, Row.MinEffectorSpanCm, OutError)) { return false; }
			if (!MotionTerminalTestJson::RequirePositive(*Obj, TEXT("maxEffectorDriftCm"), TEXT("ik_effector_manifest.json"), RowIndex, Row.MaxEffectorDriftCm, OutError)) { return false; }
			if (!MotionTerminalTestJson::RequirePositive(*Obj, TEXT("maxEffectorRotDeg"), TEXT("ik_effector_manifest.json"), RowIndex, Row.MaxEffectorRotDeg, OutError)) { return false; }
			(*Obj)->TryGetBoolField(TEXT("localOnly"), Row.bLocalOnly);   // genuinely optional
			OutRows.Add(Row);
		}
		if (OutRows.Num() == 0)
		{
			OutError = TEXT("ik_effector_manifest.json yielded zero usable clip rows");
			return false;
		}
		return true;
	}

	const FString kIKEffectorManifestErrorPrefix = TEXT("<manifest-error>");

	// Per-bone local key cache over one clip. A bone with NO animation track contributes its REFERENCE
	// pose, which is exactly what evaluation does — and is why the cache stores a resolved array per bone
	// rather than reaching for the data model per key.
	class FLocalPoseReader
	{
	public:
		FLocalPoseReader(const IAnimationDataModel& InModel, const FReferenceSkeleton& InRefSkel, int32 InNumKeys)
			: Model(InModel), RefSkel(InRefSkel), NumKeys(InNumKeys) {}

		const FTransform& Local(const FName& Bone, int32 Key)
		{
			if (TArray<FTransform>* Found = Cache.Find(Bone)) { return (*Found)[Key]; }

			TArray<FTransform> Keys;
			if (Model.IsValidBoneTrackName(Bone))
			{
				Model.GetBoneTrackTransforms(Bone, Keys);
			}
			if (Keys.Num() != NumKeys)
			{
				// No track (or a track of unexpected length): fall back to the reference pose for every
				// key, which is what evaluation does. The
				// ref-pose fallback is only ever hit by intermediate chain bones, and every bone this
				// test ASSERTS on is separately required to have a real track.
				const int32 Idx = RefSkel.FindBoneIndex(Bone);
				const FTransform Ref = (Idx != INDEX_NONE) ? RefSkel.GetRefBonePose()[Idx] : FTransform::Identity;
				Keys.Init(Ref, NumKeys);
			}
			return Cache.Add(Bone, MoveTemp(Keys))[Key];
		}

		// Component-space transform of `Bone` at `Key`, composed up the parent chain to the root.
		// UE bone convention: ComponentSpace = Local * ParentComponentSpace.
		FTransform Component(const FName& Bone, int32 Key)
		{
			int32 Idx = RefSkel.FindBoneIndex(Bone);
			if (Idx == INDEX_NONE) { return FTransform::Identity; }
			FTransform Xf = Local(Bone, Key);
			for (int32 Parent = RefSkel.GetParentIndex(Idx); Parent != INDEX_NONE; Parent = RefSkel.GetParentIndex(Parent))
			{
				Xf = Xf * Local(RefSkel.GetBoneName(Parent), Key);
			}
			return Xf;
		}

	private:
		const IAnimationDataModel& Model;
		const FReferenceSkeleton& RefSkel;
		int32 NumKeys;
		TMap<FName, TArray<FTransform>> Cache;
	};
}

IMPLEMENT_COMPLEX_AUTOMATION_TEST(FVerifyIKEffectorDrivesTest,
	"MotionTerminal.Verify.IKEffectorDrives",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::ProductFilter)

void FVerifyIKEffectorDrivesTest::GetTests(TArray<FString>& OutBeautifiedNames, TArray<FString>& OutTestCommands) const
{
	TArray<FIKEffectorRow> Rows; FString Err;
	if (!LoadIKEffectorManifest(Rows, Err))
	{
		OutBeautifiedNames.Add(TEXT("_ManifestLoadFailed"));
		OutTestCommands.Add(kIKEffectorManifestErrorPrefix + Err);
		return;
	}
	for (const FIKEffectorRow& R : Rows)
	{
		OutBeautifiedNames.Add(FPaths::GetBaseFilename(R.Clip));
		OutTestCommands.Add(FString::Printf(TEXT("%s|%s|%s|%s|%d"),
			*R.Clip,
			*FString::SanitizeFloat(R.MinEffectorSpanCm),
			*FString::SanitizeFloat(R.MaxEffectorDriftCm),
			*FString::SanitizeFloat(R.MaxEffectorRotDeg),
			R.bLocalOnly ? 1 : 0));
	}
}

bool FVerifyIKEffectorDrivesTest::RunTest(const FString& Parameters)
{
	if (Parameters.StartsWith(kIKEffectorManifestErrorPrefix))
	{
		AddError(FString::Printf(
			TEXT("ik_effector_manifest.json failed to load — Verify.IKEffectorDrives rows have NOT run: %s"),
			*Parameters.RightChop(kIKEffectorManifestErrorPrefix.Len())));
		return false;
	}

	TArray<FString> Parts;
	Parameters.ParseIntoArray(Parts, TEXT("|"), /*CullEmpty*/ false);
	if (Parts.Num() != 5)
	{
		AddError(FString::Printf(TEXT("malformed IKEffectorDrives command '%s'"), *Parameters));
		return false;
	}
	const FString ClipPath = Parts[0];
	const double MinSpanCm = FCString::Atod(*Parts[1]);
	const double MaxDriftCm = FCString::Atod(*Parts[2]);
	const double MaxRotDeg = FCString::Atod(*Parts[3]);
	const bool bLocalOnly = (Parts[4] == TEXT("1"));
	const FString Tag = FPaths::GetBaseFilename(ClipPath);

	// Machine-local skip: a localOnly clip whose package is absent SKIPs (fresh clones stay green).
	if (bLocalOnly && !FPackageName::DoesPackageExist(FPackageName::ObjectPathToPackageName(ClipPath)))
	{
		AddInfo(FString::Printf(TEXT("SKIP (localOnly clip absent on this machine): %s"), *ClipPath));
		return true;
	}

	UAnimSequence* Seq = LoadObject<UAnimSequence>(nullptr, *ClipPath);
	if (!TestNotNull(*FString::Printf(TEXT("[%s] AnimSequence '%s' loads"), *Tag, *ClipPath), Seq))
	{
		return false;
	}
	const IAnimationDataModel* Model = Seq->GetDataModel();
	if (!Model)
	{
		AddError(FString::Printf(TEXT("[%s] no animation data model (editor-only source data missing)"), *Tag));
		return false;
	}
	USkeleton* Skeleton = Seq->GetSkeleton();
	if (!Skeleton)
	{
		AddError(FString::Printf(TEXT("[%s] clip has no skeleton — cannot resolve the ik_foot parent chain"), *Tag));
		return false;
	}
	const FReferenceSkeleton& RefSkel = Skeleton->GetReferenceSkeleton();

	const int32 NumKeys = Model->GetNumberOfKeys();
	if (NumKeys < 2)
	{
		AddError(FString::Printf(TEXT("[%s] %d key(s) (<2) — a single-pose clip cannot drive an effector"), *Tag, NumKeys));
		return false;
	}

	static const FName IKParent(TEXT("ik_foot_root"));
	const FName Effectors[2] = { FName(TEXT("ik_foot_l")), FName(TEXT("ik_foot_r")) };
	const FName Feet[2]      = { FName(TEXT("foot_l")),    FName(TEXT("foot_r")) };

	// Every bone this test ASSERTS on must carry a real animation track. Without this, a clip missing
	// ik_foot_l would silently compare ref pose against ref pose: the drift would read 0.0 and the span
	// would read 0.0, i.e. it would fail the span check for the RIGHT reason but report a misleading
	// cause — and a future relaxation of the span check would turn it into a vacuous pass. This is the
	// same guard khai_inplace_author.py's _assert_tracked() applies.
	{
		TArray<FString> Missing;
		auto RequireTrack = [&](const FName& B)
		{
			if (!Model->IsValidBoneTrackName(B)) { Missing.Add(B.ToString()); }
			if (RefSkel.FindBoneIndex(B) == INDEX_NONE) { Missing.AddUnique(B.ToString() + TEXT(" (not on skeleton)")); }
		};
		RequireTrack(IKParent);
		for (int32 i = 0; i < 2; ++i) { RequireTrack(Effectors[i]); RequireTrack(Feet[i]); }
		if (Missing.Num() > 0)
		{
			AddError(FString::Printf(
				TEXT("[%s] missing animation track(s) for %s — this clip cannot drive the mannequin foot-IK pass, and a per-key comparison on these bones would compare reference pose to reference pose and read 0.0 vacuously"),
				*Tag, *FString::Join(Missing, TEXT(", "))));
			return false;
		}
	}

	FLocalPoseReader Pose(*Model, RefSkel, NumKeys);

	for (int32 Side = 0; Side < 2; ++Side)
	{
		const FName Eff = Effectors[Side];
		const FName Foot = Feet[Side];

		FVector Lo(TNumericLimits<double>::Max()), Hi(-TNumericLimits<double>::Max());
		double MaxDrift = 0.0, MaxRot = 0.0;
		int32 WorstDriftKey = 0, WorstRotKey = 0;

		for (int32 Key = 0; Key < NumKeys; ++Key)
		{
			// BOTH SIDES ARE DERIVED THE SAME WAY: each is "the bone expressed in
			// ik_foot_root space", built by composing to component space and then taking the relative
			// transform. Reading Have as a bare Local() would give the identical number ONLY while
			// ik_foot_l remains a direct child of ik_foot_root — true on every rig measured, but it is an
			// assumption the comparison does not need, and an asymmetric derivation invites the reader to
			// wonder whether the two sides are really the same quantity. They are.
			const FTransform IKParentComp = Pose.Component(IKParent, Key);
			const FTransform Have = Pose.Component(Eff, Key).GetRelativeTransform(IKParentComp);
			// What the effector SHOULD hold: the FK foot expressed in that same space.
			const FTransform Want = Pose.Component(Foot, Key).GetRelativeTransform(IKParentComp);

			const FVector T = Have.GetTranslation();
			Lo = Lo.ComponentMin(T);
			Hi = Hi.ComponentMax(T);

			const double Drift = FVector::Dist(T, Want.GetTranslation());
			if (Drift > MaxDrift) { MaxDrift = Drift; WorstDriftKey = Key; }
			const double RotDeg = FMath::RadiansToDegrees(Have.GetRotation().AngularDistance(Want.GetRotation()));
			if (RotDeg > MaxRot) { MaxRot = RotDeg; WorstRotKey = Key; }
		}

		const FVector Span = Hi - Lo;
		const double SpanMax = FMath::Max3(Span.X, Span.Y, Span.Z);

		AddInfo(FString::Printf(
			TEXT("[%s] %s over %d keys: translation span %.3f / %.3f / %.3f cm (largest axis %.3f, floor %.3f); tracks %s to max %.4f cm at key %d (bound %.3f) and max %.5f deg at key %d (bound %.3f)"),
			*Tag, *Eff.ToString(), NumKeys, Span.X, Span.Y, Span.Z, SpanMax, MinSpanCm,
			*Foot.ToString(), MaxDrift, WorstDriftKey, MaxDriftCm, MaxRot, WorstRotKey, MaxRotDeg));

		// (1) NOT DEGENERATE. Both the overall excursion and the VERTICAL one, because a clip could in
		// principle slide a foot along the floor without ever lifting it and that is not a stride.
		if (!(SpanMax >= MinSpanCm))
		{
			AddError(FString::Printf(
				TEXT("[%s] DEAD EFFECTOR: '%s' translation span is %.3f / %.3f / %.3f cm — largest axis %.4f < %.3f. CR_Mannequin_FootIK solves the deform foot ONTO this bone, so a constant effector PINS the foot every frame exactly as a missing bone does. The deform pose can be perfect and the rig will still not stride."),
				*Tag, *Eff.ToString(), Span.X, Span.Y, Span.Z, SpanMax, MinSpanCm));
		}
		else if (!(Span.Z >= MinSpanCm))
		{
			AddError(FString::Printf(
				TEXT("[%s] FLAT EFFECTOR: '%s' moves horizontally (span %.3f / %.3f / %.3f cm) but its VERTICAL span %.4f < %.3f — the solved foot would slide along the floor and never leave it."),
				*Tag, *Eff.ToString(), Span.X, Span.Y, Span.Z, Span.Z, MinSpanCm));
		}

		// (2)/(3) CORRECTNESS. An effector that varies but was baked in the wrong space passes (1) and
		// then moves the foot confidently to the wrong place — invisible to any bone-range probe.
		if (!(MaxRot <= MaxRotDeg))
		{
			AddError(FString::Printf(
				TEXT("[%s] WRONG-SPACE EFFECTOR: '%s' rotation disagrees with '%s' by %.5f deg at key %d (bound %.3f). The effector carries the foot's transform in ik_foot_root space; a rotation mismatch this large means it was authored against a different parent or space."),
				*Tag, *Eff.ToString(), *Foot.ToString(), MaxRot, WorstRotKey, MaxRotDeg));
		}
		if (!(MaxDrift <= MaxDriftCm))
		{
			AddError(FString::Printf(
				TEXT("[%s] EFFECTOR DOES NOT TRACK THE FOOT: '%s' is %.4f cm from '%s' at key %d (bound %.3f). The solve drives the deform foot ONTO this bone, so this distance is the error it would introduce into the shipped pose."),
				*Tag, *Eff.ToString(), MaxDrift, *Foot.ToString(), WorstDriftKey, MaxDriftCm));
		}
	}

	return true;   // AddError already marks failure; returning true lets every row report
}

#endif // WITH_AUTOMATION_TESTS
