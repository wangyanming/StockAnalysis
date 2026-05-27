#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日数据对账脚本（工程规范 10 运维监控）
检查各核心表的最新数据日期、数据量、关键指标是否正常。

执行：
    python3 tests/data_reconciliation.py
    python3 tests/data_reconciliation.py --date 20260526  # 指定对账日期
"""
import sys, os, logging
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)

from dao import get_db


def get_today():
    """获取当前日期（交易日），周末/节假日沿用前一个交易日"""
    today = datetime.now()
    # 如果周末，用周五
    if today.weekday() == 5:  # 周六→周五
        today -= timedelta(days=1)
    elif today.weekday() == 6:  # 周日→周五
        today -= timedelta(days=2)
    return today.strftime('%Y%m%d')


def check_table(table: str, date_field: str, expected_date: str, min_rows: int = 0, max_stale_days: int = 1) -> dict:
    """检查单个表：最新日期、数据量、表状态"""
    result = {
        'table': table,
        'status': 'OK',
        'latest_date': None,
        'row_count': 0,
        'message': '',
    }
    
    db = get_db()
    
    try:
        # 检查表是否存在
        cur = db.execute(f"SELECT COUNT(*) as cnt FROM information_schema.TABLES "
                         f"WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='{table}'")
        r = cur.fetchone()
        if not r or r['cnt'] == 0:
            result['status'] = 'ERROR'
            result['message'] = f'表 {table} 不存在'
            return result
        
        # 检查表数据量
        cur = db.execute(f"SELECT COUNT(*) as cnt FROM `{table}`")
        r = cur.fetchone()
        result['row_count'] = r['cnt']
        
        # 检查最新日期
        cur = db.execute(f"SELECT MAX(`{date_field}`) as max_date FROM `{table}`")
        r = cur.fetchone()
        result['latest_date'] = r['max_date'] if r else None
        
        # 判断
        if result['row_count'] == 0:
            result['status'] = 'EMPTY'
            result['message'] = '表为空'
        elif min_rows > 0 and result['row_count'] < min_rows:
            result['status'] = 'WARN'
            result['message'] = f'数据量偏少: {result["row_count"]}条 < {min_rows}条'
        elif result['latest_date'] and str(result['latest_date'])[:8] != str(expected_date)[:8]:
            try:
                ld = datetime.strptime(str(result['latest_date'])[:8], '%Y%m%d')
                ed = datetime.strptime(str(expected_date)[:8], '%Y%m%d')
                diff = (ed - ld).days
                if diff <= max_stale_days:
                    result['status'] = 'OK'
                else:
                    result['status'] = 'STALE'
                    result['message'] = f'数据过期: 最新{str(result["latest_date"])[:8]}, 预期{expected_date}, 差{diff}天(允许{max_stale_days}天)'
            except Exception:
                pass
        else:
            result['status'] = 'OK'
            
    except Exception as e:
        result['status'] = 'ERROR'
        result['message'] = str(e)
    
    return result


def check_core_tables(expected_date: str):
    """检查核心表"""
    tables = [
        ('stock_daily', 'trade_date', 100000),
        ('daily_limit_up', 'trade_date', 10),
        ('limit_up_tracking', 'latest_limit_date', 10, 30),  # 非每日更新，允许30天延迟
        ('sector_performance', 'record_date', 50),
        ('index_quotes', 'record_date', 3),
        ('daily_picks', 'trade_date', 1),
    ]
    
    results = []
    for table_info in tables:
        table = table_info[0]
        date_field = table_info[1]
        min_rows = table_info[2]
        max_stale = table_info[3] if len(table_info) > 3 else 1
        r = check_table(table, date_field, expected_date, min_rows, max_stale)
        results.append(r)
    
    return results
    """检查核心表"""
    tables = [
        ('stock_daily', 'trade_date', 100000),
        ('daily_limit_up', 'trade_date', 10),
        ('limit_up_tracking', 'latest_limit_date', 10),  # 无 trade_date 字段，用 latest_limit_date
        ('sector_performance', 'record_date', 50),
        ('index_quotes', 'record_date', 3),
        ('daily_picks', 'trade_date', 1),
    ]
    
    results = []
    for table, date_field, min_rows in tables:
        r = check_table(table, date_field, expected_date, min_rows)
        results.append(r)
    
    return results


def run(date_str=None):
    """主入口"""
    expected_date = date_str or get_today()
    
    print(f"\n{'='*60}")
    print(f" 📊 每日数据对账 — {expected_date}")
    print(f"{'='*60}\n")
    
    results = check_core_tables(expected_date)
    
    status_icons = {'OK': '✅', 'WARN': '⚠️', 'STALE': '🟡', 'EMPTY': '❌', 'ERROR': '❌'}
    
    has_error = False
    from collections import Counter
    status_counts = Counter()
    
    for r in results:
        icon = status_icons.get(r['status'], '❓')
        print(f"{icon} {r['table']}")
        print(f"   最新数据: {r['latest_date'] or '无'}")
        print(f"   数据量: {r['row_count']:,} 条")
        if r['message']:
            print(f"   状态: {r['message']}")
        if r['status'] in ('STALE', 'EMPTY', 'ERROR'):
            has_error = True
        status_counts[r['status']] += 1
    
    print(f"\n{'='*60}")
    print(f" 汇总: ✅ {status_counts.get('OK', 0)} | ⚠️ {status_counts.get('WARN', 0)} | 🟡 {status_counts.get('STALE', 0)} | ❌ {status_counts.get('EMPTY', 0)+status_counts.get('ERROR', 0)}")
    
    if has_error:
        print(f" ❌ 部分核心表数据异常，请人工介入检查！")
        return False
    else:
        print(f" ✅ 所有核心表数据正常")
        return True


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='数据对账')
    parser.add_argument('--date', help='对账日期，格式 YYYYMMDD')
    args = parser.parse_args()
    
    success = run(args.date)
    sys.exit(0 if success else 1)
