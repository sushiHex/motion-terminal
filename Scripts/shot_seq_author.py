# The render sequences the shot sequence is filmed through, as a script instead of a paragraph.
#
# Runs inside the editor over `run_python`; the caller PREPENDS an action and exec()s this file:
#     SEQ_ACTION = 'author'          # 'author' | 'report'
#     SEQ_NAMES  = None              # or ('Seq_Lane',) to restrict to a subset
#     SEQ_SUFFIX = None              # or 'Proof' -> authors Seq_LaneProof beside the real asset
#     exec(open(r'.../Scripts/shot_seq_author.py').read())
# Every action prints one `RSEQ <json>` line to LogPython so the caller can parse it, and an unrecognised
# action RAISES rather than doing nothing quietly (the army_conductor.py lesson: a silent no-op is
# indistinguishable from an action that ran and said nothing).
#
# ============================ WHY THIS FILE EXISTS ============================
#
# `Seq_Lane` used to exist only as PROSE: one sentence,
# "new gitignored sequence /Game/MT/BeatsDE/Seq_Lane (duplicate of Seq_BeatsDE, retimed to 30 fps /
# 0..660)", was the only description of it anywhere in the repo. Three shipped shots
# (02, 02b, 03) are rendered through it, so from a clean checkout those three could not be re-rendered
# without a human reading a paragraph and reproducing an asset by hand. That is a repeatability gap in
# a pipeline whose entire claim is that it is reproducible and verified, and it was one of the two
# load-bearing gaps in the shot sequence's rebuild chain. `Seq_Army` (the crowd take) had the same gap for
# the same reason: it was authored from a throwaway `setup_seq_army.py` script that was never committed.
#
# THE SEQUENCES ARE STILL MACHINE-LOCAL, AND THAT IS UNCHANGED AND DELIBERATE. Both live under gitignored
# stage folders (`Content/MT/BeatsDE/`, `Content/MT/Army/`) because their STAGES reference the gitignored
# Khaimera content. What this file changes is that the sequence is now REBUILDABLE rather than merely
# described — the same treatment `metahuman_bind.sequence` gives `Seq_Roster4Up`.
#
# THE STAGES WERE THE OTHER HALF, AND THEY ARE REBUILDABLE NOW TOO. When this file was written
# the STAGES themselves (`Lvl_MT_BeatsDE`, `Lvl_MT_Army`, `Lvl_MT_Roster`) had no authoring script — the
# army stage's notes said so plainly ("the STAGE is not [rebuildable], there is no stage-authoring
# script") and it was true of all three, so this file closes the sequence half only. The stage half
# is `Scripts/stage_author.py`, which builds all four stages from `new_level()`.
#
# THIS FILE COVERS EVERY MACHINE-LOCAL SEQUENCE IN THE PROJECT — seven of them.
# `Seq_BeatsDE` (the turn/aim/hand-IK take) was the last prose link in the shot sequence's rebuild
# chain; `Seq_Intro` (the cold open), `Seq_FootIK` (the foot-IK A/B take), `Seq_RosterLineup` (provenance of an earlier roster take) and
# `Seq_MRQSmoke` (the render-environment smoke fixture) were the rest. The only one deliberately not here is `Seq_Roster4Up` — see DELEGATED.
#
# ============================ WHY IT AUTHORS RATHER THAN DUPLICATES ============================
#
# Every one of these was originally made by duplicating the previous one in the editor UI and re-timing
# it — a chain running Seq_RosterLineup -> Seq_BeatsDE -> Seq_Lane. This file does NOT reproduce that:
# it creates each asset from `LevelSequenceFactoryNew`, because the duplication carried nothing worth
# carrying and a `duplicate_asset` chain would make each sequence depend on ANOTHER machine-local asset
# surviving on the machine — which is the gap being closed. Verified against the live assets before each
# entry was written; with one exception (below) every one of them is exactly:
#
#     display rate + playback range   (30/1, 0..660 for Seq_Lane; 60/1, 0..1600 for Seq_Army; …)
#     ONE spawnable CineCameraActor   (its only track is the MovieSceneSpawnTrack the spawnable implies)
#     ONE MovieSceneCameraCutTrack    (one section, spanning the whole range, bound to that camera)
#
# NO TRANSFORM TRACK, NO ANIMATION TRACK, and that is the load-bearing property rather than an omission.
# The camera is driven live by the shot's Python driver inside the MRQ PIE world (the pattern every
# MRQ-over-PIE take in this project uses — the driver IS the director), so a transform track here would
# fight the driver; and the POSE must come from the live driven AnimBP, so a MovieSceneSkeletalAnimation track would
# make the footage a playback of a bake rather than a recording of the graph.
#
# THE ONE EXCEPTION IS `Seq_MRQSmoke`, and it is the case the rule is carved out of rather than a leak:
# the smoke render runs with `driver_path=None`, so there is no driver for a transform track to fight and
# the keys ARE the camera move. Its nine channels are recorded as DATA off the live asset and verified
# key-for-key. An animation track is still forbidden everywhere, unconditionally.
#
# IDEMPOTENT, AND IT RE-POINTS RATHER THAN REPLACING. Run against an existing sequence it re-asserts the
# rate/range, reuses the existing camera binding if there is one, and rebuilds the cut section — so
# running it on a machine that already has the assets does not invalidate the footage already shot through these assets.
#
# GOTCHA-A' (project rule): nothing here binds a World, Level or Actor to a module-level name. A
# LevelSequence is an asset, not a world, so authoring one is not the `duplicate_asset`/`new_level`
# hazard that has crashed the editor before — but every handle is still a local that dies with its call.
import importlib
import json
import os
import sys

import unreal

# The shared driver scaffolding — here for `require_saved_gitignored`, the one write guard this project
# applies to every asset it saves. Resolved off the .uproject, NOT off `__file__`: this module is exec'd
# into a bare namespace by `editor.run_python`, where `__file__` does not exist. `mt_drive` has no
# import-time side effects, which is exactly why a helper two modules share lives there and not here —
# THIS file ends in a mandatory dispatcher that raises, and a caller reloading it to reach a helper got
# the dispatcher instead.
_MT_SCRIPTS = os.path.join(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()),
                           'Scripts')
if _MT_SCRIPTS not in sys.path:
    sys.path.insert(0, _MT_SCRIPTS)
_mt = importlib.reload(importlib.import_module('mt_drive'))

# frames is the sequence's playback END, which is EXCLUSIVE — the same convention mrq_render.render_shot
# uses for its custom playback range, so `frames` is literally the number of frames a full-range render
# writes. Each entry records which shipped shots are rendered through it, so a future re-time can see what
# it would invalidate before it happens.
SEQUENCES = {
    'Seq_Lane': {
        'path': '/Game/MT/BeatsDE/Seq_Lane',
        'fps': 30,
        'frames': 660,
        # 02 renders the full 0..660; 02b and 03 use MRQ's use_custom_playback_range to take 0..240 and
        # 0..540 out of the same asset. That works precisely BECAUSE the camera binding is a spawnable
        # with no transform track: there is no keyed camera move to be truncated, so one sequence serves
        # every 30 fps shot on this stage and no per-shot asset is needed.
        'spawn_section': 'bounded',
        'shots': ('02_locomotion_lane', '02b_gameplay_bridge', '03_montage_layer'),
    },
    'Seq_Army': {
        'path': '/Game/MT/Army/Seq_Army',
        # 60, NOT 30. MRQ drives the PIE world at the sequence's display rate, so this is also the rate
        # the crowd is SIMULATED at, and the conductor's throttle (army_conductor.DRIVE_HZ = 12) was
        # characterised under a fixed 1/60. Re-timing this to 30 would change the filmed cadence,
        # not just the file. The crowd take renders 0..480 of it — 8.0 s of world time, played back in the
        # assembled sequence at 30 fps (shots_manifest fps_out) as 16.0 s.
        'fps': 60,
        'frames': 1600,
        'spawn_section': 'bounded',
        'shots': ('06_crowd',),
    },
    # ---- the four remaining machine-local sequences ------------------------------------------------
    'Seq_BeatsDE': {
        # The turn/aim/hand-IK take's sequence, and the LAST prose link in the rebuild chain. It was first made as a
        # duplicate of Seq_RosterLineup re-timed to 0..780, and the one sentence saying so was the
        # only description of it anywhere.
        'path': '/Game/MT/BeatsDE/Seq_BeatsDE',
        'fps': 60,
        'frames': 780,
        'spawn_section': 'bounded',
        'shots': ('07_aim_hand_ik',),
    },
    'Seq_Intro': {
        # The cold open. The original take shot through it is what the sequence still ships; see the manifest row's
        # _why_a_rebuild_will_not_reproduce_this — the STAGE it was shot on has since been re-laid-out
        # by metahuman_bind.place(), so this sequence rebuilding exactly does NOT make the cold open
        # re-shootable frame-for-frame.
        #
        # AND THIS SEQUENCE IS NOT WHERE THE FRAMING WENT, which was worth checking rather than
        # assuming: read back from the live asset it carries one spawnable CineCameraActor, a
        # MovieSceneSpawnTrack and ZERO transform keys. The cold open's camera lived in its driver, and that
        # driver survives nowhere — only its capture directory's `_drive.log`, which logs the pawns and
        # not the camera. That is the one genuinely unrecoverable thing in the shot sequence.
        'path': '/Game/MT/Showcase/Seq_Intro',
        'fps': 60,
        'frames': 300,
        'spawn_section': 'infinite',
        'shots': ('01_intro',),
    },
    'Seq_RosterLineup': {
        # The sequence of an earlier roster take. NO shipped shot renders through it any more — the four-rig
        # lineup take moved to Seq_Roster4Up — but it is kept and reproduced because that earlier take was shot
        # through it and its provenance is recorded. Listed with an empty `shots` rather than
        # omitted, so nobody re-times it believing it is unused scaffolding.
        'path': '/Game/MT/Showcase/Seq_RosterLineup',
        'fps': 60,
        'frames': 300,
        'spawn_section': 'infinite',
        'shots': (),
    },
    'Seq_FootIK': {
        # The foot-IK A/B take's sequence. Stage (`stage_author.py` footik), sequence (here) and DRIVER
        # (`Scripts/pie_footik_ab_drive.py`, promoted out of a gitignored capture-directory script)
        # are all in the repo now, so the shot IS
        # re-shootable. It is not re-PRODUCIBLE: a re-shoot put the camera and the settle back
        # byte-for-byte and still landed 13.5/255 off the shipped frames, because ABP_MT_Locomotion has
        # been edited many times since the original take. Stated at the manifest row too.
        'path': '/Game/MT/Showcase/Seq_FootIK',
        'fps': 60,
        'frames': 240,
        'spawn_section': 'infinite',
        'shots': ('04_footik_ab',),
    },
    'Seq_MRQSmoke': {
        # THE ONE SEQUENCE IN THIS FILE THAT CARRIES A CAMERA TRANSFORM TRACK, and it is not a violation
        # of the rule above — it is the case the rule is carved out of. Every other sequence here is
        # rendered WITH a Python driver that owns the camera live inside the MRQ PIE world, so a keyed
        # transform would fight the driver. The render-environment smoke render has NO driver
        # (`render_shot(level='/Game/MT/Lvl_MT_Test', sequence='/Game/MT/Showcase/Seq_MRQSmoke', 0..60)`
        # with driver_path=None), so the keys below ARE its camera move: a static three-quarter set-up
        # with a 100-degree yaw sweep across the second, which is what makes the 60 frames non-static
        # and therefore able to fail `capture_check --min-motion`.
        #
        # The keys are DATA, recorded off the live asset rather than derived from a recipe, and they are
        # written and then verified key-for-key. No shipped frame depends on this sequence; it
        # exists so a clean checkout can re-run the render-environment smoke test, which was once
        # skipped at the cost of a whole stage of work.
        'path': '/Game/MT/Showcase/Seq_MRQSmoke',
        'fps': 60,
        'frames': 60,
        'spawn_section': 'bounded',
        'shots': (),
        'camera_transform_keys': {
            'Location.X': [[0, 150.0]],
            'Location.Y': [[0, -500.0]],
            'Location.Z': [[0, 240.0]],
            # The live asset reads 1.997e-07 here — float noise from the editor's rotator round-trip,
            # not an authored value. Recorded as 0.0 and compared with a tolerance, because writing the
            # noise back would be recording a measurement error as a specification.
            'Rotation.X': [[0, 0.0]],
            'Rotation.Y': [[0, -13.9842]],
            'Rotation.Z': [[0, 106.6992], [60, 206.6992]],
            'Scale.X': [[0, 1.0]],
            'Scale.Y': [[0, 1.0]],
            'Scale.Z': [[0, 1.0]],
        },
    },
}

# NOT IN THIS FILE, AND DELIBERATELY: `Seq_Roster4Up` (30 fps, 0..900, Shots 05 and 09) is authored by
# `Scripts/metahuman_bind.py` `sequence`, which owns it along with the four line-up
# pawns it is framed on. Adding a second author here would give one asset two specs that could disagree
# — the same single-owner reasoning that keeps the roster pawns out of stage_author.py's STAGES.
DELEGATED = {'Seq_Roster4Up': '/Game/MT/Showcase/Seq_Roster4Up -> Scripts/metahuman_bind.py `sequence`'}

# Key comparison tolerance. The setters are exact; this absorbs the float round-trip through the
# editor's transform channels (see Rotation.X above).
_KEY_TOL = 1e-3


def _selected(names=None):
    if not names:
        return list(SEQUENCES.items())
    missing = [n for n in names if n not in SEQUENCES]
    if missing:
        raise ValueError('unknown sequence name(s) %s — known: %s'
                         % (missing, sorted(SEQUENCES)))
    return [(n, SEQUENCES[n]) for n in names]


def _camera_binding(seq):
    """The existing spawnable camera binding, or None.

    Matched on the BOUND CLASS, not on the display name. The name is user-editable and a renamed camera
    would make this create a second binding beside the first — two spawnables, one cut track, and a
    render that is a coin-flip about which camera the driver finds."""
    for b in seq.get_bindings():
        cls = b.get_possessed_object_class()
        if cls is not None and 'CineCameraActor' in str(cls.get_name()):
            return b
    return None


def _binding_track(seq, track_class):
    cam = _camera_binding(seq)
    if cam is None:
        return None
    for tr in cam.get_tracks():
        if isinstance(tr, track_class):
            return tr
    return None


def _spawn_shape(seq):
    """'bounded' / 'infinite' / None, read off the spawnable's own MovieSceneSpawnTrack section.

    Worth recording because the shipped sequences DISAGREE and both variants demonstrably work:
    `add_spawnable_from_class` leaves an INFINITE spawn section, which is what Seq_Intro,
    Seq_RosterLineup, Seq_FootIK and Seq_Roster4Up carry, while Seq_Lane, Seq_Army, Seq_BeatsDE and
    Seq_MRQSmoke carry one bounded to the playback range because they descend from a hand-bounded
    ancestor. Until this was measured, a sequence CREATED FROM NOTHING by this file came out infinite
    regardless of what the shipped one held — a silent shape difference the first creation proof did not
    check for. It is now specified per sequence and verified, so the rebuild matches the asset that
    shot the footage rather than merely working.
    """
    tr = _binding_track(seq, unreal.MovieSceneSpawnTrack)
    if tr is None:
        return None
    for s in tr.get_sections():
        return 'bounded' if (s.has_start_frame() and s.has_end_frame()) else 'infinite'
    return None


def _transform_keys(seq):
    """Every key on the camera binding's transform track, by channel name. {} when there is none."""
    tr = _binding_track(seq, unreal.MovieScene3DTransformTrack)
    if tr is None:
        return {}
    out = {}
    for s in tr.get_sections():
        for ch in s.get_all_channels():
            out[str(ch.get_editor_property('channel_name'))] = [
                [int(k.get_time().frame_number.value), round(float(k.get_value()), 4)]
                for k in ch.get_keys()]
    return out


def _describe(path):
    """Read the live asset back. This is what `report` prints and what `author` verifies against."""
    seq = unreal.load_asset(path)
    if seq is None:
        return {'path': path, 'exists': False}
    rate = seq.get_display_rate()
    cut = None
    for t in seq.get_tracks():
        if isinstance(t, unreal.MovieSceneCameraCutTrack):
            cut = t
    return {
        'path': path, 'exists': True,
        'fps': [rate.numerator, rate.denominator],
        'range': [seq.get_playback_start(), seq.get_playback_end()],
        'bindings': [str(b.get_display_name()) for b in seq.get_bindings()],
        'master_tracks': [str(t.get_class().get_name()) for t in seq.get_tracks()],
        'cut_sections': [[s.get_start_frame(), s.get_end_frame()]
                         for s in (cut.get_sections() if cut else [])],
        # An ANIMATION track appearing here is a real defect, not cosmetic (the pose must come
        # from the live AnimBP). A TRANSFORM track is a defect on every sequence except Seq_MRQSmoke —
        # see its entry above for why that one is the exception rather than a leak.
        'binding_tracks': sorted({str(tr.get_class().get_name())
                                  for b in seq.get_bindings() for tr in b.get_tracks()}),
        'spawn_section': _spawn_shape(seq),
        'transform_keys': _transform_keys(seq),
    }


def resolve_seq_path(spec, suffix=None):
    """The asset path this run targets. `suffix` appends to the ASSET NAME, keeping the package in the
    same gitignored folder — /Game/MT/BeatsDE/Seq_BeatsDE + 'Proof' -> .../Seq_BeatsDEProof.

    Exists so "prove it CREATES rather than merely re-saves" is a supported call rather than a
    hand-typed one-off: running author against an existing asset only proves idempotency, and the first creation
    proof had to hand-write a throwaway `Seq_LaneProof` to demonstrate creation. Now `SEQ_SUFFIX='Proof'`
    does it for any row."""
    path = spec['path']
    return path + suffix if suffix else path


def author_one(name, spec, suffix=None):
    path = resolve_seq_path(spec, suffix)
    created = not unreal.EditorAssetLibrary.does_asset_exist(path)
    if created:
        pkg, asset = path.rsplit('/', 1)
        if unreal.AssetToolsHelpers.get_asset_tools().create_asset(
                asset_name=asset, package_path=pkg, asset_class=unreal.LevelSequence,
                factory=unreal.LevelSequenceFactoryNew()) is None:
            raise RuntimeError('create_asset failed for %s' % path)
    seq = unreal.load_asset(path)
    if seq is None:
        raise RuntimeError('could not load %s after create' % path)

    seq.set_display_rate(unreal.FrameRate(int(spec['fps']), 1))
    seq.set_playback_start(0)
    seq.set_playback_end(int(spec['frames']))

    cam = _camera_binding(seq)
    if cam is None:
        cam = seq.add_spawnable_from_class(unreal.CineCameraActor)

    cut = None
    for t in seq.get_tracks():
        if isinstance(t, unreal.MovieSceneCameraCutTrack):
            cut = t
            break
    if cut is None:
        cut = seq.add_track(unreal.MovieSceneCameraCutTrack)
    # Rebuild rather than edit: a re-run that widens the range must not leave a stale short section
    # beside the new one, which reads on camera as the cut going black partway through the take.
    for s in list(cut.get_sections()):
        cut.remove_section(s)
    sec = cut.add_section()
    sec.set_range(0, int(spec['frames']))
    binding_id = unreal.MovieSceneObjectBindingID()
    binding_id.set_editor_property('guid', cam.get_id())
    sec.set_editor_property('camera_binding_id', binding_id)

    # ---- spawn-section shape ---------------------------------------------------------------------
    # `add_spawnable_from_class` leaves the section INFINITE, so a from-nothing rebuild of a sequence
    # whose shipped asset is bounded would silently differ. Written in whichever direction the spec
    # names, on every run, so a re-run cannot leave the previous run's shape either.
    spawn = _binding_track(seq, unreal.MovieSceneSpawnTrack)
    if spawn is not None:
        for s in spawn.get_sections():
            if spec.get('spawn_section') == 'bounded':
                s.set_range(0, int(spec['frames']))
            else:
                s.set_start_frame_bounded(False)
                s.set_end_frame_bounded(False)

    # ---- the camera transform track: Seq_MRQSmoke ONLY -------------------------------------------
    xf = _binding_track(seq, unreal.MovieScene3DTransformTrack)
    if spec.get('camera_transform_keys'):
        if xf is None:
            xf = cam.add_track(unreal.MovieScene3DTransformTrack)
        for s in list(xf.get_sections()):
            xf.remove_section(s)
        xsec = xf.add_section()
        xsec.set_range(0, int(spec['frames']))
        wanted = spec['camera_transform_keys']
        for ch in xsec.get_all_channels():
            cname = str(ch.get_editor_property('channel_name'))
            for existing in list(ch.get_keys()):
                ch.remove_key(existing)
            for frame, value in wanted.get(cname, []):
                ch.add_key(unreal.FrameNumber(int(frame)), float(value))
    elif xf is not None:
        # A transform track on any OTHER sequence is the defect the header describes — remove it here
        # rather than only reporting it, because a re-author is exactly the moment it can be fixed.
        cam.remove_track(xf)

    _mt.require_saved_gitignored(path, 'shot_seq_author')

    # VERIFY FROM THE SAVED ASSET, not from the calls just made. The whole point of this file is that a
    # future checkout gets the sequence the shots were shot through; "the setters returned" is not that.
    got = _describe(path)
    want_fps, want_end = [int(spec['fps']), 1], int(spec['frames'])
    problems = []
    if got.get('fps') != want_fps:
        problems.append('display rate %s != %s' % (got.get('fps'), want_fps))
    if got.get('range') != [0, want_end]:
        problems.append('playback range %s != [0, %d]' % (got.get('range'), want_end))
    if got.get('cut_sections') != [[0, want_end]]:
        problems.append('cut sections %s != [[0, %d]]' % (got.get('cut_sections'), want_end))
    if len(got.get('bindings') or []) != 1:
        problems.append('%d bindings, expected exactly 1 (the spawnable camera)'
                        % len(got.get('bindings') or []))
    allowed = ['MovieSceneSpawnTrack']
    if spec.get('camera_transform_keys'):
        allowed.append('MovieScene3DTransformTrack')
    stray = [t for t in got.get('binding_tracks') or [] if t not in allowed]
    if stray:
        problems.append('binding carries %s — the driver owns the camera and the live AnimBP owns the '
                        'pose, so a transform/animation track here would fight one of them' % stray)
    if got.get('spawn_section') != spec.get('spawn_section', 'bounded'):
        problems.append('spawn section %s != %s — a from-nothing rebuild that leaves this infinite is '
                        'not the asset the footage was shot through'
                        % (got.get('spawn_section'), spec.get('spawn_section', 'bounded')))
    want_keys = spec.get('camera_transform_keys') or {}
    got_keys = got.get('transform_keys') or {}
    if sorted(want_keys) != sorted(got_keys):
        problems.append('transform channels %s != %s' % (sorted(got_keys), sorted(want_keys)))
    else:
        for cname, rows in want_keys.items():
            mine = got_keys.get(cname) or []
            if len(mine) != len(rows) or any(
                    int(a[0]) != int(b[0]) or abs(float(a[1]) - float(b[1])) > _KEY_TOL
                    for a, b in zip(mine, rows)):
                problems.append('transform channel %s keys %s != %s' % (cname, mine, rows))
    if problems:
        raise RuntimeError('%s authored but does not verify: %s' % (path, '; '.join(problems)))
    got['created'] = created
    got['shots'] = list(spec['shots'])
    return got


def author(names=None, suffix=None):
    out = [author_one(n, s, suffix) for n, s in _selected(names)]
    unreal.log('RSEQ ' + json.dumps({'action': 'author', 'suffix': suffix, 'sequences': out}))
    return out


def report(names=None, suffix=None):
    out = [_describe(resolve_seq_path(s, suffix)) for _n, s in _selected(names)]
    unreal.log('RSEQ ' + json.dumps({'action': 'report', 'sequences': out}))
    return out


# POP, do not get: run_python execs into a PERSISTENT namespace, so a `get` inherits the previous call's
# value whenever this one forgot to set it (the army_spawn.py ARMY_COUNTS incident). An unset name is
# None, never stale. Set BOTH on every call.
_action = globals().pop('SEQ_ACTION', None)
_names = globals().pop('SEQ_NAMES', None)
_suffix = globals().pop('SEQ_SUFFIX', None)
if _action == 'author':
    author(_names, _suffix)
elif _action == 'report':
    report(_names, _suffix)
else:
    unreal.log_error('RSEQ ' + json.dumps({'action': 'ERROR', 'reason': 'unknown SEQ_ACTION',
                                           'got': repr(_action), 'valid': ['author', 'report']}))
    raise RuntimeError('SEQ_ACTION is %r — set it to author or report on EVERY call (both names are '
                       'popped, so an unset name is None, never stale)' % (_action,))
