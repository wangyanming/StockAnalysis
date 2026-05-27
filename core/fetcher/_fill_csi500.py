#!/usr/bin/env python3
"""继续补中证500剩余缺失"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.chdir(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import akshare as ak
from dao import get_db
from fetch_all_stocks_daily import batch_fetch, show_stats

df = ak.index_stock_cons_csindex(symbol='000905')
stocks = [{'code': str(r['成分券代码']).zfill(6), 'name': str(r['成分券名称'])} for _, r in df.iterrows()]

db = get_db()
have = set(r[0] for r in db.execute('SELECT DISTINCT code FROM stock_daily').fetchall())

missing = [s for s in stocks if s['code'] not in have]
print(f'中证500: 需补{len(missing)}只')
batch_fetch(missing, '20240101', '20260511', sleep_sec=0.8)
show_stats()
