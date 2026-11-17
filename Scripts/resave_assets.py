#!/usr/bin/env python
"""Re-save every git-tracked Unreal asset with the project's engine, after an engine upgrade.

    python Scripts/resave_assets.py          # the editor must NOT be running on this project

An asset saved by an older engine still loads, but is upgraded in memory on every load, and the first editor
save that touches it rewrites it anyway, inside an unrelated change. Re-saving all of them at once makes the
upgrade one reviewable commit. It runs the engine's own ResavePackages commandlet on exactly the tracked
packages (-FILE= list), so licensed or untracked content is never rewritten. Prints which tracked files changed.
Exit 0 = the commandlet succeeded; 1 = it failed (the log names the package).
"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_verification as rv  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
LIST = REPO / 'Saved' / 'resave_assets.txt'


def tracked_packages():
    """/Game/... package names of the tracked .uasset and .umap files under Content/."""
    files = subprocess.run(['git', 'ls-files', '--', 'Content/*.uasset', 'Content/*.umap'], cwd=REPO, check=True,
                           capture_output=True, text=True).stdout.split()
    return ['/Game/' + f[len('Content/'):].rsplit('.', 1)[0] for f in files]


def changed():
    out = subprocess.run(['git', 'status', '--porcelain', '--', 'Content'], cwd=REPO, check=True,
                         capture_output=True, text=True).stdout
    return [line[3:] for line in out.splitlines()]


def main():
    packages = tracked_packages()
    LIST.parent.mkdir(exist_ok=True)
    LIST.write_text('\n'.join(packages) + '\n', encoding='utf-8')
    cmd = [str(rv._editor_cmd(rv.resolve_engine())), str(rv.PROJECT), '-run=ResavePackages', '-FILE=%s' % LIST,
           '-unattended', '-nosplash', '-nullrhi', '-stdout', '-FullStdOutLogOutput']
    print('RESAVE %d tracked packages' % len(packages))
    code = subprocess.run(cmd, cwd=REPO).returncode
    print('RESAVE exit %d; changed: %s' % (code, changed() or 'none'))
    return 0 if code == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
