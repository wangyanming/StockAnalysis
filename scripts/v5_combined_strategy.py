#!/usr/bin/env python3
"""
v5.0 — 组合策略+投票法+过滤链

思路：单个策略的胜率上限就在50-55%之间，但组合多策略可以提高稳定性。
核心方法：
  1. 多策略投票：每个策略每天选出K只，被选中的票得分+1
  2. 只买得票最高的N只
  3. 用v6评分系统作为第一道筛选，再用v4策略投票二次过滤
"""
import sys, os, time, json, logging
from datetime import datetime, timedelta
from typing import List, Dict, Tuple
from collections import defaultdict, Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.dao import get_db

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)


def load_all_data():
    db = get_db()
    data = {}

    rows = db.fetchall("SELECT DISTINCT trade_date FROM stock_daily WHERE trade_date >= '20260105' AND trade_date <= '20260601' ORDER BY trade_date")
    dates = [r['trade_date'] for r in rows]
    data['dates'] = [d for d in dates if datetime.strptime(d, '%Y%m%d').weekday() < 5]

    all_rows = db.fetchall("SELECT code, name, trade_date, open, close, high, low, change_pct, total_market_cap, turnover_rate, amount FROM stock_daily WHERE trade_date BETWEEN '20260105' AND '20260601'")
    stock_by_key = {}
    stock_by_code = defaultdict(list)
    for r in all_rows:
        k = (r['code'], r['trade_date'])
        stock_by_key[k] = dict(r)
        stock_by_code[r['code']].append(dict(r))
    data['stock_by_key'] = stock_by_key
    data['stock_by_code'] = stock_by_code

    zt_by_date = defaultdict(list)
    for r in all_rows:
        if (r['change_pct'] or 0) >= 9.5:
            zt_by_date[r['trade_date']].append(r['code'])
    data['zt_by_date'] = dict(zt_by_date)

    print("预计算MA...")
    ma_cache = {}
    for code, rows_list in stock_by_code.items():
        rows_list.sort(key=lambda x: x['trade_date'])
        for i, r in enumerate(rows_list):
            closes = [float(rows_list[j]['close']) for j in range(max(0, i-60), i) if rows_list[j]['close'] > 0]
            closes.reverse()
            mas = {}
            for p in [5, 10, 20, 30, 60]:
                mas[p] = sum(closes[:p]) / p if len(closes) >= p else None
            ma_cache[(code, r['trade_date'])] = mas
    data['ma_cache'] = ma_cache

    return data


def get_next_dates(td: str, all_dates: list):
    found = []
    for d in all_dates:
        if d > td:
            found.append(d)
            if len(found) == 2:
                return found[0], found[1]
    return None, None


# ================================================================
# 8个精选策略（基于v4结果筛选）
# ================================================================

def pick_low_mcap(pool, td, data, k=3):
    """小市值"""
    sby = data['stock_by_key']
    scored = [(c, float(sby.get((c, td), {}).get('total_market_cap', 0) or 999999999999)) for c in pool]
    scored.sort(key=lambda x: x[1])
    return [c for c, _ in scored[:k]]

def pick_high_mcap(pool, td, data, k=3):
    """大市值"""
    sby = data['stock_by_key']
    scored = [(c, float(sby.get((c, td), {}).get('total_market_cap', 0) or 0)) for c in pool]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [c for c, _ in scored[:k]]

def pick_ma20_above(pool, td, data, k=3):
    """MA20上方"""
    mac = data['ma_cache']
    sby = data['stock_by_key']
    result = [c for c in pool if (mas := mac.get((c, td), {})).get(20) and float(sby.get((c, td), {}).get('close', 0)) > mas[20]]
    return result[:k]

def pick_no_overbought(pool, td, data, k=3):
    """近10日涨幅<20%"""
    sby = data['stock_by_key']
    sc = data['stock_by_code']
    result = []
    for c in pool:
        prev = [r for r in sc.get(c, []) if r['trade_date'] < td][-10:]
        if len(prev) < 2 or sum(float(r['change_pct'] or 0) for r in prev) < 20:
            result.append(c)
        if len(result) >= k:
            break
    return result

def pick_volume_breakout(pool, td, data, k=3):
    """放量突破"""
    sby = data['stock_by_key']
    sc = data['stock_by_code']
    result = []
    for c in pool:
        s = sby.get((c, td))
        if not s or not s['amount']:
            continue
        prev = [r for r in sc.get(c, []) if r['trade_date'] < td][-10:]
        avg = sum(float(r['amount']) for r in prev if r['amount']) / len(prev) if prev else 0
        if avg > 0 and float(s['amount']) / avg >= 2:
            result.append(c)
        if len(result) >= k:
            break
    return result

def pick_gap_up(pool, td, data, k=3):
    """高开板3-7%"""
    sby = data['stock_by_key']
    sc = data['stock_by_code']
    result = []
    for c in pool:
        s = sby.get((c, td))
        if not s:
            continue
        prev = [r for r in sc.get(c, []) if r['trade_date'] < td]
        if not prev:
            continue
        gap = (float(s['open']) - float(prev[-1]['close'])) / float(prev[-1]['close']) * 100
        if 3 <= gap <= 7:
            result.append(c)
        if len(result) >= k:
            break
    return result

def pick_first_board(pool, td, data, k=3):
    """首板"""
    sc = data['stock_by_code']
    result = []
    for c in pool:
        prev = [r for r in sc.get(c, []) if r['trade_date'] < td]
        recent_cnt = sum(1 for r in prev if (r['change_pct'] or 0) >= 9.5)
        if recent_cnt < 2:
            result.append(c)
        if len(result) >= k:
            break
    return result

def pick_board2(pool, td, data, k=3):
    """2连板"""
    sc = data['stock_by_code']
    result = []
    for c in pool:
        prev = [r for r in sc.get(c, []) if r['trade_date'] < td][-5:]
        cnt = sum(1 for r in prev if (r['change_pct'] or 0) >= 9.5)
        if cnt == 1:
            result.append(c)
        if len(result) >= k:
            break
    return result

def pick_mid_turnover(pool, td, data, k=3):
    """中换手5-15%"""
    sby = data['stock_by_key']
    result = [c for c in pool if (s := sby.get((c, td))) and s['turnover_rate'] and 5 <= float(s['turnover_rate']) <= 15]
    return result[:k]


ALL_PICKERS = [
    ('low_mcap', pick_low_mcap),
    ('high_mcap', pick_high_mcap),
    ('ma20_above', pick_ma20_above),
    ('no_overbought', pick_no_overbought),
    ('volume_breakout', pick_volume_breakout),
    ('gap_up', pick_gap_up),
    ('first_board', pick_first_board),
    ('board2', pick_board2),
    ('mid_turnover', pick_mid_turnover),
]


# ================================================================
# 数据驱动的组合筛选器
# 不猜哪个策略好，而是用历史数据自动筛选
# ================================================================

def run_combined(data, top_k=3, use_pickers=None):
    """运行投票法组合策略"""
    all_dates = data['dates'][:-2]
    zbd = data['zt_by_date']
    sby = data['stock_by_key']
    picks = ALL_PICKERS if use_pickers is None else use_pickers
    
    results = []
    for td in all_dates:
        pool = zbd.get(td, [])
        if not pool:
            continue
        
        # 每个策略投票
        votes = Counter()
        for pname, pfn in picks:
            selected = pfn(pool, td, data, k=3)
            for c in selected:
                votes[c] += 1
        
        if not votes:
            continue
        
        # 按得票排序
        top = [c for c, _ in votes.most_common(top_k)]
        
        d1, d2 = get_next_dates(td, all_dates)
        if not d1 or not d2:
            continue
        
        for code in top:
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
                'votes': votes.get(code, 0),
                'trade_return': ret,
            })
    
    return results


def analyze(results):
    rets = [r['trade_return'] for r in results]
    if not rets:
        return {'count': 0}
    wins = sum(1 for v in rets if v > 0)
    return {
        'count': len(rets),
        'win_rate': wins / len(rets) * 100,
        'avg_return': sum(rets) / len(rets),
        'max_win': max(rets),
        'max_loss': min(rets),
        'big_win_pct': sum(1 for v in rets if v > 5) / len(rets) * 100,
        'big_loss_pct': sum(1 for v in rets if v < -3) / len(rets) * 100,
    }


# ================================================================
# 逐策略剔除实验（看去掉哪个策略后胜率最高）
# ================================================================

if __name__ == '__main__':
    data = load_all_data()
    
    print("\n" + "="*80)
    print("策略组合探索 v5.0 — 投票法找出最佳组合")
    print("="*80)
    
    # 1. 全策略投票（9个策略各选3只，看重合度）
    print("\n1️⃣  全策略投票结果:")
    print(f"{'票数':>6} {'策略数':>8} {'交易数':>8} {'胜率':>8} {'平均':>8}")
    
    for k in [5, 3, 2, 1]:
        results = run_combined(data, top_k=k)
        stats = analyze(results)
        print(f"{'top'+str(k):>6} {9:>8} {stats['count']:>8} {stats['win_rate']:>7.1f}% {stats['avg_return']:>+7.2f}%")
    
    # 2. 逐个剔除策略（看去掉哪个效果最好）
    print("\n2️⃣  逐个剔除实验（top3）:")
    print(f"{'剔除策略':>15} {'交易数':>8} {'胜率':>8} {'平均':>8} {'大赢%':>8} {'大亏%':>8}")
    
    base_results = run_combined(data, top_k=3, use_pickers=ALL_PICKERS)
    base_stats = analyze(base_results)
    print(f"{'(全量)':>15} {base_stats['count']:>8} {base_stats['win_rate']:>7.1f}% "
          f"{base_stats['avg_return']:>+7.2f}% {base_stats['big_win_pct']:>7.1f}% {base_stats['big_loss_pct']:>7.1f}%")
    
    best_win = 0
    best_removed = None
    for pname, pfn in ALL_PICKERS:
        subset = [(n, fn) for n, fn in ALL_PICKERS if n != pname]
        r = run_combined(data, top_k=3, use_pickers=subset)
        s = analyze(r)
        print(f"{pname:>15} {s['count']:>8} {s['win_rate']:>7.1f}% "
              f"{s['avg_return']:>+7.2f}% {s['big_win_pct']:>7.1f}% {s['big_loss_pct']:>7.1f}%")
        if s['win_rate'] > best_win:
            best_win = s['win_rate']
            best_removed = pname
    
    print(f"\n最佳剔除: {best_removed} (胜率{best_win:.1f}%)")
    
    # 3. 两两组合（随机遍历关键配对）
    print("\n3️⃣  少量最佳策略组合（top3）:")
    # 尝试最佳3个策略的组合
    combos = [
        (['low_mcap', 'high_mcap', 'ma20_above', 'volume_breakout', 'gap_up', 'first_board'], 
         '剔除board2+mid_turnover+no_overbought'),
        (['first_board', 'board2', 'volume_breakout', 'gap_up', 'ma20_above'], 
         '进攻型(首板+连板+放量+高开+MA20)'),
        (['low_mcap', 'no_overbought', 'ma20_above', 'mid_turnover'], 
         '防守型(小市值+非追高+MA20+中换手)'),
        (['volume_breakout', 'gap_up', 'first_board', 'ma20_above'], 
         '精华4(放量+高开+首板+MA20)'),
        (['first_board', 'ma20_above', 'low_mcap'], 
         '核心3(首板+MA20+低市值)'),
        (['volume_breakout', 'gap_up', 'board2'], 
         '连板3(放量+高开+2连板)'),
    ]
    for pickers, label in combos:
        subset = [(n, fn) for n, fn in ALL_PICKERS if n in pickers]
        r = run_combined(data, top_k=3, use_pickers=subset)
        s = analyze(r)
        print(f"{label:>30} {s['count']:>4}次 {s['win_rate']:>5.1f}% 平均{s['avg_return']:+.2f}% 大赢{s['big_win_pct']:.0f}% 大亏{s['big_loss_pct']:.0f}%")
    
    # 4. 只看投票重合度高的票（票数>=4）
    print("\n4️⃣  高共识票（票数过滤）:")
    for min_votes in [4, 5, 6, 7, 8, 9]:
        results = run_combined(data, top_k=10)  # 多取一些，后面只保留高票的
        filtered = [r for r in results if r.get('votes', 0) >= min_votes]
        if filtered:
            s = analyze(filtered)
            print(f"  ≥{min_votes}票: {s['count']:>4}次 {s['win_rate']:>5.1f}% 平均{s['avg_return']:+.2f}%")
    
    # 5. 最佳单一策略测试
    print("\n5️⃣  各策略单独评比（选最佳策略）:")
    for pname, pfn in ALL_PICKERS:
        r = run_combined(data, top_k=3, use_pickers=[(pname, pfn)])
        s = analyze(r)
        print(f"  {pname:>20}: {s['count']:>4}次 胜率{s['win_rate']:>5.1f}% 平均{s['avg_return']:+.2f}%")
PYEOF
