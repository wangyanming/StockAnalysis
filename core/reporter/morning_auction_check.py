#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
09:26 集合竞价检查
数据源：
  新浪实时行情(9:25后): 开盘价=元, volume(竞价量)=手(x100->股)
  买卖盘口: 委买/委卖价=元, 挂单量=手(x100->股)
"""

import sys, os, json, logging, re
import urllib.request
# 确保项目根目录在 sys.path + 日志落盘
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _project_root)
os.chdir(_project_root)
from datetime import datetime, timedelta

from utils.logger import setup_logger
logger = setup_logger("morning_auction_check")


def fetch_realtime_quote(code: str):
    """
    拉取个股实时行情（新浪）
    9:25之后返回包含今日开盘价的数据
    返回: dict(name, open, prev_close, current, high, low, volume_hand, amount) 或 None
    """
    market = 'sz' if code.startswith('00') or code.startswith('30') or code.startswith('399') else 'sh'
    url = f'https://hq.sinajs.cn/list={market}{code}'
    req = urllib.request.Request(url, headers={
        'Referer': 'https://finance.sina.com.cn',
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
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
        'bid1_vol': int(parts[10]) if parts[10] else 0,
        'bid1_price': float(parts[11]) if parts[11] else 0,
        'ask1_vol': int(parts[12]) if parts[12] else 0,
        'ask1_price': float(parts[13]) if parts[13] else 0,
    }


def fetch_index(code: str):
    """获取指数实时行情"""
    prefix = 'sh' if code.startswith('000') else 'sz'
    url = f'https://hq.sinajs.cn/list=s_{prefix}{code}'
    req = urllib.request.Request(url, headers={
        'Referer': 'https://finance.sina.com.cn',
        'User-Agent': 'Mozilla/5.0',
    })
    resp = urllib.request.urlopen(req, timeout=5)
    data = resp.read().decode('gbk')
    parts = data.split('"')[1].split(',')
    return {
        'name': parts[0],
        'price': float(parts[1]) if parts[1] else 0,
        'change_pct': float(parts[3]) if parts[3] else 0,
    }


def analyze_auction(q: dict, code: str, score: int) -> dict:
    """
    分析集合竞价结果
    """
    result = {
        'code': code,
        'name': q['name'],
        'score': score,
        'prev_close': q['prev_close'],
        'open': q['open'],
        'current': q['current'],
        'change_pct': 0,
        'open_change_pct': 0,
        'volume_ratio_est': 0,
        'verdict': '',
        'advice': '',
        'level': '',
    }

    if q['prev_close'] == 0:
        result['verdict'] = '⚠️ 数据异常，昨收为0'
        result['level'] = 'warning'
        return result

    # 开盘涨幅
    open_chg = (q['open'] - q['prev_close']) / q['prev_close'] * 100
    result['open_change_pct'] = round(open_chg, 2)

    # 当前涨幅
    cur_chg = (q['current'] - q['prev_close']) / q['prev_close'] * 100
    result['change_pct'] = round(cur_chg, 2)

    # 估计竞价量比 —— 用开盘量/近期日均量粗略估算
    # 9:26时 volume_hand 就是竞价成交量(手)
    # 用简化的方法：竞价成交量越大越好
    # 这里volume_hand是竞价撮合量（9:25分产生的量）
    result['volume_hand'] = q['volume_hand']

    # 判断成交额是否异常大
    # 竞价成交额(元) = volume(股) × 均价 ≈ 开盘价 × volume_hand × 100
    if q['volume_hand'] > 0:
        est_amount = q['open'] * q['volume_hand'] * 100
        result['est_bid_amount'] = round(est_amount / 10000, 0)  # 万元

    # ── 综合判断 ──
    level = ''
    advice = ''
    verdict_parts = []

    # 1) 高开 vs 低开
    if open_chg >= 5:
        verdict_parts.append(f'🚀 大幅高开+{open_chg:.2f}%')
        level = 'strong'
    elif open_chg >= 2:
        verdict_parts.append(f'🟢 高开+{open_chg:.2f}%')
        level = 'good'
    elif open_chg > 0:
        verdict_parts.append(f'🔵 微幅高开+{open_chg:.2f}%')
        level = 'neutral'
    elif open_chg > -2:
        verdict_parts.append(f'🟡 低开{open_chg:.2f}%')
        level = 'weak'
    else:
        verdict_parts.append(f'🔴 大幅低开{open_chg:.2f}%')
        level = 'bad'

    # 2) 竞价成交量 / 换手预估
    vol_hand = q['volume_hand']
    if vol_hand >= 50000:
        verdict_parts.append(f'💹 竞价巨量{vol_hand/10000:.0f}万手')
        if level in ('strong', 'good'):
            level = 'strong'  # 高开+巨量=最强信号
    elif vol_hand >= 10000:
        verdict_parts.append(f'📊 竞价放量{vol_hand/10000:.1f}万手')
    elif vol_hand >= 2000:
        verdict_parts.append(f'📈 竞价温和{vol_hand/1000:.0f}千手')
    else:
        verdict_parts.append(f'📉 竞价缩量{vol_hand/1000:.0f}千手')
        if level in ('weak', 'bad'):
            level = 'bad'  # 低开+缩量=最弱

    # 3) 卖一/买一挂单判断（盘中时来看撤单/封板意愿）
    # 9:26集合竞价结束，买卖一为连续竞价的开始挂单
    bid1_v = q.get('bid1_vol', 0)
    ask1_v = q.get('ask1_vol', 0)
    if bid1_v > 0 or ask1_v > 0:
        ratio = (bid1_v - ask1_v) / (bid1_v + ask1_v + 1) * 100
        if ratio > 30:
            verdict_parts.append('🛡️ 买盘旺盛')
        elif ratio < -30:
            verdict_parts.append('⚠️ 卖压较重')

    # ── 操作建议 ──
    if level == 'strong':
        advice = '✅ **建议参与**：大幅高开+放量，强势信号，开盘后回踩分时均线可介入'
    elif level == 'good':
        advice = '✅ **可关注**：高开放量，走势健康，观察开盘后能否站稳开盘价不破'
    elif level == 'neutral':
        advice = '⏸️ **观望**：开盘波动不大，等10分钟后方向明确再决定'
    elif level == 'weak':
        advice = '⚠️ **谨慎**：低开，先观察是否有资金承接拉升，若持续走弱放弃'
    elif level == 'bad':
        advice = '❌ **放弃**：低开缩量，弱势明显，不符合买入条件'

    result['level'] = level
    result['verdict'] = ' | '.join(verdict_parts)
    result['advice'] = advice

    return result


def run():
    now = datetime.now()
    lines = []
    date_str = now.strftime('%Y-%m-%d')

    lines.append(f'🔔 **集合竞价检查** — {date_str} 09:26')
    lines.append('')

    # ── 0. 大盘竞价情况 ──
    lines.append('**📊 大盘竞价概览**')
    idx_config = [('000001', '上证'), ('399001', '深证'), ('399006', '创业板'), ('000688', '科创50')]
    idx_parts = []
    for code, name in idx_config:
        try:
            idx = fetch_index(code)
            arrow = '🟢' if idx['change_pct'] >= 0 else '🔴'
            idx_parts.append(f'{arrow} {name} {idx["change_pct"]:+.2f}%')
        except Exception:
            idx_parts.append(f'⚠️ {name}')
    lines.append(f'  {" / ".join(idx_parts)}')

    # 大盘风险提示
    try:
        sh = fetch_index('000001')
        if sh['change_pct'] < -1.0:
            lines.append('  ⚠️ **大盘大幅低开>1%，风险较高，建议暂停买入操作！**')
        elif sh['change_pct'] < -0.5:
            lines.append('  🟡 大盘低开0.5%+，需警惕，控制仓位')
        elif sh['change_pct'] > 0.5:
            lines.append('  🟢 大盘高开，环境偏暖')
        elif sh['change_pct'] > 1.0:
            lines.append('  🚀 大盘高开>1%，环境强势')
    except Exception:
        pass
    lines.append('')

    # ── 1. 读取昨日选股候选 ──
    from utils.dao import get_db
    db = get_db()

    # 最近3个有选股记录的交易日（近3日精选）
    cur = db.execute(
        "SELECT DISTINCT trade_date FROM daily_picks WHERE total_score>=60 ORDER BY trade_date DESC LIMIT 3")
    date_rows = cur.fetchall()
    cur.close()
    if not date_rows:
        lines.append('⚠️ 没有找到选股记录，请确认 `daily_picks` 表已有数据')
        lines.append('')
        print('\n'.join(lines))
        return

    # 打标签
    trade_dates = [r['trade_date'] for r in date_rows]
    date_labels = {}
    for td in trade_dates:
        try:
            dt = datetime.strptime(td, '%Y%m%d')
            date_labels[td] = f'{dt.month}/{dt.day}'
        except Exception:
            date_labels[td] = td

    # 取近3日精选，按trade_date从近到远扫描，去重保留首次出现
    all_picks = []
    seen_codes = set()
    for td in trade_dates:
        cur = db.execute(
            'SELECT code, name, total_score, highlights, data_tag, is_pick, `rank`, trade_date '
            'FROM daily_picks WHERE trade_date=%s AND total_score>=60 ORDER BY `rank`',
            (td,))
        for row in cur.fetchall():
            code = row['code'].strip()
            if code not in seen_codes:
                seen_codes.add(code)
                row['_date_label'] = date_labels[td]
                row['_is_today_pick'] = (td == trade_dates[0])
                all_picks.append(row)
        cur.close()

    if not all_picks:
        lines.append('⚠️ 近3日无≥60分的股票数据')
        lines.append('')
        print('\n'.join(lines))
        return

    lines.append(f'**📋 近3日≥60分监控（{len(trade_dates)}个交易日，{len(all_picks)}只，去重）：**')
    date_str_parts = []
    for td in trade_dates:
        date_str_parts.append(f'{date_labels[td]}({td[-2:]}日)')
    lines.append(f'  {", ".join(date_str_parts)}')
    lines.append('')

    # ── 2. 逐只采集竞价数据 ──
    results = []
    for p in all_picks:
        code = p['code'].strip()
        name = p['name']
        score = p['total_score']
        try:
            q = fetch_realtime_quote(code)
            if q:
                r = analyze_auction(q, code, score)
                r['highlights'] = p.get('highlights', '')
                r['data_tag'] = p.get('data_tag', '')
                r['_date_label'] = p.get('_date_label', '')
                r['_is_today_pick'] = p.get('_is_today_pick', False)
                r['rank'] = p.get('rank', 999)
                results.append(r)
            else:
                results.append({
                    'code': code, 'name': name, 'score': score,
                    'verdict': '⚠️ 行情数据获取失败', 'level': 'warning',
                    'advice': '跳过', 'open': 0, 'open_change_pct': 0,
                    '_date_label': p.get('_date_label', ''),
                    '_is_today_pick': p.get('_is_today_pick', False),
                    'rank': p.get('rank', 999),
                })
        except Exception as e:
            logger.warning(f'{code} {name} 采集失败: {e}')
            results.append({
                'code': code, 'name': name, 'score': score,
                'verdict': f'⚠️ 数据异常', 'level': 'warning',
                'advice': '跳过', 'open': 0, 'open_change_pct': 0,
                '_date_label': p.get('_date_label', ''),
                '_is_today_pick': p.get('_is_today_pick', False),
                'rank': p.get('rank', 999),
            })

    # ── 3. 输出 ──
    if not results:
        lines.append('  全部采集失败，请检查网络连接')
        lines.append('')
        print('\n'.join(lines))
        return

    # 排序：今日精选 > 昨日 > 前日，每组内按竞价强弱排序
    level_order = {'strong': 0, 'good': 1, 'neutral': 2, 'weak': 3, 'bad': 4, 'warning': 5}
    # 构造日期优先级映射：trade_dates[0]=今日, trade_dates[1]=昨日...
    date_priority = {}
    for idx, td in enumerate(trade_dates):
        date_priority[date_labels.get(td, td)] = idx
    results.sort(key=lambda r: (
        date_priority.get(r.get('_date_label', ''), 9),
        level_order.get(r['level'], 9),
        r.get('rank', 999)
    ))

    # 分批打印
    prev_date = None
    for i, r in enumerate(results, 1):
        # 日期分隔线
        cur_date = r.get('_date_label', '')
        if cur_date and cur_date != prev_date:
            if r.get('_is_today_pick'):
                lines.append(f'📅 **今日精选（{cur_date}）**')
            else:
                lines.append(f'📅 **{cur_date}精选**')
            prev_date = cur_date
        # 评分+股票名
        score_str = f'[{r["score"]}分]' if r['score'] else ''
        line = f'{i}. **{r["name"]}({r["code"]})** {score_str}'
        if r.get('_is_today_pick'):
            line += ' ⭐今日精选'
        lines.append(line)

        # 竞价详情
        if r['level'] not in ('warning'):
            lines.append(f'   开盘: {r["open"]:.2f}  昨收: {r["prev_close"]:.2f}  竞价量: {r["volume_hand"]/10000:.1f}万手')
            lines.append(f'   开盘涨幅: {r["open_change_pct"]:+.2f}%  |  当前涨幅: {r["change_pct"]:+.2f}%')
            if r.get('est_bid_amount'):
                lines.append(f'   竞价成交额: {r["est_bid_amount"]:.0f}万元')

        lines.append(f'   {r["verdict"]}')

        # 标签（数据标签/亮点）
        tag = r.get('data_tag', '')
        highlights = r.get('highlights', '')
        if tag:
            lines.append(f'   🏷️ {tag}')
        if highlights:
            lines.append(f'   💡 {highlights[:100]}')

        lines.append(f'   ➡️ {r["advice"]}')
        lines.append('')

    # ── 4. 汇总建议 ──
    strong_cnt = sum(1 for r in results if r['level'] in ('strong', 'good'))
    bad_cnt = sum(1 for r in results if r['level'] in ('weak', 'bad'))
    warning_cnt = sum(1 for r in results if r['level'] == 'warning')

    summary_parts = []
    if strong_cnt > 0:
        summary_parts.append(f'✅ 可参与: {strong_cnt}只')
    if bad_cnt > 0:
        summary_parts.append(f'❌ 建议放弃: {bad_cnt}只')
    if warning_cnt > 0:
        summary_parts.append(f'⚠️ 数据异常: {warning_cnt}只')

    lines.append('**📊 竞价汇总：**')
    if summary_parts:
        lines.append(f'  {" | ".join(summary_parts)}')
    else:
        lines.append('  全部观望等待')

    lines.append('')
    lines.append('⚙️ **纪律提醒：**')
    lines.append('  • 竞价表现只是参考，开盘后前5分钟方向更关键')
    lines.append('  • 单票仓位≤50%，大盘不好时减半仓操作')
    lines.append('  • 止损线-5%触发→盘中三问判断洗盘/真跌，详见 docs/交易纪律.md')
    lines.append('  • 大盘跌>1.5%暂停买入')
    lines.append('  • 大幅高开不下追，等回踩确认')
    lines.append('  • 低开>2%直接放弃，不要赌反包')

    print('\n'.join(lines))


if __name__ == '__main__':
    run()
