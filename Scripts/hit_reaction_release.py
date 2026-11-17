"""Release the hit reaction for any heroes, end to end, from a normal shell (PowerShell, editor up):

    python Scripts/hit_reaction_release.py --hero <Rig> [--hero <Rig> ...] [--res=3840x2160]

Per hero, in order, each step regenerated rather than reused:
  1. onboarding   the reaction body the generator makes today; a refused or missing record
                  (hit_reaction_run._reaction_spec) re-runs hero_onboard's physics stage, which must build and read
                  back, and is checked again
  2. ABP          the dynamics ABP, rebuilt against that body (dynamics_graph)
  3. matrix       hit_reaction_run's three drives and verdict; a failing hero stops here, unrendered
  4. take         the hit under MRQ (hit_reaction_run.render); its own record must be fresh, error-free, the
                  matrix's impacts and carry every one, and its frames contiguous (take_problems); judged in place
                  of the matrix hit run, its figures are the page's
  5. clips        the take and its reaction overlay (dyn_overlay), both cut to the reaction, with posters just
                  after the last impact
  6. dynamics     whichever dynamics takes the page shows (page_media): simulation on against off, and/or its
                  heatmap (dyn_heat), each as a looping cut
The release record, captures/hit-reaction/release.json, carries per hero the verdict, its summary, the media and the
commit it was made from; breakdown_page.py builds the breakdown page from it. A hero's entry is cleared when its
release starts and written when it ends, so a release that fails or raises leaves no figures or media behind for the
page. A hero re-released replaces only its own entry.
"""
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dyn_hero  # noqa: E402
import hero_onboard  # noqa: E402
import hit_reaction_analyze  # noqa: E402
import hit_reaction_run  # noqa: E402
import hit_reaction_sweep  # noqa: E402
import dyn_overlay  # noqa: E402
import dynamics_graph  # noqa: E402

REPO = os.path.dirname(HERE)
OUT = os.path.join(REPO, 'captures', 'hit-reaction')
RECORD = os.path.join(OUT, 'release.json')
TAG = 'release'
POSTER_AFTER_S = 0.15       # the poster: this long after the last impact, the reaction under way
# The garment's settle after the run (garment_gate, dyn_heat.garment_settle): at rest this soon after the pawn stops,
# springing back out at most this often. Aurora's dress as authored: never at rest in the 1.75 s stand, 5 rebounds;
# with the garment setting (dynamics_behaviours.json): 0.73 s, 3 (2026-11-06).
GARMENT_SETTLE_MAX_S = 1.0
GARMENT_REBOUNDS_MAX = 3
# What a release is made from: the build's own sources (dynamics_graph.SOURCES, which the gate's identity row also
# reads), plus what drives, gates, renders and selects it, the plugin's runtime tools among them (their source: which
# build the editor loaded is not checked here).
_PLUGIN = '../Plugins/MotionTerminal/'
GENERATOR_SOURCES = dynamics_graph.SOURCES + (
    'hit_reaction_release.py', 'hit_reaction_run.py', 'hit_reaction_analyze.py', 'pie_dynamics_drive.py',
    'dyn_heat.py', 'dyn_contact.py', 'cloth_rest.py', 'dyn_gate.py', 'dyn_render.py', 'dyn_overlay.py',
    'loop_cut.py', 'mrq_render.py', 'dyn_behaviours.py', 'dynamics_behaviours.json',
    'editor_session.py', 'breakdown_page.html') + tuple(
    _PLUGIN + 'Source/MotionTerminal/Private/' + f for f in ('HeroDynamicsTools.cpp', 'HeroDynamicsTools.h',
                                                             'HeroPhysicsTools.cpp', 'HeroToolJson.h'))


def onboard(rig):
    """The hero's reaction body as the generator makes it today, regenerating the physics stage when refused."""
    h = dyn_hero.resolve(rig)
    try:
        return hit_reaction_run._reaction_spec(h)
    except (RuntimeError, OSError) as e:     # stale, or no record at all
        print('RELEASE %s: %s; re-onboarding the physics stage' % (rig, e), flush=True)
    # The physics stage raises on a body that does not read back as specified; the verify stage's other gaps are about
    # the locomotion assets (a hero onboarded by hand before the pipeline, the first one, has them) and are reported,
    # not gated: the reaction is gated by the fresh-spec check below and by the matrix.
    summary = hero_onboard.summary_of(hero_onboard.run(rig, 'physics'))
    if summary.get('status') != 'PASS':
        print('RELEASE %s: onboarding %s (locomotion gaps, not the reaction body): %s'
              % (rig, summary.get('status'), summary.get('gaps')), flush=True)
    return hit_reaction_run._reaction_spec(h)


VOLLEY_KEYS = ('bone', 'dir', 'strength', 'at_s', 'offset_cm', 'radius', 'vel_change', 'pawns')


def take_problems(rec, written, started, frame_names, matrix=None):
    """Why a rendered take is not the volley the matrix passed, [] when it is: its record must be written after the
    release started and carry no error and every planned impact (MRQ's success flags say nothing of the driver), its
    frames must be contiguous (the take encodes a numbered sequence, the overlay every file), and its volley must be
    the matrix hit run's (matrix: that record): a take at twice the strength passed every check on its own
   ."""
    bad = [] if written >= started else ['the take record predates this release']
    if matrix is not None:
        volley = lambda r: [{k: h.get(k) for k in VOLLEY_KEYS} for h in (r.get('config') or {}).get('hits') or []]  # noqa: E731
        if volley(rec) != volley(matrix):
            bad.append('the take volley is not the matrix volley')
    if rec.get('error'):
        bad.append('the take drive failed: %s' % rec['error'])
    planned = len((rec.get('config') or {}).get('hits') or [])
    if not planned or len(hit_reaction_analyze.hits_of(rec)) != planned:
        bad.append('the take delivered %d of %d impacts' % (len(hit_reaction_analyze.hits_of(rec)), planned))
    nums = sorted(int(re.search(r'(\d+)', f).group(1)) for f in frame_names)
    if not nums or nums != list(range(nums[0], nums[0] + len(nums))):
        bad.append('the take has %d frames, not one contiguous sequence' % len(nums))
    return bad


def poster(mp4, frame, out):
    if os.path.exists(out):
        os.remove(out)
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', mp4, '-vf', 'select=eq(n\\,%d)' % frame,
                    '-frames:v', '1', '-q:v', '3', out], check=True)
    if not (os.path.exists(out) and os.path.getsize(out)):     # ffmpeg exits 0 on a frame past the end
        raise RuntimeError('no poster at frame %d of %s' % (frame, mp4))
    return out


def media(rig, resolution, started, matrix_hit, spec):
    """The take, its overlay and their posters, under captures/hit-reaction/<short>/."""
    h = dyn_hero.resolve(rig)
    d = os.path.join(OUT, dyn_hero.short_name(rig).lower())
    frames = os.path.join(d, 'frames')
    os.makedirs(frames, exist_ok=True)
    look = TAG + '_look'
    names = lambda: [f for f in os.listdir(frames) if f.startswith('frame_') and f.endswith('.png')]  # noqa: E731
    for f in names():                   # this take's frames only: an older, longer take's tail would play on
        os.remove(os.path.join(frames, f))
    if not hit_reaction_run.render(rig, frames, look, resolution=resolution, outputs=()):   # clip() cuts its own
        raise RuntimeError('%s: the render did not complete' % rig)
    path = dyn_hero.record_path(h, look)
    rec = json.load(open(path))
    bad = take_problems(rec, os.path.getmtime(path), started, names(), json.load(open(matrix_hit)))
    if bad:
        raise RuntimeError('%s: %s' % (rig, '; '.join(bad)))
    # The volley and its overlay are cut to the reaction: the take goes on into the run, which is not the hit (the
    # hit frames are enough).
    cut, last = dyn_overlay.reaction_cut(rec)
    volley = os.path.join(d, 'volley.mp4')
    clip(frames, cut, last, volley)
    still = dyn_overlay.frame_at(rec, hit_reaction_analyze.hits_of(rec)[-1]['t'] + POSTER_AFTER_S) - cut
    files = {'volley': volley, 'volley_poster': poster(volley, still, os.path.join(d, 'volley.jpg'))}
    if 'overlay' in page_media(rig):        # the reaction overlay, when the page shows it
        overlay = os.path.join(d, 'overlay.mp4')
        dyn_overlay.main([frames, path, overlay, '--layers', 'reaction', '--window', 'reaction'])
        files.update(overlay=overlay, overlay_poster=poster(overlay, still, os.path.join(d, 'overlay.jpg')))
    files.update(dynamics_media(rig, resolution, started, d, spec))
    return path, files


def clip(frames_dir, first, last, out, crop=None):
    """Frames [first, last] of a rendered sequence (indices into its sorted frames) as a delivery MP4 (crop: the
    filter that frames it instead of the delivery scale)."""
    import dyn_render
    names = sorted(f for f in os.listdir(frames_dir) if f.startswith('frame_') and f.endswith('.png'))
    start = int(re.search(r'(\d+)', names[first]).group(1))
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-framerate', '60', '-start_number', str(start),
                    '-i', os.path.join(frames_dir, 'frame_%04d.png'), '-frames:v', str(last - first + 1),
                    '-vf', crop or 'scale=%s:flags=lanczos' % dyn_render.DELIVERY, '-c:v', 'libx264',
                    '-pix_fmt', 'yuv420p', '-crf', '16', out], check=True)
    return out


def release_figures(spec, dynamics):
    """The page figures the release adds to the take's summary: the reaction body and the dynamics gate it passed."""
    return {'bodies': len(spec['bodies']),
            'simulated': sum(1 for b in spec['bodies'] if b['physics_type'] != 'kinematic'),
            'dynamics_checks': dynamics['checks'], 'dynamics_passed': dynamics['checks'] - len(dynamics['failed'])}


def cloth_rest_gate(rig):
    """No movable cloth particle inside a collider at the reference pose (cloth_rest): a collider in the garment's rest
    pose shoves it off the body where its simulation begins (seen as a shelf at Aurora's hip). A hero
    without clothing passes."""
    import cloth_rest
    h = dyn_hero.resolve(rig)
    if not h['profile'].get('stock', {}).get('clothing_assets'):
        return {'pass': True, 'clothing': False}
    ok, worst, where = cloth_rest.verdict(cloth_rest.report(h))
    return {'pass': ok, 'deepest_cm': worst, 'where': where}


def garment_gate(rig, started):
    """The garment comes to rest after the run without springing back (Aurora's dress read rubbery
    as it settled after she stopped), read from this release's heat take (dyn_heat.garment_settle): settled within
    GARMENT_SETTLE_MAX_S of the stop, with at most GARMENT_REBOUNDS_MAX rebounds. A hero without a garment, or whose page
    shows no heat take, has no measurement and passes."""
    import dyn_heat
    h = dyn_hero.resolve(rig)
    if not h['profile'].get('stock', {}).get('clothing_assets') or 'dynamics_heat' not in page_media(rig):
        return {'pass': True, 'measured': False}
    path = dyn_hero.record_path(h, TAG + '_heat')
    if os.path.getmtime(path) < started:
        return {'pass': False, 'measured': False, 'why': 'the heat record predates this release'}
    got = dyn_heat.garment_settle(json.load(open(path)))
    if got is None:
        return {'pass': False, 'measured': False, 'why': 'the heat take carries no cloth motion'}
    ok = got['settled'] and got['settle_s'] <= GARMENT_SETTLE_MAX_S and got['rebounds'] <= GARMENT_REBOUNDS_MAX
    return dict(got, measured=True, **{'pass': ok})


def dynamics_gate(rig):
    """The build the dynamics clip is rendered from, judged against the hero's dynamics baseline the way
    `dyn_run <tag> --gate` judges it: a release shows no dynamics clip of a build that fails it (a clip
    once was rendered and published with no gate at all)."""
    import dyn_gate
    import dyn_run
    result = dyn_run.run(rig, TAG + '_dyngate', [1.0, 0.0])
    if not result or result.get('error'):
        return {'pass': False, 'failed': ['the gated dynamics run produced no analysis: %s' % (result or {}).get('error')]}
    with open(dyn_gate.baseline_path(rig)) as f:
        base = json.load(f)
    with open(result['path']) as f:
        rows = dyn_gate.check(result, base, json.load(f))
    return {'pass': dyn_gate.passed(rows), 'checks': len(rows),
            'failed': ['%s %s: %s' % (n, s, d) for n, s, ok, d in rows if not ok]}


def dynamics_take(rig, resolution, started, frames, tag, extra, overlay=None, loop=True):
    """One rendered dynamics take (dyn_render: simulation on, near, against none at all, far, unless extra's
    alphas say otherwise) and its looping cut (overlay: an image composited into it, in its one encode), both of this
    release: (record, loop path, frame count). Only the loop is encoded; loop=False encodes nothing (loop path None)."""
    import dyn_render
    os.makedirs(frames, exist_ok=True)
    cut = os.path.join(frames, 'dyn_%s_%s_loop.mp4' % (rig, tag)) if loop else None
    is_frame = lambda f: f.startswith('frame_') and f.endswith('.png')                  # noqa: E731
    for f in os.listdir(frames):        # this take's frames and cut only: an old loop survived a failed cut
        if is_frame(f) or f.endswith('.mp4'):
            os.remove(os.path.join(frames, f))
    kw = {'resolution': resolution} if resolution else {}
    if not dyn_render.render(rig, frames.replace('\\', '/'), tag, extra=extra, outputs=('loop',) if loop else (),
                                  overlay=overlay, **kw):
        raise RuntimeError('%s: the %s render did not complete' % (rig, tag))
    path = dyn_hero.record_path(dyn_hero.resolve(rig), tag)
    rec = json.load(open(path))
    if os.path.getmtime(path) < started or rec.get('error'):
        raise RuntimeError('%s: the %s record is stale or failed' % (rig, tag))
    if loop and (not os.path.exists(cut) or os.path.getmtime(cut) < started):
        raise RuntimeError('%s: the %s take has no looping cut of its own' % (rig, tag))
    return rec, cut, len([f for f in os.listdir(frames) if is_frame(f)])


# The lineup's strips (every rigged hero running side by side, a hero added to the running lineup as it is
# rigged): each hero alone, simulation on, framed by one camera for every hero (as a
# LINEUP_FRAME_CM hero: side by side they keep their true sizes), cut by the drive's own clock so every strip shows the
# same moment: from just before the run starts to the settle after it stops (every drive runs one schedule). A strip
# is the frame's centre, where the camera holds the hero.
LINEUP_FRAME_CM = 209.9
LINEUP_LEAD_S, LINEUP_TAIL_S = 0.5, 1.5
LINEUP_STRIP = (864, 1080)


def run_strip(rig, resolution, started, d, cloth):
    """The hero's lineup strip: (path, poster path)."""
    frames = os.path.join(d, 'run')
    rec, _cut, _n = dynamics_take(rig, resolution, started, frames, TAG + '_run',
                                  dict(cloth, alphas=[1.0], frame_height_cm=LINEUP_FRAME_CM), loop=False)
    ev = rec.get('events') or {}
    first = dyn_overlay.frame_at(rec, ev['t_drive'] - LINEUP_LEAD_S)
    last = dyn_overlay.frame_at(rec, min(ev['t_release'] + LINEUP_TAIL_S, rec['frames'][-1]['t']))
    out = os.path.join(d, 'run.mp4')
    w, h = LINEUP_STRIP
    clip(frames, first, last, out, crop='scale=1920:1080:flags=lanczos,crop=%d:%d' % (w, h))
    return out, poster(out, (last - first) // 4, os.path.join(d, 'run.jpg'))


def page_media(rig, template=None):
    """The media keys the breakdown page shows for this hero ({{<rig>.media.<key>}} in its template): a dynamics take
    the page does not show is not rendered (2026-10-31: Aurora's plain take and Khaimera's heat take were 34 min of
    renders nobody saw)."""
    import breakdown_page                   # the page's own token grammar (it imports this module: deferred)
    with open(template or os.path.join(HERE, 'breakdown_page.html'), encoding='utf-8') as f:
        prefix = rig + '.media.'
        tokens = breakdown_page.TOKEN.findall(f.read())
    # the lineup ({{lineup}}) is every released hero's run strip side by side: every hero renders one
    return {t[len(prefix):] for t in tokens if t.startswith(prefix)} | (
        {'run'} if breakdown_page.LINEUP in tokens else set())


def dynamics_media(rig, resolution, started, d, spec):
    """The hero's dynamics take and/or its heatmap take (dyn_heat: the ON mesh tinted by its distance from the
    animation), whichever the page shows, rendered by this release so the page never shows an older one (a
    hand-made render once showed a hero from before the hair and leg fixes)."""
    import dyn_heat
    shown, out = page_media(rig), {}
    cloth = {'cloth_bodies': spec.get('cloth_bodies') or []}
    if 'run' in shown:
        out['run'], out['run_poster'] = run_strip(rig, resolution, started, d, cloth)
    if shown & {'dynamics', 'dynamics_overlay'}:
        # the reaction's bodies ride along, recorded and sampled but never driven (no reaction alphas, no hits), so the
        # overlay can draw every collision shape over the run (the run with every collision
        # and dynamics overlay drawn)
        bodies = {'reaction_bodies': spec['bodies'], 'reaction_constraints': spec['constraints'],
                  'tm_bones': [b['bone'] for b in spec['bodies']]}
        tk = os.path.join(d, 'dynamics')
        rec, loop, n = dynamics_take(rig, resolution, started, tk, TAG + '_dynamics', dict(cloth, **bodies))
        if 'dynamics' in shown:
            out.update(dynamics=loop, dynamics_poster=poster(loop, n // 4, os.path.join(d, 'dynamics.jpg')))
        if 'dynamics_overlay' in shown:
            import loop_cut
            cut = loop_cut.window(tk)
            if cut is None:
                raise RuntimeError('%s: the dynamics take has no run to loop' % rig)
            over = os.path.join(d, 'dynamics_overlay.mp4')
            dyn_overlay.main([tk, dyn_hero.record_path(dyn_hero.resolve(rig), TAG + '_dynamics'), over,
                                   '--layers', 'dynamics,reaction', '--from', str(cut[1]), '--to', str(cut[2])])
            out.update(dynamics_overlay=over,
                       dynamics_overlay_poster=poster(over, (cut[2] - cut[1]) // 4,
                                                      os.path.join(d, 'dynamics_overlay.jpg')))
    if 'dynamics_heat' in shown:
        rec, heat, n = dynamics_take(rig, resolution, started, os.path.join(d, 'heat'), TAG + '_heat',
                                     dict(cloth, heat=True), overlay=dyn_heat.legend(os.path.join(d, 'heat_legend.png')))
        # every RENDERED frame measured (the drive names each row's MRQ output frame): an unmeasured one would show a
        # stale tint, not "no motion"
        # rows before MRQ's first output frame (out -1: warm-up, the cloth's first sample) are never rendered
        failed = [r['f'] for r in rec['frames']
                  if r.get('out') is not None and r['out'] >= 0 and not (r.get('heat') or {}).get('ok')]
        measured = {r.get('out') for r in rec['frames'] if (r.get('heat') or {}).get('ok')}
        unmeasured = [i for i in range(n) if i not in measured]
        if failed or unmeasured:
            raise RuntimeError('%s: the heat take has %d failed rows (first %s) and %d of %d rendered frames unmeasured '
                               '(first %s)' % (rig, len(failed), failed[:3], len(unmeasured), n, unmeasured[:3]))
        out.update(dynamics_heat=heat, dynamics_heat_poster=poster(heat, n // 4, os.path.join(d, 'dynamics_heat.jpg')))
    return out


def generator():
    """A fingerprint of the sources that generate and judge the reaction: two heroes' entries on one page must share it
    ('same generated setup'). `git describe --dirty` cannot tell two dirty trees apart."""
    import hashlib
    sha = hashlib.sha1()
    for name in GENERATOR_SOURCES:
        with open(os.path.join(HERE, name), 'rb') as f:
            sha.update(f.read())
    return sha.hexdigest()[:12]


def _record(rig, entry):
    os.makedirs(OUT, exist_ok=True)
    rec = json.load(open(RECORD)) if os.path.exists(RECORD) else {}
    rec[rig] = entry
    with open(RECORD + '.tmp', 'w') as f:
        json.dump(rec, f, indent=1, sort_keys=True)
    os.replace(RECORD + '.tmp', RECORD)


def release(rig, resolution=None):
    import editor_session
    editor_session.yield_cpu()      # its cuts and encodes yield to the desktop, as the editor does
    started = time.time()
    commit = subprocess.run(['git', '-C', REPO, 'describe', '--always', '--dirty'], capture_output=True, text=True,
                            check=True).stdout.strip()
    entry = {'started': time.strftime('%Y-%m-%dT%H:%M:%S'), 'commit': commit, 'generator': generator(),
             'status': 'RUNNING'}
    _record(rig, entry)
    try:
        spec = onboard(rig)
        entry['cloth_rest'] = rest = cloth_rest_gate(rig)
        if not rest['pass']:
            raise RuntimeError('%s: the cloth at rest is inside its colliders: %s' % (rig, rest))
        hit_reaction_sweep.build(rig)
        entry['verdict'] = verdict = hit_reaction_run.run(rig, TAG)
        entry['status'] = 'FAIL'
        entry['dynamics_gate'] = dynamics = dynamics_gate(rig)
        if verdict['verdict']['pass'] and dynamics['pass']:
            take, files = media(rig, resolution, started, verdict['records'][0], spec)
            # The page's figures are the TAKE's own: the matrix and the render are separate drives (Aurora's left-arm
            # impact 16.0 cm in one, 14.9 in the other). The take is judged in place of the
            # matrix's hit run (hit_reaction_analyze.main take=), and must pass too.
            entry['take_verdict'] = shown = hit_reaction_analyze.main(verdict['records'], take=take)
            entry['garment'] = garment = garment_gate(rig, started)
            if shown['verdict']['pass'] and garment['pass']:
                entry['summary'] = dict(hit_reaction_analyze.summary(shown), **release_figures(spec, dynamics))
                entry['media'] = {k: os.path.relpath(v, OUT).replace('\\', '/') for k, v in files.items()}
                entry['status'] = 'PASS'
    except Exception as e:
        entry['status'], entry['error'] = 'ERROR', repr(e)[:2000]
        entry.pop('media', None)
        raise
    finally:
        entry['seconds'] = round(time.time() - started)
        _record(rig, entry)
        print('RELEASE %s %s in %d s -> %s' % (rig, entry['status'], entry['seconds'], RECORD), flush=True)
    return entry['status'] == 'PASS'


if __name__ == '__main__':
    rigs, args = dyn_hero.heroes_arg(sys.argv[1:])
    res = next((tuple(int(v) for v in a[len('--res='):].split('x')) for a in args if a.startswith('--res=')), None)
    sys.exit(0 if all([release(rig, res) for rig in rigs]) else 1)
