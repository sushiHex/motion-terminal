# A/B foot-IK evidence (quantitative, rendering-independent)

> ## ⛔ SUPERSEDED — THE CONCLUSION BELOW IS FALSE
>
> **The foot IK CONFORMS both deform feet to the ramp (+3.46 cm / −7.14 cm).** This fixture's
> conclusion ("the rig writes no foot displacement" / "no measurable change in foot-ground contact" /
> "no visible A/B foot difference to capture") is WRONG because its **A/B was confounded**: Take-A
> "IK OFF" cleared the ControlRig `Alpha` binding and forced the literal to `0.0`, but a **live
> `Alpha ← GroundedFlag` Property binding overwrote the forced 0 every tick** — so on the grounded
> ramp the node ran at **W:100% in BOTH takes**. This was rig-ON vs rig-ON, never a control. An
> alpha/binding-disable is not a valid off-baseline.
>
> The confound-immune measurement — the ControlRig node **structurally removed and *proven absent*
> from the live evaluated graph** (12 vs 13 nodes in the GatherDebugData tree), NOT an alpha-disable —
> settled it: node-present vs proven-node-free evens a split ramp stance (clrL~6.84 penetrating /
> clrR~17.75 floating) to level soles (clrL~10.26 / clrR~10.61), **+3.46/−7.14 cm**, byte-identical to
> stock. See the real A/B still
> **`research/fixtures/roster-contact/03_footik_ab_split_vs_plant.jpg`**.
>
> The body below is RETAINED as the documented FALSE-NEGATIVE record (how the confounded A/B misled).
> Do NOT cite it as current evidence.

UE 5.8 (CL-55116800).

## Why this is data, not a contact-sheet of stills

The intended evidence was a matched A/B **still pair** (IK off vs IK on) showing feet
clipping stair nosings (A) vs conforming (B). Two independent blocks made that impossible, and a
third finding made it moot:

1. **MRQ absent** — the `MovieRenderPipeline` plugin is not enabled in this project (every
   `unreal.MoviePipeline*` class is missing from Python). MRQ at 60fps is impossible without a
   plugin-enable + relaunch.
2. **Viewport-less editor** — `get_viewport_info` reports `0x0 / realtime:false`. Only the
   auto-possessed player's own first-person view renders to a captured frame; any placed
   `CameraActor` view target, and `SceneCaptureComponent2D` render targets, come back pure black
   (the viewport screen-grab reports "PIE viewport likely not rendered"). The pawn's own view
   cannot frame its own feet from outside. The screen-grab also caps at ~3fps
   regardless of `capture_interval` (documented in research/khaimera-retarget-pipeline.md).
3. **The A/B contrast does not exist** — measured below. Even with a working renderer, A and B
   would look identical, so a "clip vs conform" still pair could not be produced honestly.

So the evidence is the **foot-bone clearance relative to the ground directly beneath each foot**
(downward raycast), sampled every 0.05 s along the flat→ramp→stairs lane, for both takes. This is
confound-free (unlike a raw matched-px foot-Z comparison, which is polluted by walk-cycle phase and
the capsule snapping between stair steps). Penetration (clearance < 0) through a rising surface is
the signature the foot IK gate would suppress if it were doing ground-conforming work.

## Setup (verified)

- Take A (IK OFF): pawn's mesh AnimClass runtime-swapped to a throwaway duplicate
  `Tmp_MT_Locomotion_IKOff` whose ControlRig `Alpha` binding was cleared and its literal forced to
  `0.0` (ControlRig contributes nothing → foot IK off). `runtime_identity.anim_class_changed:true`,
  `anim_instance_class:/Game/MT/Tmp_MT_Locomotion_IKOff.Tmp_MT_Locomotion_IKOff_C`.
- Take B (IK ON): shipped `/Game/MT/ABP_MT_Locomotion` unmodified; `ControlRig.Alpha ← GroundedFlag`
  (=1 while grounded, verified `is_bound:true`). `anim_class_changed:false`.
- Identical movement (sustained `add_movement_input((1,0,0))`), identical lane. Root motion is
  unaffected by IK, so matched pawn-X ⇒ matched ground under the foot in both takes.
- `BP_MT_TestPawn` and `ABP_MT_Locomotion` never edited on disk (git clean; gate binding re-read
  IDENTICAL after cleanup). Duplicate deleted after use.

## Foot-ground clearance (cm), planted-foot rows are the ~0.3–0.6 values; large positive = swing foot

```
region              take  clrL: min / mean / max   clrR: min / mean / max   deepest-penetration(either foot)
flat run-up (0-480)   A    0.59 / 0.65 / 0.70        0.31 / 0.33 / 0.37        0.31
                      B    0.59 / 0.65 / 0.70        0.31 / 0.33 / 0.37        0.31     <- IDENTICAL
ramp 20deg (500-1060) A   -6.52 / -6.22 / -2.29      6.08 / 6.12 / 6.14       -6.52
                      B   -6.52 / -6.11 / -0.40      6.08 / 6.12 / 6.14       -6.52     <- planted foot penetrates ~6.5cm in BOTH
landing (1080-1330)   A    0.39 / 0.39 / 0.40        0.11 / 0.12 / 0.12        0.11
                      B    0.63 / 0.63 / 0.63        0.35 / 0.36 / 0.36        0.35     <- ~0.24cm diff (negligible; wrong sign for "conform")
STAIRS (1364-1724)    A   -8.02 / -0.29 / 0.62       0.31 / 6.93 / 25.31      -8.02
                      B   -9.86 / -0.75 / 0.61       0.24 / 8.97 / 25.31      -9.86     <- B penetrates MORE, not less
```

### Stairs, per-sample (px, clrL, clrR, ball-L world Z, ground-under-ball Z)

```
TAKE A (IK OFF)                              TAKE B (IK ON)
px=1380.0 clrL= 0.62 clrR= 0.35 bl=230.8    px=1377.4 clrL= 0.52 clrR= 0.24 bl=230.7
px=1417.9 clrL= 0.62 clrR= 0.34 bl=230.8    px=1409.3 clrL= 0.51 clrR= 0.24 bl=230.7
px=1449.4 clrL= 0.33 clrR=25.05 bl=255.5    px=1441.0 clrL=-1.89 clrR=22.84 bl=253.3
px=1483.5 clrL= 0.61 clrR= 0.33 bl=255.8    px=1480.5 clrL= 0.61 clrR= 0.33 bl=255.8
px=1521.2 clrL=-8.02 clrR=16.70 bl=272.2    px=1519.3 clrL=-9.86 clrR=14.86 bl=270.4
px=1558.2 clrL= 0.60 clrR= 0.31 bl=280.8    px=1550.2 clrL= 0.60 clrR=25.31 bl=280.8
px=1598.2 clrL= 0.59 clrR= 0.31 bl=280.8    px=1590.3 clrL= 0.59 clrR= 0.31 bl=280.8
px=1634.9 clrL= 0.58 clrR=25.31 bl=305.8    px=1629.1 clrL= 0.25 clrR=24.98 bl=305.5
px=1665.2 clrL= 0.58 clrR= 0.31 bl=305.8    px=1668.1 clrL= 0.58 clrR= 0.31 bl=305.8
px=1709.1 clrL= 0.57 clrR= 0.31 bl=305.8    px=1703.9 clrL= 0.57 clrR= 0.31 bl=305.8
```
The −8/−10 spikes are the swing foot crossing a stair nosing at a slightly different walk phase, not
a planted-foot difference; they occur in BOTH takes and are marginally deeper in B.

## IK-vs-deform bone probe (ramp, matched px)

```
IKBONE B (IK ON)  px~513  foot_l=15.62 ik_foot_l=15.62 foot_r=15.29 ik_foot_r=15.29
IKBONE A (IK OFF) px~520  foot_l=18.37 ik_foot_l=18.37 foot_r=18.04 ik_foot_r=18.04
   ... foot_l == ik_foot_l in BOTH takes at every sample; A and B foot Z match at matched px.
```
In take B (IK full weight) the `ik_foot_*` bones equal the deform `foot_*` bones — their default
ref-pose relationship — i.e. the ControlRig is not driving the foot targets to anything. The rig
writes no foot displacement.

## Conclusion (as recorded at the time; superseded, see the banner above)

`CR_Mannequin_FootIK`, gated ON (Alpha=1, grounded) vs forced OFF (Alpha=0), produces **no
measurable change in foot-ground contact** on the flat, the 20° ramp, or the stairs. The gate WIRE
is real and correct (`Pie.FootIKAlphaGates` asserts Alpha tracks GroundedFlag), but the ControlRig
it gates does not perform grounded foot placement on the Manny deform skeleton in this setup.
There is therefore no visible A/B foot difference to capture. Reporting this negative
result rather than an unverified "they differ" claim follows the lesson of the silently-static
retarget bake (a success status is not proof of a correct result).
