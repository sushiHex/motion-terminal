#!/usr/bin/env python
"""Unit tests for Scripts/terminal_capture.py — the text-layout half.

WHY THIS FILE EXISTS. `_wrap` is the last thing that touches transcript text before it becomes
pixels, and nothing downstream can see what it produced: `capture_check.py` counts frames and
measures luma and motion, `shot_check.py` reads a manifest. A panel can render every character as an
empty .notdef box and pass every gate in this repo — that is exactly what happened to the tool-transcript take's
golden-diff segment, and it was caught by a human opening frame_0265.png. These tests are the only
affordable mitigation for a defect class the gate stack is structurally blind to.

They also pin `_wrap`'s signature and return shape for `terminal_frames.resolve_beats`, which calls
it directly to reproduce the renderer's exact wrap. A silent change there would mis-resolve every
beat in the tool-transcript take with nothing to catch it.

Runs headless, no PIL image work, no Unreal:
    python -m pytest Scripts/test_terminal_capture.py -q
"""
import pytest

import terminal_capture as tc


# --- tabs: the tool-transcript take's defect -----------------------------------------------------

def test_tab_becomes_spaces_not_a_notdef_glyph():
    """PIL draws '\\t' as the font's .notdef box, so a tab that survives _wrap displays a character
    that is not in the file. Every scale report and every golden diff in this repo is tab-indented
    JSON."""
    (text, _cont), = tc._wrap(["a\tb"], 80)
    assert text == "a   b"
    assert "\t" not in text


def test_tabs_expand_to_the_declared_tab_stop():
    """Pinned against TAB_STOP rather than the literal 4: the point is that the module's constant is
    the one actually applied, not that it happens to be 4 today."""
    (text, _cont), = tc._wrap(["\tx"], 80)
    assert text == " " * tc.TAB_STOP + "x"


def test_leading_tabs_expand_before_the_width_test():
    """Order matters. Expanding AFTER the `len(line) <= cols` check would let a 3-character line of
    tabs slip through unexpanded because it 'fits'."""
    line = "\t" * 20 + "value"          # 5 chars raw, 85 columns once expanded
    disp = tc._wrap([line], 40)
    assert len(disp) > 1                # it wrapped, i.e. the width test saw the expanded length
    assert not any("\t" in t for t, _ in disp)


def test_nested_json_indent_survives_as_alignment():
    """The real payload: two tab-indented JSON levels must still read as two levels."""
    (one, _), (two, _) = tc._wrap(['\t"boneLengths": {', '\t\t"pelvis": 95.9'], 80)
    assert one.startswith(" " * tc.TAB_STOP + '"')
    assert two.startswith(" " * (2 * tc.TAB_STOP) + '"')


# --- the rest of _wrap's contract, which resolve_beats depends on --------------------------------

def test_short_lines_pass_through_untouched_and_are_not_continuations():
    assert tc._wrap(["Tests: 17  Failed: 0"], 80) == [("Tests: 17  Failed: 0", False)]


def test_no_line_is_ever_dropped_and_the_characters_are_preserved():
    long_line = "x" * 250
    disp = tc._wrap([long_line], 60)
    assert len(disp) > 1
    assert disp[0][1] is False and all(cont for _t, cont in disp[1:])
    # Continuations carry a hanging indent, so compare after stripping it back off.
    joined = disp[0][0] + "".join(t.lstrip() for t, _ in disp[1:])
    assert joined == long_line


def test_wrapped_chunks_never_exceed_the_column_budget():
    disp = tc._wrap(["> AnimImportTools.FixImportScale(" + "p" * 300 + ")"], 88)
    assert all(len(t) <= 88 for t, _ in disp)


def test_continuations_hang_indent_under_the_original_indent():
    disp = tc._wrap(["    " + "y" * 200], 40)
    assert disp[1][0].startswith(" " * 6)   # original 4 + the module's 2-space hang


def test_carriage_returns_are_stripped():
    """Transcripts are teed from a Windows subprocess; a surviving \\r renders as another .notdef."""
    (text, _), = tc._wrap(["done\r"], 80)
    assert text == "done"


def test_empty_input_and_empty_lines():
    assert tc._wrap([], 80) == []
    assert tc._wrap([""], 80) == [("", False)]


# --- the inline-text refusal, which is the module's reason for existing ----------------------------------

def test_inline_text_is_refused():
    with pytest.raises(ValueError, match="PATH"):
        tc._read_output("FLAGGED: something\nOK: applied 0.01\n")


def test_a_missing_file_is_a_hard_error_not_an_empty_panel(tmp_path):
    with pytest.raises(FileNotFoundError):
        tc._read_output(str(tmp_path / "never_captured.txt"))


def test_an_empty_capture_is_refused(tmp_path):
    p = tmp_path / "empty.txt"
    p.write_text("   \n\n", encoding="utf-8")
    with pytest.raises(ValueError):
        tc._read_output(str(p))


def test_a_real_captured_file_is_returned_verbatim(tmp_path):
    p = tmp_path / "captured.txt"
    body = "> AnimImportTools.AssertImportScale('/Game/MT/X', 'manny')\nOK\n"
    p.write_text(body, encoding="utf-8")
    assert tc._read_output(str(p)) == body
