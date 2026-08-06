# DESIGN-20260805-04 废弃 limit_up_tracking 表（基于新基线 4196eca 重做）

- **编号**：DESIGN-20260805-04
- **日期**：2026-08-05
- **状态**：待实施
- **开发分支**：dev（main 保持生产基线 4196eca 不动）
- **背景**：原 DESIGN-20260805-01 基于过期基线 b777b64 实施，已因脱节舍弃。本次基于 4196eca 重做。经勘查，4196eca 的 backtest/（backtest_controller/group_backtest）直接用 utils.dao.get_db() 取数，**不读 limit_up_tracking**，故"回测按 T 日改算连板"部分无对应目标，本方案仅聚焦 limit_up_tracking 废弃清理。

## 一、目标
废弃 limit_up_tracking 表相关代码（建表/索引/写入/读取/孤儿接口/死文件/死代码块）。仅代码层面废弃，**不动生产库表数据**。

## 二、清理范围（已勘查确认）

### A. core/fetcher/limit_up_analysis.py
| 行 | 内容 | 处理 |
|----|------|------|
| 61 | `CREATE TABLE IF NOT EXISTS limit_up_tracking(...)`（init_zt_tables 内） | 删该建表块 |
| 101 | `CREATE INDEX idx_track_code ON limit_up_tracking(code)` | 删 |
| 382-434 | `def update_tracking(self, trade_date=None)` | 删整个方法 |
| 467 | `self.update_tracking(trade_date)`（run_daily_analysis 内） | 删这行调用（run_daily_analysis 其余保留） |
| 496-512 | `def get_tracking_list(...)` | 删整个方法 |
| 525-536 | `def get_continuous_trackers(...)` | 删整个方法 |

**保留**：get_today_limit_up(472)/get_today_limit_down(484)/get_industry_stats(513)/get_available_dates(545)/fetch_today_limit_up(153)/save_today_limit_up(322)/save_industry_stats(435)/_parse_board(537)/run_daily_analysis(除467行)/init_zt_tables 其余表。

### B. core/analyzer/daily_pick_v2.py 80-90
连板梯队块（调 get_continuous_trackers 赋 results['trackers']）——**删整个块**。已确认 results['trackers'] 无消费方（仅 86 行赋值+日志），候选评分不依赖，是纯死代码。

### C. core/analyzer/daily_pick.py（174 行整文件）
死文件，零引用（无 import）——删除文件。

### D. web_server.py
- 79-80：`elif path == "/api/limit-up/track": self.api_limit_up_track()` 路由 — 删
- 290-296：`def api_limit_up_track(self):...` — 删整个方法
- **保留** /api/limit-up、/api/limit-up/industry、/api/limit-up/refresh、/api/limit-up/dates（活接口）

## 三、不做的事
- 不删生产库 limit_up_tracking 表数据（仅代码废弃）
- 不改 backtest/、不改 morning_check、不改 close_task（4196eca 上无对应）
- 不加 commit hook、不动 main

## 四、验证
1. py_compile 改动文件
2. `grep -rn "get_continuous_trackers\|get_tracking_list\|update_tracking\|limit_up_tracking"` 在 py 文件 → 应只剩 limit_up_analysis.py 顶部注释/文档提及（或无）
3. web_server 能 import + `/api/limit-up/track` 路由消失、其余 4 个 limit-up 接口仍在
4. daily_pick.py 已删除，web_server 无引用
5. daily_pick_v2 能 import（连板梯队块删除后无悬空引用）
6. limit_up_analysis.py 能 import + 活方法（get_today_limit_up 等）仍可用
7. bash tests/check_engineering.sh 门禁

## 五、回滚
未提交 git checkout -- 还原；已提交 git revert。

## 六、风险
- 风险-1：误删活方法 → 验证项 2/3/4/6 覆盖
- 风险-2：daily_pick_v2 删块后 import 报错（如日志变量 _log_timing 作用域）→ 验证项 5

## 【补充：方向A】morning_check.py 连板查询改活数据源

### 发现（验收时）
morning_check.py:108 有一条真实 SQL 读 limit_up_tracking 死表（晨报🔗连板栏目）：
```sql
SELECT name, max_board_count FROM limit_up_tracking WHERE latest_limit_date=%s AND max_board_count>=3 ORDER BY max_board_count DESC LIMIT 5
```
- limit_up_analysis 的 update_tracking 删除后无人再写 limit_up_tracking
- 实测：limit_up_tracking 最新数据停留在 2026-05-15（5月以来无人写），晨报一直显示过期连板，误导
- 原方案勘查漏了 reporter/ 目录，范围未覆盖 morning_check（RDAgent 严格按范围执行未动它，是正确的）

### 活数据源（已实证）
daily_limit_up 表有 **board_times 字段 = 当日连板数**，实时（实测 08-05 有传智教育8板/德龙汇能4板等）。

### 改动
morning_check.py:108 连板查询改为：
```sql
SELECT name, board_times FROM daily_limit_up WHERE trade_date=%s AND board_times>=3 ORDER BY board_times DESC, seal_first_time ASC LIMIT 5
```
- trade_date 参数沿用（晨报用 T 日日期的 trade_date 变量）
- 输出格式 ``{name}{board_times}板`` 保持不变（变量名 max_board_count→board_times）
- 语义等价（>=3板 top5），换成实时数据

### 验证
- morning_check.py py_compile
- grep 确认 limit_up_analysis/morning_check 无 limit_up_tracking 方法残留
- 全项目 limit_up_tracking 仅剩 close_task.py 注释（217/396 行 docstring/注释，非执行代码，可保留或一并清）
