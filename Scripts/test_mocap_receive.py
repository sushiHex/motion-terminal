"""mocap_receive: stage 1 of the intake (docs/specs/2026-11-16-mocap-intake-design.md, sections 4 and 5).

Every delivery here is a few bytes of stand-in text: receive hashes and freezes files, it never parses motion."""
import copy
import hashlib
import json
import os

import pytest

import mocap_receive as mr
import stage_runner as sr

ASF = b':version 1.10\n:name stand-in\n'
AMC = b':FULLY-SPECIFIED\n:DEGREES\n1\nroot 0 0 0 0 0 0\n'


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _signed_cmu():
    lic = mr.cmu_licence_template()
    lic.update(status='checked', checked_by='owner', checked_on='2026-11-16', terms_sha256='a' * 64)
    lic['permits']['public_display'] = True
    return lic


def _manifest(revision=1, **over):
    m = {'batch': 'B001', 'revision': revision, 'supersedes': None if revision == 1 else revision - 1,
         'exchange': 'simulated', 'source': {'kind': 'library', 'library': 'cmu', 'subject': '07'},
         'intended_use': ['public_display', 'derivatives'], 'skeleton_map': 'maps/cmu.json',
         'clips': [{'id': 'walk', 'format': 'asf_amc', 'files': {'asf': 'take/07.asf', 'amc': 'take/07_01.amc'},
                    'sha256': {'asf': _sha(ASF), 'amc': _sha(AMC)}, 'fps_source': 120, 'source_frames': [1, 600],
                    'brief': 'walk 3 m on flat ground'}],
         'disclosed_defects': [{'clip': 'walk', 'kind': 'foot_slide', 'foot': 'l', 'source_frames': [212, 260],
                                'size_cm': 5.0, 'seed': 7}]}
    m.update(over)
    return m


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as fh:
        fh.write(data if isinstance(data, bytes) else json.dumps(data, sort_keys=True).encode())


@pytest.fixture
def world(tmp_path):
    """tests_root with a signed CMU record and a map; a delivery folder per revision; an out_root."""
    tests_root, out_root = str(tmp_path / 'tests'), str(tmp_path / 'out')
    _write(os.path.join(tests_root, 'licenses', 'cmu.json'), _signed_cmu())
    _write(os.path.join(tests_root, 'maps', 'cmu.json'),
           {'library': 'cmu', 'bones': {'root': 'pelvis', 'lfemur': 'thigh_l', 'rfemur': 'thigh_r'}})

    def deliver(manifest, asf=ASF, amc=AMC):
        d = str(tmp_path / 'delivery' / ('r%d' % manifest['revision']))
        _write(os.path.join(d, 'take', '07.asf'), asf)
        _write(os.path.join(d, 'take', '07_01.amc'), amc)
        _write(os.path.join(d, 'manifest.json'), manifest)
        return os.path.join(d, 'manifest.json')

    def receive(manifest_path, **kw):
        return mr.receive(manifest_path, tests_root=tests_root, out_root=out_root,
                          tool_versions=kw.pop('tool_versions', {'blender': '4.5.0'}),
                          settings=kw.pop('settings', {'basis': 'B1'}), **kw)
    return {'tests_root': tests_root, 'out_root': out_root, 'deliver': deliver, 'receive': receive}


# --------------------------------------------------------------------------------------------------------- manifest
def test_the_spec_example_manifest_is_valid():
    assert mr.validate_manifest(_manifest()) is not None


@pytest.mark.parametrize('mutate, reason, state', [
    (lambda m: m['clips'][0].update(format='bvh'), 'format', 'unsupported'),
    (lambda m: m.update(exchange='real'), 'exchange', 'unsupported'),
    (lambda m: m.pop('exchange'), 'manifest', 'refused'),
    (lambda m: m['source'].update(kind='generation'), 'source_kind', 'unsupported'),
    (lambda m: m.update(revision=0), 'manifest', 'refused'),
    (lambda m: m.update(revision=True), 'manifest', 'refused'),
    (lambda m: m.update(supersedes=1), 'supersedes', 'refused'),
    (lambda m: m.update(intended_use=['public_display', 'resale']), 'manifest', 'refused'),
    (lambda m: m.update(intended_use=[]), 'manifest', 'refused'),
    (lambda m: m.update(skeleton_map='../maps/cmu.json'), 'manifest', 'refused'),
    (lambda m: m['clips'][0]['files'].update(amc='C:/elsewhere/x.amc'), 'manifest', 'refused'),
    (lambda m: m['clips'][0]['files'].update(amc='take\\07_01.amc'), 'manifest', 'refused'),
    (lambda m: m['clips'][0]['sha256'].update(asf='ABC'), 'manifest', 'refused'),
    (lambda m: m['clips'][0].update(source_frames=[600, 1]), 'manifest', 'refused'),
    (lambda m: m['clips'][0].update(fps_source=0), 'manifest', 'refused'),
    (lambda m: m['clips'].append(copy.deepcopy(m['clips'][0])), 'manifest', 'refused'),
    (lambda m: m['disclosed_defects'][0].update(source_frames=[590, 610]), 'manifest', 'refused'),
    (lambda m: m['disclosed_defects'][0].update(clip='run'), 'manifest', 'refused'),
    (lambda m: m.update(batch='B 001'), 'manifest', 'refused'),
    (lambda m: m.update(extra=1), 'manifest', 'refused'),
])
def test_a_manifest_outside_the_schema_is_refused_with_its_reason(mutate, reason, state):
    m = _manifest()
    mutate(m)
    with pytest.raises(mr.ReceiveError) as err:
        mr.validate_manifest(m)
    assert (err.value.reason, err.value.state) == (reason, state)


def test_a_later_revision_must_supersede_the_one_before():
    assert mr.validate_manifest(_manifest(revision=2))
    with pytest.raises(mr.ReceiveError, match='supersede'):
        mr.validate_manifest(_manifest(revision=2, supersedes=None))
    with pytest.raises(mr.ReceiveError, match='supersede'):
        mr.validate_manifest(_manifest(revision=3, supersedes=1))


# ---------------------------------------------------------------------------------------------------------- licence
def test_the_cmu_template_quotes_the_faq_and_is_unsigned():
    lic = mr.validate_licence(mr.cmu_licence_template(), 'cmu')
    assert lic['terms_excerpt'] == ('The motion capture data may be copied, modified, or redistributed without '
                                    'permission.')
    assert not mr.is_signed(lic)
    # the FAQ does not speak to public display or commercial use: the signer decides, the template does not
    assert lic['permits']['public_display'] is None and lic['permits']['commercial'] is None
    assert lic['facts']['fps_source']['provenance'] == 'unverified'


def test_an_unsigned_licence_refuses_the_delivery(world):
    lic = mr.cmu_licence_template()
    lic['permits']['public_display'] = True
    _write(os.path.join(world['tests_root'], 'licenses', 'cmu.json'), lic)
    with pytest.raises(mr.ReceiveError) as err:
        world['receive'](world['deliver'](_manifest()))
    assert err.value.reason == 'licence_unsigned'
    assert not os.path.exists(mr.revision_dir(world['out_root'], 'B001', 1))


@pytest.mark.parametrize('sign', [
    {'status': 'checked', 'checked_by': '', 'checked_on': '2026-11-16'},
    {'status': 'checked', 'checked_by': 'owner', 'checked_on': None},
    {'status': 'unchecked', 'checked_by': 'owner', 'checked_on': '2026-11-16'},
])
def test_a_half_signed_record_is_unsigned(sign):
    lic = _signed_cmu()
    lic.update(sign)
    with pytest.raises(mr.ReceiveError) as err:
        mr.gate(lic, ['derivatives'])
    assert err.value.reason == 'licence_unsigned'


@pytest.mark.parametrize('value', [None, False])
def test_a_permit_the_intended_use_needs_must_be_exactly_true(value):
    lic = _signed_cmu()
    lic['permits']['public_display'] = value
    with pytest.raises(mr.ReceiveError) as err:
        mr.gate(lic, ['public_display'])
    assert err.value.reason == 'licence_permit'
    mr.gate(lic, ['derivatives'])             # an unused permit does not matter


def test_a_checked_record_without_the_terms_hash_or_a_bad_date_is_malformed():
    lic = _signed_cmu()
    lic['terms_sha256'] = None
    with pytest.raises(mr.ReceiveError, match='hash of the terms'):
        mr.validate_licence(lic)
    lic = _signed_cmu()
    lic['checked_on'] = '16/11/2026'
    with pytest.raises(mr.ReceiveError, match='YYYY-MM-DD'):
        mr.validate_licence(lic)
    lic = _signed_cmu()
    lic['permits']['commercial'] = 1
    with pytest.raises(mr.ReceiveError, match='true, false or null'):
        mr.validate_licence(lic)


# ---------------------------------------------------------------------------------------------------------- receive
def test_receive_hashes_freezes_and_records_everything_under_the_revision(world):
    receipt = world['receive'](world['deliver'](_manifest()))
    assert receipt['revision'] == 1 and receipt['exchange'] == 'simulated'
    paths = [receipt['manifest']['path'], receipt['licence']['path'], receipt['skeleton_map']['path']]
    paths += [p for c in receipt['clips'] for p in c['files'].values()]
    assert all(p.startswith('B001/r1/') for p in paths)
    for p in paths:
        assert os.path.isfile(os.path.join(world['out_root'], *p.split('/')))
    clip = receipt['clips'][0]
    assert clip['sha256'] == {'asf': _sha(ASF), 'amc': _sha(AMC)} and clip['revision'] == 1
    assert clip['fps_source'] == 120 and clip['fps_provenance'] == 'unverified'
    assert receipt['licence']['checked_by'] == 'owner'
    assert receipt['licence']['terms_excerpt'] == mr.CMU_FAQ_SENTENCE
    assert receipt['licence']['permits_used'] == {'public_display': True, 'derivatives': True}
    assert receipt['skeleton_map']['bones']['lfemur'] == 'thigh_l'
    assert receipt['tool_versions']['blender'] == '4.5.0' and len(receipt['tool_versions']['mocap_receive']) == 64
    assert receipt['settings'] == {'basis': 'B1'}
    assert receipt['disclosed_defects'][0]['size_cm'] == 5.0
    assert mr.verify_frozen(world['out_root'], 'B001', 1) == receipt


def test_a_file_that_does_not_match_its_hash_is_refused_and_nothing_is_frozen(world):
    with pytest.raises(mr.ReceiveError) as err:
        world['receive'](world['deliver'](_manifest(), amc=AMC + b'2\n'))
    assert err.value.reason == 'hash_mismatch'
    assert not os.path.exists(mr.frozen_dir(world['out_root'], 'B001', 1))


def test_a_missing_delivered_file_is_an_infrastructure_fault(world):
    path = world['deliver'](_manifest())
    os.remove(os.path.join(os.path.dirname(path), 'take', '07.asf'))
    with pytest.raises(OSError):
        world['receive'](path)


def test_a_clip_rate_that_contradicts_the_library_fact_is_refused(world):
    m = _manifest()
    m['clips'][0]['fps_source'] = 30
    with pytest.raises(mr.ReceiveError) as err:
        world['receive'](world['deliver'](m))
    assert err.value.reason == 'fps_source'


def test_a_skeleton_map_that_gives_one_role_to_two_bones_is_refused(world):
    _write(os.path.join(world['tests_root'], 'maps', 'cmu.json'),
           {'library': 'cmu', 'bones': {'lfemur': 'thigh_l', 'lhipjoint': 'thigh_l'}})
    with pytest.raises(mr.ReceiveError) as err:
        world['receive'](world['deliver'](_manifest()))
    assert err.value.reason == 'skeleton_map'


def test_a_frozen_revision_is_never_overwritten(world):
    path = world['deliver'](_manifest())
    first = world['receive'](path)
    assert world['receive'](path) == first                         # the same delivery: the same receipt
    frozen_amc = os.path.join(world['out_root'], *first['clips'][0]['files']['amc'].split('/'))
    before = open(frozen_amc, 'rb').read()
    m = _manifest()
    m['clips'][0]['brief'] = 'a different brief'
    with pytest.raises(mr.ReceiveError) as err:
        world['receive'](world['deliver'](m))
    assert err.value.reason == 'revision_frozen'
    with pytest.raises(mr.ReceiveError) as err:
        world['receive'](path, settings={'basis': 'B2'})
    assert err.value.reason == 'revision_frozen'
    assert open(frozen_amc, 'rb').read() == before
    assert mr.load_receipt(world['out_root'], 'B001', 1) == first


def test_a_resubmission_freezes_beside_the_first_and_leaves_its_evidence_untouched(world):
    first = world['receive'](world['deliver'](_manifest()))
    first_bytes = open(os.path.join(mr.frozen_dir(world['out_root'], 'B001', 1), mr.RECEIPT), 'rb').read()
    m2 = _manifest(revision=2, disclosed_defects=[])
    second = world['receive'](world['deliver'](m2))
    assert second['supersedes'] == 1 and second['prior_receipt_sha256'] == _sha(first_bytes)
    assert all(p.startswith('B001/r2/') for c in second['clips'] for p in c['files'].values())
    assert open(os.path.join(mr.frozen_dir(world['out_root'], 'B001', 1), mr.RECEIPT), 'rb').read() == first_bytes
    assert mr.verify_frozen(world['out_root'], 'B001', 1) == first


def test_a_resubmission_of_a_revision_never_received_or_from_another_source_is_refused(world):
    with pytest.raises(mr.ReceiveError, match='never received'):
        world['receive'](world['deliver'](_manifest(revision=2)))
    world['receive'](world['deliver'](_manifest()))
    m2 = _manifest(revision=2)
    m2['source']['subject'] = '08'
    with pytest.raises(mr.ReceiveError) as err:
        world['receive'](world['deliver'](m2))
    assert err.value.reason == 'supersedes'


def test_a_frozen_file_edited_afterwards_is_found(world):
    receipt = world['receive'](world['deliver'](_manifest()))
    with open(os.path.join(world['out_root'], *receipt['clips'][0]['files']['amc'].split('/')), 'ab') as fh:
        fh.write(b'2\n')
    with pytest.raises(mr.ReceiveError) as err:
        mr.verify_frozen(world['out_root'], 'B001', 1)
    assert err.value.reason == 'frozen_changed'


def test_a_delivery_changed_after_its_hash_check_never_freezes_other_bytes(world, monkeypatch):
    """The bytes frozen are the bytes checked against the manifest, not a later re-read of the delivery."""
    path = world['deliver'](_manifest())
    amc = os.path.join(os.path.dirname(path), 'take', '07_01.amc')
    real_freeze = mr._freeze

    def swap_then_freeze(*args, **kw):
        _write(amc, AMC + b'2\n')                 # the delivery changes between the check and the copy
        return real_freeze(*args, **kw)
    monkeypatch.setattr(mr, '_freeze', swap_then_freeze)
    try:
        receipt = world['receive'](path)
    except mr.ReceiveError:
        assert not os.path.exists(mr.frozen_dir(world['out_root'], 'B001', 1))
        return
    frozen = os.path.join(world['out_root'], *receipt['clips'][0]['files']['amc'].split('/'))
    assert _sha(open(frozen, 'rb').read()) == _sha(AMC) == receipt['clips'][0]['sha256']['amc']
    assert mr.verify_frozen(world['out_root'], 'B001', 1) == receipt


@pytest.mark.parametrize('damage', ['corrupt', 'delete'])
def test_receiving_a_damaged_frozen_revision_again_is_refused(world, damage):
    path = world['deliver'](_manifest())
    receipt = world['receive'](path)
    frozen = os.path.join(world['out_root'], *receipt['clips'][0]['files']['amc'].split('/'))
    if damage == 'corrupt':
        with open(frozen, 'ab') as fh:
            fh.write(b'2\n')
    else:
        os.remove(frozen)
    with pytest.raises(mr.ReceiveError) as err:
        world['receive'](path)                    # the receipt still matches; the frozen files do not
    assert err.value.reason in ('frozen_changed', 'frozen_missing')


def test_receiving_a_revision_whose_frozen_receipt_is_unreadable_is_refused(world):
    path = world['deliver'](_manifest())
    world['receive'](path)
    _write(os.path.join(mr.frozen_dir(world['out_root'], 'B001', 1), mr.RECEIPT), b'{not json')
    with pytest.raises(mr.ReceiveError) as err:
        world['receive'](path)
    assert err.value.reason == 'frozen_changed'


@pytest.mark.parametrize('damage', ['corrupt', 'delete'])
def test_a_later_revision_is_refused_when_the_previous_frozen_files_are_damaged(world, damage):
    first = world['receive'](world['deliver'](_manifest()))
    frozen = os.path.join(world['out_root'], *first['clips'][0]['files']['amc'].split('/'))
    if damage == 'corrupt':
        with open(frozen, 'ab') as fh:
            fh.write(b'2\n')
    else:
        os.remove(frozen)
    with pytest.raises(mr.ReceiveError) as err:
        world['receive'](world['deliver'](_manifest(revision=2, disclosed_defects=[])))
    assert err.value.reason in ('frozen_changed', 'frozen_missing')
    assert not os.path.exists(mr.frozen_dir(world['out_root'], 'B001', 2))


def test_a_frozen_receipt_that_points_outside_its_revision_is_refused(world):
    receipt = world['receive'](world['deliver'](_manifest()))
    receipt['clips'][0]['files']['amc'] = 'B001/r9/receive/delivery/take/07_01.amc'
    _write(os.path.join(mr.frozen_dir(world['out_root'], 'B001', 1), mr.RECEIPT),
           mr._canonical_json(receipt).encode())
    with pytest.raises(mr.ReceiveError) as err:
        mr.verify_frozen(world['out_root'], 'B001', 1)
    assert err.value.reason == 'frozen_changed'


# ------------------------------------------------------------------------------------------------------------ stage
def test_receive_runs_as_a_stage_and_its_key_follows_the_delivered_bytes(world, tmp_path):
    path = world['deliver'](_manifest())
    ctx = {'manifest': path, 'tests_root': world['tests_root'], 'out_root': world['out_root'],
           'tool_versions': {'blender': '4.5.0'}, 'settings': {'basis': 'B1'}}
    ledger_path = mr.ledger_path(world['out_root'], 'B001', 1)
    assert ledger_path.replace('\\', '/').endswith('B001/r1/record.json')
    ledger = sr.run([mr.receive_stage()], ctx, ledger_path, log=lambda m: None)
    assert ledger['receive']['status'] == sr.DONE and ledger['receive']['record']['revision'] == 1
    key = ledger['receive']['key']
    assert sr._key(mr.receive_stage(), ctx, {}) == key
    ctx2 = dict(ctx, tool_versions={'blender': '4.5.1'})
    assert sr._key(mr.receive_stage(), ctx2, {}) != key


def test_a_refusal_stops_the_run_as_a_failed_stage(world):
    lic = mr.cmu_licence_template()
    _write(os.path.join(world['tests_root'], 'licenses', 'cmu.json'), lic)
    path = world['deliver'](_manifest())
    ctx = {'manifest': path, 'tests_root': world['tests_root'], 'out_root': world['out_root']}
    ledger_path = mr.ledger_path(world['out_root'], 'B001', 1)
    with pytest.raises(sr.StageFailed):
        sr.run([mr.receive_stage()], ctx, ledger_path, log=lambda m: None)
    assert 'licence_unsigned' in sr.load(ledger_path)['receive']['error']
