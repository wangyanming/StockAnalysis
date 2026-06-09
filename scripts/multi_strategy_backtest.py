#!/usr/bin/env python3
"""
多策略回测引擎 v2.0 — 目标胜率70%
每个策略独立回测，结果写入 backtest_multi_strategies 表

交易周期: T日收盘选股 → T+1开盘买入 → T+2开盘卖出
数据源: stock_daily, daily_limit_up
"""

import sys, os, time, json, logging
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Callable
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.dao import get_db

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# ================================================================
# 工具函数
# ================================================================

def get_trade_dates() -> List[str]:
    """获取交易日期列表（含所有数据）"""
    db = get_db()
    rows = db.fetchall(
        "SELECT DISTINCT trade_date FROM stock_daily WHERE trade_date >= '20260105' AND trade_date <= '20260601' ORDER BY trade_date")
    # 过滤周末
    dates = []
    for r in rows:
        dt = datetime.strptime(r['trade_date'], '%Y%m%d')
        if dt.weekday() < 5:
            dates.append(r['trade_date'])
    return dates


def calc_ma_prev(code: str, td: str, periods: List[int]) -> Dict[int, float]:
    """用trad_date之前的数据算MA（不含当天）"""
    db = get_db()
    max_p = max(periods)
    rows = db.fetchall(
        'SELECT close FROM stock_daily WHERE code=%s AND trade_date<%s ORDER BY trade_date DESC LIMIT %s',
        (code, td, max_p))
    closes = [float(r['close']) for r in rows if r['close'] > 0]
    result = {}
    for p in periods:
        if len(closes) >= p:
            result[p] = sum(closes[:p]) / p
        else:
            result[p] = None
    return result


def get_zt_count(td: str) -> int:
    """当日涨停家数"""
    db = get_db()
    r = db.fetchone("SELECT COUNT(*) as cnt FROM stock_daily WHERE trade_date=%s AND change_pct >= 9.5", (td,))
    return r['cnt'] if r else 0


def get_sh_change(td: str) -> float:
    """当日大盘涨跌幅"""
    db = get_db()
    r = db.fetchone("SELECT change_pct FROM stock_daily WHERE code='000001' AND trade_date=%s", (td,))
    return float(r['change_pct']) if r else 0


def get_industry(code: str, td: str) -> str:
    """查板块"""
    db = get_db()
    r = db.fetchone("SELECT industry FROM stock_daily WHERE code=%s AND trade_date=%s", (code, td))
    return ''


# ================================================================
# 基础数据：获取每日候选池（当日涨停股）
# ================================================================

def get_daily_pool(td: str) -> List[Dict]:
    """获取当日涨停的所有股票"""
    db = get_db()
    rows = db.fetchall("""
        SELECT s.code, s.name, s.close, s.change_pct,
               s.total_market_cap, s.turnover_rate, s.amount
        FROM stock_daily s
        WHERE s.trade_date=%s AND s.change_pct >= 9.5
        ORDER BY s.change_pct DESC
    """, (td,))
    
    pool = []
    for r in rows:
        d = dict(r)
        pool.append(d)
    return pool


# ================================================================
# 各策略的选股函数 — 输入(涨停候选池, 交易日) → 输出(选中的代码列表)
# ================================================================

# -------- 策略1: 最低市值（小票连板概率高） --------
def strategy_lowest_mcap(pool: List[Dict], td: str) -> List[str]:
    """选市值最小的N只涨停股"""
    sorted_pool = sorted(pool, key=lambda x: float(x['total_market_cap'] or 9999999999))
    return [p['code'] for p in sorted_pool[:5]]


# -------- 策略2: 最高换手率（换手充分的涨停） --------
def strategy_highest_turnover(pool: List[Dict], td: str) -> List[str]:
    sorted_pool = sorted(pool, key=lambda x: float(x['turnover_rate'] or 0), reverse=True)
    return [p['code'] for p in sorted_pool[:5]]


# -------- 策略3: 首板（近5日首次涨停） --------
def strategy_first_board(pool: List[Dict], td: str) -> List[str]:
    """选近5日内首次涨停的股票"""
    db = get_db()
    results = []
    for p in pool:
        code = p['code']
        # 查近5日其他涨停
        rows = db.fetchall(
            "SELECT COUNT(*) as cnt FROM stock_daily WHERE code=%s AND trade_date>=%s AND trade_date<%s AND change_pct >= 9.5",
            (code, 
             (datetime.strptime(td, '%Y%m%d') - timedelta(days=10)).strftime('%Y%m%d'),
             td))
        cnt = rows[0]['cnt'] if rows else 0
        if cnt == 0:
            results.append(code)
        if len(results) >= 5:
            break
    return results


# -------- 策略4: 放量突破涨停（量比>2） --------
def strategy_volume_breakout(pool: List[Dict], td: str) -> List[str]:
    """涨停日成交量是前5日平均的2倍以上"""
    db = get_db()
    results = []
    for p in pool:
        code = p['code']
        today_amt = float(p['amount'] or 0)
        if today_amt == 0:
            continue
        # 前5日均量
        rows = db.fetchall(
            "SELECT AVG(amount) as avg_amt FROM stock_daily WHERE code=%s AND trade_date>=%s AND trade_date<%s AND amount>0",
            (code,
             (datetime.strptime(td, '%Y%m%d') - timedelta(days=12)).strftime('%Y%m%d'),
             td))
        avg_amt = float(rows[0]['avg_amt']) if rows and rows[0]['avg_amt'] else 0
        if avg_amt > 0 and today_amt / avg_amt >= 2:
            results.append((code, today_amt / avg_amt))
    results.sort(key=lambda x: x[1], reverse=True)
    return [r[0] for r in results[:5]]


# -------- 策略5: 热门板块涨停（板块涨停最多的取前排） --------
def strategy_hot_sector(pool: List[Dict], td: str) -> List[str]:
    """当日涨停最多的"""
    # 没有板块数据，直接用前排
    return [p['code'] for p in pool[:5]]


# -------- 策略6: 均线多头连板（MA5>MA10>MA20 次日继续涨） --------
def strategy_ma_bull(pool: List[Dict], td: str) -> List[str]:
    """涨停时均线已多头排列的票"""
    results = []
    for p in pool:
        code = p['code']
        mas = calc_ma_prev(code, td, [5, 10, 20, 30])
        if mas[5] and mas[10] and mas[20] and mas[30]:
            if mas[5] > mas[10] > mas[20] > mas[30]:
                results.append(code)
        if len(results) >= 5:
            break
    return results[:5]


# -------- 策略7: 近5日未大涨过（排除高位板） --------
def strategy_no_overbought(pool: List[Dict], td: str) -> List[str]:
    """近5日涨幅累计不超过15%的涨停（非追高）"""
    db = get_db()
    results = []
    for p in pool:
        code = p['code']
        rows = db.fetchall(
            "SELECT change_pct FROM stock_daily WHERE code=%s AND trade_date>=%s AND trade_date<%s ORDER BY trade_date",
            (code,
             (datetime.strptime(td, '%Y%m%d') - timedelta(days=8)).strftime('%Y%m%d'),
             td))
        chgs = [float(r['change_pct']) for r in rows if r['change_pct']]
        if sum(chgs) < 15:
            results.append(code)
        if len(results) >= 5:
            break
    return results


# -------- 策略8: T+1日大盘看涨（基于前日大盘状态） --------
def strategy_market_peek(pool: List[Dict], td: str) -> List[str]:
    """大盘跌了选，大盘涨了不选"""
    sh = get_sh_change(td)
    if sh < -1.5:  # 大盘大跌不选
        return []
    return [p['code'] for p in pool[:5]]


# -------- 策略9: 收盘价在MA20上方（趋势向上） --------
def strategy_above_ma20(pool: List[Dict], td: str) -> List[str]:
    results = []
    for p in pool:
        code = p['code']
        close = float(p['close'])
        mas = calc_ma_prev(code, td, [10, 20])
        if mas[20] and close > mas[20]:
            results.append(code)
        if len(results) >= 5:
            break
    return results


# -------- 策略10: 首板+热点板块（组合策略） --------
def strategy_first_board_hot_sector(pool: List[Dict], td: str) -> List[str]:
    """首板（同S3）"""
    return strategy_first_board(pool, td)


# -------- 策略11: 放量首板（量比>1.5 + 首板） --------
def strategy_vol_first_board(pool: List[Dict], td: str) -> List[str]:
    """放量突破的首板"""
    db = get_db()
    results = []
    for p in pool:
        code = p['code']
        today_amt = float(p['amount'] or 0)
        if today_amt == 0:
            continue
        rows = db.fetchall(
            "SELECT AVG(amount) as avg_amt FROM stock_daily WHERE code=%s AND trade_date>=%s AND trade_date<%s AND amount>0",
            (code,
             (datetime.strptime(td, '%Y%m%d') - timedelta(days=12)).strftime('%Y%m%d'),
             td))
        avg_amt = float(rows[0]['avg_amt']) if rows and rows[0]['avg_amt'] else 0
        if avg_amt == 0:
            continue
        vol_ratio = today_amt / avg_amt
        if vol_ratio < 1.5:
            continue
        # 首板
        cnt = db.fetchone(
            "SELECT COUNT(*) as cnt FROM stock_daily WHERE code=%s AND trade_date>=%s AND trade_date<%s AND change_pct >= 9.5",
            (code,
             (datetime.strptime(td, '%Y%m%d') - timedelta(days=10)).strftime('%Y%m%d'),
             td))
        if cnt and cnt['cnt'] <= 1:
            results.append((code, vol_ratio))
        if len(results) >= 5:
            break
    results.sort(key=lambda x: x[1], reverse=True)
    return [r[0] for r in results[:5]]


# -------- 策略12: 高开板（开盘涨幅3%-7%的涨停） --------
def strategy_gap_up(pool: List[Dict], td: str) -> List[str]:
    """开盘涨幅3%-7%之间封板的（非一字板+非尾盘偷鸡板）"""
    db = get_db()
    results = []
    for p in pool:
        code = p['code']
        r = db.fetchone("SELECT open FROM stock_daily WHERE code=%s AND trade_date=%s", (code, td))
        if not r or not r['open']:
            continue
        prev_close = db.fetchone(
            "SELECT close FROM stock_daily WHERE code=%s AND trade_date<%s ORDER BY trade_date DESC LIMIT 1",
            (code, td))
        if not prev_close or not prev_close['close']:
            continue
        gap = (float(r['open']) - float(prev_close['close'])) / float(prev_close['close']) * 100
        if 3 <= gap <= 7:
            results.append(code)
        if len(results) >= 5:
            break
    return results


# ================================================================
# 回测核心函数
# ================================================================

# 注册所有策略
ALL_STRATEGIES = {
    'S1_最低市值': strategy_lowest_mcap,
    'S2_最高换手': strategy_highest_turnover,
    'S3_首板': strategy_first_board,
    'S4_放量突破': strategy_volume_breakout,
    'S5_热门板块': strategy_hot_sector,
    'S6_均线多头': strategy_ma_bull,
    'S7_非追高': strategy_no_overbought,
    'S8_大盘过滤': strategy_market_peek,
    'S9_MA20上方': strategy_above_ma20,
    'S10_首板+热点': strategy_first_board_hot_sector,
    'S11_放量首板': strategy_vol_first_board,
    'S12_高开板': strategy_gap_up,
}


def run_strategy_bt(name: str, pick_fn: Callable, start_date: str = '20260105', end_date: str = '20260601') -> Dict:
    """运行单一策略的回测"""
    all_dates = get_trade_dates()
    date_range = [d for d in all_dates if d >= start_date and d <= end_date]
    
    # 去掉最后两天（需要T+1和T+2数据）
    if len(date_range) > 2:
        date_range = date_range[:-2]
    
    db = get_db()
    results = []
    
    for td in date_range:
        pool = get_daily_pool(td)
        if not pool:
            continue
        
        picks = pick_fn(pool, td)
        if not picks:
            continue
        
        # T+1开盘买入，T+2开盘卖出
        next_date = None
        next2_date = None
        for d in all_dates:
            if d > td:
                if not next_date:
                    next_date = d
                elif not next2_date:
                    next2_date = d
                    break
        
        if not next_date or not next2_date:
            continue
        
        # 查T+1开盘价和T+2开盘价
        for code in picks:
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
                'name': next((p['name'] for p in pool if p['code'] == code), ''),
                'buy_price': buy_price,
                'sell_price': sell_price,
                'trade_return': trade_return,
            })
    
    # 统计
    if not results:
        return {'name': name, 'count': 0, 'win_rate': 0, 'avg_return': 0, 'max_win': 0, 'max_loss': 0}
    
    returns = [r['trade_return'] for r in results]
    wins = sum(1 for v in returns if v > 0)
    
    stats = {
        'name': name,
        'count': len(returns),
        'win_rate': wins / len(returns) * 100,
        'avg_return': sum(returns) / len(returns),
        'max_win': max(returns),
        'max_loss': min(returns),
        'big_win': sum(1 for v in returns if v > 5),
        'big_loss': sum(1 for v in returns if v < -3),
        'big_loss_pct': sum(1 for v in returns if v < -3) / len(returns) * 100,
    }
    return stats


# ================================================================
# 主入口
# ================================================================

if __name__ == '__main__':
    results = []
    for name, fn in ALL_STRATEGIES.items():
        logger.info(f"回测策略: {name}")
        start = time.time()
        stats = run_strategy_bt(name, fn)
        elapsed = time.time() - start
        logger.info(f"  ... {stats['count']}次交易, 胜率{stats['win_rate']:.1f}%, 平均{stats['avg_return']:+.2f}% (耗时{elapsed:.0f}s)")
        results.append(stats)
    
    # 输出排名
    print("\n" + "="*80)
    print("多策略回测结果排名（按胜率降序）")
    print("="*80)
    results.sort(key=lambda x: x['win_rate'], reverse=True)
    print(f"{'排名':>4} {'策略名':>20} {'交易次数':>8} {'胜率':>8} {'平均收益':>10} {'大胜':>6} {'大亏':>8} {'最大亏损':>10}")
    print("-"*80)
    for i, r in enumerate(results, 1):
        print(f"{i:>4} {r['name']:>20} {r['count']:>8} {r['win_rate']:>7.1f}% {r['avg_return']:>+9.2f}% "
              f"{r['big_win']:>4} {r['big_loss']:>4}({r['big_loss_pct']:.0f}%) {r['max_loss']:>+8.2f}%")
    
    # 保存结果
    output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'multi_strategy_results.json')
    with open(output_path, 'w') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\n结果已保存: {output_path}")
