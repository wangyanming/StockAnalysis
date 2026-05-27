#!/usr/bin/env python3
"""
v5: 基于腾讯原始数据的精准修复
已知腾讯日K接口字段(已验证):
  row[5] = volume:
    - 主板(00/30/60开头): 单位=手，×100→股
    - 科创板(688开头): 单位=股，不乘
    - 北交所(8开头): 单位=股，不乘
  row[8] = amount: 全部板块单位=万元，×10000→元

20路并发拉取+覆盖修复
"""

import sys, os, time, logging, json, urllib.request
from datetime import datetime
import concurrent.futures

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())

import pymysql

MYSQL = {
    'host': '127.0.0.1', 'port': 3306, 'user': 'root',
    'password': 'stock123', 'database': 'stock_analysis'
}

YEAR = datetime.now().year
MAX_WORKERS = 20


class DB:
    def __init__(self):
        self.conn = pymysql.connect(**MYSQL, cursorclass=pymysql.cursors.DictCursor, charset='utf8mb4')

    def fetchall(self, sql, params=None):
        c = self.conn.cursor()
        c.execute(sql, params or ())
        return c.fetchall()

    def executemany(self, sql, rows):
        c = self.conn.cursor()
        n = c.executemany(sql, rows)
        self.conn.commit()
        return n

    def close(self):
        self.conn.close()


def code_to_tx(symbol: str) -> str:
    symbol = symbol.strip().zfill(6)
    if symbol.startswith(('6', '9')):
        return f"sh{symbol}"
    elif symbol.startswith(('0', '3')):
        return f"sz{symbol}"
    elif symbol.startswith(('4', '8')):
        return f"bj{symbol}"
    return f"sz{symbol}"


def is_kcb_or_bj(code: str) -> bool:
    """科创板(688开头)或北交所(4/8开头)：volume单位是股不是手"""
    return code.startswith('688') or code.startswith('4') or code.startswith('8')


def fetch_tencent(tx_code: str, vol_is_shou: bool) -> dict:
    """拉取腾讯全年日K，返回 {trade_date: {vol_gu, amt_yuan}}"""
    params = f'{tx_code},day,{YEAR}-01-01,{YEAR}-12-31,640,qfq'
    url = f'https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?_var=kline_dayqfq{YEAR}&param={urllib.request.quote(params)}&r=0.1'
    
    req = urllib.request.Request(url)
    req.add_header('User-Agent', 'Mozilla/5.0')
    req.add_header('Referer', 'https://quotes.sina.com.cn/')
    
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        raw = resp.read().decode('utf-8')
        idx = raw.find('={')
        if idx < 0:
            return {}
        j = json.loads(raw[idx + 1:])
        d = j.get('data', {}).get(tx_code, {})
        rows = d.get('day') or d.get('qfqday') or d.get('hfqday') or []
        
        result = {}
        for row in rows:
            if len(row) < 9:
                continue
            tdate = str(row[0]).replace('-', '')
            if tdate < '2026':
                continue
            vol_raw = float(row[5]) if row[5] else 0
            amt_wan = float(row[8]) if row[8] else 0
            if vol_raw > 0 and amt_wan > 0:
                vol_gu = int(vol_raw * 100) if vol_is_shou else int(vol_raw)
                result[tdate] = {
                    'vol_gu': vol_gu,
                    'amt_yuan': round(amt_wan * 10000, 2)
                }
        return result
    except Exception as e:
        return {}


def process_one_stock(code: str, dates: list, vol_is_shou: bool) -> tuple:
    tx_code = code_to_tx(code)
    tencent_data = fetch_tencent(tx_code, vol_is_shou)
    if not tencent_data:
        return code, 0
    
    batch = []
    for tdate in dates:
        if tdate in tencent_data:
            td = tencent_data[tdate]
            batch.append((td['vol_gu'], td['amt_yuan'], code, tdate))
    
    if not batch:
        return code, 0
    
    n = 0
    try:
        db = DB()
        sql = 'UPDATE stock_daily SET volume=%s, amount=%s WHERE code=%s AND trade_date=%s'
        for vol, amt, c, td in batch:
            cur = db.conn.cursor()
            cur.execute(sql, (vol, amt, c, td))
            n += cur.rowcount
        db.conn.commit()
        db.close()
    except Exception as e:
        logger.warning(f"{code} 更新失败: {e}")
        n = 0
    
    return code, n


def verify():
    db = DB()
    logger.info("\n====== 验证 ======")
    
    # 按板块、天检查
    stats = db.fetchall("""
        SELECT trade_date,
            CASE WHEN code LIKE '688%%' THEN '科创板' ELSE '沪深主板' END as market,
            COUNT(*) as total,
            SUM(CASE WHEN close > 0 AND volume > 0 AND amount > 0
                AND ABS(amount/volume - close) < close*0.1 THEN 1 ELSE 0 END) as good,
            ROUND(SUM(amount)/100000000, 1) as total_yi
        FROM stock_daily
        WHERE trade_date >= '20260501'
        GROUP BY trade_date, market
        ORDER BY trade_date, market
    """)
    for s in stats:
        if s['total'] == 0: continue
        pct = round(s['good']/s['total']*100, 1) if s['total'] else 0
        logger.info(f"  {s['trade_date']} {s['market']}: {s['total']}行, ✅{s['good']}({pct}%), 成交≈{s['total_yi']}亿")
    
    # 抽查
    samples = db.fetchall("""
        SELECT code, trade_date, close, ROUND(volume) as vol, ROUND(amount) as amt,
               ROUND(amount/NULLIF(volume,0), 2) as calc_price
        FROM stock_daily
        WHERE trade_date >= '20260513'
          AND close > 5
          AND volume > 0
        ORDER BY RAND()
        LIMIT 10
    """)
    logger.info("抽样：")
    for s in samples:
        err = abs(s['calc_price'] - s['close']) / s['close'] * 100 if s['close'] > 0 else 999
        ok = '✅' if err < 5 else '❌'
        logger.info(f"  {ok} {s['code']} {s['trade_date']} close={s['close']} vol={s['vol']} amt={s['amt']} calc={s['calc_price']} err={err:.1f}%")
    
    db.close()


def main():
    db = DB()
    
    # 获取股票列表
    codes = db.fetchall("""
        SELECT DISTINCT code, name FROM stock_daily 
        WHERE trade_date >= '20260101' AND code IS NOT NULL AND code != ''
    """)
    total = len(codes)
    logger.info(f"共 {total} 只股票")
    
    # 获取每只股票今年有数据的日期
    code_info = {}
    for c in codes:
        rows = db.fetchall(
            "SELECT trade_date FROM stock_daily WHERE code=%s AND trade_date>='20260101' ORDER BY trade_date",
            (c['code'],))
        has_kcb = is_kcb_or_bj(c['code'])
        code_info[c['code']] = {
            'dates': [r['trade_date'] for r in rows],
            'vol_is_shou': not has_kcb
        }
    db.close()
    logger.info(f"日期索引加载完成，含{sum(1 for v in code_info.values() if not v['vol_is_shou'])}只科创板/北交所")

    total_updated = 0
    processed = 0
    start = time.time()
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {}
        for c in codes:
            code = c['code']
            info = code_info.get(code, {})
            dates = info.get('dates', [])
            vol_is_shou = info.get('vol_is_shou', True)
            if dates:
                future = executor.submit(process_one_stock, code, dates, vol_is_shou)
                futures[future] = code
        
        for future in concurrent.futures.as_completed(futures):
            code = futures[future]
            try:
                _, n = future.result()
                total_updated += n
            except Exception as e:
                logger.warning(f"{code} 异常: {e}")
            
            processed += 1
            if processed % 500 == 0 or processed == total:
                pct = processed / total * 100
                elapsed = time.time() - start
                speed = processed / (elapsed / 60) if elapsed > 0 else 0
                logger.info(f"[{processed}/{total}] {pct:.0f}% | 更新{total_updated}行 | {speed:.0f}只/分")
    
    elapsed = time.time() - start
    logger.info(f"\n✅ 完成: {processed}只, 更新{total_updated}行, {elapsed/60:.1f}分钟")
    
    verify()


if __name__ == '__main__':
    t0 = time.time()
    main()
    logger.info(f"总耗时: {(time.time()-t0)/60:.1f}分钟")
