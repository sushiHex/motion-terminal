#!/usr/bin/env python
"""Bind COMMITTED image artifacts (hero GIFs, contact sheets, stills) to the shot footage they came from.

============================ WHY THIS FILE EXISTS ============================

A committed image derived from rendered shot footage is read as evidence of what the footage shows, and
nothing else gates it. `shot_check` validates the shot manifest and the assembled
MP4; it has never known these images exist. A shot re-rendered after an artifact was built leaves that
artifact silently stale, showing footage that no longer ships, with every gate green.

Not hypothetical. A committed contact sheet was found carrying a frame from a shot's SUPERSEDED render —
the take whose evidence file a stub harness overwrote — because the shot was re-rendered 7 h 31 m after
the sheet was built. It was caught by a hand-run timestamp comparison, and the fixture README then
documented that comparison AS PROSE. This project's own stance, written into
`shot_check._check_caption_binding`, is that conventions drift while gates do not; leaving it as prose was
inconsistent with the standard everything else here is held to.

WHICH ARTIFACTS. Each fixture directory that ships derived images carries an `artifact_stamps.json`
(its `fixture_dir` names the directory it binds). This check DISCOVERS every stamp file matching
STAMPS_GLOB under the root rather than naming one folder, so a stamp file added or retired needs no edit
here. A tree with no stamp files passes with an explicit "no stamped artifacts in this tree" line;
`--stamps PATH` checks one named file instead.

============================ THE CONSTRAINT THAT SHAPES THE DESIGN ============================

Two facts rule out the obvious implementations.

  * GIT DOES NOT PRESERVE MTIMES. The comparison that actually caught the stale sheet — "is the newest
    source frame newer than the artifact?" — is meaningless in a fresh clone, where every file is stamped
    with checkout time. Shipping it as the gate would ship a check that reports whatever the last `git
    clone` happened to do. So staleness is expressed as CONTENT identity, not as time.
  * THE SOURCE FRAMES ARE GITIGNORED. `captures/` is tens of gigabytes and absent from every clean checkout, so the
    content comparison cannot run there at all. It SKIPs-with-log, the pattern this repo already uses for
    every machine-local dependency (`clean_checkout_check` asserts each tier reports >= 1 `SKIP (localOnly`
    row; the C++ rows skip when Paragon/MetaHuman/GAS content is absent).

So the gate is in two halves, and the split is the honest one rather than a convenient one:

  COMMITTED-DATA HALF - runs everywhere, including a clean clone, and never skips:
    * every file in the fixture directory has a stamp, and every stamp names a file that exists
      (a new artifact dropped in beside the others is UNGATED until it is stamped, so that is an error);
    * each artifact's own sha256 still matches the committed bytes;
    * the shot each artifact was built from still has the `source_dir` and `frame_range` the stamp
      recorded — move a shot's window and every artifact showing it is stale by construction;
    * the generator still SELECTS the same frames: `HERO`/`SHEET_FRAME` below are the offset/count/step
      arithmetic `hero_gifs.py` builds with, and a stamp that disagrees with them means the artifact
      on disk was built by different parameters than the ones committed next to it.

  LOCAL-ONLY HALF - runs only where `captures/` is, SKIPs-with-log otherwise:
    * the frames the artifact consumed still hash to what they hashed when it was built. THIS is the leg
      that catches the re-render, and it is exactly the `evidence_files` mechanism one level out:
      `shot_check._check_evidence` records the sha256 of a designated capture file, this records a digest
      over the designated capture FRAMES.

============================ WHAT A DIGEST CAN AND CANNOT PROMISE ============================

Stated here rather than left to be discovered, because a check that cannot observe what it claims to
check is the failure this whole verification layer exists to prevent.

It CANNOT verify that the GIF is a rendering of those frames. Nothing short of decoding and comparing
pixels could, and even that would founder on palette quantisation and the JPEG crop. `_check_artifact`'s
comment makes the same admission about the MP4 and it is the same admission here: what is promised is that
the artifact on disk was built from THESE source bytes under THESE parameters, because any change to either
side turns the gate red and clearing it is then a deliberate act with a reason written down. A silent
divergence is what was possible before; that is what stops.

A STILL CROPPED FROM ONE FRAME IS COVERED, WITH ONE LEG MISSING, AND THE MISSING LEG IS NAMED. Such a
still is not produced by `hero_gifs.py` — it is a crop of one shot frame (for example by a PIL command
recorded in its stamp's `built_by`, which reproduces the bytes). Its bytes, its source frame's bytes, and
its shot's window are all bound exactly like the others (derivation `hand_crop`), so a re-render of that
shot turns it red. What is NOT bound is the crop itself: no generator constant reproduces it, so `select`
for a hand crop is an author's declaration that nothing checks. That is a real residual and it is narrow
— re-cropping is a manual act — but it is the one kind of artifact whose production step is not pinned by
committed parameters, which is the thing `hero_gifs.py` exists to guarantee for the GIFs and sheets.

============================ WHERE IT RUNS ============================

`Scripts/test_fixture_freshness.py`, collected by `pytest Scripts` — which `clean_checkout_check.py` runs
against a fresh clone, so wiring it into pytest puts it in both homes with one hook. It is NOT a C++
automation row: this is host-side committed data and an automation tier would need an editor to read a
JSON file. Unlike `shot_check`, which clean_checkout_check asserts must REFUSE on a clean clone (no frames
== nothing to assemble), this one must PASS there: the committed half is checkable without footage and the
local half skips.

Stdlib only. Run:
    python Scripts/fixture_freshness_check.py [--root PATH]          # every discovered stamp file
    python Scripts/fixture_freshness_check.py --stamps research/fixtures/<dir>/artifact_stamps.json
    python Scripts/fixture_freshness_check.py --write     # re-stamp after rebuilding an artifact
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

SCHEMA_VERSION = 1
# Where stamp files are discovered, relative to the root. Only the committed fixture tree is searched:
# captures/ (gitignored, tens of GB) is never walked.
FIXTURES_DIR = "research/fixtures"
STAMPS_NAME = "artifact_stamps.json"
STAMPS_GLOB = FIXTURES_DIR + "/**/" + STAMPS_NAME
NO_STAMPS = "no stamped artifacts in this tree"
DEFAULT_MANIFEST = "Plugins/MotionTerminal/Tests/shots_manifest.json"
# README.md is prose about the directory, not a derived artifact; the stamp file is this gate's own data.
NOT_ARTIFACTS = ("README.md", STAMPS_NAME)
DERIVATIONS = ("hero_gif", "contact_sheet", "hand_crop")

# ---------------------------------------------------------------------------------------------------
# THE SELECTION ARITHMETIC LIVES HERE, NOT IN THE GENERATOR (and `hero_gifs.py` imports it back).
#
# Which source frames each artifact consumes is knowledge BOTH the generator and this gate need, and a
# second copy of it in the gate would be free to drift from the copy that actually builds the pixels —
# the same defect `hero_gifs` avoids by importing `shot_assemble._caption` instead of reimplementing
# it, so that a GIF cannot drift from the MP4's captions. The direction (generator imports gate) is the
# one already established here: `hero_gifs` and `shot_assemble` both import `shot_check`.
#
# The other reason it lives here: this module is STDLIB-ONLY and `hero_gifs` needs PIL. A gate that
# could only run where Pillow is installed would be a gate that silently does not run.
# ---------------------------------------------------------------------------------------------------

# shot_id -> (start_offset_into_the_shot, n_source_frames, source_step)
# Offsets are measured from the shot's own frame_range[0], so these stay meaningful if a shot's window
# moves. `step` subsamples: shots 04/07 were rendered at 60fps for the 50%-speed timeline, so taking
# every 2nd frame there returns them to real time rather than shipping a GIF that looks like slow-mo
# for reasons a README reader cannot see.
HERO = {
    "05_roster_lineup": (60, 120, 2),
    "04_footik_ab":     (60, 120, 2),
    "07_aim_hand_ik":   (700, 78, 2),
    "06_crowd":         (120, 120, 2),
}
# Which frame of each shot represents it on the contact sheet, as an offset into the shot's own range.
# The default is the midpoint, which is right for a continuous camera move and WRONG for a scrolling
# terminal: the verification shot's midpoint (`08_verification`) lands squarely on UnrealBuildTool platform-validation boilerplate and
# shows none of the beat. Overriding WHICH FRAME is picked still satisfies the real-output rule (the
# frame ships exactly as rendered); editing one to look better would not.
SHEET_FRAME = {"08_verification": 200}


def discover_stamps(root):
    """Every committed stamp file under `root`, as repo-relative POSIX paths, sorted."""
    root = Path(root)
    return sorted(p.relative_to(root).as_posix() for p in root.glob(STAMPS_GLOB) if p.is_file())


def shot_frames(root, shot):
    """A shot's own frame list: the PNGs in its source_dir, sliced to its frame_range."""
    d = Path(root) / shot["source_dir"]
    return sorted(d.glob("*.png"))[shot["frame_range"][0]:shot["frame_range"][1]]


def expected_select(derivation, shot_id, n_frames):
    """The frame selection the GENERATOR would make today, or None where nothing generates it.

    `n_frames` is the length of the shot's own frame list — (hi - lo) from the manifest, which
    `shot_check` already guarantees equals the on-disk count for a shipped shot (it fails the
    frame_range check otherwise). Passing the declared length rather than a directory listing is what
    lets this run on a checkout with no `captures/`."""
    if derivation == "hero_gif":
        off, n, step = HERO[shot_id]
        return {"kind": "window", "offset": off, "count": n, "step": step}
    if derivation == "contact_sheet":
        return {"kind": "index", "index": SHEET_FRAME.get(shot_id, n_frames // 2)}
    return None            # hand_crop: an author's declaration, see the module docstring


def select_indices(select, n_frames):
    """Indices into a shot's own frame list. Slice semantics, deliberately: the generator writes
    `frames[off:off + n:step]`, so this must clamp the same way an out-of-range slice does rather than
    raise where the generator would silently shorten."""
    if select.get("kind") == "window":
        return list(range(n_frames))[select["offset"]:select["offset"] + select["count"]:select["step"]]
    if select.get("kind") == "index":
        i = select["index"]
        return [i] if 0 <= i < n_frames else []
    return []


def frames_digest(paths):
    """One digest over an ordered set of frames, covering NAME and CONTENT.

    Names are in because the order and identity of the frames is part of what the artifact is: two
    renders that happened to produce the same bytes under different filenames are not the same window.
    Per-frame digests are folded in rather than the raw bytes so this stays O(1) in memory over ~1.8 MB
    PNGs."""
    h = hashlib.sha256()
    for p in paths:
        h.update(p.name.encode("utf-8"))
        h.update(b"\0")
        h.update(hashlib.sha256(p.read_bytes()).hexdigest().encode("ascii"))
        h.update(b"\n")
    return h.hexdigest()


def _sha256_file(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def validate(stamps, manifest, root):
    """Return (errors, skips). Empty `errors` == pass; `skips` are the localOnly rows that could not run
    on this machine, returned rather than printed so callers (pytest, the CLI) can each report them in
    their own idiom — the same reason `shot_check.validate` takes a `warns` list."""
    root = Path(root)
    errs, skips = [], []

    if stamps.get("schemaVersion") != SCHEMA_VERSION:
        errs.append("schemaVersion %r != %d" % (stamps.get("schemaVersion"), SCHEMA_VERSION))
    fixture_dir = root / stamps.get("fixture_dir", "")
    if not fixture_dir.is_dir():
        return errs + ["fixture_dir absent: %s" % stamps.get("fixture_dir")], skips

    by_id = {s.get("shot_id"): s for s in manifest.get("shots", [])}
    entries = stamps.get("artifacts", [])

    # EVERY FILE IN THE DIRECTORY MUST BE STAMPED. Checking only the stamped files would gate exactly
    # the artifacts somebody remembered to gate, which is the "convention, not check" failure this file
    # exists to end: the next image dropped in beside the stamped ones would be as unwatched as every
    # one of them was before this check existed.
    on_disk = sorted(p.name for p in fixture_dir.iterdir()
                     if p.is_file() and p.name not in NOT_ARTIFACTS)
    stamped = sorted(str(e.get("file")) for e in entries if isinstance(e, dict))
    for name in set(on_disk) - set(stamped):
        errs.append("%s is in %s with no stamp - it ships in git and nothing binds it to the footage it "
                    "was derived from. Add an entry (see --write)." % (name, stamps["fixture_dir"]))
    for name in set(stamped) - set(on_disk):
        errs.append("%s is stamped but is not in %s" % (name, stamps["fixture_dir"]))

    for e in entries:
        if not isinstance(e, dict) or "file" not in e:
            errs.append("malformed artifact entry: %r" % (e,))
            continue
        tag = "[%s]" % e["file"]
        art = fixture_dir / e["file"]
        if not art.is_file():
            continue                                   # already reported above

        # (1) THE COMMITTED BYTES. Rebuild an artifact and forget the re-stamp and this is what fires —
        # which is also what forces the source digests below to be re-recorded, since --write does both.
        got = _sha256_file(art)
        if got != e.get("sha256"):
            errs.append("%s the committed file has changed: hashes %s, stamp records %s. It was rebuilt "
                        "or edited without re-stamping, so the source binding below describes a file "
                        "that is no longer there. Re-run "
                        "`python Scripts/fixture_freshness_check.py --write`."
                        % (tag, got[:12], str(e.get("sha256"))[:12]))

        deriv = e.get("derivation")
        if deriv not in DERIVATIONS:
            errs.append("%s derivation %r not in %s" % (tag, deriv, list(DERIVATIONS)))
            continue

        for i, src in enumerate(e.get("sources") or []):
            stag = "%s sources[%d]" % (tag, i)
            if not isinstance(src, dict) or "shot_id" not in src:
                errs.append("%s malformed: %r" % (stag, src))
                continue
            shot = by_id.get(src["shot_id"])
            if shot is None:
                errs.append("%s names shot %r, which is not in the shot manifest" % (stag, src["shot_id"]))
                continue

            # (2) THE SHOT'S WINDOW. A frame_range or source_dir edit re-points the artifact at
            # different footage without touching a byte of it, and none of the hashes below would
            # notice on a machine with no captures/. This half runs everywhere.
            if src.get("source_dir") != shot.get("source_dir"):
                errs.append("%s was built from %s; the manifest now says the shot lives at %s"
                            % (stag, src.get("source_dir"), shot.get("source_dir")))
                continue
            if list(src.get("frame_range") or []) != list(shot.get("frame_range") or []):
                errs.append("%s was built from frame_range %s; the manifest now declares %s, so the "
                            "artifact shows frames the shot no longer covers"
                            % (stag, src.get("frame_range"), shot.get("frame_range")))
                continue

            lo, hi = shot["frame_range"]
            n_frames = hi - lo
            select = src.get("select") or {}

            # (3) THE GENERATOR'S PARAMETERS. HERO/SHEET_FRAME above are what hero_gifs builds with
            # TODAY; the stamp is what it built with. Change an offset and every GIF committed under the
            # old one is stale, on any machine, with no footage needed to prove it.
            want = expected_select(deriv, src["shot_id"], n_frames)
            if want is not None and select != want:
                errs.append("%s was built with %r; %s selects %r today, so the committed artifact is "
                            "not what the current parameters produce"
                            % (stag, select, "HERO" if deriv == "hero_gif" else "SHEET_FRAME", want))
                continue
            idx = select_indices(select, n_frames)
            if not idx:
                errs.append("%s selects no frames out of %d (%r)" % (stag, n_frames, select))
                continue
            if src.get("n_frames") != len(idx):
                errs.append("%s records %r frames, its selection yields %d"
                            % (stag, src.get("n_frames"), len(idx)))
                continue

            # (4) THE FOOTAGE ITSELF - localOnly. captures/ is gitignored and 35 GB; a clean clone has
            # none of it, and an absent source_dir there is the NORMAL state, not a stale artifact.
            d = root / shot["source_dir"]
            if not d.is_dir():
                skips.append("SKIP (localOnly) %s - %s is not on this machine, so the source-frame "
                             "digest cannot be recomputed. The committed-data checks above still ran."
                             % (stag, shot["source_dir"]))
                continue
            frames = shot_frames(root, shot)
            if len(frames) < n_frames:
                errs.append("%s %s holds %d frames in range, the manifest declares %d - shot_check "
                            "will have more to say about this" % (stag, shot["source_dir"],
                                                                  len(frames), n_frames))
                continue
            picked = [frames[i] for i in idx]
            if src.get("first") != picked[0].name or src.get("last") != picked[-1].name:
                errs.append("%s was built from %s..%s; the same selection now lands on %s..%s"
                            % (stag, src.get("first"), src.get("last"),
                               picked[0].name, picked[-1].name))
                continue
            dig = frames_digest(picked)
            if dig != src.get("digest"):
                errs.append(
                    "%s STALE ARTIFACT: the %d source frame(s) %s..%s now digest %s, the stamp records "
                    "%s. The shot was re-rendered after this artifact was built, so the committed image "
                    "shows footage that no longer ships. Rebuild it (%s) and re-stamp."
                    % (stag, len(picked), picked[0].name, picked[-1].name, dig[:12],
                       str(src.get("digest"))[:12], e.get("built_by", "see the fixture README")))
    return errs, skips


# ============================ RE-STAMPING ============================

def restamp(stamps, manifest, root):
    """Recompute every source digest from the footage on disk. Requires captures/ - refuses without it,
    because a stamp written from absent frames would be a stamp of nothing.

    IDEMPOTENT AND WHOLE-FILE ON PURPOSE. `hero_gifs --only sheet|gifs` exists because GIF palette
    quantisation is not bit-reproducible and a blanket rebuild rewrites ~8 MB of git history for nothing.
    Re-stamping has no such cost: it reads the artifacts and the frames and writes what it finds, so an
    entry whose inputs did not change comes out byte-identical and the diff shows only what moved."""
    root = Path(root)
    fixture_dir = root / stamps["fixture_dir"]
    by_id = {s["shot_id"]: s for s in manifest["shots"]}
    for e in stamps["artifacts"]:
        art = fixture_dir / e["file"]
        e["sha256"] = _sha256_file(art)
        for src in e.get("sources") or []:
            shot = by_id[src["shot_id"]]
            src["source_dir"] = shot["source_dir"]
            src["frame_range"] = list(shot["frame_range"])
            n_frames = shot["frame_range"][1] - shot["frame_range"][0]
            want = expected_select(e["derivation"], src["shot_id"], n_frames)
            if want is not None:
                src["select"] = want
            frames = shot_frames(root, shot)
            if not frames:
                raise SystemExit("REFUSING to stamp %s: %s has no frames on this machine"
                                 % (e["file"], shot["source_dir"]))
            picked = [frames[i] for i in select_indices(src["select"], n_frames)]
            src["n_frames"] = len(picked)
            src["first"], src["last"] = picked[0].name, picked[-1].name
            src["digest"] = frames_digest(picked)
    return stamps


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".")
    ap.add_argument("--stamps", default=None,
                    help="one stamp file, relative to --root (default: every file matching %s)"
                    % STAMPS_GLOB)
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST)
    ap.add_argument("--write", action="store_true",
                    help="recompute every digest from the footage on this machine and rewrite the stamp")
    a = ap.parse_args(argv)

    root = Path(a.root).resolve()
    paths = [a.stamps] if a.stamps else discover_stamps(root)
    if not paths:
        # A tree that ships no derived images has nothing to bind: a pass, said out loud so it is never
        # mistaken for a check that ran over artifacts and found them current.
        print("FIXTURE FRESHNESS CHECK OK: %s (nothing matches %s)" % (NO_STAMPS, STAMPS_GLOB))
        return 0
    manifest = json.loads((root / a.manifest).read_text(encoding="utf-8"))

    rc = 0
    for rel in paths:
        stamps = json.loads((root / rel).read_text(encoding="utf-8"))
        if a.write:
            restamp(stamps, manifest, root)
            (root / rel).write_text(json.dumps(stamps, indent=2) + "\n", encoding="utf-8")
            print("re-stamped %d artifact(s) -> %s" % (len(stamps["artifacts"]), rel))
            continue

        errs, skips = validate(stamps, manifest, root)
        for s in skips:
            print(s)
        if errs:
            print("FIXTURE FRESHNESS CHECK FAILED (%d) [%s]:" % (len(errs), rel))
            for e in errs:
                print("  -", e)
            rc = 1
            continue
        n_src = sum(len(e.get("sources") or []) for e in stamps["artifacts"])
        print("FIXTURE FRESHNESS CHECK OK: %d artifact(s), %d source binding(s), %d skipped (localOnly) [%s]"
              % (len(stamps["artifacts"]), n_src, len(skips), rel))
    return rc


if __name__ == "__main__":
    sys.exit(main())
