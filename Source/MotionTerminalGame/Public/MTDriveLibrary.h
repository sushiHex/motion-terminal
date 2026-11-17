#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "MTDriveLibrary.generated.h"

/**
 * Engine switches a Python PIE driver needs and cannot reach from Python.
 *
 * Fixed timestep: the C++ PIE rows pin FApp's fixed timestep (PieTests.cpp FFixedTimeStepScope), so every frame
 * advances game time by exactly 1/Hz regardless of wall clock. A Python driver gets the same determinism here.
 * It is also what makes a driver independent of the editor's background CPU throttle: unfocused, the editor
 * runs at a few frames per second, and without a fixed step each of those frames is a 0.3 s step.
 * A driver MUST restore the previous state when it finishes: read it with GetFixedTimeStep first.
 *
 * NOT here, deliberately: a stats read. Reading STATGROUP_Anim from a run_python call crashed the editor
 * (a cycle-only filtered read like PieTests.cpp ReadAnimGroupMs: a null read in Core a second after two
 * good reads, most likely racing the stats thread outside a latent command). Anim cost is read inside the C++
 * army rows, where the read sits mid-tick in a latent command.
 */
UCLASS()
class MOTIONTERMINALGAME_API UMTDriveLibrary : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	/** Pin (bFixed, 1/Hz) or release (bFixed false) FApp's fixed timestep. Hz <= 0 with bFixed is refused (returns false). */
	UFUNCTION(BlueprintCallable, Category = "MotionTerminal|Drive")
	static bool SetFixedTimeStep(bool bFixed, float Hz);

	/** The current state, to restore later. Hz is 1 / FApp::GetFixedDeltaTime(), or 0 when that delta is 0. */
	UFUNCTION(BlueprintPure, Category = "MotionTerminal|Drive")
	static void GetFixedTimeStep(bool& bFixed, float& Hz);
};
