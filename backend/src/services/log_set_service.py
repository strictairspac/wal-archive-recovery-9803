from sqlalchemy import select
from src.models.entities import LogSet,SegmentBatch
from src.services.wal_engine import validate, _check_initial
from src.services.segment_archive import (SegmentError, merge_segments, archive_summary,
                                          segment_digest)


def _segment_meta(seg):
    return {'seq': seg['seq'], 'source': seg.get('source', ''),
            'start_lsn': seg['start_lsn'],
            'end_lsn': seg['start_lsn'] + len(seg['records']) - 1,
            'record_count': len(seg['records']),
            'digest': seg.get('digest') or segment_digest(seg['start_lsn'], seg['records'])}


def _archive_segments(logs):
    """Segment list to display: legacy complete logs behave like one segment."""
    if logs.segments is not None:
        return logs.segments
    if not logs.records:
        return []
    return [{'seq': 0, 'source': 'legacy-complete', 'start_lsn': 1,
             'digest': segment_digest(1, logs.records), 'records': logs.records}]


def archive_view(logs):
    """Serialize a log set together with its merged archive and coverage."""
    segments = _archive_segments(logs)
    summary = archive_summary(segments)
    merged = merge_segments([], segments) if segments else {'log': []}
    return {
        'id': logs.id, 'name': logs.name, 'initial': logs.initial,
        'records': logs.records, 'description': logs.description,
        'segmented': logs.segments is not None,
        'segments': [_segment_meta(seg) for seg in segments],
        'archive': {
            **summary,
            'log': [{'lsn': lsn, 'record': r} for lsn, r in merged['log']],
        },
    }


def list_log_sets(db):
    return [archive_view(x) for x in db.scalars(select(LogSet).order_by(LogSet.id))]


def get_log_set(db, ident):
    return db.get(LogSet, ident)


def get_log_set_for_update(db, ident):
    """Take a row lock so concurrent segment batches cannot overwrite each other."""
    return db.scalar(select(LogSet).where(LogSet.id == ident).with_for_update())


def create_log_set(db, data):
    values = data.model_dump(); validate(values['initial'], values['records'])
    obj = LogSet(**values); db.add(obj); db.flush(); return archive_view(obj)


def _base_segments(logs):
    """Seed the archive from a legacy one-shot record list, if present."""
    if logs.segments is not None:
        return logs.segments
    if not logs.records:
        return []
    return [{'seq': 0, 'start_lsn': 1, 'source': 'legacy-complete',
             'digest': segment_digest(1, logs.records), 'records': logs.records}]


def _ingest(db, logs, segments, source):
    """Merge one upload batch into the archive and persist it atomically.

    ``merge_segments`` validates the whole batch before anything is mutated, so
    a conflicting or internally incomplete batch can never leave a half-written
    archive.  Returns ``{'added_lsns', 'duplicate_lsns', 'segments'}``.
    """
    incoming = []
    seen = set()
    for seg in segments:
        if hasattr(seg, 'model_dump'):
            seg = seg.model_dump()
        seg = dict(seg)
        seg['source'] = source
        seg['digest'] = segment_digest(seg['start_lsn'], seg['records'])
        key = (seg['seq'], seg['digest'])
        if key in seen:   # identical segment twice in the same batch
            continue
        seen.add(key)
        incoming.append(seg)
    existing = _base_segments(logs)
    merged = merge_segments(existing, incoming)

    # Provenance: one row per distinct accepted upload, retransmissions ignored.
    for seg in incoming:
        already = db.scalar(select(SegmentBatch.id).where(
            SegmentBatch.log_set_id == logs.id, SegmentBatch.seq == seg['seq'],
            SegmentBatch.digest == seg['digest']))
        if already:
            continue
        db.add(SegmentBatch(log_set_id=logs.id, source=source, seq=seg['seq'],
                            start_lsn=seg['start_lsn'],
                            end_lsn=seg['start_lsn'] + len(seg['records']) - 1,
                            digest=seg['digest']))

    stored = [{'seq': seg['seq'], 'source': seg.get('source', ''), 'start_lsn': seg['start_lsn'],
               'digest': _segment_meta(seg)['digest'], 'records': seg['records']}
              for seg in merged['segments']]
    logs.segments = stored
    logs.records = [r for _, r in merged['log']]
    db.flush()
    return {'added_lsns': merged['added_lsns'], 'duplicate_lsns': merged['duplicate_lsns'],
            'segments': stored}


def create_segmented_log_set(db, data):
    values = data.model_dump()
    _check_initial(values['initial'])
    obj = LogSet(name=values['name'], initial=values['initial'],
                 description=values['description'], records=[], segments=[])
    db.add(obj)
    db.flush()
    _ingest(db, obj, values['segments'], values['source'])
    return archive_view(obj)


def append_segments(db, logs, data):
    if logs.segments is None:
        # A complete one-shot log set keeps the original drill semantics; adding
        # segments to it would silently change what the frozen history means.
        raise SegmentError('该日志集是完整日志，不接受分段追加；请新建分段归档账本')
    values = data.model_dump()
    # _ingest validates the whole batch before mutating the archive.
    result = _ingest(db, logs, values['segments'], values['source'])
    view = archive_view(logs)
    view['batch'] = {key: result[key] for key in ('added_lsns', 'duplicate_lsns')}
    return view
