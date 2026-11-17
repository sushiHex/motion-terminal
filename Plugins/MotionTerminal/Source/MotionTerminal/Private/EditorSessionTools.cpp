#include "EditorSessionTools.h"

#include "GenericPlatform/GenericPlatformOutputDevices.h"
#include "HAL/FileManager.h"
#include "HeroToolJson.h"
#include "StatWindow.h"

#include UE_INLINE_GENERATED_CPP_BY_NAME(EditorSessionTools)

namespace
{
	using MotionTerminal::Stats::FStatWindow;

	/** Open windows by caller-chosen id, kept between tool calls (each call is a fresh static function). Game thread
	    only, as every toolset call is. A window whose caller vanished stays open until the editor exits. */
	TMap<FString, TUniquePtr<FStatWindow>>& OpenWindows()
	{
		static TMap<FString, TUniquePtr<FStatWindow>> Map;
		return Map;
	}

	TSharedRef<FJsonObject> Reply(const FString& Window)
	{
		const TSharedRef<FJsonObject> Obj = MakeShared<FJsonObject>();
		Obj->SetBoolField(TEXT("ok"), true);
		Obj->SetStringField(TEXT("window"), Window);
		return Obj;
	}
}

FString UEditorSessionTools::StatSampleBegin(const FString& Window, const FString& Group)
{
	if (Window.IsEmpty() || Group.IsEmpty())
	{
		return HeroToolJson::Fail(TEXT("a window id and a group are both required"));
	}
	TUniquePtr<FStatWindow>& Slot = OpenWindows().FindOrAdd(Window);
	if (!Slot)
	{
		Slot = MakeUnique<FStatWindow>();
	}
	if (!Slot->Begin(Group))
	{
		OpenWindows().Remove(Window);
		return HeroToolJson::Fail(TEXT("stats are not compiled into this build"));
	}
	const TSharedRef<FJsonObject> Obj = Reply(Window);
	Obj->SetStringField(TEXT("group"), Group);
	return HeroToolJson::ToJson(Obj);
}

FString UEditorSessionTools::StatSampleRead(const FString& Window, int32 Frames)
{
	const TUniquePtr<FStatWindow>* Slot = OpenWindows().Find(Window);
	if (!Slot || !(*Slot)->IsActive())
	{
		return HeroToolJson::Fail(FString::Printf(TEXT("no open window '%s' (StatSampleBegin first)"), *Window));
	}
	const MotionTerminal::Stats::FWindowRead Read = (*Slot)->Read(Frames);
	const TSharedRef<FJsonObject> Obj = Reply(Window);
	Obj->SetStringField(TEXT("group"), (*Slot)->GetGroup());
	Obj->SetBoolField(TEXT("collecting"), Read.bCollecting);
	Obj->SetNumberField(TEXT("frames"), Read.Frames);
	Obj->SetNumberField(TEXT("first_frame"), static_cast<double>(Read.FirstFrame));
	Obj->SetNumberField(TEXT("last_frame"), static_cast<double>(Read.LastFrame));
	const TSharedRef<FJsonObject> Stats = MakeShared<FJsonObject>();
	for (const TPair<FString, MotionTerminal::Stats::FCycleStat>& Pair : Read.Cycles)
	{
		const TSharedRef<FJsonObject> Stat = MakeShared<FJsonObject>();
		Stat->SetNumberField(TEXT("avg_ms"), Pair.Value.AvgMs);
		Stat->SetNumberField(TEXT("peak_ms"), Pair.Value.PeakMs);
		Stat->SetNumberField(TEXT("frames_present"), Pair.Value.FramesPresent);
		Stats->SetObjectField(Pair.Key, Stat);
	}
	Obj->SetObjectField(TEXT("stats"), Stats);
	return HeroToolJson::ToJson(Obj);
}

FString UEditorSessionTools::StatSampleEnd(const FString& Window)
{
	TUniquePtr<FStatWindow> Slot;
	const bool bWasOpen = OpenWindows().RemoveAndCopyValue(Window, Slot) && Slot && Slot->IsActive();
	const TSharedRef<FJsonObject> Obj = Reply(Window);
	Obj->SetBoolField(TEXT("was_open"), bWasOpen);
	return HeroToolJson::ToJson(Obj);   // Slot's destructor gives the collection reference back
}

void UEditorSessionTools::CloseAllStatWindows()
{
	OpenWindows().Empty();   // each window's destructor gives its reference back
}

FString UEditorSessionTools::GetLogFile()
{
	const FString Path = IFileManager::Get().ConvertToAbsolutePathForExternalAppForRead(
		*FGenericPlatformOutputDevices::GetAbsoluteLogFilename());
	const TSharedRef<FJsonObject> Obj = MakeShared<FJsonObject>();
	Obj->SetBoolField(TEXT("ok"), true);
	Obj->SetStringField(TEXT("path"), Path);
	return HeroToolJson::ToJson(Obj);
}
