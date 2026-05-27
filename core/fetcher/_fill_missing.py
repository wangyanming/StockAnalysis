#!/usr/bin/env python3
"""补全沪深300中缺失的77只股票"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.chdir(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from utils.dao import get_db
from core.fetcher.fetch_all_stocks_daily import get_hs300_stocks, batch_fetch

stocks = get_hs300_stocks()

db = get_db()
have = set(r[0] for r in db.execute('SELECT DISTINCT code FROM stock_daily').fetchall())

missing = [s for s in stocks if s['code'] not in have]
print(f'缺失 {len(missing)} 只, 开始补全...')

batch_fetch(missing, '20240101', '20260511', sleep_sec=0.8)
