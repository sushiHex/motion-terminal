#pragma once

#include "ToolsetRegistry/ToolsetDefinition.h"
#include "EditorSessionTools.generated.h"

/** What a host-side driver needs from the running editor beyond Python: stat sampling windows and the editor's log
    file. Used by Scripts/editor_ops.py. */
UCLASS()
class UEditorSessionTools : public UToolsetDefinition
{
	GENERATED_BODY()

public:
	/** Opens a sampling window over a stat group, e.g. "Anim", under an id the caller chose (unique to it, so two
	    callers never share a window, and the caller can close it even if this reply was lost). Holds stats
	    collection on and enables the group until StatSampleEnd. Reopening an open id restarts it. Returns JSON
	    {"ok", "window", "group"}. */
	UFUNCTION(meta = (AICallable))
	static FString StatSampleBegin(const FString& Window, const FString& Group);

	/** The newest min(Frames, available) settled frames of the window. Frames arrive as the editor ticks: call again
	    until "frames" reaches what is needed (the history holds about 13). Returns JSON {"ok", "window", "group",
	    "collecting", "frames", "first_frame", "last_frame", "stats": {name: {"avg_ms", "peak_ms",
	    "frames_present"}}}; avg_ms is over the frames_present frames the stat ran in. */
	UFUNCTION(meta = (AICallable, AIAccessMode = "Read"))
	static FString StatSampleRead(const FString& Window, int32 Frames);

	/** Closes the window and gives its collection reference back; closing an unknown id is not an error. Returns
	    JSON {"ok", "window", "was_open"}. */
	UFUNCTION(meta = (AICallable))
	static FString StatSampleEnd(const FString& Window);

	/** The absolute path of the log file this editor writes (it honours -LOG= / -ABSLOG= and numbered fallbacks).
	    Returns JSON {"ok", "path"}. */
	UFUNCTION(meta = (AICallable, AIAccessMode = "Read"))
	static FString GetLogFile();

	/** Closes every open window: the module calls it at shutdown, while the stats system still exists. */
	static void CloseAllStatWindows();
};
