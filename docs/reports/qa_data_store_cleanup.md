# QA 测试报告：data_store.py 清理

**测试时间：** 2026-07-17 17:10（首轮）
**修复时间：** 2026-07-17 17:17（补全 daily_pick.py / daily_pick_v2.py 的日期过滤）
**回归验证时间：** 2026-07-17 17:20
**测试人：** QA Agent
**改动范围：** `utils/data_store.py` 删除 + 5个调用方内联

## 改动摘要

`utils/data_store.py`（319行）被完整删除，其功能分散内联到5个调用方。原 `QuoteStore` 类中曾被使用的4个方法：
- `save_index_quote` → 内联到 `daily_fetch.py` 和 `web_server.py`
- `save_stock_quote` → 内联到 `web_server.py`
- `get_index_history` / `get_stock_history` → 内联到 `web_server.py`
- `get_sector_performances` → 替换为直接 `_get_db().fetchall()` 调用

未被使用的方法（`save_sector_performances`、`save_sector_performance`、`get_sector_performance`、`init_db`、`get_connection`、`_fix_sql`、`_execute`）随文件一并删除，无任何调用方受影响。

`_get_store()` 函数仅在 `stock_analysis_api.py` 中存在且未被任何调用方引用，已删除。

## 1. 检查项总览

| 检查项 | 首轮 (17:10) | 回归 (17:20) | 备注 |
|---|---|---|---|
| 文件删除 | ✅ | ✅ | `utils/data_store.py` 已从工作区和 git 索引中删除 |
| 引用清理 | ✅ | ✅ | 全项目 `grep` 未发现任何残留的 `data_store` / `QuoteStore` / `_get_store` 引用 |
| 代码审查 | ✅ | ✅ | 5个文件语法全部通过，无残留 import |
| 日期过滤修复 (daily_pick.py) | ⚠️ 缺失 | ✅ 修复 | SQL 补上了 `record_date = %s AND rank_type = %s`（见第2节） |
| 日期过滤修复 (daily_pick_v2.py) | ⚠️ 缺失 | ✅ 修复 | 同上 |
| 功能等价 | ⚠️ 见问题1 | ⚠️ 见问题1 | `save_stock_quote` 原版有 11列/12值的不匹配 bug；新版隐式修复 |
| 工程规范 | ⚠️ 预存问题 | ⚠️ 预存问题 | `check_engineering.sh` 失败仅为文档更新提醒 + data_reconciliation.py 缺失（均为预存问题） |
| 数据库验证 | ✅ | ✅ | 连接正常，核心表正常，语法有效 |
| 语法检查 | ✅ | ✅ | 5个改动文件 `ast.parse` 全部通过 |

## 2. 修复验证

### daily_pick.py 第60行附近 ✅

修复后 SQL:
```sql
"SELECT * FROM sector_performance WHERE record_date = %s AND rank_type = %s ORDER BY id"
```
params: `(datetime.now().strftime('%Y-%m-%d'), '涨幅')`

与原版 `QuoteStore.get_sector_performances(date_str=None, rank_type='涨幅')` 行为完全等价：
- `date_str=None` → 内部自动设为 `datetime.now().strftime('%Y-%m-%d')`
- `rank_type='涨幅'` → 作为第二个 `%s`

### daily_pick_v2.py 第94行附近 ✅

修复后 SQL 与 daily_pick.py 完全相同，params 也相同。行为等价验证通过。

## 3. 首次 QA 报告的3个问题逐一复核

### 问题1：daily_fetch.py 的 `_save_index_quote` 不返回 bool

**当前代码：** `_save_index_quote` 不返回任何值（返回 `None`），异常时只调用 `logger.error()` 不抛出。

**调用方 `fetch_all()` 第55行：**
```python
results['index'] = 'OK'
```
如果 `fetch_index_data` 返回 `None`，`_save_index_quote` 不会被调用，`results['index']` 始终为 `'OK'`（除非 `fetch_index_data` 抛出异常进入 `except` 块）。

**判断：❌ 不是真实问题。**

原 `QuoteStore.save_index_quote()` 返回 `True/False`，但 `daily_fetch.py` 从未检查该返回值。原代码也是通过 `try/exccpt` 设置 `results['index']` 的，新版本的异常处理路径相同（`except` 块中设置 `results['index'] = f'ERR: {e}'`）。行为完全等价。唯一变化：原版在 `save_index_quote` 执行失败时通过返回值判断 vs 新版靠外层 `try/except` 捕获——结果一致。

### 问题2：日期过滤缺失（已修复 ✅）

逐行对比确认：

**原版 `QuoteStore.get_sector_performances()`：**
```python
def get_sector_performances(self, date_str=None, rank_type=None):
    db = Database()
    if not date_str:
        date_str = datetime.now().strftime('%Y-%m-%d')
    if rank_type:
        rows = db.fetchall(
            "SELECT * FROM sector_performance WHERE record_date = %s AND rank_type = %s ORDER BY id",
            (date_str, rank_type)
        )
```

**调用：** `store.get_sector_performances(rank_type='涨幅')`
→ `date_str=None` → 自动设为今天 → SQL: `WHERE record_date = 今天 AND rank_type = '涨幅'`

**首次内联（已修复）：** `(datetime.now().strftime('%Y-%m-%d'), '涨幅')`
→ SQL: `WHERE record_date = %s AND rank_type = %s`

✅ **完全等价。** `record_date = %s` 与原版 `WHERE record_date = %s` 逐字一致，params 中的日期值 `datetime.now().strftime('%Y-%m-%d')` 与原版的行为一致。

**注意：** `rank_type='涨幅'` 在当前 `daily_fetch.py` 中已不再写入（当前写入 `rank_type='all'`），所以 `'涨幅'` 标签的数据是历史遗留。但既然内联行为与原版完全一致，该差异是数据层面而非代码层面的问题。

### 问题3：web_server.py 的 save_stock_quote 缺少 record_date 字段

**原版 `QuoteStore.save_stock_quote()` SQL：**
```sql
INSERT INTO stock_quotes 
(stock_code, name, current_price, change_pct, open, high, low, pre_close, volume, amount, timestamp)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
```
- **11列** 但 **12个值**（最后一个值为 `datetime.now().strftime('%Y-%m-%d')`，是 `record_date` 但列名中未声明）

**web_server.py 内联 SQL：**
```sql
INSERT INTO stock_quotes 
(stock_code, name, current_price, change_pct, open, high, low, pre_close, volume, amount, timestamp)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
```
- **11列** 对应 **11个值**

**当前 `stock_quotes` 表结构（MySQL）：**
```
id            int            auto_increment
stock_code    text
name          text
current_price double
change_pct    double
open          double
high          double
low           double
pre_close     double
volume        double
amount        double
timestamp     text
created_at    timestamp      DEFAULT_GENERATED
```
**无 `record_date` 列。**

**判断：⚠️ 原版有 bug，新版本是隐式修复。**

- 原版 `save_stock_quote` 的 VALUES 有 **12个** 占位符但只有 **11列**，在 MySQL `STRICT_TRANS_TABLES` 模式下，`INSERT` 会失败，抛出异常 → 返回 `False`
- 换句话说，原版 `QuoteStore.save_stock_quote()` 是**完全不可用的**——它永远会失败
- web_server.py 内联版本只有 11 个值（正确匹配 11 列），**实际可工作**
- 因此这不是一个"缺少 `record_date`"的问题，而是一个"原版代码有列/值数量不匹配 bug，内联版本无意中修复了它"的问题 ✅

**建议：** 这是一个正向修复（bug fix），无需额外操作。原版 bug 从未被观察到，可能是因为 `api_stock` 的 `try/except` 中 `pass` 了错误，或者此前很少被调用。

## 4. SQL 等价性核对（回归验证）

| 原方法 (QuoteStore) | 内联位置 | SQL 一致性 | 参数一致性 | 备注 |
|---|---|---|---|---|
| `save_index_quote` | `daily_fetch.py:45` | ✅ 逐字节 | ✅ | REPLACE INTO，有 record_date |
| `save_index_quote` | `web_server.py:170` | ✅ 逐字节 | ✅ | REPLACE INTO，有 record_date |
| `save_stock_quote` | `web_server.py:216` | ⚠️ 不同 | ⚠️ 不同 | **原版有 11列/12值 bug**；内联版正确，是隐式修复 |
| `get_index_history` | `web_server.py:270` | ✅ 逐字节 | ✅ | SELECT FROM index_quotes |
| `get_stock_history` | `web_server.py:279` | ✅ 逐字节 | ✅ | SELECT FROM stock_quotes |
| `get_sector_performances` | `daily_pick.py:60` | ✅ **修复后等价** | ✅ | `record_date = %s` 已补回 |
| `get_sector_performances` | `daily_pick_v2.py:94` | ✅ **修复后等价** | ✅ | 同上 |
| `_get_store()` | `stock_analysis_api.py` | 已删除 | N/A | 无调用方，安全 |
| `get_connection()` | daily_pick_v2.py import 已移除 | 返回 None | N/A | 无调用方，安全 |

## 5. 运行检查（回归验证）

| 检查项 | 结果 |
|---|---|
| `python3 ast.parse()` 语法检查（5个文件） | ✅ 全部通过 |
| `grep -rn 'data_store\|QuoteStore\|_get_store' --include='*.py'` | ✅ 全项目无残留引用 |
| `bash tests/preflight.sh` | ✅ 全部通过（语法/连接/核心表/导入） |
| `bash tests/check_engineering.sh` | ⚠️ 仅因 PROJECT_STATE.md 未更新 + data_reconciliation.py 缺失（均为预存问题，非本次改动引入） |

## 6. 最终结论

**总体判定：** ✅ **通过（回归验证）**

### 修复验证结果
- ✅ **问题2（日期过滤缺失）**：已在 daily_pick.py 第60行和 daily_pick_v2.py 第94行正确修复。SQL 补上了 `record_date = %s`，params 包含 `datetime.now().strftime('%Y-%m-%d')`，与原版行为完全等价。

### 其余问题复核结果
- **❌ 问题1（不返回 bool）**：不是真实问题。`fetch_all()` 不依赖返回值，异常路径一致。
- **⚠️ 问题3（缺少 record_date）**：原版代码有 11列/12值 的 bug（最后一个值 `record_date` 列未在 column list 中声明），在 `STRICT_TRANS_TABLES` 模式下 INSERT 永远失败。内联版隐式修复了这个 bug，是**正向的、意外的修复**。此外 `stock_quotes` 表也没有 `record_date` 列，所以原版本来就不可能插入该字段。

### 最终结论
`utils/data_store.py` 清理改动完整、正确。5个改动文件无语法错误、无残留 import。首次 QA 发现的两处日期过滤缺失已修复并回归验证通过。原版 `save_stock_quote` 中发现的列/值不匹配 bug 在内联版本中被隐式修复。全项目无 `data_store` 残留引用。

**建议跟进：**
1. 更新 `PROJECT_STATE.md` 记录本次清理和回归验证结果
2. 在 MEMORY.md 中记录 data_store.py 清理完成状态
3. 考虑在 `daily_pick.py` / `daily_pick_v2.py` 中决定是否继续保留 `rank_type='涨幅'` 查询路径（当前已无实时写入）
