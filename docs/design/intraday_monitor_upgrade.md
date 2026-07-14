# 盘中监控模版升级方案

## 1. 现状分析

### 现有代码结构

`core/reporter/intraday_monitor.py` 当前使用**新浪数据源**获取指数行情和个股行情，涨跌家数/成交额部分存在以下情况：

| 指标 | 现状 | 问题 |
|------|------|------|
| 指数涨跌 | ✅ 有（新浪） | 正常工作 |
| 涨跌家数 | ⚠️ 代码有 `fetch_realtime_market_summary()` 但跑不通 | `_get_market_summary_cached()` 依赖 `get_market_summary()` 从 `sector_performance` DB 兜底，但当日盘中无数据则回退到昨日 |
| 成交额 | ⚠️ 同上，从 `sector_performance` 取昨日数据 | 非实时数据 |
| 主力/散户资金 | ❌ 不存在 | 完全空缺 |
| 新闻摘要 | ✅ 有 | 正常工作 |
| 持仓监控 | ✅ 有 | 正常工作 |
| 止损三问 | ✅ 已整合 | 正常工作 |

### 现有3段+1段输出结构

```
📢 **盘中监控** — 2026-06-16 10:30

1️⃣ 大盘概况
  🟢 上证 +0.08% / 🟢 深证 +0.98% / 🟢 创业板 +1.50% / 🟡 科创50 -0.30%
  ⚠️ 风险提示行（按跌幅条件触发）

  （涨跌家数 + 成交额 占位空白）

2️⃣ 今日市场动态
  📰 ...

3️⃣ 持仓监控
  🟢 持股A(...)  现价xx  盈亏...

4️⃣ 操作提醒
  📌 个股提醒（涨停、止盈、止损三问）
  ⏰ 距收盘还有...
```

## 2. 升级目标

### 2.1 新增数据源：东方财富 push2 实时接口

```http
GET http://push2.eastmoney.com/api/qt/ulist.np/get
     ?fltt=2
     &fields=f2,f3,f4,f6,f12,f14,f104,f105,f106,f62,f66,f69,f72,f75,f78,f81,f84,f87
     &secids=1.000001,0.399001
     &cb=j
```

**字段含义（针对指数级别）：**

| 字段 | 含义 | 单位 | 两市处理 |
|------|------|------|---------|
| f12 | 证券代码 | 字符串 | 000001=上证, 399001=深证 |
| f2 | 现价/点数 | 点数 | 各自显示 |
| f3 | 涨跌幅 | % | 各自显示 |
| f6 | 成交额 | 元 | **合并** = 上证.f6 + 深证.f6 |
| f104 | 上涨家数 | 家 | **合并** = 上证.f104 + 深证.f104 |
| f105 | 下跌家数 | 家 | **合并** = 上证.f105 + 深证.f105 |
| f106 | 平盘家数 | 家 | **合并** = 上证.f106 + 深证.f106 |
| f62 | 主力净流入 | 元 | **合并** = 上证.f62 + 深证.f62 |
| f84 | 小单(散户)净流入 | 元 | **合并** = 上证.f84 + 深证.f84 |

> 注意：主力资金 = 超大单+大单；散户(小单) ≈ 反向。主力+散户之和理论上约为0（含中单误差）。

### 2.2 新增5个实时指标

1. **实时成交额**（元 → 亿）
2. **实时上涨家数**
3. **实时下跌家数**
4. **实时主力净流入**（元 → 亿）
5. **实时散户(小单)净流入**（元 → 亿）

## 3. 设计方案

### 3.1 新增数据获取函数

在 `core/reporter/intraday_monitor.py` 中新增一个独立的数据获取函数，抽取到一个新类或模块函数层级，与现有风格一致。

```python
def fetch_push2_market_data() -> dict:
    """
    通过东方财富 push2 ulist.np 接口获取两市实时市场数据。
    
    返回格式（所有数值统一处理妥当）：
    {
        'rise': int,          # 上涨家数（两市合计）
        'fall': int,          # 下跌家数（两市合计）
        'flat': int,          # 平盘家数（两市合计）
        'amount_yi': float,   # 成交额（亿）
        'main_flow_yi': float,   # 主力净流入（亿）
        'retail_flow_yi': float, # 散户/小单净流入（亿）
        'sh_index': {          # 上证指数详情
            'price': float, 'change_pct': float
        },
        'sz_index': {          # 深证指数详情
            'price': float, 'change_pct': float
        },
    }
    
    异常处理：
    - 接口超时/网络异常 → 抛出异常或返回 None
    - JSON 解析失败 → 返回 None
    - 单边交易所数据缺失（如只有上证没有深证）→ 有数据的那边继续使用，缺失边按0处理
    """
```

**核心实现逻辑：**

```python
import json, urllib.request
from utils.logger import setup_logger

logger = setup_logger("intraday_monitor")

# 缓存（30s）
_push2_cache = None
_push2_cache_time = 0

def fetch_push2_market_data() -> dict:
    global _push2_cache, _push2_cache_time
    now = time.time()
    if _push2_cache is not None and now - _push2_cache_time < 30:
        return _push2_cache

    url = (
        "http://push2.eastmoney.com/api/qt/ulist.np/get"
        "?fltt=2"
        "&fields=f2,f3,f4,f6,f12,f14,f104,f105,f106,f62,f66,f69,f72,f75,f78,f81,f84,f87"
        "&secids=1.000001,0.399001"
        "&cb=j"
    )
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://quote.eastmoney.com/',
        })
        resp = urllib.request.urlopen(req, timeout=5)
        raw = resp.read().decode('utf-8')
        # 去除 cb=j( 前缀和 ); 后缀
        json_str = raw[3:].rstrip(';').rstrip(')')
        data = json.loads(json_str)
        items = data.get('data', {}).get('diff', [])

        sh = {}
        sz = {}
        for item in items:
            code = item.get('f12', '')
            if code == '000001':
                sh = item
            elif code == '399001':
                sz = item

        # 合并计算（带 None 保护）
        result = {
            'rise': (sh.get('f104') or 0) + (sz.get('f104') or 0),
            'fall': (sh.get('f105') or 0) + (sz.get('f105') or 0),
            'flat': (sh.get('f106') or 0) + (sz.get('f106') or 0),
            'amount_yi': ((sh.get('f6') or 0) + (sz.get('f6') or 0)) / 1e8,
            'main_flow_yi': ((sh.get('f62') or 0) + (sz.get('f62') or 0)) / 1e8,
            'retail_flow_yi': ((sh.get('f84') or 0) + (sz.get('f84') or 0)) / 1e8,
            'sh_index': {
                'price': sh.get('f2'),
                'change_pct': sh.get('f3'),
            },
            'sz_index': {
                'price': sz.get('f2'),
                'change_pct': sz.get('f3'),
            },
        }
        _push2_cache = result
        _push2_cache_time = now
        logger.info(f'push2实时数据: 涨{result["rise"]}跌{result["fall"]} '
                     f'成交{result["amount_yi"]:.0f}亿 '
                     f'主力{result["main_flow_yi"]:+.0f}亿 '
                     f'散户{result["retail_flow_yi"]:+.0f}亿')
        return result

    except Exception as e:
        logger.warning(f'push2实时行情获取失败: {e}')
        return None
```

### 3.2 模版格式最终定稿

**保持4段结构不变**，仅强化第1段（大盘概况）：

```
📢 **盘中监控** — 2026-06-16 11:25

**1️⃣ 大盘概况**
  🟢 上证 +0.08% / 🟢 深证 +0.98% / 🟢 创业板 +1.50% / 🟡 科创50 -0.30%
  📊 🔴 跌3047家 🟢 涨2126家 ➖99家
  💰 成交额 15446亿 ｜ 🔴 主力 -77亿 ｜ 🟢 散户 +83亿
  ⚠️ 风险提示（按条件触发）

**2️⃣ 今日市场动态**
  📰 财联社6月16日电，央行开展...
  🟢 半导体板块持续走强...
  ...

**3️⃣ 持仓监控**
  🟢 东方财富(300059)  现价25.67  +1.38%  盈亏+2.10% ...
  💰 总资产: 123,456 | 成本: 100,000 | 盈亏: +23,456 (+23.46%)

**4️⃣ 操作提醒**
  📌 个股提醒：
    🚀 天齐锂业(002466) 涨停...
    ⚠️ 宁德时代(300750) 亏损-3.50%，接近止损线
      ...
  ⏰ 距收盘还有 3小时35分
```

**核心格式规则：**

#### 第1段第2行：涨跌家数行

```
📊 🔴 跌3047家 🟢 涨2126家 ➖99家
```

- 规则：**跌的多时跌在前**（大盘普跌场景），**涨的多时涨在前**（普涨场景）
- 判断逻辑：`rise >= fall` → `🟢 涨N家 🔴 跌N家`（涨在前）；否则 `🔴 跌N家 🟢 涨N家`（跌在前）
- 平盘家数固定尾缀 `➖N家`
- 所有数值为两市合并值

#### 第1段第3行：成交额+资金流

```
💰 成交额 15446亿 ｜ 🟢 主力 +...亿 ｜ 🔴 散户 +83亿
```

- 成交额取整显示（不保留小数）：`{amount_yi:.0f}亿`
- **主力 emoji**：净流入≥0 → 🟢，净流入<0 → 🔴
- **散户 emoji**：净流入>0 → 🔴（散户买多=主力大概率流出/筹码分散），净流入≤0 → 🟢（散户在卖=主力可能在吸筹）
  - 注意：散户为正默认标红，因为散户追涨通常是不利信号
  - 为负时标绿，说明散户恐慌卖出，反而可能是机会
- 金额格式化：正数 `+77亿`，负数 `-77亿`，零 `0亿`
- 如果金额过大（如主力流入 > 500亿），可在风险提示行补充说明

#### 第1段第4行：风险提示

从当前逻辑继续沿用，但新增资金流触发条件：

| 条件 | 提示内容 |
|------|---------|
| 上证跌超-1.5% | ⚠️ 大盘跌超1.5%，风险警示！建议只卖不买 |
| 上证跌超-1% | ⚠️ 上证跌超1%，注意风控 |
| 创业板跌超-1.5% | ⚠️ 创业板跌超1.5%，题材股风险偏大 |
| 主力净流出 > 500亿 | ⚠️ 主力流出{流出}亿，注意系统性风险 |
| 涨跌比 < 0.5 | ⚠️ 涨跌比严重失衡（涨:跌 ≈ {ratio}），市场弱势 |

**条件可以叠加**，每行一个风险提示。

### 3.3 与现有模版的融合方式

**融合策略：增量改造，不破坏现有逻辑。**

具体的代码修改步骤（**不用改代码，仅设计**）：

#### 修改点1：`run()` 函数中的第1段输出

将原来第1段中 `fetch_index('000001')` 和 `fetch_index('399001')` 的指数行保留，但**只保留上证和深证**。创业板和科创50仍然通过 `fetch_index` 获取并追加在同一行。

修改后的代码逻辑（伪代码）：

```python
# 1️⃣ 大盘概况
push2 = fetch_push2_market_data()

# ── 指数行 ──
idx_parts = []
if push2:
    sh_chg = push2['sh_index']['change_pct']
    sz_chg = push2['sz_index']['change_pct']
    sh_icon = '🟢' if sh_chg >= 0 else '🔴'
    sz_icon = '🟢' if sz_chg >= 0 else '🔴'
    idx_parts.append(f'{sh_icon} 上证 {sh_chg:+.2f}%')
    idx_parts.append(f'{sz_icon} 深证 {sz_chg:+.2f}%')
else:
    # 回退到新浪接口
    for code, name in [('000001', '上证'), ('399001', '深证')]:
        try:
            idx = fetch_index(code)
            icon = '🟢' if idx['change_pct'] >= 0 else '🔴'
            idx_parts.append(f'{icon} {name} {idx["change_pct"]:+.2f}%')
        except:
            idx_parts.append(f'⚠️ {name}')

# 创业板和科创50走新浪（push2 无此接口）
for code, name in [('399006', '创业板'), ('000688', '科创50')]:
    try:
        idx = fetch_index(code)
        icon = '🟢' if idx['change_pct'] >= 0 else '🔴'
        idx_parts.append(f'{icon} {name} {idx["change_pct"]:+.2f}%')
    except:
        idx_parts.append(f'⚠️ {name}')

lines.append(f'  {" / ".join(idx_parts)}')
```

#### 修改点2：新增涨跌家数+成交额+资金流行

```python
# ── 盘面摘要（新增） ──
if push2:
    rise, fall, flat = push2['rise'], push2['fall'], push2['flat']
    amt, main_flow, retail_flow = push2['amount_yi'], push2['main_flow_yi'], push2['retail_flow_yi']

    # 涨跌家数行
    if rise >= fall:
        rf_line = f'🟢 涨{rise}家 🔴 跌{fall}家 ➖{flat}家'
    else:
        rf_line = f'🔴 跌{fall}家 🟢 涨{rise}家 ➖{flat}家'
    lines.append(f'  📊 {rf_line}')

    # 资金流行
    main_icon = '🟢' if main_flow >= 0 else '🔴'
    retail_icon = '🔴' if retail_flow > 0 else '🟢'
    main_str = f'+{main_flow:.0f}亿' if main_flow >= 0 else f'{main_flow:.0f}亿'
    retail_str = f'+{retail_flow:.0f}亿' if retail_flow >= 0 else f'{retail_flow:.0f}亿'
    lines.append(f'  💰 成交额 {amt:.0f}亿 ｜ {main_icon} 主力 {main_str} ｜ {retail_icon} 散户 {retail_str}')

    # 更新风险提示（新增资金流条件）
    if main_flow < -500:
        risks.append(f'⚠️ 主力流出{main_flow:.0f}亿，注意系统性风险')
    if rise > 0 and fall > 0 and rise / fall < 0.5:
        risks.append(f'⚠️ 涨跌比严重失衡（涨:{fall}→跌:{rise}），市场弱势')
```

#### 修改点3：删除旧的涨跌家数相关函数

可保留但不再调用的函数（或标记deprecated）：
- `fetch_realtime_market_summary()` — 不再使用
- `_get_market_summary_cached()` — 不再使用  
- `fetch_amount_total_realtime()` — 不再使用

这些函数保留不影响运行，但为保持代码整洁，建议用注释标注 `# TODO: 2026-06-16 deprecated by fetch_push2_market_data`。

#### 修改点4：缓存策略

| 数据 | 缓存时长 | 说明 |
|------|---------|------|
| push2 指数+盘面 | 30s | 30s内同一调用返回缓存 |
| 新浪个股行情 | 无缓存 | 每轮run()只调一次 |
| 新闻 | 无缓存 | 实时获取 |

### 3.4 异常回退策略

| 故障场景 | 策略 | 对用户输出影响 |
|----------|------|--------------|
| **push2 接口完全不可用** | 新浪接口获取上证/深证指数行；**跳过盘面摘要**（涨跌家数/成交额/资金流全部留空或显示"数据获取中"） | 缺少第2行（涨跌家数）和第3行（资金流） |
| **push2 仅返回上证，深证缺失** | 上证数据正常使用，深证按0处理 | 涨跌家数/成交额/资金流仅包含上证，在日志中告警 |
| **push2 返回但部分字段为None** | `or 0` 保护，安全合并 | 数据可能偏低（如主力为0），带日志告警 |
| **push2 和 新浪都挂了** | 大盘概况中保留标题行（`1️⃣ 大盘概况`），指数显示为`⚠️ 上证 / ⚠️ 深证` | 大盘概况基本不可用，但2-3-4段不受影响 |

**代码层面的回退实现：**

```python
push2 = fetch_push2_market_data()
if push2 is None:
    logger.warning('push2接口失败，降级使用新浪')
    # 指数行：降级到新浪
    # 涨跌家数/资金流行：跳过输出
    # 在日志中记录，不影响其他段
```

### 3.5 完整输出对比

**升级前**（当前输出）：
```
📢 **盘中监控** — 2026-06-16 11:25

**1️⃣ 大盘概况**
  🟢 上证 +0.08% / 🔴 深证 +0.98% / 🟢 创业板 +1.50% / 🟡 科创50 -0.30%
  ⚠️ 上证跌超1%，注意风控

**2️⃣ 今日市场动态**
  ...

**3️⃣ 持仓监控**
  ...

**4️⃣ 操作提醒**
  ...
```

**升级后**（新输出）：
```
📢 **盘中监控** — 2026-06-16 11:25

**1️⃣ 大盘概况**
  🟢 上证 +0.08% / 🔴 深证 +0.98% / 🟢 创业板 +1.50% / 🟡 科创50 -0.30%
  📊 🔴 跌3047家 🟢 涨2126家 ➖99家
  💰 成交额 15446亿 ｜ 🔴 主力 -77亿 ｜ 🟢 散户 +83亿
  ⚠️ 主力流出77亿，注意系统性风险

**2️⃣ 今日市场动态**
  ...

**3️⃣ 持仓监控**
  ...

**4️⃣ 操作提醒**
  ...
```

### 3.6 测试要点

| 测试项 | 预期结果 | 测试方法 |
|--------|---------|---------|
| push2 接口正常 | 所有5个指标正确显示 | 盘中 run() |
| push2 接口超时 | 降级到新浪，跳过盘面摘要行 | 阻断 push2 或 mock |
| 盘中盘后 | 09:25前/15:00后跳过 | 同现有逻辑 |
| 午间休市 | 显示当前位置，持仓仍可查看 | 11:30-13:00 |
| 涨多跌少 | 家数行显示 `🟢 涨N家 🔴 跌N家` | 等待普涨日 |
| 跌多涨少 | 家数行显示 `🔴 跌N家 🟢 涨N家` | 等待普跌日 |
| 主力大幅流出 | 风险提示行新增主力流出警告 | 大盘下跌日 |
| 零成交/数据异常 | `or 0` 保护，日志告警 | mock 异常返回 |

## 4. 总结

| 维度 | 方案 |
|------|------|
| 数据源 | 东财 push2 `ulist.np/get` + 新浪指数(创业板/科创50) + 新浪个股行情 |
| 新增函数 | `fetch_push2_market_data()`，30s缓存 |
| 段数变化 | 4段不变，仅增强第1段 |
| 第1段行数 | 2-3行 → 3-4行（新增盘面摘要1-2行） |
| 异常回退 | push2失败 → 降级新浪(仅指数行) → 跳过盘面摘要 |
| 代码修改量 | 约+50行新函数 + 约+30行run()改造 + 移除旧函数调用 |
| 不兼容变更 | 无，增量改造，旧函数保留但标记deprecated |
