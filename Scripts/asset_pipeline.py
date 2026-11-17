"""One way to make a generated asset, whatever its kind.

    realize(ops, kind, path, spec)

    refuse a target that is not git-ignored  ->  delete what is there  ->  build  ->  describe (a separate read)
    ->  mismatch(spec, described)  ->  save, only if nothing differs  ->  a record of what was made from what

Every generated asset in this project is derived: deleted and rebuilt from a spec, never edited in place, and
accepted on a description read back by code that shares nothing with the builder (docs/architecture.md). This
module is that sequence written once. A kind supplies four functions; the order, the guards and the cleanup are
here, so no builder script can skip the delete, save a graph that is not the spec, or leave a failed build for
the next step to find.

A kind is registered where its pure parts live (the AnimGraph kind is below; a new asset kind adds one Kind).
Design: docs/specs/2026-11-12-asset-pipeline-design.md.
"""
import hashlib
import json
import os
import subprocess

from asset_delete import HELD, while_held

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class NotTheSpec(RuntimeError):
    """The asset was built, and its description is not its spec. Nothing was saved."""

    def __init__(self, kind, path, gaps):
        super().__init__('%s %s was built but is not its spec (not saved): %s' % (kind, path, '; '.join(gaps)))
        self.kind, self.path, self.gaps = kind, path, list(gaps)


class Kind:
    """One kind of generated asset: how to build it, read it back, compare and save it.

    build(ops, path, spec) leaves the asset in memory, unsaved, and raises when the build fails.
    describe(ops, path, spec) -> the description, read by code that shares nothing with build.
    mismatch(spec, described) -> [difference], pure; empty accepts.
    save(ops, path) saves what build left in memory, and raises when it does not reach disk."""

    def __init__(self, name, build, describe, mismatch, save):
        self.name, self.build, self.describe, self.mismatch, self.save = name, build, describe, mismatch, save


def digest(value):
    """A spec's identity: the same spec gives the same digest whatever its key order."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:16]


def content_file(path, repo=REPO):
    """/Game/Dir/Name -> <repo>/Content/Dir/Name.uasset; any other root is refused."""
    if not path.startswith('/Game/') or path.endswith('/') or '.' in path.rsplit('/', 1)[-1]:
        raise ValueError('%r is not a /Game/Dir/Name asset path' % (path,))
    return os.path.join(repo, 'Content', *path[len('/Game/'):].split('/')) + '.uasset'


def require_gitignored(path, repo=REPO):
    """Refuse a target whose file git would track: generated assets bind licensed content and are rebuilt, not
    versioned. Asked BEFORE anything is written, and failing closed (git missing or erroring refuses too)."""
    target = content_file(path, repo)
    try:
        code = subprocess.call(['git', 'check-ignore', '--quiet', target], cwd=repo,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise RuntimeError('REFUSING TO BUILD %s: git check-ignore could not run (%s)' % (path, exc)) from exc
    if code != 0:
        raise RuntimeError('REFUSING TO BUILD %s: %s is not git-ignored (git check-ignore exit %d)'
                           % (path, target, code))


def delete_asset(ops, path):
    """Delete an asset so it can be built again, and prove it is gone from the registry AND the disk.

    Loaded by one editor call and deleted by the next: a delete that loads the package in its own tick keeps its
    file. Waits out the asset registry's startup scan first (a fresh editor answered "does not exist" for an asset
    on disk, the delete was skipped and the create failed). The disk check runs whether or not this attempt deleted
    anything, so a retry cannot pass on an asset an earlier attempt only unregistered. Retried while held."""
    load = ("unreal.AssetRegistryHelpers.get_asset_registry().wait_for_completion()\n"
            "if unreal.EditorAssetLibrary.does_asset_exist(%r):\n"
            "    unreal.EditorAssetLibrary.load_asset(%r)\n"
            "True\n" % (path, path))
    delete = ("import os\n"
              "p = %r\n"
              "if unreal.EditorAssetLibrary.does_asset_exist(p):\n"
              "    unreal.SystemLibrary.collect_garbage()\n"
              "    assert unreal.EditorAssetLibrary.delete_asset(p), 'could not delete ' + p\n"
              "    unreal.SystemLibrary.collect_garbage()\n"      # or the create after still finds the old object
              "f = os.path.join(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_content_dir()), "
              "p[len('/Game/'):] + '.uasset')\n"
              "assert not (unreal.EditorAssetLibrary.does_asset_exist(p) or os.path.exists(f)), p + ' %s'\n"
              "True\n" % (path, HELD))

    def attempt():
        ops.py(load)
        return ops.py(delete)
    while_held(attempt)


def realize(ops, kind, path, spec, delete=delete_asset, ignored=require_gitignored):
    """Make the asset at `path` from `spec`, or raise and leave nothing there.

    Returns the record {'kind', 'path', 'spec_digest', 'described'}. Raises NotTheSpec when the build's description
    differs from the spec, and whatever build or save raised otherwise; in every failing case the asset is deleted
    again (a failed delete is printed, never allowed to hide the failure that caused it)."""
    spec_digest = digest(spec)      # first: a spec that cannot be digested is refused before anything is deleted
    ignored(path)
    delete(ops, path)
    try:
        kind.build(ops, path, spec)
        described = kind.describe(ops, path, spec)
        gaps = kind.mismatch(spec, described)
        if gaps:
            raise NotTheSpec(kind.name, path, gaps)
        kind.save(ops, path)
    except Exception:
        try:
            delete(ops, path)
        except Exception as exc:          # noqa: BLE001 - the failure being reported is the build's
            print('ASSET_PIPELINE the failed %s at %s was not deleted: %s' % (kind.name, path, str(exc)[:300]),
                  flush=True)
        raise
    return {'kind': kind.name, 'path': path, 'spec_digest': spec_digest, 'described': described}


# --- kinds -------------------------------------------------------------------------------------------------------

def _tool(ops, toolset, name, **arguments):
    reply = ops.call(toolset, name, **arguments)
    if not reply.get('ok'):
        raise RuntimeError('%s.%s: %s' % (toolset, name, reply.get('error')))
    return reply


def animgraph(folded_ok=()):
    """The AnimGraph kind (animgraph_spec): spec -> BuildAnimBlueprint -> DescribeAnimGraph -> mismatch ->
    SaveAnimBlueprint. folded_ok: the compiled values this graph's caller knows cannot be read back."""
    import animgraph_spec as ag
    return Kind(
        'animgraph',
        build=lambda ops, path, spec: _tool(ops, ag.BUILD_TOOLS, 'BuildAnimBlueprint', AssetPath=path,
                                            SpecJson=json.dumps(spec)),
        describe=lambda ops, path, spec: ops.call(ag.READ_TOOLS, 'DescribeAnimGraph', AnimBlueprintPath=path,
                                                  PathsJson=json.dumps(ag.paths(spec))),
        mismatch=lambda spec, described: ag.mismatch(spec, described, folded_ok),
        save=lambda ops, path: _tool(ops, ag.BUILD_TOOLS, 'SaveAnimBlueprint', AssetPath=path))
