#!/usr/bin/env python3
"""拉取全A剩余股票日K"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.chdir(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import akshare as ak
from dao import get_db
from fetch_all_stocks_daily import batch_fetch, show_stats

# 全A
df = ak.stock_info_a_code_name()
all_stocks = [{'code': str(r['code']).zfill(6), 'name': str(r['name'])} for _, r in df.iterrows()]

# 过滤已拉取
db = get_db()
have = set(r[0] for r in db.execute('SELECT DISTINCT code FROM stock_daily').fetchall())

missing = [s for s in all_stocks if s['code'] not in have]
print(f'全A股: {len(all_stocks)}只, 已拉{len(all_stocks)-len(missing)}只, 还需拉{len(missing)}只')
print(f'预计耗时: {len(missing)*3.2/60:.0f}分钟 ({len(missing)*3.2/3600:.1f}小时)')

batch_fetch(missing, '20240101', '20260511', sleep_sec=0.8)
show_stats()
