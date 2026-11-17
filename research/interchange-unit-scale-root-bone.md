# Interchange FBX unit conversion → root-bone scale (UE 5.8)

- **Engine:** UE 5.8, CL 55116800, `C:/Program Files/Epic Games/UE_5.8/Engine`
- **Purpose:** verify or refute four load-bearing claims about how a mis-declared FBX unit header
  reaches the imported `USkeletalMesh` reference pose, and whether the import-scale check's
  `ApplyScaleFix` can be taught to auto-correct it. Determines whether a C++ tool changes or a
  documentation paragraph changes.
- **Method:** read UE 5.8 engine source directly (paths + line numbers below); read the repo's
  import-scale implementation directly; cross-check against the controlled header-patch measurement
  (§1c). Web research: none — deliberately.

## Verdicts

| # | Claim | Verdict |
|---|---|---|
| 1 | Unit conversion is materialized as a scale on the top/root node, not normalized invisibly | **PARTIAL** — right behaviour, wrong backend named, mechanism not source-verifiable |
| 2 | That scale lands on the `root` bone; bone lengths unscaled, only bounds move | **CONFIRMED** |
| 3 | A `root` entry in `refPoseScaleViolations` blocks `ApplyScaleFix` → `FLAGGED`, no bypass | **PARTIAL** — outcome confirmed, but it is one of *two* independent blockers |
| 4 | The pre-import/post-import discriminator's two halves are both available at the decision point | **PARTIAL** — one half is data, the other is only a caller invariant; and the discriminator is not sound in general |

---

## 0. Which code path actually runs (prerequisite for everything below)

The prior report `research/fbx-scale-import-ue58.md` and the code comment at
`Plugins/MotionTerminal/Source/MotionTerminal/Private/AnimImportTools.cpp:205-213` both lean on
`UfbxParser.h:98-103` ("uFBX drops `bConvertSceneUnit`, so the conversion is unconditional"). That
code fact is correct, and it is **about a backend that does not run by default.**

Backend selection:

| Location | Content |
|---|---|
| `Engine/Plugins/Interchange/Runtime/Source/Import/Public/Fbx/InterchangeFbxTranslator.h:64` | `bool bUseUfbxParser = false;` — `EditAnywhere`, **not** `config`, dialog label "Use uFBX Translator (Experimental)" |
| `.../Import/Private/Fbx/InterchangeFbxTranslator.cpp:311` | `const bool bUseUfbxParser = CacheFbxTranslatorSettings ? ...->bUseUfbxParser : false;` — also `false` when settings are absent |
| `.../Import/Private/Fbx/InterchangeFbxTranslator.cpp:389` | `FbxParser.Reset(bUseUfbxParser);` |
| `.../Parsers/Fbx/Private/InterchangeFbxParser.cpp:57-64` | `if (bUseUfbxParser) { … FUfbxParser … } else { … FFbxParser … }` |
| `.../Parsers/Fbx/Public/InterchangeFbxParser.h:39,136` | `Reset(bool bInUseUfbxParser = false);` / `bool bUseUfbxParser = false;` |
| `.../Import/Private/Fbx/InterchangeFbxTranslator.cpp:85-88` | when the InterchangeWorker process is used, the parser is forced to `EFBXParserType::FBX_SDK` regardless |
| `.../Import/Private/Fbx/InterchangeFbxTranslator.cpp:48-53` | `Interchange.FeatureFlags.Translator.UseWorker.FBX`, default `false` (in-process) |

No CVar or `.ini` key selects uFBX; grep of `Engine/Config/*.ini` for `bUseUfbxParser` returns
nothing. **The default 5.8 Interchange FBX path is the Autodesk FBX SDK parser (`FFbxParser`).**
`UAnimImportTools::FixImportScale` supplies only an `UInterchangeGenericAssetsPipeline` override
(`AnimImportTools.cpp:82-98`) and no translator settings, so it runs on the FBX SDK backend.

Unit conversion on that backend:

| Location | Content |
|---|---|
| `.../Import/Public/Fbx/InterchangeFbxTranslator.h:52` | `bool bConvertSceneUnit = true;` |
| `.../Import/Private/Fbx/InterchangeFbxTranslator.cpp:308` | `CacheFbxTranslatorSettings ? ...->bConvertSceneUnit : true` |
| `.../Parsers/Fbx/Private/FbxConvert.cpp:116-122` | `if (bConvertSceneUnit) { if (OriginalFileUnitSystem != FbxSystemUnit::cm) { FbxSystemUnit::cm.ConvertScene(SDKScene); } }` |

`FbxConvert.cpp:116-122` is the **only** unit scaling in the whole Interchange FBX SDK path. The
file's declared unit is otherwise captured as a diagnostic string only —
`FbxConvert.cpp:113-114` writes `FileUnitSystem`, and grep shows it is never read back to scale
anything.

## 1. Claim 1 — "conversion is materialized as a scale on the top/root node" — PARTIAL

Three separable sub-claims.

### 1a. "not normalized away invisibly" — CONFIRMED, but both readings are true

5.8 *does* normalize FBX scene units to centimetres. The prior report's correction banner is right
about that. What the banner does not say — and what is the actual finding here — is that the
normalization is **expressed as a transform in the scene graph**, not baked into vertex positions or
bone translations. So "Interchange normalizes FBX units to cm" and "the conversion shows up as a
node scale" are not in tension; the second is *how* the first is done.

### 1b. "the ufbx path does it on the root node" — REFUTED at source level

`Engine/Plugins/Interchange/Runtime/Source/Parsers/Fbx/Private/Ufbx/UfbxParser.cpp:106`

```cpp
LoadOpts.space_conversion = UFBX_SPACE_CONVERSION_ADJUST_TRANSFORMS;
```

with `UfbxParser.cpp:122` `LoadOpts.target_unit_meters = 0.01; // Centimeters`.

The bundled ufbx (0.21.2 — pinned at
`.../Parsers/Fbx/InterchangeFbxParser.Build.cs:77`) documents the enum at
`.../Parsers/ThirdParty/uFBX/0.21.2/ufbx.h:3618-3636`:

```
// Store the space conversion transform in the root node.
// Sets `ufbx_node.local_transform` of the root node.
UFBX_SPACE_CONVERSION_TRANSFORM_ROOT,

// Perform the conversion by using "adjust" transforms.
// Compensates for the transforms using `ufbx_node.adjust_pre_rotation` and
// `ufbx_node.adjust_pre_scale`. …
UFBX_SPACE_CONVERSION_ADJUST_TRANSFORMS,
```

UE picks `ADJUST_TRANSFORMS`, i.e. explicitly **not** the root-node option. So on the uFBX backend
the claim as worded is false. (Whether the observable ref-pose signature ends up the same anyway was
not measured — uFBX is off by default and the repo never exercises it. **Cannot verify** the uFBX
end-to-end ref-pose result.)

### 1c. "on the path that actually runs, the conversion lands as a node scale" — CONFIRMED behaviourally, mechanism NOT source-verifiable

The FBX SDK ships to UE as headers and binaries only —
`Engine/Source/ThirdParty/FBX/2020.2/` contains just `include/` and `lib/`, and a recursive search
for `*.cxx`/`*.cpp` under it returns nothing. `FbxSystemUnit::ConvertScene`'s body is therefore not
readable. The header (`.../include/fbxsdk/core/fbxsystemunit.h:116-139`) documents only *that* it
converts, plus `ConversionOptions` (`:35-66`, `mConvertRrsNodes = true` etc.). It does note that the
overload taking an explicit `FbxNode* pFbxRoot` — the historical "put the conversion on a dedicated
Fbx_Root node" idiom — "is provided for backwards compatibility only" (`:132-139`), and UE calls the
single-argument form. **Where inside `ConvertScene` the factor is deposited cannot be verified from
source.**

What *is* verifiable is everything downstream, plus a controlled measurement:

- Interchange reads node transforms straight out of the (already-converted) SDK scene, by
  `EvaluateGlobalTransform()` and parent-inverse composition —
  `.../Parsers/Fbx/Private/FbxScene.cpp:373-390` (non-joint) and `:594-615` (joint). Nothing here
  adds or removes scale.
- The controlled experiment: take one
  self-consistent centimetre export of `SKM_Manny_Simple`; patch **only** the binary header's
  `UnitScaleFactor` and `OriginalUnitScaleFactor` doubles from 1 to 100 in place (8 bytes, twice —
  node end-offsets untouched, geometry/skeleton/node transforms byte-identical). Clean file imports
  with `refPoseScaleViolations: []`; mis-declared file imports with `["root"]`,
  `boundsHeightUU` 180.54 → 18054.39 (exactly ×100), and **every** `boneLength` identical to the
  baseline to ~13 significant figures (`pelvis` 95.92390282538743 on both sides).

That isolates the unit declaration as the sole cause, and the result is only consistent with the
factor arriving as a **scale at or above the root joint** — a factor baked into descendant
translations would have moved `boneLengths`; a factor baked into vertices alone would not have
produced a ref-pose scale violation.

**Wording correction:** "on the top/root node" is too specific. The mechanism that makes
it *land on the root bone* (§2) composes every ancestor, so the accurate statement is "at or above
the root joint node".

## 2. Claim 2 — the scale lands on `root`; bone lengths unscaled — CONFIRMED

Source mechanism, all in the Interchange skeletal-mesh path:

- `Engine/Plugins/Interchange/Runtime/Source/Import/Private/Mesh/InterchangeSkeletonHelper.cpp:319-340`
  — **only** the bone with `ParentIndex == INDEX_NONE` gets special treatment. When `bBakeMeshes` is
  true it discards the joint's local transform and substitutes the composed **global** transform:

```cpp
if (ParentIndex == INDEX_NONE)
{
    …GetCustomGlobalOffsetTransform(GlobalOffsetTransform);
    …GetBakeMeshes(bBakeMeshes);
    if (bBakeMeshes)
    {
        LocalTransform = FTransform::Identity;
        ensure(SceneNode->GetCustomGlobalTransform(NodeContainer, GlobalOffsetTransform, LocalTransform));
        …GetBindPoseGlobalTransform(…)  /  GetTimeZeroGlobalTransform(…)
    }
}
```

- `bBakeMeshes` defaults to `true` —
  `.../Pipelines/Public/InterchangeGenericAssetsPipelineSharedSettings.h:99`.
- `GetCustomGlobalTransform` walks the whole scene-node ancestor chain and then post-multiplies the
  pipeline offset — `.../Nodes/Private/InterchangeSceneNode.cpp:324-336`
  (`OutAttributeValue *= GlobalOffsetTransform;`), ancestor walk at `:341-392`.
- Every other bone keeps its own local (or bind-pose-local) transform, untouched —
  `InterchangeSkeletonHelper.cpp:310` and `:342`
  (`Info.LocalTransform = bHasBindPoseTransform ? BindPoseLocalTransform : LocalTransform;`).
- The transform is written verbatim into the reference pose —
  `InterchangeSkeletonHelper.cpp:76-84` (`RefSkelModifier.Add(BoneInfo, BoneTransform);`) →
  `Engine/Source/Runtime/Engine/Public/ReferenceSkeleton.h:202-215`, which stores `BonePose` as-is
  and only calls `NormalizeRotation()`. **Scale3D is never stripped or normalized.**
- Interchange does create a scene node for the FBX scene root and captures its transform
  (`FbxScene.cpp:373-390` no-parent branch → local = global; `FbxScene.cpp:693-695`
  `SetCustomIsSceneRoot(true)`), so a scale on the FBX root node reaches bone 0 too, via the same
  ancestor walk.

So: any scale sitting at or above the root joint appears as **bone 0's ref-pose scale and nowhere
else**, and the mesh (baked through the same global transform) grows by the same factor. Exactly the
measured signature: `["root"]`, unchanged `boneLengths`, `boundsHeightUU` ×100.

Two qualifications on the claim's wording:

1. "the top node *is* the skeleton's own `root` bone" is not required. Because bone 0 receives the
   composed global transform, an FBX with an intermediate `Armature`/`geo_grp` pivot above the root
   joint produces the *same* signature.
2. If `bBakeMeshes` were false, none of this applies — bone 0 would get its plain local transform and
   neither the ancestor chain nor `ImportOffsetUniformScale` would reach the ref pose.

Also verified from source:
`ImportOffsetUniformScale` travels the identical route —
`.../Pipelines/Private/InterchangeGenericAssetsPipeline.cpp:1306-1314` builds it into
`GlobalOffsetTransform`, which `InterchangeSkeletonHelper.cpp:325-332` applies to bone 0 only.
**A non-1.0 import offset therefore *creates* a `root` ref-pose scale violation on an otherwise
clean file**, and 100 × 0.01 cancels exactly.

## 3. Claim 3 — `root` violation blocks `ApplyScaleFix` — PARTIAL

### The blocking chain is real and has no bypass

`Plugins/MotionTerminal/Source/MotionTerminal/Private/ImportScaleCheck.cpp:188-198`:

```cpp
if (Current->TryGetArrayField(TEXT("refPoseScaleViolations"), CurViol) && CurViol && CurViol->Num() > 0)
{
    …
    Fails.Add(…"ref-pose scale violation(s) on: %s …"…);
    Summary.bNonUniformEvidence = true;
}
```

`ImportScaleCheck.cpp:329-335`:

```cpp
if (!Drift.bUniformFactor || Drift.bNonUniformEvidence)
{
    return FString::Printf(TEXT("FLAGGED: … not a clean whole-rig unit mismatch …"), …);
}
```

- The name `"root"` is **not** special-cased anywhere. Any non-empty array blocks.
- `bNonUniformEvidence` is initialised `false` (`ImportScaleCheck.h:50`) and is only ever set to
  `true` — grep across `ImportScaleCheck.cpp` shows no assignment to `false` and no clear. No CVar,
  no parameter, no override. **No bypass exists.**
- Only the **current** report's violations are read (`ImportScaleCheck.cpp:190` reads `Current`), so
  a baseline that itself carries a `root` violation does not cancel it.
- The pre-import gate cannot have fired first for this case: `ParseFbxScaleMetadata` finds no
  non-identity `Lcl Scaling` in a header-only-patched file, so `AnimImportTools.cpp:243-251` passes
  through and `ApplyScaleFix` is reached.

### But it is not the only blocker — this is the part the claim misses

For a root-only violation, **every per-bone length ratio is inside tolerance**, so `ViolatingRatios`
receives exactly one entry: the bounds-height ratio (`ImportScaleCheck.cpp:118-127`). That hits:

`ImportScaleCheck.cpp:282-285`

```cpp
else if (ViolatingRatios.Num() == 1)
{
    Summary.bNonUniformEvidence = true;
}
```

and `bUniformFactor` is never set, because the common-factor branch requires
`ViolatingRatios.Num() >= 2` (`:260`, with the rationale spelled out at `:258-259`: "one data point
is a measurement, not a whole-rig factor").

So `ApplyScaleFix` is blocked **twice, independently**: `bNonUniformEvidence == true` *and*
`bUniformFactor == false`. Either alone trips `:329`.

Empirical confirmation that `bUniformFactor` was indeed false: the shipped `FLAGGED` output contains no leading `uniform factor ~100 -- unit mismatch`
line, which `ImportScaleCheck.cpp:274-276` would have inserted at index 0 had the flag been set.

**Consequence for any fix:** removing the ref-pose-violation blocker alone would not unblock the
auto-correct lane. The single-violating-ratio rule would still veto it, and there is no measured
whole-rig factor to invert — the only ratio available is the bounds ratio.

## 4. Claim 4 — is the discriminator wired, or does it need plumbing? — PARTIAL

### Half B (post-import root violation) — available at the decision point

`ApplyScaleFix` holds `First` (the current fingerprint JSON) in scope from
`ImportScaleCheck.cpp:311` through the decision at `:329`, so
`First->GetArrayField(TEXT("refPoseScaleViolations"))` is readable there today. What is **not**
available is a structured form: `FScaleDriftSummary` (`ImportScaleCheck.h:32-53`) collapses all
violations into one bool plus a prose line in `Diff`. Nothing distinguishes "root-only" from
"violations scattered through the chain" without either re-reading `First` or parsing `Diff` text.

### Half A (pre-import parse proved no authored node scale) — NOT available as data

`ApplyScaleFix`'s signature (`ImportScaleCheck.h:83-84`, definition `ImportScaleCheck.cpp:306-307`):

```cpp
FString ApplyScaleFix(const FString& RigName, const FString& UnitText,
    const TSharedRef<FJsonObject>& Baseline, FImportAndFingerprintFn ImportAndFingerprint);
```

`FFbxScaleMetadata` does not cross that boundary. The sole production caller passes
`Meta.DescribeUnit()` — a **rendered string** ("100 (m)", "undeclared") — at
`AnimImportTools.cpp:268`. `Meta.ScaledNodes`, `Meta.UnitScaleFactor`, `Meta.bHasUnitScaleFactor`,
`Meta.HasNonUniformNode()` are all left behind. The header comment at `ImportScaleCheck.h:80-82`
states this as a deliberate design choice ("passing the text rather than the metadata keeps this
header independent of the FBX reader").

The information exists as a **control-flow invariant of one caller**:
`AnimImportTools.cpp:243-251` early-returns `FLAGGED` whenever `Meta.ScaledNodes.Num() > 0`, so
reaching `ApplyScaleFix` implies "no Model in the file carried non-identity `Lcl Scaling`". But the
invariant is nowhere expressed at the seam, and the headless tests call `ApplyScaleFix` directly with
arbitrary `UnitText` and no metadata at all (`Private/Tests/ImportScaleTests.cpp:441, 455, 478, 491,
501, 579, 603, 616`).

**Verdict: wiring the discriminator requires plumbing that does not currently exist** — a signature
change on `ApplyScaleFix` (add `FFbxScaleMetadata` or a narrow `bool bNoAuthoredNodeScale`), plus
surfacing the violating-bone list on `FScaleDriftSummary`, plus a new classification branch, plus
relaxing the `ViolatingRatios.Num() >= 2` rule for this shape (see §3). Each is small; there are four
of them, and two touch a published struct.

### The discriminator is also not sound as stated

Independent of plumbing: "a pre-import parse proved no node scale was authored" is **not** equivalent
to "no author-induced root scale exists".

- The parser reads exactly one thing: `Lcl Scaling` on `Model` records —
  `FbxScaleHeader.cpp:101-108` (`ApplyPropertyRecord`), `:43-52` (`RecordScaledNode`),
  `:233-247` (binary `Model` / `P` dispatch), `:356-378` (ASCII). Cameras and lights are excluded
  (`:38-41`).
- But Interchange **prefers the bind-pose local transform** for every bone:
  `InterchangeSkeletonHelper.cpp:342`
  `Info.LocalTransform = bHasBindPoseTransform ? BindPoseLocalTransform : LocalTransform;`
  and that value comes from the FBX `FbxPose` / skin-cluster bind matrices, not from `Lcl Scaling` —
  `FbxScene.cpp:520-552` (`GetGlobalJointBindPoseTransform` → `SetBindPoseLocalTransform`).

A rig scaled after skinning without a clean re-bind therefore produces a `root` ref-pose scale with
**no** `Lcl Scaling` anywhere in the file. That is root cause **1i** (reference-pose scale), named in
this repo's own research at `research/fbx-scale-import-ue58.md` §1i. Such a file satisfies both halves
of the discriminator and would be misclassified as importer-induced, then "corrected" by an import
offset — silently rewriting a rig, which is precisely the outcome the import-scale check's
flag-don't-fix scope boundary exists to prevent.

The discriminator is sound only against **`Lcl Scaling`-authored** node scale. To be sound against
author-induced scale generally it would additionally need the bind-pose matrices read pre-import
(not currently parsed), or a post-import check that the root violation's factor matches the header's
declared `UnitScaleFactor` — the latter is cheap and available (`Meta.UnitScaleFactor` exists at
`AnimImportTools.cpp:234`), and would be a materially stronger test than "no `Lcl Scaling`".

### Would the fix actually work if unblocked?

Yes, for this specific shape, and it is already measured. `import_offset_uniform_scale` composes into
the same `GlobalOffsetTransform` that the mis-declared unit's scale sits alongside
(`InterchangeGenericAssetsPipeline.cpp:1306-1314` → `InterchangeSkeletonHelper.cpp:325-332`), so
100 × 0.01 = 1 exactly and the re-gate comes back clean:
`manny_declared_metres.fbx` @ 0.01 → `boundsHeightUU` 180.5439
against a 180.54390167817473 baseline, `refPoseScaleViolations: []`, `AssertImportScale` → `OK`.

The corollary recorded during that measurement still holds and is worth
carrying into any design: because a non-1.0 offset *always* shows up as a root ref-pose scale, the
post-fix re-gate only passes when the original drift carried a cancelling root scale. The auto-fix
lane is therefore correct for exactly the unit-mismatch shape and would fail its own re-gate for any
drift baked into bone translations.

## 5. What should change

Stated as findings, not as a decision — the disposition is a design call.

1. **Report correction (definitely).** Both `research/fbx-scale-import-ue58.md` (correction
   banner, table rows 3-4) and the comment block at `AnimImportTools.cpp:200-219` cite
   `UfbxParser.h:98-103` as if uFBX were the operative backend. It is not
   (`InterchangeFbxTranslator.h:64`, `InterchangeFbxParser.cpp:57-64`). The operative fact is
   `bConvertSceneUnit = true` + `FbxConvert.cpp:116-122` on the FBX SDK backend. The uFBX line is a
   true statement about a path that does not run, and it should be labelled as such rather than
   deleted. Add: the conversion is expressed as a scene-graph transform, not baked into vertex or
   bone data.
2. **`research/fbx-scale-import-ue58.md` §1d** still carries the stale "Interchange does not
   auto-read/convert FBX unit metadata" claim — noted there as deferred, still open.
3. **If the C++ tool changes**, the minimum honest change is *not* "ignore a `root` violation". It is
   four coordinated edits (§4) plus a soundness test stronger than "no `Lcl Scaling`" (§4, last
   paragraph) — and it narrows the import-scale check's flag-don't-fix boundary, which is the reason
   the controlled measurement deliberately left `ImportScaleCheck.cpp` untouched.

## 6. Explicitly not verified

- The internals of `FbxSystemUnit::ConvertScene` — FBX SDK 2020.2 ships to UE as headers + libs only
  (`Engine/Source/ThirdParty/FBX/2020.2/{include,lib}`). Claim 1's *mechanism* rests on a controlled
  measurement plus verified downstream source, not on reading the converter.
- The uFBX backend's end-to-end ref-pose signature for a mis-declared unit. Source says it uses
  `ADJUST_TRANSFORMS` rather than `TRANSFORM_ROOT`; what that produces in `RefSkeleton` was never
  measured, because uFBX is off by default and the repo never enables it.
- Whether `CalculateCompleteSceneNodeTransform` (`.../Import/Private/Mesh/InterchangeMeshHelper.cpp`,
  ~`:995-1039`) double-applies the global offset to skinned vertices. Not traced; not needed for any
  of the four claims, since the measured bounds ratio was exactly 100 with no residual.
