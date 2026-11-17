using UnrealBuildTool;

public class MotionTerminalEditorTarget : TargetRules
{
	public MotionTerminalEditorTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Editor;
		DefaultBuildSettings = BuildSettingsVersion.Latest;
		IncludeOrderVersion = EngineIncludeOrderVersion.Latest;
		ExtraModuleNames.Add("MotionTerminalGame");
	}
}
