#!/usr/bin/env python
"""Shot-sequence assembler: manifest -> captioned frames (PIL) -> MP4 (ffmpeg).

Refuses to run if shot_check fails — the gate is not advisory. Shots with status != 'shipped' are silently
filtered out of the cut (no log line — see assemble()'s generator expression), so an incomplete manifest
still assembles a playable vertical slice from what exists rather than refusing outright. The "SKIP
(localOnly)" line inside the per-shot loop only fires for a 'shipped' shot whose source_dir is absent;
through main() that path is unreachable, since shot_check.validate() already makes an absent source_dir
for a shipped shot a hard error before assemble() is ever called (unconditionally, not just under
--require-complete — see shot_check.py). It exists as defense-in-depth for a caller that invokes
assemble() directly, bypassing main()'s gate.

Slow-mo: a shot whose fps_in is 60 and fps_out 30 plays at 50% by construction — the frames are consumed
1:1 into a 30fps timeline. This is why the foot-IK A/B take and the turn/aim/hand-IK take were rendered at 60 (never slomo in PIE).

Stdlib + PIL. ffmpeg must be on PATH.
"""
import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import shot_check
from fixture_freshness_check import shot_frames

SCRIM_H = 96
PAD = 36


def _font(size):
    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _caption(img, text):
    """Lower-third: dark scrim at 60% behind white text (the sequence's caption style)."""
    w, h = img.size
    scrim = Image.new("RGBA", (w, SCRIM_H), (0, 0, 0, 153))
    img = img.convert("RGBA")
    img.alpha_composite(scrim, (0, h - SCRIM_H))
    d = ImageDraw.Draw(img)
    d.text((PAD, h - SCRIM_H + 28), text, font=_font(34), fill=(255, 255, 255, 255))
    return img.convert("RGB")


def assemble(manifest, root, out_path, workdir):
    shots = sorted((s for s in manifest["shots"] if s["status"] == "shipped"),
                   key=lambda s: s["order"])
    w, h = manifest["resolution"]
    n = 0
    for shot in shots:
        d = root / shot["source_dir"]
        if not d.is_dir():
            print("SKIP (localOnly): %s" % shot["shot_id"])
            continue
        frames = shot_frames(root, shot)
        # `%-5s` never truncated as its width implied (cosmetic, log only). The slice is the only thing
        # that decides the length: a caption longer than 40 characters says so with an ellipsis instead
        # of running the column.
        cap = shot["caption_text"]
        cap = cap if len(cap) <= 40 else cap[:39] + "…"
        print("  %-22s %4d frames  %s" % (shot["shot_id"], len(frames), cap))
        for f in frames:
            img = Image.open(f).convert("RGB")
            if img.size != (w, h):
                img = img.resize((w, h), Image.LANCZOS)
            _caption(img, shot["caption_text"]).save(workdir / ("%06d.png" % n))
            n += 1
    if n == 0:
        raise RuntimeError("no frames assembled — every shipped shot was skipped")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-framerate", str(manifest["fps_out"]),
           "-i", str(workdir / "%06d.png"),
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", str(out_path)]
    subprocess.run(cmd, check=True, capture_output=True)

    # THE ASSEMBLY STAMP. Written only after ffmpeg returns 0, so a stamp on disk is
    # evidence of a COMPLETED assembly and not merely of an attempted one. `shot_check` reads it back
    # and fails if the manifest's pixel-determining fields have moved since — which is the mechanical
    # replacement for "remember to re-assemble after editing a caption". It is deliberately written
    # here, inside assemble(), rather than in main(): a caller that bypasses main()'s gate still gets a
    # stamp describing what it actually built, and an unstamped MP4 is therefore always a real finding.
    stamp = shot_check.stamp_path(out_path)
    stamp.write_text(json.dumps({
        "fingerprint": shot_check.assembly_fingerprint(manifest),
        "frames": n,
        "fps_out": manifest["fps_out"],
        "resolution": list(manifest["resolution"]),
        "assembled_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "_what_this_is": "Binds the MP4 beside it to the manifest state that produced it. Written by "
                         "Scripts/shot_assemble.py; verified by Scripts/shot_check.py. Delete it and "
                         "shot_check reports the artifact as unbound rather than passing it.",
    }, indent=2), encoding="utf-8")
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="Plugins/MotionTerminal/Tests/shots_manifest.json")
    ap.add_argument("--root", default=".")
    # Defaults to the manifest's own `artifact` path rather than repeating the literal here — two
    # independent defaults is how the assembler and the gate would come to disagree about which file
    # is the deliverable while both reported success.
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    root = Path(a.root).resolve()
    manifest = json.loads((root / a.manifest).read_text(encoding="utf-8"))
    out_rel = a.out or manifest.get("artifact", shot_check.DEFAULT_ARTIFACT)

    # A stale stamp beside a stale MP4 would make validate() refuse the very run that fixes it, so the
    # artifact leg is turned OFF here by parameter — not by filtering its messages out of the returned
    # list, which would silently swallow any future error whose text happened to start the same way.
    # This call is the assembler's pre-flight and what it needs to know is whether the MANIFEST is
    # sound; shot_check's own CLI still runs the artifact leg against the stamp written below.
    errs = shot_check.validate(manifest, root, check_artifact=False)
    if errs:
        print("REFUSING TO ASSEMBLE — shot_check failed:")
        for e in errs:
            print("  -", e)
        return 1

    # SINGLE-INSTANCE LOCK.
    #
    # Every run stages into its own tempfile.mkdtemp, so the FRAMES never collide — but `ffmpeg -y` writes
    # one FIXED output path, so two concurrent runs race that single file and the loser can leave a
    # truncated or interleaved MP4 that looks finished. This happened TWICE, from two unrelated causes (a
    # leftover process from an earlier run, and a backgrounded retry after a tool timeout), and the only
    # mitigation was a remembered `Get-CimInstance Win32_Process` habit. A habit is not a mechanism.
    #
    # O_CREAT|O_EXCL is atomic on NTFS, so the second process loses the race deterministically and exits
    # non-zero instead of corrupting the deliverable. The lock is released in a `finally`, and a stale lock
    # (from a killed run) names the file to delete rather than silently blocking forever.
    out_path = root / out_rel
    lock_path = out_path.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        print("REFUSING TO ASSEMBLE — another shot_assemble is already writing %s" % out_path)
        print("  lock: %s" % lock_path)
        print("  If no assembler is running (check Get-Process python), delete that file and re-run.")
        return 3
    os.write(lock_fd, ("pid=%d" % os.getpid()).encode())
    os.close(lock_fd)
    try:
        wd = Path(tempfile.mkdtemp(prefix="shots_"))
        try:
            n = assemble(manifest, root, out_path, wd)
        finally:
            shutil.rmtree(wd, ignore_errors=True)
    finally:
        try:
            lock_path.unlink()
        except OSError:
            pass
    print("ASSEMBLED %d frames -> %s (%.1fs at %dfps)"
          % (n, out_rel, n / manifest["fps_out"], manifest["fps_out"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
