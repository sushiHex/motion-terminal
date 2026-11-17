#include "StatWindow.h"

#include "Stats/Stats.h"

#if STATS
#include "Stats/StatsData.h"
#include "Tasks/Pipe.h"

// Defined in Core's StatsSystemTypes.cpp and exported, but declared in no public header; the engine's own stat
// commands (StatsCommand.cpp) declare it the same way.
extern CORE_API UE::Tasks::FPipe GStatsPipe;
#endif

namespace MotionTerminal::Stats
{
#if STATS
	namespace
	{
		/** Runs Work on the stats pipe and blocks until it has run, so it sees the frame history the stats thread
		    owns without racing its updates. Must not be called from the pipe itself. */
		template <typename FWork>
		void RunOnStatsPipe(FWork&& Work)
		{
			GStatsPipe.Launch(UE_SOURCE_LOCATION, Forward<FWork>(Work)).Wait();
		}

		/** The newest frame every producer (game, render, end of pipe) has finished, as the stats thread sees it.
		    Stats pipe only. A frame number at or below this one can no longer receive packets from after now. */
		int64 NewestSettledFrame(const FStatsThreadState& State)
		{
			const int64 Finished = FMath::Min<int64>(State.CurrentGameFrame, State.CurrentRenderFrame,
				State.CurrentEndOfPipeFrame) - 1;
			return FMath::Max<int64>(State.GetLatestValidFrame(), Finished);
		}

		struct FAccum
		{
			double TotalMs = 0.0;
			double PeakMs = 0.0;
			int32 Frames = 0;
		};
	}
#endif

	FStatWindow::~FStatWindow()
	{
		End();
	}

	bool FStatWindow::Begin(const FString& InGroup)
	{
#if STATS
		// Reopening an open window gives its reference back first, so a window never holds more than one.
		End();

		StatsPrimaryEnableAdd();
		bActive = true;
		Group = InGroup;

		// The same call `stat group enable <Group>` reaches (StatsCommand.cpp -> StatGroupEnableManagerCommand). It
		// prefixes STATGROUP_ and, for a group not registered yet, remembers the enable for when it registers.
		IStatGroupEnableManager::Get().StatGroupEnableManagerCommand(FString::Printf(TEXT("enable %s"), *Group));

		int64 Start = -1;
		RunOnStatsPipe([&Start]
		{
			Start = NewestSettledFrame(FStatsThreadState::GetLocalState());
		});
		StartFrame = Start;
		return true;
#else
		Group = InGroup;
		StartFrame = -1;
		bActive = false;
		return false;
#endif
	}

	FWindowRead FStatWindow::Read(int32 MaxFrames) const
	{
		FWindowRead Out;
#if STATS
		Out.bActive = bActive;
		Out.bCollecting = FThreadStats::IsCollectingData();
		if (!bActive || MaxFrames <= 0)
		{
			return Out;
		}

		const FName GroupName(*(FString(TEXT("STATGROUP_")) + Group));
		// The first frame to settle after Begin may carry work from before it; read from the one after.
		const int64 FirstAllowed = StartFrame + 2;

		TMap<FName, FAccum> Accum;
		int32 Frames = 0;
		int64 FirstFrame = -1;
		int64 LastFrame = -1;

		RunOnStatsPipe([&]
		{
			const FStatsThreadState& State = FStatsThreadState::GetLocalState();
			const int64 Latest = State.GetLatestValidFrame();
			const int64 Oldest = FMath::Max<int64>(State.GetOldestValidFrame(), FirstAllowed);
			if (Latest < 0 || Oldest > Latest)
			{
				return;
			}

			TArray<FStatMessage> FrameStats;
			for (int64 Frame = Latest; Frame >= Oldest && Frames < MaxFrames; --Frame)
			{
				if (!State.IsFrameValid(Frame))
				{
					continue;   // a bad frame (enable change, missed advance) inside the history
				}

				FrameStats.Reset();
				// Flat inclusive cost per stat, summed over threads, recursion counted once; no non-stack stats.
				State.GetInclusiveAggregateStackStats(Frame, FrameStats, nullptr, false);
				for (const FStatMessage& Msg : FrameStats)
				{
					if (!Msg.NameAndInfo.GetFlag(EStatMetaFlags::IsCycle)
						|| Msg.NameAndInfo.GetField<EStatDataType>() != EStatDataType::ST_int64
						|| Msg.NameAndInfo.GetGroupName() != GroupName)
					{
						continue;
					}
					const double Ms = FPlatformTime::ToMilliseconds64(static_cast<uint64>(Msg.GetValue_Duration()));
					FAccum& A = Accum.FindOrAdd(Msg.NameAndInfo.GetShortName());
					A.TotalMs += Ms;
					A.PeakMs = FMath::Max(A.PeakMs, Ms);
					++A.Frames;
				}

				++Frames;
				LastFrame = (LastFrame < 0) ? Frame : LastFrame;   // walking newest first
				FirstFrame = Frame;
			}
		});

		Out.Frames = Frames;
		Out.FirstFrame = FirstFrame;
		Out.LastFrame = LastFrame;
		for (const TPair<FName, FAccum>& Pair : Accum)
		{
			FCycleStat& Stat = Out.Cycles.Add(Pair.Key.ToString());
			Stat.FramesPresent = Pair.Value.Frames;
			Stat.AvgMs = Pair.Value.Frames > 0 ? Pair.Value.TotalMs / Pair.Value.Frames : 0.0;
			Stat.PeakMs = Pair.Value.PeakMs;
		}
#else
		(void)MaxFrames;
#endif
		return Out;
	}

	void FStatWindow::End()
	{
		if (!bActive)
		{
			return;
		}
		bActive = false;
		StartFrame = -1;
		// The group stays enabled: another user (a stat HUD, another window) may rely on it.
		StatsPrimaryEnableSubtract();
	}
}
