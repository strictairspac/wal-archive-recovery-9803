# 海砾 · 采样账本 WAL 恢复演练
海砾计量数据保障组（8人）用可重放日志替代恢复演练时手工拼接的SQL片段和账本截图。

## 场景与模块
上游为事务日志样例，下游为值班复核人。原流程人工标记提交、表格算恢复余额、截图写结论；痛点是崩溃点不清、撤销顺序容易错、恢复证据缺失。模块为事务日志集 / 故障注入点 / 恢复队列 / 恢复证据，实体LogSet/CrashCase/Recovery贯穿全栈。

**Python WAL教学模型，不注入真实PostgreSQL故障，不执行数据库物理恢复。** PostgreSQL真实保存业务输入、冻结快照、任务和结果，Redis实际用于worker互斥，后台Python实际演算恢复历史。

## 启动
```bash
cp .env.example .env
docker compose up -d --build
# http://localhost:18246
# http://localhost:19246/docs
docker compose exec -T backend python -m scripts.seed_demo
docker compose down --remove-orphans
```
SEED_DEMO=true时启动自动执行种子；自然键查重，不覆盖用户修改。初始化失败显式阻止启动，不隐藏结构错误。删除数据需显式down -v。

|账号|密码|权限|典型动作|
|---|---|---|---|
|admin@haili.local|Recovery2026!|读写/导出/审计|查询审计记录|
|operator@haili.local|Recovery2026!|新增日志/场景/运行/导出|设置断电点10|
|viewer@haili.local|Recovery2026!|只读/导出|核对恢复账本|

## 一天的复核剧本
09:00操作员登录；09:05查看海砾采样账本-晚班（A100、B50）；09:10复制日志集保存shift-20261117；09:15在第10条日志后注入断电点；09:20提交恢复；09:25核对检查点LSN4、已提交T3、未提交T2两条写入逆序撤销，最终A60/B50；09:30下载JSON；09:40观察员核对且写操作403；10:00管理员查看/api/audits。

分段复核剧本：09:05查看海砾采样账本-晚班-分段（LSN1—5来自shift-a、LSN6—11来自shift-b）；09:08乱序补传/原样重传同一段，页面提示新增与相同重传条数；09:12指定目标位置10排队，跨段开启的T2两条写入被撤销，结果仍为A60/B50；09:15目标10排队后继续补传目标之后的分段，已排队任务冻结不变；09:20尝试上传与已归档LSN内容不同的重叠段，整批409拒绝且归档保持原样；09:25对缺LSN2的账本指定目标4，422拒绝并列出缺段。

## 分段归档（同一账本分段上传）
段上传接口：`POST /log-sets/archive` 新建账本并接收首批分段，`POST /log-sets/{id}/segments` 追加；`GET /log-sets/{id}` 返回分来源、摘要、覆盖区间与合并日志。每段声明`seq`（分段序号）、`start_lsn`（1起）、`records`与`source`（来源批次）。

- 乱序到达可合并：分段落位由`start_lsn`决定，不要求按序号或到达顺序连续。
- 相同重传可合并：同一分段序号下同一LSN记录完全一致即去重，返回`added_lsns`与`duplicate_lsns`；不新增审计噪音。
- 重叠冲突拒绝：同一LSN在不同分段内容不同时整批409，校验先于写入，已归档内容与排队任务均不变。
- 目标之前缺段拒绝：排队与恢复都以LSN 1..目标连续为前提，缺段返回422并列出缺失LSN。
- 目标之后缺段不影响：恢复只校验并重放1..目标，目标之后缺段仅提示后续段尚未到齐。
- 排队冻结：入队时把所选段（含每段摘要和本次使用的LSN切片）、目标位置与前缀日志写入snapshot；之后补段不改变已排队任务。
- 恢复语义沿用原规则：同一写锁、静止检查点、按选定位置重放，跨段开启的未提交写入全部按LSN倒序撤销，独立oracle仍只应用已提交事务。

## 算法、格式和失败态
日志为JSON数组，操作为begin/write/commit/abort/checkpoint；write包含tx/key/value，其他事务操作包含tx，checkpoint不带tx。键必须在初始整数账本存在。事务ID不可复用；严格写锁直到commit/abort释放，同键重叠活动事务拒绝；检查点仅在无活动事务时允许。最多200条日志/30键/整数绝对值不超过10亿。

crash_after=N表示前N条日志已持久化。最近静止检查点保存全部数据页，检查点后数据页假定均未写回（no-force）。恢复重放日志写入和已abort的撤销，再按LSN倒序撤销未结束事务。独立oracle从检查点账本仅应用已提交事务写入，结果须相同。输出重做/撤销轨迹、事务集合、各阶段账本、SHA256和模型版本。空输入、未知键、事务顺序非法、检查点有活动事务、写锁冲突、崩溃点超限均拒绝。模型不涉及页LSN、CLR、模糊检查点或真实磁盘故障；不是完整ARIES实现。

## 工程与开发
前端原生HTML/CSS/JS+Nginx1.27；后端Python3.11+FastAPI+SQLAlchemy+PyJWT；PostgreSQL16和Redis7；5服务均healthcheck。持久任务队列在PG，单worker通过Redis锁互斥。20条待执行配额，审计记录写入操作，JWT8小时、PBKDF2随机盐密码。后端路由不含/api，Nginx/api/去前缀代理。

目录frontend/src/{api,stores,types,components/common,hooks,pages,router,utils,constants}与backend/src/{routes,controllers,services,models,schemas,middlewares,utils,constants,config}。本地开发安装backend/requirements.txt并设DATABASE_URL/REDIS_URL/JWT_SECRET，再运行uvicorn src.main:app；前端需代理/api。migrations记录初始化结构；`scripts/schema_upgrade.py`在种子前执行增量升级（log_sets.segments、recoveries.target_lsn、recoveries.crash_case_id可空），失败即阻止启动。

环境COMPOSE_PROJECT_NAME控制命名，FRONTEND_PORT/BACKEND_PORT/DB_PORT为18246/19246/17746，DB_PASSWORD控制演示数据库密码，JWT_SECRET签名密钥，SEED_DEMO控制种子。命名卷db-data/redis-data/artifacts；共享artifacts预留，导出由冻结快照即时生成。Google Fonts需外网，业务离线可运行。默认凭据仅本地演示。

共享枚举roles与任务states定义于backend/src/constants/enums.py、frontend/src/constants/enums.js，状态使用models/entities.py/services/recovery_service.py/worker.py/components/common/status.js；日志操作类型在schemas/log_set.py和pages/logs.js中声明/说明。分段合并与覆盖规则在services/segment_archive.py，入队冻结在services/recovery_service.py，后台执行入口services/job_runner.py。MIT许可。分段归档验证见`backend/tests/test_segmented_archive.py`（跨段事务、重叠冲突、前后缺段、排队后补段），运行：`cd backend && DATABASE_URL=sqlite:////tmp/wal-tests.db JWT_SECRET=test python -m unittest tests.test_segmented_archive -v`。真实验证见批次test-artifacts/gb-4107/TESTING.md。
