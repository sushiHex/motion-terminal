using UnrealBuildTool;

public class MotionTerminalTarget : TargetRules
{
	public MotionTerminalTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Game;
		DefaultBuildSettings = BuildSettingsVersion.Latest;
		IncludeOrderVersion = EngineIncludeOrderVersion.Latest;
		ExtraModuleNames.Add("MotionTerminalGame");
	}
}
