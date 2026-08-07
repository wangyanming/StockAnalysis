# PROJECT_STATE.md — 项目状态看板 (AI维护)

## 项目概览
- **项目**: StockAnalysis — 股票分析系统
- **路径**: `/Users/wangyanming/workspace/StockAnalysis/`
- **数据库**: MySQL，`utils/dao.py` 按项目路径自动判定生产/开发库（路径含 `StockAnalysis-dev` → 开发库 `stock_analysis_dev`；否则 → 生产库 `stock_analysis`）；凭据由 `config/db_url.local.json` 本地文件提供（版本库外、.gitignore），dao.py 按项目路径选 prod/dev；显式设 `STOCK_DB_URL` 优先
- **预检**: `bash tests/preflight.sh`
- **API校验**: `python3 tests/api_validation.py`
- **数据对账**: `python3 tests/data_reconciliation.py`

## 最新变更：选股日期参数化（trade_date 可重跑历史选股）（2026-08-06）

- **方案**: DESIGN-20260805-06
- **改法**: scorer.py 新增模块级日期锚点 `set_trade_date()`/`_get_today()`/`_get_today_dash()`；8 处 python `datetime.now()` 取数读到锚点 + 1 处 SQL CURDATE 改参数化（20日窗口回推锚点日）；`daily_pick_v2.pick_stocks_v2()` 签名改为 `(trade_date=None)`，内部一次性派生锚定变量 t/t_dash 并调 `set_trade_date` 锚定 scorer；涨停池/板块/补查统一改用 t；`__main__` 加可选 `--date` 便于手工回测
- **原因**: 让选股可按历史日期重跑支撑回测（历史日选股不再拿实时价/实时大盘）；默认 None 保持取今天行为不变

## 最新变更：DB 连接凭据改为本地配置文件（2026-08-05）

- **方案**: DESIGN-20260805-05
- **改法**: dao.py 新增 _load_db_url_from_local_config()，优先级 本地config > STOCK_DB_URL > 路径判定；新建 config/db_url.local.json（存 prod/dev 两套真实 URL，gitignore 排除）；密码不再写源码
- **原因**: 改造2 把密码脱敏为 *** 但生产/dev 均无注入机制 → Access denied；环境变量方案需逐个 cron 改命令繁琐，改本地文件最省事且 cron 兼容

## 当前阶段：选股追踪页面改版（已完成）

### 已发现问题
- **不同接口

**DAO 单连接模式导致链式崩溃**（2026-07-16）：
- `get_db()` 返回全局唯一连接，某次查询异常后连接损坏
- 后续所有 DB 请求复用损坏连接，全部报 `InterfaceError (0,'')`
- 根因：单例连接不支持多线程复用，无连接重建机制
- 修复方向：`utils/dao.py` 改为连接池（`DBUtils.PooledDB`）
- QA 也缺失了并发/连续请求测试场景（已在 SKILL.md 11.5 补充）

### 2026-08-05 git 结构重组 + DB 收敛 + limit_up_tracking 废弃

**背景**：dev 与生产在 main 上分叉且均未 push 导致脱节，已重组：
- 生产推 `4196eca` 到 origin/main（生产基线 `main`=4196eca）
- 新建独立 `dev` 分支（=4196eca 起点），开发迭代在 dev 分支，main 保持生产稳定基线
- **后续开发一律在 dev 分支进行**，main 只接受 merge 合入

#### 改造2：DB 连接统一收敛 ✅ 已完成（commit `ae7d7a7`）
- `utils/dao.py` 新增按项目路径自动判定生产/开发库（含 `StockAnalysis-dev` → 开发库，否则 → 生产库）；`STOCK_DB_URL` 显式设置最高优先；保留 unix_socket + 连接池
- 删除 `daily_fetch.py`/`fetch_all_stocks_daily.py`/`close_task.py` 的独立 `STOCK_DB_URL` 兜底块，连库统一收敛 dao.py 单一入口
- 方案：`docs/design/DESIGN-20260805-03`

#### 改造1：废弃 limit_up_tracking 表 ✅ 已完成（待提交 dev）
- `limit_up_analysis.py`：删 `limit_up_tracking` 建表+索引、`update_tracking()`、`run_daily_analysis` 内调用、`get_tracking_list()`、`get_continuous_trackers()`
- `daily_pick_v2.py`：删连板梯队死代码块（`results['trackers']` 无消费方）
- `daily_pick.py`：删除死文件（174行，零引用）
- `web_server.py`：删 `/api/limit-up/track` 孤儿接口（保留 4 个活接口）
- `morning_check.py`：🔗连板查询改活数据源 `daily_limit_up.board_times`（原读 `limit_up_tracking` 死表已停写，显示过期数据）
- **注**：`limit_up_tracking` 物理表数据保留不删（仅代码废弃，符合不动生产库数据约定）；是否 drop 表另议
- 方案：`docs/design/DESIGN-20260805-04`

### 正在处理（2026-07-16）

#### 1. MySQL 连接池改造 ✅ 已完成
- `utils/dao.py` 单连接 → `DBUtils.PooledDB`，已提交 `cbb2e34`

#### 2. 统一展示日期规则 ✅ 已完成
- 需求文档：`docs/requirements/REQ-20260716-02-统一展示日期规则.md`
- 方案设计：`docs/design/DES-20260716-02-统一展示日期规则.md`
- 新增 `utils/date_utils.py` 含 `get_display_date()` + `_is_trade_date()`
  - 规则：17:00前→T-1，17:00后→T，非交易日往前递推最多30步
- 修改 6 个 API 默认日期逻辑：
  - `api_market_overview`：从新浪实时改为 `index_quotes` 表按 `get_display_date()` 查
  - `api_market_summary`：默认日期走 `get_display_date()`
  - `api_sectors`：从 `MAX(record_date)` 改为 `get_display_date()`
  - `api_limit_up`/`api_limit_down`/`api_limit_up_industry`：从 `now.strftime` 改为 `get_display_date()`
- `get_market_summary()` 保持 `date_str` 参数兼容性（传参则用参数，不传走 `get_display_date()`）

### QA 规范补充（2026-07-16）
- `skills/engineering-rules/SKILL.md` 第 11.5 节新增：Web 服务类改动必须覆盖并发/连续请求测试

## 新增模块
| 模块 | 路径 | 说明 | 状态 |
|------|------|------|------|
| QA Subagent | `skills/qa-subagent/SKILL.md` | 独立质量验证agent | ✅ 2026-05-28 |
| 工程规范第11节 | `skills/engineering-rules/SKILL.md` | QA验证流程 | ✅ 2026-05-28 |
| git hook QA检查 | `.git/hooks/pre-commit` | 提交前检查.qa_pending标记 | ⚠️ 未装真hook（仅.sample模板） |
| 项目文档 | `project-doc/文档规范.md` | 文档根路径+命名规则 | ✅ 2026-08-06 |
| QA测试报告 | `project-doc/StockAnalysis/test/testrep-*.md` | 测试报告持久化（原 logs/qa/） | ✅ 2026-08-06 |

## 本次改动
| 文件 | 改动 | 状态 |
|------|------|------|
| `core/analyzer/daily_pick_v3.py` | **新增** 低吸抄底选股引擎（基于回测结论：大跌抄底+缩量胜率65.9%） | ✅ 2026-06-04 |
| `core/analyzer/close_task.py` | `_build_picks_data()` 改为调用 `daily_pick_v3.pick_stocks_v3()` 替代 v2 | ✅ 2026-06-04 |
| `core/reporter/close_report_tpl.py` | 第5段改为低吸抄底引擎输出格式（低吸抄底TOP5 + 放量反转组 + 操作计划） | ✅ 2026-06-04 |
| `docs/requirements/REQ-20260604-01-低吸抄底选股引擎.md` | **新增** 需求文档 | ✅ 2026-06-04 |
| `docs/design/DES-20260604-01-低吸抄底选股引擎.md` | **新增** 方案设计 | ✅ 2026-06-04 |
| `utils/date_utils.py` | **新增** `get_display_date()` + `_is_trade_date()` | ✅ 2026-07-16 |
| `web_server.py` | 6 个 API 日期逻辑统一改为 `get_display_date()` | ✅ 2026-07-16 |
| `utils/stock_analysis_api.py` | `get_market_summary()` 内部日期改用 `get_display_date()` | ✅ 2026-07-16 |
| `web_server.py` | `_build_picks_group_stats` 修复：收益率改为通过stock_daily的T+1/T+2数据计算，替代next_day_change字段 | ✅ 2026-07-28 |
| `web_server.py` | **新增** `_get_nth_trade_day_global` 方法，供分组统计使用 | ✅ 2026-07-28 |
| `docs/design/web_app.html` | 前端HTML/JS确认已无筛选控件，分组概览区块正常显示，表头11列 | ✅ 2026-07-28 |
| `core/analyzer/close_task.py` | **新增** `_render_group_stats_table` + `_push_group_stats_image` 生成分组统计表格图片并推飞书 | ✅ 2026-07-28 |

## 已改文件 (2026-06-02)

| 文件 | 改动内容 | 状态 |
|------|---------|------|
| `core/analyzer/scorer.py` | 替换 `logging.getLogger(__name__)` 为 `setup_logger("scorer")`，移除 import logging | ✅ |
| `core/reporter/morning_check.py` | 添加sys.path设置，确保子目录运行时from utils.dao可用 | ✅ 2026-05-28 |
| `utils/__init__.py` | 新建空文件，使utils成为Python包 | ✅ 2026-05-28 |

## 已改文件 (2026-05-12)

| 文件 | 改动内容 | 状态 |
|------|---------|------|
| `dao.py` | `insert_or_ignore()` 列名加反引号防MySQL保留字冲突 | ✅ 2026-05-14 |
| `close_task.py` | `run_picks()` 补调 `_save_picks_to_db()`，选股同时落库 | ✅ 2026-05-14 |
| `news_fetcher.py` | 重写为同花顺+财联社双源采集，去掉新浪；去重+按时间排序 | ✅ 2026-05-14 |
| `morning_check.py` | 涨跌家数改为从json_data解析（数据库字段为0时） | ✅ 2026-05-14 |
| `daily_fetch.py` | 新增market_summary写入、换板块接口、删除daily_market_summary写入 | ✅ |
| `limit_up_analysis.py` | DDL适配MySQL(AUTO_INCREMENT/TIMESTAMP/VARCHAR) | ✅ |
| `scorer.py` | 清除sqlite3残留、DAO适配MySQL | ✅ |
| `batch_fetch_limitup.py` | DAO适配MySQL | ✅ |
| `fetch_sector_ths.py` | DDL适配MySQL | ✅ |
| `sector_history.py` | DDL/INDEX适配MySQL | ✅ |
| `backtest_candidates*.py` | 5个文件DAO适配MySQL | ✅ |
| `_fetch_all_remain.py` | DAO适配MySQL | ✅ |
| `_fetch_csi500.py` | DAO适配MySQL | ✅ |
| `_fill_csi500.py` | DAO适配MySQL | ✅ |
| `_fill_missing.py` | DAO适配MySQL | ✅ |
| `data_store.py` | DDL适配MySQL(TEXT→VARCHAR)；删除market_summary建表及`save_market_summary`/`get_market_summary`方法；添加 `DROP TABLE IF EXISTS market_summary` 自动清理；修复sector_performance重复建表行 | ✅ |
| `close_task.py` | 迁移到MySQL(多文件)；market_summary调用改为从 `sector_performance` 汇总；成交额对比改为 `get_market_summary()` 返回值中的 `prev_amount` | ✅ |
| `dao.py` | 去掉SQLite分支，默认纯MySQL | ✅ |
| `intraday_monitor.py` | 重写为固定三段模版，去掉AI推理 | ✅ |
| 盘中监控cron×3 | payload改为直接输出脚本结果，不加分析 | ✅ |
| `daily_fetch.py` | market_summary入库校验放宽 + 板块/涨停加重试 | ✅ |
| `stock_analysis_api.py` | fetch_sector_full_data改用同花顺(收盘后可用)；_fetch_amount_total失败返回0不再返回20000亿；get_market_summary涨跌家数/成交额/板块排行均从sector_performance表汇总取数；删除硬编码假板块数据 | ✅ |
| `stock_analysis_api.py` | **market_summary清理**: 删除 `_fetch_rise_fall_counts`, `_fetch_amount_total`, `_fetch_sh/sz_market_summary`, `_fetch_market_summary_realtime`, `fetch_stock_market_summary`；`get_market_summary`重写为纯 `sector_performance` 查询 | ✅ 2026-05-14 |
| `daily_fetch.py` | 板块写入改为同花顺全量90条(rank_type='all')；板块+涨停均10次重试，不复用上日数据 | ✅ |
| `daily_fetch.py` | **market_summary清理**: 删除第3段市场汇总写入代码块，序号重新调整 | ✅ 2026-05-14 |
| `morning_check.py` | 无改动 | ✅ |
| 09:15 cron | 去掉daily_fetch.py，仅跑morning_check.py | ✅ |
| `daily_pick.py` | **market_summary清理**: `store.get_market_summary()` → `f.get_market_summary()`；`results['market_summary']` → `results['market']`；格式化输出适配新key名 | ✅ 2026-05-14 |
| `web_server.py` | **market_summary清理**: `api_market_summary()` 简化，删除已废弃的sectors字段；修复断开的dict语法错误 | ✅ 2026-05-14 |
| `tests/preflight.sh` | 从核心表检查列表中移除 `market_summary` | ✅ 2026-05-14 |
| `data_store.py` + `tests/preflight.sh` | 删除daily_market_summary表、建表代码、类方法、预检检查 | ✅ |
| `limit_up_analysis.py` | 涨停日期校验修正(092500不再误判) + 三重备份(AKShare/东财HTTP/新浪) | ✅ |
| `data_store.py` | `INDEX_quotes` INSERT→REPLACE INTO + 加 `record_date` 列 + 唯一约束 | ✅ 2026-05-14 |
| — | `stock_quotes` 表删除 | ✅ 2026-05-14 |
| — | `sector_daily_history` 表删除 + preflight.sh 核心表检查移除 | ✅ 2026-05-14 |
| `tests/preflight.sh` | `sector_daily_history` 从核心表检查列表移除 | ✅ 2026-05-14 |


## 已知问题

1. 板块数据已改用同花顺接口（`stock_board_industry_summary_ths`），非交易时段可用，问题已修复
2. 涨停数据已改为三重备份（AKShare → 东财HTTP → 新浪分页），且日期校验已放宽
3. ~~`daily_market_summary` 写入统计为0（数据源非交易时段不可用）~~ 表已废弃删除（2026-05-13）
4. ~~`close_task.py` 选股入库遗漏~~ 已补调 `_save_picks_to_db()`（2026-05-14）
5. ~~`dao.py` 的 `insert_or_ignore()` 拼SQL不带反引号，MySQL下`rank`是保留字报错~~ 已加反引号（2026-05-14），扫描全项目确认 `daily_limit_up.status` / `limit_up_tracking.status` / `limit_up_industry_stats.count` 走的是硬编码REPLACE INTO，不受影响
6. ~~`market_summary` 涨跌家数永恒为0~~ `daily_fetch.py` 取 `summary.get('rise_count')` 但实际key是 `up_count`，bug已修（2026-05-14）
7. ~~`stock_daily.volume/amount` 单位混乱（2026-05-15）~~
   - 根因：腾讯日K接口 row[5] volume=手(主板)/股(科创)，row[8] amount=万元，旧代码直接存原始值未转换
   - `fetch_all_stocks_daily.py` 已修复（单位转换逻辑补齐）
   - 全表 43万行已用腾讯原始数据重刷修复，`volume=股, amount=元` 统一
   - 9851 行 fine-tune 后 data 已同步更新（fix_all_picks_trade_dates_volume.py）

## 待办

- [x] 板块采集接口改为同花顺(收盘后可用) + 东财AKShare回退
- [x] 涨停采集改为三重备份(AKShare/东财HTTP/新浪)
- [x] 去除板块数据硬编码假数据(fetch_sector_performance_em fallback)
- [x] `close_task.py` 补调 `_save_picks_to_db()` — 2026-05-14
- [x] `news_fetcher.py` 改为同花顺+财联社双源 — 2026-05-14
- [x] `daily_fetch.py` `get_market_summary()` 返回key是`up_count`/`down_count`，但写入时取的是`rise_count`/`fall_count`→永远0→已修 — 2026-05-14
- [x] ~~`stock_daily.change_pct` 字段腾讯接口不返回涨跌幅~~ — 2026-05-15
  - ❌ 实际腾讯接口 `row[7]` 就是涨跌幅(%)，代码解析遗漏了该字段
  - `_parse_tx_kline_rows()` 原为 `change_pct: 0`（硬编码），改为 `change_pct: float(row[7])`
  - 全表 258万条 `change_pct=0` 已用前后日JOIN校正（脚本 `inline` 执行，未留文件）
  - 剩余 ~7.4万条为0（新股上市首日/池扩展无前日数据），属于正常
  - 删除已废弃的 `correct_change_pct()` 函数（不再需要）
- [x] `_save_picks_to_db()` 用 `trade_date="YYYYMMDD_精选"` 被 varchar(10) 截断 — 2026-05-14
  - `daily_picks` 表新增 `is_pick` 字段标记精选
  - 旧 `_精` 后缀数据已修复为纯日期，并标记 `is_pick=1`
  - `close_task.py` 查询改为 `is_pick=1` 优先
- [x] `sector_performance` 重复插入问题 — 2026-05-14
  - `daily_fetch.py` 改为 `INSERT IGNORE`
  - 加 UNIQUE(`record_date`,`sector_name`,`rank_type`) 约束
  - 已去重清理
- [x] `close_task.py` 昨日选股复盘只取前5只精选 — 2026-05-14
  - 改为 `is_pick=1` 条件，不再LIMIT 10全量
- [x] 涨跌家数从 `sector_performance` 行业汇总（已去重） — 2026-05-14
- [x] **评分系统 v5 重构**（2026-05-14）
  - 从"回顾型打分"改为"预测型评分"：筹码结构25+资金接力25+板块环境20+趋势位置20+大盘安全垫10
  - 去掉5个维度重叠：消息催化（快讯误判）、基本面（短线不看）、形态/技术面/板块热度（重复计分）
  - 涨停在资金接力维度仅按换手/封板质量计分，不再在多个维度叠加
  - `scorer.py`、`daily_pick_v2.py`、`docs/选股评分规则.md`、`tests/preflight.sh` 同步更新
  - 预检通过 ✅
- [ ] ~~优化 healthcheck.py 阈值~~（已废弃，被 data_reconciliation.py 覆盖）
- [ ] backtest_candidates_v3.py / v5.py / batch_fetch_limitup.py 仍有硬编码stock_data.db引用，需清理
- [ ] backtest_candidates*.py 回测脚本查 daily_limit_up 未过滤status='跌停'，需统一加过滤条件

## 已删除表
- **`market_summary`** (2026-05-14) — 依赖数据迁移到 `sector_performance`
- **`stock_quotes`** (2026-05-14) — 无使用场景，表及 `data_store.save_stock_quote()` 已删除
- **`sector_daily_history`** (2026-05-14) — 用到时再拉取，表及 preflight 检查已删除
- **`daily_snapshots`** (2026-05-15) — 冗余，指数数据在 `index_quotes` 中，覆盖代码已清理

## 今日改动 (2026-05-15)
- `fetch_all_stocks_daily.py` — `_fetch_one_tx` 传日期范围导致腾讯接口不返回今天数据，改为不传日期范围；`daily_incremental_update` 降并发为10路+二轮补拉；新增 `_verify_coverage` 校验覆盖率
- `limit_up_analysis.py` — `fetch_today_limit_up` 非交易时段直接跳过涨停采集，防止脏数据
- `daily_fetch.py`、`data_store.py`、`morning_check.py`、`web_server.py` — 清理 `daily_snapshots` 表和全部引用代码

## 今日改动 (2026-05-22)
- `fetch_all_stocks_daily.py` — `daily_incremental_update` 三处优化：
  1. **停止拉全年**：增量改为只拉今天一天的单日日K，新增 `_fetch_stock_today_batch()` 替代 `fetch_stock_daily_fast()`，节省 30 倍网络+解析开销
  2. **批量入库**：新增 `_batch_insert()` 用 INSERT IGNORE + 多行 VALUES 替代逐条 `insert_or_ignore`，减少 50 倍数据库往返
  3. **去掉第二轮补拉**：单日请求失败率 <0.5%，边际收益极低，不再需要
  - `_fetch_one_tx` 新增 `target_date` 参数（传则只拉单日，不传保持全量拉取兼容历史回溯）
  - 除权问题仍由 `_fix_exright_change_pct` 独立校正，不影响
  - 预期耗时：
    * 改前：~60 分钟（拉全年 52 万行 + 逐条 INSERT + 二轮补拉）
    * 改后：~2 分钟（单日 5201 条 + 批量 INSERT）

## 今日改动 (2026-05-22) 续
- `scorer.py` — **新增第6个维度「位置评估」（v5.4）**
  - 新增 `_score_position_in_range()` 函数：基于20日区间位置百分位打分
  - 评分逻辑：<30%低位→+15, 30~60%中位→+8, 60~85%偏高→+3, >85%极高位→0
  - 位置评分作为 bonus 加在总分后，不稀释其他维度权重，总分上限 100（总分公式追加 `+ pos_score`）
  - 触发背景：5.19~5.21 选股 30只中22只（73%）处于20日高位区间，胜率仅30%，追涨问题显著
  - breakdown 新增 `位置评估` 键值
- `daily_pick_v2.py` — 精选入库从 3+3=6只 改为 5+5=10只（与飞书推送一致）
- `docs/选股评分规则.md` — 文档同步更新至 v5.4：新增位置评估章节、总分计算公式、版本历史；精选入库规则更新
- `MEMORY.md` — 评分规则描述更新（v5.1→v5.2→v5.4）
- `PROJECT_STATE.md` — 本次改动记录

## 今日改动 (2026-05-21)
- `daily_limit_up` 表新增存储跌停数据（status='跌停'区分涨停/跌停）
- `daily_fetch.py` — 15:10采集新增跌停数据拉取（akshare stock_zt_pool_dtgc_em）
- `limit_up_analysis.py` — `get_today_limit_up()` 加 `WHERE status != '跌停'` 过滤；新增 `get_today_limit_down()` 方法
- `close_task.py` — 涨停计数改用过滤后数据；输出新增「💀 跌停分析」段
- `morning_check.py` — 涨停计数加过滤，跌停>0时显示「🚀涨停X只 | 💀跌停X只」
- `scorer.py` — 行业涨停数/最高连板/活跃板块计数均加 `status != '跌停'` 过滤
- `daily_pick_v2.py` — 3月内有过涨停标志子查询加 `status != '跌停'` 过滤
- `backtest*.py` — 待办中记录，后续统一修复
- `stock_daily` 表新增5字段: `total_market_cap`, `circulation_market_cap`, `turnover_rate`, `pe_ratio`, `pb_ratio`
- `fetch_all_stocks_daily.py` 新增 `daily_quotes_update()` 函数：腾讯实时行情接口(`qt.gtimg.cn`)采集收盘数据+市值，替换原16:30日K增量更新
- 16:30 cron 从日K增量更新改为实时行情采集
- `daily_pick_v2.py` 候选池筛选条件改为：**成交额>10亿 + 总市值>=100亿**（替代原来成交额>3000万）→ 后改为成交额>5亿 + 市值>=100亿；去掉LIMIT 300
- `limit_up_analysis.py` — 涨停采集时间校验上限从1500改为1530（修复15:10采集0条bug）
- 删除测试表 `test_market_cap`
- `daily_picks` 表新增5个维度分字段: `score_chip`, `score_money`, `score_sector`, `score_trend`, `score_market`
- `daily_pick_v2.py` — _save_picks_to_db() 写入各维度分到 daily_picks
- `pick_react.py` 新建文件：ReAct选股复盘，补全daily_picks次日涨跌数据+归因分析
- `close_task.py` — 第4段末尾追加ReAct归因报告

## 工程约束（改配置时必读）

### 同步约束
- **改 cron 时间/消息/脚本路径** → 同步更新 `MEMORY.md` 定时任务列表
- **改环境变量/端口号** → 同步更新 `MEMORY.md` 项目描述
- **改数据库连接串** → 同步更新 `AGENTS.md` 工程规则头部
- **新建数据库表** → 所有字段加 COMMENT（中文注释），表本身加 COMMENT
- **持仓表 `portfolio_positions`** — code/name/buy_date/cost_price/shares/updated_at
- **交易表 `portfolio_trades`** — trade_date/trade_time/trade_type(buy/sell)/code/name/shares/price/amount/pnl

### 数据接口字段单位约定（2026-05-15 补充）
各数据源原始单位与DB目标单位对照：

| 表 | 字段 | DB单位 | 数据源 | 原始单位 | Row索引 | 转换公式 |
|----|------|--------|--------|---------|---------|---------|
| `stock_daily` | volume | 股 | 腾讯日K(主板) | 手 | [5] | ×100 |
| `stock_daily` | volume | 股 | 腾讯日K(科创/北交) | 股 | [5] | 不转换 |
| `stock_daily` | amount | 元 | 腾讯日K(全部) | 万元 | [8] | ×10000 |
| `stock_daily` | change_pct | % | 腾讯日K(全部) | % | [7] | 直接使用 |
| `stock_daily` | volume/amount/change_pct | 各单位 | AKShare回退 | — | — | 已转换(ak内部) |
| `stock_daily` | volume | 股 | **腾讯实时(主板/创业板/中小板)** | **手** | **[6]** | **×100** |
| `stock_daily` | volume | 股 | **腾讯实时(科创板688/北交所4/8)** | **股** | **[6]** | **不转换** |
| `stock_daily` | amount | 元 | **腾讯实时(全部)** | **万元** | **[37]** | **×10000** |
| `stock_daily` | total_market_cap | 元 | **腾讯实时(全部)** | **亿** | **[45]** | **×100000000** |
| `stock_daily` | circulation_market_cap | 元 | **腾讯实时(全部)** | **亿** | **[44]** | **×100000000** |
| `stock_daily` | turnover_rate | % | **腾讯实时(全部)** | **%** | **[38]** | **直接使用** |
| `stock_daily` | pe_ratio | — | **腾讯实时(全部)** | — | **[39]** | **直接使用** |
| `stock_daily` | pb_ratio | — | **腾讯实时(全部)** | — | **[46]** | **直接使用** |
| `stock_daily` | close/open/high/low | 元 | **腾讯实时(全部)** | 元 | **[3/5/33/34]** | 直接使用 |
| `index_quotes` | volume | 手 | 新浪实时 | 手 | — | 不转换 |
| `index_quotes` | amount | 元 | 新浪实时 | 元 | — | 不转换 |

**规则：**
1. 改采集代码时，必须先在注释/常量里写清楚三个值：**原始单位 → 转换公式 → 目标单位**
2. 不允许 magic number（如 `row[5]*100` 必须写注释 `# 手→股`）
3. 不同板块/市场需要区分对待时，用代码分支处理并写明判断逻辑
4. 修复脚本需要保留字段单位转换逻辑记录（写在文件头部）
5. 回退/备用数据源单位与主源不一致时，必须做转换

## 已确认

### 定时任务状态 (2026-05-13 更新)

| 任务 | 脚本 | Payload | 输出形式 | 状态 |
|------|------|---------|---------|------|
| 09:15 盘前 | `python3 morning_check.py` | AI agent组装报告 | 四段固定模版（大盘/海外/新闻/策略） | ✅ |
| 09:40 盘中 | `python3 intraday_monitor.py` | 直接输出脚本结果 | 三段固定模版（大盘/持仓/操作提醒） | ✅ 2026-05-19新增 |
| 10:00 盘中 | `python3 intraday_monitor.py` | 直接输出脚本结果 | 同上 | ✅ 2026-05-13重写 |
| 10:30 盘中 | `python3 intraday_monitor.py` | 直接输出脚本结果 | 同上 | ✅ 2026-05-19新增 |
| 11:00 盘中 | `python3 intraday_monitor.py` | 直接输出脚本结果 | 同上 | ✅ 2026-05-19新增 |
| 11:25 盘中 | `python3 intraday_monitor.py` | 直接输出脚本结果 | 同上 | ✅ 2026-05-19新增 |
| 13:05 盘中 | `python3 intraday_monitor.py` | 直接输出脚本结果 | 同上 | ✅ 2026-05-13重写 |
| 13:30 盘中 | `python3 intraday_monitor.py` | 直接输出脚本结果 | 同上 | ✅ 2026-05-19新增 |
| 14:00 盘中 | `python3 intraday_monitor.py` | 直接输出脚本结果 | 同上 | ✅ 2026-05-19新增 |
| 14:30 尾盘 | `python3 intraday_monitor.py` | 直接输出脚本结果 | 同上 | ✅ 2026-05-13重写 |
| 15:10 采集 | `python3 daily_fetch.py` | 直接输出 | index/sector/limit_up | ✅ |
| 16:00 采集 | `python3 -c "from fetch_all_stocks_daily import daily_quotes_update; daily_quotes_update()"` | 直接输出 | 腾讯实时行情·20路并发200只/批 | ✅ 2026-05-19 |
| 17:00 复盘 | `python3 close_task.py` | AI agent组装报告 | — | ✅ 2026-05-19 |

## 最新变更 (2026-06-01 10:30)

| 文件 | 改动内容 | 状态 |
|------|---------|------|
| `docs/交易纪律.md` | **新增** — 止损三问判断流程（春秋电子卖飞复盘） | ✅ |
| `core/reporter/intraday_monitor.py` | **止损三问自动化** — 持仓亏损触发时自动输出量能/板块/时间三维判断 + 综合结论 | ✅ |
| `core/reporter/morning_check.py` | 纪律文案改为指引到新文档 | ✅ |
| `core/reporter/morning_auction_check.py` | 同上 | ✅ |
| `core/reporter/close_report_tpl.py` | 同上 | ✅ |
| `core/reporter/intraday_monitor.py` | 同上 | ✅ |
| `core/analyzer/daily_pick_v2.py` | 同上 | ✅ |
| `docs/design/止损三问自动化方案.md` | **新增** — 方案设计文档 | ✅ |

## 最新变更 (2026-05-26 16:55)

| 文件 | 改动内容 | 状态 |
|------|---------|------|
| `close_task.py` | **纯DB化改造** — 移除所有外部API调用；新增纯DB读取函数 | ✅ |
| `scorer.py` | `check_market_status()` 改为从 `index_quotes` 读取，不再调新浪API | ✅ |
| `daily_pick_v2.py` | 移除无用 `StockDataFetcher` 导入和实例化；`check_market_status` 改为纯DB | ✅ |
| `config/scorer_weights.json` | **新增** — 权重配置化 | ✅ |
| `scorer.py` | 权重改为从 JSON 配置读取 | ✅ |
| `pick_react.py` | **重写** — ReAct 三闭环 | ✅ |
| `docs/选股评分规则.md` | 更新至 v5.5 | ✅ |
| `observe_log` 表 (MySQL) | **新增** | ✅ |

## 最新变更 (2026-05-27 10:30)

| 文件 | 改动内容 | 状态 |
|------|---------|------|
| `.git/hooks/prepare-commit-msg` | **新建** — 前置检查：提交前自动显示工程规范摘要 + 当前项目阶段 | ✅ |
| `.git/hooks/pre-commit` | **新建** — 后置校验：提交前强制跑 `check_engineering.sh`，不通过阻止提交 | ✅ |
| `morning_check.py` | 修复 `format_today_picks()` 取 `is_pick=1` 精选推荐（之前取 `rank<=5` 与收盘复盘不一致） | ✅ |
| `morning_auction_check.py` | **新增** — 集合竞价检查脚本（09:26 cron） | ✅ |
| `skills/requirement-doc/` | **新增** — 需求文档 skill + 模板 | ✅ |
| `skills/design-doc/` | **新增** — 方案设计 skill + 模板 | ✅ |
| `docs/requirements/` | **新建** — 需求文档存档目录 | ✅ |
| `docs/design/` | **新建** — 方案设计存档目录 | ✅ |
| `utils/data_validator.py` | **新增** — 数据校验工具类（单位/非空/范围/时效性） | ✅ |
| `tests/data_reconciliation.py` | **新增** — 每日数据对账脚本（核心表最新日期/数据量/过期检查） | ✅ |
| `configs/` `core/` `data/` `logs/` `utils/` | **新建** — 按目标结构创建空目录 | ✅ |
| `tests/check_engineering.sh` | **优化** — scorer.py 文档检查改为检测评分逻辑变更才强制，格式修复不阻止 | ✅ |
| 空 except 修复 | **修复** — 全项目15处 `except:` → `except Exception:` | ✅ |
| 采集文件单位注释 | **新增** — 10个采集文件顶部注明数据源单位转换 | ✅ |

## 最新变更 (2026-05-27 14:30)

| 文件 | 改动内容 | 状态 |
|------|---------|------|
| `stock_analysis_api.py` → `utils/stock_analysis_api.py` | **目录迁移** — N+批迁移，工具箱:工具库 | ✅ |
| `strategy.py` → `utils/strategy.py` | 同上 | ✅ |
| `fundamental.py` → `utils/fundamental.py` | 同上 | ✅ |
| `sector_history.py` → `utils/sector_history.py` | 同上 | ✅ |
| `alert_system.py` → `utils/alert_system.py` | 同上 | ✅ |

**目录迁移进度：3/7 批（43%）**

## 最新变更 (2026-05-27 13:46)

| 文件 | 改动内容 | 状态 |
|------|---------|------|
| `daily_fetch.py` → `core/fetcher/daily_fetch.py` | **目录迁移** — 第三批：采集模块 | ✅ |
| `fetch_all_stocks_daily.py` → `core/fetcher/fetch_all_stocks_daily.py` | 同上 | ✅ |
| `limit_up_analysis.py` → `core/fetcher/limit_up_analysis.py` | 同上 | ✅ |
| `news_fetcher.py` → `core/fetcher/news_fetcher.py` | 同上 | ✅ |
| `fetch_sector_ths.py` → `core/fetcher/fetch_sector_ths.py` | 同上 | ✅ |
| `batch_fetch_limitup.py` → `core/fetcher/batch_fetch_limitup.py` | 同上 | ✅ |

## 最新变更 (2026-05-27 14:00)

| 文件 | 改动内容 | 状态 |
|------|---------|------|
| `scorer.py` → `core/analyzer/scorer.py` | **目录迁移** — 第四批：分析模块 | ✅ |
| `daily_pick.py` → `core/analyzer/daily_pick.py` | 同上；路径修复：sys.path→项目根目录 | ✅ |
| `daily_pick_v2.py` → `core/analyzer/daily_pick_v2.py` | 同上 | ✅ |
| `close_task.py` → `core/analyzer/close_task.py` | 同上 | ✅ |
| `pick_react.py` → `core/analyzer/pick_react.py` | 同上 | ✅ |

**目录迁移进度：4/7 批（57%）**

## 最新变更 (2026-05-27 14:05)

| 文件 | 改动内容 | 状态 |
|------|---------|------|
| `morning_check.py` → `core/reporter/morning_check.py` | **目录迁移** — 第五批：报告模块 | ✅ |
| `morning_auction_check.py` → `core/reporter/morning_auction_check.py` | 同上；路径修复 PROJECT_ROOT 3层 dirname | ✅ |
| `intraday_monitor.py` → `core/reporter/intraday_monitor.py` | 同上 | ✅ |
| `_report_gen.py` → `core/reporter/_report_gen.py` | 同上（模块级DB查询，跳过stub验证） | ✅ |
| `close_report_tpl.py` → `core/reporter/close_report_tpl.py` | 同上（纯模版层，无依赖） | ✅ |

**目录迁移进度：5/7 批（71%）**

## 最新变更 (2026-05-27 14:00)

| 文件 | 改动内容 | 状态 |
|------|---------|------|
| `_fetch_all_remain.py` → `core/fetcher/` | **目录迁移** — 第六批：采集辅助+回测 | ✅ |
| `_fetch_csi500.py` → `core/fetcher/` | 同上 | ✅ |
| `_fill_csi500.py` → `core/fetcher/` | 同上 | ✅ |
| `_fill_missing.py` → `core/fetcher/` | 同上 | ✅ |
| `backtest_candidates.py` → `core/fetcher/` | 同上 | ✅ |
| `backtest_candidates_v2.py` → `core/fetcher/` | 同上 | ✅ |
| `backtest_candidates_v3.py` → `core/fetcher/` | 同上 | ✅ |
| `backtest_candidates_v4.py` → `core/fetcher/` | 同上；修复硬编码路径、PROJECT_ROOT 顺序 | ✅ |
| `backtest_candidates_v5.py` → `core/fetcher/` | 同上 | ✅ |

**目录迁移进度：6/7 批（86%）**

## 最新变更 (2026-05-27 14:10)

| 文件 | 改动内容 | 状态 |
|------|---------|------|
| `dashboard.py` → `utils/dashboard.py` | **目录迁移** — 第七批：剩余文件归位 | ✅ |
| `visualization.py` → `utils/visualization.py` | 同上 | ✅ |
| `sync_portfolio.py` → `utils/sync_portfolio.py` | 同上 | ✅ |
| `healthcheck.py` → `tests/healthcheck.py` | **目录迁移后废弃** — 被 data_reconciliation.py 覆盖，已删除 | ✅ |
| `test_data_quality.py` → `tests/test_data_quality.py` | **目录迁移后重命名** — → `tests/api_validation.py`，用于API数据格式/单位校验 | ✅ |
| `migrate_to_mysql.py` | **保留** 根目录（一次性脚本） | ✅ |
| `stock_analysis_api.py` stub | **修复** 显式导出 _curl_text 保证 api_validation 可用 | ✅ |

**目录迁移进度：7/7 批（100%）✅**

## 目录迁移完成状态

| 目录 | 包含文件 | 状态 |
|------|---------|------|
| 根目录 | `main.py` `web_server.py` `migrate_to_mysql.py` + 39个 stub | 3个真身 + stub 向后兼容 |
| `utils/` | dao, data_store, data_parser, stock_analysis_api, strategy, fundamental, sector_history, alert_system, dashboard, visualization, sync_portfolio, data_validator | 12个工具类 |
| `core/fetcher/` | daily_fetch, fetch_all_stocks_daily, limit_up_analysis, news_fetcher, fetch_sector_ths, batch_fetch_limitup, _fetch_*, _fill_*, backtest_candidates* | 15个采集/回测 |
| `core/analyzer/` | scorer, daily_pick, daily_pick_v2, close_task, pick_react | 5个分析模块 |
| `core/reporter/` | morning_check, morning_auction_check, intraday_monitor, _report_gen, close_report_tpl | 5个报告模块 |
| `tests/` | preflight, check_engineering, api_validation, data_reconciliation | 4个测试/门禁 |

所有 stub 保持向后兼容，新旧 import 均可正常工作。

## 最新变更 (2026-05-27 14:00)

| 文件 | 改动内容 | 状态 |
|------|---------|------|
| `dao.py` → `utils/dao.py` | **目录迁移** — 真身迁到 utils，根目录保留 stub 向后兼容 | ✅ |
| `data_parser.py` → `utils/data_parser.py` | **目录迁移** — 同上模式 | ✅ |
| `data_store.py` → `utils/data_store.py` | **目录迁移** — 同上模式 | ✅ |
| `tests/check_engineering.sh` | **增强** — 新增函数长度检测（>80行提示）、段号统一 | ✅ |
| `utils/data_validator.py` | 已存在（上轮新增） | ✅ |
| `tests/data_reconciliation.py` | 已存在（上轮新增） | ✅ |

## 开发流程门禁体系

### 三层门禁

| 层次 | 工具 | 时机 | 作用 |
|------|------|------|------|
| **前置检查** | OpenClaw hook `dev-flow` | 消息入口 | 检测开发需求关键词 → 检查文档是否就绪 → 缺失则提醒 |
| **改前自检** | AGENTS.md 规则 + engineering-rules 3.4 节 | 开始写代码前 | AI 口头过 6 条清单 → 确认后再动代码 |
| **改后校验** | git hook `pre-commit` | 提交时 | 强制跑 check_engineering.sh → 不通过阻止提交 |

### 改前自检 6 条（AI 写代码前必须口头确认）

```
✅ [确认] 需求范围是否明确？影响哪些文件？验收标准是什么？
✅ [文档] project-doc/<项目>/requirement/ 下是否有匹配的需求文档？无 → 先生成（规范见 project-doc/文档规范.md）
✅ [文档] project-doc/<项目>/design/ 下是否有方案设计？复杂改动无 → 先生成
✅ [状态] PROJECT_STATE.md 是否已读？当前项目状态是否清晰？
✅ [规范] skills/engineering-rules/SKILL.md 是否已读？最新版本？
✅ [改后] 改完后是否会跑 tests/check_engineering.sh？
```

**规则：** 检查未通过前，禁止写任何代码。

---

**目录迁移策略**：真身迁到 `utils/`，根目录 stub 文件保留 `from utils.xxx import *`，旧代码 import 不受影响。新代码统一走 `from utils.xxx import` 路径。

### 2026-05-27

#### 今日变更
- **ReAct三闭环集成**：close_task.py 调用 pick_react.run_react_analysis() 替代 _build_react_data()，observe_log 表写入完整
- **预检修复**：preflight.sh 中 7 处 import 路径从旧路径（from dao/data_store 等）改为新路径（from utils.xxx / core.xxx）
- **ReAct 需求文档+方案设计**：docs/requirements/REQ-20260527-02-ReAct三闭环集成.md + docs/design/DES-20260527-02-ReAct三闭环集成.md

### 2026-05-29

#### 今日变更
- **修复3处import bug（commit 6b67c4a遗留）**：
  - `core/fetcher/news_fetcher.py`: `import requests` 错写成 `os.makedirs(...), requests`
  - `core/reporter/morning_auction_check.py`: `import urllib.request` 错写成 `os.makedirs(...), urllib.request`
  - `core/reporter/intraday_monitor.py`: `import time` 错写成 `os.makedirs(...), time`
- **统一9:26晨间监控与复盘推送股票池**：`morning_auction_check.py` 从 `is_pick=1 + rank<=5` 并集改为仅取 `is_pick=1`，与复盘报告保持一致
- **ReAct复盘近一周统计修复**：`pick_react.py` 的近一周/评分归因从仅统计最新1天改为统计最近5个交易日，之前重复代码导致"近一周: 5只"的误导输出
- **pre-commit hook修复**：`.qa_pending` 检查条件从 `if [ -f ]` 改为 `if [ ! -f ]`（有Python改动但无QA标记时拦截提交）
- **个股收盘采集恢复飞书推送**：`fetch_all_stocks_daily.py` daily_quotes_update() 末尾加 print() 输出采集结果，cron announce 可正常推送至飞书（之前仅有 logger.info 写日志文件，stdout 为空）
- **昨日选股复盘改为仅取精选**：`close_task.py` _load_yesterday_picks() SQL 从`LIMIT 10`改为`is_pick=1`，只展示精选5只
- **板块表现修复**：`close_task.py` _load_sector_data() 从按 rank_type=top_gain/top_fall 查询改为直接从 rank_type='all' 排序取前/后10名，解决 daily_fetch.py 只写入 'all' 类型导致板块为空的问题
- **复盘增加分步耗时日志**：`close_task.py` daily_close_task() 每个关键阶段加 [TIMING] 输出（指数加载、板块数据、涨停数据、明日选股等），便于定位cron超时瓶颈
- **ReAct复盘日期范围修复**：`pick_react.py` 原显示单一日期（取最近一天），改为显示最早一天~最近一天，如 `选股20260522~20260528 → 检验20260529`

### 2026-06-02

#### 2026-06-10 cron command模式改造
- **command模式替代agentTurn**：3个定时任务（15:10快照、16:00数据采集、16:30复盘）迁移到command模式，Gateway直接执行shell，不走模型调用
- **setup_logger默认console=False**：`utils/logger.py` 修改，所有模块日志只写文件不输出stdout
- **daily_fetch.py增加stdout输出**：`__main__`块末尾增加格式化打印，用于command模式推送飞书
- **受影响任务**：`15:10 daily_fetch`, `16:00 fetch_all_stocks_daily`, `16:30 close_task`

#### 今日变更
- **新建utils/logger.py**：统一日志工具，提供 setup_logger() + timing() 函数
  - 日志同时输出到文件（logs/目录）和 stdout
  - 使用 TimedRotatingFileHandler 按天轮转，保留30天
  - 统一格式：`YYYY-MM-DD HH:MM:SS [LEVEL] module: message`
  - 提供 TimerHelper 类支持分步计时
- **重构close_task.py日志**：删除 __main__ 块内自行配置的 logging.basicConfig，改为引用utils/logger.py的 setup_logger()
  - `_log_timing()` 从 print 改为 logger.info，TIMING 数据同时写入日志文件和 stdout
  - 日志时间戳正常显示（之前缺少 format 参数导致无时间）
- **将旧版 `print('[TIMING] ...')` 全部替换为 `logger.info('[TIMING] ...')`**

### 2026-06-02 (2)

#### 今日变更
- **涨停回踩up_group排序加二级维度**：总分数相同按recoil_pct降序（回踩越深越优先），确定性排序
- **区间潜伏non_up_group排序加二级维度**：总分数相同按_60d_position升序（低位更安全）

### 2026-06-16

#### 今日变更
- **新增 `core/fetcher/push2_market.py`**：东财 push2 实时市场数据获取模块
  - 实时获取两市：成交额(f6)、涨跌家数(f104/f105)、主力资金(f62)、散户资金(f84)
  - 30秒缓存；curl + subprocess 访问，urllib兼容性问题绕过
  - 非交易时段返回 None，不报错中断
- **升级 `core/reporter/intraday_monitor.py` 第1段大盘概况**：
  - 新增盘面摘要行：📊 涨跌家数（两市合并，跌多时跌放前）
  - 新增资金流行：💰 成交额 + 主力资金 + 散户资金
  - 风险提示新增资金流条件触发（主力流出>500亿/涨跌比严重失衡）
  - push2 失败降级：跳过盘面摘要行，仅显示指数行
- **升级 `core/reporter/intraday_monitor.py` 回退方案**：
  - push2 失败时，从新浪指数实时行情补成交额（新浪 amount 单位为万元，转亿元）
  - 同时回退到 `sector_performance` 缓存补充涨跌家数（15:10快照后可用）
  - 回退数据不足时仅输出成交额行，不输出空行
- **升级 `core/reporter/morning_auction_check.py` 09:26集合竞价**：
  - 从仅昨日精选→近3个交易日精选(去重,保留最新评分)
  - 输出按日期分组排序：今日精选→昨日精选→前日精选
  - 组内按竞价强弱排序(strong>good>neutral>weak>bad)
  - 添加日期分隔线标注

## 最新变更 (2026-07-08)

| 文件 | 改动内容 | 状态 |
|------|---------|------|
| `core/fetcher/lhb_fetcher.py` | **新增** — 龙虎榜席位交易数据采集模块 | ✅ |
| `lhb_seat_trades` 表 | **新增** — 营业部交易明细存储表 | ✅ |
| `lhb_tracking_seats` 表 | **新增** — 追踪席位清单表 | ✅ |
| `lhb_tracking_seats` 数据 | **写入** — 章盟主5个席位信息 | ✅ |
| `lhb_seat_trades` 数据 | **采集** — 2023年~2026年7月共1418条记录 | ✅ |
| `docs/requirements/REQ-20260708-01-龙虎榜数据采集与游资追踪.md` | **新增** — 需求文档(前期已完成) | ✅ |
| `docs/design/DES-20260708-01-龙虎榜数据采集与游资追踪.md` | **新增** — 方案设计(前期已完成) | ✅ |

### 新增数据库表
| 表名 | 说明 | 关键字段 |
|------|------|---------|
| `lhb_tracking_seats` | 追踪席位清单 | seat_code, seat_name, owner, status |
| `lhb_seat_trades` | 营业部交易明细 | trade_date, seat_code, stock_code, act_buy, act_sell, net_amt, d1~d30涨跌幅 |

### 数据采集完成
- **采集策略**: 每个席位一次性全量拉取（不按月切分），INSERT IGNORE 幂等入库
- **数据范围**: 2023-01 ~ 2026-07
- **总计**: 1418条（新增1182，重复462）
- **席位覆盖**:
  - 国泰海通上海海阳西路: 525条(2024-08~2026-04，已关闭)
  - 中信杭州延安路: 320条(2023-01~至今)
  - 国泰海通宁波广福街: 326条(2023-01~至今)
  - 国泰海通上海建国西路: 184条(2023-03~至今)
  - 中信杭州富春路: 63条(2023-01~至今)

### 待办
- [ ] 持仓还原分析模块（分析游资持仓及成本）
- [ ] lhb_fetcher 集成到 daily_fetch.py 的定时采集流程
- [ ] 前端龙虎榜展示（Web仪表盘）

## 最新变更 (2026-07-21) — Web页面Bug修复

| 文件 | 改动内容 | 状态 |
|------|---------|------|
| `core/fetcher/daily_fetch.py` | **修复1** — 涨停拉取成功后追加 `save_industry_stats()` 调用，`limit_up_industry_stats` 表有数据 | ✅ |
| `core/analyzer/daily_pick_v2.py` | **修复2** — `_save_picks_to_db()` 写入时按 `r.get('group')` 为 `data_tag` 赋值：涨停回踩→`limitup`，区间潜伏→`range` | ✅ |
| `web_server.py` | **修复3** — 选股追踪页面入选日格式化为 `YYYY-MM-DD`（`fmtDate()`函数），修复 `60721`截断 | ✅ |
| `web_server.py` | **修复4** — 新增选股追踪Tab，表格表头可点击排序（评分/T+1~T+5），纯前端JS实现，默认按评分降序，↑↓指示箭头 | ✅ |

## 最新变更 (2026-07-16)

| 文件 | 改动内容 | 状态 |
|------|---------|------|
| `docs/requirements/REQ-20260715-01-Web服务前端需求.md` | **更新至 v2.1** — 增加 UI 规范章节、板块排行字段说明、指数卡片 grid 5列规范 | ✅ |
| `docs/design/web_prototype.html` | **更新** — 左侧菜单、指数卡片 grid 5列、板块排行纵向双表、涨跌复盘分页排序 | ✅ |
| `docs/design/DES-20260716-01-Web前端原型技术方案.md` | **新增** — Web 前端原型技术方案，含架构设计、API 依赖、分页排序策略、新增 `/api/picks` 接口说明、开发计划 | ✅ |

## 最新变更 (2026-07-14)

| 文件 | 改动内容 | 状态 |
|------|---------|------|
| `core/analyzer/scorer.py` | **评分循环去掉 `get_risk_flags` 外部HTTP调用** — 风险扣分依赖AKShare同花顺财务接口，每天评分触发400+次外网请求，导致cron复盘间歇性超时 | ✅ |
| `docs/选股评分规则.md` | **同步更新** — 总分计算去掉风险扣分、数据源去掉AKShare | ✅ |

### 原因
- 复盘任务cron 16:30运行时，评分循环每只候选股调 `get_risk_flags` → `get_latest_financial` → AKShare同花顺接口
- 接口偶发SSL超时（`Max retries exceeded`），卡住1-2分钟，叠加cron 600s超时，导致任务被杀死
- 风险扣分仅-5~-15分，年报数据对短线选股价值低


## 最新变更：飞书推送 ReAct 复盘换行修复（2026-07-28）
- **文件修改**: `core/analyzer/close_task.py`
- **变更内容**: `daily_close_task()` 末尾 print(report) 前增加换行转换逻辑
  - `\n\n\n` → `\n\n`（缩减多余空行）
  - `\n\n` → `\n\n\n`（单换行变双换行，防飞书吞换行）
  - 只改 print 输出前处理，不改 render_report 逻辑
- **原因**: 飞书纯文本 command 模式推送合并连续换行，ReAct 复盘段 lose 了换行格式
- **验证**: check_engineering.sh ✅, 语法检查 ✅, import 检查 ✅

## 最新变更：v6评分因子（维度）剔除回测（正确版）（2026-07-31）

- **场景**: 纠正昨晚 `backtest/backtest_v6copy.py`（未跟踪）的假回测（8轮结果全一样，因每笔重跑完整选股+新浪fallback）
- **新增**: `backtest/backtest_v6_ablation.py` — 直接读 daily_picks 已存评分 + stock_daily 开盘价，维度级剔除回测
- **需求/方案**: `docs/requirements/REQ-20260731-01-v6评分因子剔除回测.md` + `docs/design/DES-20260731-01-v6评分因子剔除回测.md`

### 关键数据语义核查结论（重要）
- `daily_picks` 数据由 **v4 评分体系**（6 大维度）写入，**非** `scorer_v6_copy.py`（v6只含SKIP子因子开关，从未批量落库）
- **有效维度列仅 6 个**: score_chip/money/sector/trend/market/pos；`score_pop/tech/mkt/logic` 全 0，无法用于剔除
- 主人给出的 7 子因子（chip_deposit 等）是 v6 维度内子项，已存整维度列无法精确还原 → 本项目改为**维度级剔除**（减掉整个维度列分），仅 market_safety→score_market、position→score_pos 为精确
- 已存 `total_score`（mean 32.9, max 78）≠ 6维列和（mean 26.1），有 +5/+8/+10 固定偏差
- **A/B/C/D 绝对档位退化**: 99.8% 落 A区(<60)，档位统计无区分意义 → 因子判定改用**相对五分位区分度(Q5-Q1)**为主依据

### 回测结论（20260428~20260730，样本21481，全程只查库无网络，耗时约1.5s）
| 轮次 | 区分收益pct (Q5-Q1) | Δvs基准 | 判定 |
|------|------|------|------|
| 基准 | +0.211 | — | — |
| 剔除_筹码结构 | -0.084 | +0.295 | 弱效（略变差，保留低权重） |
| 剔除_资金接力 | +0.757 | -0.546 | **无效（剔除后变好→可删）** |
| 剔除_板块环境 | +0.198 | +0.013 | 中性（板块分几乎全=5，无区分） |
| 剔除_趋势位置 | +0.317 | -0.106 | 中性偏无效 |
| 剔除_大盘安全垫 | +0.194 | +0.017 | 中性（大盘分几乎全=7，无区分） |
| 剔除_位置评估 | -0.304 | +0.515 | **有效（剔除后区分度消失→保留）** |
- **最有效**: 位置评估（剔除后区分度翻负，唯一真正驱动排序预测力）
- **疑似无效可删**: 资金接力
- **近乎常量的无区分维**: 板块环境(几乎全5)、大盘安全垫(几乎全7)

## 最新变更：集合竞价检查筛选条件调整（2026-07-28）
- **文件修改**: `core/reporter/morning_auction_check.py`
- **变更内容**: 将筛选条件从 `is_pick=1`（精选）调整为 `total_score>=60`（≥60分），涉及4处修改
  - 第一条SQL：`is_pick=1` → `total_score>=60`
  - 第二条SQL：`AND is_pick=1` → `AND total_score>=60`
  - 无数据提示：`近3日无精选股数据` → `近3日无≥60分的股票数据`
  - 监控标题：`近3日精选监控` → `近3日≥60分监控`
- **原因**: 统一使用分数阈值筛选，与评分体系对齐
- **验证**: Preflight 全部通过

## 最新变更：大盘缓存清理 + close_task 注释清理（2026-08-06）

**对应方案**: `docs/design/DESIGN-20260806-01-大盘缓存清理.md`

**① 大盘缓存清理**（`core/analyzer/scorer.py` + `core/analyzer/daily_pick_v2.py`）：
- `scorer.py`：删除模块级全局缓存 `_market_cache`；`check_market_status()` 变纯查询（每次调用查一次库，调用方自行上移）；`score_candidate()` 新增 `market=None` 参数，None 时内部取一次大盘兜底（向后兼容）
- `daily_pick_v2.py`：L188 `score_candidate(code, name, market=market)` 显式传 key 为 0 的大盘 dict，大盘查库从「几百只 × 1 次」降为「单次查询1次」
- **根因**: 大盘数据单次选股只需算一次，旧全局缓存是「假优化」；删除后依赖在 `daily_pick_v2` 上移为单次调用
- **验证**: 语法检查 ✅、import 检查 ✅、check_engineering.sh ✅（除门禁脚本自身 pre-stored 缺陷项）、QA 报告 `logs/qa/20260806_143929.report.md`（结论 ✅ 通过）

**② close_task.py 注释清理**（`core/analyzer/close_task.py`）：
- 清理 2 处过时的 `limit_up_tracking` 注释残留（docstring L217 + 换手率注释 L396），与已废弃 `limit_up_tracking` 表保持一致。纯注释改动，无逻辑变更。
