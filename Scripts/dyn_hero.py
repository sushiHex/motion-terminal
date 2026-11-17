"""Everything hero-specific the dynamics pipeline needs, resolved from the rig name (no hero is named in code).

Pure Python; also imported inside the editor. Spec: docs/specs/2026-10-28-dynamics-hero-agnostic-pipeline-design.md.

    h = resolve('ParagonKhaimera')      # or resolve() with MT_HERO set (CLIs set it from --hero)
    h['cr_path'], h['mesh'], h['profile'], h['grip'] ...

Sources, in order:
  - the hero profile HeroProfiles/<rig>.json (intake output): mesh, skeleton, parts, colliders;
  - a naming convention for the assets the pipeline builds or drives, under /Game/MT/<short>/ where <short> is the
    rig name without a 'Paragon' prefix (ParagonKhaimera -> Khaimera);
  - optional hand-set extras in HeroProfiles/dynamics/<rig>.json, each value with a 'why' (unknown keys refused).
"""
import json
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILES = os.path.join(REPO, 'HeroProfiles')
EXTRAS_DIR = os.path.join(PROFILES, 'dynamics')
RECORDS = os.path.join(REPO, 'Saved', 'MotionTerminalTests')
ENV = 'MT_HERO'
# Hand-set per-hero extras. grip: an open-hand fix for heroes whose clips hold no weapon pose (pose + hand roots);
# solver_settings: overrides of dyn_author.SOLVER_SETTINGS once a hero's OFF arm has been measured.
EXTRAS = {'grip': ('pose', 'hands', 'why'), 'solver_settings': ('values', 'why'),
          # clip_prefix: the prefix of a hero's retargeted clips and blendspaces when it predates the '<short>_' convention
          'clip_prefix': ('value', 'why')}
# The template every hero's locomotion ABP is a child of, and the mannequin IK rig its clips are retargeted from.
TEMPLATE_ABP = '/Game/MT/ABP_MT_Locomotion'
SOURCE_IK_RIG = '/Game/Spike/IK_Manny'
SOURCE_SKELETON = '/Game/Characters/Mannequins/Meshes/SK_Mannequin'     # the skeleton of the clips retargeted from
TEMPLATE_PAWN = '/Game/MT/BP_MT_TestPawn'


def hero_name(name=None):
    """The rig name: the argument, else MT_HERO. No default: a missing hero is an error, never a silent Khaimera."""
    name = name or os.environ.get(ENV)
    if not name:
        raise ValueError('no hero given: pass --hero <Rig> (a HeroProfiles/<Rig>.json) or set %s' % ENV)
    return name


def hero_arg(argv):
    """(rig, the rest of argv) from '--hero <Rig>' / '--hero=<Rig>', else MT_HERO. Shared by the dynamics CLIs."""
    rest, rig, given = [], None, False
    it = iter(argv)
    for a in it:
        if a == '--hero':
            rig, given = next(it, None), True
        elif a.startswith('--hero='):
            rig, given = a.split('=', 1)[1], True
        else:
            rest.append(a)
    # '--hero' with no value (a failed shell expansion) is an error, never a silent fall-through to MT_HERO.
    if given and (not rig or rig.startswith('--')):
        raise ValueError('--hero needs a rig name, got %r' % rig)
    return hero_name(rig), rest


def heroes_arg(argv):
    """([rig, ...], the rest of argv) from one or more '--hero <Rig>' / '--hero=<Rig>' (each as hero_arg reads it),
    else [MT_HERO]. For the CLIs that run every hero the same way (hit_reaction_sweep, hit_reaction_release)."""
    rigs, rest = [], list(argv)
    flag = lambda a: a == '--hero' or a.startswith('--hero=')   # noqa: E731
    while any(flag(a) for a in rest):
        i = next(i for i, a in enumerate(rest) if flag(a))
        n = 2 if rest[i] == '--hero' else 1
        rigs.append(hero_arg(rest[i:i + n])[0])
        rest = rest[:i] + rest[i + n:]
    return rigs or [hero_name()], rest


def short_name(rig):
    """ParagonKhaimera -> Khaimera; a rig without the prefix keeps its name. Must be a valid asset-name fragment."""
    short = re.sub(r'^Paragon(?=[A-Z])', '', rig)
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', short):
        raise ValueError('rig name %r does not make an asset name (%r)' % (rig, short))
    return short


def load_extras(rig, path=None):
    path = path or os.path.join(EXTRAS_DIR, rig + '.json')
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        extras = json.load(f)
    unknown = sorted(set(extras) - set(EXTRAS))
    if unknown:
        raise ValueError('%s: unknown keys %s (known: %s)' % (path, unknown, sorted(EXTRAS)))
    for key, fields in EXTRAS.items():
        if key in extras and set(extras[key]) != set(fields):
            raise ValueError('%s: %s needs exactly %s, has %s' % (path, key, fields, sorted(extras[key])))
        if key in extras and not str(extras[key]['why']).strip():
            raise ValueError('%s: %s needs a why' % (path, key))
    return extras


def resolve(name=None, profile_path=None, extras_path=None):
    rig = hero_name(name)
    short = short_name(rig)
    if profile_path is None:
        check_unique_shorts()
    profile_path = profile_path or os.path.join(PROFILES, rig + '.json')
    if not os.path.exists(profile_path):
        raise ValueError('no hero profile %s: run hero_intake.py for %s first' % (profile_path, rig))
    with open(profile_path) as f:
        profile = json.load(f)
    primary = profile.get('primary') or {}
    if not primary.get('mesh') or not primary.get('skeleton'):
        raise ValueError('%s has no primary mesh/skeleton (intake status %r)' % (profile_path, profile.get('status')))
    base = '/Game/MT/%s/' % short
    extras = load_extras(rig, extras_path)
    cls = lambda path: '%s.%s_C' % (path, path.rsplit('/', 1)[1])          # noqa: E731
    cr = base + 'CR_MT_%sDynamics' % short
    abp = base + 'ABP_MT_%sDynamics' % short
    pawn = base + 'BP_MT_TestPawn_%s' % short
    inplace = base + 'InPlace/ABP_MT_%s_InPlace' % short
    prefix = (extras.get('clip_prefix') or {}).get('value') or short + '_'
    return {
        # onboarding (hero_onboard.py): the locomotion assets every hero's test pawn and in-place ABP are made from
        'clip_prefix': prefix, 'retarget_dir': base + 'Retargeted/', 'inplace_dir': base + 'InPlace/',
        'ik_rig': base + 'Retarget/IK_MT_%s' % short, 'retargeter': base + 'Retarget/RTG_MT_Manny_to_%s' % short,
        'bs': base + prefix + 'BS_Locomotion_2D', 'bs_inplace': base + 'InPlace/' + prefix + 'BS_Locomotion_2D_InPlace',
        'abp_main': base + 'ABP_MT_%s' % short, 'abp_main_class': cls(base + 'ABP_MT_%s' % short),
        'onboard_record': os.path.join(RECORDS, 'onboard_%s.json' % rig),
        # hit reaction: the hero's reaction-body physics asset (hero_onboard.py physics stage)
        'physics_asset': base + 'PA_MT_%s' % short,
        # the bodies the mesh's own cloth collides with (heroes with clothing assets only)
        'cloth_collision_asset': base + 'PA_MT_%s_ClothCollision' % short,
        'rig': rig, 'short': short, 'profile_path': profile_path, 'profile': profile,
        'mesh': primary['mesh'], 'skeleton': primary['skeleton'],
        'cr_path': cr, 'cr_class': cls(cr), 'abp_path': abp, 'abp_class': cls(abp),
        # the leg-reach rig between the reaction and its leg IK (leg_reach.py)
        'reach_cr_path': base + 'CR_MT_%sLegReach' % short, 'reach_cr_class': cls(base + 'CR_MT_%sLegReach' % short),
        'pawn_class': cls(pawn), 'anim_class': cls(inplace), 'pawn_path': pawn, 'inplace_path': inplace,
        'solver_component': 'MT_%sDynamicsSolver' % short,
        'manifest': os.path.join(RECORDS, 'dyn_%s_build.json' % rig),
        'grip': extras.get('grip') or _derived_grip(profile), 'solver_settings': (extras.get('solver_settings') or {}).get('values', {}),
    }


def _derived_grip(profile):
    """A grip for a hero that holds a prop (the intake's held_props) and has none hand-set: the hands holding props,
    with the pose left for the ABP build to find among the hero's own clips (pose None)."""
    held = profile.get('held_props') or []
    if not held:
        return None
    return {'pose': None, 'hands': sorted({p['hand'] for p in held}), 'derived': True,
            'why': 'derived: %s held (intake held_props); the retargeted unarmed clips open those hands'
                   % ', '.join('%s in %s' % (p['bone'], p['hand']) for p in held)}


def record_path(h, tag):
    """A drive record: Saved/MotionTerminalTests/dyn_<rig>_<tag>.json (the full rig id, so two heroes never share one).
    Records written before heroes were parameters are khai_dyn_<tag>.json (first hero only); readers fall back."""
    return os.path.join(RECORDS, 'dyn_%s_%s.json' % (h['rig'], tag))


def record_timing(cfg):
    """How a drive steps time: 'fixed <hz>' (a PIE run the gate judges) or 'mrq' (a render: MRQ steps the world at the
    sequence's rate, fixed_hz None). Records of different timing are not comparable."""
    hz = (cfg or {}).get('fixed_hz')
    return 'fixed %g' % hz if hz else 'mrq'


def overwrite_refusal(path, cfg):
    """Why a drive with `cfg` must not write its record over the one at `path`, or None. A render that reused a gated
    run's tag once replaced the run's record with its own, so the gate's evidence was gone and a later reader compared
    timings that differed. A record is replaced only by one of the same timing; a different kind needs its own tag."""
    if not os.path.isfile(path):
        return None
    try:
        with open(path) as fh:
            old = json.load(fh).get('config')
    except (OSError, ValueError) as exc:
        return '%s exists and cannot be read (%s); use a new tag' % (path, exc)
    if record_timing(old) != record_timing(cfg):
        return ('%s holds a %s record; this drive is %s. Use a new tag so neither replaces the other'
                % (path, record_timing(old), record_timing(cfg)))
    return None


def legacy_record_path(tag):
    return os.path.join(RECORDS, 'khai_dyn_%s.json' % tag)


def check_unique_shorts(profiles_dir=PROFILES):
    """Two profiles whose asset namespace collides (Aurora and ParagonAurora) would build into one folder: refuse."""
    seen = {}
    for name in sorted(os.listdir(profiles_dir)):
        if name.endswith('.json'):
            rig = name[:-len('.json')]
            short = short_name(rig)
            if short in seen:
                raise ValueError('heroes %s and %s share the asset namespace /Game/MT/%s/' % (seen[short], rig, short))
            seen[short] = rig
    return seen
