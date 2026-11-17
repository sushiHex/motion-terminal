#include "MTAnimBudgetLibrary.h"
#include "Misc/AutomationTest.h"

#if WITH_DEV_AUTOMATION_TESTS

// The update-rate gate (Pie.Army.BudgetThrottle) divides counter deltas by frames, so a wrong wrap turns one
// update into 65534 and a throttled ring into a hyperactive one. Pin every boundary the counter can cross.
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FAnimUpdateCountDeltaTest, "MotionTerminal.Verify.AnimUpdateCountDelta",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FAnimUpdateCountDeltaTest::RunTest(const FString& Parameters)
{
	TestEqual(TEXT("plain step"), UMTAnimBudgetLibrary::AnimUpdateCountDelta(0, 5), 5);
	TestEqual(TEXT("no change"), UMTAnimBudgetLibrary::AnimUpdateCountDelta(12, 12), 0);
	TestEqual(TEXT("over the skipped -1"), UMTAnimBudgetLibrary::AnimUpdateCountDelta(-2, 0), 1);
	TestEqual(TEXT("over the int16 wrap"), UMTAnimBudgetLibrary::AnimUpdateCountDelta(32767, -32768), 1);
	TestEqual(TEXT("over both"), UMTAnimBudgetLibrary::AnimUpdateCountDelta(32760, 3),
		7 /*to 32767*/ + 1 /*wrap*/ + 32766 /*-32768 to -2*/ + 1 /*skip -1*/ + 3 /*0 to 3*/);
	TestEqual(TEXT("a full cycle less one"), UMTAnimBudgetLibrary::AnimUpdateCountDelta(1, 0), 65534);
	TestEqual(TEXT("instance vanished: nothing measured"), UMTAnimBudgetLibrary::AnimUpdateCountDelta(12, -1), -1);
	TestEqual(TEXT("instance appeared: nothing measured"), UMTAnimBudgetLibrary::AnimUpdateCountDelta(-1, 12), -1);
	return true;
}

#endif
