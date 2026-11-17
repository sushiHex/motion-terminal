"""The public gate (Scripts/public_gate.py): its rules, and the repository passing them."""
import os
import subprocess
import tempfile
import unittest
from unittest import mock

import public_gate as pg

_TMP = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)   # every throwaway repo lives here, removed at exit


def _repo(files):
    """A throwaway git repo holding files ({path: text}); returns its root."""
    root = tempfile.mkdtemp(dir=_TMP.name)
    subprocess.run(['git', 'init', '-q'], cwd=root, check=True)
    for rel, body in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(body)
    return root


def _rules(found):
    return sorted((rel, rule) for rel, _, rule, _ in found)


class Rules(unittest.TestCase):

    def test_personal_paths_fail_in_any_form_and_placeholders_pass(self):
        root = _repo({'docs/a.md': 'C:\\Use' 'rs\\someone\\repos\\x\nC:/Use' 'rs/someone/x\nC:\\Use' 'rs\\<user>\\x\n'})
        self.assertEqual([n for _, n, rule, _ in pg.scan(root) if rule == 'personal path'], [1, 2])

    def test_emails_and_secrets_fail(self):
        root = _repo({'Config/x.ini': 'Security' 'Token=ABC123\n', 'docs/b.md': 'write to someone@' 'example.com\n'})
        self.assertEqual(_rules(pg.scan(root)), [('Config/x.ini', 'secret'), ('docs/b.md', 'email')])

    def test_a_citation_must_name_something_in_the_tree(self):
        root = _repo({'docs/a.md': 'see docs/b.md and research/gone.md and docs/*.md\n', 'docs/b.md': 'x\n'})
        self.assertEqual([e for _, _, rule, e in pg.scan(root) if rule == 'dangling citation'], ['research/gone.md'])

    def test_the_private_folder_is_never_scanned_and_never_cited(self):
        root = _repo({'private/notes.md': 'C:/Use' 'rs/someone/secret\n', 'docs/a.md': 'see private/notes.md\n'})
        self.assertEqual(_rules(pg.scan(root)), [('docs/a.md', 'dangling citation')])

    def test_tests_may_build_hypothetical_paths(self):
        root = _repo({'Scripts/test_x.py': "FAKE = 'Scripts/drv.py'\n"})
        self.assertEqual(pg.scan(root), [])

    def test_private_terms_come_from_the_private_list(self):
        root = _repo({'docs/a.md': 'an Acme project\n', 'private/public-deny.txt': '# comment\n\\bacme\\b\n'})
        terms, source = pg.private_terms(root)
        self.assertEqual(source, os.path.join(root, 'private', 'public-deny.txt'))
        self.assertEqual(_rules(pg.scan(root, terms)), [('docs/a.md', 'private term')])

    def test_a_missing_private_list_reports_itself_rather_than_passing(self):
        terms, source = pg.private_terms(_repo({}))
        self.assertIsNone(terms)
        self.assertTrue(source.endswith('public-deny.txt'))

    def test_home_paths_in_every_spelling_fail(self):
        root = _repo({'docs/a.md': '/ho' 'me/someone/x\n/Use' 'rs/someone/x\nC:\\\\Use' 'rs\\\\someone\\\\x\n'
                                   'Engine/Plugins/x and docs/a.md\n'})
        self.assertEqual([n for _, n, rule, _ in pg.scan(root) if rule == 'personal path'], [1, 2, 3])

    def test_a_json_security_token_fails(self):
        root = _repo({'a.json': '{"Security' 'Token": "ABC123"}\n'})
        self.assertEqual(_rules(pg.scan(root)), [('a.json', 'secret')])

    def test_binary_files_are_read_for_paths_in_both_encodings(self):
        root = _repo({})
        for name, data in (('a.uasset', b'\0C:/Use' b'rs/someone/x'),
                           ('b.uasset', '\0C:/Use' 'rs/someone/x'.encode('utf-16-le')),
                           ('c.uasset', b'\0/Game/MT/Clean')):
            with open(os.path.join(root, name), 'wb') as f:
                f.write(data)
        self.assertEqual(_rules(pg.scan(root)), [('a.uasset', 'personal path'), ('b.uasset', 'personal path')])

    def test_file_names_are_gated_too(self):
        root = _repo({'docs/acme-notes.md': 'x\n', 'private/public-deny.txt': 'acme\n'})
        terms, _ = pg.private_terms(root)
        self.assertEqual(pg.scan(root, terms), [('docs/acme-notes.md', 0, 'private term', 'acme')])

    def test_text_only_terms_skip_binaries_and_file_names(self):
        root = _repo({'docs/a.md': 'see QX12\n', 'docs/qx12.md': 'x\n',
                      'private/public-deny.txt': 'text:\\bQX12\\b\n'})
        with open(os.path.join(root, 'a.uasset'), 'wb') as f:
            f.write(b'\0 QX12 ')
        terms, _ = pg.private_terms(root)
        self.assertEqual(pg.scan(root, terms), [('docs/a.md', 1, 'private term', 'QX12')])

    def test_a_missing_term_list_fails_unless_generic_is_asked_for(self):
        root = _repo({'docs/a.md': 'x\n'})
        with mock.patch.dict(os.environ, {pg.TERMS_ENV: os.path.join(root, 'absent.txt')}):
            self.assertEqual(pg.main(['--root', root]), 1)
            self.assertEqual(pg.main(['--root', root, '--generic']), 0)


class Repository(unittest.TestCase):
    """The repository itself passes. Without the optional term list (a public clone) the generic rules still run."""

    def test_the_repository_passes_the_gate(self):
        terms, source = pg.private_terms()
        found = pg.scan(terms=terms)
        self.assertEqual(['%s:%d %s %s' % f for f in found], [])
        if terms is None:
            self.skipTest('term list absent (%s): generic rules passed, private terms unchecked' % source)


if __name__ == '__main__':
    unittest.main()
