# A test that cannot find the defect

**MotionTerminal — architecture note.** UE 5.8. A C++ verification plugin and an AnimGraph that
MCP tools write.

> **Language.** This note uses ASD-STE100 writing rules: short sentences, the active voice, simple
> tenses, one meaning for each word, and no metaphors. The approved-word dictionary is a licensed
> document. Thus this note obeys the rules, but no person checked each word against that dictionary.

## The result

A test suite gave 22 passes and 0 failures. The feature that it tested did not operate.

The feature was not weak. It had no effect. The variable that controls the feature did nothing. The
branch that this variable must control stayed on for all characters in all shots.

All tests passed. The values in the test output were correct and repeatable. The values did not
answer the question.

## The defect

The rifle-mode branch puts the aim offset on the base pose that the bake process used:

```
UseCachedPose 'LocomotionPose' ───────────────────────► BasePose ┐
SequenceEvaluator(MF_Rifle_Idle_ADS, t=0) ─► AO_Rifle ─► BlendPoses[0] ┴─► LayeredBoneBlend
                                                        BlendWeights[0] ◄── RifleModeAlpha
                                                                 └─► SaveCachedPose 'RifleModePose'
```

`AO_Rifle` contains mesh-space rotation additives. The bake process calculates each additive as
`D = S·R⁻¹`. The reference is frame 0 of `MF_Rifle_Idle_ADS`. On that base pose, `D·R` gives the
pose that the animator made. On a different base pose, `D·R` gives a deformation. That deformation
is the first defect. The rifle-mode branch corrects it.

The first version of the branch made the blend weight a pin binding: `BlendWeights_0 ← RifleModeAlpha`. This binding cannot
compile. `BlendWeights` is a `TArray<float>`. The property-access path went to the array and not to
element 0. The compiler removed the copy and gave this message:

```
[Compiler] Layered blend per bone  Cannot copy property (float -> TArray)
```

The pin kept its default value of `1.000000`. `RifleModeAlpha` did nothing.

## Why the tests did not find the defect

The suite has a test for the aim offset. This test passed on all three rigs:

| rig | `maxHeadRelPelvisDelta` |
|---|---|
| Manny | 79.37° |
| Quinn | 79.51° |
| Khaimera | 79.40° |

These values are correct. But this test cannot answer the question.

The test sets `AimPitch` to 0. Then the test sets `AimPitch` to 0.9. The test measures the rotation
of the upper body between the two poses. The two poses have the same base pose. When you subtract
one pose from the other, the base pose goes away. A test with this method cannot find a defect in
the base pose. The cause is the method, and not the care of the person who wrote the test.

The golden-topology gate did not find the defect for a different reason. This gate records the
target of a pin binding. It does not record if that binding compiles. The binding was present, and
the gate passed.

Only one item in the tool chain showed the defect. It was a compiler warning. The warning came in
the warnings section of the test output.

## The experiment

Both gates run on the same graph that has the defect. Do these steps:

1. Remove the link to `BlendWeights_0`. This makes the defect again. The pin has no link and keeps
   the value `1.000000`.
2. Run the new connection gate.
3. Run the golden-topology gate.
4. Make the link again.

The same graph, the same commit and two gates give opposite results:

```
MotionTerminal.Verify.RifleGateWired      -> Fail
MotionTerminal.GoldenDiff.MTLocomotion    -> Success
```

The tool kept a report file for each run. Thus we measured the difference between the two gates. We
did not only make a statement about it. This is important, because a statement of this type is easy
to believe and easy to get wrong.

The new gate gives the cause and the correction, and not only a failure:

> `[RifleGate] RIFLE BRANCH IS NOT GATED: 'BlendWeights_0' has no link, so the node reads its
> literal default of '1.000000' on every frame and RifleModeAlpha drives nothing. At a stuck 1.0
> every character in every shot renders with MF_Rifle_Idle_ADS blended over the upper body. Wire a
> variable-get of RifleModeAlpha into this pin — do NOT use a pin binding, which is how this broke
> the first time.`

## The measurement in the game

The connection gate examines the graph. A PIE probe measures the behavior. The probe changes one
variable in each phase. The probe also records a control value (`dAA`) at a constant state:

| phase | upper body (largest mean `dAB`) | lower body | control |
|---|---|---|---|
| `GATE` — rifle 1↔0, aim off | **97.72°** (hand_l) | **0.34°** (pelvis) | 0.000 upper / 0.641 lower |
| `AIM_OVER_RIFLE` — aim 1↔0, rifle on | **44.38°** (clavicle_r) | **0.26°** | 0.000 / 0.675 |

Each bone in the lower body moved less than its own control value. Thus the mask at `spine_01` is
correct, and the legs keep the locomotion pose. Before the correction, `thigh_r` moved **37.1°**.
Now it moves **0.21°**.

## The rule

The work on this branch and its gates has one fault that comes again and again:

> **A test that cannot measure the condition that it must measure.**

We found this fault many times. Two of these faults were in gates that we wrote to find the fault
before them.

A test suite that passes gives you data about the tests. It does not give you data about the
system. Thus each gate must have a proof that it can fail on the defect that it names. A test that
never failed is a test that no person tested.

## Limits

- The probe writes a witness line for the node weight. This line has the same value for the two
  arms of the toggle. Thus it does not show a difference between them. The proof comes only from
  the difference between `dAB` and `dAA`: a control of 0.000° against values of more than 10°.
- The proof of failure applies to the test for the missing link. Later we moved the search function
  and the binding test. We did not do the removal test again for those two parts.
