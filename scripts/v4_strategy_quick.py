#!/usr/bin/env python3
"""
70%胜率方案 v4.0 — 内存计算版，极速回测
"""
import sys, os, time, json, logging
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Callable
from collections import defaultdict, Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.dao import get_db

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)


def load_all_data() -> dict:
    """一次性加载所有需要的数据到内存"""
    db = get_db()
    data = {}

    # 1. 交易日历
    rows = db.fetchall("SELECT DISTINCT trade_date FROM stock_daily WHERE trade_date >= '20260105' AND trade_date <= '20260601' ORDER BY trade_date")
    dates = [r['trade_date'] for r in rows]
    data['dates'] = [d for d in dates if datetime.strptime(d, '%Y%m%d').weekday() < 5]
    date_set = set(data['dates'])

    # 2. 股票数据 key=(code, trade_date) -> dict
    all_rows = db.fetchall("SELECT code, name, trade_date, open, close, high, low, change_pct, total_market_cap, turnover_rate, amount FROM stock_daily WHERE trade_date BETWEEN '20260105' AND '20260601'")
    stock_by_key = {}
    stock_by_code = defaultdict(list)
    for r in all_rows:
        k = (r['code'], r['trade_date'])
        stock_by_key[k] = dict(r)
        stock_by_code[r['code']].append(dict(r))
    data['stock_by_key'] = stock_by_key
    data['stock_by_code'] = stock_by_code

    # 3. 涨停池: trade_date -> [code, ...]
    zt_by_date = defaultdict(list)
    for r in all_rows:
        if (r['change_pct'] or 0) >= 9.5:
            zt_by_date[r['trade_date']].append(r['code'])
    data['zt_by_date'] = dict(zt_by_date)

    # 4. 预处理MA: (code, trade_date) -> {ma5, ma10, ma20, ma30, ma60}
    print("预计算MA...")
    ma_cache = {}
    processed = 0
    for code, rows_list in stock_by_code.items():
        rows_list.sort(key=lambda x: x['trade_date'])
        for i, r in enumerate(rows_list):
            closes = [float(rows_list[j]['close']) for j in range(max(0, i-60), i) if rows_list[j]['close'] > 0]
            closes.reverse()
            td = r['trade_date']
            mas = {}
            for p in [5, 10, 20, 30, 60]:
                mas[p] = sum(closes[:p]) / p if len(closes) >= p else None
            ma_cache[(code, td)] = mas
            processed += 1
    data['ma_cache'] = ma_cache
    print(f"MA缓存: {processed}条")

    return data


def get_next_dates(td: str, all_dates: list) -> tuple:
    """获取T+1和T+2交易日"""
    found = []
    for d in all_dates:
        if d > td:
            found.append(d)
            if len(found) == 2:
                return found[0], found[1]
    return None, None


def run_strategy(name: str, pick_fn: Callable, data: dict, top_k: int = 5) -> dict:
    """通用回测函数"""
    all_dates = data['dates']
    sby = data['stock_by_key']
    zbd = data['zt_by_date']
    
    # 去掉最后2天
    test_dates = all_dates[:-2] if len(all_dates) > 2 else all_dates
    
    results = []
    for td in test_dates:
        pool = zbd.get(td, [])
        if not pool:
            continue
        
        picks = pick_fn(pool, td, data)
        if not picks:
            continue
        
        d1, d2 = get_next_dates(td, all_dates)
        if not d1 or not d2:
            continue
        
        for code in picks[:top_k]:
            s1 = sby.get((code, d1))
            s2 = sby.get((code, d2))
            if not s1 or not s2 or not s1['open'] or not s2['open']:
                continue
            buy = float(s1['open'])
            sell = float(s2['open'])
            if buy <= 0:
                continue
            ret = round((sell - buy) / buy * 100, 2)
            
            s_today = sby.get((code, td))
            results.append({
                'trade_date': td,
                'code': code,
                'name': s_today['name'] if s_today else '',
                'trade_return': ret,
            })
    
    # 统计
    rets = [r['trade_return'] for r in results]
    wins = sum(1 for v in rets if v > 0)
    big_win = sum(1 for v in rets if v > 5)
    big_loss = sum(1 for v in rets if v < -3)
    
    stats = {
        'name': name,
        'count': len(rets),
        'win_rate': wins / len(rets) * 100 if rets else 0,
        'avg_return': sum(rets) / len(rets) if rets else 0,
        'max_win': max(rets) if rets else 0,
        'max_loss': min(rets) if rets else 0,
        'big_win_pct': big_win / len(rets) * 100 if rets else 0,
        'big_loss_pct': big_loss / len(rets) * 100 if rets else 0,
    }
    return stats


# ================================================================
# 策略集 (每个函数接受 pool, td, data)
# ================================================================

def S01_random(pool, td, data):
    """基准：随机选5只"""
    import random
    return random.sample(pool, min(5, len(pool)))

def S02_random_3(pool, td, data):
    """基准：随机选3只"""
    import random
    return random.sample(pool, min(3, len(pool)))

def S03_random_1(pool, td, data):
    """基准：随机选1只"""
    import random
    return [random.choice(pool)] if pool else []

def S04_first_board(pool, td, data):
    """首板：近10日首次涨停"""
    stock_by_code = data['stock_by_code']
    zbd = data['zt_by_date']
    result = []
    for code in pool:
        rows = [r for r in stock_by_code.get(code, []) if r['trade_date'] < td]
        recent_cnt = sum(1 for r in rows if (r['change_pct'] or 0) >= 9.5)
        if recent_cnt < 2:
            result.append(code)
        if len(result) >= 5:
            break
    return result

def S05_market_filter(pool, td, data):
    """大盘过滤：大盘跌超1.5%不选"""
    sby = data['stock_by_key']
    sh = sby.get(('000001', td))
    if sh and (sh['change_pct'] or 0) < -1.5:
        return []
    return pool[:5]

def S06_lowest_mcap(pool, td, data):
    """最小市值"""
    sby = data['stock_by_key']
    scored = []
    for code in pool:
        s = sby.get((code, td))
        if s:
            scored.append((code, float(s['total_market_cap'] or 999999999999)))
    scored.sort(key=lambda x: x[1])
    return [c for c, _ in scored[:5]]

def S07_above_ma20(pool, td, data):
    """收盘在MA20上方"""
    mac = data['ma_cache']
    sby = data['stock_by_key']
    result = []
    for code in pool:
        mas = mac.get((code, td), {})
        s = sby.get((code, td))
        if mas.get(20) and s and float(s['close']) > mas[20]:
            result.append(code)
        if len(result) >= 5:
            break
    return result

def S08_gap_up(pool, td, data):
    """开盘涨幅3%-7%涨停"""
    sby = data['stock_by_key']
    stock_by_code = data['stock_by_code']
    result = []
    for code in pool:
        s = sby.get((code, td))
        if not s:
            continue
        prev = [r for r in stock_by_code.get(code, []) if r['trade_date'] < td]
        if not prev:
            continue
        prev_close = prev[-1]['close']
        gap = (float(s['open']) - float(prev_close)) / float(prev_close) * 100
        if 3 <= gap <= 7:
            result.append(code)
        if len(result) >= 5:
            break
    return result

def S09_volume_breakout(pool, td, data):
    """放量突破（量比>2）"""
    sby = data['stock_by_key']
    stock_by_code = data['stock_by_code']
    result = []
    for code in pool:
        s = sby.get((code, td))
        if not s or not s['amount']:
            continue
        today_amt = float(s['amount'])
        prev_rows = [r for r in stock_by_code.get(code, []) if r['trade_date'] < td][-10:]
        avg_amt = sum(float(r['amount']) for r in prev_rows if r['amount']) / len(prev_rows) if prev_rows else 0
        if avg_amt > 0 and today_amt / avg_amt >= 2:
            result.append(code)
        if len(result) >= 5:
            break
    return result

def S10_no_overbought(pool, td, data):
    """近10日累计涨幅<20%（非追高）"""
    stock_by_code = data['stock_by_code']
    sby = data['stock_by_key']
    result = []
    for code in pool:
        prev_rows = [r for r in stock_by_code.get(code, []) if r['trade_date'] < td][-10:]
        if len(prev_rows) < 2:
            result.append(code)
            if len(result) >= 5:
                break
            continue
        chg_total = sum(float(r['change_pct'] or 0) for r in prev_rows)
        if chg_total < 20:
            result.append(code)
            if len(result) >= 5:
                break
    return result

def S11_board2(pool, td, data):
    """2连板"""
    stock_by_code = data['stock_by_code']
    sby = data['stock_by_key']
    result = []
    for code in pool:
        prev = [r for r in stock_by_code.get(code, []) if r['trade_date'] < td][-5:]
        cnt = 0
        for r in prev:
            if (r['change_pct'] or 0) >= 9.5:
                cnt += 1
            else:
                break
        if cnt == 1:  # 今天涨停+昨天涨停=2连板
            result.append(code)
        if len(result) >= 5:
            break
    return result

def S12_first_board_hot(pool, td, data):
    """首板+热门板块(伪)：当日涨停少的票"""
    zbd = data['zt_by_date']
    zt_count = len(zbd.get(td, []))
    stock_by_code = data['stock_by_code']
    result = []
    for code in pool:
        prev = [r for r in stock_by_code.get(code, []) if r['trade_date'] < td]
        recent_cnt = sum(1 for r in prev if (r['change_pct'] or 0) >= 9.5)
        if recent_cnt < 2:
            result.append(code)
        if len(result) >= 5:
            break
    return result

def S13_turnover_high(pool, td, data):
    """高换手率"""
    sby = data['stock_by_key']
    scored = []
    for code in pool:
        s = sby.get((code, td))
        if s and s['turnover_rate']:
            scored.append((code, float(s['turnover_rate'])))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [c for c, _ in scored[:5]]

def S14_turnover_mid(pool, td, data):
    """中换手率5-15%"""
    sby = data['stock_by_key']
    result = []
    for code in pool:
        s = sby.get((code, td))
        if s and s['turnover_rate']:
            tr = float(s['turnover_rate'])
            if 5 <= tr <= 15:
                result.append(code)
        if len(result) >= 5:
            break
    return result

def S15_high_cap(pool, td, data):
    """大市值(>=50亿)涨停"""
    sby = data['stock_by_key']
    result = []
    for code in pool:
        s = sby.get((code, td))
        if s and (float(s['total_market_cap'] or 0) >= 5000000000):
            result.append(code)
        if len(result) >= 5:
            break
    return result

def S16_low_cap(pool, td, data):
    """小市值(<50亿)"""
    sby = data['stock_by_key']
    result = []
    for code in pool:
        s = sby.get((code, td))
        if s and (float(s['total_market_cap'] or 999999999999) < 5000000000):
            result.append(code)
        if len(result) >= 5:
            break
    return result

def S17_fridays_bad(pool, td, data):
    """周五不买"""
    dt = datetime.strptime(td, '%Y%m%d')
    if dt.weekday() == 4:  # 周五
        return []
    return pool[:5]


# ================================================================
# 主入口
# ================================================================

STRATEGIES = {
    'S01_随机5只(基准)': (S01_random, 5),
    'S02_随机3只': (S02_random_3, 3),
    'S03_随机1只': (S03_random_1, 1),
    'S04_首板': (S04_first_board, 5),
    'S05_大盘过滤': (S05_market_filter, 5),
    'S06_最小市值': (S06_lowest_mcap, 5),
    'S07_MA20上方': (S07_above_ma20, 5),
    'S08_高开板': (S08_gap_up, 5),
    'S09_放量突破': (S09_volume_breakout, 5),
    'S10_非追高': (S10_no_overbought, 5),
    'S11_2连板': (S11_board2, 5),
    'S12_首板+热门': (S12_first_board_hot, 5),
    'S13_高换手': (S13_turnover_high, 5),
    'S14_中换手': (S14_turnover_mid, 5),
    'S15_大市值': (S15_high_cap, 5),
    'S16_小市值': (S16_low_cap, 5),
    'S17_不周五': (S17_fridays_bad, 5),
}

if __name__ == '__main__':
    logger.info("加载数据...")
    data = load_all_data()
    
    results = []
    for name, (fn, top_k) in STRATEGIES.items():
        start = time.time()
        stats = run_strategy(name, fn, data, top_k=top_k)
        elapsed = time.time() - start
        logger.info(f"{name}: {stats['count']}次 胜率{stats['win_rate']:.1f}% 平均{stats['avg_return']:+.2f}% ({elapsed:.1f}s)")
        results.append(stats)
    
    # 排名
    results.sort(key=lambda x: x['win_rate'], reverse=True)
    print("\n" + "="*90)
    print("多策略回测结果 — 按胜率降序")
    print("="*90)
    print(f"{'排名':>4} {'策略名':>18} {'交易':>6} {'胜率':>8} {'平均':>8} {'大胜%':>8} {'大亏%':>8} {'最大赢':>8} {'最大亏':>8}")
    print("-"*90)
    for i, r in enumerate(results, 1):
        print(f"{i:>4} {r['name']:>18} {r['count']:>6} {r['win_rate']:>7.1f}% "
              f"{r['avg_return']:>+7.2f}% {r['big_win_pct']:>7.1f}% {r['big_loss_pct']:>7.1f}% "
              f"{r['max_win']:>+7.2f}% {r['max_loss']:>+7.2f}%")
    
    # 保存
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'v4_strategy_results.json')
    with open(out, 'w') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    logger.info(f"\n结果已保存: {out}")
