"""
收盘定时任务 - 每天16:30自动复盘+选股推送

⚠️ 原则：数据层优先从数据库读取，候选换手/封板从外部API补全。
   架构：close_task.py(数据层) → close_report_tpl.py(模版层)
"""
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import logging
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

# DB 连接统一收敛到 utils/dao.py（按项目路径自动判定生产/开发库）

# 使用统一日志工具初始化 logger
from utils.logger import setup_logger, timing
logger = setup_logger("close_task", console=False)

# 关掉 root logger 的 INFO 日志（避免第三方库混入）
logging.getLogger().setLevel(logging.WARNING)

from utils.dao import get_db
_db = get_db()


def fmt_amount(amt: float) -> str:
    """格式化金额（元转亿元）"""
    if abs(amt) >= 1e8:
        return f"{amt/1e8:.1f}亿"
    elif abs(amt) >= 1e4:
        return f"{amt/1e4:.1f}万"
    return f"{amt:.0f}元"


def _fetch_sina_quote(codes: list) -> dict:
    """从新浪批量获取实时行情（换手率、封板时间等）"""
    import urllib.request
    import re
    if not codes:
        return {}
    
    # 转换 codes 为 sina 格式 (sh600001, sz000001)
    sina_codes = []
    code_map = {}  # sina_key -> original_code
    for c in codes:
        c = c.strip()
        if c.startswith('6') or c.startswith('9'):
            sc = f"sh{c}"
        else:
            sc = f"sz{c}"
        sina_codes.append(sc)
        code_map[sc] = c
    
    result = {}
    batch_size = 50
    for i in range(0, len(sina_codes), batch_size):
        batch = sina_codes[i:i+batch_size]
        url = f"http://hq.sinajs.cn/list={','.join(batch)}"
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://finance.sina.com.cn',
        })
        try:
            resp = urllib.request.urlopen(req, timeout=5)
            text = resp.read().decode('gbk')
            for line in text.strip().split('\n'):
                if not line.strip():
                    continue
                # var hq_str_sh600001="name,open,close,price,high,low,...
                m = re.search(r'var hq_str_(\w+)="(.+)"', line)
                if m:
                    skey = m.group(1)
                    fields = m.group(2).split(',')
                    if len(fields) >= 10:
                        orig_code = code_map.get(skey)
                        turnover_rate = fields[9] if len(fields) > 9 else ''
                        result[orig_code] = {
                            'turnover_rate': turnover_rate,
                        }
        except Exception as e:
            logger.warning(f"新浪行情请求失败: {e}")
    
    return result


def _fetch_tx_quote(codes: list) -> dict:
    """从腾讯获取实时行情作为备用"""
    import urllib.request
    import re
    if not codes:
        return {}
    
    tx_codes = []
    code_map = {}
    for c in codes:
        c = c.strip()
        if c.startswith('6') or c.startswith('9'):
            tc = f"sh{c}"
        else:
            tc = f"sz{c}"
        tx_codes.append(tc)
        code_map[tc] = c
    
    result = {}
    batch_size = 20
    for i in range(0, len(tx_codes), batch_size):
        batch = tx_codes[i:i+batch_size]
        url = f"http://qt.gtimg.cn/q={','.join(batch)}"
        try:
            resp = urllib.request.urlopen(url, timeout=5)
            text = resp.read().decode('gbk')
            for line in text.strip().split('\n'):
                if not line.strip():
                    continue
                # v_sh600001="1~name~code~price~...
                m = re.search(r'v_(\w+)="(.+)"', line)
                if m:
                    skey = m.group(1)
                    fields = m.group(2).split('~')
                    if len(fields) >= 40:
                        orig_code = code_map.get(skey)
                        turnover_rate = fields[9] if len(fields) > 9 else ''
                        result[orig_code] = {
                            'turnover_rate': turnover_rate,
                        }
        except Exception as e:
            logger.warning(f"腾讯行情请求失败: {e}")
    
    return result


def _get_quotes_for_candidates(candidate_stocks: list) -> dict:
    """获取候选股票的换手率及封板数据（直接使用腾讯行情，新浪接口不稳定已跳过）"""
    codes = [s['code'] for s in candidate_stocks if s.get('code')]
    if not codes:
        return {}
    
    # 直接使用腾讯行情（新浪接口不稳定已跳过）
    quotes = _fetch_tx_quote(codes)
    
    return quotes


def _log_timing(t_start: float, label: str) -> None:
    """分步计时 — 通过统一日志工具输出"""
    import time as _t
    elapsed = _t.time() - t_start
    logger.info(f'[TIMING] {label}: {elapsed:.1f}s')


def _load_index_quotes(trade_date: str) -> dict:
    """从 index_quotes 表读取今日指数行情"""
    today_dash = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"
    rows = _db.fetchall(
        "SELECT index_code, name, current_price, change_pct, amount, open, high, low "
        "FROM index_quotes WHERE record_date=%s", (today_dash,))
    
    index_map = {'000001': 'szzs', '399001': 'szcz', '399006': 'cyb', '000688': 'kc50'}
    indexes = {}
    for r in rows:
        code = r['index_code']
        alias = index_map.get(code, code)
        chg = float(r['change_pct']) if r['change_pct'] else 0
        price = float(r['current_price']) if r['current_price'] else 0
        indexes[alias] = {
            'code': code,
            'current_price': price,
            'change_pct': chg,
        }
    
    return indexes


def _load_yesterday_total_amount(trade_date: str) -> float:
    """查询昨日成交额（从 sector_performance 取，跳过周末和长假）"""
    today_dash = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"
    row = _db.fetchone(
        "SELECT SUM(amount) as amt FROM sector_performance WHERE record_date=(SELECT MAX(record_date) FROM sector_performance WHERE record_date < %s AND rank_type='all' AND amount > 0) AND rank_type='all'",
        (today_dash,))
    if row and row['amt'] and row['amt'] > 0:
        return float(row['amt'])
    return 0


def _load_sector_data(trade_date: str) -> dict:
    """从 sector_performance 读取板块排行（直接取 rank_type='all' 排序）"""
    today_dash = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"
    rows = _db.fetchall(
        "SELECT sector_name, change_pct, amount, net_inflow FROM sector_performance "
        "WHERE record_date=%s AND rank_type='all' ORDER BY change_pct DESC",
        (today_dash,))
    parsed = []
    for r in rows:
        parsed.append({
            'name': r['sector_name'],
            'change_pct': float(r['change_pct']) if r['change_pct'] else 0,
            'amount': float(r['amount']) if r.get('amount') else 0,
            'net_inflow': float(r['net_inflow']) if r.get('net_inflow') else 0,
        })
    if not parsed:
        return {'top_gain': [], 'top_fall': [], 'top_inflow': [], 'top_outflow': []}
    return {
        'top_gain': parsed[:10],
        'top_fall': sorted(parsed, key=lambda x: x['change_pct'])[:10],
        'top_inflow': sorted(parsed, key=lambda x: x['net_inflow'], reverse=True)[:5],
        'top_outflow': sorted(parsed, key=lambda x: x['net_inflow'])[:5],
    }


def _load_limit_up_data(trade_date: str) -> dict:
    """从 daily_limit_up + limit_up_tracking 读取涨停/跌停"""
    # 涨停
    zt_rows = _db.fetchall(
        "SELECT code, name, price, change_pct, turnover_rate, seal_first_time, seal_last_time, "
        "board_times, bomb_times, seal_fund, industry, concept, status "
        "FROM daily_limit_up WHERE trade_date=%s AND (status IS NULL OR status != '跌停')",
        (trade_date,))
    
    # 跌停
    dt_rows = _db.fetchall(
        "SELECT code, name, price, change_pct, seal_fund, board_times "
        "FROM daily_limit_up WHERE trade_date=%s AND status='跌停'",
        (trade_date,))
    
    # 连板信息（从 daily_limit_up board_times 判断）
    board_rows = zt_rows
    
    return {
        'limit_up': zt_rows,
        'limit_down': dt_rows,
        'board_rows': board_rows,
    }


def _load_rise_fall_amount(trade_date: str) -> dict:
    """从 sector_performance 汇总涨跌分布及成交额"""
    today_dash = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"
    rows = _db.fetchall(
        "SELECT sector_name, change_pct, amount, rise_count, fall_count "
        "FROM sector_performance WHERE record_date=%s",
        (today_dash,))
    
    rise = 0
    fall = 0
    total_amt = 0
    for r in rows:
        if r.get('rise_count'):
            rise += int(r['rise_count'])
        if r.get('fall_count'):
            fall += int(r['fall_count'])
        if r.get('amount'):
            total_amt += float(r['amount'])
    
    return {'rise': rise, 'fall': fall, 'total_amount': total_amt}


def _load_yesterday_picks(trade_date: str) -> list:
    """从 daily_picks 读取选股入库日T的股票，计算 ((T+2收盘价) - (T+1开盘价)) / (T+1开盘价) × 100%"""
    # 找最近一个选股日（排除当天，拿前一个交易日的入库日）
    yesterday = _db.fetchone(
        "SELECT DISTINCT trade_date FROM daily_picks WHERE trade_date < %s "
        "ORDER BY trade_date DESC LIMIT 1 OFFSET 1", (trade_date,))
    if not yesterday:
        return None

    ymd = yesterday['trade_date']  # T 日

    # 找 T+1（T 之后第一个交易日）
    t1_row = _db.fetchone(
        "SELECT trade_date FROM stock_daily "
        "WHERE trade_date > %s ORDER BY trade_date ASC LIMIT 1", (ymd,))
    if not t1_row:
        return None
    t1 = t1_row['trade_date']

    # 找 T+2（T+1 之后下一个交易日）
    t2_row = _db.fetchone(
        "SELECT trade_date FROM stock_daily "
        "WHERE trade_date > %s ORDER BY trade_date ASC LIMIT 1", (t1,))
    if not t2_row:
        return None
    t2 = t2_row['trade_date']

    # 读取选股入库日 T 的股票列表
    rows = _db.fetchall(
        "SELECT code, name, total_score, `rank`, highlights, source "
        "FROM daily_picks WHERE trade_date=%s ORDER BY total_score DESC",
        (ymd,))

    if not rows:
        return None

    codes = [r['code'] for r in rows]
    placeholders = ','.join(['%s'] * len(codes))

    # 查 T+1 开盘价
    t1_opens = {}
    t1_q = _db.fetchall(
        f"SELECT code, open FROM stock_daily "
        f"WHERE code IN ({placeholders}) AND trade_date=%s",
        codes + [t1])
    for q in t1_q:
        t1_opens[q['code']] = q

    # 查 T+2 收盘价
    t2_closes = {}
    t2_q = _db.fetchall(
        f"SELECT code, close FROM stock_daily "
        f"WHERE code IN ({placeholders}) AND trade_date=%s",
        codes + [t2])
    for q in t2_q:
        t2_closes[q['code']] = q

    result = []
    for r in rows:
        code = r['code']
        name = r['name'] or code
        highlights_val = r['highlights'] or ''
        source = r['source'] or ''
        reason = highlights_val if highlights_val else (source if source else f"评分{r['total_score']}分")

        t1o = t1_opens.get(code)
        t2o = t2_closes.get(code)

        if not t1o or not t2o:
            continue

        t1_open = float(t1o['open'])
        t2_close = float(t2o['close'])

        if t1_open <= 0:
            continue

        # 收益率 = (T+2收盘价 - T+1开盘价) / T+1开盘价 × 100%
        chg = (t2_close - t1_open) / t1_open * 100

        is_zt = chg >= 9.8
        is_near_zt = 7 <= chg < 9.8

        result.append({
            'name': name,
            'code': code,
            'change_pct': chg,
            'is_zt': is_zt,
            'is_near_zt': is_near_zt,
            'result': 'win' if chg > 0 else 'loss',
            'reason': reason,
            'total_score': int(r['total_score']) if r['total_score'] else 0,
        })

    return result


def _build_picks_data(trade_date: str, candidate_stocks: list) -> dict:
    """
    构建明日候选数据（调用 daily_pick_v2 的结果，从外部API补全换手/封板）
    返回 data['picks'] 字典
    """
    from core.analyzer.daily_pick_v2 import pick_stocks_v2, _save_picks_to_db
    
    # 关闭 INFO 日志
    import logging as _lg
    _lg.getLogger().setLevel(_lg.WARNING)
    
    results = pick_stocks_v2()
    _save_picks_to_db(results)
    
    # 备份文件
    base_path = os.path.dirname(__file__)
    with open(os.path.join(base_path, 'daily_picks_v2.json'), 'w') as f:
        json.dump(results, f, ensure_ascii=False, default=str, indent=2)
    
    # 分封板时间早/午（从 daily_limit_up 获取）
    def _seal_time(code):
        row = _db.fetchone(
            "SELECT seal_first_time FROM daily_limit_up WHERE code=%s AND trade_date=%s",
            (code, trade_date))
        if row and row.get('seal_first_time'):
            t = row['seal_first_time']
            if isinstance(t, str) and ':' in t:
                h = int(t.split(':')[0])
                if h < 11:
                    return ('早盘封板', 5)
                elif h < 13:
                    return ('盘中封板', 2)
                else:
                    return ('午后封板', 1)
        return ('盘中封板', 2)
    
    # 换手率注释（从 limit_up_tracking / daily_limit_up 获取，兜底从新浪获取）
    def _turnover_note(code):
        # 优先从 daily_limit_up 拿
        row = _db.fetchone(
            "SELECT turnover_rate FROM daily_limit_up WHERE code=%s AND trade_date=%s",
            (code, trade_date))
        tr = float(row['turnover_rate']) if row and row.get('turnover_rate') else 0
        # 没涨停过的票，从 stock_daily 拿换手率
        if tr <= 0:
            row2 = _db.fetchone(
                "SELECT turnover_rate FROM stock_daily WHERE code=%s AND trade_date=%s",
                (code, trade_date))
            tr = float(row2['turnover_rate']) if row2 and row2.get('turnover_rate') else 0
        if tr > 20:
            return f"换手{tr:.1f}%偏高"
        elif tr > 10:
            return f"换手{tr:.1f}%适中(+12)"
        elif tr > 5:
            return f"换手{tr:.1f}%适中(+12)"
        elif tr > 0:
            return f"换手{tr:.1f}%偏低"
        return ''
    
    picks_data = {
        'total_candidates': len(results.get('scored', [])),
        'max_name': results.get('scored', [{}])[0].get('name', '') if results.get('scored') else '',
        'max_score': results.get('scored', [{}])[0].get('total_score', 0) if results.get('scored') else 0,
    }
    
    # 构建B/C/D分组TOP3（按评分区间分组，每组取评分降序TOP3）
    scored = results.get('scored', [])
    score_groups = {'B': [], 'C': [], 'D': []}
    for s in scored:
        sc = int(s.get('total_score', 0))
        if 60 <= sc < 65:
            group = 'B'
        elif 65 <= sc < 70:
            group = 'C'
        elif sc >= 70:
            group = 'D'
        else:
            continue  # <60分不展示
        
        code = s['code']
        name = s['name']
        bd = s.get('breakdown', {})
        dims = {
            '笀码': bd.get('笀码结构', {}).get('score', 0),
            '接力': bd.get('资金接力', {}).get('score', 0),
            '板块': bd.get('板块环境', {}).get('score', 0),
            '趋势': bd.get('趋势位置', {}).get('score', 0),
            '大盘': bd.get('大盘安全', {}).get('score', 0),
            '位置': bd.get('位置评估', {}).get('score', 0),
        }
        
        # 换手+封板备注（复用已有函数）
        t_note = _turnover_note(code)
        s_time, s_pts_code = _seal_time(code)
        notes = []
        if t_note:
            notes.append(t_note)
        if s_time and s_pts_code > 0:
            notes.append(f"{s_time}({s_pts_code})")
        
        # 风险提示
        risks = list(s.get('risks', []))
        if not risks:
            chip = dims.get('笀码', 0)
            trend = dims.get('趋势', 0)
            if chip < 5:
                risks.append('笀码偏高')
            elif chip < 10:
                risks.append('笀码偏高')
            if trend < 5:
                risks.append('趋势偏弱')
            if dims.get('位置', 0) < 8:
                risks.append('位置一般')
        
        score_groups[group].append({
            'name': name,
            'code': code,
            'score': s['total_score'],
            'source': s.get('source', ''),
            'dims': dims,
            'notes': notes,
            'risks': risks[:2],
        })
    
    # 每组按评分降序排列，截取TOP3
    for g in ['B', 'C', 'D']:
        score_groups[g].sort(key=lambda x: x['score'], reverse=True)
        score_groups[g] = score_groups[g][:3]
    
    picks_data['score_groups'] = score_groups
    return picks_data


def _build_position_data(trade_date: str) -> list:
    """构建持仓数据"""
    positions = _db.fetchall('SELECT * FROM portfolio_positions')
    if not positions:
        return []
    
    result = []
    for p in positions:
        code = p['code']
        name = p['name']
        cost_price = float(p['cost_price'])
        shares = int(p['shares'])
        cost_total = cost_price * shares
        
        today_data = _db.fetchone(
            'SELECT close, change_pct, amount, turnover_rate FROM stock_daily '
            'WHERE code=%s AND trade_date=%s', (code, trade_date))
        
        # 始终完整初始化的 entry，保证渲染层不会因缺键崩溃
        entry = {
            'name': name,
            'code': code,
            'cost_price': cost_price,
            'shares': shares,
            'cost_total': cost_total,
            # 以下经济指标默认值，后续根据数据覆盖；极端兜底时 close 用 cost_price
            'close': cost_price,       # 现价（元）
            'cur_total': cost_total,   # 当前市值（元）
            'pnl_pct': 0.0,            # 盈亏百分比（%）
            'pnl_sym': '⚠️',  # 盈亏符号
            'profit_flag': None,       # 触发信号：stop/near_stop/take_profit/None
            'amount_yi': None,         # 当日成交额（亿元）
            'turnover': None,          # 当日换手率（%）
        }
        
        # 默认使用今日行情：若今日无数据（时序竞态：采集与复盘并行），则兜底到最近交易日
        used_data = today_data
        used_trade_date = trade_date
        
        if not (today_data and today_data['close']):
            # 时序竞态兜底：今日查不到（采集尚未写入），改用最近一个交易日收盘价
            prev_data = _db.fetchone(
                'SELECT close, amount, turnover_rate FROM stock_daily '
                'WHERE code=%s AND trade_date<%s ORDER BY trade_date DESC LIMIT 1',
                (code, trade_date))
            
            if prev_data and prev_data['close']:
                used_data = prev_data
                used_trade_date = _db.fetchone(
                    'SELECT MAX(trade_date) AS d FROM stock_daily WHERE code=%s AND trade_date<%s',
                    (code, trade_date))
                used_trade_date = used_trade_date['d'] if used_trade_date else None
                logger.warning(
                    '持仓%s今日(%s)无数据, 用最近交易日%s收盘价兜底',
                    code, trade_date, used_trade_date)
            else:
                # 极端情况：连最近交易日都没有，用成本价兜底，绝不抛异常
                logger.error(
                    '持仓%s今日(%s)及最近交易日均无数据, 用成本价%.2f兜底, 盈亏计0',
                    code, trade_date, cost_price)
        
        if used_data and used_data['close']:
            close = float(used_data['close'])
            pnl_pct = (close - cost_price) / cost_price * 100 if cost_price else 0.0
            entry['close'] = close
            entry['cur_total'] = close * shares
            entry['pnl_pct'] = pnl_pct
            entry['pnl_sym'] = '✅' if pnl_pct > 0 else ('❌' if pnl_pct < -2 else '⚠️')
            entry['amount_yi'] = float(used_data['amount']) / 1e8 if used_data.get('amount') else None
            entry['turnover'] = float(used_data['turnover_rate']) if used_data.get('turnover_rate') else None
            
            if pnl_pct <= -5:
                entry['profit_flag'] = 'stop'
            elif pnl_pct <= -3:
                entry['profit_flag'] = 'near_stop'
            elif pnl_pct >= 5:
                entry['profit_flag'] = 'take_profit'
            else:
                entry['profit_flag'] = None
        
        result.append(entry)
    
    return result


def _render_group_stats_table(groups: dict) -> str:
    """生成分组统计表格图片，返回图片文件路径"""
    from PIL import Image, ImageDraw, ImageFont

    headers = ["分组条件", "个股数量", "盈利数量", "胜率", "平均收益率"]
    rows = []

    for label, members in groups.items():
        if members:
            cnt = len(members)
            wins = sum(1 for p in members if p['change_pct'] > 0)
            wr = round(wins / cnt * 100, 1) if cnt > 0 else 0
            avg = sum(p['change_pct'] for p in members) / cnt
            rows.append([label, str(cnt), str(wins), f"{wr}%", f"{avg:+.2f}%"])
        else:
            rows.append([label, "0", "0", "0%", "0%"])

    # 字体
    font_path = '/System/Library/Fonts/STHeiti Medium.ttc'
    font = ImageFont.truetype(font_path, 14)
    bold_font = ImageFont.truetype(font_path, 14)

    col_widths = [180, 100, 100, 80, 120]
    row_height = 36
    header_h = 40
    padding = 10

    total_w = sum(col_widths) + padding * (len(col_widths) + 1)
    total_h = header_h + row_height * len(rows) + padding * 2 + 40  # +40 标题留空

    img = Image.new('RGB', (total_w, total_h), 'white')
    draw = ImageDraw.Draw(img)

    # 标题
    draw.text((padding + 4, 8), "📊 分组统计", fill='#333', font=font)

    y = 40 + padding
    x_start = padding

    # 表头背景
    draw.rectangle([0, y, total_w, y + header_h], fill='#4a90d9')
    x = x_start
    for i, h in enumerate(headers):
        tw = draw.textlength(h, font=bold_font)
        draw.text((x + (col_widths[i] - tw) / 2, y + 8), h, fill='white', font=bold_font)
        x += col_widths[i]
    y += header_h

    # 数据行
    for ri, row in enumerate(rows):
        x = x_start
        bg = '#f5f7fa' if ri % 2 == 0 else 'white'
        draw.rectangle([0, y, total_w, y + row_height], fill=bg)
        for ci, val in enumerate(row):
            draw.rectangle([x, y, x + col_widths[ci], y + row_height], outline='#e0e0e0')
            tw = draw.textlength(val, font=font)
            draw.text((x + (col_widths[ci] - tw) / 2, y + 8), val, fill='#333', font=font)
            x += col_widths[ci]
        y += row_height

    # 下边框
    for ci in range(len(col_widths)):
        x = x_start + sum(col_widths[:ci])
        draw.line([x, y, x + col_widths[ci], y], fill='#ccc', width=1)

    # 保存
    out_path = '/tmp/group_stats.png'
    img.save(out_path)
    return out_path


def _push_group_stats_image(groups: dict) -> bool:
    """生成分组统计表格图片并推送到飞书"""
    import requests, json, os

    # 从 OpenClaw 配置读取飞书 app 凭证
    openclaw_config_path = os.path.expanduser('~/.openclaw/openclaw.json')
    try:
        with open(openclaw_config_path) as f:
            cfg = json.load(f)
        feishu_cfg = cfg.get('channels', {}).get('feishu', {})
        app_id = feishu_cfg.get('appId', '')
        app_secret = feishu_cfg.get('appSecret', '')
        receive_id = feishu_cfg.get('allowFrom', [None])[0] or 'ou_cb7d1736b41d44ff4e5485599b80cb5d'
    except Exception as e:
        logger.error(f'[图片推送] 读取配置失败: {e}')
        return False

    if not app_id or not app_secret:
        logger.error('[图片推送] appId 或 appSecret 为空')
        return False

    try:
        # 生成图片
        img_path = _render_group_stats_table(groups)

        # 获取 token
        token_resp = requests.post(
            'https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal',
            json={'app_id': app_id, 'app_secret': app_secret}, timeout=10)
        if token_resp.status_code != 200:
            return False
        token = token_resp.json().get('tenant_access_token', '')

        # 上传图片
        with open(img_path, 'rb') as f:
            upload_resp = requests.post(
                'https://open.feishu.cn/open-apis/im/v1/images',
                headers={'Authorization': f'Bearer {token}'},
                files={'image': ('table.png', f, 'image/png')},
                data={'image_type': 'message'}, timeout=15)

        if upload_resp.status_code != 200:
            return False
        upload_data = upload_resp.json()
        if upload_data.get('code') != 0:
            return False

        image_key = upload_data['data']['image_key']

        # 发送图片消息
        msg_payload = {
            'receive_id': receive_id,
            'msg_type': 'image',
            'content': json.dumps({'image_key': image_key})
        }
        headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json; charset=utf-8'}
        send_resp = requests.post(
            'https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id',
            json=msg_payload, headers=headers, timeout=10)

        return send_resp.status_code == 200
    except Exception as e:
        logger.error(f'[图片推送] 失败: {e}')
        return False


def daily_close_task() -> str:
    """收盘后执行的完整流程"""
    import time as _time
    _t_start = _time.time()

    now = datetime.now()
    trade_date = now.strftime("%Y%m%d")
    today_dash = now.strftime("%Y-%m-%d")

    # 加载所有数据
    index_data = _load_index_quotes(trade_date)
    _log_timing(_t_start, "指数加载")
    sector_data = _load_sector_data(trade_date)
    _log_timing(_t_start, "板块数据")
    limit_up_data = _load_limit_up_data(trade_date)
    _log_timing(_t_start, "涨停数据")
    rf_amt = _load_rise_fall_amount(trade_date)
    _log_timing(_t_start, "涨跌家数/成交额")

    # 成交额（从 sector_performance 汇总，单位万股->元）
    sp_amt_row = _db.fetchone(
        "SELECT SUM(amount) as amt FROM sector_performance WHERE record_date=%s AND rank_type='all'",
        (f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}",))
    today_amt = float(sp_amt_row['amt']) if sp_amt_row and sp_amt_row['amt'] else 0
    if today_amt <= 0:
        today_amt = rf_amt['total_amount']
    prev_amt = _load_yesterday_total_amount(trade_date)
    amt_chg_text = ""
    if prev_amt > 0:
        chg = today_amt - prev_amt
        amt_chg_text = f"（较昨日{'+' if chg >= 0 else ''}{fmt_amount(chg)}）"

    # 指数
    indices = index_data
    szzs = indices.get('szzs', {})
    szcz = indices.get('szcz', {})
    cyb = indices.get('cyb', {})
    kc50 = indices.get('kc50', {})

    # 板块
    sec_top_gain = [s['name'] for s in (sector_data.get('top_gain') or [])[:3]]
    sec_top_fall = [s['name'] for s in (sector_data.get('top_fall') or [])[:5]]
    sec_top3 = [(s['name'], s['change_pct']) for s in (sector_data.get('top_gain') or [])[:3]]

    # 涨停/跌停
    zt_rows = limit_up_data['limit_up']
    dt_rows = limit_up_data['limit_down']
    board_rows = limit_up_data['board_rows']
    
    # 从board_rows连板统计
    zt_board = [r for r in board_rows if r.get('status') is None or r.get('status') in ('', '涨停', '首板', '连板', '龙头')]
    dt_board = dt_rows
    
    # 连板梯队（从 daily_limit_up 按 board_times 分组）
    board_ladder = []
    seen_boards = {}
    for r in zt_rows:
        bt = r.get('board_times')
        if bt and int(bt) >= 2:
            name = r.get('name', r['code'])
            seen_boards.setdefault(int(bt), []).append({'name': name, 'board_times': int(bt)})
    for b in sorted(seen_boards.keys(), reverse=True):
        for s in seen_boards[b][:5]:
            board_ladder.append(s)
    
    # 连续跌停
    cont_down = []
    for r in dt_rows:
        bt = r.get('board_times')
        if bt and int(bt) >= 2:
            cont_down.append({'name': r.get('name', r['code']), 'board_times': int(bt)})
    
    # 大额封跌停
    big_seal_down = []
    for r in dt_rows:
        sf = r.get('seal_fund')
        if sf and float(sf) >= 1e8:
            big_seal_down.append({'name': r.get('name', r['code']), 'seal_fund': float(sf) / 1e8})

    # 昨日选股
    yesterday_picks = _load_yesterday_picks(trade_date)
    _log_timing(_t_start, "昨日选股")

    # ReAct（新：20日滚动统计 + 评分归因）
    try:
        from core.analyzer.pick_react import build_react_report
        from core.reporter.close_report_tpl import render_react_section
        react_data = build_react_report(check_date=trade_date)
        react_text = render_react_section(react_data)
        note = react_data.get('window_info', {}).get('note') or '20天'
        logger.info(f'ReAct 复盘分析完成: {note}')
    except Exception as e:
        logger.warning(f'ReAct 分析异常: {e}')
        react_text = ''
    _log_timing(_t_start, "ReAct分析")

    # 明日候选
    picks_data = _build_picks_data(trade_date, [])
    _log_timing(_t_start, "明日选股")

    # 持仓
    position_data = _build_position_data(trade_date)

    # 组装 data
    data = {
        'date': today_dash,
        'indexes': indices,
        'amount': today_amt / 1e8 if today_amt > 0 else 0,
        'amount_chg_text': amt_chg_text,
        'rise_fall': {'rise': rf_amt['rise'], 'fall': rf_amt['fall']},
        'sectors': {
            'top_gain': sec_top_gain,
            'top_fall': sec_top_fall,
            'top3': sec_top3,
        },
        'limit_up': {
            'count': len(zt_board),
            'board_count': len([r for r in zt_board if r.get('board_times') and int(r['board_times']) >= 2]),
            'board_ladder': board_ladder[:5],
            'continuous_down': cont_down[:3],
            'big_seal_down': big_seal_down[:3],
            'down_count': len(dt_board),
        },
        'yesterday_picks': yesterday_picks if isinstance(yesterday_picks, list) else [],
        'react_report': react_data if isinstance(react_data, dict) and react_data.get('window_info') else (react_text or ''),
        'picks': picks_data,
        'positions': position_data,
    }

    # 渲染
    from core.reporter.close_report_tpl import render_report
    report = render_report(data)
    
    print(report)

    # 推送分组统计表格图片（T-2日复盘 B/C/D 分组统计）
    try:
        if isinstance(yesterday_picks, list):
            groups = {
                'B组(60~64)': [],
                'C组(65~69)': [],
                'D组(≥70)': [],
            }
            for p in yesterday_picks:
                sc = p.get('total_score', 0)
                if 60 <= sc < 65:
                    groups['B组(60~64)'].append(p)
                elif 65 <= sc < 70:
                    groups['C组(65~69)'].append(p)
                elif sc >= 70:
                    groups['D组(≥70)'].append(p)
            _push_group_stats_image(groups)
            logger.info('[图片推送] 分组统计表格已推送')
    except Exception as e:
        logger.error(f'[图片推送] 异常: {e}')

    _log_timing(_t_start, "渲染输出")
    _total = _time.time() - _t_start
    logger.info(f'[TIMING] 总耗时: {_total:.1f}s')
    
    return report


def _render_group_stats_table(groups: dict) -> str:
    """生成分组统计表格图片，返回图片文件路径"""
    from PIL import Image, ImageDraw, ImageFont

    headers = ["分组条件", "个股数量", "盈利数量", "胜率", "平均收益率"]
    rows = []

    for label, members in groups.items():
        if members:
            cnt = len(members)
            wins = sum(1 for p in members if p['change_pct'] > 0)
            wr = round(wins / cnt * 100, 1) if cnt > 0 else 0
            avg = sum(p['change_pct'] for p in members) / cnt
            rows.append([label, str(cnt), str(wins), f"{wr}%", f"{avg:+.2f}%"])
        else:
            rows.append([label, "0", "0", "0%", "0%"])

    font_path = '/System/Library/Fonts/STHeiti Medium.ttc'
    font = ImageFont.truetype(font_path, 14)
    bold_font = ImageFont.truetype(font_path, 14)

    col_widths = [180, 100, 100, 80, 120]
    row_height = 36
    header_h = 40
    padding = 10

    total_w = sum(col_widths) + padding * (len(col_widths) + 1)
    total_h = header_h + row_height * len(rows) + padding * 2 + 40

    img = Image.new('RGB', (total_w, total_h), 'white')
    draw = ImageDraw.Draw(img)

    # 标题
    draw.text((padding + 4, 8), "📊 分组统计", fill='#333', font=font)

    y = 40 + padding
    x_start = padding

    # 表头行
    draw.rectangle([0, y, total_w, y + header_h], fill='#4a90d9')
    x = x_start
    for i, h in enumerate(headers):
        tw = draw.textlength(h, font=bold_font)
        draw.text((x + (col_widths[i] - tw) / 2, y + 8), h, fill='white', font=bold_font)
        x += col_widths[i]
    y += header_h

    # 数据行
    for ri, row in enumerate(rows):
        x = x_start
        bg = '#f5f7fa' if ri % 2 == 0 else 'white'
        draw.rectangle([0, y, total_w, y + row_height], fill=bg)
        for ci, val in enumerate(row):
            draw.rectangle([x, y, x + col_widths[ci], y + row_height], outline='#e0e0e0')
            tw = draw.textlength(val, font=font)
            draw.text((x + (col_widths[ci] - tw) / 2, y + 8), val, fill='#333', font=font)
            x += col_widths[ci]
        y += row_height

    out_path = '/tmp/group_stats_table.png'
    img.save(out_path)
    return out_path


def _push_group_stats_image(groups: dict) -> bool:
    """生成分组统计表格图片并推送到飞书"""
    import requests, json, os

    
    openclaw_config_path = os.path.expanduser('~/.openclaw/openclaw.json')
    try:
        with open(openclaw_config_path) as f:
            cfg = json.load(f)
        feishu_cfg = cfg.get('channels', {}).get('feishu', {})
        app_id = feishu_cfg.get('appId', '')
        app_secret = feishu_cfg.get('appSecret', '')
        receive_id = feishu_cfg.get('allowFrom', [None])[0] or 'ou_cb7d1736b41d44ff4e5485599b80cb5d'
    except Exception as e:
        logger.error(f'[图片推送] 读取配置失败: {e}')
        return False

    if not app_id or not app_secret:
        logger.error('[图片推送] appId 或 appSecret 为空')
        return False

    try:
        img_path = _render_group_stats_table(groups)

        token_resp = requests.post(
            'https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal',
            json={'app_id': app_id, 'app_secret': app_secret}, timeout=10)
        if token_resp.status_code != 200:
            return False
        token = token_resp.json().get('tenant_access_token', '')

        with open(img_path, 'rb') as f:
            upload_resp = requests.post(
                'https://open.feishu.cn/open-apis/im/v1/images',
                headers={'Authorization': f'Bearer {token}'},
                files={'image': ('table.png', f, 'image/png')},
                data={'image_type': 'message'}, timeout=15)

        if upload_resp.status_code != 200:
            return False
        upload_data = upload_resp.json()
        if upload_data.get('code') != 0:
            return False

        image_key = upload_data['data']['image_key']

        msg_payload = {
            'receive_id': receive_id,
            'msg_type': 'image',
            'content': json.dumps({'image_key': image_key})
        }
        headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json; charset=utf-8'}
        send_resp = requests.post(
            'https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id',
            json=msg_payload, headers=headers, timeout=10)

        return send_resp.status_code == 200
    except Exception as e:
        logger.error(f'[图片推送] 失败: {e}')
        return False


if __name__ == '__main__':
    # setup_logger 已在模块顶部初始化，这里不再重复配置
    result = daily_close_task()
    logger.info("[OK] 收盘复盘完成")
