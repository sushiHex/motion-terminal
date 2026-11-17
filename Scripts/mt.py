"""One entry point for the project's operations. The same for every hero.

    python Scripts/mt.py editor start            start the editor unattended, with its MCP server
    python Scripts/mt.py doctor                  thirty seconds of checks on the running editor
    python Scripts/mt.py intake /Game/<Pack>     read a character pack into a hero profile
    python Scripts/mt.py onboard <Hero>          every onboarding stage (add --resume, --stage <name>, --check)
    python Scripts/mt.py status <Hero>           where the hero's onboarding stands (no editor needed)
    python Scripts/mt.py build <Hero>            the hero's dynamics rig and graph
    python Scripts/mt.py run <Hero> <tag> --gate the ON/OFF drive, judged against the hero's baseline
    python Scripts/mt.py render <Hero> <out_dir> the review clip
    python Scripts/mt.py verify [--tier pie]     the editor test tiers (starts its own editor process)
    python Scripts/mt.py editor stop

Each operation is one of the scripts beside this file, run as its own process with the arguments after the
operation passed through unchanged; that script's header is its full reference. This file only names the
operations, so a person or an agent has one place to look for what the project can do and in what order.

Everything except `status` and `verify` needs the editor up (`editor start`). From Git Bash, /Game/... arguments
are rewritten into Windows paths before Python sees them: use PowerShell for `intake`.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# operation -> (script, arguments before the caller's own, takes a hero as its first argument, needs the editor)
OPERATIONS = {
    'editor': ('editor_session.py', [], False, False),
    'doctor': ('editor_doctor.py', [], False, True),
    'intake': ('hero_intake.py', [], False, True),
    'onboard': ('hero_onboard.py', [], True, True),
    'status': ('hero_onboard.py', ['--status'], True, False),
    'build': ('dynamics_graph.py', [], True, True),
    'run': ('dyn_run.py', [], True, True),
    'render': ('dyn_render.py', [], True, True),
    'verify': ('run_verification.py', [], False, False),
}


class Usage(Exception):
    pass


def command(argv):
    """[script path, arguments...] for `mt <operation> ...`; raises Usage for anything it cannot place."""
    if not argv or argv[0] in ('-h', '--help', 'help'):
        raise Usage(__doc__)
    operation, rest = argv[0], list(argv[1:])
    if operation not in OPERATIONS:
        raise Usage('unknown operation %r; operations: %s' % (operation, ', '.join(OPERATIONS)))
    script, fixed, hero, _editor = OPERATIONS[operation]
    if hero:
        if not rest or rest[0].startswith('-'):
            raise Usage('%s needs a hero: python Scripts/mt.py %s <Hero> ...' % (operation, operation))
        rest = ['--hero', rest[0]] + rest[1:]
    return [os.path.join(HERE, script)] + rest + fixed


def main(argv):
    try:
        args = command(argv)
    except Usage as usage:
        print(usage)
        return 2
    return subprocess.call([sys.executable] + args)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
