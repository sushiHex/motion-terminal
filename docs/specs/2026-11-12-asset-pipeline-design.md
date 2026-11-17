# The asset pipeline: target architecture

Status: built, all four layers (`Scripts/editor_ops.py`, `Scripts/asset_pipeline.py`, `Scripts/stage_runner.py`,
`Scripts/mt.py`). First live run on UE 5.9 (2026-11-13): hero onboarding passed all nine stages through them, and the
dynamics graph built and matched its spec.

## 1. The problem this solves

The project grew one script per job. `Scripts/` holds over a hundred files in one flat folder; code that works for
any hero is still named after the first one; each builder script carries its own copy of the same guards (delete
first, check git-ignore, verify, save, clean up after a failure); and steps hand results to each other by printing
lines and parsing them back. It works, and it does not scale to the thing the project is for: taking a legacy rig
and upgrading it to current-engine features with no hand work, for any number of rigs, with every step checked.

## 2. Thesis

**An agent upgrades legacy rigs to current-engine features through the engine's MCP server, and every step is
declared, built, read back and gated.** Verification is the guardrail that makes automation at that scale
trustworthy, not a separate product.

## 3. Four layers

```
4  operations     one entry point per hero: onboard, build, run, render, verify          (to build)
3  stages         a hero's assets in dependency order, each with inputs, outputs, a record (to build)
2  realize        one sequence for any generated asset                                    (built: asset_pipeline.py)
1  tools          the plugin's toolsets and the engine's own API, over one transport      (built)
```

### Layer 1: tools and transport (built)

`docs/architecture.md`. One MCP server, one transport (`Scripts/editor_ops.py`), engine routes first, own tools
where the engine has none, and a read side that shares no code with the write side.

### Layer 2: realize (built)

`asset_pipeline.realize(ops, kind, path, spec)` is the only way a generated asset gets made:

    refuse a tracked target -> delete -> build -> describe -> mismatch -> save if nothing differs -> record

A **kind** is four functions (`build`, `describe`, `mismatch`, `save`). The order, the guards and the cleanup live
in `realize`, so no script can skip the delete, save something that is not its spec, or leave a failed build
behind. Adding an asset kind means writing one `Kind`; nothing else changes.

Kinds: AnimGraph (built). To register as their builders land on this layer: blend space, physics asset, IK rig,
retargeter, Control Rig.

The record `{kind, path, spec_digest, described}` is what layer 3 stores.

### Layer 3: stages (to build)

A hero's onboarding is a short dependency graph, not a script:

    intake -> ik_rig -> retargeter -> bake -> blend_space -> locomotion_abp -> in_place -> pawn
           -> physics -> dynamics_rig -> dynamics_abp

Each stage declares what it reads (the hero profile, upstream records) and what it realizes. Its record is keyed by
the digest of its spec and of its upstream records, so:

- an unchanged stage is skipped, and a changed one invalidates exactly what depends on it;
- a failed run resumes at the failed stage;
- a record says what an asset was built from, which today only the dynamics manifest does.

Stages return values. No stage prints a line for another to parse.

#### What a record does and does not prove

These are accepted limits, stated so a green record is not read as more than it is:

- A stage's key covers its declared inputs and the keys of the stages it needs. It does not cover the contents of
  the assets at those paths, the scripts that build them, or the engine build. `--resume` is for continuing a run
  that failed; it does not detect an asset edited by hand, a changed script or a new engine.
- A stage kept by `--resume` is not checked again until verify. Verify always runs (its key carries an id made on
  every call), as does the physics stage, whose result depends on sampled animation no declared input stands for.
- A build deletes before it builds. A rebuild that fails leaves the hero without that asset until the next run that
  succeeds.
- The guard against deleting an asset something outside the hero's folder references reads saved references (the
  asset registry), not live unsaved ones.
- The motion check on the bake is an amplitude lower bound: a limb must swing at least a share of what its source
  swings, where the source swings at all. It does not see timing, direction, or a limb that swings too much.

### Layer 4: operations (built: `Scripts/mt.py`)

One entry point, the same for every hero:

    python Scripts/mt.py editor start | stop
    python Scripts/mt.py doctor              checks on the running editor before a long run
    python Scripts/mt.py onboard <hero>      every stage, resumable
    python Scripts/mt.py status  <hero>      where the hero stands (no editor)
    python Scripts/mt.py build   <hero>      the dynamics rig and graph
    python Scripts/mt.py run     <hero> ...  the gate drive
    python Scripts/mt.py render  <hero> ...  the review clip
    python Scripts/mt.py verify              the two test tiers

Each operation runs the script that does the work as its own process, arguments passed through, so this file is a
list of what the project can do and nothing else. The same operations are what an agent calls. Still to do:
publishing them as an agent skill in the engine's own format, so "onboard this hero" is one documented capability
with its guardrails stated, not a sequence an agent has to rediscover.

## 4. The review loop

Green gates have passed shots a person rejected on sight, so the person's judgement is part of the pipeline, not
after it. `mt render` produces a short clip and a contact sheet per hero; a review page records pass or fail per
shot with the reason; a rejected defect class (a stuck foot, a broken wrist) becomes a detector, and that detector
becomes a gate. The record of rejections is the roadmap for the gates.

## 5. Order of work

1. Finish moving builders onto layer 2 (the dynamics graph first, then onboarding's kinds).
2. Layer 3 for onboarding, since it is the longest sequence and the one that resumes worst today.
3. Layer 4's `onboard` and `render`, then the review page.
4. Rename the hero-agnostic modules that still carry the first hero's name, and move `Scripts/` into packages by
   layer. Last, because it touches every file and adds no behaviour.

Each step is usable on its own; none needs the ones after it.

## 6. What this deliberately leaves alone

- The test tiers and golden baselines: they are the gate and already independent of authoring.
- The in-editor drivers (the PIE drives, the conductor): they run inside one PIE session and are not asset builds.
- A scheduler, parallel stages, a database: one hero onboards in minutes; a file of records is enough.
