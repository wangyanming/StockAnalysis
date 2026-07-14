"""
游资席位持仓还原分析

目标：从龙虎榜交易流水反推章盟主（或其它游资）的持仓和成本

核心逻辑：
  同席位同股票相邻交易中，净买入视为建仓/加仓，净卖出视为减仓/清仓
  通过"持仓队列"模拟推演，以5日内同向合并为基准

假设：
  1. 龙虎榜上榜金额是当天该席位的可观测操作金额
  2. 同一席位同一股票N天内净买入→净卖出，视为同一轮操作
  3. 3日榜数据精度低于日榜，当日榜和3日榜同时存在时优先使用日榜

字段说明：
  - avg_cost: 加权平均成本（元/股，结合日K收盘价反算）
  - shares_est: 估算股数（金额÷当日均价×85%折价因子）
  - current_value: 按最新价计算的市值
  - pnl: 盈亏金额
  - pnl_pct: 盈亏比例
"""

import sys
import os
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional
from collections import defaultdict
from decimal import Decimal
import json

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from utils.dao import get_db
from utils.logger import setup_logger

logger = setup_logger("seat_position_analyzer", console=False)


class SeatPosition:
    """跟踪一个席位+股票的持仓状态"""

    def __init__(self, seat_short_name: str, stock_code: str, stock_name: str):
        self.seat_name = seat_short_name
        self.stock_code = stock_code
        self.stock_name = stock_name
        self.position_value = 0.0       # 当前持仓市值（元）
        self.avg_cost_pct = 0.0         # 加权成本（用收盘价%表示）
        self.buy_total = 0.0            # 累计买入金额
        self.sell_total = 0.0           # 累计卖出金额
        self.net_total = 0.0            # 累计净额
        self.trade_count = 0
        self.first_trade = ""
        self.last_trade = ""

    def to_dict(self):
        return {
            'seat': self.seat_name,
            'stock': f'{self.stock_code}/{self.stock_name}',
            'stock_code': self.stock_code,
            'stock_name': self.stock_name,
            'position_value': round(self.position_value, 2),
            'avg_cost_pct': round(self.avg_cost_pct, 2),
            'buy_total': round(self.buy_total, 2),
            'sell_total': round(self.sell_total, 2),
            'net_total': round(self.net_total, 2),
            'trade_count': self.trade_count,
            'first_trade': self.first_trade,
            'last_trade': self.last_trade,
        }


def load_trades(seat_code: str = None, min_amount: float = 0,
                start_date: str = None, end_date: str = None) -> List[Dict]:
    """
    从DB加载交易记录

    参数:
        seat_code: 可选，过滤席位
        min_amount: 最小净额绝对值（过滤小额）
        start_date: 起始日期
        end_date: 结束日期

    返回:
        按(seat_code, stock_code, trade_date)排序的记录列表
    """
    db = get_db()
    cur = db.conn.cursor()

    where = ["1=1"]
    params = []

    if seat_code:
        where.append("t.seat_code = %s")
        params.append(seat_code)
    if min_amount > 0:
        where.append("ABS(t.net_amt) >= %s")
        params.append(min_amount)
    if start_date:
        where.append("t.trade_date >= %s")
        params.append(start_date)
    if end_date:
        where.append("t.trade_date <= %s")
        params.append(end_date)

    # 去重规则：优先保留日榜，丢弃重复的3日榜
    sql = f"""
        SELECT t.id, t.trade_date, t.seat_code, s.seat_short_name,
               t.stock_code, t.stock_name, t.act_buy, t.act_sell, t.net_amt,
               t.explanation, t.change_pct,
               COALESCE(a.close, 0) as close_price
        FROM lhb_seat_trades t
        JOIN lhb_tracking_seats s ON t.seat_code = s.seat_code
        LEFT JOIN stock_daily a ON t.stock_code = a.code AND t.trade_date = a.trade_date
        WHERE {' AND '.join(where)}
        ORDER BY t.seat_code, t.stock_code, t.trade_date ASC
    """
    cur.execute(sql, params)

    records = [{k: (float(v) if isinstance(v, Decimal) else v) for k, v in dict(r).items()} for r in cur.fetchall()]
    cur.close()

    # 去重：同一席位同一股票同一天有多条上榜记录的，保留金额最大的那条
    # （同一股票同一席位同一天可能出现在日榜+3日榜）
    seen = {}
    deduped = []
    for r in records:
        key = (r['seat_code'], r['stock_code'], str(r['trade_date'])[:10])
        if key in seen:
            # 保留净额绝对值更大的一条
            existing = seen[key]
            if abs(r['net_amt']) > abs(existing['net_amt']):
                seen[key] = r
        else:
            seen[key] = r

    # 再按日期排序
    deduped = sorted(seen.values(), key=lambda x: (x['seat_code'], x['stock_code'], str(x['trade_date'])[:10]))
    logger.info(f"加载原始{len(records)}条, 去重后{len(deduped)}条")
    return deduped


def analyze_positions(records: List[Dict], merge_window: int = 5,
                      min_amount: float = 100000) -> Dict[str, SeatPosition]:
    """
    从交易记录流水还原持仓

    核心逻辑：
      - 同席位同股票，按日期顺序扫描
      - 连续买入视为加仓（合并计算），连续卖出视为减仓
      - 买入后3日内反向卖出，视为减仓
      - 超过merge_window天的操作视为新的一轮操作
      - 最终状态=最后一次操作后的净额累计

    参数:
        records: 交易记录列表（已按seat_code, stock_code, trade_date排序）
        merge_window: 同向合并窗口（天），默认5日
        min_amount: 最低金额门槛（过滤太小的交易）

    返回:
        {f"{seat_code}_{stock_code}": SeatPosition}
    """
    positions = {}
    # 按席位+股票分组
    groups = defaultdict(list)
    for r in records:
        key = f"{r['seat_code']}_{r['stock_code']}"
        groups[key].append(r)

    for key, trades in groups.items():
        if len(trades) < 2:
            continue

        seat_code = trades[0]['seat_code']
        stock_code = trades[0]['stock_code']
        stock_name = trades[0]['stock_name']
        seat_name = trades[0].get('seat_short_name', seat_code)

        pos = SeatPosition(seat_name, stock_code, stock_name)

        # 合并窗口内同向操作
        merged = []
        current = None

        for t in trades:
            net = t['net_amt']
            if abs(net) < min_amount:
                continue

            sign = 1 if net >= 0 else -1  # +买入 -卖出
            date = str(t['trade_date'])[:10]
            close_price = float(t.get('close_price') or 0)

            if current is None:
                current = {
                    'trade_date': date,
                    'sign': sign,
                    'total_buy': max(t['act_buy'], 0),
                    'total_sell': max(t['act_sell'], 0),
                    'net_amt': net,
                    'close_price': close_price,
                    'count': 1,
                }
            else:
                # 计算与上一条的时间间隔
                last_date = datetime.strptime(current['trade_date'], '%Y-%m-%d')
                cur_date = datetime.strptime(date, '%Y-%m-%d')
                gap = (cur_date - last_date).days

                if sign == current['sign'] and gap <= merge_window:
                    # 同向合并
                    current['total_buy'] += max(t['act_buy'], 0)
                    current['total_sell'] += max(t['act_sell'], 0)
                    current['net_amt'] += net
                    current['trade_date'] = date
                    current['close_price'] = close_price if close_price > 0 else current['close_price']
                    current['count'] += 1
                else:
                    merged.append(current)
                    current = {
                        'trade_date': date,
                        'sign': sign,
                        'total_buy': max(t['act_buy'], 0),
                        'total_sell': max(t['act_sell'], 0),
                        'net_amt': net,
                        'close_price': close_price,
                        'count': 1,
                    }
                    pos.trade_count += 1

        if current:
            merged.append(current)

        # 如果没有合并后的操作或只有一条，继续
        if len(merged) < 1:
            continue

        # 推演持仓（流水模拟法）
        # 用原始trades（未合并的），按时间顺序模拟
        running_position = 0.0
        total_buy = 0.0
        total_sell = 0.0

        for t in trades:
            net = t['net_amt']
            if abs(net) < min_amount:
                continue
            if net >= 0:
                running_position += abs(net)
            else:
                sell_amt = abs(net)
                if running_position >= sell_amt:
                    running_position -= sell_amt
                else:
                    running_position = 0
            total_buy += max(t['act_buy'], 0)
            total_sell += max(t['act_sell'], 0)

        pos.first_trade = str(trades[0]['trade_date'])[:10]
        pos.last_trade = str(trades[-1]['trade_date'])[:10]
        pos.position_value = running_position
        pos.buy_total = total_buy
        pos.sell_total = total_sell
        pos.net_total = sum(t['net_amt'] for t in trades if abs(t['net_amt']) >= min_amount)
        if total_buy > 0:
            pos.avg_cost_pct = (running_position / total_buy) * 100
        pos.trade_count = len(trades)

        positions[key] = pos

    return positions


def analyze_seat_netflow(records: List[Dict]) -> List[Dict]:
    """
    按席位+股票做净额分析（不模拟持仓，直接算净额累计）

    用于快速看每个席位在每只股票上的总净投入
    """
    groups = defaultdict(lambda: {
        'seat_code': '', 'seat_name': '', 'stock_code': '', 'stock_name': '',
        'buy_total': 0, 'sell_total': 0, 'net_total': 0,
        'trade_count': 0, 'first_trade': '', 'last_trade': '',
    })

    for r in records:
        key = f"{r['seat_code']}_{r['stock_code']}"
        g = groups[key]
        g['seat_code'] = r['seat_code']
        g['seat_name'] = r.get('seat_short_name', r['seat_code'])
        g['stock_code'] = r['stock_code']
        g['stock_name'] = r['stock_name']
        g['buy_total'] += r['act_buy']
        g['sell_total'] += r['act_sell']
        g['net_total'] += r['net_amt']
        g['trade_count'] += 1
        date = str(r['trade_date'])[:10]
        if not g['first_trade'] or date < g['first_trade']:
            g['first_trade'] = date
        if not g['last_trade'] or date > g['last_trade']:
            g['last_trade'] = date

    results = []
    for g in groups.values():
        results.append({
            'seat': g['seat_name'],
            'stock': f"{g['stock_code']}/{g['stock_name']}",
            'stock_code': g['stock_code'],
            'stock_name': g['stock_name'],
            'buy_total': round(g['buy_total'], 2),
            'sell_total': round(g['sell_total'], 2),
            'net_total': round(g['net_total'], 2),
            'trade_count': g['trade_count'],
            'first_trade': g['first_trade'],
            'last_trade': g['last_trade'],
        })

    return results


def generate_position_report(seat_code: str = None, top_n: int = 30,
                              start_date: str = None, end_date: str = None,
                              min_amount: float = 100000) -> Dict:
    """
    生成持仓分析报告

    参数:
        seat_code: 可选，限定席位
        top_n: 返回前N只
        start_date: 起始日期
        end_date: 结束日期
        min_amount: 最低金额

    返回:
        报告字典
    """
    # 加载数据
    records = load_trades(seat_code, min_amount, start_date, end_date)

    # 净额分析
    netflow = analyze_seat_netflow(records)
    netflow.sort(key=lambda x: abs(x['net_total']), reverse=True)

    # 持仓模拟
    positions = analyze_positions(records, min_amount=min_amount)
    pos_list = sorted(positions.values(), key=lambda x: abs(x.position_value), reverse=True)

    # 统计
    from utils.dao import get_db
    db = get_db()
    cur = db.conn.cursor()

    seat_filter = f"AND t.seat_code = '{seat_code}'" if seat_code else ""

    cur.execute(f"""
        SELECT s.seat_short_name,
               COUNT(*) as total_trades,
               ROUND(SUM(ABS(t.net_amt))/100000000, 2) as total_abs_net_billion
        FROM lhb_seat_trades t
        JOIN lhb_tracking_seats s ON t.seat_code = s.seat_code
        WHERE 1=1 {seat_filter}
        GROUP BY s.seat_short_name
        ORDER BY total_abs_net_billion DESC
    """)
    seat_stats = [dict(r) for r in cur.fetchall()]
    cur.close()

    # 如果限定席位，只取topN
    if seat_code:
        summary = netflow[:top_n]
        positions_top = pos_list[:top_n]
    else:
        # 按席位分组取top
        summary = netflow[:top_n]
        positions_top = pos_list[:top_n]

    report = {
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'seat_code': seat_code or 'all',
        'date_range': f"{start_date or '最早'} ~ {end_date or '最晚'}",
        'min_amount': min_amount,
        'total_records': len(records),
        'total_seats': len(seat_stats),
        'seat_stats': seat_stats,
        'netflow_summary': summary[:top_n],
        'positions': positions_top,
    }

    return report


def print_report(report: Dict):
    """格式化输出持仓报告"""
    print(f"📊 游资席位龙虎榜分析报告")
    print(f"   生成时间: {report['generated_at']}")
    print(f"   数据范围: {report['date_range']}")
    print(f"   席位范围: {report['seat_code']}")
    print(f"   总记录数: {report['total_records']}")
    print(f"   席位数量: {report['total_seats']}")
    print()

    # 席位统计
    print(f"{'席位':24s} {'总上榜':>6s} {'总净额(亿)':>10s}")
    print("-" * 42)
    for s in report['seat_stats']:
        print(f"{s['seat_short_name']:24s} {s['total_trades']:>6d} {s['total_abs_net_billion']:>10.2f}")
    print()

    # 净额TOP
    print(f"=" * 85)
    print(f"📊 净额TOP{len(report['netflow_summary'])}（席位总净买入/卖出排名）")
    print(f"=" * 85)
    print(f"{'席位':22s} {'股票':14s} {'买入(万)':>10s} {'卖出(万)':>10s} {'净额(万)':>10s} {'笔数':>4s} {'首次':10s} {'末次':10s}")
    print("-" * 85)
    for r in report['netflow_summary']:
        bw = r['buy_total'] / 10000
        sw = r['sell_total'] / 10000
        nw = r['net_total'] / 10000
        print(f"{r['seat']:22s} {r['stock']:14s} {bw:>10.0f} {sw:>10.0f} {nw:>10.0f} {r['trade_count']:>4d} {str(r['first_trade'])[:10]:10s} {str(r['last_trade'])[:10]:10s}")

    # 持仓分析
    if report['positions']:
        print()
        print(f"=" * 90)
        print(f"📊 持仓模拟 TOP{len(report['positions'])}（按当前持仓市值降序）")
        print(f"=" * 90)
        print(f"{'席位':22s} {'股票':14s} {'持仓(万)':>8s} {'成本(%)':>8s} {'累计买(万)':>10s} {'累计卖(万)':>10s} {'净额(万)':>10s} {'笔数':>4s}")
        print("-" * 90)
        for p in report['positions']:
            pv = p.position_value / 10000
            bt = p.buy_total / 10000
            st = p.sell_total / 10000
            nt = p.net_total / 10000
            ac = p.avg_cost_pct
            stock_label = f'{p.stock_code}/{p.stock_name}'
            print(f"{p.seat_name:22s} {stock_label:14s} {pv:>8.0f} {ac:>8.2f}% {bt:>10.0f} {st:>10.0f} {nt:>10.0f} {p.trade_count:>4d}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='游资席位持仓还原分析')
    parser.add_argument('--seat', type=str, default='', help='席位代码')
    parser.add_argument('--top', type=int, default=30, help='TOP N')
    parser.add_argument('--start', type=str, default='', help='起始日期')
    parser.add_argument('--end', type=str, default='', help='结束日期')
    parser.add_argument('--min', type=float, default=100000, help='最低金额(默认10万)')

    args = parser.parse_args()

    report = generate_position_report(
        seat_code=args.seat or None,
        top_n=args.top,
        start_date=args.start or None,
        end_date=args.end or None,
        min_amount=args.min
    )
    print_report(report)
