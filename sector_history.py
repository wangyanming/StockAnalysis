"""
板块历史K线数据采集
数据源：AKShare stock_board_industry_hist_em
  close/open/high/low=元
  volume=手(x100->股)
  amount=元
"""

import json
import time
import urllib.request
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional

from dao import get_db

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# 东方财富行业板块代码（同 _fetch_board_sectors_via_ulist）
BOARD_CODES = [
    "BK0420","BK0421","BK0422","BK0424","BK0425","BK0427","BK0428","BK0429","BK0433","BK0436",
    "BK0437","BK0438","BK0440","BK0447","BK0448","BK0450","BK0451","BK0454","BK0456","BK0457",
    "BK0458","BK0459","BK0464","BK0465","BK0470","BK0471","BK0473","BK0474","BK0475","BK0476",
    "BK0478","BK0479","BK0480","BK0481","BK0482","BK0484","BK0485","BK0486","BK0490",
    "BK0493","BK0494","BK0512","BK0538","BK0539","BK0545","BK0546",
    "BK0725","BK0726","BK0727","BK0728","BK0729","BK0730","BK0731","BK0732","BK0733","BK0734",
    "BK0735","BK0736","BK0737","BK0738","BK0739","BK0740",
]

# 东方财富概念板块代码
CONCEPT_CODES = [
    "BK0488","BK0489","BK0491","BK0492","BK0495","BK0496","BK0497","BK0498","BK0499","BK0500",
    "BK0501","BK0502","BK0503","BK0504","BK0505","BK0506","BK0507","BK0508","BK0509","BK0510",
    "BK0511","BK0513","BK0514","BK0515","BK0516","BK0517","BK0518","BK0519","BK0520","BK0521",
    "BK0522","BK0523","BK0524","BK0525","BK0526","BK0527","BK0528","BK0529","BK0530","BK0531",
    "BK0532","BK0533","BK0534","BK0535","BK0536","BK0537","BK0540","BK0541","BK0542","BK0543",
    "BK0544","BK0547","BK0548","BK0549","BK0550","BK0551","BK0552","BK0553","BK0554","BK0555",
    "BK0556","BK0557","BK0558","BK0559","BK0560","BK0561","BK0562","BK0563","BK0564","BK0565",
    "BK0566","BK0567","BK0568","BK0569","BK0570","BK0572","BK0596","BK0597","BK0598","BK0599",
    "BK0600","BK0601","BK0602","BK0603","BK0604","BK0605","BK0606","BK0607","BK0608","BK0609",
    "BK0610","BK0611","BK0612","BK0613","BK0614","BK0615","BK0616","BK0617","BK0618","BK0623",
    "BK0624","BK0625","BK0626","BK0627","BK0628","BK0629","BK0630","BK0631","BK0632","BK0633",
    "BK0634","BK0635","BK0636","BK0637","BK0638","BK0640","BK0641","BK0643","BK0644","BK0645",
    "BK0646","BK0647","BK0648","BK0649","BK0650","BK0651","BK0652","BK0653","BK0654","BK0655",
    "BK0656","BK0657","BK0658","BK0659","BK0660","BK0661","BK0662","BK0663","BK0664","BK0665",
    "BK0670","BK0685","BK0686","BK0687","BK0688","BK0690","BK0691","BK0692","BK0693","BK0694",
    "BK0695","BK0696","BK0697","BK0698",
]

HEADERS = {
    "Referer": "https://quote.eastmoney.com/",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}


def ensure_table():
    """创建板块日线历史表（如果不存在）"""
    db = get_db()
    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS sector_daily_history (
                id INTEGER PRIMARY KEY AUTO_INCREMENT,
                trade_date TEXT NOT NULL,
                board_code TEXT NOT NULL,
                sector_name TEXT,
                open REAL DEFAULT 0,
                close REAL DEFAULT 0,
                high REAL DEFAULT 0,
                low REAL DEFAULT 0,
                change_pct REAL DEFAULT 0,
                amount REAL DEFAULT 0,
                volume REAL DEFAULT 0,
                UNIQUE(trade_date, board_code)
            )
        """)
        try:
            db.execute("""
                CREATE INDEX idx_sector_date ON sector_daily_history(trade_date)
            """)
        except Exception:
            pass  # 索引可能已存在
        try:
            db.execute("""
                CREATE INDEX idx_sector_code ON sector_daily_history(board_code)
            """)
        except Exception:
            pass
        logger.info("✅ 表 sector_daily_history 已就绪")
    except Exception as e:
        logger.warning(f"建表跳过（可能已存在MySQL）: {e}")


def fetch_board_kline(board_code: str, start_date: str, end_date: str) -> List[Dict]:
    """
    拉取单个板块的日K线数据
    返回: [{date, open, close, high, low, change_pct, amount, volume}, ...]
    """
    url = (
        f"https://push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?secid=90.{board_code}"
        f"&fields1=f1,f2,f3,f4,f5,f6"
        f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
        f"&klt=101&fqt=1"
        f"&end={end_date}&lmt=500"
    )
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read().decode("utf-8"))
        if not data.get("data") or not data["data"].get("klines"):
            return []
        klines = data["data"]["klines"]
        results = []
        for line in klines:
            parts = line.split(",")
            if len(parts) < 11:
                continue
            date_str = parts[0].replace("-", "")
            # 只返回 start_date 之后的数据
            if date_str < start_date:
                continue
            results.append({
                "date": date_str,
                "open": float(parts[1]),
                "close": float(parts[2]),
                "high": float(parts[3]),
                "low": float(parts[4]),
                "change_pct": float(parts[7]),  # f57 = 涨跌幅
                "volume": float(parts[5]),       # f55 = 成交量
                "amount": float(parts[6]),       # f56 = 成交额
            })
        return results
    except Exception as e:
        logger.warning(f"  拉取 {board_code} 失败: {e}")
        return []


def get_board_name(code: str) -> str:
    """通过板块列表接口获取板块名称"""
    url = (
        f"https://push2.eastmoney.com/api/qt/ulist.np/get"
        f"?fltt=2&fields=f2,f3,f12,f14"
        f"&secids=90.{code}"
    )
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read().decode("utf-8"))
        diff = data.get("data", {}).get("diff", {})
        if isinstance(diff, dict) and diff:
            item = list(diff.values())[0]
            return item.get("f14", code)
        return code
    except Exception:
        return code


def batch_fetch_and_store(codes: List[str], start_date: str, end_date: str, board_type: str = "industry"):
    """批量拉取并存储板块K线数据"""
    db = get_db()
    total = len(codes)
    success = 0
    failed = 0
    total_rows = 0
    
    for idx, code in enumerate(codes):
        logger.info(f"[{idx+1}/{total}] {code} ({board_type})...")
        klines = fetch_board_kline(code, start_date, end_date)
        if not klines:
            failed += 1
            logger.warning(f"  ✗ 无数据")
            time.sleep(0.5)
            continue
        
        # 获取板块名称（只用第一个K线日期的名义）
        name = get_board_name(code)
        
        inserted = 0
        for k in klines:
            try:
                db.execute(
                    """INSERT OR IGNORE INTO sector_daily_history
                       (trade_date, board_code, sector_name, open, close, high, low,
                        change_pct, amount, volume)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (k["date"], code, name,
                     k["open"], k["close"], k["high"], k["low"],
                     k["change_pct"], k["amount"], k["volume"])
                )
                inserted += 1
            except Exception as e:
                logger.warning(f"    写入 {k['date']} 失败: {e}")
        
        success += 1
        total_rows += inserted
        logger.info(f"  ✓ 插入 {inserted} 条（共 {len(klines)} 条K线）, 名称: {name}")
        time.sleep(1)  # 避免请求过快
    
    return success, failed, total_rows


def get_missing_dates():
    """查看已有哪些日期的数据"""
    db = get_db()
    rows = db.fetchall(
        "SELECT trade_date, COUNT(DISTINCT board_code) FROM sector_daily_history GROUP BY trade_date ORDER BY trade_date"
    )
    return rows


def show_stats():
    """展示数据统计"""
    db = get_db()
    rows = get_missing_dates()
    total_codes_row = db.fetchone(
        "SELECT COUNT(DISTINCT board_code) FROM sector_daily_history"
    )
    total_codes = total_codes_row['COUNT(DISTINCT board_code)'] if total_codes_row else 0
    total_dates = len(rows)
    total_rows_row = db.fetchone(
        "SELECT COUNT(*) FROM sector_daily_history"
    )
    total_rows = total_rows_row['COUNT(*)'] if total_rows_row else 0
    
    print(f"\n📊 板块历史数据统计:")
    print(f"  总记录数: {total_rows}")
    print(f"  板块数: {total_codes}")
    print(f"  交易日数: {total_dates}")
    if rows:
        print(f"  日期范围: {rows[0]['trade_date']} ~ {rows[-1]['trade_date']}")
        print(f"  最近5日:")
        for r in rows[-5:]:
            print(f"    {r['trade_date']}: {r['COUNT(DISTINCT board_code)']} 个板块")


if __name__ == "__main__":
    import sys
    
    print("=" * 60)
    print("📈 板块历史数据补全工具")
    print("=" * 60)
    
    ensure_table()
    show_stats()
    
    today = datetime.now().strftime("%Y%m%d")
    
    # 默认补最近6个月的数据
    default_start = (datetime.now() - timedelta(days=180)).strftime("%Y%m%d")
    
    if len(sys.argv) > 1:
        start_date = sys.argv[1].replace("-", "")
    else:
        start_date = default_start
    
    end_date = sys.argv[2].replace("-", "") if len(sys.argv) > 2 else today
    
    print(f"\n🔄 数据范围: {start_date} ~ {end_date}")
    
    # 先拉行业板块
    print(f"\n🏭 === 行业板块（共 {len(BOARD_CODES)} 个）===")
    s, f, rows = batch_fetch_and_store(BOARD_CODES, start_date, end_date, "行业")
    print(f"\n  行业板块: 成功{s}个, 失败{f}个, 共{rows}条")
    
    show_stats()
