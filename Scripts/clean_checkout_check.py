#!/usr/bin/env python
"""Prove the clean-checkout claim by making one, from OUTSIDE this working tree.

    python Scripts/clean_checkout_check.py                # clone HEAD, build, run both tiers, clean up
    python Scripts/clean_checkout_check.py --ref main --keep
    python Scripts/clean_checkout_check.py --no-editor    # host-side Python only (no UE, ~10 s)

============================ WHY THIS FILE EXISTS ============================

"Works from a clean checkout" is the claim this project most needs to be true, and it cannot be tested
on a development machine that has all the licensed content, a warm DDC and a
long history of editor state. The first clean checkout done by hand came back RED on the pie tier:
`Pie.MontageSeamless` loads two clips out of the gitignored `Content/MT/GAS_Clips/` and, alone among the
suite's machine-local dependencies, FAILED instead of SKIPping. The tier had never been green from a
clean checkout while it was believed to be.

Doing it once by hand makes that an anecdote. This makes it a check.

WHAT IT ASSERTS, and why each one is the assertion rather than a nearby easier one:

  * both tiers `Failed: 0`                 - the headline claim;
  * both tiers report AT LEAST ONE `SKIP (localOnly` row - THIS is the one that would have caught the
    MontageSeamless defect. A row that needs absent content must skip; a suite where nothing skips on a
    machine with no licensed content is a suite that is not gating on absence at all;
  * `shot_check` and `shot_assemble` EXIT NON-ZERO - a clean checkout has no `captures/`, so a green
    `shot_check` there would mean the gate cannot see its own inputs;
  * the plugin BUILDS against the clone - `Result: Succeeded` and exit 0, checked before any test result
    is believed.

IT CLONES COMMITTED STATE. `git clone` cannot see the working tree, so uncommitted changes are NOT under
test — running this with a dirty tree verifies the last commit and says so loudly, because "the
clean-checkout check passed" about a fix that was still only in the editor is exactly the kind of green
nobody should be allowed to collect.

EACH RUN CLONES INTO ITS OWN FRESH DIRECTORY (`mt-clean-checkout-XXXXXXXX`, made by `tempfile.mkdtemp`
under the scratch root) and only ever deletes that directory. It never removes a directory it did not
create, so a pre-existing path cannot be wiped and two concurrent runs cannot collide.

============================ WHERE THIS BELONGS, DECIDED ============================

A command that nothing runs automatically lets the defect class it catches recur. It STAYS MANUAL
anyway, for reasons that are about cost and about honesty rather than about appetite:

  * It clones, builds and runs both tiers. ~200 s on the development machine, and the build alone can be
    minutes on a cold DDC. Wiring it into either tier — the loop that gets run many times an hour while
    iterating — would make that loop unusable, and a gate people learn to skip is worse than one they
    run deliberately, because it still reads as coverage.
  * A pre-push hook has the same problem plus a worse one: it would fire on pushes that cannot possibly
    have broken it (a docs commit), so it would be routinely bypassed with --no-verify.
  * The place it genuinely belongs is a CI runner on push. The project's CI (.github/workflows/host-tests.yml)
    runs only the editor-free legs, the host pytest suite and the public gate, on a hosted runner that has
    no Unreal Engine; the build and both editor tiers need an engine install, so they stay local, and
    bolting them onto a local hook would be a simulation of CI rather than CI.

So: it is the RELEASE gate. It runs before any claim that the project works from a clean checkout, and
docs/verification-design.md carries that as a checklist step rather than as a suggestion. The fast leg
(`--no-editor`, ~10 s, no build) keeps it cheap to be honest: it also carries the namespace-merge gate
and collects the whole pytest directory rather than two named files.

WHAT ITS "PASS" MEANS, precisely. A clean clone REGISTERS far more rows than it EXERCISES: the licensed
content is gitignored and absent by design, so a large share of the rows SKIP — registered, green, and
asserting nothing. That is the correct behaviour, and it is the exact property the localOnly-SKIP
assertion above is checking, but it means "22/0 clean" must never be read as "22 things were checked".
Every count this script prints carries the EXERCISED number beside the registered one, so the reader
never has to take the registered count as the coverage.

WHAT IT DOES NOT SET UP. The clone gets only committed state: it does not perform README Quickstart step 1b
(Epic's Third Person template content is not in the repository and is not copied into the clone), so
editor-tier rows that need that content (GoldenDiff.Unarmed among them) do not run here as they would on a
checkout with step 1b done. Its PASS is the clean-clone result, not the result after the full Quickstart.

It is not CI and does not pretend to be. It is a command anyone can run before making the claim.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# UBT refuses to build when generated filenames pass 260 characters, and the deepest one this project
# produces is ~165 characters below the project root (a clone under a long scratch path measured 278 and
# got `Result: Failed (OtherCompilationError)` in 9 seconds, with no compilation attempted).
# Checked BEFORE cloning: a 9-second failure that looks like a build break is worse than a refusal.
MAX_ROOT_LEN = 95
_DEEPEST_GENERATED = 165

# Each run's clone directory: tempfile.mkdtemp(prefix=CLONE_PREFIX) appends 8 random characters, so
# the length check above is made on the name the directory WILL have, before it is created.
CLONE_PREFIX = "mt-clean-checkout-"
_CLONE_NAME_LEN = len(CLONE_PREFIX) + 8

# The startup fatal documented in run_verification._diagnose_no_report: environmental, intermittent, and
# it kills the editor before a single test registers. Retried rather than reported, because a retry loop
# is what a human does here and burying it would make this script's own result flaky.
DEFAULT_RETRIES = 3


def rmtree(path):
    """Delete a tree that contains a .git directory, on Windows, and say so if it could not.

    `shutil.rmtree` fails on git's object store: every file under `.git/objects` is created READ-ONLY,
    and Windows refuses to unlink a read-only file. `ignore_errors=True` turns that into a silent
    partial delete AND a printed "clone removed" that is false. Clear the bit and retry; report a
    genuine failure rather than swallowing it. Only ever called on the directory this run created
    (see make_clone_dir)."""
    def _onerror(func, target, _exc):
        try:
            os.chmod(target, 0o700)
            func(target)
        except OSError:
            pass
    if not Path(path).exists():
        return True
    try:                                    # onexc is 3.12+; onerror is the older spelling
        shutil.rmtree(path, onexc=lambda f, t, e: _onerror(f, t, e))
    except TypeError:
        shutil.rmtree(path, onerror=lambda f, t, e: _onerror(f, t, e))
    except OSError:
        pass
    return not Path(path).exists()


def make_clone_dir(parent):
    """A fresh, empty directory under `parent` that this run created and therefore owns.

    Unique per run (tempfile.mkdtemp), so nothing that already existed is ever reused, cleared or
    deleted, and two concurrent runs never share a clone. `git clone` accepts an empty destination."""
    Path(parent).mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=CLONE_PREFIX, dir=str(parent)))


def sh(cmd, cwd=None, capture=True):
    p = subprocess.run(cmd, cwd=str(cwd) if cwd else None, shell=False,
                       capture_output=capture, text=True)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def is_inside(child, parent):
    try:
        Path(child).resolve().relative_to(Path(parent).resolve())
        return True
    except ValueError:
        return False


def read_report(report_dir):
    """(tests, failed, skipped, failed_names) off a UE automation report, or None if it never wrote one."""
    index = Path(report_dir) / "index.json"
    if not index.exists():
        return None
    data = json.loads(index.read_text(encoding="utf-8-sig"))     # UE writes a BOM
    tests = data.get("tests", []) or []
    failed = [t for t in tests if t.get("state") != "Success"]
    skipped = [t.get("fullTestPath") for t in tests
               if any("SKIP (localOnly" in str((e.get("event") or {}).get("message", ""))
                      for e in (t.get("entries") or []))]
    return len(tests), len(failed), skipped, [t.get("fullTestPath") for t in failed]


def latest_report(clone):
    reports = sorted((clone / "Saved" / "TestReports").glob("*"))
    return reports[-1] if reports else None


def run_tier(clone, tier, retries):
    """Run one tier, retrying past the known startup fatal. Returns (parsed, attempts, note)."""
    for attempt in range(1, retries + 1):
        before = {p.name for p in (clone / "Saved" / "TestReports").glob("*")} \
            if (clone / "Saved" / "TestReports").exists() else set()
        rc, out = sh([sys.executable, str(clone / "Scripts" / "run_verification.py"), "--tier", tier])
        after = sorted(p for p in (clone / "Saved" / "TestReports").glob("*")
                       if p.name not in before)
        parsed = read_report(after[-1]) if after else None
        if parsed is not None:
            note = "" if attempt == 1 else ("ran on attempt %d of %d" % (attempt, retries))
            return parsed, attempt, note
        # No report at all: distinguish the known environmental fatal from a real hang before retrying.
        why = "editor died before the queue ran (known flaky D3D12 startup fatal)" \
            if re.search(r"PipelineStateCache\.cpp|Shader compilation failures are Fatal",
                         (clone / "Saved" / "Logs" / "MotionTerminal.log").read_text(
                             encoding="utf-8", errors="replace")
                         if (clone / "Saved" / "Logs" / "MotionTerminal.log").exists() else "") \
            else "no report and no known fatal signature"
        print("    attempt %d/%d produced no report - %s" % (attempt, retries, why))
        if attempt == retries:
            return None, attempt, why
    return None, retries, "exhausted"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ref", default="HEAD", help="branch/commit to clone (default HEAD of this tree)")
    ap.add_argument("--scratch", default=None,
                    help="scratch parent dir; must be OUTSIDE this repo (default: system temp)")
    ap.add_argument("--keep", action="store_true", help="do not delete the clone (for debugging)")
    ap.add_argument("--no-editor", action="store_true",
                    help="host-side Python only: skip the build and both UE tiers")
    ap.add_argument("--retries", type=int, default=DEFAULT_RETRIES)
    args = ap.parse_args()

    parent = Path(args.scratch) if args.scratch else Path(tempfile.gettempdir())
    planned = parent / (CLONE_PREFIX + "X" * (_CLONE_NAME_LEN - len(CLONE_PREFIX)))

    # THE ONE THING THIS SCRIPT MUST NEVER DO. A clone inside the working tree would be one `git add`
    # from committing a copy of the repo into itself, and `--keep` plus a stray `git add -A` is all it
    # would take. Refused before anything is created.
    if is_inside(planned, REPO):
        sys.exit("REFUSING: scratch %s is inside the working tree %s. Clone outside it." % (parent, REPO))
    if len(str(planned)) + _DEEPEST_GENERATED > 260 or len(str(planned)) > MAX_ROOT_LEN:
        sys.exit("REFUSING: scratch root %s is %d chars. UBT generates filenames ~%d chars below the "
                 "project root and fails past 260, before compiling anything. Pass a shorter --scratch."
                 % (planned, len(str(planned)), _DEEPEST_GENERATED))

    # Created here and only here; the finally below is the only place it is deleted, and it deletes
    # nothing else. Every exit after this point (a failed clone included) goes through it.
    clone = make_clone_dir(parent)
    try:
        return _check(args, clone)
    finally:
        _cleanup(clone, args.keep)


def _check(args, clone):
    results = []       # (label, ok, detail)

    def record(label, ok, detail):
        results.append((label, bool(ok), detail))
        print("  %-34s %s  %s" % (label, "OK  " if ok else "FAIL", detail))

    print("CLEAN-CHECKOUT CHECK")
    print("  source tree : %s" % REPO)
    print("  ref         : %s" % args.ref)
    print("  clone       : %s" % clone)
    print()

    t0 = time.time()
    rc, out = sh(["git", "clone", "--quiet", "--no-local", str(REPO), str(clone)])
    if rc != 0:
        sys.exit("clone failed:\n" + out)
    if args.ref != "HEAD":
        rc, out = sh(["git", "checkout", "--quiet", args.ref], cwd=clone)
        if rc != 0:
            sys.exit("checkout %s failed:\n%s" % (args.ref, out))
    rc, sha = sh(["git", "log", "--oneline", "-1"], cwd=clone)
    record("clone", True, sha.strip())

    # Loud, not fatal: the clone is of COMMITTED state, so a dirty tree means the thing being verified
    # may not be the thing that was just changed.
    _rc, dirty = sh(["git", "status", "--porcelain"], cwd=REPO)
    tracked_dirty = [ln for ln in dirty.splitlines() if ln and not ln.startswith("??")]
    if tracked_dirty:
        print("  !! WORKING TREE IS DIRTY - %d tracked file(s) modified and NOT in this clone:"
              % len(tracked_dirty))
        for ln in tracked_dirty[:10]:
            print("       %s" % ln)
        print("     This run verifies %s, not your edits. Commit first." % sha.strip().split()[0])

    # The clone must NOT have the machine-local content - otherwise this whole exercise is measuring the
    # working tree again. Asserted, not assumed.
    leaked = [d for d in ("captures", "Content/ParagonKhaimera", "Content/MetaHumans",
                          "Content/MT/GAS_Clips", "Content/MT/Khaimera", "Content/MT/Showcase")
              if (clone / d).exists()]
    record("clone is content-free", not leaked, "gitignored content absent" if not leaked
           else "LEAKED into the clone: %s" % leaked)

    # COLLECT THE DIRECTORY, DO NOT LIST THE FILES. Naming test modules explicitly means a test file
    # added afterwards is simply not run here while this script still reports "pytest OK" — the same
    # shape as a grep declared clean without covering every file. Collecting Scripts/ means a new test
    # module is covered the moment it lands.
    rc, out = sh([sys.executable, "-m", "pytest", "Scripts", "-q"], cwd=clone)
    tail = out.strip().splitlines()[-1] if out.strip() else ""
    record("pytest (no captures/)", rc == 0, tail)
    # ...and assert the collection actually found something. `pytest Scripts` on a directory that
    # collected zero tests exits 5, not 0, so rc==0 already covers the empty case — but say the count
    # out loud anyway, because "pytest OK" over 3 tests and over 40 look identical otherwise.
    record("pytest collected tests", " passed" in tail, tail or "no pytest summary line at all")

    # The exec-namespace-MERGE gate. Static, stdlib-only, ~0.1 s, and it needs no editor —
    # so there is no reason for a clean-checkout run not to carry it.
    rc, out = sh([sys.executable, "Scripts/namespace_merge_check.py"], cwd=clone)
    record("namespace merge check", rc == 0,
           out.strip().splitlines()[-1] if out.strip() else "no output")

    # These MUST fail: a clean checkout has no frames, so a gate that passes there is not looking.
    #
    # AND IT MUST FAIL FOR THE RIGHT REASON. `rc != 0` alone passes on ANY non-zero exit, so a
    # shot_check.py that died on a traceback — an ImportError, a KeyError on a manifest field — would be
    # recorded OK here, i.e. this check could not tell a working gate from a broken one: a check that
    # cannot observe what it claims to check, sitting inside the release gate. Both refusals are
    # therefore verified by their TEXT, not just their exit code.
    rc, out = sh([sys.executable, "Scripts/shot_check.py", "--require-complete"], cwd=clone)
    refused = "SHOT CHECK FAILED" in out and "source_dir absent" in out
    record("shot_check refuses", rc != 0 and refused,
           "exit %d, refusal %s" % (rc, "present with a source_dir reason" if refused
                                    else "MISSING - last line: %s"
                                         % (out.strip().splitlines()[-1] if out.strip() else "<no output>")))
    rc, out = sh([sys.executable, "Scripts/shot_assemble.py"], cwd=clone)
    record("shot_assemble refuses", rc != 0 and "REFUSING TO ASSEMBLE" in out,
           "exit %d, refusal message %s" % (rc, "present" if "REFUSING TO ASSEMBLE" in out else "MISSING"))

    if not args.no_editor:
        engine = _resolve_engine_for(clone)
        rc, out = sh([str(engine / "Engine" / "Build" / "BatchFiles" / "Build.bat"),
                      "MotionTerminalEditor", "Win64", "Development",
                      "-Project=%s" % (clone / "MotionTerminal.uproject"), "-WaitMutex"])
        succeeded = rc == 0 and "Result: Succeeded" in out
        record("build against the clone", succeeded, "exit %d, %s" % (
            rc, "Result: Succeeded" if "Result: Succeeded" in out else "NO 'Result: Succeeded'"))
        if not succeeded:                      # never believe a test result over a failed build
            return _summary(results, t0)

        floors = _tier_floors(clone)
        for tier in ("default", "pie"):
            parsed, attempts, note = run_tier(clone, tier, args.retries)
            if parsed is None:
                record("%s tier" % tier, False, "no report after %d attempt(s): %s" % (attempts, note))
                continue
            total, failed, skipped, failed_names = parsed
            # "Tests: 22  Failed: 0" is not "22 things were checked". On a clean clone the licensed
            # content is absent by design, so a large fraction of the rows SKIP: registered and green
            # and asserting nothing. Reporting exercised beside registered is the honest count, and it
            # is the number a reader of this script's PASS actually wants.
            record("%s tier" % tier, failed == 0,
                   "Tests: %d  Failed: %d  Skipped(localOnly): %d  EXERCISED: %d%s%s"
                   % (total, failed, len(skipped), total - len(skipped),
                      "  " + note if note else "", "  " + str(failed_names) if failed_names else ""))
            # The assertion that would have caught MontageSeamless: absent content must SKIP, not fail.
            record("%s tier localOnly SKIPs" % tier, len(skipped) > 0,
                   "%d row(s) SKIP-with-log%s" % (len(skipped),
                                                  ": " + ", ".join(s.rsplit(".", 1)[-1] if s else "?"
                                                                   for s in skipped) if skipped else
                                                  " - NOTHING skipped, so absence is not being gated"))
            # A tier that vanished reports Failed: 0 too. See run_verification.TIER_MIN_ROWS for why
            # the whole module can disappear at once (Interchange is a hard link-time dependency) and
            # why the floor has to live outside the module rather than inside a suite.
            floor = floors.get(tier)
            record("%s tier row floor" % tier, floor is not None and total >= floor,
                   "%d row(s) registered, floor %s" % (total, floor if floor is not None else
                                                       "UNREADABLE from the clone's run_verification"))

    return _summary(results, t0)


def _tier_floors(clone):
    """The clone's own TIER_MIN_ROWS, for the same reason _resolve_engine_for imports the clone's
    resolver: a floor copied into this file would drift from the runner's and start asserting a count
    the suite no longer has."""
    try:
        return dict(_clone_run_verification(clone).TIER_MIN_ROWS)
    except Exception:
        return {}


def _resolve_engine_for(clone):
    """Reuse the clone's OWN engine resolver, so this script cannot disagree with the runner it drives."""
    return Path(_clone_run_verification(clone).resolve_engine())


def _clone_run_verification(clone):
    """The CLONE's run_verification module — never this tree's."""
    import importlib
    sys.path.insert(0, str(clone / "Scripts"))
    # POP the module first. `reload()` re-reads a module's ORIGINAL __file__, so if this repo's own
    # run_verification is already imported, reloading it would silently resolve the engine using THIS
    # tree's copy while claiming to use the clone's — the exact class of "measuring the wrong thing"
    # this whole script exists to avoid.
    sys.modules.pop("run_verification", None)
    try:
        rv = importlib.import_module("run_verification")
        assert is_inside(rv.__file__, clone), "imported %s, expected the clone's copy" % rv.__file__
        return rv
    finally:
        sys.path.pop(0)
        sys.modules.pop("run_verification", None)


def _summary(results, t0):
    print()
    bad = [r for r in results if not r[1]]
    print("=" * 78)
    print("CLEAN-CHECKOUT CHECK: %s  (%d checks, %d failed, %.0fs)"
          % ("PASS" if not bad else "FAIL", len(results), len(bad), time.time() - t0))
    for label, _ok, detail in bad:
        print("  FAILED: %-30s %s" % (label, detail))
    return 0 if not bad else 1


def _cleanup(clone, keep):
    """Remove THIS run's clone (from make_clone_dir), or say where it was kept."""
    if keep:
        print("  clone KEPT at %s" % clone)
    else:
        print("  clone removed" if rmtree(clone)
              else "  WARNING: clone at %s could NOT be fully removed - delete it by hand" % clone)


if __name__ == "__main__":
    sys.exit(main())
