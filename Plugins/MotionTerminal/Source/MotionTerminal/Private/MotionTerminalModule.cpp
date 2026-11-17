#include "Modules/ModuleManager.h"

#include "AnimAssetTools.h"
#include "AnimGraphBuildTools.h"
#include "AnimGraphTools.h"
#include "AnimDebugTools.h"
#include "AnimImportTools.h"
#include "EditorSessionTools.h"
#include "ToolsetRegistry/UToolsetRegistry.h"

class FMotionTerminalModule : public IModuleInterface
{
public:
	virtual void StartupModule() override
	{
		UToolsetRegistry::RegisterToolsetClass(UAnimGraphTools::StaticClass());
		UToolsetRegistry::RegisterToolsetClass(UAnimDebugTools::StaticClass());
		UToolsetRegistry::RegisterToolsetClass(UAnimImportTools::StaticClass());
		UToolsetRegistry::RegisterToolsetClass(UEditorSessionTools::StaticClass());
		UToolsetRegistry::RegisterToolsetClass(UAnimGraphBuildTools::StaticClass());
		UToolsetRegistry::RegisterToolsetClass(UAnimAssetTools::StaticClass());
	}

	virtual void ShutdownModule() override
	{
		UEditorSessionTools::CloseAllStatWindows();
		UToolsetRegistry::UnregisterToolsetClass(UAnimGraphTools::StaticClass());
		UToolsetRegistry::UnregisterToolsetClass(UAnimDebugTools::StaticClass());
		UToolsetRegistry::UnregisterToolsetClass(UAnimImportTools::StaticClass());
		UToolsetRegistry::UnregisterToolsetClass(UEditorSessionTools::StaticClass());
		UToolsetRegistry::UnregisterToolsetClass(UAnimGraphBuildTools::StaticClass());
		UToolsetRegistry::UnregisterToolsetClass(UAnimAssetTools::StaticClass());
	}
};

IMPLEMENT_MODULE(FMotionTerminalModule, MotionTerminal)
