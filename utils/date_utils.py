"""
日期工具函数

提供 get_display_date() 统一展示日期规则：
- 17:00 前 → 展示 T-1 日（昨天收盘数据）
- 17:00 后 → 展示 T 日（今天收盘数据）
- 非交易日自动往前递推（最多 30 步）
"""

from datetime import datetime, timedelta


def _is_trade_date(date_str: str) -> bool:
    """
    判断日期是否为交易日（在 stock_daily 表有数据）

    Args:
        date_str: YYYYMMDD 格式的日期字符串

    Returns:
        True 如果是交易日，False 否则
    """
    from utils.dao import get_db
    db = get_db()
    row = db.fetchone(
        "SELECT 1 FROM stock_daily WHERE trade_date = %s LIMIT 1",
        (date_str,)
    )
    return row is not None


def get_display_date() -> str:
    """
    返回当前应展示的日期（YYYYMMDD 格式）

    规则：
    - 当前时间 ≥ 17:00 → 目标 = 今天（T 日）
    - 当前时间 < 17:00 → 目标 = 昨天（T-1 日）
    - 如果目标日期不是交易日，逐日往前找到最近有数据的交易日
    - 最多递推 30 步，防止死循环

    Returns:
        YYYYMMDD 格式的日期字符串
    """
    now = datetime.now()

    # 确定目标日期
    if now.hour >= 17:
        target = now
    else:
        target = now - timedelta(days=1)

    # 非交易日往前递推（最多推 30 天防止死循环）
    for _ in range(30):
        target_str = target.strftime('%Y%m%d')
        if _is_trade_date(target_str):
            return target_str
        target -= timedelta(days=1)

    # 兜底：返回 30 天前的日期（极小概率触发）
    return target.strftime('%Y%m%d')
