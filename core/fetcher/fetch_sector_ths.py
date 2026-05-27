"""
板块历史数据补全脚本（AKShare 同花顺版）
补全 sector_daily_history 表的行业板块日K线数据
"""
import os
import sys
import time
import logging
from datetime import datetime, timedelta

# 确保能找到项目模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.dao import get_db
import akshare as ak

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)


def ensure_table():
    """确保 sector_daily_history 表存在（兼容旧表结构）"""
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
                UNIQUE(board_code, trade_date)
            )
        """)
        logger.info("✅ 表 sector_daily_history 已就绪")
    except Exception as e:
        logger.warning(f"建表跳过（可能已存在MySQL）: {e}")


def get_board_list():
    """从同花顺获取行业板块列表"""
    try:
        df = ak.stock_board_industry_name_ths()
        boards = df.to_dict('records')
        return boards
    except Exception as e:
        logger.error(f"获取板块列表失败: {e}")
        return []


def get_existing_dates(code: str) -> set:
    """获取数据库中该板块已有的日期"""
    db = get_db()
    rows = db.fetchall(
        "SELECT trade_date FROM sector_daily_history WHERE board_code=%s",
        (code,)
    )
    return {r['trade_date'] for r in rows}


def fetch_sector_kline(name: str, start: str, end: str) -> list:
    """
    用 AKShare 同花顺接口拉单个板块历史K线
    返回: [{trade_date, open, close, high, low, volume, amount}, ...]
    """
    try:
        df = ak.stock_board_industry_index_ths(symbol=name, start_date=start, end_date=end)
        if df is None or df.empty:
            return []
        
        results = []
        for _, row in df.iterrows():
            date_str = str(row['日期']).replace('-', '')
            results.append({
                'trade_date': date_str,
                'open_price': float(row.get('开盘价', 0)),
                'close_price': float(row.get('收盘价', 0)),
                'high_price': float(row.get('最高价', 0)),
                'low_price': float(row.get('最低价', 0)),
                'volume': float(row.get('成交量', 0)),
                'amount': float(row.get('成交额', 0)),
            })
        return results
    except Exception as e:
        logger.warning(f"  ✗ 拉取 {name} 失败: {e}")
        return []


def save_to_db(records: list, name: str, code: str):
    """写入数据库"""
    if not records:
        return 0
    
    db = get_db()
    inserted = 0
    for r in records:
        try:
            db.execute("""
                INSERT OR IGNORE INTO sector_daily_history
                (board_code, sector_name, trade_date, open, close, 
                 high, low, volume, amount)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                code, name, r['trade_date'],
                r['open_price'], r['close_price'],
                r['high_price'], r['low_price'],
                r['volume'], r['amount']
            ))
            inserted += 1
        except Exception as e:
            logger.warning(f"  写入失败: {e}")
    return inserted


def batch_fetch_all(start_date: str, end_date: str, sleep_sec: float = 1.0):
    """批量拉取所有行业板块"""
    ensure_table()
    boards = get_board_list()
    
    if not boards:
        logger.error("⚠️ 未获取到板块列表，退出！")
        return
    
    logger.info(f"📋 共 {len(boards)} 个行业板块，拉取 {start_date} ~ {end_date} 数据")
    
    total_success = 0
    total_inserted = 0
    total_failed = 0
    total_skipped = 0
    
    for i, board in enumerate(boards):
        name = board['name']
        code = str(board['code'])
        
        # 检查已有数据
        existing = get_existing_dates(code)
        
        logger.info(f"[{i+1}/{len(boards)}] {name}({code})...")
        
        records = fetch_sector_kline(name, start_date, end_date)
        
        if not records:
            total_failed += 1
            continue
        
        # 过滤已有日期
        new_records = [r for r in records if r['trade_date'] not in existing]
        skipped = len(records) - len(new_records)
        
        if not new_records:
            total_skipped += 1
            if skipped > 0:
                logger.info(f"  ⏭️  全部已存在 (跳过{skipped}条)")
            else:
                logger.info(f"  ⏭️  无新数据")
            continue
        
        inserted = save_to_db(new_records, name, code)
        total_inserted += inserted
        total_success += 1
        if skipped > 0:
            logger.info(f"  ✅ 写入{inserted}条 (跳过{skipped}条)")
        else:
            logger.info(f"  ✅ 写入{inserted}条")
        
        time.sleep(sleep_sec)
    
    logger.info(f"\n{'='*50}")
    logger.info(f"🏁 批量拉取完成！")
    logger.info(f"  成功: {total_success} | 失败: {total_failed} | 跳过: {total_skipped}")
    logger.info(f"  新增记录: {total_inserted} 条")
    logger.info(f"{'='*50}")


def show_stats():
    """展示数据统计"""
    db = get_db()
    try:
        rows = db.fetchall(
            "SELECT trade_date, COUNT(DISTINCT board_code) FROM sector_daily_history GROUP BY trade_date ORDER BY trade_date"
        )
        total_codes_row = db.fetchone(
            "SELECT COUNT(DISTINCT board_code) FROM sector_daily_history"
        )
        total_codes = total_codes_row['COUNT(DISTINCT board_code)'] if total_codes_row else 0
        total_rows_row = db.fetchone(
            "SELECT COUNT(*) FROM sector_daily_history"
        )
        total_rows = total_rows_row['COUNT(*)'] if total_rows_row else 0
    except Exception as e:
        logger.error(f"统计失败: {e}")
        return
    
    print(f"\n📊 板块历史数据统计:")
    print(f"  总记录数: {total_rows}")
    print(f"  板块数: {total_codes}")
    print(f"  交易日数: {len(rows)}")
    if rows:
        print(f"  日期范围: {rows[0]['trade_date']} ~ {rows[-1]['trade_date']}")
        print(f"  最近5日:")
        for r in rows[-5:]:
            print(f"    {r['trade_date']}: {r['COUNT(DISTINCT board_code)']} 个板块")


if __name__ == "__main__":
    import sys
    
    print("=" * 60)
    print("📈 板块历史数据补全工具 (AKShare 同花顺版)")
    print("=" * 60)
    
    if len(sys.argv) > 1:
        start_date = sys.argv[1].replace("-", "")
    else:
        # 默认近6个月
        start_date = (datetime.now() - timedelta(days=180)).strftime("%Y%m%d")
    
    if len(sys.argv) > 2:
        end_date = sys.argv[2].replace("-", "")
    else:
        end_date = datetime.now().strftime("%Y%m%d")
    
    print(f"🔄 数据范围: {start_date} ~ {end_date}")
    
    batch_fetch_all(start_date, end_date, sleep_sec=1.0)
    show_stats()
