#include "RenderSessionTools.h"

#include "Editor.h"
#include "Engine/Engine.h"
#include "Engine/GameViewportClient.h"
#include "HeroToolJson.h"

#include UE_INLINE_GENERATED_CPP_BY_NAME(RenderSessionTools)

#define LOCTEXT_NAMESPACE "MotionTerminalRenderSession"

FString URenderSessionTools::SetEditorViewportsRealtime(bool bRealtime)
{
	if (!GEditor)
	{
		return HeroToolJson::Fail(TEXT("no editor"));
	}
	const FText Owner = LOCTEXT("RenderOverride", "MotionTerminal render");
	GEditor->RemoveViewportsRealtimeOverride(Owner);        // never stacked: one override, or none
	if (!bRealtime)
	{
		GEditor->SetViewportsRealtimeOverride(false, Owner);
	}
	const TSharedRef<FJsonObject> Obj = MakeShared<FJsonObject>();
	Obj->SetBoolField(TEXT("ok"), true);
	Obj->SetBoolField(TEXT("realtime"), bRealtime);
	return HeroToolJson::ToJson(Obj);
}

FString URenderSessionTools::SetPlayWorldRendering(bool bRender)
{
	if (!GEngine)
	{
		return HeroToolJson::Fail(TEXT("no engine"));
	}
	int32 Viewports = 0;
	for (const FWorldContext& Context : GEngine->GetWorldContexts())
	{
		if (Context.WorldType == EWorldType::PIE && Context.GameViewport)
		{
			Context.GameViewport->bDisableWorldRendering = !bRender;
			++Viewports;
		}
	}
	const TSharedRef<FJsonObject> Obj = MakeShared<FJsonObject>();
	Obj->SetBoolField(TEXT("ok"), true);
	Obj->SetBoolField(TEXT("render"), bRender);
	Obj->SetNumberField(TEXT("viewports"), Viewports);
	return HeroToolJson::ToJson(Obj);
}

#undef LOCTEXT_NAMESPACE
