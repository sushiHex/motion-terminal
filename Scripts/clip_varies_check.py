"""
Re-runnable clip-varies checker (real-output rule) with a stated threshold,
denser sampling, a measured (not inferred) zero-motion floor, and a secondary-bone
spot-check for clips near the low end.

Run in the editor through `Scripts/editor_ops.py` (`ops.run_file`).
Reads clip identities from `Plugins/MotionTerminal/Tests/GAS_Clips_manifest.json` (does not hardcode the
curated list -- the manifest is the source of truth for what's imported), so this checker can be re-run
against a changed manifest without editing code.

Not meant to be run as a standalone python.exe script -- it calls into the `unreal` module, which only
exists inside the editor's embedded interpreter.

--- What this checker guarantees ---
IMPORTANT 1: the sampling code now lives here, committed and re-runnable, instead of being ad hoc.
IMPORTANT 2: VARIES_THRESHOLD_DEG below is the explicit, coded pass/fail rule (see comment for the
             justification against the measured floor).
IMPORTANT 3: floor_same_time_repeat sampling below and each clip's translation-channel reading are
             measured (not inferred) zero-motion negative controls, using the *same* instrument
             (get_bone_pose_for_time) rather than a fresh one.
IMPORTANT 4: NEAR_LOW_CUTOFF_DEG auto-triggers a secondary-bone spot-check on any clip whose primary
             spine_03 reading lands near the floor, so a spine-only under-report can't hide silently.
MINOR 5: SAMPLE_COUNT is raised from 5 to 25, and min_window_variance() reports the minimum variance
         across a sliding window of consecutive samples, so a mid-clip freeze isn't averaged away by
         motion elsewhere in the clip.
"""

import unreal
import json

# Derived from the running project, never from one machine's checkout. Five other committed
# scripts that hardcoded an absolute path; this was the sixth, missed because the grep that declared
# itself clean did not cover it. `unreal.Paths.project_dir()` returns the directory holding the .uproject,
# which is the repo root here — the same derivation the other five now use.
MANIFEST_PATH = (unreal.Paths.project_dir()
                 + "Plugins/MotionTerminal/Tests/GAS_Clips_manifest.json")

# --- Threshold (IMPORTANT 2) ---
# Justified against the measured floor (IMPORTANT 3, printed below as FLOOR_SAME_TIME_REPEAT_DEG):
# the same-time-repeat negative control reads ~0 deg (float epsilon), and every real clip's
# translation channel (spine_03 is rigid-parented, so its local translation is static across the
# whole clip regardless of animation content) also reads ~0. The lowest real clip
# (MT_GAS_Idle_Loop) measures ~1.07 deg. 0.5 deg sits comfortably between the ~0 floor and that
# lowest real clip -- roughly half of it, an order of magnitude clear of float noise.
VARIES_THRESHOLD_DEG = 0.5

# Any clip whose primary-bone variance lands below this gets an automatic secondary-bone
# spot-check (IMPORTANT 4) -- not hardcoded to the one clip first found weak, so a future weak
# clip is caught the same way.
NEAR_LOW_CUTOFF_DEG = 2.0
SPOT_CHECK_BONES = ["hand_r", "lowerarm_r", "hand_l"]

SAMPLE_COUNT = 25   # was 5 (MINOR 5)
WINDOW_SIZE = 5     # consecutive-sample window for mid-clip-freeze detection


def sample_bone(seq, bone, times):
    """Sample `bone`'s local rotation+translation at each time in `times`.
    Returns a list of {'t', 'rot': [roll,pitch,yaw] deg, 'loc': [x,y,z]} in sample order."""
    out = []
    for t in times:
        tr = unreal.AnimationLibrary.get_bone_pose_for_time(seq, bone, float(t), False)
        e = tr.rotation.euler()
        loc = tr.translation
        out.append({"t": t, "rot": [e.x, e.y, e.z], "loc": [loc.x, loc.y, loc.z]})
    return out


def _span(vectors):
    """Sum of per-axis (max-min) range across a list of 3-vectors."""
    mins = [None, None, None]
    maxs = [None, None, None]
    for v in vectors:
        for i in range(3):
            mins[i] = v[i] if mins[i] is None else min(mins[i], v[i])
            maxs[i] = v[i] if maxs[i] is None else max(maxs[i], v[i])
    return sum(maxs[i] - mins[i] for i in range(3))


def rot_variance(samples):
    return _span([s["rot"] for s in samples])


def trans_variance(samples):
    return _span([s["loc"] for s in samples])


def min_window_variance(samples, window):
    """Minimum rot_variance across every consecutive `window`-sized slice of `samples`."""
    if len(samples) < window:
        return rot_variance(samples)
    vals = [rot_variance(samples[i:i + window]) for i in range(len(samples) - window + 1)]
    return min(vals)


def sample_times(length, count):
    # 0% .. ~96% of clip length; avoids the exact-end sample (loop-wrap edge case).
    return [(i / float(count)) * length for i in range(count)]


def run():
    with open(MANIFEST_PATH, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)

    dest = manifest["destinationPath"]
    clips = manifest["clips"]

    # --- Negative control (a): same-time repeat (IMPORTANT 3) ---
    # Re-sample one real clip's probe bone at the *same* instant 5x. Genuine no-motion between
    # calls must read ~0 (float epsilon) -- this proves the instrument's own zero, independent of
    # any clip content.
    probe_asset = clips[0]["asset"]
    probe_bone = clips[0]["bone"]
    probe_seq = unreal.load_asset(f"{dest}/{probe_asset}")
    repeat_t = clips[0]["lengthSeconds"] * 0.5
    repeat_samples = sample_bone(probe_seq, probe_bone, [repeat_t] * 5)
    floor_same_time = rot_variance(repeat_samples)
    print(f"FLOOR_SAME_TIME_REPEAT_DEG={floor_same_time!r} (clip={probe_asset} bone={probe_bone} t={repeat_t:.3f}, n=5)")

    results = []
    for clip in clips:
        name = clip["asset"]
        bone = clip["bone"]
        length = clip["lengthSeconds"]
        try:
            seq = unreal.load_asset(f"{dest}/{name}")
            if seq is None:
                raise RuntimeError("load_asset returned None")

            times = sample_times(length, SAMPLE_COUNT)
            samples = sample_bone(seq, bone, times)
            rv = rot_variance(samples)
            tv = trans_variance(samples)   # negative control (b): same bone's static (local) channel
            mw = min_window_variance(samples, WINDOW_SIZE)
            passed = rv >= VARIES_THRESHOLD_DEG

            entry = {
                "asset": name, "bone": bone, "sampleCount": SAMPLE_COUNT,
                "measuredVarianceDeg": round(rv, 5), "minWindowVarianceDeg": round(mw, 5),
                "translationFloorDeg": round(tv, 5), "verdict": "varies" if passed else "STATIC",
            }
            print(f"CLIPCHECK_{name} rot={rv:.5f} minWindow={mw:.5f} transFloor={tv:.5f} "
                  f"verdict={'PASS' if passed else 'FAIL'}")

            # IMPORTANT 4: auto spot-check any clip near the floor on limb bones.
            if rv < NEAR_LOW_CUTOFF_DEG:
                spot = {}
                for sb in SPOT_CHECK_BONES:
                    try:
                        sb_samples = sample_bone(seq, sb, times)
                        spot[sb] = round(rot_variance(sb_samples), 5)
                    except Exception as e:
                        spot[sb] = f"ERROR: {e}"
                entry["spotCheck"] = spot
                numeric = {b: v for b, v in spot.items() if isinstance(v, float)}
                print(f"SPOTCHECK_{name} {spot}  (near-low cutoff {NEAR_LOW_CUTOFF_DEG})")
                if numeric:
                    best_bone = max(numeric, key=numeric.get)
                    entry["spotCheckBestBone"] = best_bone
                    entry["spotCheckBestDeg"] = numeric[best_bone]
        except Exception as e:
            entry = {"asset": name, "ok": False, "error": str(e)}
            print(f"CLIPCHECK_{name} ERROR={e}")
        results.append(entry)

    print("")
    print(f"{'asset':<20}{'rot_deg':>10}{'minWin_deg':>12}{'transFloor':>12}{'verdict':>10}")
    for r in results:
        if not r.get("ok", True):
            print(f"{r['asset']:<20}{'LOAD/SAMPLE FAILED':>44}")
            continue
        print(f"{r['asset']:<20}{r['measuredVarianceDeg']:>10.3f}{r['minWindowVarianceDeg']:>12.3f}"
              f"{r['translationFloorDeg']:>12.5f}{r['verdict']:>10}")

    n_pass = sum(1 for r in results if r.get("verdict") == "varies")
    n_total = len(results)
    overall = "PASS" if n_pass == n_total else "PARTIAL"
    print("")
    print(f"OVERALL: {n_pass}/{n_total} clips pass threshold ({VARIES_THRESHOLD_DEG} deg) -> {overall}")
    print(f"FLOOR_SAME_TIME_REPEAT_DEG={floor_same_time!r}")

    out = {
        "thresholdDeg": VARIES_THRESHOLD_DEG,
        "nearLowCutoffDeg": NEAR_LOW_CUTOFF_DEG,
        "sampleCount": SAMPLE_COUNT,
        "windowSize": WINDOW_SIZE,
        "floorSameTimeRepeatDeg": floor_same_time,
        "results": results,
        "overall": overall,
    }
    print("RESULTS_JSON=" + json.dumps(out))


run()
