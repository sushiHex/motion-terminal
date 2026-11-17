#!/usr/bin/env python
"""Tests for namespace_merge_check — including the permanent proven-OFF references.

A gate that has only ever been watched going green is indistinguishable from a gate that cannot fail,
and this repo has found that same fault in six costumes. So the OFF reference is not a
one-time manual mutation recorded in a log: it is baked in here, as synthetic script pairs that
reproduce the crowd pull-back take's retiming fault (an 8-second take length overwritten by the exec'd conductor's
24-second one) byte for byte, and it runs on every pytest invocation.

Run: python -m pytest Scripts/test_namespace_merge.py -q
"""
import namespace_merge_check as nmc


def _write(d, name, text):
    (d / name).write_text(text, encoding="utf-8")


# The historical fault, reduced to its smallest reproducing shape. `army_conductor.py` really does
# define TOTAL = 24.0 at module level; a driver really did name its take length TOTAL = 8.0; the
# conductor really was exec'd into that driver's own globals().
_CONDUCTOR = "TOTAL = 24.0\nDRIVE_HZ = 60.0\n\n\ndef start_conductor():\n    return TOTAL\n"
_DRIVER_COLLIDING = (
    "import os\n"
    "TOTAL = 8.0\n"
    "CONDUCTOR = os.path.join('Scripts', 'conductor_fixture.py')\n"
    "\n"
    "def bootstrap():\n"
    "    g = globals()\n"
    "    exec(open(CONDUCTOR).read(), g)\n"
)


def test_detects_the_crowd_take_retiming_collision(tmp_path):
    """THE PROVEN-OFF. Remove the intersection logic and this goes green — which is the whole point."""
    _write(tmp_path, "conductor_fixture.py", _CONDUCTOR)
    _write(tmp_path, "driver_fixture.py", _DRIVER_COLLIDING)
    errors, _notes = nmc.check(tmp_path)
    assert len(errors) == 1, errors
    assert "TOTAL" in errors[0]
    assert "driver_fixture.py" in errors[0] and "conductor_fixture.py" in errors[0]


def test_the_rename_that_actually_fixed_it_passes(tmp_path):
    """The shipped fix — prefix every driver name — must read clean, or the gate would just be noise."""
    _write(tmp_path, "conductor_fixture.py", _CONDUCTOR)
    _write(tmp_path, "driver_fixture.py", _DRIVER_COLLIDING.replace("TOTAL = 8.0", "AP_TOTAL = 8.0"))
    errors, _notes = nmc.check(tmp_path)
    assert errors == [], errors


def test_exec_into_a_fresh_dict_is_not_a_merge(tmp_path):
    """The other correct fix. `mrq_render.render_shot` and `pie_rifle_take_drive` both use it, so
    flagging it would punish the pattern this check is trying to push people towards."""
    _write(tmp_path, "conductor_fixture.py", _CONDUCTOR)
    _write(tmp_path, "driver_fixture.py",
           "import os\n"
           "TOTAL = 8.0\n"
           "CONDUCTOR = os.path.join('Scripts', 'conductor_fixture.py')\n"
           "\n"
           "def bootstrap():\n"
           "    ns = {}\n"
           "    exec(open(CONDUCTOR).read(), ns)\n"
           "    return ns['TOTAL']\n")
    errors, _notes = nmc.check(tmp_path, require_sites=False)
    assert errors == [], errors


def test_a_run_that_examined_nothing_is_not_a_pass(tmp_path):
    """The guard that keeps this gate from becoming the very fault it checks for. A directory with no
    resolvable merge site is an error, not a green: two merge sites are committed in Scripts/, so a
    clean run there that compared nothing means the resolver broke."""
    _write(tmp_path, "lonely.py", "X = 1\n")
    errors, _notes = nmc.check(tmp_path)
    assert len(errors) == 1 and "examined" in errors[0] and "NOTHING" in errors[0], errors


def test_shared_import_names_are_benign(tmp_path):
    """`import math` on both sides binds the identical module object. Reporting it would bury the real
    signal in noise from every file that imports os/json/unreal."""
    _write(tmp_path, "conductor_fixture.py", "import math\nimport os\nTOTAL = 24.0\n")
    _write(tmp_path, "driver_fixture.py",
           "import math\nimport os\n"
           "CONDUCTOR = os.path.join('Scripts', 'conductor_fixture.py')\n"
           "\n"
           "def bootstrap():\n"
           "    exec(open(CONDUCTOR).read(), globals())\n")
    errors, _notes = nmc.check(tmp_path)
    assert errors == [], errors


def test_an_import_against_an_assignment_is_not_benign(tmp_path):
    """Only an import/import overlap is safe. If one side ASSIGNS the name, the value is real data and
    the merge really does replace it."""
    _write(tmp_path, "conductor_fixture.py", "math = 3.0\nTOTAL = 24.0\n")
    _write(tmp_path, "driver_fixture.py",
           "import math\nimport os\n"
           "CONDUCTOR = os.path.join('Scripts', 'conductor_fixture.py')\n"
           "\n"
           "def bootstrap():\n"
           "    exec(open(CONDUCTOR).read(), globals())\n")
    errors, _notes = nmc.check(tmp_path)
    assert len(errors) == 1 and "math" in errors[0], errors


def test_a_bare_exec_with_no_globals_arg_is_a_merge(tmp_path):
    """`archive/army_budget_probe.py:43` is this shape: exec() with no globals argument writes into the
    CALLER's namespace, which under run_python is the persistent editor session."""
    _write(tmp_path, "conductor_fixture.py", _CONDUCTOR)
    _write(tmp_path, "driver_fixture.py",
           "import os\nTOTAL = 8.0\n"
           "exec(open(os.path.join('Scripts', 'conductor_fixture.py')).read())\n")
    errors, _notes = nmc.check(tmp_path)
    assert len(errors) == 1 and "TOTAL" in errors[0], errors


def test_the_committed_repo_is_clean():
    """The live assertion. Both real merge sites must resolve AND compare clean; if the resolver ever
    stops finding them, check() reports that as an error rather than as a pass."""
    # nmc.SCRIPTS, not a second hand-derived path: re-deriving it here is how a test ends up asserting
    # against a directory the tool under test does not actually scan.
    errors, notes = nmc.check(nmc.SCRIPTS)
    assert errors == [], errors
    examined = [n for n in notes if " merges " in n and "NOT checked" not in n]
    assert len(examined) >= 2, notes
