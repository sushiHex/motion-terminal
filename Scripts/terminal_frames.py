#!/usr/bin/env python
"""The verification shot — render 600 full-frame terminal frames from the FOUR real
captured terminal logs and concatenate them into one shot directory.

The verification shot is the only shot in the sequence with no Unreal render behind it: the subject IS
the terminal, so the terminal pane is drawn at full 1920x1080 rather than composited as a PIP. Everything else is
terminal_capture's — this script only decides how many frames each transcript gets and where the
reveal has to be by then.

WHY THE BEATS ARE EXPRESSED AS MARKER SUBSTRINGS, NOT LINE NUMBERS
`terminal_capture.render_terminal_frames` takes `line_beats` as (display_line_index, frame). A
display index is a function of the transcript's line count AND the wrap column, so a hardcoded index
silently points at the wrong line the moment a transcript is re-captured a line longer or the terminal
pane is re-sized. These beats name the LINE TEXT and the index is resolved against the file being
rendered, so a re-capture either lands on the same line or fails loudly (KeyError-style RuntimeError)
instead of cutting to the wrong moment.

REAL-OUTPUT RULE: this script renders files. It never writes one. Every log it reads was teed from a
real run of the command named in its title.

Stdlib + PIL (via terminal_capture).
"""
import shutil
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))

import shot_assemble
import terminal_capture as tc

REPO = Path(__file__).resolve().parents[1]
CAP = REPO / "captures" / "m9-terminal"
OUT = CAP / "verification"
TERM = CAP / "term"

FRAME = (1920, 1080)
COLS = 138

# The terminal pane is rendered SHORT and pasted at the top of a full-size frame. shot_assemble burns a 96px
# caption scrim into the bottom of every shot, and captions go into the pixels — a pane that runs
# the full 1080 puts its last two lines (here: the AssertImportScale reply, i.e. the payoff of the
# whole shot) underneath that scrim, unrecoverable without a re-render. Importing SCRIM_H from
# shot_assemble rather than restating 96 means the reserve cannot drift from the scrim it reserves.
PANEL = (FRAME[0], FRAME[1] - shot_assemble.SCRIM_H - 16)

# (transcript, frames, title, highlights, [(marker substring, frame)], reveal_end)
#
# The two green segments carry the per-rig rows the shot exists to show (green scrolls, per-rig
# rows visible). run_verification.py lists every row, so the reveal lands on
# a roster of named rigs rather than on one summary line under UnrealBuildTool boilerplate. Both green
# panels SCROLL as a result — deliberate: the held window at the end of each is the rows plus the
# count, which is the part that means anything.
SEGMENTS = [
    ("suite_green.txt", 110,
     "MotionTerminal / python Scripts/run_verification.py  (default tier)  ->  suite_green.txt",
     ("Tests: 17  Failed: 0",),
     (("RUN:", 55),),
     0.80),

    ("suite_red.txt", 160,
     "the same command, with the BlendSpace Y<-Speed pin unbound in ABP_MT_Locomotion  ->  suite_red.txt",
     # "FAIL" (not "FAIL:") so the red ROW in the listing lights up as well as the detail block below
     # it. Nothing else in this transcript contains the substring.
     ("FAIL", "Failed: 1", '"Y": "Speed"', "Topology drifted"),
     (("RUN:", 58), ("Tests: 17  Failed: 1", 95)),
     0.80),

    ("suite_green_again.txt", 90,
     "the same command again, after re-binding Y<-Speed  ->  suite_green_again.txt",
     ("Tests: 17  Failed: 0",),
     (("RUN:", 44),),
     0.80),

    ("f16_catchfix.txt", 240,
     "MotionTerminal / import-scale tools, captured in-editor  ->  f16_catchfix.txt",
     ("OK", "boundsHeightUU", "applied 0.01", "ref-pose scale violation",
      "suspectedCause", '"root"'),
     (("'/Game/MT/F16Shot8Pre'", 22),
      ("# manny_declared_metres.fbx", 52),
      ("AnimImportTools.CheckImportScale", 72),
      ("suspectedCause", 148),
      # The fix itself, then the independent re-gate. These two markers replaced the FLAGGED beat and
      # the hand-applied correction the first take had to ship: ImportScaleCheck.cpp now corrects the
      # mis-declared unit itself, so the last beats are the tool's own OK and AssertImportScale's.
      ("'/Game/MT/F16Shot8', 'manny'", 176),
      ("AnimImportTools.AssertImportScale", 208)),
     0.93),
]


def resolve_beats(path, markers):
    """Marker substring -> display-line index, against the file as it will actually be wrapped.

    Markers are matched against the transcript's ORIGINAL lines and then mapped to the LAST display
    line that source line occupies. Matching the wrapped text instead would miss any marker the
    wrap column happens to cut through — and a long tool invocation is exactly the kind of line
    that wraps, so that failure mode is the common case, not the edge case.
    """
    raw = tc._read_output(path).rstrip("\n").split("\n")
    last_display = []
    seen = 0
    for line in raw:
        seen += len(tc._wrap([line], COLS))
        last_display.append(seen - 1)

    beats = []
    for marker, frame in markers:
        hit = next((i for i, text in enumerate(raw) if marker in text), None)
        if hit is None:
            raise RuntimeError("beat marker %r is not in %s - the transcript changed shape; "
                               "re-check the cut points before rendering" % (marker, path.name))
        beats.append((last_display[hit], frame))
    return beats


def main():
    if OUT.is_dir():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    n = 0
    for idx, (name, frames, title, highlight, markers, reveal_end) in enumerate(SEGMENTS):
        src = CAP / name
        seg_dir = TERM / ("seg%d" % idx)
        seg_dir.mkdir(parents=True, exist_ok=True)
        print("\n--- %s  (%d frames) ---" % (name, frames))
        tc.render_terminal_frames(str(src), str(seg_dir), frames, cols=COLS, size=PANEL,
                                  title=title, highlight=highlight, reveal_end=reveal_end,
                                  line_beats=resolve_beats(src, markers))
        pngs = tc.glob_pngs(str(seg_dir))
        if len(pngs) != frames:
            raise RuntimeError("%s rendered %d frames, expected %d" % (name, len(pngs), frames))
        for p in pngs:
            canvas = Image.new("RGB", FRAME, (0, 0, 0))
            panel = Image.open(p).convert("RGBA")
            canvas.paste(panel, (0, 0), panel)
            canvas.save(OUT / ("frame_%04d.png" % n))
            n += 1

    # No total-vs-planned check here on purpose: the per-segment guard above already asserts every
    # segment rendered exactly its planned count, so a total mismatch is unreachable. An assertion
    # that cannot fail reads as coverage it does not provide.
    print("\nSHOT 8: %d frames -> %s" % (n, OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
