"""
风险过滤评分（满分10分，可扣负分）
统一风险判断，一票否决项返回 -100

一票否决：
  - ST/退市/停牌
扣分项：
  - 市值<30亿
  - 近3日累计跌>12%且无支撑
  - 所属行业板块当日跌幅>3%
"""

import sys
import os
from datetime import datetime, timedelta
from typing import Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from utils.logger import setup_logger
from utils.dao import get_db

logger = setup_logger("score_risk")


def score_risk(code: str, trade_date: Optional[str] = None) -> dict:
    """
    风险过滤评分

    Args:
        code: 股票代码
        trade_date: YYYYMMDD，默认今天

    Returns:
        {risk: float, veto: bool, details: list}
        - veto=True 表示一票否决，score = -100
    """
    if trade_date is None:
        trade_date = datetime.now().strftime('%Y%m%d')

    db = get_db()
    details = []
    score = 10.0  # 从10分开始扣
    veto = False

    # ─────────────── 获取基本信息 ───────────────
    today = db.fetchone("""
        SELECT code, name, close, turnover_rate, total_market_cap, change_pct
        FROM stock_daily
        WHERE code=%s AND trade_date=%s
    """, (code, trade_date))

    if not today:
        db.close()
        return {'risk': -100, 'veto': True, 'details': ['查不到该股票当日数据']}

    name = str(today['name']) if today['name'] else ''
    total_market_cap = float(today['total_market_cap']) if today['total_market_cap'] else 0

    # ─────────────── 1. ST/退市/停牌 一票否决 ───────────────
    if 'ST' in name or '退' in name or '*ST' in name or 'SST' in name:
        details.append(f"一票否决：{name}为ST/退市股")
        veto = True
        score = -100

    if not veto:
        # 检查是否停牌（close=0 或 volume=0）
        close = float(today['close']) if today['close'] else 0
        if close <= 0:
            details.append("一票否决：停牌")
            veto = True
            score = -100

    if veto:
        db.close()
        return {'risk': -100, 'veto': True, 'details': details}

    # ─────────────── 2. 市值<30亿 扣分 ───────────────
    if total_market_cap < 3_000_000_000:
        score -= 5
        details.append(f"市值{total_market_cap / 1e8:.0f}亿<30亿，-5分")

    # ─────────────── 3. 近3日累计跌>12% 扣分 ───────────────
    three_days_ago = (datetime.strptime(trade_date, '%Y%m%d') - timedelta(days=5)).strftime('%Y%m%d')
    recent_klines = db.fetchall("""
        SELECT trade_date, change_pct, close
        FROM stock_daily
        WHERE code=%s AND trade_date>=%s AND trade_date<=%s
          AND close>0
        ORDER BY trade_date DESC
        LIMIT 3
    """, (code, three_days_ago, trade_date))

    if len(recent_klines) >= 3:
        chgs_3d = [float(k['change_pct']) for k in recent_klines if k['change_pct']]
        total_chg_3d = sum(chgs_3d) if len(chgs_3d) >= 3 else 0
        if total_chg_3d < -12:
            score -= 5
            details.append(f"近3日累计跌{total_chg_3d:.1f}%>12%，-5分")

    # ─────────────── 4. 所属行业板块当日跌幅>3% 扣分 ───────────────
    # 获取个股所属行业
    industry = None
    ind_row = db.fetchone(
        "SELECT industry FROM daily_limit_up WHERE code=%s AND trade_date=%s AND industry!='' LIMIT 1",
        (code, trade_date))
    if ind_row and ind_row['industry']:
        industry = ind_row['industry']
    else:
        ind_row = db.fetchone(
            "SELECT industry FROM daily_limit_up WHERE code=%s AND industry!='' "
            "ORDER BY trade_date DESC LIMIT 1", (code,))
        if ind_row and ind_row['industry']:
            industry = ind_row['industry']

    if industry:
        today_dash = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"
        sector_row = db.fetchone("""
            SELECT change_pct FROM sector_performance
            WHERE record_date=%s AND sector_name=%s
        """, (today_dash, industry.strip()))
        if sector_row and sector_row['change_pct']:
            sector_chg = float(sector_row['change_pct'])
            if sector_chg < -3:
                score -= 5
                details.append(f"所属行业{industry}当日跌{sector_chg:.1f}%>3%，-5分")

    # ─────────────── 5. 负分截断 ───────────────
    score = max(score, -20)

    db.close()

    return {
        'risk': score,
        'veto': False,
        'details': details if details else ['无风险扣分'],
        'deduction': 10 - score,  # 扣除的总分
    }


if __name__ == '__main__':
    test_codes = ['600839', '000858', '000001']
    for c in test_codes:
        r = score_risk(c)
        print(f"{c}: risk={r['risk']} veto={r['veto']}")
        for d in r['details']:
            print(f"  - {d}")
        print()
