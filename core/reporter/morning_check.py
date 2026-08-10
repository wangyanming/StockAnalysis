#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
晨报生成模块（固定四段输出）
数据源：
  新浪实时行情: 价格=元, volume=手(x100->股), amount=元
  新浪指数: 点数/涨跌幅
  同花顺+财联社: 新闻文本
"""

import sys, os, json, logging, re
from datetime import datetime, timedelta

# 确保项目根目录在 sys.path 中，使得 from utils.dao 等导入可用
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from utils.logger import setup_logger
logger = setup_logger("morning_check")


# ============================================================
#  1️⃣ 昨日大盘概况
# ============================================================

def format_market_overview(sections: list):
    from utils.dao import get_db
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
    # 防御：用 db.fetchall 替代 db.execute().fetchall()，规避空结果返回空 tuple 的踩坑（见 v1.2 §1.5）
    seen_sectors = set()
    sector_items = []
    for s in db.fetchall(
        "SELECT sector_name, change_pct, rise_count, fall_count FROM sector_performance WHERE record_date=%s AND rank_type='all' ORDER BY change_pct DESC LIMIT 10",
        (snap_date,)):
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
    # 防御：用 db.fetchall 替代 db.execute().fetchall()，规避空结果返回空 tuple 的踩坑（见 v1.2 §1.5）
    high_rows = db.fetchall(
        "SELECT name, board_times FROM daily_limit_up WHERE trade_date=%s AND board_times>=3 ORDER BY board_times DESC, seal_first_time ASC LIMIT 5",
        (trade_date,))
    if high_rows:
        board_strs = [f"{r['name']}{r['board_times']}板" for r in high_rows]
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
        from core.fetcher.news_fetcher import _fetch_ths_news, _fetch_cls_news, _merge_news
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

    from utils.dao import get_db
    db = get_db()

    # ===== v1.2：盘前候选股取数口径修正（des-20260810 v1.2 §1.4）=====
    # 「上一个交易日」= 日历年历真实交易日（stock_daily 真实行情，周末/法定休市无数据）
    # 禁止用 daily_picks 的 MAX(trade_date)（那是「最近有选股记录日」，口径错误）
    # 禁止 WHERE ... AND total_score>=60 去取最近高分日（会回跳更早交易日）
    # 只取该真实交易日当天 total_score>=60；该日无 ≥60 → 空 list → 「今日无可关注候选股」
    # 绝不回跳补更早交易日；彻底弃用 is_pick=1 与 rank<=5 兜底分支
    cur_date = datetime.now().strftime('%Y%m%d')  # 查询基准日（今日/盘前日期），无横杠对齐 stock_daily.trade_date
    prev = db.fetchone(
        "SELECT MAX(trade_date) AS prev_date FROM stock_daily WHERE trade_date < %s",
        (cur_date,))
    if prev and prev.get('prev_date'):
        prev_date = prev['prev_date']  # 日历年历真实交易日（如周一盘前→上周五 20260807）
        try:
            dt = datetime.strptime(str(prev_date), '%Y%m%d')
            pick_label = f'{dt.month}月{dt.day}日'
        except Exception:
            pick_label = str(prev_date)
        # 只取该真实交易日当天 >=60 的个股；db.fetchall + list() 规避空 tuple 崩溃
        picks = list(db.fetchall(
            'SELECT code, name, total_score, data_tag FROM daily_picks '
            'WHERE trade_date=%s AND total_score>=60 '
            'ORDER BY total_score DESC',
            (str(prev_date),)))
        if picks:
            sections.append(f"📋 **今日关注（{len(picks)}只，{pick_label}）：**")
            for p in picks:
                tag = p.get('data_tag', '')
                tag_str = f' [{tag}]' if tag else ''
                sections.append(f"  {p['name']}({p['code']}) {p['total_score']}分{tag_str}")
        else:
            sections.append(f"📋 今日无可关注候选股")
    else:
        sections.append(f"📋 候选股数据待更新")

    sections.append("")
    sections.append("⚙️ **操作要点：**")
    sections.append("  • 大盘盘中走弱，关注下午反弹力度")
    sections.append("  • 特朗普访华期间关注相关板块脉冲")
    sections.append("  • 止损触发先做三问（缩量/板块红盘/时间早→不下车），详见 docs/交易纪律.md")
    sections.append("  • 大盘跌>1.5%暂停买入")
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
