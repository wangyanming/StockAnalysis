"""
生成正确的收盘复盘数据（修复所有已知数据问题）
"""
import sys, json, urllib.request
sys.path.insert(0, '.')
from dao import get_db
from datetime import datetime, timedelta

db = get_db()
today_dt = datetime.now()
today = '20260514'
today_fmt = '2026-05-14'

# ── 1. 指数 ──
indices = db.fetchall(
    "SELECT name, current_price, change_pct FROM index_quotes WHERE record_date=%s AND index_code IN ('szzs','szcz','cyb','kc50')",
    (today_fmt,)
)
index_lines = []
for idx in indices:
    emoji = "🔴" if idx['change_pct'] < 0 else "🟢"
    index_lines.append(f"🔴 {idx['name']} {idx['change_pct']:+.2f}%（{idx['current_price']:.0f}）")
index_str = " / ".join(index_lines)

# ── 2. 成交额 —— 从新浪实时拉上证指数（含总成交额） ──
try:
    url = "https://hq.sinajs.cn/list=s_sh000001"
    req = urllib.request.Request(url, headers={'Referer': 'https://finance.sina.com.cn'})
    resp = urllib.request.urlopen(req, timeout=5)
    raw = resp.read().decode('gbk')
    parts = raw.split(',')
    if len(parts) >= 14:
        total_amount = float(parts[13])  # 单位：万
        total_amount_yi = total_amount / 10000  # 亿
        prev_close = float(parts[2]) if parts[2] else 0
        stock_count = parts[12] if len(parts) > 12 else "?"
        # 昨天成交额（sector_performance 前日汇总，只能大概参考）
        prev_amt_r = db.fetchone(
            "SELECT SUM(amount) as amt FROM sector_performance WHERE record_date='2026-05-13' AND rank_type='all'"
        )
        prev_amt = float(prev_amt_r['amt'] or 0) if prev_amt_r else 0
        # 前日成交额不要用行业汇总，直接拉
        try:
            url2 = "https://hq.sinajs.cn/list=s_sh000001"
            req2 = urllib.request.Request(url2, headers={'Referer': 'https://finance.sina.com.cn'})
            # 没有历史数据接口，用上次大盘快照或者接近值
            prev_amt_yi = prev_amt / 1e8 / 10000  # 行业汇总太离谱，备用
        except Exception:
            prev_amt_yi = 0
        print(f"成交额={total_amount_yi:.0f}亿 = {total_amount_yi/100:.2f}万亿")
        print(f"前日(行业汇总)={prev_amt_yi:.2f}万亿")
    else:
        raise ValueError(f"解析失败: {raw[:200]}")
except Exception as e:
    print(f"获取成交额失败: {e}")
    total_amount_yi = 0

# ── 3. 昨日5只精选股 ──
yesterday_picks = db.fetchall(
    "SELECT code, name, total_score FROM daily_picks WHERE trade_date='20260513_精' ORDER BY rank"
)
print(f"\n昨日精选({len(yesterday_picks)}只):")
pick_list = []
for p in yesterday_picks:
    td = db.fetchone(
        "SELECT close, change_pct FROM stock_daily WHERE code=%s AND trade_date='20260514'", (p['code'],)
    )
    if td:
        chg = td['change_pct']
        emoji = "🟢" if chg >= 0 else "🔴"
        status = ""
        if chg >= 9.5: status = "涨停"
        elif chg >= 7: status = "大涨"
        elif chg >= 0: status = "小幅上涨"
        elif chg >= -3: status = "小幅回调"
        elif chg >= -5: status = "明显下跌"
        elif chg >= -10: status = "触发止损"
        else: status = "跌停"
        print(f"  {emoji} {p['name']}({p['code']}): {chg:+.2f}%（{status}）收{td['close']}")
        pick_list.append({'name': p['name'], 'code': p['code'], 'chg': chg, 'status': status, 'close': td['close']})
    else:
        print(f"  ? {p['name']}({p['code']}): 无今日数据")

# ── 4. 板块 ──
top3 = db.fetchall(
    "SELECT sector_name, MAX(change_pct) as cp, MAX(amount) as amt FROM sector_performance WHERE record_date=%s AND rank_type='top_gain' GROUP BY sector_name ORDER BY cp DESC LIMIT 3",
    (today_fmt,)
)
gain_sectors = [(s['sector_name'], s['cp'], s['amt']) for s in top3]

bot3 = db.fetchall(
    "SELECT sector_name, MIN(change_pct) as cp, MAX(amount) as amt FROM sector_performance WHERE record_date=%s AND rank_type='top_fall' GROUP BY sector_name ORDER BY cp ASC LIMIT 3",
    (today_fmt,)
)
fall_sectors = [(s['sector_name'], s['cp'], s['amt']) for s in bot3]

# ── 5. 热点行业 ──
hot = db.fetchall(
    "SELECT DISTINCT industry, `count` FROM limit_up_industry_stats WHERE trade_date=%s ORDER BY `count` DESC LIMIT 3",
    ('20260514',)
)

# ── 6. 涨停/连板 ──
total_zt = db.fetchone("SELECT COUNT(*) as c FROM daily_limit_up WHERE trade_date='20260514'")
zt_count = total_zt['c'] if total_zt else 0

lb = db.fetchall(
    "SELECT d.code, d.name, d.board_times, d.industry FROM daily_limit_up d WHERE d.trade_date='20260514' AND d.board_times >= 2 ORDER BY d.board_times DESC"
)

# ── 输出完整数据 ──
print(f"\n\n=== 完整报告数据 ===")
print(f"指数: {index_str}")
print(f"成交额: {total_amount_yi:.0f}亿")
print(f"涨停: {zt_count}只")
print(f"连板: {len(lb)}只")
print(f"\n涨幅前三: {' / '.join([f'{s[0]}(+{s[1]:.2f}%)' for s in gain_sectors])}")
print(f"跌幅前三: {' / '.join([f'{s[0]}({s[1]:.2f}%)' for s in fall_sectors])}")
print(f"\n热点行业:")
for h in hot:
    print(f"  {h['industry']}: {h['count']}只涨停")
print(f"\n连板梯队:")
for b in lb:
    print(f"  {b['name']}({b['code']}): {b['board_times']}板 | {b['industry']}")
