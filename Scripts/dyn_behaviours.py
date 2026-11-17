"""Reusable secondary-motion behaviours: load Scripts/dynamics_behaviours.json and resolve a part to one.

Pure Python (no editor). The intake resolves every part once (collider_autofit.build) and writes the result into the
profile, so the collision groups, the rig's spawners and the gate's identity all see the same assignment.
Spec: docs/specs/2026-10-28-dynamics-behaviours-design.md.

    lib = load()                                   # the library, every field and 'assign' target checked
    lib = load({'pendant': {'native': {'Strength': 0.5}}})   # an experiment override of VALUES, merged field by field
    name = resolve(part, lib, chosen)              # part: {'id', 'preset', 'kind'}; chosen: profile overrides by id
"""
import json
import math
import os

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dynamics_behaviours.json')
ENTRIES = {'native', 'flutter', 'twist', 'deforms_host', 'particle_radius_cm', 'chain_curves', 'tuned'}
# native Damping is the particle's WHOLE air drag: the rig's default is AIR_DAMPING_HZ and the solver adds none
# (dynamics_rig), so a behaviour states its total (soft_tissue 0: the breasts are specified to have zero
# drag).
# chain_curves: {'<Multiplier>': [[position, value], ...]}: FParticleChainCurves (RigDynamicsExecutionHelpers.h:38),
# multipliers on the particle properties along each chain, position 0 at the root to 1 at the leaf. Hair lying on a
# scalp needs a tight, stiff root and a free tip, or the clump lifts off and shows the scalp (Aurora, 2026-10-29).
CHAIN_CURVES = ('RadiusMultiplier', 'MassMultiplier', 'GravityMultiplier', 'StrengthMultiplier',
                'DampingRatioMultiplier', 'ExtraDampingMultiplier', 'DampingMultiplier', 'TargetModeMultiplier',
                'AngleLimitMultiplier', 'AngleLimitStrengthMultiplier')
# particle_radius_cm (number, (0, 5]): a CHAIN's collision particle radius when the 5 cm cloth default is too fat for the
# part (hair clumps lie on the scalp: a 5 cm particle starts inside the head, collider_autofit cannot shrink a rest copy
# that far, and the head pushes the hair out on the first frame). Flaps keep their tip-measured radius.
# deforms_host (bool): the part is skinned INTO its host surface by design (soft tissue), so the collider build keeps it
# even when almost none of its vertices are its own (collider_autofit PAINTED_SHARE drops such flaps otherwise).
# FRigDynamicsParticleProperties fields a behaviour may set (RigDynamicsData.h:560-650). Radius is the collision fit's
# (collider_autofit sizes particles from the mesh, and the clearance analysis reads the profile's radius); the collider
# lists are the profile's.
NATIVE = {'Mass': float, 'MovementType': str, 'GravityMultiplier': float, 'Strength': float, 'DampingRatio': float,
          'ExtraDamping': float, 'bAccelerationMode': bool, 'TargetVelocityInfluence': float, 'TargetMode': float,
          'AngleLimit': float, 'AngleLimitStrength': float, 'Damping': float, 'bScaleDampingByInverseMass': bool,
          'bCollideWithColliders': bool}
FLUTTER = ('FlutterRate', 'FlutterAmbient', 'FlutterSpeedGain')
# Strip twist (dynamics_rig twist modifier): roll = clamp(TwistGain x in-plane bend, +/-TwistMax deg).
TWIST = ('TwistGain', 'TwistMax')


def _check(name, spec):
    unknown = sorted(set(spec) - ENTRIES)
    if unknown:
        raise ValueError('behaviour %s has unknown entries %s (known: %s)' % (name, unknown, sorted(ENTRIES)))
    for field, value in (spec.get('native') or {}).items():
        want = NATIVE.get(field)
        if want is None:
            raise ValueError('behaviour %s sets %s, not a settable particle field (%s)' % (name, field, sorted(NATIVE)))
        ok = isinstance(value, bool) if want is bool else (
            isinstance(value, (int, float)) and not isinstance(value, bool) if want is float else isinstance(value, str))
        if not ok:
            raise ValueError('behaviour %s field %s = %r is not a %s' % (name, field, value, want.__name__))
    if 'deforms_host' in spec and not isinstance(spec['deforms_host'], bool):
        raise ValueError('behaviour %s deforms_host = %r is not a bool' % (name, spec['deforms_host']))
    for curve, keys in (spec.get('chain_curves') or {}).items():
        if curve not in CHAIN_CURVES:
            raise ValueError('behaviour %s chain_curves has %s, not one of %s' % (name, curve, CHAIN_CURVES))
        pos = [k[0] for k in keys] if isinstance(keys, list) else []
        ok = (len(keys) >= 2 and all(isinstance(k, list) and len(k) == 2 and all(
            isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in k) for k in keys)
            and pos == sorted(pos) and pos[0] >= 0.0 and pos[-1] <= 1.0 and all(k[1] >= 0.0 for k in keys))
        if not ok:
            raise ValueError('behaviour %s chain_curves %s needs >= 2 [position 0..1 ascending, value >= 0] keys: %r'
                             % (name, curve, keys))
    r = spec.get('particle_radius_cm')
    if r is not None and (isinstance(r, bool) or not isinstance(r, (int, float)) or not 0.0 < r <= 5.0):
        raise ValueError('behaviour %s particle_radius_cm = %r is not in (0, 5]' % (name, r))
    for entry, keys in (('flutter', FLUTTER), ('twist', TWIST)):
        mod = spec.get(entry)
        if mod is None:
            continue
        if set(mod) != set(keys):
            raise ValueError('behaviour %s %s needs exactly %s, has %s' % (name, entry, keys, sorted(mod)))
        bad = sorted(k for k, v in mod.items()
                     if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v))
        if bad:
            raise ValueError('behaviour %s %s values %s are not finite numbers' % (name, entry, bad))
        # A gain may be negative or over-cranked; a limit is a magnitude (a negative one inverts the clamp: at gain 0
        # the roll would be the limit, review).
        if entry == 'twist' and mod['TwistMax'] < 0.0:
            raise ValueError('behaviour %s twist TwistMax %r is negative' % (name, mod['TwistMax']))


def load(override=None, path=PATH):
    with open(path) as f:
        lib = json.load(f)
    for name, spec in (override or {}).items():
        cur = lib['behaviours'].setdefault(name, {'native': {}})
        for key, value in spec.items():
            cur[key] = dict(cur.get(key) or {}, **value) if isinstance(value, dict) else value
    for name, spec in lib['behaviours'].items():
        _check(name, spec)
    bad = sorted(b for b in lib['assign'].values() if b not in lib['behaviours'])
    if bad:
        raise ValueError('assign names unknown behaviours: %s' % bad)
    if '*' not in lib['assign']:
        raise ValueError("the behaviour library needs a '*' assignment")
    return lib


# A hero's Chaos garments (its stock clothing assets): what the drive sets on every one through the clothing interactors
# (UChaosClothingInteractor / UChaosClothingSimulationInteractor), over each asset's own config (UHeroDynamicsTools.
# ClothConfigs reports it). The library's "garment", then the hero profile's secondary.garment, key by key; a key absent
# from both keeps the asset's own value.
GARMENT = {'iterations': int, 'max_iterations': int, 'damping': float, 'local_damping': float,
           # how much of the reference bone's linear and angular velocity the cloth inherits (SetVelocityScale); the
           # interactor sets both, with the fictitious angular scale, in one call
           'linear_velocity_scale': float, 'angular_velocity_scale': float, 'fictitious_angular_scale': float}
# keys one interactor call sets together: one left out would take the engine's default, not the asset's
GARMENT_TOGETHER = (('damping', 'local_damping'),
                    ('linear_velocity_scale', 'angular_velocity_scale', 'fictitious_angular_scale'))


def garment(profile=None, override=None, path=PATH):
    """The garment settings a hero's cloth runs with: {key: value} over GARMENT, plus 'tuned' (where they came from)."""
    with open(path) as f:
        lib = json.load(f).get('garment') or {}
    out = dict(lib, **((profile or {}).get('secondary', {}).get('garment') or {}))
    out.update(override or {})
    unknown = sorted(set(out) - set(GARMENT) - {'tuned'})
    if unknown:
        raise ValueError('garment has unknown keys %s (known: %s)' % (unknown, sorted(GARMENT)))
    for k, kind in GARMENT.items():
        if k in out and (isinstance(out[k], bool) or not isinstance(out[k], (int, float)) or out[k] < 0
                         or (kind is int and out[k] != int(out[k]))):
            raise ValueError('garment %s must be a non-negative %s, got %r' % (k, kind.__name__, out[k]))
    for group in GARMENT_TOGETHER:
        if 0 < sum(k in out for k in group) < len(group):
            raise ValueError('garment %s are set together, got %s' % (', '.join(group), sorted(out)))
    return out


def resolve(part, lib, chosen=None):
    """A part's behaviour: the profile's pick for its id (`chosen`, secondary.behaviours), else the most specific
    'assign' key: '<preset>.<kind>', '<preset>', '*'."""
    pick = (chosen or {}).get(part.get('id'))
    if pick:
        if pick not in lib['behaviours']:
            raise ValueError('profile picks unknown behaviour %s for %s' % (pick, part.get('id')))
        return pick
    for key in ('%s.%s' % (part.get('preset'), part.get('kind', 'chain')), str(part.get('preset')), '*'):
        if key in lib['assign']:
            return lib['assign'][key]
