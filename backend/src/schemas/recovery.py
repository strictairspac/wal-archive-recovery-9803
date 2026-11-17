from pydantic import BaseModel, Field, model_validator
class RecoveryInput(BaseModel):
    crash_case_id: int | None=Field(default=None,gt=0)
    log_set_id: int | None=Field(default=None,gt=0)
    label: str=Field(min_length=2,max_length=120)
    target_lsn: int | None=Field(default=None,ge=0,le=200)
    @model_validator(mode='after')
    def exactly_one_target(self):
        if self.crash_case_id is None and self.log_set_id is None: raise ValueError('需指定故障场景或日志集')
        if self.crash_case_id is not None and self.log_set_id is not None: raise ValueError('故障场景与日志集只能二选一')
        if self.log_set_id is not None and self.target_lsn is None: raise ValueError('按日志位置恢复时需指定目标位置')
        return self