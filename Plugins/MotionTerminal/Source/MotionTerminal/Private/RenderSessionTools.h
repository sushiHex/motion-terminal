#pragma once

#include "ToolsetRegistry/ToolsetDefinition.h"
#include "RenderSessionTools.generated.h"

/** What a Movie Render Queue session in the automation editor needs from the editor itself, which Python cannot reach.
    Used by Scripts/mrq_render.py around every render. */
UCLASS()
class URenderSessionTools : public UToolsetDefinition
{
	GENERATED_BODY()

public:
	/** Stops (false) or restores (true) every editor viewport's realtime rendering while a render runs, as entering
	    play-in-viewport does (UEditorEngine::SetViewportsRealtimeOverride). The Movie Render Queue renders in its own
	    PIE window and the level viewport kept drawing the editor world in every render frame: two scenes on the GPU,
	    each with its own virtual shadow map pool, Lumen scene and distance fields (rhi.DumpResourceMemory
	    mid-render). Restoring removes only this override. Not held for a whole session: idle, the paused viewports
	    saved 0.6% of the GPU (7.7 -> 7.1%). Returns JSON {"ok", "realtime"}. */
	UFUNCTION(BlueprintCallable, Category = "MotionTerminal|Render", meta = (AICallable))
	static FString SetEditorViewportsRealtime(bool bRealtime);

	/** Stops (false) or restores (true) the play world's drawing in every PIE game viewport
	    (UGameViewportClient::bDisableWorldRendering): the world still ticks, animates and simulates (cloth is not tied
	    to visibility, SkeletalMeshComponent.cpp:848; the drive's pawns always tick their pose), only nothing is drawn.
	    For a drive that measures and renders nothing (drawing is load the drive does not need, and it slows
	    everything else on the machine). Returns JSON {"ok", "render", "viewports"}. */
	UFUNCTION(BlueprintCallable, Category = "MotionTerminal|Render", meta = (AICallable))
	static FString SetPlayWorldRendering(bool bRender);
};
