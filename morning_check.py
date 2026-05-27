#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
盘前检查脚本 — 用于09:15定时任务推送飞书
输出格式主参考（2026-05-14 10:00手动版）：
  📢 盘前报告 — YYYY-MM-DD HH:MM
  
  1️⃣ 昨日大盘概况（5/13）
  🟢 上证 +0.67% / 深证 +1.67% / 创业板 +2.63% / 科创50 +2.69%
  📊 涨3036 / 跌2007 / 平159
  💰 成交额: 32645亿
  🚀 涨停15只
  🏭 热点: 电力(4只) / 家居用品(4只) / 食品加工(4只)
  🔗 连板: 大唐发电6板 | 蒙娜丽莎4板

  2️⃣ 昨夜海外市场
  🔴 道琼斯 49693 (-0.14%)
  🟢 纳斯达克 26402 (+1.20%)
  🟢 标普500 7444 (+0.58%)
  🟢 富时A50 16009 (-0.45% 今早)
  💱 离岸人民币 6.79

  3️⃣ 今日市场动态
  🇨🇳🇺🇸 特朗普抵达欢迎仪式现场，今日与习近平会谈
  ...

  4️⃣ 今日策略提醒
  📋 昨日候选（5月13日选）：
  工业富联(601138) 76分
  德明利(001309) 71分
  ...
  ⚙️ 操作要点：
  • 大盘盘中走弱，关注下午反弹力度
  ...
"""

import sys, os, json, logging, re
from datetime import datetime, timedelta

logging.basicConfig(level=logging.WARNING, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)


# ============================================================
#  1️⃣ 昨日大盘概况
# ============================================================

def format_market_overview(sections: list):
    from dao import get_db
    db = get_db()

    # 找到最近一个交易日（跳过周末/节假日）：查 sector_performance 最晚的 record_date
    last = db.fetchone(
        "SELECT record_date FROM sector_performance WHERE record_date < %s ORDER BY record_date DESC LIMIT 1",
        (datetime.now().strftime('%Y-%m-%d'),))
    if last and last['record_date']:
        snap_date = last['record_date']
        trade_date = datetime.strptime(snap_date, '%Y-%m-%d').strftime('%Y%m%d')
        date_label = datetime.strptime(snap_date, '%Y-%m-%d').strftime('%-m/%-d')
    else:
        yesterday = datetime.now() - timedelta(days=1)
        snap_date = yesterday.strftime('%Y-%m-%d')
        trade_date = yesterday.strftime('%Y%m%d')
        date_label = yesterday.strftime('%-m/%-d')

    # 1A. 指数 — 从 index_quotes 获取
    idx_parts = []
    code_map = [
        ('上证指数', 'szzs'), ('深证成指', 'szcz'),
        ('创业板指', 'cyb'), ('科创50', 'kc50'),
    ]
    for name, code in code_map:
        row = db.fetchone(
            "SELECT change_pct FROM index_quotes WHERE index_code=%s AND DATE(timestamp)=%s ORDER BY id DESC LIMIT 1",
            (code, snap_date))
        if row:
            chg = float(row['change_pct'])
            arrow = '🟢' if chg > 0 else '🔴'
            idx_parts.append(f"{arrow} {name} {chg:+.2f}%")
    
    if idx_parts:
        sections.append(f"**1️⃣ 昨日大盘概况（{date_label}）**")
        sections.append(f"  {' / '.join(idx_parts)}")
    else:
        sections.append(f"**1️⃣ 昨日大盘概况**")
        sections.append("  ⚠️ 无快照数据")

    # 1B. 涨跌家数 + 成交额 — 从 sector_performance 汇总（与收盘复盘数据源一致）
    sp = db.fetchone(
        "SELECT SUM(amount) as total_amt, SUM(rise_count) as rise, SUM(fall_count) as fall FROM sector_performance WHERE record_date=%s AND rank_type='all'",
        (snap_date,))
    if sp and sp['total_amt'] and sp['total_amt'] > 0:
        rise = int(sp['rise'] or 0)
        fall = int(sp['fall'] or 0)
        total_amt = float(sp['total_amt'])
        sections.append(f"  📊 涨{rise} / 跌{fall}")
        sections.append(f"  💰 成交额: {total_amt/1e8:.0f}亿")

    # 1C. 涨停
    cnt = db.fetchone(
        "SELECT COUNT(DISTINCT code) as cnt FROM daily_limit_up WHERE trade_date=%s AND (status IS NULL OR status != '跌停')", (trade_date,))
    total = cnt['cnt'] if cnt else '?'
    cnt_down = db.fetchone(
        "SELECT COUNT(DISTINCT code) as cnt FROM daily_limit_up WHERE trade_date=%s AND status='跌停'", (trade_date,))
    down_total = cnt_down['cnt'] if cnt_down else 0
    if down_total > 0:
        sections.append(f"  🚀 涨停{total}只 | 💀 跌停{down_total}只")
    else:
        sections.append(f"  🚀 涨停{total}只")

    # 热点 — 从 sector_performance 按板块取前3（不同名）
    seen_sectors = set()
    sector_items = []
    for s in db.execute(
        "SELECT sector_name, change_pct, rise_count, fall_count FROM sector_performance WHERE record_date=%s AND rank_type='all' ORDER BY change_pct DESC LIMIT 10",
        (snap_date,)).fetchall():
        nm = s['sector_name']
        if nm not in seen_sectors:
            seen_sectors.add(nm)
            rc = s.get('rise_count', 0) or 0
            sector_items.append(f"{nm}({rc}只)")
            if len(sector_items) >= 3:
                break
    if sector_items:
        sections.append(f"  🏭 热点: {' / '.join(sector_items)}")

    # 连板
    high_rows = db.execute(
        "SELECT name, max_board_count FROM limit_up_tracking WHERE latest_limit_date=%s AND max_board_count>=3 ORDER BY max_board_count DESC LIMIT 5",
        (trade_date,)).fetchall()
    if high_rows:
        board_strs = [f"{r['name']}{r['max_board_count']}板" for r in high_rows]
        sections.append(f"  🔗 连板: {' | '.join(board_strs)}")

    sections.append("")


# ============================================================
#  2️⃣ 海外市场
# ============================================================

def format_overseas(sections: list):
    sections.append("**2️⃣ 昨夜海外市场**")
    lines = []
    import requests

    try:
        # 美股
        r = requests.get(
            "https://hq.sinajs.cn/list=gb_$dji,gb_$ixic,gb_$inx",
            headers={"Referer": "https://finance.sina.com.cn"},
            timeout=8)
        r.encoding = 'gbk'
        for line in r.text.strip().split('\n'):
            parts = line.split(',')
            if len(parts) >= 4:
                raw_name = parts[0].split('"')[-1] if '"' in parts[0] else parts[0]
                nm = {'道琼斯': '道琼斯', '纳斯达克': '纳斯达克', '标准普尔500': '标普500', '标普500指数': '标普500'}.get(raw_name, raw_name)
                price = str(int(float(parts[1]))) if parts[1] else '?'
                chg_f = float(parts[2]) if parts[2] else 0
                arrow = '🟢' if chg_f > 0 else '🔴'
                lines.append(f"  {arrow} {nm} {price} ({chg_f:+.2f}%)")

        # 富时A50
        r2 = requests.get(
            "https://hq.sinajs.cn/list=hf_CHA50CFD",
            headers={"Referer": "https://finance.sina.com.cn"},
            timeout=8)
        r2.encoding = 'gbk'
        t2 = r2.text.strip()
        if t2:
            p2 = t2.split(',')
            if len(p2) >= 8:
                m = re.search(r'"([\d\.]+)', p2[0])
                pre_close = p2[7]
                if m and pre_close:
                    price = str(int(float(m.group(1))))
                    chg = (float(m.group(1)) - float(pre_close)) / float(pre_close) * 100
                    arrow = '🟢' if chg > 0 else '🔴'
                    lines.append(f"  {arrow} 富时A50 {price} ({chg:+.2f}% 今早)")

        # 离岸汇率
        r3 = requests.get(
            "https://hq.sinajs.cn/list=fx_susdcny",
            headers={"Referer": "https://finance.sina.com.cn"},
            timeout=8)
        r3.encoding = 'gbk'
        rm = re.search(r'(\d+\.\d+)', r3.text)
        if rm:
            cny = f"{float(rm.group(1)):.2f}"
            lines.append(f"  💱 离岸人民币 {cny}")

    except Exception as e:
        logger.warning(f"海外数据获取失败: {e}")
        lines.append("  ⚠️ 海外数据暂不可用")

    sections.extend(lines if lines else ["  ⚠️ 海外数据暂不可用"])
    sections.append("")


# ============================================================
#  3️⃣ 市场动态 — 同花顺+财联社 按标题摘要精炼输出
# ============================================================

def format_news_compact(sections: list):
    """3️⃣ 今日市场动态 — 精炼标注对盘面有影响的新闻"""
    sections.append("**3️⃣ 今日市场动态**")
    lines = []

    try:
        from news_fetcher import _fetch_ths_news, _fetch_cls_news, _merge_news
        merged = _merge_news(_fetch_ths_news(), _fetch_cls_news(), 20)

        # 对短标题加emoji标签
        tag_map = [
            (r'特朗普|拜登|美国|会谈|关税|贸易战|制裁|访华', '🇨🇳🇺🇸'),
            (r'涨停|拉升|走强|活跃|冲高|大涨|概念|板块', '🟢'),
            (r'跌|下挫|回落|翻绿|调整|下跌', '📉'),
            (r'期货|商品|原油|黄金|伦铜|铁矿|螺纹|有色', '🛢️'),
            (r'央行|利率|降息|加息|逆回购|流动性|MLF', '🏦'),
            (r'AI|芯片|半导体|算力|机器人|人工智能', '🤖'),
            (r'新能源|光伏|风电|电池|锂|储能|新能源车', '⚡'),
            (r'消费|零售|食品|电商|旅游', '🛒'),
            (r'公告|财报|业绩|分红|减持|回购', '📰'),
        ]
        seen = set()
        for item in merged:
            title = item.get('title', '')[:80] or item.get('text', '')[:60]
            # 去重（同花顺和财联社可能重复）
            dedup_key = re.sub(r'\d{2}:\d{2}\s*', '', title).strip()[:40]
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            # 清洗
            title = re.sub(r'财联社\d+月\d+日电[，]*', '', title)
            title = title.replace('[', '').replace(']', '').strip()
            if len(title) < 10:
                continue
            if '风口研报' in title or '研报' in title:
                continue

            # 打标签
            tag = ''
            for pat, emoji in tag_map:
                if re.search(pat, title):
                    tag = emoji
                    break

            lines.append(f"  {tag} {title[:80]}" if tag else f"  📰 {title[:80]}")

        if len(lines) > 10:
            lines = lines[:10]
    except Exception as e:
        logger.warning(f"新闻获取失败: {e}")
        lines.append("  ⚠️ 新闻获取失败")

    if not lines:
        lines.append("  暂无重大盘前消息")

    sections.extend(lines)
    sections.append("")


# ============================================================
#  4️⃣ 今日策略
# ============================================================

def format_today_picks(sections: list):
    sections.append("**4️⃣ 今日策略提醒**")

    from dao import get_db
    db = get_db()

    # 从 daily_picks 表获取最新交易日选股结果
    last_trade_dates = db.execute(
        "SELECT DISTINCT trade_date FROM daily_picks WHERE LENGTH(trade_date)=8 ORDER BY trade_date DESC LIMIT 1")
    row = last_trade_dates.fetchone()
    if row:
        trade_date = row['trade_date']
        try:
            dt = datetime.strptime(trade_date, '%Y%m%d')
            pick_label = f'{dt.month}月{dt.day}日'
        except:
            pick_label = trade_date

        # 取精选推荐（is_pick=1）= 涨停回踩TOP5 + 区间潜伏TOP5
        cur = db.execute(
            'SELECT code, name, total_score, data_tag FROM daily_picks WHERE trade_date=%s AND is_pick=1 ORDER BY `rank`',
            (trade_date,))
        picks = cur.fetchall()

        # 如果 is_pick 标记的部分不足5只，用 rank<=5 补（兜底）
        if len(picks) < 5:
            seen = set(p['code'] for p in picks)
            cur2 = db.execute(
                'SELECT code, name, total_score, data_tag FROM daily_picks WHERE trade_date=%s AND `rank`<=5 ORDER BY `rank`',
                (trade_date,))
            for r in cur2.fetchall():
                if r['code'] not in seen:
                    seen.add(r['code'])
                    picks.append(r)

        if picks:
            sections.append(f"📋 **今日关注（{len(picks)}只）：**")
            for p in picks:
                tag = p.get('data_tag', '')
                tag_str = f' [{tag}]' if tag else ''
                sections.append(f"  {p['name']}({p['code']}) {p['total_score']}分{tag_str}")
        else:
            sections.append(f"📋 候选股待更新")
    else:
        sections.append(f"📋 候选股数据待更新")

    sections.append("")
    sections.append("⚙️ **操作要点：**")
    sections.append("  • 大盘盘中走弱，关注下午反弹力度")
    sections.append("  • 特朗普访华期间关注相关板块脉冲")
    sections.append("  • 严格止损 -5%，大盘跌>1.5%暂停买入")
    sections.append("  • 连板高标需注意分歧风险")

# ============================================================
#  主函数
# ============================================================

def morning_check() -> str:
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    sections = []

    sections.append(f"📢 **盘前报告** — {now}")
    sections.append("")

    format_market_overview(sections)       # 1️⃣
    format_overseas(sections)               # 2️⃣
    format_news_compact(sections)           # 3️⃣
    format_today_picks(sections)            # 4️⃣

    return '\n'.join(sections)


if __name__ == '__main__':
    # logger.info("=== 盘前检查 ===")
    report = morning_check()
    print(report)
