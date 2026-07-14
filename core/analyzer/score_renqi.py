"""
人气辨识度评分 V7（满分25分）← 修正版
评估个股的市场辨识度和人气活跃度

v7 修正重点：不再奖励T日涨停，改为看近10日连板历史

子维度：
  - 涨停辨识度(12分)：近10日涨停次数（当天涨停不计入加分）
  - 板块龙头性(8分)：近3日板块排位，有连板历史的可计入
  - 股性活跃度(5分)：近10日均振幅

输入：code + trade_date
输出：{renqi: float, details: dict}
"""

import sys
import os
from datetime import datetime, timedelta
from typing import Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from utils.logger import setup_logger
from utils.dao import get_db

logger = setup_logger("score_renqi")


def score_renqi(code: str, trade_date: Optional[str] = None,
                main_lines_info: Optional[dict] = None) -> dict:
    """
    人气辨识度评分（v7修正版）
    ★ 必须与T日是否涨停脱钩

    Args:
        code: 股票代码
        trade_date: YYYYMMDD，默认今天
        main_lines_info: F1_市场主线识别 的输出（可选，辅助判断板块龙头性）

    Returns:
        {renqi: float, details: dict}
    """
    if trade_date is None:
        trade_date = datetime.now().strftime('%Y%m%d')

    db = get_db()
    details = {}
    total = 0.0

    # ─────────────── 1. 涨停辨识度(12分) ───────────────
    # ★ v7修正：看近10日涨停次数（不含T日），次数越多分越高
    ten_days_ago = (datetime.strptime(trade_date, '%Y%m%d') - timedelta(days=15)).strftime('%Y%m%d')
    zt_rows = db.fetchall("""
        SELECT trade_date, board_times, status
        FROM daily_limit_up
        WHERE code=%s AND trade_date>=%s AND trade_date<%s
          AND (status IS NULL OR status != '跌停')
        ORDER BY trade_date DESC
    """, (code, ten_days_ago, trade_date))

    zt_count = len(zt_rows)
    zt_score = 0
    if zt_count >= 4:
        zt_score = 12
        zt_detail = f"近10日涨停{zt_count}次（4次+，辨识度极高）"
    elif zt_count == 3:
        zt_score = 10
        zt_detail = f"近10日涨停{zt_count}次（3次，辨识度高）"
    elif zt_count == 2:
        zt_score = 7
        zt_detail = f"近10日涨停{zt_count}次（2次，有一定辨识度）"
    elif zt_count == 1:
        zt_score = 4
        zt_detail = f"近10日涨停{zt_count}次（1次，辨识度一般）"
    else:
        zt_score = 0
        zt_detail = "近10日无涨停（辨识度低）"

    details['zt_identify'] = {
        'score': zt_score,
        'max_score': 12,
        'detail': zt_detail,
    }
    total += zt_score

    # ─────────────── 2. 板块龙头性(8分) ───────────────
    # ★ v7修正：看近3日板块内排位，不局限于T日涨停
    lead_score = 0
    lead_detail = "非龙头，无连板历史"

    # 查近3日该股在板块内的排位
    three_days_ago = (datetime.strptime(trade_date, '%Y%m%d') - timedelta(days=5)).strftime('%Y%m%d')

    # 获取该股行业
    industry = _get_stock_industry(db, code, trade_date)

    if industry:
        # 查近3日该板块涨停情况
        sector_zt_3d = db.fetchall("""
            SELECT code, name, board_times, seal_first_time, trade_date
            FROM daily_limit_up
            WHERE trade_date>=%s AND trade_date<=%s
              AND industry=%s
              AND (status IS NULL OR status != '跌停')
            ORDER BY trade_date DESC, board_times DESC, seal_first_time ASC
        """, (three_days_ago, trade_date, industry))

        if sector_zt_3d:
            # 统计该股在板块内的表现
            my_zt_3d = [s for s in sector_zt_3d if s['code'] == code]
            unique_stocks = list(dict.fromkeys(s['code'] for s in sector_zt_3d))
            my_total_zt = len(my_zt_3d)

            if my_total_zt > 0:
                my_max_board = max(int(s['board_times']) if s['board_times'] else 1 for s in my_zt_3d)
            else:
                my_max_board = 0

            # 查最高连板
            all_boards = []
            for s in sector_zt_3d:
                b = int(s['board_times']) if s['board_times'] else 1
                if s['code'] == code:
                    all_boards.append(('my', b))
                else:
                    all_boards.append(('other', b))

            max_my_board = max(b for t, b in all_boards if t == 'my') if any(t == 'my' for t, _ in all_boards) else 0
            max_all_board = max(b for _, b in all_boards) if all_boards else 0

            my_position = 0
            if max_my_board > 0:
                # 按连板数排序查看该股在板块内的位置
                stock_ranks = []
                seen_codes = set()
                for s in sector_zt_3d:
                    if s['code'] not in seen_codes:
                        seen_codes.add(s['code'])
                        b = int(s['board_times']) if s['board_times'] else 1
                        stock_ranks.append((s['code'], b, s['seal_first_time'] or ''))
                stock_ranks.sort(key=lambda x: (-x[1], x[2]))
                for idx, (sc, sb, st) in enumerate(stock_ranks):
                    if sc == code:
                        my_position = idx + 1
                        break

            # 评分
            if max_my_board >= 3:
                lead_score = 8
                lead_detail = f"近3日板块内最高连板{max_my_board}板（龙头判定）"
            elif max_my_board == 2:
                lead_score = 7
                lead_detail = f"近3日2连板，板块内有龙头属性"
            elif my_total_zt >= 2 and my_position <= 3:
                lead_score = 6
                lead_detail = f"近3日板块内排第{my_position}（多次涨停+前排）"
            elif my_total_zt >= 1 and my_position <= 2:
                lead_score = 5
                lead_detail = f"近3日板块内排第{my_position}（涨停+前排）"
            elif my_total_zt >= 1:
                lead_score = 3
                lead_detail = f"近3日板块内涨停{my_total_zt}次（跟风）"
            else:
                lead_score = 1
                lead_detail = f"近3日有涨停但不在该板块"
        else:
            lead_score = 0
            lead_detail = "近3日板块无涨停"
    else:
        lead_score = 0
        lead_detail = "无行业归属"

    details['leadership'] = {
        'score': lead_score,
        'max_score': 8,
        'detail': lead_detail,
    }
    total += lead_score

    # ─────────────── 3. 股性活跃度(5分) ───────────────
    # 与v6一致：近10日均振幅>5%加分
    fifteen_days_ago = (datetime.strptime(trade_date, '%Y%m%d') - timedelta(days=15)).strftime('%Y%m%d')
    klines = db.fetchall("""
        SELECT high, low
        FROM stock_daily
        WHERE code=%s AND trade_date>=%s AND trade_date<=%s
          AND high>0 AND low>0
        ORDER BY trade_date DESC
        LIMIT 10
    """, (code, fifteen_days_ago, trade_date))

    act_score = 0
    act_detail = "数据不足"
    if klines and len(klines) >= 3:
        amplitudes = []
        for k in klines:
            high = float(k['high'])
            low = float(k['low'])
            if low > 0:
                amplitudes.append((high - low) / low * 100)
        if amplitudes:
            avg_amp = sum(amplitudes) / len(amplitudes)
            if avg_amp > 5:
                act_score = 5
                act_detail = f"近10日均振幅{avg_amp:.1f}% (>5%，非常活跃)"
            elif avg_amp > 3:
                act_score = 3
                act_detail = f"近10日均振幅{avg_amp:.1f}% (>3%，较活跃)"
            else:
                act_score = 0
                act_detail = f"近10日均振幅{avg_amp:.1f}% (≤3%，不够活跃)"
    else:
        act_score = 0
        act_detail = "K线数据不足"

    details['activity'] = {
        'score': act_score,
        'max_score': 5,
        'detail': act_detail,
    }
    total += act_score

    db.close()

    return {
        'renqi': round(total, 1),
        'details': details,
    }


def _get_stock_industry(db, code: str, trade_date: str) -> Optional[str]:
    """获取股票所属行业"""
    r = db.fetchone(
        "SELECT industry FROM daily_limit_up WHERE code=%s AND trade_date=%s AND industry!='' LIMIT 1",
        (code, trade_date))
    if r and r['industry']:
        return r['industry'].strip()
    r = db.fetchone(
        "SELECT industry FROM daily_limit_up WHERE code=%s AND industry!='' "
        "ORDER BY trade_date DESC LIMIT 1",
        (code,))
    if r and r['industry']:
        return r['industry'].strip()
    return None


if __name__ == '__main__':
    test_codes = ['600839', '000858']
    for c in test_codes:
        r = score_renqi(c)
        print(f"{c}: 人气辨识度 {r['renqi']}/25")
        for k, v in r['details'].items():
            print(f"  {k}: {v['score']}/{v['max_score']} - {v['detail']}")
        print()
