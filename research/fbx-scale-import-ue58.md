> # ⚠️ CORRECTION (2026-07-28) — §1d/§2.1/§3/§4/§6.1's "Interchange does not convert FBX units" is WRONG for 5.8. **Interchange normalises FBX scene units to centimetres, on both backends.**
>
> This report was written from web research (Epic docs, forums, issue tracker) without reading 5.8's Interchange source. Implementing the fix half of the import-scale check required that source, and it contradicts the report on one load-bearing point. **The original text below is retained unedited**, per this repo's correct-by-annotation rule; the affected sections carry inline pointers back here.
>
> ## What is actually true on 5.8 (engine source, verified twice, independently)
>
> | Source location | What it says |
> |---|---|
> | `Engine/Plugins/Interchange/Runtime/Source/Import/Public/Fbx/InterchangeFbxTranslator.h:52` | `bool bConvertSceneUnit = true;` — the translator-settings **default** |
> | `.../Import/Private/Fbx/InterchangeFbxTranslator.cpp:308` | `CacheFbxTranslatorSettings ? ...->bConvertSceneUnit : true` — also `true` when settings are absent |
> | `.../Parsers/Fbx/Private/FbxConvert.cpp:116-122` | `if (bConvertSceneUnit) { if (OriginalFileUnitSystem != FbxSystemUnit::cm) { FbxSystemUnit::cm.ConvertScene(SDKScene); } }` |
> | `.../Parsers/Fbx/Private/Ufbx/UfbxParser.cpp:122` | `LoadOpts.target_unit_meters = 0.01; // Centimeters` |
> | `.../Parsers/Fbx/Private/Ufbx/UfbxParser.h:98-103` | uFBX's `SetConvertSettings` **takes `InbConvertSceneUnit` and never assigns it.** It assigns three of its five parameters (`InbForceFrontXAxis`, `InbKeepFbxNamespace`, `InbConsiderClusterBeforePoseForMeshBindPose`); `InbConvertScene` and `InbConvertSceneUnit` are both dropped on the floor. On the uFBX backend the unit conversion is therefore **unconditional and not disableable**, not merely defaulted on. |
>
> `import_offset_uniform_scale` is a pipeline offset applied **on top of** that normalisation — the two **stack**. So the report's recommended fix (§3 "Safe to fully automate", §6.1: parse the header's `UnitScaleFactor`, feed it into `import_offset_uniform_scale`) would **double-apply the unit conversion and manufacture the exact 100x corruption the import-scale check exists to catch.** It was not implemented.
>
> **Scope of this correction:** it applies to the **Interchange** path (5.8's default) only. Nothing here is a claim about the legacy importer's "Convert Scene Unit" checkbox (§1d), which is a separate code path and is not what `AnimImportTools` drives.
>
> ## What survives unchanged
>
> The **"One-line verdict" in the header still holds, and arguably harder**: no shipped importer reliably auto-solves the Maya→UE scale problem. The reason is different from the one this report gives. Because 5.8 *does* convert on the header's authority, a self-consistent FBX — whatever unit it declares — imports correctly and needs no offset. The failure that survives is **§1a**: Maya stores geometry in centimetres regardless of the display unit, so a file whose declared unit disagrees with its geometry makes the importer's *own conversion* the error, by exactly `UnitScaleFactor`. Metadata alone cannot distinguish that from a legitimately-metric file — so the correction cannot be read from the header.
>
> Also untouched: §1a/1b/1c/1e/1f/1g/1h/1i root causes, §2.3–§2.6 post-import detection, §5 retarget interaction, and the entire flag-don't-fix scope boundary (§3 "Do NOT auto-fix", §6 "Judgment calls"). §2.1's pre-import header parse is **kept and shipped** — as the *detector* (and as the reader for baked/rig-intentional node scale), not as the source of the correction factor.
>
> ## What shipped instead — measure, correct, re-verify
>
> `UAnimImportTools::FixImportScale` (`Private/FbxScaleHeader.{h,cpp}` + `AnimImportTools.cpp`):
> 1. Parse the FBX header pre-import (binary + ASCII): declared unit, and every Model's `Lcl Scaling`.
> 2. **Flag-don't-fix runs first, pre-import.** Any non-identity node scale → `FLAGGED`, and *nothing is imported at all*.
> 3. Import at offset **1.0** (5.8 already did the unit conversion).
> 4. **Re-run the per-rig gate.** Clean → done.
> 5. If the gate reports its whole-rig **uniform-factor** signature — *measured* evidence, not a metadata guess — reimport at the reciprocal and gate again. Still dirty → `ERROR`, never a partial success.
>
> A correction is now **measured from what actually landed** rather than derived from metadata, which also makes it robust to *how* a file is mis-unit'd.

> # ⚠️ CORRECTION TO THE CORRECTION (2026-07-29) — the banner above names the wrong backend, and misses where the factor lands
>
> Two amendments to the 2026-07-28 banner. Neither reverses its verdict — Interchange **does** convert FBX scene units to centimetres, and feeding the header factor into `import_offset_uniform_scale` **would** double-apply it. Both are added rather than substituted; the banner above stands unedited. Full derivation, with engine-source line numbers for every claim: **`research/interchange-unit-scale-root-bone.md`**.
>
> ## A. "on both backends" is misleading — uFBX does not run
>
> The banner's table cites `UfbxParser.cpp:122` and `UfbxParser.h:98-103`. Those two rows are **true facts about a code path this project never enters.**
>
> | Source location | What it says |
> |---|---|
> | `.../Import/Public/Fbx/InterchangeFbxTranslator.h:64` | `bool bUseUfbxParser = false;` — `EditAnywhere`, **not** `config`; UI label "Use uFBX Translator (Experimental)" |
> | `.../Import/Private/Fbx/InterchangeFbxTranslator.cpp:311` | also `false` when translator settings are absent |
> | `.../Parsers/Fbx/Private/InterchangeFbxParser.cpp:57-64` | `if (bUseUfbxParser) { FUfbxParser } else { FFbxParser }` |
> | `.../Import/Private/Fbx/InterchangeFbxTranslator.cpp:85-88` | the InterchangeWorker path forces `EFBXParserType::FBX_SDK` regardless |
>
> No CVar or `.ini` key selects it (`grep bUseUfbxParser Engine/Config/*.ini` → nothing). **The operative backend is the Autodesk FBX SDK parser**, and the only unit scaling in that whole path is `FbxConvert.cpp:116-122` — which the banner already had right. Note its inner condition, `if (OriginalFileUnitSystem != FbxSystemUnit::cm)`: **a file declaring centimetres is never rescaled at all.** That turns out to be load-bearing (§C).
>
> As a bonus, `UfbxParser.cpp:106` sets `UFBX_SPACE_CONVERSION_ADJUST_TRANSFORMS`, explicitly *not* `TRANSFORM_ROOT` — so had uFBX been the live path, §B's root-scale signature would not obviously follow. Another reason to name the backend precisely.
>
> ## B. The conversion is a TRANSFORM, not baked data — and it lands on bone 0
>
> The banner says the conversion happens. It does not say **where the factor ends up**, and that omission is what made the fix path unreachable (below).
>
> `InterchangeSkeletonHelper.cpp:319-340`: only the bone with `ParentIndex == INDEX_NONE` is special-cased. With `bBakeMeshes` (default `true`, `InterchangeGenericAssetsPipelineSharedSettings.h:99`) it discards that bone's local transform and substitutes the composed **global** transform × the pipeline's `GlobalOffsetTransform`. Every other bone keeps its own local/bind-pose-local transform, untouched (`:342`). The result is written verbatim into the reference pose (`:76-84` → `ReferenceSkeleton.h:202-215`, which only calls `NormalizeRotation()` — **Scale3D is never stripped**).
>
> So a 100× mis-declared unit produces: **every `boneLength` unchanged** (measured: `pelvis` 95.92390282538743 on both sides), `boundsHeightUU` × 100, and `refPoseScaleViolations: ["root"]` — and nothing else. `ImportOffsetUniformScale` travels the identical route (`InterchangeGenericAssetsPipeline.cpp:1306-1314`), which is why the reciprocal cancels exactly, **and** why a non-1.0 offset on a clean file *creates* a root violation.
>
> Consequence the banner could not foresee: the per-rig gate reads that lone root violation as detection #3 of §2 (a rig-intentional scale) and refused to auto-correct — so on 2026-07-28 the fix half returned `FLAGGED` on a textbook unit mismatch. The banner's "what shipped instead — measure, correct, re-verify" was correct in *method* and unreachable in *practice* for the failure it was written for.
>
> ## C. What shipped 2026-07-29 — the declared unit as corroborator, not as the factor
>
> `ImportScaleCheck.cpp` now recognises the root-only shape as a whole-rig factor (bone lengths structurally *cannot* register a root scale, so demanding a second agreeing ratio demanded evidence the mesh cannot produce), and `ApplyScaleFix` corrects it on one condition: **the measured factor must equal the file's declared `UnitScaleFactor`** (`MotionTerminal::IsImporterInducedUnitConversion`).
>
> That is what separates the importer's own conversion from an artist's baked root scale, which produces the identical fingerprint. It works *because* of `FbxConvert.cpp:116-122`'s inner condition: a file declaring cm was never rescaled, so a root scale on one of those is the author's and still `FLAGGED` — which is exactly root cause **§1i** below, whose scale lives in the `FbxPose`/skin-cluster bind matrices (`FbxScene.cpp:520-552`) where the pre-import `Lcl Scaling` parse cannot see it.
>
> **The header factor is still never the correction.** It is measured post-import, as the banner above prescribes; the header only answers "who put this here". `FLAGGED` remains the verdict for an undeclared unit, a declared-cm file, a measured factor that does not match the declaration, and any drift that also moved a bone length or the bone count.
>
> Verified end to end on a real Interchange import: `FixImportScale` → `OK: applied 0.01`, `AssertImportScale` → `OK`.

---

# FBX Scale Import — UE 5.8 Findings (Maya → FBX → Unreal)

- **Date:** 2026-07-10
- **Purpose:** Design input for an MCP-driven "auto-handle FBX scale import" tool in the **MotionTerminal** plugin: the import-scale check and its tool surface.
- **Method:** Web research (Epic docs, Interchange docs, UE Python API, forums/issue tracker) + first-principles reasoning. No implementation code. Confidence ~93%.
- **One-line verdict:** Neither the legacy nor the current-default (Interchange) FBX importer reliably auto-solves the Maya→UE scale problem in 5.8 — a purpose-built **detect + assert + deterministic-fix** tool fills a genuine, currently-unaddressed gap. Auto-fix only the metadata-driven cases; detect-and-flag (never silently fix) baked non-uniform / rig-intentional scale.

---

## 1. Root causes

- **1a. Maya working units vs UE's fixed cm.** Maya stores geometry in **centimeters internally regardless of the display unit** (m/cm/in is a display convention). The FBX exporter's default Scale Factor is 1.0; with **Automatic** unit conversion it writes the host's `UnitScaleFactor` into FBX global settings. UE is hard-coded to 1 unit = 1 cm. If Maya's *display* unit is meters but conversion is off/misconfigured (or "File units converted to" is overridden), the embedded scale factor and mesh data disagree → 100x-too-large or 0.01x-too-small. ([Autodesk FBX Plug-in Guide](https://download.autodesk.com/us/fbx/2012/maya/files/GUID-C9BDF6BE-722E-4E97-8AD7-B1270331227-88.htm))
- **1b. The classic 100x / 0.01x factor** = 1a manifesting (meters-authored geometry vs UE cm). Why this exact ratio recurs constantly. ([forum 1](https://forums.unrealengine.com/t/imported-fbx-files-are-100-times-too-large/379868), [forum 2](https://forums.unrealengine.com/t/fbx-imports-at-1-100-scale-and-is-automatically-scaled-up-again/216538))
- **1c. Stale "Import Uniform Scale" (legacy).** A manual multiplier in `FbxMeshImportData`/`FbxSkeletalMeshImportData`; a stale non-1.0 value from a copy-pasted preset silently re-scales every later import. Symptom: right on first import, wrong on reimport.
- **1d. "Convert Scene Unit" (legacy only).** Checkbox to read FBX embedded unit metadata → cm. Long buggy (100x-too-large when enabled since ≥4.14, [UE-38672](https://issues.unrealengine.com/issue/UE-38672)); reimport resets the workaround. **Removed entirely from the Interchange UI in 5.5+** ([forum](https://forums.unrealengine.com/t/convert-scene-in-fbx-import-gone-in-5-5/2380073)) — replaced by a flat manual `Offset Uniform Scale` correction (a knob, not an auto-detector). **Interchange does not auto-read/convert FBX unit metadata.**
  > **⚠️ The final sentence is WRONG for 5.8 — see the correction banner at the top.** The *UI checkbox* is gone, but the translator converts anyway: defaulted-on on the FBX-SDK backend, unconditional on uFBX. Losing the checkbox is not losing the behaviour.
- **1e. Baked non-uniform joint scale.** Maya rig-hygiene issue (joints scaled directly, or inheriting non-uniform scale from constraints/drivers) bakes asymmetric scale into the FBX skeleton. Inflates every keyframe with scale channels; can skew collision/physics-asset gen or need "Preserve Local Transform". Fix upstream: `Modify > Freeze Transformations` before export. ([polycount](https://polycount.com/discussion/198738/non-uniform-scale-joint-fbx-export), [4.27 FBX anim docs](https://docs.unrealengine.com/4.27/en-US/WorkingWithContent/Importing/FBX/Animations))
- **1f. Namespace / root-node scale.** Top group/namespace root carrying leftover scale (e.g. a MetaHuman-style rig-scaling driver) exports scale on the root joint; imports geometrically fine but compounds retarget math. ([polycount](https://polycount.com/discussion/234224/scaling-and-freezing-a-skeleton-with-a-rig-driver-in-maya-metahuman))
- **1g. Skeletal vs static mesh import scale** — independent pipelines (`FbxStaticMeshImportData` vs `FbxSkeletalMeshImportData`); a preset fix on one doesn't carry to the other. Classic "props right, characters wrong."
- **1h. "Import Content Type" / skeleton mismatch on reimport** — reimporting against an existing Skeleton with `update_skeleton_reference_pose` mishandled, or GeoOnly/RigOnly against a differently-scaled ref pose, causes silent double-scaling / pose mismatch visible only in animation.
- **1i. T0 / reference-pose scale.** Bind/reference pose (`bindPose`) carrying scale independent of mesh export scale (rig scaled after skinning without clean re-bind). Mesh looks right, but the skeleton's **reference pose reports inconsistent scale — breaking retargeting math** (see §5) even though the character "looks fine."
  > **Load-bearing as of 2026-07-29 (see the second correction banner, §C).** This cause is the reason the import-scale check's auto-correct lane is gated on the *declared unit* rather than on "the pre-import parse found no `Lcl Scaling`". A 1i rig's scale lives in the `FbxPose` / skin-cluster bind matrices that Interchange prefers (`InterchangeSkeletonHelper.cpp:342`, `FbxScene.cpp:520-552`), **not** in any `Lcl Scaling` the header parser can read — so it is invisible pre-import and post-import it is indistinguishable from an importer-induced unit conversion. The declared unit is what tells them apart.

## 2. Detection (programmatic)

Reliability-ranked (authoritative reads > geometric inference):

1. **Pre-import: parse FBX header** `UnitScaleFactor` / `OriginalUnitScaleFactor`. If ≠ 1.0 (not cm), strong signal + exact correction factor. Most reliable pre-import signal (metadata from Maya's own exporter).
   > **⚠️ "exact correction factor" is WRONG for 5.8 — see the correction banner at the top.** The importer has already applied it, so feeding it back in double-applies. The parse itself is kept and shipped, as the *detector* (and as the reader for baked/rig-intentional `Lcl Scaling`); the correction is measured post-import instead.
2. **Post-import: import-options metadata audit.** `AssetImportData`/`InterchangeAssetImportData` stores the exact options used (`ImportUniformScale` / `import_offset_uniform_scale`). Assert == canonical value across the roster to catch stuck/stale options (1c/1d). Direct read, fully reliable.
3. **Post-import: reference-pose scale = 1.0 check.** Any bone with non-1.0 scale in `RefSkeleton` reference pose = unambiguous baked/non-uniform signal (1e/1i), independent of size.
   > **⚠️ "unambiguous" is WRONG for 5.8 — see the second correction banner, §B.** It is unambiguous that *a* scale is there; it says nothing about *who put it there*. UE 5.8 deposits its own FBX unit conversion on bone 0 and nowhere else, so a plain 100× mis-declared unit shows up here identically to a baked rig scale. Still a correct DETECTOR; not, by itself, an attribution.
4. **Post-import: mesh bounds vs expected height** (`ImportedBounds`) vs a known-good range (biped ~170–200 uu). Cheap; catches gross 100x/0.01x; needs a per-rig reference table; can't tell "legit giant" from "bug."
5. **Post-import: per-chain bone-length ratio** vs a reference skeleton of the same family — the *same metric IK Retargeter uses internally* (§5), so directly load-bearing for retarget, not just diagnostic.
6. **Cross-asset consistency** — compare a new import against already-verified roster members, not just one static reference.

## 3. Auto-fix — UE 5.8 Python surface

**Interchange (default in 5.8):**
- `unreal.InterchangeGenericAssetsPipeline.import_offset_uniform_scale` (float, r/w) — "Uniform scale offset applied to meshes and animations."
- `import_offset_translation` / `import_offset_rotation` companions.
- A **flat correction multiplier**, not auto-unit-detection — the tool must parse the FBX header (§2.1) and feed the factor in.
  > **⚠️ The clause after the dash is WRONG for 5.8 — see the correction banner at the top.** It is a flat multiplier, correctly; but it stacks *on top of* the translator's own unit conversion, so feeding the header factor in corrupts. Ship offset `1.0` and measure.

**Legacy (`Interchange.FeatureFlags.Import.FBX 0`):**
- `unreal.FbxSkeletalMeshImportData.import_uniform_scale` (r/w); `unreal.FbxMeshImportData.import_uniform_scale`; `update_skeleton_reference_pose`; driven via `unreal.AssetImportTask` + `asset_tools.import_asset_tasks(tasks)`. ([FbxSkeletalMeshImportData API](https://docs.unrealengine.com/5.4/en-US/PythonAPI/class/FbxSkeletalMeshImportData.html))

**Safe to fully automate (deterministic, lossless):** set the scale offset from a detected FBX-header unit mismatch before first import; re-import with corrected options when the metadata audit shows drift.
  > **⚠️ The first clause is WRONG for 5.8 — see the correction banner at the top.** "Set the offset from the header" double-applies the conversion. What IS safe to fully automate: import at `1.0`, let the per-rig gate measure the whole-rig drift, cancel it with the reciprocal, and re-gate. Deterministic and lossless, but the factor is measured, not read.

**Do NOT auto-fix (detect + flag, human/Maya-side):** baked non-uniform joint scale (1e — auto-"freezing" can alter intended proportions); rig-driver/build-customization root scale (1f — may be *intentional*); any scale change to an already-baselined asset without re-running the verification gate.

## 4. Existing tooling (5.8)

- **Interchange is the default FBX importer from UE 5.4+**; 5.8 adds an experimental **uFBX** backend for heavy files; FBX support still flagged **Experimental** in 5.8 Interchange docs.
- Legacy importer reachable via `Interchange.FeatureFlags.Import.FBX 0` ([post](https://impromptu.zone/t/disable-the-new-fbx-importer-in-ue-5-5/154)); people revert for real regressions (e.g. blendshape/morph splitting, [5.5.1 thread](https://forums.unrealengine.com/t/ue-5-5-1-import-problems-new-interchange-interface-imports-maya-single-fbx-animations-into-2-skeletal-and-morphs-why/2303777)).
- **No auto-unit-detection exists in either path today** — legacy's attempt (Convert Scene Unit) was buggy ([UE-38672](https://issues.unrealengine.com/issue/UE-38672)); Interchange removed it. Confirms the tool fills a real gap.
  > **⚠️ WRONG for Interchange on 5.8 — see the correction banner at the top.** Interchange detects and converts. The gap is real anyway, for a different reason: the conversion trusts a header that a misconfigured Maya export can contradict, so it converts *wrongly* and silently.

## 5. Retarget interaction (load-bearing)

- IK Rig + IK Retargeter handles "vastly different skeleton hierarchies **and proportions**" via **joint chains** not 1:1 bones. ([Retargeting Bipeds with IK Rig, 5.8](https://dev.epicgames.com/documentation/unreal-engine/retargeting-bipeds-with-ik-rig-in-unreal-engine))
- Core metric = **bone-length ratio** per chain (target/source) — compensates for proportion *if* the IK Rig has a full dedicated chain per limb. A minimal rig (root + one full-body chain) does not → correct rotations mapped to wrong endpoints (matches the Khaimera 3-vs-5 spine fixup).
- **Scale normalization is NOT an automatic step** — the retargeter exposes *manual* controls (`Scale Horizontal`, `Translation Offset Z`); no hidden pre-normalization pass in 5.8 docs.
- **Reference-pose match is the critical requirement**: "Make sure both characters have matching reference poses… you will need to adjust one of their retarget poses so they match." → **a scale-polluted reference pose (1i) silently corrupts retargeting even if chain-ratio logic is sound**, because the ratio is computed *from* that reference pose.
- **Project implication:** Compatible Skeletons (Manny/Quinn/MetaHuman) sidesteps scale-mismatch entirely (no retarget math — the cheap proof). The **IK Retargeter path to Khaimera** is exactly where a scale-polluted ref pose (1i/1f) masquerades as a "genuine proportion" bug — so **run scale-verify first**, then diagnose retarget-chain issues.

## 6. Buildable MCP-tool scope

**Cleanly automatable (detect + assert + fix):**
1. **Pre-import FBX unit-metadata scan** → compute factor → set `import_offset_uniform_scale`/`import_uniform_scale` via `AssetImportTask`. Deterministic, lossless.
   > **⚠️ The "compute factor" step is WRONG for 5.8 — see the correction banner at the top.** The scan shipped; the `AssetImportTask` + `import_offset_uniform_scale` mechanism shipped; the factor is taken from the per-rig gate's measured drift instead of from the header.
2. **Post-import bounds/bone-ratio sanity assert** — gross-error tripwire (100x/0.01x) vs per-rig reference; fails the import loudly. Fits the per-commit golden gate.
3. **Import-options metadata audit across the roster** — assert canonical scale option; auto-reimport on drift (stuck-option mode).
4. **Reference-pose non-uniform-scale detector** — flag (not fix) any non-1.0 ref-pose bone; hard-block retarget setup until resolved.

**Judgment calls — surface, do not auto-fix:** baked non-uniform joint scale (1e, Maya-side); rig-driver/build root scale (1f, may be intentional); IK Retargeter chain-length compensation itself (craft); establishing the per-rig "known-good" reference table.

**Recommendation:** a **pre-import FBX unit reader + post-import assertion/audit layer**, wired into the per-commit golden-diff gate as one more per-rig baseline check. Auto-fix only deterministic metadata-driven cases; everything touching baked non-uniform or rig-intentional scale is a **hard-fail-with-diagnostic**, not a silent correction — consistent with "scale-affecting changes must re-run verification."

## 7. Gaps / to-verify locally

- Epic publishes no canonical "correct" bone-length reference table or official scale-validation API — the §2 heuristics are first-principles-derived (none exists to confirm against; that's the gap the tool fills).
- Could not fully confirm `import_offset_uniform_scale`'s exact Python signature is unchanged 5.3→5.8 (versioned docs stop ~5.4). **Cheap local check before implementation:** `C:/Program Files/Epic Games/UE_5.8/Engine/Binaries/Win64/UnrealEditor-Cmd.exe <proj> -run=pythonscript` (or the in-editor Python API reference) to confirm `unreal.InterchangeGenericAssetsPipeline.import_offset_uniform_scale` and `unreal.FbxSkeletalMeshImportData.import_uniform_scale`.
  > **CLOSED** by `Scripts/archive/fbx_scale_signature_smoke.py` — both properties confirmed present and correctly typed on 5.8.
  >
  > **Method lesson (2026-07-28):** that smoke test confirmed the API *surface* and nothing about the importer's *behaviour*, and this report's one falsified claim was a behaviour claim. Confirming a property exists is not confirming what happens when you set it. The gap list should have carried a second item — "confirm whether Interchange converts scene units, by reading `FbxConvert.cpp`/`UfbxParser.cpp`" — which a `dir()` probe could never have answered. Read the source for behaviour; probe the API for signatures.
