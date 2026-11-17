import editor_doctor as doc


class _Log:
    path = 'C:/x/Saved/Logs/Editor.log'


class _Ops:
    """A healthy editor, with one answer overridable per test."""

    def __init__(self, **over):
        self.over = over

    def py(self, script):
        if 'get_engine_version' in script:
            return self.over.get('version', '5.9.0-60351404+++UE5+Release-5.9')
        return self.over.get('value', {'a': [1, 2.5, 'x'], 'b': None})

    def call(self, toolset, name, **arguments):
        if name == 'DescribeAnimGraph':
            return self.over.get('graph', {'ok': True, 'compile_status': 'clean', 'skeleton': '/Game/S/SK',
                                           'nodes': [{}, {}], 'links': [[]]})
        if name == 'DescribeSkeleton':
            return self.over.get('skeleton', {'ok': True, 'bones': [
                {'name': 'root', 'parent': '', 'translation_retargeting': 'Animation'},
                {'name': 'pelvis', 'parent': 'root', 'translation_retargeting': 'Skeleton'}]})
        if name == 'SaveAnimBlueprint':
            return self.over.get('save', {'ok': False, 'error': '/Game/MT/X is not in memory: nothing was built'})
        raise AssertionError(name)

    def log(self):
        if 'log' in self.over:
            raise self.over['log']
        return _Log()

    def sample_stats(self, group, frames, timeout=30.0):
        return self.over.get('stats', {'frames': 4, 'stats': {'A': {}}})


def _run(**over):
    return doc.run(_Ops(**over), log=lambda line: None)


def test_a_healthy_editor_passes_every_check():
    results = _run()
    assert [name for name, _ok, _d in results] == [name for name, _fn in doc.CHECKS]
    assert all(ok for _n, ok, _d in results), results


def test_each_fault_fails_its_own_check_and_only_that_one():
    faults = {
        'engine': dict(version='5.8.1-0'),
        'script values': dict(value='{"a": 1}'),                          # a value that came back undecoded
        'template graph': dict(graph={'ok': True, 'compile_status': 'error', 'skeleton': '/Game/S/SK',
                                      'nodes': [{}], 'links': []}),
        'template skeleton': dict(skeleton={'ok': True, 'bones': [
            {'name': 'a', 'parent': '', 'translation_retargeting': 'Animation'},
            {'name': 'b', 'parent': '', 'translation_retargeting': 'Animation'}]}),   # two roots
        'build tools': dict(save={'ok': True}),                           # it saved what was never built
        'log marker': dict(log=RuntimeError('log marker never reached the file')),
        'stat window': dict(stats={'frames': 0}),
    }
    assert sorted(faults) == sorted(name for name, _fn in doc.CHECKS)     # every check has a fault that trips it
    for name, over in faults.items():
        failed = [n for n, ok, _d in _run(**over) if not ok]
        assert failed == [name], (name, failed)


def test_a_failing_check_reports_why_and_the_rest_still_run():
    results = _run(version='5.8.1-0', stats={'frames': 0})
    by_name = {n: (ok, d) for n, ok, d in results}
    assert by_name['engine'] == (False, 'RuntimeError: the editor is engine 5.8.1-0, the project targets 5.9')
    assert not by_name['stat window'][0] and by_name['log marker'][0]
    assert len(results) == len(doc.CHECKS)


def test_a_toolset_that_is_not_registered_fails_the_checks_that_need_it():
    class Unregistered(_Ops):
        def call(self, toolset, name, **arguments):
            raise RuntimeError('Unknown toolset %s' % toolset)
    failed = [n for n, ok, _d in doc.run(Unregistered(), log=lambda line: None) if not ok]
    assert failed == ['template graph', 'template skeleton', 'build tools']
