#include "CoreMinimal.h"

#if WITH_AUTOMATION_TESTS

#include "Animation/AnimBlueprint.h"
#include "Animation/Skeleton.h"
#include "AnimGraphBuildTools.h"
#include "AnimGraphTools.h"
#include "Dom/JsonObject.h"
#include "HAL/FileManager.h"
#include "Misc/AutomationTest.h"
#include "Misc/Guid.h"
#include "Misc/PackageName.h"
#include "Misc/Paths.h"
#include "ObjectTools.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

// The acceptance fixture of docs/specs/2026-11-12-animgraph-build-describe-design.md, section 7, minus its Control Rig
// and RigidBodyWithControl nodes (they need a rig and a physics asset; the dynamics graph's own build covers them).
// Headless-safe: no PIE, no RHI. It needs the mannequin skeleton, like the other default-tier rows.
//
// What it must show, because a build that only LOOKS right is the failure this design exists for:
//   - CopyBone's copy flags, set true in the spec, are true in the editor node, on the pin, and in the COMPILED class
//     (they are pins shown by default, and the compiler reads the pin);
//   - the link set is exactly the spec's;
//   - each refusal refuses, and leaves nothing on disk.

namespace
{
	const TCHAR* const TemplateABP = TEXT("/Game/Characters/Mannequins/Anims/Unarmed/ABP_Unarmed");

	TSharedPtr<FJsonObject> Parse(const FString& Text)
	{
		TSharedPtr<FJsonObject> Obj;
		FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), Obj);
		return Obj;
	}

	FString FileOf(const FString& AssetPath)
	{
		return FPackageName::LongPackageNameToFilename(AssetPath, FPackageName::GetAssetPackageExtension());
	}

	/** Remove a fixture asset: the object if it is in memory, and its file whatever the object delete did. */
	void Remove(const FString& AssetPath)
	{
		if (UObject* Asset = FindObject<UAnimBlueprint>(nullptr, *(AssetPath + TEXT(".") + FPackageName::GetShortName(AssetPath))))
		{
			ObjectTools::ForceDeleteObjects({ Asset }, /*ShowConfirmation*/ false);
		}
		IFileManager::Get().Delete(*FileOf(AssetPath), /*RequireExists*/ false, /*EvenReadOnly*/ true);
	}

	/** The fixture spec. Mutate turns it into one of the refused variants. */
	FString Spec(const FString& Skeleton, const TCHAR* ExtraLink = TEXT(""), const TCHAR* CopyProperty = TEXT(""))
	{
		return FString::Printf(TEXT(R"json({
			"skeleton": "%s", "parent_class": "",
			"variables": [{"name": "LayerAlpha", "type": "float", "default": "0.25", "instance_editable": true},
			              {"name": "ClassOnly", "type": "bool", "default": "false"}],
			"nodes": [
				{"id": "in", "class": "/Script/AnimGraph.AnimGraphNode_LinkedInputPose"},
				{"id": "grip", "class": "/Script/AnimGraph.AnimGraphNode_SequenceEvaluator",
				 "properties": [["Sequence", "/Script/Engine.AnimSequence'/Game/Characters/Mannequins/Anims/Unarmed/MM_Idle.MM_Idle'"]]},
				{"id": "extra", "class": "/Script/AnimGraph.AnimGraphNode_SequenceEvaluator",
				 "properties": [["Sequence", "/Script/Engine.AnimSequence'/Game/Characters/Mannequins/Anims/Unarmed/MM_Idle.MM_Idle'"]]},
				{"id": "layer", "class": "/Script/AnimGraph.AnimGraphNode_LayeredBoneBlend", "poses": 2,
				 "properties": [["LayerSetup", "((BranchFilters=((BoneName=\"hand_l\",BlendDepth=0))),(BranchFilters=((BoneName=\"hand_r\",BlendDepth=0))))"],
				                ["CurveBlendOption", "UseBasePose"]]},
				{"id": "l2c", "class": "/Script/AnimGraph.AnimGraphNode_LocalToComponentSpace"},
				{"id": "copy", "class": "/Script/AnimGraph.AnimGraphNode_CopyBone",
				 "properties": [["SourceBone", "(BoneName=\"foot_l\")"], ["TargetBone", "(BoneName=\"ik_foot_l\")"],
				                ["bCopyTranslation", "True"], ["bCopyRotation", "True"],
				                ["ControlSpace", "BCS_ComponentSpace"]%s]},
				{"id": "c2l", "class": "/Script/AnimGraph.AnimGraphNode_ComponentToLocalSpace"}
			],
			"variable_gets": [],
			"links": [["in", "Pose", "layer", "BasePose"], ["grip", "Pose", "layer", "BlendPoses_0"],
			          ["extra", "Pose", "layer", "BlendPoses_1"], ["layer", "Pose", "l2c", "LocalPose"],
			          ["l2c", "ComponentPose", "copy", "ComponentPose"], ["copy", "Pose", "c2l", "ComponentPose"]%s],
			"output": ["c2l", "Pose"]
		})json"), *Skeleton, CopyProperty, ExtraLink);
	}

	const TSharedPtr<FJsonObject>* NodeById(const TSharedPtr<FJsonObject>& Described, const FString& Id)
	{
		const TArray<TSharedPtr<FJsonValue>>* Nodes = nullptr;
		if (!Described.IsValid() || !Described->TryGetArrayField(TEXT("nodes"), Nodes))
		{
			return nullptr;
		}
		for (const TSharedPtr<FJsonValue>& Value : *Nodes)
		{
			const TSharedPtr<FJsonObject>* Obj = nullptr;
			if (Value.IsValid() && Value->TryGetObject(Obj) && (*Obj)->GetStringField(TEXT("id")) == Id)
			{
				return Obj;
			}
		}
		return nullptr;
	}
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FAnimGraphBuildTest,
	"MotionTerminal.Verify.AnimGraphBuild",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::ProductFilter)

bool FAnimGraphBuildTest::RunTest(const FString& Parameters)
{
	const UAnimBlueprint* Template = LoadObject<UAnimBlueprint>(nullptr, TemplateABP);
	if (!TestNotNull(TEXT("the mannequin's ABP loads (its skeleton is the fixture's)"), Template)
		|| !TestNotNull(TEXT("it has a target skeleton"), Template->TargetSkeleton.Get()))
	{
		return false;
	}
	const FString Skeleton = Template->TargetSkeleton->GetOutermost()->GetName();
	const FString Run = FGuid::NewGuid().ToString(EGuidFormats::Digits).Left(8);
	const FString Path = TEXT("/Game/MT/Tmp_MT_AGB_") + Run;
	TArray<FString> Made = { Path };
	ON_SCOPE_EXIT
	{
		for (const FString& Each : Made)
		{
			Remove(Each);
		}
	};

	// --- the faithful build -----------------------------------------------------------------------------------
	const TSharedPtr<FJsonObject> Built = Parse(UAnimGraphBuildTools::BuildAnimBlueprint(Path, Spec(Skeleton)));
	if (!TestTrue(TEXT("the build reply parses"), Built.IsValid()))
	{
		return false;
	}
	if (!Built->GetBoolField(TEXT("ok")))
	{
		AddError(FString::Printf(TEXT("the fixture did not build: %s"), *Built->GetStringField(TEXT("error"))));
		return false;
	}
	TestFalse(TEXT("a build alone puts nothing on disk"), IFileManager::Get().FileExists(*FileOf(Path)));

	const FString Asked = TEXT(R"({"copy": ["bCopyTranslation", "bCopyRotation", "SourceBone", "TargetBone", "ControlSpace"],
		"layer": ["LayerSetup", "CurveBlendOption"]})");
	const TSharedPtr<FJsonObject> Described = Parse(UAnimGraphTools::DescribeAnimGraph(Path, Asked));
	if (!TestTrue(TEXT("the description parses and is ok"), Described.IsValid() && Described->GetBoolField(TEXT("ok"))))
	{
		return false;
	}
	const FString Status = Described->GetStringField(TEXT("compile_status"));
	TestTrue(FString::Printf(TEXT("compiled (status %s)"), *Status), Status == TEXT("clean") || Status == TEXT("warnings"));
	TestEqual(TEXT("no ambiguous ids"), Described->GetArrayField(TEXT("ambiguous_ids")).Num(), 0);
	TestEqual(TEXT("links: the spec's six and the output"), Described->GetArrayField(TEXT("links")).Num(), 7);
	// "instance_editable" is read from the compiled property: what a drive setting it on an instance meets
	TMap<FString, bool> Editable;
	for (const TSharedPtr<FJsonValue>& Value : Described->GetArrayField(TEXT("variables")))
	{
		bool bEditable = false;
		const TSharedPtr<FJsonObject> Var = Value->AsObject();
		if (Var.IsValid() && Var->TryGetBoolField(TEXT("instance_editable"), bEditable))
		{
			Editable.Add(Var->GetStringField(TEXT("name")), bEditable);
		}
	}
	TestTrue(TEXT("a variable declared instance-editable reads back so"), Editable.FindRef(TEXT("LayerAlpha")));
	TestTrue(TEXT("one not declared so reads back class-only"),
		Editable.Contains(TEXT("ClassOnly")) && !Editable.FindRef(TEXT("ClassOnly")));
	TestEqual(TEXT("nodes: the spec's seven and the root"), Described->GetArrayField(TEXT("nodes")).Num(), 8);

	if (const TSharedPtr<FJsonObject>* Copy = NodeById(Described, TEXT("copy")))
	{
		const TSharedPtr<FJsonObject> Editor = (*Copy)->GetObjectField(TEXT("editor"));
		const TSharedPtr<FJsonObject> Compiled = (*Copy)->GetObjectField(TEXT("compiled"));
		const TSharedPtr<FJsonObject> Unread = (*Copy)->GetObjectField(TEXT("unread"));
		TestTrue(TEXT("the copy node compiled into the class"), (*Copy)->GetBoolField(TEXT("compiled_node")));
		for (const TCHAR* Flag : { TEXT("bCopyTranslation"), TEXT("bCopyRotation") })
		{
			bool bEditor = false;
			TestTrue(FString::Printf(TEXT("editor %s"), Flag), Editor->TryGetBoolField(Flag, bEditor) && bEditor);
			// The value the design exists for: set in the struct, read by the compiler from the pin.
			bool bCompiled = false;
			FString Why;
			Unread->TryGetStringField(FString(TEXT("compiled:")) + Flag, Why);
			if (TestTrue(FString::Printf(TEXT("compiled %s was read (unread: %s)"), Flag, *Why),
				Compiled->TryGetBoolField(Flag, bCompiled)))
			{
				TestTrue(FString::Printf(TEXT("compiled %s"), Flag), bCompiled);
			}
		}
		const TSharedPtr<FJsonObject>* Bone = nullptr;
		TestTrue(TEXT("editor SourceBone"), Editor->TryGetObjectField(TEXT("SourceBone"), Bone)
			&& (*Bone)->GetStringField(TEXT("BoneName")) == TEXT("foot_l"));
		TestTrue(TEXT("compiled TargetBone"), Compiled->TryGetObjectField(TEXT("TargetBone"), Bone)
			&& (*Bone)->GetStringField(TEXT("BoneName")) == TEXT("ik_foot_l"));
		bool bPinTrue = false;
		for (const TSharedPtr<FJsonValue>& Pin : (*Copy)->GetArrayField(TEXT("pins")))
		{
			const TSharedPtr<FJsonObject> Obj = Pin->AsObject();
			if (Obj->GetStringField(TEXT("name")) == TEXT("bCopyTranslation"))
			{
				bPinTrue = Obj->GetStringField(TEXT("default")).Equals(TEXT("true"), ESearchCase::IgnoreCase);
			}
		}
		TestTrue(TEXT("the bCopyTranslation pin's default is true"), bPinTrue);
	}
	else
	{
		AddError(TEXT("the description has no node 'copy'"));
	}

	if (const TSharedPtr<FJsonObject>* Layer = NodeById(Described, TEXT("layer")))
	{
		int32 Poses = 0;
		for (const TSharedPtr<FJsonValue>& Pin : (*Layer)->GetArrayField(TEXT("pins")))
		{
			Poses += Pin->AsObject()->GetStringField(TEXT("name")).StartsWith(TEXT("BlendPoses_")) ? 1 : 0;
		}
		TestEqual(TEXT("two blend poses"), Poses, 2);
		// Both layers, in order, each with its one branch filter, depth included (a member the engine's text export
		// would drop for being the default).
		const TArray<TSharedPtr<FJsonValue>>* Setup = nullptr;
		if (TestTrue(TEXT("compiled LayerSetup is an array of two"),
			(*Layer)->GetObjectField(TEXT("compiled"))->TryGetArrayField(TEXT("LayerSetup"), Setup) && Setup->Num() == 2))
		{
			const TCHAR* Hands[] = { TEXT("hand_l"), TEXT("hand_r") };
			for (int32 i = 0; i < 2; ++i)
			{
				const TArray<TSharedPtr<FJsonValue>>* Filters = nullptr;
				const bool bOne = (*Setup)[i]->AsObject()->TryGetArrayField(TEXT("BranchFilters"), Filters)
					&& Filters->Num() == 1;
				TestTrue(FString::Printf(TEXT("layer %d has one branch filter"), i), bOne);
				if (bOne)
				{
					const TSharedPtr<FJsonObject> Filter = (*Filters)[0]->AsObject();
					TestEqual(FString::Printf(TEXT("layer %d bone"), i), Filter->GetStringField(TEXT("BoneName")),
						FString(Hands[i]));
					TestTrue(FString::Printf(TEXT("layer %d reports its depth"), i), Filter->HasField(TEXT("BlendDepth")));
				}
			}
		}
	}
	else
	{
		AddError(TEXT("the description has no node 'layer'"));
	}

	// Accepted, so saved: only now does the build reach disk.
	const TSharedPtr<FJsonObject> Saved = Parse(UAnimGraphBuildTools::SaveAnimBlueprint(Path));
	TestTrue(TEXT("the accepted build saves"), Saved.IsValid() && Saved->GetBoolField(TEXT("ok")));
	TestTrue(TEXT("a saved build is on disk"), IFileManager::Get().FileExists(*FileOf(Path)));
	const TSharedPtr<FJsonObject> NoSuch = Parse(UAnimGraphBuildTools::SaveAnimBlueprint(Path + TEXT("_none")));
	TestTrue(TEXT("saving what was never built is refused"), NoSuch.IsValid() && !NoSuch->GetBoolField(TEXT("ok")));

	// --- the refusals: each fails for its reason and leaves no file ------------------------------------------------
	struct FRefusal
	{
		const TCHAR* What;
		FString Path;
		FString SpecText;
		const TCHAR* Reason;
	};
	const TArray<FRefusal> Refusals = {
		{ TEXT("an existing asset"), Path, Spec(Skeleton), TEXT("already exists") },
		{ TEXT("two consumers of one pose"), Path + TEXT("_a"),
			Spec(Skeleton, TEXT(R"(, ["in", "Pose", "c2l", "LocalPose"])")), TEXT("two consumers") },
		{ TEXT("an input linked twice"), Path + TEXT("_b"),
			Spec(Skeleton, TEXT(R"(, ["grip", "Pose", "layer", "BasePose"])")), TEXT("linked twice") },
		{ TEXT("an unknown property"), Path + TEXT("_c"),
			Spec(Skeleton, TEXT(""), TEXT(R"(, ["NoSuchMember", "1"])")), TEXT("no property 'NoSuchMember'") },
		{ TEXT("a value the property refuses"), Path + TEXT("_d"),
			Spec(Skeleton, TEXT(""), TEXT(R"(, ["ControlSpace", "BCS_NotASpace"])")), TEXT("ControlSpace") },
		{ TEXT("an unknown pin"), Path + TEXT("_e"),
			Spec(Skeleton, TEXT(R"(, ["extra", "NoSuchPin", "copy", "Alpha"])")), TEXT("NoSuchPin") },
	};
	for (const FRefusal& Refusal : Refusals)
	{
		if (Refusal.Path != Path)
		{
			Made.Add(Refusal.Path);
		}
		const TSharedPtr<FJsonObject> Reply = Parse(UAnimGraphBuildTools::BuildAnimBlueprint(Refusal.Path, Refusal.SpecText));
		if (!TestTrue(FString::Printf(TEXT("%s: the reply parses"), Refusal.What), Reply.IsValid()))
		{
			continue;
		}
		const FString Error = Reply->HasField(TEXT("error")) ? Reply->GetStringField(TEXT("error")) : FString();
		TestFalse(FString::Printf(TEXT("%s is refused"), Refusal.What), Reply->GetBoolField(TEXT("ok")));
		TestTrue(FString::Printf(TEXT("%s is refused for its reason (got: %s)"), Refusal.What, *Error),
			Error.Contains(Refusal.Reason));
		if (Refusal.Path != Path)
		{
			TestFalse(FString::Printf(TEXT("%s leaves no file"), Refusal.What),
				IFileManager::Get().FileExists(*FileOf(Refusal.Path)));
		}
	}
	return true;
}

#endif // WITH_AUTOMATION_TESTS
