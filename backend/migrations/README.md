# 初始化结构与增量升级
SQLAlchemy metadata.create_all首次建users、log_sets、segment_batches、crash_cases、recoveries、audits。log_sets.name、crash_cases.name唯一，segment_batches以(log_set_id,seq,digest)唯一保证相同重传只记一份；关联使用外键，入队冻结日志、分段来源和目标位置快照。

## 分段归档字段
- `log_sets.segments` JSON可空：NULL表示原有完整日志集；数组元素为`{seq,source,start_lsn,digest,records}`，`records`为规范合并后的该段记录。`log_sets.records`继续保存合并后的完整日志，供原演练与导出使用。
- `recoveries.target_lsn` INTEGER可空：本次恢复选定的日志位置。
- `recoveries.crash_case_id` 允许NULL：按日志位置恢复时无需故障场景。
- `recoveries.snapshot` 冻结入队时的`mode`（segmented/legacy）、`frozen_prefix`、`source_segments`（含每段摘要与本次使用区间）、`coverage_before_target`与`missing_after_target`。

## 升级
`scripts/schema_upgrade.py`在`scripts/seed_demo.py`开头执行，幂等：为旧库补`log_sets.segments`、`recoveries.target_lsn`，并把`recoveries.crash_case_id`放宽为可空（PostgreSQL直接DROP NOT NULL；SQLite重建表并保留全部行）。升级失败显式抛出并阻止启动，不静默吞掉结构错误。删除数据仍需显式down -v。
