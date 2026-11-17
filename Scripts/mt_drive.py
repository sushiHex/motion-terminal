# Shared scaffolding for the PIE capture drivers. It replaces driver-scaffold duplication (five
# near-verbatim copies) and a triplicated `_dominant_state`, which had the same root cause.
#
# =====================================================================================================
# WHY THIS IS AN `import` AND NOT AN `exec`, WHICH IS THE WHOLE REASON IT DID NOT EXIST BEFORE
#
# `mrq_render.render_shot` runs a driver with `exec(open(driver_path).read(), {'unreal': unreal})`.
# That namespace has no `__file__` and no `__name__`, so the obvious `os.path.dirname(__file__)` route
# to a sibling module is not available — which is what made a shared module look like added risk,
# and why the duplication was carried for a while instead of removed.
#
# Two facts close it:
#
#   1. `exec` DOES inject `__builtins__`, so `import` works normally inside that bare namespace. The
#      only missing piece is knowing WHERE to import from.
#   2. `unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir())` already answers that,
#      under exec, today. Five committed drivers plus `army_budget_probe`, `army_spawn`,
#      `clip_varies_check`, `metahuman_bind` and `stage_author` resolve repo-relative paths that way —
#      it is this repo's established answer to "find a file without hardcoding one machine's checkout".
#
# So each driver opens with a six-line loader that puts `Scripts/` on `sys.path` and imports this file.
#
# AND IMPORTING IS NOT MERGING, which is the point that matters most here. `exec(src, some_globals)` is
# a plain namespace merge: every module-level name the exec'd file defines lands on top of whatever the
# target namespace already held, silently. That fault retimed the crowd take from 8 s to 24 s with 480
# frames written, `capture_check` PASS and `success=True` — `army_conductor.TOTAL = 24.0` landing on a
# driver's own `TOTAL = 8.0`. `Scripts/namespace_merge_check.py` exists to gate that class. An `import`
# binds ONE name (the module object) and puts everything else behind an attribute lookup, so sharing
# code this way cannot reproduce the fault, and the gate stays green with no new sites to examine.
#
# THE MODULE IS RELOADED ON EVERY LOAD, on purpose. `render_shot` re-reads the driver from disk for
# every render, so an edit to a driver takes effect on the next take. A plain `import` would NOT behave
# that way: `sys.modules` would hold the first copy for the life of the editor session, so an edit to
# THIS file would be silently ignored while every gate stayed green — the exact silent-staleness shape
# this project gates everywhere else. The loader therefore ends in
# `importlib.reload(importlib.import_module('mt_drive'))`. Everything here is stateless apart from one
# class-default-object cache, so a reload mid-session is free and cannot desynchronise a driver that is
# already ticking (reload mutates the module object in place, so old and new callers see one copy).
#
# =====================================================================================================
# GOTCHA-A' — WHAT IS ALLOWED TO LIVE IN THIS FILE
#
# No World, Level, Actor or Component may ever be bound to a module-level name in a driver: the
# `run_python` namespace persists, and a surviving world reference makes the next map change a fatal
# EditorServer.cpp:2544 crash. That rule applies to this module too, and more sharply, because a module
# global here outlives every driver that imports it.
#
# So: `first_cine_camera()` RETURNS an actor and never stores one; `drive_cmc()` takes a component and
# never stores it. The single cached value is the `AnimDebugTools` CLASS-DEFAULT-OBJECT, which is not a
# world object at all — it belongs to the plugin's C++ module, has process lifetime, and is exactly what
# the per-driver `_S['tools']` slots this replaces were already caching (and reachable from
# `unreal._MT_LANE['tools']` &c. over a long development history, across many map changes, with no crash).
#
# =====================================================================================================
# WHAT IS DELIBERATELY *NOT* HERE
#
#   * `_read_var`. Two committed versions exist and they are genuinely different functions, not two
#     copies of one: `pie_lane_drive`'s returns a STRING and turns a failed read into the visible
#     sentinel `READ_FAILED(...)` so a malformed read cannot masquerade as a reading;
#     `pie_rifle_take_drive`'s returns a FLOAT (its caller does arithmetic on it) and None on failure.
#     Merging them would change one shot's log format or the other's arithmetic. Left alone.
#   * `pie_army_pullback_drive`'s scaffolding. It is the one driver that `exec`s another file into its
#     own globals, so it is the file where a new module-level name is not free, and its shareable
#     surface is two short blocks. Left verbatim; the reasoning is recorded at its own header.
#   * `_ap_lerp`. It does NOT clamp, unlike the `_lerp` in every other driver, and its caller feeds it a
#     smoothstep that is already clamped. Not the same function.
#   * Anything that decides WHAT a shot measures. Camera geometry, sampling cadence, scoring windows and
#     thresholds stay in the driver that owns the shot, because those are the shot's assertions.
import glob as _glob
import json
import math
import os
import uuid

import unreal

# The AnimDebugTools class-default-object. Populated on first use, never cleared: see GOTCHA-A' above
# for why a CDO is not the thing that rule forbids.
_TOOLS = {'cdo': None}


# =====================================================================================================
# CAPTURE EVIDENCE — THE ONE DOOR, AND WHY IT IS LOCKED
#
# THE FAULT THIS EXISTS TO MAKE IMPOSSIBLE. Three drivers each owned a
# module-level constant naming a FIXED repo path and wrote their transcript straight to it:
#
#     TRANSCRIPT = REPO + '/captures/m9-bridge/bridge_output.txt'      # pie_bridge_drive
#     TRANSCRIPT = REPO + '/captures/m9-montage/montage_nodeweights.txt'   # pie_montage_layer_drive
#     OUT        = REPO + '/captures/m9-terminal/f16_catchfix.txt'     # fbx_unit_catchfix_drive
#
# Those files are not scratch. `Scripts/terminal_capture.py` composites them into the shipped frames,
# and its whole real-output contract is that a terminal pane may only render output a tool actually produced — a
# contract enforced by THE PROVENANCE OF THE PATH. So the path is the guarantee, and any process that
# could reach it could void the guarantee silently.
#
# One did. A driver-equivalence harness ran each driver the way `render_shot` does —
# `exec(src, {'unreal': <stub>})` — for 60 simulated ticks. `pie_bridge_drive._tick` reaches its
# `DUMP_AT = 7.40` well inside 60 ticks, so it wrote its REAL path with the stub's canned return
# values: `IsFalling = 450.000000`, a reading `AnimDebugTools::GetAnimVariable` cannot produce for an
# FBoolProperty. A take's 240 shipped frames carried the honest transcript; the file backing them on
# disk did not, for seven hours, with every gate green — because no gate read a transcript, and the
# one thing that did (`terminal_capture._read_output`) refuses inline text, a missing file and an
# empty file, none of which a plausible fabrication is.
#
# WHAT THIS CHANGES. A driver no longer knows where its evidence goes. There is no constant to clobber
# and no path to reach: the destination arrives from OUTSIDE, in an ARM RECORD that only a real
# capture run installs, and `write_capture_evidence` raises if there isn't one. Under a stub there is
# no arm, so the write does not go somewhere wrong — it does not happen, and it says why.
#
# WHY THE ARM LIVES ON `unreal` AND NOT IN THIS MODULE. Every driver loads this file with
# `importlib.reload`, which re-runs the module body — so an arm held in a module global here would be
# wiped by the very driver it was set for. `unreal` is the one namespace that survives both the reload
# and the exec, which is also precisely why a stub cannot inherit it: the harness builds its own.
#
# WHAT EACH HALF OF THE GUARD CAN AND CANNOT SEE, stated rather than implied:
#   * the ARM RECORD is what stops a harness. It is installed by `mrq_render.render_shot` for an MRQ
#     shot and torn down when the render finishes, so a driver still ticking after its render — or
#     exec'd by anything that is not a render — has nowhere to write. This half is structural against
#     the ACCIDENT that happened: the driver never names a capture path, so a harness that merely
#     exec's it cannot make a write land on one by supplying canned return values. It is NOT a defence
#     against a harness that sets out to write there — see the honest division of labour below.
#   * the LIVENESS PROBE in `arm_capture_evidence` catches a MISCONFIGURATION, and that is all it
#     catches. It requires `unreal.Paths.project_dir()` to resolve to a directory holding a
#     `.uproject`, which stops an arm from landing somewhere meaningless. IT DOES NOT STOP A STUB, and
#     an earlier version of this comment said it did. Every driver's loader is
#     `sys.path.insert(0, project_dir() + '/Scripts')` followed by `import mt_drive` — so a stub that
#     does NOT answer with the real checkout cannot exec a driver past its first six lines. The probe's
#     one condition is therefore a condition any harness able to reach this code has already had to
#     satisfy, and the equivalence harness demonstrably did. It was reproduced: an 8-line stub, no
#     project tree shipped, armed `captures/m9-terminal` and the door resolved to the verification
#     shot's real evidence file.
#
#     So the honest division of labour is: the ARM RECORD is the defence, and what it defends against
#     is the ACCIDENT — a harness that exec's a driver without meaning to write anywhere real. It is
#     NOT a defence against a harness that intends to. A caller in this process can simply call the
#     public `arm_capture_evidence`, or forge `_MT_CAPTURE_ARM` with a matching pid and any token, and
#     the door opens on a real capture path; both were reproduced, and this
#     module's own `test_a_stub_pointing_at_a_real_tree_passes_the_probe` demonstrates a stub building
#     an arm record. An earlier version of this comment claimed `render_shot` was the only thing that
#     could install one, which the same module's tests contradict.
#
#     That is the correct scope, not a weakness to apologise for: the fault this exists to stop was an
#     accident, and against an accident it is complete. Against intent, nothing at this layer can help
#     — a caller that can exec arbitrary code in this process can write the file directly. What
#     backstops THAT — and what covers `fbx_unit_catchfix_drive`, which arms ITSELF because it is not an
#     MRQ shot at all — is
#     `shot_check`'s evidence gate: each designated file's sha256 is recorded and a file newer than the
#     frames it is composited into is refused, so a re-capture — honest or not — turns
#     `shot_check` RED until a human reconciles it. That is stated at the driver too.
# =====================================================================================================

_ARM_SLOT = '_MT_CAPTURE_ARM'


def arm_capture_evidence(out_dir, source):
    """Open the door. `out_dir` is where evidence for THIS run may be written; `source` names what
    opened it (a driver path, or the render harness) and appears in every refusal message.

    Returns the arm record. Raises if `unreal.Paths.project_dir()` does not resolve to a directory
    holding a `.uproject`.

    THAT CHECK IS A MISCONFIGURATION GUARD, NOT A STUB GUARD, and the difference is load-bearing
    enough to say twice — see the capture-evidence block above. Every driver reaches this module by
    `sys.path.insert(0, project_dir() + '/Scripts')`, so anything able to call this has already had to
    answer with the real checkout. What stops a harness is the ARM RECORD, which only
    `mrq_render.render_shot` installs."""
    proj = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir())
    if not isinstance(proj, str) or not _glob.glob(os.path.join(proj, '*.uproject')):
        raise RuntimeError(
            'REFUSING to arm capture evidence for %s: unreal.Paths.project_dir() resolved to %r, '
            'which holds no .uproject — so `out_dir` cannot be trusted to be where this run thinks it '
            'is, and nothing here may write a file that a shipped frame is composited from.'
            % (source, proj))
    out = os.path.abspath(out_dir).replace('\\', '/')
    os.makedirs(out, exist_ok=True)
    rec = {'out_dir': out, 'pid': os.getpid(), 'token': uuid.uuid4().hex, 'source': str(source)}
    setattr(unreal, _ARM_SLOT, rec)
    unreal.log('MT CAPTURE ARMED %s -> %s' % (source, out))
    return rec


def disarm_capture_evidence():
    """Close the door. Called when a render finishes or fatals, so a driver that outlives its render
    cannot write evidence for frames that are no longer being produced."""
    setattr(unreal, _ARM_SLOT, None)


def capture_evidence_path(name):
    """Absolute path for evidence file `name` inside the armed directory. Raises if unarmed.

    `name` must be a BARE FILENAME. A relative path with separators in it would let an armed driver
    climb back out into another shot's capture directory, which is the same fault one level down."""
    rec = getattr(unreal, _ARM_SLOT, None)
    if not isinstance(rec, dict):
        raise RuntimeError(
            'REFUSING to write capture evidence %r: no arm record. A driver may only write a file that '
            'a shipped frame is composited from while a real capture run has armed it — '
            'mrq_render.render_shot arms an MRQ shot, and fbx_unit_catchfix_drive arms itself against a '
            'live editor. Running under a stub or a test harness gets here, which is the point (an '
            'equivalence harness once overwrote a shipped transcript with stub values).'
            % name)
    if rec.get('pid') != os.getpid():
        raise RuntimeError('REFUSING to write capture evidence %r: arm record was made by pid %r, this '
                           'is pid %d.' % (name, rec.get('pid'), os.getpid()))
    tok = rec.get('token')
    if not (isinstance(tok, str) and len(tok) == 32 and all(c in '0123456789abcdef' for c in tok)):
        raise RuntimeError('REFUSING to write capture evidence %r: arm record has no valid token (%r).'
                           % (name, tok))
    out = rec.get('out_dir')
    if not (isinstance(out, str) and os.path.isdir(out)):
        raise RuntimeError('REFUSING to write capture evidence %r: armed out_dir %r is not a directory.'
                           % (name, out))
    if not name or os.path.basename(name) != name:
        raise RuntimeError('capture evidence name must be a bare filename, got %r' % name)
    return out + '/' + name


def write_capture_evidence(name, body):
    """Write `body` to `name` inside the armed directory. Returns the path written."""
    path = capture_evidence_path(name)
    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(body)
    unreal.log('MT CAPTURE EVIDENCE %s (%d bytes, armed by %s)'
               % (path, len(body), getattr(unreal, _ARM_SLOT, {}).get('source')))
    return path


def require_saved_gitignored(path, who):
    """Save `path` and refuse if the write is not safe. The write guard the rest of these scripts
    apply, applied to the last two writers that lacked it.

    TWO HABITS, both of which those writers had and both of which were fixed elsewhere first:

      1. `EditorAssetLibrary.save_asset` RETURNS A BOOL and they discarded it. A save that quietly
         failed reads back correct from the in-memory object, so a post-save verification that re-reads
         the path (`shot_seq_author._describe` does exactly that) would have passed against an unwritten
         asset for the whole editor session. `khai_inplace_author._require_saved` raises on a False
         return for exactly that reason.
      2. Nothing checked that the target is git-ignored. `metahuman_bind.place()` and
         `stage_author.author_one()` both refuse a non-ignored target BEFORE writing, because once a
         script that operated on "whatever level is loaded" saved the stock, git-tracked
         /Game/ThirdPerson/Lvl_ThirdPerson. Every sequence these two write lives under
         Content/MT/Showcase/, Content/MT/BeatsDE/ or Content/MT/Army/, all git-ignored — so nothing was at risk, and a
         safety pattern with two exemptions is one people learn to leave off.

    WHY IT LIVES HERE AND NOT IN `shot_seq_author`. It was defined there and
    `metahuman_bind` reached it with
    `importlib.reload(importlib.import_module('shot_seq_author'))._require_saved_gitignored(...)`.
    `shot_seq_author`'s module body ends in a mandatory dispatcher that RAISES on an unset `SEQ_ACTION`
    (and pops the name, so a reload always sees None), and `importlib.reload` re-executes the body — so
    the guard was unreachable and `metahuman_bind.sequence()` raised at its last statement, never saving
    `Seq_Roster4Up` at all. Strictly worse than the unguarded save it replaced, and it shipped green
    because nothing executed it. The pattern was copied from this file's own loader, which is safe here
    precisely because THIS module has no import-time side effects — so this is where a helper two
    modules share belongs. `who` names the caller in every refusal, which is the only thing the move
    costs.

    FAILS CLOSED, like its siblings: if git cannot be run, or the path is not under /Game, this raises
    rather than assuming the write is safe."""
    import subprocess
    if not isinstance(path, str) or not path.startswith('/Game/'):
        raise RuntimeError('%s: refusing a non-/Game save target %r' % (who, path))
    repo = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()).rstrip('/')
    uasset = repo + '/Content/' + path[len('/Game/'):] + '.uasset'
    try:
        rc = subprocess.call(['git', 'check-ignore', '--quiet', uasset], cwd=repo,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except Exception as exc:
        raise RuntimeError('%s: could not run `git check-ignore` (%s) — failing CLOSED rather than '
                           'assuming %s is safe to write' % (who, exc, path))
    if rc != 0:
        raise RuntimeError(
            'REFUSING TO SAVE: %s is NOT git-ignored (git check-ignore exit %d, file %s). Every asset '
            '%s writes must live under a machine-local gitignored tree.' % (path, rc, uasset, who))
    # Saved through AnimAssetTools.SaveBuiltAsset (UPackage::SavePackage), not EditorAssetLibrary.save_asset: the
    # editor's save renders a thumbnail, which for an Animation Blueprint runs its event graph in a world with no
    # pawn; the Blueprint warning that logs (TryGetPawnOwner is None) reaches Python as an exception from a save
    # that succeeded, and the caller then deletes a good asset. It refuses an asset that is not in memory.
    reply = json.loads(unreal.AnimAssetTools.save_built_asset(path))
    if not reply.get('ok'):
        raise RuntimeError('%s: %s was not saved: %s' % (who, path, reply.get('error')))
    unreal.log('MT SAVED %s (%s, gitignored)' % (path, who))
    return True


def remove_capture_evidence(name):
    """Delete `name` in the armed directory if it exists. Same door, so a driver that withholds a
    transcript cannot reach a path it is not allowed to write."""
    path = capture_evidence_path(name)
    if os.path.exists(path):
        os.remove(path)
        unreal.log('MT CAPTURE EVIDENCE REMOVED %s' % path)
    return path


def anim_debug_tools():
    """The `AnimDebugTools` CDO — the handle every driver calls `GetAnimDebugState` /
    `GetAnimVariable` through. Was nine byte-identical `_tools()` definitions across `Scripts/`."""
    if _TOOLS['cdo'] is None:
        _TOOLS['cdo'] = unreal.get_default_object(
            unreal.load_class(None, '/Script/MotionTerminal.AnimDebugTools'))
    return _TOOLS['cdo']


def parse_num(reply):
    """The number out of a `<var> = <value>` reflection reply, or None if it is not one.

    GetAnimVariable answers "Speed = 450.000000"; the callers want the float. Returning None
    rather than raising is deliberate - a probe that dies mid-tick leaves a registered callback
    and a half-measured run - but None then MEANS "the read failed", and a caller that formats it
    into a log line without checking has turned a failed read into the string "NA" and moved on.
    That is exactly how two probes came to claim a read-back PROVED something it never checked.
    """
    try:
        value = float(str(reply).split('=')[-1].strip())
    except Exception:
        return None
    # NaN AND INF ARE FAILED READS, NOT VALUES. Callers compare with
    # `abs(rd - expected) > tol`, and that is FALSE for NaN - so a variable driven to NaN by a bad
    # blend or a divide sailed through the very read-back assertions added to make read-backs mean
    # something. Rejecting it here fixes every call site at once instead of asking each one to
    # remember, which is the whole reason this helper was hoisted.
    return value if math.isfinite(value) else None


def set_anim_vars_checked(label, pairs, verify=False, tol=1e-3):
    """Write (name, value) pairs through SetAnimVariable; return the names that FAILED.

    WHAT "FAILED" MEANS, PRECISELY. By default: the call did not answer
    "ok:". That is NOT the same as "the value stuck" - an EventGraph that re-derives a variable
    every update answers ok: and then overwrites it, which is exactly how `Speed` fooled an earlier
    driver. The docstring used to say "did NOT stick" and claimed more than the code checked,
    which is the defect class this whole series is about.

    Pass verify=True to also read the value back and compare within `tol`; that IS a stick check,
    and it is what pie_turninplace and pie_handreach had to hand-roll. It is opt-in rather than
    default because it is only meaningful for PURE EXTERNAL vars - asking it of a derived one like
    Speed would report a permanent, correct failure.

    SetAnimVariable answers "ok: <var> = <val>" on success and "ERROR: ..." otherwise, and discarding
    that answer is how this project keeps producing probes that measure a graph they never configured.
    A dropped write does not raise - it leaves the variable at its default, and now that the gate
    alphas default to 0 that reads as "the feature does nothing" rather than as a failure. Which is
    indistinguishable, in a log, from the feature genuinely doing nothing.

    Returning the failures rather than raising is deliberate: a probe that dies mid-tick leaves a
    registered callback and a half-measured run, which is worse than one that finishes and says
    loudly that its numbers are void. Callers MUST do something with the result - reporting it once
    is enough; ignoring it is the exact bug this function exists to stop.
    """
    bad = []
    # INSIDE the try, not outside it. anim_debug_tools() raises when the
    # MotionTerminal module is not loaded, and this function promises not to raise - a probe that
    # dies mid-tick leaves its slate callback registered for the life of the editor session, which
    # is the state the promise exists to avoid. Resolving it per call is cheap: it is cached.
    for name, val in pairs:
        try:
            reply = str(anim_debug_tools().call_method(
                'SetAnimVariable', args=(label, name, repr(float(val)))))
        except Exception as e:
            reply = 'ERROR: %s' % e
        if not reply.startswith('ok:'):
            bad.append(name)
            continue
        if verify:
            try:
                back = parse_num(anim_debug_tools().call_method(
                    'GetAnimVariable', args=(label, name)))
            except Exception:
                back = None
            # parse_num already rejects NaN/inf, so None here means "unreadable or not finite".
            if back is None or abs(back - float(val)) > tol:
                bad.append(name)
    return bad


def rifle_aim_pair(on):
    """The (name, value) pairs that turn the rifle-mode aim beat ON or OFF, as ONE unit.

    WHY THIS IS A HELPER AND NOT TWO WRITES AT EACH CALL SITE. Since 2026-08-14 the aim offset lives
    INSIDE the rifle branch of ABP_MT_Locomotion: SequenceEvaluator(MF_Rifle_Idle_ADS) -> AO_Rifle ->
    LayeredBoneBlend(spine_01, weight=RifleModeAlpha) -> 'RifleModePose'. AO_Rifle's samples are
    mesh-space rotation additives extracted as D = S*R^-1 against frame 0 of MF_Rifle_Idle_ADS, so
    D*R reconstructs the authored pose S and D over any OTHER base is a deformation. That makes the
    two variables a single fact with two names:

      * `AimOffsetAlpha` alone re-enables the aim over whatever base the branch supplies. When the
        branch is off that base is the UNARMED pose, which IS the original defect this pair of variables
        exists to fix (research/aim-offset-neutrality-defect.md).
      * `RifleModeAlpha` alone is a rifle idle that does not aim.
      * At RifleModeAlpha=0 the AO's output is blended out of the result entirely, so a test that
        writes only AimOffsetAlpha measures a zero response from a graph that is working.

    The explanation above used to be restated, in four different wordings, at every site that wrote
    the pair. That is the same "open-coded in five files" fault this module's `set_anim_vars_checked`
    was extracted to end: a future change to the invariant would have to be re-argued in four places
    and would rot in whichever one was missed. Callers splice this into their own writes tuple.

    NOT for probes that drive the two axes INDEPENDENTLY - pie_riflegate_verify_drive.py holds one
    fixed while toggling the other precisely to prove they are separable, and must keep writing them
    by hand. The contract here is "the aim beat, on or off", not "these two are always equal"."""
    v = 1.0 if on else 0.0
    return (('RifleModeAlpha', v), ('AimOffsetAlpha', v))


def node_debug_line(label, substr):
    """The first `GetNodeWeights` line whose text contains `substr` (case-insensitive), or a marker.

    GatherDebugData is the only route to a node's ACTUAL runtime alpha/weight — the .uasset records
    what a pin is BOUND to, not what it resolves to, and the difference is where an unbound pin
    sitting at its constructor default hides. Never raises: a probe that cannot read a debug line
    still has its measurement, and losing the corroboration is not a reason to break the tick."""
    try:
        dump = str(anim_debug_tools().call_method('GetNodeWeights', args=(label,)))
        for raw in dump.split('\n'):
            if substr.lower() in raw.lower():
                return raw.strip()
        return '<no line matching %r>' % substr
    except Exception as e:
        return '<GetNodeWeights err %s>' % e


_BONE_FNAMES = {}


def bone_fname(name):
    """Cached FName for a bone name. `unreal.Name(str)` builds a fresh FName on every call, and a
    per-tick multi-bone sampler pays that for every bone on every frame — for a bone set that is
    fixed at module scope."""
    fn = _BONE_FNAMES.get(name)
    if fn is None:
        fn = _BONE_FNAMES[name] = unreal.Name(name)
    return fn


_SOCKET_LOCALS = {}


def bone_transform(mesh, bone, space=None):
    """A BONE's transform. Every bone read Python reaches (get_socket_transform, get_bone_transform,
    get_socket_location) prefers a socket of the same name, case-insensitively (SkinnedMeshComponent.h:1516 ->
    GetSocketTransform): the Paragon meshes' Foot_L / Foot_R sockets sit 13.3 cm down each foot, so 'foot_r' read
    the socket, and the knee's turn moved it against the calf. A socket on the bone itself is
    undone (the socket is its local transform on top of the bone's); a socket of that name on another bone is refused."""
    asset = mesh.get_skeletal_mesh_asset()
    key = (asset.get_path_name() if asset else None, bone)
    if key not in _SOCKET_LOCALS:
        sock = asset.find_socket(bone_fname(bone)) if asset else None
        if sock is not None and str(sock.bone_name).lower() != bone.lower():
            raise RuntimeError('socket %s sits on %s, not on the bone %s' % (sock.socket_name, sock.bone_name, bone))
        _SOCKET_LOCALS[key] = sock and unreal.Transform(sock.relative_location, sock.relative_rotation,
                                                        sock.relative_scale).inverse()
    tm = mesh.get_socket_transform(bone_fname(bone), unreal.RelativeTransformSpace.RTS_WORLD if space is None else space)
    return _SOCKET_LOCALS[key] * tm if _SOCKET_LOCALS[key] else tm


def sample_bone_rotations(mesh, bones):
    """{bone: world-space rotation quaternion} for `bones`, sampled on one tick."""
    return {b: bone_transform(mesh, b).rotation for b in bones}


def quat_angle_deg(a, b):
    """Angular distance between two rotations, wrap-safe, all three axes.

    theta = 2*acos(|dot|) off the raw components — `Quat.get_angle()` is the angle of ONE rotation,
    not the angle BETWEEN two, and the abs() is what makes q and -q (the same orientation, opposite
    sign) read as 0 rather than 360."""
    dot = a.x * b.x + a.y * b.y + a.z * b.z + a.w * b.w
    return math.degrees(2.0 * math.acos(max(-1.0, min(1.0, abs(dot)))))


def alternating_pairs_deltas(hist, bones):
    """Score an A/B probe that toggles its subject EVERY tick. Returns (ab, aa): per-bone lists of
    degrees, `ab` the A-vs-B delta and `aa` the SAME-STATE control across the same interval.

    `hist` is [(is_state_a, {bone: quat}), ...] in tick order. Triples of (A, B, A) yield one
    sample each: ab from the adjacent A->B pair, aa from the A->A pair that straddles it.

    WHY ALTERNATE RATHER THAN HOLD. The obvious A/B — hold A for seconds, then B — compares poses
    seconds apart, and every pose here is cyclic, so the gait lands inside the delta and is
    indistinguishable from the effect. Toggling every tick makes the compared poses ONE CALLBACK apart -
    which is a frame only if the callback runs per frame. A slate pre-tick callback in this project has
    been measured as low as ~3 Hz under load (see render_evidence.json's slide _method note); at that rate
    'adjacent' spans most of a stride, so the interval is a property of the RUN and has to be read off
    it rather than assumed.
    `aa` is what keeps that honest: it is the base animation's own drift across an A -> A pair. But the
    subject is NOT held in one state across it - the sequence is A/B/A, so a B tick sits between the two
    endpoints and whatever B did to the pose has one tick to decay. It is a same-state ENDPOINT
    comparison, not a same-state interval, and it also spans twice the time (see below). Neither
    departure is automatically conservative: for cyclic motion a longer interval can carry a bone back
    toward where it started and UNDERSTATE the drift. Callers that report `ab` without `aa` have
    measured nothing; callers that treat `aa` as a strict lower bound have overread it.

    NOTE THE INTERVALS ARE NOT EQUAL. `ab` spans ONE tick (i -> i+1); `aa` spans TWO (i -> i+2),
    because the same-state pair straddling the toggle is the only same-state pair available. So
    `aa` OVERSTATES the one-tick drift, by roughly a factor of two for smooth motion. That biases
    the comparison in the conservative direction — clearing a control that is too large is a
    stronger result, not a weaker one — but it is not a like-for-like control, and it must never be
    described as one. A same-duration control needs a hold-A/hold-B probe, not this one."""
    ab, aa = {}, {}
    for i in range(len(hist) - 2):
        (a0, p0), (b1, p1), (a2, p2) = hist[i], hist[i + 1], hist[i + 2]
        if not (a0 and not b1 and a2):
            continue
        for b in bones:
            ab.setdefault(b, []).append(quat_angle_deg(p0[b], p1[b]))
            aa.setdefault(b, []).append(quat_angle_deg(p0[b], p2[b]))
    return ab, aa


def dominant_state(debug_text, machine='Locomotion'):
    """Heaviest state of `machine` in a `GetAnimDebugState` dump.

    `AppendMachineStates` prints `machine '<name>' (index N, weight W):` followed by one indented
    `  state '<name>' weight W` line per state whose weight exceeds KINDA_SMALL_NUMBER, so during a
    blend two states are listed and the heaviest is the one the shot reads as. Returns None if the
    machine is absent, which is itself a finding — it means the READ is broken, not the rig.

    Was triplicated across `pie_lane_drive`, `pie_roster4_drive` and `metahuman_bind`."""
    cur, best, best_w = None, None, 0.0
    for line in debug_text.splitlines():
        s = line.strip()
        if s.startswith("machine '"):
            cur = s.split("'")[1]
        elif s.startswith("state '") and cur == machine:
            name = s.split("'")[1]
            try:
                w = float(s.rsplit(' ', 1)[1])
            except (IndexError, ValueError):
                w = 0.0
            if w > best_w:
                best, best_w = name, w
    return best


def lerp(a, b, t):
    """Scalar lerp, CLAMPED at both ends — the camera drives feed it `now / TOTAL`, which runs past 1.0
    whenever MRQ ticks a frame beyond the take length, and an unclamped camera would fly past B."""
    t = max(0.0, min(1.0, t))
    return a + (b - a) * t


def vector_lerp(a, b, t):
    """Component-wise `lerp` between two `unreal.Vector`s. Same clamp."""
    return unreal.Vector(lerp(a.x, b.x, t), lerp(a.y, b.y, t), lerp(a.z, b.z, t))


def retire_previous(slot):
    """Unregister the previous instance of ONE driver, found at `unreal.<slot>` (e.g. `_MT_LANE`).

    `run_python` execs into a PERSISTENT namespace, so a driver loaded twice in one editor session
    would otherwise leave the first copy registered and ticking against the second one's world.
    Wrapped in try/except because the handle can already be dead if the editor tore the callback down,
    and failing to retire the old copy is not a reason to refuse to start the new one."""
    prev = getattr(unreal, slot, None)
    if prev and prev.get('handle') is not None:
        try:
            unreal.unregister_slate_pre_tick_callback(prev['handle'])
        except Exception:
            pass
        prev['handle'] = None


def retire_and_claim(slot):
    """`retire_previous`, then claim a fresh generation. Returns the generation claimed.

    The second half is what retires drivers this one has never heard of: bumping the GLOBAL
    `unreal._MT_DRIVE_GEN` invalidates every other driver in the namespace, each of which compares it
    against the generation it claimed and unregisters itself on its own next tick.
    `mrq_render.render_shot` bumps the same counter by 1000 when a render ends or fatals, which is
    what tears the whole set down once the frames are written.

    `pie_rifle_take_drive` deliberately calls `retire_previous` alone: it snapshots the CURRENT
    generation without claiming a new one, because it rides alongside `pie_aim_hand_ik_drive` and must
    retire WITH that driver rather than retiring it."""
    retire_previous(slot)
    gen = int(getattr(unreal, '_MT_DRIVE_GEN', 0)) + 1
    unreal._MT_DRIVE_GEN = gen
    return gen


def first_cine_camera(world):
    """The first `CineCameraActor` in `world`, or None.

    FIRST-WINS is a known limitation, carried deliberately: every stage this drives holds exactly one
    CineCameraActor (the sequence's spawnable), and picking by label would couple the driver to a
    binding name the sequencer owns. Returns the actor to a LOCAL; nothing here retains it (GOTCHA-A')."""
    for a in unreal.GameplayStatics.get_all_actors_of_class(world, unreal.CineCameraActor):
        return a
    return None


def drive_ab_camera(world, f, cam_a, cam_b, focal=None, yaw_off=0.0):
    """Place the shot camera on the A->B path at fraction `f`. Returns the camera actor, or None if
    the world has none yet (the first ticks of an MRQ PIE session, before the spawnable exists).

    `cam_a` / `cam_b` are `(location, look_at_target)` pairs of `unreal.Vector`. BOTH ends are lerped,
    including the aim point, so a future re-frame that moves the target cannot silently keep using the
    A end for the whole take.

    ALWAYS `find_look_at_rotation`, never a hand-built Rotator — `unreal.Rotator`'s constructor takes
    (roll, pitch, yaw) while its print order is (pitch, yaw, roll), and a transposition of
    exactly that aimed two capture drivers straight up. `yaw_off` is applied AFTER the look-at, which
    is a framing offset rather than an aim, and is why it is a separate argument.

    `focal` is optional because the callers disagree on purpose: the beat drivers latch the focal
    length on the first tick that finds a camera (pass it once, then pass None), while
    `pie_army_pullback_drive` re-asserts it every tick so a camera the sequence re-templates mid-render
    cannot quietly shoot on the CineCamera default. Passing None skips the write entirely."""
    cam = first_cine_camera(world)
    if cam is None:
        return None
    if focal is not None:
        comp = cam.get_cine_camera_component()
        if comp:
            comp.set_editor_property('current_focal_length', focal)
    loc = vector_lerp(cam_a[0], cam_b[0], f)
    tgt = vector_lerp(cam_a[1], cam_b[1], f)
    rot = unreal.MathLibrary.find_look_at_rotation(loc, tgt)
    if yaw_off:
        rot.yaw += yaw_off
    cam.set_actor_location_and_rotation(loc, rot, False, True)
    return cam


def drive_cmc(cmc, speed, falling=False):
    """Drive `Speed` (and `IsFalling`) at their SOURCE, in the one order that works.

    All three locomotion contract vars are DERIVED every tick by ABP_MT_Locomotion's
    EventBlueprintUpdateAnimation, so a reflection write via `SetAnimVariable('Speed', 400)` returns
    `ok`, reads back as 400.0, and is OVERWRITTEN before the state machine evaluates — a pawn that
    stands in Idle while every log line says it is running (see the `Speed` finding in army_conductor.py). What survives is a
    write to the CharacterMovementComponent the derivation reads:

        Speed      = VectorLength(GetVelocity(pawn))   <- CharacterMovementComponent.Velocity
        IsFalling  = CMC.IsFalling(), i.e. MovementMode == MOVE_Falling
        GroundedFlag = select(IsFalling, 0, 1)          <- follows, never written

    ---- ORDER IS LOAD-BEARING. Mode, then velocity, then disable the tick — LAST. ----

    MODE BEFORE VELOCITY: `OnMovementModeChanged` zeroes Velocity on entry to MOVE_None
    (StopMovementKeepPathing), so writing velocity first lets the mode change eat the write.

    TICK-DISABLE LAST, BECAUSE THE REFLECTED `velocity` WRITE RE-ENABLES THE COMPONENT TICK. Measured
    on the live PIE component, one call per line, tick disabled at the start:

        baseline                            -> is_component_tick_enabled() False
        set_movement_mode(MOVE_NONE)        -> False
        set_movement_mode(MOVE_FALLING)     -> False
        set_editor_property('velocity', ..) -> True     <-- the re-enabler

    (`set_editor_property` routes through PostEditChangeProperty, which re-registers the component in
    an editor build and restores tick-enabled from its defaults. `SetMovementMode` was cleared as a
    suspect by the two middle lines.) The first version of the lane driver disabled the tick FIRST and
    the airborne beat silently died: the velocity write re-armed the tick, PhysFalling ran for real,
    the pawn hit the floor within one frame and the engine put it in MOVE_Walking with braking friction
    zeroing the velocity — an ordering bug that costs the jump, the fall and the landing while every
    other beat still looks green.

    try/finally, NOT bare statement order: the invariant "the tick is off when this returns" would
    otherwise hold only because the two calls are adjacent, so ANY raise between them (a bad Vector, a
    stale component handle) would leave that CMC ticking into the next world tick — reproducing that
    failure intermittently, and without the ordering being visibly wrong. The finally makes it
    structural.

    With the tick off and MOVE_None, `APawn::GetVelocity()` returns `MovementComponent->Velocity`
    verbatim, so the written value survives to ABP-update time, nothing re-integrates it, and the actor
    does not translate. That is what makes these shots play IN PLACE."""
    try:
        cmc.set_movement_mode(unreal.MovementMode.MOVE_FALLING if falling
                              else unreal.MovementMode.MOVE_NONE)
        cmc.set_editor_property('velocity', unreal.Vector(speed, 0.0, 0.0))
    finally:
        cmc.set_component_tick_enabled(False)
