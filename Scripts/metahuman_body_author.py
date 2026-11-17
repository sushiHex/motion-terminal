# Parametric MetaHuman BODY authoring over Python (no Creator GUI clicks).
#
# WHY THIS EXISTS. The roster's "one Template, many proportions" claim is only as strong as the spread of
# bodies it runs on, and the shipped roster is weaker than it looks: Manny 180.5 cm and Quinn 180.2 cm are
# the SAME SIZE (0.4 cm apart, measured live off SKM_*_Simple bounds), so the proportion claim rests almost
# entirely on Khaimera at 209.9 cm. Nothing in the roster is under 180. This tool authors the missing short
# body parametrically, so the target proportions are a committed CONFIG rather than an unrecorded afternoon
# of dragging sliders — re-runnable, diffable, and reviewable like every other authored mechanism here.
#
# API PROVENANCE (not reverse-engineered): Epic ships the workflow at
#   Engine/Plugins/MetaHuman/MetaHumanCharacter/Content/Python/examples/example_{create_asset,sculpt_body,assembly}.py
#   and .../test_sculpt_character.py
# This file follows those examples; deviations are commented where they occur.
#
# GOTCHA-MH-A (cost one editor crash to learn): `unreal.new_object(unreal.MetaHumanCharacter)` produces an
# object with no internal body state, and handing THAT to `try_add_object_to_edit` trips
# `Assertion failed: IsValid()` in SharedPointer.h:1133 — a hard crash, not a Python exception. The character
# MUST come from `AssetToolsHelpers.get_asset_tools().create_asset(..., factory=MetaHumanCharacterFactoryNew)`
# (or `load_asset` of one already created that way). Never construct one directly.
#
# GOTCHA-MH-B: the edit scope is a real resource — `try_add_object_to_edit` fails if the asset is already
# open in the Creator UI, and a scope left open blocks later runs. Every path here closes it in `finally`.
#
# Usage (run in the editor through `Scripts/editor_ops.py` (`ops.run_file`)):
#     MH_ACTION = 'author'   # 'probe' | 'author' | 'build'
#     exec(open(r'.../Scripts/metahuman_body_author.py').read())
import json

import unreal

PKG_PATH = '/Game/MetaHumans'          # gitignored (see .gitignore) — generated bodies stay machine-local
ASSET_NAME = 'MH_MT_Roster'

# The authored target. Measurements are centimetres; the unitless dials (Fat / Muscularity /
# Masculine-Feminine) are the engine's own normalized ranges, read live in `probe`.
#
# HEIGHT 158.0 is chosen against MEASURED roster values, not taste: Manny 180.5 / Quinn 180.2 / Khaimera
# 209.9. At 158 the new body sits ~22 cm under the mannequin pair and ~52 cm under Khaimera, so the line-up
# reads short -> medium -> medium -> tall instead of the current medium/medium/tall. A mid-height MetaHuman
# would add a fourth body to the lineup take and no additional evidence.
BODY_TARGET = {
    'Height': 158.0,
    'Fat': 0.9,           # softer silhouette than the athletic mannequin default
    'Muscularity': -0.4,  # away from the mannequins' uniformly athletic build
}


def _subsystem():
    return unreal.get_editor_subsystem(unreal.MetaHumanCharacterEditorSubsystem)


def _obj_path():
    return '%s/%s.%s' % (PKG_PATH, ASSET_NAME, ASSET_NAME)


def get_or_create_character():
    """Return the MetaHumanCharacter asset, creating it via the FACTORY if absent (GOTCHA-MH-A)."""
    if unreal.EditorAssetLibrary.does_asset_exist('%s/%s' % (PKG_PATH, ASSET_NAME)):
        return unreal.load_asset(_obj_path()), False
    tools = unreal.AssetToolsHelpers.get_asset_tools()
    ch = tools.create_asset(
        asset_name=ASSET_NAME,
        package_path=PKG_PATH,
        asset_class=unreal.MetaHumanCharacter,
        factory=unreal.new_object(type=unreal.MetaHumanCharacterFactoryNew),
    )
    return ch, True


def _constraints_map(sub, ch):
    """name -> constraint. Absolute ranges (scale_measurement_ranges_with_height=False) so a Height change
    does not silently move the other dials' bounds underneath the values we are about to assert."""
    cons = sub.get_body_constraints(character=ch, scale_measurement_ranges_with_height=False)
    return {str(c.name): c for c in cons}, cons


def probe():
    """Read-only: enumerate every dial with its live range. Nothing is committed."""
    ch, created = get_or_create_character()
    sub = _subsystem()
    if not sub.try_add_object_to_edit(ch):
        raise RuntimeError('cannot open %s for edit (already open in the Creator UI?)' % ASSET_NAME)
    try:
        by_name, cons = _constraints_map(sub, ch)
        rows = [{'name': str(c.name), 'active': bool(c.is_active),
                 'target': round(c.target_measurement, 3),
                 'min': round(c.min_measurement, 3), 'max': round(c.max_measurement, 3)} for c in cons]
        unreal.log('MH ' + json.dumps({'action': 'probe', 'asset': _obj_path(),
                                       'created': created, 'n': len(rows), 'constraints': rows}))
        return rows
    finally:
        if sub.is_object_added_for_editing(ch):
            sub.remove_object_to_edit(ch)


def author():
    """Apply BODY_TARGET and commit the body state. Verifies every requested dial exists and is in range
    BEFORE writing — a silently-dropped constraint name would otherwise read as a successful author."""
    ch, created = get_or_create_character()
    sub = _subsystem()
    if not sub.try_add_object_to_edit(ch):
        raise RuntimeError('cannot open %s for edit (already open in the Creator UI?)' % ASSET_NAME)
    try:
        by_name, cons = _constraints_map(sub, ch)

        missing = [k for k in BODY_TARGET if k not in by_name]
        if missing:
            raise KeyError('constraint name(s) not present on this build: %s (have: %s)'
                           % (missing, sorted(by_name)))
        out_of_range = []
        for k, v in BODY_TARGET.items():
            c = by_name[k]
            if not (c.min_measurement <= v <= c.max_measurement):
                out_of_range.append('%s=%s not in [%s..%s]' % (k, v, c.min_measurement, c.max_measurement))
        if out_of_range:
            raise ValueError('target(s) outside the engine range: ' + '; '.join(out_of_range))

        before = {k: round(by_name[k].target_measurement, 3) for k in BODY_TARGET}
        for k, v in BODY_TARGET.items():
            by_name[k].is_active = True          # inactive => target_measurement is IGNORED (Epic's note)
            by_name[k].target_measurement = float(v)

        sub.set_body_constraints(character=ch, body_constraints=list(by_name.values()))
        sub.commit_body_state(character=ch)

        # Read back through a FRESH query rather than trusting the objects we just mutated.
        after_map, _ = _constraints_map(sub, ch)
        after = {k: round(after_map[k].target_measurement, 3) for k in BODY_TARGET}
        applied = {k: bool(after_map[k].is_active) for k in BODY_TARGET}
        drift = {k: round(after[k] - BODY_TARGET[k], 3) for k in BODY_TARGET}

        unreal.log('MH ' + json.dumps({'action': 'author', 'asset': _obj_path(), 'created': created,
                                       'before': before, 'requested': BODY_TARGET, 'after': after,
                                       'active': applied, 'drift_from_request': drift}))
        unreal.EditorAssetLibrary.save_asset(_obj_path(), only_if_is_dirty=False)
        return after
    finally:
        if sub.is_object_added_for_editing(ch):
            sub.remove_object_to_edit(ch)


def build():
    """Run the assembly pipeline -> the skeletal mesh assets metahuman_bind.py binds to the Template."""
    ch, _ = get_or_create_character()
    sub = _subsystem()
    if not sub.can_build_meta_human(ch):
        raise RuntimeError('can_build_meta_human() == False — body state not committed yet?')
    if not sub.try_add_object_to_edit(ch):
        raise RuntimeError('cannot open %s for edit' % ASSET_NAME)
    try:
        p = unreal.MetaHumanCharacterEditorBuildParameters()
        p.pipeline_type = unreal.MetaHumanDefaultPipelineType.OPTIMIZED
        p.pipeline_quality = unreal.MetaHumanQualityLevel.MEDIUM
        p.absolute_build_path = PKG_PATH
        p.common_folder_path = PKG_PATH + '/Common'
        p.enable_wardrobe_item_validation = False
        sub.build_meta_human(character=ch, params=p)
        unreal.log('MH ' + json.dumps({'action': 'build', 'asset': _obj_path(),
                                       'build_path': PKG_PATH, 'quality': 'MEDIUM'}))
    finally:
        if sub.is_object_added_for_editing(ch):
            sub.remove_object_to_edit(ch)


_action = globals().pop('MH_ACTION', None)
if _action == 'probe':
    probe()
elif _action == 'author':
    author()
elif _action == 'build':
    build()
else:
    unreal.log_error('MH ' + json.dumps({'action': 'ERROR', 'got': repr(_action),
                                         'valid': ['probe', 'author', 'build']}))
    raise RuntimeError('MH_ACTION is %r — set it to probe/author/build' % (_action,))
