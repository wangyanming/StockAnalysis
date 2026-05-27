"""
批量拉取历史涨停板数据到数据库
"""
import os
import sys
import time
from datetime import datetime, date, timedelta
import logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dao import get_db
from limit_up_analysis import LimitUpAnalyzer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)


def get_existing_dates(db_path="stock_data.db") -> set:
    """获取已有数据的日期集合"""
    existing = set()
    try:
        db = get_db()
        rows = db.fetchall("SELECT DISTINCT trade_date FROM daily_limit_up")
        for r in rows:
            existing.add(r["trade_date"])
    except Exception:
        pass
    return existing


def is_trading_day(d: date) -> bool:
    """是否为交易日（周一到周五，排除法定节假日粗略）"""
    if d.weekday() >= 5:
        return False
    # 排除一些已知非交易日（可扩展）
    holidays = [
        # 元旦
        (2026, 1, 1), (2026, 1, 2),
        # 春节
        (2026, 2, 16), (2026, 2, 17), (2026, 2, 18), (2026, 2, 19), (2026, 2, 20),
        (2026, 2, 21), (2026, 2, 22),
        # 清明
        (2026, 4, 4), (2026, 4, 5), (2026, 4, 6),
        # 劳动节
        (2026, 5, 1), (2026, 5, 2), (2026, 5, 3), (2026, 5, 4), (2026, 5, 5),
        # 端午
        (2026, 6, 25), (2026, 6, 26), (2026, 6, 27),
        # 中秋+国庆
        (2026, 9, 27), (2026, 9, 28), (2026, 9, 29), (2026, 9, 30),
        (2026, 10, 1), (2026, 10, 2), (2026, 10, 3), (2026, 10, 4), (2026, 10, 5), (2026, 10, 6), (2026, 10, 7),
    ]
    if (d.year, d.month, d.day) in holidays:
        return False
    return True


def batch_fetch(start_date: date, end_date: date):
    """批量拉取涨停数据"""
    analyzer = LimitUpAnalyzer()
    existing = get_existing_dates()

    total = 0
    success = 0
    skipped = 0
    failed = 0

    d = start_date
    while d <= end_date:
        if not is_trading_day(d):
            d += timedelta(days=1)
            continue

        date_str = d.strftime("%Y%m%d")
        total += 1

        if date_str in existing:
            logger.info(f"  ⏭️  {date_str} 已存在，跳过")
            skipped += 1
            d += timedelta(days=1)
            continue

        try:
            logger.info(f"  📥 拉取 {date_str} ({d.strftime('%Y-%m-%d')})...")
            result = analyzer.run_daily_analysis(date_str)
            cnt = result.get("count", 0)
            if cnt > 0:
                success += 1
                logger.info(f"     ✅ {cnt} 只涨停")
            else:
                skipped += 1
                logger.info(f"     ⏭️  无涨停数据（非交易日或数据为空）")
            time.sleep(1.5)  # 避免请求过快
        except Exception as e:
            logger.error(f"     ❌ {e}")
            failed += 1
            time.sleep(3)

        d += timedelta(days=1)

    logger.info(f"\n{'='*50}")
    logger.info(f"拉取完成！")
    logger.info(f"  交易日: {total}")
    logger.info(f"  成功:   {success}")
    logger.info(f"  跳过:   {skipped}")
    logger.info(f"  失败:   {failed}")
    logger.info(f"{'='*50}")


if __name__ == "__main__":
    today = date.today()
    start = date(today.year, 1, 1)

    logger.info(f"开始批量拉取 {start} ~ {today} 的涨停板数据")
    batch_fetch(start, today)
