"""Execute one queued recovery job from its frozen snapshot.

Kept separate from the worker loop so the exact production path can be tested
without Redis/PostgreSQL.  A job never reads the current log set: everything it
needs is inside ``job.snapshot``, which was frozen when the job was enqueued.
"""
from src.services.wal_engine import recover, recover_segmented


def execute(job):
    snap = job.snapshot
    if snap.get('mode') == 'segmented':
        return recover_segmented(
            snap['log_set']['initial'], snap['frozen_prefix'], snap['target_lsn'],
            snap.get('source_segments', []),
            {'target_intervals': snap.get('coverage_before_target'),
             'missing_after_target': snap.get('missing_after_target'),
             'max_lsn': snap.get('available_max_lsn'),
             'covered_positions': snap.get('available_records')})
    # Legacy full log sets still replay a persisted prefix; the target is the one
    # frozen at enqueue time (a fault scene's crash point, optionally overridden).
    target = snap.get('target_lsn')
    if target is None:
        target = snap['case']['crash_after']
    return recover(snap['log_set']['initial'], snap['log_set']['records'], target)
