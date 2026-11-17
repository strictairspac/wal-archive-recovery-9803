from fastapi import APIRouter, Depends, HTTPException
from src.config.database import get_db
from src.middlewares.auth import current_user,writable
from src.schemas.log_set import LogSetInput,LogSetArchiveInput,SegmentUploadInput
from src.services.log_set_service import (list_log_sets,create_log_set,create_segmented_log_set,
                                          append_segments,get_log_set,get_log_set_for_update,archive_view)
from src.services.segment_archive import SegmentError
from src.controllers.actions import audit
router=APIRouter()
@router.get('/log-sets')
def listing(db=Depends(get_db),user=Depends(current_user)):return list_log_sets(db)
@router.post('/log-sets',status_code=201)
def create(data:LogSetInput,db=Depends(get_db),user=Depends(writable)):
    try:out=create_log_set(db,data)
    except ValueError as exc:raise HTTPException(422,str(exc))
    audit(db,user,'log_set:create');db.commit();return out
@router.post('/log-sets/archive',status_code=201)
def create_archive(data:LogSetArchiveInput,db=Depends(get_db),user=Depends(writable)):
    try:out=create_segmented_log_set(db,data)
    except SegmentError as exc:raise HTTPException(409,str(exc))
    except ValueError as exc:raise HTTPException(422,str(exc))
    audit(db,user,f'log_set:segment:{out["id"]}:{data.source[:60]}');db.commit();return out
@router.get('/log-sets/{ident}')
def detail(ident:int,db=Depends(get_db),user=Depends(current_user)):
    obj=get_log_set(db,ident)
    if obj is None:raise HTTPException(404,'日志集不存在')
    return archive_view(obj)
@router.post('/log-sets/{ident}/segments',status_code=201)
def upload_segments(ident:int,data:SegmentUploadInput,db=Depends(get_db),user=Depends(writable)):
    # Row lock serializes concurrent batches; the merge is validate-then-write.
    obj=get_log_set_for_update(db,ident)
    if obj is None:raise HTTPException(404,'日志集不存在')
    try:out=append_segments(db,obj,data)
    except SegmentError as exc:raise HTTPException(409,str(exc))
    except ValueError as exc:raise HTTPException(422,str(exc))
    added=len(out['batch']['added_lsns']);dup=len(out['batch']['duplicate_lsns'])
    audit(db,user,f'log_set:segment:{ident}:{data.source[:60]}:+{added}/={dup}')
    db.commit();return out
