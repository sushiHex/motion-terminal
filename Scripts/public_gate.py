"""The public gate: nothing in the repository leaks what is not meant to be public.

    python Scripts/public_gate.py              # every finding, path:line; exit 1 on a finding or a missing term list
    python Scripts/public_gate.py --generic    # without the optional term list (a public clone, CI)
    python Scripts/public_gate.py --root DIR   # gate another checkout, e.g. the exported public snapshot

It reads every file git would commit (tracked plus untracked-not-ignored), and every file's NAME, and fails on
  personal paths      a home-directory path (X:/Users/<name>, /Users/<name>, /home/<name>), whoever's machine it is
  emails              an address in any text file
  secrets             token-shaped strings and engine-generated security tokens, in ini or JSON spelling
  dangling citations  a repo path a file cites (docs/..., research/..., Scripts/...) that is not in the tree, so
                      nothing points at material that was kept out
  private terms       every pattern in the optional term list (kept outside the repository), one regex per line,
                      case-insensitive. The list is read from public-deny.txt in a private/ folder (ignored by .gitignore), or
                      from the file MT_PRIVATE_TERMS names, so the gate can name what must not appear without
                      publishing it.
                      A pattern prefixed `text:` applies to text only, not to file names or binaries: short prose
                      patterns would match random bytes inside an asset.
A binary file (a NUL in its first 8 KB) is still read, as Latin-1 and as UTF-16, for personal paths and private terms:
an asset can embed the path it was imported from.
"""
import fnmatch
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRIVATE_DIR = 'private'
TERMS_ENV = 'MT_PRIVATE_TERMS'
TEXT_ONLY = 'text:'

_SEP = r'(?:\\\\|\\|/)+'
PERSONAL_PATH = re.compile(r'(?:[A-Za-z]:%s|(?<![\w:.\\/])/)(?:Users|home)%s(?!<)[A-Za-z0-9_.-]+' % (_SEP, _SEP), re.I)
EMAIL = re.compile(r'\b[\w.+-]+@[A-Za-z0-9-]+\.[A-Za-z][\w.-]*\b')
SECRET = re.compile(r'ghp_[A-Za-z0-9]{20,}|github_pat_\w{20,}|\bsk-[A-Za-z0-9-]{20,}|AKIA[0-9A-Z]{16}|'
                    r'SecurityToken["\']?\s*[:=]\s*["\']?\w+|-----BEGIN [A-Z ]*PRIVATE KEY-----')
# a repo-relative path into the folders that hold citable material (Plugins/ and Source/ paths also name the
# engine's and third-party plugins, so they are not checked), ending in a file name or a glob
CITATION = re.compile(r'(?<![\w./-])((?:docs|research|Scripts|HeroProfiles|private)/[\w.*/-]*[\w*])')
NEVER_CITED = ('private/',)
# tests build hypothetical paths on purpose; a citation there is a fixture, not a pointer
CITATION_EXEMPT = re.compile(r'(^|/)test_[^/]*\.py$')


def files(repo=REPO):
    """Repo-relative paths of everything git would commit, outside the private folder."""
    out = subprocess.run(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=repo,
                         capture_output=True, check=True).stdout.decode('utf-8')
    return sorted(p for p in out.split('\0') if p and not p.startswith(PRIVATE_DIR + '/')
                  and os.path.isfile(os.path.join(repo, p)))


def read(path):
    """(text, binary): a text file's text, or a binary file's bytes read as Latin-1 plus UTF-16 (both byte orders)."""
    with open(path, 'rb') as f:
        data = f.read()
    if b'\0' not in data[:8192]:
        return data.decode('utf-8', errors='replace'), False
    return '\n'.join([data.decode('latin-1'), data.decode('utf-16-le', errors='ignore'),
                      data[1:].decode('utf-16-le', errors='ignore')]), True


def private_terms(repo=REPO):
    """([(compiled pattern, text only)], source path), or (None, path looked for) when the list is absent."""
    path = os.environ.get(TERMS_ENV) or os.path.join(repo, PRIVATE_DIR, 'public-deny.txt')
    if not os.path.isfile(path):
        return None, path
    lines = [l.strip() for l in open(path, encoding='utf-8')]
    return [(re.compile(l[len(TEXT_ONLY):] if l.startswith(TEXT_ONLY) else l, re.I), l.startswith(TEXT_ONLY))
            for l in lines if l and not l.startswith('#')], path


def cited_exists(cite, tree):
    """Whether a cited path names a file or folder in the tree (a glob must match at least one)."""
    cite = cite.rstrip('.')
    if any(cite.startswith(p) for p in NEVER_CITED):
        return False
    if '*' in cite:
        return any(fnmatch.fnmatch(p, cite) or fnmatch.fnmatch(p, cite + '/*') for p in tree)
    return cite in tree or any(p.startswith(cite.rstrip('/') + '/') for p in tree)


def _leaks(line, terms, text):
    """[(rule, excerpt)] for the rules that apply to any content: personal paths and private terms (the text-only
    ones only when `text`)."""
    hits = [('personal path', m.group(0)) for m in PERSONAL_PATH.finditer(line)]
    return hits + [('private term', m.group(0)) for t, text_only in (terms or []) if text or not text_only
                   for m in t.finditer(line)]


def scan(repo=REPO, terms=None):
    """[(path, line, rule, excerpt)] over the repo (line 0 = the file's name). terms: compiled private patterns, or
    None to skip them."""
    tree = files(repo)
    tree_set = set(tree)
    found = []
    for rel in tree:
        found += [(rel, 0, rule, excerpt) for rule, excerpt in _leaks(rel, terms, False)]
        body, binary = read(os.path.join(repo, rel))
        for n, line in enumerate(body.splitlines(), 1):
            hits = _leaks(line, terms, not binary)
            if not binary:
                hits += [('email', m.group(0)) for m in EMAIL.finditer(line)]
                hits += [('secret', m.group(0)[:12] + '...') for m in SECRET.finditer(line)]
                if not CITATION_EXEMPT.search(rel):
                    hits += [('dangling citation', m.group(1)) for m in CITATION.finditer(line)
                             if not cited_exists(m.group(1), tree_set)]
            found += [(rel, 0 if binary else n, rule, excerpt) for rule, excerpt in hits]
    return found


def main(argv):
    root = argv[argv.index('--root') + 1] if '--root' in argv else REPO
    generic = '--generic' in argv
    # gating a snapshot (--root): the snapshot has no private folder, so the list comes from this checkout
    terms, source = (None, None) if generic else private_terms(REPO)
    found = scan(root, terms)
    for rel, n, rule, excerpt in found:
        print('%s:%d  %s  %s' % (rel, n, rule, excerpt))
    missing = terms is None and not generic
    if missing:
        print('term list not found (%s): run with --generic to gate without it' % source)
    print('PUBLIC GATE %s: %d finding(s)%s' % ('FAIL' if found or missing else 'PASS', len(found),
                                                 ' (generic rules only)' if generic else ''))
    return 1 if found or missing else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
