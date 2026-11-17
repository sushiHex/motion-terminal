"""The verification shot's import-scale catch->fix exchange, captured as it happens (in-editor).

Run in the editor through `Scripts/editor_ops.py` (`ops.run_file`) against a live editor. Writes
captures/m9-terminal/f16_catchfix.txt.

================================================================================================
REAL-OUTPUT RULE — HOW THIS FILE STAYS A CAPTURE AND NOT A SCRIPT OF A CAPTURE
================================================================================================
Every line in the transcript is produced one of exactly three ways:

  * a '> ' command line, formatted from the SAME function name and argument tuple that are then
    passed to the invocation (see `call()` — the echo and the call share one name and one tuple, so
    the echo cannot drift from what actually ran);
  * a verbatim line of `str(return_value)` from that call; or
  * an authored '#' line, under the three conditions terminal_capture's module docstring declares
    (AUTHORED LINES IN THE BODY). There are FIVE, in two banners: the two fixed lines of
    `AUTHORED_PREFLIGHT`, the one fixed line of `AUTHORED_MISUNIT_HEAD`, and the one `#   <name>
    <was> -> <now>` line emitted per entry of `misunit_binary_fbx`'s return tuple (two entries:
    UnitScaleFactor and OriginalUnitScaleFactor) — 2 + 1 + 2. Every fixed line lives in a constant
    precisely so this count can be checked against the code — an earlier revision of this paragraph
    claimed "two" while the file emitted twelve.

Nothing else is written. There is no branch that prints a reading, a verdict, or a number that a
tool did not return. The two '#' lines that carry values (`UnitScaleFactor 1 -> 100`) print what
`misunit_binary_fbx` actually read out of and wrote into the file — the same tuple it returns, not
literals restating what the patch was supposed to do.

The transcript is NOT cropped, summarised or reflowed — CheckImportScale returns the whole
89-bone fingerprint and the whole thing goes in the file. The verification shot crops by frame range instead.

And nothing is written AT ALL unless every beat lands: the file is opened only after the last
assertion in `main()` passes. A run that fails leaves no transcript to shoot around.

================================================================================================
WHAT MECHANISM ACTUALLY PRODUCES SURVIVING DRIFT IN UE 5.8 (determined empirically, see below)
================================================================================================
UE 5.8's Interchange normalises FBX scene units to centimetres — on the FBX SDK backend, which is
the only one that runs by default (bUseUfbxParser defaults false; see
research/interchange-unit-scale-root-bone.md §0, which corrects the earlier "both backends"
wording). The naive reading of that is "you cannot mis-unit an FBX, the importer will just fix it" —
which is backwards. The normalisation is precisely what manufactures the drift: take a
self-consistent centimetre export (a 180 uu mannequin whose numbers are already centimetres),
rewrite ONLY the header's UnitScaleFactor to declare metres, and the importer dutifully multiplies
by 100 to "convert metres to centimetres". The geometry was never in metres. The result is an
18000 uu mannequin.

That is import-scale case 1a in its natural habitat: a file whose declared unit disagrees with its geometry,
which is exactly what Maya produces (Maya stores centimetres internally regardless of the display
unit the artist set). It is also why the fix half measures the factor from the gate instead of
reading it from the header — the header is the thing that is lying.

The other candidate mechanism, baking scale onto nodes (Lcl Scaling), is deliberately NOT usable
here: FixImportScale FLAGs any non-identity node scale pre-import and refuses to import at all,
because that is a Maya-side craft decision and "fixing" it rewrites artist intent.

================================================================================================
WHERE THE FACTOR LANDS, AND WHY THE FIX HALF CAN CANCEL IT
================================================================================================
UE 5.8 does not bake the unit conversion into bone translations or vertices. It lands the whole
factor at or above the root joint — Interchange substitutes the composed GLOBAL transform for bone 0
and leaves every other bone's local transform alone — so the drift shows up as every boneLength
UNCHANGED, boundsHeightUU multiplied by 100, and `refPoseScaleViolations: ["root"]`.

On its first run this transcript ended FLAGGED: the gate read that lone root violation
as import-scale detection #3 (a rig-intentional scale) and refused to auto-correct, which left the fix lane
unreachable for the exact failure it was built for. `import_offset_uniform_scale` travels the
identical route, so the reciprocal cancels the mis-declared unit exactly — the correction the tool
declined to apply was empirically the right one, and it was applied by hand in that first take.

ImportScaleCheck.cpp now makes that call itself, on one corroborated condition: the MEASURED factor
must equal the file's own DECLARED centimetres-per-unit. That is what separates the importer's
conversion from an artist's baked root scale, which produces the identical fingerprint. A file
declaring centimetres is never rescaled by the importer, so a root scale on one of those still
FLAGs. See MotionTerminal::IsImporterInducedUnitConversion and
research/interchange-unit-scale-root-bone.md.
"""
import importlib
import os
import struct
import sys

import unreal

# The shared driver scaffolding — here for ONE thing: `write_capture_evidence`, the single door
# through which any driver in this project may write a file that a shipped frame is composited from.
# Resolved off the .uproject, not off `__file__`, because `editor.run_python` exec's this file.
_MT_SCRIPTS = os.path.join(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()),
                           "Scripts")
if _MT_SCRIPTS not in sys.path:
    sys.path.insert(0, _MT_SCRIPTS)
_mt = importlib.reload(importlib.import_module("mt_drive"))

MESH = "/Game/Characters/Mannequins/Meshes/SKM_Manny_Simple"
RIG = "manny"
# NOT /Game/MT/ImportScaleTest: the automation row's flag-before-import guard scans every LOADED package
# under that prefix, process-wide, so importing there would corrupt its state.
DEST_PREFLIGHT = "/Game/MT/F16Shot8Pre"
DEST_BAD = "/Game/MT/F16Shot8Bad"
DEST_FIX = "/Game/MT/F16Shot8"

DESTS = (DEST_PREFLIGHT, DEST_BAD, DEST_FIX)

REPO = os.path.abspath(unreal.Paths.project_dir()).replace("\\", "/")
WORK = REPO + "/Saved/MotionTerminalShot8"

# The verification shot's evidence. This driver is NOT an MRQ shot — it runs through `editor.run_python` against a
# live editor, so no render arms it and it must arm itself.
#
# WHICH MEANS THE ARM RECORD IS NOT A DEFENCE FOR THIS ONE FILE, and an earlier wording here claimed
# otherwise: that the liveness probe was "enough to stop the stub harness
# that overwrote an evidence file". It is not: every driver's loader resolves `Scripts/` off
# `unreal.Paths.project_dir()`, so a stub that cannot answer with the real checkout cannot exec a
# driver at all — the probe's one condition is one any harness reaching this line has already met, and
# an 8-line stub was reproduced arming this exact directory. The probe catches a
# misconfigured `out_dir`, nothing more.
#
# What actually protects the verification shot's evidence is `shot_check`'s evidence gate: this file's sha256 is
# recorded in shots_manifest.json and a file newer than the frames it was rendered into is refused. A
# re-run of this driver therefore does not silently replace the verification shot's evidence — it turns
# `shot_check` RED until someone re-renders the shot or re-records the hash on purpose. The two MRQ drivers get
# the structural arm-record defence on top of that; this one does not, and that asymmetry is the
# residual, recorded rather than papered over.
OUT_DIR = REPO + "/captures/m9-terminal"
OUT_NAME = "f16_catchfix.txt"
OUT = OUT_DIR + "/" + OUT_NAME

# The FIXED authored lines this driver may put in the transcript body, in one place, so the count in
# the docstring above can be checked against the code. terminal_capture's module contract permits
# '#'-prefixed authored lines that state why a beat exists and never what a beat found. The only
# other authored lines are the per-property "#   <name> <was> -> <now>" lines in main(), which are
# not constants BECAUSE they carry values — and those values come from misunit_binary_fbx's return
# tuple (what it actually read and wrote), never from a literal restating the intent.
AUTHORED_PREFLIGHT = (
    "# preflight: the clean export must pass BEFORE anything is mis-unit'd — a FLAGGED baseline here",
    "# would mean the exporter itself wrote node scale, and the whole demonstration would be void.",
)
AUTHORED_MISUNIT_HEAD = "# manny_declared_metres.fbx: the same file, header rewritten to declare metres."

# The MCP-facing UFUNCTIONs are AICallable, not BlueprintCallable, so they are not bound as python
# attributes; call_method on the class default object reaches the same shipped UFUNCTION.
TOOLS = unreal.AnimImportTools.get_default_object()

_lines = []


def emit(text=""):
    for line in str(text).split("\n"):
        _lines.append(line.rstrip("\r"))
        unreal.log("FBXCATCHFIX| " + line.rstrip("\r"))


def call(func_name, *args):
    """Echo the call, run it, echo the return verbatim. The echoed name and arguments ARE the ones
    handed to call_method — there is no second copy to fall out of date."""
    emit("> AnimImportTools.%s(%s)" % (func_name, ", ".join(repr(a) for a in args)))
    result = TOOLS.call_method(func_name, tuple(args))
    emit(result)
    emit()
    return result


# --- FBX plumbing -------------------------------------------------------------------------------

def export_fbx(asset_path, out_path):
    task = unreal.AssetExportTask()
    task.set_editor_property("object", unreal.load_asset(asset_path))
    task.set_editor_property("filename", out_path)
    task.set_editor_property("automated", True)
    task.set_editor_property("prompt", False)
    task.set_editor_property("replace_identical", True)
    opts = unreal.FbxExportOption()
    opts.set_editor_property("ascii", False)
    opts.set_editor_property("level_of_detail", False)
    opts.set_editor_property("collision", False)
    task.set_editor_property("options", opts)
    ok = unreal.Exporter.run_asset_export_task(task)
    return bool(ok) and os.path.isfile(out_path)


def misunit_binary_fbx(src, dst, declared_cm_per_unit):
    """Rewrite ONLY the two GlobalSettings unit properties of a binary FBX. Geometry, skeleton and
    every node transform are copied byte-for-byte — the file's numbers stay centimetres while its
    header claims otherwise, which is the mis-unit the import-scale guard exists to catch.

    Binary FBX property layout for a P70 entry:
        S"UnitScaleFactor" S"double" S"Number" S"" D<8-byte little-endian double>
    Each S is [4-byte length][bytes]; D is the ascii byte 'D' then the double.
    """
    data = bytearray(open(src, "rb").read())
    patched = []
    for name in (b"UnitScaleFactor", b"OriginalUnitScaleFactor"):
        key = struct.pack("<I", len(name)) + name
        at = data.find(key)
        if at < 0:
            raise RuntimeError("no %s property found in %s" % (name.decode(), src))
        cur = at + len(key)
        for tag in (b"double", b"Number", b""):
            expect = b"S" + struct.pack("<I", len(tag)) + tag
            if bytes(data[cur:cur + len(expect)]) != expect:
                raise RuntimeError("unexpected FBX property layout after %s" % name.decode())
            cur += len(expect)
        if data[cur:cur + 1] != b"D":
            raise RuntimeError("expected a double value for %s" % name.decode())
        was = struct.unpack("<d", bytes(data[cur + 1:cur + 9]))[0]
        data[cur + 1:cur + 9] = struct.pack("<d", float(declared_cm_per_unit))
        patched.append((name.decode(), was, float(declared_cm_per_unit)))
    open(dst, "wb").write(bytes(data))
    return patched


def import_plain(fbx_path, dest, offset):
    """A vanilla Interchange import at `offset` — the import an animator would do without the
    import-scale guard in the loop. Returns the imported SkeletalMesh's object path, and RAISES if there isn't one.

    Raising rather than returning None is the point: the old version emitted the literal string
    "no SkeletalMesh imported" into the transcript and carried on, so a failed import produced a
    plausible-looking capture instead of stopping. Every other error path in this file raises."""
    pipeline = unreal.InterchangeGenericAssetsPipeline()
    pipeline.set_editor_property("import_offset_uniform_scale", float(offset))
    override = unreal.InterchangePipelineStackOverride()
    override.add_pipeline(pipeline)
    task = unreal.AssetImportTask()
    task.set_editor_property("filename", fbx_path)
    task.set_editor_property("destination_path", dest)
    task.set_editor_property("automated", True)
    task.set_editor_property("replace_existing", True)
    task.set_editor_property("save", False)
    task.set_editor_property("options", override)
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    for obj in task.get_objects():
        if isinstance(obj, unreal.SkeletalMesh):
            return obj.get_path_name().split(".")[0]
    raise RuntimeError("importing %s into %s at offset %s produced no SkeletalMesh"
                       % (fbx_path, dest, offset))


# --- the exchange -------------------------------------------------------------------------------

def cleanup_destinations():
    """Delete the /Game/MT/F16Shot8* trees this run created.

    Nothing reached disk (every import runs with save=False), but the packages stay in the asset
    registry for the life of the editor session, so a second run without this inherits them. Called
    from a finally, because a run that failed halfway has left MORE behind, not less."""
    for dest in DESTS:
        try:
            if unreal.EditorAssetLibrary.does_directory_exist(dest):
                unreal.EditorAssetLibrary.delete_directory(dest)
        except Exception as exc:                                  # noqa: BLE001 - best effort only
            unreal.log_warning("FBXCATCHFIX| could not delete %s: %s" % (dest, exc))


def expect_ok(reply, what):
    """A beat that did not land must stop the run, not become a frame.

    The real-output rule that governs this shot is 'every pixel is real captured output'. Its dangerous
    corollary is that a real capture of the WRONG outcome still looks like a finished shot. The
    transcript is written after these, so a failure here produces no file at all — which is the
    honest outcome: report it, do not shoot around it."""
    if not str(reply).startswith("OK"):
        raise RuntimeError("%s did not return OK — nothing was captured. Got:\n%s" % (what, reply))


def main():
    for d in (WORK, os.path.dirname(OUT)):
        if not os.path.isdir(d):
            os.makedirs(d)

    clean = WORK + "/manny_clean.fbx"
    misunit = WORK + "/manny_declared_metres.fbx"

    if not export_fbx(MESH, clean):
        raise RuntimeError("FBX export of %s failed" % MESH)
    patched = misunit_binary_fbx(clean, misunit, 100.0)

    for line in AUTHORED_PREFLIGHT:
        emit(line)
    emit()
    expect_ok(call("FixImportScale", clean, DEST_PREFLIGHT, RIG), "the preflight FixImportScale")

    emit(AUTHORED_MISUNIT_HEAD)
    for name, was, now in patched:
        emit("#   %s %g -> %g" % (name, was, now))
    emit()

    # Beat 1 — the break, imported the way an animator would, with the import-scale guard nowhere in the loop.
    bad = import_plain(misunit, DEST_BAD, 1.0)
    emit("> import '%s' -> '%s' at import_offset_uniform_scale=1.0"
         % (misunit.rsplit("/", 1)[-1], DEST_BAD))
    emit(bad)
    emit()

    # Beat 2 — the catch: the full fingerprint, with the root violation and the diagnosis in it.
    call("CheckImportScale", bad)

    # Beat 3 — the fix. Interchange names the imported asset after the FBX and puts it directly under
    # the destination, which is how DEST_BAD resolved above; deriving the corrected mesh's path the
    # same way keeps the AssertImportScale argument honest. If the derivation is ever wrong,
    # AssertImportScale returns ERROR and expect_ok stops the run — it cannot quietly gate nothing.
    expect_ok(call("FixImportScale", misunit, DEST_FIX, RIG), "FixImportScale on the mis-unit'd FBX")
    fixed = "%s/%s" % (DEST_FIX, os.path.splitext(os.path.basename(misunit))[0])

    # Beat 4 — the payoff, re-measured rather than asserted: the per-rig gate, run again from scratch
    # against the asset the tool just wrote.
    expect_ok(call("AssertImportScale", fixed, RIG), "the post-fix AssertImportScale")

    # Armed HERE, after the last assertion in main() has passed — so the door is open for exactly the
    # one write this run is entitled to make, and a run that failed a beat never opens it at all.
    _mt.arm_capture_evidence(OUT_DIR, "Scripts/fbx_unit_catchfix_drive.py")
    try:
        path = _mt.write_capture_evidence(OUT_NAME, "\n".join(_lines) + "\n")
    finally:
        _mt.disarm_capture_evidence()
    unreal.log("FBXCATCHFIX| WROTE %s (%d lines)" % (path, len(_lines)))
    print("WROTE %s (%d lines)" % (path, len(_lines)))


try:
    main()
finally:
    cleanup_destinations()
