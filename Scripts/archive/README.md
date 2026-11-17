# Scripts/archive

Scripts kept for the **provenance of recorded results**. Each one produced, or proved, a number or a finding
that a research note, design doc or recorded measurement still relies on, so the method stays in the repo next to the claim.

They are **not part of the live toolset**. Nothing in `Scripts/` imports or runs them, no gate depends on them,
and they are not kept in step with the live code. Rerunning one may need path or API tweaks: they were written
against the repo and the editor as they stood on the date below.

Paths that still work:

- `army_budget_probe.py` execs the live `Scripts/army_spawn.py`. Its `__file__` branch climbs from
  `Scripts/archive/` to `Scripts/`, and its exec'd-from-`run_python` branch already resolves
  `<project>/Scripts`. `namespace_merge_check.py` scans `Scripts/archive/*.py` too, so a live rename in
  `army_spawn.py` that would collide with this merge is still caught.
- `pie_aimnull_ab_drive.py` and `pie_aimgate_verify_drive.py` put `<project>/Scripts` on `sys.path` (from
  `unreal.Paths.project_dir()`, not from their own location) before importing `mt_drive`, so they still find it.
- To exec any of them in the editor, use `.../Scripts/archive/<name>.py` as the path.

| File | What it produced or proved | Cited at (doc:line) | Date written |
|---|---|---|---|
| `khai_dyn_visibility.py` | Whether the ON-vs-OFF cloth difference shows at the planned shot framing (>= 12 px on 3 of 6 chains; the pass rule was fixed before the measurement). Measured PASS. | `research/ue58-rigging-tools-assessment.md:299`; `docs/specs/2026-10-28-dynamics-hero-agnostic-pipeline-design.md:20` | 2026-10-25 |
| `pawn_base_migrate.py` | Reparented the roster pawns onto `AMTCharacter` (budgeted mesh component) and diffed a before/after property snapshot to prove nothing else moved. | `docs/specs/2026-10-29-hero-onboarding-design.md:12` | 2026-10-19 |
| `pie_capture_drive.py` | PIE visual-capture attempt (movement plus tracking/side camera). Later found mis-aimed by the `Rotator` argument-order bug. | no document; result recorded when the probe ran | 2026-07-13 |
| `pie_scenecapture_drive.py` | Viewport-independent SceneCapture2D capture attempt. Same `Rotator` mis-aim. | no document; result recorded when the probe ran | 2026-07-13 |
| `pie_montage_drive.py` | Montage seamlessness effect probe (`MSEAM` lines, inertial vs hard cut). Its header is why `army_conductor.py` takes no seamlessness figure. | `Scripts/army_conductor.py:110` | 2026-07-20 |
| `pie_aimnull_ab_drive.py` | Established the aim-offset neutrality defect: AO_Rifle at AimPitch 0 vs absent moves thigh_r 37.1 deg and foot_r 21.8 deg. | `research/aim-offset-neutrality-defect.md:80`; `Plugins/MotionTerminal/Source/MotionTerminal/Private/Tests/PieTests.cpp:2959` | 2026-08-07 |
| `pie_aimgate_verify_drive.py` | Verified the `AimOffsetAlpha` gate in both directions (later withdrawn as "proven"; not a gate, no threshold). | `research/aim-offset-neutrality-defect.md:170`, `:182`, `:185`, `:362`; `Scripts/pie_riflegate_verify_drive.py:4` | 2026-08-07 |
| `pie_footlog_drive.py` | Foot world-Z logger at matched pawn X (superseded, confounded metric). | no document; result recorded when the probe ran | 2026-07-13 |
| `pie_ikbone_probe.py` | `ik_foot` vs deform-foot displacement probe (`foot_l == ik_foot_l` at every sample). | no document; result recorded when the probe ran | 2026-07-13 |
| `pie_rigvar_probe.py` | Live ControlRig variable probe (could not read the live `UControlRig`). | no document; result recorded when the probe ran | 2026-07-16 |
| `pie_footclear_drive.py` | Ground-relative foot clearance A/B by raycast: the shipped proof and acceptance meter for foot IK, and the template the later PIE effect probes were modelled on. | no document; result recorded when the probe ran | 2026-07-13 |
| `pie_khai_weaponverify_drive.py` | Khaimera weapon read-back and off-hand arm-chain resolve (`WVERIFY` lines). | no document; result recorded when the probe ran | 2026-07-18 |
| `fbx_scale_signature_smoke.py` | Confirmed the UE 5.8 import-scale Python surface (`import_offset_uniform_scale`, `import_uniform_scale`), closing the open signature question in the FBX scale research note. | `research/fbx-scale-import-ue58.md:163` | 2026-07-16 |
| `army_budget_probe.py` | Army N-vs-eval-ms sweep (`research/fixtures/army-contact/n_vs_eval_ms.csv`). Its first sweep measured a standing crowd by mistake (it never starts the conductor); the shipped numbers are a driven re-measure. | `Plugins/MotionTerminal/Tests/army_config.json:6`, `:71`; `docs/verification-design.md:122` | 2026-07-23 |

Line numbers are as of the archive move (2026-10-29) and drift as those documents change.
