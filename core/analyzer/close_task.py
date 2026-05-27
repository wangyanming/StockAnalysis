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

# 默认使用 MySQL
if 'STOCK_DB_URL' not in os.environ:
    os.environ['STOCK_DB_URL'] = 'mysql://root:stock123@127.0.0.1:3306/stock_analysis'

logger = logging.getLogger(__name__)

# 关掉 INFO 日志（避免混入 print 输出）
logging.getLogger().setLevel(logging.WARNING)

from dao import get_db
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
    """获取候选股票的换手率及封板数据"""
    codes = [s['code'] for s in candidate_stocks if s.get('code')]
    if not codes:
        return {}
    
    # 先试新浪
    quotes = _fetch_sina_quote(codes)
    # 新浪没数据的试腾讯
    missing = [c for c in codes if c not in quotes or not quotes[c].get('turnover_rate')]
    if missing:
        tx_quotes = _fetch_tx_quote(missing)
        quotes.update(tx_quotes)
    
    return quotes


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
    """查询昨日成交额"""
    td = datetime.strptime(trade_date, '%Y%m%d')
    for i in range(1, 8):
        prev = td - timedelta(days=i)
        prev_str = prev.strftime('%Y%m%d')
        row = _db.fetchone(
            "SELECT SUM(amount) as amt FROM index_quotes WHERE record_date=%s",
            (f"{prev_str[:4]}-{prev_str[4:6]}-{prev_str[6:8]}",))
        if row and row['amt'] and row['amt'] > 0:
            return float(row['amt'])
    return 0


def _load_sector_data(trade_date: str) -> dict:
    """从 sector_performance 表读取板块排行"""
    today_dash = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"
    result = {'top_gain': [], 'top_fall': [], 'top_inflow': [], 'top_outflow': []}
    
    for rank_type in ['top_gain', 'top_fall', 'top_inflow', 'top_outflow']:
        rows = _db.fetchall(
            "SELECT sector_name, change_pct, amount, net_inflow FROM sector_performance "
            "WHERE record_date=%s AND rank_type=%s ORDER BY change_pct DESC",
            (today_dash, rank_type))
        for r in rows:
            result[rank_type].append({
                'name': r['sector_name'],
                'change_pct': float(r['change_pct']) if r['change_pct'] else 0,
                'amount': float(r['amount']) if r.get('amount') else 0,
                'net_inflow': float(r['net_inflow']) if r.get('net_inflow') else 0,
            })
    
    return result


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
    """从 daily_picks 读取昨日选股及今日表现"""
    today = datetime.now()
    today_str = today.strftime("%Y%m%d")
    
    # 找最近一个选股日
    yesterday = _db.fetchone(
        "SELECT DISTINCT trade_date FROM daily_picks WHERE trade_date < %s "
        "ORDER BY trade_date DESC LIMIT 1", (today_str,))
    if not yesterday:
        return None
    
    ymd = yesterday['trade_date']
    
    rows = _db.fetchall(
        "SELECT code, name, total_score, `rank`, highlights, source "
        "FROM daily_picks WHERE trade_date=%s ORDER BY `rank` ASC LIMIT 10",
        (ymd,))
    
    if not rows:
        return None
    
    # 查今日行情
    codes = [r['code'] for r in rows]
    today_quotes = {}
    if codes:
        placeholders = ','.join(['%s'] * len(codes))
        quotes = _db.fetchall(
            f"SELECT code, close, change_pct FROM stock_daily "
            f"WHERE code IN ({placeholders}) AND trade_date=%s",
            codes + [today_str])
        for q in quotes:
            today_quotes[q['code']] = q
    
    # 查选股日行情
    pick_quotes = {}
    if codes:
        pk = _db.fetchall(
            f"SELECT code, close FROM stock_daily "
            f"WHERE code IN ({placeholders}) AND trade_date=%s",
            codes + [ymd])
        for q in pk:
            pick_quotes[q['code']] = q
    
    result = []
    for r in rows:
        code = r['code']
        name = r['name'] or code
        highlights_val = r['highlights'] or ''
        source = r['source'] or ''
        reason = highlights_val if highlights_val else (source if source else f"评分{r['total_score']}分")
        
        tq = today_quotes.get(code)
        pq = pick_quotes.get(code)
        
        if tq and pq and pq['close'] and float(pq['close']) > 0:
            chg = (float(tq['close']) - float(pq['close'])) / float(pq['close']) * 100
        else:
            continue
        
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
        })
    
    return result


def _build_picks_data(trade_date: str, candidate_stocks: list) -> dict:
    """
    构建明日候选数据（调用 daily_pick_v2 的结果，从外部API补全换手/封板）
    返回 data['picks'] 字典
    """
    from daily_pick_v2 import pick_stocks_v2, _save_picks_to_db
    
    # 关闭 INFO 日志
    import logging as _lg
    _lg.getLogger().setLevel(_lg.WARNING)
    
    results = pick_stocks_v2()
    _save_picks_to_db(results)
    
    # 备份文件
    base_path = os.path.dirname(__file__)
    with open(os.path.join(base_path, 'daily_picks_v2.json'), 'w') as f:
        json.dump(results, f, ensure_ascii=False, default=str, indent=2)
    
    up_top5 = results.get('up_top5', [])
    non_up_top5 = results.get('non_up_top5', [])
    
    # 从外部API获取换手率
    all_codes = [s['code'] for s in up_top5] + [s['code'] for s in non_up_top5]
    quotes = _get_quotes_for_candidates([{'code': c} for c in all_codes])
    
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
        'market_status': results.get('market', {}).get('status', '正常'),
        'market_change': results.get('market', {}).get('sh_change', 0),
        'limit_up_total': results.get('total_limit_up', 0),
        'hot_industries': results.get('hot_industries', [])[:4] if results.get('hot_industries') else [],
        'up_top5': [],
        'non_up_top5': [],
        'top3_advice': [],
    }
    
    for s in up_top5:
        code = s['code']
        name = s['name']
        bd = s.get('breakdown', {})
        dims = {
            '筹码': bd.get('筹码结构', {}).get('score', 0),
            '接力': bd.get('资金接力', {}).get('score', 0),
            '板块': bd.get('板块环境', {}).get('score', 0),
            '趋势': bd.get('趋势位置', {}).get('score', 0),
            '大盘': bd.get('大盘安全', {}).get('score', 0),
            '位置': bd.get('位置评估', {}).get('score', 0),
        }
        
        # 换手+封板
        t_note = _turnover_note(code)
        s_time, s_pts_code = _seal_time(code)
        
        notes = []
        if t_note:
            notes.append(t_note)
        if s_time and s_pts_code > 0:
            notes.append(f"{s_time}({s_pts_code})")
        
        # 风险
        risks = list(s.get('risks', []))
        if not risks:
            chip = dims.get('筹码', 0)
            trend = dims.get('趋势', 0)
            if chip < 5:
                risks.append('筹码偏高')
            elif chip < 10:
                risks.append('筹码偏高')
            if trend < 5:
                risks.append('趋势偏弱')
            if dims.get('位置', 0) < 8:
                risks.append('位置一般')
        
        picks_data['up_top5'].append({
            'name': name,
            'code': code,
            'score': s['total_score'],
            'source': s.get('source', ''),
            'dims': dims,
            'notes': notes,
            'risks': risks[:2],
        })
    
    # 区间潜伏
    for s in non_up_top5:
        code = s['code']
        name = s['name']
        bd = s.get('breakdown', {})
        dims = {
            '筹码': bd.get('筹码结构', {}).get('score', 0),
            '接力': bd.get('资金接力', {}).get('score', 0),
            '板块': bd.get('板块环境', {}).get('score', 0),
            '趋势': bd.get('趋势位置', {}).get('score', 0),
            '大盘': bd.get('大盘安全', {}).get('score', 0),
            '位置': bd.get('位置评估', {}).get('score', 0),
        }
        
        notes = []
        pos_s = dims.get('位置', 0)
        if pos_s >= 8:
            notes.append(f"位置适中(+{pos_s})")
        
        # 均线多头
        ma5 = s.get('_ma5', 0)
        close_now = s.get('today_close', 0)
        if ma5 > 0 and close_now and isinstance(close_now, (int, float)) and isinstance(ma5, (int, float)):
            notes.append(f"均线多头排列(MA5>{ma5:.1f})(+8)")
        
        if not notes:
            notes.append(f"60日底部{s.get('_60d_position', 0):.0f}%分位")
        
        risks = list(s.get('risks', []))
        if not risks:
            if dims.get('位置', 0) < 8:
                risks.append('位置一般')
        
        picks_data['non_up_top5'].append({
            'name': name,
            'code': code,
            'score': s['total_score'],
            'source': s.get('source', ''),
            'dims': dims,
            'notes': notes,
            'risks': risks[:2],
        })
    
    # TOP3 盯盘建议
    all_top = up_top5[:5] + non_up_top5[:5]
    all_sorted = sorted(all_top, key=lambda x: x.get('total_score', 0), reverse=True)
    for i, s in enumerate(all_sorted[:3]):
        code = s['code']
        name = s['name']
        bd = s.get('breakdown', {})
        pos_s = bd.get('位置评估', {}).get('score', 0)
        trend_s = bd.get('趋势位置', {}).get('score', 0)
        pos_tag = '低位' if pos_s >= 15 else ('中位' if pos_s >= 8 else '高位')
        trend_tag = '趋势强' if trend_s >= 14 else ('趋势好' if trend_s >= 10 else '趋势弱')
        
        group = s.get('group', '')
        if group == '涨停回踩':
            advice = "首板，竞价量比>3可参与，评分偏低，小仓试"
        elif group == '区间潜伏':
            advice = "回踩5日线低吸，竞价量比>3可参与"
        else:
            advice = "竞价关注量比>3、高开的候选股"
        
        picks_data['top3_advice'].append({
            'name': name,
            'code': code,
            'score': s['total_score'],
            'source': s.get('source', ''),
            'position': pos_tag,
            'trend': trend_tag,
            'advice': advice,
        })
    
    return picks_data


def _build_react_data(trade_date: str, yesterday_picks: list) -> dict:
    """构建ReAct复盘数据（直接从 daily_picks + stock_daily）"""
    today_str = datetime.now().strftime('%Y%m%d')
    
    result = {
        'pick_date': '',
        'check_date': today_str,
        'total_count': 0,
        'win_rate': 0,
        'avg_return': 0,
        'max_gain': 0,
        'max_loss': 0,
        'big_gain_count': 0,
        'score_groups': [],
        'group_groups': [],
        'past_week': {'count': 0, 'win_rate': 0, 'avg_return': 0},
    }
    
    # --- 今日选股复盘 ---
    if yesterday_picks:
        total = len(yesterday_picks)
        up = sum(1 for s in yesterday_picks if s['result'] == 'win')
        avg_ret = sum(s['change_pct'] for s in yesterday_picks) / total if total > 0 else 0
        result.update({
            'pick_date': '',
            'total_count': total,
            'win_rate': round(up / total * 100, 0) if total > 0 else 0,
            'avg_return': round(avg_ret, 2),
            'max_gain': round(max((s['change_pct'] for s in yesterday_picks), default=0), 2),
            'max_loss': round(min((s['change_pct'] for s in yesterday_picks), default=0), 2),
            'big_gain_count': sum(1 for s in yesterday_picks if s['change_pct'] >= 2),
        })
        
        # 评分分组 + 推荐分组
        from dao import get_db
        try:
            db = get_db()
            yest_date = db.fetchone(
                "SELECT DISTINCT trade_date FROM daily_picks WHERE trade_date < %s "
                "ORDER BY trade_date DESC LIMIT 1", (today_str,))
            if yest_date:
                ymd = yest_date['trade_date']
                result['pick_date'] = ymd
                
                scored = db.fetchall(
                    "SELECT code, total_score, source FROM daily_picks WHERE trade_date=%s", (ymd,))
                score_map = {r['code']: int(r['total_score']) for r in scored if r['total_score']}
                source_map = {r['code']: r.get('source', '') for r in scored}
                db.close()
                
                groups = {'高分(≥50)': [], '中分(40-50)': [], '低分(<40)': []}
                groups2 = {'涨停接力': [], '区间潜伏': []}
                for s in yesterday_picks:
                    sc = score_map.get(s['code'], 40)
                    if sc >= 50:
                        groups['高分(≥50)'].append(s['change_pct'])
                    elif sc >= 40:
                        groups['中分(40-50)'].append(s['change_pct'])
                    else:
                        groups['低分(<40)'].append(s['change_pct'])
                    src = source_map.get(s['code'], '')
                    if src in ('涨停热点', '涨停回踩', '涨停接力'):
                        groups2['涨停接力'].append(s['change_pct'])
                    else:
                        groups2['区间潜伏'].append(s['change_pct'])
                
                grp = []
                for label, chgs in groups.items():
                    if chgs:
                        wr = len([c for c in chgs if c > 0]) / len(chgs) * 100
                        ag = sum(chgs) / len(chgs)
                        icon = '✅' if wr >= 50 else ('⚠️' if wr >= 30 else '❌')
                        grp.append((label, len(chgs), round(wr), round(ag, 2), icon))
                result['score_groups'] = grp
                
                grp2 = []
                for label, chgs in groups2.items():
                    if chgs:
                        wr = len([c for c in chgs if c > 0]) / len(chgs) * 100
                        ag = sum(chgs) / len(chgs)
                        icon = '✅' if wr >= 50 else ('⚠️' if wr >= 30 else '❌')
                        grp2.append((label, len(chgs), round(wr), round(ag, 2), icon))
                result['group_groups'] = grp2
        except Exception:
            pass
    
    # --- 近一周统计 ---
    try:
        from dao import get_db
        db = get_db()
        week_ago = (datetime.now() - timedelta(days=10)).strftime('%Y%m%d')
        rows = db.fetchall(
            "SELECT DISTINCT trade_date FROM daily_picks WHERE trade_date>=%s AND trade_date<=%s "
            "ORDER BY trade_date DESC", (week_ago, today_str))
        if rows:
            pick_dates = [r['trade_date'] for r in rows]
            valid_chgs = []
            for pd in pick_dates:
                if pd == today_str:
                    continue
                check_dt = datetime.strptime(pd, '%Y%m%d') + timedelta(days=1)
                while check_dt.weekday() >= 5:
                    check_dt += timedelta(days=1)
                check_d = check_dt.strftime('%Y%m%d')
                
                p_rows = db.fetchall(
                    "SELECT p.code, sd2.close as pick_close "
                    "FROM daily_picks p "
                    "JOIN stock_daily sd2 ON p.code=sd2.code AND sd2.trade_date=p.trade_date "
                    "WHERE p.trade_date=%s AND sd2.close>0", (pd,))
                for pr in p_rows:
                    today_row = db.fetchone(
                        "SELECT close FROM stock_daily WHERE code=%s AND trade_date=%s",
                        (pr['code'], check_d))
                    if today_row and today_row['close']:
                        chg = (float(today_row['close']) - float(pr['pick_close'])) / float(pr['pick_close']) * 100
                        valid_chgs.append(chg)
            db.close()
            
            if valid_chgs:
                result['past_week'] = {
                    'count': len(valid_chgs),
                    'win_rate': round(len([c for c in valid_chgs if c > 0]) / len(valid_chgs) * 100, 0),
                    'avg_return': round(sum(valid_chgs) / len(valid_chgs), 2),
                }
    except Exception:
        pass
    
    return result


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
        
        entry = {
            'name': name,
            'code': code,
            'cost_price': cost_price,
            'shares': shares,
            'cost_total': cost_total,
        }
        
        if today_data and today_data['close']:
            close = float(today_data['close'])
            pnl_pct = (close - cost_price) / cost_price * 100
            entry['close'] = close
            entry['cur_total'] = close * shares
            entry['pnl_pct'] = pnl_pct
            entry['pnl_sym'] = '✅' if pnl_pct > 0 else ('❌' if pnl_pct < -2 else '⚠️')
            entry['amount_yi'] = float(today_data['amount']) / 1e8 if today_data['amount'] else None
            entry['turnover'] = float(today_data['turnover_rate']) if today_data['turnover_rate'] else None
            
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


def daily_close_task() -> str:
    """收盘后执行的完整流程"""
    now = datetime.now()
    trade_date = now.strftime("%Y%m%d")
    today_dash = now.strftime("%Y-%m-%d")

    # 加载所有数据
    index_data = _load_index_quotes(trade_date)
    sector_data = _load_sector_data(trade_date)
    limit_up_data = _load_limit_up_data(trade_date)
    rf_amt = _load_rise_fall_amount(trade_date)

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

    # ReAct
    react_data = _build_react_data(trade_date, yesterday_picks)

    # 明日候选
    picks_data = _build_picks_data(trade_date, [])

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
        'react_report': react_data,
        'picks': picks_data,
        'positions': position_data,
    }

    # 渲染
    from close_report_tpl import render_report
    report = render_report(data)
    print(report)
    
    return report


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
    result = daily_close_task()
    print(f"[OK] 收盘复盘完成", file=sys.stderr)
