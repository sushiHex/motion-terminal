# Shared MRQ-over-PIE render harness for the shot sequence. Generalises the army-stage render pattern
# (the same MRQ-over-PIE setup the earlier PIE captures used).
#
# WHY MRQ-OVER-PIE, NOT PIE CAPTURE: the pose must come from the LIVE driven AnimBP, not a baked
# MovieSceneSkeletalAnimation track. MRQ runs a real PIE world, so a slate pre-tick driver
# registered BEFORE the render persists into it and drives the contract vars while frames are written.
#
# GOTCHA-A' — the driver and this harness must never bind a World/Level/Actor to a module-level name;
# run_python execs into a PERSISTENT namespace and a surviving world ref makes the next map change a
# fatal EditorServer.cpp:2544 crash.
import glob
import json
import importlib
import os
import sys

import unreal

# There is deliberately no `REPO` constant here. One used to hardcode a single machine's absolute
# checkout path, unread by any line in the file. Deleted rather than carried:
# dead code is cheap to ignore, but dead code that pins one machine's path is an invitation for the
# next caller to use it, and every path this module actually needs is passed in by its caller.


def _mt_drive():
    """The shared driver scaffolding, imported the way the drivers import it (off the .uproject, not
    off `__file__` — this module is itself loaded through `run_python`). Function-local so nothing
    here needs `unreal.Paths` at import time."""
    scripts = os.path.join(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()),
                           'Scripts')
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    return importlib.reload(importlib.import_module('mt_drive'))


def _norm_dir(path):
    """Canonical form for comparing two directory paths on this platform.

    Lowercasing a string is not path identity. './captures/x', a trailing slash, a junction, and a
    differently-cased drive letter all name one directory and compare unequal as strings - and the
    caller below is a guard whose MISS deletes a shipped take with no diagnostic. Being fail-closed
    about reading the manifest while being fail-open about matching it is not a guard, it is a
    coin flip that usually lands the right way up. realpath also resolves junctions, which is how
    captures/ is laid out on some checkouts.
    """
    return os.path.normcase(os.path.realpath(path)).rstrip(os.sep).rstrip('/')


def _shipped_source_dirs(repo):
    """Every directory the sequence manifest depends on: shipped source_dirs AND evidence_files dirs.

    THE EVIDENCE DIRECTORIES ARE THE ONES THIS FUNCTION EXISTS FOR. The
    first version collected `source_dir` only, and `source_dir` is the COMPOSITED directory - while
    render_shot is actually called with the RAW one. The gameplay-bridge take ships from captures/m9-bridge/bridge
    and pins its sha256 evidence in captures/m9-bridge/bridge_raw; same shape for 03 -> montage_raw
    and for all four of the verification take's transcripts under captures/m9-terminal. None of those appeared in
    the set, so a render into bridge_raw sailed past the guard and the *.txt half of the clear loop
    deleted the transcript that shot_check pins by hash and terminal_capture needs to recomposite
    the shipped panel. The guard covered the directory that was safe and missed the one at risk.

    Read fresh on every call rather than cached: the whole point is to reflect what the manifest
    claims RIGHT NOW, and a cached answer would go stale exactly when a shot is repointed."""
    mf = repo + '/Plugins/MotionTerminal/Tests/shots_manifest.json'
    try:
        with open(mf, 'r', encoding='utf-8') as fh:
            manifest = json.load(fh)
    except Exception as e:
        # FAIL CLOSED. If the manifest cannot be read we cannot tell whether this directory is
        # shipped, and 'unknown' must not resolve to 'go ahead and delete it'.
        raise RuntimeError('cannot read %s to check whether %r is shipped footage: %s' % (mf, repo, e))
    out = set()
    for shot in manifest.get('shots', []):
        if shot.get('status') != 'shipped':
            continue
        if shot.get('source_dir'):
            out.add(_norm_dir(repo + '/' + shot['source_dir']))
        for ev in (shot.get('evidence_files') or []):
            if ev.get('path'):
                out.add(_norm_dir(os.path.dirname(repo + '/' + ev['path'])))
    return out


def render_shot(level, sequence, out_dir, start, end, driver_path=None,
                resolution=(1920, 1080), job_name='MT_Shot', allow_overwrite_shipped=False, quality=None):
    """Kick an MRQ render. Frames land as out_dir/frame_<n>.png. Returns immediately — poll
    unreal._MT_RENDER_DONE. `end` is EXCLUSIVE.

    `quality` (None = the session's defaults) is a dict of opt-in render overrides for close-up work: 'aa' =
    (spatial, temporal) samples with the AA override on, 'cvars' = {console variable: value} for the render,
    'lod_zero' = True, 'cinematic' = True/False (MRQ's Cinematic scalability tier or the engine's). A LEAN session
    (editor_session.py start, the default) renders without hardware ray tracing and at the engine's
    tier; the shipped shots were rendered with neither, so re-render one only from `editor_session.py start
    --full` (and quality=None) to reproduce it. Never set it for the crowd take (see use_lod_zero below).

    A driver's TRANSCRIPT lands here too, and that is deliberate — see `mt_drive`'s capture-evidence
    block. The driver does not know this path; it is handed the directory by the arm below and can
    write nowhere else, so a driver exec'd by a harness that did not MEAN to write anywhere real (a
    test stub, which is how this fault first appeared) cannot reach a shipped shot's evidence. That is
    the fault this stops, and the scope is exact: a caller that deliberately arms — by calling
    `arm_capture_evidence` itself, or forging the slot — still gets a real path, and `shot_check`'s
    evidence gate rather than this arm is what catches that."""
    mt = _mt_drive()

    # VALIDATE BEFORE DELETING. The clear below now removes `*.txt` as well
    # as `*.png`, i.e. it removes EVIDENCE — so a mistyped `out_dir` destroys a shipped shot's
    # transcript and the only thing that would notice is the manifest gate's absent-file red, after the
    # fact. Every real caller renders into the repo's own `captures/`, so requiring that is free here
    # and turns a typo into a refusal instead of a deletion.
    repo = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()).rstrip('/')
    resolved = os.path.abspath(out_dir).replace(os.sep, '/')
    captures = (repo + '/captures').lower()
    if not resolved.lower().startswith(captures + '/'):
        raise RuntimeError(
            'REFUSING to render into %r: it resolves to %r, which is not under %s. This function '
            'CLEARS its output directory, transcripts included, before it renders anything.'
            % (out_dir, resolved, repo + '/captures'))

    os.makedirs(out_dir, exist_ok=True)
    # Stale FRAMES would make the count dishonest; a stale TRANSCRIPT is worse, because it composites
    # cleanly and looks right. They belong to the same run, so they are cleared together: a render
    # whose driver never reaches its dump now leaves NO evidence file, and `terminal_capture` refuses
    # a missing file by name instead of burning last take's readings over this take's footage.
    # DO NOT CLEAR A DIRECTORY THE SEQUENCE CURRENTLY SHIPS FROM. The clear below is right for a fresh
    # take and wrong for a re-shoot, because it is NOT atomic: the frames go first and the render can
    # still fail, hang, or be killed, and several shipped shots are not re-shootable at all (the cold open's
    # camera driver survives nowhere in this project - see shots_manifest's _why_a_rebuild_will_not_
    # reproduce_this). Render the new take into a NEW directory, verify it, then repoint the manifest.
    # That ordering also keeps the assembly gate honest: assembly_fingerprint hashes source_dir and
    # frame_range, NOT the frame bytes, so re-rendering in place can leave the old MP4 and its stamp
    # silently valid. Pass allow_overwrite_shipped=True only when losing the existing take is intended.
    if not allow_overwrite_shipped and _norm_dir(out_dir) in _shipped_source_dirs(repo):
        # COUNT WHAT THE LOOP BELOW ACTUALLY DELETES, which is *.png AND *.txt. Guarding on frames
        # alone meant a directory holding only a transcript - frames pruned after assembly, or a
        # previous run killed between the delete and the render - had `existing == []`, passed the
        # guard, and lost the .txt. This function's own comment says a stale transcript is worse
        # than stale frames; the guard was testing the cheaper of the two artifacts.
        existing = glob.glob(out_dir + '/*.png') + glob.glob(out_dir + '/*.txt')
        if existing:
            raise RuntimeError(
                'REFUSING to clear %r: the shot-sequence manifest depends on %d file(s) in it (frames and/or '
                'pinned evidence), and this function deletes both BEFORE it renders, so a failed run '
                'would destroy the shipped take. Render into a NEW directory and repoint the manifest, '
                'or pass allow_overwrite_shipped=True.' % (out_dir, len(existing)))

    for f in glob.glob(out_dir + '/*.png') + glob.glob(out_dir + '/*.txt'):
        os.remove(f)

    if driver_path:
        mt.arm_capture_evidence(out_dir, driver_path)
        exec(open(driver_path).read(), {'unreal': unreal})

    sub = unreal.get_editor_subsystem(unreal.MoviePipelineQueueSubsystem)
    queue = sub.get_queue()
    queue.delete_all_jobs()
    job = queue.allocate_new_job(unreal.MoviePipelineExecutorJob)
    job.set_editor_property('job_name', job_name)
    job.map = unreal.SoftObjectPath(level)
    job.sequence = unreal.SoftObjectPath(sequence)

    cfg = job.get_configuration()
    cfg.find_or_add_setting_by_class(unreal.MoviePipelineDeferredPassBase)
    cfg.find_or_add_setting_by_class(unreal.MoviePipelineImageSequenceOutput_PNG)
    o = cfg.find_or_add_setting_by_class(unreal.MoviePipelineOutputSetting)
    o.output_directory = unreal.DirectoryPath(out_dir)
    o.output_resolution = unreal.IntPoint(*resolution)
    o.file_name_format = 'frame_{frame_number}'
    o.use_custom_playback_range = True
    o.custom_start_frame = start
    o.custom_end_frame = end
    go = cfg.find_or_add_setting_by_class(unreal.MoviePipelineGameOverrideSetting)
    # The render game mode is the setting's own default (SoftGameModeOverride = MoviePipelineGameMode,
    # MoviePipelineGameOverrideSetting.h:33). The old GameModeOverride field is deprecated since 5.5.
    # FLUSH STREAMING BEFORE EACH FRAME. One render's early frames carried
    # UNSTREAMED LOD MESHES, and two takes of the same stage differing by 0.66/255 — the class of
    # artifact that looks like an animation bug and is not. bFlushStreamingManagers exists precisely
    # for this (MoviePipelineGameOverrideSetting.h:143). It costs render time and buys frames whose
    # content does not depend on how warm the streaming pool happened to be.
    go.flush_streaming_managers = True
    # DELIBERATELY NOT use_lod_zero, though it is right there at the same setting (…:95) and would
    # also stop LOD pop. army_config.json forces a LOD PER CROWD TIER (hero 0, mid 2, far 3) and
    # the crowd take exists to show that tiering holding a budget; forcing LOD 0 across the render would
    # silently overrule the very thing the shot demonstrates, and quietly invalidate its ms figures.
    q = quality or {}
    # MRQ's Cinematic scalability tier unless this is a lean session (editor_session.py marks it -MTLean) or the caller
    # says otherwise: lean renders at the engine's own tier, a fraction of the transient memory for the same frames
    lean = '-mtlean' in unreal.SystemLibrary.get_command_line().lower()
    go.cinematic_quality_settings = q.get('cinematic', not lean)
    if q.get('lod_zero'):
        go.use_lod_zero = True
    if 'aa' in q:
        aa = cfg.find_or_add_setting_by_class(unreal.MoviePipelineAntiAliasingSetting)
        aa.spatial_sample_count, aa.temporal_sample_count = q['aa']
        aa.override_anti_aliasing = True
        aa.anti_aliasing_method = unreal.AntiAliasingMethod.AAM_NONE   # the accumulated samples ARE the AA
    if q.get('cvars'):
        cv = cfg.find_or_add_setting_by_class(unreal.MoviePipelineConsoleVariableSetting)
        for name, value in sorted(q['cvars'].items()):
            cv.add_or_update_console_variable(name, float(value))

    unreal._MT_RENDER_DONE = False
    unreal._MT_RENDER_SUCCESS = None
    # The editor's own viewports stop drawing while the render runs, and come back however it ends (finished, failed,
    # cancelled): two scenes on one GPU doubled every big pool (URenderSessionTools).
    unreal.RenderSessionTools.set_editor_viewports_realtime(False)

    def _fin(exe, success):
        unreal.RenderSessionTools.set_editor_viewports_realtime(True)
        unreal._MT_RENDER_DONE = True
        unreal._MT_RENDER_SUCCESS = bool(success)
        unreal._MT_DRIVE_GEN = int(getattr(unreal, '_MT_DRIVE_GEN', 0)) + 1000   # retire the driver
        # Close the evidence door with the same call that retires the driver. A driver gets exactly
        # one tick to notice the generation bump, and the write path must be shut for that tick too.
        # Through mt_drive, NOT by assigning the slot name here: this file duplicated the
        # `_MT_CAPTURE_ARM` literal in two places, so renaming `mt_drive._ARM_SLOT` would have left the
        # door silently open after every render — a duplicated key in the one place whose whole job is
        # not duplicating keys.
        mt.disarm_capture_evidence()
        unreal.log('MT RENDER FINISHED %s success=%s' % (job_name, success))

    def _err(exe, pipeline, is_fatal, msg):
        unreal.log_warning('MT RENDER ERROR fatal=%s msg=%s' % (is_fatal, msg))
        # A FATAL aborts the pipeline, and MRQ does not reliably fire on_executor_finished afterwards.
        # Every caller in Scripts/ drives this by polling `unreal._MT_RENDER_DONE`, so before this the
        # only outcome of a fatal was a poll that never terminated, with the reason buried in a 200 MB
        # Output Log. Set the same two flags the Finished path sets — DONE so the wait ends, SUCCESS
        # False so it ends with the right answer — and retire the driver generation the way _fin does,
        # or a slate pre-tick driver keeps ticking against a render that is gone. Non-fatal errors are
        # deliberately left alone: the pipeline is still running and the render can still succeed.
        if is_fatal:
            unreal.RenderSessionTools.set_editor_viewports_realtime(True)
            unreal._MT_RENDER_DONE = True
            unreal._MT_RENDER_SUCCESS = False
            unreal._MT_RENDER_ERROR = str(msg)
            unreal._MT_DRIVE_GEN = int(getattr(unreal, '_MT_DRIVE_GEN', 0)) + 1000
            mt.disarm_capture_evidence()

    try:
        ex = unreal.MoviePipelinePIEExecutor()
        ex.on_executor_finished_delegate.add_callable(_fin)
        ex.on_executor_errored_delegate.add_callable(_err)
        unreal._MT_EXECUTOR = ex                               # prevent GC
        sub.render_queue_with_executor_instance(ex)
    except Exception:
        unreal.RenderSessionTools.set_editor_viewports_realtime(True)   # a render that never started paused nothing
        raise
    unreal.log('MT render kicked: %s %s..%s -> %s' % (job_name, start, end, out_dir))
