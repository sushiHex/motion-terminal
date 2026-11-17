"""Copy everything git does not carry, and this machine cannot regenerate, to a backup location.

A push protects the committed source. It does not protect the rendered frames (`captures/`), the
machine-local stages and Khaimera/MetaHuman assets (`Content/`),
the FBX import-scale fixtures, the cited proven-OFF test reports, or the only raw record of the D3D12 fatal.
The opening shot's camera exists nowhere else, so its frames are irreplaceable.

Additive, never a mirror: robocopy /E copies new and changed files and deletes nothing in the target,
so a mistaken local deletion cannot propagate into the backup.

Usage: python Scripts/backup_local.py D:/MotionTerminal-backup [--dry-run]
Writes <target>/backup_manifest.json (git HEAD, per-source file count and bytes, robocopy exit codes).
"""
import argparse
import datetime
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (source relative to the repo, why it is here). Keep each reason one line; the list is the policy.
SOURCES = [
    ("captures", "rendered frames and evidence; gitignored; the opening shot cannot be re-shot"),
    ("Content", "machine-local stages, Khaimera/MetaHuman binds, licensed packs"),
    ("Saved/MotionTerminalShot8", "FBX import-scale fixtures cited by fbx_unit_catchfix_drive.py"),
    ("Saved/TestReports", "the three cited proven-OFF runs"),
    ("Saved/Crashes", "only raw record of the undiagnosed D3D12 fatal"),
]
EXCLUDE_DIRS = ["Intermediate", "Binaries", "DerivedDataCache", "__pycache__", ".pytest_cache"]


def tree_stats(path):
    files = size = 0
    for root, dirs, names in os.walk(path):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for n in names:
            files += 1
            size += os.path.getsize(os.path.join(root, n))
    return files, size


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("target")
    ap.add_argument("--dry-run", action="store_true", help="list what would be copied; copy nothing")
    args = ap.parse_args(argv)
    head = subprocess.run(["git", "-C", REPO, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    report = {"when": datetime.datetime.now().isoformat(timespec="seconds"), "repo": REPO, "head": head, "sources": []}
    failed = False
    for rel, why in SOURCES:
        src = os.path.join(REPO, rel)
        if not os.path.isdir(src):
            report["sources"].append({"path": rel, "why": why, "status": "absent"})
            print("absent  %s" % rel)
            continue
        files, size = tree_stats(src)
        entry = {"path": rel, "why": why, "files": files, "bytes": size}
        if not args.dry_run:
            cmd = ["robocopy", src, os.path.join(args.target, rel), "/E", "/R:1", "/W:1", "/NP", "/NFL", "/NDL",
                   "/XD"] + EXCLUDE_DIRS
            rc = subprocess.run(cmd, capture_output=True, text=True).returncode
            entry["robocopy_exit"] = rc
            failed |= rc >= 8  # robocopy: 0-7 are success variants, 8+ means some copy failed
        report["sources"].append(entry)
        print("%-8s %-28s %7d files %8.2f GB" % ("listed" if args.dry_run else
                                                    ("FAILED" if entry.get("robocopy_exit", 0) >= 8 else "copied"),
                                                    rel, files, size / 1e9))
    if not args.dry_run:
        os.makedirs(args.target, exist_ok=True)
        with open(os.path.join(args.target, "backup_manifest.json"), "w") as f:
            json.dump(report, f, indent=1)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
