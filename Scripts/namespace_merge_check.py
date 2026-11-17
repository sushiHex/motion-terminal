#!/usr/bin/env python
"""Static gate for the exec-namespace-MERGE fault class.

    python Scripts/namespace_merge_check.py            # exit 0 clean, 1 on a collision

============================ WHY THIS FILE EXISTS ============================

`exec(src, some_globals)` is a plain namespace MERGE. Every module-level name the exec'd file defines
lands on top of whatever the target namespace already held, silently, with no error and no log line.

A 480-frame take was lost to exactly that. `pie_army_pullback_drive.py` named its take length
`TOTAL = 8.0`; it exec'd `army_conductor.py`, which defines `TOTAL = 24.0`, into its own `globals()`;
the conductor bootstrapped at t=0.03 and retimed an 8-second camera reveal to a 24-second one, so the
camera finished at x=2439 instead of x=7200 and two thirds of the crowd was outside the frame.

EVERY SIGNAL STAYED GREEN: 480 frames written, `army=46` every second, `capture_check` PASS,
the render's `RENDER FINISHED ... success=True` log line. It was caught only by eyeballing the frames against a committed
hero GIF. Nothing in this repo could detect it, and the only defence afterwards was a naming
convention (`AP_`/`_ap_` prefixes) recorded in three comment blocks. A convention is not a mechanism:
it holds until the next constant is added to either side.

This is that mechanism. It is static — no editor, no UE, stdlib `ast` only — because the fault is
visible in the source and a runtime check would need the editor, a live PIE world and a 480-frame
render to reach the moment it matters.

WHAT IT DOES, and why each step is the one that matters rather than a nearby easier one:

  1. Finds every `exec(...)` in Scripts/ (and Scripts/archive/) whose target namespace is a MERGE — the globals argument is
     absent, is `globals()`, or is a name bound to `globals()`. An `exec(src, {'unreal': unreal})`
     into a fresh dict (mrq_render.render_shot, pie_rifle_take_drive) is NOT a merge and is not
     reported: isolating the namespace is the fix, so flagging it would punish the correct pattern.
     An unresolvable globals expression is treated AS a merge — an unknown namespace is precisely the
     thing not to assume is safe.
  2. Resolves which file is exec'd by folding module-level string constants, so
     `exec(open(AP_CONDUCTOR).read(), g)` resolves through `AP_CONDUCTOR = os.path.join(..., 'Scripts',
     'army_conductor.py')`. Matching on the exec call's literal text alone would miss both of this
     repo's real merge sites, since neither passes a literal.
  3. Intersects the two files' MODULE-LEVEL bindings — assignments, `def`, `class`, `for` targets, and
     `with ... as` at column 0. Names bound inside a function are irrelevant: `exec` merges module
     level onto module level.
  4. Subtracts collisions where BOTH sides bound the name by `import`. `import math` in two files binds
     the identical module object, so that overlap cannot change behaviour; every other overlap can.
     A name imported on one side and assigned on the other is NOT benign and is reported.

WHAT IT CANNOT SEE, stated so the green is not read as more than it is: a driver that mutates the
target namespace through `g['NAME'] = ...` (deliberate control-variable injection, which is this
repo's dispatch protocol and is supposed to overwrite), an exec whose source file is chosen at
runtime, and any collision on a name introduced by `exec`-within-`exec`. It reads committed source,
so it also cannot see a driver typed into the editor console.
"""
import argparse
import ast
import sys
from pathlib import Path

# THE DIRECTORY THIS FILE IS IN, not `<repo>/"scripts"`. It used to go up to the repo
# root and back down by NAME, spelled lowercase — which resolves on case-insensitive NTFS and resolves
# NOWHERE on a case-sensitive filesystem, where `glob("*.py")` would then match nothing. It fails CLOSED
# (`require_sites` turns an empty scan into an error rather than a pass, which is the whole point of
# that guard), so it could never have produced a false green — but `clean_checkout_check.py` runs this
# module inside a fresh clone, so the repo's "works from a clean checkout" claim would
# have been permanently RED for a spelling on the first non-Windows machine to try it. That is the
# seventh recurrence of this repo's most persistent fault family: a path re-derived by hand at the
# wrong altitude. This module LIVES in Scripts/, so its own location is the answer and there is no
# name left to misspell.
SCRIPTS = Path(__file__).resolve().parent


def _module_bindings(tree):
    """(all module-level bound names, the subset bound by an import statement).

    Column 0 only: `exec` merges one module namespace onto another, so a name bound inside a function
    body can never collide. `global X` inside a function is deliberately NOT counted — it does not
    create the binding until that function runs, and by then the merge has already happened."""
    bound, imported = set(), set()

    def _targets(node):
        if isinstance(node, ast.Name):
            bound.add(node.id)
        elif isinstance(node, (ast.Tuple, ast.List)):
            for e in node.elts:
                _targets(e)
        elif isinstance(node, ast.Starred):
            _targets(node.value)

    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                _targets(t)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            _targets(node.target)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            _targets(node.target)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    _targets(item.optional_vars)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                name = alias.asname or alias.name.split(".")[0]
                bound.add(name)
                imported.add(name)
    return bound, imported


def _const_exprs(tree):
    """Module-level `NAME = <expr>`, for folding a path constant back to its filename literal."""
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            out[node.targets[0].id] = node.value
    return out


def _py_literals(node, consts, seen=None):
    """Every '*.py' string literal reachable from `node`, following module-level constants once.

    Deliberately loose: `os.path.join(root, 'Scripts', 'army_conductor.py').replace('\\\\', '/')` is a
    call chain around a literal, and the literal is the only part that identifies the file. Following
    it is what lets this gate resolve the two real merge sites, neither of which passes a literal."""
    seen = seen if seen is not None else set()
    found = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str) and sub.value.endswith(".py"):
            found.add(Path(sub.value.replace("\\", "/")).name)
        elif isinstance(sub, ast.Name) and sub.id in consts and sub.id not in seen:
            seen.add(sub.id)
            found |= _py_literals(consts[sub.id], consts, seen)
    return found


def _is_globals_call(node):
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "globals"


def _namespace_kind(call, scope_assigns):
    """'merge' | 'isolated' — what namespace this exec() writes into.

    `scope_assigns` maps names assigned in the enclosing function (or module) to their value
    expressions, so `g = globals(); exec(src, g)` is recognised as the merge it is."""
    if len(call.args) < 2:
        return "merge"                                  # no globals arg: the CALLER's own namespace
    g = call.args[1]
    if isinstance(g, ast.Dict):
        return "isolated"                               # a fresh dict: the correct pattern
    if _is_globals_call(g):
        return "merge"
    if isinstance(g, ast.Name):
        bound = scope_assigns.get(g.id)
        if bound is not None and isinstance(bound, ast.Dict):
            return "isolated"
        if bound is not None and _is_globals_call(bound):
            return "merge"
        if bound is not None and isinstance(bound, ast.Call) and isinstance(bound.func, ast.Attribute) \
                and bound.func.attr == "copy" and _is_globals_call(bound.func.value):
            return "isolated"                           # globals().copy() is a snapshot, not a merge
    # Anything else is unresolved. Treated as a merge on purpose: this gate exists because an
    # unexamined namespace was assumed safe once already.
    return "merge"


def _exec_sites(path, tree, consts):
    """[(lineno, kind, {target basenames})] for every exec() call in the file."""
    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            continue
        scope_assigns = {}
        for sub in ast.walk(node):
            if isinstance(sub, ast.Assign) and len(sub.targets) == 1 and isinstance(sub.targets[0], ast.Name):
                scope_assigns.setdefault(sub.targets[0].id, sub.value)
        scope_assigns.update({k: v for k, v in consts.items() if k not in scope_assigns})
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id == "exec":
                targets = _py_literals(sub, consts) - {Path(path).name}
                sites.append((sub.lineno, _namespace_kind(sub, scope_assigns), targets))
    # ast.walk from Module also reaches the nested FunctionDefs, so the same call is visited twice.
    return sorted(set((ln, kind, tuple(sorted(t))) for ln, kind, t in sites))


def check(scripts_dir=SCRIPTS, require_sites=True):
    """(errors, notes). errors == [] is a pass; notes record what was examined, so a run that
    resolved NOTHING cannot be mistaken for a run that found nothing wrong.

    `require_sites` is that guard, and it defaults ON for every real invocation: two merge sites are
    committed, so a run of Scripts/ that resolves none has a broken resolver, not a clean repo. Only
    the unit tests' synthetic no-merge fixtures turn it off, and they assert the guard separately."""
    scripts_dir = Path(scripts_dir)
    errors, notes = [], []
    parsed = {}
    # Scripts/archive/ too: an archived driver still execs live Scripts/ modules when it is rerun
    # (army_budget_probe -> army_spawn), so its merge is still one a live rename can break.
    for p in sorted(scripts_dir.glob("*.py")) + sorted(scripts_dir.glob("archive/*.py")):
        try:
            parsed[p] = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        except SyntaxError as exc:
            errors.append("%s does not parse: %s" % (p.name, exc))

    merges = 0
    for path, tree in parsed.items():
        consts = _const_exprs(tree)
        for lineno, kind, targets in _exec_sites(path, tree, consts):
            if kind != "merge":
                continue
            if not targets:
                notes.append("%s:%d exec() merges into a live namespace but its source could not be "
                             "resolved statically - NOT checked" % (path.name, lineno))
                continue
            for basename in targets:
                target = scripts_dir / basename
                if target not in parsed:
                    notes.append("%s:%d merges %s, which is not a Scripts/*.py file - NOT checked"
                                 % (path.name, lineno, basename))
                    continue
                merges += 1
                d_bound, d_imported = _module_bindings(tree)
                t_bound, t_imported = _module_bindings(parsed[target])
                shared = (d_bound & t_bound) - (d_imported & t_imported)
                if shared:
                    errors.append(
                        "%s:%d execs %s into a MERGED namespace and they share module-level name(s): "
                        "%s. The exec'd file's value WINS silently - this is the fault that retimed "
                        "the crowd pull-back take (TOTAL 8.0 -> 24.0) with every gate green. Rename on the driver "
                        "side (the `AP_`/`_ap_` convention), or exec into a fresh dict."
                        % (path.name, lineno, basename, ", ".join(sorted(shared))))
                notes.append("%s:%d merges %s - %d/%d module-level names compared, %d benign import "
                             "overlap(s)" % (path.name, lineno, basename, len(d_bound), len(t_bound),
                                             len(d_imported & t_imported)))
    if require_sites and not merges and not errors:
        # A check that examined nothing must not report a pass. Both real merge sites are committed;
        # if neither resolved, the resolver broke, not the repo.
        errors.append("no exec-into-merged-namespace site resolved at all in %s - this check examined "
                      "NOTHING and its green means nothing. Expected at least the "
                      "pie_army_pullback_drive -> army_conductor merge." % scripts_dir)
    return errors, notes


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scripts", default=str(SCRIPTS))
    ap.add_argument("-v", "--verbose", action="store_true", help="print what was examined")
    a = ap.parse_args()
    errors, notes = check(a.scripts)
    if a.verbose or errors:
        for n in notes:
            print("  .", n)
    if errors:
        print("NAMESPACE MERGE CHECK FAILED (%d):" % len(errors))
        for e in errors:
            print("  -", e)
        return 1
    print("NAMESPACE MERGE CHECK OK: %d merge site(s) examined, no shared module-level names"
          % sum(1 for n in notes if " merges " in n and "NOT checked" not in n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
