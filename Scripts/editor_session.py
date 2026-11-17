"""Start and stop the automation editor the same way every time, from a normal shell.

    python Scripts/editor_session.py start      # launch lean (no hardware ray tracing), wait until its MCP server answers
    python Scripts/editor_session.py start --full   # the project's own rendering, for a final look on a free GPU
    python Scripts/editor_session.py stop       # quit, refusing while anything is unsaved

start launches -unattended: the engine checks it before raising a native message box no tool can answer (the
"Wait for ZenServer?" prompt, ZenServerInterface.cpp:2400, can block a session for hours), and the
local ZenServer outlives the editor (Config/DefaultEngine.ini [Zen.AutoLaunch]), so a relaunch finds it running.
stop never kills the process: it asks the editor to quit, and only when no package is dirty (it lists them instead).

The editor is heavy: avoid running it alongside other GPU-heavy work, and keep the machine usable while it runs.
The known causes of slowdowns are 4K renders, a per-frame render flush (both gone) and VRAM overcommitted by other
GPU work, which pages GPU memory through system memory and stalls the desktop compositor with it. So:
  - the editor starts at below-normal CPU priority, and every process it starts (shader compilers, PIE) inherits it, as
    do the encodes of a release or render that calls yield_cpu(); its GPU scheduling is below-normal too (yield_gpu);
  - it starts LEAN (below) and its viewports stop drawing while a render runs (mrq_render, URenderSessionTools),
    which leaves more VRAM free mid-render;
  - start refuses when the GPU has less free than the editor needs plus headroom (EDITOR_VRAM_MB, VRAM_HEADROOM_MB),
    rather than overcommit it;
  - the editor and its children never get the last DESKTOP_CORES logical processors (leave_cores);
  - a drive that renders nothing draws nothing (dyn_run._draw_nothing: the editor viewports and the play world's
    game viewport stop drawing for the run).
"""
import os
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import unreal_mcp  # noqa: E402
import wait_for_editor  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPROJECT = os.path.join(REPO, 'MotionTerminal.uproject')


def editor_exe():
    """UnrealEditor.exe of the engine the .uproject names, found the way run_verification finds the engine
    (MT_UE_ROOT, the registry, Epic's launcher manifest, the conventional install path)."""
    from run_verification import resolve_engine
    return os.path.join(str(resolve_engine()), 'Engine', 'Binaries', 'Win64', 'UnrealEditor.exe')


PORT = 8000                 # the editor's own MCP server (unreal_mcp.DEFAULT_URL)
# The server does not start by itself, so the launch asks for it. It has no authentication: it must stay on
# this machine (the engine's default bind), which is why nothing here passes an address.
MCP_SERVER = ['-ModelContextProtocolStartServer', '-ModelContextProtocolPort=%d' % PORT]
START_TIMEOUT_S, STOP_TIMEOUT_S = 900, 180
SETTLE_S = 20               # the server listens before the editor has finished loading the default map
# What the lean editor took through a 1080p render at its lowest point, rounded up (the non-lean editor took more)
EDITOR_VRAM_MB = 6500
VRAM_HEADROOM_MB = 1500     # left to other GPU users (the render's own guard, dyn_render.VRAM_GUARD_MB)
BELOW_NORMAL = 0x00004000   # BELOW_NORMAL_PRIORITY_CLASS: children inherit it (unlike NORMAL or above)
# The automation editor starts LEAN: hardware ray tracing off for the session (a command-line ini override: the
# project's own settings are untouched), so Lumen traces in software against the distance fields the project already
# builds. A render once stalled with the GPU idle and the desktop crawling, its breadcrumbs stuck in
# RayTracingDynamicGeometryUpdate (two cloth-wearing pawns' ray-tracing geometry rebuilt every frame) with nearly all
# of VRAM in use. The virtual shadow map page pool is the engine default (the project's scalability doubled it to
# 4096 pages: 1 GB per scene, rhi.DumpResourceMemory mid-render). `start --full` keeps the project's rendering for a
# final look on an uncontended GPU.
# -MTLean marks the session for mrq_render, which then renders at the engine's own scalability rather than MRQ's
# Cinematic tier (on the same take: less transient render memory, 11% faster, frames within run-to-run
# noise: mean difference 0.84/255, the cloth's own).
LEAN = ['-ini:Engine:[/Script/Engine.RendererSettings]:r.RayTracing=False,'
        '[ConsoleVariables]:r.Shadow.Virtual.MaxPhysicalPages=2048', '-MTLean']


def gpu_free_mb():
    """Free memory on GPU 0 in MB (nvidia-smi), or None when it cannot be read."""
    try:
        out = subprocess.run(['nvidia-smi', '--query-gpu=memory.free', '--format=csv,noheader,nounits', '-i', '0'],
                             capture_output=True, text=True, timeout=10)
        return int(out.stdout.strip().splitlines()[0])
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def yield_gpu(pid):
    """The process's GPU scheduling at below-normal (D3DKMTSetProcessSchedulingPriorityClass), so the desktop's own
    GPU work goes first while it renders flat out (a render saturating the GPU starves the compositor). The GPU twin
    of yield_cpu. Raises when Windows refuses."""
    if os.name != 'nt':
        return
    import ctypes
    from ctypes import wintypes
    k32, gdi = ctypes.windll.kernel32, ctypes.windll.gdi32
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    gdi.D3DKMTSetProcessSchedulingPriorityClass.argtypes = (wintypes.HANDLE, ctypes.c_int)
    handle = k32.OpenProcess(0x0200 | 0x0400, False, pid)          # PROCESS_SET_INFORMATION | QUERY_INFORMATION
    if not handle:
        raise OSError('EDITOR_SESSION cannot open process %d to lower its GPU priority' % pid)
    try:
        status = gdi.D3DKMTSetProcessSchedulingPriorityClass(handle, 1)   # D3DKMT_SCHEDULINGPRIORITYCLASS_BELOW_NORMAL
        if status != 0:
            raise OSError('EDITOR_SESSION D3DKMTSetProcessSchedulingPriorityClass returned 0x%08x' % (status & 0xFFFFFFFF))
    finally:
        k32.CloseHandle(handle)


# Logical processors the editor never gets (Unreal load on top of everything else running slows the
# system down): below-normal priority still lets it fill every core, and the desktop and
# other applications then wait on the scheduler. Its children (shader compilers, PIE) inherit the
# affinity.
DESKTOP_CORES = 8


def leave_cores(pid, keep=DESKTOP_CORES):
    """Pin the process to all but `keep` logical processors (the last ones, left to the desktop). Raises when Windows
    refuses; a machine with no more than `keep` processors is left as it is."""
    if os.name != 'nt' or (os.cpu_count() or 0) <= keep:
        return
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k32.SetProcessAffinityMask.argtypes = (wintypes.HANDLE, ctypes.c_size_t)
    handle = k32.OpenProcess(0x0200 | 0x0400, False, pid)          # PROCESS_SET_INFORMATION | QUERY_INFORMATION
    if not handle:
        raise OSError('EDITOR_SESSION cannot open process %d to limit its cores' % pid)
    try:
        if not k32.SetProcessAffinityMask(handle, (1 << (os.cpu_count() - keep)) - 1):
            raise OSError('EDITOR_SESSION SetProcessAffinityMask failed (%d)' % ctypes.get_last_error())
    finally:
        k32.CloseHandle(handle)


def yield_cpu():
    """This process, and every process it starts from now on (ffmpeg, the loop cut), at below-normal CPU priority."""
    if os.name == 'nt':
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = ctypes.c_void_p        # a pseudo-handle (-1): truncated as a C int it is not
        k32.SetPriorityClass.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
        if not k32.SetPriorityClass(k32.GetCurrentProcess(), BELOW_NORMAL):
            raise OSError('SetPriorityClass failed (%d)' % ctypes.get_last_error())


def _running():
    out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq UnrealEditor.exe', '/NH'], capture_output=True, text=True)
    if out.returncode != 0:     # a failed query is not "not running"
        raise SystemExit('EDITOR_SESSION tasklist failed: %s' % (out.stderr or out.stdout).strip())
    return 'UnrealEditor.exe' in out.stdout


def _py(script, timeout=60):
    """Run `script` in the editor (execute_python) and return the value of its final expression."""
    with unreal_mcp.Client(timeout=timeout) as client:
        return client.execute_python(script).value()


def _same_file(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _this_project():
    """The editor answering on the port has THIS project file loaded (a listener is not readiness, and another
    checkout of the same project is not this one: the full path is compared, not the file name). The tools are
    listed before any is called: the server answers before every toolset has registered, and a call to a missing
    one is logged by the editor as an error."""
    try:
        if wait_for_editor.missing():
            return False
        path = _py("import json\nimport unreal\n"
                   "json.dumps(unreal.Paths.convert_relative_path_to_full(unreal.Paths.get_project_file_path()))\n",
                   timeout=30)
    except unreal_mcp.McpError:
        return False
    return isinstance(path, str) and _same_file(path, UPROJECT)


def _listening():
    try:
        with socket.create_connection(('127.0.0.1', PORT), timeout=2):
            return True
    except OSError:
        return False


def start(full=False):
    if _running():
        raise SystemExit('EDITOR_SESSION an editor is already running: stop it first')
    free = gpu_free_mb()
    if free is not None and free < EDITOR_VRAM_MB + VRAM_HEADROOM_MB:
        raise SystemExit('EDITOR_SESSION the GPU has %d MB free; the editor needs ~%d MB plus %d MB headroom. Free some '
                         'GPU memory or wait' % (free, EDITOR_VRAM_MB, VRAM_HEADROOM_MB))
    proc = subprocess.Popen([editor_exe(), UPROJECT, '-unattended'] + MCP_SERVER + ([] if full else LEAN), close_fds=True,
                            creationflags=BELOW_NORMAL if os.name == 'nt' else 0)
    try:
        leave_cores(proc.pid)       # at once: before it starts its workers and shader compilers
    except OSError as e:            # said loudly, but the editor is up either way (as yield_gpu)
        print('EDITOR_SESSION warning: cores not limited (%s)' % e)
    began = time.time()
    while not (_listening() and _this_project()):
        if time.time() - began > START_TIMEOUT_S or proc.poll() is not None:
            raise SystemExit('EDITOR_SESSION the editor did not come up within %d s (exit code %s)'
                             % (START_TIMEOUT_S, proc.poll()))
        time.sleep(5)
    time.sleep(SETTLE_S)
    if proc.poll() is not None:
        raise SystemExit('EDITOR_SESSION the editor exited after starting (exit code %s)' % proc.poll())
    try:
        yield_gpu(proc.pid)         # once up: Windows refuses it on a process with no GPU device yet (0xc000000d)
    except OSError as e:            # a courtesy to the desktop, not a precondition: the editor is up either way
        print('EDITOR_SESSION warning: GPU priority not lowered (%s)' % e)
    print('EDITOR_SESSION up in %d s (%s)' % (time.time() - began, 'full' if full else 'lean: no hardware ray tracing'))


def stop():
    if not _running():
        print('EDITOR_SESSION not running')
        return
    # A drive stopped mid-run (for example, closed to free resources) leaves PIE up and its placed pawns
    # (tagged MT_KDD) in the level: PIE is ended and the drive's own cleanup is done for it, as its `clear` does: the
    # tagged pawns removed, the level saved only when the drive recorded it clean before placing them
    # (_MT_KDD_LEVEL_WAS_CLEAN). Anything still dirty after that is someone's unsaved work, and stop refuses.
    _py("import json\nimport unreal\n"
        "les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)\n"
        "json.dumps(bool(les.is_in_play_in_editor() and (les.editor_request_end_play() or True)))\n")
    time.sleep(3)                   # PIE ends on the next editor tick
    dirty = _py(
        "import json\nimport unreal\n"
        "sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)\n"
        "drive = [a for a in sub.get_all_level_actors() if unreal.Name('MT_KDD') in a.tags]\n"
        "[sub.destroy_actor(a) for a in drive]\n"
        "if drive and getattr(unreal, '_MT_KDD_LEVEL_WAS_CLEAN', False):\n"
        "    unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).save_current_level()\n"
        "json.dumps([p.get_name() for p in unreal.EditorLoadingAndSavingUtils.get_dirty_content_packages()] + "
        "[p.get_name() for p in unreal.EditorLoadingAndSavingUtils.get_dirty_map_packages()])\n")
    if not isinstance(dirty, list):
        raise SystemExit('EDITOR_SESSION could not read the unsaved packages (got %r): not quitting' % (dirty,))
    if dirty:
        raise SystemExit('EDITOR_SESSION not quitting, unsaved: %s' % dirty)
    try:                            # its own call: the editor may exit before it answers this one
        _py("import json\nimport unreal\nunreal.SystemLibrary.quit_editor()\njson.dumps(True)\n")
    except unreal_mcp.McpError:
        pass                        # whether it quit is read off the process list below, not off this reply
    began = time.time()
    while _running():
        if time.time() - began > STOP_TIMEOUT_S:
            raise SystemExit('EDITOR_SESSION the editor did not exit within %d s' % STOP_TIMEOUT_S)
        time.sleep(3)
    print('EDITOR_SESSION stopped')


if __name__ == '__main__':
    if sys.argv[1] == 'start':
        start(full='--full' in sys.argv[2:])
    else:
        {'stop': stop}[sys.argv[1]]()
