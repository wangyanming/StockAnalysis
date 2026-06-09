#!/usr/bin/env python3
"""
70%胜率方案 v3.0 — 基于评分系统优化

核心思路：不抛弃评分系统，而是改良它。
问题诊断：当前评分区分度不足（大赢大亏的维度均值几乎一样）
整改方向：
  1. 趋势维度权重下调（当前无区分度，且拖累过多）
  2. 正负收益的"剪刀差维度"加分：统计大赢票与大亏票在每个维度的差距
  3. 增加"标的稀缺度"加分：当日同板块涨停票少=好
  4. 给连板票额外加分（数据显示有一定持续性）
  5. 增加首日涨停封印（排除已经炒作过的老面孔）
"""

import sys, os, time, logging
from datetime import datetime, timedelta
from typing import List, Dict, Tuple
from collections import defaultdict, Counter
import random

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.dao import get_db

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# ================================================================
# 数据准备
# ================================================================

def get_trade_dates() -> List[str]:
    db = get_db()
    rows = db.fetchall(
        "SELECT DISTINCT trade_date FROM stock_daily WHERE trade_date >= '20260105' AND trade_date <= '20260601' ORDER BY trade_date")
    return [r['trade_date'] for r in rows if datetime.strptime(r['trade_date'], '%Y%m%d').weekday() < 5]


def get_prev_close(code: str, td: str) -> float:
    db = get_db()
    r = db.fetchone(
        "SELECT close FROM stock_daily WHERE code=%s AND trade_date<%s ORDER BY trade_date DESC LIMIT 1",
        (code, td))
    return float(r['close']) if r and r['close'] else 0


def calc_ma_prev(code: str, td: str, max_p: int) -> Dict[int, float]:
    db = get_db()
    rows = db.fetchall(
        'SELECT close FROM stock_daily WHERE code=%s AND trade_date<%s ORDER BY trade_date DESC LIMIT %s',
        (code, td, max_p))
    closes = [float(r['close']) for r in rows if r['close'] > 0]
    result = {}
    for p in [5, 10, 20, 30, 60]:
        if len(closes) >= p:
            result[p] = sum(closes[:p]) / p
        else:
            result[p] = None
    return result


# ================================================================
# 改进版评分系统
# ================================================================

def score_v3(code: str, name: str, td: str, pool_size: int) -> Tuple[float, Dict]:
    """
    新一代评分系统 v3.0
    
    评分维度（总分100）：
    - 资金维度 30分：换手率、量比、成交额
    - 稀缺维度 20分：当日同板块涨停数、是否首板
    - 趋势维度 20分：均线排列、距MA20位置
    - 连板维度 15分：连板数、最近涨停频率
    - 大盘维度 15分：当日大盘环境
    """
    db = get_db()
    score = 0
    dims = {}
    reasons = []
    
    # 获取当日数据
    row = db.fetchone(
        "SELECT * FROM stock_daily WHERE code=%s AND trade_date=%s", (code, td))
    if not row:
        return 0, {}
    
    close = float(row['close'])
    change_pct = float(row['change_pct'] or 0)
    turnover = float(row['turnover_rate'] or 0)
    amount = float(row['amount'] or 0)
    mcap = float(row['total_market_cap'] or 0)
    sh = db.fetchone(
        "SELECT change_pct FROM stock_daily WHERE code='000001' AND trade_date=%s", (td))
    sh_chg = float(sh['change_pct']) if sh and sh['change_pct'] else 0
    
    mas = calc_ma_prev(code, td, 60)
    
    # ─────────────── 1. 资金维度（30分）───────────────
    f_score = 0
    
    # 换手率评分（5-15%最佳）
    if 5 <= turnover <= 15:
        f_score += 12
    elif 3 <= turnover < 5:
        f_score += 8
    elif 15 < turnover <= 25:
        f_score += 8
    elif turnover < 3:
        f_score += 4
    else:
        f_score += 5
    
    # 量比（当日量/前5日均量）最佳范围2-5
    prev5 = db.fetchall(
        "SELECT AVG(amount) as avg FROM stock_daily WHERE code=%s AND trade_date>=%s AND trade_date<%s AND amount>0",
        (code, (datetime.strptime(td, '%Y%m%d') - timedelta(days=12)).strftime('%Y%m%d'), td))
    avg_amt = float(prev5[0]['avg']) if prev5 and prev5[0]['avg'] and float(prev5[0]['avg']) > 0 else 0
    if avg_amt > 0:
        vol_ratio = amount / avg_amt
        if 2 <= vol_ratio <= 5:
            f_score += 10
        elif 1.5 <= vol_ratio < 2:
            f_score += 7
        elif 5 < vol_ratio <= 8:
            f_score += 7
        elif vol_ratio < 1.5:
            f_score += 4
        else:
            f_score += 5
    else:
        f_score += 5
    
    # 成交额（>1亿但不要太大）
    if 100000000 <= amount <= 1000000000:
        f_score += 8
    elif amount < 100000000:
        f_score += 4
    else:  # >10亿
        f_score += 5
    
    f_score = min(f_score, 30)
    dims['资金'] = f_score
    if f_score >= 25: reasons.append(f'资金活跃({f_score}/30)')
    elif f_score >= 20: reasons.append(f'资金一般({f_score}/30)')
    
    # ─────────────── 2. 稀缺维度（20分）───────────────
    s_score = 0
    
    # 同板块涨停数（稀缺度）
    # 没有板块数据，用"全市场涨停总数"替代
    zt_count = db.fetchone(
        "SELECT COUNT(*) as cnt FROM stock_daily WHERE trade_date=%s AND change_pct >= 9.5", (td,))
    zt_total = zt_count['cnt'] if zt_count else 100
    
    # 涨停潮时稀缺度低
    if zt_total <= 50:
        s_score += 12
    elif zt_total <= 80:
        s_score += 9
    elif zt_total <= 120:
        s_score += 6
    else:
        s_score += 3
    
    # 是否首板（近5日首次涨停）
    recent_zt = db.fetchone(
        "SELECT COUNT(*) as cnt FROM stock_daily WHERE code=%s AND trade_date>=%s AND trade_date<%s AND change_pct >= 9.5",
        (code, (datetime.strptime(td, '%Y%m%d') - timedelta(days=10)).strftime('%Y%m%d'), td))
    if recent_zt and recent_zt['cnt'] == 0:
        s_score += 8  # 首板加分
        reasons.append('首板')
    elif recent_zt and recent_zt['cnt'] <= 2:
        s_score += 4
    
    dims['稀缺'] = s_score
    
    # ─────────────── 3. 趋势维度（20分）───────────────
    t_score = 0
    
    # 核心：距离MA20的位置
    if mas[20] and mas[20] > 0:
        deviation = (close - mas[20]) / mas[20] * 100
        # 关键观察：偏离-5%~+10%最佳
        if -2 <= deviation <= 5:
            t_score += 12  # 最理想：刚突破MA20或回踩MA20
        elif -5 <= deviation < -2:
            t_score += 8   # 稍弱但可接受
        elif 5 < deviation <= 10:
            t_score += 8   # 合理的上涨趋势
        elif 10 < deviation <= 15:
            t_score += 5   # 偏高了
        elif deviation > 15:
            t_score += 2   # 追高危险
        elif deviation < -5:
            t_score += 4   # MA20下方，弱
    else:
        t_score += 5
    
    # 均线排列加分
    if mas[5] and mas[10] and mas[20]:
        if mas[5] > mas[10] > mas[20]:
            t_score += 8
        elif mas[5] > mas[10]:
            t_score += 4
    
    t_score = min(t_score, 20)
    dims['趋势'] = t_score
    if t_score >= 15: reasons.append('趋势良好')
    
    # ─────────────── 4. 连板维度（15分）───────────────
    c_score = 0
    
    # 查连板数
    prev_zs = db.fetchall(
        "SELECT trade_date, change_pct FROM stock_daily WHERE code=%s AND trade_date>=%s AND trade_date<%s ORDER BY trade_date DESC LIMIT 5",
        (code, (datetime.strptime(td, '%Y%m%d') - timedelta(days=8)).strftime('%Y%m%d'), td))
    
    board_count = 1  # 今天涨停算1
    prev_chgs = [float(r['change_pct']) for r in prev_zs if r['change_pct']]
    for chg in prev_chgs:
        if chg >= 9.5:
            board_count += 1
        else:
            break
    
    if board_count == 1:
        c_score += 8  # 首板，想象空间大
        reasons.append('首板')
    elif board_count == 2:
        c_score += 12  # 2连板，最好的状态
        reasons.append('2连板')
    elif board_count == 3:
        c_score += 8  # 3连板，还可以但要谨慎
        reasons.append('3连板')
    elif board_count >= 4:
        c_score += 3  # 高位连板，风险大
    
    # 涨停前是否缩量调整
    prev_amts = []
    for rz in prev_zs[:5]:
        rz_date = str(rz['trade_date']).strip() if hasattr(rz['trade_date'], 'strip') else str(rz['trade_date'])
        amt_r = db.fetchone(
            "SELECT amount FROM stock_daily WHERE code=%s AND trade_date=%s",
            (code, rz_date))
        if amt_r and amt_r['amount']:
            prev_amts.append(float(amt_r['amount']))
    if len(prev_amts) >= 3:
        latest3 = sum(prev_amts[:3])
        earlier3 = sum(prev_amts[3:6]) if len(prev_amts) >= 6 else latest3
        if earlier3 > 0 and latest3 < earlier3 * 0.7:
            c_score += 3  # 缩量调整后涨停，典型洗盘形态
            reasons.append('缩量洗盘')
    
    c_score = min(c_score, 15)
    dims['连板'] = c_score
    
    # ─────────────── 5. 大盘维度（15分）───────────────
    m_score = 0
    
    # 大盘上涨日加分
    if sh_chg >= 0.5:
        m_score += 8
    elif sh_chg >= -0.5:
        m_score += 5
    elif sh_chg >= -1.5:
        m_score += 2
    else:
        m_score -= 5  # 大盘大跌，减分
    
    # 涨停潮加分（说明市场情绪好）
    if zt_total >= 100:
        m_score += 7
    elif zt_total >= 60:
        m_score += 4
    elif zt_total >= 30:
        m_score += 2
    else:
        m_score += 0
    
    m_score = max(-5, min(m_score, 15))
    dims['大盘'] = m_score
    if m_score >= 10: reasons.append('大盘环境好')
    
    # ─────────────── 总分 ───────────────
    total = f_score + s_score + t_score + c_score + m_score
    total = max(0, total)  # 最低0分
    
    return total, {
        'score': total,
        'dims': dims,
        'reasons': reasons,
        'change_pct': change_pct,
        'turnover': turnover,
        'zt_total': zt_total,
        'mcap': mcap,
        'board_count': board_count,
        'ma20_deviation': (close - mas[20]) / mas[20] * 100 if mas[20] and mas[20] > 0 else None,
    }


# ================================================================
# 回测引擎
# ================================================================

def run_v3_backtest(start='20260105', end='20260601', top_k=5) -> Dict:
    """
    运行v3改良版回测
    
    参数: top_k = 每天选前几名买入（模拟买入N只）
    """
    all_dates = get_trade_dates()
    dates = [d for d in all_dates if start <= d <= end]
    if len(dates) > 2:
        dates = dates[:-2]  # 去掉最后2天（需要T+2数据）
    
    db = get_db()
    results = []
    
    total_start = time.time()
    for idx, td in enumerate(dates):
        if (idx + 1) % 20 == 0:
            elapsed = time.time() - total_start
            logger.info(f"  [{idx+1}/{len(dates)}] 耗时{elapsed:.0f}s, 已采{len(results)}条")
        
        # 获取涨停池
        pool = db.fetchall(
            "SELECT code, name, change_pct, total_market_cap FROM stock_daily WHERE trade_date=%s AND change_pct >= 9.5",
            (td,))
        if not pool:
            continue
        
        pool_size = len(pool)
        
        # 评分排序
        scored = []
        for p in pool:
            score, detail = score_v3(p['code'], p['name'], td, pool_size)
            if score > 0:
                scored.append((p['code'], p['name'], score, detail))
        
        scored.sort(key=lambda x: x[2], reverse=True)
        picks = scored[:top_k]
        
        if not picks:
            continue
        
        # T+1 / T+2
        next_date, next2_date = None, None
        for d in all_dates:
            if d > td:
                if not next_date:
                    next_date = d
                elif not next2_date:
                    next2_date = d
                    break
        
        if not next_date or not next2_date:
            continue
        
        for code, name, score, detail in picks:
            t1 = db.fetchone("SELECT open FROM stock_daily WHERE code=%s AND trade_date=%s", (code, next_date))
            t2 = db.fetchone("SELECT open FROM stock_daily WHERE code=%s AND trade_date=%s", (code, next2_date))
            if not t1 or not t2 or not t1['open'] or not t2['open']:
                continue
            buy_price = float(t1['open'])
            sell_price = float(t2['open'])
            if buy_price <= 0:
                continue
            trade_return = round((sell_price - buy_price) / buy_price * 100, 2)
            
            results.append({
                'trade_date': td,
                'code': code,
                'name': name,
                'score': score,
                'dims': detail['dims'],
                'board_count': detail['board_count'],
                'zt_total': detail['zt_total'],
                'ma20_dev': detail.get('ma20_deviation'),
                'trade_return': trade_return,
            })
    
    # 统�的
    rets = [r['trade_return'] for r in results]
    wins = sum(1 for v in rets if v > 0)
    
    stats = {
        'count': len(rets),
        'win_rate': wins / len(rets) * 100 if rets else 0,
        'avg_return': sum(rets) / len(rets) if rets else 0,
        'max_win': max(rets) if rets else 0,
        'max_loss': min(rets) if rets else 0,
        'big_win': sum(1 for v in rets if v > 5) if rets else 0,
        'big_win_pct': sum(1 for v in rets if v > 5) / len(rets) * 100 if rets else 0,
        'big_loss': sum(1 for v in rets if v < -3) if rets else 0,
        'big_loss_pct': sum(1 for v in rets if v < -3) / len(rets) * 100 if rets else 0,
        'results': results,
    }
    
    return stats


# ================================================================
# 多版本对比
# ================================================================

if __name__ == '__main__':
    logger.info("=== v3.0 回测开始 ===")
    
    versions = {
        'v3_top5': 5,
        'v3_top3': 3,
        'v3_top2': 2,
        'v3_top1': 1,
    }
    
    all_stats = []
    for ver, top_k in versions.items():
        logger.info(f"\n运行 {ver} (top_k={top_k})")
        s = run_v3_backtest(top_k=top_k)
        all_stats.append((ver, s))
        logger.info(f"  {ver}: {s['count']}次, 胜率{s['win_rate']:.1f}%, 平均{s['avg_return']:+.2f}%")
    
    # 排名
    print("\n" + "="*80)
    print("v3.0 结果汇总")
    print("="*80)
    print(f"{'版本':>10} {'交易数':>8} {'胜率':>8} {'平均收益':>10} {'大赢>5%':>8} {'大亏<-3%':>8} {'最大获胜':>10} {'最大亏损':>10}")
    print("-"*80)
    for ver, s in all_stats:
        print(f"{ver:>10} {s['count']:>8} {s['win_rate']:>7.1f}% {s['avg_return']:>+9.2f}% "
              f"{s['big_win_pct']:>7.1f}% {s['big_loss_pct']:>7.1f}% "
              f"{s['max_win']:>+9.2f}% {s['max_loss']:>+9.2f}%")
    
    # 保存详细结果
    import json
    output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'v3_backtest_results.json')
    save_data = {ver: {
        'stats': {k: v for k, v in stats.items() if k != 'results'},
        'samples': stats['results'][:500]  # 只保存前500条
    } for ver, stats in all_stats}
    with open(output_path, 'w') as f:
        json.dump(save_data, f, indent=2, ensure_ascii=False)
    logger.info(f"\n结果已保存: {output_path}")
