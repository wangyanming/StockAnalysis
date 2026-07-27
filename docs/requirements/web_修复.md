# Web 页面问题修复需求

## 问题一：🏭 涨停行业分布无数据

### 根因
- `daily_fetch.py` 15:10 快照任务中，调用 `save_today_limit_up()` 后 **没有接着调用 `save_industry_stats()`**
- 而 `run_daily_analysis()` 虽然同时做了这两件事，但**实际无人调用它**（只有 web_server 的"手动刷新"API 才触发）
- 导致 `limit_up_industry_stats` 表自 2026-06-18 后无新数据

### 修复方案
在 `daily_fetch.py` 的涨停拉取逻辑中，`save_today_limit_up()` 成功后加一行 `zt.save_industry_stats()`：

```python
# 现有代码：
res = zt.save_today_limit_up()
limit_up_count = res.get('count', 0)
# 新增：
if limit_up_count > 0:
    try:
        zt.save_industry_stats()
    except Exception as e2:
        logger.warning(f"行业统计保存失败: {e2}")
```

### 另外
- `save_industry_stats()` 内部的 `df.groupby("所属行业")` 依赖的是 AKShare 原始返回的**中文列名**，但现在 `fetch_today_limit_up` 在新浪兜底模式下 `"所属行业"` 字段为空。这没问题——行业为空不会报错，只是行业统计数为0。

## 问题二：选股追踪「涨停接力/区间潜伏」无数据

### 根因
- `daily_picks` 表中 `data_tag` 字段**全为 NULL**（查看 20260721 共412条 `data_tag` 均为 None）
- 从2026-06-04的V3改造后，`daily_pick_v2.py` 还在跑但输出的是 `is_pick=0` 且 `data_tag=None` 的数据
- 前端 `api_picks` 按 tag 筛选（`data_tag = %s`）时，传入 "real"/"simulated"（前端映射的 key）与数据库里**无此值**匹配，所以查不到

### 修复方案
**方式A（推荐——还原V2分组逻辑）**：
1. 在 `daily_pick_v2.py` 的 `pick_stocks_v2()` 中，输出到 `daily_picks` 表时，给 `data_tag` 赋值：
   - `limitup` 组（涨停接力）= 原 `real` 分组结果 → `data_tag='limitup'`
   - `range` 组（区间潜伏）= 原 `simulated` 分组结果 → `data_tag='range'`
2. 前端 `web_server.py` `api_picks` 中 `tag_display_map` 已经写了 `"limitup": "涨停接力", "range": "区间潜伏"`，所以无需改前端
3. 给 `daily_pick_v2.py` 中插入数据的位置补 `data_tag` 字段

**方式B（简化——前端改为不过滤tag）**：
- 前端 `api_picks` 的 `tag == "all"` 分支已不过滤 tag，所以默认展示全部
- 问题只是当选择 "涨停接力" 或 "区间潜伏" 的 tab 时查不到
- 但方式A更规范，用V2的完整语义

## 问题三：入选日展示日期截断（60721 → 应为 2026-07-21）

### 根因
- 前端 `api_picks` 返回的 `trade_date` 字段就是 `"20260721"`，前端展示时没有格式化为 `YYYY-MM-DD`
- 需要在代码中找到展示 `trade_date` 的位置，加格式化逻辑

### 修复方案
在 web_server.py 的前端 HTML/JS 模板中，展示入选日的代码把 `pick.trade_date` 格式化为 `YYYY-MM-DD`，类似：
```js
function formatDate(dateStr) {
    return dateStr.slice(0,4) + '-' + dateStr.slice(4,6) + '-' + dateStr.slice(6,8);
}
```

## 问题四：表头增加可点击排序（评分、T+1~T+5）

### 修复方案
在 web_server.py 的前端模板中，为选股追踪结果表格的**表头**增加排序功能：
1. 表头 `<th>` 改为可点击
2. 点击后按对应列重新排序
3. 需要排序的字段：`total_score`（评分）、`tracking[0].change_pct`（T+1）、`tracking[1].change_pct`（T+2）… T+5
4. 用纯前端 JS 实现（数据已在内存中，不需要再请求后端）
5. 默认按 `total_score` 降序

### 排序实现思路
```js
// 在 picks 数组中增加计算字段
picks.forEach(p => {
    p._t1 = p.tracking[0]?.change_pct ?? null;
    p._t2 = p.tracking[1]?.change_pct ?? null;
    // ... T+3, T+4, T+5
});

// 排序函数
function sortBy(field, asc) {
    picks.sort((a, b) => {
        let va = a[field] ?? -Infinity;
        let vb = b[field] ?? -Infinity;
        return asc ? va - vb : vb - va;
    });
    renderPicks(); // 重新渲染表格
}
```

## 影响范围
| 文件 | 改动量 |
|------|--------|
| `core/fetcher/daily_fetch.py` | +5行（加 `save_industry_stats` 调用） |
| `core/analyzer/daily_pick_v2.py` | +? 行（补 `data_tag` 字段写入） |
| `web_server.py` | +50行左右（日期格式化 + 排序功能） |

## 验收标准
1. ✅ 打开涨停复盘页面，选择有涨停的日期，行业分布有数据
2. ✅ 选股追踪默认显示所有，选择"涨停接力"或"区间潜伏"能筛出对应的股
3. ✅ 入选日完整显示 `2026-07-21` 而非 `60721`
4. ✅ 点击评分/T+1~T+5 表头可以排序
