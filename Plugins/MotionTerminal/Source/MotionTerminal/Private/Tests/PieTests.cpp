#include "CoreMinimal.h"
#include "TestJsonRow.h"

#if WITH_AUTOMATION_TESTS && WITH_EDITOR

#include "AnimIntrospection.h"          // MotionTerminal::GatherFlatNodeWeights / FormatFlatNodeWeights (Mechanism A)
#include "Animation/AnimBlueprintGeneratedClass.h"
#include "Animation/AnimClassInterface.h"
#include "Animation/AnimInstance.h"
#include "Animation/AnimMontage.h"     // UAnimMontage / FAnimMontageInstance::GetWeight (Mechanism C)
#include "Animation/AnimSequence.h"    // the GAS walk/run clips played as dynamic montages
#include "AssetCompilingManager.h"     // FinishAllCompilation before any PIE row samples a pose
#include "Animation/AnimNodeBase.h"    // FNodeDebugData / FFlattenedDebugData (Mechanism A)
#include "Components/SkeletalMeshComponent.h"
#include "Components/StaticMeshComponent.h"  // MT_Weapon-tagged weapon component + socket reach (hand-IK test)
#include "GameFramework/CharacterMovementComponent.h"  // IsFalling / Velocity (airborne gate)
#include "Editor.h"
#include "EngineUtils.h"
#include "GameFramework/Character.h"
#include "GameFramework/Pawn.h"
#include "GameFramework/PlayerController.h"
#include "HAL/IConsoleManager.h"
#include "AnimationBudgetAllocatorParameters.h"   // Pie.Army.BudgetThrottle
#include "IAnimationBudgetAllocator.h"
#include "SkeletalMeshComponentBudgeted.h"
#include "Misc/App.h"                  // FApp fixed-time-step — a pinned frame basis for the pop detector
#include "Misc/AutomationTest.h"
#include "Templates/Function.h"        // TFunction — the RunGatedPieSubTest command factory
#include <initializer_list>             // FlatHasNode candidate-substring set
#include "Tests/AutomationEditorCommon.h"
#include "UObject/StrongObjectPtr.h"   // keep the montage alive across latent ticks
#include "UObject/UObjectGlobals.h"    // LoadObject
// Per-rig PIE fixture manifest (pie_rigs.json) parsing.
#include "Dom/JsonObject.h"
#include "Misc/FileHelper.h"
#include "Misc/PackageName.h"
#include "Misc/Paths.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
// Crowd-scale budget assertion. Reads STATGROUP_Anim (PerformAnimEvaluation_WorkerThread /
// AnimGameThreadTime) through MotionTerminal::Stats::FStatWindow, the same window the editor session tools
// serve, so the automation test measures the SAME counters the budget was priced against. STATS is defined in
// Development editor builds (what --tier pie runs) but NOT Shipping/Test; every use below is #if STATS-gated.
#include "StatWindow.h"

// The baseline PIE-RUNTIME weight assertion.
// Runs in the `pie` tier ONLY (real RHI): under -NullRHI the ThirdPerson skinned mesh
// stalls at "0/1 ready". The CommonUI viewport check is disabled via CVar below
// (the documented workaround) so its warning cannot auto-fail the test.

namespace
{
	// Baked-index trap (shared by both commands): resolve a state index by NAME against the BAKED
	// machine — the runtime machine index is never a baked-array index.
	int32 FindBakedStateIndex(const FBakedAnimationStateMachine& Baked, const TCHAR* StateName)
	{
		for (int32 i = 0; i < Baked.States.Num(); ++i)
		{
			if (Baked.States[i].StateName == FName(StateName)) { return i; }
		}
		return INDEX_NONE;
	}

	// Mechanism B: resolve a BAKED state machine by NAME on an AnimInstance. Returns the baked
	// machine (or nullptr) and sets OutMachineIdx. This is the baked-index trap — the runtime machine index
	// (OutMachineIdx) is NOT a baked-array index; states must be resolved by NAME against the returned baked
	// machine via FindBakedStateIndex. Also resolves a NESTED machine (a state machine inside a state's inner
	// graph, e.g. TurnInPlace inside Idle): the nested SM is compiled into its OWN entry in the generated
	// class's baked-machine array with its own runtime machine index — GetStateMachineIndex resolves it by name
	// and GetBakedStateMachines carries it (verified live: TurnInPlace resolved at index 9 alongside Locomotion
	// at 7).
	const FBakedAnimationStateMachine* ResolveBakedMachineByName(UAnimInstance& Inst, const TCHAR* MachineName, int32& OutMachineIdx)
	{
		OutMachineIdx = Inst.GetStateMachineIndex(FName(MachineName));
		if (OutMachineIdx == INDEX_NONE) { return nullptr; }
		IAnimClassInterface* AnimClass = IAnimClassInterface::GetFromClass(Inst.GetClass());
		if (!AnimClass) { return nullptr; }
		for (const FBakedAnimationStateMachine& M : AnimClass->GetBakedStateMachines())
		{
			if (M.MachineName == FName(MachineName)) { return &M; }
		}
		return nullptr;
	}

	// Factored (rule of three): resolve the "Locomotion" BAKED state machine on an
	// AnimInstance — the ubiquitous case. A thin forwarder over ResolveBakedMachineByName(TEXT("Locomotion")).
	// Same baked-index trap: OutMachineIdx is the RUNTIME machine index, NOT a baked-array index; states must be
	// resolved by NAME against the returned baked machine via FindBakedStateIndex.
	const FBakedAnimationStateMachine* ResolveLocomotionBaked(UAnimInstance& Inst, int32& OutMachineIdx)
	{
		return ResolveBakedMachineByName(Inst, TEXT("Locomotion"), OutMachineIdx);
	}

	// The shared possessed-pawn -> Locomotion-baked-machine preamble every possessed-pawn latent
	// command repeats (first factored out of FTurnInPlace / FAimOffset / FHandReachesSocket). Resolves the PIE world -> first PC -> possessed
	// pawn -> skeletal mesh -> anim instance -> the Locomotion baked machine (baked-index trap: OutLocoIdx is the RUNTIME
	// machine index; resolve states by NAME via FindBakedStateIndex). Returns false if ANY link is null — the
	// callers then fall through to their per-phase budget/backstop exactly as the inline `if (Mesh && Inst &&
	// Loco)` / `if (Pawn && Mesh && Inst && Loco)` guard did.
	static bool ResolvePossessedLocomotionContext(APawn*& OutPawn, USkeletalMeshComponent*& OutMesh,
		UAnimInstance*& OutInst, int32& OutLocoIdx, const FBakedAnimationStateMachine*& OutBaked)
	{
		FWorldContext* Pie = GEditor ? GEditor->GetPIEWorldContext() : nullptr;
		UWorld* World = Pie ? Pie->World() : nullptr;
		APlayerController* PC = World ? World->GetFirstPlayerController() : nullptr;
		OutPawn = PC ? PC->GetPawn() : nullptr;
		OutMesh = OutPawn ? OutPawn->FindComponentByClass<USkeletalMeshComponent>() : nullptr;
		OutInst = OutMesh ? OutMesh->GetAnimInstance() : nullptr;
		OutLocoIdx = INDEX_NONE;
		OutBaked = OutInst ? ResolveLocomotionBaked(*OutInst, OutLocoIdx) : nullptr;
		return OutPawn && OutMesh && OutInst && OutBaked;
	}

	// The shared per-phase-budget tail the three turn/aim/hand latent commands repeat — if the current phase
	// has run past its budget (default 20s, measured from the phase start), AddError with the phase name + the
	// command's own "never developed" noun and return true (the caller ends the test); otherwise return false
	// (keep polling). Preserves each command's exact "Timed out (20s) in <phase> phase (... or the <what> never
	// developed)" message; the 20s budgets and phase logic are unchanged.
	static bool PhaseBudgetExceeded(FAutomationTestBase* Test, double Now, double StartTime,
		const TCHAR* PhaseName, const TCHAR* NeverDevelopedWhat, double BudgetSec = 20.0)
	{
		if ((Now - StartTime) > BudgetSec)
		{
			Test->AddError(FString::Printf(
				TEXT("Timed out (%.0fs) in %s phase (pawn/anim did not resolve, never settled in Idle, or the %s never developed)"),
				BudgetSec, PhaseName, NeverDevelopedWhat));
			return true;
		}
		return false;
	}

	// ---- Per-rig PIE fixture (Plugins/MotionTerminal/Tests/pie_rigs.json) --------------
	// The RigLocomotion + FootIKNodeIntegration complex tests enumerate rows from this manifest. Each row's
	// map has a GameMode override that auto-possesses that rig's pawn on the SHARED ABP_MT_Locomotion_C
	// (the zero-retarget claim), so the SAME latent commands (which pin the POSSESSED pawn) drive each rig.
	struct FPieRigRow
	{
		FString Rig;
		FString Map;
		bool bFootIKNode = false;
		bool bTurnInPlace = false;   // opt this rig into Pie.TurnInPlace (nested TurnInPlace SM)
		bool bAimOffset = false;     // opt this rig into Pie.AimOffset (RotationOffsetBlendSpace)
		bool bHandIK = false;        // opt this rig into Pie.HandReachesSocket (TwoBoneIK hand_l)
		bool bFootIKGate = false;    // opt this rig into Pie.SkeletonAwareFootIK (the gate's both-branch assertion)
		bool bLocalOnly = false;
	};

	// Loads pie_rigs.json into OutRows. Returns false + OutError on read/parse/duplicate-rig failure.
	// GetTests (both complex tests below) checks this return value AND Rows.Num(): on any failure it
	// emits ONE synthetic "_ManifestLoadFailed" sentinel row (NEVER zero rows), whose RunTest
	// immediately AddErrors with the embedded OutError and fails. That is the actual "loud" mechanism —
	// a missing/malformed/empty-rigs/duplicate-rig manifest becomes a RED named row at the pie tier,
	// never a silent zero-subtest vanish that lets the tier report green.
	bool LoadPieRigs(TArray<FPieRigRow>& OutRows, FString& OutError)
	{
		TSharedPtr<FJsonObject> Root;
		if (!MotionTerminalTestJson::LoadManifestRoot(TEXT("pie_rigs.json"), Root, OutError)) { return false; }
		const TArray<TSharedPtr<FJsonValue>>* Rigs = nullptr;
		if (!MotionTerminalTestJson::RequireArrayField(Root, TEXT("rigs"), TEXT("pie_rigs.json"), Rigs, OutError)) { return false; }
		int32 RowIndex = -1;
		for (const TSharedPtr<FJsonValue>& V : *Rigs)
		{
			++RowIndex;
			const TSharedPtr<FJsonObject>* Obj = nullptr;
			// A ROW THAT CANNOT BE READ DELETES A RIG'S ENTIRE PIE COVERAGE, SILENTLY. This used to be
			// `continue` plus `if (!Rig.IsEmpty() && !Map.IsEmpty()) Add;` - so a row that lost its 'map'
			// key was dropped, every sub-test GetTests would have registered for that rig simply never
			// existed, and the _ManifestLoadFailed sentinel could not save it because that fires only on
			// ZERO rows. N-1 rows reports green with a rig's assertions gone - verbatim the failure the
			// sentinel's own comment says it exists to prevent. ClipVaries was repaired for this
			// first and its sibling here was not; both now route through the same [[nodiscard]] readers.
			if (!MotionTerminalTestJson::RequireObject(V, TEXT("pie_rigs.json"), RowIndex, Obj, OutError)) { return false; }
			FPieRigRow Row;
			if (!MotionTerminalTestJson::RequireString(*Obj, TEXT("rig"), TEXT("pie_rigs.json"), RowIndex, Row.Rig, OutError)) { return false; }
			if (!MotionTerminalTestJson::RequireString(*Obj, TEXT("map"), TEXT("pie_rigs.json"), RowIndex, Row.Map, OutError)) { return false; }
			(*Obj)->TryGetBoolField(TEXT("footIKNode"), Row.bFootIKNode);
			(*Obj)->TryGetBoolField(TEXT("turnInPlace"), Row.bTurnInPlace);
			// Parse the aimOffset field (previously an unparsed placeholder in the manifest schema).
			// A row's aimOffset:true opts it into FPieAimOffsetTest below.
			(*Obj)->TryGetBoolField(TEXT("aimOffset"), Row.bAimOffset);
			// Parse the handIK field — RESOLVES the schemaVersion-2 placeholder (the field
			// existed in pie_rigs.json long before it was parsed). A row's handIK:true opts it into
			// FPieHandReachesSocketTest below.
			(*Obj)->TryGetBoolField(TEXT("handIK"), Row.bHandIK);
			// A row's footIKGate:true opts it into FPieSkeletonAwareFootIKTest below.
			(*Obj)->TryGetBoolField(TEXT("footIKGate"), Row.bFootIKGate);
			(*Obj)->TryGetBoolField(TEXT("localOnly"), Row.bLocalOnly);
			OutRows.Add(Row);
		}

		// Duplicate 'rig' names would collide beautified test names in GetTests (both complex tests
		// below key each sub-test's display name off Row.Rig) — treat as a load failure so it rides
		// the same sentinel path as a missing/malformed manifest.
		TArray<FString> SeenRigNames;
		for (const FPieRigRow& Row : OutRows)
		{
			if (SeenRigNames.Contains(Row.Rig))
			{
				OutError = FString::Printf(TEXT("pie_rigs.json has duplicate rig name '%s'"), *Row.Rig);
				return false;
			}
			SeenRigNames.Add(Row.Rig);
		}
		return true;
	}

	// Prefix embedded in a synthetic GetTests row's Parameters when LoadPieRigs fails (or yields zero
	// rows). RunTest checks for this prefix and AddErrors with the tail (the embedded LoadPieRigs
	// OutError) instead of trying to drive PIE off a bogus "map path".
	const FString kManifestErrorPrefix = TEXT("<manifest-error>");

	// FootIKNodeIntegration-specific sentinel: the manifest loaded fine (rows > 0)
	// but ZERO rows opted into footIKNode — so the footIKNode filter would add no sub-tests and the
	// hero conformance test would silently vanish while the pie tier stayed green (a typo of the
	// footIKNode key defaults every row to false). Distinct from kManifestErrorPrefix (which means the
	// manifest itself failed to load) so the RunTest message is accurate — the manifest DID load here.
	const FString kNoFootIKRowsPrefix = TEXT("<no-footik-rows>");

	// TurnInPlace analogue of kNoFootIKRowsPrefix: the manifest loaded fine but ZERO rows opted into
	// turnInPlace, so FPieTurnInPlaceTest would register no sub-tests and silently vanish while the pie
	// tier stayed green. Emit a RED _NoTurnRows sentinel instead. Distinct from kManifestErrorPrefix.
	const FString kNoTurnRowsPrefix = TEXT("<no-turn-rows>");

	// AimOffset analogue: the manifest loaded fine but ZERO rows opted into aimOffset, so
	// FPieAimOffsetTest would register no sub-tests and silently vanish while the pie tier stayed green.
	// Emit a RED _NoAimRows sentinel instead. Distinct from kManifestErrorPrefix.
	const FString kNoAimRowsPrefix = TEXT("<no-aim-rows>");

	// HandIK analogue: the manifest loaded fine but ZERO rows opted into handIK, so
	// FPieHandReachesSocketTest would register no sub-tests and silently vanish while the pie tier stayed
	// green. Emit a RED _NoHandIKRows sentinel instead. Distinct from kManifestErrorPrefix.
	const FString kNoHandIKRowsPrefix = TEXT("<no-handik-rows>");

	// SkeletonAwareFootIK analogue: the manifest loaded fine but ZERO rows opted into footIKGate, so
	// FPieSkeletonAwareFootIKTest would register no sub-tests and silently vanish while the pie tier
	// stayed green. Emit a RED _NoFootIKGateRows sentinel instead. Distinct from kManifestErrorPrefix.
	const FString kNoFootIKGateRowsPrefix = TEXT("<no-footikgate-rows>");

	// Shared by both complex tests' GetTests: emit ONE named sentinel row instead of zero rows, so a
	// corrupt/missing pie_rigs.json fails loud (RED) rather than silently vanishing both hero tests
	// while the pie tier still reports green.
	void EmitManifestErrorSentinel(TArray<FString>& OutBeautifiedNames, TArray<FString>& OutTestCommands, const FString& Err)
	{
		OutBeautifiedNames.Add(TEXT("_ManifestLoadFailed"));
		OutTestCommands.Add(kManifestErrorPrefix + Err);
	}

	// Per-map skip decision (machine-local semantics, same as ImportScale/ClipVaries): a localOnly row
	// whose map package is absent SKIPs (fresh clones stay green); a NON-local absent map is a real defect.
	enum class EPieMapGate : uint8 { Proceed, Skip, Missing };
	EPieMapGate GatePieMap(const FString& MapPath)
	{
		if (FPackageName::DoesPackageExist(MapPath)) { return EPieMapGate::Proceed; }
		TArray<FPieRigRow> Rows; FString Err;
		bool bLocalOnly = false;
		if (LoadPieRigs(Rows, Err))
		{
			if (const FPieRigRow* R = Rows.FindByPredicate([&](const FPieRigRow& X) { return X.Map == MapPath; }))
			{
				bLocalOnly = R->bLocalOnly;
			}
		}
		return bLocalOnly ? EPieMapGate::Skip : EPieMapGate::Missing;
	}

	// The ONE ceiling shared by every pawn-relative foot-swing assertion in
	// this file, so the two that exist cannot drift apart or be anchored on different quantities. Its full
	// derivation, its single supporting observation, and the failure mode it does NOT catch are documented
	// at FSkeletonAwareFootIKCommand::kFootSwingMax, which forwards to this.
	//
	// All figures pawn-relative (foot.Z - pawnRootZ) over a gated flat-lane window, harvested from every
	// automation report on disk carrying these rows:
	//   healthy                                          31.02 .. 57.14 cm
	//   degenerate-LOW  (frozen feet)                     0.01 ..  0.83 cm  -> the FLOOR catches this
	//   degenerate-HIGH (one report, 9 samples)          92.08 .. 93.59 cm  -> only a CEILING can
	// THE FLOOR LIVES HERE TOO. Hoisting only the CEILING (for the stated reason "so the two pawn-relative
	// swing assertions in this file cannot drift apart") once left `kFootSwingMin = 8.0` defined twice,
	// once per command: the same drift risk, half the fix. Its derivation, unchanged: the fixed rigs
	// measure per-foot ranges ~25-40 cm on the gated flat-lane window while a frozen foot measures
	// ~0.03 cm, so 8.0 sits ~3x below the
	// smallest real swing and ~250x above the broken one — fixed from that prior A/B, not tuned to
	// either test's own numbers. What it does NOT catch is documented at
	// FRigLocomotionCommand::kFootSwingMin (an unreachable IK effector drags a leg at full extension and
	// reads 33-35 cm, comfortably over this floor; Verify.IKEffectorDrives is the gate for that).
	constexpr double kFootSwingMinCm = 8.0;

	// 75.0 is ~the geometric midpoint of 57.14 and 92.08: 1.31x the largest healthy value ever recorded
	// and 0.81x the smallest degenerate-high one. The healthy upper edge is set by this file's gate-OFF
	// arm (foot_r, which runs consistently ~6 cm hotter than the ON arm because the Control Rig is not
	// there to refine it) and it WANDERS run to run — 53.33, 54.14, 54.81, 55.05, 56.88, 57.14 across the
	// runs on disk — so the margin is real but not generous. Re-derive if it ever creeps past ~65.
	constexpr double kFootSwingMaxCm = 75.0;

	class FWaitForIdleWeightCommand : public IAutomationLatentCommand
	{
	public:
		explicit FWaitForIdleWeightCommand(FAutomationTestBase* InTest)
			: Test(InTest), StartTime(0.0) {}

		virtual bool Update() override
		{
			// Lazy-init: the constructor runs at ENQUEUE (before the map loads), so timing
			// from there would spend the budget on map load. Start the clock on first poll.
			if (StartTime == 0.0) { StartTime = FPlatformTime::Seconds(); }

			FWorldContext* Pie = GEditor ? GEditor->GetPIEWorldContext() : nullptr;
			UWorld* World = Pie ? Pie->World() : nullptr;
			if (World)
			{
				// Intentional scan-all: this baseline assertion is "ANY pawn settled in Idle" on the
				// stock ThirdPerson map (the driven command below pins the possessed pawn).
				for (TActorIterator<APawn> It(World); It; ++It)
				{
					USkeletalMeshComponent* Mesh = It->FindComponentByClass<USkeletalMeshComponent>();
					UAnimInstance* Inst = Mesh ? Mesh->GetAnimInstance() : nullptr;
					if (!Inst) { continue; }

					// Baked-index trap: resolve the Locomotion baked machine by NAME (factored helper;
					// runtime machine index is never a baked-array index).
					int32 MachineIdx = INDEX_NONE;
					const FBakedAnimationStateMachine* Baked = ResolveLocomotionBaked(*Inst, MachineIdx);
					if (!Baked) { continue; }

					const int32 IdleIdx = FindBakedStateIndex(*Baked, TEXT("Idle"));
					if (IdleIdx == INDEX_NONE) { continue; }

					if (Inst->GetInstanceStateWeight(MachineIdx, IdleIdx) > 0.99f)
					{
						return true;   // assertion satisfied
					}
				}
			}

			if (FPlatformTime::Seconds() - StartTime > 20.0)
			{
				Test->AddError(TEXT("Timed out (20s) waiting for Locomotion/Idle weight > 0.99 on a PIE pawn"));
				return true;
			}
			return false;   // keep polling
		}

	private:
		FAutomationTestBase* Test;
		double StartTime;
	};

	// ---- Helpers for the driven command below -----------------------------------

	// Read a numeric contract var as double for AddInfo self-documentation — same reflection
	// path as UAnimDebugTools::SetAnimVariable (read side). BP "float" vars are FDoubleProperty
	// in UE5; GroundedFlag is FIntProperty.
	bool TryReadNumericVar(UAnimInstance& Inst, const TCHAR* VarName, double& Out)
	{
		FProperty* Prop = Inst.GetClass()->FindPropertyByName(FName(VarName));
		if (const FDoubleProperty* DP = CastField<FDoubleProperty>(Prop)) { Out = DP->GetPropertyValue_InContainer(&Inst); return true; }
		if (const FFloatProperty* FP = CastField<FFloatProperty>(Prop)) { Out = FP->GetPropertyValue_InContainer(&Inst); return true; }
		if (const FIntProperty* IP = CastField<FIntProperty>(Prop)) { Out = static_cast<double>(IP->GetPropertyValue_InContainer(&Inst)); return true; }
		return false;
	}

	// The WRITE side of the reflection contract — mirrors the FIntProperty branch
	// of UAnimDebugTools::SetAnimVariable. Drives a Blueprint int var (UpperBodyState) so the trigger test
	// exercises the EventGraph contract, not a direct Montage_Play. false if the var is absent/non-int.
	bool TryWriteIntVar(UAnimInstance& Inst, const TCHAR* VarName, int32 Value)
	{
		FProperty* Prop = Inst.GetClass()->FindPropertyByName(FName(VarName));
		if (FIntProperty* IP = CastField<FIntProperty>(Prop))
		{
			IP->SetPropertyValue_InContainer(&Inst, Value);
			return true;
		}
		return false;
	}

	// The float sibling of TryWriteIntVar — mirrors the FDoubleProperty/FFloatProperty branches
	// of UAnimDebugTools::SetAnimVariable (raw reflection, so it drives a var that is NOT instance-editable,
	// which a Python set_editor_property cannot). BP "float" vars are FDoubleProperty in UE5; handle both.
	// Drives TurnYawDelta (a pure external contract var, no EventGraph re-derive) so the write STICKS and a
	// TryReadNumericVar read-back proves the OFF/ON state (the confound-immunity property).
	// ONE OWNER FOR THE OWNERSHIP FLOOR. "A slot/branch that OWNS the pose"
	// is asserted in two unrelated commands, and both used a bare 0.95 while a comment in one claimed
	// it was "the same floor kSlotWeightFloor uses elsewhere in this file" - which it was not, because
	// kSlotWeightFloor is a private member of a different class and unreachable from the other. Retune
	// one and the comment starts documenting an invariant the code no longer holds: the stale-transcribed
	// -figure class this file keeps getting bitten by. Now there is one constant and both read it.
	static constexpr float kPoseOwnershipFloor = 0.95f;

	bool TryWriteFloatVar(UAnimInstance& Inst, const TCHAR* VarName, double Value)
	{
		FProperty* Prop = Inst.GetClass()->FindPropertyByName(FName(VarName));
		if (FDoubleProperty* DP = CastField<FDoubleProperty>(Prop)) { DP->SetPropertyValue_InContainer(&Inst, Value); return true; }
		if (FFloatProperty* FP = CastField<FFloatProperty>(Prop)) { FP->SetPropertyValue_InContainer(&Inst, static_cast<float>(Value)); return true; }
		return false;
	}

	// The BOOL siblings of the two above, mirroring UAnimDebugTools::SetAnimVariable's
	// FBoolProperty branch. Needed for HasMannequinFootIKRig, which the AnimBP computes ONCE in
	// BlueprintInitializeAnimation — nothing re-derives it per tick, so a raw reflection write STICKS.
	// (Contrast `Speed`, which the EventGraph recomputes every frame and which therefore cannot be driven
	// this way at all.) That property is exactly what makes the OFF arm of
	// FSkeletonAwareFootIKCommand possible without a second skeleton or a second map.
	bool TryReadBoolVar(UAnimInstance& Inst, const TCHAR* VarName, bool& Out)
	{
		FProperty* Prop = Inst.GetClass()->FindPropertyByName(FName(VarName));
		if (const FBoolProperty* BP = CastField<FBoolProperty>(Prop)) { Out = BP->GetPropertyValue_InContainer(&Inst); return true; }
		return false;
	}

	bool TryWriteBoolVar(UAnimInstance& Inst, const TCHAR* VarName, bool Value)
	{
		FProperty* Prop = Inst.GetClass()->FindPropertyByName(FName(VarName));
		if (FBoolProperty* BP = CastField<FBoolProperty>(Prop)) { BP->SetPropertyValue_InContainer(&Inst, Value); return true; }
		return false;
	}

	// The DRIVEN PIE assertion. Clones the lazy-init-StartTime pattern above and
	// adds two budgeted phases plus an observation-only tail:
	//   Settle    — wait for Locomotion/Idle weight > 0.99 (own 20 s budget).
	//   Drive     — REAL movement: Pawn->AddMovementInput each tick pushes velocity to
	//               MaxWalkSpeed 600; the EventGraph sets Speed=|vel|; the Idle->Locomotion
	//               transition fires. Assert Locomotion weight > 0.9 (own 20 s budget).
	//               (A smoke run showed this AddMovementInput path drives Speed to ~600.)
	//   BSOutput  — assert the BlendSpace OUTPUT, not just the machine state. At sustained
	//               Speed ~600 the 2D BS_Idle_Walk_Run must sample the forward-JOG corner, so the feet
	//               CYCLE. OSCILLATES the pawn on the FLAT LANE (reversing before the ramp) for a fixed
	//               multi-stride window, samples per-foot root-relative Z, and asserts BOTH feet's vertical
	//               swing exceeds kFootSwingMin. Closes a latent-bug gap: the state machine read
	//               Locomotion at Speed 600 even while X<-Speed made the BS sample MM_Idle (feet frozen)
	//               — invisible to every prior weight-only assertion.
	//   JumpProbe — best-effort watch-list observation (Character->Jump(), airborne GroundedFlag
	//               and Land-state reachability). AddInfo only; NEVER fails the test.
	class FDriveSpeedToLocomotionCommand : public IAutomationLatentCommand
	{
	public:
		explicit FDriveSpeedToLocomotionCommand(FAutomationTestBase* InTest)
			: Test(InTest), StartTime(0.0) {}

		virtual bool Update() override
		{
			// Lazy-init the clock on the first poll (constructor runs at ENQUEUE, before the
			// map/PIE exist) — the same pattern as the Idle-weight command. Reset per phase.
			if (StartTime == 0.0) { StartTime = FPlatformTime::Seconds(); }
			const double Now = FPlatformTime::Seconds();

			// Pin to the POSSESSED pawn (the map's GameMode override
			// auto-possesses BP_MT_TestPawn) instead of scanning all pawns — removes the
			// first-eligible-pawn ambiguity and keeps AddMovementInput off unintended actors.
			// Baked-index trap: the Locomotion baked machine is resolved by NAME (runtime index is never a
			// baked-array index). If anything fails to resolve, we fall through to the phase timeout
			// gates (same degrade semantics as a null world).
			APawn* Pawn = nullptr; USkeletalMeshComponent* Mesh = nullptr; UAnimInstance* Inst = nullptr;
			int32 MachineIdx = INDEX_NONE; const FBakedAnimationStateMachine* Baked = nullptr;
			if (ResolvePossessedLocomotionContext(Pawn, Mesh, Inst, MachineIdx, Baked))
			{
				if (Phase == EPhase::Settle)
				{
					const int32 IdleIdx = FindBakedStateIndex(*Baked, TEXT("Idle"));
					if (IdleIdx != INDEX_NONE)
					{
						const float W = Inst->GetInstanceStateWeight(MachineIdx, IdleIdx);
						if (W > 0.99f)
						{
							Test->AddInfo(FString::Printf(
								TEXT("Settled in Idle (weight %.3f) after %.1fs; drive path = APawn::AddMovementInput (real movement)"),
								W, Now - StartTime));
							Phase = EPhase::Drive;
							StartTime = Now;   // fresh 20 s budget for the drive phase
						}
					}
				}
				else if (Phase == EPhase::Drive)
				{
					// Accumulate control input each tick; CMC consumes it over several frames.
					// Safe target: this IS the possessed pawn (pinned above).
					Pawn->AddMovementInput(FVector(1.f, 0.f, 0.f), 1.f);

					const int32 LocoIdx = FindBakedStateIndex(*Baked, TEXT("Locomotion"));
					if (LocoIdx != INDEX_NONE)
					{
						const float W = Inst->GetInstanceStateWeight(MachineIdx, LocoIdx);
						if (W > 0.9f)
						{
							double Speed = -1.0;
							TryReadNumericVar(*Inst, TEXT("Speed"), Speed);
							Test->AddInfo(FString::Printf(
								TEXT("Locomotion reached: state weight %.3f, Speed=%.1f, after %.1fs of drive"),
								W, Speed, Now - StartTime));
							Phase = EPhase::BSOutput;   // state proven; now assert the BS OUTPUT (feet cycle)
							StartTime = Now;
						}
					}
				}
				else if (Phase == EPhase::BSOutput)
				{
					// Assert the BlendSpace OUTPUT, not just the machine state. A working Y<-Speed
					// binding makes the 2D BS sample the forward-JOG corner at Speed ~600, so the feet CYCLE;
					// the latent X<-Speed bug sampled MM_Idle -> the feet stayed FROZEN even though the state
					// machine (asserted above) read Locomotion. We OSCILLATE the pawn along the flat lane
					// (reverse before the ramp) across a FIXED window so many full strides are sampled — a
					// single short pass at variable PIE frame cadence under-samples the swing and makes the
					// range flaky. Root-relative foot Z cancels body translation; a flat-Z gate rejects any
					// stray ramp sample. Speed is |velocity| ~600 in EITHER travel direction (no Direction var,
					// so the BS pose is forward-jog regardless of which way the pawn is moving).

					// One-time bone-existence check (verify before relying): a rostered locomotion rig
					// must carry foot bones for the cadence assertion (Khaimera's KHAI_MESH does).
					if (!bBSBonesChecked)
					{
						bBSBonesChecked = true;
						BSFlatZ = Pawn->GetActorLocation().Z;   // grounded/flat when Locomotion was reached
						const int32 IdxL = Mesh->GetBoneIndex(FName(TEXT("foot_l")));
						const int32 IdxR = Mesh->GetBoneIndex(FName(TEXT("foot_r")));
						if (IdxL == INDEX_NONE || IdxR == INDEX_NONE)
						{
							Test->AddError(FString::Printf(
								TEXT("[BSOutput] foot_l/foot_r not on this mesh (bone idx l=%d r=%d) — cannot assert BlendSpace foot cadence"),
								IdxL, IdxR));
							return true;
						}
					}

					const double PawnX = Pawn->GetActorLocation().X;
					const double PawnZ = Pawn->GetActorLocation().Z;
					// Oscillate within the flat lane: reverse before the ramp (X>kBSFlatHighX) and near spawn
					// (X<kBSFlatLowX) so the pawn never climbs — keeps a long, terrain-clean jog window.
					if (PawnX > kBSFlatHighX) { BSDir = -1.f; }
					else if (PawnX < kBSFlatLowX) { BSDir = +1.f; }
					Pawn->AddMovementInput(FVector(BSDir, 0.f, 0.f), 1.f);

					double SpeedNow = -1.0; TryReadNumericVar(*Inst, TEXT("Speed"), SpeedNow);
					bool bFalling = false; TryReadBoolVar(*Inst, TEXT("IsFalling"), bFalling);

					// Accumulate on the flat lane, at jog speed, grounded (root-relative Z cancels the pawn's
					// own translation). The flat-Z gate rejects any sample that strayed onto the ramp.
					if (SpeedNow > 550.0 && !bFalling && PawnX < kBSFlatMaxX
						&& FMath::Abs(PawnZ - BSFlatZ) < 30.0)
					{
						const FVector FootL = Mesh->GetSocketLocation(FName(TEXT("foot_l")));
						const FVector FootR = Mesh->GetSocketLocation(FName(TEXT("foot_r")));
						const double RelL = FootL.Z - PawnZ;
						const double RelR = FootR.Z - PawnZ;
						BSMinRelL = FMath::Min(BSMinRelL, RelL); BSMaxRelL = FMath::Max(BSMaxRelL, RelL);
						BSMinRelR = FMath::Min(BSMinRelR, RelR); BSMaxRelR = FMath::Max(BSMaxRelR, RelR);
						++BSSampleCount;
					}

					// Fixed ~3s sampling window -> several full strides -> a stable peak-to-peak range.
					if ((Now - StartTime) > 3.0)
					{
						const double RangeL = BSMaxRelL - BSMinRelL;
						const double RangeR = BSMaxRelR - BSMinRelR;
						if (BSSampleCount < 5)
						{
							Test->AddError(FString::Printf(
								TEXT("[BSOutput] flat-lane Speed~600 window too sparse (%d samples) — could not reliably sample foot cadence"),
								BSSampleCount));
							return true;
						}
						Test->AddInfo(FString::Printf(
							TEXT("[BSOutput] flat-lane foot cadence over %d samples: foot_l relZ range %.2f (%.2f..%.2f), foot_r relZ range %.2f (%.2f..%.2f); pass band %.1f .. %.1f"),
							BSSampleCount, RangeL, BSMinRelL, BSMaxRelL, RangeR, BSMinRelR, BSMaxRelR,
							kFootSwingMin, kFootSwingMaxCm));
						bool bSwingOk = true;
						// This assertion was once FLOOR-ONLY, and the gap was
						// documented as a blind spot rather than fixed here — on the stated
						// grounds that a ceiling would turn Pie.RigLocomotion.Khaimera red. THAT REASON WAS
						// FALSE: it rested on a world-Z figure (93.26 cm) for an assertion that compares
						// PAWN-RELATIVE Z, in which Khaimera reads 33-35 cm and passes comfortably. With the
						// reason gone the retrofit is just an improvement, so it is done. It costs no
						// currently-green row, and it WOULD have caught a recorded report where this
						// very row logged foot_l 93.59 / foot_r 92.08 over 9 samples — feet travelling from
						// 92 cm BELOW the pawn origin to just above it — and still passed on the floor alone.
						if (RangeL > kFootSwingMaxCm || RangeR > kFootSwingMaxCm)
						{
							Test->AddError(FString::Printf(
								TEXT("[BSOutput] foot travel is IMPLAUSIBLY LARGE: foot_l relZ range %.2f (%.2f..%.2f), foot_r %.2f (%.2f..%.2f), ceiling %.1f cm. A pawn-relative sweep this big is not a stride — check whether the feet start far below the pawn origin (a placement or retarget-pose fault) rather than raising this number."),
								RangeL, BSMinRelL, BSMaxRelL, RangeR, BSMinRelR, BSMaxRelR, kFootSwingMaxCm));
							bSwingOk = false;
						}
						if (!(RangeL > kFootSwingMin))
						{
							Test->AddError(FString::Printf(
								TEXT("[BSOutput] BlendSpace-output assertion FAILED: foot_l vertical swing %.2f not > %.1f cm at Speed~600 — the feet are not cycling (BlendSpace sampling idle, e.g. Speed misbound to the Direction axis)"),
								RangeL, kFootSwingMin));
							bSwingOk = false;
						}
						if (!(RangeR > kFootSwingMin))
						{
							Test->AddError(FString::Printf(
								TEXT("[BSOutput] BlendSpace-output assertion FAILED: foot_r vertical swing %.2f not > %.1f cm at Speed~600 — the feet are not cycling (BlendSpace sampling idle, e.g. Speed misbound to the Direction axis)"),
								RangeR, kFootSwingMin));
							bSwingOk = false;
						}
						if (bSwingOk)
						{
							Test->AddInfo(FString::Printf(
								TEXT("[BSOutput] BlendSpace-output assertion PASS: BOTH feet cycle at Speed~600 (foot_l %.2f, foot_r %.2f > %.1f cm) — the Y<-Speed binding drives the jog, not a frozen idle pose"),
								RangeL, RangeR, kFootSwingMin));
						}
						Phase = EPhase::JumpProbe;   // BS output proven; observe the jump tail
						StartTime = Now;
					}
				}
				else   // EPhase::JumpProbe — observation only; cannot fail the test.
				{
					ACharacter* Character = Cast<ACharacter>(Pawn);
					if (!Character)
					{
						Test->AddInfo(TEXT("Jump probe skipped: possessed pawn is not an ACharacter."));
						return true;
					}
					if (!bJumpIssued) { Character->Jump(); bJumpIssued = true; }

					double Grounded = 1.0;
					TryReadNumericVar(*Inst, TEXT("GroundedFlag"), Grounded);
					bool bFalling = false; TryReadBoolVar(*Inst, TEXT("IsFalling"), bFalling);
					if (Grounded < 0.5 || bFalling) { bSawAirborne = true; }

					const int32 LandIdx = FindBakedStateIndex(*Baked, TEXT("Land"));
					if (LandIdx != INDEX_NONE && Inst->GetInstanceStateWeight(MachineIdx, LandIdx) > 0.5f)
					{ bSawLand = true; }

					if (bSawLand || (Now - StartTime) > 8.0)
					{
						Test->AddInfo(FString::Printf(
							TEXT("Jump probe (observation only): airborne GroundedFlag=0/IsFalling seen=%s; Land reached=%s (watch-list #1/#4)."),
							bSawAirborne ? TEXT("yes") : TEXT("no"),
							bSawLand ? TEXT("yes") : TEXT("no")));
						return true;
					}
				}
			}

			// Outer backstop for the JumpProbe: the probe's normal exits live
			// INSIDE the pawn-resolution branch, so if the world/pawn stopped resolving mid-probe
			// the command would poll forever and FEndPlayMapCommand would never run. Cap it here
			// too, AddInfo-only — and carry the observations gathered so far so the watch-list
			// evidence is not lost on a no-resolution exit.
			if (Phase == EPhase::JumpProbe && (Now - StartTime) > 8.0)
			{
				Test->AddInfo(FString::Printf(
					TEXT("Jump probe: 8s cap reached (outer backstop; pawn stopped resolving). Observations so far: airborne GroundedFlag=0/IsFalling seen=%s; Land reached=%s (watch-list #1/#4)."),
					bSawAirborne ? TEXT("yes") : TEXT("no"),
					bSawLand ? TEXT("yes") : TEXT("no")));
				return true;   // observation window over — never an error
			}

			// BSOutput backstop: the finalize above lives inside the pawn-resolution
			// branch; if the pawn/anim stopped resolving mid-window this caps it so FEndPlayMapCommand runs.
			if (Phase == EPhase::BSOutput && (Now - StartTime) > 8.0)
			{
				if (BSSampleCount >= 2)
				{
					const double RangeL = BSMaxRelL - BSMinRelL;
					const double RangeR = BSMaxRelR - BSMinRelR;
					// Same BAND as the normal finalize, not just the floor — a backstop that asserts less
					// than the path it stands in for is a hole.
					if (!(RangeL > kFootSwingMin) || !(RangeR > kFootSwingMin))
					{
						Test->AddError(FString::Printf(
							TEXT("[BSOutput] 8s cap (pawn stopped resolving): foot swing L %.2f / R %.2f not both > %.1f cm"),
							RangeL, RangeR, kFootSwingMin));
					}
					else if (RangeL > kFootSwingMaxCm || RangeR > kFootSwingMaxCm)
					{
						Test->AddError(FString::Printf(
							TEXT("[BSOutput] 8s cap (pawn stopped resolving): foot swing L %.2f / R %.2f exceeds the %.1f cm ceiling — a pawn-relative sweep this big is not a stride"),
							RangeL, RangeR, kFootSwingMaxCm));
					}
					else
					{
						Test->AddInfo(FString::Printf(
							TEXT("[BSOutput] 8s cap (pawn stopped resolving): foot swing L %.2f / R %.2f inside the %.1f .. %.1f cm band (pass on collected samples)"),
							RangeL, RangeR, kFootSwingMin, kFootSwingMaxCm));
					}
				}
				else
				{
					Test->AddError(TEXT("[BSOutput] 8s cap: pawn/anim never resolved a flat-lane Speed~600 window"));
				}
				return true;
			}

			// Per-phase budget: Settle and Drive each get their own 20 s (measured from the phase
			// start). The JumpProbe is capped by the backstop above.
			if ((Phase == EPhase::Settle || Phase == EPhase::Drive) && (Now - StartTime) > 20.0)
			{
				Test->AddError(FString::Printf(
					TEXT("Timed out (20s) in %s phase waiting for %s on a PIE pawn"),
					Phase == EPhase::Settle ? TEXT("settle") : TEXT("drive"),
					Phase == EPhase::Settle ? TEXT("Locomotion/Idle weight > 0.99")
					                        : TEXT("Locomotion/Locomotion weight > 0.9")));
				return true;
			}
			return false;   // keep polling
		}

	private:
		enum class EPhase : uint8 { Settle, Drive, BSOutput, JumpProbe };

		// BSOutput flat-lane geometry. The ramp on Lvl_MT_Test (and the Quinn/Khaimera
		// duplicates) starts at world X=500 (kRampLowX in the FootIK helpers below). The pawn spawns near
		// X=0, so [kBSFlatLowX, kBSFlatHighX] is the flat pre-ramp lane; the pawn is REVERSED at those
		// bounds so it oscillates on the flat lane and never climbs (FootIK-on-ramp inflates the Z range
		// even in the idle pose — observed in a smoke run). kBSFlatMaxX is the sample cutoff (a hair below the ramp).
		static constexpr double kBSFlatLowX  = 100.0;   // reverse toward +X here (stay off the spawn edge)
		static constexpr double kBSFlatHighX = 400.0;   // reverse toward -X here (buffer before the ramp)
		static constexpr double kBSFlatMaxX  = 480.0;   // never sample a foot at/after the ramp toe

		// Foot vertical-swing pass floor (cm). DERIVED from the live pre-regen A/B smoke on Lvl_MT_Test,
		// per-foot (foot.Z - pawnRootZ) RANGE over the flat-lane Speed~600
		// grounded window:
		//   BROKEN (X<-Speed; 2D BS samples (Dir=180-clamp, Speed=0) = MM_Idle): feet FROZEN — relL/relR
		//     range 0.02-0.03 cm (world foot Z pinned ~8.5 cm). The latent bug's on-camera signature.
		//   FIXED  (Y<-Speed; 2D BS samples (Dir=0, Speed=600) = MF_Unarmed_Jog_Fwd): feet CYCLE in
		//     antiphase — relL range 38.4 cm, relR 32.8 cm (foot_l world Z 12->51, foot_r 11->40).
		// Over the oscillating multi-stride window the fixed rigs measure per-foot ranges ~25-40 cm
		// (Manny/Quinn ~40, Khaimera's taller legs ~25). 8.0 sits ~3x below the smallest fixed swing and
		// ~250x above the broken (frozen ~0.03) range — a clean two-sided discriminator. Confirmed per rig
		// (Manny/Quinn/Khaimera) at --tier pie.
		//
		// ⚠️ WHAT THIS FLOOR DOES AND DOES NOT ESTABLISH. It proves the feet
		// are MOVING. It does not prove they are moving FOR THE RIGHT REASON, and on this row's Khaimera
		// fixture they are not: his map plays the ORIGINAL Khai_* clips, whose ik_foot_l/r tracks are
		// constant (0.000/0.000/0.000), so the Template's foot-IK Control Rig solves his deform feet onto
		// an effector the animation never drives. The travelling clip's pelvis then walks ~1362 cm away
		// from that fixed target, the leg is dragged at full extension rather than stepped, and this
		// assertion has been GREEN throughout at 33-35 cm. Those clips are off-limits to write
		// (GoldenDiff.MTKhaimera + six Verify.ClipVaries rows diff them), so the row is left as it is —
		// but it is weaker evidence than its name suggests, and the check that actually catches the cause
		// is MotionTerminal.Verify.IKEffectorDrives, at the clip level, in the default tier.
		//
		// A CEILING sits beside this floor (kFootSwingMaxCm, file scope). It does
		// NOT catch the case above — measured pawn-relative, the dragged legs read BELOW the healthy
		// mannequins, so no ceiling separates them. It catches a different, real, once-observed failure:
		// one recorded report logged 93.59 / 92.08 cm here, feet sweeping from 92 cm below the pawn
		// origin to above it, and passed on the floor alone.
		//
		// ONE NUMBER, forwarded to the file-scope constant exactly as kFootSwingMax is. This was once a
		// second `= 8.0` literal; the derivation is at kFootSwingMinCm.
		static constexpr double kFootSwingMin = kFootSwingMinCm;

		FAutomationTestBase* Test;
		double StartTime;
		EPhase Phase = EPhase::Settle;
		bool bJumpIssued = false;
		bool bSawAirborne = false;
		bool bSawLand = false;
		// BSOutput accumulators (flat-lane oscillating foot-cadence window).
		bool bBSBonesChecked = false;
		float BSDir = 1.f;          // current drive direction along X (flipped at the flat-lane bounds)
		double BSFlatZ = 0.0;       // grounded/flat pawn Z captured at BSOutput entry (ramp-sample gate)
		int32 BSSampleCount = 0;
		double BSMinRelL = 1.0e9, BSMaxRelL = -1.0e9;
		double BSMinRelR = 1.0e9, BSMaxRelR = -1.0e9;
	};

	// Restore a CVar after PIE teardown. Both PIE tests run in ONE automation
	// process, so a Set(0) left behind by one test would bleed into every later test in the run.
	// AddInfo on restore; never errors.
	class FRestoreCVarCommand : public IAutomationLatentCommand
	{
	public:
		FRestoreCVarCommand(FAutomationTestBase* InTest, IConsoleVariable* InVar, int32 InValue)
			: Test(InTest), Var(InVar), Value(InValue) {}

		virtual bool Update() override
		{
			if (Var) { Var->Set(Value); }
			Test->AddInfo(FString::Printf(
				TEXT("Restored CommonUI.Debug.CheckGameViewportClientValid to %d"), Value));
			return true;
		}

	private:
		FAutomationTestBase* Test;
		IConsoleVariable* Var;
		int32 Value;
	};

	// Blocks until every asynchronous asset build the engine is running has finished: animation compression,
	// shaders, meshes, textures (each registers with FAssetCompilingManager). On a cold derived-data cache, the
	// first run after an engine upgrade or on a fresh clone, a clip still compressing samples as the reference
	// pose. A row measuring it then reads "the aim offset moved nothing" and fails for a reason that has
	// nothing to do with the graph (seen on the first 5.9 run: Pie.AimOffset.Khaimera 0.00 deg, 24/24 warm).
	class FFinishAssetCompilationCommand : public IAutomationLatentCommand
	{
	public:
		virtual bool Update() override
		{
			FAssetCompilingManager::Get().FinishAllCompilation();
			return true;
		}
	};

	// The PIE run every Pie.* RunTest enqueues once its own gating has passed: the CommonUI CVar save/zero, then
	// the FEditorLoadMap(Map) -> FStartPIECommand(false) -> Cmd -> FEndPlayMapCommand -> FRestoreCVarCommand
	// latent chain. Takes ownership of Cmd.
	void EnqueuePieMapRun(FAutomationTestBase* Test, const FString& Map, IAutomationLatentCommand* Cmd)
	{
		// CommonUI's viewport-validity warning would auto-fail the test; disable the check.
		// Capture the prior value and restore it after teardown (state bleed).
		IConsoleVariable* CVar = IConsoleManager::Get()
			.FindConsoleVariable(TEXT("CommonUI.Debug.CheckGameViewportClientValid"));
		const int32 PrevCVarValue = CVar ? CVar->GetInt() : 0;
		if (CVar) { CVar->Set(0); }

		ADD_LATENT_AUTOMATION_COMMAND(FEditorLoadMap(Map));
		ADD_LATENT_AUTOMATION_COMMAND(FFinishAssetCompilationCommand());
		// false = real Play-in-Editor. The parameter is bSimulateInEditor (AutomationEditorCommon.h:225):
		// passing true would SIMULATE — no PlayerController/pawn spawns, so there is nothing possessed to drive.
		ADD_LATENT_AUTOMATION_COMMAND(FStartPIECommand(false));
		ADD_LATENT_AUTOMATION_COMMAND(FFinishAssetCompilationCommand());   // what the spawned pawns loaded
		FAutomationTestFramework::Get().EnqueueLatentCommand(MakeShareable(Cmd));
		ADD_LATENT_AUTOMATION_COMMAND(FEndPlayMapCommand());
		if (CVar)
		{
			FAutomationTestFramework::Get().EnqueueLatentCommand(
				MakeShareable(new FRestoreCVarCommand(Test, CVar, PrevCVarValue)));
		}
	}

	// ---- Mechanism A helpers (per-node GatherDebugData AbsoluteWeight) ------------------
	// The tree itself comes from MotionTerminal::GatherFlatNodeWeights / FormatFlatNodeWeights
	// (AnimIntrospection.h, the same path the GetNodeWeights MCP tool dumps). Under the pie tier's real
	// RHI it walks a LIVE evaluated graph; the full indented dump is the raw differentiator evidence.

	// AbsoluteWeight of the first node whose DebugLine contains Substr (records the matched line so
	// the assertion is self-documenting). Returns -1 when no node matches. The two match targets are
	// source-verified against UE 5.8: the state-machine node's DebugLine carries "(Locomotion->Idle)"
	// (FAnimNode_StateMachine::GatherDebugData) and the active state's top pose node carries
	// "(State: Idle)" (propagated via FNodeDebugData::BranchFlow's NodeDescription).
	float FindNodeWeight(const TArray<FNodeDebugData::FFlattenedDebugData>& Flat, const TCHAR* Substr, FString& OutLine)
	{
		for (const FNodeDebugData::FFlattenedDebugData& Item : Flat)
		{
			if (Item.DebugLine.Contains(Substr))
			{
				OutLine = Item.DebugLine;
				return Item.AbsoluteWeight;
			}
		}
		OutLine = FString::Printf(TEXT("<no node matched '%s'>"), Substr);
		return -1.f;
	}

	// The DIFFERENTIATOR PIE assertion. The pawn stays in Idle the whole test; a montage
	// is played into the UpperBody slot and the isolation proof is that the Locomotion branch is
	// undisturbed WHILE the slot is active — proven by THREE introspection mechanisms:
	//   (a) Mechanism C — the montage's blend weight > 0 (slot is live).
	//   (b) Mechanism B — Locomotion/Idle machine state weight still > 0.99 (machine not flipped).
	//   (c) Mechanism A — the Locomotion SM node's and active Idle player node's GatherDebugData
	//       AbsoluteWeight are UNCHANGED vs a pre-montage baseline (delta < 0.02, i.e. still ~1.0).
	//       This is the increment Mechanism A adds: a FULL-BODY montage would drive the SM node's
	//       AbsoluteWeight toward 0 (whole-pose takeover); a SLOT montage leaves it at ~1.0 because
	//       the locomotion pose is the LayeredBlendPerBone base and only upper-body bones are overlaid.
	// Phases mirror the driven command's lazy-StartTime + per-phase 20s budgets.
	class FMontageIsolationCommand : public IAutomationLatentCommand
	{
	public:
		explicit FMontageIsolationCommand(FAutomationTestBase* InTest)
			: Test(InTest), StartTime(0.0) {}

		virtual bool Update() override
		{
			// Lazy-init the clock on first poll (constructor runs at ENQUEUE, before map/PIE exist).
			if (StartTime == 0.0) { StartTime = FPlatformTime::Seconds(); }
			const double Now = FPlatformTime::Seconds();

			// Pin the POSSESSED pawn (map GameMode auto-possesses BP_MT_TestPawn) — same as the driven command.
			APawn* Pawn = nullptr; USkeletalMeshComponent* Mesh = nullptr; UAnimInstance* Inst = nullptr;
			int32 MachineIdx = INDEX_NONE; const FBakedAnimationStateMachine* Baked = nullptr;
			const bool bResolved = ResolvePossessedLocomotionContext(Pawn, Mesh, Inst, MachineIdx, Baked);
			const int32 IdleIdx = bResolved ? FindBakedStateIndex(*Baked, TEXT("Idle")) : INDEX_NONE;

			if (bResolved && IdleIdx != INDEX_NONE)
			{
				if (Phase == EPhase::Settle)
				{
					const float W = Inst->GetInstanceStateWeight(MachineIdx, IdleIdx);
					if (W > 0.99f)
					{
						// Capture the Mechanism-A baseline (pre-montage node weights).
						const TArray<FNodeDebugData::FFlattenedDebugData> Flat = MotionTerminal::GatherFlatNodeWeights(*Inst);
						BaselineSMWeight   = FindNodeWeight(Flat, TEXT("(Locomotion->"), BaselineSMLine);
						BaselineIdleWeight = FindNodeWeight(Flat, TEXT("(State: Idle)"), BaselineIdleLine);
						Test->AddInfo(FString::Printf(
							TEXT("[Settle] Locomotion/Idle machine weight %.3f after %.1fs. BASELINE node-weight dump:\n%s"),
							W, Now - StartTime, *MotionTerminal::FormatFlatNodeWeights(Flat)));
						Test->AddInfo(FString::Printf(
							TEXT("[Settle] baseline matched: SM node '%s' AbsWeight=%.3f ; Idle player '%s' AbsWeight=%.3f"),
							*BaselineSMLine, BaselineSMWeight, *BaselineIdleLine, BaselineIdleWeight));

						if (BaselineSMWeight < 0.f || BaselineIdleWeight < 0.f)
						{
							Test->AddError(TEXT("[Settle] could not locate the Locomotion SM node and/or the active Idle player node in GatherDebugData (see baseline dump)"));
							return true;
						}
						// THE BASELINE MUST SHOW LOCOMOTION ACTUALLY OWNING THE POSE. The check above only rejects
						// -1 (node not found), so a baseline of 0.0 - nodes present, contributing nothing - passed as
						// valid, and the mid-montage test below compares ABSOLUTE DIFFERENCES against it. Zero to zero
						// is a zero delta, so an always-on full-body layer that suppressed locomotion before the montage
						// and throughout it would have been reported as PERFECT ISOLATION. The claim is 'locomotion never
						// flinches', which presupposes locomotion is there to flinch: assert that, then measure the delta.
						// kPoseOwnershipFloor is shared with FMontageSeamlessCommand's kSlotWeightFloor, which now
						// reads it rather than restating it - they cannot drift apart.
						if (BaselineSMWeight < kPoseOwnershipFloor || BaselineIdleWeight < kPoseOwnershipFloor)
						{
							Test->AddError(FString::Printf(
								TEXT("[Settle] BASELINE DOES NOT OWN THE POSE: Locomotion SM AbsWeight=%.3f, Idle player AbsWeight=%.3f (both must be >= 0.95 before the montage plays). The isolation assertion compares differences FROM this baseline, so a near-zero baseline would let \"locomotion contributes nothing, before and during\" report as perfect isolation."),
								BaselineSMWeight, BaselineIdleWeight));
							return true;
						}

						// Load the montage once (guard null) and play it into the UpperBody slot.
						if (!MontagePtr.IsValid())
						{
							MontagePtr.Reset(LoadObject<UAnimMontage>(nullptr, TEXT("/Game/MT/AM_MT_Attack.AM_MT_Attack")));
						}
						UAnimMontage* Montage = MontagePtr.Get();
						if (!Montage)
						{
							Test->AddError(TEXT("[Settle] failed to load /Game/MT/AM_MT_Attack.AM_MT_Attack"));
							return true;
						}
						const float PlayLen = Inst->Montage_Play(Montage, 1.0f);
						if (PlayLen <= 0.f)
						{
							Test->AddError(FString::Printf(
								TEXT("[Settle] Montage_Play returned %.3f — montage failed to start (skeleton mismatch?)"), PlayLen));
							return true;
						}
						Test->AddInfo(FString::Printf(
							TEXT("[Settle] Montage_Play('AM_MT_Attack') started (length %.3fs); pawn stays in Idle."), PlayLen));
						Phase = EPhase::AssertDuring;
						StartTime = Now;   // fresh 20s budget
					}
				}
				else if (Phase == EPhase::AssertDuring)
				{
					UAnimMontage* Montage = MontagePtr.Get();
					const bool bPlaying = Montage && Inst->Montage_IsPlaying(Montage);
					FAnimMontageInstance* MI = Montage ? Inst->GetActiveInstanceForMontage(Montage) : nullptr;
					const float MontageWeight = MI ? MI->GetWeight() : 0.f;

					if (bPlaying && MI && MontageWeight > KINDA_SMALL_NUMBER)
					{
						// Sample all three mechanisms on ONE tick while the montage is blended in.
						const TArray<FNodeDebugData::FFlattenedDebugData> Flat = MotionTerminal::GatherFlatNodeWeights(*Inst);
						FString MidSMLine, MidIdleLine;
						const float MidSMWeight   = FindNodeWeight(Flat, TEXT("(Locomotion->"), MidSMLine);
						const float MidIdleWeight = FindNodeWeight(Flat, TEXT("(State: Idle)"), MidIdleLine);
						const float IdleMachineWeight = Inst->GetInstanceStateWeight(MachineIdx, IdleIdx);
						const float SlotLocalWeight = Inst->GetSlotMontageLocalWeight(FName(TEXT("UpperBody")));

						Test->AddInfo(FString::Printf(
							TEXT("[AssertDuring] MID-MONTAGE node-weight dump (montage weight %.3f, slot 'UpperBody' local %.3f):\n%s"),
							MontageWeight, SlotLocalWeight, *MotionTerminal::FormatFlatNodeWeights(Flat)));

						bool bOk = true;

						// (a) Mechanism C — the montage reaches the UpperBody SLOT specifically (not
						// merely "a montage is playing", which restates the entry gate). SlotLocalWeight
						// is the slot-specific evidence: a montage misrouted to a different slot fails here.
						if (!(SlotLocalWeight > 0.f))
						{
							Test->AddError(FString::Printf(
								TEXT("(a) Mechanism C FAILED: UpperBody slot local weight %.3f not > 0 — montage not reaching the UpperBody slot"), SlotLocalWeight));
							bOk = false;
						}

						// (b) Mechanism B — the Locomotion machine is still fully in Idle.
						if (!(IdleMachineWeight > 0.99f))
						{
							Test->AddError(FString::Printf(
								TEXT("(b) Mechanism B FAILED: Locomotion/Idle machine weight %.3f not > 0.99 — the montage disturbed the state machine"),
								IdleMachineWeight));
							bOk = false;
						}

						// (c) Mechanism A — the per-node differentiator: SM + Idle player unchanged.
						if (MidSMWeight < 0.f || MidIdleWeight < 0.f)
						{
							Test->AddError(TEXT("(c) Mechanism A FAILED: could not locate the SM node and/or Idle player node mid-montage"));
							bOk = false;
						}
						else
						{
							const float dSM   = FMath::Abs(MidSMWeight - BaselineSMWeight);
							const float dIdle = FMath::Abs(MidIdleWeight - BaselineIdleWeight);
							if (!(dSM < 0.02f))
							{
								Test->AddError(FString::Printf(
									TEXT("(c) Mechanism A FAILED: Locomotion SM node AbsoluteWeight moved %.4f (baseline %.3f -> mid %.3f); a slot montage must leave the base pose unchanged — this delta means whole-pose takeover"),
									dSM, BaselineSMWeight, MidSMWeight));
								bOk = false;
							}
							if (!(dIdle < 0.02f))
							{
								Test->AddError(FString::Printf(
									TEXT("(c) Mechanism A FAILED: active Idle player node AbsoluteWeight moved %.4f (baseline %.3f -> mid %.3f)"),
									dIdle, BaselineIdleWeight, MidIdleWeight));
								bOk = false;
							}
						}

						if (bOk)
						{
							Test->AddInfo(FString::Printf(
								TEXT("[AssertDuring] ISOLATION PROVEN: (a) montage weight %.3f>0 ; (b) Locomotion/Idle machine %.3f>0.99 ; (c) SM node AbsWeight %.3f->%.3f (delta %.4f) and Idle player %.3f->%.3f (delta %.4f) UNCHANGED vs baseline."),
								MontageWeight, IdleMachineWeight,
								BaselineSMWeight, MidSMWeight, FMath::Abs(MidSMWeight - BaselineSMWeight),
								BaselineIdleWeight, MidIdleWeight, FMath::Abs(MidIdleWeight - BaselineIdleWeight)));
						}
						Phase = EPhase::AssertAfter;
						StartTime = Now;   // fresh 20s budget
					}
					else if (!bPlaying)
					{
						// The montage must be CAUGHT while active — if it ended first, that's a failure.
						Test->AddError(TEXT("[AssertDuring] montage ended before it could be caught active — all three mechanisms must be asserted WHILE the montage is playing"));
						return true;
					}
					// else: playing but weight still ~0 (mid blend-in) — keep polling within budget.
				}
				else   // EPhase::AssertAfter
				{
					UAnimMontage* Montage = MontagePtr.Get();
					// Wait for the montage to finish, THEN for the Locomotion machine to re-settle in
					// Idle > 0.99. Reading the recorded state weight on the EXACT tick the montage
					// instance is removed can catch a one-frame zeroed sample before the graph records a
					// fresh post-montage weight — so poll (the pawn never left Idle; steady state is
					// ~1.0, as Settle measured). A genuine "never returns to Idle" trips the 20s gate.
					if (!Montage || !Inst->Montage_IsPlaying(Montage))
					{
						LastAfterWeight = Inst->GetInstanceStateWeight(MachineIdx, IdleIdx);
						if (LastAfterWeight > 0.99f)
						{
							Test->AddInfo(FString::Printf(
								TEXT("[AssertAfter] montage finished; re-settled in Idle (weight %.3f) %.2fs after montage end. Isolation held start-to-finish."),
								LastAfterWeight, Now - StartTime));
							return true;
						}
						// else: not yet re-settled — keep polling within the budget.
					}
				}
			}

			// Per-phase 20s budget: Settle / AssertDuring / AssertAfter each get their own.
			if (Now - StartTime > 20.0)
			{
				if (Phase == EPhase::AssertAfter)
				{
					Test->AddError(FString::Printf(
						TEXT("Timed out (20s) in AssertAfter: Locomotion/Idle never re-settled > 0.99 after the montage ended (last weight %.3f) — the pawn should never have left Idle"),
						LastAfterWeight));
					return true;
				}
				const TCHAR* PhaseName =
					Phase == EPhase::Settle ? TEXT("Settle (Locomotion/Idle weight > 0.99)")
					                        : TEXT("AssertDuring (catch montage active + assert 3 mechanisms)");
				Test->AddError(FString::Printf(TEXT("Timed out (20s) in %s phase"), PhaseName));
				return true;
			}
			return false;   // keep polling
		}

	private:
		enum class EPhase : uint8 { Settle, AssertDuring, AssertAfter };
		FAutomationTestBase* Test;
		double StartTime;
		EPhase Phase = EPhase::Settle;
		TStrongObjectPtr<UAnimMontage> MontagePtr;   // keep the loaded montage alive across ticks
		float BaselineSMWeight = -1.f;
		float BaselineIdleWeight = -1.f;
		float LastAfterWeight = -1.f;
		FString BaselineSMLine;
		FString BaselineIdleLine;
	};

	// ---- The UpperBodyState TRIGGER-CONTRACT assertion -----------------------
	// Pie.MontageIsolation calls Montage_Play DIRECTLY (bypasses the EventGraph), so a write_graph_dsl
	// re-author that DROPS the EventGraph trigger branch — `if (UpperBodyState==1) { Montage_Play(AM_MT_Attack);
	// SetUpperBodyState(0); }` (both run in sequence in the then-body, no else) —
	// would pass every current test. This test drives the contract END-TO-END through the EventGraph:
	//   1. set UpperBodyState=1 by reflection (the SetAnimVariable write path);
	//   2. assert the montage FIRES into the UpperBody slot (slot local weight rises — the trigger ran);
	//   3. assert UpperBodyState AUTO-RESETS to 0 (the downstream SetUpperBodyState(0), same then-body).
	// A dropped trigger = slot weight never rises AND UpperBodyState stays 1 -> this test's timeout fails.
	class FUpperBodyTriggerCommand : public IAutomationLatentCommand
	{
	public:
		explicit FUpperBodyTriggerCommand(FAutomationTestBase* InTest)
			: Test(InTest), StartTime(0.0) {}

		virtual bool Update() override
		{
			if (StartTime == 0.0) { StartTime = FPlatformTime::Seconds(); }
			const double Now = FPlatformTime::Seconds();

			// Pin the POSSESSED pawn (map GameMode auto-possesses BP_MT_TestPawn) — same as the commands above.
			APawn* Pawn = nullptr; USkeletalMeshComponent* Mesh = nullptr; UAnimInstance* Inst = nullptr;
			int32 MachineIdx = INDEX_NONE; const FBakedAnimationStateMachine* Baked = nullptr;
			const bool bResolved = ResolvePossessedLocomotionContext(Pawn, Mesh, Inst, MachineIdx, Baked);
			const int32 IdleIdx = bResolved ? FindBakedStateIndex(*Baked, TEXT("Idle")) : INDEX_NONE;

			if (bResolved && IdleIdx != INDEX_NONE)
			{
				if (Phase == EPhase::Settle)
				{
					if (Inst->GetInstanceStateWeight(MachineIdx, IdleIdx) > 0.99f)
					{
						// The contract var must exist (a dropped/renamed var is itself a contract break).
						double Pre = -1.0;
						if (!TryReadNumericVar(*Inst, TEXT("UpperBodyState"), Pre))
						{
							Test->AddError(TEXT("[Settle] UpperBodyState property not found on the anim instance — the trigger contract var is missing"));
							return true;
						}
						if (Pre > 0.5)
						{
							Test->AddInfo(FString::Printf(TEXT("[Settle] UpperBodyState started at %.0f (expected 0); driving anyway"), Pre));
						}
						// Drive the contract: set UpperBodyState=1 by reflection (SetAnimVariable path).
						if (!TryWriteIntVar(*Inst, TEXT("UpperBodyState"), 1))
						{
							Test->AddError(TEXT("[Settle] UpperBodyState is not an int property — cannot drive the trigger contract"));
							return true;
						}
						double Confirm = -1.0; TryReadNumericVar(*Inst, TEXT("UpperBodyState"), Confirm);
						const float PreSlot = Inst->GetSlotMontageLocalWeight(FName(TEXT("UpperBody")));
						Test->AddInfo(FString::Printf(
							TEXT("[Settle] settled in Idle after %.1fs; set UpperBodyState=1 by reflection (read-back %.0f, pre-tick). UpperBody slot pre-trigger weight %.3f (expect ~0). Now watching for the EventGraph trigger."),
							Now - StartTime, Confirm, PreSlot));
						Phase = EPhase::AssertFired;
						StartTime = Now;
					}
				}
				else   // EPhase::AssertFired
				{
					const float SlotWeight = Inst->GetSlotMontageLocalWeight(FName(TEXT("UpperBody")));
					if (SlotWeight > MaxSlotWeight) { MaxSlotWeight = SlotWeight; }

					double UBS = 1.0; TryReadNumericVar(*Inst, TEXT("UpperBodyState"), UBS);
					if (UBS < 0.5 && !bSawReset)
					{
						bSawReset = true;
						ResetSeenAt = Now - StartTime;
					}

					// Contract satisfied once BOTH hold: the montage reached the UpperBody slot (the trigger
					// fired via the EventGraph — a direct Montage_Play is never called here) AND UpperBodyState
					// auto-reset to 0 (the downstream reset in the same then-body).
					if (MaxSlotWeight > kSlotFiredMin && bSawReset)
					{
						Test->AddInfo(FString::Printf(
							TEXT("[AssertFired] TRIGGER CONTRACT PROVEN: UpperBodyState=1 -> UpperBody slot fired (peak local weight %.3f > %.2f) AND auto-reset to 0 (seen %.2fs after set) — end-to-end through the EventGraph, not a direct Montage_Play."),
							MaxSlotWeight, kSlotFiredMin, ResetSeenAt));
						return true;
					}
				}
			}

			if (Now - StartTime > 20.0)
			{
				if (Phase == EPhase::Settle)
				{
					Test->AddError(TEXT("Timed out (20s) in Settle waiting for Locomotion/Idle weight > 0.99"));
				}
				else
				{
					Test->AddError(FString::Printf(
						TEXT("[AssertFired] timed out (20s): trigger contract NOT proven — peak UpperBody slot weight %.3f (need >%.2f), UpperBodyState reset-to-0 seen=%s. A dropped EventGraph trigger branch (UpperBodyState==1 -> Montage_Play + reset) would look exactly like this."),
						MaxSlotWeight, kSlotFiredMin, bSawReset ? TEXT("yes") : TEXT("no")));
				}
				return true;
			}
			return false;
		}

	private:
		enum class EPhase : uint8 { Settle, AssertFired };
		// AM_MT_Attack blends into the UpperBody slot over 0.25s to ~1.0; >0.3 is unambiguously "fired".
		static constexpr float kSlotFiredMin = 0.30f;
		FAutomationTestBase* Test;
		double StartTime;
		EPhase Phase = EPhase::Settle;
		float MaxSlotWeight = 0.f;
		bool bSawReset = false;
		double ResetSeenAt = 0.0;
	};

	// ---- Full-body dynamic-montage seamlessness (DefaultSlot + Inertialization) -------
	// The Template gained `LayeredBlendPerBone -> Slot 'DefaultSlot' -> Inertialization -> AimOffset`.
	// EMontageBlendMode::Inertialization is documented (AnimMontage.h:138) to REQUIRE an inertialization
	// node downstream of the slot the montage plays into — this test is the runtime proof that the wiring
	// satisfies that requirement, not just that the nodes exist.
	//
	// THE POP DETECTOR. Component-space bone positions are sampled every tick; the signal is the max
	// per-frame |Δposition| over every bone. A smooth (inertial) walk->run swap spreads the pose difference
	// over the blend window, so the per-frame delta stays in the same band as ordinary animated motion. A
	// hard cut delivers the ENTIRE pose difference in one frame, so it spikes. Component space (not world)
	// so pawn/capsule motion cannot leak into the signal.
	//
	// CONFOUND-IMMUNITY. A "seamless" assertion that cannot tell a smooth blend from a cut proves nothing,
	// so this runs BOTH arms in one PIE session against the same graph, the same two clips and the same
	// sampler — only the blend mechanism differs:
	//   arm 1 (inertial)  FMontageBlendSettings{ BlendMode=Inertialization, Blend.BlendTime=0.25 }
	//   arm 2 (hard cut)  FMontageBlendSettings{ BlendMode=Standard,        Blend.BlendTime=0.0  }
	// (Montage_PlayInternal blends the superseded montage OUT using the NEW montage's blend-in settings, so
	// arm 2 is a true single-frame swap on both sides.) The test asserts the inertial arm stays UNDER the
	// per-run derived threshold PopMax (see Report(); there is no fixed cm/frame constant) *and* that the
	// hard cut EXCEEDS it. If the control ever passes, the detector does not discriminate and the test fails
	// loudly rather than reporting a green it cannot defend.
	//
	// The arms are also asserted to AGREE on both natural-motion floors (walk and run). They measure those
	// floors independently, from separate windows, so agreement is what makes "identical in everything but
	// blend mechanism" a checked property instead of an assumption — a divergence means the arms differ in
	// something other than the one variable, which is exactly the confound this design exists to exclude.
	//
	// A third number is logged for calibration honesty: the walk-only steady-state max per-frame delta
	// (natural animated motion), so the threshold can be read against the floor it must clear.
	//
	// FIXED FRAME BASIS (first-run finding, kept as the reason this is here). The first build of this test
	// measured raw per-frame deltas under the automation PIE loop and did NOT discriminate: inertial
	// 174.4 cm/frame vs hard-cut control 159.5 cm/frame (ratio 0.91x), with a walk-only natural-motion floor
	// of 303.8 cm/frame — LARGER than either arm. The signal was frame-time noise, not blending: automation
	// PIE hitches to ~0.3s frames, and a hand travelling ~10 m/s covers ~3 m in such a frame, swamping any
	// pose discontinuity. "cm per frame" is only a meaningful unit if a frame is a fixed amount of time, so
	// the command pins FApp fixed-time-step at kSimHz for the whole run (restored unconditionally by the
	// FFixedTimeStepScope the command holds by value, so no exit path — not even an external
	// DequeueAllCommands — can leak a pinned clock into later tests). Both cm/frame and the dt-normalized
	// cm/s are reported.

	// Max per-frame component-space bone displacement (cm) between two pose samples, plus which bone.
	float MaxComponentBoneDeltaCm(const TArray<FTransform>& Prev, const TArray<FTransform>& Cur, int32& OutBoneIdx)
	{
		OutBoneIdx = INDEX_NONE;
		if (Prev.Num() == 0 || Prev.Num() != Cur.Num()) { return -1.f; }
		float MaxDelta = 0.f;
		for (int32 i = 0; i < Cur.Num(); ++i)
		{
			const float D = static_cast<float>(FVector::Dist(Prev[i].GetLocation(), Cur[i].GetLocation()));
			if (D > MaxDelta) { MaxDelta = D; OutBoneIdx = i; }
		}
		return MaxDelta;
	}

	// Max per-frame component-space bone ROTATION (deg) between two pose samples, plus which bone.
	//
	// WHY THIS EXISTS. MaxComponentBoneDeltaCm above compares GetLocation() and nothing else, so a bone
	// that SPINS about an unmoved origin is invisible to it. A rotating spine or shoulder usually shifts
	// its descendants' origins and gets caught indirectly, which is how the positional-only gate looked
	// adequate for so long — but a LEAF, twist or end bone has no descendants to betray it, and a 45 deg
	// one-frame snap on one reads as a perfectly seamless blend. Scale is deliberately not measured: the
	// clips here do not animate it, so a scale channel would be a gate with no negative control.
	float MaxComponentBoneAngleDeg(const TArray<FTransform>& Prev, const TArray<FTransform>& Cur, int32& OutBoneIdx)
	{
		OutBoneIdx = INDEX_NONE;
		if (Prev.Num() == 0 || Prev.Num() != Cur.Num()) { return -1.f; }
		float MaxDeg = 0.f;
		for (int32 i = 0; i < Cur.Num(); ++i)
		{
			// AngularDistance is the angle BETWEEN two rotations and is double-cover safe, unlike
			// comparing GetAngle() of each.
			const float A = FMath::RadiansToDegrees(Prev[i].GetRotation().AngularDistance(Cur[i].GetRotation()));
			if (A > MaxDeg) { MaxDeg = A; OutBoneIdx = i; }
		}
		return MaxDeg;
	}

	// The seam gate's derivation, in ONE place. Two channels (cm and deg) are gated identically and
	// the angular channel's whole justification is that its derivation is the positional one verbatim -
	// same factor, same floors, same epsilon guards. Typed twice that claim decays into a comment nobody
	// can enforce; called twice it is structural. Message prose stays per-channel, because the two do
	// NOT say the same thing (only the cm channel reports the transient bands).
	struct FSeamGate
	{
		float DerivFloor = 0.f;
		float PopMax = 0.f;
		bool  bFloorValid = false;   // did the sampler produce a floor at all
		bool  bSmooth = false;       // the inertial arm stayed under the gate
		bool  bControlFired = false; // the hard-cut arm cleared it, so the gate CAN go red
	};
	FSeamGate MakeSeamGate(float RunFloor, float WalkFloor, float AbsMinFloor, float PopFactor,
	                       float InertialMaxPop, float HardCutMaxPop)
	{
		FSeamGate G;
		G.DerivFloor    = FMath::Max3(RunFloor, WalkFloor, AbsMinFloor);
		G.PopMax        = PopFactor * G.DerivFloor;
		G.bFloorValid   = (FMath::Max(RunFloor, WalkFloor) > KINDA_SMALL_NUMBER);
		G.bSmooth       = G.bFloorValid && (InertialMaxPop > KINDA_SMALL_NUMBER) && (InertialMaxPop < G.PopMax);
		G.bControlFired = G.bFloorValid && (HardCutMaxPop > G.PopMax);
		return G;
	}

	// RAII holder for the engine's fixed-time-step state. FMontageSeamlessCommand keeps one BY VALUE, so the
	// prior clock is restored by the destructor — which fires on every path the queue can take, including the
	// one no latent command can cover: FAutomationWorkerModule::HandleStopTestsMessage calls
	// DequeueAllCommands() on an external StopTests (controller abort, Session Frontend "Stop"), destroying the
	// queued commands without running them. A restore expressed as a *following command* is discarded there,
	// leaving bUseFixedTimeStep = true for the rest of the editor process and silently changing the simulation
	// rate of every later PIE test — precisely the confound this test exists to eliminate. DequeueAllCommands
	// destroys each command and normal completion pops (and destroys) it, so the destructor covers both.
	struct FFixedTimeStepScope
	{
		FFixedTimeStepScope()
			: bWasFixed(FApp::UseFixedTimeStep()), PrevDelta(FApp::GetFixedDeltaTime()) {}
		~FFixedTimeStepScope()
		{
			FApp::SetUseFixedTimeStep(bWasFixed);
			FApp::SetFixedDeltaTime(PrevDelta);
		}
		FFixedTimeStepScope(const FFixedTimeStepScope&) = delete;
		FFixedTimeStepScope& operator=(const FFixedTimeStepScope&) = delete;

		void Pin(double Hz) const { FApp::SetFixedDeltaTime(1.0 / Hz); FApp::SetUseFixedTimeStep(true); }

		const bool bWasFixed;
		const double PrevDelta;
	};

	// The two clips Pie.MontageSeamless plays, named ONCE so the machine-local gate in
	// FPieMontageSeamlessTest::RunTest and the load in FMontageSeamlessCommand::LoadClips cannot drift onto
	// different assets — a gate that checked for a clip the command does not load would skip a row that
	// could still have run, and the reverse would leave the RED this pair exists to remove.
	const TCHAR* const kGasWalkClip = TEXT("/Game/MT/GAS_Clips/MT_GAS_Walk_Fwd.MT_GAS_Walk_Fwd");
	const TCHAR* const kGasRunClip  = TEXT("/Game/MT/GAS_Clips/MT_GAS_Run_Fwd.MT_GAS_Run_Fwd");

	class FMontageSeamlessCommand : public IAutomationLatentCommand
	{
	public:
		explicit FMontageSeamlessCommand(FAutomationTestBase* InTest)
			: Test(InTest), StartTime(0.0) {}

		// ClockScope's own destructor (member, destroyed right after this one runs) does the actual restore;
		// this only logs it, so the pinned clock's lifetime stays visible in the test output instead of being
		// inferred. Runs on every exit path, including an external DequeueAllCommands().
		~FMontageSeamlessCommand()
		{
			Test->AddInfo(FString::Printf(TEXT("Restored fixed time step to %s (delta %.6f)"),
				ClockScope.bWasFixed ? TEXT("on") : TEXT("off"), ClockScope.PrevDelta));
		}

		virtual bool Update() override
		{
			if (StartTime == 0.0)
			{
				StartTime = FPlatformTime::Seconds();
				// Pin the clock for the whole run — see FIXED FRAME BASIS above. Restored by ClockScope's
				// destructor, never here (this command has many exit paths, and one of them is external).
				ClockScope.Pin(kSimHz);
			}
			const double Now = FPlatformTime::Seconds();

			APawn* Pawn = nullptr; USkeletalMeshComponent* Mesh = nullptr; UAnimInstance* Inst = nullptr;
			int32 LocoIdx = INDEX_NONE; const FBakedAnimationStateMachine* Loco = nullptr;
			if (!ResolvePossessedLocomotionContext(Pawn, Mesh, Inst, LocoIdx, Loco))
			{
				return PhaseBudgetExceeded(Test, Now, StartTime, PhaseName(Phase), TEXT("montage blend"), kPhaseBudgetSec);
			}
			const int32 IdleIdx = FindBakedStateIndex(*Loco, TEXT("Idle"));
			if (IdleIdx == INDEX_NONE)
			{
				Test->AddError(TEXT("Locomotion machine has no 'Idle' state — fixture broken"));
				return true;
			}

			// Sample the pose EVERY tick so the frame-to-frame delta is continuous across phase edges.
			const TArray<FTransform>& CS = Mesh->GetComponentSpaceTransforms();
			int32 BoneIdx = INDEX_NONE;
			const float FrameDelta = MaxComponentBoneDeltaCm(PrevCS, CS, BoneIdx);
			int32 BoneIdxDeg = INDEX_NONE;
			const float FrameAngleDeg = MaxComponentBoneAngleDeg(PrevCS, CS, BoneIdxDeg);
			PrevCS = CS;

			const float SlotW = Inst->GetSlotMontageLocalWeight(kDefaultSlot);

			switch (Phase)
			{
			case EPhase::Settle:
			{
				if (Inst->GetInstanceStateWeight(LocoIdx, IdleIdx) > 0.99f)
				{
					if (!LoadClips()) { return true; }
					if (!PlayWalk(*Inst)) { return true; }
					Test->AddInfo(FString::Printf(
						TEXT("[Settle] settled in Idle after %.1fs; MT_GAS_Walk_Fwd -> DefaultSlot (full-body). %d bones sampled per frame."),
						Now - StartTime, CS.Num()));
					Advance(EPhase::RampInertial, Now);
				}
				break;
			}

			// Both ramp phases are identical: wait for the walk montage to fully own the slot, and while
			// waiting record the walk-only steady-state per-frame delta (the natural-motion floor). The
			// accumulator is PER ARM (not one shared across both ramps) so each arm's floor is a max over
			// its own window and needs no argument about which frames it covers.
			case EPhase::RampInertial:
			case EPhase::RampHardCut:
			{
				const bool bInertialRamp = (Phase == EPhase::RampInertial);
				FArm& RampArm = bInertialRamp ? Inertial : HardCut;
				if (SlotW > kSlotWeightFloor) { ++PhaseFrames; }
				if (FrameDelta >= 0.f && SlotW > kSlotWeightFloor && FrameDelta > RampArm.WalkFloorCm)
				{
					RampArm.WalkFloorCm = FrameDelta;
				}
				if (FrameAngleDeg >= 0.f && SlotW > kSlotWeightFloor && FrameAngleDeg > RampArm.WalkFloorDeg)
				{
					RampArm.WalkFloorDeg = FrameAngleDeg;
				}
				if (SlotW > kSlotWeightFloor && PhaseFrames > kRampSettleFrames)
				{
					const bool bInertial = bInertialRamp;
					FMontageBlendSettings BlendIn;
					BlendIn.BlendMode = bInertial ? EMontageBlendMode::Inertialization : EMontageBlendMode::Standard;
					BlendIn.Blend.BlendTime = bInertial ? kInertialBlendSec : 0.f;
					FMontageBlendSettings BlendOut(kInertialBlendSec);

					UAnimMontage* M = Inst->PlaySlotAnimationAsDynamicMontage_WithBlendSettings(
						RunClip.Get(), kDefaultSlot, BlendIn, BlendOut, 1.f, kLoopCount);
					if (!M)
					{
						Test->AddError(FString::Printf(
							TEXT("[%s] PlaySlotAnimationAsDynamicMontage_WithBlendSettings(MT_GAS_Run_Fwd, 'DefaultSlot') returned null — the slot is not reachable in the graph"),
							PhaseName(Phase)));
						return true;
					}
					RunMontage.Reset(M);
					Test->AddInfo(FString::Printf(
						TEXT("[%s] %.1fs wall; walk owns DefaultSlot (local weight %.3f); swapping to MT_GAS_Run_Fwd with BlendMode=%s, BlendTime=%.2fs. This arm's walk-only steady-state max per-frame bone delta: %.3f cm."),
						PhaseName(Phase), Now - StartTime, SlotW, bInertial ? TEXT("Inertialization") : TEXT("Standard"),
						BlendIn.Blend.BlendTime, RampArm.WalkFloorCm));
					Advance(bInertial ? EPhase::MeasureInertial : EPhase::MeasureHardCut, Now);
				}
				break;
			}

			case EPhase::MeasureInertial:
			case EPhase::MeasureHardCut:
			{
				const bool bInertial = (Phase == EPhase::MeasureInertial);
				FArm& Arm = bInertial ? Inertial : HardCut;
				++PhaseFrames;
				// BOTH samplers must have produced a reading. They fail together today (identical size/empty
				// preconditions), but gating the ANGULAR accumulators on the POSITIONAL sampler's return made
				// one channel's validity depend on the other's - invisible while they agree, wrong the moment
				// either sampler's failure mode changes. Stated explicitly instead of inherited.
				if (FrameDelta >= 0.f && FrameAngleDeg >= 0.f)
				{
					if (FrameDelta > Arm.MaxPopCm) { Arm.MaxPopCm = FrameDelta; Arm.PopBone = BoneIdx; }
					if (FrameAngleDeg > Arm.MaxPopDeg) { Arm.MaxPopDeg = FrameAngleDeg; Arm.PopBoneDeg = BoneIdxDeg; }
					// SPLIT THE WINDOW. The first kBlendFrames frames after the swap are the blend transient;
					// everything after is the run clip playing on its own. Maxing over the WHOLE window (what
					// the first build reported) cannot distinguish "the blend adds nothing above the run
					// clip's own natural motion" from "the blend peaks well above a low run floor" — and only
					// the former supports the word *seamless*. Both halves come from samples already taken.
					//
					// GUARD BAND at the split, excluded from BOTH accumulators. The montage is played in the
					// PREVIOUS latent Update, so the frame index at which the blend's first affected pose
					// lands is off-by-at-most-one against PhaseFrames == 1. If it lands late the blend
					// occupies frames 2..kBlendFrames+1 and frame kBlendFrames+1 would be counted into
					// RunFloorCm — which BIASES TOWARD THE CLAIM in two ways at once: it inflates the run
					// floor (loosening the derived PopMax) and it deflates TransientRatio (understating the
					// blend against that floor). The bias is small and bounded — UE's inertializer terminates
					// exactly at Duration and no blend profile is set, so the overhang cannot exceed the
					// scheduling slop — but the ratio lands in a marginal band where a small-margin judgement
					// is being made, and this is the one boundary that could move it. Dropping the frames
					// outright (rather than assigning them to either side) is the choice that cannot flatter
					// either accumulator. MaxPopCm above still spans EVERY frame: the pass/fail gate stays
					// conservative and no excluded frame can hide a pop.
					if (PhaseFrames > kBlendFrames + kGuardFrames)
					{
						if (FrameDelta > Arm.RunFloorCm) { Arm.RunFloorCm = FrameDelta; }
						if (FrameAngleDeg > Arm.RunFloorDeg) { Arm.RunFloorDeg = FrameAngleDeg; }
					}
					else if (PhaseFrames <= kBlendFrames)
					{
						if (FrameDelta > Arm.BlendMaxCm)   { Arm.BlendMaxCm = FrameDelta; }
						if (FrameAngleDeg > Arm.BlendMaxDeg) { Arm.BlendMaxDeg = FrameAngleDeg; }
					}
				}
				if (SlotW < Arm.MinSlotW) { Arm.MinSlotW = SlotW; }

				if (PhaseFrames > kMeasureFrames)
				{
					Test->AddInfo(FString::Printf(
						TEXT("[%s] %.1fs wall; window closed (%d frames @ %.0f Hz = %.2fs sim): max per-frame component-space bone delta %.3f cm (%.1f cm/s) = blend window (first %d frames) %.3f cm vs run-only floor (%d frames, after a %d-frame guard band excluded from both) %.3f cm; min DefaultSlot weight %.3f."),
						PhaseName(Phase), Now - StartTime, kMeasureFrames, kSimHz, kMeasureFrames / kSimHz,
						Arm.MaxPopCm, Arm.MaxPopCm * kSimHz, kBlendFrames, Arm.BlendMaxCm,
						kMeasureFrames - kBlendFrames - kGuardFrames, kGuardFrames, Arm.RunFloorCm, Arm.MinSlotW));

					if (bInertial)
					{
						// Restart the same ramp for the control arm: clear the slot, replay walk.
						Inst->Montage_Stop(0.f);
						if (!PlayWalk(*Inst)) { return true; }
						Advance(EPhase::RampHardCut, Now);
					}
					else
					{
						Inst->Montage_Stop(0.f);
						Report();
						return true;
					}
				}
				break;
			}

			default: break;
			}

			return PhaseBudgetExceeded(Test, Now, StartTime, PhaseName(Phase), TEXT("montage blend"), kPhaseBudgetSec);
		}

	private:
		enum class EPhase : uint8 { Settle, RampInertial, MeasureInertial, RampHardCut, MeasureHardCut };

		static const TCHAR* PhaseName(EPhase P)
		{
			switch (P)
			{
			case EPhase::Settle:          return TEXT("Settle");
			case EPhase::RampInertial:    return TEXT("RampInertial");
			case EPhase::MeasureInertial: return TEXT("MeasureInertial");
			case EPhase::RampHardCut:     return TEXT("RampHardCut");
			default:                      return TEXT("MeasureHardCut");
			}
		}

		// StartTime doubles as the phase start: it is what PhaseBudgetExceeded measures against and what the
		// per-phase "%.1fs wall" figures are printed from.
		void Advance(EPhase Next, double Now) { Phase = Next; StartTime = Now; PhaseFrames = 0; }

		bool LoadClips()
		{
			WalkClip.Reset(LoadObject<UAnimSequence>(nullptr, kGasWalkClip));
			RunClip.Reset(LoadObject<UAnimSequence>(nullptr, kGasRunClip));
			if (!WalkClip.IsValid() || !RunClip.IsValid())
			{
				// STILL RED, deliberately. RunTest already skipped the row if the packages do not exist, so
				// reaching here means a clip is PRESENT and will not load — a real defect, not a clean
				// checkout. Do not soften this into a second skip.
				Test->AddError(TEXT("[Settle] failed to load /Game/MT/GAS_Clips/MT_GAS_Walk_Fwd and/or MT_GAS_Run_Fwd (Game Animation Sample clips; machine-local assets)"));
				return false;
			}
			return true;
		}

		bool PlayWalk(UAnimInstance& Inst)
		{
			UAnimMontage* M = Inst.PlaySlotAnimationAsDynamicMontage(
				WalkClip.Get(), kDefaultSlot, 0.1f, 0.25f, 1.f, kLoopCount);
			if (!M)
			{
				Test->AddError(TEXT("PlaySlotAnimationAsDynamicMontage(MT_GAS_Walk_Fwd, 'DefaultSlot') returned null — is the DefaultSlot node present on the full-body path?"));
				return false;
			}
			WalkMontage.Reset(M);
			return true;
		}

		// One half of the ARMS-AGREE gate (see Report()). Relative to the larger of the two so the tolerance
		// means the same thing on a fast rig and a slow one; the KINDA_SMALL_NUMBER short-circuit keeps a
		// pair of legitimately-zero floors from dividing by ~0 (bFloorValid already errors on that case).
		void ArmFloorsAgree(const TCHAR* Which, float InertialCm, float HardCutCm)
		{
			const float Larger = FMath::Max(InertialCm, HardCutCm);
			if (Larger <= KINDA_SMALL_NUMBER) { return; }
			const float RelDiff = FMath::Abs(InertialCm - HardCutCm) / Larger;
			if (RelDiff > kArmFloorTol)
			{
				Test->AddError(FString::Printf(
					TEXT("ARMS DIVERGED on the %s natural-motion floor: inertial arm %.3f cm/frame vs hard-cut arm %.3f (%.1f%% apart, tolerance %.1f%%). Both arms run the same clips through the same slot with the same sampler and differ ONLY in blend mechanism, which cannot change a floor measured outside the blend — so they are no longer the controlled comparison every cross-arm figure here depends on. Not reporting a discrimination ratio between two arms that are not comparable."),
					Which, InertialCm, HardCutCm, RelDiff * 100.f, kArmFloorTol * 100.f));
			}
		}

		void Report()
		{
			// THRESHOLD DERIVED FROM PHYSICS, NOT FROM THE CONTROL. The run clip's own steady-state motion is
			// the noise floor any pop must clear to be a pop at all, and it is measured in this same run, from
			// the same sampler, on whatever rig and clip pair the command was pointed at. A hard-coded cm/frame
			// constant is calibrated only for THIS pair on THIS rig; other montage chains use different clips
			// (MT_GAS_Jump_Land, Gesture_A/B) on a possibly retargeted skeleton, where a fixed 75.0 would be an
			// uncalibrated number. kPopFactor is fixed a priori; the threshold auto-calibrates under it.
			//
			// FLOORED, because deriving from the INCOMING clip alone is pathologically tight in exactly the
			// case those chains hit. The blend transient scales with the POSE DIFFERENCE between outgoing and
			// incoming clips, and that difference is LARGEST when the incoming clip is slow — a run ->
			// MT_GAS_Gesture_B (bench idle, the lowest-motion asset in the set) chain drives RunFloor toward
			// zero while leaving the transient untouched, so kPopFactor * RunFloor becomes a bound a
			// perfectly correct blend cannot clear: a FALSE RED. The transient is bounded by the FASTER of
			// the two clips, not the slower, so the derivation takes the max of both measured floors. The
			// natural response to that false RED would be to raise kPopFactor — which is precisely the
			// tuning this derivation exists to avoid, so the fix is the floor and kPopFactor stays 3.0.
			const FSeamGate GateCm = MakeSeamGate(Inertial.RunFloorCm, Inertial.WalkFloorCm, kAbsMinFloorCm,
			                                      kPopFactor, Inertial.MaxPopCm, HardCut.MaxPopCm);
			const float RunFloor = Inertial.RunFloorCm;
			const float DerivFloor = GateCm.DerivFloor;
			const float PopMax   = GateCm.PopMax;

			// > KINDA_SMALL_NUMBER, not >= 0: an exactly-zero maximum means the sampler never ran, which
			// would otherwise read as the most seamless blend imaginable. Tested against BOTH floors, not
			// RunFloor alone: now that kAbsMinFloorCm keeps the threshold meaningful for a legitimately
			// near-static incoming clip, gating on RunFloor would re-introduce the same false RED through a
			// different branch (a run -> bench-idle swap is a valid measurement, not a dead sampler). A dead
			// sampler produces zero on both windows, which this still catches.
			const bool bFloorValid     = GateCm.bFloorValid;
			const bool bInertialSmooth = GateCm.bSmooth;
			const bool bControlFired   = GateCm.bControlFired;
			const bool bSlotHeld       = (Inertial.MinSlotW > kSlotWeightFloor);

			// Is the blend transient even distinguishable from the run clip's own motion? Both figures are
			// maxima over disjoint frame sets of ONE continuous signal, so if the blend contributes nothing
			// measurable the blend-window max sits at or below the run floor. Three descriptive bands, none
			// of which gates pass/fail (that is kPopFactor's job alone) — they exist so the *strength* of the
			// seamlessness claim is stated from the data instead of asserted. Measured here: 1.11x, which is
			// why the marginal band exists at all. The band's prose is deliberately SYMMETRIC — at that ratio
			// calling it a "real transient" overclaims, and so does calling it "adds nothing", because the
			// measurement cannot choose between them. Moving the boundary until it read as either would be
			// tuning. (The band's post-hoc origin is disclosed and it gates nothing.)
			// Divides by RunFloor specifically (this ratio IS "blend vs the incoming clip's own motion"), so
			// it needs its own non-zero guard now that bFloorValid tolerates a near-static incoming clip.
			const bool bRunFloorMeasured  = (RunFloor > KINDA_SMALL_NUMBER);
			const float TransientRatio = bRunFloorMeasured ? (Inertial.BlendMaxCm / RunFloor) : -1.f;
			const bool bTransientVisible  = bRunFloorMeasured && (TransientRatio > 1.10f);
			const bool bTransientMarginal = bTransientVisible && (TransientRatio < 1.25f);

			Test->AddInfo(FString::Printf(
				TEXT("SEAMLESSNESS @ %.0f Hz pinned — four figures, one run, one sampler: walk-only floor %.3f cm/frame (inertial arm) / %.3f (hard-cut arm); run-only floor %.3f cm/frame (%.1f cm/s); inertial walk->run swap %.3f cm/frame (%.1f cm/s), of which blend window %.3f; hard-cut control %.3f cm/frame (%.1f cm/s). Threshold %.2f = %.1fx max(run %.3f, walk %.3f, abs-min %.2f) = %.3f (derived per run, not fixed; floored by the faster clip because the transient scales with the pose difference, not with the incoming clip alone). Discrimination ratio %.2fx."),
				kSimHz, Inertial.WalkFloorCm, HardCut.WalkFloorCm,
				RunFloor, RunFloor * kSimHz,
				Inertial.MaxPopCm, Inertial.MaxPopCm * kSimHz, Inertial.BlendMaxCm,
				HardCut.MaxPopCm, HardCut.MaxPopCm * kSimHz,
				PopMax, kPopFactor, RunFloor, Inertial.WalkFloorCm, kAbsMinFloorCm, DerivFloor,
				Inertial.MaxPopCm > KINDA_SMALL_NUMBER ? HardCut.MaxPopCm / Inertial.MaxPopCm : -1.f));

			// The honest form of the seamlessness claim, stated from the data rather than asserted.
			if (bTransientMarginal)
			{
				Test->AddInfo(FString::Printf(
					TEXT("BLEND TRANSIENT IS MARGINAL against the run clip's natural motion: the blend window peaks at %.3f cm/frame against a run-only floor of %.3f — only %.2fx. At this ratio the measurement CANNOT DISTINGUISH the two available readings: 'the blend adds nothing measurable above the run clip's own motion' and 'the blend adds a small (~%.0f%%) transient on top of it' are both consistent with the data, and both are consistent with seamless. What this test PROVES is the narrow claim: the blend stays under %.1fx the derived floor, and ~%.1fx below a hard cut of the SAME two clips through the SAME slot (%.3f cm/frame, %.1fx this floor). The STRONG form — that the swap adds nothing at all — is admitted by these numbers but NOT established by them."),
					Inertial.BlendMaxCm, RunFloor, TransientRatio, (TransientRatio - 1.f) * 100.f,
					kPopFactor, Inertial.MaxPopCm > KINDA_SMALL_NUMBER ? HardCut.MaxPopCm / Inertial.MaxPopCm : -1.f,
					HardCut.MaxPopCm, HardCut.MaxPopCm / RunFloor));
			}
			else if (bTransientVisible)
			{
				Test->AddInfo(FString::Printf(
					TEXT("BLEND TRANSIENT IS DISTINGUISHABLE from the run clip's natural motion: the blend window peaks at %.3f cm/frame against a run-only floor of %.3f (%.2fx). The claim is therefore the NARROW one — the inertial swap produces a real transient, but one that stays below %.2f cm/frame where the hard cut reaches %.3f."),
					Inertial.BlendMaxCm, RunFloor, TransientRatio, PopMax, HardCut.MaxPopCm));
			}
			else if (bRunFloorMeasured)
			{
				Test->AddInfo(FString::Printf(
					TEXT("BLEND TRANSIENT IS NOT DISTINGUISHABLE from the run clip's natural motion: the blend window peaks at %.3f cm/frame against a run-only floor of %.3f — the inertial swap adds nothing measurable above what the run clip does on its own. This is the STRONGEST form of the claim; the detector is not blind, because the hard cut of the same two clips reaches %.3f."),
					Inertial.BlendMaxCm, RunFloor, HardCut.MaxPopCm));
			}
			else
			{
				// Legitimate for a near-static incoming clip (the run -> Gesture_B chain is one). The pass/fail gate still
				// stands on the floored threshold; only this ratio is unavailable, and saying so beats
				// printing "adds nothing measurable" about a division that was never performed.
				Test->AddInfo(FString::Printf(
					TEXT("BLEND TRANSIENT RATIO UNAVAILABLE: the run-only floor is %.6f cm/frame — the incoming clip is essentially static, so 'blend vs the incoming clip's own motion' has no denominator. The pass/fail gate is unaffected (the threshold is floored at %.1fx max(run, walk %.3f, abs-min %.2f) = %.2f); the blend window peaked at %.3f cm/frame against a hard cut of %.3f."),
					RunFloor, kPopFactor, Inertial.WalkFloorCm, kAbsMinFloorCm, PopMax,
					Inertial.BlendMaxCm, HardCut.MaxPopCm));
			}

			if (!bFloorValid)
			{
				Test->AddError(FString::Printf(
					TEXT("NO NATURAL-MOTION FLOOR ON EITHER WINDOW: max per-frame delta was %.6f cm over the %d post-blend frames AND %.6f cm over the walk ramp — the sampler never ran, so the derived threshold is meaningless and no figure below can be trusted."),
					RunFloor, kMeasureFrames - kBlendFrames - kGuardFrames, Inertial.WalkFloorCm));
			}
			if (bFloorValid && !bControlFired)
			{
				Test->AddError(FString::Printf(
					TEXT("CONFOUND: the pop detector did NOT fire on the hard-cut control (%.3f cm/frame, needs > %.2f). The detector cannot tell a smooth blend from a cut, so the inertial arm's %.3f cm/frame proves nothing. Not tuning kPopFactor to manufacture a pass."),
					HardCut.MaxPopCm, PopMax, Inertial.MaxPopCm));
			}
			if (bFloorValid && !bInertialSmooth)
			{
				Test->AddError(FString::Printf(
					TEXT("POP: the inertial walk->run swap through DefaultSlot exceeded the pop threshold (%.3f cm/frame >= %.2f = %.1fx max(run-only floor %.3f, walk-only floor %.3f, abs-min %.2f)) on bone index %d. Either the Inertialization node is not downstream of the DefaultSlot node, or the blend is genuinely not seamless."),
					Inertial.MaxPopCm, PopMax, kPopFactor, RunFloor, Inertial.WalkFloorCm, kAbsMinFloorCm, Inertial.PopBone));
			}
			if (!bSlotHeld)
			{
				Test->AddError(FString::Printf(
					TEXT("SLOT GAP: DefaultSlot local weight dropped to %.3f mid-blend (floor %.2f) — the slot handed the pose back to locomotion instead of a montage owning it throughout."),
					Inertial.MinSlotW, kSlotWeightFloor));
			}

			// ARMS-AGREE GATE. The two arms measure both natural-motion floors independently, over separate
			// windows, running the same ramp and measure code against the same clips through the same slot —
			// so they SHOULD agree, and their agreement was previously only noted in passing. Asserted here,
			// because it is the strongest available evidence that the arms really are identical in everything
			// but blend mechanism: a divergence means they differ in something ELSE, and every cross-arm
			// comparison below (the discrimination ratio, the control firing, the derived threshold applying
			// to both) silently stops being a controlled comparison. That is precisely the confound this
			// whole two-arm design exists to exclude, so it fails rather than being read past.
			// ===================== ANGULAR SEAM =====================
			// Every figure above is a bone ORIGIN displacement. A bone that rotates about an origin it does
			// not move contributes nothing to it, so a leaf, twist or end bone snapping tens of degrees in a
			// single frame passed this test as a flawless blend. Rotations higher in the chain usually drag
			// descendant origins and were caught indirectly, which is why the gap survived. The derivation
			// here is the positional one verbatim - same kPopFactor, same windows - so this adds a CHANNEL
			// rather than a tuned constant.
			const FSeamGate GateDeg = MakeSeamGate(Inertial.RunFloorDeg, Inertial.WalkFloorDeg, kAbsMinFloorDeg,
			                                       kPopFactor, Inertial.MaxPopDeg, HardCut.MaxPopDeg);
			const float RunFloorDeg        = Inertial.RunFloorDeg;
			const float DerivFloorDeg      = GateDeg.DerivFloor;
			const float PopMaxDeg          = GateDeg.PopMax;
			const bool  bFloorValidDeg     = GateDeg.bFloorValid;
			const bool  bInertialSmoothDeg = GateDeg.bSmooth;
			const bool  bControlFiredDeg   = GateDeg.bControlFired;
			Test->AddInfo(FString::Printf(
				TEXT("[Seamless/deg] floors walk=%.3f run=%.3f -> deriv %.3f (abs min %.2f); gate %.1fx = %.3f deg/frame. Inertial max %.3f deg (bone %d), blend-window max %.3f. Hard-cut control max %.3f deg."),
				Inertial.WalkFloorDeg, RunFloorDeg, DerivFloorDeg, kAbsMinFloorDeg, kPopFactor, PopMaxDeg,
				Inertial.MaxPopDeg, Inertial.PopBoneDeg, Inertial.BlendMaxDeg, HardCut.MaxPopDeg));
			if (!bFloorValidDeg)
			{
				Test->AddError(TEXT("[Seamless/deg] BOTH ANGULAR FLOORS ARE ZERO - the angular sampler never ran, which would otherwise read as the smoothest blend measurable"));
			}
			// INDEPENDENT ifs, NOT else-if. Chained, an angular pop
			// suppressed the "negative control did not fire" line: the reader fixes the pop, re-runs,
			// gets a green angular channel - and the green still means nothing, because the detector
			// was never shown able to fire. The cm channel reports its two independently for this
			// exact reason.
			if (bFloorValidDeg && !bInertialSmoothDeg)
			{
				Test->AddError(FString::Printf(
					TEXT("[Seamless/deg] ANGULAR POP: inertial max %.3f deg/frame on bone %d is not < %.3f (%.1fx the %.3f deg floor). The cm gate cannot see this - a bone rotating about a stationary origin displaces no origin at all."),
					Inertial.MaxPopDeg, Inertial.PopBoneDeg, PopMaxDeg, kPopFactor, DerivFloorDeg));
			}
			if (bFloorValidDeg && !bControlFiredDeg)
			{
				Test->AddError(FString::Printf(
					TEXT("[Seamless/deg] NEGATIVE CONTROL DID NOT FIRE: the hard-cut arm reached only %.3f deg/frame, which does not exceed the %.3f gate. A discontinuity this gate cannot tell from a blend has not been demonstrated, so its green says nothing."),
					HardCut.MaxPopDeg, PopMaxDeg));
			}
			// BOTH angular floors, not just the run floor. WalkFloorDeg feeds
			// DerivFloorDeg through MakeSeamGate's Max3 and therefore sets PopMaxDeg, so a cross-arm
			// divergence in the walk ramp silently moves the angular threshold. The cm channel asserts
			// both; asserting one here left the gate half-controlled.
			ArmFloorsAgree(TEXT("angular run-only"), Inertial.RunFloorDeg, HardCut.RunFloorDeg);
			ArmFloorsAgree(TEXT("angular walk-only"), Inertial.WalkFloorDeg, HardCut.WalkFloorDeg);

			ArmFloorsAgree(TEXT("run-only"), Inertial.RunFloorCm, HardCut.RunFloorCm);
			ArmFloorsAgree(TEXT("walk-only"), Inertial.WalkFloorCm, HardCut.WalkFloorCm);
			// THE BANNER SPEAKS FOR BOTH CHANNELS, so it must be gated on both.
			// Gated on cm alone it printed "SEAMLESS BLEND PROVEN" into the same
			// test log in which the angular gate had just AddError'd - a row that is RED while its
			// own report claims proof, which is precisely the over-claim the write-ups
			// were cleaned of.
			if (bControlFired && bInertialSmooth && bSlotHeld
				&& bFloorValidDeg && bInertialSmoothDeg && bControlFiredDeg)
			{
				Test->AddInfo(FString::Printf(
					TEXT("SEAMLESS BLEND PROVEN, DETECTOR PROVEN RED ON THE CONTROL: the same detector that reports %.3f cm/frame for the inertial swap reports %.3f cm/frame for a no-inertialization hard cut of the SAME two clips through the SAME slot, against a threshold derived from the run clip's own motion."),
					Inertial.MaxPopCm, HardCut.MaxPopCm));
			}
		}

		// The DefaultSlot node in the Template, on the full-body path (post LayeredBlendPerBone).
		static const FName kDefaultSlot;
		// Pinned simulation rate — makes "per frame" a fixed unit (see FIXED FRAME BASIS above). Phase
		// lengths are counted in FRAMES, not wall-clock seconds: under a pinned clock the two diverge.
		static constexpr double kSimHz = 60.0;
		// Per-phase budget, in WALL-CLOCK seconds — while phase progress is counted in PINNED frames, so the
		// two units do not track each other. Pinning does not make a frame render faster; it makes the frame
		// claim 1/60 s. The margin used to be an unmeasured inference, so each phase now prints its own
		// "%.1fs wall" and it is not: MEASURED, all five phases land at 0.1-0.6 s (a 45-frame
		// phase costs ~0.4 s, i.e. ~9 ms/frame — this PIE loop does NOT hitch to the ~0.3 s/frame that the
		// earlier unpinned runs showed, so the inherited 20 s default was never close to tight here). 60 s is kept
		// anyway as a deliberate ~100x margin: this is the phase length most exposed to slower hardware, and
		// the log now makes the true cost visible rather than leaving the next reader to infer it.
		static constexpr double kPhaseBudgetSec = 60.0;
		// Blend length for the inertial arm; the control uses 0.0 (single-frame swap).
		static constexpr float kInertialBlendSec = 0.25f;
		// Let the walk montage own the slot for this many frames before swapping, so the measured floor is
		// steady-state animated motion and not the walk montage's own blend-in ramp.
		static constexpr int32 kRampSettleFrames = 45;   // 0.75s @ 60Hz
		// Measurement window: comfortably longer than kInertialBlendSec so the whole blend is covered. The
		// first kBlendFrames of it are the blend transient; the rest is the run clip on its own.
		static constexpr int32 kMeasureFrames = 45;      // 0.75s @ 60Hz
		static constexpr int32 kBlendFrames = static_cast<int32>(kInertialBlendSec * kSimHz);  // 15 = 0.25s
		// Frames straddling the blend/run split, excluded from BOTH accumulators — see the GUARD BAND note in
		// the measure phase. 2 covers the worst case the mechanism admits: the montage is played in the
		// previous latent Update, so the blend's first affected pose can land one frame later than
		// PhaseFrames == 1, and the inertializer terminates exactly at Duration (no blend profile is set) so
		// the overhang cannot exceed that scheduling slop. Wider would only discard honest run-clip frames
		// and shrink the run floor's sample count for nothing. Costs 2 of the 30 post-blend frames.
		static constexpr int32 kGuardFrames = 2;
		// Loop the clips so neither runs out mid-measurement (walk is 4.0s, run 2.5s per GAS_Clips_manifest).
		static constexpr int32 kLoopCount = 20;
		// The pop threshold is DERIVED per run as kPopFactor * (run-only natural-motion floor) — see Report().
		// The factor, not the threshold, is the fixed choice: a pop is only a pop if it clears the clip's own
		// steady-state motion by a wide margin, and 3x is wide while leaving the order-of-magnitude separation
		// between a blend and a cut (measured 11x) untouched. Fixed a priori and NOT retuned to preserve a
		// headline number; the previous build's hard-coded 75.0 cm/frame was calibrated for exactly one clip
		// pair on exactly one rig and would have travelled silently into the other montage chains.
		static constexpr float kPopFactor = 3.0f;
		// Absolute lower bound on the derived threshold's basis, in cm/frame at kSimHz. It binds only when
		// BOTH clips are near-static (a bench-idle -> bench-idle swap), where max(run, walk) is itself ~0 and
		// kPopFactor * that would bound a correct blend below its own transient. Chosen from the two-sided
		// constraint, not by trial:
		//   UPPER — the control must still fire. PopMax = 3 * 1.0 = 3.0 cm/frame, so a hard cut fires
		//   whenever the two poses differ by >3 cm on any bone. Two clips closer than that on EVERY bone are
		//   the same pose; there is no seam to be seamless about, and nothing is being asserted away.
		//   LOWER — a correct blend must stay under it. An inertial blend spreads a pose difference D over
		//   kInertialBlendSec, peaking near 2D/(kInertialBlendSec * kSimHz) per frame for a smooth ease, so
		//   3.0 cm/frame admits D up to ~22 cm — comfortably more than two near-static clips differ by.
		// 1.0 cm/frame is also 60 cm/s: below it a bone is not meaningfully moving, which is the sense in
		// which this is a floor on "natural motion" and not a tuning knob. It is inert for the walk/run pair
		// measured here (both floors are an order of magnitude above it) and must not be raised to rescue a
		// red — the whole point is that it is set by what a pop IS, not by what any run returned.
		static constexpr float kAbsMinFloorCm = 1.0f;
		// Angular twin of kAbsMinFloorCm. 0.5 deg/frame is a numerical-noise floor, not a tuned value:
		// it exists so a legitimately near-static incoming clip cannot drive the angular gate to zero.
		static constexpr float kAbsMinFloorDeg = 0.5f;
		// Tolerance on the two arms' independently measured natural-motion floors, as a fraction of the
		// larger. The arms run identical ramp and measure code against identical clips, so they should agree
		// closely (measured: bit-identical to three decimals under the pinned clock); 5% is loose enough not
		// to fire on rig- or clip-dependent jitter under an unpinned or slower host, and far tighter than any
		// difference a genuine mechanism divergence would produce.
		static constexpr float kArmFloorTol = 0.05f;
		// Slot-ownership RESIDUAL GUARD (not evidence of a cross-blend). GetSlotMontageLocalWeight ->
		// FAnimInstanceProxy::GetSlotWeight SUMS the weights of every montage valid for the slot and clamps to
		// 1.0, so a same-slot cross-blend (both montages counted) and a hard cut (StopAllMontagesByGroupName
		// removes the old and adds the new at full weight in the same call) BOTH read 1.0 — the signal cannot
		// distinguish them and must not be reported as if it could. What it does still catch: a montage
		// expiring or being stopped before the swap lands, which would drop the slot back to locomotion.
		// Precisely: no control can make this signal discriminate A CROSS-BLEND FROM A CUT — that is a
		// property of the summed-and-clamped engine signal, not of the test setup, and it is the claim the
		// original report overreached on. A control for what the guard NOW claims to catch is trivial by
		// contrast (a third arm that stops the walk montage and delays the run play by N frames drops the
		// slot weight to 0 and fires it); it is simply not worth the PIE time to build.
		static constexpr float kSlotWeightFloor = kPoseOwnershipFloor;

		// Per-arm measurements. Both arms run the SAME ramp and measure code against the SAME clips and slot,
		// differing only in blend mechanism, so every figure is directly comparable across the two.
		struct FArm
		{
			float WalkFloorCm = 0.f;   // max per-frame delta while walk alone owns the slot (pre-swap)
			float BlendMaxCm  = 0.f;   // max inside the blend window (first kBlendFrames after the swap)
			float RunFloorCm  = 0.f;   // max after the blend window: the run clip's own natural motion
			float MaxPopCm    = 0.f;   // max over the whole measure window == max(BlendMaxCm, RunFloorCm)
			// Angular mirror of the four above, same windows, same accumulation. Origins-only was the gap.
			float WalkFloorDeg = 0.f;
			float BlendMaxDeg  = 0.f;
			float RunFloorDeg  = 0.f;
			float MaxPopDeg    = 0.f;
			int32 PopBoneDeg   = INDEX_NONE;
			float MinSlotW    = 1.f;
			int32 PopBone     = INDEX_NONE;
		};

		FAutomationTestBase* Test;
		// Constructed with the command, destroyed with it — including on an external DequeueAllCommands().
		FFixedTimeStepScope ClockScope;
		double StartTime;
		int32 PhaseFrames = 0;
		EPhase Phase = EPhase::Settle;

		TStrongObjectPtr<UAnimSequence> WalkClip;
		TStrongObjectPtr<UAnimSequence> RunClip;
		TStrongObjectPtr<UAnimMontage> WalkMontage;
		TStrongObjectPtr<UAnimMontage> RunMontage;

		TArray<FTransform> PrevCS;
		FArm Inertial;
		FArm HardCut;
	};

	const FName FMontageSeamlessCommand::kDefaultSlot(TEXT("DefaultSlot"));

	// ---- Bone-level foot-IK gate proof helpers --------------------------------------
	// Ramp geometry as built + engine-verified:
	//   Ramp_20deg surface height depends ONLY on world X (pitched about Y).
	//   low_top_world  (X=500.0,    Z=0.0)      <- floor meets ramp
	//   high_top_world (X=1063.816, Z=205.212)  <- ramp meets Landing_TopOfRamp (flat top)
	constexpr double kRampLowX  = 500.0;
	constexpr double kRampHighX = 1063.816;
	constexpr double kRampHighZ = 205.212;

	// Analytic ramp-surface world Z directly under a foot at world X (robust comparison).
	// Below the low seam -> floor (0.0); past the high seam -> Landing_TopOfRamp flat top (205.212).
	double ExpectedRampGroundZ(double X)
	{
		if (X <= kRampLowX)  { return 0.0; }
		if (X >= kRampHighX) { return kRampHighZ; }
		return (X - kRampLowX) / (kRampHighX - kRampLowX) * kRampHighZ;
	}

	// Both foot sockets' world-space locations in one shot ("verify the bone exists" discipline is
	// enforced by the caller via GetBoneIndex before trusting these).
	struct FFootSockets
	{
		FVector L = FVector::ZeroVector;
		FVector R = FVector::ZeroVector;
	};
	FFootSockets ReadFootSockets(USkeletalMeshComponent& Mesh)
	{
		FFootSockets S;
		S.L = Mesh.GetSocketLocation(FName(TEXT("foot_l")));
		S.R = Mesh.GetSocketLocation(FName(TEXT("foot_r")));
		return S;
	}

	// Self-documenting one-liner for a settled/airborne foot sample: raw X/Y/Z for each foot, the
	// two robust discriminators (per-foot clearance above the analytic ramp surface under it, and the
	// foot-vs-foot |ΔZ|), plus the pawn root Z for context.
	FString FormatFootSample(const TCHAR* Tag, const FFootSockets& S, double PawnZ)
	{
		const double ExpL = ExpectedRampGroundZ(S.L.X);
		const double ExpR = ExpectedRampGroundZ(S.R.X);
		const double ClearL = S.L.Z - ExpL;   // foot height above the local ramp surface under it
		const double ClearR = S.R.Z - ExpR;
		const double dZfeet = FMath::Abs(S.L.Z - S.R.Z);
		const double dXfeet = FMath::Abs(S.L.X - S.R.X);
		return FString::Printf(
			TEXT("[%s] foot_l=(%.2f, %.2f, %.2f) foot_r=(%.2f, %.2f, %.2f) | dX_feet=%.2f dZ_feet=%.2f")
			TEXT(" | expGroundZ_l=%.2f expGroundZ_r=%.2f clearance_l=%.2f clearance_r=%.2f minClear=%.2f")
			TEXT(" | pawnZ=%.2f"),
			Tag, S.L.X, S.L.Y, S.L.Z, S.R.X, S.R.Y, S.R.Z, dXfeet, dZfeet,
			ExpL, ExpR, ClearL, ClearR, FMath::Min(ClearL, ClearR), PawnZ);
	}

	// ============================================================================================
	// MotionTerminal.Pie.FootIKNodeIntegration — WHAT THIS TEST ASSERTS.
	// SOURCE OF TRUTH: the foot-IK confirmation run (a structural node-removal A/B, summarised below).
	// The shipped ABP_MT_Locomotion foot IK is CONFIRMED to actively conform BOTH deform feet to the ramp —
	// established by a confound-immune measurement that STRUCTURALLY REMOVES the ControlRig node from the live
	// evaluated graph (proven absent: 12 vs 13 nodes in the GatherDebugData tree) rather than disabling it via
	// alpha/binding. Node-present vs proven-node-free: the split ramp stance (clrL~6.84 penetrating / clrR~17.75
	// floating) is evened to grounded, level soles (clrL~10.26 / clrR~10.61, |Δ|~0.35cm), identically at 3 ramp
	// positions and byte-identical across 2 PIE settles.
	//
	// HARD ASSERTIONS (all AddError-gated below) — now THREE:
	//   (1) foot_l / foot_r resolve on the possessed mesh (bone-name existence).
	//   (2) the ControlRig foot-IK node is PRESENT in the flattened, post-LayeredBlendPerBone (LBPB) anim tree
	//       — the MCP-authored CR-node insertion is structurally integrated and observable in the live graph.
	//   (3) GROUNDED-RAMP CONFORMANCE (re-grounded per the confirmation): at the settled grounded sample the
	//       deform soles are both on the ramp band [7,13]cm AND level (|clrL-clrR| < 4cm) — the evened stance a
	//       proven-node-free/inert rig CANNOT produce (it splits to clrL~6.84 / clrR~17.75, failing the band on
	//       BOTH sides). This gates a SMALL clearance spread (evenness, slope normalized out), NOT the raw-Z
	//       spread the old false-positive gated; it is a geometry measurement of the rendered deform feet, never
	//       an alpha/weight assertion. It is an ON-STATE SIGNATURE match, not itself a per-run confound-immune
	//       node-on-vs-off proof — that proof is the one-time establishing measurement summarised above.
	//
	// Airborne + Recover phases remain OBSERVATION-ONLY (AddInfo): the confirmation proved the GROUNDED
	// conformance; the airborne gate-release / landing recovery were not independently confound-immune-tested.
	//
	// HONEST HISTORY: the conformance claim went claim -> "INERT" (a CONFOUNDED adjudication — its node-off
	// baseline was an alpha/binding disable that left AnimNode_ControlRig running at W:100%, so it measured the
	// node ON in both arms) -> CONFIRMED (confound-immune STRUCTURAL removal). A disable-based reference is not
	// a control. research/foot-ik-erratum.md is retained as the documented FALSE-NEGATIVE record — it is
	// SUPERSEDED by the confirmation run above; do NOT treat its "nets-zero / undriven-ik_foot" hypothesis
	// as current. Locomotion / feet-cycle behavior is covered by Pie.RigLocomotion (unaffected by any of this).
	// ============================================================================================
	class FFootIKNodeIntegrationCommand : public IAutomationLatentCommand
	{
	public:
		explicit FFootIKNodeIntegrationCommand(FAutomationTestBase* InTest)
			: Test(InTest), StartTime(0.0) {}

		virtual bool Update() override
		{
			if (StartTime == 0.0) { StartTime = FPlatformTime::Seconds(); }
			const double Now = FPlatformTime::Seconds();

			// Pin the POSSESSED pawn (map GameMode auto-possesses BP_MT_TestPawn) — same as the commands above.
			APawn* Pawn = nullptr; USkeletalMeshComponent* Mesh = nullptr; UAnimInstance* Inst = nullptr;
			int32 MachineIdx = INDEX_NONE; const FBakedAnimationStateMachine* Baked = nullptr;
			const bool bResolved = ResolvePossessedLocomotionContext(Pawn, Mesh, Inst, MachineIdx, Baked);
			ACharacter* Character = Cast<ACharacter>(Pawn);
			UCharacterMovementComponent* CMC = Character ? Character->GetCharacterMovement() : nullptr;

			if (bResolved && Character && CMC)
			{
				const double PawnZ = Pawn->GetActorLocation().Z;
				const double PawnX = Pawn->GetActorLocation().X;

				if (Phase == EPhase::Settle)
				{
					// One-time bone-existence check ("verify before relying" discipline).
					if (!bBonesChecked)
					{
						const int32 IdxL = Mesh->GetBoneIndex(FName(TEXT("foot_l")));
						const int32 IdxR = Mesh->GetBoneIndex(FName(TEXT("foot_r")));
						bBonesChecked = true;
						if (IdxL == INDEX_NONE || IdxR == INDEX_NONE)
						{
							Test->AddError(FString::Printf(
								TEXT("[Settle] foot_l/foot_r not on this mesh (bone idx l=%d r=%d) — cannot probe foot sockets"),
								IdxL, IdxR));
							return true;
						}
						Test->AddInfo(FString::Printf(
							TEXT("[Settle] bone-name check OK: foot_l boneIdx=%d, foot_r boneIdx=%d (mesh '%s')"),
							IdxL, IdxR, *GetNameSafe(Mesh->GetSkeletalMeshAsset())));
					}

					const int32 IdleIdx = FindBakedStateIndex(*Baked, TEXT("Idle"));
					if (IdleIdx != INDEX_NONE && Inst->GetInstanceStateWeight(MachineIdx, IdleIdx) > 0.99f)
					{
						Test->AddInfo(FString::Printf(
							TEXT("[Settle] settled in Idle after %.1fs at pawnX=%.1f; driving onto ramp."),
							Now - StartTime, PawnX));
						Phase = EPhase::Drive;
						StartTime = Now;
					}
				}
				else if (Phase == EPhase::Drive)
				{
					// Sustained real movement toward +X (the driven command's proven driver). Stop pushing at X>=700 so
					// the coast settles near the ramp midpoint (~X=780; band is [500, 1063.816]).
					if (PawnX < 700.0)
					{
						Pawn->AddMovementInput(FVector(1.f, 0.f, 0.f), 1.f);
					}
					else
					{
						Test->AddInfo(FString::Printf(
							TEXT("[Drive] reached pawnX=%.1f (input released); settling on ramp."), PawnX));
						Phase = EPhase::GroundSettle;
						StartTime = Now;
					}
				}
				else if (Phase == EPhase::GroundSettle)
				{
					// Let residual motion decay: require low speed, grounded, and a min dwell before
					// sampling (foot IK needs a few frames to converge on the new stance).
					const bool bSlow = CMC->Velocity.Size() < 20.0;
					const bool bGrounded = !CMC->IsFalling();
					if (bSlow && bGrounded && (Now - StartTime) > 1.5)
					{
						const FFootSockets S = ReadFootSockets(*Mesh);
						double Grounded = -1.0; TryReadNumericVar(*Inst, TEXT("GroundedFlag"), Grounded);
						Test->AddInfo(FString::Printf(
							TEXT("[GroundSettle] GROUNDED sample (GroundedFlag=%.0f, IsFalling=%d, speed=%.1f, pawnX=%.1f):"),
							Grounded, CMC->IsFalling() ? 1 : 0, CMC->Velocity.Size(), PawnX));
						Test->AddInfo(FormatFootSample(TEXT("GROUNDED"), S, PawnZ));

						const double ClearL = S.L.Z - ExpectedRampGroundZ(S.L.X);
						const double ClearR = S.R.Z - ExpectedRampGroundZ(S.R.X);
						GroundedMinClear = FMath::Min(ClearL, ClearR);
						GroundedMaxClear = FMath::Max(ClearL, ClearR);
						GroundedDZfeet   = FMath::Abs(S.L.Z - S.R.Z);
						GroundedClearL   = ClearL;
						GroundedClearR   = ClearR;

						// Structural Mechanism-A check (presence only): the ControlRig node is in the tree.
						const TArray<FNodeDebugData::FFlattenedDebugData> Flat = MotionTerminal::GatherFlatNodeWeights(*Inst);
						Test->AddInfo(FString::Printf(TEXT("[GroundSettle] flattened node dump:\n%s"),
							*MotionTerminal::FormatFlatNodeWeights(Flat)));
						// Probe found the CR node's DebugLine is
						// "AnimNode_ControlRig<...> (CR_Mannequin_FootIK_C)" — match on "ControlRig".
						FString CRLine;
						const float CRWeight = FindNodeWeight(Flat, TEXT("ControlRig"), CRLine);
						if (CRWeight < 0.f)
						{
							Test->AddError(TEXT("[GroundSettle] structural check FAILED: no 'ControlRig' node in the flattened tree"));
						}
						else
						{
							Test->AddInfo(FString::Printf(
								TEXT("[GroundSettle] structural check OK: ControlRig node PRESENT — '%s' (AbsWeight %.3f; presence-only, NOT an alpha assertion)"),
								*CRLine, CRWeight));
						}

						// ON-STATE CONFORMANCE SIGNATURE (re-grounded on the confirmation run). Band + evenness, calibrated to the
						// confirmation's MEASURED node-on 10.3/10.6 and PROVEN node-off split 6.84/17.75
						// (the foot-IK confirmation run), for the mannequin-
						// family rigs (Manny/Quinn share SK_Mannequin + the same ABP). At the settled grounded sample the
						// working foot IK evens a split ramp stance to grounded, level soles: node-off the deform feet SPLIT
						// to clrL~6.84 (penetrating) / clrR~17.75 (floating ~7.8cm); the shipped rig conforms both to
						// clrL~10.26 / clrR~10.61 (|Δ|~0.35cm). The band [7,13] rejects the node-off split on BOTH sides
						// (6.84<7 AND 17.75>13); the evenness guard |clrL-clrR|<4 is the second gate.
						// HONEST SCOPE: this is an on-state SIGNATURE match, NOT itself a confound-immune node-on-vs-off
						// proof. A standing PIE test cannot cheaply structurally-remove the CR node each run, so it cannot
						// re-establish the node-present-vs-provably-node-free comparison — that confound-immune proof is the
						// one-time establishing measurement summarised in the header above. This test asserts the shipped rig
						// keeps producing the signature that measurement tied to the working node; do NOT read a green here
						// as a per-run confound-immune efficacy proof.
						// CORRECTED vs the old false-positive: the demoted assertion gated a LARGE raw-Z spread (dZ_feet>5cm)
						// which the split node-off stance ALSO passes; this gates a SMALL CLEARANCE spread (evenness, slope
						// normalized out) + a grounded band — only the conformed signature yields it. NEVER alpha-based: it
						// measures rendered deform-foot geometry, not node weight/binding.
						const double ClearSpread = FMath::Abs(GroundedClearL - GroundedClearR);
						if (GroundedClearL < 7.0 || GroundedClearL > 13.0 || GroundedClearR < 7.0 || GroundedClearR > 13.0)
						{
							Test->AddError(FString::Printf(
								TEXT("[GroundSettle] CONFORMANCE FAILED: a deform sole is off the ramp band [7,13]cm (clrL=%.2f clrR=%.2f) — a node-free split gives clrL~6.84 / clrR~17.75. See research/foot-ik-erratum.md"),
								GroundedClearL, GroundedClearR));
						}
						else if (ClearSpread >= 4.0)
						{
							Test->AddError(FString::Printf(
								TEXT("[GroundSettle] CONFORMANCE FAILED: deform soles not level (|clrL-clrR|=%.2f >= 4.0cm; node-free split ~10.9cm, conformed ~0.35cm) — foot IK not evening the ramp stance. See research/foot-ik-erratum.md"),
								ClearSpread));
						}
						else
						{
							Test->AddInfo(FString::Printf(
								TEXT("[GroundSettle] CONFORMANCE OK: soles grounded+level clrL=%.2f clrR=%.2f (|Δ|=%.2f < 4.0, band [7,13]) — on-state conformance signature the node-free split (6.84/17.75) fails (see research/foot-ik-erratum.md)"),
								GroundedClearL, GroundedClearR, ClearSpread));
						}

						// Force the gate OFF with a REAL jump (never a reflection override — the EventGraph
						// re-derives GroundedFlag from IsFalling every tick and would overwrite an override).
						Character->Jump();
						Test->AddInfo(TEXT("[GroundSettle] issued Character->Jump() to force the gate off via the real IsFalling->GroundedFlag->Alpha path."));
						Phase = EPhase::Airborne;
						StartTime = Now;
					}
					else if (Now - StartTime > 10.0)
					{
						Test->AddError(FString::Printf(
							TEXT("[GroundSettle] timed out (10s) waiting to settle on ramp (speed=%.1f, IsFalling=%d, pawnX=%.1f)"),
							CMC->Velocity.Size(), CMC->IsFalling() ? 1 : 0, PawnX));
						return true;
					}
				}
				else if (Phase == EPhase::Airborne)
				{
					double Grounded = 1.0; TryReadNumericVar(*Inst, TEXT("GroundedFlag"), Grounded);
					const bool bFalling = CMC->IsFalling();
					if (bFalling || Grounded < 0.5)
					{
						// Genuinely airborne (real end-to-end contract). Track the PEAK-clearance sample
						// across the airborne window (feet rise clear of the slope as the body lifts).
						const FFootSockets S = ReadFootSockets(*Mesh);
						const double MinClear = FMath::Min(S.L.Z - ExpectedRampGroundZ(S.L.X),
						                                    S.R.Z - ExpectedRampGroundZ(S.R.X));
						if (!bSawAirborne)
						{
							bSawAirborne = true;
							Test->AddInfo(FString::Printf(
								TEXT("[Airborne] FIRST confirmed-airborne tick (IsFalling=%d, GroundedFlag=%.0f):"),
								bFalling ? 1 : 0, Grounded));
							Test->AddInfo(FormatFootSample(TEXT("AIRBORNE-first"), S, PawnZ));

							// OBSERVATION ONLY: first-airborne-tick clearance growth is dominated by JUMP physics
							// (body-rise + leg-tuck lifting the feet off the slope). The GROUNDED conformance is now a
							// HARD assertion above (the foot-IK confirmation run); the airborne gate
							// RELEASE was NOT independently confound-immune-tested, so it stays logged, not gated.
							const double FirstClearL = S.L.Z - ExpectedRampGroundZ(S.L.X);
							const double FirstClearR = S.R.Z - ExpectedRampGroundZ(S.R.X);
							Test->AddInfo(FString::Printf(
								TEXT("[Airborne] OBSERVATION (jump physics, NOT IK gate): first-tick clearance foot_l %.2f->%.2f (Δ%.2f), foot_r %.2f->%.2f (Δ%.2f) — see research/foot-ik-erratum.md"),
								GroundedClearL, FirstClearL, FirstClearL - GroundedClearL,
								GroundedClearR, FirstClearR, FirstClearR - GroundedClearR));
						}
						if (MinClear > PeakAirborneMinClear)
						{
							PeakAirborneMinClear = MinClear;
							AirbornePeak = S;
							AirbornePeakPawnZ = PawnZ;
						}
					}
					else if (bSawAirborne)
					{
						// Landed again (IsFalling back to false) — log the peak airborne sample as an
						// observation, then re-settle to observe the pose recovery.
						Test->AddInfo(FString::Printf(TEXT("[Airborne] PEAK-clearance airborne sample:")));
						Test->AddInfo(FormatFootSample(TEXT("AIRBORNE-peak"), AirbornePeak, AirbornePeakPawnZ));
						// OBSERVATION ONLY: peak airborne clearance is the jumping body carrying the feet off the
						// slope — jump physics. The grounded conformance is asserted above; this peak is logged, never gated.
						Test->AddInfo(FString::Printf(
							TEXT("[Airborne] OBSERVATION (jump physics, NOT IK): peak minClearance %.2f (grounded was %.2f) — see research/foot-ik-erratum.md"),
							PeakAirborneMinClear, GroundedMinClear));
						Phase = EPhase::Recover;
						StartTime = Now;
					}
					if (Phase == EPhase::Airborne && (Now - StartTime) > 8.0)
					{
						Test->AddError(FString::Printf(
							TEXT("[Airborne] timed out (8s): never observed a clean airborne->land cycle (sawAirborne=%d, peakClear=%.2f)"),
							bSawAirborne ? 1 : 0, PeakAirborneMinClear));
						return true;
					}
				}
				else   // EPhase::Recover
				{
					const bool bSlow = CMC->Velocity.Size() < 20.0;
					const bool bGrounded = !CMC->IsFalling();
					if (bSlow && bGrounded && (Now - StartTime) > 1.5)
					{
						const FFootSockets S = ReadFootSockets(*Mesh);
						double Grounded = -1.0; TryReadNumericVar(*Inst, TEXT("GroundedFlag"), Grounded);
						const double ClearL = S.L.Z - ExpectedRampGroundZ(S.L.X);
						const double ClearR = S.R.Z - ExpectedRampGroundZ(S.R.X);
						const double MaxClear = FMath::Max(ClearL, ClearR);
						const double MinClear = FMath::Min(ClearL, ClearR);
						const double RecoverDZfeet = FMath::Abs(S.L.Z - S.R.Z);
						Test->AddInfo(FString::Printf(
							TEXT("[Recover] RE-SETTLED sample (GroundedFlag=%.0f, IsFalling=%d, speed=%.1f, pawnX=%.1f):"),
							Grounded, CMC->IsFalling() ? 1 : 0, CMC->Velocity.Size(), PawnX));
						Test->AddInfo(FormatFootSample(TEXT("RE-SETTLED"), S, PawnZ));
						// OBSERVATION ONLY: the re-settled clearance band + foot-vs-foot stagger describe the post-landing
						// re-settle. The grounded-ramp conformance is asserted at the first GroundSettle sample above
						// (the foot-IK confirmation run); this post-landing re-settle is logged, not
						// independently gated (the confirmation covered the grounded stance, not a landing-recovery gate).
						Test->AddInfo(FString::Printf(
							TEXT("[Recover] OBSERVATION (post-landing re-settle; grounded conformance asserted above): re-settled clearance min %.2f / max %.2f, foot-vs-foot dZ_feet=%.2f (grounded dZ was %.2f)"),
							MinClear, MaxClear, RecoverDZfeet, GroundedDZfeet));
						return true;   // full grounded->airborne->grounded POSE cycle observed (no IK-efficacy claim)
					}
					else if (Now - StartTime > 15.0)
					{
						Test->AddError(FString::Printf(
							TEXT("[Recover] timed out (15s) waiting to re-settle after landing (speed=%.1f, IsFalling=%d)"),
							CMC->Velocity.Size(), CMC->IsFalling() ? 1 : 0));
						return true;
					}
				}
			}

			// Global backstops (outer per-phase timeouts). The GroundSettle (10s),
			// Airborne (8s) and Recover (15s) per-phase timeouts above live INSIDE the pawn-resolution
			// branch (if Mesh && Inst && Character && CMC && Baked); if the pawn stops resolving mid-late-
			// phase they never fire, Update() returns false forever, PIE never ends, and the framework
			// spuriously times the whole test out. Mirror the sibling FDriveSpeedToLocomotionCommand
			// (its JumpProbe/BSOutput backstops are hoisted the same way): cap EVERY phase at the outer
			// level regardless of resolution, carrying the same budgets. When the pawn IS resolving the
			// inner timeout returns first; these fire only on a no-resolution stall. Nothing polls forever.
			if ((Phase == EPhase::Settle || Phase == EPhase::Drive) && (Now - StartTime) > 25.0)
			{
				Test->AddError(FString::Printf(TEXT("Timed out (25s) in %s phase (pawn/anim did not resolve or never reached the ramp)"),
					Phase == EPhase::Settle ? TEXT("Settle") : TEXT("Drive")));
				return true;
			}
			if (Phase == EPhase::GroundSettle && (Now - StartTime) > 10.0)
			{
				Test->AddError(TEXT("[GroundSettle] outer backstop 10s (pawn stopped resolving mid-phase) — never reached a grounded ramp sample"));
				return true;
			}
			if (Phase == EPhase::Airborne && (Now - StartTime) > 8.0)
			{
				Test->AddError(FString::Printf(
					TEXT("[Airborne] outer backstop 8s (pawn stopped resolving mid-phase) — never observed a clean airborne->land cycle (sawAirborne=%d)"),
					bSawAirborne ? 1 : 0));
				return true;
			}
			if (Phase == EPhase::Recover && (Now - StartTime) > 15.0)
			{
				Test->AddError(TEXT("[Recover] outer backstop 15s (pawn stopped resolving mid-phase) — never re-settled after landing"));
				return true;
			}
			return false;   // keep polling
		}

	private:
		enum class EPhase : uint8 { Settle, Drive, GroundSettle, Airborne, Recover };

		// GROUNDED-RAMP CONFORMANCE is a HARD assertion again (re-grounded per the confound-immune
		// foot-IK confirmation run): the GroundSettle sample AddError-gates
		// a SMALL clearance-evenness spread (|clrL-clrR| < 4cm) inside a grounded band [7,13]cm — the evened stance
		// a PROVEN-node-free rig cannot produce (it splits to clrL~6.84 / clrR~17.75). This is the CORRECTED discriminator: the
		// old false-positive gated a LARGE raw-Z spread (dZ_feet > 5cm) that the split inert stance ALSO passes, so
		// it was never valid; the evenness+band gate keys on conformance only. Historical grounded values (~10.2-10.7cm
		// clearance, dZ_feet ~10.8cm, airborne peak ~157cm) are consistent with the confirmed clrL~10.26 / clrR~10.61.
		// The airborne/recover clearance/peak numbers remain OBSERVATIONS (jump physics; the airborne gate-release
		// and landing recovery were not independently confound-immune-tested). HARD assertions this command keeps:
		// foot-bone existence, CR-node structural presence, AND grounded-ramp conformance (see header).
		// research/foot-ik-erratum.md is retained as the superseded false-negative record.

		FAutomationTestBase* Test;
		double StartTime;
		EPhase Phase = EPhase::Settle;
		bool bBonesChecked = false;
		double GroundedMinClear = 0.0;
		double GroundedMaxClear = 0.0;
		double GroundedDZfeet = 0.0;
		double GroundedClearL = -1.0e9;
		double GroundedClearR = -1.0e9;
		bool bSawAirborne = false;
		double PeakAirborneMinClear = -1.0e9;
		double AirbornePeakPawnZ = 0.0;
		FFootSockets AirbornePeak;
	};

	// ============================================================================================
	// The TURN-IN-PLACE assertion. Proves the nested TurnInPlace sub-state-machine
	// (inside the Locomotion/Idle state) ROTATES THE PAWN IN PLACE in BOTH directions when TurnYawDelta
	// crosses the +/-45 threshold. Phases (each with its own reset per-phase 20s backstop):
	//   Settle    — wait for Locomotion/Idle weight > 0.99 (idle, so the nested SM is live in TIP_Neutral)
	//               and capture the neutral root/pelvis/XY baseline.
	//   NeutralHold — hold the default TurnYawDelta=0 for a short window and MEASURE root's own idle sway.
	//   RightTurn — write TurnYawDelta=+90 (> +45 -> TIP_TurnRight); expect a POSITIVE root sweep.
	//   ReNeutral — write TurnYawDelta=0 (back through TIP_Neutral so root returns to ~0) and re-baseline.
	//   LeftTurn  — write TurnYawDelta=-90 (< -45 -> TIP_TurnLeft); expect a NEGATIVE root sweep.
	// (RightTurn is the original assertion; NeutralHold + LeftTurn were added so BOTH
	// directions of the shipped mechanism are gated — the LEFT RotateRootBone carried the identical
	// independent inert-pin risk and was never measured. Live probe confirmed it rotates: DELTA=-90 ->
	// rootCSsweep -57.58 deg, TIP_TurnLeft; DELTA=+90 -> +57.58 deg, TIP_TurnRight.)
	//
	// PRIMARY signal = the ROOT bone COMPONENT-SPACE yaw delta (SIGNED). root carries NO idle animation, so
	// the only thing that yaws it is the active turn state's AnimGraphNode_RotateRootBone (+/-60 deg local-
	// space root rotation, which propagates to the whole body). This makes the measurement CONFOUND-IMMUNE:
	// MM_Idle's large pelvis sway (~+/-22 deg world yaw) that swamps a pelvis-relative reading leaves root
	// untouched. Pelvis world-yaw delta is logged as corroboration only, never gated.
	//
	// HARD ASSERTIONS (all AddError-gated), per direction: (1) SIGNED root-yaw sweep past +/-kTurnYawMin in
	// the RIGHT direction for +90 and the LEFT direction for -90 — the body yaws the correct way (a wrong-
	// direction or inert node fails); (2) actor XY drift < kInPlaceMax — IN PLACE (RotateRootBone moves the
	// pose root, not the capsule); (3) TryReadNumericVar('TurnYawDelta') == the injected value — the write
	// STUCK (rules out an EventGraph re-derive clobbering the input; TurnYawDelta is a pure external var, so
	// this only confirms the ON-injected value is read-back-confirmed, NOT an OFF/ON isolation — the effect
	// itself is a delta FROM the neutral baseline, gated by this compound assertion; the pie_turninplace
	// probe does the direct OFF(0)/ON(+/-90) isolation); (4) Mechanism B — the nested TurnInPlace baked
	// machine is in TIP_TurnRight / TIP_TurnLeft (state weight > 0.5). NeutralHold additionally asserts the
	// neutral root sway stays BELOW kTurnYawMin, so the sway-free assumption is CHECKED, not assumed.
	// ============================================================================================
	class FTurnInPlaceCommand : public IAutomationLatentCommand
	{
	public:
		explicit FTurnInPlaceCommand(FAutomationTestBase* InTest)
			: Test(InTest), StartTime(0.0) {}

		virtual bool Update() override
		{
			if (StartTime == 0.0) { StartTime = FPlatformTime::Seconds(); }
			const double Now = FPlatformTime::Seconds();

			// The possessed-pawn -> Locomotion-baked-machine preamble (shared with the sibling
			// turn/aim/hand commands) — resolves Pawn/Mesh/Inst/LocoIdx/Loco or returns false (fall through to the budget).
			APawn* Pawn = nullptr; USkeletalMeshComponent* Mesh = nullptr; UAnimInstance* Inst = nullptr;
			int32 LocoIdx = INDEX_NONE;
			const FBakedAnimationStateMachine* Loco = nullptr;
			if (ResolvePossessedLocomotionContext(Pawn, Mesh, Inst, LocoIdx, Loco))
			{
				// Live root component-space yaw + pelvis world yaw (read every eligible tick).
				const double RootYaw   = Mesh->GetSocketTransform(FName(TEXT("root")), ERelativeTransformSpace::RTS_Component)
											.GetRotation().Rotator().Yaw;
				const double PelvisYaw = Mesh->GetSocketRotation(FName(TEXT("pelvis"))).Yaw;

				if (Phase == EPhase::Settle)
				{
					const int32 IdleIdx = FindBakedStateIndex(*Loco, TEXT("Idle"));
					if (IdleIdx != INDEX_NONE && Inst->GetInstanceStateWeight(LocoIdx, IdleIdx) > 0.99f)
					{
						// One-time bone-existence check ("verify before relying").
						if (Mesh->GetBoneIndex(FName(TEXT("root"))) == INDEX_NONE
							|| Mesh->GetBoneIndex(FName(TEXT("pelvis"))) == INDEX_NONE)
						{
							Test->AddError(TEXT("[Settle] root/pelvis bone not on this mesh — cannot measure the turn sweep"));
							return true;
						}
						// Capture the NEUTRAL baseline (TurnYawDelta default 0 -> TIP_Neutral is active) into the
						// single turn baseline; ReNeutral OVERWRITES it before the LEFT turn.
						BaseRootYaw   = RootYaw;
						BasePelvisYaw = PelvisYaw;
						BaseXY        = Pawn->GetActorLocation();
						Test->AddInfo(FString::Printf(
							TEXT("[Settle] settled in Idle after %.1fs. Neutral baseline: rootCSyaw=%.2f, pelvisWorldYaw=%.2f. Holding neutral to measure idle root sway."),
							Now - StartTime, BaseRootYaw, BasePelvisYaw));
						Phase = EPhase::NeutralHold;
						StartTime = Now;   // fresh budget for the neutral-hold window
					}
				}
				else if (Phase == EPhase::NeutralHold)
				{
					// GUARD: root has no idle animation on the mannequin family, so its own
					// sway over the settle window MUST stay below the turn floor. A future rig whose idle
					// animates the root (sway > floor) then fails HERE, loudly — the sway-free primary-signal
					// assumption is checked, not assumed. (Manny/Quinn are sway-free -> passes immediately.)
					MaxNeutralRootDelta = FMath::Max(MaxNeutralRootDelta,
						FMath::Abs(FRotator::NormalizeAxis(RootYaw - BaseRootYaw)));

					if ((Now - StartTime) > 1.2)
					{
						Test->AddInfo(FString::Printf(
							TEXT("[NeutralHold] neutral root sway over the settle window = %.2f deg (must stay < %.1f — sway-free assumption CHECKED)."),
							MaxNeutralRootDelta, kTurnYawMin));
						if (!(MaxNeutralRootDelta < kTurnYawMin))
						{
							Test->AddError(FString::Printf(
								TEXT("[NeutralHold] NEUTRAL ROOT SWAY %.2f deg >= floor %.1f — this rig's idle animates the root, so the sway-free root-yaw primary signal is INVALID here (extend pie_rigs.json / pick a different channel for this rig)"),
								MaxNeutralRootDelta, kTurnYawMin));
							return true;
						}
						// Drive the RIGHT turn: TurnYawDelta = +90 (> the +45 threshold -> TIP_TurnRight).
						if (!TryWriteFloatVar(*Inst, TEXT("TurnYawDelta"), 90.0))
						{
							Test->AddError(TEXT("[NeutralHold] TurnYawDelta is not a float/double property — cannot drive the turn contract"));
							return true;
						}
						Phase = EPhase::RightTurn;
						StartTime = Now;   // fresh budget for the right-turn phase
					}
				}
				else if (Phase == EPhase::RightTurn)
				{
					// Keep the external var asserted (harmless to re-write; no EventGraph re-derive).
					TryWriteFloatVar(*Inst, TEXT("TurnYawDelta"), 90.0);
					AccumTurn(RootYaw, PelvisYaw, Pawn, /*bRight*/ true, *Inst);

					// Give the 0.2s cross-fade + RotateRootBone ramp ~2.5s to develop, then adjudicate RIGHT.
					if ((Now - StartTime) > 2.5)
					{
						double TurnRead = -999.0; TryReadNumericVar(*Inst, TEXT("TurnYawDelta"), TurnRead);
						Test->AddInfo(FString::Printf(
							TEXT("[RightTurn] maxRootYawSweep=%.2f (signed, sway-free primary), maxPelvisWorldYawDelta=%.2f (corroboration), maxActorDrift=%.2f cm, TurnYawDelta read-back=%.0f, TIP_TurnRight seen=%s. Gate: rootSweep > +%.1f, drift < %.1f."),
							MaxRightSigned, MaxPelvisRight, MaxDrift, TurnRead,
							bSawRight ? TEXT("yes") : TEXT("no"), kTurnYawMin, kInPlaceMax));

						if (!AdjudicateTurn(/*bRight*/ true, MaxRightSigned, bSawRight, TurnRead)) { return true; }

						// Re-neutralize so the LEFT measurement starts from a fresh TIP_Neutral baseline.
						// (TIP_TurnRight's only exit is Abs(TurnYawDelta)<10, so -90 cannot go Right->Left
						// directly; pass through 0 -> TIP_Neutral, where root returns to ~0 with no degrade.)
						TryWriteFloatVar(*Inst, TEXT("TurnYawDelta"), 0.0);
						Phase = EPhase::ReNeutral;
						StartTime = Now;   // fresh budget for the re-neutralize window
					}
				}
				else if (Phase == EPhase::ReNeutral)
				{
					TryWriteFloatVar(*Inst, TEXT("TurnYawDelta"), 0.0);

					// Hold 0 long enough for TIP_Neutral to re-engage and root to unwind to ~0, then baseline.
					if ((Now - StartTime) > 1.5)
					{
						// OVERWRITE the single turn baseline with the re-neutralized capture (the RIGHT turn is
						// done, so the neutral/RIGHT baseline is no longer needed — the LEFT sweep baselines here).
						BaseRootYaw   = RootYaw;
						BasePelvisYaw = PelvisYaw;
						BaseXY        = Pawn->GetActorLocation();
						int32 TipIdx = INDEX_NONE;
						const FBakedAnimationStateMachine* Tip = ResolveBakedMachineByName(*Inst, TEXT("TurnInPlace"), TipIdx);
						const int32 NeutralIdx = Tip ? FindBakedStateIndex(*Tip, TEXT("TIP_Neutral")) : INDEX_NONE;
						const float NeutralW = (Tip && NeutralIdx != INDEX_NONE) ? Inst->GetInstanceStateWeight(TipIdx, NeutralIdx) : -1.f;
						Test->AddInfo(FString::Printf(
							TEXT("[ReNeutral] re-baselined for the LEFT turn: rootCSyaw=%.2f, pelvisWorldYaw=%.2f, TIP_Neutral weight=%.2f. Driving TurnYawDelta=-90."),
							BaseRootYaw, BasePelvisYaw, NeutralW));
						if (!TryWriteFloatVar(*Inst, TEXT("TurnYawDelta"), -90.0))
						{
							Test->AddError(TEXT("[ReNeutral] TurnYawDelta is not a float/double property — cannot drive the left turn"));
							return true;
						}
						Phase = EPhase::LeftTurn;
						StartTime = Now;   // fresh budget for the left-turn phase
					}
				}
				else   // EPhase::LeftTurn
				{
					TryWriteFloatVar(*Inst, TEXT("TurnYawDelta"), -90.0);
					AccumTurn(RootYaw, PelvisYaw, Pawn, /*bRight*/ false, *Inst);

					if ((Now - StartTime) > 2.5)
					{
						double TurnRead = -999.0; TryReadNumericVar(*Inst, TEXT("TurnYawDelta"), TurnRead);
						Test->AddInfo(FString::Printf(
							TEXT("[LeftTurn] minRootYawSweep=%.2f (signed, sway-free primary), maxPelvisWorldYawDelta=%.2f (corroboration), maxActorDrift=%.2f cm, TurnYawDelta read-back=%.0f, TIP_TurnLeft seen=%s. Gate: rootSweep < -%.1f, drift < %.1f."),
							MinLeftSigned, MaxPelvisLeft, MaxDrift, TurnRead,
							bSawLeft ? TEXT("yes") : TEXT("no"), kTurnYawMin, kInPlaceMax));

						if (!AdjudicateTurn(/*bRight*/ false, MinLeftSigned, bSawLeft, TurnRead)) { return true; }

						Test->AddInfo(FString::Printf(
							TEXT("[Done] TURN-IN-PLACE PROVEN BOTH DIRECTIONS: RIGHT root swept +%.2f deg (TIP_TurnRight), LEFT root swept %.2f deg (TIP_TurnLeft), both > %.1f magnitude and in the correct direction, actor stayed put (max %.2f cm < %.1f), TurnYawDelta read back at each injected value (external, no re-derive)."),
							MaxRightSigned, MinLeftSigned, kTurnYawMin, MaxDrift, kInPlaceMax));
						return true;
					}
				}
			}

			// Per-phase 20s budget (measured from each phase start). Outer backstop for a stalled resolve.
			if (PhaseBudgetExceeded(Test, Now, StartTime, PhaseName(Phase), TEXT("turn"))) { return true; }
			return false;   // keep polling
		}

	private:
		enum class EPhase : uint8 { Settle, NeutralHold, RightTurn, ReNeutral, LeftTurn };

		static const TCHAR* PhaseName(EPhase P)
		{
			switch (P)
			{
			case EPhase::Settle:      return TEXT("Settle");
			case EPhase::NeutralHold: return TEXT("NeutralHold");
			case EPhase::RightTurn:   return TEXT("RightTurn");
			case EPhase::ReNeutral:   return TEXT("ReNeutral");
			default:                  return TEXT("LeftTurn");
			}
		}

		// Accumulate one turn-phase sample: SIGNED root sweep (max for right, min for left), pelvis
		// corroboration, in-place drift, and the Mechanism-B turn-state sighting. Baselines off the single
		// Base{RootYaw,PelvisYaw,XY} triplet (Settle-captured for RIGHT; ReNeutral-overwritten for LEFT).
		void AccumTurn(double RootYaw, double PelvisYaw, APawn* Pawn, bool bRight, UAnimInstance& Inst)
		{
			const double RootSigned = FRotator::NormalizeAxis(RootYaw - BaseRootYaw);
			const double PelvisDelta = FMath::Abs(FRotator::NormalizeAxis(PelvisYaw - BasePelvisYaw));
			MaxDrift = FMath::Max(MaxDrift, FVector::Dist2D(Pawn->GetActorLocation(), BaseXY));

			int32 TipIdx = INDEX_NONE;
			const FBakedAnimationStateMachine* Tip = ResolveBakedMachineByName(Inst, TEXT("TurnInPlace"), TipIdx);
			const int32 StateIdx = Tip ? FindBakedStateIndex(*Tip, bRight ? TEXT("TIP_TurnRight") : TEXT("TIP_TurnLeft")) : INDEX_NONE;
			const bool bInTurn = (Tip && StateIdx != INDEX_NONE && Inst.GetInstanceStateWeight(TipIdx, StateIdx) > 0.5f);

			if (bRight)
			{
				MaxRightSigned = FMath::Max(MaxRightSigned, RootSigned);
				MaxPelvisRight = FMath::Max(MaxPelvisRight, PelvisDelta);
				bSawRight = bSawRight || bInTurn;
			}
			else
			{
				MinLeftSigned = FMath::Min(MinLeftSigned, RootSigned);
				MaxPelvisLeft = FMath::Max(MaxPelvisLeft, PelvisDelta);
				bSawLeft = bSawLeft || bInTurn;
			}
		}

		// Gate one direction. Returns true if all four assertions hold; on failure AddErrors and returns
		// false (the caller ends the test). Sweep must exceed the floor IN THE CORRECT DIRECTION.
		bool AdjudicateTurn(bool bRight, double Sweep, bool bSawTurn, double TurnRead)
		{
			const TCHAR* Tag = bRight ? TEXT("RightTurn") : TEXT("LeftTurn");
			const TCHAR* StateName = bRight ? TEXT("TIP_TurnRight") : TEXT("TIP_TurnLeft");
			const double Injected = bRight ? 90.0 : -90.0;
			bool bOk = true;

			const bool bSwept = bRight ? (Sweep > kTurnYawMin) : (Sweep < -kTurnYawMin);
			if (!bSwept)
			{
				Test->AddError(FString::Printf(
					TEXT("[%s] TURN FAILED: root/body yaw swept %.2f deg (need %s %s%.1f) — the RotateRootBone degrade did not rotate the pawn the correct way"),
					Tag, Sweep, bRight ? TEXT(">") : TEXT("<"), bRight ? TEXT("+") : TEXT("-"), kTurnYawMin));
				bOk = false;
			}
			if (!(MaxDrift < kInPlaceMax))
			{
				Test->AddError(FString::Printf(
					TEXT("[%s] NOT IN PLACE: actor drifted %.2f cm (must stay < %.1f) — the turn moved the capsule, not just the pose"),
					Tag, MaxDrift, kInPlaceMax));
				bOk = false;
			}
			if (!FMath::IsNearlyEqual(TurnRead, Injected, 0.5))
			{
				Test->AddError(FString::Printf(
					TEXT("[%s] READ-BACK MISMATCH: TurnYawDelta read back %.2f, not %.0f — the injected value did not stick (an EventGraph re-derive would clobber it; a re-derived var is not a controllable input)"),
					Tag, TurnRead, Injected));
				bOk = false;
			}
			if (!bSawTurn)
			{
				Test->AddError(FString::Printf(
					TEXT("[%s] Mechanism B FAILED: the nested TurnInPlace machine never reached %s (weight > 0.5) — the sub-state-machine did not select the turn"),
					Tag, StateName));
				bOk = false;
			}
			return bOk;
		}

		// Root/body in-place yaw floor (deg). The TIP turn states apply RotateRootBone Yaw=+/-60; the live
		// sweep converges to ~57-60 deg (root component-space, sway-free; measured in a live
		// probe). 25 sits well below the ~60 signal and far above root's idle motion (0 —
		// root carries no animation), a clean directional discriminator immune to MM_Idle's pelvis sway.
		static constexpr double kTurnYawMin = 25.0;
		// Actor XY drift ceiling (cm). RotateRootBone rotates the POSE root, not the capsule, so drift ~0.
		static constexpr double kInPlaceMax = 20.0;

		FAutomationTestBase* Test;
		double StartTime;
		EPhase Phase = EPhase::Settle;
		// Single turn baseline: captured NEUTRAL at Settle (the RIGHT-turn baseline), then OVERWRITTEN at
		// ReNeutral (the LEFT-turn baseline). RightTurn completes before ReNeutral, so the two are never live
		// simultaneously — one triplet suffices.
		double BaseRootYaw = 0.0;
		double BasePelvisYaw = 0.0;
		FVector BaseXY = FVector::ZeroVector;
		double MaxNeutralRootDelta = 0.0;
		// RIGHT-turn accumulators (signed: expect POSITIVE).
		double MaxRightSigned = -1.0e9;
		double MaxPelvisRight = 0.0;
		bool bSawRight = false;
		// LEFT-turn accumulators (signed: expect NEGATIVE; baselined off the ReNeutral overwrite above).
		double MinLeftSigned = 1.0e9;
		double MaxPelvisLeft = 0.0;
		bool bSawLeft = false;
		// In-place drift ceiling (shared across both turns; the capsule never moves).
		double MaxDrift = 0.0;
	};

	// ============================================================================================
	// The AIM-OFFSET assertion. Proves the RotationOffsetBlendSpace node (AO_Rifle,
	// spliced LayeredBoneBlend -> Aim -> ControlRig) CHANGES THE UPPER-BODY POSE as the aim PITCH varies, while
	// the LOWER body is untouched. Phases (each with its own per-phase 20s backstop):
	//   Settle      — wait for Locomotion/Idle weight > 0.99 (idle, so the aim runs on the idle base pose)
	//                 and write AimPitch/AimYaw = 0 (proven-neutral).
	//   NeutralHold — hold AimPitch=0, capture the neutral SWAY-CANCELED baseline, and MEASURE how far the
	//                 sway-canceled signal drifts at aim=0 (the proven-off / inert-equivalent reference).
	//   Extreme     — write AimPitch=+0.9 (near the +1 pitch limit) and accumulate the max sway-canceled
	//                 upper-body delta + the world corroboration/foot guard + the Mechanism-A aim-node weight.
	//
	// WHY DRIVE PITCH, NOT YAW (an AO_Rifle asset finding — the BlendSpace X/Y lesson): AO_Rifle's axes are
	// normalized -1..1 (NOT degrees). Y=PITCH is POPULATED; X=YAW is UNPOPULATED (zero left/right samples), so
	// driving yaw shows NOTHING in this stock asset. The test drives AimPitch in -1..1 (neutral 0 -> +0.9).
	//
	// PRIMARY signal (CONFOUND-IMMUNE): the head's COMPONENT-space rotation RELATIVE to the
	// pelvis. MM_Idle carries a LARGE whole-body/pelvis yaw sway (~+/-22 deg world yaw; the branch's own turn
	// probe documents it — pie_turninplace_drive.py, PieTests.cpp turn header) and the head INHERITS it, so a
	// head WORLD-rotation delta is swamped by sway: a dropped/mis-axis'd AimPitch binding leaves the node
	// spliced inline but samples pitch=0 -> IDENTITY offset -> no pose change, yet the head world delta still
	// reads >= ~22 deg from idle sway alone > a fixed 15 deg floor, so the gate would pass GREEN with the aim
	// INERT (the exact confound this project exists to prevent). The sway is COMMON-MODE between pelvis and
	// head (applied at/above the pelvis), so head-RELATIVE-to-pelvis CANCELS it and leaves only the additive
	// aim offset (applied ABOVE the pelvis via the LayeredBoneBlend). Live head-rel-pelvis probe:
	// head-rel-pelvis delta ~82 deg aimed vs ~2-3.5 deg at neutral — a clean confound-immune separation, where
	// the world delta (~84 deg aimed but >= ~22 deg achievable from sway alone) is NOT. CORROBORATION = the
	// head WORLD delta (logged, NOT gated — it is the confoundable signal). CONFOUND GUARD = foot_r WORLD-rot
	// delta. NOT because "AO_Rifle is upper-body only" - it is not, and the note below on kAimLowerMaxDeg
	// says so with the numbers. This guard works because the CU-vs-CC PITCH DIFFERENCE is upper-body,
	// which is the only thing a sweep between two pitch samples can see; the additive's own whole-body
	// contribution is common to both samples and cancels out of the difference.
	//
	// HARD ASSERTIONS (all AddError-gated): (1) NeutralHold — the sway-canceled head-rel-pelvis drift at aim=0
	// stays < kAimDeltaMinDeg (the inert-equivalent pose is BELOW the floor the aimed pose must EXCEED, so an
	// inert aim => RED; sway-cancel CHECKED, not assumed — mirrors FTurnInPlaceCommand's neutral-root-sway
	// gate); (2) Extreme — sway-canceled head-rel-pelvis delta > kAimDeltaMinDeg (the upper body visibly aims,
	// sway removed); (3) foot_r world-rot delta < kAimLowerMaxDeg (lower body undisturbed); (4)
	// TryReadNumericVar('AimPitch') ~= +0.9 (the injected value STUCK — AimPitch is a pure external var, no
	// EventGraph re-derive, so the OFF/ON state is a controllable input); (5) Mechanism A —
	// the RotationOffsetBlendSpace node is PRESENT in the live GatherDebugData chain with weight > 0 (a
	// sliced/absent node returns -1 from FindNodeWeight and fails here). (1)+(2) together are the
	// confound-immunity gate: aim=0 is exactly the pose a dropped/mis-axis'd binding produces, and it is
	// proven below the floor the driven pose exceeds.
	// (6) Settle — the node's RUNTIME alpha is OFF before this test opts in. See kAimAlphaOffMaxPct.
	// ============================================================================================

	/** Parse the `Alpha (12.3%)` field out of a GatherDebugData node line. False if the field is absent.
	 *
	 *  FAnimNode_RotationOffsetBlendSpace::GatherDebugData prints "Alpha (%.1f%%) PlayTime (%.3f)" from
	 *  ActualAlpha — the value the pin RESOLVED to this frame. That is the only honest source for it: the
	 *  .uasset records what a pin is BOUND to, and the defect this guards against was a pin bound to
	 *  nothing at all, silently resolving to the node's constructor default of 1.0. A baseline diff cannot
	 *  see that, because an absent binding has nothing to diff. */
	static bool TryParseAlphaPercent(const FString& NodeLine, double& OutPercent)
	{
		const FString Tag = TEXT("Alpha (");
		const int32 Start = NodeLine.Find(Tag, ESearchCase::CaseSensitive, ESearchDir::FromStart);
		if (Start == INDEX_NONE) { return false; }
		const int32 ValueStart = Start + Tag.Len();
		const int32 Pct = NodeLine.Find(TEXT("%"), ESearchCase::CaseSensitive, ESearchDir::FromStart, ValueStart);
		if (Pct == INDEX_NONE || Pct <= ValueStart) { return false; }
		// FULLY NUMERIC OR NOTHING. Atod returns 0.0 for text it cannot parse, and 0.0 sails through the
		// "< 1%" default-off test — so an unparseable line would read as PROOF the node is off, which is
		// this gate's own failure mode turned against it. Reject instead, and let the caller error.
		const FString Value = NodeLine.Mid(ValueStart, Pct - ValueStart).TrimStartAndEnd();
		if (!Value.IsNumeric()) { return false; }
		OutPercent = FCString::Atod(*Value);
		return true;
	}

	class FAimOffsetCommand : public IAutomationLatentCommand
	{
	public:
		explicit FAimOffsetCommand(FAutomationTestBase* InTest)
			: Test(InTest), StartTime(0.0) {}

		virtual bool Update() override
		{
			if (StartTime == 0.0) { StartTime = FPlatformTime::Seconds(); }
			const double Now = FPlatformTime::Seconds();

			// The possessed-pawn -> Locomotion-baked-machine preamble (shared with the sibling
			// turn/aim/hand commands) — resolves Pawn/Mesh/Inst/LocoIdx/Loco or returns false (fall through to the budget).
			APawn* Pawn = nullptr; USkeletalMeshComponent* Mesh = nullptr; UAnimInstance* Inst = nullptr;
			int32 LocoIdx = INDEX_NONE;
			const FBakedAnimationStateMachine* Loco = nullptr;
			if (ResolvePossessedLocomotionContext(Pawn, Mesh, Inst, LocoIdx, Loco))
			{
				// Sway-canceled PRIMARY signal: the head's COMPONENT-space rotation RELATIVE to the pelvis
				// (Pelvis^-1 * Head). MM_Idle's whole-body/pelvis yaw sway is common-mode between pelvis and
				// head, so this cancels it and leaves only the additive upper-body aim offset. Plus the WORLD
				// head (corroboration, confoundable) and foot_r WORLD (lower-body confound guard).
				const FQuat HeadCS   = Mesh->GetSocketTransform(FName(TEXT("head")),   ERelativeTransformSpace::RTS_Component).GetRotation();
				const FQuat PelvisCS = Mesh->GetSocketTransform(FName(TEXT("pelvis")), ERelativeTransformSpace::RTS_Component).GetRotation();
				const FQuat HeadRelPelvis = PelvisCS.Inverse() * HeadCS;
				const FQuat HeadWorld = Mesh->GetSocketTransform(FName(TEXT("head")), ERelativeTransformSpace::RTS_World).GetRotation();
				const FQuat FootWorld = Mesh->GetSocketTransform(FName(TEXT("foot_r")), ERelativeTransformSpace::RTS_World).GetRotation();

				if (Phase == EPhase::Settle)
				{
					const int32 IdleIdx = FindBakedStateIndex(*Loco, TEXT("Idle"));
					if (IdleIdx != INDEX_NONE && Inst->GetInstanceStateWeight(LocoIdx, IdleIdx) > 0.99f)
					{
						// One-time bone-existence check ("verify before relying"). pelvis is required for the
						// sway-canceled head-rel-pelvis primary signal; head + foot_r for the
						// world corroboration + lower-body confound guard.
						if (Mesh->GetBoneIndex(FName(TEXT("head"))) == INDEX_NONE
							|| Mesh->GetBoneIndex(FName(TEXT("pelvis"))) == INDEX_NONE
							|| Mesh->GetBoneIndex(FName(TEXT("foot_r"))) == INDEX_NONE)
						{
							Test->AddError(TEXT("[Settle] head/pelvis/foot_r bone not on this mesh — cannot measure the sway-canceled aim pose delta"));
							return true;
						}
						// Prove-neutral: write AimPitch/AimYaw = 0 (default is 0 anyway; explicit so the baseline
						// is a known-neutral aim). These are pure external vars (no EventGraph re-derive).
						// ============================================================================
						// THE GATE IS OFF BY DEFAULT — asserted BEFORE this test opts in.
						// ============================================================================
						// Nothing has written AimOffsetAlpha at this point, so the node's runtime alpha is
						// whatever the graph resolves on its own. It must be 0. This is the assertion that
						// would have caught the original defect: the Alpha pin was bound to NOTHING and
						// resolved to FAnimNode_RotationOffsetBlendSpace's constructor default of 1.0, so
						// AO_Rifle's rifle-stance additive was applied to every shot at full weight. A
						// golden-baseline diff cannot see that — an absent binding has no line to diff — and
						// every existing assertion here drives AimPitch and measures whether the pose MOVES,
						// which an always-on node passes happily. Read ActualAlpha, not the .uasset.
						{
							FString DefaultAimLine;
							const TArray<FNodeDebugData::FFlattenedDebugData> DefaultFlat = MotionTerminal::GatherFlatNodeWeights(*Inst);
							// EXACTLY ONE MATCH, OR THIS READS THE WRONG NODE. FindNodeWeight returns the FIRST
							// line containing the substring. Add a second RotationOffsetBlendSpace to this graph
							// and whichever one is flattened first wins — a spare node sitting at 0% would mask
							// the real one at 100% and this gate would report the defect as fixed.
							int32 AimNodeMatches = 0;
							for (const FNodeDebugData::FFlattenedDebugData& Item : DefaultFlat)
							{
								if (Item.DebugLine.Contains(TEXT("AnimNode_RotationOffsetBlendSpace"))) { ++AimNodeMatches; }
							}
							if (AimNodeMatches != 1)
							{
								Test->AddError(FString::Printf(
									TEXT("[Settle] expected EXACTLY ONE AnimNode_RotationOffsetBlendSpace in the evaluated graph, found %d — the runtime-alpha read below picks the first match, so with any other count it is not reading the node this gate is about"),
									AimNodeMatches));
								return true;
							}
							FindNodeWeight(DefaultFlat, TEXT("AnimNode_RotationOffsetBlendSpace"), DefaultAimLine);
							double DefaultAlphaPct = -1.0;
							if (!TryParseAlphaPercent(DefaultAimLine, DefaultAlphaPct))
							{
								Test->AddError(FString::Printf(
									TEXT("[Settle] could not read the aim node's runtime Alpha out of its GatherDebugData line ('%s') — without it the default-off half of this gate is unverifiable, so it is an error rather than a skip"),
									*DefaultAimLine));
								return true;
							}
							Test->AddInfo(FString::Printf(
								TEXT("[Settle] aim node default runtime alpha = %.1f%% (must be < %.1f%% with nothing driving AimOffsetAlpha). Line: '%s'"),
								DefaultAlphaPct, kAimAlphaOffMaxPct, *DefaultAimLine));
							if (!(DefaultAlphaPct < kAimAlphaOffMaxPct))
							{
								Test->AddError(FString::Printf(
									TEXT("[Settle] AIM OFFSET IS ON BY DEFAULT: runtime alpha %.1f%% >= %.1f%% with nothing driving AimOffsetAlpha. AO_Rifle's samples are mesh-space rotation additives based on frame 0 of MF_Rifle_Idle_ADS, so over the Template's UNARMED pose an always-on node adds the full rifle stance to every bone below the root (measured at AimPitch=0: pelvis 32.8 deg, clavicle_r 44.5 deg, thigh_r 37.1 deg against a 0.1-2.5 deg control). Check that the node's Alpha pin is still bound to AimOffsetAlpha and that the variable still defaults to 0"),
									DefaultAlphaPct, kAimAlphaOffMaxPct));
								return true;
							}
						}

						// CHECK THE PINS. AimYaw is pinned to 0 so nothing else can drive the measurement, and a
						// silently-dropped write leaves it at whatever the graph last held - a confound reported
						// as a clean aim reading. The same defect was closed in five Python
						// drivers; these calls are its C++ mirror and were discarding the bool.
						if (!TryWriteFloatVar(*Inst, TEXT("AimPitch"), 0.0) || !TryWriteFloatVar(*Inst, TEXT("AimYaw"), 0.0))
						{
							Test->AddError(TEXT("[Settle] could not pin AimPitch/AimYaw to 0 - the neutral baseline this test measures would be taken against an undriven aim input"));
							return true;
						}
						// OPT INTO THE AIM OFFSET — BOTH HALVES. `AimOffsetAlpha` gates the RotationOffsetBlendSpace
						// and defaults to 0, because the node is NOT neutral at AimPitch=0: its samples are
						// mesh-space rotation additives based on frame 0 of MF_Rifle_Idle_ADS, so over the
						// Template's UNARMED pose the full rifle-stance delta lands on every bone below the root
						// (measured: pelvis 32.8 deg, clavicle_r 44.5 deg, thigh_r 37.1 deg).
						//
						// `RifleModeAlpha` is the second half. The aim node now sits INSIDE the rifle
						// branch — SequenceEvaluator(MF_Rifle_Idle_ADS) -> AO -> LayeredBoneBlend.BlendPoses[0],
						// weighted by RifleModeAlpha — so that the delta is applied over the base it was extracted
						// against instead of over the unarmed pose. Two consequences for this test, and the second
						// is why the write is here: at RifleModeAlpha=0 the AO's output NO LONGER REACHES THE
						// OUTPUT POSE AT ALL, so writing AimOffsetAlpha alone would measure a zero delta and fail
						// as "aim did nothing" while the graph was in fact correct. Both, or neither.
						if (!TryWriteFloatVar(*Inst, TEXT("AimOffsetAlpha"), 1.0))
						{
							Test->AddError(TEXT("[Settle] AimOffsetAlpha is not a float/double property on this AnimInstance — the aim-offset node cannot be enabled, so this test would measure an aim beat that was never switched on"));
							return true;
						}
						if (!TryWriteFloatVar(*Inst, TEXT("RifleModeAlpha"), 1.0))
						{
							Test->AddError(TEXT("[Settle] RifleModeAlpha is not a float/double property on this AnimInstance — the rifle branch carrying the aim node cannot be enabled, so the aim offset would be blended out of the result and this test would report a zero delta for a graph that is working"));
							return true;
						}
						Test->AddInfo(FString::Printf(
							TEXT("[Settle] settled in Idle after %.1fs. Holding AimPitch=0 (AimOffsetAlpha=1, RifleModeAlpha=1) to capture the neutral upper/lower baseline."),
							Now - StartTime));
						Phase = EPhase::NeutralHold;
						StartTime = Now;   // fresh budget for the neutral-hold window
					}
				}
				else if (Phase == EPhase::NeutralHold)
				{
					// Keep aim neutral. Capture the SWAY-CANCELED reference ONCE (first neutral tick), then
					// measure how far head-rel-pelvis drifts from it over the hold — the residual idle
					// differential in the sway-canceled signal, i.e. how quiet the PROVEN-OFF pose is. The SAME
					// reference is the Extreme aim-delta baseline (apples-to-apples: aimed vs neutral off one
					// anchor). NeutralHead/NeutralFoot capture the world corroboration / confound baselines.
					// Re-pinned every tick of the hold, and CHECKED once: a write that stops sticking midway
					// through the hold turns the neutral baseline into a moving one.
					if ((!TryWriteFloatVar(*Inst, TEXT("AimPitch"), 0.0)
						 | !TryWriteFloatVar(*Inst, TEXT("AimYaw"), 0.0)
						 | !TryWriteFloatVar(*Inst, TEXT("AimOffsetAlpha"), 1.0)
						 | !TryWriteFloatVar(*Inst, TEXT("RifleModeAlpha"), 1.0)) && !bHoldWriteFailed)
					{
						bHoldWriteFailed = true;
						Test->AddError(TEXT("[NeutralHold] a contract-var write stopped sticking during the hold - AimPitch/AimYaw/AimOffsetAlpha/RifleModeAlpha are what hold this baseline still"));
					}

					// LET THE ALPHA LAND BEFORE ANCHORING. AimOffsetAlpha steps 0 -> 1 at the Settle boundary,
					// and that step is worth ~42 deg in this very signal — AO_Rifle's CC sample is not identity,
					// which is the whole defect this gate exists for. Anchoring on the FIRST NeutralHold tick
					// captured the pose from before the step, so the step landed inside the window this phase
					// measures and reported 42.5 deg of "neutral drift": a real measurement of the wrong thing,
					// caught by this phase's own confound-immunity assertion. The reference is therefore taken
					// only after the node has been on for kAimArmSettleSec, so what this phase reports is idle
					// motion at a STABLE alpha — which is what it claims to report.
					if (!bNeutralRefSet)
					{
						if ((Now - StartTime) < kAimArmSettleSec) { return false; }
						NeutralHeadRel = HeadRelPelvis;
						NeutralHead = HeadWorld;
						NeutralFoot = FootWorld;
						bNeutralRefSet = true;
					}
					MaxNeutralHeadRelDelta = FMath::Max(MaxNeutralHeadRelDelta,
						(double)FMath::RadiansToDegrees(NeutralHeadRel.AngularDistance(HeadRelPelvis)));

					// +kAimArmSettleSec so the arm dwell is added to the hold rather than eaten out of it —
					// the drift window stays the 1.2 s it was calibrated over.
					if ((Now - StartTime) > (1.2 + kAimArmSettleSec))
					{
						double PitchRead = -999.0; TryReadNumericVar(*Inst, TEXT("AimPitch"), PitchRead);
						Test->AddInfo(FString::Printf(
							TEXT("[NeutralHold] neutral sway-canceled head-rel-pelvis drift = %.2f deg over the hold (must stay < %.1f — the aim=0 / inert-equivalent signal is BELOW the gate floor the aimed pose must EXCEED; sway-cancel CHECKED, not assumed). AimPitch read-back %.2f. Driving AimPitch=+%.2f."),
							MaxNeutralHeadRelDelta, kAimDeltaMinDeg, PitchRead, kAimPitchExtreme));
						// CONFOUND-IMMUNITY GATE: a dropped/mis-axis'd AimPitch binding leaves
						// the RotationOffsetBlendSpace node spliced inline but samples pitch=0 -> IDENTITY offset ->
						// the SAME pose as this aim=0 neutral. If that neutral signal reached the floor the aimed
						// signal must exceed, an INERT aim would pass green (the exact bug). Assert the neutral
						// drift stays below the floor, so inert => RED. (The sibling FTurnInPlaceCommand asserts
						// its neutral ROOT sway < kTurnYawMin for the same reason.)
						if (!(MaxNeutralHeadRelDelta < kAimDeltaMinDeg))
						{
							Test->AddError(FString::Printf(
								TEXT("[NeutralHold] NEUTRAL SWAY-CANCELED DRIFT %.2f deg >= floor %.1f — at aim=0 the upper body already moves relative to the pelvis by more than the gate floor, so head-rel-pelvis is NOT confound-immune on this rig (its idle animates the upper body relative to the pelvis); the aimed gate could pass on idle motion alone — pick a different reference bone / recalibrate the floor for this rig"),
								MaxNeutralHeadRelDelta, kAimDeltaMinDeg));
							return true;
						}
						if (!TryWriteFloatVar(*Inst, TEXT("AimPitch"), kAimPitchExtreme))
						{
							Test->AddError(TEXT("[NeutralHold] AimPitch is not a float/double property — cannot drive the aim contract"));
							return true;
						}
						Phase = EPhase::Extreme;
						StartTime = Now;   // fresh budget for the extreme phase
					}
				}
				else   // EPhase::Extreme
				{
					// Keep the external var asserted (harmless to re-write; no EventGraph re-derive).
					TryWriteFloatVar(*Inst, TEXT("AimPitch"), kAimPitchExtreme);
					TryWriteFloatVar(*Inst, TEXT("AimYaw"), 0.0);
					TryWriteFloatVar(*Inst, TEXT("AimOffsetAlpha"), 1.0);
					TryWriteFloatVar(*Inst, TEXT("RifleModeAlpha"), 1.0);

					// PRIMARY (sway-canceled): head-rel-pelvis angular delta vs the neutral baseline — the idle
					// sway is common-mode and cancels, leaving only the additive aim offset. Corroboration: the
					// WORLD head delta (the OLD, sway-confoundable signal — logged, NOT gated). Confound guard:
					// foot_r WORLD delta. (Quaternion angular distance: wrap-safe, all 3 axes.) The Mechanism-A
					// aim-node weight is sampled ONCE at adjudication below.
					MaxHeadRelDelta   = FMath::Max(MaxHeadRelDelta,   (double)FMath::RadiansToDegrees(NeutralHeadRel.AngularDistance(HeadRelPelvis)));
					MaxHeadWorldDelta = FMath::Max(MaxHeadWorldDelta, (double)FMath::RadiansToDegrees(NeutralHead.AngularDistance(HeadWorld)));
					MaxFootDelta      = FMath::Max(MaxFootDelta,      (double)FMath::RadiansToDegrees(NeutralFoot.AngularDistance(FootWorld)));

					// Give the blend-space blend-in + the ~0.2s node update ~2.5s to develop, then adjudicate.
					if ((Now - StartTime) > 2.5)
					{
						// Mechanism A (one-shot): sample the RotationOffsetBlendSpace aim-node presence/weight ONCE
						// here, near adjudication — the blend is fully developed, so a single weight > 0 suffices
						// (a per-tick max over the window was redundant; Mechanism A needs one non-zero weight).
						{
							const TArray<FNodeDebugData::FFlattenedDebugData> Flat = MotionTerminal::GatherFlatNodeWeights(*Inst);
							MaxAimNodeWeight = FindNodeWeight(Flat, TEXT("AnimNode_RotationOffsetBlendSpace"), AimNodeLine);
						}
						double PitchRead = -999.0; TryReadNumericVar(*Inst, TEXT("AimPitch"), PitchRead);
						Test->AddInfo(FString::Printf(
							TEXT("[Extreme] maxHeadRelPelvisDelta=%.2f deg (upper, sway-canceled PRIMARY; neutral drift was %.2f), maxHeadWorldRotDelta=%.2f deg (world corroboration, sway-confoundable), maxFootRWorldRotDelta=%.2f deg (lower confound guard), AimPitch read-back=%.2f, aim-node '%s' weight=%.3f. Gate: headRel > %.1f (neutral %.2f < %.1f), foot_r < %.1f, AimPitch ~= %.2f, node weight > 0."),
							MaxHeadRelDelta, MaxNeutralHeadRelDelta, MaxHeadWorldDelta, MaxFootDelta, PitchRead, *AimNodeLine, MaxAimNodeWeight,
							kAimDeltaMinDeg, MaxNeutralHeadRelDelta, kAimDeltaMinDeg, kAimLowerMaxDeg, kAimPitchExtreme));

						bool bOk = true;
						if (!(MaxHeadRelDelta > kAimDeltaMinDeg))
						{
							Test->AddError(FString::Printf(
								TEXT("[Extreme] AIM FAILED: sway-canceled upper-body (head-rel-pelvis) delta %.2f deg not > %.1f — the aim offset did not change the upper-body pose relative to the pelvis (a dropped/mis-axis'd AimPitch binding samples pitch=0 -> identity offset -> this reads ~neutral; check the Y<-AimPitch axis binding and the -1..1 range)"),
								MaxHeadRelDelta, kAimDeltaMinDeg));
							bOk = false;
						}
						if (!(MaxFootDelta < kAimLowerMaxDeg))
						{
							Test->AddError(FString::Printf(
								TEXT("[Extreme] CONFOUND: lower-body (foot_r) world-rot delta %.2f deg not < %.1f — the pose change is not upper-body-isolated (whole-body motion, not the aim offset)"),
								MaxFootDelta, kAimLowerMaxDeg));
							bOk = false;
						}
						if (!FMath::IsNearlyEqual(PitchRead, kAimPitchExtreme, 0.05))
						{
							Test->AddError(FString::Printf(
								TEXT("[Extreme] READ-BACK MISMATCH: AimPitch read back %.3f, not %.2f — the injected value did not stick (an EventGraph re-derive would clobber it; a re-derived var is not a controllable input)"),
								PitchRead, kAimPitchExtreme));
							bOk = false;
						}
						if (!(MaxAimNodeWeight > 0.f))
						{
							Test->AddError(FString::Printf(
								TEXT("[Extreme] Mechanism A FAILED: the RotationOffsetBlendSpace aim node was not found with weight > 0 in the live GatherDebugData chain (matched line '%s', weight %.3f) — the node is absent/sliced from the evaluated pose"),
								*AimNodeLine, MaxAimNodeWeight));
							bOk = false;
						}

						if (bOk)
						{
							Test->AddInfo(FString::Printf(
								TEXT("[Done] AIM OFFSET PROVEN (confound-immune): sway-canceled upper body (head-rel-pelvis) rotated %.2f deg with AimPitch=+%.2f while the SAME signal drifted only %.2f deg at aim=0 (neutral/inert-equivalent, < %.1f floor) and the lower body (foot_r) stayed put (%.2f deg < %.1f); AimPitch read back at the injected value (external, no re-derive) and the RotationOffsetBlendSpace node ran (weight %.3f). World head delta %.2f deg corroborates."),
								MaxHeadRelDelta, kAimPitchExtreme, MaxNeutralHeadRelDelta, kAimDeltaMinDeg, MaxFootDelta, kAimLowerMaxDeg, MaxAimNodeWeight, MaxHeadWorldDelta));
						}
						return true;
					}
				}
			}

			// Per-phase 20s budget (measured from each phase start). Outer backstop for a stalled resolve.
			if (PhaseBudgetExceeded(Test, Now, StartTime, PhaseName(Phase), TEXT("aim"))) { return true; }
			return false;   // keep polling
		}

	private:
		enum class EPhase : uint8 { Settle, NeutralHold, Extreme };

		static const TCHAR* PhaseName(EPhase P)
		{
			switch (P)
			{
			case EPhase::Settle:      return TEXT("Settle");
			case EPhase::NeutralHold: return TEXT("NeutralHold");
			default:                  return TEXT("Extreme");
			}
		}

		// Aim pitch extreme — NORMALIZED -1..1 (AO_Rifle's axes are normalized, NOT degrees; the BlendSpace X/Y
		// lesson). +0.9 sits near the +1 pitch limit (look-up); the live probe measured head ~84 deg here.
		static constexpr double kAimPitchExtreme = 0.9;
		// Upper-body sway-canceled pose-change floor (deg), applied to the head-rel-pelvis angular delta AND
		// (as the confound-immunity gate) to the neutral aim=0 drift. Live head-rel-pelvis probe (
		// AimPitch 0 -> +0.9): head-rel-pelvis delta ~82 deg aimed (Manny 81.6, Khaimera 81.7) vs neutral drift
		// ~2-3.5 deg (Manny 1.8, Khaimera 3.5). 15 sits ~4.3x above the worst neutral and ~5.4x below the aimed
		// signal — a clean two-sided discriminator on the CONFOUND-IMMUNE signal. (The OLD world-space head
		// delta of ~84 deg sat ABOVE MM_Idle's +/-22 deg pelvis sway, so a dropped/mis-axis'd AimPitch could
		// pass green off idle sway alone; head-rel-pelvis cancels that sway.)
		static constexpr double kAimDeltaMinDeg = 15.0;
		// Lower-body confound ceiling (deg). Live probe: foot_r ~0.2-0.4 deg for THIS test's signal, which is
		// the CU-vs-CC pitch delta — that delta really is upper-body-only.
		//
		// DO NOT GENERALISE THAT INTO "AO_Rifle is upper-body only". It is not. The asset's CC sample measured
		// against the node being absent moves thigh_r 37.1 deg and foot_r 21.8 deg (Scripts/archive/pie_aimnull_ab_drive.py).
		// That probe also wrote FootIKAlpha=0 as a control and an earlier revision of this comment claimed it
		// "proved the Control Rig is not the coupler" — RETRACTED: FootIKAlpha is recomputed every
		// update (see the HOW BOTH BRANCHES ARE REACHED note on FSkeletonAwareFootIKCommand below, which depends
		// on exactly that), so the write never survived to evaluation and the control never ran. The leg claim
		// now rests on the ASSET measurement instead, which is upstream of any Control Rig: AO_Rifle's centre
		// sample carries local rotations of thigh_l 22.4, calf_r 23.8, foot_r 12.6 deg against its own reference
		// pose (Scripts/probe_ao_rifle_neutrality.py). What is upper-body-only is
		// the DIFFERENCE between two pitch samples, not the additive itself — and this ceiling only ever sees
		// the difference. An earlier revision of this comment stated the general claim and it was wrong.
		static constexpr double kAimLowerMaxDeg = 5.0;
		// Runtime-alpha ceiling for "the gate is off" (percent), read out of GatherDebugData's `Alpha (x%)`.
		// The two states this discriminates are exactly 0.0 and 100.0 — an unbound pin resolves to the node's
		// constructor default of 1.0, there is no partial case in between to calibrate against — so this is a
		// separator, not a tolerance, and 1.0 leaves room for float formatting without admitting anything real.
		static constexpr double kAimAlphaOffMaxPct = 1.0;
		// Dwell (seconds) between enabling the aim node and anchoring the neutral reference, so the 0 -> 1
		// alpha step is not itself measured as neutral drift. 0.35 is ~21 frames at 60 Hz — the pin binding
		// resolves on the next Update, so this is orders of magnitude more than the mechanism needs; it is
		// sized for the phase to be obviously past the transient rather than marginally past it.
		static constexpr double kAimArmSettleSec = 0.35;

		FAutomationTestBase* Test;
		double StartTime;
		EPhase Phase = EPhase::Settle;
		// Sway-canceled baseline (head COMPONENT-space rotation relative to pelvis), captured ONCE at the first
		// NeutralHold tick and used for BOTH the neutral drift and the Extreme aim delta.
		FQuat NeutralHeadRel = FQuat::Identity;
		bool bNeutralRefSet = false;
		// Latched so a write that stops sticking reports once rather than every tick of the hold.
		bool bHoldWriteFailed = false;
		FQuat NeutralHead = FQuat::Identity;   // world head baseline (corroboration, not gated)
		FQuat NeutralFoot = FQuat::Identity;   // world foot_r baseline (lower-body confound guard)
		double MaxHeadRelDelta = 0.0;          // sway-canceled aim signal (PRIMARY, gated > floor)
		double MaxNeutralHeadRelDelta = 0.0;   // sway-canceled neutral/inert drift (gated < floor)
		double MaxHeadWorldDelta = 0.0;        // world head delta (corroboration only)
		double MaxFootDelta = 0.0;             // world foot_r delta (confound guard)
		float MaxAimNodeWeight = -1.f;
		FString AimNodeLine;
	};

	// ---- Weapon-reach helper + the hand-reaches-socket assertion ------------------

	// Resolve the pawn's MT_Weapon-tagged UStaticMeshComponent (nullptr if absent). The FHandReachesSocketCommand
	// resolves it ONCE at Settle (the socket-existence guard) and caches it, so IKOff/IKOn reach the SAME
	// component the EventGraph does without a per-tick re-scan.
	UStaticMeshComponent* ResolveWeaponComponent(APawn& Pawn, const TCHAR* WeaponTag)
	{
		const TArray<UActorComponent*> Tagged = Pawn.GetComponentsByTag(UStaticMeshComponent::StaticClass(), FName(WeaponTag));
		for (UActorComponent* C : Tagged)
		{
			if (UStaticMeshComponent* SMC = Cast<UStaticMeshComponent>(C)) { return SMC; }
		}
		return nullptr;
	}

	// The distance from a hand bone to a weapon component's socket, in one shot. Mesh and Weapon are the
	// caller's ALREADY-RESOLVED components (FHandReachesSocketCommand holds both that same tick — the mesh from
	// the possessed-pawn preamble, the weapon cached+validated at Settle). Mirrors ReadFootSockets.
	// GOTCHA: UStaticMeshComponent::GetSocketLocation returns the COMPONENT ORIGIN — silently, no error — for
	// an UNKNOWN socket name. The FHandReachesSocketCommand Settle-phase DoesSocketExist guard is what keeps a
	// regressed IK_Hand_Gun socket from collapsing THIS reading (and the EventGraph effector) onto the weapon
	// origin, where both would coincide and the test would measure a false ~0 cm PASS.
	double ReadWeaponReach(USkeletalMeshComponent& Mesh, UStaticMeshComponent& Weapon, const TCHAR* HandBone, const TCHAR* SockName)
	{
		const FVector Hand = Mesh.GetSocketLocation(FName(HandBone));
		const FVector Sock = Weapon.GetSocketLocation(FName(SockName));
		return FVector::Dist(Hand, Sock);
	}

	// ============================================================================================
	// THE load-bearing HAND-REACHES-SOCKET assertion. Proves the TwoBoneIK node
	// (ik_bone=hand_l, world-space effector = the weapon IK_Hand_Gun socket world pos re-read each tick,
	// gated by WeaponIKAlpha) actually drives the off-hand bone ONTO the foregrip when IK is ON, versus the
	// free-swinging animated off-hand when it is PROVEN OFF. Phases (each with its own 20s backstop):
	//   Settle — wait for Locomotion/Idle weight > 0.99 (idle base pose), confirm the weapon component +
	//            hand_l resolve (ReadWeaponReach), ASSERT the IK_Hand_Gun socket + hand_l bone EXIST (the
	//            socket/bone guard — see below), and write WeaponIKAlpha=0 (the proven-off baseline).
	//   IKOff  — hold WeaponIKAlpha=0; capture distOff (hand_l -> IK_Hand_Gun) and read WeaponIKAlpha back 0.
	//            The read-back rules out a MISSING/MISNAMED WeaponIKAlpha property and confirms the injected 0
	//            is ACCEPTED — but it is a same-tick write-then-read (no anim eval between the write and the
	//            read), so it does NOT by itself prove OFF against an EventGraph re-derive. The OFF-is-off
	//            confound-immunity is the GAP GATE in IKOn: if WeaponIKAlpha were secretly re-derived (OFF->1),
	//            the "off" hand would ALSO reach the socket, distOff would collapse toward distOn, and the
	//            distOff - distOn > kReachGapMinCm gate would fail RED.
	//   IKOn   — drive WeaponIKAlpha=1; accumulate the MIN reach over the window (distOn) + the LAST reach
	//            (LastOn); assert WeaponIKAlpha reads back 1, distOn < kReachTolCm (the hand REACHES the socket),
	//            LastOn < kReachTolCm (the reach is SUSTAINED, not a momentary graze), AND distOff - distOn >
	//            kReachGapMinCm (the measured effect + the OFF-is-off gap gate: on reaches, off does not).
	//
	// The instrument (GetSocketLocation on the mesh bone + the weapon socket) shares NO code with the IK node
	// under test, at matched conditions (same possessed pawn, same idle pose), and OFF-is-off is enforced by
	// the gap gate (a re-derived alpha collapses distOff and fails RED) — the effect-level-probe convention.
	// The socket-existence guard closes the shared-fallback confound: a regressed IK_Hand_Gun socket would make
	// GetSocketLocation return the weapon ORIGIN for BOTH the EventGraph effector and this probe, coinciding at
	// a false ~0 cm PASS. Live probe (pie_handreach_drive.py): distOff ~53.5cm, distOn ~0.6cm mean / 0.11cm min,
	// gap ~53cm, WeaponIKAlpha read-back 0/1.
	// ============================================================================================
	class FHandReachesSocketCommand : public IAutomationLatentCommand
	{
	public:
		explicit FHandReachesSocketCommand(FAutomationTestBase* InTest)
			: Test(InTest), StartTime(0.0) {}

		virtual bool Update() override
		{
			if (StartTime == 0.0) { StartTime = FPlatformTime::Seconds(); }
			const double Now = FPlatformTime::Seconds();

			// The possessed-pawn -> Locomotion-baked-machine preamble (shared with the sibling
			// turn/aim/hand commands) — resolves Pawn/Mesh/Inst/LocoIdx/Loco or returns false (fall through to the budget).
			APawn* Pawn = nullptr; USkeletalMeshComponent* Mesh = nullptr; UAnimInstance* Inst = nullptr;
			int32 LocoIdx = INDEX_NONE;
			const FBakedAnimationStateMachine* Loco = nullptr;
			if (ResolvePossessedLocomotionContext(Pawn, Mesh, Inst, LocoIdx, Loco))
			{
				if (Phase == EPhase::Settle)
				{
					const int32 IdleIdx = FindBakedStateIndex(*Loco, TEXT("Idle"));
					if (IdleIdx != INDEX_NONE && Inst->GetInstanceStateWeight(LocoIdx, IdleIdx) > 0.99f)
					{
						// One-time contract check: the MT_Weapon component + hand_l must resolve ("verify
						// before relying"). A missing weapon component / bone is a hard hand-IK defect, not a skip.
						UStaticMeshComponent* Weapon = ResolveWeaponComponent(*Pawn, TEXT("MT_Weapon"));
						if (!Weapon)
						{
							Test->AddError(TEXT("[Settle] no MT_Weapon-tagged StaticMeshComponent (or no hand_l bone) on the possessed pawn — the off-hand weapon component is missing; cannot measure the hand reach"));
							return true;
						}

						// CRITICAL socket/bone-existence guard (closes the shared-fallback confound). Mirrors the
						// one-time foot_l/foot_r check in FFootIKNodeIntegrationCommand. UStaticMeshComponent::
						// GetSocketLocation returns the COMPONENT ORIGIN — silently — for an UNKNOWN socket name.
						// The socket-persistence bug has ALREADY occurred once (SM_MT_Rifle shipped socket-less).
						// If IK_Hand_Gun regresses, BOTH the EventGraph effector (WeaponIKTargetWorld) and this
						// test's ReadWeaponReach collapse to the weapon origin and COINCIDE — the IK drives hand_l
						// there, the test measures ~0 cm, and it PASSES GREEN with the off-hand never on the
						// foregrip. So assert BOTH exist on the SAME component the EventGraph reads, and fail LOUD.
						const bool bSockExists = Weapon->DoesSocketExist(FName(TEXT("IK_Hand_Gun")));
						// Discrimination proof (self-documenting): a KNOWN-bogus name must NOT exist on the same
						// component — proves the guard is not vacuously true and WOULD fire on a missing socket.
						const bool bBogusExists = Weapon->DoesSocketExist(FName(TEXT("__bogus__")));
						const int32 HandIdx = Mesh->GetBoneIndex(FName(TEXT("hand_l")));
						// ENFORCE THE DISCRIMINATION PROOF, do not merely print it. Previously bBogusExists
						// was read, formatted as "TRUE?!" in the info line below, and never gated on — so if
						// DoesSocketExist ever returned true for every name, the guard above would be vacuously
						// satisfied, this command would pass, and the reach assertion would go on to measure ~0 cm
						// against a collapsed origin. A control that cannot fail the test is not a control.
						if (bBogusExists)
						{
							Test->AddError(FString::Printf(
								TEXT("[Settle] SOCKET GUARD IS VACUOUS: DoesSocketExist('__bogus__') returned TRUE on %s, so 'IK_Hand_Gun exists' proves nothing — every subsequent reach number is measured against an unverified socket"),
								*GetNameSafe(Weapon->GetStaticMesh())));
							return true;
						}
						if (!bSockExists || HandIdx == INDEX_NONE)
						{
							Test->AddError(FString::Printf(
								TEXT("[Settle] weapon socket/bone contract FAILED: DoesSocketExist('IK_Hand_Gun')=%d, hand_l boneIdx=%d — a missing socket makes GetSocketLocation silently return the weapon ORIGIN, collapsing the effector and the probe onto one point (a false ~0 cm PASS). Failing LOUD instead of measuring the fallback."),
								bSockExists ? 1 : 0, HandIdx));
							return true;
						}
						Test->AddInfo(FString::Printf(
							TEXT("[Settle] socket/bone guard OK: DoesSocketExist('IK_Hand_Gun')=true, DoesSocketExist('__bogus__')=%s (guard discriminates — would fire on a missing socket), hand_l boneIdx=%d (mesh '%s')."),
							bBogusExists ? TEXT("TRUE?!") : TEXT("false"), HandIdx, *GetNameSafe(Mesh->GetSkeletalMeshAsset())));

						// Cache the resolved+validated weapon for IKOff/IKOn (no per-tick GetComponentsByTag re-scan).
						WeaponComp = Weapon;

						// Prove-OFF: WeaponIKAlpha is a pure external var; write 0 (default is 0 anyway).
						TryWriteFloatVar(*Inst, TEXT("WeaponIKAlpha"), 0.0);
						const double D = ReadWeaponReach(*Mesh, *Weapon, TEXT("hand_l"), TEXT("IK_Hand_Gun"));
						Test->AddInfo(FString::Printf(
							TEXT("[Settle] settled in Idle after %.1fs; weapon reach resolves (d=%.2f cm). Holding WeaponIKAlpha=0 for the OFF baseline."),
							Now - StartTime, D));
						Phase = EPhase::IKOff;
						StartTime = Now;
					}
				}
				else if (Phase == EPhase::IKOff)
				{
					TryWriteFloatVar(*Inst, TEXT("WeaponIKAlpha"), 0.0);
					// Reuse the weapon component cached+validated at Settle (no per-tick GetComponentsByTag re-scan).
					// The cached component is dereferenced unguarded while the pawn/mesh
					// are re-resolved each tick — if possession changed mid-test it can dangle. Validate it before
					// the deref so a vanished weapon fails LOUD instead of dereferencing a stale pointer.
					if (!IsValid(WeaponComp))
					{
						Test->AddError(TEXT("[IKOff] the MT_Weapon component cached at Settle is no longer valid (possession/pawn changed mid-test) — cannot measure the OFF-baseline hand reach against a vanished weapon component"));
						return true;
					}
					DistOff = ReadWeaponReach(*Mesh, *WeaponComp, TEXT("hand_l"), TEXT("IK_Hand_Gun"));

					if ((Now - StartTime) > 1.2)
					{
						double AlphaRead = -999.0; TryReadNumericVar(*Inst, TEXT("WeaponIKAlpha"), AlphaRead);
						if (!FMath::IsNearlyEqual(AlphaRead, 0.0, 0.01))
						{
							Test->AddError(FString::Printf(
								TEXT("[IKOff] WeaponIKAlpha read back %.3f, not 0 — the injected 0 was not accepted (missing/misnamed property, or a same-tick re-derive overwrote it); cannot establish the OFF baseline"),
								AlphaRead));
							return true;
						}
						if (!TryWriteFloatVar(*Inst, TEXT("WeaponIKAlpha"), 1.0))
						{
							Test->AddError(TEXT("[IKOff] WeaponIKAlpha is not a float/double property — cannot drive the IK gate"));
							return true;
						}
						Test->AddInfo(FString::Printf(
							TEXT("[IKOff] OFF baseline distOff=%.2f cm (WeaponIKAlpha read-back 0 — property present + injected value accepted; OFF-is-off is enforced by the gap gate in IKOn, not this same-tick read). Driving WeaponIKAlpha=1."),
							DistOff));
						Phase = EPhase::IKOn;
						StartTime = Now;
					}
				}
				else   // EPhase::IKOn
				{
					// Keep the external gate asserted (harmless to re-write each tick).
					TryWriteFloatVar(*Inst, TEXT("WeaponIKAlpha"), 1.0);
					// Reuse the weapon component cached+validated at Settle (no per-tick re-scan).
					// Guard the cached deref — possession could have changed mid-test,
					// leaving WeaponComp dangling; fail LOUD rather than dereferencing a stale pointer.
					if (!IsValid(WeaponComp))
					{
						Test->AddError(TEXT("[IKOn] the MT_Weapon component cached at Settle is no longer valid (possession/pawn changed mid-test) — cannot measure the ON hand reach against a vanished weapon component"));
						return true;
					}
					const double D = ReadWeaponReach(*Mesh, *WeaponComp, TEXT("hand_l"), TEXT("IK_Hand_Gun"));
					DistOn = FMath::Min(DistOn, D);   // best (closest) reach over the window
					LastOn = D;                        // last reach — the SUSTAINED-reach evidence (see below)

					// Give the IK ~2s to develop (the effector is EventGraph-read each tick), then adjudicate.
					if ((Now - StartTime) > 2.0)
					{
						double AlphaRead = -999.0; TryReadNumericVar(*Inst, TEXT("WeaponIKAlpha"), AlphaRead);
						const double Gap = DistOff - DistOn;
						Test->AddInfo(FString::Printf(
							TEXT("[IKOn] distOff=%.2f distOn(min)=%.2f last=%.2f WeaponIKAlpha read-back=%.2f. Gates: distOn < %.1f, last < %.1f (sustained), (distOff-distOn) > %.1f, alpha == 1."),
							DistOff, DistOn, LastOn, AlphaRead, kReachTolCm, kReachTolCm, kReachGapMinCm));

						bool bOk = true;
						if (!FMath::IsNearlyEqual(AlphaRead, 1.0, 0.01))
						{
							Test->AddError(FString::Printf(
								TEXT("[IKOn] ON NOT PROVEN: WeaponIKAlpha read back %.3f, not 1 — the injected gate did not stick"),
								AlphaRead));
							bOk = false;
						}
						if (!(DistOn < kReachTolCm))
						{
							Test->AddError(FString::Printf(
								TEXT("[IKOn] REACH FAILED: hand_l -> IK_Hand_Gun min distance %.2f cm not < %.1f — the off-hand did not reach the weapon socket with IK on (check WeaponIKTargetWorld derivation / effector-space / reachability)"),
								DistOn, kReachTolCm));
							bOk = false;
						}
						// Sustained-reach gate: distOn is the MIN (best single frame). Require the LAST ON sample
						// to also be within tolerance so the hand REACHES AND HOLDS the foregrip — a momentary
						// graze (min small, last large) is not the hand-IK effect.
						if (!(LastOn < kReachTolCm))
						{
							Test->AddError(FString::Printf(
								TEXT("[IKOn] REACH NOT SUSTAINED: last ON sample %.2f cm not < %.1f — the off-hand grazed the socket (min %.2f) but did not HOLD it; 'reaches AND holds' is required, not a momentary graze"),
								LastOn, kReachTolCm, DistOn));
							bOk = false;
						}
						if (!(Gap > kReachGapMinCm))
						{
							Test->AddError(FString::Printf(
								TEXT("[IKOn] NO MEASURED EFFECT: distOff %.2f - distOn %.2f = %.2f cm gap not > %.1f — IK-on is not materially closer than proven-off (the effect is not observable)"),
								DistOff, DistOn, Gap, kReachGapMinCm));
							bOk = false;
						}
						if (bOk)
						{
							Test->AddInfo(FString::Printf(
								TEXT("[Done] HAND REACHES SOCKET: with WeaponIKAlpha=1 the off-hand (hand_l) reached IK_Hand_Gun at %.2f cm min / %.2f cm last (both < %.1f — reaches AND holds) while the held-off (WeaponIKAlpha=0) off-hand was %.2f cm away (gap %.2f > %.1f). The gap gate is the OFF-is-off confound-immunity (a re-derived alpha would collapse distOff and fail RED); effect measured."),
								DistOn, LastOn, kReachTolCm, DistOff, Gap, kReachGapMinCm));
						}
						return true;
					}
				}
			}

			// Per-phase 20s budget (measured from each phase start). Outer backstop for a stalled resolve.
			if (PhaseBudgetExceeded(Test, Now, StartTime, PhaseName(Phase), TEXT("IK"))) { return true; }
			return false;   // keep polling
		}

	private:
		enum class EPhase : uint8 { Settle, IKOff, IKOn };

		static const TCHAR* PhaseName(EPhase P)
		{
			switch (P)
			{
			case EPhase::Settle: return TEXT("Settle");
			case EPhase::IKOff:  return TEXT("IKOff");
			default:             return TEXT("IKOn");
			}
		}

		// Reach tolerance (cm): IK-on must bring hand_l within this of the foregrip. Live probe: distOn ~0.6cm
		// mean / 0.11cm min — 5.0 sits ~8x above the measured on-reach and ~10x below the off distance.
		static constexpr double kReachTolCm = 5.0;
		// Minimum on-vs-off gap (cm): the measured effect. Live probe: gap ~53cm. 10.0 is ~5x below it and far
		// above any idle-pose jitter, a clean two-sided discriminator (on reaches, off does not).
		static constexpr double kReachGapMinCm = 10.0;

		FAutomationTestBase* Test;
		double StartTime;
		EPhase Phase = EPhase::Settle;
		UStaticMeshComponent* WeaponComp = nullptr;   // MT_Weapon component, resolved+validated once at Settle
		double DistOff = 0.0;
		double DistOn = 1.0e9;   // accumulate the MIN reach over the ON window
		double LastOn = -1.0;
	};

	// The shared GetTests skeleton the five filtered complex tests repeat. LoadPieRigs (emit the
	// _ManifestLoadFailed sentinel on load failure / zero rows), then filter rows by the given bool member —
	// adding (Rig, Map) per opted-in row; if ZERO rows opt in, emit the given _No*Rows silent-vanish sentinel.
	// Field selects the per-row opt-in flag; FeatureKey (e.g. "turnInPlace") + FeatureHuman (e.g. "turn-in-
	// place") fill the exact no-rows message; NoRowsBeautified/NoRowsPrefix are that sentinel's name + prefix.
	void BuildFilteredRigTestList(bool FPieRigRow::* Field, const TCHAR* FeatureKey, const TCHAR* FeatureHuman,
		const TCHAR* NoRowsBeautified, const FString& NoRowsPrefix,
		TArray<FString>& OutNames, TArray<FString>& OutCommands)
	{
		TArray<FPieRigRow> Rows; FString Err;
		const bool bLoaded = LoadPieRigs(Rows, Err);
		if (!bLoaded || Rows.Num() == 0)
		{
			// A successful load with a genuinely empty 'rigs' array has no OutError — synthesize one so the
			// sentinel's embedded message is never blank.
			if (bLoaded && Err.IsEmpty()) { Err = TEXT("pie_rigs.json loaded but yielded zero rig rows"); }
			EmitManifestErrorSentinel(OutNames, OutCommands, Err);
			return;
		}
		int32 RowCount = 0;
		for (const FPieRigRow& R : Rows)
		{
			if (R.*Field)
			{
				OutNames.Add(R.Rig);
				OutCommands.Add(R.Map);
				++RowCount;
			}
		}
		// Silent-vanish guard: the manifest loaded with rows but ZERO opted in -> emit a RED sentinel instead
		// of vanishing green (a typo of the opt-in key defaults every row to false).
		if (RowCount == 0)
		{
			OutNames.Add(NoRowsBeautified);
			OutCommands.Add(NoRowsPrefix + FString::Printf(
				TEXT("manifest loaded %d rows but none set %s:true — the %s test would silently vanish"),
				Rows.Num(), FeatureKey, FeatureHuman));
		}
	}

	// The gate half of RunGatedPieSubTest, shared with Pie.RigLocomotion (which has no no-rows sentinel): the
	// manifest-error sentinel prefix (AddError + fail RED), then the GatePieMap skip/missing decision. Returns
	// true when the row should run; otherwise OutResult is the RunTest return value (true = SKIP, false = RED).
	bool GatePieRigRow(FAutomationTestBase* Test, const FString& Parameters, bool& OutResult)
	{
		if (Parameters.StartsWith(kManifestErrorPrefix))
		{
			Test->AddError(FString::Printf(
				TEXT("pie_rigs.json failed to load — per-rig PIE rows have NOT run: %s"),
				*Parameters.RightChop(kManifestErrorPrefix.Len())));
			OutResult = false;
			return false;
		}

		const EPieMapGate Gate = GatePieMap(Parameters);
		if (Gate == EPieMapGate::Skip)
		{
			Test->AddInfo(FString::Printf(TEXT("SKIP (localOnly map absent on this machine): %s"), *Parameters));
			OutResult = true;
			return false;
		}
		if (Gate == EPieMapGate::Missing)
		{
			Test->AddError(FString::Printf(TEXT("map package does not exist (non-local row): %s"), *Parameters));
			OutResult = false;
			return false;
		}
		return true;
	}

	// The shared RunTest skeleton the five filtered complex tests repeat. Checks the no-rows
	// sentinel prefix (AddError + fail RED), then GatePieRigRow, and only once the row passes builds
	// MakeCommand()'s latent command and hands it to EnqueuePieMapRun. NoRowsErrorLabel is the per-test
	// "Pie.<Name> registered ZERO <feature> sub-tests" text.
	bool RunGatedPieSubTest(FAutomationTestBase* Test, const FString& Parameters, const FString& NoRowsPrefix,
		const TCHAR* NoRowsErrorLabel, TFunction<IAutomationLatentCommand*()> MakeCommand)
	{
		if (Parameters.StartsWith(NoRowsPrefix))
		{
			Test->AddError(FString::Printf(TEXT("%s — %s"),
				NoRowsErrorLabel, *Parameters.RightChop(NoRowsPrefix.Len())));
			return false;
		}
		bool bResult = false;
		if (!GatePieRigRow(Test, Parameters, bResult)) { return bResult; }

		// The map's GameMode override auto-possesses this rig's pawn (ABP_MT_Locomotion_C). Real PIE + real RHI.
		EnqueuePieMapRun(Test, Parameters, MakeCommand());
		return true;
	}

	// ============================================================================================
	// Crowd-scale verification (Pie.Army.*) — budget assertion + spot-check + no-frozen.
	//
	// These tests spawn the SHIPPED tiered army into the PIE world in C++ (UWorld::SpawnActor works in-PIE;
	// the Python spawner's pre-PIE detour exists only because EditorActorSubsystem refuses during a live PIE)
	// and apply the same per-tier knobs army_spawn.py sets, from the same army_config.json: forced
	// AlwaysTickPoseAndRefreshBones (or the 0x0 headless viewport culls the pose tick and the crowd
	// undercounts), per-tier URO, per-tier forced LOD.
	//
	// WHAT ACTUALLY RUNS WHERE — read this before trusting a green pie tier.
	//   * Per-tier SHAPE readback (URO + forced LOD vs config), well-formed ceilings, full spawn count, and
	//     ANIMATION MEMORY (GetResourceSizeBytes summed over the crowd, under mb_ceiling AND proven to scale
	//     with N): these need NO stats thread, so they run and HARD-FAIL in headless CI. The memory assertion
	//     is a real CI gate; the shape/count/ceiling checks catch config or _apply_tier drift.
	//   * The anim-eval MS gate (summed PerformAnimEvaluation_WorkerThread + serial AnimGameThreadTime vs the
	//     ceilings) reads STATGROUP_Anim off the stats thread, which only settles with a rendered viewport. In
	//     headless -unattended it NEVER settles (see the EPhase::Prime degrade), so in CI this path DEGRADES to
	//     AddInfo + a pointer to the committed interactive measurement and the test still passes. A GREEN PIE
	//     TIER THEREFORE DOES NOT MEAN THE LIVE MS BUDGET WAS VALIDATED — only that the structural + memory
	//     checks held. The live ms number's authority is the interactive-editor sweep in
	//     research/fixtures/army-contact/n_vs_eval_ms.csv and army_config budget._measured; the C++ ms
	//     hard-gate (on the serial game-thread cost) executes ONLY when a frame settles (interactive) and has
	//     not run in the automated headless suite. See army_config _costs_validated_via_interactive_probe.
	//
	// HARD GATE = AVERAGE, PEAK = DISCLOSED RISK (a methodology decision). The driven
	// load oscillates with the speed wave and spikes on the montage volley, so the serial game-thread cost has an
	// AVG (~3.6 ms at N=46) and a reproducible PEAK (~4.21 ms, over 4.0). The gate asserts on the AVG over the
	// settled window because (a) the 4.0 ms ceiling is itself a sustained allocation (24% of a 60 fps 16.67 ms
	// frame), (b) the average governs sustained throughput, (c) a lone 4.2 ms peak is still 25% of the frame with
	// ~12 ms of frame margin so it does not by itself drop a frame, and (d) a short-window max is too noisy to be
	// a reliable gate. The PEAK is REPORTED (AddInfo, disclosed risk) but never failed on. N=46 is KEPT on that
	// basis (avg 3.6/4.0). The gate is NOT toothless — the driven AVG crosses 4.0 at N~=52.
	//
	// WHY THE STAT COUNTERS, NOT world_ms_per_tick. An earlier ~324 ms `world_ms_per_tick` reading is the editor
	// idle-throttle FLOOR (present with the army despawned), not the crowd's compute; the stat cycle counters
	// measure the CPU cost of the anim work DONE per frame regardless of the frame's wall-clock length, so
	// they price the crowd. frame-time / N is forbidden.
	// ============================================================================================

	struct FArmyTierCfg
	{
		FString Name;
		int32 Count = 0;
		double Radius = 0.0;
		bool bUro = false;
		int32 ForcedLod = 0;
		// A ring's optional "budget" object: present means the ring is handed to the Animation Budget
		// Allocator (which then owns its tick rate and switches its URO off), at this significance.
		bool bBudgeted = false;
		float BudgetSignificance = 1.0f;
		bool bBudgetTickEvenIfNotRendered = false;
	};

	struct FArmyCfg
	{
		TArray<FArmyTierCfg> Tiers;
		// 0.0, NOT 8.0/4.0. These are loaded from army_config.json and are REQUIRED there; a baked
		// default that happens to be usable is what let a deleted or misspelled ceiling pass silently.
		double MsCeiling = 0.0;
		double GtMsCeiling = 0.0;
		bool bHasMbCeiling = false;
		double MbCeiling = 0.0;
		FString RosterPath = TEXT("/Game/MT/BP_MT_TestPawn.BP_MT_TestPawn_C");
		double SpawnZ = 110.0;
		int32 SpotCheckK = 10;    // instances sampled by the spot-check / no-frozen tests
		// Fallback only — army_config.json's shipped_safe_n_worker_ceiling overrides this at load, and always
		// should: 42 was the STANDING-crowd worker-ceiling N from the
		// original (superseded) sweep. The current DRIVEN figure is 33 — see budget._ceiling_policy.
		int32 ShippedSafeN = 33;
		// Pie.Army.OnCapsule: the crowd's rig mix. Roster is rig -> pawn class path (every key; RosterPath above
		// stays the Manny entry the other rows spawn). CrowdRigs is the union of spawn.tier_pawns over the rings
		// with count > 0, in first-seen order — the rigs army_spawn.py actually spawns. CrowdAnimClass is
		// spawn.crowd_anim_class minus its `_` comment keys: rig -> the graph the crowd swaps that rig onto.
		TMap<FString, FString> Roster;
		TArray<FString> CrowdRigs;
		TMap<FString, FString> CrowdAnimClass;
	};

	// Parse army_config.json into FArmyCfg. false + OutError on read/parse failure (never a silent default —
	// a test that silently ran against baked-in ceilings would not be asserting against the shipped config).
	bool LoadArmyConfig(FArmyCfg& Out, FString& OutError)
	{
		TSharedPtr<FJsonObject> Root;
		if (!MotionTerminalTestJson::LoadManifestRoot(TEXT("army_config.json"), Root, OutError)) { return false; }
		const TSharedPtr<FJsonObject>* Rings = nullptr;
		if (!Root->TryGetObjectField(TEXT("rings"), Rings) || !Rings)
		{
			OutError = TEXT("army_config.json has no 'rings' object");
			return false;
		}
		static const TCHAR* TierNames[] = { TEXT("hero"), TEXT("mid"), TEXT("far") };
		for (const TCHAR* TierName : TierNames)
		{
			const TSharedPtr<FJsonObject>* Ring = nullptr;
			if (!(*Rings)->TryGetObjectField(TierName, Ring) || !Ring) { continue; }
			FArmyTierCfg Tier;
			Tier.Name = TierName;
			double Num = 0.0;
			if ((*Ring)->TryGetNumberField(TEXT("count"), Num)) { Tier.Count = (int32)Num; }
			(*Ring)->TryGetNumberField(TEXT("radius"), Tier.Radius);
			(*Ring)->TryGetBoolField(TEXT("uro"), Tier.bUro);
			if ((*Ring)->TryGetNumberField(TEXT("forced_lod"), Num)) { Tier.ForcedLod = (int32)Num; }
			const TSharedPtr<FJsonObject>* RingBudget = nullptr;
			if ((*Ring)->TryGetObjectField(TEXT("budget"), RingBudget) && RingBudget)
			{
				double Sig = 0.0;
				if (!(*RingBudget)->TryGetNumberField(TEXT("significance"), Sig) || !(Sig > 0.0 && Sig <= 1.0))
				{
					OutError = FString::Printf(TEXT("army_config.json ring '%s' has a 'budget' without a 'significance' in (0, 1]"), TierName);
					return false;
				}
				Tier.bBudgeted = true;
				Tier.BudgetSignificance = (float)Sig;
				(*RingBudget)->TryGetBoolField(TEXT("tick_even_if_not_rendered"), Tier.bBudgetTickEvenIfNotRendered);
			}
			Out.Tiers.Add(Tier);
		}
		if (Out.Tiers.Num() == 0) { OutError = TEXT("army_config.json 'rings' had no hero/mid/far tiers"); return false; }
		const TSharedPtr<FJsonObject>* Budget = nullptr;
		// THE CEILINGS ARE REQUIRED, which is what this function's own contract above already claimed.
		// An earlier version broke that claim: 'budget' was optional and both ceiling reads discarded
		// their result, so deleting or misspelling gt_ms_ceiling left the baked 4.0 standing and a 3 ms
		// result passed a 2 ms budget the config believed it was enforcing. Fail the load instead.
		if (!Root->TryGetObjectField(TEXT("budget"), Budget) || !Budget)
		{
			OutError = TEXT("army_config.json has no 'budget' object — the ceilings it carries are what this gate asserts against");
			return false;
		}
		{
			if (!(*Budget)->TryGetNumberField(TEXT("ms_ceiling"), Out.MsCeiling)
				|| !(*Budget)->TryGetNumberField(TEXT("gt_ms_ceiling"), Out.GtMsCeiling)
				|| !(Out.MsCeiling > 0.0 && Out.GtMsCeiling > 0.0))
			{
				OutError = TEXT("army_config.json 'budget' needs positive numeric 'ms_ceiling' and 'gt_ms_ceiling'");
				return false;
			}
			double Mb = 0.0;
			// mb_ceiling may be JSON null until one is measured — TryGetNumberField fails on null, leaving it unset.
			if ((*Budget)->TryGetNumberField(TEXT("mb_ceiling"), Mb)) { Out.bHasMbCeiling = true; Out.MbCeiling = Mb; }
			double SafeN = 0.0;
			if ((*Budget)->TryGetNumberField(TEXT("shipped_safe_n_worker_ceiling"), SafeN) && SafeN >= 1.0) { Out.ShippedSafeN = (int32)SafeN; }
		}
		const TSharedPtr<FJsonObject>* Roster = nullptr;
		if (Root->TryGetObjectField(TEXT("roster"), Roster) && Roster)
		{
			FString Manny;
			if ((*Roster)->TryGetStringField(TEXT("manny"), Manny) && !Manny.IsEmpty()) { Out.RosterPath = Manny; }
			for (const TPair<FString, TSharedPtr<FJsonValue>>& KV : (*Roster)->Values)
			{
				FString ClassPath;
				if (KV.Value.IsValid() && KV.Value->TryGetString(ClassPath) && !ClassPath.IsEmpty()) { Out.Roster.Add(KV.Key, ClassPath); }
			}
		}
		const TSharedPtr<FJsonObject>* Spawn = nullptr;
		if (Root->TryGetObjectField(TEXT("spawn"), Spawn) && Spawn)
		{
			(*Spawn)->TryGetNumberField(TEXT("z"), Out.SpawnZ);
			// The rigs a ring that will actually spawn asks for, as army_spawn.spawn_army computes `requested`.
			// A rig named here but absent from the roster is an ERROR, not a skipped rig: the Python spawner
			// refuses that crowd (require_all_rigs), so a gate that quietly measured the rest would be measuring
			// a crowd that is never shipped.
			// A malformed entry is an error too: skipping it would silently drop that rig from the measured set.
			// spawn.require_all_rigs false is army_spawn's deliberate escape hatch (spawn without the absent rigs),
			// so it is honoured the same way here.
			bool bRequireAllRigs = true;
			(*Spawn)->TryGetBoolField(TEXT("require_all_rigs"), bRequireAllRigs);
			const TSharedPtr<FJsonObject>* TierPawns = nullptr;
			if ((*Spawn)->TryGetObjectField(TEXT("tier_pawns"), TierPawns) && TierPawns)
			{
				for (const FArmyTierCfg& Tier : Out.Tiers)
				{
					if (Tier.Count <= 0) { continue; }
					const TArray<TSharedPtr<FJsonValue>>* Rigs = nullptr;
					if (!(*TierPawns)->TryGetArrayField(Tier.Name, Rigs) || !Rigs)
					{
						OutError = FString::Printf(TEXT("army_config.json spawn.tier_pawns has no rig array for ring '%s' (count %d)"), *Tier.Name, Tier.Count);
						return false;
					}
					for (const TSharedPtr<FJsonValue>& V : *Rigs)
					{
						FString Rig;
						if (!V.IsValid() || V->Type != EJson::String || !V->TryGetString(Rig) || Rig.IsEmpty())
						{
							OutError = FString::Printf(TEXT("army_config.json spawn.tier_pawns.%s has an entry that is not a rig name string"), *Tier.Name);
							return false;
						}
						if (!Out.Roster.Contains(Rig))
						{
							if (!bRequireAllRigs) { continue; }
							OutError = FString::Printf(TEXT("army_config.json spawn.tier_pawns.%s names rig '%s', which is not in 'roster'"), *Tier.Name, *Rig);
							return false;
						}
						Out.CrowdRigs.AddUnique(Rig);
					}
				}
			}
			const TSharedPtr<FJsonObject>* AnimMap = nullptr;
			if ((*Spawn)->TryGetObjectField(TEXT("crowd_anim_class"), AnimMap) && AnimMap)
			{
				for (const TPair<FString, TSharedPtr<FJsonValue>>& KV : (*AnimMap)->Values)
				{
					FString AnimPath;
					if (KV.Key.StartsWith(TEXT("_"))) { continue; }   // `_why` and friends are comments
					if (KV.Value.IsValid() && KV.Value->TryGetString(AnimPath) && !AnimPath.IsEmpty()) { Out.CrowdAnimClass.Add(KV.Key, AnimPath); }
				}
			}
		}
		double KNum = 0.0;
		if (Root->TryGetNumberField(TEXT("spot_check_k"), KNum) && KNum >= 1.0) { Out.SpotCheckK = (int32)KNum; }
		return true;
	}

	// Spawn the tiered crowd into the PIE world, mirroring army_spawn.py's per-tier knobs. Appends to
	// OutPawns; returns how many spawned. Every pawn is set up for in-place locomotion (MOVE_None + a
	// forward velocity the ABP derives Speed from — army_conductor.py's mechanism) so the graph runs
	// walk/run: a methodology decision moved the budget test's hard gate to the DRIVEN
	// average (the original standing/at-rest measurement under-priced the shipped cost by ~1.4-1.8x).
	int32 SpawnArmyIntoPIE(UWorld* World, const FArmyCfg& Cfg, TArray<APawn*>& OutPawns, FString& OutError)
	{
		UClass* PawnClass = LoadClass<APawn>(nullptr, *Cfg.RosterPath);
		if (!PawnClass)
		{
			OutError = FString::Printf(TEXT("could not load roster pawn class '%s'"), *Cfg.RosterPath);
			return 0;
		}
		int32 Made = 0;
		for (const FArmyTierCfg& Tier : Cfg.Tiers)
		{
			for (int32 i = 0; i < Tier.Count; ++i)
			{
				const double Angle = (2.0 * PI * i) / FMath::Max(Tier.Count, 1);
				const FVector Loc(Tier.Radius * FMath::Cos(Angle), Tier.Radius * FMath::Sin(Angle), Cfg.SpawnZ);
				const FRotator Rot(0.0, FMath::RadiansToDegrees(Angle) + 180.0, 0.0);
				FActorSpawnParameters Params;
				Params.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
				APawn* Pawn = World->SpawnActor<APawn>(PawnClass, Loc, Rot, Params);
				if (!Pawn) { continue; }
				if (USkeletalMeshComponent* Mesh = Pawn->FindComponentByClass<USkeletalMeshComponent>())
				{
					Mesh->VisibilityBasedAnimTickOption = EVisibilityBasedAnimTickOption::AlwaysTickPoseAndRefreshBones;
					Mesh->bEnableUpdateRateOptimizations = Tier.bUro;
					Mesh->SetForcedLOD(Tier.ForcedLod);
				}
				if (UCharacterMovementComponent* CMC = Pawn->FindComponentByClass<UCharacterMovementComponent>())
				{
					CMC->SetMovementMode(MOVE_None);
					CMC->SetComponentTickEnabled(false);
					const FVector Fwd = Pawn->GetActorForwardVector();
					CMC->Velocity = FVector(Fwd.X * 400.0, Fwd.Y * 400.0, 0.0);
				}
				OutPawns.Add(Pawn);
				++Made;
			}
		}
		return Made;
	}

	// Shared by the three Pie.Army.* commands' Update() — resolves the PIE world exactly as
	// each one repeated. Returns true once World is non-null and ready to use this tick. On false, the
	// caller must `return OutUpdateResult` immediately (false = keep polling; true = gave up after the 20s
	// timeout, error already logged).
	bool TryResolveArmyPieWorld(FAutomationTestBase* Test, double StartTime, double Now, const TCHAR* LogTag,
		UWorld*& OutWorld, bool& OutUpdateResult)
	{
		FWorldContext* Pie = GEditor ? GEditor->GetPIEWorldContext() : nullptr;
		OutWorld = Pie ? Pie->World() : nullptr;
		if (OutWorld) { return true; }
		if (Now - StartTime > 20.0)
		{
			Test->AddError(FString::Printf(TEXT("[%s] no PIE world after 20s"), LogTag));
			OutUpdateResult = true;
		}
		else
		{
			OutUpdateResult = false;
		}
		return false;
	}

	// Shared by the three Pie.Army.* commands' Spawn phase — LoadArmyConfig -> SpawnArmyIntoPIE
	// -> compare Made vs Want -> AddError-on-short. Returns true and fills OutCfg/OutPawns/OutMade on success;
	// on false it has already called Test->AddError and the caller should `return true` itself.
	bool SpawnShippedArmyOrFail(FAutomationTestBase* Test, UWorld* World, const TCHAR* LogTag,
		FArmyCfg& OutCfg, TArray<APawn*>& OutPawns, int32& OutMade)
	{
		FString Err;
		if (!LoadArmyConfig(OutCfg, Err))
		{
			Test->AddError(FString::Printf(TEXT("[%s] %s"), LogTag, *Err));
			return false;
		}
		OutMade = SpawnArmyIntoPIE(World, OutCfg, OutPawns, Err);
		int32 Want = 0; for (const FArmyTierCfg& T : OutCfg.Tiers) { Want += T.Count; }
		if (OutMade < Want)
		{
			Test->AddError(FString::Printf(TEXT("[%s] spawned %d/%d army pawns (%s)"), LogTag, OutMade, Want, *Err));
			return false;
		}
		return true;
	}

	// The animation-memory confound guard, extracted to a pure function so a synthetic
	// unit test (MotionTerminal.Verify.ArmyBudgetZeroGuard, below) can exercise it directly without a live
	// PIE crowd. History: it was first anchored on the FIRST-spawned (hero-tier) pawn's bytes; a later
	// change moved the anchor to the crowd MINIMUM, reasoning that was more robust — a later check
	// found that this was true in general (a smaller anchor makes `TotalMb > PerMb * Counted * 0.5` more
	// permissive) but ALSO not the actual defect: re-run against the real shipped hero 6 / mid 8 / far 32
	// config, an 8-pawn mid-tier collapsing to zero totals 3.72 MB, which clears BOTH the old hero-anchored
	// threshold (3.45 MB) and the new min-anchored one (2.02 MB). A scale-vs-anchor inequality cannot
	// reliably catch a middle-weight tier collapsing, whichever anchor is picked — that is a structural
	// limit of the approach, not a bug in either anchor choice.
	//
	// What actually catches it (and is checked first, independent of anchor/tier shape): any counted
	// instance reading exactly 0 bytes means THAT instance's measurement did not work — full stop. The
	// scale check is kept as a secondary/legacy signal (it still catches an all-instances-undercounted
	// reading that never even collapses to a clean zero) but must not be relied on alone.
	//
	// ---- TWO LIMITS OF THIS GUARD, RECORDED AS DECISIONS ----
	//
	// (a) IT CANNOT CATCH A TIER DEGRADING TO SMALL-BUT-NONZERO. A mid tier reading 1 byte each instead
	//     of 0 passes the zero check and, being 8 of 46 instances, comfortably clears the scale check
	//     too. LEFT AS IS: the only collapse ever OBSERVED is the clean zero (UE's stat reader returns
	//     exactly 0 when it has nothing for an instance; it does not return a plausible-looking small
	//     number), and the alternative — a per-instance floor — needs a per-tier expected byte size, i.e.
	//     a second measured constant to keep in sync with the rig set, whose staleness would be a NEW
	//     silent-failure surface of exactly the kind this test suite works to remove. A guard that
	//     needs a hand-maintained magic number per tier is not obviously better than one that catches
	//     the observed mode and says plainly that it catches only that.
	//
	// (b) THE ZERO CHECK WOULD FALSE-POSITIVE ON A LEGITIMATELY ZERO-ANIM-COST TIER — a far tier of
	//     static meshes, say. UNREACHABLE at the shipped 6/8/32 shape (every tier is a skeletal pawn
	//     running the same ABP; army_config.json's rings differ in LOD and tick rate, never in whether
	//     an anim instance exists). It becomes reachable the day a tier is changed to static meshes, and
	//     the correct response THEN is to stop counting that tier, not to weaken the check — the zero
	//     reading would be true, so the check would be right and the input wrong.
	//
	// Both are asserted-on-the-observed-mode by Verify.ArmyBudgetZeroGuard, whose negative path is the
	// collapsed mid tier and whose positive control is the healthy 6/8/32 crowd.
	void CheckArmyMemoryReading(const TArray<uint64>& InstanceBytes, int32 SpawnedN, double MbCeiling,
		bool bHasMbCeiling, TArray<FString>& OutErrors, double& OutTotalMb, int32& OutZeroCount)
	{
		uint64 TotalBytes = 0, MinBytes = 0; int32 ZeroCount = 0;
		for (uint64 B : InstanceBytes)
		{
			TotalBytes += B;
			if (B == 0) { ++ZeroCount; }
			else if (MinBytes == 0 || B < MinBytes) { MinBytes = B; }
		}
		const int32 Counted = InstanceBytes.Num();
		const double TotalMb = TotalBytes / (1024.0 * 1024.0);
		const double PerMb = MinBytes / (1024.0 * 1024.0);
		OutTotalMb = TotalMb;
		OutZeroCount = ZeroCount;

		// 1) ZERO-READING (deterministic, the real gate — see the block comment above).
		if (ZeroCount > 0)
		{
			OutErrors.Add(FString::Printf(
				TEXT("[Army.Budget] %d of %d counted instance(s) read a ZERO animation-memory size — partial ")
				TEXT("measurement collapse, caught independent of tier shape or anchor choice"),
				ZeroCount, Counted));
		}

		// 2) SCALE (secondary/legacy signal, min-anchored). Does NOT reliably catch a mid-weight tier
		// collapsing (see above) — it is a floor on the reading being grossly wrong, not a tier-shape check.
		const bool bScaleBad = (Counted < SpawnedN) || !(TotalMb > PerMb * Counted * 0.5);
		if (bScaleBad)
		{
			OutErrors.Add(FString::Printf(
				TEXT("[Army.Budget] animation-memory measurement did not scale with N (counted %d of %d; total ")
				TEXT("%.3f MB vs per-instance %.4f MB) — the reading is not trustworthy"),
				Counted, SpawnedN, TotalMb, PerMb));
		}

		// Ceiling check only means anything once the reading itself is trustworthy by both checks above.
		if (ZeroCount == 0 && !bScaleBad && bHasMbCeiling && !(TotalMb < MbCeiling))
		{
			OutErrors.Add(FString::Printf(
				TEXT("[Army.Budget] OVER memory ceiling: animation memory %.3f MB >= mb_ceiling %.1f MB at N=%d"),
				TotalMb, MbCeiling, SpawnedN));
		}
	}

	// The budget assertion. Spawn the shipped army -> settle -> prime `stat anim` -> wait for the stats
	// thread to settle -> read STATGROUP_Anim -> assert worker/game-thread ms under the config ceilings.
	class FArmyBudgetCommand : public IAutomationLatentCommand
	{
	public:
		explicit FArmyBudgetCommand(FAutomationTestBase* InTest) : Test(InTest) {}

		virtual bool Update() override
		{
			if (StartTime == 0.0) { StartTime = FPlatformTime::Seconds(); }
			const double Now = FPlatformTime::Seconds();
			UWorld* World = nullptr; bool bWorldResult = false;
			if (!TryResolveArmyPieWorld(Test, StartTime, Now, TEXT("Army.Budget"), World, bWorldResult)) { return bWorldResult; }
#if !STATS
			Test->AddError(TEXT("[Army.Budget] STATS not compiled — cannot measure anim-eval cost (needs a Development editor build; --tier pie runs one)."));
			return true;
#else
			if (Phase == EPhase::Spawn)
			{
				// SpawnArmyIntoPIE locomotion-drives the crowd so the interactive stat read is NOT idle. This
				// drives locomotion only (a fixed forward velocity) — it does NOT run the full conductor (speed/turn/aim
				// waves + montage sectors), so the MONTAGE-VOLLEY gt PEAK (~4.21 ms, over 4.0) does NOT appear
				// here; that full DRIVEN cost is the interactive conductor sweep (n_vs_eval_ms.csv, condition=driven
				// / army_config budget._measured). The headless ms path degrades regardless (see EPhase::Prime);
				// memory (this test's only CI number) is ~invariant to driving.
				TArray<APawn*> Pawns; int32 Made = 0;
				if (!SpawnShippedArmyOrFail(Test, World, TEXT("Army.Budget"), Cfg, Pawns, Made)) { return true; }
				SpawnedN = Made;
				for (APawn* P : Pawns) { Crowd.Add(P); }   // kept for the animation-memory pass (below)

				// Config ceilings must be well-formed — a null/zero/negative ceiling could never gate anything,
				// so treat that as a failure rather than let it read green.
				if (!(Cfg.MsCeiling > 0.0 && Cfg.GtMsCeiling > 0.0))
				{
					Test->AddError(FString::Printf(
						TEXT("[Army.Budget] army_config budget ceilings are not well-formed (ms_ceiling=%.3f, gt_ms_ceiling=%.3f — both must be > 0)"),
						Cfg.MsCeiling, Cfg.GtMsCeiling));
					return true;
				}

				// Read the per-tier shape back off the spawned meshes. This does NOT independently re-verify the
				// tier mechanism — it reads the URO/LOD SpawnArmyIntoPIE just set from THIS SAME Cfg, so a
				// uniformly-wrong config would be applied and read back "consistent". What it DOES catch as a real
				// headless failure: a config that fails to load / malformed ceilings (checked above), a short
				// spawn count (above), meshless pawns, and a PARTIAL application where some meshes in a tier did
				// not take the URO/LOD the rest did (a genuine _apply_tier or PIE-duplication regression). Pawns
				// spawn in tier order (hero, mid, far), so a running offset maps them back to tiers.
				int32 Offset = 0;
				bool bShapeOk = true;
				FString ShapeReport;
				for (const FArmyTierCfg& T : Cfg.Tiers)
				{
					int32 UroBad = 0, LodBad = 0, Meshless = 0;
					for (int32 k = 0; k < T.Count; ++k)
					{
						APawn* P = Pawns.IsValidIndex(Offset + k) ? Pawns[Offset + k] : nullptr;
						USkeletalMeshComponent* M = P ? P->FindComponentByClass<USkeletalMeshComponent>() : nullptr;
						if (!M) { ++Meshless; continue; }
						if (static_cast<bool>(M->bEnableUpdateRateOptimizations) != T.bUro) { ++UroBad; }
						if (M->GetForcedLOD() != T.ForcedLod) { ++LodBad; }
					}
					Offset += T.Count;
					ShapeReport += FString::Printf(TEXT(" %s[n=%d uro=%d lod=%d]"), *T.Name, T.Count, T.bUro ? 1 : 0, T.ForcedLod);
					if (UroBad || LodBad || Meshless)
					{
						bShapeOk = false;
						Test->AddError(FString::Printf(
							TEXT("[Army.Budget] tier '%s' shape DRIFT vs army_config: %d/%d wrong URO, %d/%d wrong forced-LOD, %d meshless (expected uro=%d lod=%d)"),
							*T.Name, UroBad, T.Count, LodBad, T.Count, Meshless, T.bUro ? 1 : 0, T.ForcedLod));
					}
				}
				if (!bShapeOk) { return true; }

				Test->AddInfo(FString::Printf(
					TEXT("[Army.Budget] spawned N=%d, per-tier shape VERIFIED against army_config (URO + forced LOD match):%s. Shipped config sets NO global a.URO.ForceAnimRate; AlwaysTickPoseAndRefreshBones forced so the 0x0 headless viewport does not cull the pose tick. Live anim-eval cost is validated SEPARATELY via the interactive probe (n_vs_eval_ms.csv) — this run does not measure it."),
					Made, *ShapeReport));
				Phase = EPhase::Settle; PhaseStart = Now;
				return false;
			}
			if (Phase == EPhase::Settle)
			{
				// Let spawns register + graphs settle, then open the Anim window. Headless automation has no stat
				// HUD, so `stat anim` alone never takes the primary enable that makes the stats thread assemble
				// frames (an earlier run's frames_sampled:0); the window holds its own reference, independent of any
				// viewport, and enables the group. Closed in Teardown().
				if (Now - PhaseStart > 1.5)
				{
					StatWindow.Begin(TEXT("Anim"));
					Phase = EPhase::Prime; PhaseStart = Now;
				}
				return false;
			}
			// ---- Animation MEMORY at N (the budget covers anim-eval ms + MEMORY). Unlike the ms path this reads
			// GetResourceSizeBytes off the live components — NO stats thread — so it is a REAL headless assertion,
			// not a degrade. Measured once, after the settle so per-instance transient arrays (bone buffers, the
			// anim proxy) are allocated. Checked by CheckArmyMemoryReading (above FArmyBudgetCommand) — see its
			// header comment for why this is TWO independent checks (zero-reading + scale), not one. ----
			if (!bMemDone)
			{
				bMemDone = true;
				TArray<uint64> InstanceBytes;
				for (const TWeakObjectPtr<APawn>& WP : Crowd)
				{
					APawn* P = WP.Get();
					USkeletalMeshComponent* M = P ? P->FindComponentByClass<USkeletalMeshComponent>() : nullptr;
					if (!M) { continue; }
					uint64 B = static_cast<uint64>(M->GetResourceSizeBytes(EResourceSizeMode::EstimatedTotal));
					if (UAnimInstance* AI = M->GetAnimInstance())
					{
						B += static_cast<uint64>(AI->GetResourceSizeBytes(EResourceSizeMode::EstimatedTotal));
					}
					InstanceBytes.Add(B);
				}
				const int32 Counted = InstanceBytes.Num();
				TArray<FString> MemErrors; double TotalMb = 0.0; int32 ZeroCount = 0;
				CheckArmyMemoryReading(InstanceBytes, SpawnedN, Cfg.MbCeiling, Cfg.bHasMbCeiling, MemErrors, TotalMb, ZeroCount);
				Test->AddInfo(FString::Printf(
					TEXT("[Army.Budget] animation memory at N=%d: %.3f MB total (SkeletalMeshComponent + AnimInstance GetResourceSizeBytes / EstimatedTotal), ~%.4f MB/instance, %d zero-reading(s). mb_ceiling=%s."),
					Counted, TotalMb, Counted > 0 ? TotalMb / Counted : 0.0, ZeroCount,
					Cfg.bHasMbCeiling ? *FString::Printf(TEXT("%.1f MB"), Cfg.MbCeiling) : TEXT("null (unset — logging only)")));
				for (const FString& Err : MemErrors) { Test->AddError(Err); }
			}

			// EPhase::Prime — read STATGROUP_Anim. Assert the physically-binding cost when a settled frame
			// exists; degrade honestly (neither a vacuous green nor a red) when headless produces no frame.
			const MotionTerminal::Stats::FWindowRead Read = StatWindow.Read(30);
			TMap<FString, double> Ms, Peak;
			for (const TPair<FString, MotionTerminal::Stats::FCycleStat>& Stat : Read.Cycles)
			{
				Ms.Add(Stat.Key, Stat.Value.AvgMs);
				Peak.Add(Stat.Key, Stat.Value.PeakMs);
			}
			const int32 Frames = Read.Frames;
			const bool bHaveWorker = Ms.Contains(TEXT("PerformAnimEvaluation_WorkerThread"));
			if (Frames > 0 && bHaveWorker && (Now - PhaseStart) > 2.0)
			{
				const double Worker = Ms.FindRef(TEXT("PerformAnimEvaluation_WorkerThread"));
				const double WorkerPk = Peak.FindRef(TEXT("PerformAnimEvaluation_WorkerThread"));
				const double Gt = Ms.FindRef(TEXT("AnimGameThreadTime"));
				const double GtPk = Peak.FindRef(TEXT("AnimGameThreadTime"));
				Test->AddInfo(FString::Printf(
					TEXT("[Army.Budget] N=%d settled over %d frames (locomotion-driven; NO conductor waves/montages, so this UNDERSTATES the shipped driven cost — see n_vs_eval_ms.csv condition=driven).\n  AnimGameThreadTime (HARD gate, SERIAL): avg %.3f ms -> %s %.1f ; peak %.3f ms (disclosed risk, NOT gated)\n  PerformAnimEvaluation_WorkerThread (ADVISORY, SUMMED cycles): avg %.3f ms / peak %.3f ms (ceiling %.1f)"),
					SpawnedN, Frames,
					Gt, Gt < Cfg.GtMsCeiling ? TEXT("under") : TEXT("OVER"), Cfg.GtMsCeiling, GtPk,
					Worker, WorkerPk, Cfg.MsCeiling));
				// HARD gate = the AVERAGE serial game-thread cost over the settled window vs gt_ms_ceiling
				// (a methodology decision). WHY the AVG and not the PEAK: the 4.0 ms
				// ceiling is itself a SUSTAINED allocation (24% of a 60 fps 16.67 ms frame), the average governs
				// sustained throughput, and a lone peak frame (~4.2 ms driven) is still only 25% of the frame with
				// ~12 ms of frame margin left, so it does not by itself drop a frame; a 14-frame-window max is also
				// too noisy (one GC/PSO hitch) to be a reliable gate. The PEAK is therefore REPORTED as a disclosed
				// risk signal, not failed on. The gate still catches a runaway crowd: the driven AVG crosses 4.0 at
				// N~=52. See army_config budget._ceiling_policy. (This path runs only on an interactive settled
				// frame — headless CI degrades below — and this locomotion drive lacks the conductor's montage
				// volley, so the authoritative full-driven peak is the n_vs_eval_ms.csv sweep, not this reading.)
				if (!(Gt < Cfg.GtMsCeiling))
				{
					Test->AddError(FString::Printf(
						TEXT("[Army.Budget] OVER game-thread ceiling (AVERAGE): AnimGameThreadTime avg %.3f ms >= %.1f ms at N=%d"),
						Gt, Cfg.GtMsCeiling, SpawnedN));
				}
				if (GtPk >= Cfg.GtMsCeiling)
				{
					Test->AddInfo(FString::Printf(
						TEXT("[Army.Budget] DISCLOSED RISK (not a failure): game-thread PEAK %.3f ms >= %.1f ms at N=%d — the hard gate is on the AVERAGE (%.3f ms, %s) by decision; peaks are expected on the montage volley. See budget._ceiling_policy."),
						GtPk, Cfg.GtMsCeiling, SpawnedN, Gt, Gt < Cfg.GtMsCeiling ? TEXT("under") : TEXT("OVER")));
				}
				if (!(Worker < Cfg.MsCeiling))
				{
					Test->AddInfo(FString::Printf(
						TEXT("[Army.Budget] NOTE summed-worker avg %.3f ms over the ADVISORY %.1f ms ceiling at N=%d (parallel cores; ~0.8 ms wall-clock). Advisory, not hard-failed; driven worker-avg-safe N~=%d. See budget._ceiling_policy."),
						Worker, Cfg.MsCeiling, SpawnedN, Cfg.ShippedSafeN));
				}
				Teardown(World);
				return true;
			}
			if ((Now - PhaseStart) > 25.0)
			{
				// Headless automation cannot produce a settled stat frame — PROVEN: collection is enabled and
				// metadata flows, but the stat-frame pipeline needs the viewport-driven frame advance a 0x0
				// -unattended editor lacks, so GetLatestValidFrame stays -1. This is NOT vacuous: the crowd DID
				// spawn at the shipped tier config (asserted in EPhase::Spawn), and the authoritative live cost is
				// the DRIVEN interactive-editor sweep committed to n_vs_eval_ms.csv + army_config budget._measured.
				// Do NOT restate the ms figures in this message — army_config is the single source of truth (a
				// hardcoded number here went stale once: it kept citing the standing values after the driven
				// re-measure). Degrade to a pass rather than red the pie tier over an environment limitation or
				// fake a stat number CI cannot take.
				Test->AddWarning(FString::Printf(
					TEXT("[Army.Budget] CHECK DID NOT RUN - N=%d spawned at the shipped tier config; headless automation produced no settled STATGROUP_Anim frame (frames_sampled=%d) — the stat-frame pipeline needs a rendered viewport this run lacks. The live-measured ms assertion did NOT run here (the crowd-spawn + memory assertions did). The authoritative DRIVEN anim-eval cost lives in research/fixtures/army-contact/n_vs_eval_ms.csv (condition=driven) and army_config budget._measured / budget._ceiling_policy — read those, not a figure restated here (kept out on purpose so this line can't go stale)."),
					SpawnedN, Frames));
				Teardown(World);
				return true;
			}
			return false;
#endif
		}

	private:
		enum class EPhase : uint8 { Spawn, Settle, Prime };
#if STATS
		// Close the stats window and clear the display so neither bleeds into later tests in the run.
		void Teardown(UWorld* World)
		{
			StatWindow.End();
			if (GEngine && World) { GEngine->Exec(World, TEXT("stat none")); }
		}
#endif
		FAutomationTestBase* Test;
		double StartTime = 0.0;
		double PhaseStart = 0.0;
		EPhase Phase = EPhase::Spawn;
		int32 SpawnedN = 0;
		MotionTerminal::Stats::FStatWindow StatWindow;
		bool bMemDone = false;
		FArmyCfg Cfg;
		TArray<TWeakObjectPtr<APawn>> Crowd;
	};

	// True if any flattened node's DebugLine contains any of the candidate substrings.
	bool FlatHasNode(const TArray<FNodeDebugData::FFlattenedDebugData>& Flat, std::initializer_list<const TCHAR*> Subs)
	{
		for (const FNodeDebugData::FFlattenedDebugData& It : Flat)
		{
			for (const TCHAR* S : Subs) { if (It.DebugLine.Contains(S)) { return true; } }
		}
		return false;
	}

	// Evenly-spaced K sample indices over N (no silent cap — caller logs K and N).
	void SampleIndices(int32 N, int32 K, TArray<int32>& Out)
	{
		Out.Reset();
		const int32 Step = FMath::Max(1, N / FMath::Max(K, 1));
		for (int32 i = 0; i < N && Out.Num() < K; i += Step) { Out.Add(i); }
	}

	// The statistical spot-check. Drive the crowd, then run the per-node weight
	// assertions on K evenly-spaced instances (deterministic sampling — reproducible, not random): locomotion machine weight ~1 at speed, the aim-offset node
	// present, the DefaultSlot weight sane. Logs K and N (the sampled fraction).
	class FArmySpotCheckCommand : public IAutomationLatentCommand
	{
	public:
		explicit FArmySpotCheckCommand(FAutomationTestBase* InTest) : Test(InTest) {}

		virtual bool Update() override
		{
			if (StartTime == 0.0) { StartTime = FPlatformTime::Seconds(); }
			const double Now = FPlatformTime::Seconds();
			UWorld* World = nullptr; bool bWorldResult = false;
			if (!TryResolveArmyPieWorld(Test, StartTime, Now, TEXT("Army.SpotCheck"), World, bWorldResult)) { return bWorldResult; }
			if (Phase == EPhase::Spawn)
			{
				TArray<APawn*> Pawns; int32 Made = 0;
				if (!SpawnShippedArmyOrFail(Test, World, TEXT("Army.SpotCheck"), Cfg, Pawns, Made)) { return true; }
				for (APawn* P : Pawns) { Spawned.Add(P); }
				N = Made;
				K = FMath::Clamp(Cfg.SpotCheckK, 1, N);
				Phase = EPhase::Settle; PhaseStart = Now;
				return false;
			}
			if (Phase == EPhase::Settle)
			{
				if (Now - PhaseStart > 3.5) { Phase = EPhase::Assert; }   // let the driven crowd reach Locomotion
				return false;
			}
			// EPhase::Assert
			TArray<int32> Idx; SampleIndices(N, K, Idx);
			int32 Sampled = 0, Passed = 0;
			bool bDumped = false;
			for (int32 i : Idx)
			{
				APawn* P = Spawned.IsValidIndex(i) ? Spawned[i].Get() : nullptr;
				USkeletalMeshComponent* Mesh = P ? P->FindComponentByClass<USkeletalMeshComponent>() : nullptr;
				UAnimInstance* Inst = Mesh ? Mesh->GetAnimInstance() : nullptr;
				if (!Inst) { Test->AddError(FString::Printf(TEXT("[Army.SpotCheck] sample #%d: no anim instance"), i)); ++Sampled; continue; }
				++Sampled;
				bool bOk = true;

				// (1) locomotion machine weight ~1 at speed.
				int32 LocoIdx = INDEX_NONE;
				const FBakedAnimationStateMachine* Loco = ResolveLocomotionBaked(*Inst, LocoIdx);
				if (!Loco) { Test->AddError(FString::Printf(TEXT("[Army.SpotCheck] sample #%d: no Locomotion machine"), i)); bOk = false; }
				else
				{
					const int32 LocoState = FindBakedStateIndex(*Loco, TEXT("Locomotion"));
					const float W = (LocoState != INDEX_NONE) ? Inst->GetInstanceStateWeight(LocoIdx, LocoState) : -1.f;
					if (!(W > 0.9f)) { Test->AddError(FString::Printf(TEXT("[Army.SpotCheck] sample #%d: Locomotion state weight %.3f not > 0.9 at speed"), i, W)); bOk = false; }
				}

				// (2) aim-offset node present, (3) DefaultSlot present + weight sane — from the live flattened tree.
				const TArray<FNodeDebugData::FFlattenedDebugData> Flat = MotionTerminal::GatherFlatNodeWeights(*Inst);
				if (!bDumped) { bDumped = true; Test->AddInfo(FString::Printf(TEXT("[Army.SpotCheck] sample #%d live node tree:\n%s"), i, *MotionTerminal::FormatFlatNodeWeights(Flat))); }
				if (!FlatHasNode(Flat, { TEXT("AimOffset"), TEXT("AO_Rifle"), TEXT("RotationOffset"), TEXT("Aim Offset") }))
				{ Test->AddError(FString::Printf(TEXT("[Army.SpotCheck] sample #%d: aim-offset node not present in the evaluated graph"), i)); bOk = false; }
				if (!FlatHasNode(Flat, { TEXT("DefaultSlot") }))
				{ Test->AddError(FString::Printf(TEXT("[Army.SpotCheck] sample #%d: DefaultSlot node not present"), i)); bOk = false; }
				const float SlotW = Inst->GetSlotMontageLocalWeight(FName(TEXT("DefaultSlot")));
				if (!(SlotW >= 0.f && SlotW <= 1.0001f))
				{ Test->AddError(FString::Printf(TEXT("[Army.SpotCheck] sample #%d: DefaultSlot local weight %.3f out of [0,1]"), i, SlotW)); bOk = false; }

				if (bOk) { ++Passed; }
			}
			Test->AddInfo(FString::Printf(
				TEXT("[Army.SpotCheck] sampled K=%d of N=%d instances (%.0f%%); %d/%d passed the per-node weight assertions (locomotion~1 at speed, aim node present, slot weight sane)."),
				Sampled, N, N > 0 ? 100.0 * Sampled / N : 0.0, Passed, Sampled));
			if (Sampled == 0) { Test->AddError(TEXT("[Army.SpotCheck] sampled zero instances — the crowd did not resolve")); }
			return true;
		}

	private:
		enum class EPhase : uint8 { Spawn, Settle, Assert };
		FAutomationTestBase* Test;
		double StartTime = 0.0;
		double PhaseStart = 0.0;
		EPhase Phase = EPhase::Spawn;
		int32 N = 0;
		int32 K = 0;
		FArmyCfg Cfg;
		TArray<TWeakObjectPtr<APawn>> Spawned;
	};

	// No-frozen-instance (the Verify.ClipVaries doctrine, extended to the crowd). Across K driven instances a moving
	// bone's world Z must VARY over a sampled window (a frozen/never-ticked instance reads ~0). Confound-
	// immune: one deliberately-unticked CONTROL (its skeletal mesh component tick disabled) is sampled the same
	// way and MUST read frozen — if it does not, the varies-check cannot tell ticking from frozen and the test
	// fails. So the test proves it CAN fire before trusting that the crowd passed.
	class FArmyNoFrozenCommand : public IAutomationLatentCommand
	{
	public:
		explicit FArmyNoFrozenCommand(FAutomationTestBase* InTest) : Test(InTest) {}

		virtual bool Update() override
		{
			if (StartTime == 0.0) { StartTime = FPlatformTime::Seconds(); }
			const double Now = FPlatformTime::Seconds();
			UWorld* World = nullptr; bool bWorldResult = false;
			if (!TryResolveArmyPieWorld(Test, StartTime, Now, TEXT("Army.NoFrozen"), World, bWorldResult)) { return bWorldResult; }
			if (Phase == EPhase::Spawn)
			{
				TArray<APawn*> Pawns; int32 Made = 0;
				if (!SpawnShippedArmyOrFail(Test, World, TEXT("Army.NoFrozen"), Cfg, Pawns, Made)) { return true; }
				N = Made;
				K = FMath::Clamp(Cfg.SpotCheckK, 1, N);
				SampleIndices(N, K, SampleIdx);
				for (int32 s : SampleIdx) { if (Pawns.IsValidIndex(s)) { Sampled.Add(Pawns[s]); } }

				// The deliberately-unticked control: driven like the crowd (so its ONLY difference is the tick),
				// but its skeletal mesh component tick is disabled, so its pose never updates -> must read frozen.
				UClass* PawnClass = LoadClass<APawn>(nullptr, *Cfg.RosterPath);
				if (PawnClass)
				{
					FActorSpawnParameters Params; Params.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
					const FVector Loc(0.0, 0.0, Cfg.SpawnZ + 500.0);
					APawn* Ctl = World->SpawnActor<APawn>(PawnClass, Loc, FRotator::ZeroRotator, Params);
					if (Ctl)
					{
						if (USkeletalMeshComponent* M = Ctl->FindComponentByClass<USkeletalMeshComponent>())
						{
							M->VisibilityBasedAnimTickOption = EVisibilityBasedAnimTickOption::AlwaysTickPoseAndRefreshBones;
							if (UCharacterMovementComponent* CMC = Ctl->FindComponentByClass<UCharacterMovementComponent>())
							{
								CMC->SetMovementMode(MOVE_None); CMC->SetComponentTickEnabled(false);
								const FVector F = Ctl->GetActorForwardVector();
								CMC->Velocity = FVector(F.X * 400.0, F.Y * 400.0, 0.0);
							}
							M->SetComponentTickEnabled(false);   // THE control variable: pose never re-evaluates
						}
						Control = Ctl;
					}
				}
				if (!Control.IsValid()) { Test->AddError(TEXT("[Army.NoFrozen] could not spawn the unticked control instance")); return true; }
				Phase = EPhase::Settle; PhaseStart = Now;
				return false;
			}
			if (Phase == EPhase::Settle)
			{
				if (Now - PhaseStart > 2.0)   // let motion develop before the window opens
				{
					MinZ.Init(BIG, Sampled.Num()); MaxZ.Init(-BIG, Sampled.Num());
					CtlMinZ = BIG; CtlMaxZ = -BIG;
					// Resolve mesh components ONCE here rather than re-resolving via FindComponentByClass on
					// every Accumulate tick — the component identity is stable for the life of this window.
					SampledMesh.SetNum(Sampled.Num());
					for (int32 j = 0; j < Sampled.Num(); ++j)
					{
						if (const APawn* P = Sampled[j].Get()) { SampledMesh[j] = P->FindComponentByClass<USkeletalMeshComponent>(); }
					}
					if (const APawn* C = Control.Get()) { ControlMesh = C->FindComponentByClass<USkeletalMeshComponent>(); }
					Phase = EPhase::Accumulate; PhaseStart = Now;
				}
				return false;
			}
			if (Phase == EPhase::Accumulate)
			{
				for (int32 j = 0; j < SampledMesh.Num(); ++j)
				{
					if (const USkeletalMeshComponent* M = SampledMesh[j].Get())
					{
						const double Z = M->GetSocketLocation(FName(TEXT("foot_r"))).Z;
						MinZ[j] = FMath::Min(MinZ[j], Z); MaxZ[j] = FMath::Max(MaxZ[j], Z);
					}
				}
				if (const USkeletalMeshComponent* M = ControlMesh.Get())
				{
					const double Z = M->GetSocketLocation(FName(TEXT("foot_r"))).Z;
					CtlMinZ = FMath::Min(CtlMinZ, Z); CtlMaxZ = FMath::Max(CtlMaxZ, Z);
				}
				if (Now - PhaseStart > 1.5) { Phase = EPhase::Assert; }
				return false;
			}
			// EPhase::Assert
			int32 Moving = 0, Checked = 0;
			for (int32 j = 0; j < Sampled.Num(); ++j)
			{
				if (!Sampled[j].IsValid()) { continue; }
				++Checked;
				const double Range = MaxZ[j] - MinZ[j];
				if (Range > kMoveMin) { ++Moving; }
				else { Test->AddError(FString::Printf(TEXT("[Army.NoFrozen] sampled instance #%d FROZEN: foot_r Z range %.3f cm not > %.1f cm"), SampleIdx.IsValidIndex(j) ? SampleIdx[j] : j, Range, kMoveMin)); }
			}
			const double CtlRange = CtlMaxZ - CtlMinZ;
			// Confound gate: the control MUST read frozen, else the varies-check cannot distinguish tick from frozen.
			const bool bControlFrozen = (CtlRange < kFrozenMax);
			Test->AddInfo(FString::Printf(
				TEXT("[Army.NoFrozen] %d/%d sampled instances move (foot_r Z range > %.1f cm); unticked CONTROL range %.3f cm (frozen gate < %.1f cm => %s)."),
				Moving, Checked, kMoveMin, CtlRange, kFrozenMax, bControlFrozen ? TEXT("frozen, as required") : TEXT("NOT frozen — DISCRIMINATION BROKEN")));
			if (!bControlFrozen)
			{
				Test->AddError(FString::Printf(
					TEXT("[Army.NoFrozen] CONFOUND FAILURE: the deliberately-unticked control read foot_r Z range %.3f cm >= %.1f cm — the no-frozen check cannot distinguish a ticking instance from a frozen one, so a genuinely frozen crowd could pass. The varies-check is not trustworthy."),
					CtlRange, kFrozenMax));
			}
			if (Checked == 0) { Test->AddError(TEXT("[Army.NoFrozen] sampled zero instances — the crowd did not resolve")); }
			return true;
		}

	private:
		enum class EPhase : uint8 { Spawn, Settle, Accumulate, Assert };
		static constexpr double BIG = 1.0e12;
		static constexpr double kMoveMin = 0.5;    // cm: a driven, in-place instance's foot cycles far more than this
		static constexpr double kFrozenMax = 0.1;  // cm: an unticked pose does not move at all
		FAutomationTestBase* Test;
		double StartTime = 0.0;
		double PhaseStart = 0.0;
		EPhase Phase = EPhase::Spawn;
		int32 N = 0;
		int32 K = 0;
		FArmyCfg Cfg;
		TArray<int32> SampleIdx;
		TArray<TWeakObjectPtr<APawn>> Sampled;
		TWeakObjectPtr<APawn> Control;
		TArray<TWeakObjectPtr<USkeletalMeshComponent>> SampledMesh;
		TWeakObjectPtr<USkeletalMeshComponent> ControlMesh;
		TArray<double> MinZ, MaxZ;
		double CtlMinZ = BIG, CtlMaxZ = -BIG;
	};

	// ================================================================================================
	// Pie.Army.BudgetThrottle — does the Animation Budget Allocator actually throttle the far ring, and is
	// it the BUDGET that throttles it?
	//
	// It originally could not, for two independent reasons. (1) The allocator drives only
	// USkeletalMeshComponentBudgeted, and the roster pawns' Mesh was a plain USkeletalMeshComponent (0/32
	// budgeted, army_config.json far `_budgeted_applied`); AMTCharacter in the game module fixes that.
	// (2) It runs only when the GLOBAL switch a.Budget.Enabled is 1 AND its world is enabled
	// (AnimationBudgetAllocator.cpp Update); army_spawn.register_budget set only the world flag. Registration
	// alone also throttles nothing: a component registers at significance 1 with bNeverThrottle = true.
	//
	// A CONFOUND THE FIRST VERSION OF THIS GATE HAD. With bTickEvenIfNotRendered the
	// allocator ticks at most MaxTickedOffsreenComponents (default 4) offscreen meshes per frame and disables
	// the rest (AnimationBudgetAllocator.cpp, the NonRendered pass) — and this tier's viewport is 0x0, so the
	// whole far ring can count as offscreen. A low rate therefore did not show that the BUDGET was doing it.
	// Every enabled arm now lifts that cap to the ring size, and C and D differ ONLY in the budget.
	//
	// The observable is the graph UPDATE rate — per mesh, the fraction of frames on which its anim-proxy
	// UpdateCounter changed — not component ticks, which continue on interpolated frames. Four arms on one
	// crowd, each one switch from the last:
	//   A  control         far ring unregistered, URO off             -> far ~1 (the instrument reads ~1)
	//   B  the old route   registered + world enabled, CVar 0         -> far ~1 (defect 2 kept visible)
	//   C  live, ample     B + a.Budget.Enabled 1, budget >> cost     -> far ~1, allocator in control
	//   D  live, tight     C with a budget << cost                    -> far throttled, every mesh still updating
	// The hero ring is never registered and must read ~1 in every arm. C proves the allocator, once live, is not
	// what lowers the rate by itself (no offscreen cap, no heuristic); D minus C is the budget.
	// Registration mirrors UMTAnimBudgetLibrary::RegisterBudgetedMesh (what the Python crowd calls); this plugin
	// does not link the game module, so it makes the same two engine calls directly.
	// ================================================================================================
	class FArmyBudgetThrottleCommand : public IAutomationLatentCommand
	{
	public:
		explicit FArmyBudgetThrottleCommand(FAutomationTestBase* InTest) : Test(InTest) {}
		virtual ~FArmyBudgetThrottleCommand() override { Teardown(); }

		virtual bool Update() override
		{
			if (StartTime == 0.0) { StartTime = FPlatformTime::Seconds(); }
			const double Now = FPlatformTime::Seconds();
			UWorld* World = nullptr; bool bWorldResult = false;
			if (!TryResolveArmyPieWorld(Test, StartTime, Now, TEXT("Army.BudgetThrottle"), World, bWorldResult)) { return bWorldResult; }
			if (Arm < 0)
			{
				return !Spawn(World);   // Spawn reports its own errors; false from it ends the test
			}
			if (++FrameInArm > kWarmupFrames)
			{
				++Sampled[Arm];
				Count(HeroMeshes, HeroLast, HeroChanged[Arm]);
				Count(FarMeshes, FarLast, FarChanged[Arm]);
			}
			else
			{
				Prime(HeroMeshes, HeroLast, HeroChanged[Arm]);
				Prime(FarMeshes, FarLast, FarChanged[Arm]);
			}
			if (FrameInArm < kWarmupFrames + kWindowFrames) { return false; }
			ExternalControlled[Arm] = CountExternallyControlled();
			if (Arm == kArms - 1) { Assert(); Teardown(); return true; }
			++Arm; FrameInArm = 0;
			if (!EnterArm(World)) { Teardown(); return true; }
			return false;
		}

	private:
		static constexpr int32 kArms = 4;

		bool Spawn(UWorld* World)
		{
			TArray<APawn*> Pawns; int32 Made = 0;
			if (!SpawnShippedArmyOrFail(Test, World, TEXT("Army.BudgetThrottle"), Cfg, Pawns, Made)) { return false; }
			WorldWeak = World;
			int32 Offset = 0, NoAnim = 0;
			for (const FArmyTierCfg& T : Cfg.Tiers)
			{
				for (int32 k = 0; k < T.Count; ++k)
				{
					APawn* P = Pawns.IsValidIndex(Offset + k) ? Pawns[Offset + k] : nullptr;
					USkeletalMeshComponent* M = P ? P->FindComponentByClass<USkeletalMeshComponent>() : nullptr;
					const bool bTracked = T.Name == TEXT("hero") || T.bBudgeted;
					if (bTracked && !(M && M->GetAnimInstance())) { ++NoAnim; continue; }
					if (T.Name == TEXT("hero")) { HeroMeshes.Add(M); }
					if (T.bBudgeted)
					{
						FarMeshes.Add(M);
						FarTier = T;
						M->bEnableUpdateRateOptimizations = false;   // arm A: neither URO nor the allocator
					}
				}
				Offset += T.Count;
			}
			// A mesh with no anim instance would be dropped from the rates, so a ring that lost meshes could
			// still read healthy. Every tracked mesh must be measurable, or the gate does not run.
			if (NoAnim > 0)
			{
				Test->AddError(FString::Printf(TEXT("[Army.BudgetThrottle] %d hero/budgeted mesh(es) have no anim instance — their rates cannot be measured"), NoAnim));
				return false;
			}
			if (FarMeshes.Num() == 0)
			{
				Test->AddError(TEXT("[Army.BudgetThrottle] no ring in army_config.json declares a 'budget' object — nothing is budgeted, so there is nothing to throttle"));
				return false;
			}
			int32 Plain = 0;
			for (const TWeakObjectPtr<USkeletalMeshComponent>& M : FarMeshes) { if (!Cast<USkeletalMeshComponentBudgeted>(M.Get())) { ++Plain; } }
			if (Plain > 0)
			{
				Test->AddError(FString::Printf(
					TEXT("[Army.BudgetThrottle] %d/%d '%s' meshes are a plain USkeletalMeshComponent, which the allocator cannot drive. The roster pawn '%s' must derive from AMTCharacter (game module)."),
					Plain, FarMeshes.Num(), *FarTier.Name, *Cfg.RosterPath));
				return false;
			}
			if (!Allocator())
			{
				Test->AddError(TEXT("[Army.BudgetThrottle] the PIE world has no Animation Budget Allocator (is the AnimationBudgetAllocator plugin enabled?)"));
				return false;
			}
			BudgetCVar = IConsoleManager::Get().FindConsoleVariable(TEXT("a.Budget.Enabled"));
			if (!BudgetCVar)
			{
				Test->AddError(TEXT("[Army.BudgetThrottle] console variable a.Budget.Enabled not found"));
				return false;
			}
			// Write at the CVar's current priority or SetByCode, whichever is higher, so no write of ours is
			// silently rejected by a higher-priority console setting; every write is then verified to have taken.
			PrevCVar = BudgetCVar->GetInt();
			const int32 PrevPriority = (int32)(BudgetCVar->GetFlags() & ECVF_SetByMask);
			WritePriority = (EConsoleVariableFlags)FMath::Max(PrevPriority, (int32)ECVF_SetByCode);
			bTouched = true;
			Arm = 0; FrameInArm = 0;
			return SetSwitch(0);   // arms A and B run with the global switch OFF, whatever it was
		}

		bool EnterArm(UWorld* World)
		{
			IAnimationBudgetAllocator* Alloc = Allocator();
			if (!Alloc) { Test->AddError(TEXT("[Army.BudgetThrottle] the allocator went away mid-test (PIE world torn down)")); return false; }
			if (Arm == 1)
			{
				ApplyBudget(kAmpleBudgetMs);
				Alloc->SetEnabled(true);
				for (const TWeakObjectPtr<USkeletalMeshComponent>& WM : FarMeshes)
				{
					if (USkeletalMeshComponentBudgeted* M = Cast<USkeletalMeshComponentBudgeted>(WM.Get()))
					{
						Alloc->RegisterComponent(M);
						M->SetComponentSignificance(FarTier.BudgetSignificance, /*bNeverSkip=*/false,
							FarTier.bBudgetTickEvenIfNotRendered, /*bAllowReducedWork=*/true, /*bForceInterpolate=*/false);
					}
				}
				bRegistered = true;
				return true;
			}
			if (Arm == 2) { if (!SetSwitch(1)) { return false; } ApplyBudget(kAmpleBudgetMs); return true; }
			ApplyBudget(kTightBudgetMs);   // arm D: the budget is the only change from C
			return true;
		}

		void ApplyBudget(float BudgetMs)
		{
			// Any a.Budget.* parameter CVar change replaces the whole per-world struct with CVar values
			// (GOnCVarParametersChanged -> SetParametersFromCVars), so this is re-applied at every arm entry.
			if (IAnimationBudgetAllocator* Alloc = Allocator())
			{
				FAnimationBudgetAllocatorParameters Params;
				Params.BudgetInMs = BudgetMs;
				Params.MaxTickedOffsreenComponents = FMath::Max(FarMeshes.Num(), 1);   // the confound, removed
				Alloc->SetParameters(Params);
			}
		}

		bool SetSwitch(int32 Value)
		{
			BudgetCVar->Set(Value, WritePriority);
			if (BudgetCVar->GetInt() != Value)
			{
				Test->AddError(FString::Printf(TEXT("[Army.BudgetThrottle] a.Budget.Enabled did not take the value %d (reads %d): a higher-priority setting holds it"), Value, BudgetCVar->GetInt()));
				return false;
			}
			return true;
		}

		int32 CountExternallyControlled() const
		{
			int32 N = 0;
			for (const TWeakObjectPtr<USkeletalMeshComponent>& WM : FarMeshes)
			{
				if (const USkeletalMeshComponent* M = WM.Get()) { if (M->IsUsingExternalTickRateControl()) { ++N; } }
			}
			return N;
		}

		void Assert()
		{
			static const TCHAR* Names[kArms] = { TEXT("A control"), TEXT("B world-enabled, CVar 0"), TEXT("C live, ample budget"), TEXT("D live, tight budget") };
			const float BudgetOf[kArms] = { 0.0f, kAmpleBudgetMs, kAmpleBudgetMs, kTightBudgetMs };
			double Hero[kArms], Far[kArms];
			for (int32 a = 0; a < kArms; ++a)
			{
				Hero[a] = Rate(HeroChanged[a], Sampled[a]);
				Far[a] = Rate(FarChanged[a], Sampled[a]);
				Test->AddInfo(FString::Printf(TEXT("[Army.BudgetThrottle] arm %s (budget %.2f ms): graph-update rate hero %.3f, %s %.3f (min mesh %.3f); %d/%d %s meshes under allocator tick control (%d frames)"),
					Names[a], BudgetOf[a], Hero[a], *FarTier.Name, Far[a], MinRate(FarChanged[a], Sampled[a]),
					ExternalControlled[a], FarMeshes.Num(), *FarTier.Name, Sampled[a]));
			}
			auto Expect = [this](bool bOk, const FString& What) { if (!bOk) { Test->AddError(TEXT("[Army.BudgetThrottle] ") + What); } };
			for (int32 a = 0; a < kArms; ++a)
			{
				Expect(Hero[a] >= kUnthrottledMin, FString::Printf(TEXT("arm %s: the unregistered hero ring updated on %.3f of frames, not >= %.2f — %s"),
					Names[a], Hero[a], kUnthrottledMin, a == 0 ? TEXT("the instrument does not read an unthrottled mesh as ~1") : TEXT("budgeting the far ring leaked into a ring it must not touch")));
			}
			Expect(Far[0] >= kUnthrottledMin, FString::Printf(TEXT("arm A: the far ring updated on %.3f of frames with neither URO nor the allocator, not >= %.2f — the control is not a control"), Far[0], kUnthrottledMin));
			Expect(ExternalControlled[0] == 0 && ExternalControlled[1] == 0, FString::Printf(TEXT("arms A/B: %d / %d far meshes were already under allocator tick control with the global switch off"), ExternalControlled[0], ExternalControlled[1]));
			Expect(Far[1] >= kUnthrottledMin, FString::Printf(TEXT("arm B: with a.Budget.Enabled 0 the far ring updated on %.3f of frames, not >= %.2f — the global switch is no longer load-bearing"), Far[1], kUnthrottledMin));
			for (int32 a = 2; a < kArms; ++a)
			{
				Expect(ExternalControlled[a] == FarMeshes.Num(), FString::Printf(TEXT("arm %s: only %d/%d far meshes are under allocator tick control — the allocator did not take the ring"), Names[a], ExternalControlled[a], FarMeshes.Num()));
			}
			Expect(Far[2] >= kUnthrottledMin, FString::Printf(TEXT("arm C: with an ample %.2f ms budget the far ring updated on %.3f of frames, not >= %.2f — something other than the budget lowers the rate, so arm D cannot attribute its throttling to the budget"), kAmpleBudgetMs, Far[2], kUnthrottledMin));
			Expect(Far[3] <= kThrottledMax, FString::Printf(TEXT("arm D: under a %.2f ms budget the far ring updated on %.3f of frames, not <= %.2f — the budget does not throttle it"), kTightBudgetMs, Far[3], kThrottledMax));
			const double MinD = MinRate(FarChanged[3], Sampled[3]);
			Expect(MinD > 0.0, TEXT("arm D: at least one far mesh never updated in the window — a dead mesh, not a throttled one"));
		}

		void Teardown()
		{
			// The allocator is owned by the PIE world and deleted at world cleanup (AnimationBudgetAllocatorModule.cpp),
			// so it is re-resolved from a weak world here, never cached: a command destroyed after PIE ended must not
			// touch it.
			if (bRegistered)
			{
				if (IAnimationBudgetAllocator* Alloc = Allocator())
				{
					for (const TWeakObjectPtr<USkeletalMeshComponent>& WM : FarMeshes)
					{
						if (USkeletalMeshComponentBudgeted* M = Cast<USkeletalMeshComponentBudgeted>(WM.Get())) { Alloc->UnregisterComponent(M); }
					}
					Alloc->SetEnabled(false);
				}
			}
			bRegistered = false;
			if (bTouched && BudgetCVar) { BudgetCVar->Set(PrevCVar, WritePriority); }
			bTouched = false;
		}

		IAnimationBudgetAllocator* Allocator() const
		{
			UWorld* World = WorldWeak.Get();
			return World ? IAnimationBudgetAllocator::Get(World) : nullptr;
		}

		// Per mesh: Changed[i] counts frames on which mesh i's update counter moved. At most one graph update
		// happens per mesh per frame, so this is its update count; a counter reset reads as one change.
		static void Prime(const TArray<TWeakObjectPtr<USkeletalMeshComponent>>& Meshes, TArray<int32>& Last, TArray<int32>& Changed)
		{
			Last.SetNum(Meshes.Num());
			Changed.SetNumZeroed(Meshes.Num());
			for (int32 i = 0; i < Meshes.Num(); ++i) { Last[i] = ReadCounter(Meshes[i].Get()); }
		}

		static void Count(const TArray<TWeakObjectPtr<USkeletalMeshComponent>>& Meshes, TArray<int32>& Last, TArray<int32>& Changed)
		{
			for (int32 i = 0; i < Meshes.Num(); ++i)
			{
				const int32 Now = ReadCounter(Meshes[i].Get());
				if (Now != INDEX_NONE && Now != Last[i]) { ++Changed[i]; }   // a vanished instance never counts as an update
				Last[i] = Now;
			}
		}

		static int32 ReadCounter(const USkeletalMeshComponent* M)
		{
			const UAnimInstance* Anim = M ? M->GetAnimInstance() : nullptr;
			return Anim ? static_cast<int32>(Anim->GetUpdateCounter().Get()) : INDEX_NONE;
		}

		static double Rate(const TArray<int32>& Changed, int32 Frames)
		{
			int64 Sum = 0; for (int32 C : Changed) { Sum += C; }
			return (Changed.Num() > 0 && Frames > 0) ? static_cast<double>(Sum) / (static_cast<double>(Changed.Num()) * Frames) : 0.0;
		}

		static double MinRate(const TArray<int32>& Changed, int32 Frames)
		{
			if (Changed.Num() == 0 || Frames <= 0) { return 0.0; }
			int32 Lo = MAX_int32; for (int32 C : Changed) { Lo = FMath::Min(Lo, C); }
			return static_cast<double>(Lo) / Frames;
		}

		static constexpr int32 kWarmupFrames = 30;     // the allocator smooths its work estimate over frames
		static constexpr int32 kWindowFrames = 90;
		static constexpr double kUnthrottledMin = 0.9;
		static constexpr double kThrottledMax = 0.5;
		// Far above and far below the far ring's modelled game-thread cost (army_config far gt_ms_per_inst 0.032
		// x 32 = 1.024 ms). The shipped budget is a separate, measured claim.
		static constexpr float kAmpleBudgetMs = 100.0f;
		static constexpr float kTightBudgetMs = 0.1f;
		FAutomationTestBase* Test;
		double StartTime = 0.0;
		FArmyCfg Cfg;
		FArmyTierCfg FarTier;
		TWeakObjectPtr<UWorld> WorldWeak;
		TArray<TWeakObjectPtr<USkeletalMeshComponent>> HeroMeshes, FarMeshes;
		TArray<int32> HeroLast, FarLast;
		TArray<int32> HeroChanged[kArms], FarChanged[kArms];
		int32 Sampled[kArms] = { 0, 0, 0, 0 }, ExternalControlled[kArms] = { 0, 0, 0, 0 };
		int32 Arm = -1, FrameInArm = 0;
		IConsoleVariable* BudgetCVar = nullptr;
		int32 PrevCVar = 0;
		EConsoleVariableFlags WritePriority = ECVF_SetByCode;
		bool bTouched = false, bRegistered = false;
	};

	// ================================================================================================
	// Pie.Army.OnCapsule — is every crowd character standing where its capsule is?
	//
	// A measurement before publication found the crowd's Khaimera walking his mesh 941.2 cm off his capsule at speed 450. The original
	// retargeted clips carry their forward travel on the PELVIS (1362.86 cm over the jog loop), every ABP here
	// runs ROOT_MOTION_FROM_MONTAGES_ONLY, so nothing extracts that travel and it plays as raw animation. The
	// instance was fixed by army_config spawn.crowd_anim_class (an in-place graph: 10.1 cm). The CLASS of
	// defect was not gated: Army.NoFrozen asserts feet move (a dragged foot moves), Army.Budget asserts
	// milliseconds, Army.SpotCheck samples state weights. None of them asks where the mesh is.
	//
	// One pawn per rig the crowd actually uses (FArmyCfg::CrowdRigs — SpawnArmyIntoPIE spawns only the Manny
	// pawn, so it cannot be reused here), set up the way the crowd sets it up: the crowd_anim_class swap,
	// AlwaysTickPoseAndRefreshBones, and SpawnArmyIntoPIE's in-place drive (CMC MOVE_None, component tick off,
	// Velocity = forward * speed). The capsule therefore never moves, and any horizontal distance between the
	// pelvis and the actor's axis is drift the animation put there. The measure is the one those figures
	// come from (pie_roster4_drive.py): max |pelvis - actor location| in XY, pelvis read in world space.
	//
	// Fixed 60 Hz clock (FFixedTimeStepScope, held by value, so every exit path restores it), so the 1.0 s
	// settle and 4.0 s window are fixed frame counts however the automation PIE loop hitches. 4.0 s is ~2.3
	// loops of the 1.767 s jog.
	//
	// A small drift is only evidence if the rig was really locomoting, so before a rig's drift counts as
	// measured the row also requires: the pawn's velocity read back at the drive speed on every sampled frame
	// (an idle pose stands on its capsule by construction), the graph updated on most frames (the anim-proxy
	// update counter Pie.Army.BudgetThrottle reads), the pelvis actually moving in Z (updates and bone
	// evaluation are separate counters, and invalid bone transforms read identity), and no non-finite sample
	// (NaN fails every comparison, so it would never exceed the bound). The pelvis is read by bone index,
	// not GetSocketLocation, which would prefer a socket of the same name.
	// Not checked: which state the locomotion machine is in. The velocity is the input; the state is not.
	//
	// NOT YET RUN RED. The intended negative control is removing crowd_anim_class.khaimera from
	// army_config.json, which should put Khaimera back on the original graph (941.2 cm in that earlier measurement).
	// ================================================================================================
	class FArmyOnCapsuleCommand : public IAutomationLatentCommand
	{
	public:
		explicit FArmyOnCapsuleCommand(FAutomationTestBase* InTest) : Test(InTest) {}

		virtual bool Update() override
		{
			if (StartTime == 0.0)
			{
				StartTime = FPlatformTime::Seconds();
				ClockScope.Pin(kSimHz);   // restored by ClockScope's destructor, never here
			}
			const double Now = FPlatformTime::Seconds();
			UWorld* World = nullptr; bool bWorldResult = false;
			if (!TryResolveArmyPieWorld(Test, StartTime, Now, TEXT("Army.OnCapsule"), World, bWorldResult)) { return bWorldResult; }
			if (!bSpawned)
			{
				bSpawned = true;
				return !Spawn(World);   // Spawn reports its own errors; false from it ends the test
			}
			if (++Frame <= kSettleFrames) { return false; }
			Sample();
			if (Frame < kSettleFrames + kSampleFrames) { return false; }
			Assert();
			return true;
		}

	private:
		struct FRig
		{
			FString Name;
			FString AnimClassPath;   // empty: the rig keeps its pawn's own graph in the crowd
			TWeakObjectPtr<APawn> Pawn;
			TWeakObjectPtr<USkeletalMeshComponent> Mesh;
			int32 PelvisIndex = INDEX_NONE;
			bool bHasPelvis = false;
			double MaxDriftCm = 0.0;
			int32 Samples = 0;
			int32 Updates = 0;
			int32 LastCounter = INDEX_NONE;
			int32 NonFinite = 0;                       // samples with a NaN/Inf pelvis or axis: never "under the bound"
			double MinPelvisZ = 1.0e12, MaxPelvisZ = -1.0e12;   // pose liveness: an unevaluated pose holds still
			double MinSpeed = 1.0e12, MaxSpeed = -1.0e12;       // the velocity the graph reads, every sampled frame
		};

		bool Spawn(UWorld* World)
		{
			FString Err;
			if (!LoadArmyConfig(Cfg, Err)) { Test->AddError(TEXT("[Army.OnCapsule] ") + Err); return false; }
			if (Cfg.CrowdRigs.Num() == 0)
			{
				Test->AddError(TEXT("[Army.OnCapsule] army_config.json spawn.tier_pawns names no rig in any ring with count > 0 — there is no crowd to measure"));
				return false;
			}
			for (int32 i = 0; i < Cfg.CrowdRigs.Num(); ++i)
			{
				FRig R;
				R.Name = Cfg.CrowdRigs[i];
				const FString& PawnPath = Cfg.Roster.FindChecked(R.Name);   // LoadArmyConfig guarantees the key
				UClass* PawnClass = LoadClass<APawn>(nullptr, *PawnPath);
				if (!PawnClass) { Test->AddError(FString::Printf(TEXT("[Army.OnCapsule] rig '%s': roster pawn class '%s' does not load"), *R.Name, *PawnPath)); return false; }
				// On a line along Y, kSpacingCm apart and centred on the crowd's origin, all facing +X, so even a
				// drifting mesh (drift runs mostly along the rig's own forward axis) stays clear of its neighbours.
				const FVector Loc(0.0, (i - 0.5 * (Cfg.CrowdRigs.Num() - 1)) * kSpacingCm, Cfg.SpawnZ);
				FActorSpawnParameters Params;
				Params.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
				APawn* Pawn = World->SpawnActor<APawn>(PawnClass, Loc, FRotator::ZeroRotator, Params);
				USkeletalMeshComponent* Mesh = Pawn ? Pawn->FindComponentByClass<USkeletalMeshComponent>() : nullptr;
				if (!Mesh) { Test->AddError(FString::Printf(TEXT("[Army.OnCapsule] rig '%s': %s"), *R.Name, Pawn ? TEXT("pawn has no SkeletalMeshComponent") : TEXT("SpawnActor failed"))); return false; }
				// The crowd's graph swap (army_spawn.spawn_army's crowd_anim_class pre-check). A class that does not load fails
				// the row, as it refuses the crowd there: that instance would walk off its capsule.
				if (const FString* AnimPath = Cfg.CrowdAnimClass.Find(R.Name))
				{
					UClass* AnimClass = LoadClass<UAnimInstance>(nullptr, **AnimPath);
					if (!AnimClass) { Test->AddError(FString::Printf(TEXT("[Army.OnCapsule] rig '%s': crowd_anim_class '%s' does not load"), *R.Name, **AnimPath)); return false; }
					Mesh->SetAnimInstanceClass(AnimClass);
					if (!(Mesh->GetAnimInstance() && Mesh->GetAnimInstance()->GetClass() == AnimClass))
					{
						Test->AddError(FString::Printf(TEXT("[Army.OnCapsule] rig '%s': SetAnimInstanceClass('%s') did not take — the mesh is not running the crowd's graph"), *R.Name, **AnimPath));
						return false;
					}
					R.AnimClassPath = *AnimPath;
				}
				if (!Mesh->GetAnimInstance()) { Test->AddError(FString::Printf(TEXT("[Army.OnCapsule] rig '%s': mesh has no anim instance — nothing drives the pose"), *R.Name)); return false; }
				Mesh->VisibilityBasedAnimTickOption = EVisibilityBasedAnimTickOption::AlwaysTickPoseAndRefreshBones;
				// URO off so the graph evaluates every frame and the update-rate liveness check below is exact.
				Mesh->bEnableUpdateRateOptimizations = false;
				// SetForcedLOD is 1-BASED (0 = auto, 1 = LOD0; army_spawn._apply_tier): 1 pins LOD0, the full skeleton.
				Mesh->SetForcedLOD(1);
				// Without a CMC there is no velocity for the graph to read, so the rig would idle — and an idle
				// pose stands on its capsule by construction. That is a pass without measuring, so it fails.
				UCharacterMovementComponent* CMC = Pawn->FindComponentByClass<UCharacterMovementComponent>();
				if (!CMC) { Test->AddError(FString::Printf(TEXT("[Army.OnCapsule] rig '%s': pawn has no CharacterMovementComponent — it cannot be driven"), *R.Name)); return false; }
				CMC->SetMovementMode(MOVE_None);
				CMC->SetComponentTickEnabled(false);
				const FVector Fwd = Pawn->GetActorForwardVector();
				CMC->Velocity = FVector(Fwd.X * kSpeedCmS, Fwd.Y * kSpeedCmS, 0.0);
				R.PelvisIndex = Mesh->GetBoneIndex(kPelvis);
				R.bHasPelvis = R.PelvisIndex != INDEX_NONE;
				if (!R.bHasPelvis)
				{
					// Fails the row but keeps measuring the other rigs, so their numbers still print.
					Test->AddError(FString::Printf(TEXT("[Army.OnCapsule] rig '%s': mesh has no '%s' bone — its drift cannot be measured"), *R.Name, *kPelvis.ToString()));
				}
				R.Pawn = Pawn;
				R.Mesh = Mesh;
				Rigs.Add(R);
			}
			return true;
		}

		void Sample()
		{
			for (FRig& R : Rigs)
			{
				const APawn* P = R.Pawn.Get();
				const USkeletalMeshComponent* M = R.Mesh.Get();
				if (!R.bHasPelvis || !P || !M) { continue; }   // a vanished pawn shows in Assert as a short sample count
				// The BONE by index, world space. Not GetSocketLocation: a mesh socket named `pelvis` would win over
				// the bone there (SkinnedMeshComponent.cpp GetSocketTransform tries sockets first).
				const FVector Pelvis = M->GetBoneTransform(R.PelvisIndex).GetLocation();
				const FVector Axis = P->GetActorLocation();
				const double Dx = Pelvis.X - Axis.X, Dy = Pelvis.Y - Axis.Y;
				const double Drift = FMath::Sqrt(Dx * Dx + Dy * Dy);
				++R.Samples;
				const UAnimInstance* Anim = M->GetAnimInstance();
				const int32 Counter = Anim ? static_cast<int32>(Anim->GetUpdateCounter().Get()) : INDEX_NONE;
				if (Counter != INDEX_NONE && R.LastCounter != INDEX_NONE && Counter != R.LastCounter) { ++R.Updates; }
				R.LastCounter = Counter;
				const double Speed = P->GetVelocity().Size2D();
				R.MinSpeed = FMath::Min(R.MinSpeed, Speed); R.MaxSpeed = FMath::Max(R.MaxSpeed, Speed);
				// NaN fails every comparison, so it would sit "under the bound" forever: counted and failed instead.
				if (!FMath::IsFinite(Drift) || !FMath::IsFinite(Pelvis.Z)) { ++R.NonFinite; continue; }
				R.MaxDriftCm = FMath::Max(R.MaxDriftCm, Drift);
				R.MinPelvisZ = FMath::Min(R.MinPelvisZ, Pelvis.Z); R.MaxPelvisZ = FMath::Max(R.MaxPelvisZ, Pelvis.Z);
			}
		}

		void Assert()
		{
			for (const FRig& R : Rigs)
			{
				if (!R.bHasPelvis) { continue; }   // already failed at spawn
				const int32 Intervals = R.Samples - 1;
				const double UpdateRate = Intervals > 0 ? static_cast<double>(R.Updates) / Intervals : 0.0;
				const double PelvisZRange = R.MaxPelvisZ - R.MinPelvisZ;   // negative if no finite sample landed
				Test->AddInfo(FString::Printf(
					TEXT("[Army.OnCapsule] %s: max pelvis-to-capsule-axis drift %.1f cm (XY) over %d frames at %.0f Hz, driven speed %.1f-%.1f cm/s (want %.0f), bound %.1f cm; graph %s; updated on %.3f of frames; pelvis Z range %.2f cm; %d non-finite sample(s)"),
					*R.Name, R.MaxDriftCm, R.Samples, kSimHz, R.MinSpeed, R.MaxSpeed, kSpeedCmS, kOnCapsuleMaxDriftCm,
					R.AnimClassPath.IsEmpty() ? TEXT("the pawn's own") : *R.AnimClassPath, UpdateRate, PelvisZRange, R.NonFinite));
				// Each guard below is a way the drift number could be small WITHOUT the mesh being on its capsule
				// under load; any one of them failing means the bound was never really tested for this rig.
				if (R.Samples < kSampleFrames)
				{
					Test->AddError(FString::Printf(TEXT("[Army.OnCapsule] %s: sampled %d of %d frames — the pawn or its mesh went away mid-window"), *R.Name, R.Samples, kSampleFrames));
				}
				else if (R.NonFinite > 0)
				{
					Test->AddError(FString::Printf(TEXT("[Army.OnCapsule] %s: %d sample(s) read a non-finite pelvis or drift — the measurement is broken"), *R.Name, R.NonFinite));
				}
				else if (FMath::Abs(R.MinSpeed - kSpeedCmS) > kSpeedTolCmS || FMath::Abs(R.MaxSpeed - kSpeedCmS) > kSpeedTolCmS)
				{
					Test->AddError(FString::Printf(TEXT("[Army.OnCapsule] %s: the pawn's velocity read %.1f-%.1f cm/s, not %.0f — the graph was not driven at the measured speed, and an idle pose stands on its capsule by construction"),
						*R.Name, R.MinSpeed, R.MaxSpeed, kSpeedCmS));
				}
				else if (UpdateRate < kMinUpdateRate)
				{
					Test->AddError(FString::Printf(TEXT("[Army.OnCapsule] %s: the graph updated on %.3f of frames, not >= %.2f — a pose that does not update cannot drift, so %.1f cm is not a measurement"),
						*R.Name, UpdateRate, kMinUpdateRate, R.MaxDriftCm));
				}
				else if (PelvisZRange < kPoseMoveMinCm)
				{
					// The update counter counts graph UPDATES; bones are evaluated separately. A pose that was never
					// evaluated (or bone transforms not yet valid, which read identity) holds the pelvis still.
					Test->AddError(FString::Printf(TEXT("[Army.OnCapsule] %s: the pelvis Z range was %.2f cm, not >= %.1f — the pose was not evaluated, so %.1f cm is not a measurement"),
						*R.Name, PelvisZRange, kPoseMoveMinCm, R.MaxDriftCm));
				}
				else if (R.MaxDriftCm > kOnCapsuleMaxDriftCm)
				{
					Test->AddError(FString::Printf(TEXT("[Army.OnCapsule] %s: the mesh walks %.1f cm off its capsule (pelvis XY vs actor axis), over the %.1f cm bound — a graph with baked root travel the crowd never extracts (add an in-place graph under army_config spawn.crowd_anim_class)"),
						*R.Name, R.MaxDriftCm, kOnCapsuleMaxDriftCm));
				}
			}
		}

		static constexpr double kSimHz = 60.0;
		static constexpr double kSettleSec = 1.0;
		static constexpr double kSampleSec = 4.0;
		static constexpr int32 kSettleFrames = static_cast<int32>(kSettleSec * kSimHz);   // 60
		static constexpr int32 kSampleFrames = static_cast<int32>(kSampleSec * kSimHz);   // 240
		// The speed both earlier figures were measured at (Khaimera 941.2 cm original / 10.1 cm in-place, render_evidence.json
		// roster_4up khaimera_pelvis_drift_*_at_speed450_cm). SpawnArmyIntoPIE drives the crowd at 400.
		static constexpr double kSpeedCmS = 450.0;
		static constexpr double kSpacingCm = 500.0;
		// CALIBRATED from this row's own runs: green manny 9.6 / quinn 9.9 / khaimera (in-place graph)
		// 8.9 cm; red, with crowd_anim_class.khaimera removed, khaimera 932.2 cm (the earlier measurement read 941.2 over a
		// different window). 30 cm is ~3x the worst green and ~31x under the red.
		static constexpr double kOnCapsuleMaxDriftCm = 30.0;
		static constexpr double kMinUpdateRate = 0.9;   // URO off + AlwaysTick: BudgetThrottle reads 1.000 on such meshes
		static constexpr double kSpeedTolCmS = 1.0;     // the velocity is written once and nothing ticks it
		// A locomoting pelvis bobs several cm per stride; Army.NoFrozen's moving-bone floor (kMoveMin) is 0.5 cm.
		static constexpr double kPoseMoveMinCm = 0.5;
		const FName kPelvis{TEXT("pelvis")};   // the bone the earlier measurement used
		FAutomationTestBase* Test;
		// Constructed with the command, destroyed with it — including on an external DequeueAllCommands().
		FFixedTimeStepScope ClockScope;
		double StartTime = 0.0;
		bool bSpawned = false;
		int32 Frame = 0;
		FArmyCfg Cfg;
		TArray<FRig> Rigs;
	};

	// ================================================================================================
	// The SKELETON-AWARE FOOT-IK gate assertion — Pie.SkeletonAwareFootIK.
	//
	// WHAT HOLE THIS FILLS. ABP_MT_Locomotion is skeleton-aware: a bool
	// HasMannequinFootIKRig = (GetBoneIndex("ik_foot_root") != -1) computed once in
	// BlueprintInitializeAnimation, ANDed with GroundedFlag into an int FootIKAlpha, which drives the
	// CR_Mannequin_FootIK node's Alpha pin. That work is very nearly INVISIBLE to the verification suite:
	// GoldenDiff schema v2 records node class, title, asset and pin bindings FOR THE ANIMGRAPH ONLY. It
	// records neither variables nor the EventGraph, so the new bool, the init event and the AND cannot
	// appear in a golden diff — the ONLY thing that moved in the baseline was one string,
	// "Alpha": "GroundedFlag" -> "Alpha": "FootIKAlpha". The consequence is direct:
	// an edit that kept the NAME FootIKAlpha while gutting the logic behind it would pass the
	// golden. Nothing in either tier could tell the difference.
	//
	// So this test asserts the BEHAVIOUR of the gate, in both directions, on a live evaluated graph.
	//
	// HOW BOTH BRANCHES ARE REACHED WITHOUT A SECOND SKELETON. The only rostered rig that genuinely lacks
	// ik_foot_root is the MetaHuman, whose body is licensed, machine-local, and has no PIE map fixture.
	// Rather than invent one, the OFF branch is reached by forcing HasMannequinFootIKRig=false through raw
	// reflection each tick. That is sound precisely BECAUSE the AnimBP computes it ONCE at init: nothing
	// re-derives it, so the write sticks — and FootIKAlpha, which IS recomputed every update, must then
	// follow it to 0. (The same trick is impossible on `Speed`, which the EventGraph rebuilds every frame.)
	//
	// THE FOUR THINGS EACH ROW GATES:
	//   (a) the bool is SKELETON-DERIVED — it equals (Mesh->GetBoneIndex("ik_foot_root") != INDEX_NONE),
	//       read off the live component rather than restated from the asset;
	//   (b) with the rig present and the pawn grounded, FootIKAlpha == 1 — the pass RUNS;
	//   (c) forcing the bool false drives FootIKAlpha to 0 WHILE GroundedFlag stays 1 — the AND is real.
	//       This is the assertion that fails if the logic is gutted and the name kept: a FootIKAlpha wired
	//       straight through from GroundedFlag reads 1 here and goes RED;
	//   (d) BOTH feet cycle in BOTH arms — the bypass leaves the feet free (the MetaHuman's fix), and the
	//       solve does not destroy the stride on a rig that has the rig (the mannequins' invariant).
	//
	// WHY (d) IS NOT A KHAIMERA ROW, stated rather than left to be noticed. Khaimera's PIE map plays the
	// ORIGINAL Khai_* clips, whose ik_foot tracks are dead (0.000/0.000/0.000) and which are permanently
	// off-limits to write, so the foot-IK pass runs at Alpha=1 against an effector the animation never
	// drives. The pelvis of the travelling clip walks 1362 cm away from that CONSTANT effector, the target
	// goes unreachable, and the leg is yanked to full extension and dragged rather than stepped — which a
	// range check cannot distinguish from a stride. (On the in-place lineup stage, where the in-place duplicate
	// pins the pelvis and the same constant effector is therefore REACHABLE, the same rig, same ABP family
	// and same Alpha=1 measure 0.37 cm.)
	//
	// ☠️ CORRECTED: an earlier version of this paragraph put that drag at
	// "93.26 / 91.76 cm" and cited it as the artefact this row's ceiling guards against. THOSE ARE WORLD-Z
	// FIGURES from an ad-hoc probe, and every foot assertion in this file compares PAWN-RELATIVE Z over a
	// gated flat-lane window. In the quantity actually asserted, Pie.RigLocomotion.Khaimera reads
	// 33.08-35.09 cm — BELOW the healthy mannequins' 45.93-50.81, not above. Mixing the two quantities is
	// a quantity mistake, and it is recorded here rather than quietly deleted. A swing-range
	// row for him here would be green and meaningless. The honest gate for his content is the headless
	// Verify.IKEffectorDrives row over the clip data, which is where it lives.
	//
	// Same real-RHI / CVar guards and the same flat-lane oscillation geometry as Pie.RigLocomotion's
	// BSOutput phase, so the two describe the same window.
	// ================================================================================================
	class FSkeletonAwareFootIKCommand : public IAutomationLatentCommand
	{
	public:
		explicit FSkeletonAwareFootIKCommand(FAutomationTestBase* InTest) : Test(InTest) {}

		virtual bool Update() override
		{
			if (StartTime == 0.0) { StartTime = FPlatformTime::Seconds(); }
			const double Now = FPlatformTime::Seconds();

			APawn* Pawn = nullptr; USkeletalMeshComponent* Mesh = nullptr; UAnimInstance* Inst = nullptr;
			int32 MachineIdx = INDEX_NONE; const FBakedAnimationStateMachine* Baked = nullptr;
			const bool bResolved = ResolvePossessedLocomotionContext(Pawn, Mesh, Inst, MachineIdx, Baked);

			if (bResolved)
			{
				if (Phase == EPhase::Settle)
				{
					const int32 IdleIdx = FindBakedStateIndex(*Baked, TEXT("Idle"));
					if (IdleIdx != INDEX_NONE && Inst->GetInstanceStateWeight(MachineIdx, IdleIdx) > 0.99f)
					{
						Phase = EPhase::Drive;
						StartTime = Now;
					}
				}
				else if (Phase == EPhase::Drive)
				{
					Pawn->AddMovementInput(FVector(1.f, 0.f, 0.f), 1.f);
					const int32 LocoIdx = FindBakedStateIndex(*Baked, TEXT("Locomotion"));
					if (LocoIdx != INDEX_NONE && Inst->GetInstanceStateWeight(MachineIdx, LocoIdx) > 0.9f)
					{
						// (a) The bool must be SKELETON-DERIVED. Read the live component's bone index and
						// the live AnimInstance's variable on the SAME tick, so this compares two live
						// facts rather than a live fact against the asset it was supposed to come from.
						BoneIdx = Mesh->GetBoneIndex(FName(TEXT("ik_foot_root")));
						bGateVarPresent = TryReadBoolVar(*Inst, TEXT("HasMannequinFootIKRig"), bGateAtInit);
						double AlphaProbe = -1.0;
						bAlphaVarPresent = TryReadNumericVar(*Inst, TEXT("FootIKAlpha"), AlphaProbe);
						Test->AddInfo(FString::Printf(
							TEXT("[Gate] live mesh bone index for 'ik_foot_root' = %d; HasMannequinFootIKRig %s (=%s); FootIKAlpha %s (=%.0f)"),
							BoneIdx,
							bGateVarPresent ? TEXT("present") : TEXT("ABSENT"), bGateAtInit ? TEXT("true") : TEXT("false"),
							bAlphaVarPresent ? TEXT("present") : TEXT("ABSENT"), AlphaProbe));
						if (!bGateVarPresent || !bAlphaVarPresent)
						{
							// Nothing downstream can mean anything without both variables; fail here rather
							// than accumulate 6 s of samples against a graph that has no gate at all.
							Test->AddError(FString::Printf(
								TEXT("[Gate] the shared Template no longer exposes %s%s%s on its AnimInstance — the skeleton-aware foot-IK gate has been removed or renamed, and GoldenDiff cannot see that (schema v2 covers the AnimGraph only, not variables or the EventGraph)"),
								bGateVarPresent ? TEXT("") : TEXT("'HasMannequinFootIKRig'"),
								(!bGateVarPresent && !bAlphaVarPresent) ? TEXT(" and ") : TEXT(""),
								bAlphaVarPresent ? TEXT("") : TEXT("'FootIKAlpha'")));
							return true;
						}
						// Class default of the gate bool, for the "computed, not defaulted" assertion in
						// Finalize(). Read off the CDO of the live AnimInstance's own class.
						if (const UObject* CDO = Inst->GetClass()->GetDefaultObject())
						{
							if (const FBoolProperty* BP = CastField<FBoolProperty>(
								Inst->GetClass()->FindPropertyByName(FName(TEXT("HasMannequinFootIKRig")))))
							{
								bCDODefault = BP->GetPropertyValue_InContainer(CDO);
								bCDODefaultRead = true;
							}
						}
						BSFlatZ = Pawn->GetActorLocation().Z;
						if (Mesh->GetBoneIndex(FName(TEXT("foot_l"))) == INDEX_NONE
							|| Mesh->GetBoneIndex(FName(TEXT("foot_r"))) == INDEX_NONE)
						{
							Test->AddError(TEXT("[Gate] foot_l/foot_r not on this mesh — cannot assert the foot-IK gate's behavioural half"));
							return true;
						}
						Phase = EPhase::GateOn;
						StartTime = Now;
					}
				}
				else if (Phase == EPhase::GateOn || Phase == EPhase::GateOff)
				{
					const bool bOff = (Phase == EPhase::GateOff);
					// The OFF arm forces the bool every tick. It is computed once at init, so one write
					// would do — but re-writing costs nothing and is immune to any future re-init.
					if (bOff) { TryWriteBoolVar(*Inst, TEXT("HasMannequinFootIKRig"), false); }

					// Same flat-lane oscillation as Pie.RigLocomotion's BSOutput phase: reverse before the
					// ramp and near spawn so the pawn never climbs, and sample only grounded flat-lane
					// frames at jog speed. Root-relative foot Z cancels the pawn's own translation.
					const double PawnX = Pawn->GetActorLocation().X;
					const double PawnZ = Pawn->GetActorLocation().Z;
					if (PawnX > kBSFlatHighX) { BSDir = -1.f; }
					else if (PawnX < kBSFlatLowX) { BSDir = +1.f; }
					Pawn->AddMovementInput(FVector(BSDir, 0.f, 0.f), 1.f);

					double SpeedNow = -1.0; TryReadNumericVar(*Inst, TEXT("Speed"), SpeedNow);
					bool bFalling = false; TryReadBoolVar(*Inst, TEXT("IsFalling"), bFalling);

					// A short settle after the OFF flip so the first samples are not a one-frame transient
					// as the Control Rig hands the pose back.
					const bool bPastFlipSettle = !bOff || (Now - StartTime) > kFlipSettleSec;

					if (bPastFlipSettle && SpeedNow > 550.0 && !bFalling && PawnX < kBSFlatMaxX
						&& FMath::Abs(PawnZ - BSFlatZ) < 30.0)
					{
						FArm& A = bOff ? Off : On;
						const double RelL = Mesh->GetSocketLocation(FName(TEXT("foot_l"))).Z - PawnZ;
						const double RelR = Mesh->GetSocketLocation(FName(TEXT("foot_r"))).Z - PawnZ;
						A.MinL = FMath::Min(A.MinL, RelL); A.MaxL = FMath::Max(A.MaxL, RelL);
						A.MinR = FMath::Min(A.MinR, RelR); A.MaxR = FMath::Max(A.MaxR, RelR);
						++A.Samples;
						// Read the contract vars on the SAME tick the pose is sampled.
						TryReadBoolVar(*Inst, TEXT("HasMannequinFootIKRig"), A.bGate);
						TryReadNumericVar(*Inst, TEXT("FootIKAlpha"), A.Alpha);
						TryReadNumericVar(*Inst, TEXT("GroundedFlag"), A.Grounded);
						A.bVarsRead = true;
					}

					if ((Now - StartTime) > (bOff ? kSampleSec + kFlipSettleSec : kSampleSec))
					{
						if (!bOff) { Phase = EPhase::GateOff; StartTime = Now; }
						else       { Finalize(); return true; }
					}
				}
			}

			// Per-phase budgets. The two sampling arms have their own caps so a pawn that stops resolving
			// mid-window cannot poll forever and strand FEndPlayMapCommand.
			if ((Phase == EPhase::Settle || Phase == EPhase::Drive)
				&& PhaseBudgetExceeded(Test, Now, StartTime,
					Phase == EPhase::Settle ? TEXT("settle") : TEXT("drive"),
					TEXT("skeleton-aware foot-IK gate")))
			{
				return true;
			}
			if ((Phase == EPhase::GateOn || Phase == EPhase::GateOff) && (Now - StartTime) > 15.0)
			{
				Test->AddError(FString::Printf(
					TEXT("[Gate] 15s cap in the %s arm (pawn/anim stopped resolving); samples on=%d off=%d"),
					Phase == EPhase::GateOn ? TEXT("gate-ON") : TEXT("gate-OFF"), On.Samples, Off.Samples));
				Finalize();
				return true;
			}
			return false;
		}

	private:
		struct FArm
		{
			double MinL = 1.0e9, MaxL = -1.0e9, MinR = 1.0e9, MaxR = -1.0e9;
			int32 Samples = 0;
			bool bGate = false, bVarsRead = false;
			double Alpha = -1.0, Grounded = -1.0;
			double RangeL() const { return Samples ? MaxL - MinL : 0.0; }
			double RangeR() const { return Samples ? MaxR - MinR : 0.0; }
		};

		void Finalize()
		{
			if (bFinalized) { return; }
			bFinalized = true;

			Test->AddInfo(FString::Printf(
				TEXT("[Gate] ON  arm: %d samples, HasMannequinFootIKRig=%s FootIKAlpha=%.0f GroundedFlag=%.0f, foot_l relZ range %.2f cm, foot_r %.2f cm"),
				On.Samples, On.bGate ? TEXT("true") : TEXT("false"), On.Alpha, On.Grounded, On.RangeL(), On.RangeR()));
			Test->AddInfo(FString::Printf(
				TEXT("[Gate] OFF arm: %d samples, HasMannequinFootIKRig=%s FootIKAlpha=%.0f GroundedFlag=%.0f, foot_l relZ range %.2f cm, foot_r %.2f cm"),
				Off.Samples, Off.bGate ? TEXT("true") : TEXT("false"), Off.Alpha, Off.Grounded, Off.RangeL(), Off.RangeR()));

			if (On.Samples < kMinSamples || Off.Samples < kMinSamples)
			{
				Test->AddError(FString::Printf(
					TEXT("[Gate] window too sparse to assert anything (ON %d, OFF %d samples, floor %d) — the flat-lane Speed~600 grounded window never developed"),
					On.Samples, Off.Samples, kMinSamples));
				return;
			}

			// (a) SKELETON-DERIVED — and this assertion is ONE-SIDED on the current fixtures, which is
			// stated here rather than glossed. Every rig eligible for this row HAS ik_foot_root, so
			// `bExpected` is always true and `On.bGate == bExpected` alone would also be satisfied by a
			// gate hard-wired to constant true. (c) below does not close that either, because it WRITES
			// the variable and so bypasses whatever derives it.
			//
			// The consequential direction — a skeleton WITHOUT the bone yielding false — is not reachable
			// from this tier: the only rostered rig that genuinely lacks ik_foot_root is the MetaHuman,
			// whose body is licensed machine-local content with no PIE map fixture, and whose bind needs
			// an in-memory compatible-skeleton declaration that the automation process never performs.
			// Where it IS observed: metahuman_bind.py's probe and the shipped render's own driver log both
			// read HasMannequinFootIKRig=false off the live MetaHuman AnimInstance (render_evidence.json
			// roster_4up._which_rigs_actually_stride).
			//
			// What CAN be closed here is the most plausible shape of "hard-wired true": a variable whose
			// EventGraph derivation was deleted and whose default was set true instead. A skeleton-derived
			// gate must be COMPUTED at init, so its class default must not already be the answer.
			const bool bExpected = (BoneIdx != INDEX_NONE);
			if (On.bGate != bExpected)
			{
				Test->AddError(FString::Printf(
					TEXT("[Gate] HasMannequinFootIKRig reads %s but the live mesh's bone index for 'ik_foot_root' is %d (expected %s) — the gate is not derived from the skeleton it claims to be derived from"),
					On.bGate ? TEXT("true") : TEXT("false"), BoneIdx, bExpected ? TEXT("true") : TEXT("false")));
			}
			if (bCDODefaultRead)
			{
				Test->AddInfo(FString::Printf(
					TEXT("[Gate] class-default HasMannequinFootIKRig = %s; live instance = %s (expected %s from bone index %d)"),
					bCDODefault ? TEXT("true") : TEXT("false"), On.bGate ? TEXT("true") : TEXT("false"),
					bExpected ? TEXT("true") : TEXT("false"), BoneIdx));
				if (bCDODefault)
				{
					Test->AddError(TEXT("[Gate] HasMannequinFootIKRig's CLASS DEFAULT is true. A skeleton-derived gate must be COMPUTED in BlueprintInitializeAnimation and therefore default to false; a true default means the live value cannot be distinguished from a hard-wired constant on any rig that has the bone — which is every rig this row can run on."));
				}
			}
			else
			{
				Test->AddError(TEXT("[Gate] could not read the class default for HasMannequinFootIKRig — cannot establish that the live value was computed rather than defaulted"));
			}

			// The AND assertions only mean something while the pawn is grounded in BOTH arms — otherwise a
			// zero Alpha could be GroundedFlag's doing rather than the gate's. Assert the precondition.
			if (!(On.Grounded > 0.5) || !(Off.Grounded > 0.5))
			{
				Test->AddError(FString::Printf(
					TEXT("[Gate] GroundedFlag was not 1 in both arms (ON %.0f, OFF %.0f) — with the other AND input moving, FootIKAlpha proves nothing about the skeleton gate"),
					On.Grounded, Off.Grounded));
			}
			else
			{
				// (b) the pass RUNS on a rig that has the mannequin IK rig.
				if (!(On.Alpha > 0.5))
				{
					Test->AddError(FString::Printf(
						TEXT("[Gate] FootIKAlpha is %.0f on a GROUNDED rig that HAS ik_foot_root (bone index %d) — the mannequin foot-IK pass is switched off for a rig it is supposed to run on"),
						On.Alpha, BoneIdx));
				}
				// (c) THE LOAD-BEARING ONE. Forcing the bool false must drive Alpha to 0 while
				// GroundedFlag stays 1. A FootIKAlpha that kept its name but lost the AND — wired straight
				// through from GroundedFlag — reads 1 here, and GoldenDiff cannot see the difference.
				if (!(Off.Alpha < 0.5))
				{
					Test->AddError(FString::Printf(
						TEXT("[Gate] AND IS NOT REAL: HasMannequinFootIKRig was forced false and read back %s, GroundedFlag stayed %.0f, yet FootIKAlpha is still %.0f. FootIKAlpha is not (GroundedFlag AND HasMannequinFootIKRig) — the skeleton-aware foot-IK gate has been gutted while keeping its variable name, which the golden baseline (AnimGraph-only, schema v2) cannot detect."),
						Off.bGate ? TEXT("true") : TEXT("false"), Off.Grounded, Off.Alpha));
				}
				if (Off.bGate)
				{
					Test->AddError(TEXT("[Gate] the forced HasMannequinFootIKRig=false did not stick (read back true) — something re-derives it per tick, so the OFF arm never actually exercised the bypass"));
				}
			}

			// (d) BEHAVIOUR, both arms. The OFF arm is the MetaHuman's fix (bypass -> feet free); the ON
			// arm is the mannequins' invariant (the solve must not destroy the stride it is refining).
			auto AssertSwing = [&](const FArm& A, const TCHAR* ArmName, const TCHAR* Why)
			{
				if (!(A.RangeL() > kFootSwingMin) || !(A.RangeR() > kFootSwingMin))
				{
					Test->AddError(FString::Printf(
						TEXT("[Gate] %s arm: feet are NOT cycling — foot_l relZ range %.2f cm, foot_r %.2f cm, floor %.1f cm over %d samples at Speed~600. %s"),
						ArmName, A.RangeL(), A.RangeR(), kFootSwingMin, A.Samples, Why));
				}
				// A floor alone cannot tell "moving" from "moving implausibly"; see kFootSwingMax for the
				// measured band, for the single historical observation that anchors the high side, and for
				// the failure mode this ceiling does NOT catch.
				if (A.RangeL() > kFootSwingMax || A.RangeR() > kFootSwingMax)
				{
					Test->AddError(FString::Printf(
						TEXT("[Gate] %s arm: foot travel is IMPLAUSIBLY LARGE — foot_l relZ range %.2f cm, foot_r %.2f cm, ceiling %.1f cm over %d samples at Speed~600. A pawn-relative foot sweep this big is not a stride; the one prior instance was a leg travelling from far below the pawn origin to above it during a broken take. Check the clip (Verify.IKEffectorDrives) and the rig's placement before touching this number."),
						ArmName, A.RangeL(), A.RangeR(), kFootSwingMax, A.Samples));
				}
			};
			AssertSwing(On, TEXT("gate-ON"),
				TEXT("The rig HAS ik_foot_root so the foot-IK pass runs at full weight; a planted foot under a moving leg here means the pass is solving onto effectors the animation does not drive (see Verify.IKEffectorDrives, which gates that at the clip level)."));
			AssertSwing(Off, TEXT("gate-OFF"),
				TEXT("With the foot-IK pass bypassed the feet are driven straight from the BlendSpace, so a frozen foot here is not the Control Rig's doing — the animation itself is not reaching the legs."));
		}

		// Flat-lane geometry, IDENTICAL to FDriveSpeedToLocomotionCommand's BSOutput phase so the two
		// tests describe the same window on the same maps (the ramp starts at world X=500).
		static constexpr double kBSFlatLowX  = 100.0;
		static constexpr double kBSFlatHighX = 400.0;
		static constexpr double kBSFlatMaxX  = 480.0;
		// The SAME floor as Pie.RigLocomotion's BSOutput - and that is
		// mechanically true rather than a comment claiming it: both forward to the one file-scope
		// constant, where the derivation lives. Two `= 8.0` literals asserting they were the same number
		// was the shape once removed from the ceiling and left standing here.
		static constexpr double kFootSwingMin = kFootSwingMinCm;
		// CEILING (RE-ANCHORED on pawn-relative Z). Every figure below is
		// PAWN-RELATIVE foot Z over a gated flat-lane window — the same quantity this file asserts —
		// harvested from every automation report on disk that carries these rows:
		//   healthy, this command's ON arm  (Manny/Quinn)   45.92 .. 50.80 cm
		//   healthy, this command's OFF arm (Manny/Quinn)   47.47 .. 57.14 cm   <- the real upper edge
		//   healthy, Pie.RigLocomotion Manny/Quinn           31.02 .. 51.56 cm
		//   healthy, Pie.RigLocomotion.Khaimera              33.08 .. 35.09 cm
		//   DEGENERATE-LOW, Pie.RigLocomotion.Khaimera (old)  0.01 ..  0.83 cm  <- the FLOOR catches this
		//   DEGENERATE-HIGH, Pie.RigLocomotion.Khaimera      92.08 .. 93.59 cm  <- only the ceiling can
		// 75.0 sits 1.31x above the largest healthy measurement (57.14, the OFF arm) and 0.81x below the
		// smallest degenerate-high one. Note the OFF arm — not the ON arm — sets the healthy upper edge:
		// the earlier "68% headroom" figure omitted it and overstated the margin.
		//
		// TWO HONEST LIMITS ON THIS NUMBER, because the first version of it was anchored on the wrong
		// quantity entirely and that is not a mistake to paper over twice:
		//  1. The high-side failure mode rests on ONE historical observation — one recorded report,
		//     9 samples, feet sweeping -92.43 -> +1.16 during an early broken take. It is real, it is in
		//     the right quantity, and it came from this very assertion; it is not a reproducible signature.
		//  2. It does NOT catch the unreachable-effector case. That was the original claim and it was
		//     wrong: measured pawn-relative, Khaimera's dragged legs read 33.08-35.09 cm, i.e. BELOW the
		//     healthy mannequins, so no ceiling separates them. The gate for that failure is
		//     Verify.IKEffectorDrives, at the clip level, where the cause actually lives.
		// If a future rig legitimately exceeds this, re-derive from that rig's measurement in THIS
		// quantity rather than raising it until a red goes green.
		//
		// ONE NUMBER, shared with Pie.RigLocomotion's BSOutput phase via the file-scope constant, so the
		// two pawn-relative swing assertions in this file cannot drift apart.
		static constexpr double kFootSwingMax = kFootSwingMaxCm;
		static constexpr double kSampleSec = 3.0;
		static constexpr double kFlipSettleSec = 0.5;
		static constexpr int32  kMinSamples = 5;

		enum class EPhase : uint8 { Settle, Drive, GateOn, GateOff };

		FAutomationTestBase* Test;
		double StartTime = 0.0;
		EPhase Phase = EPhase::Settle;
		bool bFinalized = false;
		bool bGateVarPresent = false, bAlphaVarPresent = false, bGateAtInit = false;
		bool bCDODefault = false, bCDODefaultRead = false;
		int32 BoneIdx = INDEX_NONE;
		double BSFlatZ = 0.0;
		float BSDir = 1.f;
		FArm On, Off;
	};
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FPieUnarmedIdleWeightTest,
	"MotionTerminal.Pie.UnarmedIdleWeight",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::ProductFilter)

bool FPieUnarmedIdleWeightTest::RunTest(const FString& Parameters)
{
	EnqueuePieMapRun(this, TEXT("/Game/ThirdPerson/Lvl_ThirdPerson"), new FWaitForIdleWeightCommand(this));
	return true;
}

// Driven assertion on the Template, parametrized as ONE COMPLEX test over the
// pie_rigs.json fixture. Real movement drives Speed, flipping the Locomotion machine Idle -> Locomotion,
// proving the pawn -> EventGraph -> variable -> transition contract end-to-end for EACH rig. Each row's
// map auto-possesses that rig's pawn on the SHARED ABP_MT_Locomotion_C (zero retarget), and the unchanged
// FDriveSpeedToLocomotionCommand pins the POSSESSED pawn — so the map selects the rig. Same real-RHI/CVar guards.
// The drive command also has a BSOutput phase that asserts the BlendSpace OUTPUT (feet cycle
// on the flat lane at Speed ~600), not just the state weight — closing a latent axis-misbinding gap
// (X<-Speed sampled MM_Idle: state read Locomotion while the feet stayed frozen). Runs for every rig.
IMPLEMENT_COMPLEX_AUTOMATION_TEST(FPieRigLocomotionTest,
	"MotionTerminal.Pie.RigLocomotion",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::ProductFilter)

void FPieRigLocomotionTest::GetTests(TArray<FString>& OutBeautifiedNames, TArray<FString>& OutTestCommands) const
{
	TArray<FPieRigRow> Rows; FString Err;
	const bool bLoaded = LoadPieRigs(Rows, Err);
	if (!bLoaded || Rows.Num() == 0)
	{
		// A successful load with a genuinely empty 'rigs' array has no OutError set — synthesize one
		// so the sentinel's embedded message is never blank.
		if (bLoaded && Err.IsEmpty()) { Err = TEXT("pie_rigs.json loaded but yielded zero rig rows"); }
		EmitManifestErrorSentinel(OutBeautifiedNames, OutTestCommands, Err);
		return;
	}
	for (const FPieRigRow& R : Rows)
	{
		OutBeautifiedNames.Add(R.Rig);       // e.g. "Manny", "Quinn"
		OutTestCommands.Add(R.Map);          // map path -> RunTest Parameters
	}
}

bool FPieRigLocomotionTest::RunTest(const FString& Parameters)
{
	// Every row runs, so there is no no-rows sentinel to check: only the manifest-error + map gate.
	bool bResult = false;
	if (!GatePieRigRow(this, Parameters, bResult)) { return bResult; }

	// The map's GameMode override auto-possesses this rig's pawn (ABP_MT_Locomotion_C); real PIE, because
	// AddMovementInput needs a possessed pawn to drive.
	EnqueuePieMapRun(this, Parameters, new FDriveSpeedToLocomotionCommand(this));
	return true;
}

// The DIFFERENTIATOR assertion. Proves AM_MT_Attack plays in the
// UpperBody slot WITHOUT disturbing the Locomotion state machine, via three introspection mechanisms
// (the third being the per-node GatherDebugData AbsoluteWeight, the increment over the first two).
// Same tier (`Pie.`), same real-RHI / CVar guards, same map (auto-possessed BP_MT_TestPawn).
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FPieMontageIsolationTest,
	"MotionTerminal.Pie.MontageIsolation",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::ProductFilter)

bool FPieMontageIsolationTest::RunTest(const FString& Parameters)
{
	// Lvl_MT_Test: GameMode override auto-possesses BP_MT_TestPawn (ABP_MT_Locomotion_C). Real PIE, so a
	// pawn is possessed for Montage_Play to run on; real RHI so GatherDebugData walks a live evaluated graph
	// (Mechanism A).
	EnqueuePieMapRun(this, TEXT("/Game/MT/Lvl_MT_Test"), new FMontageIsolationCommand(this));
	return true;
}

// The UpperBodyState TRIGGER-CONTRACT test. MontageIsolation calls Montage_Play
// DIRECTLY (bypassing the EventGraph), so a re-author that drops the `if (UpperBodyState==1) { Montage_Play;
// SetUpperBodyState(0); }` trigger branch goes undetected. This drives the contract end-to-end: set
// UpperBodyState=1 by reflection -> assert the UpperBody slot fires -> assert the auto-reset to 0. Fixture-
// independent (the shared Template's EventGraph), so Manny map only. Same tier / RHI / CVar guards.
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FPieUpperBodyTriggerTest,
	"MotionTerminal.Pie.UpperBodyTrigger",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::ProductFilter)

bool FPieUpperBodyTriggerTest::RunTest(const FString& Parameters)
{
	// Manny map: GameMode override auto-possesses BP_MT_TestPawn (ABP_MT_Locomotion_C, which carries the
	// EventGraph trigger). Real PIE + real RHI so the montage actually blends into the live UpperBody slot.
	EnqueuePieMapRun(this, TEXT("/Game/MT/Lvl_MT_Test"), new FUpperBodyTriggerCommand(this));
	return true;
}

// Re-grounded on the foot-IK confirmation run (see the FFootIKNodeIntegrationCommand header): the foot-IK
// CR node INTEGRATION + grounded-ramp CONFORMANCE test. Drives the pawn onto the 20° ramp, settles, jumps,
// and re-settles while sampling the feet. HARD assertions: (1) foot_l/foot_r exist, (2) the ControlRig
// foot-IK node is PRESENT in the flattened, post-LayeredBlendPerBone tree (structural integration — real and
// observable), and (3) grounded-ramp CONFORMANCE — the settled deform soles are grounded+level (band [7,13]cm
// AND |clrL-clrR| < 4cm), the evened stance a PROVEN-node-free rig FAILS (it splits to clrL~6.84 / clrR~17.75).
// Foot IK is CONFIRMED to conform both deform feet +3.46/−7.14 cm against a confound-immune baseline that
// STRUCTURALLY REMOVES the CR node from the live evaluated graph (proven absent). The earlier "no net deform-
// foot conformance / FALSE-POSITIVE" narrative is RETRACTED: its node-off arm was an alpha-disable that left
// AnimNode_ControlRig at W:100% (rig-on vs rig-on — a confounded control). Airborne/recover numbers remain
// OBSERVATIONS (jump physics). Full mechanism/history is in the FFootIKNodeIntegrationCommand header above.
// Locomotion/feet-cycle is covered by Pie.RigLocomotion. Same tier / RHI / CVar guards.
//
// Parametrized as ONE COMPLEX test over the pie_rigs.json rows with "footIKNode": true. Manny
// and Quinn share SK_Mannequin + the SAME ABP + the duplicated ramp lane, so the same structural checks in
// FFootIKNodeIntegrationCommand apply to both (verified per rig at build).
IMPLEMENT_COMPLEX_AUTOMATION_TEST(FPieFootIKNodeIntegrationTest,
	"MotionTerminal.Pie.FootIKNodeIntegration",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::ProductFilter)

void FPieFootIKNodeIntegrationTest::GetTests(TArray<FString>& OutBeautifiedNames, TArray<FString>& OutTestCommands) const
{
	// The foot-IK NODE covers the mannequin-family rigs only (Khaimera's row is footIKNode:false). Happy path:
	// Manny + Quinn both footIKNode:true -> 2 sub-tests, so the _NoFootIKRows sentinel is never emitted.
	BuildFilteredRigTestList(&FPieRigRow::bFootIKNode, TEXT("footIKNode"), TEXT("foot-IK conformance"),
		TEXT("_NoFootIKRows"), kNoFootIKRowsPrefix, OutBeautifiedNames, OutTestCommands);
}

bool FPieFootIKNodeIntegrationTest::RunTest(const FString& Parameters)
{
	// The map's GameMode override auto-possesses this rig's pawn (ABP_MT_Locomotion_C); each map carries
	// the ramp (Manny original, Quinn duplicate). Real PIE + real RHI is required for GetSocketLocation
	// to read a live ControlRig-evaluated skeleton and for GatherDebugData to walk the live graph.
	return RunGatedPieSubTest(this, Parameters, kNoFootIKRowsPrefix,
		TEXT("Pie.FootIKNodeIntegration registered ZERO foot-IK sub-tests"),
		[this]() -> IAutomationLatentCommand* { return new FFootIKNodeIntegrationCommand(this); });
}

// The TURN-IN-PLACE per-rig assertion. ONE COMPLEX test over the pie_rigs.json
// rows with "turnInPlace": true (Manny + Quinn share SK_Mannequin + the SAME ABP_MT_Locomotion_C carrying
// the nested TurnInPlace SM, so the same FTurnInPlaceCommand drives both). Each row's map auto-possesses
// that rig's pawn; the command pins the POSSESSED pawn, drives TurnYawDelta to +90 THEN -90, and proves the
// pawn rotates IN PLACE in BOTH directions (signed sway-free root-yaw sweep +/- + drift ceiling + injected-
// value read-back + Mechanism-B TIP_TurnRight/TIP_TurnLeft state). Same real-RHI / CVar guards as the other
// Pie.* tests.
IMPLEMENT_COMPLEX_AUTOMATION_TEST(FPieTurnInPlaceTest,
	"MotionTerminal.Pie.TurnInPlace",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::ProductFilter)

void FPieTurnInPlaceTest::GetTests(TArray<FString>& OutBeautifiedNames, TArray<FString>& OutTestCommands) const
{
	BuildFilteredRigTestList(&FPieRigRow::bTurnInPlace, TEXT("turnInPlace"), TEXT("turn-in-place"),
		TEXT("_NoTurnRows"), kNoTurnRowsPrefix, OutBeautifiedNames, OutTestCommands);
}

bool FPieTurnInPlaceTest::RunTest(const FString& Parameters)
{
	return RunGatedPieSubTest(this, Parameters, kNoTurnRowsPrefix,
		TEXT("Pie.TurnInPlace registered ZERO turn-in-place sub-tests"),
		[this]() -> IAutomationLatentCommand* { return new FTurnInPlaceCommand(this); });
}

// The AIM-OFFSET per-rig assertion. ONE COMPLEX test over the pie_rigs.json rows
// with "aimOffset": true (Manny + Quinn share SK_Mannequin + the SAME ABP_MT_Locomotion_C carrying the
// RotationOffsetBlendSpace over AO_Rifle, so the same FAimOffsetCommand drives both; Khaimera's row stays
// aimOffset:false — the mannequin AO_Rifle on Khaimera's retargeted bones is a documented degrade, left
// open). Each row's map auto-possesses that rig's pawn; the command pins the POSSESSED pawn, drives
// AimPitch 0 -> +0.9, and proves the upper body (head) rotates > 15 deg while the lower body (foot_r) stays
// put (< 5 deg), AimPitch proven-set via read-back, and the RotationOffsetBlendSpace node runs (Mechanism A).
// Same real-RHI / CVar guards as the other Pie.* tests.
IMPLEMENT_COMPLEX_AUTOMATION_TEST(FPieAimOffsetTest,
	"MotionTerminal.Pie.AimOffset",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::ProductFilter)

void FPieAimOffsetTest::GetTests(TArray<FString>& OutBeautifiedNames, TArray<FString>& OutTestCommands) const
{
	BuildFilteredRigTestList(&FPieRigRow::bAimOffset, TEXT("aimOffset"), TEXT("aim-offset"),
		TEXT("_NoAimRows"), kNoAimRowsPrefix, OutBeautifiedNames, OutTestCommands);
}

bool FPieAimOffsetTest::RunTest(const FString& Parameters)
{
	return RunGatedPieSubTest(this, Parameters, kNoAimRowsPrefix,
		TEXT("Pie.AimOffset registered ZERO aim-offset sub-tests"),
		[this]() -> IAutomationLatentCommand* { return new FAimOffsetCommand(this); });
}

// The HAND-REACHES-SOCKET per-rig assertion — THE load-bearing hand-IK test. ONE
// COMPLEX test over the pie_rigs.json rows with "handIK": true (Manny + Quinn share SK_Mannequin + the SAME
// ABP_MT_Locomotion_C carrying the TwoBoneIK(hand_l) node, and each pawn carries the MT_Weapon-tagged
// WeaponMesh on hand_r, so the same FHandReachesSocketCommand drives both). Each row's map auto-possesses that
// rig's pawn; the command settles Idle, captures the proven-off reach (WeaponIKAlpha=0), drives WeaponIKAlpha=1,
// and hard-asserts the off-hand reaches IK_Hand_Gun (distOn < 5cm) with a materially larger off gap
// (distOff - distOn > 10cm), the alpha proven at both ends via read-back. Same real-RHI / CVar guards as the
// other Pie.* tests (real RHI so the TwoBoneIK evaluates a live graph and GetSocketLocation reads a live skeleton).
IMPLEMENT_COMPLEX_AUTOMATION_TEST(FPieHandReachesSocketTest,
	"MotionTerminal.Pie.HandReachesSocket",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::ProductFilter)

void FPieHandReachesSocketTest::GetTests(TArray<FString>& OutBeautifiedNames, TArray<FString>& OutTestCommands) const
{
	BuildFilteredRigTestList(&FPieRigRow::bHandIK, TEXT("handIK"), TEXT("hand-reaches-socket"),
		TEXT("_NoHandIKRows"), kNoHandIKRowsPrefix, OutBeautifiedNames, OutTestCommands);
}

bool FPieHandReachesSocketTest::RunTest(const FString& Parameters)
{
	return RunGatedPieSubTest(this, Parameters, kNoHandIKRowsPrefix,
		TEXT("Pie.HandReachesSocket registered ZERO hand-IK sub-tests"),
		[this]() -> IAutomationLatentCommand* { return new FHandReachesSocketCommand(this); });
}

// The SKELETON-AWARE FOOT-IK gate assertion. ONE COMPLEX test over the pie_rigs.json rows with
// "footIKGate": true (Manny + Quinn — both share SK_Mannequin, the SAME ABP_MT_Locomotion_C carrying the
// HasMannequinFootIKRig / FootIKAlpha gate, and clips whose ik_foot effectors are genuinely driven).
// Khaimera's row stays footIKGate:false ON PURPOSE, and the full reasoning is in FSkeletonAwareFootIKCommand's
// header: his PIE map plays the ORIGINAL Khai_* clips whose ik_foot tracks are dead and which are off-limits
// to write, so his legs are dragged by an unreachable effector rather than stepped — a foot-swing assertion
// there passes without meaning it. In the pawn-relative quantity this file asserts, that reads 33.08-35.09 cm
// (NOT the 93.26 / 91.76 an earlier revision quoted, which were world-Z figures from an ad-hoc probe — see
// the correction in FSkeletonAwareFootIKCommand's header). The gate on HIS content is Verify.IKEffectorDrives.
//
// This closes a known gap: the golden baseline is AnimGraph-only (schema v2), so the bool,
// the init event and the AND are invisible to GoldenDiff.MTLocomotion — only the Alpha binding STRING moved.
// This test drives the gate to both values on a live graph and asserts the alpha and the feet follow.
// Same tier / real-RHI / CVar guards as the other Pie.* tests.
IMPLEMENT_COMPLEX_AUTOMATION_TEST(FPieSkeletonAwareFootIKTest,
	"MotionTerminal.Pie.SkeletonAwareFootIK",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::ProductFilter)

void FPieSkeletonAwareFootIKTest::GetTests(TArray<FString>& OutBeautifiedNames, TArray<FString>& OutTestCommands) const
{
	BuildFilteredRigTestList(&FPieRigRow::bFootIKGate, TEXT("footIKGate"), TEXT("skeleton-aware-foot-IK"),
		TEXT("_NoFootIKGateRows"), kNoFootIKGateRowsPrefix, OutBeautifiedNames, OutTestCommands);
}

bool FPieSkeletonAwareFootIKTest::RunTest(const FString& Parameters)
{
	return RunGatedPieSubTest(this, Parameters, kNoFootIKGateRowsPrefix,
		TEXT("Pie.SkeletonAwareFootIK registered ZERO skeleton-aware-foot-IK sub-tests"),
		[this]() -> IAutomationLatentCommand* { return new FSkeletonAwareFootIKCommand(this); });
}

// The montage MECHANISM assertion. Pie.MontageIsolation proves an UPPER-BODY montage leaves
// locomotion alone; this proves the complementary claim the army-scale montage takes rest on — a FULL-BODY
// dynamic montage plays through the DefaultSlot node and swaps clip-to-clip with a measurably seamless
// inertial blend. Confound-immune by construction: the same detector is run against a no-inertialization
// hard cut of the same two clips through the same slot, and the test FAILS if that control does not spike.
// Fixture-independent (the shared Template's own AnimGraph), so the Manny map only. Same tier / real-RHI /
// CVar guards as the other Pie.* tests — real RHI so the graph actually evaluates and the component-space
// bone transforms this test differentiates are live, not stale.
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FPieMontageSeamlessTest,
	"MotionTerminal.Pie.MontageSeamless",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::ProductFilter)

bool FPieMontageSeamlessTest::RunTest(const FString& Parameters)
{
	// FOUND BY A GENUINE CLEAN-CHECKOUT RUN, not by reasoning about one.
	//
	// This test's two clips live under `Content/MT/GAS_Clips/`, which is GITIGNORED: they are the Fab
	// "Game Animation Sample" pack, machine-local exactly like the Paragon and MetaHuman content. Every
	// other machine-local dependency in this suite SKIPs-with-log when absent (GoldenDiff.MTKhaimera,
	// the six ClipVaries.Khai_* rows, IKEffectorDrives.Khai_*, ImportScale.khaimera/.metahuman, and the
	// Pie.* rows gated by GatePieMap). This one did not: it walked into PIE, failed to load the clips and
	// went RED, so a fresh clone's pie tier came back 22/1 instead of green — when the whole point is
	// that the pipeline is reproducible from a clean checkout. Cloned to a scratch directory and
	// run for real, that is exactly what it did.
	//
	// Gated HERE rather than inside the latent command so no PIE session is started for a row that cannot
	// run, and on DoesPackageExist rather than on a load result so the distinction stays sharp: absent is a
	// clean checkout and SKIPs; present-but-unloadable is a defect and stays RED in LoadClips().
	for (const TCHAR* const ClipPath : { kGasWalkClip, kGasRunClip })
	{
		const FString PackageName = FPackageName::ObjectPathToPackageName(FString(ClipPath));
		if (!FPackageName::DoesPackageExist(PackageName))
		{
			AddInfo(FString::Printf(TEXT("SKIP (localOnly asset absent on this machine): %s"), ClipPath));
			return true;
		}
	}

	// The command pins the engine clock so "cm per frame" has a fixed basis; FMontageSeamlessCommand's own
	// ClockScope member captures the prior state and restores + logs it on destruction, no matter how the
	// measurement ends (including an external DequeueAllCommands()).
	EnqueuePieMapRun(this, TEXT("/Game/MT/Lvl_MT_Test"), new FMontageSeamlessCommand(this));
	return true;
}

// The shared RunTest skeleton the Pie.Army.* tests below repeat — skip if the machine-local
// army map is absent, then EnqueuePieMapRun with the MakeCommand() latent command. Same shape as
// RunGatedPieSubTest above, minus the manifest/row gating that helper carries (the army tests aren't
// per-row — one fixed map, one command per test).
namespace
{
	// RequiredPackages (Pie.Army.OnCapsule): further machine-local content the row needs beyond the map — the
	// crowd's roster pawns, Khaimera's among them. Absent ones SKIP with the counted marker, like the map.
	bool RunGatedArmyPieTest(FAutomationTestBase* Test, TFunction<IAutomationLatentCommand*()> MakeCommand,
		const TArray<FString>& RequiredPackages = TArray<FString>())
	{
		const FString Map = TEXT("/Game/MT/Army/Lvl_MT_Army");
		if (!FPackageName::DoesPackageExist(Map))
		{
			// The localOnly marker is what run_verification counts as a SKIP (_SKIP_MARKER). Without it these rows
			// read as EXERCISED on a clean clone while asserting nothing — which they once did.
			Test->AddInfo(FString::Printf(TEXT("SKIP (localOnly army map absent on this machine): %s"), *Map));
			return true;
		}
		for (const FString& Package : RequiredPackages)
		{
			if (!FPackageName::DoesPackageExist(Package))
			{
				Test->AddInfo(FString::Printf(TEXT("SKIP (localOnly army content absent on this machine): %s"), *Package));
				return true;
			}
		}

		EnqueuePieMapRun(Test, Map, MakeCommand());
		return true;
	}
}

// The crowd-scale BUDGET assertion. Spawns the shipped tiered army into the PIE
// world and asserts its measured anim-eval cost (summed PerformAnimEvaluation_WorkerThread + AnimGameThreadTime,
// read off the stats thread) is under army_config.json's ceilings — the first wall-clock-basis measurement of
// the shipped configuration, not the modelled per-instance sum. Real RHI so the graph actually evaluates.
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FPieArmyBudgetTest,
	"MotionTerminal.Pie.Army.Budget",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::ProductFilter)

bool FPieArmyBudgetTest::RunTest(const FString& Parameters)
{
	return RunGatedArmyPieTest(this, [this]() -> IAutomationLatentCommand* { return new FArmyBudgetCommand(this); });
}

// The negative-path probe CheckArmyMemoryReading needed (a later check found that
// Pie.Army.Budget only re-confirmed the healthy 4.637 MB pass path — that proves the pass path still
// passes, not that the guard can still fail). Runs synchronously against synthetic byte arrays, no PIE /
// live crowd required, so it lands in the fast default (-NullRHI) tier as MotionTerminal.Verify.*, not
// MotionTerminal.Pie.*.
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FArmyBudgetZeroGuardTest,
	"MotionTerminal.Verify.ArmyBudgetZeroGuard",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::ProductFilter)

bool FArmyBudgetZeroGuardTest::RunTest(const FString& Parameters)
{
	// Real shipped shape (army_config.json): hero 6 / mid 8 / far 32, N=46, mb_ceiling=6.0. Per-instance
	// MB approximated from the measured 4.637 MB total (hero heaviest, far lightest).
	const uint64 HeroBytes = static_cast<uint64>(0.15 * 1024.0 * 1024.0);
	const uint64 MidBytes = static_cast<uint64>(0.11 * 1024.0 * 1024.0);
	const uint64 FarBytes = static_cast<uint64>(0.088 * 1024.0 * 1024.0);

	// Negative path: the mid tier (8 pawns) collapses to zero. This is the exact case that motivated the
	// fix — a later check showed a scale-vs-anchor check (hero-anchored OR min-anchored) clears this shape
	// (total 3.72 MB vs. both a 3.45 MB and a 2.02 MB threshold), so only the explicit zero count can
	// catch it. If this assertion regresses, the guard has silently lost its ability to catch a collapsed
	// middle tier — the whole reason this test exists.
	{
		TArray<uint64> Bytes;
		for (int32 i = 0; i < 6; ++i) { Bytes.Add(HeroBytes); }
		for (int32 i = 0; i < 8; ++i) { Bytes.Add(0); }   // collapsed mid tier
		for (int32 i = 0; i < 32; ++i) { Bytes.Add(FarBytes); }

		TArray<FString> Errors; double TotalMb = 0.0; int32 ZeroCount = 0;
		CheckArmyMemoryReading(Bytes, /*SpawnedN=*/46, /*MbCeiling=*/6.0, /*bHasMbCeiling=*/true, Errors, TotalMb, ZeroCount);

		if (!TestEqual(TEXT("collapsed mid tier: exactly 8 zero readings counted"), ZeroCount, 8)) { return false; }
		const bool bFoundZeroError = Errors.ContainsByPredicate(
			[](const FString& E) { return E.Contains(TEXT("ZERO animation-memory")); });
		if (!TestTrue(TEXT("collapsed mid tier: the zero-reading check raises an error"), bFoundZeroError)) { return false; }
	}

	// Positive control: the same shape, fully healthy, must NOT false-positive on either check.
	{
		TArray<uint64> Bytes;
		for (int32 i = 0; i < 6; ++i) { Bytes.Add(HeroBytes); }
		for (int32 i = 0; i < 8; ++i) { Bytes.Add(MidBytes); }
		for (int32 i = 0; i < 32; ++i) { Bytes.Add(FarBytes); }

		TArray<FString> Errors; double TotalMb = 0.0; int32 ZeroCount = 0;
		CheckArmyMemoryReading(Bytes, /*SpawnedN=*/46, /*MbCeiling=*/6.0, /*bHasMbCeiling=*/true, Errors, TotalMb, ZeroCount);

		if (!TestEqual(TEXT("healthy crowd: zero readings counted"), ZeroCount, 0)) { return false; }
		if (!TestEqual(TEXT("healthy crowd: no errors raised"), Errors.Num(), 0)) { return false; }
	}

	// Short-count control: fewer readings than SpawnedN (a partial-spawn confound, independent of zeroing)
	// must still trip the scale check kept as the secondary signal.
	{
		TArray<uint64> Bytes;
		for (int32 i = 0; i < 40; ++i) { Bytes.Add(FarBytes); }   // 40 counted, but SpawnedN=46

		TArray<FString> Errors; double TotalMb = 0.0; int32 ZeroCount = 0;
		CheckArmyMemoryReading(Bytes, /*SpawnedN=*/46, /*MbCeiling=*/6.0, /*bHasMbCeiling=*/true, Errors, TotalMb, ZeroCount);

		const bool bFoundScaleError = Errors.ContainsByPredicate(
			[](const FString& E) { return E.Contains(TEXT("did not scale with N")); });
		if (!TestTrue(TEXT("short count: the scale check still raises an error"), bFoundScaleError)) { return false; }
	}

	return true;
}

// The statistical SPOT-CHECK. Drives the crowd and runs the per-node weight
// assertions on K sampled instances (locomotion machine weight ~1 at speed, aim-offset node present, slot
// weight sane) — QA at scale with the sampled fraction logged, no silent cap. Real RHI so the graph evaluates.
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FPieArmySpotCheckTest,
	"MotionTerminal.Pie.Army.SpotCheck",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::ProductFilter)

bool FPieArmySpotCheckTest::RunTest(const FString& Parameters)
{
	return RunGatedArmyPieTest(this, [this]() -> IAutomationLatentCommand* { return new FArmySpotCheckCommand(this); });
}

// The NO-FROZEN-INSTANCE check — the Verify.ClipVaries doctrine at crowd scale.
// Across K driven instances a moving bone's pose must vary over a window; a deliberately-unticked control
// MUST read frozen, so the test proves it can distinguish ticking from frozen before trusting the crowd.
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FPieArmyNoFrozenTest,
	"MotionTerminal.Pie.Army.NoFrozen",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::ProductFilter)

bool FPieArmyNoFrozenTest::RunTest(const FString& Parameters)
{
	return RunGatedArmyPieTest(this, [this]() -> IAutomationLatentCommand* { return new FArmyNoFrozenCommand(this); });
}

// The far ring is budgeted for real: AMTCharacter makes it drivable, a.Budget.Enabled makes the allocator run,
// and an explicit significance lets it throttle. See FArmyBudgetThrottleCommand for the three arms.
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FPieArmyBudgetThrottleTest,
	"MotionTerminal.Pie.Army.BudgetThrottle",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::ProductFilter)

bool FPieArmyBudgetThrottleTest::RunTest(const FString& Parameters)
{
	return RunGatedArmyPieTest(this, [this]() -> IAutomationLatentCommand* { return new FArmyBudgetThrottleCommand(this); });
}

// Previously no gate asked whether a crowd character stands where its capsule is. One pawn per crowd rig, driven
// in place at 450; the pelvis's horizontal distance from the capsule axis must stay under the bound. See
// FArmyOnCapsuleCommand. The roster pawns are machine-local content too (Khaimera is licensed), so each one's
// package gates the row alongside the map; a crowd_anim_class that does not load is a FAILURE, not a skip.
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FPieArmyOnCapsuleTest,
	"MotionTerminal.Pie.Army.OnCapsule",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::ProductFilter)

bool FPieArmyOnCapsuleTest::RunTest(const FString& Parameters)
{
	FArmyCfg Cfg; FString Err;
	if (!LoadArmyConfig(Cfg, Err)) { AddError(TEXT("[Army.OnCapsule] ") + Err); return false; }
	TArray<FString> Required;
	for (const FString& Rig : Cfg.CrowdRigs) { Required.Add(FPackageName::ObjectPathToPackageName(Cfg.Roster.FindChecked(Rig))); }
	return RunGatedArmyPieTest(this, [this]() -> IAutomationLatentCommand* { return new FArmyOnCapsuleCommand(this); }, Required);
}

#endif // WITH_AUTOMATION_TESTS && WITH_EDITOR
