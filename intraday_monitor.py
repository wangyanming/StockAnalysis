#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
盘中监控模块（固定三段输出）
数据源：
  新浪实时行情: 价格=元, volume=手(x100->股), amount=元
  新浪指数: 点数/涨跌幅
"""

import sys, os, json, logging, re, time
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)
_market_summary_cache = None
_market_summary_cache_time = 0

def _get_market_summary_cached():
    """缓存实时市场汇总 — 一次性调用 StockDataFetcher.get_market_summary()"""
    global _market_summary_cache, _market_summary_cache_time
    now = time.time()
    if _market_summary_cache is not None and now - _market_summary_cache_time < 30:
        return _market_summary_cache
    from stock_analysis_api import StockDataFetcher
    f = StockDataFetcher()
    try:
        ms = f.get_market_summary()
        if ms and ms.get('up_count', 0) > 0:
            logger.info(f'盘中实时涨跌家数: 涨{ms["up_count"]} 跌{ms["down_count"]}')
            # 成交额实时拿不到时填补昨天数据
            if ms.get('total_amount', 0) == 0:
                from dao import get_db
                db = get_db()
                yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
                sp = db.fetchone("SELECT SUM(amount) as total_amt FROM sector_performance WHERE record_date=%s AND rank_type='all'", (yesterday,))
                if sp and sp['total_amt'] and sp['total_amt'] > 0:
                    ms['total_amount'] = float(sp['total_amt'])
            _market_summary_cache = ms
            _market_summary_cache_time = now
            return ms
    except Exception as e:
        logger.warning(f'实时市场汇总失败: {e}')
    return None


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(SCRIPT_DIR)
sys.path.insert(0, os.getcwd())


def fetch_index(idx_code: str):
    """获取指数实时行情（新浪）"""
    import urllib.request
    # 新浪指数编码: sh000001, sz399001, sz399006, sh000688
    prefix = 'sh' if idx_code.startswith('000') else 'sz'
    url = f'https://hq.sinajs.cn/list=s_{prefix}{idx_code}'
    req = urllib.request.Request(url, headers={
        'Referer': 'https://finance.sina.com.cn',
        'User-Agent': 'Mozilla/5.0',
    })
    resp = urllib.request.urlopen(req, timeout=5)
    data = resp.read().decode('gbk')
    # 新浪指数格式: name,current_price,change_amount,change_pct%,volume,amount
    parts = data.split('"')[1].split(',')
    name = parts[0]
    cur = float(parts[1]) if parts[1] else 0
    chg = float(parts[3]) if parts[3] else 0
    return {'name': name, 'price': cur, 'change_pct': chg}


def fetch_realtime_market_summary() -> dict:
    """盘中实时涨跌家数+成交额 — 优先实时API，回退至 sector_performance 兜底"""
    try:
        d = _get_market_summary_cached()
        if d and d.get('up_count', 0) > 0:
            return {
                'rise': d['up_count'],
                'fall': d['down_count'],
                'flat': d.get('flat_count', 0),
                'total_yi': d.get('total_amount', 0) / 1e8,
            }
    except Exception as e:
        logger.warning(f'实时市场汇总获取失败: {e}')
    # 回退：从 sector_performance 取昨日数据
    from dao import get_db
    db = get_db()
    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    sp = db.fetchone(
        "SELECT SUM(rise_count) as rise, SUM(fall_count) as fall, SUM(amount) as total_amt FROM sector_performance WHERE record_date=%s AND rank_type='all'",
        (yesterday,))
    if sp and sp['rise'] and sp['rise'] > 0:
        return {
            'rise': int(sp['rise']),
            'fall': int(sp['fall'] or 0),
            'flat': 0,
            'total_yi': float(sp['total_amt'] or 0) / 1e8,
        }
    return {'rise': '?', 'fall': '?', 'flat': 0, 'total_yi': 0}


def fetch_amount_total_realtime() -> dict:
    """成交额 — 复用 fetch_realtime_market_summary（含实时+回退）"""
    ms = fetch_realtime_market_summary()
    total_yi = ms.get('total_yi', 0)
    # 前日比较
    from dao import get_db
    db = get_db()
    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    sp = db.fetchone(
        "SELECT SUM(amount) as total_amt FROM sector_performance WHERE record_date=%s AND rank_type='all'",
        (yesterday,))
    prev_yi = float(sp['total_amt']) / 1e8 if sp and sp['total_amt'] else -1
    diff_yi = total_yi - prev_yi if prev_yi > 0 else None
    return {'total_yi': total_yi, 'yesterday_same_yi': prev_yi if prev_yi > 0 else 0, 'diff_yi': diff_yi}


def fetch_news_compact(max_items: int = 6) -> list:
    """获取精炼新闻（同花顺+财联社）"""
    lines = []
    try:
        from news_fetcher import _fetch_ths_news, _fetch_cls_news, _merge_news
        merged = _merge_news(_fetch_ths_news(), _fetch_cls_news(), 20)
        tags = [
            (r'特朗普|拜登|美国|会谈|关税|贸易|制裁|访华', '🇨🇳🇺🇸'),
            (r'涨停|拉升|走强|活跃|冲高|大涨', '🟢'),
            (r'跌|下挫|回落|翻绿|下跌|调整', '📉'),
            (r'AI|芯片|半导体|算力|机器人|人工智能', '🤖'),
            (r'新能源|光伏|风电|电池|储能|新能源车', '⚡'),
            (r'消费|零售|食品|电商|旅游', '🛒'),
            (r'公告|财报|业绩|分红|减持|回购', '📰'),
        ]
        seen = set()
        for item in merged:
            title = (item.get('title', '') or item.get('text', ''))[:80]
            title = re.sub(r'财联社\d+月\d+日电[，]*', '', title)
            title = title.replace('[', '').replace(']', '').strip()
            if len(title) < 10:
                continue
            dedup = re.sub(r'\d{2}:\d{2}\s*', '', title)[:30]
            if dedup in seen:
                continue
            seen.add(dedup)
            tag = ''
            for pat, emoji in tags:
                if re.search(pat, title):
                    tag = emoji
                    break
            lines.append(f'{tag} {title}' if tag else f'📰 {title}')
            if len(lines) >= max_items:
                break
    except Exception as e:
        logger.warning(f'新闻获取失败: {e}')
    return lines


def fetch_quote(code: str):
    """获取个股实时行情"""
    import urllib.request
    market = 'sz' if code.startswith('00') or code.startswith('30') else 'sh'
    url = f'https://hq.sinajs.cn/list={market}{code}'
    req = urllib.request.Request(url, headers={
        'Referer': 'https://finance.sina.com.cn',
        'User-Agent': 'Mozilla/5.0',
    })
    resp = urllib.request.urlopen(req, timeout=5)
    data = resp.read().decode('gbk')
    parts = data.split('"')[1].split(',')
    if len(parts) < 32:
        return None
    return {
        'name': parts[0],
        'open': float(parts[1]) if parts[1] else 0,
        'prev_close': float(parts[2]) if parts[2] else 0,
        'current': float(parts[3]) if parts[3] else 0,
        'high': float(parts[4]) if parts[4] else 0,
        'low': float(parts[5]) if parts[5] else 0,
        'volume_hand': int(parts[8]) if parts[8] else 0,
        'amount': float(parts[9]) if parts[9] else 0,
    }


def run():
    now = datetime.now()
    lines = []

    # ── 标题 ──
    lines.append(f'📢 **盘中监控** — {now.strftime("%Y-%m-%d %H:%M")}')
    lines.append('')

    # ════════════════════════════════════════
    # 1️⃣ 大盘概况
    # ════════════════════════════════════════
    lines.append('**1️⃣ 大盘概况**')

    # 实时指数一行合并
    idx_config = [
        ('000001', '上证'),
        ('399001', '深证'),
        ('399006', '创业板'),
        ('000688', '科创50'),
    ]
    idx_parts = []
    for code, name in idx_config:
        try:
            idx = fetch_index(code)
            arrow = '🟢' if idx['change_pct'] >= 0 else '🔴'
            idx_parts.append(f'{arrow} {name} {idx["change_pct"]:+.2f}%')
        except Exception:
            idx_parts.append(f'⚠️ {name}')
    lines.append(f'  {" / ".join(idx_parts)}')

    # 风险提示行（结合大盘涨跌）
    risks = []
    try:
        sh = fetch_index('000001')
        if sh['change_pct'] < -1.5:
            risks.append('⚠️ 大盘跌超1.5%，风险警示！建议只卖不买')
        elif sh['change_pct'] < -1:
            risks.append('⚠️ 上证跌超1%，注意风控')
        cy = fetch_index('399006')
        if cy['change_pct'] < -1.5:
            risks.append('⚠️ 创业板跌超1.5%，题材股风险偏大')
    except Exception:
        pass
    if risks:
        lines.append(f'  {"；".join(risks)}')

    # 涨跌家数 成交额
    lines.append('')

    # ════════════════════════════════════════
    # 2️⃣ 今日市场动态
    # ════════════════════════════════════════
    lines.append('')
    lines.append('**2️⃣ 今日市场动态**')
    news = fetch_news_compact(6)
    if news:
        for n in news:
            lines.append(f'  {n}')
    else:
        lines.append('  暂无盘中重要消息')
    lines.append('')

    # ════════════════════════════════════════
    # 3️⃣ 持仓监控
    # ════════════════════════════════════════
    lines.append('')
    lines.append('**3️⃣ 持仓监控**')

    total_cost = 0
    total_value = 0
    pos_rows = []

    from dao import get_db
    _db = get_db()
    positions = _db.fetchall("SELECT * FROM portfolio_positions")

    for pos in positions:
        code = pos['code']
        name = pos['name']
        cost = float(pos['cost_price'])
        shares = int(pos['shares'])
        if not code or not shares:
            continue
        q = fetch_quote(code)
        if q is None or q['prev_close'] <= 0:
            lines.append(f'  ⚠️ {name}({code}) — 行情获取失败')
            continue
        cur = q['current']
        change_pct = (cur - q['prev_close']) / q['prev_close'] * 100
        profit_pct = (cur - cost) / cost * 100 if cost > 0 else 0
        market_value = cur * shares
        amp = (q['high'] - q['low']) / q['prev_close'] * 100
        amount_str = f"{q['amount']/1e8:.1f}亿" if q['amount'] >= 1e8 else f"{q['amount']/1e4:.0f}万"

        flag = '🟢' if change_pct >= 0 else '🔴'
        pos_rows.append({
            'name': name, 'code': code, 'flag': flag,
            'price': f'{cur:.2f}',
            'change': f'{change_pct:+.2f}%',
            'pnl': f'{profit_pct:+.2f}%',
            'amp': f'{amp:.1f}%',
            'vol': amount_str,
        })
        total_cost += cost * shares
        total_value += market_value

    if pos_rows:
        for r in pos_rows:
            chg_str = r['change']
            lines.append(f"  {r['flag']} {r['name']}({r['code']})  现价{r['price']}  {chg_str}  盈亏{r['pnl']}  振幅{r['amp']}  量{r['vol']}")

        if total_cost > 0:
            total_pnl = total_value - total_cost
            total_pnl_pct = total_pnl / total_cost * 100
            lines.append(f'  💰 总资产: {total_value:,.0f} | 成本: {total_cost:,.0f} | 盈亏: {total_pnl:+,.0f} (+{total_pnl_pct:.2f}%)' if total_pnl >= 0 else f'  💰 总资产: {total_value:,.0f} | 成本: {total_cost:,.0f} | 盈亏: {total_pnl:+,.0f} ({total_pnl_pct:.2f}%)')
    else:
        lines.append('  无持仓记录')
    lines.append('')

    # ════════════════════════════════════════
    # 4️⃣ 操作提醒
    # ════════════════════════════════════════
    lines.append('')
    lines.append('**4️⃣ 操作提醒**')

    stock_tips = []
    _db2 = get_db()
    positions_for_tips = _db2.fetchall("SELECT * FROM portfolio_positions")
    for pos in positions_for_tips:
        code = pos['code']
        name = pos['name']
        cost = float(pos['cost_price'])
        shares = int(pos['shares'])
        q = fetch_quote(code)
        if q and q['prev_close'] > 0:
            cur = q['current']
            profit_pct = (cur - cost) / cost * 100
            day_pct = (cur - q['prev_close']) / q['prev_close'] * 100

            if day_pct >= 9.8:
                stock_tips.append(f'🚀 {name}({code}) 涨停 {cur:.2f}(+{day_pct:.2f}%)')
            elif profit_pct > 7:
                stock_tips.append(f'💰 {name}({code}) 盈利+{profit_pct:.2f}%，可以考虑止盈')
            elif profit_pct < -5:
                stock_tips.append(f'🚨 {name}({code}) 亏损{profit_pct:.2f}%，已触及止损线，建议卖出')
            elif profit_pct < -3:
                stock_tips.append(f'⚠️ {name}({code}) 亏损{profit_pct:.2f}%，接近-5%止损线')

    if stock_tips:
        lines.append('  📌 个股提醒：')
        for t in stock_tips:
            lines.append(f'    {t}')

    # 时间提醒
    h, m = now.hour, now.minute
    if h < 11 or (h == 11 and m < 30):
        lines.append('  ⏰ 午盘收盘 11:30')
    elif 11 <= h < 13:
        lines.append('  ⏰ 午间休市 (11:30-13:00)')
    elif 13 <= h < 15:
        lines.append(f'  ⏰ 距收盘还有 {14 - h}小时{60 - m:02d}分')
    lines.append('')

    return '\n'.join(lines)


if __name__ == '__main__':
    now = datetime.now()
    current_min = now.hour * 60 + now.minute
    if current_min < 9 * 60 + 25:
        print(f'⏰ 尚未开盘（09:25开始），跳过盘中监控')
        sys.exit(0)
    if current_min >= 15 * 60:
        print(f'⏰ 已收盘，跳过盘中监控')
        sys.exit(0)
    print(run())
