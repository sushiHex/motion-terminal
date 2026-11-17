#!/usr/bin/env python
"""Shot-sequence manifest gate. Validates the committed shot manifest against what is actually on disk and,
for every caption that quotes a number, against the committed evidence that number came from.

WHY THE CITATION LAYER EXISTS. A caption quoting a measured figure is an assertion. This project has
already shipped two contradictory ones: an army-stage Take-4 still disagreed with its own folder-mate chart,
and the entire budget sweep was standing-crowd data presented as driven for two weeks. Both were prose
drifting away from evidence with nothing mechanically checking. Citations bind claim -> evidence key, so
a re-measurement turns every stale caption RED instead of shipping confidently wrong.

THE CAPTION IS THE ARTIFACT, NOT THE CLAIM FIELD. A citation binds cite["claim"] to
evidence; `shot_assemble` burns cite-free `caption_text` into the pixels. Before this check
nothing connected the two, so the claim could be checked to death while the caption said something
else — and nothing scanned `caption_text` for numbers no citation covered. `_check_caption_binding`
closes both: every claim must appear verbatim in its caption, and every number in a caption must be
covered by one of that shot's claims.

Citations name an EXACT dotted key and never fuzzy-match on value: army_config.json carries both
budget.measured_n46_driven_gt_ms_avg (3.6, current) and budget.measured_n46_standing_gt_ms (3.33,
superseded-but-retained), so a value-search would happily resolve to the wrong sibling.

A shot marked "shipped" is an assertion that its footage exists — unlike this repo's other localOnly-SKIP
gates (ImportScale/ClipVaries/pie_rigs), which SKIP an *explicitly flagged* row when its asset is absent,
"shipped" here has no such per-row opt-in, so an absent source_dir for a shipped shot is always an error,
with or without --require-complete. Shots without footage belong to "missing" status instead (which this
gate always tolerates, since the dir check only runs for status == "shipped"); --require-complete additionally
forces every shot to be "shipped" for a shipping cut, where "missing"/"placeholder" also become failures.

WHY duration_s IS DERIVED, NOT DECLARED. duration_s started as prose intent authored from the prose plan's
target timings, never reconciled against frame_range / fps_out — which is what actually determines the
assembled clip's length. That let a manifest carry a runtime nothing checked against the artifact (the
same failure class the citation layer exists to catch, just for timing instead of a measured figure). For
a "shipped" shot, (hi - lo) / fps_out IS the output length; duration_s must agree with it within
DURATION_TOL or the check fails. "missing" shots have no frames to derive from, so this only runs for
status == "shipped".

THE DESIGNATED CAPTURE FILES ARE BOUND TOO. Two shots burn a terminal panel
into their frames, and that panel's content comes from a file on disk that `terminal_capture.py`
renders. Nothing in this gate ever read one. `terminal_capture._read_output` refuses inline text, a
missing file and an empty file — it cannot refuse a file that is present, non-empty and fabricated,
which is exactly what the gameplay-bridge take's `bridge_output.txt` became when a scratch equivalence harness ran the
driver against a stubbed `unreal` and its fixed output path caught the stub's canned returns
(`IsFalling = 450.000000`, seven hours after the frames it backs). A shot may now declare
`evidence_files`, each with the sha256 of the bytes its frames were rendered from, and this gate
refuses two things a checksum genuinely can refuse: content that is not those bytes, and a file
written AFTER the frames it is composited into. See `_check_evidence`.

THE ASSEMBLED MP4 IS NOW BOUND TO THE MANIFEST. Every other check here reads JSON, so a
caption edited without a re-assemble left the shipped pixels contradicting the manifest with the whole
gate green, and the only compensating control was procedural. `shot_assemble` now stamps the artifact with a fingerprint of the fields that determine the
pixels, and `_check_artifact` refuses a mismatch. See that function for what a checksum can and cannot
honestly promise here.

Stdlib only. Run:
    python Scripts/shot_check.py [--manifest PATH] [--require-complete] [--root PATH]
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

SCHEMA_VERSION = 1
DEFAULT_ARTIFACT = "captures/shots/shots.mp4"
STAMP_SUFFIX = ".assembly.json"
VALID_STATUS = ("shipped", "placeholder", "missing")
REQUIRED_FIELDS = ("shot_id", "title", "order", "source_dir", "frame_range", "fps_in",
                   "duration_s", "caption_text", "caption_cites", "status", "source_take",
                   # REQUIRED, not defaulted. `evidence_files` is optional
                   # and correctly so — eight of ten shots have no designated capture file — but that
                   # left the property that actually matters unchecked: a shot whose frames carry a
                   # composited terminal panel MUST declare the file that panel was rendered from. A
                   # future panel shot that declares nothing would get no gate and no red, which is
                   # the fabricated-transcript exposure (see the module docstring) re-opened one shot
                   # along. A default of False would reproduce that
                   # exactly, so the field is mandatory and an author has to answer the question.
                   "has_terminal_panel")
NUM_RE = re.compile(r"-?\d+\.?\d*")
TOL = 0.051   # claims are written to 1dp; 0.051 accepts 3.6 vs 3.60 but rejects 3.6 vs 3.7
DURATION_TOL = 0.05   # frame_range / fps_out is exact arithmetic; 0.05s only absorbs float rounding
EVIDENCE_FIELDS = ("path", "sha256", "written_by")
CITE_FIELDS = ("claim", "source", "keys")


def _dotted(obj, key):
    """Resolve 'a.b.c'. Raises KeyError naming the full path if any segment is absent."""
    cur = obj
    for seg in key.split("."):
        if not isinstance(cur, dict) or seg not in cur:
            raise KeyError(key)
        cur = cur[seg]
    return cur


def _frames_on_disk(d):
    return sorted(p for p in d.glob("*.png"))


def _derived_duration_s(shot, fps_out):
    """(hi - lo) / fps_out — the actual output length of a shipped shot. Caller must have already
    confirmed frame_range is a well-formed [lo, hi] pair; malformed ranges are reported separately
    in validate() and never reach here."""
    lo, hi = shot["frame_range"]
    return (hi - lo) / float(fps_out)


def shipped_total_s(manifest):
    """Total assembled length of all 'shipped' shots, derived from frame_range/fps_out — the same
    per-shot figure validate() gates the ≤180s budget against. Callers (e.g. main()'s summary line)
    should use this instead of summing declared duration_s, so the printed number is always the
    number that was actually enforced, not an independent recomputation from the looser, unchecked
    source. Assumes the manifest already passed validate() (frame_range well-formed for every
    shipped shot) — call after checking errs == []."""
    fps_out = float(manifest.get("fps_out", 30))
    return sum(_derived_duration_s(s, fps_out) for s in manifest.get("shots", [])
               if s.get("status") == "shipped")


# ============================ BINDING THE PIXELS TO THE MANIFEST ============================
#
# Every gate in this file reads JSON. None of them can see the MP4, and the MP4 is the deliverable.
# Edit a caption and skip the re-assemble and the shipped frames contradict the manifest with every
# check green — and the control was PROCEDURAL ONLY
# ("remember to re-assemble"), which is the same species as the naming convention that guarded the
# namespace merge. This makes it mechanical.
#
# It still cannot see pixels, and that is not a fudge — it is what a checksum can honestly promise.
# What it CAN promise is that the file on disk was produced by an assembler run over THIS manifest
# state. The fingerprint covers exactly the fields `shot_assemble.assemble()` consumes: output
# resolution, fps_out, and per shipped shot in render order its id, order, source_dir, frame_range and
# caption_text. Change any of those and the MP4 is stale by construction. Change anything else
# (duration_s, a citation, a _why_ note) and the pixels are provably identical, so forcing a 4440-frame
# re-assemble for it would just teach people to bypass the gate.
#
# TWO LATER ADDITIONS.
#   * `artifact` is now covered. Hashing the path means repointing it at a DIFFERENT FILE
#     THAT EXISTS invalidates the stamp instead of quietly validating the wrong deliverable. It does
#     NOT, on its own, catch repointing at a path where nothing sits: `_check_artifact` returns before
#     the fingerprint is ever computed in that case, which the first version of this comment claimed
#     otherwise and was wrong about. That half is closed in `_check_artifact`
#     itself, by a condition — "does this checkout have shipped footage?" — rather than here.
#   * `evidence_files` are covered. The transcript IS pixel-determining for the two panel shots: change
#     it and the composited frames must be rebuilt, so the MP4 is stale by exactly the same argument
#     that covers caption_text. Hashing the declared sha256 (not the file) keeps this a pure function
#     of the manifest, which is what lets `shot_assemble` stamp it without reading captures/.
def assembly_fingerprint(manifest):
    """sha256 over the pixel-determining projection of the manifest."""
    shots = sorted((s for s in manifest.get("shots", []) if s.get("status") == "shipped"),
                   key=lambda s: s.get("order", 0))
    payload = {
        "resolution": list(manifest.get("resolution", [])),
        "fps_out": manifest.get("fps_out"),
        "artifact": manifest.get("artifact", DEFAULT_ARTIFACT),
        "shots": [[s.get("shot_id"), s.get("order"), s.get("source_dir"),
                   list(s.get("frame_range", [])), s.get("caption_text"),
                   [[e.get("path"), e.get("sha256")] for e in (s.get("evidence_files") or [])]]
                  for s in shots],
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def stamp_path(artifact):
    return Path(str(artifact) + STAMP_SUFFIX)


def _check_artifact(manifest, root):
    """Errors if the assembled MP4 on disk was not produced from this manifest state.

    EXISTENCE-GATED, on purpose. A clean checkout has no captures/ at all, and an absent artifact is
    not a stale artifact — it is the normal state of a fresh clone, which `clean_checkout_check.py`
    relies on. The check fires only when there IS something at the shipping path, which is exactly
    when a reader would otherwise trust it.

    BUT "NOTHING IS THERE" AND "THIS MACHINE HAS NO FOOTAGE" ARE DIFFERENT QUESTIONS. The existence
    gate answered the first and the comment claimed the second. On a machine
    that HAS footage, repointing `artifact` at a path where nothing sits returned `[]` here and the
    staleness gate switched itself off silently — reproduced directly: a manifest identical except
    for `artifact` pointing at a nonexistent `nothing_is_here.mp4` exits 0, while the same technique
    with one caption character changed exits 1. Hashing `artifact` into the fingerprint did not
    close that, because the early return happens BEFORE the fingerprint is computed; that change
    only bites when `artifact` is repointed at a different file that exists AND carries a stamp.

    The discriminator is the shipped shots' own `source_dir`s. If none of them is on this disk, this is
    a fresh clone and an absent artifact is expected — `validate()` is already refusing, loudly, for
    every one of those absent directories. If ANY of them IS on this disk, the footage exists, so a
    declared artifact that does not is either a repoint or a build that was never run, and both are
    exactly what this gate is for."""
    rel = manifest.get("artifact", DEFAULT_ARTIFACT)
    artifact = Path(root) / rel
    if not artifact.is_file():
        has_footage = any((Path(root) / s["source_dir"]).is_dir()
                          for s in manifest.get("shots", [])
                          if s.get("status") == "shipped" and "source_dir" in s)
        if not has_footage:
            return []
        return ["artifact %s does not exist, but this checkout HAS shipped footage on disk. Either the "
                "cut has never been assembled here (run `python Scripts/shot_assemble.py`), or "
                "`artifact` has been repointed at a path nothing sits at - which used to switch this "
                "staleness gate off silently rather than failing." % rel]
    stamp = stamp_path(artifact)
    if not stamp.is_file():
        return ["assembled artifact %s exists but carries no %s stamp - it predates this check or was "
                "produced by something other than shot_assemble.py. Re-run "
                "`python Scripts/shot_assemble.py` so the shipped file is bound to the manifest that "
                "describes it." % (rel, STAMP_SUFFIX)]
    try:
        data = json.loads(stamp.read_text(encoding="utf-8"))
    except Exception as exc:
        return ["assembly stamp %s is unreadable (%s)" % (stamp.name, exc)]
    want = assembly_fingerprint(manifest)
    got = str(data.get("fingerprint", ""))
    if got != want:
        return ["STALE ARTIFACT: %s was assembled from a different manifest state (stamp %s, manifest "
                "now %s). Something that determines the pixels - a caption, a frame_range, a "
                "source_dir, the resolution or fps_out - changed since it was built, so the shipped "
                "MP4 contradicts the manifest this gate just passed. Re-run "
                "`python Scripts/shot_assemble.py`." % (rel, got[:12] or "<absent>", want[:12])]
    return []


def _check_evidence(shot, root, tag):
    """Refuse a designated capture file that is not the file the frames were rendered from.

    WHAT THIS IS FOR. Two shipped shots (02b, 08) are footage with a terminal panel burned into it, and
    that panel's text comes from a file a driver wrote. `terminal_capture.py`'s contract — a terminal
    panel may only render output a tool actually produced — was enforced by the PROVENANCE OF THE
    PATH and by nothing else, so it held only for as long as nothing else wrote that path. One such
    file was once found silently replaced, for seven hours, with a stub harness's canned
    returns, while every gate in this repo stayed green. That is the eighth instance found of a check
    that cannot observe what it claims to check, and it is the one that landed on the evidence layer
    itself.

    THE TWO REFUSALS, AND WHY EACH IS HONEST ABOUT ITS REACH.

    (1) CONTENT. `sha256` is the digest of the bytes the shipped frames were rendered from, recorded
        in the manifest at capture time. It cannot tell an honest re-capture from a fabrication — that
        is not a checksum's job and pretending otherwise would be the same overclaim this project
        keeps removing. What it does is make ANY change to the file a deliberate act: the gate goes
        red, and clearing it means re-rendering the shot or re-recording the digest on purpose, with
        the reason written down. A silent replacement is what was possible before; that is what stops.

    (2) ORDER. Evidence is written DURING the render and composited into frames AFTER it, so a file
        whose mtime is newer than the oldest frame in `source_dir` cannot be the file those frames
        were made from — whatever it contains. This is the half that would have caught the replaced file on its own:
        the transcript's mtime was 6 h 58 min newer than the frames'. It is a weaker signal than
        the hash (mtimes are settable) and a stronger one in the case that matters, because it fires
        even when nobody knows what the right bytes were.

    Runs for `shipped` shots only. `evidence_files` is optional — a shot with no designated capture
    file declares none, and eight of the ten do.
    """
    errs = []
    decls = shot.get("evidence_files") or []

    # A panel shot MUST name its evidence. See REQUIRED_FIELDS for why this is a declared property and
    # not an inferred one: nothing readable from the manifest distinguishes footage-with-a-panel from
    # plain footage, so the alternative to asking the author is not asking at all.
    panel = shot.get("has_terminal_panel")
    if not isinstance(panel, bool):
        errs.append("%s has_terminal_panel must be true or false, got %r" % (tag, panel))
    elif panel and not decls:
        errs.append("%s has_terminal_panel is true but no evidence_files are declared - the frames "
                    "carry a terminal panel and nothing on disk is bound to what it says" % tag)
    elif decls and not panel:
        errs.append("%s declares evidence_files but has_terminal_panel is false - one of the two is "
                    "wrong, and this gate cannot tell which" % tag)

    if not decls:
        return errs

    src = root / shot["source_dir"]
    frame_mtimes = [p.stat().st_mtime for p in _frames_on_disk(src)] if src.is_dir() else []
    oldest_frame = min(frame_mtimes) if frame_mtimes else None

    for i, d in enumerate(decls):
        etag = "%s evidence_files[%d]" % (tag, i)
        if not isinstance(d, dict):
            errs.append("%s is not an object: %r" % (etag, d))
            continue
        missing = [f for f in EVIDENCE_FIELDS if f not in d]
        if missing:
            errs.append("%s missing field(s): %s" % (etag, ", ".join(missing)))
            continue
        p = root / d["path"]
        if not p.is_file():
            errs.append("%s designated capture file absent: %s - this shot's frames have a terminal "
                        "panel composited from it, so its absence means the shipped pixels have no "
                        "evidence behind them on this checkout" % (etag, d["path"]))
            continue
        got = hashlib.sha256(p.read_bytes()).hexdigest()
        if got != d["sha256"]:
            errs.append(
                "%s FOREIGN OR RE-CAPTURED EVIDENCE: %s hashes %s, manifest records %s. The file the "
                "shipped frames were rendered from is not the file on disk. Either restore it, or "
                "re-render this shot from the new capture and update sha256 (and re-assemble - the "
                "digest is inside the assembly fingerprint). Written by %s."
                % (etag, d["path"], got[:12], str(d["sha256"])[:12], d["written_by"]))
        if oldest_frame is not None and p.stat().st_mtime > oldest_frame:
            errs.append(
                "%s EVIDENCE WRITTEN AFTER ITS FRAMES: %s is stamped %.0f, the oldest frame in %s is "
                "stamped %.0f. A transcript is written during the render and composited into the "
                "frames afterwards, so this file cannot be the one those frames were made from."
                % (etag, d["path"], p.stat().st_mtime, shot["source_dir"], oldest_frame))
    return errs


def _check_citation(cite, root, tag, warns=None):
    errs = []
    # Shape first. `REQUIRED_FIELDS` validates the SHOT's shape and nothing validated a
    # citation's, so a malformed cite raised KeyError out of a gate whose entire output contract is a
    # list of readable validation errors - a crash where a red belongs.
    # The shape, all the way down. The first version checked that the
    # three keys were PRESENT and stopped there, so `cite` itself being a list, or a non-string inside
    # `cite["keys"]`, still raised out of a gate whose entire output contract is a list of readable
    # validation errors — an AttributeError from `_dotted` that the `except KeyError` /
    # `except (TypeError, ValueError)` below do not catch. `_check_evidence` already checks its
    # declarations this way; a half-checked shape is the same defect as an unchecked one.
    if not isinstance(cite, dict):
        return ["%s citation is not an object: %r" % (tag, cite)]
    missing = [f for f in CITE_FIELDS if f not in cite]
    if missing:
        return ["%s citation malformed, missing field(s): %s (have %s)"
                % (tag, ", ".join(missing), ", ".join(sorted(cite)) or "nothing")]
    if not isinstance(cite["keys"], (list, tuple)) or not cite["keys"]:
        return ["%s citation 'keys' must be a non-empty list, got %r" % (tag, cite["keys"])]
    bad = [k for k in cite["keys"] if not isinstance(k, str)]
    if bad:
        return ["%s citation 'keys' must all be dotted-key strings, got %r" % (tag, bad)]
    if not isinstance(cite["claim"], str) or not isinstance(cite["source"], str):
        return ["%s citation 'claim' and 'source' must be strings" % tag]
    src = root / cite["source"]
    if not src.is_file():
        return ["%s citation source missing: %s" % (tag, cite["source"])]
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except Exception as exc:
        return ["%s citation source unparseable (%s): %s" % (tag, cite["source"], exc)]

    values = []
    for key in cite["keys"]:
        try:
            values.append(float(_dotted(data, key)))
        except KeyError:
            errs.append("%s citation key not found in %s: %s" % (tag, cite["source"], key))
        except (TypeError, ValueError):
            errs.append("%s citation key is not numeric in %s: %s" % (tag, cite["source"], key))
    if errs:
        return errs

    claimed = [float(n) for n in NUM_RE.findall(cite["claim"])]

    # Positional binding is the whole point of a citation: claimed[i] must match the value resolved
    # from keys[i], not merely appear somewhere in the resolved set. Set-membership alone would pass
    # a citation whose two adjacent fields got transposed (e.g. an up/down or before/after pair) as
    # long as both numbers are present ANYWHERE in the evidence, which defeats the "bound to evidence"
    # guarantee this whole mechanism exists for. Only fall back to set-membership when the claim's
    # number count doesn't line up 1:1 with keys[] (e.g. "46 instances: 3.6 / 4.0 ms" cites 2 budget
    # keys but the claim text also contains "46", which isn't one of them) — guessing an alignment
    # when the counts disagree would be worse than the old behavior, not better.
    if len(claimed) == len(cite["keys"]):
        for c, key, v in zip(claimed, cite["keys"], values):
            if abs(c - v) > TOL:
                errs.append("%s citation drift: claim %r has %s at the position of key %s, evidence %s has %s"
                            % (tag, cite["claim"], c, key, cite["source"], v))
    else:
        # THE WEAKER PATH SAYS SO NOW. Falling back to
        # set-membership was a deliberate, documented choice — guessing an alignment when the counts
        # disagree is worse than not guessing — but it was SILENT, and a silent downgrade from
        # positional binding to "appears somewhere" is the shape of every fault these gates kept
        # finding. Every shipped citation currently takes the strong path, so this warning is latent by
        # construction; it exists for the author who adds the first mismatched claim and would
        # otherwise get the weaker guarantee with nothing on screen to say so.
        if warns is not None:
            warns.append("%s citation %r has %d number(s) but cites %d key(s), so it is checked by "
                         "SET-MEMBERSHIP, not by position - a transposed pair would still pass. Make "
                         "the counts line up 1:1 to get the positional guarantee."
                         % (tag, cite["claim"], len(claimed), len(cite["keys"])))
        for c in claimed:
            if not any(abs(c - v) <= TOL for v in values):
                errs.append("%s citation drift: claim %r contains %s, evidence %s has %s"
                            % (tag, cite["claim"], c, cite["source"], values))
    return errs


def _check_caption_binding(shot, tag):
    """Bind the BURNED-IN caption to the citations. Both rules below were previously left to authoring
    convention, and this gate exists precisely because conventions drift while gates do not.

    (1) THE CLAIM MUST APPEAR IN THE CAPTION. `_check_citation` binds cite["claim"] to committed
        evidence, but nothing bound cite["claim"] to `caption_text` — and `caption_text` is the string
        `shot_assemble` burns into the pixels. Shots 04/05/06/07 agree with their claims word for word
        by authoring accident, not by check. Edit a caption alone and every gate stayed green while the
        frames asserted something no evidence backed. That is the still-vs-chart failure one level
        up, and a caption edit once hit the near-miss: caption_text's prose copy was edited instead of
        the claim field, and the slip was only caught before running.

    (2) EVERY NUMBER IN THE CAPTION MUST LIVE INSIDE SOME CLAIM. The citation layer only ever inspected
        numbers an author chose to put in a `claim`, so a caption could quote a figure that no citation
        mentions and nothing would notice. A number burned over footage IS an assertion whether or not
        somebody copied it into a claim field. (Before this rule, shot_check did NOT scan caption_text
        for uncited numerals, and every-numeric-claim-gets-a-citation was enforced by convention.)

    Numbers are compared as floats within TOL rather than as substrings so that a caption writing
    "+3.46" against a claim of "3.46" is not a false red — the check is about the VALUE being cited,
    not about punctuation.

    WHAT RULE (2) CANNOT SEE, STATED RATHER THAN LEFT TO BE DISCOVERED.
    `NUM_RE` matches DIGITS. A caption that spells a number out — "Three variables are the whole
    animator↔engineer interface", which shipped in 240 of the gameplay-bridge take's frames — carries a numeric
    assertion this function reports clean, and that particular one was contradicted by the graph's own
    pin bindings. It is not fixed here on purpose: a word-number matcher would have to decide whether
    "one movement source", "a single Template" and "both feet" are quantities, and a gate that guesses
    at English is a gate whose reds cannot be trusted. The residual is real and it is narrow — a
    spelled-out numeral in a caption is an UNGATED claim and has to be checked by a reader — so it is
    written down here, where anyone editing a caption is already looking.
    """
    errs = []
    caption = shot["caption_text"]
    claim_nums = []
    for cite in shot["caption_cites"]:
        # `_check_citation` type-guards this and reports a clean error, but `validate()` calls THIS
        # function unconditionally afterwards, so a non-dict citation reached `.get` and came out of the
        # gate as an AttributeError traceback instead of a finding. Skipping is right rather
        # than re-reporting: the citation check has already named it, and a second error for the same
        # entry would read as two defects.
        if not isinstance(cite, dict):
            continue
        claim = cite.get("claim", "")
        if claim not in caption:
            errs.append("%s caption_text does not contain its cited claim %r - the burned-in caption "
                        "and the checked claim have diverged" % (tag, claim))
        claim_nums.extend(float(n) for n in NUM_RE.findall(claim))

    uncited = [n for n in (float(x) for x in NUM_RE.findall(caption))
               if not any(abs(n - c) <= TOL for c in claim_nums)]
    if uncited:
        errs.append("%s caption_text quotes number(s) %s that no caption_cites claim covers - every "
                    "figure burned into the frames needs a citation"
                    % (tag, ", ".join("%g" % n for n in uncited)))
    return errs


def validate(manifest, root, require_complete=False, warns=None, check_artifact=True):
    """Return a list of human-readable error strings. Empty list == pass.

    `warns` is an optional list the caller passes in to collect NON-fatal notes — findings that do not
    make the manifest wrong but do mean a check is running in a weaker mode than its name implies.
    They are a separate channel rather than errors because turning them into failures would gate on
    authoring style, and rather than prints because `shot_assemble` imports validate() and its stdout
    is the assembly log."""
    root = Path(root)
    errs = []

    if manifest.get("schemaVersion") != SCHEMA_VERSION:
        errs.append("schemaVersion %r != %d" % (manifest.get("schemaVersion"), SCHEMA_VERSION))

    shots = manifest.get("shots", [])
    if not shots:
        errs.append("manifest has no shots")

    seen_ids, seen_order, total = set(), set(), 0.0
    for shot in shots:
        tag = "[%s]" % shot.get("shot_id", "?")

        # THIS SHOT'S OWN ERRORS, NOT THE TAIL OF EVERY SHOT'S. The guard used
        # to read `if any("missing required field" in e for e in errs[-len(REQUIRED_FIELDS):])`, which
        # inspects the tail of the GLOBAL error list. Once any shot logged a missing field, later shots
        # appended nothing, so that window still ended with the earlier shot's error and every
        # subsequent shot was `continue`d — skipping its citations, its caption binding, its
        # frame-range check, its derived-duration check and its contribution to the ≤180 s budget.
        # Reproduced by mutation at head: deleting shots[0]["title"] and corrupting the crowd take's citation
        # produced only the missing-field error, with the citation drift unreported. It could not
        # produce a false GREEN (the missing-field error is itself an error), which is why this is the
        # gate quietly narrowing rather than lying — the same shape as the faults these gates exist
        # to catch, inside the gate itself. `before` makes the window this shot's own.
        before = len(errs)
        for f in REQUIRED_FIELDS:
            if f not in shot:
                errs.append("%s missing required field: %s" % (tag, f))
        if len(errs) > before:
            continue

        if shot["shot_id"] in seen_ids:
            errs.append("%s duplicate shot_id" % tag)
        seen_ids.add(shot["shot_id"])
        if shot["order"] in seen_order:
            errs.append("%s duplicate order %s" % (tag, shot["order"]))
        seen_order.add(shot["order"])

        if shot["status"] not in VALID_STATUS:
            errs.append("%s status %r not in %s" % (tag, shot["status"], list(VALID_STATUS)))
        if require_complete and shot["status"] != "shipped":
            errs.append("%s status is %r — a shipping cut requires 'shipped'" % (tag, shot["status"]))

        if shot["status"] == "shipped":
            d = root / shot["source_dir"]
            if not d.is_dir():
                errs.append("%s source_dir absent: %s" % (tag, shot["source_dir"]))

            fr = shot["frame_range"]
            fr_ok = isinstance(fr, (list, tuple)) and len(fr) == 2
            if not fr_ok:
                errs.append("%s frame_range malformed (need [lo, hi]): %r" % (tag, fr))
            else:
                lo, hi = fr
                if d.is_dir():
                    frames = _frames_on_disk(d)
                    if hi > len(frames) or lo < 0 or lo >= hi:
                        errs.append("%s frame_range %s outside %d frames on disk"
                                    % (tag, fr, len(frames)))

                # duration_s is prose intent until frames exist to check it against — once a shot
                # is "shipped", (hi - lo) / fps_out IS the output length, nothing else determines
                # it, so duration_s becomes a checked assertion here rather than an unchecked wish
                # (same treatment caption_cites already gets for numbers in the caption text).
                # "missing" shots have no frame_range worth deriving from, so this only runs here.
                fps_out = float(manifest.get("fps_out", 30))
                derived_s = _derived_duration_s(shot, fps_out)
                declared_s = float(shot["duration_s"])
                if abs(derived_s - declared_s) > DURATION_TOL:
                    errs.append(
                        "%s duration_s %.2f disagrees with derived %.2f from frame_range %s @ %gfps_out"
                        % (tag, declared_s, derived_s, fr, fps_out))
                total += derived_s

            errs.extend(_check_evidence(shot, root, tag))

        for cite in shot["caption_cites"]:
            errs.extend(_check_citation(cite, root, tag, warns))
        errs.extend(_check_caption_binding(shot, tag))

    cap = float(manifest.get("target_duration_max_s", 180.0))
    if total > cap:
        errs.append("total shipped duration %.1fs exceeds budget %.1fs" % (total, cap))

    # OFF only for shot_assemble's own pre-flight: it is about to REPLACE the artifact, so refusing
    # because the artifact it is replacing is stale would deadlock the one command that fixes it.
    if check_artifact:
        errs.extend(_check_artifact(manifest, root))

    return errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="Plugins/MotionTerminal/Tests/shots_manifest.json")
    ap.add_argument("--root", default=".")
    ap.add_argument("--require-complete", action="store_true")
    a = ap.parse_args()

    root = Path(a.root).resolve()
    manifest = json.loads((root / a.manifest).read_text(encoding="utf-8"))
    warns = []
    errs = validate(manifest, root, a.require_complete, warns)
    for w in warns:
        print("NOTE:", w)
    if errs:
        print("SHOT CHECK FAILED (%d):" % len(errs))
        for e in errs:
            print("  -", e)
        return 1
    shipped = sum(1 for s in manifest["shots"] if s["status"] == "shipped")
    total = shipped_total_s(manifest)   # derived total — the same figure the budget check above gated on
    # `.get` with the same default validate() uses. Reading the key directly here meant a
    # manifest without `target_duration_max_s` passed validation and then crashed with a KeyError on
    # the SUCCESS path — a gate that says nothing is wrong and then dies printing so.
    print("SHOT CHECK OK:%d/%d shots shipped, %.1fs / %.1fs"
          % (shipped, len(manifest["shots"]), total,
             float(manifest.get("target_duration_max_s", 180.0))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
