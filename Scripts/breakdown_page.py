"""Build captures/breakdown/index.html, the breakdown page, from its template (Scripts/breakdown_page.html) and the
records, so every figure on it comes from a run and the page is rebuilt, never hand-edited.

    python Scripts/breakdown_page.py

Template tokens:
  {{<Rig>.<key>}}         a figure from the hero's release summary (hit_reaction_release.py ->
                          captures/hit-reaction/release.json, hit_reaction_analyze.summary); a list joins with ' · '
  {{<Rig>.media.<key>}}   that release's media, copied beside the page as <short>_<key>.<ext>
  {{file:<repo path>}}    any other file, copied beside the page under its own name
  {{tests}}               the host-side suite's count as README.md states it (test_repo_integrity holds the README to
                          the collected count)
  {{lineup}}              every released hero running side by side: each PASS release's run strip
  {{lineup_poster}}       (hit_reaction_release.run_strip), in name order, joined into lineup.mp4 / lineup.jpg; a hero
                          joins it by being released, and the page needs at least two
A token that does not resolve (a hero whose release failed has no figures) stops the build: nothing is published
half-true.
"""
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dyn_hero  # noqa: E402
import hit_reaction_release  # noqa: E402

REPO = os.path.dirname(HERE)
TEMPLATE = os.path.join(HERE, 'breakdown_page.html')
PAGE = os.path.join(REPO, 'captures', 'breakdown')
TOKEN = re.compile(r'\{\{\s*([^}]+?)\s*\}\}')
LINEUP = 'lineup'
LINEUP_MAX_W = 1920         # a long lineup is scaled down to this width


def lineup_heroes(release):
    """The heroes in the lineup: every PASS release with a run strip on disk, in name order."""
    return [r for r in sorted(release) if (release[r] or {}).get('status') == 'PASS'
            and os.path.isfile(os.path.join(hit_reaction_release.OUT, (release[r].get('media') or {}).get('run', '')))]


def lineup(release, out_dir):
    """(video name, poster name) of the heroes' run strips side by side in out_dir, or None with fewer than two. The
    strips share the drive's clock (hit_reaction_release.run_strip): the shortest sets the length."""
    import subprocess
    rigs = lineup_heroes(release)
    if len(rigs) < 2:
        return None
    srcs = [os.path.join(hit_reaction_release.OUT, release[r]['media']['run']) for r in rigs]
    w = len(rigs) * hit_reaction_release.LINEUP_STRIP[0]
    scale = ',scale=%d:-2:flags=lanczos' % LINEUP_MAX_W if w > LINEUP_MAX_W else ''
    video, still = os.path.join(out_dir, 'lineup.mp4'), os.path.join(out_dir, 'lineup.jpg')
    args = ['ffmpeg', '-y', '-loglevel', 'error']
    for s in srcs:
        args += ['-i', s]
    graph = ''.join('[%d:v]' % i for i in range(len(srcs))) + 'hstack=inputs=%d:shortest=1%s[v]' % (len(srcs), scale)
    subprocess.run(args + ['-filter_complex', graph, '-map', '[v]', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                           '-crf', '16', video], check=True)
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-ss', '1.0', '-i', video, '-frames:v', '1', '-q:v', '3',
                    still], check=True)
    return os.path.basename(video), os.path.basename(still)


def tests_passed(readme):
    m = re.search(r'-> (\d+) passed', open(readme, encoding='utf-8').read())
    return m and m.group(1)


def resolve(token, release, copy):
    """The text for one token, or None when it does not resolve. copy(src, name) puts a file beside the page."""
    if token == 'tests':
        return tests_passed(os.path.join(REPO, 'README.md'))
    if token.startswith('file:'):
        src = os.path.join(REPO, token[len('file:'):])
        return copy(src, os.path.basename(src)) if os.path.isfile(src) else None
    rig, _, key = token.partition('.')
    entry = release.get(rig) or {}
    if entry.get('status') != 'PASS':       # running, failed or raised: no figures, no media
        return None
    if key.startswith('media.'):
        rel = (entry.get('media') or {}).get(key[len('media.'):])
        if not rel:
            return None
        src = os.path.join(hit_reaction_release.OUT, rel)
        name = '%s_%s%s' % (dyn_hero.short_name(rig).lower(), key[len('media.'):], os.path.splitext(rel)[1])
        return copy(src, name) if os.path.isfile(src) else None
    value = (entry.get('summary') or {}).get(key)
    if value is None:
        return None
    return ' · '.join(str(v) for v in value) if isinstance(value, list) else str(value)


def build(template, release, out_dir):
    """(html, [unresolved tokens]); files the tokens name are copied into out_dir. A failed release carries no summary
    or media (hit_reaction_release), so its tokens are unresolved."""
    names = {}

    def copy(src, name):
        if names.setdefault(name, src) != src:
            raise ValueError('two files would be published as %s: %s, %s' % (name, names[name], src))
        shutil.copy2(src, os.path.join(out_dir, name))
        return name

    missing = []
    made = {}

    def sub(m):
        if m.group(1) in (LINEUP, LINEUP + '_poster'):
            if 'lineup' not in made:
                made['lineup'] = lineup(release, out_dir)
            text = made['lineup'] and made['lineup'][m.group(1) != LINEUP]
        else:
            text = resolve(m.group(1), release, copy)
        if text is None:
            missing.append(m.group(1))
            return m.group(0)
        return text

    html = TOKEN.sub(sub, template)
    # one generated setup: every hero the page names was released from the same generator sources
    rigs = set(t.split('.', 1)[0] for t in TOKEN.findall(template) if '.' in t and not t.startswith('file:'))
    if LINEUP in TOKEN.findall(template):
        rigs |= set(lineup_heroes(release))         # the lineup's heroes are on the page too
    gens = {r: (release.get(r) or {}).get('generator') for r in sorted(rigs)}
    if len(set(gens.values())) > 1:
        missing.append('one generator for every hero (%s)' % ', '.join('%s %s' % kv for kv in sorted(gens.items())))
    return html, missing


def main():
    """Built whole into a staging folder and swapped in only when every token resolved: a failed build leaves the
    previous page and its media exactly as they were."""
    release = json.load(open(hit_reaction_release.RECORD)) if os.path.exists(hit_reaction_release.RECORD) else {}
    stage = PAGE + '.staging'
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage)
    html, missing = build(open(TEMPLATE, encoding='utf-8').read(), release, stage)
    if missing:
        shutil.rmtree(stage)
        raise SystemExit('breakdown page NOT built, unresolved: %s' % ', '.join(missing))
    with open(os.path.join(stage, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(html)
    shutil.rmtree(PAGE, ignore_errors=True)
    os.replace(stage, PAGE)
    print('BREAKDOWN %s (%d files)' % (os.path.join(PAGE, 'index.html'), len(os.listdir(PAGE)) - 1))


if __name__ == '__main__':
    main()
