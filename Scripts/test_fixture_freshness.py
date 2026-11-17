"""Unit tests for fixture_freshness_check — the gate that binds COMMITTED image artifacts (stamped in
an `artifact_stamps.json` under research/fixtures/) to the shot footage they were derived from.

EVERY LEG HAS A PROVEN-OFF HERE, and that is the point of the file rather than a nicety. Checks that
could not observe what they claimed to check have turned up repeatedly in this project, several of them
introduced by tooling built to fix the previous one, so a gate nobody has watched go red is not evidence
of anything. Each test below breaks exactly one thing and asserts the specific refusal.

The other half of the bar is the opposite failure: `Pie.MontageSeamless` was RED on every clean checkout
while it was believed to be green. `test_a_checkout_with_no_captures_skips` and
`test_the_local_leg_skips_with_log_when_captures_is_absent` are that half — a machine with no
`captures/` must come out green with a visible localOnly SKIP, never red. `DiscoveryAndCli` covers the
tree-level half: a tree with no stamp files passes and says so.

Stdlib only — no Unreal, no PIL. Run:
    python -m unittest Scripts.test_fixture_freshness -v
"""
import ast
import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import fixture_freshness_check as ffc

REPO = Path(__file__).resolve().parents[1]


def _manifest(shots):
    return {"schemaVersion": 1, "target_duration_max_s": 180.0, "resolution": [1920, 1080],
            "fps_out": 30, "shots": shots}


def _shot(**kw):
    base = {"shot_id": "04_footik_ab", "title": "X", "order": 10,
            "source_dir": "captures/x", "frame_range": [0, 240], "fps_in": 30,
            "duration_s": 8.0, "caption_text": "hi", "caption_cites": [],
            "status": "shipped", "source_take": "n/a","has_terminal_panel": False}
    base.update(kw)
    return base


class _Tree(unittest.TestCase):
    """A miniature repo: one shot with 240 frames, one stamped hero GIF built from it.

    `04_footik_ab` is used as the shot id on purpose — `expected_select` looks the id up in the real
    `HERO` table, so a fixture with an invented id could not exercise the generator-parameter leg at all."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.frames = self.root / "captures" / "x"
        self.frames.mkdir(parents=True)
        for i in range(240):
            (self.frames / ("frame_%04d.png" % i)).write_bytes(b"frame-%d" % i)
        self.fix = self.root / "research" / "fixtures" / "demo"
        self.fix.mkdir(parents=True)
        (self.fix / "README.md").write_text("prose", encoding="utf-8")
        self.art = self.fix / "04_footik_ab.gif"
        self.art.write_bytes(b"GIF89a-pretend")

        self.manifest = _manifest([_shot()])
        self.stamps = {
            "schemaVersion": 1,
            "fixture_dir": "research/fixtures/demo",
            "artifacts": [{"file": "04_footik_ab.gif", "sha256": "", "derivation": "hero_gif",
                           "built_by": "hero_gifs.py --only gifs",
                           "sources": [{"shot_id": "04_footik_ab"}]}],
        }
        ffc.restamp(self.stamps, self.manifest, self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _run(self):
        return ffc.validate(self.stamps, self.manifest, self.root)


class GreenState(_Tree):
    def test_a_freshly_stamped_tree_passes_with_nothing_skipped(self):
        errs, skips = self._run()
        self.assertEqual((errs, skips), ([], []))

    def test_restamp_is_idempotent(self):
        # --write must be safe to run at any time: an entry whose inputs did not change has to come out
        # byte-identical, or the ~8 MB-of-git-history hazard that `hero_gifs --only` exists to avoid
        # would just move into the stamp file.
        before = json.dumps(self.stamps, sort_keys=True)
        ffc.restamp(self.stamps, self.manifest, self.root)
        self.assertEqual(before, json.dumps(self.stamps, sort_keys=True))

    def test_the_selection_is_the_one_the_generator_would_make(self):
        # 240 frames, HERO["04_footik_ab"] == (60, 120, 2) -> frames 60,62..178, sixty of them.
        src = self.stamps["artifacts"][0]["sources"][0]
        self.assertEqual((src["n_frames"], src["first"], src["last"]),
                         (60, "frame_0060.png", "frame_0178.png"))


class ProvenOff(_Tree):
    """One broken thing per test, and the exact refusal it must produce."""

    def test_a_re_rendered_source_frame_goes_red(self):
        # THE SCENARIO THE WHOLE GATE EXISTS FOR, and the one that actually happened: a shot re-rendered
        # after the artifact was built. Nothing about the artifact changes; the footage under it does.
        (self.frames / "frame_0100.png").write_bytes(b"re-rendered, different pixels")
        errs, _ = self._run()
        self.assertTrue(any("STALE ARTIFACT" in e for e in errs), errs)

    def test_a_re_rendered_frame_OUTSIDE_the_window_is_not_a_false_red(self):
        # The other side of the same promise. Frame 0 is in the shot but not in the GIF's window, so
        # re-rendering it does not make this artifact stale, and a gate that cried wolf here would teach
        # people to re-stamp reflexively — which is how a stamp stops meaning anything.
        (self.frames / "frame_0000.png").write_bytes(b"re-rendered, different pixels")
        errs, _ = self._run()
        self.assertEqual(errs, [])

    def test_an_edited_artifact_goes_red(self):
        self.art.write_bytes(b"GIF89a-rebuilt-but-not-restamped")
        errs, _ = self._run()
        self.assertTrue(any("the committed file has changed" in e for e in errs), errs)

    def test_a_moved_frame_range_goes_red_with_no_footage_needed(self):
        # COMMITTED-DATA LEG. Deleting captures/ first proves this fires on a clean clone too: moving a
        # shot's window re-points every artifact showing it at different footage, and no digest is
        # available there to notice.
        shutil.rmtree(self.root / "captures")
        self.manifest["shots"][0]["frame_range"] = [30, 270]
        errs, _ = self._run()
        self.assertTrue(any("frame_range" in e and "no longer covers" in e for e in errs), errs)

    def test_a_moved_source_dir_goes_red(self):
        self.manifest["shots"][0]["source_dir"] = "captures/elsewhere"
        errs, _ = self._run()
        self.assertTrue(any("the shot lives at" in e for e in errs), errs)

    def test_changing_a_generator_parameter_goes_red(self):
        # Edit HERO's offset and every GIF committed under the old one is stale by construction. Patched
        # rather than mocked so the failure text names the real table.
        saved = ffc.HERO["04_footik_ab"]
        ffc.HERO["04_footik_ab"] = (90, 120, 2)
        try:
            errs, _ = self._run()
        finally:
            ffc.HERO["04_footik_ab"] = saved
        self.assertTrue(any("HERO selects" in e for e in errs), errs)

    def test_an_unstamped_file_in_the_directory_goes_red(self):
        # The hole one artifact along: gating only what somebody remembered to gate is the failure this
        # file replaces, so a new image dropped in beside the others is an error until it is stamped.
        (self.fix / "10_new_hero.gif").write_bytes(b"GIF89a-ungated")
        errs, _ = self._run()
        self.assertTrue(any("10_new_hero.gif" in e and "no stamp" in e for e in errs), errs)

    def test_a_stamped_file_that_is_gone_goes_red(self):
        self.art.unlink()
        errs, _ = self._run()
        self.assertTrue(any("is stamped but is not in" in e for e in errs), errs)

    def test_a_stamp_naming_a_shot_the_manifest_dropped_goes_red(self):
        self.manifest["shots"] = []
        errs, _ = self._run()
        self.assertTrue(any("not in the shot manifest" in e for e in errs), errs)

    def test_readme_is_not_treated_as_an_artifact(self):
        # It is prose about the directory. If it were gated, every wording fix would demand a re-stamp,
        # and a gate that fires on prose is a gate people learn to re-stamp without reading.
        (self.fix / "README.md").write_text("rewritten entirely", encoding="utf-8")
        errs, _ = self._run()
        self.assertEqual(errs, [])


class CleanCheckout(_Tree):
    """A machine with no captures/ must be GREEN with a visible localOnly SKIP — never red.

    This is the `Pie.MontageSeamless` half of the bar: that row was red on every clean checkout while it
    was believed to be green, because absence was being treated as failure."""

    def test_a_checkout_with_no_captures_skips_rather_than_failing(self):
        shutil.rmtree(self.root / "captures")
        errs, skips = self._run()
        self.assertEqual(errs, [])
        self.assertTrue(skips)
        self.assertTrue(all("SKIP (localOnly)" in s for s in skips), skips)

    def test_the_committed_half_still_bites_with_no_captures(self):
        # A SKIP that skips everything is not coverage. The artifact's own bytes are committed, so that
        # leg must still fire on a machine with no footage at all.
        shutil.rmtree(self.root / "captures")
        self.art.write_bytes(b"edited on a machine with no footage")
        errs, skips = self._run()
        self.assertTrue(any("the committed file has changed" in e for e in errs), errs)
        self.assertTrue(skips)


class HandCrop(unittest.TestCase):
    """A still cropped from one shot frame is not produced by hero_gifs.py. Its coverage is
    deliberately one leg short and these two tests say which leg."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        d = self.root / "captures" / "x"
        d.mkdir(parents=True)
        for i in range(780):
            (d / ("frame_%04d.png" % i)).write_bytes(b"f%d" % i)
        self.fix = self.root / "fx"
        self.fix.mkdir()
        (self.fix / "still.jpg").write_bytes(b"jpeg")
        self.manifest = _manifest([_shot(shot_id="07_aim_hand_ik", frame_range=[0, 780], duration_s=26.0)])
        self.stamps = {"schemaVersion": 1, "fixture_dir": "fx",
                       "artifacts": [{"file": "still.jpg", "sha256": "", "derivation": "hand_crop",
                                      "built_by": "hand crop",
                                      "sources": [{"shot_id": "07_aim_hand_ik",
                                                   "select": {"kind": "index", "index": 779}}]}]}
        ffc.restamp(self.stamps, self.manifest, self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_the_source_frame_is_bound_like_any_other(self):
        # What IS covered: re-render the source shot and the still goes red, same as the GIFs.
        errs, _ = ffc.validate(self.stamps, self.manifest, self.root)
        self.assertEqual(errs, [])
        (self.root / "captures" / "x" / "frame_0779.png").write_bytes(b"re-rendered")
        errs, _ = ffc.validate(self.stamps, self.manifest, self.root)
        self.assertTrue(any("STALE ARTIFACT" in e for e in errs), errs)

    def test_the_crop_index_is_an_author_declaration_and_nothing_generates_it(self):
        # What is NOT covered, asserted rather than left in prose: no generator constant produces this
        # index, so `expected_select` returns None and a re-declaration to a different frame is accepted
        # on the author's word. The residual is real and narrow — re-cropping is a manual act.
        self.assertIsNone(ffc.expected_select("hand_crop", "07_aim_hand_ik", 780))
        self.stamps["artifacts"][0]["sources"][0]["select"] = {"kind": "index", "index": 100}
        ffc.restamp(self.stamps, self.manifest, self.root)
        errs, _ = ffc.validate(self.stamps, self.manifest, self.root)
        self.assertEqual(errs, [])


class OneImplementationOfTheSelection(unittest.TestCase):
    """The selection arithmetic moved into the gate and `hero_gifs` imports it back. A second copy
    reintroduced in the generator would be free to drift from the one the gate digests — which is the
    whole failure mode, one level down. Read statically: importing hero_gifs needs PIL, and a check
    that only runs where Pillow is installed is a check that silently does not run."""

    def test_the_generator_does_not_define_its_own_selection_tables(self):
        tree = ast.parse((REPO / "Scripts" / "hero_gifs.py").read_text(encoding="utf-8"))
        assigned = {t.id for n in ast.walk(tree) if isinstance(n, ast.Assign)
                    for t in n.targets if isinstance(t, ast.Name)}
        for name in ("HERO", "SHEET_FRAME"):
            self.assertNotIn(name, assigned,
                             "%s is defined in hero_gifs.py again - it must be imported from "
                             "fixture_freshness_check so the gate and the generator cannot disagree "
                             "about which frames an artifact is built from" % name)
        imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                    and n.module == "fixture_freshness_check" for a in n.names}
        self.assertIn("HERO", imported)
        self.assertIn("select_indices", imported)


class DiscoveryAndCli(_Tree):
    """The tree-level behaviour: stamp files are discovered, and a tree with none passes OUT LOUD."""

    def _main(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = ffc.main(["--root", str(self.root)] + list(argv))
        return rc, out.getvalue()

    def _write_stamps(self):
        (self.fix / ffc.STAMPS_NAME).write_text(json.dumps(self.stamps), encoding="utf-8")
        manifest = self.root / ffc.DEFAULT_MANIFEST
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps(self.manifest), encoding="utf-8")

    def test_a_stamp_file_under_research_fixtures_is_discovered(self):
        self._write_stamps()
        self.assertEqual(ffc.discover_stamps(self.root),
                         ["research/fixtures/demo/artifact_stamps.json"])

    def test_a_tree_with_no_stamp_files_passes_and_says_so(self):
        self.assertEqual(ffc.discover_stamps(self.root), [])
        rc, out = self._main()
        self.assertEqual(rc, 0)
        self.assertIn(ffc.NO_STAMPS, out)

    def test_a_discovered_stamp_file_is_actually_checked(self):
        # Discovery must lead to validation, not just to a count: a stale artifact still goes red.
        self._write_stamps()
        rc, out = self._main()
        self.assertEqual(rc, 0, out)
        self.assertIn("1 source binding(s)", out)
        self.art.write_bytes(b"GIF89a-rebuilt-but-not-restamped")
        rc, out = self._main()
        self.assertEqual(rc, 1, out)
        self.assertIn("the committed file has changed", out)


class TheCommittedStamps(unittest.TestCase):
    """Against the REAL stamp files in this tree, not fixtures. Everything above proves the gate works
    on synthetic data; this is the row that actually watches whatever artifacts the tree ships. A tree
    that ships none (no file matches STAMPS_GLOB) skips with that reason."""

    def setUp(self):
        self.paths = ffc.discover_stamps(REPO)
        if not self.paths:
            self.skipTest("%s (nothing matches %s)" % (ffc.NO_STAMPS, ffc.STAMPS_GLOB))
        self.stamps = [(p, json.loads((REPO / p).read_text(encoding="utf-8"))) for p in self.paths]
        self.manifest = json.loads(
            (REPO / ffc.DEFAULT_MANIFEST).read_text(encoding="utf-8"))

    def test_the_committed_artifacts_are_bound_and_current(self):
        # Runs everywhere, including a clean clone: the committed-data legs need no footage.
        for path, stamps in self.stamps:
            errs, _skips = ffc.validate(stamps, self.manifest, REPO)
            self.assertEqual(errs, [], "%s:\n%s" % (path, "\n".join(errs)))

    def test_no_stamp_file_is_silently_empty(self):
        # A stamp file with no artifacts, or an artifact with no source bindings, validates clean and
        # binds nothing. That is what keeps a silently emptied stamp from reading as a pass.
        for path, stamps in self.stamps:
            self.assertTrue(stamps.get("artifacts"), "%s stamps no artifacts" % path)
            for e in stamps["artifacts"]:
                self.assertTrue(e.get("sources"), "%s: %s has no source binding" % (path, e.get("file")))

    def test_the_local_leg_skips_with_log_when_captures_is_absent(self):
        # The localOnly row. On a machine with captures/ it exercises every source binding; on a clean
        # clone it SKIPs with a reason, which is what `clean_checkout_check` wants to see and what
        # MontageSeamless failed to do.
        skips = [s for _p, stamps in self.stamps for s in ffc.validate(stamps, self.manifest, REPO)[1]]
        if skips:
            self.skipTest("SKIP (localOnly) captures/ is not on this machine - %d source binding(s) "
                          "could not be digest-checked. First: %s" % (len(skips), skips[0]))


if __name__ == "__main__":
    unittest.main()
