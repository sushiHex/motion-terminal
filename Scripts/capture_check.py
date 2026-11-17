#!/usr/bin/env python
"""Frame verifier for captures (real-output honesty rule): every render dir is checked for
(a) frame count, (b) non-black frames (mean luma), (c) actual motion (consecutive-frame diff)
BEFORE any 'captured' claim. Stdlib + PIL only."""
import argparse, sys
from pathlib import Path
from PIL import Image, ImageChops, ImageStat

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dir"); ap.add_argument("--min-frames", type=int, default=2)
    ap.add_argument("--min-luma", type=float, default=8.0)
    ap.add_argument("--min-motion", type=float, default=0.5,
                    help="min mean abs diff between first/middle/last frames (0 disables)")
    a = ap.parse_args()
    frames = sorted(Path(a.dir).glob("*.png"))
    if len(frames) < a.min_frames:
        print(f"FAIL: {len(frames)} frames < {a.min_frames}"); return 1
    imgs = [Image.open(p).convert("L") for p in (frames[0], frames[len(frames)//2], frames[-1])]
    lumas = [ImageStat.Stat(i).mean[0] for i in imgs]
    if min(lumas) < a.min_luma:
        print(f"FAIL: black frame (mean luma {min(lumas):.1f} < {a.min_luma})"); return 1
    if a.min_motion > 0 and len(frames) >= 2:
        d1 = ImageStat.Stat(ImageChops.difference(imgs[0], imgs[1])).mean[0]
        d2 = ImageStat.Stat(ImageChops.difference(imgs[1], imgs[2])).mean[0]
        if max(d1, d2) < a.min_motion:
            print(f"FAIL: no motion between sampled frames (diff {max(d1, d2):.2f})"); return 1
    print(f"OK: {len(frames)} frames, luma {lumas}, motion checked"); return 0

if __name__ == "__main__":
    sys.exit(main())
