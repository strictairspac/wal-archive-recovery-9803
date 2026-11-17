"""Focused checks for segmented log archives.

Run with::

    cd backend && DATABASE_URL=sqlite:////tmp/wal-tests.db JWT_SECRET=test \
        python -m unittest tests.test_segmented_archive -v

The tests use the service layer directly so they need no Redis, worker or
PostgreSQL; they cover cross-segment transactions, overlapping conflicts,
gaps before/after the target and late segments arriving after enqueueing.
"""
import json
import os
import unittest

os.environ.setdefault('DATABASE_URL', 'sqlite:////tmp/wal-tests.db')
os.environ.setdefault('JWT_SECRET', 'test-secret')

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from src.config.database import Base  # noqa: E402
from src.models.entities import LogSet, CrashCase, SegmentBatch  # noqa: E402
from src.schemas.log_set import LogSetArchiveInput, SegmentUploadInput  # noqa: E402
from src.schemas.recovery import RecoveryInput  # noqa: E402
from src.services.log_set_service import create_segmented_log_set, append_segments, archive_view  # noqa: E402
from src.services.recovery_service import create_recovery  # noqa: E402
from src.services.segment_archive import SegmentError  # noqa: E402
from src.services.job_runner import execute  # noqa: E402

RECORDS = [
    {'op': 'begin', 'tx': 'T1'}, {'op': 'write', 'tx': 'T1', 'key': 'A', 'value': 75},
    {'op': 'commit', 'tx': 'T1'}, {'op': 'checkpoint'},
    {'op': 'begin', 'tx': 'T2'}, {'op': 'write', 'tx': 'T2', 'key': 'B', 'value': 40},
    {'op': 'begin', 'tx': 'T3'}, {'op': 'write', 'tx': 'T3', 'key': 'A', 'value': 60},
    {'op': 'commit', 'tx': 'T3'}, {'op': 'write', 'tx': 'T2', 'key': 'B', 'value': 35},
    {'op': 'commit', 'tx': 'T2'},
]
INITIAL = {'A': 100, 'B': 50}


def segment(seq, start, records, source='batch'):
    return {'seq': seq, 'start_lsn': start, 'records': records, 'source': source}


def run_snapshot(job):
    """Run the same frozen-snapshot path the worker uses."""
    return execute(job)


class SegmentedArchiveTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.Session()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def make_set(self, name, segments):
        return create_segmented_log_set(self.db, LogSetArchiveInput(
            name=name, initial=INITIAL, description='d', source='batch', segments=segments))

    def enqueue(self, logs_id, target):
        return create_recovery(self.db, RecoveryInput(log_set_id=logs_id, label='测试恢复', target_lsn=target))

    def test_out_of_order_merge_and_identical_retransmission(self):
        first = self.make_set('乱序账本', [segment(2, 6, RECORDS[5:], 'shift-b')])
        self.assertEqual(first['archive']['ranges'], [[6, 11]])
        view = append_segments(self.db, self.db.get(LogSet, first['id']),
                               SegmentUploadInput(source='shift-a', segments=[segment(1, 1, RECORDS[:5])]))
        self.assertEqual(view['archive']['ranges'], [[1, 11]])
        self.assertEqual(view['batch']['added_lsns'], [1, 2, 3, 4, 5])
        repeat = append_segments(self.db, self.db.get(LogSet, first['id']),
                                 SegmentUploadInput(source='shift-a', segments=[segment(1, 1, RECORDS[:5])]))
        self.assertEqual(repeat['batch']['added_lsns'], [])
        self.assertEqual(len(repeat['batch']['duplicate_lsns']), 11)
        self.assertEqual(repeat['archive']['ranges'], [[1, 11]])

    def test_overlapping_conflict_rejects_batch_and_keeps_archive(self):
        view = self.make_set('冲突账本', [segment(1, 1, RECORDS[:5])])
        logs = self.db.get(LogSet, view['id'])
        before = json.dumps(logs.segments, sort_keys=True)
        conflicting = [segment(2, 4, [{'op': 'write', 'tx': 'T1', 'key': 'A', 'value': 1}])]
        with self.assertRaises(SegmentError):
            append_segments(self.db, logs, SegmentUploadInput(source='bad', segments=conflicting))
        # Validation runs before any mutation, so the stored archive is untouched.
        logs = self.db.get(LogSet, view['id'])
        self.assertEqual(json.dumps(logs.segments, sort_keys=True), before)
        self.assertEqual(archive_view(logs)['archive']['ranges'], [[1, 5]])
        self.assertIsNone(self.db.scalar(select(SegmentBatch).where(SegmentBatch.source == 'bad')))

    def test_gap_before_target_refused_gap_after_target_allowed(self):
        view = self.make_set('缺段账本', [segment(1, 1, [{'op': 'begin', 'tx': 'X1'}]),
                                      segment(3, 4, [{'op': 'commit', 'tx': 'X1'}])])
        with self.assertRaises(HTTPException) as ctx:
            self.enqueue(view['id'], 4)
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn('LSN 2', ctx.exception.detail)
        job = self.enqueue(view['id'], 1)
        self.assertEqual(job.snapshot['missing_after_target'], [2, 3])
        result = run_snapshot(job)
        self.assertEqual(result['recovered'], INITIAL)
        self.assertTrue(result['invariant_passed'])

    def test_cross_segment_transaction_undone(self):
        view = self.make_set('跨段账本', [segment(1, 1, RECORDS[:5]), segment(2, 6, RECORDS[5:])])
        job = self.enqueue(view['id'], 10)
        result = run_snapshot(job)
        self.assertEqual(result['recovered'], {'A': 60, 'B': 50})
        self.assertEqual(result['losers'], ['T2'])
        self.assertEqual(result['undo_count'], 2)
        self.assertEqual([t['phase'] for t in result['trace']].count('undo_loser'), 2)
        self.assertTrue(result['invariant_passed'])
        self.assertEqual(result['target_lsn'], 10)

    def test_queued_job_frozen_against_later_segments(self):
        view = self.make_set('冻结账本', [segment(1, 1, RECORDS[:5])])
        job = self.enqueue(view['id'], 5)
        frozen = json.dumps(job.snapshot, sort_keys=True, default=str)
        logs = self.db.get(LogSet, view['id'])
        appended = append_segments(self.db, logs, SegmentUploadInput(
            source='late', segments=[segment(2, 6, RECORDS[5:])]))
        self.assertEqual(appended['batch']['added_lsns'], [6, 7, 8, 9, 10, 11])
        self.db.refresh(job)
        self.assertEqual(json.dumps(job.snapshot, sort_keys=True, default=str), frozen)
        self.assertEqual(job.snapshot['source_segments'][0]['used_to'], 5)
        self.assertNotIn(RECORDS[5], job.snapshot['frozen_prefix'])
        result = run_snapshot(job)
        self.assertEqual(result['recovered'], {'A': 75, 'B': 50})
        self.assertEqual(result['losers'], ['T2'])

    def test_legacy_complete_log_still_runs_original_drill(self):
        logs = LogSet(name='完整账本', initial=INITIAL, records=RECORDS,
                      description='legacy', segments=None)
        self.db.add(logs)
        self.db.flush()
        case = CrashCase(name='断电点10', log_set_id=logs.id, crash_after=10)
        self.db.add(case)
        self.db.flush()
        job = create_recovery(self.db, RecoveryInput(crash_case_id=case.id, label='原演练'))
        self.assertEqual(job.snapshot['mode'], 'legacy')
        result = run_snapshot(job)
        self.assertEqual(result['recovered'], {'A': 60, 'B': 50})
        self.assertEqual(result['losers'], ['T2'])
        self.assertNotIn('mode', result)
        self.assertEqual(result['model_version'], 'strict-wal-quiescent-v1')


if __name__ == '__main__':
    unittest.main()
