from sqlalchemy import select
from fastapi import HTTPException
from src.models.entities import Recovery,CrashCase,LogSet
from src.services.wal_engine import validate_prefix
from src.services.segment_archive import (CoverageError, SegmentError, archive_summary,
                                          merge_segments, missing_after, ranges,
                                          segment_digest, target_prefix)
from src.utils.serialize import record

def list_recoveries(db):return [record(x) for x in db.scalars(select(Recovery).order_by(Recovery.id.desc()).limit(100))]
def get_recovery(db,ident):
    obj=db.get(Recovery,ident)
    if obj is None:raise HTTPException(404,'恢复记录不存在')
    return obj

def _frozen_sources(segments, target_lsn):
    """Segments contributing to LSN 1..target, with the slice actually used."""
    sources = []
    for seg in segments:
        start = seg['start_lsn']; end = start + len(seg['records']) - 1
        lo, hi = start, min(end, target_lsn)
        if lo > hi:
            continue
        sources.append({'seq': seg['seq'], 'source': seg.get('source', ''),
                        'start_lsn': start, 'end_lsn': end,
                        'digest': seg.get('digest') or segment_digest(start, seg['records']),
                        'used_from': lo, 'used_to': hi})
    return sources

def create_recovery(db,data):
    if data.crash_case_id is not None:
        case=db.get(CrashCase,data.crash_case_id)
        if case is None:raise HTTPException(404,'故障场景不存在')
        logs=db.get(LogSet,case.log_set_id)
        case_ref=record(case)
        target=case.crash_after if data.target_lsn is None else data.target_lsn
    else:
        logs=db.get(LogSet,data.log_set_id)
        case_ref=None
        target=data.target_lsn
    if logs is None:raise HTTPException(404,'日志集不存在')
    if len(db.scalars(select(Recovery.id).where(Recovery.status.in_(['queued','running']))).all())>=20:raise HTTPException(429,'待处理配额20已满')

    if logs.segments is not None:
        # Segmented archive: only LSN 1..target must be present.  Keep anything
        # after the target out of the snapshot so late segments cannot alter it.
        try:
            merged=merge_segments([],logs.segments)
        except SegmentError as exc:
            raise HTTPException(409,f'归档分段冲突，拒绝排队：{exc}')
        present={lsn for lsn,_ in merged['log']}
        try:
            target_prefix(present,target)
        except CoverageError as exc:
            raise HTTPException(422,str(exc))
        prefix=[r for lsn,r in merged['log'] if lsn<=target]
        try:
            validate_prefix(logs.initial,prefix)
        except ValueError as exc:
            raise HTTPException(422,f'目标日志位置无法重放：{exc}')
        summary=archive_summary(logs.segments)
        snapshot={'mode':'segmented','log_set':{'id':logs.id,'name':logs.name,'initial':logs.initial,
                  'description':logs.description},'case':case_ref,'target_lsn':target,
                  'frozen_prefix':prefix,'source_segments':_frozen_sources(logs.segments,target),
                  'coverage_before_target':ranges(present,target),
                  'missing_after_target':missing_after(present,target),
                  'available_max_lsn':summary['max_lsn'],'available_records':len(present)}
    else:
        records=logs.records or []
        if not isinstance(target,int) or not 0<=target<=len(records):
            raise HTTPException(422,f'目标日志位置必须在0—{len(records)}之间')
        snapshot={'mode':'legacy','log_set':record(logs),'case':case_ref,'target_lsn':target,
                  'frozen_prefix':records[:target],'source_segments':[
                      {'seq':0,'source':'legacy-complete','start_lsn':1,'end_lsn':len(records),
                       'digest':segment_digest(1,records),'used_from':1,'used_to':target}] if target else [],
                  'coverage_before_target':[[1,target]] if target else [],
                  'missing_after_target':[],'available_max_lsn':len(records),
                  'available_records':len(records)}

    obj=Recovery(crash_case_id=case_ref['id'] if case_ref else None,label=data.label,target_lsn=target,status='queued',snapshot=snapshot)
    db.add(obj);db.flush();return obj
