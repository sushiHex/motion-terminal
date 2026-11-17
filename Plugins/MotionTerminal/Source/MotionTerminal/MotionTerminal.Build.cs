using UnrealBuildTool;

public class MotionTerminal : ModuleRules
{
	public MotionTerminal(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

		PrivateDependencyModuleNames.AddRange(new string[]
		{
			"Core",
			"CoreUObject",
			"Engine",
			"UnrealEd",
			"AnimGraph",        // graph/node types: read by ReadAnimGraph and DescribeAnimGraph, written by AnimGraphOps
			"AnimGraphRuntime", // FAnimNode_LayeredBoneBlend: the pose count AnimGraphOps grows
			"BlueprintGraph",   // UAnimGraphNode_* derive from UK2Node; node spawners, variable-get nodes
			"ToolsetRegistry",
			"Json",             // golden-diff topology serialization
			"JsonUtilities",    // DescribeAnimGraph: property values as typed JSON (FJsonObjectConverter)
			"AnimationBudgetAllocator", // Pie.Army.BudgetThrottle drives the allocator directly

			// Import-scale fix half (FixImportScale): drive a scale-corrected FBX import.
			"AssetTools",           // IAssetTools::ImportAssetTasks
			"InterchangeCore",      // UInterchangePipelineBase
			"InterchangeEngine",    // UInterchangePipelineStackOverride (AssetImportTask::Options)
			"InterchangePipelines", // UInterchangeGenericAssetsPipeline::ImportOffsetUniformScale

			// Hit reaction: a hero's ragdoll physics asset (HeroPhysicsTools).
			"PhysicsUtilities",     // FPhysicsAssetUtils::CreateNewBody / CreateNewConstraint
			"PhysicsCore",          // FKSphylElem / FKSphereElem aggregate geometry
			"AssetRegistry",

			// Dynamics at runtime (HeroDynamicsTools): cloth configs and interactors, the deformed vertices.
			"ChaosCloth",                       // UChaosClothConfig, UChaosClothingInteractor
			"ClothingSystemRuntimeCommon",      // UClothingAssetCommon::ClothConfigs
			"ClothingSystemRuntimeInterface",   // UClothingSimulationInteractor
			"RenderCore",                       // ENQUEUE_RENDER_COMMAND: the heat colours rewritten in place
			"RHI",                              // LockBuffer / UnlockBuffer
		});
	}
}
