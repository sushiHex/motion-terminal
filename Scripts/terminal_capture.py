#!/usr/bin/env python
"""Terminal picture-in-picture: renders REAL captured tool output to PNG frames with a
typewriter reveal, then composites those frames as a PIP panel over the Unreal render.

================================================================================================
HONESTY RULE — WHY THIS TAKES A PATH AND NEVER A STRING
================================================================================================
`render_terminal_frames` accepts a *path to a file containing output the tool actually produced*.
It does NOT accept inline text, and it actively refuses anything that looks like inline text
(see _read_output). That restriction is the whole point of the module, not an ergonomic accident:

A terminal panel burned into rendered footage is the single easiest place in this project to ship
a fabrication. Nobody can tell, from the pixels, whether `Speed = 450.000000` came off a live
AnimInstance or was typed into a caption by someone who expected it to say that. Requiring a file
means the plausible-looking string has to exist on disk before it can be rendered, and a file has
provenance anyone can chase: who wrote it, when, and from which run.

The two files this module renders as a PIP for the shot sequence are both written *by the driver, inside the MRQ
render that produced the footage they are composited onto* — see Scripts/pie_bridge_drive.py and
Scripts/pie_montage_layer_drive.py, which build their line lists out of the verbatim return values
of AnimDebugTools calls and never out of literals. So the PIP is not merely "real output", it is
the output of the run you are watching.

THAT PARAGRAPH WAS ONCE FALSE ON DISK FOR SEVEN HOURS, AND SAYING SO IS THE POINT. It was written
as a description of a guarantee and was in fact a description of an intention.
Each driver owned a module-level constant naming a fixed repo path; an uncommitted
driver-equivalence harness exec'd Scripts/pie_bridge_drive.py against a stubbed `unreal` for 60
simulated ticks, the driver reached its dump, and the gameplay-bridge take's transcript became the stub's canned
returns — `IsFalling = 450.000000`, which AnimDebugTools::GetAnimVariable cannot print for an
FBoolProperty. The shipped MP4 was never affected. Nothing in this repo could see it: _read_output
(below) refuses inline text, a missing file and an empty file, and a fabrication is none of those.

Two things now make the paragraph true rather than aspirational, and neither is a comment:
  * A driver no longer knows where its evidence goes. `mt_drive.write_capture_evidence` writes into
    the directory the RENDER armed and raises if nothing armed one, so there is no path left for a
    stub to reach. `mrq_render.render_shot` clears stale transcripts with the stale frames, so a
    render whose driver never dumped leaves NO file and this module refuses it by name.
  * `shot_check` records each designated capture file's sha256 in shots_manifest.json and refuses both
    content that is not those bytes and a file NEWER than the frames it is composited into.
The tool-transcript take's four transcripts are rendered by Scripts/terminal_frames.py rather than composited here;
they are teed from real runs and are covered by the same manifest gate.

WHAT THIS MODULE IS ALLOWED TO DO TO THE TEXT: wrap it at `cols`, scroll it, reveal it over time,
and colour lines matching `highlight`. It never edits, reorders, filters or appends a line. The
`title` argument draws a provenance strip ABOVE the text body precisely so the terminal panel can say where
its content came from without that authored sentence being mistaken for tool output.

AUTHORED LINES IN THE BODY — the one narrow allowance, added for the tool-transcript take.
The original wording of this paragraph read "anything a driver's author wrote lives in the title bar
or in the shot caption, NEVER in the body". The tool-transcript take's driver broke that rule to disclose a limitation,
which made it an undeclared amendment to a shared contract. It is declared here instead, because a
one-line title strip cannot carry the setup a wordless shot needs, and because the rule that actually
protects the artifact is "no fabricated reading", not "no authored character":

A driver MAY write authored lines into the body if ALL of the following hold.
  1. Every such line starts with '#' at column 0. Tool output in this project never does, and neither
     does a '> ' command echo, so the reader can tell them apart without knowing the driver.
  2. No such line states a reading, a verdict, a measurement or a filename that a tool did not
     produce. Where one carries a value, that value must come from the SAME variable the code acted
     on — never a literal restating what the author expects the value to be.
  3. No such line replaces, rewords, summarises or contradicts adjacent tool output. It may say why a
     beat exists; it may not say what a beat found.
Anything that fails one of these belongs in the title strip or the shot caption. A '#' prefix is not
a licence to narrate a result — it is a label saying "a human wrote this line, and the machine's
answer is the one below it".

Stdlib + PIL.
================================================================================================
"""
import argparse
import math
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Terminal palette. Deliberately not pure black: the rendered shots are bright outdoor renders and a
# 0,0,0 panel reads as a hole punched in the frame rather than an overlay.
BG = (18, 20, 26, 235)
BORDER = (86, 96, 112, 255)
TITLE_BG = (30, 34, 42, 245)
TITLE_FG = (150, 160, 176, 255)
FG = (206, 214, 226, 255)
PROMPT_FG = (126, 208, 130, 255)      # the '> ' of an issued command
HILITE_FG = (250, 205, 108, 255)      # lines matching `highlight`
CURSOR = (206, 214, 226, 255)

MONO_CANDIDATES = ("C:/Windows/Fonts/consola.ttf", "consola.ttf", "cour.ttf", "DejaVuSansMono.ttf")

PAD = 14
TITLE_H = 30


def _mono(size):
    for name in MONO_CANDIDATES:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _read_output(output_file):
    """Load the captured output. Refuses inline text — see the module docstring.

    The `\n` check is the load-bearing one: a caller who wants to fabricate a panel will reach for
    a multi-line string literal long before they reach for a 300-character filename, so rejecting
    embedded newlines closes the path of least resistance. A missing file is a hard error rather
    than an empty panel, because an empty panel is a silent failure that still renders 540 frames.
    """
    if isinstance(output_file, str) and "\n" in output_file:
        raise ValueError(
            "render_terminal_frames takes a PATH to a file of real captured tool output, not "
            "inline text. Capture the output to a file first — see the module docstring.")
    p = Path(output_file)
    if not p.is_file():
        raise FileNotFoundError(
            "captured-output file not found: %s — the terminal overlay is only allowed to render output "
            "that was actually produced and written to disk." % p)
    text = p.read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError("captured-output file is empty: %s" % p)
    return text


TAB_STOP = 4


def _wrap(lines, cols):
    """Hard-wrap to `cols`, hanging-indenting continuations so a wrapped node-tree line still reads
    as one entry. Returns (display_line, is_continuation) pairs; no line is ever dropped.

    Tabs are expanded to spaces first. This is NOT a licence to rewrite the text — it is the only
    way to render it faithfully: PIL draws '\\t' as the font's .notdef glyph, so a tab-indented
    transcript (the golden-diff output and every scale report are tab-indented JSON) comes out as a
    run of empty boxes, i.e. the terminal panel displays characters that are not in the file. Turning one
    whitespace character into the whitespace it means is a rendering decision; the alternative was
    shipping tofu. Caught by reading a frame, not by any gate — see the tool-transcript take.
    """
    out = []
    for raw in lines:
        line = raw.rstrip("\r").expandtabs(TAB_STOP)
        if len(line) <= cols:
            out.append((line, False))
            continue
        indent = " " * (len(line) - len(line.lstrip()) + 2)
        first = True
        while line:
            take = cols if first else max(1, cols - len(indent))
            chunk, line = line[:take], line[take:]
            out.append((chunk if first else indent + chunk, not first))
            first = False
    return out


def _reveal_schedule(disp, frames, reveal_end, line_beats):
    """Characters revealed at each frame, as a piecewise-linear curve through `line_beats`.

    line_beats is [(display_line_index, frame), ...] — "the reveal has finished this line by this
    frame". It exists so the terminal panel can be cut to the action: the gameplay-bridge take's `cmc.velocity = (450,0,0)`
    line must land on the frame the pawn breaks into a run, and a uniform typing rate cannot do
    that. Beats are clamped to be monotonic in both axes so a mis-ordered list degrades to a slower
    reveal rather than to text that un-types itself.
    """
    cum, n = [], 0
    for text, _ in disp:
        n += len(text) + 1          # +1 for the newline the cursor sits after
        cum.append(n)
    total = max(1, n)
    last_frame = max(1, int(frames * reveal_end))

    pts = [(0, 0)]
    for idx, frame in sorted(line_beats or [], key=lambda b: b[1]):
        idx = max(0, min(len(cum) - 1, int(idx)))
        c, f = cum[idx], int(frame)
        if c > pts[-1][0] and f > pts[-1][1]:
            pts.append((c, f))
    if total > pts[-1][0] and last_frame > pts[-1][1]:
        pts.append((total, last_frame))

    sched, seg = [], 0
    for f in range(frames):
        while seg < len(pts) - 2 and f >= pts[seg + 1][1]:
            seg += 1
        c0, f0 = pts[seg]
        c1, f1 = pts[seg + 1]
        if f <= f0:
            sched.append(c0)
        elif f >= f1:
            sched.append(c1)
        else:
            sched.append(int(c0 + (c1 - c0) * (f - f0) / float(f1 - f0)))
    # Everything after the final beat stays fully revealed — the tail of the shot is reading time.
    return [min(total, max(0, c)) for c in sched]


def render_terminal_frames(output_file, out_dir, frames, cols=88, size=(760, 420),
                           title=None, highlight=(), reveal_end=0.85, line_beats=None):
    """output_file: a file containing REAL captured tool output. Never inline text.

    Writes `frames` PNGs named term_<n>.png into out_dir, revealing the file's contents with a
    typewriter cursor. Returns the number of frames written.

    highlight: substrings; any display line containing one is drawn in HILITE_FG. This is colour
    only — the text is never altered, reordered or filtered — and exists so a node-weight tree
    dozens of lines long points the eye at the handful of lines the caption is about.
    """
    text = _read_output(output_file)
    disp = _wrap(text.rstrip("\n").split("\n"), cols)

    w, h = size
    # Size the font so `cols` characters exactly fill the body width, then derive the row count
    # from real metrics rather than a guessed ratio — Consolas' advance is 0.55em, but a fallback
    # face is not, and a hardcoded ratio silently clips the right-hand column on that face.
    fs = 8
    while fs < 48:
        adv = _mono(fs + 1).getlength("M" * cols)
        if adv > (w - 2 * PAD):
            break
        fs += 1
    font = _mono(fs)
    line_h = int(math.ceil(fs * 1.34))
    body_top = TITLE_H + PAD if title else PAD
    rows = max(1, (h - body_top - PAD) // line_h)

    # Report the fit rather than letting it be discovered in the pixels. A panel one row too short
    # scrolls the '> command' line off the top at the exact moment the shot holds for reading, and
    # nothing downstream can see that: capture_check counts frames, shot_check reads the manifest,
    # and the reveal still "completes". Sizing is the caller's job, so give the caller the numbers.
    print("terminal panel: font %dpx, %d rows x %d cols, %d display lines -> %s"
          % (fs, rows, cols, len(disp), "fits" if len(disp) <= rows else "SCROLLS"))

    sched = _reveal_schedule(disp, frames, reveal_end, line_beats)
    os.makedirs(out_dir, exist_ok=True)
    for f in glob_pngs(out_dir):
        os.remove(f)

    title_font = _mono(max(9, int(fs * 0.82)))
    for i in range(frames):
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((0, 0, w - 1, h - 1), radius=8, fill=BG, outline=BORDER, width=1)
        if title:
            d.rounded_rectangle((1, 1, w - 2, TITLE_H), radius=8, fill=TITLE_BG)
            d.rectangle((1, TITLE_H - 6, w - 2, TITLE_H), fill=TITLE_BG)
            d.line((1, TITLE_H, w - 2, TITLE_H), fill=BORDER, width=1)
            d.text((PAD, (TITLE_H - title_font.size) // 2 - 1), title, font=title_font, fill=TITLE_FG)

        # Resolve the reveal head to a (line, column) so the terminal panel can scroll with the cursor.
        # `complete` is load-bearing: once the budget covers every line the loop never breaks, and
        # clamping the overshot line index back to the last line would leave cur_col at 0 — which
        # renders the final line BLANK for the whole tail of the shot, i.e. the one line the beat
        # schedule was aimed at landing is the one line that never appears.
        budget = sched[i]
        cur_line, cur_col, complete = 0, 0, True
        for li, (t, _) in enumerate(disp):
            need = len(t) + 1
            if budget >= need:
                budget -= need
                cur_line, cur_col = li + 1, 0
            else:
                cur_line, cur_col, complete = li, budget, False
                break
        if not disp:
            cur_line, cur_col = 0, 0
        elif complete:
            cur_line, cur_col = len(disp) - 1, len(disp[-1][0])

        top = max(0, min(cur_line - rows + 1, max(0, len(disp) - rows)))
        y = body_top
        for li in range(top, min(len(disp), top + rows)):
            t, _cont = disp[li]
            shown = t if li < cur_line else (t[:cur_col] if li == cur_line else "")
            if shown:
                if t.lstrip().startswith(">"):
                    colour = PROMPT_FG
                elif any(hl in t for hl in highlight):
                    colour = HILITE_FG
                else:
                    colour = FG
                d.text((PAD, y), shown, font=font, fill=colour)
            if li == cur_line and i < frames - 1:
                cx = PAD + font.getlength(shown)
                d.rectangle((cx, y + 2, cx + max(3, fs * 0.55), y + line_h - 3), fill=CURSOR)
            y += line_h

        img.save(os.path.join(out_dir, "term_%04d.png" % i))
    return frames


def glob_pngs(d):
    p = Path(d)
    return sorted(str(x) for x in p.glob("*.png")) if p.is_dir() else []


def composite_pip(base_dir, term_dir, out_dir, margin=(48, 132), anchor="br"):
    """Alpha-composite term_dir's panel onto base_dir's Unreal frames, 1:1 by index.

    margin is (from the anchored horizontal edge, from the anchored vertical edge) in px. The
    default bottom margin of 132 clears shot_assemble's 96px caption scrim with room to spare —
    a panel that overlaps the burned-in caption is unreadable and cannot be fixed without a
    re-render, since captions go into the pixels.

    Frame counts must match: a short PIP run silently leaving the last N frames bare is exactly
    the kind of half-finished artifact capture_check's count gate cannot see.
    """
    # Refuse in-place composites BEFORE touching anything. out_dir is wiped below, and the wipe runs
    # against a list snapshotted from disk — so pointing out_dir at base_dir (or term_dir) deletes
    # every input frame and only then fails on the first missing file. That destroys an MRQ render
    # that costs minutes to reproduce, for a typo. Compare resolved paths, not the strings passed in,
    # since 'captures/x' and 'captures/./x' are the same directory.
    rbase, rterm, rout = (Path(p).resolve() for p in (base_dir, term_dir, out_dir))
    if rout == rbase or rout == rterm:
        raise ValueError(
            "out_dir must differ from base_dir and term_dir — compositing in place would delete the "
            "input frames before reading them (out=%s base=%s term=%s)" % (rout, rbase, rterm))

    base = glob_pngs(base_dir)
    term = glob_pngs(term_dir)
    if not base:
        raise RuntimeError("no base frames in %s" % base_dir)
    if len(term) != len(base):
        raise RuntimeError("frame-count mismatch: %d base vs %d terminal frames"
                           % (len(base), len(term)))
    os.makedirs(out_dir, exist_ok=True)
    for f in glob_pngs(out_dir):
        os.remove(f)

    mx, my = margin
    for i, (bp, tp) in enumerate(zip(base, term)):
        img = Image.open(bp).convert("RGBA")
        panel = Image.open(tp).convert("RGBA")
        W, H = img.size
        pw, ph = panel.size
        x = (W - pw - mx) if anchor.endswith("r") else mx
        y = (H - ph - my) if anchor.startswith("b") else my
        img.alpha_composite(panel, (x, y))
        img.convert("RGB").save(os.path.join(out_dir, "frame_%04d.png" % i))
    return len(base)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("output_file", help="path to a file of REAL captured tool output")
    ap.add_argument("--base-dir", required=True, help="Unreal render frames")
    ap.add_argument("--term-dir", required=True, help="scratch dir for the terminal frames")
    ap.add_argument("--out-dir", required=True, help="composited frames land here")
    ap.add_argument("--frames", type=int, required=True)
    ap.add_argument("--cols", type=int, default=88)
    ap.add_argument("--size", default="820x500", help="WxH of the terminal overlay")
    ap.add_argument("--title", default=None)
    ap.add_argument("--highlight", action="append", default=[])
    ap.add_argument("--reveal-end", type=float, default=0.85)
    ap.add_argument("--beat", action="append", default=[],
                    help="LINE:FRAME — the reveal finishes display line LINE by frame FRAME")
    a = ap.parse_args()

    w, h = (int(x) for x in a.size.lower().split("x"))
    beats = [tuple(int(v) for v in b.split(":")) for b in a.beat]
    n = render_terminal_frames(a.output_file, a.term_dir, a.frames, cols=a.cols, size=(w, h),
                               title=a.title, highlight=a.highlight,
                               reveal_end=a.reveal_end, line_beats=beats)
    print("terminal frames: %d -> %s" % (n, a.term_dir))
    m = composite_pip(a.base_dir, a.term_dir, a.out_dir)
    print("composited: %d -> %s" % (m, a.out_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
