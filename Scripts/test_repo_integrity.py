# Cheap, editor-free integrity gates for the things that break silently.
#
# ============================================================================================
# WHY THIS FILE EXISTS
# ============================================================================================
# Every check here exists because something in this repo was broken in exactly this way, and the
# break was invisible to every gate that already existed.
#
#   test_every_script_parses      On 2026-08-09 a global rename in three drivers rewrote
#                                 `def _tools():` into `def _mt.anim_debug_tools():` - a syntax
#                                 error in three PIE drivers at once. The duplicate-helper scan
#                                 that produced the rename reported "OK", because it matched with
#                                 a regex and never parsed. Nothing else would have caught it
#                                 either: these drivers are exec'd inside the editor, so neither
#                                 tier imports them and pytest never touched them. A broken driver
#                                 shows up as a failed RENDER, an hour later, with the frames gone.
#
#   test_every_tests_json_parses  On the same day, an edit that added a correction note to
#                                 render_evidence.json put an unescaped double quote inside a JSON
#                                 string. shot_check would have caught it; the tiers would not, and
#                                 the tiers are what usually run first.
#
#   test_manifest_rows_are_complete
#                                 LoadPieRigs silently DROPPED any row missing `rig` or `map` and
#                                 the suite reported green with a whole rig's coverage gone (fixed
#                                 in the C++ loader, 2026-08-09). The C++ fix only fires when a tier
#                                 runs, which needs an editor and several minutes. This is the same
#                                 assertion in a form that runs in under a second, so a malformed
#                                 manifest is caught at the point it is edited rather than at the
#                                 point someone happens to run a tier.
#
# NONE OF THESE REPLACE THE C++ LOADERS. They duplicate them ON PURPOSE, at a different speed and a
# different stage. The C++ side is authoritative because it is what the suite actually consumes; this
# side is what tells you before you wait for an editor.
# ============================================================================================
import ast
import glob
import json
import os
import re
import time
import unittest

import run_verification as rv

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
_TESTS_DIR = os.path.join(_REPO, 'Plugins', 'MotionTerminal', 'Tests')


def _scripts():
    # the archived provenance scripts too: kept to rerun, so they must still parse
    return sorted(glob.glob(os.path.join(_HERE, '*.py')) + glob.glob(os.path.join(_HERE, 'archive', '*.py')))


def _test_jsons():
    return sorted(glob.glob(os.path.join(_TESTS_DIR, '*.json')))


class RepoIntegrity(unittest.TestCase):

    def test_every_script_parses(self):
        """Every Scripts/*.py is syntactically valid Python.

        Deliberately parse, not import: importing would need `unreal`, which only exists inside the
        editor. Parsing catches the failure mode that actually happens here - a generated or
        search-and-replaced edit that produces a file no interpreter would accept - without needing
        the engine at all."""
        broken = []
        for path in _scripts():
            try:
                ast.parse(open(path, encoding='utf-8').read(), filename=path)
            except SyntaxError as e:
                broken.append('%s:%s: %s' % (os.path.basename(path), e.lineno, e.msg))
        self.assertEqual([], broken, 'scripts that do not parse:\n  ' + '\n  '.join(broken))

    def test_every_tests_json_parses(self):
        """Every Plugins/MotionTerminal/Tests/*.json is valid JSON.

        These files are read by C++ loaders that report a parse failure as a red test - correct, but
        only once an editor has started. A malformed manifest is worth knowing about immediately."""
        broken = []
        for path in _test_jsons():
            try:
                json.load(open(path, encoding='utf-8'))
            except (ValueError, UnicodeDecodeError) as e:
                broken.append('%s: %s' % (os.path.basename(path), e))
        self.assertEqual([], broken, 'manifests that do not parse:\n  ' + '\n  '.join(broken))

    def test_manifest_rows_are_complete(self):
        """Mirror of the C++ Require* readers, for the three row-shaped manifests.

        A row missing a required field used to be SKIPPED by the loader, which deleted that row's
        assertions while the suite stayed green. The thresholds are required to be positive for the
        same reason the C++ side requires it: a zero threshold passes everything, because
        `measured < 0.0` is false even for a completely frozen clip."""
        specs = (
            ('pie_rigs.json', 'rigs', {'rig': 'str', 'map': 'str'}),
            ('clip_manifest.json', 'clips',
             {'clip': 'str', 'bone': 'str', 'minRotVarianceDeg': 'pos'}),
            ('ik_effector_manifest.json', 'clips',
             {'clip': 'str', 'minEffectorSpanCm': 'pos',
              'maxEffectorDriftCm': 'pos', 'maxEffectorRotDeg': 'pos'}),
        )
        problems = []
        for name, key, required in specs:
            path = os.path.join(_TESTS_DIR, name)
            if not os.path.exists(path):
                problems.append('%s: missing' % name)
                continue
            data = json.load(open(path, encoding='utf-8'))
            rows = data.get(key)
            if not isinstance(rows, list) or not rows:
                problems.append("%s: '%s' is not a non-empty array" % (name, key))
                continue
            for i, row in enumerate(rows):
                if not isinstance(row, dict):
                    problems.append('%s: entry %d is not an object' % (name, i))
                    continue
                for field, kind in sorted(required.items()):
                    value = row.get(field)
                    if kind == 'str' and (not isinstance(value, str) or not value):
                        problems.append('%s: entry %d needs a non-empty %r (got %r)'
                                        % (name, i, field, value))
                    elif kind == 'pos' and (not isinstance(value, (int, float))
                                            or isinstance(value, bool) or value <= 0):
                        problems.append('%s: entry %d needs a positive %r (got %r)'
                                        % (name, i, field, value))
        self.assertEqual([], problems, 'manifest rows:\n  ' + '\n  '.join(problems))

    def test_army_budget_ceilings_are_present_and_positive(self):
        """army_config.json must carry both ms ceilings.

        The C++ loader used to fall back to baked 8.0/4.0 defaults with the read's bool discarded, so
        deleting or misspelling `gt_ms_ceiling` silently substituted 4.0 and a 3 ms result passed a
        2 ms budget. That is fixed in the loader; this asserts the config half, which is the part a
        person edits."""
        path = os.path.join(_TESTS_DIR, 'army_config.json')
        self.assertTrue(os.path.exists(path), 'army_config.json is missing')
        budget = json.load(open(path, encoding='utf-8')).get('budget')
        self.assertIsInstance(budget, dict, "army_config.json has no 'budget' object")
        for key in ('ms_ceiling', 'gt_ms_ceiling'):
            value = budget.get(key)
            self.assertIsInstance(value, (int, float), '%s must be numeric, got %r' % (key, value))
            self.assertGreater(value, 0, '%s must be positive, got %r' % (key, value))

    def test_every_cpp_skip_message_is_one_the_runner_counts(self):
        """Every whole-row C++ `SKIP (` message carries the marker run_verification counts (`SKIP (localOnly`).

        The army rows once logged `SKIP (army map absent on this machine)`. The runner counts a
        row as skipped only by its marker, so on a clean clone those rows asserted nothing yet were reported
        as EXERCISED, which overstated the README's clean-clone coverage by three rows.

        A PARTIAL skip inside a row that still asserts other things is raised with AddWarning instead
        (ImportScaleTests' binary-FBX leg), which the runner surfaces on a green row; that is allowed."""
        stray = []
        for path in glob.glob(os.path.join(_REPO, 'Plugins', 'MotionTerminal', 'Source', '**', '*.cpp'),
                              recursive=True) + glob.glob(os.path.join(_REPO, 'Source', '**', '*.cpp'), recursive=True):
            for n, line in enumerate(open(path, encoding='utf-8', errors='replace'), 1):
                # Anywhere inside a TEXT literal, so a prefixed message ("[Army] SKIP (...") cannot slip by.
                if (re.search(r'TEXT\("[^"]*SKIP \(', line) and 'SKIP (localOnly' not in line
                        and 'AddWarning(' not in line):
                    stray.append('%s:%d' % (os.path.relpath(path, _REPO), n))
        self.assertEqual([], stray, 'SKIP messages the runner will count as EXERCISED:\n  ' + '\n  '.join(stray))

    def test_budgeted_rings_declare_a_valid_significance(self):
        """A ring's `budget` object needs a significance in (0, 1], as LoadArmyConfig requires.

        Registration without one leaves the allocator's default (1, never throttle), so a ring could be
        "budgeted" and still run at full rate."""
        rings = json.load(open(os.path.join(_TESTS_DIR, 'army_config.json'), encoding='utf-8'))['rings']
        # Pie.Army.BudgetThrottle errors when no ring declares a budget; deleting them all must not read green here.
        self.assertTrue(any('budget' in r for r in rings.values()), 'no ring declares a budget object')
        for name, ring in rings.items():
            if 'budget' not in ring:
                continue
            sig = ring['budget'].get('significance')
            self.assertIsInstance(sig, (int, float), "ring %r budget.significance must be numeric" % name)
            self.assertTrue(0 < sig <= 1, 'ring %r budget.significance %r not in (0, 1]' % (name, sig))


class TierCountClaims(unittest.TestCase):
    """The test-count claims in README.md / docs/verification-design.md must match the recorded tier counts.

    Measured 2026-10-19: the README said default 23 while the suite had 24, and `54 passed` while pytest
    had 111, and nothing noticed. tier_counts.json (written by run_verification on a whole-tier run) is
    the record; TIER_MIN_ROWS must equal its `rows`, and each CURRENT-claim sentence below must match
    it. Only the sentences pinned here are checked: dated measurements and correction notes elsewhere
    quote old figures on purpose. If you reword a pinned sentence the test goes red on 'phrasing not
    found' - update the regex with it, that is the point.

    Not checked: exercised/skipped, which depend on the machine's licensed content (see tier_counts docs
    in run_verification.py)."""

    @classmethod
    def setUpClass(cls):
        cls.counts = json.load(open(os.path.join(_TESTS_DIR, 'tier_counts.json'), encoding='utf-8'))
        cls.readme = open(os.path.join(_REPO, 'README.md'), encoding='utf-8').read()
        cls.design = open(os.path.join(_REPO, 'docs', 'verification-design.md'), encoding='utf-8').read()

    def _find(self, text, pattern, what):
        m = re.search(pattern, text)
        self.assertIsNotNone(m, '%s: phrasing not found (reworded? update the regex): %s' % (what, pattern))
        return m

    def test_floor_equals_recorded_rows(self):
        rows = {t: v['rows'] for t, v in self.counts.items()}
        self.assertEqual(rows, rv.TIER_MIN_ROWS,
                         'TIER_MIN_ROWS differs from tier_counts.json - re-run the tiers or raise the floor')

    def test_readme_tier_rows(self):
        want = self.counts
        got = {
            'default': int(self._find(self.readme, r'run_verification\.py\s+# topology[^\n]*Tests: (\d+)',
                                      'README default').group(1)),
            'pie': int(self._find(self.readme, r'--tier pie\s+# PIE[^\n]*Tests: (\d+)',
                                  'README pie').group(1)),
        }
        m = self._find(self.readme,
                       r'\*\*\d+ of the (\d+)\*\* default-tier rows and \*\*\d+ of the (\d+)\*\* pie rows',
                       'README skip sentence')
        got_skip = {'default': int(m.group(1)), 'pie': int(m.group(2))}
        for tier in ('default', 'pie'):
            self.assertEqual(want[tier]['rows'], got[tier], 'README %s Tests: row count' % tier)
            self.assertEqual(want[tier]['rows'], got_skip[tier], 'README "N of the M" %s total' % tier)

    def test_design_doc_current_measurement(self):
        # The current figure is either a clean-clone measurement or, when rows were added after the last one,
        # a derivation from it that says so (the measured count plus the rows added since); a superseded figure is struck through, so its
        # `pie ~~**N/M**~~` no longer matches and the first match is the current claim.
        m = self._find(self.design,
                       r'(?:Measured on a real clone of|Derived from the) `\w+`(?: clone measurement[^:]*)?: '
                       r'default \*\*(\d+)/\d+\*\* \(\d+ SKIP\), pie \*\*(\d+)/\d+\*\*',
                       'verification-design measurement')
        self.assertEqual(self.counts['default']['rows'], int(m.group(1)), 'design doc default rows')
        self.assertEqual(self.counts['pie']['rows'], int(m.group(2)), 'design doc pie rows')

    def test_readme_pytest_count_matches_collection(self):
        m = self._find(self.readme, r'pytest Scripts/ -q[^\n]*?(\d+) passed, (\d+) skipped', 'README pytest')
        claimed = int(m.group(1)) + int(m.group(2))
        # --collect-only never executes a test body, so this test cannot recurse into itself.
        import subprocess, sys
        out = subprocess.run([sys.executable, '-m', 'pytest', 'Scripts', '--collect-only', '-q'],
                             cwd=_REPO, capture_output=True, text=True, timeout=300)
        c = re.search(r'(\d+) tests? collected', out.stdout)
        self.assertIsNotNone(c, 'could not read the collection count:\n' + out.stdout[-500:] + out.stderr[-500:])
        self.assertEqual(int(c.group(1)), claimed,
                         'README claims %d passed+skipped; pytest collects %s' % (claimed, c.group(1)))


class HeadroomCorrelation(unittest.TestCase):
    """run_verification.low_headroom_note - the advisory that tells you a red might be GPU paging.

    It exists because two consecutive pie reds were pure paging (free VRAM above the old 4096 MiB floor, failing
    DIFFERENT rows each time on identical code) and were read as possible code regressions. It lives
    in a pure function specifically so these four cases can prove it fires and, just as importantly,
    proves it stays quiet - an advisory that cried wolf on every green run would be turned off within
    a week, and then it would be there without being there."""

    def test_fires_when_a_red_coincides_with_low_headroom(self):
        note = rv.low_headroom_note(2, (5100, 4800), floor=8192)
        self.assertTrue(note, 'a red at 4800 MiB against a 8192 floor must produce the advisory')
        self.assertIn('4800', ' '.join(note), 'it must quote the WORST sample, not the first')

    def test_silent_when_nothing_failed(self):
        self.assertEqual([], rv.low_headroom_note(0, (40, 40), floor=8192),
                         'a green run must stay quiet no matter how little headroom there was')

    def test_silent_when_headroom_was_ample(self):
        self.assertEqual([], rv.low_headroom_note(3, (21561, 20000), floor=8192),
                         'a red on a quiet GPU is a real red and must not be excused')

    def test_silent_when_headroom_is_unmeasurable(self):
        self.assertEqual([], rv.low_headroom_note(3, (None, None), floor=8192),
                         'no nvidia-smi means no opinion - it must not guess')

    # Per-row windowing: a VRAM dip is attributed to a failing row only if it falls inside that row's own
    # time window (start, duration, padding), tested against a synthetic 1 Hz series. T0 is 2026-10-19 21:39:03 UTC.
    T0 = 1792445943

    def _row(self, start_offset, duration, name='Pie.X'):
        stamp = time.strftime('%Y.%m.%d-%H.%M.%S', time.gmtime(self.T0 + start_offset))
        return {'fullTestPath': name, 'dateTime': stamp, 'duration': duration}

    def test_row_window_parses_report_timestamp(self):
        w = rv.row_window(self._row(0, 6.0))
        pad = rv.ROW_WINDOW_PAD_S
        self.assertEqual((self.T0 - pad, self.T0 + 6.0 + pad), w)
        self.assertIsNone(rv.row_window({'fullTestPath': 'x'}), 'no dateTime means no window')

    def test_min_in_window_ignores_samples_outside_it(self):
        series = [(self.T0 + i, 9000) for i in range(0, 60)]
        series[30] = (self.T0 + 30, 1200)     # the dip, during the failing row
        series[50] = (self.T0 + 50, 300)      # a deeper dip AFTER the row: must not be attributed to it
        w = rv.row_window(self._row(28, 5.0))
        self.assertEqual(1200, rv.min_free_in_window(series, w))
        self.assertIsNone(rv.min_free_in_window(series, rv.row_window(self._row(500, 5.0))))
        self.assertIsNone(rv.min_free_in_window(series, None))

    def test_failed_row_lines_quote_row_and_run_minimum(self):
        series = [(self.T0 + i, 9000) for i in range(60)]
        series[30] = (self.T0 + 30, 1200)
        series[50] = (self.T0 + 50, 300)
        lines = rv.failed_row_vram_lines(
            [self._row(28, 5.0, 'Pie.A'), self._row(10, 3.0, 'Pie.B')], series)
        self.assertEqual(2, len(lines))
        self.assertIn('Pie.A', lines[0]); self.assertIn('1200 MiB', lines[0]); self.assertIn('300 MiB', lines[0])
        self.assertIn('9000 MiB', lines[1], 'a row outside the dip must report its own, higher, minimum')
        self.assertEqual([], rv.failed_row_vram_lines([self._row(0, 1.0)], []),
                         'no samples (default tier / no nvidia-smi) means no lines')


class TierCountRecord(unittest.TestCase):
    def test_record_merges_per_tier(self):
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / 'tier_counts.json'
            rv._record_tier_counts('default', 25, path=p)
            rv._record_tier_counts('pie', 23, path=p)
            data = json.loads(p.read_text(encoding='utf-8'))
            self.assertEqual({'default': {'rows': 25}, 'pie': {'rows': 23}}, data,
                             'recording pie must not drop default; only rows is stored')
            before = p.read_bytes()
            os.utime(p, (1, 1))
            rv._record_tier_counts('pie', 23, path=p)
            self.assertEqual(before, p.read_bytes(), 'a no-change run must leave the bytes identical')
            self.assertEqual(1, int(os.stat(p).st_mtime), 'a no-change run must not rewrite the file')

    def test_failed_or_subset_run_does_not_record(self):
        self.assertTrue(rv._should_record(None, []))
        self.assertFalse(rv._should_record(None, [{'state': 'Fail'}]), 'a red run is not evidence')
        self.assertFalse(rv._should_record('MotionTerminal.Pie.X', []), 'a --prefix subset is not the tier')


if __name__ == '__main__':
    unittest.main()
