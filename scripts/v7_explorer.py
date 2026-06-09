#!/usr/bin/env python3
"""
v7 — 暴力模式搜索器

不猜战法，让数据说话。
搜索各种K线形态组合（量价、均线、涨跌序列等）对T+1开→T+2开的预测能力。
"""
import sys, os, json, time, math
from datetime import datetime, timedelta
from typing import List, Dict, Tuple
from collections import defaultdict, Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.dao import get_db

# ================================================================
# 数据加载
# ================================================================

def load_data():
    db = get_db()

    all_rows = db.fetchall(
        "SELECT code, trade_date, open, close, high, low, volume, amount, "
        "change_pct, total_market_cap, turnover_rate "
        "FROM stock_daily WHERE trade_date BETWEEN '20260105' AND '20260601' ORDER BY trade_date")

    # 股票按code分组
    by_code = defaultdict(list)
    for r in all_rows:
        by_code[r['code']].append(r)
    for code, rows in by_code.items():
        rows.sort(key=lambda x: x['trade_date'])

    date_list = sorted(set(r['trade_date'] for r in all_rows))
    date_set = set(date_list)

    return all_rows, by_code, date_list, date_set


def future_dates(td, n, date_list, date_set):
    idx = date_list.index(td) if td in date_set else -1
    if idx < 0:
        return []
    return date_list[idx + 1:idx + 1 + n]


# ================================================================
# 特征工程
# ================================================================

def make_features(code, td, rows, by_code, date_list, date_set):
    """为某日某股生成所有特征"""
    # 当前日数据
    key = (code, td)
    # 找到该股在已排序rows中的位置
    cro = by_code.get(code, [])
    idx = -1
    for i, r in enumerate(cro):
        if r['trade_date'] == td:
            idx = i
            break
    if idx < 0:
        return None

    cur = cro[idx]
    close = float(cur['close'] or 0)
    open_ = float(cur['open'] or 0)
    high = float(cur['high'] or 0)
    low = float(cur['low'] or 0)
    vol = float(cur['volume'] or 0)
    amount = float(cur['amount'] or 0)
    chg = float(cur['change_pct'] or 0)
    tr = float(cur['turnover_rate'] or 0)
    mcap = float(cur['total_market_cap'] or 0)

    if idx < 20:
        return None

    feats = {}
    feats['code'] = code
    feats['td'] = td
    feats['close'] = close
    feats['open'] = open_
    feats['chg'] = chg
    feats['turnover'] = tr
    feats['mcap'] = mcap
    feats['vol'] = vol
    feats['amount'] = amount

    # 前20日close
    prev_20 = [float(cro[j]['close']) for j in range(max(0, idx - 20), idx) if cro[j]['close'] > 0]

    # 均线
    if len(prev_20) >= 5:
        feats['ma5'] = sum(prev_20[-5:]) / 5
        feats['dev_ma5'] = (close - feats['ma5']) / feats['ma5'] * 100
    if len(prev_20) >= 10:
        feats['ma10'] = sum(prev_20[-10:]) / 10
        feats['dev_ma10'] = (close - feats['ma10']) / feats['ma10'] * 100
    if len(prev_20) >= 20:
        feats['ma20'] = sum(prev_20[-20:]) / 20
        feats['dev_ma20'] = (close - feats['ma20']) / feats['ma20'] * 100

    # 实体大小
    feats['body'] = abs(close - open_)
    feats['body_pct'] = abs(close - open_) / open_ * 100 if open_ > 0 else 0
    feats['is_red'] = 1 if close >= open_ else 0

    # 上影线/下影线
    feats['upper_shadow'] = (high - max(close, open_)) / (high - low) * 100 if high > low else 0
    feats['lower_shadow'] = (min(close, open_) - low) / (high - low) * 100 if high > low else 0

    # 昨日涨跌幅
    if idx > 0:
        prev_chg = float(cro[idx - 1]['change_pct'] or 0)
        prev_vol = float(cro[idx - 1]['volume'] or 0)
        feats['prev_chg'] = prev_chg
        feats['vol_ratio'] = vol / prev_vol if prev_vol > 0 else 0
    else:
        feats['prev_chg'] = 0
        feats['vol_ratio'] = 0

    # 前5日均量
    prev_vols = [float(cro[j]['volume']) for j in range(max(0, idx - 7), idx) if cro[j]['volume'] > 0]
    if len(prev_vols) >= 5:
        feats['avg_vol_5'] = sum(prev_vols[-5:]) / 5
        feats['vol_ratio_5'] = vol / feats['avg_vol_5'] if feats['avg_vol_5'] > 0 else 0
    else:
        feats['avg_vol_5'] = 0
        feats['vol_ratio_5'] = 0

    # 近期涨跌序列
    prev_chgs = [float(cro[j]['change_pct'] or 0) for j in range(max(0, idx - 5), idx)]
    feats['n_red_prev5'] = sum(1 for v in prev_chgs if v > 0)
    feats['pct_chg_prev5'] = sum(prev_chgs)

    # 是否连阳
    feats['consecutive_red'] = 0
    for j in range(idx - 1, max(idx - 6, -1), -1):
        if float(cro[j]['change_pct'] or 0) > 0:
            feats['consecutive_red'] += 1
        else:
            break

    # 是否涨停
    feats['is_zt'] = 1 if chg >= 9.5 else 0

    # 是否20cm
    feats['is_20cm'] = 1 if chg >= 19.0 else 0

    # 涨停类型（首板/连板）
    if feats['is_zt']:
        board_cnt = 1
        for j in range(idx - 1, max(idx - 6, -1), -1):
            if float(cro[j]['change_pct'] or 0) >= 9.5:
                board_cnt += 1
            else:
                break
        feats['board_cnt'] = board_cnt
    else:
        feats['board_cnt'] = 0

    # 一阳穿三线
    if len(prev_20) >= 20 and open_ > 0:
        ma5 = feats.get('ma5', 0)
        ma10 = feats.get('ma10', 0)
        ma20 = feats.get('ma20', 0)
        if ma5 and ma10 and ma20:
            feats['yycx'] = 1 if (open_ < ma5 < close and open_ < ma10 < close and open_ < ma20 < close) else 0
        else:
            feats['yycx'] = 0
    else:
        feats['yycx'] = 0

    # 跳空高开
    if idx > 0:
        prev_close = float(cro[idx - 1]['close'] or 0)
        feats['gap_up'] = (open_ - prev_close) / prev_close * 100 if prev_close > 0 else 0
        feats['gap_down'] = (prev_close - open_) / prev_close * 100 if prev_close > 0 else 0
    else:
        feats['gap_up'] = 0
        feats['gap_down'] = 0

    # 振幅
    feats['amplitude'] = (high - low) / low * 100 if low > 0 else 0

    # 未来收益
    fds = future_dates(td, 5, date_list, date_set)
    if len(fds) >= 2:
        d1 = fds[0]
        d2 = fds[1]
        for r2 in by_code.get(code, []):
            if r2['trade_date'] == d1:
                feats['d1_open'] = float(r2['open'] or 0)
                feats['d1_close'] = float(r2['close'] or 0)
            if r2['trade_date'] == d2:
                feats['d2_open'] = float(r2['open'] or 0)
        if feats.get('d1_open', 0) > 0 and feats.get('d2_open', 0) > 0:
            feats['ret'] = (feats['d2_open'] - feats['d1_open']) / feats['d1_open'] * 100
            return feats

    return None


# ================================================================
# 单条件搜索：找出最佳阈值
# ================================================================

def search_threshold(feats_list, feat_name, lo, hi, steps=20):
    """在范围内搜索最佳阈值"""
    best = {'threshold': None, 'win_rate': 0, 'count': 0, 'avg': 0, 'direction': None}

    for i in range(steps + 1):
        t = lo + (hi - lo) * i / steps
        # 大于等于
        cnt = 0
        wins = 0
        rets = []
        for f in feats_list:
            v = f.get(feat_name)
            if v is not None and v >= t:
                cnt += 1
                rets.append(f['ret'])
                if f['ret'] > 0:
                    wins += 1
        if cnt >= 30:
            wr = wins / cnt * 100
            avg = sum(rets) / cnt
            if wr > best['win_rate']:
                best = {'threshold': t, 'win_rate': wr, 'count': cnt, 'avg': avg,
                        'direction': '>='}

        # 小于等于
        cnt = 0
        wins = 0
        rets = []
        for f in feats_list:
            v = f.get(feat_name)
            if v is not None and v <= t:
                cnt += 1
                rets.append(f['ret'])
                if f['ret'] > 0:
                    wins += 1
        if cnt >= 30:
            wr = wins / cnt * 100
            avg = sum(rets) / cnt
            if wr > best['win_rate']:
                best = {'threshold': t, 'win_rate': wr, 'count': cnt, 'avg': avg,
                        'direction': '<='}

    return best


def search_discrete(feats_list, feat_name, values):
    """搜索离散值"""
    best = {'value': None, 'win_rate': 0, 'count': 0, 'avg': 0}

    for v in values:
        cnt = 0
        wins = 0
        rets = []
        for f in feats_list:
            if f.get(feat_name) == v:
                cnt += 1
                rets.append(f['ret'])
                if f['ret'] > 0:
                    wins += 1
        if cnt >= 20:
            wr = wins / cnt * 100
            avg = sum(rets) / cnt
            if wr > best['win_rate']:
                best = {'value': v, 'win_rate': wr, 'count': cnt, 'avg': avg}

    return best


# ================================================================
# 双条件组合搜索
# ================================================================

def search_two_conditions(feats_list, feat1, feat2):
    """搜索两个特征的最佳组合"""
    candidates = []
    f1_vals = [f.get(feat1) for f in feats_list if f.get(feat1) is not None]
    f2_vals = [f.get(feat2) for f in feats_list if f.get(feat2) is not None]

    if not f1_vals or not f2_vals:
        return []

    # 找f1的分位值
    f1_sorted = sorted(f1_vals)
    f2_sorted = sorted(f2_vals)

    thresholds = []
    for p in [10, 20, 30, 40, 50, 60, 70, 80, 90]:
        thresholds.append(f1_sorted[int(len(f1_sorted) * p / 100)])

    f2_thresholds = []
    for p in [10, 20, 30, 40, 50, 60, 70, 80, 90]:
        f2_thresholds.append(f2_sorted[int(len(f2_sorted) * p / 100)])

    for t1 in thresholds:
        for t2 in f2_thresholds:
            cnt = 0
            wins = 0
            rets = []
            for f in feats_list:
                if f.get(feat1) is not None and f.get(feat2) is not None:
                    if f[feat1] >= t1 and f[feat2] >= t2:
                        cnt += 1
                        rets.append(f['ret'])
                        if f['ret'] > 0:
                            wins += 1
            if cnt >= 15:
                wr = wins / cnt * 100
                avg = sum(rets) / cnt
                if wr > 55:
                    candidates.append({
                        'feat1': feat1, 'th1': round(t1, 2), 'dir1': '>=',
                        'feat2': feat2, 'th2': round(t2, 2), 'dir2': '>=',
                        'count': cnt, 'win_rate': wr, 'avg': round(avg, 2)
                    })
            
            # 也试 and <= 组合
            cnt = 0
            wins = 0
            rets = []
            for f in feats_list:
                if f.get(feat1) is not None and f.get(feat2) is not None:
                    if f[feat1] <= t1 and f[feat2] <= t2:
                        cnt += 1
                        rets.append(f['ret'])
                        if f['ret'] > 0:
                            wins += 1
            if cnt >= 15:
                wr = wins / cnt * 100
                avg = sum(rets) / cnt
                if wr > 55:
                    candidates.append({
                        'feat1': feat1, 'th1': round(t1, 2), 'dir1': '<=',
                        'feat2': feat2, 'th2': round(t2, 2), 'dir2': '<=',
                        'count': cnt, 'win_rate': wr, 'avg': round(avg, 2)
                    })
            
            # 混合
            cnt = 0
            wins = 0
            rets = []
            for f in feats_list:
                if f.get(feat1) is not None and f.get(feat2) is not None:
                    if f[feat1] >= t1 and f[feat2] <= t2:
                        cnt += 1
                        rets.append(f['ret'])
                        if f['ret'] > 0:
                            wins += 1
            if cnt >= 15:
                wr = wins / cnt * 100
                avg = sum(rets) / cnt
                if wr > 55:
                    candidates.append({
                        'feat1': feat1, 'th1': round(t1, 2), 'dir1': '>=',
                        'feat2': feat2, 'th2': round(t2, 2), 'dir2': '<=',
                        'count': cnt, 'win_rate': wr, 'avg': round(avg, 2)
                    })

    candidates.sort(key=lambda x: x['win_rate'], reverse=True)
    return candidates[:10]


# ================================================================
# 主流程
# ================================================================

if __name__ == '__main__':
    print("加载数据...")
    all_rows, by_code, date_list, date_set = load_data()
    print(f"总行: {len(all_rows)}, 股票数: {len(by_code)}, 交易日: {len(date_list)}")

    print("生成特征...")
    all_feats = []
    count = 0
    for code, rows in by_code.items():
        for r in rows:
            td = r['trade_date']
            if td < '20260108' or td > '20260530':
                continue
            f = make_features(code, td, r, by_code, date_list, date_set)
            if f:
                all_feats.append(f)
            count += 1
            if count % 50000 == 0:
                print(f"  处理: {count}/497k")

    print(f"\n特征样本: {len(all_feats)}")

    # 基准：全样本胜率
    rets = [f['ret'] for f in all_feats]
    all_wr = sum(1 for v in rets if v > 0) / len(rets) * 100
    all_avg = sum(rets) / len(rets)
    print(f"全样本: N={len(rets)} 胜率{all_wr:.1f}% 平均{all_avg:+.2f}%")

    # ================================================================
    # 单条件搜索
    # ================================================================
    print("\n" + "=" * 80)
    print("单条件特征搜索（>=或<=阈值）")
    print("=" * 80)

    # 连续特征
    continuous_feats = [
        ('chg', -10, 20),
        ('turnover', 0, 50),
        ('dev_ma20', -20, 30),
        ('vol_ratio', 0, 20),
        ('vol_ratio_5', 0, 20),
        ('amplitude', 1, 20),
        ('body_pct', 0, 10),
        ('prev_chg', -10, 10),
        ('pct_chg_prev5', -20, 30),
        ('n_red_prev5', 0, 5),
        ('consecutive_red', 0, 5),
        ('gap_up', -5, 15),
    ]

    single_results = []
    for feat_name, lo, hi in continuous_feats:
        best = search_threshold(all_feats, feat_name, lo, hi)
        if best['win_rate'] > all_wr + 2 and best['count'] >= 30:
            single_results.append(best)
            single_results[-1]['feat_name'] = feat_name
            print(f"  {feat_name:>20} {best['direction']:>2} {best['threshold']:>8.2f}: "
                  f"N={best['count']:>5} 胜率{best['win_rate']:>5.1f}% 平均{best['avg']:>+7.2f}%")

    # 离散特征
    discrete_feats = [
        ('is_zt', [0, 1]),
        ('is_20cm', [0, 1]),
        ('yycx', [0, 1]),
        ('board_cnt', [0, 1, 2, 3, 4]),
    ]

    for feat_name, values in discrete_feats:
        best = search_discrete(all_feats, feat_name, values)
        if best['win_rate'] > all_wr + 2 and best['count'] >= 20:
            print(f"  {feat_name:>20} == {best['value']}: "
                  f"N={best['count']:>5} 胜率{best['win_rate']:>5.1f}% 平均{best['avg']:>+7.2f}%")

    # ================================================================
    # 双条件搜索
    # ================================================================
    print("\n" + "=" * 80)
    print("双条件组合搜索")
    print("=" * 80)

    pair_results = []
    feat_names = [f[0] for f in continuous_feats] + [f[0] for f in discrete_feats]
    searched = set()
    
    for i in range(len(feat_names)):
        for j in range(i + 1, len(feat_names)):
            f1, f2 = feat_names[i], feat_names[j]
            key = tuple(sorted([f1, f2]))
            if key in searched:
                continue
            searched.add(key)
            candidates = search_two_conditions(all_feats, f1, f2)
            if candidates:
                for c in candidates:
                    if c['win_rate'] >= 58 and c['count'] >= 15:
                        pair_results.append(c)

    pair_results.sort(key=lambda x: x['win_rate'], reverse=True)
    for p in pair_results[:30]:
        print(f"  ({p['feat1']:>15} {p['dir1']:>2} {p['th1']:>8.2f}) AND "
              f"({p['feat2']:>15} {p['dir2']:>2} {p['th2']:>8.2f}): "
              f"N={p['count']:>4} 胜率{p['win_rate']:>5.1f}% 平均{p['avg']:>+7.2f}%")

    # ================================================================
    # 九转序列（连续9日涨跌条件）
    # ================================================================
    print("\n" + "=" * 80)
    print("九转序列搜索（连续涨/跌N天反转）")
    print("=" * 80)

    for direction, label in [('up', '连涨'), ('down', '连跌')]:
        for n in [3, 4, 5, 6, 7, 8, 9]:
            cnt = 0
            wins = 0
            rets = []
            for f in all_feats:
                code = f['code']
                td = f['td']
                rows = by_code[code]
                idx = next((i for i, r in enumerate(rows) if r['trade_date'] == td), -1)
                if idx < 0:
                    continue
                if direction == 'up':
                    cond = all(float(rows[j]['change_pct'] or 0) > 0 for j in range(idx - n, idx) if j >= 0) if idx >= n else False
                    cond2 = float(rows[idx]['change_pct'] or 0) < 0  # 今天收阴
                else:
                    cond = all(float(rows[j]['change_pct'] or 0) < 0 for j in range(idx - n, idx) if j >= 0) if idx >= n else False
                    cond2 = float(rows[idx]['change_pct'] or 0) > 0  # 今天收阳
                if cond and cond2:
                    cnt += 1
                    rets.append(f['ret'])
                    if f['ret'] > 0:
                        wins += 1
            if cnt >= 15:
                wr = wins / cnt * 100
                avg = sum(rets) / cnt
                print(f"  {label}{n}天+反转: N={cnt:>4} 胜率{wr:>5.1f}% 平均{avg:>+7.2f}%")

    # ================================================================
    # 策略推导
    # ================================================================
    print("\n" + "=" * 80)
    print("策略推导")
    print("=" * 80)
    
    # 找出足够好的单条件
    good_singles = []
    for fn, lo, hi in continuous_feats:
        best = search_threshold(all_feats, fn, lo, hi)
        if best['win_rate'] >= 54 and best['count'] >= 30:
            good_singles.append((fn, best))
    
    for fn, values in discrete_feats:
        best = search_discrete(all_feats, fn, values)
        if best['win_rate'] >= 54 and best['count'] >= 20:
            good_singles.append((fn, best))

    print(f"有效单条件: {len(good_singles)}")
    
    # 三条件组合 = 过滤链
    print("\n三条件过滤链（从全样本逐步过滤，找出高胜率子集）:")
    
    # 用最好的几个条件逐步过滤
    filters = []
    
    # 1. 找出top 5 single features
    top5_fn = []
    for fn, lo, hi in continuous_feats:
        best = search_threshold(all_feats, fn, lo, hi)
        if best['win_rate'] >= all_wr + 1 and best['count'] >= 100:
            top5_fn.append((best['win_rate'], fn, best['direction'], best['threshold']))
    top5_fn.sort(reverse=True)
    
    # 逐步过滤
    for wr, fn, direction, th in top5_fn[:5]:
        cnt = 0
        for f in all_feats:
            v = f.get(fn)
            if v is not None:
                if direction == '>=' and v >= th:
                    cnt += 1
                elif direction == '<=' and v <= th:
                    cnt += 1
        filters.append((fn, direction, th, cnt))
    
    print(f"  初始全样本: N={len(all_feats)} 胜率{all_wr:.1f}%")
    remaining = list(all_feats)
    for fn, direction, th, expected_cnt in filters:
        filtered = [f for f in remaining if (
            f.get(fn) is not None and (
                (direction == '>=' and f[fn] >= th) or
                (direction == '<=' and f[fn] <= th)
            )
        )]
        remaining = filtered
        if not remaining:
            break
        r = [f['ret'] for f in remaining]
        wr = sum(1 for v in r if v > 0) / len(r) * 100
        avg = sum(r) / len(r)
        print(f"  + {fn} {direction} {th:.2f}: N={len(remaining):>5} 胜率{wr:>5.1f}% 平均{avg:>+7.2f}%")

    print("\n===== 搜索完成 =====")

PYEOF
