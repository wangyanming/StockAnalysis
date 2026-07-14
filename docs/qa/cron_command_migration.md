# QA 提测清单 — cron command模式迁移

## 改动文件
1. `utils/logger.py` — setup_logger 默认 console=False
2. `core/analyzer/close_task.py` — 去掉 stdout 劫持逻辑（已由 console=False 覆盖）
3. `core/fetcher/daily_fetch.py` — 增加格式化 stdout 输出
4. `PROJECT_STATE.md` — 记录变更
5. `.git/hooks/pre-commit` — QA 报告检查改警告

## 验收要点

### 1. logger 行为
- [ ] setup_logger() 不带 console 参数时，日志不输出到 stdout
- [ ] 其他模块调用 setup_logger(name) 后，logger.info 只写日志文件
- [ ] close_task.py 运行只输出 print(report)，无 [INFO] 日志行

### 2. daily_fetch.py 输出
- [ ] `python3 core/fetcher/daily_fetch.py 2>/dev/null` 输出格式化摘要
- [ ] 格式：`盘中快照采集完成 ✅` + 时间 + 各项状态 + 耗时

### 3. 定时任务（cron）
- [ ] 15:10 command 任务已创建，脚本路径正确
- [ ] 16:00 command 任务已创建，脚本路径正确
- [ ] 16:30 command 任务已创建，脚本路径正确
- [ ] 旧 agentTurn 任务已删除

### 4. pre-commit hook
- [ ] QA 报告不存在时警告不阻止
- [ ] QA 报告过时时警告不阻止
