"""Merge archived log segments into a single canonical log sequence.

A log set can be uploaded in segments.  Every segment declares ``start_lsn``
(the 1-based LSN of its first record) and the records it carries.  Segments may
arrive out of order, and an identical segment may be retransmitted any number
of times.  Rules:

* same LSN and identical record -> merge (retransmission is idempotent);
* same LSN and different record -> overlap conflict, the whole segment is
  rejected and the stored archive is left untouched;
* a gap inside LSN 1..target -> refuse the recovery; a gap after the target is
  irrelevant to that run.
"""
import hashlib
import json

MAX_LSN = 200
RECORD_FIELDS = ('op', 'tx', 'key', 'value')


class SegmentError(ValueError):
    """A segment cannot be accepted (invalid range, conflict, internal gap)."""


class CoverageError(ValueError):
    """The stored segments cannot cover LSN 1..target."""


def normalize_records(records):
    """Drop unset optional fields and keep only known WAL record fields."""
    out = []
    for item in records:
        item = dict(item)
        row = {}
        for key in RECORD_FIELDS:
            if item.get(key) is not None:
                row[key] = item[key]
        out.append(row)
    return out


def _normalize_segment(seg):
    records = normalize_records(seg.get('records') or [])
    seq = seg.get('seq')
    start = seg.get('start_lsn')
    if not isinstance(seq, int) or seq < 0:
        raise SegmentError('分段序号必须为非负整数')
    if not isinstance(start, int) or start < 1:
        raise SegmentError(f'分段 {seq} 的起始LSN必须为正整数')
    if not records:
        raise SegmentError(f'分段 {seq} 没有日志记录')
    if start + len(records) - 1 > MAX_LSN:
        raise SegmentError(f'分段 {seq} 的LSN范围 {start}—{start + len(records) - 1} 超出1—{MAX_LSN}')
    return seq, start, records


def segment_digest(start_lsn, records):
    """Stable SHA256 covering the segment position and its normalized records."""
    payload = json.dumps({'start_lsn': start_lsn, 'records': normalize_records(records)},
                         sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode()).hexdigest()


def merge_segments(existing, incoming):
    """Merge ``incoming`` into ``existing``.

    Both are lists of ``{'seq', 'start_lsn', 'records'}``.  Returns
    ``{'segments': [...], 'log': [(lsn, record), ...], 'added_lsns': [...],
    'duplicate_lsns': [...]}``.  Raises :class:`SegmentError` on conflicting
    overlap or an internally incomplete segment.
    """
    by_seq = {}
    sources = {}
    for seg in list(existing) + list(incoming):
        seq, start, records = _normalize_segment(seg)
        slots = by_seq.setdefault(seq, {})
        if seg.get('source'):
            sources.setdefault(seq, [])
            if seg['source'] not in sources[seq]:
                sources[seq].append(seg['source'])
        for offset, record in enumerate(records):
            lsn = start + offset
            if lsn in slots and slots[lsn] != record:
                raise SegmentError(f'分段 {seq} 在LSN {lsn} 与已归档内容冲突，请核对来源日志')
            slots[lsn] = record
    ordered = []
    for seq in sorted(by_seq):
        slots = by_seq[seq]
        start, end = min(slots), max(slots)
        for lsn in range(start, end + 1):
            if lsn not in slots:
                raise SegmentError(f'分段 {seq} 内部缺少LSN {lsn}，请重新上传完整分段')
        ordered.append({'seq': seq, 'start_lsn': start, 'source': '+'.join(sources.get(seq, [])),
                        'records': [slots[lsn] for lsn in range(start, end + 1)]})

    existing_lsns = set()
    for seg in existing:
        _, start, records = _normalize_segment(seg)
        existing_lsns.update(range(start, start + len(records)))
    merged = {}
    for seg in sorted(ordered, key=lambda item: item['start_lsn']):
        for offset, record in enumerate(seg['records']):
            lsn = seg['start_lsn'] + offset
            if lsn in merged and merged[lsn] != record:
                raise SegmentError(f'LSN {lsn} 在多个分段中内容不一致，拒绝合并')
            merged[lsn] = record
    log = [(lsn, merged[lsn]) for lsn in sorted(merged)]
    return {'segments': ordered, 'log': log,
            'added_lsns': [lsn for lsn, _ in log if lsn not in existing_lsns],
            'duplicate_lsns': [lsn for lsn, _ in log if lsn in existing_lsns]}


def ranges(present, up_to=None):
    """Contiguous inclusive LSN ranges present in ``present`` (1-based)."""
    present = set(present)
    if up_to is None:
        up_to = max(present) if present else 0
    out, start = [], None
    for lsn in range(1, up_to + 1):
        if lsn in present:
            if start is None:
                start = lsn
        elif start is not None:
            out.append([start, lsn - 1])
            start = None
    if start is not None:
        out.append([start, up_to])
    return out


def missing_before(present, target_lsn):
    present = set(present)
    return [lsn for lsn in range(1, target_lsn + 1) if lsn not in present]


def missing_after(present, target_lsn):
    present = set(present)
    top = max(present) if present else 0
    return [lsn for lsn in range(target_lsn + 1, top + 1) if lsn not in present]


def target_prefix(present, target_lsn):
    """Validate that LSN 1..target_lsn is continuous.

    Returns the target LSN.  Raises :class:`CoverageError` listing the missing
    LSNs *before* the target.  Missing LSNs after the target are allowed and
    are reported separately by :func:`missing_after`.
    """
    if not isinstance(target_lsn, int) or target_lsn < 0 or target_lsn > MAX_LSN:
        raise CoverageError(f'目标日志位置必须在0—{MAX_LSN}之间')
    gaps = missing_before(present, target_lsn)
    if gaps:
        shown = '、'.join(str(lsn) for lsn in gaps[:12])
        more = '' if len(gaps) <= 12 else f' 等{len(gaps)}个'
        raise CoverageError(f'目标位置之前缺少LSN {shown}{more}，无法恢复')
    return target_lsn


def archive_summary(segments):
    """Coverage summary for a list of stored segments."""
    try:
        merged = merge_segments([], segments)
    except SegmentError as exc:
        return {'max_lsn': 0, 'covered_count': 0, 'ranges': [], 'missing': [],
                'continuous_prefix': 0, 'conflict': str(exc)}
    present = [lsn for lsn, _ in merged['log']]
    max_lsn = max(present) if present else 0
    missing = [lsn for lsn in range(1, max_lsn + 1) if lsn not in set(present)]
    return {'max_lsn': max_lsn, 'covered_count': len(present), 'ranges': ranges(present),
            'missing': missing, 'continuous_prefix': (missing[0] - 1) if missing else max_lsn,
            'conflict': None}
