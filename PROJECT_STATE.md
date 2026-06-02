# PROJECT_STATE.md — 项目状态看板 (AI维护)

## 项目概览
- **项目**: StockAnalysis — 股票分析系统
- **路径**: `/Users/wangyanming/workspace/StockAnalysis/`
- **数据库**: MySQL `mysql://root:stock123@127.0.0.1:3306/stock_analysis`
- **预检**: `bash tests/preflight.sh`
- **API校验**: `python3 tests/api_validation.py`
- **数据对账**: `python3 tests/data_reconciliation.py`

## 当前阶段：QA Subagent 构建完成 ✓

## 新增模块
| 模块 | 路径 | 说明 | 状态 |
|------|------|------|------|
| QA Subagent | `skills/qa-subagent/SKILL.md` | 独立质量验证agent | ✅ 2026-05-28 |
| 工程规范第11节 | `skills/engineering-rules/SKILL.md` | QA验证流程 | ✅ 2026-05-28 |
| git hook QA检查 | `.git/hooks/pre-commit` | 提交前检查.qa_pending标记 | ✅ 2026-05-28 |
| QA日志目录 | `logs/qa/` | 测试报告持久化 | ✅ 2026-05-28 |

## 本次改动
| 文件 | 改动 | 状态 |
|------|------|------|
| `core/analyzer/scorer.py` | 替换 logging.getLogger 为 setup_logger 统一日志工具 | ✅ 2026-06-02 |

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
✅ [文档] docs/requirements/ 下是否有匹配的需求文档？无 → 先生成
✅ [文档] docs/design/ 下是否有方案设计？复杂改动无 → 先生成
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

