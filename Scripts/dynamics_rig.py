# Author CR_MT_KhaimeraDynamics: Control Rig Dynamics secondary motion on Khaimera's loincloth and torso cloth.
#
# Spec: docs/specs/2026-10-06-khaimera-cr-dynamics-design.md (v1.2), §3.2-3.3. This file is the
# spike's first deliverable ("can a script create the CR asset, declare its curve elements, and author
# Construction and Forwards Solve with the Dynamics units?") written to be promoted, not thrown away.
#
# WHAT IT BUILDS (the core; thresholds, colliders and the witness-curve writes are layered on below):
#   Construction:  PrepareForExecution -> SpawnDynamicsSolver1 -> SpawnDynamicsChains(roots, gravity)
#   Forwards:      BeginExecution      -> StepDynamicsSolver(Alpha)
#   Hierarchy:     Khaimera's bones + the four witness curves declared as curve elements (SetCurveValue does
#                  nothing for a name the hierarchy cannot resolve, RigUnit_SetCurveValue.cpp:14-18).
# The calls follow the engine's own authoring toolset, AnimationAssistantToolset/.../toolsets/controlrig.py.
#
# REBUILDABLE, NOT ACCRETED: `author` deletes and re-creates the asset. The asset binds Khaimera's skeleton,
# so it lives under the git-ignored Content/MT/Khaimera/ and is saved through mt_drive.require_saved_gitignored.
#
# Usage (run in the editor through `Scripts/editor_ops.py` (`ops.run_file`)):
#     KDYN_ACTION = 'author' | 'report'
#     exec(open(r'.../Scripts/dynamics_rig.py').read())
# Every action prints one `KHAIDYN <json>` line.
import importlib
import json
import os
import sys

import unreal

_MT_SCRIPTS = os.path.join(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()), 'Scripts')
if _MT_SCRIPTS not in sys.path:
    sys.path.insert(0, _MT_SCRIPTS)
_mt = importlib.reload(importlib.import_module('mt_drive'))
_af = importlib.reload(importlib.import_module('collider_autofit'))
_bh = importlib.reload(importlib.import_module('dyn_behaviours'))
_dh = importlib.reload(importlib.import_module('dyn_hero'))

# The hero, FIRST: every path below depends on it, and run_python execs into a persistent namespace, so the caller
# names it on every exec (KDYN_HERO; dynamics_graph passes it) rather than relying on a value left from a previous
# run or on an environment variable the running editor never saw (docs/specs/2026-10-28-dynamics-hero-agnostic-pipeline-design.md).
HERO = _dh.resolve(globals().pop('KDYN_HERO', None))
CR_PATH = HERO['cr_path']
MESH_PATH = HERO['mesh']

# Experimental plugin: every struct path it is reached through is pinned here, in one place.
CR_UNIT = '/Script/ControlRig.'
DYN_UNIT = '/Script/ControlRigDynamics.'
UNITS = {
    'Construction': CR_UNIT + 'RigUnit_PrepareForExecution',
    'ForwardsSolve': CR_UNIT + 'RigUnit_BeginExecution',
    'SpawnSolver': DYN_UNIT + 'RigUnit_SpawnDynamicsSolver1',   # the unsuffixed one is Deprecated 5.8
    'StepSolver': DYN_UNIT + 'RigUnit_StepDynamicsSolver',
    # The witnesses. A stateful counter for MT_DynRigTick, and the solver's own event flags.
    'TickCounter': '/Script/RigVM.RigVMFunction_AccumulateFloatAdd',
    'SolverData': DYN_UNIT + 'RigUnit_GetDynamicsSolverData',
    'SetTick': CR_UNIT + 'RigUnit_SetCurveValue',
    'SetTeleport': CR_UNIT + 'RigUnit_SetCurveValue',
    'SetPosReset': CR_UNIT + 'RigUnit_SetCurveValue',
    'SetKinReset': CR_UNIT + 'RigUnit_SetCurveValue',
}
BOOL_TO_FLOAT = '/Script/RigVM.RigVMFunction_MathBoolToFloat'
# Forwards Solve execution order after Step; each writes one witness curve from the named source pin.
WITNESS_WRITES = [('SetTick', 'MT_DynRigTick', 'TickCounter.Result'),
                  ('SetTeleport', 'MT_DynTeleport', 'SolverData.bTeleportDetected'),
                  ('SetPosReset', 'MT_DynPosReset', 'SolverData.bPositionResetTriggered'),
                  ('SetKinReset', 'MT_DynKinReset', 'SolverData.bKinematicSpeedResetTriggered')]
# The rig input the AnimBP drives. Public, so the anim node can expose it as a pin.
# Default 0: an unlinked pin leaves the rig inert, which gate (b) reads as red rather than passing silently.
ALPHA_VAR = 'DynamicsAlpha'
# The chains come from the hero profile (the intake's discovered chains; on Khaimera exactly the spike's six hand-picked
# roots). Every one is skinned to visible mesh at LOD0-2 and to nothing at LOD3+
# (research/ue58-rigging-tools-assessment.md, 'Spike step 2'), which is why the overridden pawns are forced to LOD 0.
WITNESS_CURVES = ['MT_DynRigTick', 'MT_DynTeleport', 'MT_DynPosReset', 'MT_DynKinReset', 'MT_DynFrameSpeed']
GRAVITY_MULTIPLIER = 1.0   # the unit's default is 0.0 (RigDynamicsExecutionHelpers.h), i.e. weightless cloth
SIMULATION_SPACE = 'Component'
# Air drag (the cloth first draped straight down while running): A particle relaxes toward the air, which is minus
# the body's world velocity (RigParticleSimulation.cpp:102-106), only when its damping is above zero (:88-89); the
# spike had none, so the cloth hung straight although the pawns really ran at 500 cm/s. One global rate (Hz),
# CALIBRATED BY MEASUREMENT to a target trail at the drive's run speed. The free-pendulum estimate
# D = g tan(trail) / v (1.374 Hz for 35 deg at 500 cm/s) undershoots because the chain is also pulled toward the
# animated pose. Measured loincloth trail, mean of bk/fr, ON pawn (dyn_analyze run_trail_deg, 2026-10-26):
#   D = 0 -> 3.2 deg (no drag), 1.374 -> 10.5, 3 -> 18.4, 6 -> 30.0, 12 -> 44.0; linear between 6 and 12 gives 8.2 for 35.
#
# Spring and drag (8 Hz drag made the cloth read as too damped): drag damps ALL relative motion, so 8 Hz bought the trail by
# killing the swing. The trail is really fought by the spring pulling each particle back to the animated pose
# (Strength, RigDynamicsData.h:584, engine default 2 Hz), and the stock pose hangs the cloth straight down. Softening
# that spring lets far less drag reach the same trail. Measured, ON pawn, loincloth mean (dyn_analyze trail +
# liveliness; stop = peak forward swing within 1.5 s of the hard stop; bounce = trail spread while running):
#   S 2 / D 8   -> trail 35.6, stop 12.4, bounce 1.44   (drag only: too damped)
#   S 1 / D 4   -> 45.2, 42.0, 2.44        S 1 / D 2   -> 31.9, 64.9, 2.29
#   S 1 / D 2.5 -> 35.7, 58.8, 2.23        <- chosen: the drag-only trail, ~5x the swing
#   S 0.5 / D 1 -> 27.9, 154.0, 6.98       S 0.25 / D 0.5 -> -15.1, 178.6, 37.3   (flips over: too loose)
# No solver resets/teleports at any setting; leg clearance stays >= 6.9 cm.
AIR_DAMPING_HZ = 2.5
TARGET_STRENGTH_HZ = 1.0
SOLVER_OWNER = 'root'      # the skeleton's root bone; author() takes the imported hierarchy's top bone if it differs
SOLVER_COMPONENT = HERO['solver_component']

# The solver thresholds, each derived from the OFF arm of the drive profile at 60 Hz fixed step
# (Scripts/dyn_analyze.py over Saved/MotionTerminalTests/khai_dyn_s5_*.json;
# research/ue58-rigging-tools-assessment.md, 'Spike steps 3–7', records the measurements). Change one only with a research entry giving a physical reason.
#   OFF arm, measured: largest particle distance from the simulation origin 159.26 cm (a torso-cloth root at the
#   chest); largest kinematic (root) component-space speed 614.7 cm/s (the jog-to-idle transition at the stop);
#   longest chain 43.96 cm; one frame's travel 8.33 cm; peak linear acceleration 10047 cm/s^2 (the hard stop).
K_C = 1.5                 # excursion bound, x chain length: below the 2x a rotation-only readback can reach
SOLVER_SETTINGS = {
    'Settings.MaxTimeStep': 1.0 / 60.0,             # one substep per 60 Hz frame...
    'Settings.MaxNumSteps': 4,                      # ...and up to four, so a 15 fps hitch still integrates
    'Settings.bResetFromPosition': True,
    'Settings.PositionThresholdForReset': round(159.26 + K_C * 43.96, 2),      # 225.20 cm
    'Settings.bResetFromKinematicSpeed': True,
    'Settings.KinematicSpeedThresholdForReset': round(3.0 * 614.7, 1),        # 1844.1 cm/s
    'Settings.bResetFromEvaluationInterval': False,  # it fires on editor hitches
    # Autofit: collision (and distance) constraints are resolved this many times per step (engine default 1,
    # RigDynamicsData.h:165-170). With one pass, a thigh swinging forward into its rest box left the loincloth up to
    # 5.8 cm inside it on 57 of 420 frames (collider_clearance).
    'Settings.NumConstraintSubIterations': 3,
    'TeleportDetection.bFromPositionChange': True,
    'TeleportDetection.PositionChangeThreshold': round(3.0 * 8.33, 2),        # 3 frames' travel: 24.99 cm
    'TeleportDetection.bFromOrientationChange': True,
    'TeleportDetection.OrientationChangeThreshold': 30.0,   # engine default; the drive has no turn
    'TeleportDetection.bFromLinearAcceleration': True,
    'TeleportDetection.LinearAccelerationThreshold': round(3.0 * 10047.2, 1),  # 30141.6 cm/s^2
    'TeleportDetection.bFromAngularAcceleration': True,
    'TeleportDetection.AngularAccelerationThreshold': 100000.0,  # engine default; the drive has no turn
}
# The thresholds above were measured on the first hero's OFF arm; a hero measured since overrides them in its
# HeroProfiles/dynamics/<rig>.json (solver_settings, with a why). Unknown keys are refused.
_unknown = sorted(set(HERO['solver_settings']) - set(SOLVER_SETTINGS))
if _unknown:
    raise RuntimeError('KHAIDYN %s solver_settings has unknown keys %s' % (HERO['rig'], _unknown))
SOLVER_SETTINGS.update(HERO['solver_settings'])

# Colliders, hand-placed then autofit (the loincloth was seen cutting through the thighs, then the chest strips into
# the torso). Every collider, and which chain collides with which, now comes from the hero profile's `colliders` section,
# derived from the mesh by Scripts/collider_autofit.py (+ hero_intake_fit.py): fitted capsules for every body segment
# a chain can reach, the torso split left/right where it is wide, and a REST BOX per chain on any part of the body it
# already rests against, so it can swing away from the body but not into it. No collider is placed by hand here.
PROFILE = HERO['profile_path']


BACKEND = 'cr_dynamics'
# BEHAVIOURS (docs/specs/2026-10-28-dynamics-behaviours-design.md): every part's settings come from a named,
# reusable behaviour in Scripts/dynamics_behaviours.json, picked by the part's preset and kind ('assign'), or by a hero
# profile override (secondary.behaviours.<part id>). No character name is in the library. The tuning history of the
# behaviours tuned on Khaimera follows; the library records each one's summary ('tuned').
# Native per-particle settings (docs/specs/2026-10-27-secondary-motion-backends-design.md §3.2: native first). The angle limit holds each joint
# within AngleLimit of the animated pose, at AngleLimitStrength Hz (0 disables it, RigDynamicsData.h:612-618).
# Cloth (the chest strips' ends were seen tucking behind the belt at the stop). Swept 2026-10-27 through the
# gate (dyn_run --gate; bend = a joint's angle ON minus OFF, stop window), left strip end / strip stop swing /
# front loincloth stop swing and frames inside its thigh rest capsule:
#   off          61.5 deg / 31-33 / 5  / 36      30 deg 4 Hz  37.5 / 27-29 / 34 / 35
#   30 deg 6 Hz  22.7 / 24-26 / 32 / 25  <- chosen      30 deg 10 Hz 20.7 / 21-23 / 28 / 16 (too stiff a swing)
#   40 deg 4 Hz  43.7 (barely enforced)              25 deg 10 Hz 13.2 / 17 / 24 / 20 (strip swing halved)
# Tail (the axe tails, step 3): unlimited, the cord flipped over the axe at the stop (stop swing 83 / 166 deg, tip 64 /
# 60 cm from the animation on a 33 cm chain). 30 deg 6 Hz: 33 / -55 deg, 36 / 19 cm; 45 deg 6 Hz: 37 / -46, 39 / 23;
# 30 deg 12 Hz: 20 / -55, 26 / 18. The cloth setting: it stops the flip and keeps the cord swinging.
# Tail, revised (the axe feathers did not hang under gravity): the limit above held
# the cord within 30 deg of its authored pose, which sticks straight out, so it never hung (83-91 deg from straight down
# at rest). Now it barely tracks the pose (Strength 0.25 Hz: ~5 deg off a free hang on the 60 cm cord), has no angle
# limit, and its own air drag replaces the limit as the brake (per-particle Damping sums with AIR_DAMPING_HZ,
# RigParticleSimulation.cpp:88, so Damping 4.5 is 7 Hz in all). Swept (from-down after the stop / frames inside a
# collider / worst clearance): Damping 2 (S 0.1) 2 deg / 8 / -2.21 cm; 4 (S 0.1) 1 / 4 / -1.16; 3 4-6 / 1 / -1.09;
# 6 4-8 / 5 / -1.51; 4.5 4-7 / 3 / -1.30 <- chosen. Then (the cords moved too elastically and too damped): Damping 2.0, swept
# in research/ue58-rigging-tools-assessment.md, 'Looser axe cords'; Damping 1 flips a cord over the axe at the stop. Running it swings 20-100 deg from down in every setting (the arm
# swing), settling within ~1.5 s of the stop; it never flips over the axe.
#
# Hood feathers ('cloth.flap'; they read as far too stiff and damped, and should settle with very little gravity
# influence): the cloth settings drooped every feather tip 6.4 cm
# (median) off the sculpt at rest. Swept (rest droop / run flutter, dyn_analyze.flutter, medians in cm): cloth
# settings 6.4 / 2.45; gravity 1 with a softer spring (Strength 0.5) 11.9 / 4.54; gravity 0.1 1.75 / 3.86;
# gravity 0.05 S 0.5 3.65 / 4.86; gravity 0.02 1.60 / 4.75; gravity 0 S 0.5 0.79 / 4.70 <- chosen. The 45 deg / 3 Hz
# limit is a soft stop; Strength above 1 Hz only stiffened them (3 Hz: flutter 2.45).
# Revised (too much settle at the end, as though underwater): the 0.5 Hz spring under the
# 2.5/s air drag is a ~2 s swing, damped to about half critical, so each feather drifted back over ~1.2 s. Swept at
# gravity 0, 45 deg / 3 Hz (Strength Hz / DampingRatio: run flutter / stop swing / settle after the stop,
# dyn_analyze.settle_time, medians; all 33 settled in every run): 0.5/0.1 4.70 / 14.6 / 1.20 s; 1.25/0.05 4.07 /
# 13.7 / 0.88; 1.5/0 3.74 / 12.8 / 0.82; 1.0/0.3 3.48 / 13.6 / 0.57; 1.5/0.1 3.40 / 12.5 / 0.53 (worst 0.72) <- chosen,
# a ~0.7 s swing; 1.5/0.3 2.77 / 10.6 / 0.47; 2.0/0.1 2.84 / 9.0 / 0.42; 2.5/0.15 2.43 / 6.1 / 0.30. A faster settle
# costs flutter amplitude at every setting. One drive, one release phase: not a controlled ring-down;
# the render is the judge.
# Revised (flutter faster and stop moving faster): the amplitude no longer has to come
# from a soft spring, because the behaviour's flutter (see GROUP_INPUTS) pushes the feathers while running. Stiffer:
# 2.5 Hz / 0.15 settles in 0.30 s (worst 0.37) against 0.53; the sweep is recorded at GROUP_INPUTS.
# These became the behaviours cloth_panel ('cloth'), hanging_cord ('tail') and feather ('cloth.flap'), with exactly the
# merged values the preset table gave each part (the gate passed unchanged on the switch).


load_behaviours = _bh.load       # the library + an experiment override of its values (Scripts/dyn_behaviours.py)


def _autofit(lib=None):
    """The profile's colliders, for the parts this backend simulates (docs/specs/2026-10-27-secondary-motion-backends-design.md §3.2). A part assigned to a
    backend that is not built is an error, never silently left unsimulated. Each part's 'behaviour' is the one the
    intake resolved into the profile (collider_autofit.build); with `lib`, it must exist there."""
    with open(PROFILE) as f:
        doc = json.load(f)
    col, parts = doc['colliders'], doc.get('secondary', {}).get('parts')
    if not parts:
        raise RuntimeError('KHAIDYN profile has no secondary.parts: re-run hero_intake.py')
    other = sorted('%s=%s' % (p['id'], p['backend']) for p in parts if p['backend'] != BACKEND)
    if other:
        raise RuntimeError('KHAIDYN parts assigned to a backend not built yet: %s' % ', '.join(other))
    mine = {p['id'] for p in parts}
    missing = sorted(mine - set(col['chains']))
    if missing:
        raise RuntimeError('KHAIDYN parts with no collider entry (re-run the intake fit): %s' % ', '.join(missing))
    presets = {p['id']: p.get('preset') for p in parts}
    kinds = {p['id']: p.get('kind', 'chain') for p in parts}
    chains = {k: dict(v, preset=presets[k], kind=kinds[k]) for k, v in col['chains'].items() if k in mine}
    unresolved = sorted(k for k, v in chains.items() if not v.get('behaviour'))
    if unresolved:
        raise RuntimeError('KHAIDYN parts with no behaviour in the profile (re-run hero_intake.py): %s'
                           % ', '.join(unresolved))
    unknown = sorted('%s=%s' % (k, v['behaviour']) for k, v in chains.items()
                     if lib is not None and v['behaviour'] not in lib['behaviours'])
    if unknown:
        raise RuntimeError('KHAIDYN profile behaviours not in the library (re-run hero_intake.py): %s'
                           % ', '.join(unknown))
    return [s for s in col['shapes'] if 'local' in s], chains


def _shape_literal(s):
    loc = s['local']
    q, t = loc['rotation_xyzw'], loc['translation']
    tm = ('TM=(Rotation=(X=%.6f,Y=%.6f,Z=%.6f,W=%.6f),Translation=(X=%.4f,Y=%.4f,Z=%.4f),Scale3D=(X=1.0,Y=1.0,Z=1.0))'
          % (q[0], q[1], q[2], q[3], t[0], t[1], t[2]))
    if s['type'] == 'box':
        e = loc['extents_cm']
        return 'Boxes', '(Name="%s",%s,Extents=(X=%.2f,Y=%.2f,Z=%.2f))' % (s['name'], tm, e[0], e[1], e[2])
    return 'Capsules', '(Name="%s",%s,Radius=%.2f,Length=%.2f)' % (s['name'], tm, loc['radius_cm'], loc['length_cm'])


def _collider_name(s):
    return 'MT_' + s['name']


def _controller(bp):
    return bp.get_controller_by_name('RigVMModel')


def _set_solver_key(ctl, node):
    """Give a Forwards-Solve node the solver key as literals (no data link across events, see author)."""
    _set(ctl, node + '.DynamicsSolverComponentKey.ElementKey.Type', 'Bone')
    _set(ctl, node + '.DynamicsSolverComponentKey.ElementKey.Name', SOLVER_OWNER)
    _set(ctl, node + '.DynamicsSolverComponentKey.Name', SOLVER_COMPONENT)


def _require(cond, what):
    if not cond:
        raise RuntimeError('KHAIDYN ' + what)


def _set(ctl, pin, text):
    """Set a pin default, or raise. The controller refuses a pin that does not exist or a value it cannot import
    (RigVMController.cpp SetPinDefaultValue): unchecked, an engine field rename drops the value and the rig runs on
    the engine default (5.9 renamed Drag.LinearDragMultiplier, which re-enabled the native linear drag)."""
    _require(ctl.set_pin_default_value(pin, text, True, False, False), 'set %s = %s refused' % (pin, text))


def _unit(ctl, struct, pos, name):
    """Add a unit node, or raise: a renamed or removed struct adds nothing (research/engine-identifier-table.md)."""
    _require(ctl.add_unit_node_from_struct_path(struct, 'Execute', pos, name, False, False),
             'add %s (%s) refused' % (name, struct))


def _var(ctl, var, cpp_type, cpp_object, getter, pos, name):
    """Add a variable get/set node, or raise."""
    _require(ctl.add_variable_node(var, cpp_type, cpp_object, getter, '', pos, name, False, False),
             'add variable node %s (%s) refused' % (name, var))


COLLIDER_UNIT = DYN_UNIT + 'RigUnit_SpawnDynamicsCollider'
CHAINS_UNIT = DYN_UNIT + 'RigUnit_SpawnDynamicsChains'

# Air and motion scale (docs/specs/2026-10-27-dynamics-air-and-motion-scale-design.md). The rig owns the LINEAR frame coupling: the native terms are off, and every frame the rig feeds the
# solver its own current-frame velocity v and acceleration a, scaled per axis in the character's frame by
# M = R diag(S) R^-1 (quaternion unrotate -> multiply -> rotate: no Euler angles, so turns cannot wind). The solver's own
# readback (GetDynamicsSolverData) is one update stale, so correcting the native terms would let the full jolt through
# at a hard stop. Over-cranking (S > 1) and negative S are always allowed: nothing here clamps them.
# The public inputs. Defaults reproduce the native coupling with no wind: with the native linear terms off, the gate
# passed 185/185 against the pre-change baseline with no rebaseline, and the rig's own speed read 500.0 cm/s against
# 499.9 recorded travel. Swept 2026-10-27 (dyn_run, medians: cloth-chain trail / hood flutter / hood settle; no
# resets in any run): defaults 32.3 deg / 3.40 cm / 0.53 s; MotionScale 0 10.3 / 5.74 / 0.63; 0.5 22.2 / 4.77 / 0.58;
# 2 41.3 / 2.75 / 0.50; Forward -0.5 -1.2 (tails -27.7: leading) / 5.24 / 0.67; Forward 0 = MotionScale 0 and Up 0 =
# defaults (the drive runs straight on flat ground); WindVelocity 300 cm/s along the run 20.0 / 5.08 / 0.73, standing
# feathers 6.1 cm downwind; SpeedGust 0.5 28.7 / 3.90 / 0.53, 1.0 24.5 / 4.50 / 0.55, 1.5 21.4 / 5.14 / 0.55, 2.0 18.9 /
# 5.81 / 0.55. The gusts reach every part with drag: at 1.0 the cloth chains flutter 6.9 cm (3.5 at 0).
AIR_VARS = {'MotionScale': 1.0, 'MotionScaleUp': 1.0, 'MotionScaleSide': 1.0, 'MotionScaleForward': 1.0,
            'WindVelocity': (0.0, 0.0, 0.0), 'WindGust': 0.0, 'SpeedGust': 0.0}
# Component axes of (Side, Forward, Up). Forward = component +Y, measured: mean component-space travel (0, 1) on both
# pawns of khai_dyn_st_rep.json (mesh yaw -90, the UE skeletal-mesh convention). The scale is unsigned per axis.
AXIS_OF = {'MotionScaleSide': 'X', 'MotionScaleForward': 'Y', 'MotionScaleUp': 'Z'}
# One SpaceMotion table for SpawnSolver and the per-frame setter (SetDynamicsSolverSpaceMotion replaces the whole
# struct, RigDynamicsSolverExecution.cpp:43), so the two cannot drift. Every field not named keeps the engine default.
#
# The air graph below computes the ether itself (wind - S v, the motion scaled per axis in the character's frame) and
# hands it to the solver as external velocity. 5.8 added external velocity to the ether 1:1 (5.8
# RigParticleSimulation.cpp:102-106). 5.9 treats it as MORE MOTION OF THE SIMULATION SPACE: ether =
# -(space velocity + external) x LinearDampingMultiplier (5.9 RigParticleSimulation.cpp:103-114). With the multiplier
# at 0, as on 5.8, the authored air was multiplied away and every hair part trailed less (Aurora's first 5.9 gate, up
# to 42% lower). So on 5.9: the native space velocity is zeroed (LinearMotionScale 0, which also zeroes its linear
# acceleration; LinearEulerAmount was already 0, as AirField supplies it), the multiplier is 1, and the air graph's
# ether and turbulence are NEGATED into the external slots (AirExtNeg, AirTurbNeg), giving back 5.8's ether.
# ComponentTransformSource Live is 5.8's sampling (the execute context's transform, 5.8 RigDynamicsSolver.cpp:1129),
# the same transform the air graph reads; 5.9's default StartOfFrameSnapshot lags it a frame.
SPACE_MOTION = {'Drag.AdditionalDamping': 0.0, 'Drag.LinearDampingMultiplier': 1.0,
                'Drag.ExternalVelocitySpace': 'World', 'LinearMotionScale': (0.0, 0.0, 0.0),
                'ComponentTransformSource': 'Live',
                'InertialForces.LinearEulerAmount': 0.0}
# The inertial correction's field: radii so large the default falling multiplier curves (1 at the centre, 0 at the
# surface, RigDynamicsExecutionHelpers.h:303) are >= 0.9999 for every particle within 2 m of the origin.
FIELD_RADIUS_CM = 1.0e6
MATH = '/Script/RigVM.RigVMFunction_Math'
AIR_NODES = [   # (node, struct path), evaluated by data pull; the two mutable units are chained on Forwards Solve
    ('AirDt', '/Script/RigVM.RigVMFunction_GetDeltaTime'),
    ('AirDtSafe', MATH + 'FloatMax'), ('AirInvDt', MATH + 'FloatDiv'),
    ('AirPos', CR_UNIT + 'RigUnit_ToWorldSpace_Location'),
    ('AirDPos', '/Script/RigVM.RigVMFunction_DeltaFromPreviousVector'),
    ('AirStep', MATH + 'VectorLength'), ('AirTele', MATH + 'FloatGreater'), ('AirTeleF', BOOL_TO_FLOAT),
    ('AirTelePrev', '/Script/RigVM.RigVMFunction_DeltaFromPreviousFloat'),
    ('AirKeepV', MATH + 'FloatSub'), ('AirKeepPrev', MATH + 'FloatSub'), ('AirKeepA', MATH + 'FloatMul'),
    ('AirDtOk', MATH + 'FloatGreater'), ('AirDtOkF', BOOL_TO_FLOAT),
    ('AirKeepVDt', MATH + 'FloatMul'), ('AirKeepADt', MATH + 'FloatMul'),
    ('AirVelGain', MATH + 'FloatMul'), ('AirVel', MATH + 'VectorScale'),
    ('AirDVel', '/Script/RigVM.RigVMFunction_DeltaFromPreviousVector'),
    ('AirAccGain', MATH + 'FloatMul'), ('AirAcc', MATH + 'VectorScale'),
    ('AirRot', CR_UNIT + 'RigUnit_ToWorldSpace_Rotation'), ('AirRotInv', MATH + 'QuaternionInverse'),
    ('AirVelL', MATH + 'QuaternionRotateVector'), ('AirAccL', MATH + 'QuaternionRotateVector'),
    # S = MotionScale x (Side e_X + Forward e_Y + Up e_Z): MathVectorMake is deprecated in 5.8 (MathVector.h:95)
    ('AirAxisMotionScaleSide', MATH + 'VectorScale'), ('AirAxisMotionScaleForward', MATH + 'VectorScale'),
    ('AirAxisMotionScaleUp', MATH + 'VectorScale'), ('AirAxesXY', MATH + 'VectorAdd'), ('AirAxes', MATH + 'VectorAdd'),
    ('AirS', MATH + 'VectorScale'),
    ('AirSVelL', MATH + 'VectorMul'), ('AirSAccL', MATH + 'VectorMul'),
    ('AirSVelW', MATH + 'QuaternionRotateVector'), ('AirExt', MATH + 'VectorSub'),
    ('AirFieldDir', MATH + 'VectorNegate'), ('AirFieldMag', MATH + 'VectorLength'),
    ('AirFlat', MATH + 'VectorMul'), ('AirSpeedH', MATH + 'VectorLength'), ('AirSpeed', MATH + 'VectorLength'),
    ('AirGustSpeed', MATH + 'FloatMul'), ('AirGust', MATH + 'FloatAdd'), ('AirTurb', MATH + 'VectorFromFloat'),
    ('AirExtNeg', MATH + 'VectorNegate'), ('AirTurbNeg', MATH + 'VectorNegate'),   # 5.9's external sign (SPACE_MOTION)
    ('SetMotion', DYN_UNIT + 'RigUnit_HierarchySetDynamicsSolverSpaceMotion'),
    ('AirField', DYN_UNIT + 'RigUnit_HierarchyAddDynamicsParticleForceField'),
    ('SetFrameSpeed', CR_UNIT + 'RigUnit_SetCurveValue'),
]
AIR_LINKS = [
    # dt, guarded: v and a are held at zero on a zero-time evaluation. Its position delta is NOT
    # zero (Delta = Cache - Value always; only the cache waits for dt > 0, DeltaFromPrevious.cpp:34-40), so without the
    # hold a 1 cm move read as 10,000 cm/s.
    ('AirDt.Result', 'AirDtSafe.A'), ('AirDtSafe.Result', 'AirInvDt.B'),
    ('AirDt.Result', 'AirDtOk.A'), ('AirDtOk.Result', 'AirDtOkF.Value'),
    ('AirKeepV.Result', 'AirKeepVDt.A'), ('AirDtOkF.Result', 'AirKeepVDt.B'),
    ('AirKeepA.Result', 'AirKeepADt.A'), ('AirDtOkF.Result', 'AirKeepADt.B'),
    # v = -(previous - current)/dt: DeltaFromPrevious returns previous - current (RigVMFunction_DeltaFromPrevious.cpp:35)
    ('AirPos.World', 'AirDPos.Value'), ('AirDPos.Delta', 'AirStep.Value'), ('AirStep.Result', 'AirTele.A'),
    ('AirTele.Result', 'AirTeleF.Value'), ('AirTeleF.Result', 'AirKeepV.B'), ('AirTeleF.Result', 'AirTelePrev.Value'),
    ('AirTelePrev.PreviousValue', 'AirKeepPrev.B'), ('AirKeepV.Result', 'AirKeepA.A'),
    ('AirKeepPrev.Result', 'AirKeepA.B'),
    ('AirInvDt.Result', 'AirVelGain.A'), ('AirKeepVDt.Result', 'AirVelGain.B'),
    ('AirDPos.Delta', 'AirVel.Value'), ('AirVelGain.Result', 'AirVel.Factor'),
    # a = -(v_prev - v)/dt, held off on a teleport frame and the frame after (the native UpdatesSinceReset rule)
    ('AirVel.Result', 'AirDVel.Value'), ('AirInvDt.Result', 'AirAccGain.A'), ('AirKeepADt.Result', 'AirAccGain.B'),
    ('AirDVel.Delta', 'AirAcc.Value'), ('AirAccGain.Result', 'AirAcc.Factor'),
    # into the character's frame by R^-1, scale per axis, drag back out by R
    ('AirRot.World', 'AirRotInv.Value'), ('AirRotInv.Result', 'AirVelL.Transform'), ('AirVel.Result', 'AirVelL.Vector'),
    ('AirRotInv.Result', 'AirAccL.Transform'), ('AirAcc.Result', 'AirAccL.Vector'),
    ('AirAxisMotionScaleSide.Result', 'AirAxesXY.A'), ('AirAxisMotionScaleForward.Result', 'AirAxesXY.B'),
    ('AirAxesXY.Result', 'AirAxes.A'), ('AirAxisMotionScaleUp.Result', 'AirAxes.B'), ('AirAxes.Result', 'AirS.Value'),
    ('AirVelL.Result', 'AirSVelL.A'), ('AirS.Result', 'AirSVelL.B'),
    ('AirAccL.Result', 'AirSAccL.A'), ('AirS.Result', 'AirSAccL.B'),
    ('AirRot.World', 'AirSVelW.Transform'), ('AirSVelL.Result', 'AirSVelW.Vector'),
    ('AirSVelW.Result', 'AirExt.B'),                                         # ether = wind - M v
    ('AirSAccL.Result', 'AirFieldDir.Value'), ('AirSAccL.Result', 'AirFieldMag.Value'),   # pseudo-force -S (R^-1 a)
    # gusts: ambient + gain x horizontal speed, the same on every axis
    ('AirVel.Result', 'AirFlat.A'), ('AirFlat.Result', 'AirSpeedH.Value'), ('AirSpeedH.Result', 'AirGustSpeed.A'),
    ('AirGustSpeed.Result', 'AirGust.A'), ('AirGust.Result', 'AirTurb.Value'),
    ('AirExt.Result', 'AirExtNeg.Value'), ('AirExtNeg.Result', 'SetMotion.SpaceMotion.Drag.ExternalLinearVelocity'),
    ('AirTurb.Result', 'AirTurbNeg.Value'),
    ('AirTurbNeg.Result', 'SetMotion.SpaceMotion.Drag.ExternalTurbulenceVelocity'),
    ('AirFieldDir.Result', 'AirField.LinearForceDirection'), ('AirFieldMag.Result', 'AirField.LinearForce'),
    ('AirVel.Result', 'AirSpeed.Value'), ('AirSpeed.Result', 'SetFrameSpeed.Value'),
]
# (variable, node pin): the public inputs
AIR_VAR_LINKS = [('MotionScale', 'AirS.Factor'), ('WindVelocity', 'AirExt.A'), ('WindGust', 'AirGust.B'),
                 ('SpeedGust', 'AirGustSpeed.B')] + [(v, 'AirAxis%s.Factor' % v) for v in AXIS_OF]


def _literal(value):
    if isinstance(value, bool):
        return 'True' if value else 'False'
    if isinstance(value, (tuple, list)):
        return '(X=%r,Y=%r,Z=%r)' % tuple(float(c) for c in value)
    return str(value)


def _author_air(bp, ctl, air, teleport_cm):
    """The air/motion-scale nodes, between Forwards Solve and StepSolver (see AIR_NODES). `air` overrides AIR_VARS."""
    values = dict(AIR_VARS, **(air or {}))
    unknown = sorted(set(values) - set(AIR_VARS))
    _require(not unknown, 'unknown air inputs: %s' % unknown)
    for name, default in values.items():
        vtype = '/Script/CoreUObject.Vector' if isinstance(default, (tuple, list)) else 'float'
        _require(bp.add_member_variable(name, vtype, True, False, _literal(default)), 'add_member_variable %s' % name)
    cpp = {str(v.get_editor_property('name')): v for v in bp.get_member_variables()}
    for i, (node, struct) in enumerate(AIR_NODES):
        _unit(ctl, struct, unreal.Vector2D(-1800.0 + 160.0 * (i % 12), 600.0 + 160.0 * (i // 12)), node)
    for name, pin in AIR_VAR_LINKS:
        get = 'AirGet' + name
        v = cpp[name]
        _var(ctl, name, str(v.get_editor_property('cpp_type')), v.get_editor_property('cpp_type_object'),
             True, unreal.Vector2D(-2100.0, 600.0), get)
        _require(ctl.add_link(get + '.Value', pin, False), 'link %s -> %s' % (get, pin))
    for src, dst in AIR_LINKS:
        _require(ctl.add_link(src, dst, False), 'link %s -> %s' % (src, dst))
    # Both at UE_SMALL_NUMBER (UnrealMathUtility.h:130), the threshold DeltaFromPrevious advances its caches at: an
    # evaluation is either held (no cache update, v = a = 0) or measured with its true dt, never measured as held
    # while the caches advance (5e-7 s then read as a 30,000 cm/s^2 jolt).
    for pin, value in (('AirDtSafe.B', 1.0e-8), ('AirDtOk.B', 1.0e-8), ('AirInvDt.A', -1.0), ('AirTele.B', teleport_cm),
                       ('AirKeepV.A', 1.0), ('AirKeepPrev.A', 1.0), ('AirFlat.B', (1.0, 1.0, 0.0)),
                       ('AirRot.Value', '(X=0.0,Y=0.0,Z=0.0,W=1.0)'), ('AirPos.Value', (0.0, 0.0, 0.0)),
                       ('AirField.Space', 'Component'), ('AirField.Type', 'Acceleration'),
                       ('AirField.FieldTransform', '(Rotation=(X=0.0,Y=0.0,Z=0.0,W=1.0),Translation=(X=0.0,Y=0.0,Z=0.0),'
                                                   'Scale3D=(X=%r,Y=%r,Z=%r))' % ((FIELD_RADIUS_CM,) * 3)),
                       ('SetFrameSpeed.Curve', 'MT_DynFrameSpeed')) + tuple(
            ('AirAxis%s.Value' % v, tuple(float(a == c) for c in 'XYZ')) for v, a in AXIS_OF.items()):
        _set(ctl, pin, _literal(value))
    _set_solver_key(ctl, 'SetMotion')
    _set_solver_key(ctl, 'AirField')
    # Forwards Solve -> SetMotion -> AirField -> SetFrameSpeed -> StepSolver
    _require(ctl.break_link('ForwardsSolve.ExecutePin', 'StepSolver.ExecutePin', False), 'unlink ForwardsSolve -> Step')
    prev = 'ForwardsSolve'
    for node in ('SetMotion', 'AirField', 'SetFrameSpeed', 'StepSolver'):
        _require(ctl.add_link(prev + '.ExecutePin', node + '.ExecutePin', False), 'exec %s -> %s' % (prev, node))
        prev = node


# Group modifiers (docs/specs/2026-10-28-dynamics-group-modifiers-design.md): a behaviour with a 'flutter'
# entry is a group. Its inputs are public rig variables named <Behaviour><Input> (feather -> FeatherFlutterRate). Flutter pushes
# every NON-ROOT particle of the group (a chain root is kinematic, RigDynamicsSolver.cpp:301-307, and ignores a force)
# with its own Perlin noise, so the group flutters at its own rate and the parts stay independent of each other:
#   a_i = (FlutterAmbient + FlutterSpeedGain x |v_xy|) x noise_i(t),  noise_i in (-1, 1)^3 advancing FlutterRate/s
# as a world-space acceleration (AddDynamicsParticleForce, RigDynamicsSolver.cpp:600-651). Nothing is clamped: Rate 0
# holds a constant push, Ambient > 0 keeps exciting the parts after the character stops (both by design). On Khaimera
# the feather behaviour is exactly the 33 hood feathers.
GROUP_INPUTS = _bh.FLUTTER
# Swept 2026-10-28 (dyn_run, hood medians: flutter cm / flutter_hz / settle s (worst); no resets; every non-hood
# part's trail, stop swing, tip delta, max step and clearance identical to the amplitude-0 run in every row):
#   gain 0 (parity, gate PASS 185/185 on the old baseline) 3.40 / 2.63 / 0.53 (0.72); SpeedGust 1.0 4.50 / 2.10 /
#   0.55 (non-hood parts moved up to 22.8). Spring 1.5/0.1: rate 2 gain 1 3.55 / 2.45; rate 4 g1 3.86 / 2.48;
#   r8 g1 3.43 / 2.62; r4 g2 4.56 / 2.34; r8 g2 3.65 / 2.62 (all 0.53-0.55 s). Spring 2.5/0.15 (0.30 s, worst 0.35-0.38):
#   r4 g2 2.93 / 3.00; r8 g2 2.75 / 3.27; r8 g3 3.08 / 3.22; r4 g5 4.47 / 2.57; r6 g5 4.26 / 2.96 <- chosen;
#   r4 g7 5.47 / 2.47. Spring 3.5/0.15: r6 g7 3.53 / 3.25 / 0.17 (0.25). The spring sets the flutter rate; a faster
#   noise is mostly filtered out and costs swing. Hood clearance -0.08 cm in every row (as at parity). Shipped as the
#   feather behaviour's flutter (dynamics_behaviours.json).
PARTICLE_COMPONENT = 'DynamicsParticle'   # FRigDynamicsParticleComponent::GetDefaultName, RigDynamicsParticleComponent.h:37
FORCE_UNIT = DYN_UNIT + 'RigUnit_HierarchyAddDynamicsParticleForce'
NOISE_UNIT = '/Script/RigVM.RigVMFunction_NoiseVector2'
# Perlin1D is already (-1, 1) (UnrealMath.cpp:3625) and the node maps Lerp(Min, Max, Perlin + 0.5), so Min/Max of
# -0.5/0.5 pass it through unscaled; -1/1 would double it.
NOISE_RANGE = (-0.5, 0.5)


def group_var(group, name):
    return ''.join(w[:1].upper() + w[1:] for w in group.replace('.', '_').split('_')) + name


def noise_seed(name):
    """Per-particle, per-axis offsets into Perlin1D's 256-unit period, from the particle's NAME (stable when the part
    list changes). Phase offsets of one periodic function, not independent noise: parts decorrelate, they are not
    random."""
    import zlib
    return tuple(round((zlib.crc32(('%s/%d' % (name, k)).encode()) % 2560000) / 10000.0 + 0.5, 4) for k in range(3))


def _free_particles(bp, roots, chains):
    """(element type, name) of every particle a group's force can move: each root's descendants, Nulls (the tails)
    included, in hierarchy order. The roots themselves are kinematic."""
    out = []
    for root in roots:
        key = unreal.RigElementKey(type=unreal.RigElementType.BONE, name=root)
        for child in bp.hierarchy.get_children(key, True):
            kind = {unreal.RigElementType.BONE: 'Bone', unreal.RigElementType.NULL: 'Null'}.get(child.type)
            if kind:
                out.append((kind, str(child.name)))
    return out


def _author_groups(bp, ctl, chains, lib):
    """Per-behaviour flutter, chained AirField -> one force per free particle -> SetFrameSpeed, for every behaviour
    with a 'flutter' entry that this hero's parts use (a library behaviour no part uses adds nothing)."""
    table = {b: v['flutter'] for b, v in lib['behaviours'].items() if v.get('flutter')}
    members = {}
    for root, spec in sorted(chains.items()):
        if spec['behaviour'] in table:
            members.setdefault(spec['behaviour'], []).append(root)
    names = [group_var(g, i) for g in members for i in GROUP_INPUTS]
    taken = {str(v.get_editor_property('name')) for v in bp.get_member_variables()}
    _require(len(set(names)) == len(names) and not taken & set(names), 'group input names collide: %s' % names)
    # One owner per particle: overlapping roots, or two groups reaching one particle, would push it twice.
    owner = {}
    for g, roots in members.items():
        for p in _free_particles(bp, roots, chains):
            _require(p not in owner, 'particle %s claimed twice (%s, %s)' % (p, owner.get(p), g))
            owner[p] = g
    _require(ctl.break_link('AirField.ExecutePin', 'SetFrameSpeed.ExecutePin', False), 'unlink AirField -> SetFrameSpeed')
    prev, n = 'AirField', 0
    for gi, (g, roots) in enumerate(sorted(members.items())):
        for name in GROUP_INPUTS:
            var = group_var(g, name)
            _require(bp.add_member_variable(var, 'float', True, False, _literal(table[g][name])),
                     'add_member_variable %s' % var)
        y = 2400.0 + 600.0 * gi
        cpp = {str(v.get_editor_property('name')): v for v in bp.get_member_variables()}
        for name in GROUP_INPUTS:
            var = group_var(g, name)
            _var(ctl, var, str(cpp[var].get_editor_property('cpp_type')), None, True, unreal.Vector2D(-2100.0, y),
                 'Get' + var)
        rate, gain, amp = 'G%dRate' % gi, 'G%dGain' % gi, 'G%dAmp' % gi
        for node, struct in ((rate, MATH + 'VectorFromFloat'), (gain, MATH + 'FloatMul'), (amp, MATH + 'FloatAdd')):
            _unit(ctl, struct, unreal.Vector2D(-1800.0, y), node)
        for src, dst in (('Get%s.Value' % group_var(g, 'FlutterRate'), rate + '.Value'),
                         ('AirSpeedH.Result', gain + '.A'), ('Get%s.Value' % group_var(g, 'FlutterSpeedGain'), gain + '.B'),
                         (gain + '.Result', amp + '.A'), ('Get%s.Value' % group_var(g, 'FlutterAmbient'), amp + '.B')):
            _require(ctl.add_link(src, dst, False), 'link %s -> %s' % (src, dst))
        particles = _free_particles(bp, roots, chains)
        _require(particles, 'group %s has no free particle' % g)
        for etype, ename in particles:
            noise, scale, force = 'Flut%d' % n, 'FlutA%d' % n, 'FlutF%d' % n
            pos = unreal.Vector2D(-1500.0 + 160.0 * (n % 12), y + 160.0 * (n // 12 % 3))
            _unit(ctl, NOISE_UNIT, pos, noise)
            _unit(ctl, MATH + 'VectorScale', pos, scale)
            _unit(ctl, FORCE_UNIT, pos, force)
            for src, dst in ((rate + '.Result', noise + '.Speed'), (noise + '.Result', scale + '.Value'),
                             (amp + '.Result', scale + '.Factor'), (scale + '.Result', force + '.Force')):
                _require(ctl.add_link(src, dst, False), 'link %s -> %s' % (src, dst))
            for pin, value in ((noise + '.Value', noise_seed(ename)), (noise + '.Frequency', (1.0, 1.0, 1.0)),
                               (noise + '.Minimum', NOISE_RANGE[0]), (noise + '.Maximum', NOISE_RANGE[1]),
                               (force + '.Space', 'World'), (force + '.Type', 'Acceleration'),
                               (force + '.DynamicsParticleComponentKey.ElementKey.Type', etype),
                               (force + '.DynamicsParticleComponentKey.ElementKey.Name', ename),
                               (force + '.DynamicsParticleComponentKey.Name', PARTICLE_COMPONENT)):
                _set(ctl, pin, _literal(value))
            _require(ctl.add_link(prev + '.ExecutePin', force + '.ExecutePin', False), 'exec %s -> %s' % (prev, force))
            prev, n = force, n + 1
    _require(ctl.add_link(prev + '.ExecutePin', 'SetFrameSpeed.ExecutePin', False), 'exec %s -> SetFrameSpeed' % prev)
    # Read every force's key, type and noise settings back: a force whose key misses is silent at runtime
    # (RigDynamicsParticleExecution.cpp:472), so a default that did not take must fail the build.
    graph = ctl.get_graph()
    i = 0
    for g, roots in sorted(members.items()):
        for etype, ename in _free_particles(bp, roots, chains):
            want = {'FlutF%d.DynamicsParticleComponentKey' % i: '(ElementKey=(Type=%s,Name="%s"),Name="%s")'
                    % (etype, ename, PARTICLE_COMPONENT),
                    'FlutF%d.Type' % i: 'Acceleration', 'FlutF%d.Space' % i: 'World'}
            for pin, value in want.items():
                got = graph.find_pin(pin)
                _require(got is not None and got.get_default_value() == value,
                         'pin %s is %s, not %s' % (pin, got.get_default_value() if got else 'MISSING', value))
            for pin, value in (('Flut%d.Minimum' % i, NOISE_RANGE[0]), ('Flut%d.Maximum' % i, NOISE_RANGE[1])):
                got = graph.find_pin(pin)
                _require(got is not None and abs(float(got.get_default_value()) - value) < 1e-6,
                         'pin %s is %s, not %s' % (pin, got.get_default_value() if got else 'MISSING', value))
            i += 1
    return {g: len(_free_particles(bp, r, chains)) for g, r in members.items()}


# Twist modifier (docs/specs/2026-10-28-dynamics-heavy-cloth-and-twist-design.md; the
# motion showed no twist). The solver has no twist: a bone with one particle child is turned by the minimal arc from its
# animated direction to the particle's (RigDynamicsSolver.cpp:849-900). A flat strip cannot bend within its own plane,
# so for every bone of a behaviour with a 'twist' entry that has a face normal in the profile (intake: face_normal):
#   a = animated direction (bone -> next particle), from transforms STORED before Step (a pure read wired after Step
#       would see the solved pose); s = solved direction; N = the animated face normal made perpendicular to a;
#   w = N x a (the strip's width); beta = atan2(s.w, s.a), the in-plane bend;
#   roll = clamp(TwistGain x beta x clamp(s.a, 0, 1), +/-TwistMax deg) about s, on the solved global rotation, not
#   propagated: a roll about s keeps the next particle where the solver put it.
# At alpha 0 (pass-through) s = a, so the roll is 0. TwistGain may be negative or above 1.
TWIST_INPUTS = _bh.TWIST
TRANSFORM_TYPE = '/Script/CoreUObject.Transform'
GET_UNIT = CR_UNIT + 'RigUnit_GetTransform'
SET_ROT_UNIT = CR_UNIT + 'RigUnit_SetRotation'


def _twist_bones(bp, chains, table):
    """[(behaviour, bone, next element (type, name), normal_local)] for every bone of a twist behaviour's parts that has
    a face normal and exactly one particle child in the rig (the chain search takes every descendant of a root,
    Nulls included, RigDynamicsExecutionHelpers.cpp:173)."""
    out = []
    for root, spec in sorted(chains.items()):
        if spec['behaviour'] not in table:
            continue
        for bone, face in sorted(spec.get('faces', {}).items()):
            key = unreal.RigElementKey(type=unreal.RigElementType.BONE, name=bone)
            kids = [(({unreal.RigElementType.BONE: 'Bone', unreal.RigElementType.NULL: 'Null'}.get(c.type)), str(c.name))
                    for c in bp.hierarchy.get_children(key, False)]
            kids = [k for k in kids if k[0]]
            _require(len(kids) == 1, 'twist bone %s on %s has %d particle children %s (need 1)'
                     % (bone, root, len(kids), kids))
            out.append((spec['behaviour'], bone, kids[0], face['normal_local']))
    return out


def _author_twist(bp, ctl, chains, lib):
    """Pre-Step snapshots chained SetFrameSpeed -> ... -> StepSolver, the roll after StepSolver, for every behaviour with
    a 'twist' entry this hero's parts use. Returns {behaviour: bones twisted}."""
    table = {b: v['twist'] for b, v in lib['behaviours'].items() if v.get('twist')}
    bones = _twist_bones(bp, chains, table)
    groups = sorted({b for b, _, _, _ in bones})
    if not bones:
        return {}
    names = [group_var(g, i) for g in groups for i in TWIST_INPUTS]
    taken = {str(v.get_editor_property('name')) for v in bp.get_member_variables()}
    _require(len(set(names)) == len(names) and not taken & set(names), 'twist input names collide: %s' % names)
    for g in groups:
        for name in TWIST_INPUTS:
            _require(bp.add_member_variable(group_var(g, name), 'float', True, False, _literal(table[g][name])),
                     'add_member_variable %s' % group_var(g, name))
    for k in range(len(bones)):
        for v in ('TwSnapB%d' % k, 'TwSnapN%d' % k):
            _require(bp.add_member_variable(v, TRANSFORM_TYPE, False, False, ''), 'add_member_variable %s' % v)
    cpp = {str(v.get_editor_property('name')): v for v in bp.get_member_variables()}

    def var_node(var, getter, node, pos):
        v = cpp[var]
        _var(ctl, var, str(v.get_editor_property('cpp_type')), v.get_editor_property('cpp_type_object'), getter, pos,
             node)

    def unit(struct, node, pos):
        _unit(ctl, struct, pos, node)

    def link(src, dst):
        _require(ctl.add_link(src, dst, False), 'link %s -> %s' % (src, dst))

    def pin(path, value):
        _set(ctl, path, _literal(value))

    # Per behaviour: the limit in radians, and its negative.
    for gi, g in enumerate(groups):
        y = 4200.0 + 300.0 * gi
        for name in TWIST_INPUTS:
            var_node(group_var(g, name), True, 'TwGet%s%d' % (name, gi), unreal.Vector2D(-2100.0, y))
        # |TwistMax|: the variable is public, and a negative limit set at runtime would invert the clamp.
        unit(MATH + 'FloatAbs', 'TwMaxA%d' % gi, unreal.Vector2D(-1950.0, y))
        unit(MATH + 'FloatRad', 'TwMaxR%d' % gi, unreal.Vector2D(-1800.0, y))
        unit(MATH + 'FloatNegate', 'TwMinR%d' % gi, unreal.Vector2D(-1650.0, y))
        link('TwGetTwistMax%d.Value' % gi, 'TwMaxA%d.Value' % gi)
        link('TwMaxA%d.Result' % gi, 'TwMaxR%d.Value' % gi)
        link('TwMaxR%d.Result' % gi, 'TwMinR%d.Value' % gi)

    def item(node, etype, ename):
        pin(node + '.Item.Type', etype)
        pin(node + '.Item.Name', ename)
        pin(node + '.Space', 'GlobalSpace')

    _require(ctl.break_link('SetFrameSpeed.ExecutePin', 'StepSolver.ExecutePin', False), 'unlink SetFrameSpeed -> Step')
    first_witness = WITNESS_WRITES[0][0]
    _require(ctl.break_link('StepSolver.ExecutePin', first_witness + '.ExecutePin', False),
             'unlink StepSolver -> %s' % first_witness)
    pre, post = 'SetFrameSpeed', 'StepSolver'
    for k, (g, bone, (ntype, nname), normal) in enumerate(bones):
        gi = groups.index(g)
        y = 5000.0 + 400.0 * k
        P = lambda col: unreal.Vector2D(-2000.0 + 160.0 * col, y)   # noqa: E731
        # Before Step: store the animated transforms of the bone and its next particle.
        for tag, (etype, ename) in (('B', ('Bone', bone)), ('N', (ntype, nname))):
            unit(GET_UNIT, 'TwPre%s%d' % (tag, k), P(0))
            item('TwPre%s%d' % (tag, k), etype, ename)
            var_node('TwSnap%s%d' % (tag, k), False, 'TwSet%s%d' % (tag, k), P(1))
            link('TwPre%s%d.Transform' % (tag, k), 'TwSet%s%d.Value' % (tag, k))
            link(pre + '.ExecutePin', 'TwSet%s%d.ExecutePin' % (tag, k))
            pre = 'TwSet%s%d' % (tag, k)
            var_node('TwSnap%s%d' % (tag, k), True, 'TwAnim%s%d' % (tag, k), P(2))
            unit(GET_UNIT, 'TwPost%s%d' % (tag, k), P(3))
            item('TwPost%s%d' % (tag, k), etype, ename)
        n = 'Tw%d' % k
        for node, struct, col in (('A', 'VectorSub', 4), ('Au', 'VectorUnit', 5), ('S', 'VectorSub', 4),
                                  ('Su', 'VectorUnit', 5), ('N', 'QuaternionRotateVector', 4), ('Nd', 'VectorDot', 5),
                                  ('Ns', 'VectorScale', 6), ('Np', 'VectorSub', 7), ('Nu', 'VectorUnit', 8),
                                  ('W', 'VectorCross', 9), ('Sw', 'VectorDot', 10), ('Sa', 'VectorDot', 10),
                                  ('Beta', 'FloatAtan2', 11), ('Fade', 'FloatClamp', 11), ('G', 'FloatMul', 12),
                                  ('GF', 'FloatMul', 13), ('C', 'FloatClamp', 14), ('Q', 'QuaternionFromAxisAndAngle', 15),
                                  ('R', 'QuaternionMul', 16)):
            unit(MATH + struct, n + node, P(col))
        unit(SET_ROT_UNIT, n + 'Set', P(17))
        for src, dst in (('TwAnimN%d.Value.Translation' % k, n + 'A.A'), ('TwAnimB%d.Value.Translation' % k, n + 'A.B'),
                         (n + 'A.Result', n + 'Au.Value'),
                         ('TwPostN%d.Transform.Translation' % k, n + 'S.A'),
                         ('TwPostB%d.Transform.Translation' % k, n + 'S.B'), (n + 'S.Result', n + 'Su.Value'),
                         ('TwAnimB%d.Value.Rotation' % k, n + 'N.Transform'),
                         (n + 'N.Result', n + 'Nd.A'), (n + 'Au.Result', n + 'Nd.B'),
                         (n + 'Au.Result', n + 'Ns.Value'), (n + 'Nd.Result', n + 'Ns.Factor'),
                         (n + 'N.Result', n + 'Np.A'), (n + 'Ns.Result', n + 'Np.B'), (n + 'Np.Result', n + 'Nu.Value'),
                         (n + 'Nu.Result', n + 'W.A'), (n + 'Au.Result', n + 'W.B'),
                         (n + 'Su.Result', n + 'Sw.A'), (n + 'W.Result', n + 'Sw.B'),
                         (n + 'Su.Result', n + 'Sa.A'), (n + 'Au.Result', n + 'Sa.B'),
                         (n + 'Sw.Result', n + 'Beta.A'), (n + 'Sa.Result', n + 'Beta.B'),
                         (n + 'Sa.Result', n + 'Fade.Value'),
                         (n + 'Beta.Result', n + 'G.A'), ('TwGetTwistGain%d.Value' % gi, n + 'G.B'),
                         (n + 'G.Result', n + 'GF.A'), (n + 'Fade.Result', n + 'GF.B'),
                         (n + 'GF.Result', n + 'C.Value'), ('TwMinR%d.Result' % gi, n + 'C.Minimum'),
                         ('TwMaxR%d.Result' % gi, n + 'C.Maximum'),
                         (n + 'Su.Result', n + 'Q.Axis'), (n + 'C.Result', n + 'Q.Angle'),
                         (n + 'Q.Result', n + 'R.A'), ('TwPostB%d.Transform.Rotation' % k, n + 'R.B'),
                         (n + 'R.Result', n + 'Set.Value')):
            link(src, dst)
        pin(n + 'N.Vector', tuple(normal))
        pin(n + 'Fade.Minimum', 0.0)
        pin(n + 'Fade.Maximum', 1.0)
        item(n + 'Set', 'Bone', bone)
        pin(n + 'Set.Weight', 1.0)
        pin(n + 'Set.bPropagateToChildren', False)
        link(post + '.ExecutePin', n + 'Set.ExecutePin')
        post = n + 'Set'
    link(pre + '.ExecutePin', 'StepSolver.ExecutePin')
    link(post + '.ExecutePin', first_witness + '.ExecutePin')
    # Read back the keys and flags: a SetRotation on a missing bone or with propagation on would ship a different rig.
    graph = ctl.get_graph()
    for k, (g, bone, (ntype, nname), _n) in enumerate(bones):
        for path, want in (('Tw%dSet.Item' % k, '(Type=Bone,Name="%s")' % bone),
                           ('Tw%dSet.bPropagateToChildren' % k, 'False'), ('Tw%dSet.Space' % k, 'GlobalSpace'),
                           ('TwPreN%d.Item' % k, '(Type=%s,Name="%s")' % (ntype, nname))):
            got = graph.find_pin(path)
            _require(got is not None and got.get_default_value() == want,
                     'pin %s is %s, not %s' % (path, got.get_default_value() if got else 'MISSING', want))
    return {g: [b for gg, b, _, _ in bones if gg == g] for g in groups}


def author(with_colliders=True, behaviours=None, air=None):
    """with_colliders=False builds the proven-red variant: the same rig with every collider removed.
    air: AIR_VARS defaults to override (experiments).
    behaviours: an override merged into the behaviour library (load_behaviours; experiments)."""
    lib = load_behaviours(behaviours)
    mesh = unreal.load_asset(MESH_PATH)
    _require(mesh is not None, '%s mesh not found at %s (licensed content absent?)' % (HERO['rig'], MESH_PATH))
    if unreal.EditorAssetLibrary.does_asset_exist(CR_PATH):
        _require(unreal.EditorAssetLibrary.delete_asset(CR_PATH), 'could not delete the previous %s' % CR_PATH)
    bp = unreal.ControlRigBlueprintFactory.create_new_control_rig_asset(CR_PATH, False)   # False = not modular
    _require(bp is not None and bp.get_path_name().split('.')[0] == CR_PATH,
             'factory returned %s, not %s (a uniquified name means the delete did not take)'
             % (bp.get_path_name() if bp else None, CR_PATH))

    hc = bp.get_hierarchy_controller()
    # From the SKELETON, not import_bones_from_skeletal_mesh: that one silently left out two of the mesh's 198 bones,
    # feather_02_r (a skinned hood feather) and jaw (measured 2026-10-27), so a part on them would have no rig bone.
    hc.import_bones(mesh.skeleton, '', True, True, False, False)
    bp.set_preview_mesh(mesh, True)
    # The solver's owner is the skeleton's root bone: 'root' on UE skeletons, else whatever bone has no parent.
    global SOLVER_OWNER
    tops = [str(k.name) for k in bp.hierarchy.get_bones() if not bp.hierarchy.get_parents(k)]
    _require(len(tops) == 1, 'the skeleton has %d root bones %s (need 1)' % (len(tops), tops))
    SOLVER_OWNER = tops[0]
    for curve in WITNESS_CURVES:
        hc.add_curve(curve, 0.0, False, False)
    # Chain tails (profile colliders.chains[*].tails): a Null at the end of the mesh that hangs past each leaf bone.
    # The chain search takes every descendant of a root, Nulls included (RigDynamicsExecutionHelpers.cpp:173), so the
    # tail becomes the chain's last particle: it collides, and the leaf bone is aimed at it (RigDynamicsSolver.cpp:896).
    shapes, chains = _autofit(lib)
    have = {str(k.name) for k in bp.hierarchy.get_bones()}
    absent = sorted(r for r in chains if r not in have)
    _require(not absent, 'part roots missing from the rig hierarchy: %s' % absent)
    for spec in chains.values():
        for t in spec.get('tails', []):
            key = hc.add_null(t['null'], unreal.RigElementKey(type=unreal.RigElementType.BONE, name=t['bone']),
                              unreal.Transform(location=unreal.Vector(*t['local'])), False, False, False)
            _require(key is not None and str(key.name) == t['null'], 'add_null %s returned %s' % (t['null'], key))
            # A missing parent bone gives a PARENTLESS Null with the right name (RigHierarchyController.cpp:467), which
            # the chain search then skips: assert the parent, not just the name.
            parent = bp.hierarchy.get_first_parent(key)
            _require(str(parent.name) == t['bone'], 'tail %s parented to %s, not %s' % (t['null'], parent.name, t['bone']))

    ctl = _controller(bp)
    for i, (name, struct) in enumerate(UNITS.items()):
        _unit(ctl, struct, unreal.Vector2D(300.0 * i, 0.0), name)
    for src, dst in (('Construction.ExecutePin', 'SpawnSolver.ExecutePin'),
                     ('ForwardsSolve.ExecutePin', 'StepSolver.ExecutePin')):
        _require(ctl.add_link(src, dst, False), 'link %s -> %s refused' % (src, dst))
    # FINDING: RigVM refuses a data link from a Construction node to a Forwards Solve node ("You cannot
    # combine nodes from Construction and Forwards Solve"), so Step cannot take Spawn's output key. A rig
    # component key is (owning element, component name): Spawn is given an explicit name and owner, and Step
    # is given the same key as literals, so the two can only drift by editing this one constant.
    _set(ctl, 'SpawnSolver.Owner.Type', 'Bone')
    _set(ctl, 'SpawnSolver.Owner.Name', SOLVER_OWNER)
    _set(ctl, 'SpawnSolver.SolverComponentName', SOLVER_COMPONENT)
    _set_solver_key(ctl, 'StepSolver')

    _require(bp.add_member_variable(ALPHA_VAR, 'float', True, False, '0.0'), 'add_member_variable %s' % ALPHA_VAR)
    _var(ctl, ALPHA_VAR, 'float', None, True, unreal.Vector2D(1200.0, 300.0), 'AlphaGet')
    _require(ctl.add_link('AlphaGet.Value', 'StepSolver.Alpha', False), 'link AlphaGet.Value -> StepSolver.Alpha')
    _set(ctl, 'TickCounter.Increment', '1.0')
    _set(ctl, 'TickCounter.InitialValue', '0.0')
    _set(ctl, 'TickCounter.bIntegrateDeltaTime', 'False')
    _set_solver_key(ctl, 'SolverData')
    prev = 'StepSolver'
    for node, curve, source in WITNESS_WRITES:
        _require(ctl.add_link(prev + '.ExecutePin', node + '.ExecutePin', False), 'exec %s -> %s' % (prev, node))
        _set(ctl, node + '.Curve', curve)
        if source.startswith('SolverData.'):   # the solver's flags are bool; RigVM will not link bool -> float
            cast = node + 'Cast'
            _unit(ctl, BOOL_TO_FLOAT, unreal.Vector2D(1800.0, 0.0), cast)
            _require(ctl.add_link(source, cast + '.Value', False), 'link %s -> %s.Value' % (source, cast))
            source = cast + '.Result'
        _require(ctl.add_link(source, node + '.Value', False), 'link %s -> %s.Value' % (source, node))
        prev = node

    for pin, value in SOLVER_SETTINGS.items():
        text = ('True' if value else 'False') if isinstance(value, bool) else str(value)
        _set(ctl, 'SpawnSolver.' + pin, text)

    prev = 'SpawnSolver'
    for i, sh in enumerate(shapes if with_colliders else []):
        node = 'Collider_%s' % sh['name']
        _unit(ctl, COLLIDER_UNIT, unreal.Vector2D(900.0 + 250.0 * i, -300.0), node)
        _require(ctl.add_link(prev + '.ExecutePin', node + '.ExecutePin', False), 'exec %s -> %s' % (prev, node))
        _require(ctl.add_link('SpawnSolver.DynamicsSolverComponentKey', node + '.DynamicsSolverComponentKey', False),
                 'solver key -> %s' % node)
        _set(ctl, node + '.Owner.Type', 'Bone')
        _set(ctl, node + '.Owner.Name', sh['owner'])
        _set(ctl, node + '.ColliderComponentName', _collider_name(sh))
        array, literal = _shape_literal(sh)
        elem = ctl.add_array_pin(node + '.Shapes.' + array, '', False, False)
        _require(bool(elem), 'add_array_pin on %s.Shapes.%s returned nothing' % (node, array))
        _set(ctl, elem, literal)
        prev = node
    # One SpawnDynamicsChains per chain, so each carries its own particle radius and its own exclusions: a chain
    # ignores every collider outside its profile `collides_with` list (FRigDynamicsParticleProperties::
    # NoCollisionColliders, RigDynamicsData.h:640-642), which is how a rest box binds only the chain it was made for.
    built = {sh['name'] for sh in shapes} if with_colliders else set()
    for i, (node, roots, spec) in enumerate(_af.spawners(chains)):
        _unit(ctl, CHAINS_UNIT, unreal.Vector2D(900.0 + 250.0 * i, 300.0), node)
        _require(ctl.add_link(prev + '.ExecutePin', node + '.ExecutePin', False), 'exec %s -> %s' % (prev, node))
        _require(ctl.add_link('SpawnSolver.DynamicsSolverComponentKey', node + '.DynamicsSolverComponentKey', False),
                 'solver key -> %s' % node)
        for root in roots:
            elem = ctl.add_array_pin(node + '.RootBones', '', False, False)
            _require(bool(elem), 'add_array_pin on %s.RootBones returned nothing' % node)
            _set(ctl, elem + '.Type', 'Bone')
            _set(ctl, elem + '.Name', root)
        pp = node + '.ParticleProperties.'
        _set(ctl, pp + 'Strength', str(TARGET_STRENGTH_HZ))
        _set(ctl, pp + 'Radius', str(spec['particle_radius_cm']))
        _set(ctl, pp + 'GravityMultiplier', str(GRAVITY_MULTIPLIER))
        # The air's drag is each particle's own Damping, not the solver's AdditionalDamping (which the engine adds to
        # every particle's, RigParticleSimulation.cpp:88): a behaviour's Damping states its whole drag, and can be none.
        native = dict({'Damping': AIR_DAMPING_HZ}, **(lib['behaviours'][spec['behaviour']].get('native') or {}))
        for prop, value in sorted(native.items()):
            _set(ctl, pp + prop, str(value))
            # Read back: a field that did not take must fail the build, not ship a different behaviour
            pin = ctl.get_graph().find_pin(pp + prop)
            got = pin.get_default_value() if pin else None
            same = got is not None and (got.lower() == str(value).lower() if isinstance(value, (bool, str))
                                        else abs(float(got) - float(value)) < 1e-4)
            _require(same, 'behaviour %s: %s%s is %s, not %s' % (spec['behaviour'], pp, prop, got, value))
        for curve, keys in sorted((lib['behaviours'][spec['behaviour']].get('chain_curves') or {}).items()):
            # FRuntimeFloatCurve text: the rich curve's keys; read back like every other pin, a curve that did not take
            # must fail the build.
            text = '(EditorCurveData=(Keys=(%s)),ExternalCurve=None)' % ','.join(
                '(Time=%f,Value=%f)' % (t, v) for t, v in keys)
            _set(ctl, node + '.ChainCurves.' + curve, text)
            pin = ctl.get_graph().find_pin(node + '.ChainCurves.' + curve)
            got = pin.get_default_value() if pin else ''
            _require(all(('Value=%f' % v)[:-3] in got for _t, v in keys),
                     'behaviour %s: %s.ChainCurves.%s is %s, not %s' % (spec['behaviour'], node, curve, got, keys))
        for sh in shapes:
            if sh['name'] in built and sh['name'] not in spec['collides_with']:
                e = ctl.add_array_pin(pp + 'NoCollisionColliders', '', False, False)
                _require(bool(e), 'add_array_pin on %sNoCollisionColliders returned nothing' % pp)
                _set(ctl, e, '(ElementKey=(Type=Bone,Name="%s"),Name="%s")'
                                          % (sh['owner'], _collider_name(sh)))
        prev = node

    _set(ctl, 'SpawnSolver.Settings.SimulationSpace', SIMULATION_SPACE)
    _author_air(bp, ctl, air, SOLVER_SETTINGS['TeleportDetection.PositionChangeThreshold'])
    _author_groups(bp, ctl, chains, lib)
    _author_twist(bp, ctl, chains, lib)
    for node in ('SpawnSolver', 'SetMotion'):
        for pin, value in SPACE_MOTION.items():
            _set(ctl, '%s.SpaceMotion.%s' % (node, pin), _literal(value))

    bp.recompile_vm()
    _mt.require_saved_gitignored(CR_PATH, 'dynamics_rig')
    return report()


def report():
    """Read the asset back, independently of what author() believes it wrote."""
    out = {'action': 'report', 'hero': HERO['rig'], 'path': CR_PATH,
           'exists': unreal.EditorAssetLibrary.does_asset_exist(CR_PATH)}
    if not out['exists']:
        unreal.log('KHAIDYN ' + json.dumps(out))
        return out
    bp = unreal.load_asset(CR_PATH)
    graph = _controller(bp).get_graph()
    nodes = {}
    for node in graph.get_nodes():
        struct = node.get_script_struct() if hasattr(node, 'get_script_struct') else None
        nodes[node.get_name()] = struct.get_path_name() if struct else node.get_class().get_name()
    out['nodes'] = nodes
    out['units_resolved'] = {n: nodes.get(n) == ('/Script/' + s.split('/Script/')[1]) for n, s in UNITS.items()}
    out['links'] = sorted('%s -> %s' % (l.get_source_pin().get_pin_path(), l.get_target_pin().get_pin_path())
                          for l in graph.get_links())
    hierarchy = bp.hierarchy
    out['bones'] = len(hierarchy.get_bones())
    out['curves'] = {c: hierarchy.contains(unreal.RigElementKey(type=unreal.RigElementType.CURVE, name=c))
                     for c in WITNESS_CURVES}
    shapes, chains = _autofit()
    out['behaviours'] = {k: spec['behaviour'] for k, spec in sorted(chains.items())}   # as the profile assigns them
    out['roots_present'] = {b: hierarchy.contains(unreal.RigElementKey(type=unreal.RigElementType.BONE, name=b))
                            for b in chains}
    out['tails_present'] = {t['null']: hierarchy.contains(unreal.RigElementKey(type=unreal.RigElementType.NULL,
                                                                                 name=t['null']))
                            for spec in chains.values() for t in spec.get('tails', [])}
    out['colliders'] = sorted(n for n in nodes if n.startswith('Collider_'))
    out['chains'] = sorted(n for n in nodes if n.startswith(('Chain_', 'Flaps_')))
    out['spawners_expected'] = sorted(name for name, _r, _s in _af.spawners(chains))
    pins = {}
    paths = ['SpawnSolver.Settings.SimulationSpace', 'StepSolver.Alpha', 'SpawnSolver.Owner',
             'SpawnSolver.SolverComponentName', 'StepSolver.DynamicsSolverComponentKey', 'AirField.FieldTransform',
             'AirField.Space', 'AirField.Type', 'AirTele.B']
    paths += ['%s.SpaceMotion.%s' % (n, p) for n in ('SpawnSolver', 'SetMotion') for p in SPACE_MOTION]
    out['air_vars'] = {str(v.get_editor_property('name')): str(v.get_editor_property('default_value'))
                       for v in bp.get_member_variables() if str(v.get_editor_property('name')) in AIR_VARS}
    out['group_vars'] = {str(v.get_editor_property('name')): str(v.get_editor_property('default_value'))
                         for v in bp.get_member_variables()
                         if str(v.get_editor_property('name')).endswith(GROUP_INPUTS + TWIST_INPUTS)}
    out['twist_sets'] = {n: graph.find_pin(n + '.Item').get_default_value()
                         for n in sorted(nodes) if n.startswith('Tw') and n.endswith('Set') and n[2:-3].isdigit()}
    forces = sorted((n for n in nodes if n.startswith('FlutF')), key=lambda s: int(s[5:]))
    out['flutter_forces'] = {n: (graph.find_pin(n + '.DynamicsParticleComponentKey').get_default_value(),
                                 graph.find_pin(n + '.Type').get_default_value()) for n in forces}
    for c in out['chains']:
        paths += [c + '.RootBones', c + '.ParticleProperties.Radius', c + '.ParticleProperties.NoCollisionColliders',
                  c + '.ParticleProperties.AngleLimit', c + '.ParticleProperties.AngleLimitStrength']
    for path in paths:
        pin = graph.find_pin(path)
        pins[path] = pin.get_default_value() if pin else 'PIN NOT FOUND'
    out['pins'] = pins
    out['pins_missing'] = sorted(p for p, v in pins.items() if v == 'PIN NOT FOUND')
    unreal.log('KHAIDYN ' + json.dumps(out))
    return out


# POP, do not get (army_spawn.py's reason): run_python execs into a persistent namespace.
_action = globals().pop('KDYN_ACTION', None)
_colliders = globals().pop('KDYN_COLLIDERS', True)
_behaviours = globals().pop('KDYN_BEHAVIOURS', None)  # {behaviour: {'native': {...}, 'flutter': {...}}}, experiments
_airvars = globals().pop('KDYN_AIR', None)          # {AIR_VARS name: default}, experiments only
if _action == 'author':
    author(with_colliders=_colliders, behaviours=_behaviours, air=_airvars)
elif _action == 'report':
    report()
