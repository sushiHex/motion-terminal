#include "CoreMinimal.h"

#if WITH_AUTOMATION_TESTS

#include "Animation/AnimBlueprintGeneratedClass.h"
#include "Animation/AnimClassInterface.h"
#include "Animation/AnimInstance.h"
#include "Misc/AutomationTest.h"

// Headless automation-verification harness. Proves the CI-grade verification path
// end to end -- `Scripts/run_verification.py` runs UnrealEditor-Cmd headless, writes index.json,
// and reports real pass/fail parsed from it.
//
// This assertion is a TOPOLOGY check on the baked AnimGraph -- the seed of the golden-diff gate
// -- chosen because it is fully headless-safe (no PIE, no skinned mesh, no RHI). The
// PIE-runtime *weight* assertion (drive a var, assert a state transition) is not made
// here: it needs the clean ABP_Spike (a free `Speed` var + `Speed > 10` rule),
// plus headless-PIE tuning -- the ThirdPerson template's skinned mesh stalls at "0/1 ready" under
// -NullRHI and its CommonUI warning auto-fails an automation test.

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FAnimUnarmedTopologyTest,
	"MotionTerminal.Verify.UnarmedTopology",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::ProductFilter)

bool FAnimUnarmedTopologyTest::RunTest(const FString& Parameters)
{
	UClass* AnimClass = LoadClass<UAnimInstance>(nullptr,
		TEXT("/Game/Characters/Mannequins/Anims/Unarmed/ABP_Unarmed.ABP_Unarmed_C"));
	if (!TestNotNull(TEXT("ABP_Unarmed_C loads"), AnimClass)) { return false; }

	IAnimClassInterface* AnimClassInterface = IAnimClassInterface::GetFromClass(AnimClass);
	if (!TestNotNull(TEXT("class implements IAnimClassInterface"), AnimClassInterface)) { return false; }

	const TArray<FBakedAnimationStateMachine>& Machines = AnimClassInterface->GetBakedStateMachines();
	const FBakedAnimationStateMachine* Loco = nullptr;
	for (const FBakedAnimationStateMachine& M : Machines)
	{
		if (M.MachineName == FName(TEXT("Locomotion"))) { Loco = &M; break; }
	}
	if (!TestNotNull(TEXT("has a 'Locomotion' state machine"), Loco)) { return false; }

	TestTrue(TEXT("'Locomotion' machine has >= 2 states"), Loco->States.Num() >= 2);

	bool bHasIdle = false;
	for (int32 i = 0; i < Loco->States.Num(); ++i)
	{
		if (Loco->States[i].StateName == FName(TEXT("Idle"))) { bHasIdle = true; break; }
	}
	TestTrue(TEXT("'Locomotion' machine has an 'Idle' state"), bHasIdle);

	return true;
}

#endif // WITH_AUTOMATION_TESTS
