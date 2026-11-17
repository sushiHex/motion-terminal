#!/usr/bin/env python
"""Build captures/_review/golden.html — the shot-by-shot review page, before/after.

WHY THIS FILE EXISTS. The page it writes had been produced ad hoc and lived only in `captures/`,
which is gitignored — so the artifact survived and the invocation that made it did not. That is the
exact gap this project closes everywhere else ("if pixels ship, the invocation that made them ships
too"), and it bit immediately: the abandoned page was still showing captions
and evidence keys that had been renamed days earlier, with no way to refresh it but to hand-edit
10 KB of HTML.

WHAT IT SHOWS. Every shipped shot, read straight out of shots_manifest.json.

WHAT IT REFUSES TO PRETEND. Numeric claims are resolved live through the manifest's citation keys
against render_evidence.json AND army_config.json; a key that stops resolving renders as UNRESOLVED
rather than vanishing.

    python Scripts/review_page.py            # rebuild page, encode any missing previews
    python Scripts/review_page.py --force    # re-encode every preview
"""
import argparse
import html
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REVIEW = os.path.join(REPO, 'captures', '_review')
CLIPS = os.path.join(REVIEW, 'golden')
MANIFEST = os.path.join(REPO, 'Plugins', 'MotionTerminal', 'Tests', 'shots_manifest.json')
EVIDENCE = os.path.join(REPO, 'Plugins', 'MotionTerminal', 'Tests', 'render_evidence.json')
ARMY = os.path.join(REPO, 'Plugins', 'MotionTerminal', 'Tests', 'army_config.json')


def encode(src_dir, lo, hi, fps_out, out_path, force=False):
    """720px CRF28 preview of frames [lo, hi) at fps_out. Returns True if the clip exists after."""
    if os.path.exists(out_path) and not force:
        return True
    frames = [f for f in sorted(os.listdir(src_dir)) if f.endswith('.png')] if os.path.isdir(src_dir) else []
    if not frames:
        return False
    # The renders number from 0 with a 4-digit pad; -start_number selects the window and -frames:v
    # bounds it, so the preview is exactly the shot's own frame_range and not the whole take.
    pattern = os.path.join(src_dir, 'frame_%04d.png')
    cmd = ['ffmpeg', '-y', '-loglevel', 'error',
           '-framerate', str(fps_out), '-start_number', str(lo), '-i', pattern,
           '-frames:v', str(max(0, hi - lo)),
           '-vf', 'scale=720:-2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '28',
           out_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write('ffmpeg failed for %s:\n%s\n' % (out_path, proc.stderr[-600:]))
        return False
    return os.path.exists(out_path)


def resolve(dotted, evidence, army):
    """A manifest citation key -> its value, or None. Mirrors shot_check's two sources."""
    head, _, tail = dotted.partition('.')
    for source in (evidence, army):
        node = source.get(head)
        if isinstance(node, dict) and tail in node:
            return node[tail]
    return None


def card(shot, clip_rel, evidence, army, *, missing=False):
    sid = html.escape(shot['shot_id'])
    title = html.escape(shot['title'])
    lo, hi = shot['frame_range']
    dur = (hi - lo) / 30.0
    bits = []
    bits.append('<div class="card">')
    if missing:
        bits.append('<div class="absent">no take on disk</div>')
    else:
        bits.append('<video src="%s" controls preload="none" loop></video>' % html.escape(clip_rel))
    bits.append('<div class="body"><div class="top"><h2>%s</h2><span class="dur">%.1fs</span></div>'
                % (title, dur))
    bits.append('<div class="badge shipped">SHIPPED</div>')
    bits.append('<div class="caption">%s</div>' % html.escape(shot.get('caption_text', '')))
    cites = shot.get('caption_cites') or []
    if cites:
        for c in cites:
            rows = []
            for key in c.get('keys', []):
                val = resolve(key, evidence, army)
                shown = ('<span class="bad">UNRESOLVED</span>' if val is None
                         else html.escape(str(val)))
                rows.append('<span class="k">%s = %s</span>' % (html.escape(key), shown))
            bits.append('<div class="claim">claim <b>%s</b>%s</div>'
                        % (html.escape(c.get('claim', '')), ''.join(rows)))
    else:
        bits.append('<div class="nocite">No numeric claim &mdash; nothing to cite.</div>')
    src = html.escape(shot['source_dir'])
    bits.append('<div class="meta">%s<br>%s<br>frames [%d, %d) @ %d fps in</div></div></div>'
                % (sid, src, lo, hi, shot['fps_in']))
    return ''.join(bits)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--force', action='store_true', help='re-encode every preview')
    args = ap.parse_args()

    manifest = json.load(open(MANIFEST, encoding='utf-8'))
    evidence = json.load(open(EVIDENCE, encoding='utf-8'))
    army = json.load(open(ARMY, encoding='utf-8'))
    os.makedirs(CLIPS, exist_ok=True)

    shots = sorted((s for s in manifest['shots'] if s.get('status') == 'shipped'),
                   key=lambda s: s.get('order', 0))
    fps_out = manifest['fps_out']

    rows, n_cited = [], 0
    for shot in shots:
        sid = shot['shot_id']
        lo, hi = shot['frame_range']
        n_cited += len(shot.get('caption_cites') or [])

        shipped_clip = os.path.join(CLIPS, '%s.mp4' % sid)
        encode(os.path.join(REPO, shot['source_dir']), lo, hi, fps_out, shipped_clip, args.force)
        left = card(shot, 'golden/%s.mp4' % sid, evidence, army,
                    missing=not os.path.exists(shipped_clip))
        rows.append('<div class="pair">%s</div>' % left)
        print('%-22s shipped=%s' % (sid, os.path.exists(shipped_clip)))

    total = sum((s['frame_range'][1] - s['frame_range'][0]) / fps_out for s in shots)
    page = PAGE % {
        'summary': '%d shots &middot; %.1fs &middot; %d cited value(s)'
                   % (len(shots), total, n_cited),
        'rows': ''.join(rows),
    }
    out = os.path.join(REVIEW, 'golden.html')
    with open(out, 'w', encoding='utf-8') as fh:
        fh.write(page)
    print('\nwrote %s (%d shots)' % (out, len(shots)))


PAGE = """<meta charset="utf-8"><title>MotionTerminal &mdash; shot review page</title><style>
:root{--bg:#0e1013;--card:#171a1e;--line:#282d34;--fg:#e8eaed;--dim:#98a1ab;--go:#5ec27a;
      --warn:#e0a33e;--acc:#6aa9e0;--new:#5ec27a}
*{box-sizing:border-box}
body{margin:0;padding:34px;background:var(--bg);color:var(--fg);
     font:15px/1.55 -apple-system,Segoe UI,Roboto,sans-serif}
h1{margin:0 0 6px;font-size:27px}
.sub{color:var(--dim);max-width:960px;margin-bottom:22px}
.hero{background:var(--card);border:1px solid var(--line);border-radius:11px;padding:18px;
      margin-bottom:30px;max-width:1100px}
.hero video{width:100%%;border-radius:7px;background:#000;display:block}
.hero .cap{color:var(--dim);font-size:13px;margin-top:10px;font-family:ui-monospace,Consolas,monospace}
.pair{display:grid;gap:18px;grid-template-columns:repeat(auto-fit,minmax(430px,1fr));margin-bottom:26px}
.card{background:var(--card);border:1px solid var(--line);border-radius:11px;overflow:hidden;
      display:flex;flex-direction:column}
.card video{width:100%%;display:block;background:#000;cursor:pointer}
.absent{padding:46px 12px;text-align:center;color:var(--warn);background:#101317;
        font-family:ui-monospace,Consolas,monospace;font-size:13px}
.body{padding:14px 17px 17px;flex:1;display:flex;flex-direction:column}
.top{display:flex;justify-content:space-between;align-items:baseline;gap:10px;margin-bottom:6px}
h2{margin:0;font-size:16.5px}
.dur{color:var(--acc);font-family:ui-monospace,Consolas,monospace;font-size:13px;white-space:nowrap}
.badge{display:inline-block;align-self:flex-start;font-family:ui-monospace,Consolas,monospace;
       font-size:11px;letter-spacing:.06em;padding:2px 7px;border-radius:4px;margin-bottom:8px;
       background:#1d3b28;color:var(--new);border:1px solid #2f5c3f}
.badge.shipped{background:#1b2027;color:var(--dim);border-color:var(--line)}
.caption{border-left:3px solid var(--acc);padding:7px 0 7px 11px;margin:4px 0 12px;font-size:14.5px}
.claim{background:#12161a;border:1px solid var(--line);border-radius:6px;padding:9px 11px;
       margin-bottom:9px;font-size:13px}
.claim b{color:var(--go)}
.k{color:var(--dim);font-family:ui-monospace,Consolas,monospace;font-size:12px;display:block;
   margin-top:4px;word-break:break-all}
.nocite{color:var(--dim);font-size:13px;font-style:italic;margin-bottom:9px}
.bad{color:var(--warn)}
.meta{color:var(--dim);font-size:12.5px;font-family:ui-monospace,Consolas,monospace;
      border-top:1px solid var(--line);padding-top:9px;margin-top:auto}
footer{margin-top:36px;color:var(--dim);font-size:13.5px;border-top:1px solid var(--line);
       padding-top:16px;max-width:960px}
code{background:#22262b;padding:1px 5px;border-radius:3px;font-size:12.5px}
</style><h1>The golden shot sequence &mdash; every shot that ships</h1>
<div class="sub">Read straight out of <code>shots_manifest.json</code> by <code>Scripts/review_page.py</code>.
Each preview is cut to that shot&rsquo;s own <code>frame_range</code> and played at <code>fps_out</code>, so what you
watch is what the MP4 would contain, at real duration. Numeric claims are resolved live through the
manifest&rsquo;s citation keys &mdash; one that stopped resolving reads <span class="bad">UNRESOLVED</span> here rather
than quietly disappearing.</div>
<div class="hero"><video src="../shots/shots.mp4" controls preload="metadata"></video>
<div class="cap">captures/shots/shots.mp4 &middot; the assembled shot sequence &mdash; still built from the SHIPPED
column, gitignored and regenerable from shot_assemble.py</div></div>
<div class="sub"><b>%(summary)s</b></div>
%(rows)s
<footer>Previews are 720&nbsp;px / CRF&nbsp;28 for quick review; the full-resolution frames and the
1920&times;1080 MP4 are on disk. All of <code>captures/</code> is gitignored, so nothing here ships with the
repo &mdash; what ships is the manifest, the evidence file, the gates that bind them, and now the script
that builds this page.</footer>
"""

if __name__ == '__main__':
    main()
