"""Is this editor ready for the scripts? Thirty seconds of checks before a long run is trusted to it.

    python Scripts/editor_doctor.py          # every check; exit 1 if any fails

Run with the editor up. Each check exercises one thing the authoring and drive scripts depend on (the transport, a
toolset, an engine behaviour they assume) and says what it found, so a long onboarding or a render does not spend
ten minutes discovering that the server is the wrong engine, a toolset is not registered, or the template no longer
compiles clean. Every check reads; none builds, saves or deletes.

A check is a name and a function of the editor connection returning a short description, or raising. Add one when
a script starts depending on something a fresh editor might not provide.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dyn_hero  # noqa: E402

ENGINE = '5.9'
GRAPH_TOOLS = 'MotionTerminal.AnimGraphTools'
ASSET_TOOLS = 'MotionTerminal.AnimAssetTools'
BUILD_TOOLS = 'MotionTerminal.AnimGraphBuildTools'
COMPILED = ('clean', 'warnings')


def _ok(reply, what):
    if not isinstance(reply, dict) or not reply.get('ok'):
        raise RuntimeError('%s: %r' % (what, (reply or {}).get('error') if isinstance(reply, dict) else reply))
    return reply


def engine(ops):
    """The editor answering is the engine the project targets."""
    version = ops.py('unreal.SystemLibrary.get_engine_version()')
    if not str(version).startswith(ENGINE):
        raise RuntimeError('the editor is engine %s, the project targets %s' % (version, ENGINE))
    return 'engine %s' % version


def values_return(ops):
    """A script's final expression comes back as a value, decoded once."""
    got = ops.py("import json\njson.dumps({'a': [1, 2.5, 'x'], 'b': None})")
    if got != {'a': [1, 2.5, 'x'], 'b': None}:
        raise RuntimeError('a script value came back as %r' % (got,))
    return 'script values round-trip'


def template_graph(ops):
    """The read toolset answers, and the template Animation Blueprint is compiled with a graph. (The template has
    no target skeleton of its own: onboarding duplicates it and sets the hero's.)"""
    reply = _ok(ops.call(GRAPH_TOOLS, 'DescribeAnimGraph', AnimBlueprintPath=dyn_hero.TEMPLATE_ABP, PathsJson=''),
                'DescribeAnimGraph %s' % dyn_hero.TEMPLATE_ABP)
    if reply.get('compile_status') not in COMPILED:
        raise RuntimeError('%s compile status is %s' % (dyn_hero.TEMPLATE_ABP, reply.get('compile_status')))
    if not reply.get('nodes'):
        raise RuntimeError('%s described with no nodes' % dyn_hero.TEMPLATE_ABP)
    return '%s: %s, %d nodes, %d links' % (dyn_hero.TEMPLATE_ABP, reply['compile_status'], len(reply['nodes']),
                                          len(reply.get('links', [])))


def template_skeleton(ops):
    """The asset toolset answers, and the source skeleton the clips are retargeted from reads with one root and a
    mode on every bone."""
    reply = _ok(ops.call(ASSET_TOOLS, 'DescribeSkeleton', SkeletonPath=dyn_hero.SOURCE_SKELETON), 'DescribeSkeleton')
    bones = reply.get('bones') or []
    roots = [b['name'] for b in bones if not b['parent']]
    unknown = [b['name'] for b in bones if b.get('translation_retargeting') in (None, '', 'Unknown')]
    if len(roots) != 1 or unknown:
        raise RuntimeError('skeleton %s: roots %s, bones with no mode %s' % (dyn_hero.SOURCE_SKELETON, roots,
                                                                           unknown[:5]))
    return '%s: %d bones, root %s' % (dyn_hero.SOURCE_SKELETON, len(bones), roots[0])


def build_tools_refuse(ops):
    """The build toolset answers, by refusing what it must refuse (nothing is created)."""
    reply = ops.call(BUILD_TOOLS, 'SaveAnimBlueprint', AssetPath='/Game/MT/Tmp_MT_DoctorNeverBuilt')
    if not isinstance(reply, dict) or reply.get('ok') or 'not in memory' not in str(reply.get('error')):
        raise RuntimeError('SaveAnimBlueprint of an asset never built replied %r' % (reply,))
    return 'the build toolset answers and refuses an asset it did not build'


def log_marker(ops):
    """A line written through the editor is found in its log file (how a driver's summary line is read)."""
    log = ops.log()
    return 'log marker found in %s' % os.path.basename(log.path)


def stat_window(ops):
    """A stat-group window opens, collects frames and closes."""
    read = ops.sample_stats('Anim', 4, timeout=20.0)
    if not read.get('frames'):
        raise RuntimeError('the Anim stat window collected no frames: %r' % (read,))
    return 'Anim stat window: %d frames, %d stats' % (read['frames'], len(read.get('stats') or {}))


CHECKS = [('engine', engine), ('script values', values_return), ('template graph', template_graph),
          ('template skeleton', template_skeleton), ('build tools', build_tools_refuse), ('log marker', log_marker),
          ('stat window', stat_window)]


def run(ops, checks=CHECKS, log=print):
    """Run every check (one failing does not stop the next) and return [(name, ok, detail)]."""
    out = []
    for name, check in checks:
        try:
            detail, ok = check(ops), True
        except Exception as exc:          # noqa: BLE001 - a check's failure is its result
            detail, ok = '%s: %s' % (type(exc).__name__, exc), False
        out.append((name, ok, detail))
        log('%s  %-18s %s' % ('ok  ' if ok else 'FAIL', name, detail))
    return out


if __name__ == '__main__':
    import editor_ops
    results = run(editor_ops.shared(timeout=120))
    failed = [name for name, ok, _d in results if not ok]
    print('EDITOR_DOCTOR ' + json.dumps({'ok': not failed, 'failed': failed, 'checks': len(results)}))
    sys.exit(1 if failed else 0)
