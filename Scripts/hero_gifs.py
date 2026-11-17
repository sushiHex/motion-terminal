#!/usr/bin/env python
"""Hero GIFs + a one-frame-per-shot contact sheet, built from the rendered shots in shots_manifest.json.

WHY THIS IS A COMMITTED SCRIPT AND NOT A SHELL ONE-LINER. Composited shots were once produced by a
hand-tuned invocation that existed nowhere in the repo, which meant the authored half of some burned-in
pixels was unrecoverable. The rule that came out of it: if pixels ship, the invocation that made them
ships too. GIFs and contact sheets are the artifacts that can live in git (the assembled MP4 stays
gitignored - it is 50+ MB and regenerable), so this file is their provenance.

REAL-OUTPUT RULE. Every frame here comes out of a real MRQ render, addressed through the SAME manifest
the MP4 is assembled from, and cropped only by frame index. Nothing is composited, retimed, recoloured or
redrawn. The one thing added is the shot's own caption, drawn by importing `shot_assemble._caption`
rather than reimplementing it - so a GIF cannot drift from the MP4's caption styling or text, because
there is exactly one function that draws both.

WHERE THE OUTPUT GOES. `--out DIR` is required: the script names no fixture folder of its own. If the
output is committed, put an `artifact_stamps.json` beside it (its `fixture_dir` naming DIR) and stamp it
with `fixture_freshness_check.py --write`, which then gates it.

Stdlib + PIL. Run from Scripts/ (or anywhere, with --root):
    python Scripts/hero_gifs.py --root . --out research/fixtures/<dir>
"""
import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw

import shot_assemble
import shot_check
# WHICH FRAMES EACH ARTIFACT IS BUILT FROM LIVES IN THE GATE, and is imported back here rather than
# duplicated. If `HERO`, `SHEET_FRAME`, `shot_frames` and the offset/count/step arithmetic lived in this
# file, the check that binds committed artifacts to their footage would need its own copy — free to
# drift from the copy that actually builds the pixels. That is the same reason `_captioned` imports `shot_assemble._caption` instead of reimplementing
# it. The gate is stdlib-only (this module needs PIL), so the dependency only runs one way.
from fixture_freshness_check import HERO, expected_select, select_indices, shot_frames

GIF_W = 480
GIF_FPS = 15
# These GIFs are COMMITTED, so their size is a repo cost, not a rendering preference. At 640 px with
# per-frame palettes the four came to ~31 MB together, which is not a reasonable thing to put in git
# history for a README illustration. One shared 64-colour adaptive palette derived from the middle frame,
# plus a 480 px width, brings the set to ~8.5 MB with no visible banding on this material (near-monochrome
# renders over flat ground). The MP4 remains the full-fidelity artifact; these are thumbnails.
GIF_COLORS = 64
SHEET_COLS = 2
SHEET_TILE_W = 640


def _load(root, manifest_rel):
    manifest = json.loads((root / manifest_rel).read_text(encoding="utf-8"))
    errs = shot_check.validate(manifest, root)
    if errs:
        # Same refusal posture as the assembler: never produce a deliverable from a manifest the gate
        # rejects, or the GIFs become a second, ungated copy of the claims.
        raise SystemExit("REFUSING - shot_check failed:\n  " + "\n  ".join(errs))
    return manifest


def _captioned(path, shot, width):
    img = Image.open(path).convert("RGB")
    img = shot_assemble._caption(img, shot["caption_text"])
    h = int(round(img.height * width / img.width))
    return img.resize((width, h), Image.LANCZOS)


def make_gifs(root, manifest, out_dir):
    by_id = {s["shot_id"]: s for s in manifest["shots"]}
    made = []
    for shot_id, (off, n, step) in HERO.items():
        shot = by_id[shot_id]
        frames = shot_frames(root, shot)
        # Through the gate's own selector, not `frames[off:off + n:step]` written out a second time —
        # so the frames that go INTO a GIF and the frames `fixture_freshness_check` later digests to
        # decide whether that GIF is stale cannot be two different sets.
        window = [frames[i] for i in
                  select_indices(expected_select("hero_gif", shot_id, len(frames)), len(frames))]
        if not window:
            raise SystemExit("%s: window [%d:%d:%d] is empty in %d frames"
                             % (shot_id, off, off + n, step, len(frames)))
        imgs = [_captioned(f, shot, GIF_W) for f in window]
        # One shared palette for the whole clip, taken from the middle frame (the most representative
        # of the action) rather than the first, then every frame quantized to it. Per-frame palettes
        # would be marginally more accurate and several times larger.
        pal = imgs[len(imgs) // 2].convert("P", palette=Image.ADAPTIVE, colors=GIF_COLORS)
        frames = [im.quantize(palette=pal, dither=Image.FLOYDSTEINBERG) for im in imgs]
        out = out_dir / ("%s.gif" % shot_id)
        frames[0].save(out, save_all=True, append_images=frames[1:], optimize=True,
                       duration=int(round(1000.0 / GIF_FPS)), loop=0)
        made.append((out, len(imgs)))
        print("  %-18s %3d frames -> %s (%.1f MB)"
              % (shot_id, len(imgs), out.name, out.stat().st_size / 1e6))
    return made


def make_contact_sheet(root, manifest, out_path):
    """One representative frame per shot, in cut order, captioned. The point is that a reader can see
    the WHOLE shot sequence's claims at a glance and check each against the evidence file, without a video
    player - the same reason every other fixture directory in this repo has a contact sheet."""
    shots = sorted(manifest["shots"], key=lambda s: s["order"])
    tiles = []
    for shot in shots:
        frames = shot_frames(root, shot)
        idx = select_indices(expected_select("contact_sheet", shot["shot_id"], len(frames)),
                             len(frames))[0]
        tiles.append((shot, _captioned(frames[idx], shot, SHEET_TILE_W)))

    tw, th = tiles[0][1].size
    label_h = 30
    cols = SHEET_COLS
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tw, rows * (th + label_h)), (18, 18, 20))
    d = ImageDraw.Draw(sheet)
    font = shot_assemble._font(19)
    for i, (shot, img) in enumerate(tiles):
        x, y = (i % cols) * tw, (i // cols) * (th + label_h)
        label = "%s  -  %s" % (shot["shot_id"], shot["title"])
        # Trim to the tile rather than letting a long title run under the neighbouring tile's label.
        while d.textlength(label, font=font) > tw - 20 and len(label) > 4:
            label = label[:-2] + "…"
        d.text((x + 10, y + 6), label, font=font, fill=(210, 210, 215))
        sheet.paste(img, (x, y + label_h))
    sheet.save(out_path, quality=88)
    print("  contact sheet %dx%d -> %s (%.1f MB)"
          % (sheet.width, sheet.height, out_path.name, out_path.stat().st_size / 1e6))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--manifest", default="Plugins/MotionTerminal/Tests/shots_manifest.json")
    ap.add_argument("--out", required=True,
                    help="directory to write the GIFs and contact sheet into, relative to --root")
    # Committed artifacts make regenerating one that did not change not free: GIF palette quantisation
    # is not bit-reproducible, so a blanket re-run rewrites ~8 MB of git history to fix a 400 KB JPEG.
    # That is not hypothetical — a contact sheet was once found stale (one shot re-rendered 7.5 h after
    # it, during an evidence-file fix) while all four GIFs were current, and re-running everything would
    # have churned the four to correct the one.
    ap.add_argument("--only", choices=("all", "gifs", "sheet"), default="all",
                    help="regenerate only part of the set; default all")
    a = ap.parse_args()

    root = Path(a.root).resolve()
    manifest = _load(root, a.manifest)
    out_dir = root / a.out
    out_dir.mkdir(parents=True, exist_ok=True)

    if a.only in ("all", "gifs"):
        print("hero GIFs:")
        make_gifs(root, manifest, out_dir)
    if a.only in ("all", "sheet"):
        print("contact sheet:")
        make_contact_sheet(root, manifest, out_dir / "contact_sheet.jpg")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
