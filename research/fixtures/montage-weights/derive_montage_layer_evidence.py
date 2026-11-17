#!/usr/bin/env python
"""Recompute render_evidence.json's `montage_layer` figures from the shipped render's own log.

WHY THIS FILE EXISTS. The montage-layer take's caption ("locomotion never flinches") is backed by numbers in
Plugins/MotionTerminal/Tests/render_evidence.json. Three of them — the two-witness agreement figures —
could not be recomputed by a reader, because the driver that produced the shipped render collected
both slot readings per sample but never compared them, so the comparison was done after the fact
against Saved/Logs/MotionTerminal.log: a gitignored, rotating file. The evidence entry stated that
provenance honestly, but honest sourcing is not the same as reproducibility, and the whole point of
render_evidence.json is that a stale claim goes RED rather than merely being explained.

So the backing data is committed next to this script (montage-shipped-render.log, the driver's own
MBEAT lines lifted out of that render's log with the UE timestamp prefix stripped and nothing else
changed), and this script re-derives every montage_layer figure from it and CHECKS them against the
committed evidence file. Run it and the claim either reproduces or fails loudly:

    python research/fixtures/montage-weights/derive_montage_layer_evidence.py

Scripts/pie_montage_layer_drive.py now performs this comparison in-band and reports it on its
MBEAT RESULT line, so newer renders carry the figures without this step.

THE LOG WAS RE-CAPTURED, AND ONE FIGURE GOT MORE HONEST. The montage-layer take was re-shot to hide a cube stand-in
prop, so the render this file describes was replaced and the committed log had to be replaced with it
or it would describe footage that no longer exists. EVERY figure reproduced identically
(288 / 138 / 1.0 / 1.0 / 1.0 / 0 / 0). The new log is the first one to carry the in-band RESULT line,
which reports `slot_delta_max=0.00041` where the recomputation from the printed samples gives 0.000 —
the two witnesses agree to about four decimals, not exactly, and the older figure was an artefact of
the sample lines printing to three. Both are now derived and both are checked.

Stdlib only. Exit 0 = every figure reproduces.
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
LOG = HERE / "montage-shipped-render.log"
EVIDENCE = REPO / "Plugins/MotionTerminal/Tests/render_evidence.json"

# Mirrors Scripts/pie_montage_layer_drive.py's constants. Duplicated deliberately: this script must be
# able to falsify the driver, so it cannot import its thresholds from it.
SLOT_AGREE_TOL = 0.001
SLOT_OWNED_MIN = 0.5

SAMPLE = re.compile(
    r"MBEAT t=(?P<t>[\d.]+) loco_sm=(?P<sm>[\d.]+) loco_state=(?P<state>\S+) "
    r"upper_slot=(?P<slot>[\d.]+) slot_api=(?P<api>-?[\d.]+)")

# The driver's own end-of-take line. Everything above is recomputed from the per-sample lines; this one
# figure CANNOT be, and pretending otherwise was an understatement — see the key's comment below.
RESULT_DELTA = re.compile(r"MBEAT RESULT .*? slot_delta_max=(?P<d>[\d.]+)")


def derive():
    text = LOG.read_text(encoding="utf-8")
    rows = [m.groupdict() for m in SAMPLE.finditer(text)]
    if not rows:
        raise SystemExit("no MBEAT sample lines in %s" % LOG)

    owned = [r for r in rows if float(r["slot"]) >= SLOT_OWNED_MIN]
    api_ok = [r for r in rows if float(r["api"]) >= 0.0]
    deltas = [abs(float(r["slot"]) - float(r["api"])) for r in api_ok]

    # The two witnesses are printed to THREE DECIMALS on every sample line, so a delta recomputed from
    # that text is quantised to 0.001 and reads 0.000 whenever the two values round the same way. The
    # driver computes the same quantity in full float precision before printing and reports it once, on
    # its RESULT line. Both are recorded: the recomputed one is what a reader can re-derive here, the
    # in-band one is what actually happened. Reporting only the first states perfect agreement that was
    # never measured; reporting only the second gives up the reproduction. Note SLOT_AGREE_TOL is 0.001,
    # so the disagreement COUNT above is unaffected either way.
    m = RESULT_DELTA.search(text)
    if m is None:
        raise SystemExit("no `MBEAT RESULT ... slot_delta_max=` line in %s — the log predates the "
                         "in-band comparison; re-capture it from a render after 833646f" % LOG)

    return {
        "slot_witness_max_abs_difference_inband": float(m.group("d")),
        "mid_montage_samples": len(rows),
        "mid_montage_samples_slot_owned": len(owned),
        # The minima are over the SLOT-OWNED subset, not over every sample — the distinction an
        # earlier revision of the evidence entry got wrong (it quoted the 288 for both).
        "locomotion_sm_absweight_min": min(float(r["sm"]) for r in owned),
        "locomotion_state_absweight_min": min(float(r["state"]) for r in owned
                                              if r["state"] != "NA"),
        "upperbody_slot_weight_max": max(float(r["slot"]) for r in rows),
        # Quantised to the 0.001 the sample lines print at — a LOWER BOUND on the true spread. The
        # full-precision figure is slot_witness_max_abs_difference_inband, above.
        "slot_witness_max_abs_difference": max(deltas) if deltas else -1.0,
        "slot_witness_disagreements": sum(1 for d in deltas if d > SLOT_AGREE_TOL),
        "slot_api_read_failures": len(rows) - len(api_ok),
    }


def main():
    got = derive()
    want = json.loads(EVIDENCE.read_text(encoding="utf-8"))["montage_layer"]

    bad = []
    print("%-38s %12s %12s" % ("key", "derived", "evidence"))
    for k, v in sorted(got.items()):
        ref = want.get(k, "<absent>")
        ok = isinstance(ref, (int, float)) and abs(float(ref) - float(v)) <= 1e-9
        print("%-38s %12s %12s  %s" % (k, v, ref, "ok" if ok else "MISMATCH"))
        if not ok:
            bad.append(k)

    if bad:
        print("\nFAILED: %d figure(s) in render_evidence.json do not reproduce from the shipped "
              "render's log: %s" % (len(bad), ", ".join(bad)))
        return 1
    print("\nOK: every montage_layer figure reproduces from %s" % LOG.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
