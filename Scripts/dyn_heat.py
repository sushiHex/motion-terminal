"""The dynamics heatmap: the ON mesh's own textures, overlaid per render vertex with how far it is from the animation.

The aim: show what part of the clothing mesh is dynamic by heatmapping the mesh itself, as an overlay on
the original texture. The drive (pie_dynamics_drive.py, config 'heat') writes the distances each frame
(UHeroDynamicsTools.ApplyDisplacementHeat) into the mesh's vertex colours; MATERIAL is the mesh's overlay material (drawn
over every section with the same vertex factory, SkeletalMeshSceneProxy.cpp:839), unlit and translucent. It is half a
tint, half paint (a pure tint lost too much of the texture detail, pure paint
too little): the tint reads the lit texture under it (SceneColor, in scene units: MaterialTemplate.ush:3497) and
keeps its luminance with the hue of the yellow -> red ramp over SCALE_CM; PAINT mixes it toward the ramp itself. It
fades in over the first FADE_IN of the scale, so what holds to the animation shows untouched. It is displacement, not
speed: cloth held off the animation stays hot while still.
"""
import math
import os

# Versioned: ensure_material repairs settings but never rebuilds an existing graph, so a graph change takes a new name.
MATERIAL = '/Game/MT/Generated/M_MT_DisplacementHeat_v4'
SCALE_CM = 20.0                          # the top of the scale: 20 cm or more is full red (the legend says so)
WARM = (1.0, 0.85, 0.1)                  # the bottom of the scale
HOT = (1.0, 0.08, 0.02)                  # the scale's top
OPACITY = 0.9                            # the overlay at full strength: a little of the texture's own colour still shows
PAINT = 0.5                              # between a tint (0: the texture's luminance, the ramp's hue) and paint (1: the ramp)
                                         # (half way, leaning slightly towards the ramp)
LUMA = (0.2126, 0.7152, 0.0722)          # Rec. 709 luminance weights: what the tint keeps of the texture
FADE_IN = 0.1                            # of the scale (2 cm): under it the overlay fades out to the bare texture
# The overlay's material settings, checked (and repaired) on every build: a material without the skeletal mesh or
# cloth usage is dropped from those sections.
SETTINGS = {'blend_mode': 'BLEND_TRANSLUCENT', 'shading_model': 'MSM_UNLIT', 'two_sided': True,
            'used_with_skeletal_mesh': True, 'used_with_clothing': True}
LEGEND_UNDER = (0.32, 0.32, 0.34)        # the legend's stand-in for "the texture": a mid grey


def ensure_material(unreal):
    """The overlay material (editor side), built if absent: emissive = ramp x luma(scene) / luma(ramp) with ramp =
    lerp(WARM, HOT, r), mixed PAINT of the way to the ramp itself; opacity = OPACITY * sat(r / FADE_IN), r the vertex
    colour's red."""
    mel = unreal.MaterialEditingLibrary
    built = not unreal.EditorAssetLibrary.does_asset_exist(MATERIAL)
    if built:
        folder, name = MATERIAL.rsplit('/', 1)
        mat = unreal.AssetToolsHelpers.get_asset_tools().create_asset(name, folder, unreal.Material,
                                                                      unreal.MaterialFactoryNew())
        _graph(unreal, mel, mat)
    else:
        mat = unreal.load_asset(MATERIAL)
    want = {k: (getattr(unreal.BlendMode if k == 'blend_mode' else unreal.MaterialShadingModel, v)
                if isinstance(v, str) else v) for k, v in SETTINGS.items()}
    wrong = [k for k, v in want.items() if mat.get_editor_property(k) != v]
    for k in wrong:
        mat.set_editor_property(k, want[k])
    if built or wrong:
        mel.recompile_material(mat)
    import mt_drive
    mt_drive.require_saved_gitignored(MATERIAL, 'dyn_heat')     # also one built but never saved
    return mat


def _graph(unreal, mel, mat):
    """The overlay's expression graph on a new material."""

    def node(cls, x, y, **props):
        n = mel.create_material_expression(mat, cls, x, y)
        for k, v in props.items():
            n.set_editor_property(k, v)
        return n

    def link(a, out, b, inp):
        if not mel.connect_material_expressions(a, out, b, inp):
            raise RuntimeError('DYN_HEAT could not link %s.%s -> %s.%s' % (a.get_name(), out, b.get_name(), inp))
    rgb = lambda c: unreal.LinearColor(c[0], c[1], c[2], 1.0)                     # noqa: E731
    vc = node(unreal.MaterialExpressionVertexColor, -900, 0)
    warm = node(unreal.MaterialExpressionConstant3Vector, -650, -300, constant=rgb(WARM))
    hot = node(unreal.MaterialExpressionConstant3Vector, -650, -220, constant=rgb(HOT))
    ramp = node(unreal.MaterialExpressionLinearInterpolate, -450, -250)
    luma = node(unreal.MaterialExpressionConstant3Vector, -650, -120, constant=rgb(LUMA))
    ramp_luma = node(unreal.MaterialExpressionDotProduct, -300, -180)
    scene = node(unreal.MaterialExpressionSceneColor, -900, -450)
    scene_rgb = node(unreal.MaterialExpressionComponentMask, -700, -450, r=True, g=True, b=True, a=False)
    scene_luma = node(unreal.MaterialExpressionDotProduct, -450, -420)
    gain = node(unreal.MaterialExpressionDivide, -200, -320)
    tint = node(unreal.MaterialExpressionMultiply, -50, -250)
    paint = node(unreal.MaterialExpressionLinearInterpolate, 100, -250, const_alpha=PAINT)
    fade = node(unreal.MaterialExpressionMultiply, -650, 80, const_b=1.0 / FADE_IN)
    sat = node(unreal.MaterialExpressionSaturate, -450, 80)
    alpha = node(unreal.MaterialExpressionMultiply, -250, 80, const_b=OPACITY)
    link(warm, '', ramp, 'A')
    link(hot, '', ramp, 'B')
    link(vc, 'R', ramp, 'Alpha')
    link(ramp, '', ramp_luma, 'A')
    link(luma, '', ramp_luma, 'B')
    link(scene, '', scene_rgb, '')
    link(scene_rgb, '', scene_luma, 'A')
    link(luma, '', scene_luma, 'B')
    link(scene_luma, '', gain, 'A')
    link(ramp_luma, '', gain, 'B')
    link(ramp, '', tint, 'A')
    link(gain, '', tint, 'B')
    link(tint, '', paint, 'A')
    link(ramp, '', paint, 'B')
    link(vc, 'R', fade, 'A')
    link(fade, '', sat, '')
    link(sat, '', alpha, 'A')
    for n, prop in ((paint, unreal.MaterialProperty.MP_EMISSIVE_COLOR), (alpha, unreal.MaterialProperty.MP_OPACITY)):
        if not mel.connect_material_property(n, '', prop):
            raise RuntimeError('DYN_HEAT could not connect %s' % prop)


def colour(t):
    """The overlay at t in 0..1 (host side): (rgb 0..1, opacity), as the material computes it."""
    t = min(max(t, 0.0), 1.0)
    return tuple(w + (h - w) * t for w, h in zip(WARM, HOT)), OPACITY * min(t / FADE_IN, 1.0)


def over(t, under=LEGEND_UNDER):
    """The overlay at t over a surface colour, as 8-bit rgb (the legend's swatch): the ramp's hue at the surface's
    luminance, PAINT of the way to the ramp itself, blended in by the opacity."""
    rgb, a = colour(t)
    gain = sum(w * u for w, u in zip(LUMA, under)) / sum(w * c for w, c in zip(LUMA, rgb))
    return tuple(int(round(255 * min(1.0, u + (c * gain + (c - c * gain) * PAINT - u) * a))) for c, u in zip(rgb, under))


# The garment's settle after the run stops (Aurora's dress read rubbery as it settled after she
# stopped), from the heat rows' cloth_motion_cm (UHeroDynamicsTools.ApplyDisplacementHeat: the RMS, over the vertices the
# cloth moves, of how far each one's offset from the animation moved since the last frame; a sum of squares, so panels
# swinging opposite ways never cancel). Measured from when the pawn has actually stopped
# (its speed under STOP_CMS), not the input release; settled once that motion stays under SETTLE_CMS for SETTLE_DWELL_S.
# A take that ends first is NOT settled. A rebound is each new peak of the motion above the threshold after the first:
# a rubbery garment springs back out again and again.
STOP_CMS = 5.0
SETTLE_CMS = 5.0
SETTLE_DWELL_S = 0.25


def garment_settle(rec):
    """{'settled', 'settle_s', 'rebounds', 'peak_cms', 't_stop'} after the pawn stops, or None when the record has no
    release or no cloth motion (a hero without a garment, a take without heat)."""
    t0 = (rec.get('events') or {}).get('t_release')
    rows = [r for r in rec.get('frames') or [] if t0 is not None and r['t'] >= t0]
    t_stop = None
    for a, b in zip(rows, rows[1:]):
        la, lb = a['pawns'][0].get('loc'), b['pawns'][0].get('loc')
        if la and lb and b['t'] > a['t'] and math.dist(la, lb) / (b['t'] - a['t']) < STOP_CMS:
            t_stop = b['t']
            break
    pts = []
    for a, b in zip(rows, rows[1:]):
        m = (b.get('heat') or {}).get('cloth_motion_cm')
        if t_stop is not None and b['t'] >= t_stop and m is not None and b['t'] > a['t']:
            pts.append((b['t'], m / (b['t'] - a['t'])))
    if len(pts) < 3:
        return None
    settled_at, quiet_from = None, None
    for t, v in pts:
        if v < SETTLE_CMS:
            quiet_from = t if quiet_from is None else quiet_from
            if t - quiet_from >= SETTLE_DWELL_S:
                settled_at = quiet_from
                break
        else:
            quiet_from = None
    peaks = [b for (_ta, a), (_tb, b), (_tc, c) in zip(pts, pts[1:], pts[2:]) if b > a and b >= c and b > SETTLE_CMS]
    return dict({'settled': settled_at is not None, 'settle_s': round((settled_at if settled_at is not None
                                                                       else pts[-1][0]) - t_stop, 3),
                 'rebounds': max(0, len(peaks) - 1), 'peak_cms': round(max(v for _t, v in pts), 1),
                 't_stop': round(t_stop, 3)}, **garment_run(rec))


def garment_run(rec):
    """How much the garment moves while the pawn runs (after a settle-only tuning it
    barely moved, floaty and rigid), over the drive's run (events t_drive to t_release): its mean RMS offset from the
    animation (cm: how far it trails) and its mean motion relative to the animation (cm/s: how much it moves on the
    body). {} when the record has neither."""
    ev = rec.get('events') or {}
    t0, t1 = ev.get('t_drive'), ev.get('t_release')
    if t0 is None or t1 is None:
        return {}
    rows = [r for r in rec.get('frames') or [] if t0 <= r['t'] <= t1]
    off = [r['heat']['cloth_rms_cm'] for r in rows if (r.get('heat') or {}).get('cloth_rms_cm') is not None]
    def speed(key):
        return [b['heat'][key] / (b['t'] - a['t']) for a, b in zip(rows, rows[1:])
                if (b.get('heat') or {}).get(key) is not None and b['t'] > a['t']]
    out = {}
    if off:
        out['run_offset_cm'] = round(sum(off) / len(off), 2)
    # the motion, and the ripple: that motion less the garment's common motion (it moving as one sheet)
    for key, name in (('cloth_motion_cm', 'run_motion_cms'), ('cloth_ripple_cm', 'run_ripple_cms')):
        v = speed(key)
        if v:
            out[name] = round(sum(v) / len(v), 1)
    return out


def legend(path, width=520, height=70):
    """A PNG legend of the scale (host side, PIL): the overlay from 0 (the bare texture) to SCALE_CM, over a grey."""
    from PIL import Image, ImageDraw
    img = Image.new('RGBA', (width, height), (0, 0, 0, 150))
    d = ImageDraw.Draw(img)
    x0, x1, y0, y1 = 16, width - 16, 30, 50
    for x in range(x0, x1):
        d.line([(x, y0), (x, y1)], fill=over((x - x0) / float(x1 - x0 - 1)) + (255,))
    d.text((x0, 8), 'distance from the animation', fill=(255, 255, 255, 255))
    d.text((x0, y1 + 2), '0 (texture)', fill=(255, 255, 255, 255))
    d.text((x1 - 70, y1 + 2), '%g cm or more' % SCALE_CM, fill=(255, 255, 255, 255))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path)
    return path
