"""
F1 市场主线识别模块
从 daily_limit_up / sector_performance 识别当日市场主线板块及运行模式

输出：
  mode: 'strong_trend' / 'pullback' / 'rotation'
  main_lines: [{sector, score, zt_count, max_board, change_pct}]
"""

import sys
import os
from datetime import datetime, timedelta
from typing import Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from utils.logger import setup_logger
from utils.dao import get_db

logger = setup_logger("F1_市场主线识别")


def identify_main_lines(trade_date: Optional[str] = None) -> dict:
    """
    识别当前市场主线

    Args:
        trade_date: YYYYMMDD 格式，默认今天

    Returns:
        dict: {
            'mode': 'strong_trend' | 'pullback' | 'rotation',
            'main_lines': [
                {'sector': str, 'score': float, 'zt_count': int,
                 'max_board': int, 'change_pct': float},
            ],
            'top_sectors': [...],  # 全部板块完整排序
            'mode_reason': str,    # 判定理由
        }
    """
    if trade_date is None:
        trade_date = datetime.now().strftime('%Y%m%d')

    db = get_db()

    # ① 当日行业涨停分布
    sector_zt = db.fetchall("""
        SELECT industry, COUNT(*) as cnt,
               MAX(board_times) as max_board
        FROM daily_limit_up
        WHERE trade_date = %s
          AND (status IS NULL OR status != '跌停')
          AND industry IS NOT NULL AND industry != ''
        GROUP BY industry
        ORDER BY cnt DESC
    """, (trade_date,))

    # ② 板块涨幅
    today_dash = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"
    sector_perf = db.fetchall("""
        SELECT sector_name, change_pct
        FROM sector_performance
        WHERE record_date = %s
        ORDER BY change_pct DESC
    """, (today_dash,))

    # 构建板块涨幅 map
    perf_map = {}
    for s in sector_perf:
        name = s['sector_name'].strip()
        perf_map[name] = float(s['change_pct']) if s['change_pct'] else 0

    # ③ 综合评分
    sectors = []
    for zt in sector_zt:
        ind = zt['industry'].strip()
        zt_cnt = int(zt['cnt'])
        max_b = int(zt['max_board']) if zt['max_board'] else 1

        chg = perf_map.get(ind, 0)

        # 涨停数评分：满分12只=100分
        zt_score = min(100, zt_cnt / 12 * 100)
        # 涨幅评分：满分5%=100分
        chg_score = min(100, chg / 5 * 100)
        # 连板评分：满分7板=100分
        board_score = min(100, max_b / 7 * 100)

        total = round(zt_score * 0.4 + chg_score * 0.3 + board_score * 0.3, 1)
        sectors.append({
            'sector': ind,
            'score': total,
            'zt_count': zt_cnt,
            'max_board': max_b,
            'change_pct': chg,
        })

    sectors.sort(key=lambda x: x['score'], reverse=True)
    top3 = sectors[:3]
    top5 = sectors[:5]

    # ④ 判断模式
    # 强主线条件：前3板块中得分>=80且涨停数>=8
    strong = [s for s in top3 if s['score'] >= 80 and s['zt_count'] >= 8]
    # 正常主线条件：得分>=60且涨停数>=3
    normal = [s for s in top3 if s['score'] >= 60 and s['zt_count'] >= 3]

    if strong:
        mode = 'strong_trend'
        mode_reason = f"强主线模式：{', '.join(s['sector'] for s in strong)}表现强势"
    elif normal:
        # 检查主线回调（前三日曾有≥5涨停板块，今日仍≥2但<5）
        pullback_sectors = _check_pullback_sectors(db, trade_date)
        if pullback_sectors:
            mode = 'pullback'
            mode_reason = f"主线回调模式：{', '.join(s['sector'] for s in pullback_sectors[:2])}今日涨停减少但仍在"
        else:
            mode = 'strong_trend'
            mode_reason = f"正常主线模式：{', '.join(s['sector'] for s in normal)}保持热度"
    else:
        mode = 'rotation'
        mode_reason = "无主线轮动模式：板块涨停分散，无明显主线"

    db.close()

    return {
        'mode': mode,
        'main_lines': top3,
        'top_sectors': sectors,
        'mode_reason': mode_reason,
    }


def _check_pullback_sectors(db, trade_date: str) -> list:
    """
    检查主线回调：
    前3日中至少有2天涨停数≥5的板块，今日涨停数仍≥2但<5

    Returns:
        符合条件的板块列表 [{sector, zt_count, prev_zt_count}, ...]
    """
    td = datetime.strptime(trade_date, '%Y%m%d')
    result = []

    # 取前3日
    prev_dates = []
    for i in range(1, 8):
        d = td - timedelta(days=i)
        ds = d.strftime('%Y%m%d')
        r = db.fetchone(
            "SELECT COUNT(*) as c FROM daily_limit_up WHERE trade_date=%s "
            "AND (status IS NULL OR status != '跌停')",
            (ds,))
        if r and r['c'] > 5:
            prev_dates.append(ds)
        if len(prev_dates) >= 3:
            break

    if not prev_dates:
        return []

    # 查前3日各板块涨停数
    for pd in prev_dates:
        rows = db.fetchall("""
            SELECT industry, COUNT(*) as cnt
            FROM daily_limit_up
            WHERE trade_date=%s
              AND (status IS NULL OR status != '跌停')
              AND industry IS NOT NULL AND industry != ''
            GROUP BY industry
            HAVING cnt >= 5
        """, (pd,))
        for r in rows:
            sector = r['industry'].strip()
            prev_cnt = int(r['cnt'])

            # 查今日该板块涨停数
            today_zt = db.fetchone("""
                SELECT COUNT(*) as cnt
                FROM daily_limit_up
                WHERE trade_date=%s
                  AND industry=%s
                  AND (status IS NULL OR status != '跌停')
            """, (trade_date, sector))
            today_cnt = int(today_zt['cnt']) if today_zt and today_zt['cnt'] else 0

            if 2 <= today_cnt < 5:
                result.append({
                    'sector': sector,
                    'zt_count': today_cnt,
                    'prev_zt_count': prev_cnt,
                })

    # 去重
    seen = set()
    unique = []
    for s in result:
        if s['sector'] not in seen:
            seen.add(s['sector'])
            unique.append(s)
    return unique


if __name__ == '__main__':
    result = identify_main_lines()
    print(f"模式: {result['mode']}")
    print(f"理由: {result['mode_reason']}")
    for s in result['main_lines']:
        print(f"  {s['sector']}: 得分{s['score']} 涨停{s['zt_count']} 连板{s['max_board']} 涨幅{s['change_pct']:+.2f}%")
