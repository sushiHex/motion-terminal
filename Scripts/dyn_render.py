"""Render the ON/OFF harness run under MRQ-over-PIE at the ON/OFF comparison framing, from a normal shell (editor up).

    python Scripts/dyn_render.py --hero <Rig> <out_dir under captures/> [tag] [--res=3840x2160]

The render cancels itself if the GPU falls under VRAM_GUARD_MB free or no frame lands for STALL_S.

Reuses the army stage's existing Seq_Army (60 fps, one spawnable camera, no transform track: built for a live
driver, shot_seq_author.py) and mrq_render.render_shot. The driver is registered here, with its config, rather
than handed to render_shot, because render_shot execs a driver into a bare namespace that cannot carry one. The
driver flies the camera and writes its record as usual, so the render leaves the same JSON a PIE run does, which
is what the MRQ-vs-PIE determinism comparison (docs/specs/2026-10-06-khaimera-cr-dynamics-design.md) reads. Frames: <out_dir>/frame_<n>.png; then an MP4 beside
them (ffmpeg, 60 fps).
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dyn_hero  # noqa: E402
import editor_ops  # noqa: E402
from editor_session import gpu_free_mb, yield_cpu  # noqa: E402
from dyn_run import STAGE, _exec  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__))).replace('\\', '/')
SEQUENCE = '/Game/MT/Army/Seq_Army'
FRAMES = 450          # the drive's settle 2 s + run 3 s + hold 2 s = 420 frames at 60 fps, plus margin (it holds on)
TIMEOUT_S = 1800


def _py(cmd, timeout=600):
    return editor_ops.shared().py(cmd, timeout=timeout)


POLL_TIMEOUT_S = 60       # a render-state poll; an editor that cannot answer one in this long counts as not progressing


# Rendered at 1080p by default: 4K renders exhaust the VRAM headroom and starve the rest of the desktop. --res=3840x2160 for a
# final look on an uncontended GPU, downscaled (lanczos) to the 1080p MP4s (native 1080p
# read as soft): Laplacian variance over the near pawn's chest, native 1080p 200.1, 4K downscaled 295.4, 1080p with 4
# spatial samples and no TAA 200.6 (and more aliasing).
RESOLUTION = (1920, 1080)
DELIVERY = '1920:1080'
VRAM_GUARD_MB = 1500      # cancel the render when the GPU has less free than this (keeps render VRAM use modest)
# ...or when no new frame lands for this long: a GPU contended but not short of memory rendered at ~0.05 fps and every
# take then ran to TIMEOUT_S (2026-10-31: the editor stuck on one frame with GPU-breadcrumb warnings, Windows crawling).
# Before the first frame (PIE start, MRQ's asset compile and streaming, warm-up samples) the allowance is longer.
STALL_S = 300
FIRST_FRAME_S = 900


def render(hero, out_dir, tag, frames=FRAMES, extra=None, resolution=RESOLUTION, outputs=('full', 'loop'),
           overlay=None, quality=None):
    """extra: driver CONFIG keys (e.g. cam_focus). outputs: the MP4s encoded beside the frames ('full', and 'loop' the
    looping cut): a caller that cuts its own clips from the frames asks for none, and no encode runs for nothing.
    overlay: an image composited into the loop (a legend), in its one encode. quality: mrq_render.render_shot's
    (e.g. {'cinematic': True} for MRQ's Cinematic tier in a lean session)."""
    yield_cpu()             # the encodes below run under the editor's priority, not over the desktop
    cfg = {'tag': tag, 'hero': hero, 'alphas': [1.0, 0.0], 'fixed_hz': None, 'camera': True}
    cfg.update(extra or {})
    editor_ops.shared().reset_level(STAGE)
    _exec('place', cfg, timeout=600)
    try:
        # The driver registers first and waits for the render's PIE world; the render starts in the next call.
        _exec('drive', cfg, timeout=600)
        _py("import importlib, sys\nsys.path.insert(0, r'%s/Scripts')\n"
            "importlib.reload(importlib.import_module('mrq_render')).render_shot("
            "%r, %r, r'%s', 0, %d, driver_path=None, job_name='KhaiDyn_S7', resolution=%r, quality=%r)"
            % (REPO, STAGE, SEQUENCE, out_dir, frames, tuple(resolution), quality))
        started = progressed = time.time()
        landed, line, low = 0, '', None
        while time.time() - started < TIMEOUT_S:
            time.sleep(5.0)
            free = gpu_free_mb()
            low = free if low is None or (free is not None and free < low) else low
            # this render's frames only: a caller that did not clear out_dir has older ones under the same names
            now = sum(1 for e in (os.scandir(out_dir) if os.path.isdir(out_dir) else ())
                      if e.name.startswith('frame_') and e.stat().st_mtime >= started)
            if now > landed:
                landed, progressed = now, time.time()
            # A render that leaves little VRAM free stalls the desktop and the render itself. Stop it rather than starve the machine.
            why = ('GPU free %d MB < %d MB' % (free, VRAM_GUARD_MB) if free is not None and free < VRAM_GUARD_MB else
                   'no new frame in %d s (%d landed)' % (STALL_S if landed else FIRST_FRAME_S, landed)
                   if time.time() - progressed > (STALL_S if landed else FIRST_FRAME_S) else None)
            if why:
                print(json.dumps({'render_cancelled': why}))
                line = ''
                break           # the finally below cancels this render's job
            try:
                done, success = _py("[getattr(unreal, '_MT_RENDER_DONE', None), "
                                    "getattr(unreal, '_MT_RENDER_SUCCESS', None)]", timeout=POLL_TIMEOUT_S)
            except editor_ops.TRANSIENT as e:   # an editor too busy to answer: the stall clock runs on
                print(json.dumps({'render_poll_timeout': str(e)[:200]}))
                continue
            line = 'RENDER %s %s' % (done, success)
            if done is True:
                break
        ok = line == 'RENDER True True'
    finally:
        # A render left running (a timeout, an interrupted caller) kept rendering into the next one: the next release's
        # MRQ job stalled the GPU (compute payloads >5 s) and its clear could not save the stage (2026-10-30). Whatever
        # ended this call, its own job (mrq_render keeps it as unreal._MT_EXECUTOR) does not outlive it; another
        # caller's is left alone, and a failed cancel neither masks the result nor skips the clear.
        try:
            _py("q = unreal.get_editor_subsystem(unreal.MoviePipelineQueueSubsystem)\n"
                "ex = q.get_active_executor()\n"
                "ex and ex == getattr(unreal, '_MT_EXECUTOR', None) and q.is_rendering() and ex.cancel_all_jobs()",
                timeout=POLL_TIMEOUT_S)
        except Exception as e:      # noqa: BLE001
            # an editor that cannot take a cancel is wedged: stop it (Scripts/editor_session.py stop)
            print(json.dumps({'render_cancel_failed': str(e)[:300]}))
        _exec('clear', cfg, timeout=600)
    requested = frames
    frames = sorted(f for f in os.listdir(out_dir) if f.startswith('frame_') and f.endswith('.png'))
    # every frame asked for: a truncated sequence still encoded and passed the frame-contiguity check
    ok = ok and len(frames) == requested
    print(json.dumps({'render_ok': ok, 'frames': len(frames), 'requested': requested, 'out_dir': out_dir,
                      'gpu_free_min_mb': low}))
    if ok and frames and 'full' in outputs:
        mp4 = os.path.join(out_dir, 'dyn_%s_%s.mp4' % (hero, tag))
        first = int(frames[0][len('frame_'):-len('.png')])
        subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-framerate', '60', '-start_number', str(first),
                        '-i', os.path.join(out_dir, 'frame_%04d.png'), '-vf', 'scale=%s:flags=lanczos' % DELIVERY,
                        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '16', mp4], check=True)
        print('MP4 ' + mp4)
    if ok and frames and 'loop' in outputs:
        # The looping cut: the closing stand flows into the opening one; loop_cut prints its seam figures.
        # Not fatal: a short test render with no run in it has no two stands to loop between.
        subprocess.run([sys.executable, os.path.join(REPO, 'Scripts', 'loop_cut.py'), out_dir,
                        os.path.join(out_dir, 'dyn_%s_%s_loop.mp4' % (hero, tag)), '--scale', DELIVERY]
                       + (['--overlay', overlay] if overlay else []), check=False)
    return ok


if __name__ == '__main__':
    hero, argv = dyn_hero.hero_arg(sys.argv[1:])
    dyn_hero.resolve(hero)
    args = [a for a in argv if not a.startswith('--res=')]
    res = next((tuple(int(v) for v in a[len('--res='):].split('x')) for a in argv if a.startswith('--res=')),
               RESOLUTION)
    out = os.path.abspath(args[0]).replace('\\', '/')
    sys.exit(0 if render(hero, out, args[1] if len(args) > 1 else 'look', resolution=res) else 1)
