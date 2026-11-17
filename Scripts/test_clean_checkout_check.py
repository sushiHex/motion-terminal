"""Host tests for clean_checkout_check: it deletes only the clone directory it created itself.

The clone step is faked (no git, no Unreal), so these run in milliseconds and exercise the real
main() path: argument parsing, the scratch checks, make_clone_dir and the cleanup in its finally.

    python -m pytest Scripts/test_clean_checkout_check.py -q
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import clean_checkout_check as ccc


def _run_main_with_failing_clone(scratch, *extra):
    """Run main() against `scratch` with `git clone` failing; returns the clone dirs it was handed."""
    seen = []

    def fake_sh(cmd, cwd=None, capture=True):
        if cmd[:2] == ["git", "clone"]:
            seen.append(Path(cmd[-1]))
            return 128, "fatal: simulated clone failure"
        return 0, ""

    argv = ["clean_checkout_check.py", "--scratch", str(scratch), "--no-editor"] + list(extra)
    with mock.patch.object(ccc, "sh", fake_sh), mock.patch.object(sys, "argv", argv), \
            mock.patch.object(ccc, "MAX_ROOT_LEN", 10_000), \
            mock.patch.object(ccc, "_DEEPEST_GENERATED", 0):
        try:
            ccc.main()
        except SystemExit as exc:                   # a failed clone exits the script
            assert "clone failed" in str(exc.code), exc.code
        else:
            raise AssertionError("main() returned despite the failed clone")
    return seen


class NeverDeletesWhatItDidNotCreate(unittest.TestCase):
    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp(prefix="ccc-test-"))
        self.addCleanup(shutil.rmtree, self.scratch, True)
        # What an older run, or another tool, could have left at the old fixed name.
        self.foreign = self.scratch / "mt-clean-checkout"
        self.foreign.mkdir()
        (self.foreign / "keep-me.txt").write_text("not this run's", encoding="utf-8")

    def test_a_preexisting_directory_survives_and_the_own_clone_is_removed(self):
        seen = _run_main_with_failing_clone(self.scratch)
        self.assertEqual(len(seen), 1)
        own = seen[0]
        self.assertNotEqual(own.resolve(), self.foreign.resolve())
        self.assertTrue(own.name.startswith(ccc.CLONE_PREFIX))
        self.assertFalse(own.exists(), "the directory this run created must be removed")
        self.assertTrue((self.foreign / "keep-me.txt").is_file(),
                        "a directory this run did not create must never be touched")

    def test_keep_leaves_the_own_clone_in_place(self):
        seen = _run_main_with_failing_clone(self.scratch, "--keep")
        self.assertTrue(seen[0].is_dir())
        self.assertTrue((self.foreign / "keep-me.txt").is_file())

    def test_two_runs_get_distinct_directories(self):
        first = _run_main_with_failing_clone(self.scratch, "--keep")[0]
        second = _run_main_with_failing_clone(self.scratch, "--keep")[0]
        self.assertNotEqual(first, second)
        self.assertEqual(len(first.name), ccc._CLONE_NAME_LEN)

    def test_scratch_inside_the_repo_is_refused_before_anything_is_created(self):
        argv = ["clean_checkout_check.py", "--scratch", str(ccc.REPO / "Saved"), "--no-editor"]
        with mock.patch.object(sys, "argv", argv), \
                mock.patch.object(ccc, "make_clone_dir", side_effect=AssertionError("created")):
            with self.assertRaises(SystemExit) as cm:
                ccc.main()
        self.assertIn("REFUSING", str(cm.exception.code))


if __name__ == "__main__":
    unittest.main()
