"""Retry an editor-side asset delete while the editor still holds the asset.

    from asset_delete import HELD, while_held
    while_held(lambda: run_delete_script())     # the script raises an error whose text contains HELD

Transport-agnostic: fn() may reach the editor any way it likes; only the text of what it raises decides a retry.
"""
import time

HELD = 'still exists after its delete'
HELD_RETRY_S, HELD_WAIT_S = 300, 15


def while_held(fn):
    """fn() (an editor-side delete), retried while the editor still holds the asset: delete_asset returns True, the
    force delete drops a still-referenced package without deleting its file (ObjectTools.cpp
    CleanupAfterSuccessfulDelete). The cause found (2026-10-30): a delete that had to LOAD the package itself, the
    loader still referencing it at cleanup; both delete sites now load it in an earlier call. This retry stays as the net
    for any other transient holder. An error whose text contains HELD is retried every HELD_WAIT_S for up to
    HELD_RETRY_S, from outside the editor so the game thread is not blocked; any other failure is raised at once."""
    deadline = time.time() + HELD_RETRY_S
    while True:
        try:
            return fn()
        except Exception as exc:          # noqa: BLE001 - the transport's own error type is not known here
            if HELD not in str(exc) or time.time() + HELD_WAIT_S > deadline:
                raise
            print('ASSET_DELETE held after its delete; retrying in %d s' % HELD_WAIT_S, flush=True)
            time.sleep(HELD_WAIT_S)
