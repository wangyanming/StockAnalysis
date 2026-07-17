# 清理 data_store.py 方案

## 目标
删除 `utils/data_store.py`，将仍在使用的4个方法内联到各调用方，直接使用 `utils.dao.get_db()` 执行 SQL。

## 背景
- `data_store.py` 包装了 `QuoteStore` 类，但底层全调 `dao.get_db()` 跑 SQL
- `init_db()` 每次实例化都会跑废弃建表逻辑，吐出无意义 stderr
- 已有 `dao.py` 作为统一的数据库底层，不需要中间这层

## 改动范围 —— 5个文件

### 1. `core/fetcher/daily_fetch.py`
**现状：** 第41行 `store = QuoteStore()`，第52行 `store.save_index_quote(idx, data)`
**改法：** 删掉 `from utils.data_store import QuoteStore` 和 `store = QuoteStore()`，直接内联 `save_index_quote` 的 SQL：
```python
from utils.dao import get_db

def _save_index_quote(index_code, data):
    cur = get_db().execute(
        """REPLACE INTO index_quotes 
           (index_code, name, current_price, change_pct, open, high, low, volume, amount, timestamp, record_date)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            index_code,
            data.get("name", index_code),
            data.get("current_price", 0),
            data.get("change_pct", 0),
            data.get("open", 0),
            data.get("high", 0),
            data.get("low", 0),
            data.get("volume", 0),
            data.get("amount", 0),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            datetime.now().strftime("%Y-%m-%d")
        )
    )
    cur.close()
```
调用处改为 `_save_index_quote(idx, data)`。

### 2. `web_server.py`
**现状：** `store = QuoteStore()`，`store.save_index_quote(code, data)`，`store.save_stock_quote(secid, data)`，`store.get_index_history()`, `store.get_stock_history()`
**改法：** 同样内联。写两个辅助函数 `_save_index_quote()` 和 `_save_stock_quote()` 放在文件内。`get_index_history`/`get_stock_history` 直接改为 `dao` 查询。

### 3. `core/analyzer/daily_pick_v2.py`
**现状：** 第22行 `from utils.data_store import QuoteStore, get_connection`，第40行 `store = QuoteStore()`，第95行 `store.get_sector_performances(rank_type='涨幅')`
**改法：** 删掉 `QuoteStore, get_connection` 导入，直接查 `dao`：
```python
from utils.dao import get_db as _get_db
sectors = _get_db().fetchall(
    "SELECT * FROM sector_performance WHERE rank_type = %s ORDER BY id",
    ('涨幅',)
)
```
`get_connection` 一直返回 `None`，应该没人用 → 确认后删除。

### 4. `core/analyzer/daily_pick.py`
**现状：** `from utils.data_store import QuoteStore`，`store = QuoteStore()`，`store.get_sector_performances(rank_type='涨幅')`
**改法：** 同上，用 `dao.get_db().fetchall()` 替代。

### 5. `utils/stock_analysis_api.py`
**现状：** 第26-29行定义了 `_get_store()` 函数，返回 `QuoteStore()`，但**全项目没有一处调用这个函数**。
**改法：** 直接删除 `_get_store()` 函数。

### 6. 删除 `utils/data_store.py` 本身
**改法：** `rm utils/data_store.py`

## 验收标准
1. `utils/data_store.py` 不存在
2. 上述5个文件 import 路径全部正确，无引用残留
3. 所有功能等价：写入/查询行为不变
4. 运行 `bash tests/check_engineering.sh` 通过
5. web_server 启动不报错
6. `python3 core/analyzer/close_task.py` 运行无 `"建表跳过"` 相关 stderr

## 注意
- `daily_pick_v2.py:22` 还导入了 `get_connection`，要一并确认是否被用到（返回 None，实际就是空壳）
- `save_stock_quote` 里 SQL 有 `timestamp` 字段但 params 传了 `datetime.now()` 和 `datetime.today()` 两个值，实际只用了前11个参数。内联时注意保持这个行为（不修改逻辑，只移位置）
