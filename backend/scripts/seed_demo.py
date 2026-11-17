import os
from sqlalchemy import select
from src.config.database import Base,engine,Session
from src.models.entities import User,LogSet,CrashCase,SegmentBatch
from src.services.segment_archive import segment_digest
from src.utils.security import hash_password
from scripts.schema_upgrade import upgrade
RECORDS=[{'op':'begin','tx':'T1'},{'op':'write','tx':'T1','key':'sample_A','value':75},{'op':'commit','tx':'T1'},{'op':'checkpoint'},{'op':'begin','tx':'T2'},{'op':'write','tx':'T2','key':'sample_B','value':40},{'op':'begin','tx':'T3'},{'op':'write','tx':'T3','key':'sample_A','value':60},{'op':'commit','tx':'T3'},{'op':'write','tx':'T2','key':'sample_B','value':35},{'op':'commit','tx':'T2'}]
def seed():
    Base.metadata.create_all(engine)
    upgrade(engine)
    if os.getenv('SEED_DEMO','true').lower()!='true':return
    with Session() as db:
        for role in ('admin','operator','viewer'):
            email=role+'@haili.local'
            if not db.scalar(select(User).where(User.email==email)):db.add(User(email=email,role=role,password_hash=hash_password('Recovery2026!')))
        logs=db.scalar(select(LogSet).where(LogSet.name=='海砾采样账本-晚班'))
        if logs is None:
            logs=LogSet(name='海砾采样账本-晚班',description='A初始100份、B初始50份，T1提交后检查点，T2未提交，T3已提交',initial={'sample_A':100,'sample_B':50},records=RECORDS);db.add(logs);db.flush()
        for name,point in [('断电点10-撤销T2',10),('完整提交点11',11),('初始点0',0)]:
            if not db.scalar(select(CrashCase).where(CrashCase.name==name)):db.add(CrashCase(name=name,log_set_id=logs.id,crash_after=point))
        segmented=db.scalar(select(LogSet).where(LogSet.name=='海砾采样账本-晚班-分段'))
        if segmented is None:
            # Two segments covering LSN 1—5 and 6—11; the cross-segment transaction T2 stays open past the boundary.
            parts=[{'seq':1,'source':'shift-a','start_lsn':1,'records':RECORDS[:5]},
                   {'seq':2,'source':'shift-b','start_lsn':6,'records':RECORDS[5:]}]
            stored=[{'seq':p['seq'],'source':p['source'],'start_lsn':p['start_lsn'],
                     'digest':segment_digest(p['start_lsn'],p['records']),'records':p['records']} for p in parts]
            segmented=LogSet(name='海砾采样账本-晚班-分段',description='同一账本分两段回传，支持乱序补段与相同重传合并',initial={'sample_A':100,'sample_B':50},records=RECORDS,segments=stored)
            db.add(segmented);db.flush()
            for p in stored:db.add(SegmentBatch(log_set_id=segmented.id,source=p['source'],seq=p['seq'],start_lsn=p['start_lsn'],end_lsn=p['start_lsn']+len(p['records'])-1,digest=p['digest']))
        if not db.scalar(select(CrashCase).where(CrashCase.name=='分段目标10-跨段撤销T2')):
            db.add(CrashCase(name='分段目标10-跨段撤销T2',log_set_id=segmented.id,crash_after=10))
        db.commit()
if __name__=='__main__':seed()
