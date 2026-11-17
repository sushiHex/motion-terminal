#pragma once

#include "CoreMinimal.h"

namespace MotionTerminal::Stats
{
	/** One cycle stat over a window: AvgMs is over the frames it ran in (FramesPresent of them; the army budget's
	    thresholds were priced this way), PeakMs the largest single frame. A caller that needs the stat in every
	    frame compares FramesPresent with FWindowRead::Frames. */
	struct FCycleStat
	{
		double AvgMs = 0.0;
		double PeakMs = 0.0;
		int32 FramesPresent = 0;
	};

	struct FWindowRead
	{
		bool bActive = false;       // Begin succeeded (stats compiled in) and End has not run
		bool bCollecting = false;   // the engine is collecting stat data at all (primary enable)
		int32 Frames = 0;           // settled frames read, every one completed after Begin
		int64 FirstFrame = -1;
		int64 LastFrame = -1;
		TMap<FString, FCycleStat> Cycles;   // short stat name -> its cost over the frames read
	};

	/** A sampling window over one stat group, e.g. "Anim".

	    Begin takes one reference on the engine's primary stats enable (collection runs while any holder has one, a
	    stat HUD included) and enables the group's stats; End, or destruction, gives the reference back. The group's
	    stats stay enabled, since another user may want them. A frame settles only once every producer thread has
	    finished it (StatsData.cpp:932), so the frame that settles first after Begin may hold work from before it;
	    the window skips it and reads from the next one on.

	    Read runs its whole snapshot as one task on the stats thread's own pipe, so the frame history cannot change
	    under it, and copies the numbers out. Game thread only. Frames are produced as the engine ticks: a caller
	    that needs N frames reads again on a later tick, never by waiting inside one. */
	class FStatWindow
	{
	public:
		FStatWindow() = default;
		~FStatWindow();
		FStatWindow(const FStatWindow&) = delete;
		FStatWindow& operator=(const FStatWindow&) = delete;

		/** Opens (or reopens) the window. False when stats are not compiled into this build. */
		bool Begin(const FString& Group);
		/** The newest min(MaxFrames, available) frames completed since Begin. */
		FWindowRead Read(int32 MaxFrames) const;
		void End();
		bool IsActive() const { return bActive; }
		const FString& GetGroup() const { return Group; }

	private:
		FString Group;
		int64 StartFrame = -1;
		bool bActive = false;
	};
}
