"""Draw the simulated pawn's dynamics over a rendered take: particles (bones + tail ends) as circles at the particle
radius, the chain links, and every collider the chains use (body capsules blue, rest copies yellow). Everything comes
from the take's own record (the driver samples the same frames MRQ renders) and the hero profile; the camera is the
driver's deterministic one (pie_dynamics_drive.py CAM_OFFSET / CAM_LOOK, horizontal FOV 50).

    python Scripts/dyn_overlay.py <frames dir> <record.json> <out.mp4> [--offset N] [--from A --to B]
                                       [--layers dynamics,reaction] [--window reaction]

A hit record (hit_reaction_run) adds the reaction layer: its physics bodies (green simulated, grey kinematic), the
joints between them (white) and a red arrow at each impact's bone along its push.

--offset: record index = frame index - offset; -2 measured on the reference render by motion start (record 121 vs render ~119).
"""
import json, math, os, subprocess, sys
from PIL import Image, ImageDraw
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collider_autofit as af  # noqa: E402
import dyn_hero  # noqa: E402
import hit_reaction_analyze  # noqa: E402
import hit_reaction_core  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAM_OFFSET, CAM_LOOK, HFOV = (430.0, -330.0, 115.0), (0.0, 0.0, 95.0), 50.0
OUT_W, OUT_H = 1920, 1080
SS = 2                                                   # supersampling factor for the overlay layer
BODY, REST, PART, ROOT, TAIL = (80, 160, 255), (255, 210, 0), (255, 60, 60), (255, 255, 255), (255, 60, 200)
REACT, KIN, JOINT, ARROW = (60, 220, 120), (150, 150, 150), (255, 255, 255), (255, 40, 40)   # hit-reaction layer
CLOTH = (255, 140, 0)                                                                     # cloth collision layer
FILL_ALPHA = {BODY: 46, REST: 56, PART: 90, ROOT: 90, TAIL: 90, REACT: 60, KIN: 40, CLOTH: 60}   # of 255
ARROW_S, ARROW_CM = 0.35, 45.0          # an impact's arrow shows this long after it, this long, ending at its bone
CUT_LEAD_S, CUT_TAIL_S = 0.5, 1.0       # --window reaction: this long before the first impact and after the fade


def yaw_rot(v, yaw):
    c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    return [c * v[0] - s * v[1], s * v[0] + c * v[1], v[2]]


class Cam:
    def __init__(self, x, offset=CAM_OFFSET, look=CAM_LOOK):
        self.pos = [x + offset[0], offset[1], offset[2]]
        tgt = [x + look[0], look[1], look[2]]
        self.f = af.unit(af.sub(tgt, self.pos))
        self.r = af.unit([-self.f[1], self.f[0], 0.0])          # UE: X forward, Y right, Z up
        self.u = [self.f[1] * self.r[2] - self.f[2] * self.r[1], self.f[2] * self.r[0] - self.f[0] * self.r[2],
                  self.f[0] * self.r[1] - self.f[1] * self.r[0]]
        self.focal = (OUT_W / 2.0) / math.tan(math.radians(HFOV / 2.0))

    def px(self, p):
        rel = af.sub(p, self.pos)
        d = af.dot(rel, self.f)
        return (OUT_W / 2.0 + self.focal * af.dot(rel, self.r) / d, OUT_H / 2.0 - self.focal * af.dot(rel, self.u) / d), d


def frame_at(rec, t, offset=-2):
    """The rendered frame index showing the record's game time t: the row's own MRQ output frame when it names one,
    else record index + offset (an older record)."""
    rendered = lambda r: r.get('out') is not None and r['out'] >= 0           # noqa: E731
    if any(rendered(r) for r in rec['frames']):
        return next(r['out'] for r in rec['frames'] if r['t'] >= t and rendered(r))
    return next(k for k, r in enumerate(rec['frames']) if r['t'] >= t) + offset


def reaction_cut(rec, offset=-2):
    """(first, last) rendered frame of a hit take's reaction: CUT_LEAD_S before its first impact to CUT_TAIL_S after the
    reaction's fade (hit_reaction_analyze.reaction_window), read from the take's own record."""
    first = hit_reaction_analyze.hits_of(rec)[0]['t']
    end = hit_reaction_analyze.reaction_window(rec)[1]
    last = min(end + CUT_TAIL_S, rec['frames'][-1]['t'])
    return frame_at(rec, first - CUT_LEAD_S, offset), frame_at(rec, last, offset)


def main(argv):
    frames_dir, rec_path, out = argv[0], argv[1], argv[2]
    opt = lambda k, d: type(d)(argv[argv.index(k) + 1]) if k in argv else d
    offset, a, b = opt('--offset', -2), opt('--from', 0), opt('--to', 10 ** 6)
    rec = json.load(open(rec_path))
    if opt('--window', '') == 'reaction':
        a, b = reaction_cut(rec, offset)
    # Layers: 'dynamics' (the chains and their colliders) and 'reaction' (a hit record's physics bodies, joints and
    # impacts; hit_reaction_run records them in the config).
    cfg = rec.get('config') or {}
    reaction, joints = cfg.get('reaction_bodies') or [], cfg.get('reaction_constraints') or []
    impacts = (rec.get('events') or {}).get('hits') or []
    layers = opt('--layers', 'dynamics,reaction' if reaction else 'dynamics').split(',')
    # 'cloth': the bodies the mesh's own cloth collides with (the onboarded cloth collision source, hero_onboard's
    # physics stage), to see which one shapes the garment (seen as a ledge on Aurora's upper thigh).
    # The take's own snapshot (hit_reaction_run records it); a record without one, today's onboarded bodies.
    cloth = cfg.get('cloth_bodies') or []
    if 'cloth' in layers and not cloth:
        import hit_reaction_core
        with open(dyn_hero.resolve(rec.get('hero'))['onboard_record']) as f:
            cloth = (hit_reaction_core.onboard_physics(json.load(f))[1].get('spec') or {}).get('bodies') or []
    # The take's own camera (the driver scales the framing to the hero); records from before it used the constants.
    camera = ((rec['camera']['offset'], rec['camera']['look']) if rec.get('camera') else (CAM_OFFSET, CAM_LOOK))
    # The take's own snapshot; a record without one falls back to its hero's profile on disk (MT_HERO for records that
    # predate the hero field).
    prof = {} if rec.get('colliders') and rec.get('parts') else dyn_hero.resolve(rec.get('hero'))['profile']
    col = rec.get('colliders') or prof['colliders']
    shapes = {s['name']: s for s in col['shapes']}
    used = sorted({n for c in col['chains'].values() for n in c['collides_with']})
    # A body shape and its rest copies (one per chain, each shrunk differently) share one axis: draw only the outermost
    # of the group - the body shape itself when any chain uses it, else the largest copy.
    outer = {}
    for n in used:
        s = shapes[n]
        base = n[len('rest_%s_' % s['for_chain']):] if s.get('role') == 'rest' else n
        if base not in outer or s['radius_cm'] > shapes[outer[base]]['radius_cm']:
            outer[base] = n
    used = sorted(outer.values())
    # Every simulated part (chains, flaps, held-prop tails), from the record when it carries them, else the profile's
    # parts list: the profile's `chains` are only the intake's discovered chains, so flaps (the hood feathers) were absent.
    parts = rec.get('parts') or prof.get('secondary', {}).get('parts', [])
    chains = {p['root']: p['bones'] for p in parts if p['root'] in col['chains']}
    names = sorted(f for f in os.listdir(frames_dir) if f.startswith('frame_') and f.endswith('.png'))
    rows = rec['frames']
    tmp = os.path.join(frames_dir, '_overlay')
    os.makedirs(tmp, exist_ok=True)
    for f in os.listdir(tmp):       # ffmpeg reads the numbered sequence on: a longer earlier pass's tail would play on
        if f.startswith('o_') and f.endswith('.png'):
            os.remove(os.path.join(tmp, f))
    k_out = 0
    # A record whose rows name their MRQ output frame ('out': the index of the rendered file in order) is drawn by that
    # name, and a rendered frame no row names is refused; an older record by --offset.
    by_out = {r['out']: k for k, r in enumerate(rows) if r.get('out') is not None and r['out'] >= 0}
    if by_out:
        missing = [n for n in range(len(names)) if a <= n <= b and n not in by_out]
        if missing:
            raise RuntimeError('OVERLAY %d rendered frames name no record row (first %s)' % (len(missing), missing[:3]))
    for n, name in enumerate(names):
        if not (a <= n <= b):
            continue
        k = by_out[n] if by_out else n - offset
        img = Image.open(os.path.join(frames_dir, name)).convert('RGB').resize((OUT_W, OUT_H), Image.LANCZOS)
        if 0 <= k < len(rows):
            x = rows[k]['pawns'][0]
            mx, my, mz, yaw = x['mesh_w'][:4]
            to_w = lambda p: af.add(yaw_rot(p, yaw), [mx, my, mz])
            cam = Cam(x['loc'][0], *camera)
            # drawn at SS x and downsampled (anti-aliased); fills go into one opaque mask per colour, composited at
            # FILL_ALPHA, so overlapping shapes of a colour read as one translucent region instead of stacking darker
            W, H = OUT_W * SS, OUT_H * SS
            lines = Image.new('RGBA', (W, H), (0, 0, 0, 0))
            dr = ImageDraw.Draw(lines)
            masks = {}

            def px(pw):
                (u, v), d = cam.px(pw)
                return u * SS, v * SS, d

            def fill(color):
                if color not in masks:
                    masks[color] = Image.new('L', (W, H), 0)
                return ImageDraw.Draw(masks[color])

            def circle(pw, r_cm, color, width=2):
                u, v, d = px(pw)
                rp = cam.focal * r_cm / d * SS
                fill(color[:3]).ellipse([u - rp, v - rp, u + rp, v + rp], fill=255)
                dr.ellipse([u - rp, v - rp, u + rp, v + rp], outline=color, width=width * SS)

            def capsule(pa, pb, radius, color, radius_b=None):
                """A capsule's silhouette from its two segment ends (component space): end circles + side lines; with
                radius_b, a tapered one (radius at pa, radius_b at pb)."""
                ua, va, da = px(to_w(pa))
                ub, vb, db = px(to_w(pb))
                ra = cam.focal * radius / da * SS
                rb = cam.focal * (radius if radius_b is None else radius_b) / db * SS
                L = math.hypot(ub - ua, vb - va)
                if math.dist(pa, pb) <= 0.5 or L < SS:
                    circle(to_w(pa), radius, color, 2)          # a sphere (or seen end-on)
                else:
                    # fill: both end discs + the quad between them; outline: each end's outer half-circle + side lines
                    nx, ny = -(vb - va) / L, (ub - ua) / L
                    side = [(ua + nx * ra, va + ny * ra), (ub + nx * rb, vb + ny * rb),
                            (ub - nx * rb, vb - ny * rb), (ua - nx * ra, va - ny * ra)]
                    f = fill(color[:3])
                    f.ellipse([ua - ra, va - ra, ua + ra, va + ra], fill=255)
                    f.ellipse([ub - rb, vb - rb, ub + rb, vb + rb], fill=255)
                    f.polygon(side, fill=255)
                    th = math.degrees(math.atan2(vb - va, ub - ua))
                    dr.arc([ua - ra, va - ra, ua + ra, va + ra], th + 90, th + 270, fill=color, width=2 * SS)
                    dr.arc([ub - rb, vb - rb, ub + rb, vb + rb], th - 90, th + 90, fill=color, width=2 * SS)
                    dr.line(side[:2], fill=color, width=2 * SS)
                    dr.line(side[2:], fill=color, width=2 * SS)

            def local_capsule(tm, loc, radius, color):
                t, q = tm[:3], tm[3:]
                c = af.add(t, af.qrot(q, loc.get('translation') or loc.get('center') or [0.0, 0.0, 0.0]))
                ax = af.qrot(af.qmul(q, loc.get('rotation_xyzw') or [0.0, 0.0, 0.0, 1.0]), [0.0, 0.0, 1.0])
                h = loc.get('length_cm', 0.0) / 2
                capsule(af.sub(c, af.scale(ax, h)), af.add(c, af.scale(ax, h)), radius, color)

            for nme in (used if 'dynamics' in layers else ()):
                s = shapes[nme]
                tm = x['tm'].get(s['owner'])
                if tm is None:
                    continue
                color = REST + (200,) if s.get('role') == 'rest' else BODY + (200,)
                local_capsule(tm, s['local'], s['local']['radius_cm'], color)
            for body in cloth:
                if body['bone'] in x['tm']:
                    for pa, pb, ra, rb in hit_reaction_core.body_tapered(body, x['tm']):
                        capsule(pa, pb, ra, CLOTH + (220,), rb)
            if 'reaction' in layers:
                # The hit reaction's physics bodies (the physics asset's own shapes), its joints (each body's bone
                # origin, linked to its parent body's) and, for a moment after each impact, the push.
                for body in reaction:
                    tm = x['tm'].get(body['bone'])
                    if tm is None:
                        continue
                    color = (KIN if body['physics_type'] == 'kinematic' else REACT) + (220,)
                    # as the builder makes them, rigid in their bone's frame (hit_reaction_core.body_capsules; an older
                    # record's aimed capsule is approximated from the live aim bone)
                    for pa, pb, r in hit_reaction_core.body_capsules(body, x['tm']):
                        capsule(pa, pb, r, color)
                for c in joints:
                    a_, b_ = x['tm'].get(c['child']), x['tm'].get(c['parent'])
                    if a_ and b_:
                        dr.line([px(to_w(a_[:3]))[:2], px(to_w(b_[:3]))[:2]], fill=JOINT + (255,), width=2 * SS)
                for bone in {c['child'] for c in joints} | {c['parent'] for c in joints}:
                    tm = x['tm'].get(bone)
                    if tm:
                        u, v, _d = px(to_w(tm[:3]))
                        dr.ellipse([u - 4 * SS, v - 4 * SS, u + 4 * SS, v + 4 * SS], fill=JOINT + (255,),
                                   outline=(0, 0, 0, 255), width=SS)
                for h in impacts:
                    age = rows[k]['t'] - h['t']
                    tm = x['tm'].get(h['config']['bone'])
                    if tm is None or not (0.0 <= age <= ARROW_S):
                        continue
                    d = af.unit(h['config']['dir'])
                    # world direction into the component frame (the record's transforms are component space)
                    d = yaw_rot(d, -yaw)
                    tip = tm[:3]
                    tail = af.sub(tip, af.scale(d, ARROW_CM))
                    (ut, vt, _), (uh, vh, _) = px(to_w(tail)), px(to_w(tip))
                    w = int(7 * SS * (1.0 - 0.6 * age / ARROW_S))
                    dr.line([(ut, vt), (uh, vh)], fill=ARROW + (255,), width=w)
                    ang, hl = math.atan2(vh - vt, uh - ut), 18 * SS
                    dr.polygon([(uh, vh), (uh - hl * math.cos(ang - 0.45), vh - hl * math.sin(ang - 0.45)),
                                (uh - hl * math.cos(ang + 0.45), vh - hl * math.sin(ang + 0.45))], fill=ARROW + (255,))
            for root, bones in (chains.items() if 'dynamics' in layers else ()):
                spec = col['chains'][root]
                pts = [x['chain'][bn] for bn in bones if bn in x['chain']]
                for tl in spec.get('tails', []):
                    tm = x['tm'].get(tl['bone'])
                    if tm:
                        tail = af.add(tm[:3], af.qrot(tm[3:], tl['local']))
                        dr.line([px(to_w(x['chain'][tl['bone']]))[:2], px(to_w(tail))[:2]], fill=TAIL + (255,), width=3 * SS)
                        circle(to_w(tail), spec['particle_radius_cm'], TAIL + (255,), 3)
                for p, q in zip(pts, pts[1:]):
                    dr.line([px(to_w(p))[:2], px(to_w(q))[:2]], fill=PART + (255,), width=3 * SS)
                for i, p in enumerate(pts):
                    circle(to_w(p), spec['particle_radius_cm'], (ROOT if i == 0 else PART) + (255,), 3)
            ov = Image.new('RGBA', (W, H), (0, 0, 0, 0))
            for rgb, m in masks.items():
                layer = Image.new('RGBA', (W, H), rgb + (0,))
                layer.putalpha(m.point(lambda v, a=FILL_ALPHA[rgb]: v * a // 255))
                ov.alpha_composite(layer)
            ov.alpha_composite(lines)
            img = Image.alpha_composite(img.convert('RGBA'), ov.resize((OUT_W, OUT_H), Image.LANCZOS)).convert('RGB')
            ImageDraw.Draw(img).text((20, 20), 't=%.2f  frame %d  (record %d)' % (rows[k]['t'], n, k), fill=(0, 0, 0))
        img.save(os.path.join(tmp, 'o_%04d.png' % k_out))
        k_out += 1
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-framerate', '60', '-i', os.path.join(tmp, 'o_%04d.png'),
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '16', out], check=True)
    print('OVERLAY offset %d, %d frames -> %s' % (offset, k_out, out))


if __name__ == '__main__':
    main(sys.argv[1:])
