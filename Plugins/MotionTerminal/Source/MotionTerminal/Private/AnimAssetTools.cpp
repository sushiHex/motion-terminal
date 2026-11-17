#include "AnimAssetTools.h"

#include "Animation/AnimSequence.h"
#include "Animation/BlendSpace.h"
#include "Animation/Skeleton.h"
#include "AssetToolsModule.h"
#include "Factories/BlendSpaceFactoryNew.h"
#include "HeroToolJson.h"
#include "IAssetTools.h"
#include "Misc/PackageName.h"
#include "ReferenceSkeleton.h"
#include "Serialization/JsonReader.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"
#include "UObject/UnrealType.h"

#include UE_INLINE_GENERATED_CPP_BY_NAME(AnimAssetTools)

namespace
{
	const TCHAR* RetargetingName(EBoneTranslationRetargetingMode::Type Mode)
	{
		switch (Mode)
		{
		case EBoneTranslationRetargetingMode::Animation: return TEXT("Animation");
		case EBoneTranslationRetargetingMode::Skeleton: return TEXT("Skeleton");
		case EBoneTranslationRetargetingMode::AnimationScaled: return TEXT("AnimationScaled");
		case EBoneTranslationRetargetingMode::AnimationRelative: return TEXT("AnimationRelative");
		case EBoneTranslationRetargetingMode::OrientAndScale: return TEXT("OrientAndScale");
		default: return TEXT("Unknown");
		}
	}

	/** "/Game/Dir/Name" as package path and asset name; false for anything else. */
	bool SplitAssetPath(const FString& AssetPath, FString& OutPackagePath, FString& OutName)
	{
		return AssetPath.StartsWith(TEXT("/")) && AssetPath.Split(TEXT("/"), &OutPackagePath, &OutName,
			ESearchCase::CaseSensitive, ESearchDir::FromEnd) && !OutName.IsEmpty() && !OutName.Contains(TEXT("."));
	}

	/** The object an asset path names when it is ALREADY in memory; never loads. */
	UObject* InMemory(const FString& AssetPath)
	{
		FString PackagePath, Name;
		return SplitAssetPath(AssetPath, PackagePath, Name)
			? FindObject<UObject>(nullptr, *(AssetPath + TEXT(".") + Name)) : nullptr;
	}

	/** The blend space's axis parameters: a protected fixed array, reached by reflection. Null when the engine's
	    layout moved. */
	FBlendParameter* Axis(UBlendSpace& BlendSpace, int32 Index)
	{
		const FStructProperty* Prop = CastField<FStructProperty>(
			UBlendSpace::StaticClass()->FindPropertyByName(TEXT("BlendParameters")));
		if (!Prop || Prop->Struct != FBlendParameter::StaticStruct() || Index < 0 || Index >= Prop->ArrayDim)
		{
			return nullptr;
		}
		return Prop->ContainerPtrToValuePtr<FBlendParameter>(&BlendSpace, Index);
	}

	bool ReadAxis(const FJsonObject& Spec, const TCHAR* Field, FBlendParameter& Out, FString& OutError)
	{
		const TSharedPtr<FJsonObject>* Obj = nullptr;
		double Min = 0.0, Max = 0.0;
		int32 Grid = 0;
		FString Name;
		if (!Spec.TryGetObjectField(Field, Obj) || !(*Obj)->TryGetStringField(TEXT("name"), Name)
			|| !(*Obj)->TryGetNumberField(TEXT("min"), Min) || !(*Obj)->TryGetNumberField(TEXT("max"), Max)
			|| !(*Obj)->TryGetNumberField(TEXT("grid_divisions"), Grid))
		{
			OutError = FString::Printf(TEXT("%s needs name, min, max and grid_divisions"), Field);
			return false;
		}
		if (!(Max > Min) || Grid < 1)
		{
			OutError = FString::Printf(TEXT("%s: max must exceed min and grid_divisions be at least 1"), Field);
			return false;
		}
		Out.DisplayName = Name;
		Out.Min = static_cast<float>(Min);
		Out.Max = static_cast<float>(Max);
		Out.GridNum = Grid;
		(*Obj)->TryGetBoolField(TEXT("snap_to_grid"), Out.bSnapToGrid);
		(*Obj)->TryGetBoolField(TEXT("wrap_input"), Out.bWrapInput);
		return true;
	}

	TSharedRef<FJsonObject> AxisJson(const FBlendParameter& Axis)
	{
		const TSharedRef<FJsonObject> Obj = MakeShared<FJsonObject>();
		Obj->SetStringField(TEXT("name"), Axis.DisplayName);
		Obj->SetNumberField(TEXT("min"), Axis.Min);
		Obj->SetNumberField(TEXT("max"), Axis.Max);
		Obj->SetNumberField(TEXT("grid_divisions"), Axis.GridNum);
		Obj->SetBoolField(TEXT("snap_to_grid"), Axis.bSnapToGrid);
		Obj->SetBoolField(TEXT("wrap_input"), Axis.bWrapInput);
		return Obj;
	}

	/** Fill a new blend space from the spec. False with OutError; the caller owns what to do with the asset. */
	bool Fill(UBlendSpace& BlendSpace, const FJsonObject& Spec, FString& OutError)
	{
		FBlendParameter* X = Axis(BlendSpace, 0);
		FBlendParameter* Y = Axis(BlendSpace, 1);
		if (!X || !Y)
		{
			OutError = TEXT("the engine's blend-parameter layout changed: BlendParameters was not found as expected");
			return false;
		}
		BlendSpace.Modify();
		if (!ReadAxis(Spec, TEXT("axis_x"), *X, OutError) || !ReadAxis(Spec, TEXT("axis_y"), *Y, OutError))
		{
			return false;
		}

		const TArray<TSharedPtr<FJsonValue>>* Samples = nullptr;
		if (!Spec.TryGetArrayField(TEXT("samples"), Samples) || Samples->Num() == 0)
		{
			OutError = TEXT("the spec has no samples");
			return false;
		}
		for (const TSharedPtr<FJsonValue>& Item : *Samples)
		{
			const TSharedPtr<FJsonObject>* Obj = nullptr;
			FString AnimPath;
			double SampleX = 0.0, SampleY = 0.0;
			if (!Item.IsValid() || !Item->TryGetObject(Obj) || !(*Obj)->TryGetStringField(TEXT("animation"), AnimPath)
				|| !(*Obj)->TryGetNumberField(TEXT("x"), SampleX) || !(*Obj)->TryGetNumberField(TEXT("y"), SampleY))
			{
				OutError = TEXT("a sample needs animation, x and y");
				return false;
			}
			UAnimSequence* Sequence = LoadObject<UAnimSequence>(nullptr, *AnimPath);
			if (!Sequence)
			{
				OutError = FString::Printf(TEXT("sample animation '%s' did not load"), *AnimPath);
				return false;
			}
			// Checked here: AddSample does not refuse a position outside the axes, it WIDENS the axes to hold it
			// (BlendSpace.cpp, AddSample expanding the blend parameters), and the asset would no longer be the spec.
			if (SampleX < X->Min || SampleX > X->Max || SampleY < Y->Min || SampleY > Y->Max)
			{
				OutError = FString::Printf(TEXT("sample %s at (%g, %g) is outside the axes [%g, %g] x [%g, %g]"),
					*AnimPath, SampleX, SampleY, X->Min, X->Max, Y->Min, Y->Max);
				return false;
			}
			const int32 Before = BlendSpace.GetNumberOfBlendSamples();
			const int32 Index = BlendSpace.AddSample(Sequence, FVector(SampleX, SampleY, 0.0));
			if (Index == INDEX_NONE || BlendSpace.GetNumberOfBlendSamples() != Before + 1)
			{
				OutError = FString::Printf(
					TEXT("the blend space refused the sample %s at (%g, %g): wrong skeleton or a duplicate position"),
					*AnimPath, SampleX, SampleY);
				return false;
			}
			double RateScale = 1.0;
			if ((*Obj)->TryGetNumberField(TEXT("rate_scale"), RateScale))
			{
				// The sample array is protected; the one member a spec may carry beyond position is set by reflection.
				const FArrayProperty* Samples_ = CastField<FArrayProperty>(
					UBlendSpace::StaticClass()->FindPropertyByName(TEXT("SampleData")));
				const FStructProperty* Inner = Samples_ ? CastField<FStructProperty>(Samples_->Inner) : nullptr;
				if (!Inner || Inner->Struct != FBlendSample::StaticStruct())
				{
					OutError = TEXT("the engine's blend-sample layout changed: SampleData was not found as expected");
					return false;
				}
				FScriptArrayHelper Helper(Samples_, Samples_->ContainerPtrToValuePtr<void>(&BlendSpace));
				if (!Helper.IsValidIndex(Index))
				{
					OutError = TEXT("the added sample was not found in the sample array");
					return false;
				}
				reinterpret_cast<FBlendSample*>(Helper.GetRawPtr(Index))->RateScale = static_cast<float>(RateScale);
			}
		}

		bool bGrid = BlendSpace.bInterpolateUsingGrid;
		Spec.TryGetBoolField(TEXT("interpolate_using_grid"), bGrid);
		BlendSpace.bInterpolateUsingGrid = bGrid;

		BlendSpace.ValidateSampleData();
		BlendSpace.ResampleData();
		// A blend space that interpolates on its grid keeps no segments or triangles, by design.
		if (!bGrid && BlendSpace.GetBlendSpaceData().IsEmpty())
		{
			OutError = TEXT("the samples were accepted but no interpolation data was built from them");
			return false;
		}
		BlendSpace.MarkPackageDirty();
		return true;
	}
}

FString UAnimAssetTools::DescribeSkeleton(const FString& SkeletonPath)
{
	const USkeleton* Skeleton = LoadObject<USkeleton>(nullptr, *SkeletonPath);
	if (!Skeleton)
	{
		return HeroToolJson::Fail(FString::Printf(TEXT("skeleton '%s' did not load"), *SkeletonPath));
	}
	const FReferenceSkeleton& Ref = Skeleton->GetReferenceSkeleton();
	// The retargeting modes live in the skeleton's bone tree, which has no names: it is indexed like the reference
	// skeleton's raw bones. If the two ever differ in length, the modes cannot be attributed and nothing is reported.
	const FArrayProperty* Tree = CastField<FArrayProperty>(USkeleton::StaticClass()->FindPropertyByName(TEXT("BoneTree")));
	const int32 TreeNum = Tree ? FScriptArrayHelper(Tree, Tree->ContainerPtrToValuePtr<void>(Skeleton)).Num() : INDEX_NONE;
	if (TreeNum != Ref.GetRawBoneNum())
	{
		return HeroToolJson::Fail(FString::Printf(
			TEXT("%s: the bone tree has %d entries and the reference skeleton %d bones; modes cannot be matched to names"),
			*SkeletonPath, TreeNum, Ref.GetRawBoneNum()));
	}

	TArray<TSharedPtr<FJsonValue>> Bones;
	for (int32 Index = 0; Index < Ref.GetRawBoneNum(); ++Index)
	{
		const int32 Parent = Ref.GetRawParentIndex(Index);
		const TSharedRef<FJsonObject> Bone = MakeShared<FJsonObject>();
		Bone->SetStringField(TEXT("name"), Ref.GetRawRefBoneInfo()[Index].Name.ToString());
		Bone->SetNumberField(TEXT("index"), Index);
		Bone->SetStringField(TEXT("parent"), Parent == INDEX_NONE ? FString()
			: Ref.GetRawRefBoneInfo()[Parent].Name.ToString());
		Bone->SetStringField(TEXT("translation_retargeting"),
			RetargetingName(Skeleton->GetBoneTranslationRetargetingMode(Index)));
		Bones.Add(MakeShared<FJsonValueObject>(Bone));
	}
	const TSharedRef<FJsonObject> Reply = MakeShared<FJsonObject>();
	Reply->SetBoolField(TEXT("ok"), true);
	Reply->SetStringField(TEXT("path"), Skeleton->GetOutermost()->GetName());
	Reply->SetArrayField(TEXT("bones"), Bones);
	return HeroToolJson::ToJson(Reply);
}

FString UAnimAssetTools::BuildBlendSpace(const FString& AssetPath, const FString& SpecJson)
{
	TSharedPtr<FJsonObject> Spec;
	if (!FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(SpecJson), Spec) || !Spec.IsValid())
	{
		return HeroToolJson::Fail(TEXT("the spec is not a JSON object"));
	}
	FString PackagePath, Name;
	if (!SplitAssetPath(AssetPath, PackagePath, Name))
	{
		return HeroToolJson::Fail(FString::Printf(TEXT("'%s' is not a package path like /Game/Dir/Name"), *AssetPath));
	}
	if (FindPackage(nullptr, *AssetPath) || FPackageName::DoesPackageExist(AssetPath))
	{
		return HeroToolJson::Fail(FString::Printf(
			TEXT("%s already exists: a build never writes over an asset, delete it first"), *AssetPath));
	}
	FString SkeletonPath;
	Spec->TryGetStringField(TEXT("skeleton"), SkeletonPath);
	USkeleton* Skeleton = LoadObject<USkeleton>(nullptr, *SkeletonPath);
	if (!Skeleton)
	{
		return HeroToolJson::Fail(FString::Printf(TEXT("skeleton '%s' did not load"), *SkeletonPath));
	}

	UBlendSpaceFactoryNew* Factory = NewObject<UBlendSpaceFactoryNew>();
	Factory->TargetSkeleton = Skeleton;
	IAssetTools& AssetTools = FModuleManager::LoadModuleChecked<FAssetToolsModule>(TEXT("AssetTools")).Get();
	UBlendSpace* BlendSpace = Cast<UBlendSpace>(AssetTools.CreateAsset(Name, PackagePath, UBlendSpace::StaticClass(), Factory));
	if (!BlendSpace)
	{
		return HeroToolJson::Fail(FString::Printf(TEXT("the factory did not create %s"), *AssetPath));
	}

	FString Error;
	const bool bBuilt = Fill(*BlendSpace, *Spec, Error);
	const TSharedRef<FJsonObject> Reply = MakeShared<FJsonObject>();
	Reply->SetBoolField(TEXT("ok"), bBuilt);
	Reply->SetStringField(TEXT("path"), AssetPath);
	Reply->SetBoolField(TEXT("saved"), false);
	if (!bBuilt)
	{
		// In memory, unsaved: the caller deletes it (a delete inside the call that made it leaves a file behind).
		Reply->SetBoolField(TEXT("created"), true);
		Reply->SetStringField(TEXT("error"), Error);
	}
	return HeroToolJson::ToJson(Reply);
}

FString UAnimAssetTools::DescribeBlendSpace(const FString& AssetPath)
{
	UBlendSpace* BlendSpace = LoadObject<UBlendSpace>(nullptr, *AssetPath);
	if (!BlendSpace)
	{
		return HeroToolJson::Fail(FString::Printf(TEXT("blend space '%s' did not load"), *AssetPath));
	}
	const TSharedRef<FJsonObject> Reply = MakeShared<FJsonObject>();
	Reply->SetBoolField(TEXT("ok"), true);
	Reply->SetStringField(TEXT("path"), BlendSpace->GetOutermost()->GetName());
	const USkeleton* Skeleton = BlendSpace->GetSkeleton();
	Reply->SetStringField(TEXT("skeleton"), Skeleton ? Skeleton->GetOutermost()->GetName() : FString());
	Reply->SetObjectField(TEXT("axis_x"), AxisJson(BlendSpace->GetBlendParameter(0)));
	Reply->SetObjectField(TEXT("axis_y"), AxisJson(BlendSpace->GetBlendParameter(1)));

	TArray<TSharedPtr<FJsonValue>> Samples;
	for (const FBlendSample& Sample : BlendSpace->GetBlendSamples())
	{
		const TSharedRef<FJsonObject> Obj = MakeShared<FJsonObject>();
		Obj->SetStringField(TEXT("animation"), Sample.Animation ? Sample.Animation->GetPathName() : FString());
		Obj->SetNumberField(TEXT("x"), Sample.SampleValue.X);
		Obj->SetNumberField(TEXT("y"), Sample.SampleValue.Y);
		Obj->SetBoolField(TEXT("valid"), Sample.bIsValid);
		Obj->SetNumberField(TEXT("rate_scale"), Sample.RateScale);
		Samples.Add(MakeShared<FJsonValueObject>(Obj));
	}
	Reply->SetArrayField(TEXT("samples"), Samples);
	Reply->SetBoolField(TEXT("interpolate_using_grid"), BlendSpace->bInterpolateUsingGrid);
	// Segments or triangles exist. A blend space that interpolates on its grid keeps none by design, so this is
	// read together with interpolate_using_grid, never alone.
	Reply->SetBoolField(TEXT("triangulation_baked"), !BlendSpace->GetBlendSpaceData().IsEmpty());
	return HeroToolJson::ToJson(Reply);
}

FString UAnimAssetTools::SaveBuiltAsset(const FString& AssetPath)
{
	UObject* Asset = InMemory(AssetPath);
	if (!Asset)
	{
		return HeroToolJson::Fail(FString::Printf(
			TEXT("%s is not in memory: nothing was built there in this session"), *AssetPath));
	}
	UPackage* Package = Asset->GetOutermost();
	const FString File = FPackageName::LongPackageNameToFilename(Package->GetName(),
		FPackageName::GetAssetPackageExtension());
	FSavePackageArgs Args;
	Args.TopLevelFlags = RF_Public | RF_Standalone;
	Args.SaveFlags = SAVE_NoError;
	if (!UPackage::SavePackage(Package, Asset, *File, Args))
	{
		return HeroToolJson::Fail(FString::Printf(TEXT("%s was not saved to %s"), *Package->GetName(), *File));
	}
	const TSharedRef<FJsonObject> Reply = MakeShared<FJsonObject>();
	Reply->SetBoolField(TEXT("ok"), true);
	Reply->SetStringField(TEXT("path"), AssetPath);
	Reply->SetBoolField(TEXT("saved"), true);
	return HeroToolJson::ToJson(Reply);
}
