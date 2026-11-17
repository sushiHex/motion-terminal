#include "HeroPhysicsTools.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "Dom/JsonObject.h"
#include "Engine/SkeletalMesh.h"
#include "FileHelpers.h"
#include "HeroToolJson.h"
#include "PhysicsAssetUtils.h"
#include "PhysicsEngine/PhysicsAsset.h"
#include "PhysicsEngine/PhysicsConstraintTemplate.h"
#include "PhysicsEngine/SkeletalBodySetup.h"
#include "PhysicsEngine/SphereElem.h"
#include "PhysicsEngine/SphylElem.h"
#include "PhysicsEngine/TaperedCapsuleElem.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "UObject/Package.h"

#include UE_INLINE_GENERATED_CPP_BY_NAME(HeroPhysicsTools)

using HeroToolJson::Fail;
using HeroToolJson::ToJson;

namespace
{
	/** Component-space reference pose of every bone (Local * ParentComponentSpace, parents before children). */
	TArray<FTransform> RefComponentSpace(const FReferenceSkeleton& Ref)
	{
		const TArray<FTransform>& Local = Ref.GetRefBonePose();
		TArray<FTransform> CS;
		CS.SetNum(Local.Num());
		for (int32 i = 0; i < Local.Num(); ++i)
		{
			const int32 P = Ref.GetParentIndex(i);
			CS[i] = P == INDEX_NONE ? Local[i] : Local[i] * CS[P];
		}
		return CS;
	}

	TArray<TSharedPtr<FJsonValue>> DescribeBodies(const UPhysicsAsset* PA)
	{
		TArray<TSharedPtr<FJsonValue>> Out;
		for (int32 i = 0; i < PA->SkeletalBodySetups.Num(); ++i)
		{
			const USkeletalBodySetup* B = PA->SkeletalBodySetups[i];
			const TSharedRef<FJsonObject> O = MakeShared<FJsonObject>();
			O->SetStringField(TEXT("bone"), B->BoneName.ToString());
			O->SetNumberField(TEXT("index"), i);
			if (B->AggGeom.SphylElems.Num())
			{
				O->SetStringField(TEXT("shape"), TEXT("capsule"));
				O->SetNumberField(TEXT("radius"), B->AggGeom.SphylElems[0].Radius);
				O->SetNumberField(TEXT("length"), B->AggGeom.SphylElems[0].Length);
			}
			else if (B->AggGeom.SphereElems.Num())
			{
				O->SetStringField(TEXT("shape"), TEXT("sphere"));
				O->SetNumberField(TEXT("radius"), B->AggGeom.SphereElems[0].Radius);
			}
			O->SetNumberField(TEXT("elements"), B->AggGeom.GetElementCount());
			// Every capsule and sphere, not just the first (otherwise a body built with a second shape,
			// or the wrong one second, read back as matching the spec). Bone-local geometry in the builder's own terms
			// ("center", "rotation_xyzw", "radius_cm", "length_cm"), capsules first then spheres: AggGeom keeps them
			// in separate arrays, so the spec's interleaving is not recoverable. Box/convex elements count in "elements"
			// above but are never built here.
			TArray<TSharedPtr<FJsonValue>> Shapes;
			const auto Numbers = [](std::initializer_list<double> Vs)
			{
				TArray<TSharedPtr<FJsonValue>> A;
				for (const double V : Vs)
				{
					A.Add(MakeShared<FJsonValueNumber>(V));
				}
				return A;
			};
			const auto AddShape = [&Shapes, &Numbers](const TCHAR* Type, const FVector& C, const FQuat& Q, double R, double L,
				double REnd = -1.0)
			{
				const TSharedRef<FJsonObject> S = MakeShared<FJsonObject>();
				S->SetStringField(TEXT("type"), Type);
				S->SetArrayField(TEXT("center"), Numbers({C.X, C.Y, C.Z}));
				S->SetArrayField(TEXT("rotation_xyzw"), Numbers({Q.X, Q.Y, Q.Z, Q.W}));
				S->SetNumberField(TEXT("radius_cm"), R);
				S->SetNumberField(TEXT("length_cm"), L);
				if (REnd >= 0.0)
				{
					S->SetNumberField(TEXT("radius_end_cm"), REnd);
				}
				Shapes.Add(MakeShared<FJsonValueObject>(S));
			};
			for (const FKSphylElem& S : B->AggGeom.SphylElems)
			{
				AddShape(TEXT("capsule"), S.Center, S.Rotation.Quaternion(), S.Radius, S.Length);
			}
			for (const FKSphereElem& S : B->AggGeom.SphereElems)
			{
				AddShape(TEXT("sphere"), S.Center, FQuat::Identity, S.Radius, 0.0);
			}
			// The spec's near radius is Radius1 (the -Z end), its far radius Radius0 (+Z: FClothCollisionSource puts
			// Radius0 at Center + Z * Length / 2, SkeletalMeshComponentPhysics.cpp:241)
			for (const FKTaperedCapsuleElem& S : B->AggGeom.TaperedCapsuleElems)
			{
				AddShape(TEXT("tapered"), S.Center, S.Rotation.Quaternion(), S.Radius1, S.Length, S.Radius0);
			}
			O->SetArrayField(TEXT("shapes"), Shapes);
			O->SetStringField(TEXT("physics_type"), B->PhysicsType == PhysType_Simulated ? TEXT("simulated")
				: B->PhysicsType == PhysType_Kinematic ? TEXT("kinematic") : TEXT("default"));
			Out.Add(MakeShared<FJsonValueObject>(O));
		}
		return Out;
	}

	/** Every body pair whose collision is enabled, as [a, b] bone names (a body's index order). */
	TArray<TSharedPtr<FJsonValue>> DescribeCollisions(const UPhysicsAsset* PA)
	{
		TArray<TSharedPtr<FJsonValue>> Out;
		for (int32 i = 0; i < PA->SkeletalBodySetups.Num(); ++i)
		{
			for (int32 j = i + 1; j < PA->SkeletalBodySetups.Num(); ++j)
			{
				if (PA->IsCollisionEnabled(i, j))
				{
					Out.Add(MakeShared<FJsonValueArray>(TArray<TSharedPtr<FJsonValue>>{
						MakeShared<FJsonValueString>(PA->SkeletalBodySetups[i]->BoneName.ToString()),
						MakeShared<FJsonValueString>(PA->SkeletalBodySetups[j]->BoneName.ToString())}));
				}
			}
		}
		return Out;
	}

	struct FCapsuleCS
	{
		FVector A, B;
		double Radius;
	};

	/** A body's shapes in component space at the reference pose, as segments with a radius (a sphere: A == B). */
	TArray<FCapsuleCS> BodyCapsules(const USkeletalBodySetup* Setup, const FTransform& BoneCS)
	{
		TArray<FCapsuleCS> Out;
		for (const FKSphylElem& S : Setup->AggGeom.SphylElems)
		{
			const FTransform T = S.GetTransform() * BoneCS;
			const FVector Axis = T.GetUnitAxis(EAxis::Z) * (S.Length * 0.5);
			Out.Add({T.GetLocation() - Axis, T.GetLocation() + Axis, S.Radius});
		}
		for (const FKSphereElem& S : Setup->AggGeom.SphereElems)
		{
			const FVector C = BoneCS.TransformPosition(S.Center);
			Out.Add({C, C, S.Radius});
		}
		return Out;
	}

	/** The surface gap between two bodies at the reference pose, cm (negative: they overlap). */
	double RefClearance(const USkeletalBodySetup* A, const FTransform& ACS, const USkeletalBodySetup* B, const FTransform& BCS)
	{
		double Best = TNumericLimits<double>::Max();
		for (const FCapsuleCS& X : BodyCapsules(A, ACS))
		{
			for (const FCapsuleCS& Y : BodyCapsules(B, BCS))
			{
				FVector P, Q;
				FMath::SegmentDistToSegmentSafe(X.A, X.B, Y.A, Y.B, P, Q);
				Best = FMath::Min(Best, FVector::Dist(P, Q) - X.Radius - Y.Radius);
			}
		}
		return Best;
	}

	TArray<TSharedPtr<FJsonValue>> DescribeConstraints(const UPhysicsAsset* PA)
	{
		TArray<TSharedPtr<FJsonValue>> Out;
		for (const UPhysicsConstraintTemplate* C : PA->ConstraintSetup)
		{
			const FConstraintInstance& I = C->DefaultInstance;
			const TSharedRef<FJsonObject> O = MakeShared<FJsonObject>();
			O->SetStringField(TEXT("child"), I.ConstraintBone1.ToString());
			O->SetStringField(TEXT("parent"), I.ConstraintBone2.ToString());
			O->SetBoolField(TEXT("angular_free"), I.GetAngularSwing1Motion() == ACM_Free && I.GetAngularSwing2Motion() == ACM_Free
				&& I.GetAngularTwistMotion() == ACM_Free);
			O->SetNumberField(TEXT("swing1_deg"), I.GetAngularSwing1Limit());
			O->SetNumberField(TEXT("swing2_deg"), I.GetAngularSwing2Limit());
			O->SetNumberField(TEXT("twist_deg"), I.GetAngularTwistLimit());
			Out.Add(MakeShared<FJsonValueObject>(O));
		}
		return Out;
	}
}

FString UHeroPhysicsTools::BuildPhysicsAsset(const FString& MeshPath, const FString& DestAssetPath, const FString& SpecJson)
{
	// Generated hero content only: never write next to (or over) a stock asset.
	if (!DestAssetPath.StartsWith(TEXT("/Game/MT/")))
	{
		return Fail(FString::Printf(TEXT("refusing %s: generated hero assets live under /Game/MT/"), *DestAssetPath));
	}
	USkeletalMesh* Mesh = LoadObject<USkeletalMesh>(nullptr, *MeshPath);
	if (!Mesh)
	{
		return Fail(FString::Printf(TEXT("no skeletal mesh at %s"), *MeshPath));
	}
	if (FPackageName::DoesPackageExist(DestAssetPath) || FindObject<UPackage>(nullptr, *DestAssetPath))
	{
		return Fail(FString::Printf(TEXT("%s exists: delete it first (the caller guards its referencers)"), *DestAssetPath));
	}
	TSharedPtr<FJsonObject> Spec;
	if (!FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(SpecJson), Spec) || !Spec.IsValid())
	{
		return Fail(TEXT("SpecJson does not parse"));
	}
	const FReferenceSkeleton& Ref = Mesh->GetRefSkeleton();
	const TArray<FTransform> CS = RefComponentSpace(Ref);

	UPackage* Package = CreatePackage(*DestAssetPath);
	const FName AssetName(*FPackageName::GetLongPackageAssetName(DestAssetPath));
	UPhysicsAsset* PA = NewObject<UPhysicsAsset>(Package, AssetName, RF_Public | RF_Standalone | RF_Transactional);
	// The preview mesh supplies the reference pose SnapTransformsToDefault reads; the mesh itself is not changed.
	PA->SetPreviewMesh(Mesh, false);

	FPhysAssetCreateParams Params;
	// Stated, not inherited from the engine default: every new body disables collision with every earlier one (the
	// reaction is the controls against the hit, not bodies pushing each other; docs/specs/2026-10-29-hit-reaction-design.md).
	Params.bDisableCollisionsByDefault = true;
	TMap<FName, int32> BodyIndex;
	for (const TSharedPtr<FJsonValue>& V : Spec->GetArrayField(TEXT("bodies")))
	{
		const TSharedPtr<FJsonObject> B = V->AsObject();
		const FName Bone(*B->GetStringField(TEXT("bone")));
		const int32 BI = Ref.FindBoneIndex(Bone);
		if (BI == INDEX_NONE)
		{
			return Fail(FString::Printf(TEXT("body bone %s is not on %s"), *Bone.ToString(), *MeshPath));
		}
		const int32 Idx = FPhysicsAssetUtils::CreateNewBody(PA, Bone, Params);
		USkeletalBodySetup* Setup = PA->SkeletalBodySetups[Idx];
		Setup->AggGeom.EmptyElements();
		// Explicit, never PhysType_Default: RigidBodyWithControl treats only Simulated bodies as simulated.
		const FString Type = B->GetStringField(TEXT("physics_type"));
		if (Type != TEXT("simulated") && Type != TEXT("kinematic"))
		{
			return Fail(FString::Printf(TEXT("body %s physics_type %s is neither simulated nor kinematic"), *Bone.ToString(), *Type));
		}
		Setup->PhysicsType = Type == TEXT("simulated") ? PhysType_Simulated : PhysType_Kinematic;
		for (const TSharedPtr<FJsonValue>& EV : B->GetArrayField(TEXT("elements")))
		{
			const TSharedPtr<FJsonObject> E = EV->AsObject();
			const float Radius = static_cast<float>(E->GetNumberField(TEXT("radius_cm")));
			const TArray<TSharedPtr<FJsonValue>>* C = nullptr;
			const TArray<TSharedPtr<FJsonValue>>* Q = nullptr;
			const bool bLocal = E->TryGetArrayField(TEXT("center"), C) && C->Num() == 3;
			const FVector Center = bLocal ? FVector((*C)[0]->AsNumber(), (*C)[1]->AsNumber(), (*C)[2]->AsNumber()) : FVector::ZeroVector;
			FString Aim;
			if (E->GetStringField(TEXT("type")) == TEXT("capsule") && bLocal && E->TryGetArrayField(TEXT("rotation_xyzw"), Q) && Q->Num() == 4)
			{
				// A fitted shape's local geometry, as the profile stores it (the dynamics colliders use the same).
				FKSphylElem S(Radius, static_cast<float>(E->GetNumberField(TEXT("length_cm"))));
				S.Center = Center;
				S.Rotation = FQuat((*Q)[0]->AsNumber(), (*Q)[1]->AsNumber(), (*Q)[2]->AsNumber(), (*Q)[3]->AsNumber()).Rotator();
				Setup->AggGeom.SphylElems.Add(S);
			}
			else if (E->GetStringField(TEXT("type")) == TEXT("tapered"))
			{
				// A tapered capsule, explicit only (the spec resolves its aim: hit_reaction_core.resolve_aims). Chaos Cloth
				// collides with it through a cloth collision source (two spheres and a connection); rigid bodies ignore
				// it. Its near radius (radius_cm) is the -Z end, Radius1; its far radius the +Z end, Radius0.
				if (!(bLocal && E->TryGetArrayField(TEXT("rotation_xyzw"), Q) && Q->Num() == 4))
				{
					return Fail(FString::Printf(TEXT("tapered element on %s needs center and rotation_xyzw"), *Bone.ToString()));
				}
				FKTaperedCapsuleElem S(static_cast<float>(E->GetNumberField(TEXT("radius_end_cm"))), Radius,
					static_cast<float>(E->GetNumberField(TEXT("length_cm"))));
				S.Center = Center;
				S.Rotation = FQuat((*Q)[0]->AsNumber(), (*Q)[1]->AsNumber(), (*Q)[2]->AsNumber(), (*Q)[3]->AsNumber()).Rotator();
				Setup->AggGeom.TaperedCapsuleElems.Add(S);
			}
			else if (E->GetStringField(TEXT("type")) == TEXT("capsule") && E->TryGetStringField(TEXT("aim"), Aim))
			{
				const int32 AI = Ref.FindBoneIndex(FName(*Aim));
				if (AI == INDEX_NONE)
				{
					return Fail(FString::Printf(TEXT("aim bone %s is not on %s"), *Aim, *MeshPath));
				}
				// The aim bone in this bone's frame: the capsule runs from the bone's origin to it, inside both joints.
				const FVector L = CS[BI].InverseTransformPosition(CS[AI].GetLocation());
				const double Len = L.Size();
				if (Len < UE_KINDA_SMALL_NUMBER)
				{
					return Fail(FString::Printf(TEXT("%s and its aim %s coincide"), *Bone.ToString(), *Aim));
				}
				FKSphylElem S(Radius, static_cast<float>(FMath::Max(0.0, Len - 2.0 * Radius)));
				S.Center = L * 0.5;
				S.Rotation = FRotationMatrix::MakeFromZ(L / Len).Rotator();
				Setup->AggGeom.SphylElems.Add(S);
			}
			else
			{
				FKSphereElem S(Radius);
				S.Center = Center;
				Setup->AggGeom.SphereElems.Add(S);
			}
		}
		if (Setup->AggGeom.GetElementCount() == 0)
		{
			return Fail(FString::Printf(TEXT("body %s has no elements"), *Bone.ToString()));
		}
		BodyIndex.Add(Bone, Idx);
	}
	PA->UpdateBodySetupIndexMap();
	PA->UpdateBoundsBodiesArray();
	// RigidBodyWithControl copies the ASSET's solver settings (it has no writable step of its own): match the harness.
	double Step = 0.0;
	if (Spec->TryGetNumberField(TEXT("fixed_step_s"), Step))
	{
		PA->SolverSettings.FixedTimeStep = static_cast<float>(Step);
	}

	for (const TSharedPtr<FJsonValue>& V : Spec->GetArrayField(TEXT("constraints")))
	{
		const TSharedPtr<FJsonObject> C = V->AsObject();
		const FName Child(*C->GetStringField(TEXT("child"))), Parent(*C->GetStringField(TEXT("parent")));
		const int32* ChildBody = BodyIndex.Find(Child);
		const int32* ParentBody = BodyIndex.Find(Parent);
		if (!ChildBody || !ParentBody)
		{
			return Fail(FString::Printf(TEXT("constraint %s -> %s names a bone with no body"), *Child.ToString(), *Parent.ToString()));
		}
		const int32 Idx = FPhysicsAssetUtils::CreateNewConstraint(PA, Child);
		UPhysicsConstraintTemplate* T = PA->ConstraintSetup[Idx];
		FConstraintInstance& I = T->DefaultInstance;
		// As the engine's own generator does (PhysicsAssetUtils.cpp CreateFromSkeletalMeshInternal): joint at the child's
		// origin, collision off between the pair.
		I.ConstraintBone1 = Child;
		I.ConstraintBone2 = Parent;
		I.SnapTransformsToDefault(EConstraintTransformComponentFlags::All, PA);
		// "angular": "free" leaves rotation to the caller's controls (the limit frames are the reference pose's and
		// would fight an animated pose); otherwise limited by swing1/swing2/twist degrees.
		FString Angular;
		if (C->TryGetStringField(TEXT("angular"), Angular) && Angular == TEXT("free"))
		{
			I.SetAngularSwing1Limit(ACM_Free, 0.f);
			I.SetAngularSwing2Limit(ACM_Free, 0.f);
			I.SetAngularTwistLimit(ACM_Free, 0.f);
		}
		else
		{
			I.SetAngularSwing1Limit(ACM_Limited, static_cast<float>(C->GetNumberField(TEXT("swing1_deg"))));
			I.SetAngularSwing2Limit(ACM_Limited, static_cast<float>(C->GetNumberField(TEXT("swing2_deg"))));
			I.SetAngularTwistLimit(ACM_Limited, static_cast<float>(C->GetNumberField(TEXT("twist_deg"))));
		}
		T->SetDefaultProfile(I);
		PA->DisableCollision(*ChildBody, *ParentBody);
	}

	// "collide": [{"a", "b", "min_clearance_cm"}]: body pairs that collide (an arm against the torso it must not pass
	// through). Each must be at least that far apart at the reference pose, so the solver never starts by pushing an
	// overlapping pair apart (depenetration spun overlapping torso bodies on Aurora). The caller's spec already
	// keeps only the pairs that clear, so one that does not is an inconsistency between it and this mesh: refused, never
	// silently skipped (a skipped pair left an arm free to pass through the torso).
	TArray<TSharedPtr<FJsonValue>> Collide;
	const TArray<TSharedPtr<FJsonValue>>* Pairs = nullptr;
	if (Spec->TryGetArrayField(TEXT("collide"), Pairs))
	{
		for (const TSharedPtr<FJsonValue>& V : *Pairs)
		{
			const TSharedPtr<FJsonObject> C = V->AsObject();
			const FName A(*C->GetStringField(TEXT("a"))), B(*C->GetStringField(TEXT("b")));
			const int32* IA = BodyIndex.Find(A);
			const int32* IB = BodyIndex.Find(B);
			if (!IA || !IB)
			{
				return Fail(FString::Printf(TEXT("collide pair %s / %s names a bone with no body"), *A.ToString(), *B.ToString()));
			}
			const double Gap = RefClearance(PA->SkeletalBodySetups[*IA], CS[Ref.FindBoneIndex(A)],
				PA->SkeletalBodySetups[*IB], CS[Ref.FindBoneIndex(B)]);
			const double MinGap = C->GetNumberField(TEXT("min_clearance_cm"));
			const bool bOn = Gap >= MinGap;
			if (!bOn)
			{
				return Fail(FString::Printf(TEXT("collide pair %s / %s clears %.3f cm at the reference pose, under its "
					"min_clearance_cm %.3f: the spec and the mesh disagree"), *A.ToString(), *B.ToString(), Gap, MinGap));
			}
			PA->EnableCollision(*IA, *IB);
			const TSharedRef<FJsonObject> O = MakeShared<FJsonObject>();
			O->SetStringField(TEXT("a"), A.ToString());
			O->SetStringField(TEXT("b"), B.ToString());
			O->SetNumberField(TEXT("ref_clearance_cm"), Gap);
			O->SetBoolField(TEXT("enabled"), bOn);
			Collide.Add(MakeShared<FJsonValueObject>(O));
		}
	}

	FAssetRegistryModule::AssetCreated(PA);
	PA->MarkPackageDirty();
	if (!UEditorLoadingAndSavingUtils::SavePackages({Package}, false))
	{
		return Fail(FString::Printf(TEXT("could not save %s"), *DestAssetPath));
	}
	const TSharedRef<FJsonObject> Obj = MakeShared<FJsonObject>();
	Obj->SetBoolField(TEXT("ok"), true);
	Obj->SetStringField(TEXT("path"), PA->GetPathName());
	Obj->SetArrayField(TEXT("bodies"), DescribeBodies(PA));
	Obj->SetArrayField(TEXT("constraints"), DescribeConstraints(PA));
	Obj->SetArrayField(TEXT("collide"), Collide);
	Obj->SetArrayField(TEXT("collisions"), DescribeCollisions(PA));
	return ToJson(Obj);
}

FString UHeroPhysicsTools::DescribePhysicsAsset(const FString& PhysicsAssetPath)
{
	const UPhysicsAsset* PA = LoadObject<UPhysicsAsset>(nullptr, *PhysicsAssetPath);
	if (!PA)
	{
		return Fail(FString::Printf(TEXT("no physics asset at %s"), *PhysicsAssetPath));
	}
	const TSharedRef<FJsonObject> Obj = MakeShared<FJsonObject>();
	Obj->SetBoolField(TEXT("ok"), true);
	Obj->SetStringField(TEXT("path"), PA->GetPathName());
	Obj->SetStringField(TEXT("preview_mesh"), PA->GetPreviewMesh() ? PA->GetPreviewMesh()->GetPathName() : TEXT(""));
	Obj->SetNumberField(TEXT("fixed_step_s"), PA->SolverSettings.FixedTimeStep);
	Obj->SetArrayField(TEXT("bodies"), DescribeBodies(PA));
	Obj->SetArrayField(TEXT("constraints"), DescribeConstraints(PA));
	Obj->SetArrayField(TEXT("collisions"), DescribeCollisions(PA));
	return ToJson(Obj);
}
