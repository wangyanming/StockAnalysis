#!/usr/bin/env python3
"""
修复 stock_daily 的 volume 数据
从新浪历史K线重新拉取2026年至今的成交量，存储为"手"单位
覆盖重写，不损坏已有的 change_pct 数据

使用: python3 scripts/fix_stock_daily_volume.py
"""
import sys, os, json, time, logging, urllib.request
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())
import pymysql

MYSQL = {'host': '127.0.0.1', 'port': 3306, 'user': 'root',
         'password': 'stock123', 'database': 'stock_analysis'}
INTERVAL = 0.15


class DB:
    def __init__(self):
        self.conn = pymysql.connect(**MYSQL, cursorclass=pymysql.cursors.DictCursor, charset='utf8mb4')

    def fetchall(self, sql, params=None):
        c = self.conn.cursor()
        c.execute(sql, params or ())
        return c.fetchall()

    def update_batch(self, rows):
        """批量写入: rows = [(volume_hand, code, trade_date), ...]"""
        c = self.conn.cursor()
        n = 0
        for vol, code, tdate in rows:
            c.execute('UPDATE stock_daily SET volume=%s WHERE code=%s AND trade_date=%s', (vol, code, tdate))
            n += c.rowcount
        self.conn.commit()
        return n

    def close(self):
        self.conn.close()


def fetch_sina(code):
    prefix = 'sh' if code.startswith('6') else 'sz'
    url = (f'https://quotes.sina.cn/cn/api/json_v2.php/'
           f'CN_MarketData.getKLineData?symbol={prefix}{code}&scale=240&datalen=200')
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn'})
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        logger.debug(f"{code} 失败: {e}")
        return None


def main():
    db = DB()
    rows = db.fetchall("""
        SELECT DISTINCT code FROM stock_daily
        WHERE trade_date >= '20260101'
    """)
    all_codes = [r['code'] for r in rows if len(r['code']) == 6]
    logger.info(f"总计{len(all_codes)}只股票")

    db.close()
    total_updated = 0
    processed = 0
    start = time.time()

    for i, code in enumerate(all_codes):
        data = fetch_sina(code)
        if not data:
            continue

        batch = []
        for d in data:
            if d.get('day', '') >= '2026':
                vol = int(d.get('volume', 0)) // 100  # 新浪是"股"，÷100 = "手"
                if vol > 0:
                    batch.append((vol, code, d['day'].replace('-', '')))

        if batch:
            db2 = DB()
            n = db2.update_batch(batch)
            db2.close()
            total_updated += n
            processed += 1

        if (i + 1) % 50 == 0:
            pct = (i + 1) / len(all_codes) * 100
            spd = (i + 1) / ((time.time() - start) / 60) if time.time() > start else 0
            logger.info(f"[{i+1}/{len(all_codes)}] {pct:.0f}% | 处理{processed}只, 更新{total_updated}行, {spd:.0f}只/分")

        time.sleep(INTERVAL)

    elapsed = time.time() - start
    logger.info(f"完成: {processed}只, 更新{total_updated}行, {elapsed/60:.1f}分钟")


if __name__ == '__main__':
    t0 = time.time()
    main()
    logger.info(f"总耗时: {(time.time()-t0)/60:.1f}分钟")
