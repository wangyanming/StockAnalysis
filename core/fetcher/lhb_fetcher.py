"""
龙虎榜数据采集模块

数据源: 东方财富数据中心的营业部交易明细接口 (RPT_OPERATEDEPT_TRADE_DETAILSNEW)
目标: 按营业部代码采集其历史龙虎榜交易记录
策略: 从最新日期往前倒推，分批次采集（以月为单位）
单位: 金额（元）

接口说明:
  - URL: https://datacenter-web.eastmoney.com/api/data/v1/get
  - reportName: RPT_OPERATEDEPT_TRADE_DETAILSNEW
  - 返回字段: 营业部信息、交易日期、股票、买入/卖出金额、净额、涨跌幅、后续涨跌幅
"""

import sys
import os
import time
import json
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

# 项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

import requests

from utils.dao import get_db
from utils.data_validator import validate_range
from utils.logger import setup_logger

logger = setup_logger("lhb_fetcher", console=False)

# ─────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────

# 东方财富营业部交易明细API
EASTMONEY_API = "https://datacenter-web.eastmoney.com/api/data/v1/get"

# 采集批次大小（每批次多少条记录）
BATCH_SIZE = 2000

# 每次请求间隔（秒），避免被封
REQUEST_INTERVAL = 0.3


def fetch_seat_trades(seat_code: str, seat_name: str, start_date: str, end_date: str,
                       page_size: int = 1000) -> List[Dict]:
    """
    采集指定营业部在日期范围内的所有交易记录

    参数:
        seat_code: 营业部代码
        seat_name: 营业部名称（仅用于日志）
        start_date: 起始日期 YYYY-MM-DD
        end_date: 结束日期 YYYY-MM-DD
        page_size: 每页条数

    返回:
        交易记录列表
    """
    params = {
        'sortColumns': 'TRADE_DATE,SECURITY_CODE',
        'sortTypes': '-1,1',
        'pageSize': str(page_size),
        'pageNumber': '1',
        'reportName': 'RPT_OPERATEDEPT_TRADE_DETAILSNEW',
        'columns': 'ALL',
        'source': 'WEB',
        'client': 'WEB',
        'filter': f'(OPERATEDEPT_CODE="{seat_code}")',
    }

    all_records = []
    page = 1
    max_pages = 200  # 安全限制：最多200页（20万条）

    try:
        # 获取第一页，判断总页数
        r = requests.get(EASTMONEY_API, params=params, timeout=30)
        data = r.json()

        if not (data.get('result') and data['result']['data']):
            return []

        total_pages = min(data['result'].get('pages', 1), max_pages)
        all_records.extend(data['result']['data'])

        # 获取剩余页
        for pg in range(2, total_pages + 1):
            page = pg
            params['pageNumber'] = str(pg)
            time.sleep(REQUEST_INTERVAL)

            r2 = requests.get(EASTMONEY_API, params=params, timeout=30)
            d2 = r2.json()

            if d2.get('result') and d2['result']['data']:
                all_records.extend(d2['result']['data'])

        logger.info(f"[{seat_name}] 共获取 {len(all_records)} 条记录（{total_pages}页）")

    except Exception as e:
        logger.error(f"[{seat_name}] 采集异常: {e}, 已获取 {len(all_records)} 条")

    # 按日期范围过滤
    filtered = []
    for item in all_records:
        d = str(item.get('TRADE_DATE', ''))[:10]
        if start_date <= d <= end_date:
            filtered.append(item)

    logger.info(f"[{seat_name}] 日期过滤后: {len(filtered)}/{len(all_records)} 条")
    return filtered


def save_trades_to_db(records: List[Dict], batch_id: str) -> Tuple[int, int]:
    """
    将交易记录写入lhb_seat_trades表

    参数:
        records: 交易记录列表
        batch_id: 采集批次ID

    返回:
        (成功数, 重复数)
    """
    if not records:
        return 0, 0

    db = get_db()
    cur = db.conn.cursor()
    success = 0
    duplicate = 0

    for item in records:
        try:
            trade_date = str(item.get('TRADE_DATE', ''))[:10]
            seat_code = str(item.get('OPERATEDEPT_CODE', ''))
            seat_name = str(item.get('OPERATEDEPT_NAME', ''))
            stock_code = str(item.get('SECURITY_CODE', ''))
            stock_name = str(item.get('SECURITY_NAME_ABBR', ''))
            secucode = str(item.get('SECUCODE', ''))

            # 金额字段（单位：元）
            act_buy = float(item.get('ACT_BUY') or 0)
            act_sell = float(item.get('ACT_SELL') or 0)
            net_amt = float(item.get('NET_AMT') or 0)

            # 涨跌幅字段（单位：%）
            change_pct = item.get('CHANGE_RATE')
            d1 = item.get('D1_CLOSE_ADJCHRATE')
            d2 = item.get('D2_CLOSE_ADJCHRATE')
            d3 = item.get('D3_CLOSE_ADJCHRATE')
            d5 = item.get('D5_CLOSE_ADJCHRATE')
            d10 = item.get('D10_CLOSE_ADJCHRATE')
            d20 = item.get('D20_CLOSE_ADJCHRATE')
            d30 = item.get('D30_CLOSE_ADJCHRATE')

            explanation = str(item.get('EXPLANATION') or '')

            # 数据校验
            validate_range(act_buy, 0, 1e12, "act_buy")
            validate_range(act_sell, 0, 1e12, "act_sell")

            cur.execute("""
                INSERT IGNORE INTO lhb_seat_trades
                (trade_date, seat_code, seat_name, stock_code, stock_name,
                 act_buy, act_sell, net_amt, change_pct,
                 d1_change, d2_change, d3_change, d5_change,
                 d10_change, d20_change, d30_change,
                 explanation, secucode, batch_id)
                VALUES (%s, %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s)
            """, (
                trade_date, seat_code, seat_name, stock_code, stock_name,
                act_buy, act_sell, net_amt, change_pct,
                d1, d2, d3, d5,
                d10, d20, d30,
                explanation, secucode, batch_id
            ))

            if cur.rowcount > 0:
                success += 1
            else:
                duplicate += 1

        except Exception as e:
            logger.warning(f"写入异常: {e}, 跳过")

    cur.close()
    db.conn.commit()
    return success, duplicate


def fetch_seat_by_month(seat_code: str, seat_name: str, year: int, month: int,
                        batch_id: str) -> Tuple[int, int]:
    """
    采集某个营业部在指定年月的交易数据

    返回:
        (成功数, 重复数)
    """
    if month == 12:
        start_date = f"{year}-12-01"
        end_date = f"{year}-12-31"
    else:
        start_date = f"{year}-{month:02d}-01"
        # 下月第一天减1天 = 本月最后一天
        end_date = (datetime(year, month + 1, 1) - timedelta(days=1)).strftime("%Y-%m-%d")

    logger.info(f"[{seat_name}] 采集 {start_date} ~ {end_date}")
    records = fetch_seat_trades(seat_code, seat_name, start_date, end_date)

    if not records:
        logger.info(f"[{seat_name}] {start_date}~{end_date} 无数据")
        return 0, 0

    success, dup = save_trades_to_db(records, batch_id)
    logger.info(f"[{seat_name}] {start_date}~{end_date}: 写入{success}, 重复{dup}")
    return success, dup


def fetch_seat_full_history(seat_code: str, seat_name: str, short_name: str,
                              start_date: str, end_date: str,
                              batch_id: str) -> Tuple[int, int]:
    """
    采集一个席位的完整历史记录（一次性全量拉取，按日期过滤后入库）

    优化说明：
    - 东财接口返回的是该席位的所有历史记录，不分页时按月切会导致重复请求
    - 改为一次性拉取全量（接口有分页但每页1000条，一般3~10页），只请求一次

    返回:
        (成功数, 重复数)
    """
    records = fetch_seat_trades(seat_code, seat_name, start_date, end_date)
    if not records:
        logger.info(f"[{short_name}] {start_date}~{end_date} 无数据")
        return 0, 0
    return save_trades_to_db(records, batch_id)


def batch_fetch_all_seats(start_year: int = 2023, end_year: int = 2026,
                           start_month: int = 1, end_month: int = 7,
                           reverse: bool = True):
    """
    采集所有追踪席位的交易数据

    优化说明：
    - 每个席位一次性拉全量数据，不按月切分（避免重复请求API）
    - 东财接口返回全量历史，按月拉会导致N次相同数据的重复请求
    - INSERT IGNORE 自动处理重复记录，幂等安全

    参数:
        start_year: 起始年份
        end_year: 结束年份
        start_month: 起始月份
        end_month: 结束月份（含）
        reverse: 无效参数（保留兼容）
    """
    from utils.dao import get_db
    db = get_db()
    cur = db.conn.cursor()

    # 获取所有席位
    cur.execute("SELECT seat_code, seat_name, seat_short_name, is_active, status, closed_date FROM lhb_tracking_seats ORDER BY id")
    all_seats = [dict(r) for r in cur.fetchall()]
    cur.close()

    start_date = f"{start_year}-{start_month:02d}-01"
    end_date = f"{end_year}-{end_month:02d}-31"

    batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    logger.info(f"批次ID: {batch_id}, 共 {len(all_seats)} 个席位, 范围: {start_date}~{end_date}")

    total_success = 0
    total_dup = 0

    for seat in all_seats:
        seat_code = seat['seat_code']
        seat_name = seat['seat_name']
        short_name = seat['seat_short_name']
        is_active = seat['is_active']
        closed_date = str(seat['closed_date'] or '')

        # 已关闭的席位只采到关闭日期
        actual_end_date = end_date
        if is_active == 0 and closed_date:
            actual_end_date = closed_date
            if actual_end_date < start_date:
                logger.info(f"[{short_name}] 在采集范围前已关闭({closed_date})，跳过")
                continue

        logger.info(f"[{short_name}] 开始采集 {start_date}~{actual_end_date}")
        try:
            s, d = fetch_seat_full_history(seat_code, seat_name, short_name,
                                            start_date, actual_end_date, batch_id)
            total_success += s
            total_dup += d
        except Exception as e:
            logger.error(f"[{short_name}] 异常: {e}")

        time.sleep(1)

    stats = {
        'batch_id': batch_id,
        'total_records': total_success + total_dup,
        'new_records': total_success,
        'duplicates': total_dup,
        'date_range': f"{start_date} ~ {end_date}",
        'seats_count': len(all_seats),
    }
    logger.info(f"采集完成: 总{total_success + total_dup}条, 新增{total_success}, 重复{total_dup}")
    print(f"采集完成: 总{total_success + total_dup}条, 新增{total_success}, 重复{total_dup}")
    return stats


def get_seat_trades_summary(seat_code: str = None) -> List[Dict]:
    """
    获取席位交易统计摘要

    参数:
        seat_code: 可选，指定营业部代码

    返回:
        统计列表
    """
    from utils.dao import get_db
    db = get_db()
    cur = db.conn.cursor()

    if seat_code:
        cur.execute("""
            SELECT seat_code, seat_name, COUNT(*) as total, 
                   MIN(trade_date) as first_date, MAX(trade_date) as last_date,
                   COUNT(DISTINCT stock_code) as stock_count,
                   SUM(CASE WHEN act_buy > 0 AND act_sell = 0 THEN 1 ELSE 0 END) as buy_only,
                   SUM(CASE WHEN act_sell > 0 AND act_buy = 0 THEN 1 ELSE 0 END) as sell_only
            FROM lhb_seat_trades 
            WHERE seat_code = %s
            GROUP BY seat_code, seat_name
        """, (seat_code,))
    else:
        cur.execute("""
            SELECT t.seat_code, s.seat_short_name, t.seat_name, 
                   COUNT(*) as total, 
                   MIN(t.trade_date) as first_date, 
                   MAX(t.trade_date) as last_date,
                   COUNT(DISTINCT t.stock_code) as stock_count
            FROM lhb_seat_trades t
            LEFT JOIN lhb_tracking_seats s ON t.seat_code = s.seat_code
            GROUP BY t.seat_code, t.seat_name
            ORDER BY last_date DESC
        """)

    results = []
    for r in cur.fetchall():
        results.append(dict(r))
    cur.close()
    return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='龙虎榜席位数据采集')
    parser.add_argument('--action', choices=['fetch', 'summary', 'fetch_one'], default='fetch',
                        help='操作: fetch=全量采集, fetch_one=单席位, summary=统计')
    parser.add_argument('--seat', type=str, default='',
                        help='营业部代码(fetch_one时必填)')
    parser.add_argument('--start-year', type=int, default=2023,
                        help='起始年份(默认2023)')
    parser.add_argument('--start-month', type=int, default=1,
                        help='起始月份(默认1)')
    parser.add_argument('--end-year', type=int, default=2026,
                        help='结束年份(默认2026)')
    parser.add_argument('--end-month', type=int, default=7,
                        help='结束月份(默认7)')

    args = parser.parse_args()

    if args.action == 'fetch':
        print(f"开始采集龙虎榜数据 ({args.start_year}-{args.start_month:02d} ~ {args.end_year}-{args.end_month:02d}, 反向)...")
        stats = batch_fetch_all_seats(
            start_year=args.start_year, end_year=args.end_year,
            start_month=args.start_month, end_month=args.end_month,
            reverse=True
        )
        print(f"\n采集完成: {json.dumps(stats, ensure_ascii=False)}")

    elif args.action == 'fetch_one':
        if not args.seat:
            print("请指定 --seat 营业部代码")
            sys.exit(1)

        from utils.dao import get_db
        db = get_db()
        cur = db.conn.cursor()
        cur.execute("SELECT seat_code, seat_name, seat_short_name FROM lhb_tracking_seats WHERE seat_code=%s", (args.seat,))
        row = cur.fetchone()
        cur.close()

        if row:
            seat_code, seat_name, short_name = row['seat_code'], row['seat_name'], row['seat_short_name']
            print(f"采集 {short_name}({seat_code}) ...")
            stats = {'batch_id': datetime.now().strftime("%Y%m%d_%H%M%S")}
            total_s, total_d = 0, 0
            for y in range(args.start_year, args.end_year + 1):
                ms = args.start_month if y == args.start_year else 1
                me = args.end_month if y == args.end_year else 12
                for m in range(ms, me + 1):
                    s, d = fetch_seat_by_month(seat_code, seat_name, y, m, stats['batch_id'])
                    total_s += s
                    total_d += d
            stats['new_records'] = total_s
            stats['duplicates'] = total_d
            stats['total_records'] = total_s + total_d
            print(f"完成: 新增{total_s}, 重复{total_d}")
        else:
            print(f"未找到席位: {args.seat}")

    elif args.action == 'summary':
        results = get_seat_trades_summary()
        print(f"{'席位名称':30s} {'总记录':>8s} {'最早日期':12s} {'最晚日期':12s} {'涉及股票':>8s}")
        print("-" * 80)
        for r in results:
            short = r.get('seat_short_name', '') or r['seat_name'][:20]
            print(f"{short:30s} {r['total']:>8d} {str(r['first_date'])[:10]:12s} {str(r['last_date'])[:10]:12s} {r['stock_count']:>8d}")
