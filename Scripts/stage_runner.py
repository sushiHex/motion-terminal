"""Run a hero's stages in dependency order, with a record of each that survives a failure.

    ledger = run(STAGES, ctx, ledger_path)                 # every stage
    ledger = run(STAGES, ctx, ledger_path, start='bake')   # that stage and every later one
    ledger = run(STAGES, ctx, ledger_path, resume=True)    # skip what is already done from the same inputs

Layer 3 of docs/specs/2026-11-12-asset-pipeline-design.md. A stage is a name, what it needs, what decides its result,
and a function. The runner owns the order, the record and what "already done" means, so a script that onboards a
hero is a list of stages and nothing else:

- the record file is written after EVERY stage, so a run that fails at stage five keeps the first four records and says
  where and why it stopped;
- a stage's key is the digest of its declared inputs and of the keys of the stages it needs, so a change upstream
  changes every key downstream, and `resume` can never reuse a result made from something that has since changed;
- a stage that is about to run again takes its dependents' records with it: a stale record is worse than none;
- stages hand each other values (their records), never printed lines.

Pure: no editor, no hero knowledge. What a stage does is the stage's business.
"""
import hashlib
import json
import os
import time

DONE, FAILED = 'done', 'failed'
SUMMARY_KEY = 'summary'      # the one key in a record file that is not a stage: whatever the caller's finish() puts there


class StageFailed(RuntimeError):
    """A stage raised. The record file on disk records it; `stage` names it and __cause__ is what it raised."""

    def __init__(self, stage, error):
        super().__init__('stage %s failed: %s' % (stage, error))
        self.stage = stage


class Stage:
    """name: unique. needs: names of the stages whose records it reads. run(ctx, records) -> its record (a JSON-able
    dict), where records is {name: record} for its needs. inputs(ctx) -> everything else that decides its result, as
    a JSON-able value (the hero's profile slice, a tool version, an option); None when its needs decide everything."""

    def __init__(self, name, run, needs=(), inputs=None):
        self.name, self.run, self.needs, self.inputs = name, run, tuple(needs), inputs


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()[:16]


def check(stages):
    """Raise unless every name is unique and every need names an EARLIER stage: the list's order is the run order,
    so a cycle or a forward reference is a mistake in the list, found before anything runs."""
    seen = []
    for stage in stages:
        if stage.name in seen:
            raise ValueError('stage %s is declared twice' % stage.name)
        missing = [n for n in stage.needs if n not in seen]
        if missing:
            raise ValueError('stage %s needs %s, which is not declared before it' % (stage.name, missing))
        seen.append(stage.name)


def load(path):
    """The record file at `path`: {name: {'status', 'key', 'record', 'seconds', 'error'?}}; {} when there is none."""
    if not os.path.exists(path):
        return {}
    with open(path) as fh:
        return json.load(fh)


def _save(path, ledger):
    """Written whole and swapped in, so a ledger is never half a file."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w') as fh:
        json.dump(ledger, fh, indent=1, sort_keys=True)
    os.replace(tmp, path)


def dependents(stages, name):
    """Every stage that needs `name`, directly or through another, in run order."""
    out = []
    for stage in stages:
        if stage.name != name and any(n == name or n in out for n in stage.needs):
            out.append(stage.name)
    return out


class _Lock:
    """One run per record file: two runs on one hero would interleave their writes and rebuild the same assets at
    once. A lock left by a run that died names its process; it is removed by hand, never guessed stale."""

    def __init__(self, ledger_path):
        self.path = ledger_path + '.lock'

    def __enter__(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                with open(self.path) as fh:
                    holder = fh.read().strip()
            except OSError:
                holder = '?'
            raise RuntimeError('another run holds %s (process %s); if none is running, delete that file'
                               % (self.path, holder)) from None
        try:
            with os.fdopen(fd, 'w') as fh:
                fh.write(str(os.getpid()))
        except Exception:
            self._release()         # a lock this run could not finish taking must not outlive it
            raise
        return self

    def _release(self):
        try:
            os.remove(self.path)
        except OSError as exc:
            print('STAGE lock %s was not removed (%s): delete it before the next run' % (self.path, exc), flush=True)

    def __exit__(self, *exc):
        self._release()
        return False


def _key(stage, ctx, keys):
    return _digest({'inputs': stage.inputs(ctx) if stage.inputs else None,
                    'needs': {n: keys[n] for n in stage.needs}})


def run(stages, ctx, ledger_path, start=None, resume=False, log=print, prepare=None, finish=None):
    """Run `stages` in order and return the record file's content, written to ledger_path after every stage.

    start: run that stage and every later one. Each earlier stage a selected one needs must be done in the record
    file UNDER THE KEY IT WOULD HAVE NOW: an earlier stage whose inputs changed since it ran is stale, and starting
    past it is refused rather than building on it. resume: skip a selected stage whose entry is done under the key
    it would have now. A stage that raises, or whose inputs cannot be read, is recorded as failed and StageFailed is
    raised; nothing after it runs.

    prepare(records) and finish(records, failed) run inside this run's lock, so nothing else writes the record file:
    prepare once after it is loaded (it may drop or change entries), finish after the last stage or after a failure
    (failed is the StageFailed, or None); what finish leaves under SUMMARY_KEY is kept in the file across runs."""
    check(stages)
    names = [s.name for s in stages]
    if start is not None and start not in names:
        raise ValueError('no stage %r (stages: %s)' % (start, names))
    selected = names[names.index(start):] if start is not None else names
    with _Lock(ledger_path):
        whole = load(ledger_path)
        # a stage that no longer exists has no record; the caller's summary is the one other key kept
        ledger = {k: v for k, v in whole.items() if k in names or k == SUMMARY_KEY}
        if prepare:
            prepare(ledger)
            _save(ledger_path, ledger)

        # Earlier stages that are not run this time: which of their records still stand for today's inputs.
        keys, stale = {}, {}
        for stage in stages:
            if stage.name in selected:
                break
            entry = ledger.get(stage.name) or {}
            behind = [n for n in stage.needs if n in stale]
            if entry.get('status') != DONE:
                stale[stage.name] = 'has no finished record'
            elif behind:
                stale[stage.name] = 'was made from %s, which is stale' % behind
            else:
                try:
                    now = _key(stage, ctx, keys)
                except Exception as exc:          # noqa: BLE001 - an unreadable input cannot vouch for a record
                    now = None
                    stale[stage.name] = 'its inputs could not be read (%s: %s)' % (type(exc).__name__, exc)
                if now is not None and now != entry.get('key'):
                    stale[stage.name] = 'its inputs changed since it ran'
            keys[stage.name] = entry.get('key')
        # A record found stale is removed, with everything made from it, and the file says so: a refusal below must
        # not leave "done" standing for something that is no longer what its inputs describe.
        dropped = False
        for name in list(stale):
            for gone in [name] + dependents(stages, name):
                dropped = ledger.pop(gone, None) is not None or dropped
        if dropped:
            _save(ledger_path, ledger)

        try:
            _stages(stages, ctx, ledger_path, selected, ledger, stale, resume, log)
        except StageFailed as failed:
            if finish:
                finish(ledger, failed)
                _save(ledger_path, ledger)
            raise
        if finish:
            finish(ledger, None)
            _save(ledger_path, ledger)
        return ledger


def _stages(stages, ctx, ledger_path, selected, ledger, stale, resume, log):
    """The selected stages, in order (run's body, under its lock)."""
    for stage in stages:
        if stage.name not in selected:
            continue
        lacking = {n: stale[n] for n in stage.needs if n in stale}
        lacking.update({n: 'has no finished record' for n in stage.needs
                        if n not in stale and ledger.get(n, {}).get('status') != DONE})
        if lacking:
            raise StageFailed(stage.name, 'it needs %s: run from there'
                              % '; '.join('%s, which %s' % (n, why) for n, why in sorted(lacking.items())))
        began = time.time()
        try:
            key = _key(stage, ctx, {n: ledger[n]['key'] for n in stage.needs})
        except Exception as exc:          # noqa: BLE001 - the old record cannot stand when its inputs cannot be read
            for name in [stage.name] + dependents(stages, stage.name):
                ledger.pop(name, None)
            ledger[stage.name] = {'status': FAILED, 'key': None, 'seconds': 0.0,
                                  'error': 'reading its inputs: %s: %s' % (type(exc).__name__, exc)}
            _save(ledger_path, ledger)
            raise StageFailed(stage.name, exc) from exc
        entry = ledger.get(stage.name)
        if resume and entry and entry.get('status') == DONE and entry.get('key') == key:
            log('STAGE %s unchanged, kept' % stage.name)
            continue

        # About to be made again: what was made from its old result is stale from this moment, pass or fail.
        for name in [stage.name] + dependents(stages, stage.name):
            ledger.pop(name, None)
        _save(ledger_path, ledger)

        try:
            record = stage.run(ctx, {n: ledger[n]['record'] for n in stage.needs})
        except Exception as exc:          # noqa: BLE001 - recorded, then raised as StageFailed with its cause
            ledger[stage.name] = {'status': FAILED, 'key': key, 'error': '%s: %s' % (type(exc).__name__, exc),
                                  'seconds': round(time.time() - began, 1)}
            _save(ledger_path, ledger)
            raise StageFailed(stage.name, exc) from exc
        ledger[stage.name] = {'status': DONE, 'key': key, 'record': record,
                              'seconds': round(time.time() - began, 1)}
        _save(ledger_path, ledger)
        log('STAGE %s done in %.1f s' % (stage.name, ledger[stage.name]['seconds']))


def status(stages, ledger):
    """[(name, 'done' | 'failed' | 'pending')] in run order: where a hero stands."""
    return [(s.name, (ledger.get(s.name) or {}).get('status', 'pending')) for s in stages]
