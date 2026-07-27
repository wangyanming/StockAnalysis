# ReAct复盘改版 — 技术方案 v1.0

**日期:** 2026-07-27
**状态:** 待审核
**需求文档:** docs/requirements/REQ-20260727-01-ReAct复盘改版.md (v1.1)

---

## 1. 修改文件清单

| # | 文件 | 改动类型 | 改动内容 |
|---|------|---------|---------|
| 1 | `core/analyzer/pick_react.py` | 修改 | `_fill_next_day_change()` 收益率公式修复 |
| 2 | `core/analyzer/daily_pick_v2.py` | 修改 | `_save_picks_to_db()` 补充写入 `score_pos` |
| 3 | `core/analyzer/pick_react.py` | 修改 | 新增 `build_react_report()` 函数（结构化数据） |
| 4 | `core/analyzer/close_task.py` | 修改 | `close_main()` 调用 `build_react_report()`，传给模版 |
| 5 | `core/reporter/close_report_tpl.py` | 修改 | 新增 `render_react_section()` 渲染结构化 react_report |
| 6 | `core/analyzer/close_task.py` | 删除 | 移除 `_build_react_data()` 旧版兼容逻辑 |

---

## 2. 数据流

```
收盘选股(daily_pick_v2.pick_stocks_v2)
  │ 调用 scorer.score_candidate() → 产出6维度评分(含score_pos)
  │
  ▼
入库(daily_pick_v2._save_picks_to_db)
  │ 补充写入 score_pos
  │
  ▼
盘后补全(pick_react.update_feedback → _fill_next_day_change)
  │ 按新公式计算：((T+2收盘) - (T+1开盘)) / (T+1开盘) × 100%
  │ 写入 daily_picks.next_day_change / next_open / next_close
  │
  ▼
同步到 observe_log (_sync_observe_log)
  │ is_pick=1且total_score≥60（本轮移除is_pick=1限制）
  │
  ▼
复盘渲染
  1. build_react_report() 从 observe_log 查询20天数据
  2. 生成结构化 dict（概览 + 维度归因 + B/C/D分组 + Act建议）
  3. close_report_tpl.render_react_section() 渲染为文本
  4. 追加到复盘报告第4段
```

---

## 3. 关键SQL

### 3.1 获取20个有效选股日的 trade_date

```sql
SELECT DISTINCT trade_date FROM observe_log
WHERE trade_date < %s AND next_day_change IS NOT NULL
ORDER BY trade_date DESC LIMIT 20
```

### 3.2 查询20天内的所有评分+收益率数据

```sql
SELECT trade_date, code, name, total_score, grade, next_day_change,
       score_chip, score_money, score_sector, score_trend, score_market, score_pos
FROM daily_picks
WHERE total_score >= 60
  AND trade_date IN (<20个日期占位符>)
  AND next_day_change IS NOT NULL
ORDER BY trade_date DESC, total_score DESC
```

说明：不再加 `is_pick=1` 限制。

### 3.3 维度归因分析（全量数据取回Python分段）

一次性取回数据后在Python中按区间分段统计，不做 GROUP BY 分段SQL（分段逻辑在Python中更灵活）。伪代码：

```python
def _dimension_analysis(rows, dim_field, thresholds):
    """如 dim_field='score_chip', thresholds=[(15,25,'高分'), (5,14,'中分'), (0,4,'低分')]"""
    groups = {label: {'count':0, 'wins':0, 'returns':[]} for _,_,label in thresholds}
    for r in rows:
        val = r[dim_field] or 0
        for lo, hi, label in thresholds:
            if lo <= val <= hi:
                groups[label]['count'] += 1
                if r['next_day_change'] > 0:
                    groups[label]['wins'] += 1
                groups[label]['returns'].append(r['next_day_change'])
                break
    # 统计结果
```

### 3.4 B/C/D分组统计

```python
def _group_stats(rows):
    groups = {'B': [], 'C': [], 'D': []}
    for r in rows:
        s = r['total_score'] or 0
        if 60 <= s < 65: groups['B'].append(r)
        elif 65 <= s < 70: groups['C'].append(r)
        elif s >= 70: groups['D'].append(r)
    
    result = []
    for label, items in groups.items():
        if not items:
            continue
        wins = sum(1 for r in items if r['next_day_change'] > 0)
        result.append({
            'label': f'{label}组(60~64)' if label == 'B' else (f'{label}组(65~69)' if label == 'C' else f'{label}组(≥70)'),
            'count': len(items),
            'wins': wins,
            'win_rate': round(wins/len(items)*100, 1),
            'avg_return': round(sum(r['next_day_change'] for r in items)/len(items), 2),
        })
    return result
```

### 3.5 日K线查询（用于回填 next_day_change/score_pos）

```sql
-- 找到每个 daily_picks 记录对应的 T+1 开盘价和 T+2 收盘价
SELECT dp.id AS pick_id, dp.code, dp.trade_date,
       t1.open AS t1_open,
       t2.close AS t2_close
FROM daily_picks dp
LEFT JOIN stock_daily t1 ON t1.code = dp.code AND t1.trade_date = (
    SELECT MIN(trade_date) FROM stock_daily WHERE code = dp.code AND trade_date > dp.trade_date
)
LEFT JOIN stock_daily t2 ON t2.code = dp.code AND t2.trade_date = (
    SELECT MIN(trade_date) FROM stock_daily WHERE code = dp.code AND trade_date > (
        SELECT MIN(trade_date) FROM stock_daily WHERE code = dp.code AND trade_date > dp.trade_date
    )
)
WHERE dp.next_day_change IS NOT NULL  -- 已有值的也要重算
```

---

## 4. 回填策略

### 4.1 next_day_change 回填

```python
def batch_fix_next_day_change(check_date: str = None):
    """
    全量回填 daily_picks 的收益率数据。
    对所有 next_day_change IS NOT NULL 的记录重算。
    
    步骤:
    1. 查出所有有 stock_daily 记录的选股数据 (trade_date >= 2026-01-01)
    2. 逐条：
       a. 查 T+1 开盘价
       b. 查 T+2 收盘价
       c. UPDATE daily_picks SET next_day_change, next_open, next_close
    3. 清空 observe_log 中对应的记录，重新同步
    """
    db = get_db()
    
    # 分页分批处理，每批500条
    offset = 0
    batch_size = 500
    while True:
        rows = db.fetchall(f'''
            SELECT dp.id, dp.code, dp.trade_date
            FROM daily_picks dp
            WHERE dp.trade_date >= '20260101'
              AND dp.next_day_change IS NOT NULL
            ORDER BY dp.id
            LIMIT {batch_size} OFFSET {offset}
        ''')
        if not rows:
            break
        offset += len(rows)
        
        for r in rows:
            code = r['code']
            pick_date = r['trade_date']
            
            # T+1 开盘价
            t1 = db.fetchone('''
                SELECT open, trade_date FROM stock_daily
                WHERE code=%s AND trade_date > %s AND trade_date <= %s AND open > 0
                ORDER BY trade_date ASC LIMIT 1
            ''', (code, pick_date, check_date or datetime.now().strftime('%Y%m%d')))
            if not t1:
                continue
            
            # T+2 收盘价
            t2 = db.fetchone('''
                SELECT close FROM stock_daily
                WHERE code=%s AND trade_date > %s AND close > 0
                ORDER BY trade_date ASC LIMIT 1
            ''', (code, t1['trade_date']))
            if not t2:
                continue
            
            change = (t2['close'] - t1['open']) / t1['open'] * 100
            
            db.execute('''
                UPDATE daily_picks
                SET next_day_change=%s, next_open=%s, next_close=%s
                WHERE id=%s
            ''', (round(change, 2), t1['open'], t2['close'], r['id']))
        
        db.commit()
        logger.info(f'已回填 {offset} 条')
    
    # 回填完成后，重建 observe_log
    db.execute('DELETE FROM observe_log WHERE 1=1')
    _sync_observe_log(db, check_date or datetime.now().strftime('%Y%m%d'))
```

### 4.2 score_pos 回填

```python
def batch_fix_score_pos():
    """
    补写 daily_picks 中 score_pos IS NULL 的记录。
    
    逻辑：
    1. 从 daily_picks 查 trade_date + code
    2. 从 stock_daily 取20日区间，调用 scorer 的 _score_position_in_range 逻辑
    3. UPDATE daily_picks SET score_pos = xxx
    """
    db = get_db()
    
    rows = db.fetchall('''
        SELECT id, code, trade_date FROM daily_picks
        WHERE score_pos IS NULL AND trade_date >= '20260101'
        ORDER BY id
    ''')
    
    updated = 0
    for r in rows:
        # 取 trade_date 当日及前20日
        row = db.fetchone('''
            SELECT MIN(low) as min_l, MAX(high) as max_h,
                   (SELECT close FROM stock_daily WHERE code=%s AND trade_date=%s) as current_close
            FROM stock_daily
            WHERE code=%s AND trade_date <= %s
              AND trade_date >= DATE_FORMAT(DATE_SUB(STR_TO_DATE(%s,'%%Y%%m%%d'), INTERVAL 20 DAY), '%%Y%%m%%d')
        ''', (r['code'], r['trade_date'], r['code'], r['trade_date'], r['trade_date']))
        
        if not row or not row['max_h'] or not row['min_l'] or not row['current_close']:
            continue
        if row['max_h'] <= row['min_l']:
            continue
        
        pos_pct = (row['current_close'] - row['min_l']) / (row['max_h'] - row['min_l']) * 100
        
        if pos_pct < 30:
            pos_score = 15
        elif pos_pct < 60:
            pos_score = 8
        elif pos_pct < 85:
            pos_score = 3
        else:
            pos_score = 0
        
        db.execute('UPDATE daily_picks SET score_pos=%s WHERE id=%s', (pos_score, r['id']))
        updated += 1
        if updated % 100 == 0:
            logger.info(f'已回填位置评分 {updated}/{len(rows)} 条')
    
    db.commit()
    logger.info(f'位置评分回填完成: {updated}/{len(rows)} 条')
```

---

## 5. 风险点与回滚

### 5.1 风险点

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| next_day_change 全量回填时，当天已有正确的值被覆盖 | 数据一致性问题 | 回填脚本加 dry-run 模式，先查不写 |
| observe_log 清空后重建失败 | 当日晚间复盘数据缺失 | 备份表：`CREATE TABLE observe_log_bak_20260727 AS SELECT * FROM observe_log` |
| score_pos 回填依赖 stock_daily 历史数据完整性 | 早期数据可能无法计算 | 跳过数据不足的记录，`WHERE count(20日内K线) >= 5` |
| `_sync_observe_log` 当前只同步 `is_pick=1`，但新需求去掉此限制 | observe_log 中缺少非精选数据 | 同步逻辑改为同步 total_score≥60 的全部数据 |

### 5.2 回滚方案

```bash
# 1. 回滚 next_day_change
# 如果跑错了，从备份恢复 observe_log
mysql -u root -p stock_analysis < backup_observe_log.sql

# 或者重新跑回填脚本（幂等，可以反复跑）

# 2. 代码回滚
git revert <commit-hash>
```

---

## 6. 验收方式

### 6.1 前置修复验收

```bash
# 验证 next_day_change 公式正确
python3 -c "
from core.analyzer.close_task import _load_yesterday_picks
picks = _load_yesterday_picks('20260727')  # T=20260727, 实际取daily_picks中最新一天的数据
for p in picks[:3]:
    print(f'{p[\"code\"]} {p[\"name\"]} 收益率={p[\"change_pct\"]:+.2f}%')
"

# 验证 score_pos 写入
python3 -c "
from utils.dao import get_db
db = get_db()
rows = db.fetchall('SELECT code, trade_date, score_pos FROM daily_picks WHERE score_pos IS NOT NULL LIMIT 5')
for r in rows:
    print(f'{r[\"code\"]} {r[\"trade_date\"]} score_pos={r[\"score_pos\"]}')
"
```

### 6.2 核心改造验收

```bash
# 验证新 run_react_analysis 输出结构化 dict
python3 -c "
from core.analyzer.pick_react import build_react_report
report = build_react_report('20260727')
print('window_start:', report['window_start'])
print('summary:', report['summary'])
print('group_stats:', report['group_stats'])
print('dimensions count:', len(report['dimension_analysis']))
"
```

### 6.3 渲染验收

```bash
# 验证完整复盘报告能正常生成
python3 -c "
from core.analyzer.close_task import daily_close_task
result = daily_close_task()
print('报告长度:', len(result))
print('包含新ReAct段落:', '近20日滚动统计' in result)
"
```

---

## 7. 执行顺序（给 RDAgent）

1. **第1步**：`pick_react.py` 的 `_fill_next_day_change()` 修公式
2. **第2步**：`daily_pick_v2.py` 的 `_save_picks_to_db()` 补 `score_pos`
3. **第3步**：运行回填脚本 `batch_fix_next_day_change()` + `batch_fix_score_pos()`
4. **第4步**：修 `_sync_observe_log()` 去掉 `is_pick=1` 限制，改为 total_score≥60
5. **第5步**：在 `pick_react.py` 中新增 `build_react_report(check_date)` 函数
6. **第6步**：在 `close_report_tpl.py` 中新增 `render_react_section(data)` 函数
7. **第7步**：改 `close_task.py` 调用侧
8. **第8步**：移除 `_build_react_data()` 旧版逻辑
9. **第9步**：自测并跑 `check_engineering.sh`
