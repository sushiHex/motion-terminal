#!/usr/bin/env python
"""One-command verification runner.

Tiers:
  default : headless (-NullRHI). Topology + golden-diff suites
            ("MotionTerminal.Verify." and "MotionTerminal.GoldenDiff.").
  pie     : local, real RHI (no -NullRHI). PIE assertions ("MotionTerminal.Pie.").

Engine automation traps honored:
  - UE 5.8 renamed -ReportOutputPath -> -ReportExportPath.
  - Process exit codes are ~always 0: index.json is the source of truth.
  - -TestExit="Automation Test Queue Empty" prevents ;Quit races.
"""
import argparse
import calendar
import json
import os
import subprocess
import threading
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PROJECT = REPO / "MotionTerminal.uproject"

# ============================ THE ENGINE PATH ============================
#
# This module used to open with `ENGINE = Path(r"C:/Program Files/Epic Games/UE_5.8")`, and that made
# the project's ONE-COMMAND VERIFICATION RUNNER — the file every claim in this repo is ultimately read
# out of — unrunnable on any machine whose engine is not at the stock Windows install path. It was the
# third recurrence of the same family in three passes (one sweep found five drivers hardcoding the
# CHECKOUT root, a follow-up found `clip_varies_check.py` after that sweep was declared clean, and a
# clean-clone run found this one).
#
# Resolved instead of assumed, in this order, and the first candidate that actually holds a
# `UnrealEditor-Cmd.exe` wins — a registry key or a manifest entry pointing at a moved/deleted install
# must not shadow a good later candidate:
#
#   1. $MT_UE_ROOT                    explicit override; the answer to "it is somewhere else"
#   2. HKLM EpicGames\Unreal Engine\<assoc>\InstalledDirectory      (+ the WOW6432Node mirror)
#   3. HKCU Epic Games\Unreal Engine\Builds\<assoc>                 source builds; assoc is a GUID
#   4. C:/ProgramData/Epic/UnrealEngineLauncher/LauncherInstalled.dat, AppName == "UE_<assoc>"
#   5. the conventional launcher path, C:/Program Files/Epic Games/UE_<assoc>
#
# <assoc> is READ OUT OF THE .uproject (`EngineAssociation`) rather than written here, so the runner
# follows the project to 5.9 without an edit — the same reason `REPO` derives from `__file__`.
_ENGINE_ENV = "MT_UE_ROOT"
_LAUNCHER_MANIFEST = Path(r"C:/ProgramData/Epic/UnrealEngineLauncher/LauncherInstalled.dat")


def _editor_cmd(engine_root):
    return Path(engine_root) / "Engine" / "Binaries" / "Win64" / "UnrealEditor-Cmd.exe"


def engine_association(project=PROJECT):
    """The .uproject's EngineAssociation ('5.8', or a GUID for a source build). '' if unreadable —
    an unparseable .uproject is not this function's error to raise."""
    try:
        return str(json.loads(project.read_text(encoding="utf-8-sig")).get("EngineAssociation", ""))
    except Exception:
        return ""


def _registry_candidates(assoc):
    """Windows registry, both the launcher key and the source-build key. Silent on any failure:
    winreg does not exist off Windows and a missing key is the normal case, not an error."""
    out = []
    try:
        import winreg
    except ImportError:
        return out
    for hive, key, value in (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\EpicGames\Unreal Engine\%s" % assoc, "InstalledDirectory"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\WOW6432Node\EpicGames\Unreal Engine\%s" % assoc, "InstalledDirectory"),
        (winreg.HKEY_CURRENT_USER, r"Software\Epic Games\Unreal Engine\Builds", assoc),
    ):
        try:
            with winreg.OpenKey(hive, key) as k:
                out.append(Path(str(winreg.QueryValueEx(k, value)[0])))
        except OSError:
            pass
    return out


def _launcher_candidates(assoc):
    """Epic's own install manifest — the authoritative record for launcher installs, and the one that
    survives a non-default install directory. Keyed `UE_<assoc>`; the file also lists plugins
    (QuixelBridge_5.8, FabPlugin_5.8) against the same directory, so match the AppName exactly."""
    try:
        data = json.loads(_LAUNCHER_MANIFEST.read_text(encoding="utf-8-sig"))
    except Exception:
        return []
    return [Path(str(i.get("InstallLocation", "")))
            for i in data.get("InstallationList", []) or []
            if str(i.get("AppName", "")) == "UE_%s" % assoc and i.get("InstallLocation")]


def resolve_engine(assoc=None):
    """The engine root, or raise with the exact thing to set. Never returns an unverified path."""
    assoc = assoc if assoc is not None else engine_association()
    tried = []
    override = os.environ.get(_ENGINE_ENV)
    candidates = ([Path(override)] if override else []) + _registry_candidates(assoc) \
        + _launcher_candidates(assoc) + [Path(r"C:/Program Files/Epic Games/UE_%s" % assoc)]
    for c in candidates:
        tried.append(str(c))
        if _editor_cmd(c).exists():
            return c
    # Name the override AND what was searched: "engine not found" sends a reader hunting, whereas the
    # list of rejected paths usually contains the answer (a moved install, a stale registry entry).
    raise SystemExit(
        "ERROR: could not locate Unreal Engine %s (EngineAssociation in %s).\n"
        "Set %s to the engine root - the directory that CONTAINS Engine/Binaries/Win64/"
        "UnrealEditor-Cmd.exe, e.g.\n"
        "    %s=\"C:/Program Files/Epic Games/UE_%s\"\n"
        "Searched, in order, and none held that executable:\n  %s"
        % (assoc or "<unset>", PROJECT, _ENGINE_ENV, _ENGINE_ENV, assoc or "5.8",
           "\n  ".join(tried) or "(nothing - no candidates at all)"))

TIER_PREFIXES = {
    "default": "MotionTerminal.Verify.+MotionTerminal.GoldenDiff.",
    "pie": "MotionTerminal.Pie.",
}

# ============================ A TIER THAT REPORTS NOTHING HAS NOT PASSED ============================
#
# `Tests: 0  Failed: 0` reads like a clean run and is the exact opposite of one. One single point of
# failure produces it: the Interchange plugin is a HARD dependency of the whole
# MotionTerminal module (MotionTerminal.Build.cs links InterchangeCore/Engine/Pipelines unconditionally
# and AnimImportTools.cpp instantiates UInterchangeGenericAssetsPipeline with no #if guard), so if
# Interchange is disabled or absent the module does not load and ALL of the default tier's rows vanish
# TOGETHER — not just the import-scale row. The same shape results from a plugin that failed to compile,
# a .uproject with MotionTerminal disabled, or a test-filter typo.
#
# Each manifest-backed suite already refuses to register zero sub-rows (they emit a red `_ManifestLoadFailed`
# sentinel instead). That guards a suite disappearing from INSIDE the module. Nothing guarded the module
# itself disappearing, because there is nobody left inside it to complain. This floor lives outside.
#
# IT IS A FLOOR, NOT AN EXPECTED COUNT: adding rows is normal and must not go red, so a run above the
# floor is fine and unremarked. RAISING it when rows are added is bookkeeping; LOWERING it is a claim
# that the suite legitimately got smaller, and is the thing that should have to be argued for.
#   default 27 = Verify.TopologyRoundTrip 1 + Verify.UnarmedTopology 1 + Verify.ClipVaries 7
#              + Verify.ImportScale 4 + Verify.FixImportScale 1 + Verify.FbxHeaderWalkIntegrity 1
#              + Verify.IKEffectorDrives 4 + Verify.ArmyBudgetZeroGuard 1 + GoldenDiff.* 5
#              + Verify.RifleGateWired 1 (the floor was not raised when it landed)
#              + Verify.AnimUpdateCountDelta 1 (game module, MotionTerminalGame)
#   pie     24 = RigLocomotion 3 + TurnInPlace 3 + AimOffset 3 + FootIKNodeIntegration 2
#              + HandReachesSocket 2 + SkeletonAwareFootIK 2 + 9 singleton rows (Army.BudgetThrottle the 8th,
#              Army.OnCapsule the 9th)
TIER_MIN_ROWS = {"default": 29, "pie": 24}

# ============================ THE COUNTS THE PROSE QUOTES ============================
#
# Plugins/MotionTerminal/Tests/tier_counts.json is the committed record of the last full-tier run, and
# test_repo_integrity checks TIER_MIN_ROWS and the README / verification-design figures against it.
# Schema, one object per tier, merged per tier so a pie run never clobbers default:
#   {"<tier>": {"rows": <registered>}}
# Only machine-independent facts live here, so a run on any machine rewrites the file to the same bytes
# (and a no-change run does not touch it). Exercised/skipped depend on the machine's licensed content;
# the README's "N of M" skip split is clean-clone evidence from clean_checkout_check.py and stays ungated.
TIER_COUNTS = REPO / "Plugins" / "MotionTerminal" / "Tests" / "tier_counts.json"


def _should_record(prefix, failed):
    """Only a whole-tier (no --prefix), green run is evidence of the tier's composition."""
    return prefix is None and not failed


def _record_tier_counts(tier, rows, path=TIER_COUNTS):
    """Merge this tier's row count into tier_counts.json; write only on change. Never raises."""
    try:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data[tier] = {"rows": rows}
        new = (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8")
        try:
            if path.read_bytes() == new:
                return
        except OSError:
            pass
        path.write_bytes(new)
    except OSError as e:
        print(f"(could not record {path.name}: {e})")

_SKIP_MARKER = "SKIP (localOnly"

# ============================ "NO index.json" IS TWO DIFFERENT EVENTS ============================
#
# A missing report means the automation queue never emptied, and that is compatible with a project
# defect AND with the editor dying at startup before a single test registered. One clean-checkout
# investigation spent most of its time on the second one while it looked like the first: the pie tier died at
# the editor's FIRST RENDER TICK with a fatal D3D12 pipeline-state error, on a fresh clone and — later,
# which is what settled it — on the fully-populated working checkout too.
#
# It is ENVIRONMENTAL and it is FLAKY. Ruled out by experiment: VRAM headroom (recurred with
# most of the GPU's memory free), ray tracing (`-noraytracing` and `r.RayTracing=0` both still died), the project's
# `Saved/Config`, and the `.uproject` plugin set. Observed 3 of 8 runs on the working checkout and 10 of
# 13 on a clone in one session, then 0 of 5 on a clone in the next. The remedy is to RE-RUN, and the
# reason this function exists is that a reader who does not know that will otherwise go looking for a
# regression that is not there.
_STARTUP_FATAL_MARKERS = (
    "PipelineStateCache.cpp",              # the fatal's own file
    "Shader compilation failures are Fatal",
    "Failed to create pipeline state with combined hash",
)


def _diagnose_no_report():
    """Say WHICH of the two events happened, by reading the log the run just wrote."""
    log = REPO / "Saved" / "Logs" / "MotionTerminal.log"
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        print(f"       (no editor log at {log} to diagnose from)")
        return
    hits = [m for m in _STARTUP_FATAL_MARKERS if m in text]
    if not hits:
        print(f"       Editor log: {log}  - no known startup-fatal signature. Read it: the queue was "
              f"started and something in it did not finish.")
        return
    print("       DIAGNOSIS: the editor died at STARTUP, before any test ran - this is the known "
          "flaky D3D12 pipeline-state fatal, NOT a project failure.")
    print("       Signature found in %s: %s" % (log, ", ".join(hits)))
    print("       It is environmental and intermittent (an investigation ruled out VRAM headroom, ray "
          "tracing, Saved/Config and the .uproject plugin set). RE-RUN THIS COMMAND.")


# The pie tier runs on a real RHI, and its green depends on GPU headroom that nothing in this repo used to
# report. A 6/22-red run turned out to have almost no VRAM free, with gigabytes spilled to system RAM and the
# driver paging — under which five gated rows fail with messages that
# read like animation defects (FROZEN, "window too sparse"). It fails safe, but it fails ILLEGIBLY. The
# long-suspected "flaky D3D12 PSO crash at startup" is plausibly the same condition seen earlier in the
# launch — real-RHI only, clustering in time, tracking nothing else under our control.
#
# ADVISORY ONLY — never gate on it. A machine with less VRAM is not a defect in this project, and a hard
# floor here would turn someone else's hardware into a red suite.
#
# RECALIBRATED, and the old value is the lesson. At 4096 this advisory stayed SILENT
# through two consecutive red pie runs that were pure GPU paging: they failed with
# free VRAM comfortably ABOVE the threshold, and the same tree then ran 22/0 twice with ample
# headroom. The two reds also failed DIFFERENT rows (RigLocomotion.Quinn + SkeletonAwareFootIK.Quinn,
# then Army.NoFrozen + RigLocomotion.Khaimera/Manny) - identical code, non-overlapping failures,
# which is the tell. A warning threshold set below the level where the harm actually starts is a
# check that cannot observe what it claims, which is the defect class these gates exist to remove.
LOW_VRAM_WARN_MIB = 8192


def _report_gpu_headroom():
    """Print per-GPU headroom; return the smallest free MiB seen, or None if unmeasurable."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total,memory.used,memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        print("GPU: nvidia-smi unavailable - headroom unknown (advisory only, not a failure)")
        return None
    if out.returncode != 0 or not out.stdout.strip():
        print("GPU: nvidia-smi returned nothing - headroom unknown (advisory only, not a failure)")
        return None
    worst = None
    for line in out.stdout.strip().splitlines():
        try:
            total, used, free = (int(x.strip()) for x in line.split(","))
        except ValueError:
            continue
        worst = free if worst is None else min(worst, free)
        note = ""
        if free < LOW_VRAM_WARN_MIB:
            note = ("   <-- LOW. Below ~%d MiB free, this tier produces rows that read like animation "
                    "defects (FROZEN / window too sparse) but are GPU paging. Free VRAM and re-run "
                    "before believing a red." % LOW_VRAM_WARN_MIB)
        print("GPU: %d MiB free of %d (%d used)%s" % (free, total, used, note))
    return worst



class _VramMinSampler:
    """Track the LOWEST free VRAM seen while the tier runs.

    WHY A SAMPLER AND NOT A SECOND READING. The first version of this took
    its second sample immediately after `subprocess.run` returned - which is after the editor process
    has exited and released everything it held. It therefore measured an idle GPU, always came back
    high, and could never lower the minimum: the "sample again afterwards" half was a check that could
    not observe the thing its own comment said it existed to catch ("the pressure that breaks it is
    often absent at launch"). Only the threshold recalibration was doing real work.

    Polling on a daemon thread is the cheap honest fix: nvidia-smi costs milliseconds, the interval is
    seconds, and a daemon thread cannot keep the runner alive if the tier dies. Every failure path
    returns None rather than raising - this is advisory, and an advisory that can break the run it
    advises on is worse than no advisory.
    """

    def __init__(self, interval=1.0):
        self.interval = interval
        self._stop = threading.Event()
        self._min = None
        self._thread = None
        # (epoch seconds, smallest free MiB across GPUs). Kept so a red row can be read against the
        # headroom AT ITS OWN TIME: the run-wide minimum alone cannot say whether the
        # failing row was the one that was starved. ~1 Hz x a few minutes is a few hundred tuples.
        self.series = []

    def _poll_once(self):
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            return
        if out.returncode != 0:
            return
        now = time.time()
        frees = []
        for line in out.stdout.strip().splitlines():
            try:
                frees.append(int(line.strip()))
            except ValueError:
                continue
        if frees:
            self.series.append((now, min(frees)))
            self._min = min(frees) if self._min is None else min(self._min, min(frees))

    def _run(self):
        while not self._stop.is_set():
            self._poll_once()
            self._stop.wait(self.interval)

    def start(self):
        self._poll_once()   # one reading up front, so a very short run still yields a sample
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=20)
        return self._min


# A FAILED row, read against the free VRAM during that row. UE's report carries, per test,
# `dateTime` ("YYYY.MM.DD-HH.MM.SS", UTC, the row's START) and `duration` (seconds), so the row's own
# window is derivable. Padded by ROW_WINDOW_PAD_S because both are whole-second and the sampler is 1 Hz.
ROW_WINDOW_PAD_S = 2.0


def row_window(test):
    """(start, end) epoch seconds for a report row, or None if it carries no usable dateTime."""
    try:
        start = calendar.timegm(time.strptime(str(test.get("dateTime", "")), "%Y.%m.%d-%H.%M.%S"))
        return (start - ROW_WINDOW_PAD_S, start + float(test.get("duration") or 0.0) + ROW_WINDOW_PAD_S)
    except (ValueError, TypeError, OverflowError):
        return None


def min_free_in_window(series, window):
    """Smallest free MiB among (epoch, free) samples inside the inclusive window; None if none."""
    if window is None:
        return None
    vals = [free for ts, free in series if window[0] <= ts <= window[1]]
    return min(vals) if vals else None


def failed_row_vram_lines(failed, series):
    """One advisory line per failed row: min free VRAM during that row, beside the run-wide min.

    Pure, like low_headroom_note. Empty when nothing was sampled (default tier, no nvidia-smi).
    Advisory only; it never changes the exit code."""
    if not series:
        return []
    run_min = min(free for _, free in series)
    lines = []
    for t in failed:
        row_min = min_free_in_window(series, row_window(t))
        shown = "%d MiB" % row_min if row_min is not None else "no sample in window"
        lines.append("VRAM: %s  min free during row: %s  (run-wide min: %d MiB)"
                     % (t.get("fullTestPath"), shown, run_min))
    return lines


def low_headroom_note(failed_count, samples, floor=None):
    """The lines to print when a red MIGHT be GPU paging rather than a code defect.

    Pure on purpose. The inline version of this could only ever be exercised by a real red on a
    loaded GPU, which meant it would ship having never run once - a warning path that cannot be
    demonstrated is the same species of defect as a gate that cannot fail, and this project has
    spent days removing those. As a function it is provable both ways in milliseconds; see
    Scripts/test_repo_integrity.py.

    Returns [] when it should stay quiet: no failures, no usable samples, or ample headroom.
    """
    if floor is None:
        floor = LOW_VRAM_WARN_MIB
    if failed_count <= 0:
        return []
    usable = [v for v in samples if isinstance(v, int)]
    if not usable or min(usable) >= floor:
        return []
    return [
        "",
        "READ THE RED ABOVE WITH THIS IN MIND: GPU headroom fell to %d MiB (warn floor %d)."
        % (min(usable), floor),
        "       Under paging this tier fails with messages that READ LIKE ANIMATION DEFECTS - "
        "FROZEN, 'window too sparse', 'grounded window never developed' - and the failing rows "
        "MOVE between runs on identical code.",
        "       Free VRAM and re-run before bisecting. If the same rows fail twice on a quiet GPU, "
        "it is real. Advisory only: this does not change the exit code.",
    ]

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier", choices=sorted(TIER_PREFIXES), default="default")
    parser.add_argument("--prefix", default=None,
                        help="Override the test filter (Automation RunTests argument).")
    args = parser.parse_args()

    prefix = args.prefix or TIER_PREFIXES[args.tier]
    report = REPO / "Saved" / "TestReports" / time.strftime("%Y%m%d-%H%M%S")
    engine = resolve_engine()

    cmd = [
        str(_editor_cmd(engine)),
        str(PROJECT),
        f"-ExecCmds=Automation RunTests {prefix}",
        "-unattended", "-nop4", "-nosplash", "-nosound", "-log",
        f"-ReportExportPath={report}",
        "-TestExit=Automation Test Queue Empty",
    ]
    vram_before = None
    if args.tier == "default":
        cmd.append("-NullRHI")
    else:
        vram_before = _report_gpu_headroom()

    print("RUN:", subprocess.list2cmdline(cmd))
    # Watch headroom WHILE the tier runs. See _VramMinSampler for why sampling afterwards cannot
    # work: by the time subprocess.run returns, the editor has exited and given its VRAM back.
    sampler = _VramMinSampler().start() if args.tier != "default" else None
    try:
        subprocess.run(cmd, check=False)  # exit code intentionally ignored (see module docstring)
    finally:
        vram_min = sampler.stop() if sampler is not None else None
    vram_series = sampler.series if sampler is not None else []

    index = report / "index.json"
    if not index.exists():
        print(f"ERROR: no index.json at {index} - run did not complete")
        _diagnose_no_report()
        return 2

    data = json.loads(index.read_text(encoding="utf-8-sig"))  # UE writes a BOM
    tests = data.get("tests", []) or []
    failed = [t for t in tests if t.get("state") != "Success"]
    skipped = [t for t in tests if _row_skipped(t)]
    _print_rows(tests)
    _print_warnings(tests)

    # "Tests: 22  Failed: 0" invites the reading "22 things were checked", and on a machine without the
    # licensed content that is false: a localOnly row that SKIPs is registered, green, and asserts
    # NOTHING. Reporting the exercised count beside the registered one is the whole fix — it costs a
    # line and removes the only reading of this summary that was ever wrong.
    #
    # NO SPLIT IS QUOTED HERE ANY MORE. This comment used to say "a clean clone
    # verifies 29 of the two tiers' 44 rows; the other 15 skip" — arithmetic written against 22+22 and
    # not updated when the 23rd default row landed in the same commit, which is the exact class of
    # stale transcribed figure this file's own output exists to make unnecessary. The split is a
    # property of a specific commit on a specific machine, so it belongs where it is MEASURED
    # (clean_checkout_check.py's output, beside the run that produced it) and not in a comment that
    # nothing checks. The two numbers this function prints are always current, because it computes them.
    print(f"Tests: {len(tests)}  Failed: {len(failed)}  "
          f"Skipped(localOnly): {len(skipped)}  Exercised: {len(tests) - len(skipped)}")
    for t in failed:
        print("FAIL:", t.get("fullTestPath"))
        _print_errors(t, index)
    for line in failed_row_vram_lines(failed, vram_series):
        print(line)

    # SAY IT HERE, WHERE THE RED IS, instead of leaving it to be remembered. The start-of-run advisory
    # is printed before hundreds of lines of test output, and once it was both too low to fire
    # AND scrolled far out of sight by the time the failures appeared - so two GPU-paging reds were read
    # as possible code regressions and cost a bisect. Headroom is sampled again AFTER the run because
    # the tier itself allocates: the pressure that breaks it is often absent at launch.
    if failed and args.tier != "default":
        for line in low_headroom_note(len(failed), (vram_before, vram_min)):
            print(line)

    # The floor applies to a whole tier only. A hand-passed --prefix legitimately selects a subset, so
    # asserting the tier's count against it would just be wrong; an EMPTY result is still an error there.
    floor = TIER_MIN_ROWS.get(args.tier) if args.prefix is None else None
    if not tests or (floor is not None and len(tests) < floor):
        expected = f"at least {floor} are expected" if floor is not None else "the filter matched nothing"
        print(f"ERROR: the {args.tier} tier registered {len(tests)} row(s); {expected}. "
              f"THIS IS NOT A PASS — rows that never ran cannot fail.")
        print("       Rows do not disappear one at a time here: the MotionTerminal module links "
              "Interchange unconditionally, so if that plugin is absent or the module failed to load, "
              "EVERY row in this tier vanishes together and the queue still empties cleanly.")
        print("       Check, in this order: the build's exit code and 'Result: Succeeded'; "
              "'MotionTerminal' and 'Interchange' both Enabled in MotionTerminal.uproject; "
              "'LogModuleManager'/'Interchange' lines in Saved/Logs/MotionTerminal.log.")
        return 2
    if _should_record(args.prefix, failed):   # == args.prefix is None and not failed
        _record_tier_counts(args.tier, len(tests))
    return 1 if failed else 0


# A summary line cannot tell you WHICH rigs were gated. "Tests: 17" is compatible with a roster gate
# that enumerated three rigs and with one that enumerated zero and registered a sentinel — and the
# whole design of the import-scale and golden-diff suites is that each rig gets its own row. Print the
# rows. Sorted by path so two runs of the same tier diff cleanly, and BEFORE the summary so the count
# and any failure detail still land together as the last thing on screen.
def _print_rows(tests):
    for t in sorted(tests, key=lambda r: str(r.get("fullTestPath", ""))):
        state = str(t.get("state", "Unknown"))
        # Print the report's own word for anything that is not a plain success — "Skipped" and
        # "NotRun" are not failures and must not be flattened into one. A row that logged a localOnly
        # SKIP is reported by UE as Success, which is correct (nothing failed) and misleading (nothing
        # ran); label it for what it is, since PASS next to a row that asserted nothing is the whole
        # reading this runner kept having to correct.
        label = ("SKIP" if _row_skipped(t) else "PASS") if state == "Success" else state.upper()
        print(f"  {label:<7} {t.get('fullTestPath')}")


# A runner that says WHICH row went red but not WHY sends every failure on a second trip through a
# 200 MB engine log to find a message the report is already holding. The assertion text is the whole
# value of a red: "GoldenDiff.MTLocomotion failed" and "GoldenDiff.MTLocomotion failed because the
# BlendSpace Y pin lost its Speed binding" are different facts. Print the second one.
MAX_ERROR_LINES = 40


def _entries(test):
    return test.get("entries", []) or []          # present-and-null must not TypeError on the red path


def _row_skipped(test):
    """A row that logged a localOnly SKIP asserted nothing, whatever colour it printed."""
    return any(_SKIP_MARKER in str((e.get("event") or {}).get("message", "")) for e in _entries(test))


# WARNINGS ON A GREEN ROW WERE INVISIBLE.
#
# Verify.FixImportScale's binary-FBX leg needs two engine-shipped sample files. When they are absent it
# AddWarnings — deliberately, not AddError, because AddError would make the suite unportable to an
# engine install without Engine/Content/FbxEditorAutomation. That tradeoff was recorded and is still the
# right one. What was NOT right is that the warning went nowhere: this runner printed Error entries on
# failed rows and nothing else, so "the flag-before-import ordering guard DID NOT RUN" — the only
# falsifiable assertion in that leg — could be true while the screen said `Failed: 0` and moved on.
#
# Printing warnings costs nothing on a healthy run (there are none) and turns every deliberate
# keep-it-portable warning into something a reader actually sees. It is the cheap half of the same
# principle as the skip accounting above: a green that means less than it looks like must say so.
def _print_warnings(tests):
    shown = 0
    for t in sorted(tests, key=lambda r: str(r.get("fullTestPath", ""))):
        for e in _entries(t):
            event = e.get("event", {}) or {}
            if event.get("type") == "Warning":
                if shown == 0:
                    print("Warnings (rows below are GREEN — these are coverage notes, not failures):")
                shown += 1
                for line in str(event.get("message", "")).splitlines():
                    print(f"  ! {t.get('fullTestPath')}: {line}")
    if shown:
        print(f"  ({shown} warning(s) — a warning here usually means a check did not RUN.)")


def _print_errors(test, index_path):
    """Echo a failed row's Error entries from the report. Capped, and it SAYS when it caps —
    a silent truncation would reintroduce exactly the blindness this exists to remove."""
    lines = []
    for entry in _entries(test):
        event = entry.get("event", {}) or {}
        if event.get("type") == "Error":
            lines.extend(str(event.get("message", "")).splitlines())
    for line in lines[:MAX_ERROR_LINES]:
        print("   ", line)
    if len(lines) > MAX_ERROR_LINES:
        print(f"    ... {len(lines) - MAX_ERROR_LINES} more error lines - full text in {index_path}")


if __name__ == "__main__":
    sys.exit(main())
