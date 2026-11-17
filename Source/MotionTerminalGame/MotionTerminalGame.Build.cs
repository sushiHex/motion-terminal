using UnrealBuildTool;

public class MotionTerminalGame : ModuleRules
{
	public MotionTerminalGame(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
		PublicDependencyModuleNames.AddRange(new string[] { "Core", "CoreUObject", "Engine" });
		// AMTCharacter's budgeted mesh and UMTAnimBudgetLibrary. The plugin is enabled in MotionTerminal.uproject.
		PrivateDependencyModuleNames.Add("AnimationBudgetAllocator");
	}
}
