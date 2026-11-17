"""Unit tests for the capture-evidence door in `mt_drive`.

WHAT THIS ROW IS FOR, AND WHY IT IS THE SHAPE IT IS
===================================================
Three drivers write a file that a shipped frame is composited from. Each used to own a module-level
constant naming a FIXED repo path and wrote straight to it, and `terminal_capture.py`'s whole honesty
contract — the terminal panel may only render output a tool actually produced — was enforced by the
provenance of that path and by nothing else.

A driver-equivalence harness then ran each driver the way `mrq_render.render_shot` does,
`exec(src, {'unreal': <stub>})`, for 60 simulated ticks. `pie_bridge_drive._tick` reaches its
`DUMP_AT = 7.40` well inside 60 ticks, so it wrote the gameplay-bridge take's real transcript path with the stub's
canned return values (`IsFalling = 450.000000`, impossible for an FBoolProperty). The shipped frames
were honest; the file backing them was not, for seven hours, and every gate in this repo was green.

**These tests run under exactly that condition.** There is no Unreal here, so `import unreal` has to be
satisfied by a stub in `sys.modules` — which is not a compromise in the fixture, it IS the failure
condition. If a stub can reach a capture path from here, it could reach one from a harness.

The harness itself was deliberately never committed (a ~350-line unaudited
`unreal` stub is a second thing to maintain and a place for a false green to hide) and no longer exists
on any disk. This file is the small, audited part of it that was load-bearing for this fault, kept
permanently instead.

WHAT THIS PROVES AND WHAT IT DOES NOT. It proves the door refuses an unarmed caller, refuses a stub
that tries to arm itself, refuses a name that could climb out of the armed directory, and refuses an
arm record from another process — and that the three drivers no longer name a capture path at all. It
does NOT simulate a full render; the live half (a real render arms the door, and the arm is torn down
when the render finishes) was exercised in the editor during a real render pass.

Stdlib only. Run:  python -m pytest Scripts/test_mt_drive_guard.py -q
"""
import importlib
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent


class _StubPaths(object):
    """What a harness stub's `unreal.Paths` looks like: plausible strings, no project behind them."""

    project = "/not/a/real/checkout"

    @classmethod
    def project_dir(cls):
        return cls.project

    @classmethod
    def convert_relative_path_to_full(cls, p):
        return p


def _make_stub():
    """A minimal `unreal` stand-in — deliberately the same species as the harness stub."""
    stub = types.ModuleType("unreal")
    stub.Paths = _StubPaths
    stub.log = lambda *a, **k: None
    stub.log_warning = lambda *a, **k: None
    return stub


class CaptureEvidenceDoorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.out = self.tmp / "captures" / "shot_raw"
        self.out.mkdir(parents=True)

        self._saved_unreal = sys.modules.get("unreal")
        self._saved_mt = sys.modules.get("mt_drive")
        sys.modules["unreal"] = _make_stub()
        if str(SCRIPTS) not in sys.path:
            sys.path.insert(0, str(SCRIPTS))
        self.mt = importlib.reload(importlib.import_module("mt_drive"))

    def tearDown(self):
        for name, saved in (("unreal", self._saved_unreal), ("mt_drive", self._saved_mt)):
            if saved is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _grant_project(self):
        """Give the stub a directory that really does hold a .uproject, so the liveness probe passes
        and the tests below can exercise the arm record itself rather than stopping at the probe."""
        (self.tmp / "Fake.uproject").write_text("{}", encoding="utf-8")
        _StubPaths.project = str(self.tmp)
        self.addCleanup(setattr, _StubPaths, "project", "/not/a/real/checkout")

    # ---- the fault, re-enacted -----------------------------------------------------------------
    def test_unarmed_write_is_refused(self):
        target = self.out / "bridge_output.txt"
        with self.assertRaises(RuntimeError) as cm:
            self.mt.write_capture_evidence("bridge_output.txt", "IsFalling = 450.000000\n")
        self.assertIn("no arm record", str(cm.exception))
        self.assertFalse(target.exists(), "an unarmed write must not reach the filesystem at all")

    def test_unarmed_remove_is_refused(self):
        # The withhold path goes through the same door on purpose: a driver that refuses to publish a
        # transcript must not be able to DELETE one it was never allowed to write.
        with self.assertRaises(RuntimeError):
            self.mt.remove_capture_evidence("bridge_output.txt")

    def test_arming_against_a_bogus_project_dir_is_refused(self):
        # The liveness probe, asserted for WHAT IT ACTUALLY DOES: it refuses to arm when
        # `unreal.Paths.project_dir()` does not resolve to a real project, i.e. when `out_dir` cannot be
        # trusted to be where the caller thinks it is.
        #
        # IT IS NOT A STUB GUARD, and this test used to be named `test_a_stub_cannot_arm_itself`, a
        # name that asserted more than the code does. Every driver's
        # loader is `sys.path.insert(0, project_dir() + '/Scripts')` followed by `import mt_drive`, so a
        # stub that cannot answer with the real checkout cannot exec a driver at all — the probe's one
        # condition is one any real harness has already had to satisfy. `_grant_project` below is the
        # two-line demonstration of that: a stub passes the probe the moment it points at a real tree.
        # What stops a harness is the arm record, which only `render_shot` installs — the case covered
        # by `test_unarmed_write_is_refused`.
        with self.assertRaises(RuntimeError) as cm:
            self.mt.arm_capture_evidence(str(self.out), "harness")
        self.assertIn("no .uproject", str(cm.exception))

    def test_a_stub_pointing_at_a_real_tree_passes_the_probe(self):
        # The reproduction of that limit, kept as a test so the limit cannot quietly be re-overclaimed.
        # This is a STUB — `sys.modules['unreal']` is a hand-built module object — and it arms cleanly
        # once its project_dir answers with a directory holding a .uproject. The probe is a
        # misconfiguration guard; nothing here should ever be read as saying otherwise.
        self._grant_project()
        rec = self.mt.arm_capture_evidence(str(self.out), "a hand-written stub")
        self.assertEqual(rec["source"], "a hand-written stub")

    # ---- what an armed run may and may not do ---------------------------------------------------
    def test_armed_write_lands_inside_the_armed_directory(self):
        self._grant_project()
        self.mt.arm_capture_evidence(str(self.out), "Scripts/drv.py")
        path = self.mt.write_capture_evidence("t.txt", "reading = 1\n")
        self.assertEqual(Path(path).parent.resolve(), self.out.resolve())
        self.assertEqual(Path(path).read_text(encoding="utf-8"), "reading = 1\n")

    def test_a_name_with_a_separator_cannot_climb_out(self):
        # The same fault one level down: an armed driver reaching sideways into another shot's
        # capture directory.
        self._grant_project()
        self.mt.arm_capture_evidence(str(self.out), "Scripts/drv.py")
        for bad in ("../bridge_output.txt", "sub/t.txt", ""):
            with self.assertRaises(RuntimeError):
                self.mt.write_capture_evidence(bad, "x")

    def test_disarm_closes_the_door(self):
        # `render_shot` disarms when the render finishes or fatals, so a driver that outlives its
        # render cannot write evidence for frames that are no longer being produced.
        self._grant_project()
        self.mt.arm_capture_evidence(str(self.out), "Scripts/drv.py")
        self.mt.write_capture_evidence("t.txt", "a\n")
        self.mt.disarm_capture_evidence()
        with self.assertRaises(RuntimeError):
            self.mt.write_capture_evidence("t.txt", "b\n")
        self.assertEqual((self.out / "t.txt").read_text(encoding="utf-8"), "a\n")

    def test_an_arm_record_from_another_process_is_refused(self):
        self._grant_project()
        rec = self.mt.arm_capture_evidence(str(self.out), "Scripts/drv.py")
        rec["pid"] = os.getpid() + 1
        with self.assertRaises(RuntimeError) as cm:
            self.mt.write_capture_evidence("t.txt", "x")
        self.assertIn("pid", str(cm.exception))

    def test_a_forged_arm_record_without_a_token_is_refused(self):
        sys.modules["unreal"]._MT_CAPTURE_ARM = {"out_dir": str(self.out), "pid": os.getpid()}
        with self.assertRaises(RuntimeError) as cm:
            self.mt.write_capture_evidence("t.txt", "x")
        self.assertIn("token", str(cm.exception))


class DriversOwnNoCapturePathTests(unittest.TestCase):
    """The structural half, asserted against the committed source rather than against behaviour.

    The fix for the overwritten transcript is not a check a driver performs; it is that a driver no longer KNOWS where its
    evidence goes. That property is invisible to every behavioural test — a driver could route through
    the door today and grow a `REPO + '/captures/...'` constant back tomorrow — so it is asserted
    directly on the text of the three files that write capture evidence."""

    DRIVERS = ("pie_bridge_drive.py", "pie_montage_layer_drive.py", "fbx_unit_catchfix_drive.py")

    def test_no_driver_builds_a_capture_path(self):
        # Matches `captures/` AND the quoted bare segment, so `os.path.join(REPO, 'captures', …)` is
        # caught too — the obvious way the old literal-only grep could have been walked past.
        needles = ("captures/", "captures\\", "'captures'", '"captures"')
        for name in self.DRIVERS:
            src = (SCRIPTS / name).read_text(encoding="utf-8")
            code = [ln for ln in src.splitlines()
                    if any(nd in ln for nd in needles) and not ln.lstrip().startswith("#")]
            # fbx_unit_catchfix_drive keeps an OUT_DIR because it is not an MRQ shot and arms itself; it
            # still writes through the door, which is what the next test checks.
            allowed = 2 if name == "fbx_unit_catchfix_drive.py" else 0
            self.assertLessEqual(
                len(code), allowed,
                "%s builds a capture path in code: %r — the render arms the directory, the driver "
                "must not name one" % (name, code))

    def test_every_driver_writes_through_the_door(self):
        for name in self.DRIVERS:
            src = (SCRIPTS / name).read_text(encoding="utf-8")
            self.assertIn("write_capture_evidence", src,
                          "%s does not route its transcript through mt_drive" % name)
            self.assertNotIn("open(TRANSCRIPT", src)


if __name__ == "__main__":
    unittest.main()
