# Dynamics tuning workflow

How a visual requirement for secondary motion becomes a measured, gated, reusable setting. Proven on Khaimera's hood feathers,
axe cords, wind and motion scale, and necklace. The rules below are the ones that cost
time when they were missed.

## The loop

1. **Restate the visual requirement as something measurable.** "Flutter faster and stop sooner" became flutter rate (Hz), flutter
   amplitude (cm) and settle time (s). "Hugs the chest" became the hang angle from straight down, ON minus OFF. Add the
   measure to `dyn_analyze.py` with a unit test before tuning anything.
2. **Read the engine source before designing.** The UE 5.8 plugin is experimental and its behaviour is not what the
   names suggest (see Engine facts). Cite file:line in the design.
3. **Inspect the mesh before tuning a part.** Probe the skin weights: how much of the part is bound to its bone alone,
   whether it is a separate island or welded to its host. Painted-on geometry cannot be simulated without stretching
   the host; free geometry can. `hero_intake_fit.py` records this per flap (`skin`).
4. **Write the design** in `docs/specs/`, then **verify each of its claims against the engine source and the
   measurements before building** (look for the case that breaks it, not the case that confirms it). Record
   what was adopted and what was not, with reasons.
5. **Parity first.** Build the change with its new effect at zero and gate it against the existing baseline with no
   rebaseline (`python Scripts/dynamics_graph.py --hero <Rig>`, then
   `python Scripts/dyn_run.py --hero <Rig> <tag> --gate`). A pass proves the plumbing changed nothing.
   `dyn_run --gate` does not rebuild the rig: always rebuild first.
6. **Sweep.** Put candidates in a JSON file and run `python Scripts/dyn_sweep.py --hero <Rig> <candidates.json>`: one build and one
   ON/OFF drive per candidate, overrides merged into the behaviour library for that build only.
7. **Compare per behaviour** with `python Scripts/dyn_compare.py --hero <Rig> <reference> <tags...>`. Check the groups you did not
   touch read `max_d 0.000` (isolation), and that every part of the touched group moved (`moved n/n`): a silent key
   miss shows as a part that did not move.
8. **Choose, write it into `Scripts/dynamics_behaviours.json`** with a `tuned` note, and keep the sweep table as a
   comment next to the code it tunes.
9. **Rebaseline** with a reason (`dyn_run.py --hero <Rig> <tag> --rebaseline "<why>"`), then a repeat run must PASS and the
   causal control must FAIL (`--nostep`, solver not stepping).
10. **Render it and look** (`dyn_render.py`, `dyn_overlay.py`) under `captures/<topic>/`. What the render
    shows outranks the metrics.
11. **Review the diff**, fix, rebuild, re-gate, then merge.

## A new hero

Every dynamics script takes `--hero <Rig>` (a `HeroProfiles/<Rig>.json`); no hero is named in their code
(`test_no_hero_name_in_dynamics_code`). `Scripts/dyn_hero.py` resolves the rest:

- from the profile: mesh, skeleton, parts, colliders, body roles (leg checks use the skeleton's own names);
- by convention under `/Game/MT/<short>/` (`short` = the rig without `Paragon`): `CR_MT_<short>Dynamics` and
  `ABP_MT_<short>Dynamics` (built), `BP_MT_TestPawn_<short>` and `InPlace/ABP_MT_<short>_InPlace` (the hero's own test
  pawn and in-place locomotion: create them first; the drive names what is missing);
- optional hand-set extras in `HeroProfiles/dynamics/<Rig>.json`, each with a `why`: `grip` (pose + hands, for clips
  that hold no weapon pose) and `solver_settings` (reset thresholds once the hero's OFF arm is measured);
- framing and lane spacing scale with the profile's height; records are `dyn_<Rig>_<tag>.json`, the build manifest
  `dyn_<Rig>_build.json`, the baseline `Tests/DynamicsBaselines/<Rig>.json`.

Then, all from PowerShell with the editor up:

1. `python Scripts/hero_intake.py /Game/Paragon<Hero>`: the profile (roles, parts, colliders, held props).
2. `python Scripts/hero_onboard.py --hero <Rig>`: IK rig, retargeter, the template's clips baked onto the hero, a
   forward blendspace and child ABP, the in-place set, the test pawn, the reaction physics asset; read back into
   `onboard_<Rig>.json` (PASS, or the gaps by name). `--check` audits a hero onboarded by hand.
3. Check which behaviour each part resolves to (add one only when none fits), build (`dynamics_graph.py`), first
   baseline (`--rebaseline`), repeat PASS, `--nostep` FAIL. A hero with nothing hanging from the pelvis records the
   leg check as not applicable, and its baseline must agree.
4. `python Scripts/hit_reaction_run.py --hero <Rig> <tag>`: the hit-reaction matrix.

What a new hero brought that the first did not (Aurora) became rules, not exceptions: travel on the
root, blended soft tissue, thin hair particles, a held prop's grip, mesh cloth. Expect the next hero to add more.

## Behaviours: reuse across characters

Settings live in `Scripts/dynamics_behaviours.json` as named behaviours (`feather`, `cloth_panel` (heavy cloth),
`cloth_strip` (heavy cloth plus twist), `hanging_cord`, `pendant`, `default`). A part gets one by its profile facts (`assign`: `<preset>.<kind>`, `<preset>`, `*`) or by a
profile override `secondary.behaviours.<part id>`. To give a new character's parts a look: first check which existing
behaviour fits; add a new behaviour only when none does, tune it with the loop above, and record where it was tuned.

## Engine facts (UE 5.8 Control Rig Dynamics) that changed decisions

- `GetDynamicsSolverData` read before Step is one update stale: correct motion from your own current-frame
  measurement, not the solver's readback.
- Per-particle `Damping` is air drag and sums with the solver's drag: it pushes a part back against the body at speed.
  The global drag (2.5/s, tuned for cloth trail) is ~1,250 cm/s^2 of push at a 500 cm/s run, more than gravity: a
  heavy ornament needs `bScaleDampingByInverseMass` with a `Mass` above 1, which divides the whole drag by the mass
  (RigParticleSimulation.cpp:88-110). Turning its own `Damping` to 0 alone changes nothing (measured on the pendant).
- A chain's root particle is always kinematic; force a part through its tail Null.
- `AddDynamicsParticleForce` with a key that misses does nothing, silently: read every authored pin back.
- `PerlinNoise1D` is already (-1, 1); the noise node's Lerp(Min, Max, P + 0.5) doubles it at -1/1.
- `DeltaFromPrevious` reports a delta on a zero-dt evaluation; only its cache waits for dt > UE_SMALL_NUMBER.
- `SetDynamicsSolverSpaceMotion` replaces the whole struct; keep one table for spawn and per-frame values.
- The drag and inertial multipliers are runtime-clamped to [0, 1]; per-axis scaling has to be done by the rig.
- **There is no twist.** Particles are points and a bone gets the minimal arc to its particle; roll about a strip only
  comes from a post-solve modifier (`twist`, on the strip's intake-measured face normal). Read the pre-solve pose from
  variables stored before Step, never from a node wired after it.
- **Mass is inert unless drag is mass-scaled.** "Heavy" = `Mass` > 1 with `bScaleDampingByInverseMass`; a low
  `DampingRatio` adds the overshoot, a softer angle limit stops it soaking up the swing.

## Measuring a change that moves no particle

The gate and `dyn_compare` read positions, plus the roll (`twist`). A modifier that only rotates bones must add its
own measure first, or every check passes while it is broken.

## Measurement gotchas

- The spring sets a part's flutter rate; driving it with faster noise mostly gets filtered out.
- `flutter_hz` is a derivative-energy ratio, not a perceived frequency; read it beside the amplitude and the look.
- Stop-swing angles of near-horizontal flaps wrap around +/-180 deg.
- Flaps share one rest copy of each body collider; one flap's tail change resizes it for every flap.
- A rig build with ~100 extra nodes takes ~3.5 min; the build call's timeout is 900 s.
- Check free GPU memory before a sweep or render. Starved of VRAM the editor crawls (~0.05 fps)
  and drives time out; renders cancel themselves under 1.5 GB free (`dyn_render.py`, `--res=1920x1080`).
- One sweep at a time (`dyn_sweep.py` takes `Saved/MotionTerminalTests/dyn_sweep.lock`): a sweep's last step rebuilds
  the shipped rig, and a second sweep started then deletes the AnimBP under it.
- Run `/Game/...` scripts from PowerShell (Git Bash rewrites the paths). Quit the editor through `quit_clean`, never by
  killing it.
