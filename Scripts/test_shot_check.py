"""Unit tests for shot_check. Stdlib only — no Unreal, no PIL. Run:
    python -m unittest Scripts.test_shot_check -v
"""
import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import shot_check


def _manifest(shots, max_s=180.0, fps_out=30):
    return {"schemaVersion": 1, "target_duration_max_s": max_s,
            "resolution": [1920, 1080], "fps_out": fps_out, "shots": shots}


def _shot(**kw):
    base = {"shot_id": "01_x", "title": "X", "order": 10,
            "source_dir": "captures/x", "frame_range": [0, 3], "fps_in": 30,
            "duration_s": 0.1, "caption_text": "hi", "caption_cites": [],
            "status": "shipped", "source_take": "fixture take", "has_terminal_panel": False}
    base.update(kw)
    return base


class _Root(unittest.TestCase):
    """A temp checkout root holding the three frames _shot()'s captures/x points at."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self._frames("x")

    def _frames(self, name):
        d = self.root / "captures" / name
        d.mkdir(parents=True)
        for i in range(3):
            (d / ("frame_%04d.png" % i)).write_bytes(b"x")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)


class ShotCheckTests(_Root):
    def test_valid_manifest_passes(self):
        errs = shot_check.validate(_manifest([_shot()]), self.root, check_artifact=False)
        self.assertEqual(errs, [])

    def test_frame_range_beyond_disk_fails(self):
        errs = shot_check.validate(_manifest([_shot(frame_range=[0, 99])]), self.root)
        self.assertTrue(any("frame_range" in e for e in errs), errs)

    def test_missing_source_dir_for_shipped_fails(self):
        errs = shot_check.validate(_manifest([_shot(source_dir="captures/nope")]), self.root)
        self.assertTrue(any("source_dir" in e for e in errs), errs)

    def test_missing_status_tolerated_unless_require_complete(self):
        m = _manifest([_shot(status="missing", source_dir="captures/nope")])
        self.assertEqual(shot_check.validate(m, self.root), [])
        errs = shot_check.validate(m, self.root, require_complete=True)
        self.assertTrue(any("status" in e for e in errs), errs)

    def test_duration_budget_enforced(self):
        # duration_s matches its derived value (3 frames / 30fps = 0.1s) so only the budget check
        # fires here, not the derived-mismatch check exercised separately below. The budget total
        # is itself computed from the derived value now, not the declaration — see
        # test_duration_derived_mismatch_fails for proof the declaration can't diverge silently.
        errs = shot_check.validate(_manifest([_shot()], max_s=0.05), self.root)
        self.assertTrue(any("duration" in e for e in errs), errs)

    def test_duration_derived_mismatch_fails(self):
        errs = shot_check.validate(_manifest([_shot(duration_s=5.0)]), self.root)
        self.assertTrue(any("duration_s" in e and "derived" in e for e in errs), errs)

    def test_duration_derived_mismatch_not_checked_for_missing(self):
        # "missing" shots have no frames yet, so duration_s is still unchecked intent for them.
        m = _manifest([_shot(status="missing", source_dir="captures/nope", duration_s=999.0)])
        self.assertEqual(shot_check.validate(m, self.root), [])

    def test_duration_derived_from_fps_out_not_fps_in(self):
        # Mirrors the real foot-IK A/B and turn/aim/hand-IK shape: recorded at 60fps, played out on a 30fps timeline
        # (intentional slow-mo — see shot_assemble.py). 240 frames / fps_out(30) = 8.0s is correct;
        # 240 frames / fps_in(60) = 4.0s is the bug this test exists to catch. Every OTHER fixture
        # in this file has fps_in == fps_out == 30, so a divide-by-fps_in bug would pass all of
        # them unchanged — this is the one case where the two denominators disagree.
        d = self.root / "captures" / "slowmo"
        d.mkdir(parents=True)
        for i in range(240):
            (d / ("frame_%04d.png" % i)).write_bytes(b"x")
        shot = _shot(source_dir="captures/slowmo", frame_range=[0, 240], fps_in=60, duration_s=8.0)
        errs = shot_check.validate(_manifest([shot], fps_out=30), self.root, check_artifact=False)
        self.assertEqual(errs, [], errs)

    def test_citation_matching_value_passes(self):
        ev = self.root / "ev.json"
        ev.write_text(json.dumps({"budget": {"gt": 3.6, "ceil": 4.0}}))
        cite = {"claim": "3.6 / 4.0 ms", "source": "ev.json",
                "keys": ["budget.gt", "budget.ceil"]}
        errs = shot_check.validate(_manifest([_shot(
            caption_text="game-thread 3.6 / 4.0 ms", caption_cites=[cite])]), self.root,
            check_artifact=False)
        self.assertEqual(errs, [])

    # ---- caption <-> claim binding (also replaces the convention that every numeral in a caption is
    # cited). The caption is what gets burned into the frames; until these, nothing connected it to
    # the claim the citation layer actually checks.

    def test_claim_absent_from_caption_fails(self):
        # The exact drift this binding exists for: evidence still backs the claim, the claim is still positionally
        # bound — but the caption burned over the footage says something else entirely.
        ev = self.root / "ev.json"
        ev.write_text(json.dumps({"budget": {"gt": 3.6, "ceil": 4.0}}))
        cite = {"claim": "3.6 / 4.0 ms", "source": "ev.json",
                "keys": ["budget.gt", "budget.ceil"]}
        errs = shot_check.validate(_manifest([_shot(
            caption_text="comfortably inside the budget", caption_cites=[cite])]), self.root)
        self.assertTrue(any("does not contain its cited claim" in e for e in errs), errs)

    def test_uncited_number_in_caption_fails(self):
        # 46 is true and evidenced elsewhere, but no citation on this shot covers it, so the gate
        # must say so rather than trusting that somebody checked it by hand.
        ev = self.root / "ev.json"
        ev.write_text(json.dumps({"budget": {"gt": 3.6, "ceil": 4.0}}))
        cite = {"claim": "3.6 / 4.0 ms", "source": "ev.json",
                "keys": ["budget.gt", "budget.ceil"]}
        errs = shot_check.validate(_manifest([_shot(
            caption_text="46 instances: game-thread 3.6 / 4.0 ms", caption_cites=[cite])]), self.root)
        self.assertTrue(any("no caption_cites claim covers" in e and "46" in e for e in errs), errs)

    def test_caption_number_covered_by_a_second_citation_passes(self):
        # The fix shape the crowd take actually ships: a second citation whose claim carries the number the
        # first one never did. Proves the check is satisfiable by citing, not only by deleting.
        ev = self.root / "ev.json"
        ev.write_text(json.dumps({"budget": {"gt": 3.6, "ceil": 4.0}, "crowd": {"n": 46}}))
        cites = [{"claim": "46 instances", "source": "ev.json", "keys": ["crowd.n"]},
                 {"claim": "3.6 / 4.0 ms", "source": "ev.json",
                  "keys": ["budget.gt", "budget.ceil"]}]
        errs = shot_check.validate(_manifest([_shot(
            caption_text="46 instances: game-thread 3.6 / 4.0 ms", caption_cites=cites)]), self.root,
            check_artifact=False)
        self.assertEqual(errs, [])

    def test_caption_number_sign_and_punctuation_tolerated(self):
        # The foot-IK A/B take's real shape: the caption writes "+3.46" where the claim writes "3.46". Comparing
        # as floats keeps that from being a false red while still catching a genuinely different value.
        ev = self.root / "ev.json"
        ev.write_text(json.dumps({"footik": {"up": 3.46, "down": -7.14}}))
        cite = {"claim": "3.46 / -7.14 cm", "source": "ev.json",
                "keys": ["footik.up", "footik.down"]}
        errs = shot_check.validate(_manifest([_shot(
            caption_text="node off vs on: +3.46 / -7.14 cm", caption_cites=[cite])]), self.root,
            check_artifact=False)
        self.assertEqual(errs, [])

    def test_citation_drift_fails(self):
        ev = self.root / "ev.json"
        ev.write_text(json.dumps({"budget": {"gt": 8.6, "ceil": 4.0}}))
        cite = {"claim": "3.6 / 4.0 ms", "source": "ev.json",
                "keys": ["budget.gt", "budget.ceil"]}
        errs = shot_check.validate(_manifest([_shot(caption_cites=[cite])]), self.root)
        self.assertTrue(any("citation" in e for e in errs), errs)

    def test_citation_missing_key_fails(self):
        ev = self.root / "ev.json"
        ev.write_text(json.dumps({"budget": {"gt": 3.6}}))
        cite = {"claim": "3.6 ms", "source": "ev.json", "keys": ["budget.nope"]}
        errs = shot_check.validate(_manifest([_shot(caption_cites=[cite])]), self.root)
        self.assertTrue(any("key" in e for e in errs), errs)

    def test_citation_positional_transposition_fails(self):
        # Both claimed numbers ARE present somewhere in the evidence — just bound to the wrong keys
        # (as if conformance_up_cm and conformance_down_cm got swapped in transcription). A
        # set-membership check would pass this; a positional check must not.
        ev = self.root / "ev.json"
        ev.write_text(json.dumps({"footik": {"up": -7.14, "down": 3.46}}))
        cite = {"claim": "+3.46 / -7.14 cm", "source": "ev.json",
                "keys": ["footik.up", "footik.down"]}
        errs = shot_check.validate(_manifest([_shot(caption_cites=[cite])]), self.root)
        self.assertTrue(any("citation" in e for e in errs), errs)

    def test_citation_count_mismatch_falls_back_to_set_membership(self):
        # "46 instances: 3.6 / 4.0 ms" contains 3 numbers but only 2 keys are cited (46 isn't a
        # citable figure). Counts disagree, so this must use the set-membership fallback rather
        # than erroring or guessing an alignment.
        ev = self.root / "ev.json"
        ev.write_text(json.dumps({"budget": {"gt": 3.6, "ceil": 4.0}}))
        cite = {"claim": "46 instances: 3.6 / 4.0 ms", "source": "ev.json",
                "keys": ["budget.gt", "budget.ceil"]}
        errs = shot_check.validate(_manifest([_shot(caption_cites=[cite])]), self.root)
        self.assertTrue(any("citation drift" in e and "46" in e for e in errs), errs)

    def test_citation_source_missing_fails(self):
        cite = {"claim": "3.6 ms", "source": "nope.json", "keys": ["a.b"]}
        errs = shot_check.validate(_manifest([_shot(caption_cites=[cite])]), self.root)
        self.assertTrue(any("citation source missing" in e for e in errs), errs)

    def test_citation_source_unparseable_fails(self):
        ev = self.root / "bad.json"
        ev.write_text("{not json")
        cite = {"claim": "3.6 ms", "source": "bad.json", "keys": ["a.b"]}
        errs = shot_check.validate(_manifest([_shot(caption_cites=[cite])]), self.root)
        self.assertTrue(any("unparseable" in e for e in errs), errs)

    def test_citation_key_non_numeric_fails(self):
        ev = self.root / "ev.json"
        ev.write_text(json.dumps({"budget": {"gt": "n/a"}}))
        cite = {"claim": "3.6 ms", "source": "ev.json", "keys": ["budget.gt"]}
        errs = shot_check.validate(_manifest([_shot(caption_cites=[cite])]), self.root)
        self.assertTrue(any("not numeric" in e for e in errs), errs)

    def test_frame_range_malformed_fails_cleanly(self):
        errs = shot_check.validate(_manifest([_shot(frame_range=[0])]), self.root)
        self.assertTrue(any("frame_range" in e for e in errs), errs)


class ArtifactBindingTests(_Root):
    """The assembled-MP4 <-> manifest binding.

    THE FAULT IT CLOSES: every other check in shot_check reads JSON, so a caption edited without a
    re-assemble left the shipped pixels contradicting the manifest with the whole gate green. The
    control used to be procedural. Each test below is a proven-OFF for one leg of the replacement."""

    def setUp(self):
        super().setUp()
        self.art = self.root / "out" / "shots.mp4"
        self.art.parent.mkdir(parents=True)
        self.manifest = _manifest([_shot()])
        self.manifest["artifact"] = "out/shots.mp4"

    def _stamp(self, manifest):
        shot_check.stamp_path(self.art).write_text(
            json.dumps({"fingerprint": shot_check.assembly_fingerprint(manifest)}), encoding="utf-8")

    def test_no_artifact_and_no_footage_is_silent(self):
        # THE CLEAN-CLONE CASE, now modelled properly. This test used to keep its frames on disk while
        # claiming to be a clean clone, which is why the absent-artifact hole (next test) had no coverage. A
        # fresh checkout has no captures/ at all: the shipped shot's source_dir is absent (which
        # validate() reports, loudly, on its own line) and the artifact check must add nothing.
        # clean_checkout_check depends on this staying quiet.
        shutil.rmtree(self.root / "captures")
        errs = shot_check.validate(self.manifest, self.root)
        self.assertTrue(any("source_dir absent" in e for e in errs), errs)
        self.assertFalse(any("artifact" in e for e in errs), errs)

    def test_absent_artifact_with_footage_on_disk_fails(self):
        # Repointing `artifact` at a path where nothing sits used to return []
        # from _check_artifact BEFORE the fingerprint was computed, so the staleness gate switched
        # itself off silently on a machine that has the footage. Hashing `artifact` into the
        # fingerprint does not reach this: the early return happens first.
        self.manifest["artifact"] = "out/nothing_is_here.mp4"
        errs = shot_check.validate(self.manifest, self.root)
        self.assertTrue(any("does not exist, but this checkout HAS shipped footage" in e for e in errs),
                        errs)

    def test_artifact_without_a_stamp_fails(self):
        self.art.write_bytes(b"mp4")
        errs = shot_check.validate(self.manifest, self.root)
        self.assertTrue(any("carries no" in e and "stamp" in e for e in errs), errs)

    def test_matching_stamp_passes(self):
        self.art.write_bytes(b"mp4")
        self._stamp(self.manifest)
        self.assertEqual(shot_check.validate(self.manifest, self.root), [])

    def test_caption_edited_after_assembly_fails(self):
        # THE ACTUAL SCENARIO, end to end: assemble, then edit only the burned-in caption. Every other
        # check still passes — the caption has no citations to drift from — and this is the one that
        # catches that the MP4 now says something the manifest does not.
        self.art.write_bytes(b"mp4")
        self._stamp(self.manifest)
        self.manifest["shots"][0]["caption_text"] = "something else entirely"
        errs = shot_check.validate(self.manifest, self.root)
        self.assertTrue(any("STALE ARTIFACT" in e for e in errs), errs)

    def test_frame_range_edited_after_assembly_fails(self):
        self.art.write_bytes(b"mp4")
        self._stamp(self.manifest)
        self.manifest["shots"][0]["frame_range"] = [0, 2]
        self.manifest["shots"][0]["duration_s"] = 2 / 30.0
        errs = shot_check.validate(self.manifest, self.root)
        self.assertTrue(any("STALE ARTIFACT" in e for e in errs), errs)

    def test_non_pixel_fields_do_not_invalidate_the_artifact(self):
        # The other half of the promise. A checksum over the WHOLE manifest would force a 4440-frame
        # re-assemble to change a prose note, which is how a gate teaches people to bypass it. Only
        # what assemble() consumes is in the fingerprint.
        self.art.write_bytes(b"mp4")
        self._stamp(self.manifest)
        self.manifest["shots"][0]["title"] = "a different title"
        self.manifest["_source_of_truth"] = "reworded"
        self.assertEqual(shot_check.validate(self.manifest, self.root), [])

    def test_assemble_preflight_ignores_a_stale_artifact(self):
        # shot_assemble is about to REPLACE the artifact, so a stale stamp must not block the one
        # command that fixes it. Turned off by parameter, not by filtering the message text out.
        self.art.write_bytes(b"mp4")
        self._stamp(self.manifest)
        self.manifest["shots"][0]["caption_text"] = "something else entirely"
        self.assertEqual(shot_check.validate(self.manifest, self.root, check_artifact=False), [])


class WarningChannelTests(_Root):
    """The count-mismatch fallback used to downgrade to set-membership in silence."""

    def setUp(self):
        super().setUp()
        (self.root / "ev.json").write_text(json.dumps({"budget": {"gt": 3.6, "ceil": 4.0}}))

    def _cite(self, claim, keys):
        return {"claim": claim, "source": "ev.json", "keys": keys}

    def test_set_membership_fallback_now_warns(self):
        cite = self._cite("46 instances: 3.6 / 4.0 ms", ["budget.gt", "budget.ceil"])
        warns = []
        shot_check.validate(_manifest([_shot(
            caption_text="46 instances: 3.6 / 4.0 ms", caption_cites=[cite])]), self.root, warns=warns)
        self.assertTrue(any("SET-MEMBERSHIP" in w for w in warns), warns)

    def test_the_positional_path_is_silent(self):
        # A warning on every citation would be noise, and noise is how a real warning gets ignored.
        cite = self._cite("3.6 / 4.0 ms", ["budget.gt", "budget.ceil"])
        warns = []
        errs = shot_check.validate(_manifest([_shot(
            caption_text="3.6 / 4.0 ms", caption_cites=[cite])]), self.root, warns=warns,
            check_artifact=False)
        self.assertEqual((errs, warns), ([], []))

    def test_the_shipped_manifest_takes_the_strong_path_everywhere(self):
        # "Latent by construction" used to be asserted only in prose. Asserted here instead.
        repo = Path(__file__).resolve().parents[1]
        manifest = json.loads(
            (repo / "Plugins" / "MotionTerminal" / "Tests" / "shots_manifest.json").read_text(encoding="utf-8"))
        warns = []
        shot_check.validate(manifest, repo, warns=warns, check_artifact=False)
        self.assertEqual([w for w in warns if "SET-MEMBERSHIP" in w], [])


class MissingFieldScopeTests(_Root):
    """The missing-field guard used to inspect the tail of the GLOBAL error list,
    so one shot with a missing field silenced every LATER shot's validation entirely. The 32 cases that
    existed at the time had no coverage of the missing-field path at all, which is how it survived."""

    def setUp(self):
        super().setUp()
        self._frames("y")

    def test_missing_field_is_reported(self):
        bad = _shot()
        del bad["title"]
        errs = shot_check.validate(_manifest([bad]), self.root)
        self.assertTrue(any("missing required field: title" in e for e in errs), errs)

    def test_a_later_shot_is_still_validated_after_an_earlier_missing_field(self):
        # THE REGRESSION ITSELF. The first fixture is missing a field; the second declares a duration
        # that its own frame_range contradicts. Under the old guard, `errs[-11:]` still ended with the
        # first one's error, so the second was `continue`d and its duration mismatch was never reported. Both errors must
        # appear: skipping the malformed shot must not skip the well-formed ones behind it.
        bad = _shot(shot_id="01_x", order=10)
        del bad["title"]
        later = _shot(shot_id="02_y", order=20, source_dir="captures/y", duration_s=9.0)
        errs = shot_check.validate(_manifest([bad, later]), self.root)
        self.assertTrue(any("missing required field: title" in e for e in errs), errs)
        self.assertTrue(any("[02_y]" in e and "duration_s" in e for e in errs), errs)

    def test_a_later_shot_s_citation_is_still_checked(self):
        # The same regression against the CITATION layer, which is the part of this gate everything
        # else is built on. A citation that cannot resolve its key must go red even when an
        # earlier shot was malformed.
        (self.root / "ev.json").write_text(json.dumps({"a": {"b": 1.0}}), encoding="utf-8")
        bad = _shot(shot_id="01_x", order=10)
        del bad["status"]
        later = _shot(shot_id="02_y", order=20, source_dir="captures/y",
                      caption_text="value is 9.0",
                      caption_cites=[{"claim": "9.0", "source": "ev.json", "keys": ["a.b"]}])
        errs = shot_check.validate(_manifest([bad, later]), self.root)
        self.assertTrue(any("missing required field: status" in e for e in errs), errs)
        self.assertTrue(any("[02_y]" in e and "citation drift" in e for e in errs), errs)


class EvidenceFileTests(_Root):
    """The designated capture files a terminal panel is rendered from were
    checked by nothing at all — `terminal_capture._read_output` refuses inline text, a missing file and
    an empty file, none of which a plausible fabrication is."""

    def setUp(self):
        # Not super().setUp(): the frames here must be written after the transcript (see below).
        self.root = Path(tempfile.mkdtemp())
        self.frames = self.root / "captures" / "x"
        self.frames.mkdir(parents=True)
        self.ev = self.root / "captures" / "raw" / "t.txt"
        self.ev.parent.mkdir(parents=True)
        self.ev.write_text("> tool.Call()\nreading = 1\n", encoding="utf-8", newline="\n")
        self.sha = hashlib.sha256(self.ev.read_bytes()).hexdigest()
        # Frames are written AFTER the transcript, which is the real order: the driver dumps during
        # the render and the composite runs afterwards. The gate's ORDER refusal depends on it.
        old = self.ev.stat().st_mtime
        for i in range(3):
            p = self.frames / ("frame_%04d.png" % i)
            p.write_bytes(b"x")
            os.utime(p, (old + 100, old + 100))

    def _shot_with_evidence(self, **over):
        decl = {"path": "captures/raw/t.txt", "sha256": self.sha, "written_by": "Scripts/drv.py"}
        decl.update(over)
        # has_terminal_panel is what makes the declaration REQUIRED rather than optional; a fixture
        # that declares evidence without it is the "one of the two is wrong" case, tested separately.
        return _shot(evidence_files=[decl], has_terminal_panel=True)

    def test_matching_evidence_passes(self):
        self.assertEqual(
            shot_check.validate(_manifest([self._shot_with_evidence()]), self.root,
                                check_artifact=False), [])

    def test_fabricated_content_fails(self):
        # The fabricated-transcript shape exactly: the file is present, non-empty and plausible-looking, and its content
        # is not the content the frames were rendered from.
        self.ev.write_text("> tool.Call()\nreading = 450.000000\n", encoding="utf-8", newline="\n")
        os.utime(self.ev, (self.ev.stat().st_mtime - 1000, self.ev.stat().st_mtime - 1000))
        errs = shot_check.validate(_manifest([self._shot_with_evidence()]), self.root)
        self.assertTrue(any("FOREIGN OR RE-CAPTURED EVIDENCE" in e for e in errs), errs)

    def test_evidence_newer_than_its_frames_fails_even_with_the_right_bytes(self):
        # The half that would have caught the fabricated transcript on its own, and the half that still works when nobody
        # knows what the right bytes were. Content is untouched; only the mtime moves.
        newest = max(p.stat().st_mtime for p in self.frames.glob("*.png"))
        os.utime(self.ev, (newest + 10, newest + 10))
        errs = shot_check.validate(_manifest([self._shot_with_evidence()]), self.root)
        self.assertTrue(any("EVIDENCE WRITTEN AFTER ITS FRAMES" in e for e in errs), errs)
        self.assertFalse(any("FOREIGN" in e for e in errs), errs)

    def test_absent_evidence_fails(self):
        self.ev.unlink()
        errs = shot_check.validate(_manifest([self._shot_with_evidence()]), self.root)
        self.assertTrue(any("designated capture file absent" in e for e in errs), errs)

    def test_malformed_declaration_is_a_validation_error_not_a_crash(self):
        errs = shot_check.validate(
            _manifest([_shot(evidence_files=[{"path": "captures/raw/t.txt"}])]), self.root)
        self.assertTrue(any("missing field(s)" in e for e in errs), errs)

    def test_evidence_digest_is_inside_the_assembly_fingerprint(self):
        # Binding, not decoration: change the recorded digest and the shipped MP4 must be rebuilt,
        # because the composited frames it is cut from were made from the old bytes.
        a = shot_check.assembly_fingerprint(_manifest([self._shot_with_evidence()]))
        b = shot_check.assembly_fingerprint(_manifest([self._shot_with_evidence(sha256="0" * 64)]))
        self.assertNotEqual(a, b)

    def test_artifact_path_is_inside_the_assembly_fingerprint(self):
        # `_check_artifact` is existence-gated, so pointing `artifact` at a path where
        # nothing sits used to switch the staleness gate OFF silently instead of invalidating it.
        m1 = _manifest([_shot()])
        m2 = _manifest([_shot()])
        m2["artifact"] = "captures/somewhere/else.mp4"
        self.assertNotEqual(shot_check.assembly_fingerprint(m1), shot_check.assembly_fingerprint(m2))

    def test_a_panel_shot_must_declare_its_evidence(self):
        # `evidence_files` is optional and correctly so, which left the
        # property that actually matters unchecked: a shot whose frames carry a composited terminal
        # panel MUST name the file that panel was rendered from. A future panel shot declaring nothing
        # would otherwise get no gate and no red — the fabricated-transcript exposure re-opened one
        # shot along.
        errs = shot_check.validate(
            _manifest([_shot(has_terminal_panel=True)]), self.root, check_artifact=False)
        self.assertTrue(any("has_terminal_panel is true but no evidence_files" in e for e in errs), errs)

    def test_declaring_evidence_without_the_panel_flag_fails(self):
        decl = {"path": "captures/raw/t.txt", "sha256": self.sha, "written_by": "Scripts/drv.py"}
        errs = shot_check.validate(
            _manifest([_shot(evidence_files=[decl], has_terminal_panel=False)]), self.root,
            check_artifact=False)
        self.assertTrue(any("has_terminal_panel is false" in e for e in errs), errs)

    def test_a_non_boolean_panel_flag_fails(self):
        errs = shot_check.validate(
            _manifest([_shot(has_terminal_panel="yes")]), self.root, check_artifact=False)
        self.assertTrue(any("must be true or false" in e for e in errs), errs)


class CommittedEvidenceTests(unittest.TestCase):
    """Checks against the REAL committed files, not fixtures. Everything above proves the gate works
    on synthetic data; this proves one committed number is not a hand-transcribed wish."""

    REPO = Path(__file__).resolve().parents[1]

    def test_crowd_instance_count_matches_army_config_rings(self):
        # The crowd take's caption burns "46 instances" into the frames and cites
        # render_evidence.json crowd_budget.instances_spawned for it. That value is a TRANSCRIPTION of a
        # sum taken from army_config.json's ring counts — and a transcribed total is precisely the kind
        # of number that goes stale silently while a caption keeps asserting it (the army-stage
        # still-vs-chart failure, which is why the evidence layer exists at all). So recompute it here: change a ring
        # count without updating the evidence and this goes red before the caption can lie.
        tests = self.REPO / "Plugins" / "MotionTerminal" / "Tests"
        army = json.loads((tests / "army_config.json").read_text(encoding="utf-8"))
        ev = json.loads((tests / "render_evidence.json").read_text(encoding="utf-8"))
        rings = sum(army["rings"][t]["count"] for t in ("hero", "mid", "far"))
        self.assertEqual(ev["crowd_budget"]["instances_spawned"], rings)

    def test_every_baked_inplace_clip_is_in_the_ik_effector_manifest(self):
        # `ik_effector_manifest.json` is hand-maintained and is the ONLY guard
        # against a documented baking hazard: a bake that reads through a retargeting-APPLYING API
        # (`AnimationLibrary.get_bone_pose_for_frame`) produces effector tracks that look right to the
        # baker and are wrong in the raw data model. `khai_inplace_author.py` bakes the clips and adds
        # no rows, so a newly baked clip that nobody remembers to list is unguarded and silent — the
        # gate reports green over a clip it has never heard of.
        #
        # Read with `ast` rather than by importing: that module imports `unreal`, which does not exist
        # here, and a test that can only run inside the editor is not a test a clean clone gets.
        import ast as _ast
        src = (self.REPO / "Scripts" / "khai_inplace_author.py").read_text(encoding="utf-8")
        tree = _ast.parse(src)
        consts = {}
        for node in tree.body:
            if isinstance(node, _ast.Assign) and len(node.targets) == 1 \
                    and isinstance(node.targets[0], _ast.Name):
                try:
                    consts[node.targets[0].id] = _ast.literal_eval(node.value)
                except ValueError:
                    pass
        self.assertIn("CLIPS", consts, "khai_inplace_author.CLIPS is no longer a literal - this check "
                                       "reads it statically and must be updated with it")
        self.assertIn("DST_DIR", consts)
        baked = {consts["DST_DIR"] + dst for _src, dst in consts["CLIPS"]}

        manifest = json.loads(
            (self.REPO / "Plugins" / "MotionTerminal" / "Tests"
             / "ik_effector_manifest.json").read_text(encoding="utf-8"))
        listed = {row["clip"] for row in manifest["clips"]}
        self.assertEqual(baked - listed, set(),
                         "clip(s) baked by khai_inplace_author.py with no row in "
                         "ik_effector_manifest.json - Verify.IKEffectorDrives cannot see them")


if __name__ == "__main__":
    unittest.main()
