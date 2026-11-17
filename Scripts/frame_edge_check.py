#!/usr/bin/env python
"""Edge-strip intrusion check for a rendered frame directory — "is anything touching the frame edge?"

WHY THIS FILE EXISTS AND WHY IT HAS TWO CHANNELS.

The four-rig lineup take once rested its in-frame gate on a single measurement: "the rightmost 30 px never exceed
chroma 8". The number was real and reproduced exactly on re-run. The CONCLUSION drawn from it — "neither
edge is ever touched" — was false. Khaimera's motion-blurred axe crosses into the rightmost 30 px, and it
does so against the black sky where it is RGB about (47, 47, 46), i.e. CHROMA 1. The most saturated
subject in the shot has an achromatic extremity, and the gate was measuring the wrong property of it.
That is the same species as the `hand_r` clip-varies probe that was fixed alongside it: a check that cannot
observe the thing it is quoted as proving.

THE BACKGROUND MODEL, STATED SO IT CAN BE CHECKED RATHER THAN TRUSTED. In this shot an edge strip contains
only two things: black sky (luma ~0) above the horizon and a smooth grey floor (luma ~120-180) below it,
joined by a one-pixel anti-aliased seam. That is BIMODAL, and the luma values BETWEEN the two modes are
therefore almost entirely geometry rather than background. So the second channel counts pixels in that gap
band. The script PRINTS both modes it found and the per-frame baseline count, so a reader can see whether
the bimodal assumption actually holds for the footage in front of them instead of taking it on faith.

A per-pixel TEMPORAL median was tried first and rejected, and the failure is worth recording: the camera
dollies through this shot, so the floor's stipple texture slides across every pixel and the median is not
a background estimate at all — it flagged 60 frames of 60 on three edges, i.e. it "detected" the camera.

Neither channel is a tuned pass/fail. The script reports what it measured, including the extreme column or
row any intrusion reached and the frame it happened on, so the sentence written into render_evidence.json
can be the sentence the measurement supports. Each strip's full extent is printed too, because the original
left-strip figure ("luma 99..170") silently excluded the dark sky band and a reader had no way to know.

A blurred WEAPON grazing a frame edge is ordinary cinematography. A rig BODY reaching one is not. This
script does not know the difference — it gives you the numbers and the frame number so you can look.

Usage:
    python Scripts/frame_edge_check.py <frames_dir> [--start 540] [--end 900] [--every 6]
                                       [--strip 30] [--gap-lo 25] [--gap-hi 110]
"""
import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image


def _luma(a):
    return 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]


def _modes(lum, nbins=32):
    """The two most populated luma bins — the sky mode and the floor mode if the assumption holds."""
    hist, edges = np.histogram(lum, bins=nbins, range=(0.0, 256.0))
    top = np.argsort(hist)[::-1][:2]
    return sorted(float((edges[i] + edges[i + 1]) / 2.0) for i in top)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("frames_dir")
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None, help="exclusive")
    ap.add_argument("--every", type=int, default=6)
    ap.add_argument("--strip", type=int, default=30, help="strip thickness in px")
    ap.add_argument("--gap-lo", type=float, default=25.0)
    ap.add_argument("--gap-hi", type=float, default=110.0)
    a = ap.parse_args()

    on_disk = sorted(Path(a.frames_dir).glob("*.png"))
    end = len(on_disk) if a.end is None else a.end
    files = on_disk[a.start:end][::a.every]
    if not files:
        print("ERROR: no frames in range")
        return 2

    stack = np.stack([np.asarray(Image.open(f).convert("RGB"), dtype=np.float32) for f in files])
    n, h, w, _ = stack.shape
    s = a.strip
    strips = {
        "left":   (slice(None), slice(0, s)),
        "right":  (slice(None), slice(w - s, w)),
        "top":    (slice(0, s), slice(None)),
        "bottom": (slice(h - s, h), slice(None)),
    }

    print("frames sampled: %d  (index %d..%d step %d, %d on disk)"
          % (n, a.start, end - 1, a.every, len(on_disk)))
    print("resolution %dx%d, strip %d px, gap band luma %.0f..%.0f"
          % (w, h, s, a.gap_lo, a.gap_hi))
    print()

    for name, (rs, cs) in strips.items():
        sub = stack[:, rs, cs, :]
        chroma = sub.max(axis=-1) - sub.min(axis=-1)
        lum = _luma(sub)
        gap = (lum > a.gap_lo) & (lum < a.gap_hi)
        counts = gap.reshape(n, -1).sum(axis=1)
        base = float(np.median(counts))
        wi = int(counts.argmax())

        r0 = 0 if rs == slice(None) else (rs.start or 0)
        c0 = 0 if cs == slice(None) else (cs.start or 0)
        print("%-7s rows %d..%d  cols %d..%d   (full strip extent, nothing excluded)"
              % (name, r0, (rs.stop or h) - 1 if rs != slice(None) else h - 1,
                 c0, (cs.stop or w) - 1 if cs != slice(None) else w - 1))
        print("        luma modes found      %.0f and %.0f   (bimodal background assumption)"
              % tuple(_modes(lum)))
        print("        chroma                max %5.1f   <- the original ONLY channel; blind to grey blur"
              % chroma.max())
        print("        luma                  range %.1f..%.1f" % (lum.min(), lum.max()))
        print("        gap-band px / frame   median %6.1f   max %6.1f on %s"
              % (base, counts.max(), files[wi].name))
        flagged = counts > max(base * 3.0, 20.0)
        print("        frames > 3x median    %d of %d" % (int(flagged.sum()), n))
        # Reach is reported ONLY for a flagged frame. On an unflagged frame the gap-band pixels ARE the
        # horizon seam, which spans the whole strip in every frame — quoting its reach would manufacture
        # an intrusion out of background.
        if flagged[wi]:
            idx = np.argwhere(gap[wi])
            if name in ("left", "right"):
                col = c0 + (int(idx[:, 1].min()) if name == "left" else int(idx[:, 1].max()))
                print("        worst-frame reach     absolute column %d (frame is 0..%d)" % (col, w - 1))
            else:
                row = r0 + (int(idx[:, 0].min()) if name == "top" else int(idx[:, 0].max()))
                print("        worst-frame reach     absolute row %d (frame is 0..%d)" % (row, h - 1))
        else:
            print("        worst-frame reach     n/a — no frame exceeded the seam baseline")
        print()

    print("HOW TO READ THIS: the gap-band median is the ANTI-ALIASED HORIZON SEAM that crosses every")
    print("side strip in every frame — it is background, not an intrusion. A frame whose count leaps to")
    print("many times that median has real geometry in the strip. Open that frame and say what it was.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
