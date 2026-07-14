"""
主线属性评分 V7（满分25分）← 修正版
评估个股在当前市场主线中的定位

v7 修正重点：板块地位看近3日排位，不再依赖T日是否涨停

子维度：
  - 主线锚定(15分)：个股所在板块是否在主线TOP3/TOP5/TOP10
  - 板块地位(10分)：近3日板块内龙头/前排/后排（不依赖T日涨停）

依赖：F1_市场主线识别 的输出
"""

import sys
import os
from datetime import datetime, timedelta
from typing import Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from utils.logger import setup_logger
from utils.dao import get_db

logger = setup_logger("score_zhuxian")


def score_zhuxian(code: str, trade_date: Optional[str] = None,
                  main_lines_info: Optional[dict] = None) -> dict:
    """
    主线属性评分（v7修正版）
    ★ 板块地位看近3日排位，不依赖T日涨停

    Args:
        code: 股票代码
        trade_date: YYYYMMDD，默认今天
        main_lines_info: F1_市场主线识别 的输出
            {'mode': str, 'main_lines': list, 'top_sectors': list, ...}

    Returns:
        {zhuxian: float, details: dict}
    """
    if trade_date is None:
        trade_date = datetime.now().strftime('%Y%m%d')

    db = get_db()
    details = {}
    total = 0.0

    # 获取个股行业归属
    industry = _get_stock_industry(db, code, trade_date)
    if not industry:
        db.close()
        return {'zhuxian': 0, 'details': {
            'anchor': {'score': 0, 'max_score': 15, 'detail': '未找到行业归属'},
            'position': {'score': 0, 'max_score': 10, 'detail': '无行业信息'},
        }}

    # ─────────────── 1. 主线锚定(15分) ───────────────
    # 与v6一致
    if main_lines_info and main_lines_info.get('top_sectors'):
        top_sectors = main_lines_info['top_sectors']
        match_pos = None
        for i, sec in enumerate(top_sectors):
            if sec['sector'].strip() == industry.strip():
                match_pos = i
                break

        if match_pos is not None:
            rank = match_pos + 1
            if rank <= 3:
                anchor_score = 15
                anchor_detail = f"所在板块{industry}在主线TOP3 (排名第{rank})"
            elif rank <= 5:
                anchor_score = 10
                anchor_detail = f"所在板块{industry}在主线TOP5 (排名第{rank})"
            elif rank <= 10:
                anchor_score = 5
                anchor_detail = f"所在板块{industry}在主线TOP10 (排名第{rank})"
            else:
                anchor_score = 0
                anchor_detail = f"所在板块{industry}未进入主线TOP10"
        else:
            anchor_score = 0
            anchor_detail = f"所在板块{industry}不在此次主线中"
    elif main_lines_info and main_lines_info.get('main_lines'):
        is_main = any(sec['sector'].strip() == industry.strip()
                      for sec in main_lines_info['main_lines'])
        anchor_score = 15 if is_main else 0
        anchor_detail = f"所在板块{'在' if is_main else '不在'}主线TOP3中"
    else:
        anchor_score = 0
        anchor_detail = "无主线信息，无法锚定"

    details['anchor'] = {
        'score': anchor_score,
        'max_score': 15,
        'detail': anchor_detail,
    }
    total += anchor_score

    # ─────────────── 2. 板块地位(10分) ───────────────
    # ★ v7修正：看近3日排位，不是只看T日
    # 只看T日的涨停票在位置判定上会占优势
    # 改为：近3日内，如果该股有连板/最先封板记录，则算龙头
    pos_score = 0
    pos_detail = "非主线板块或板块内非前排"

    if main_lines_info and any(
        sec['sector'].strip() == industry.strip()
        for sec in (main_lines_info.get('top_sectors', []) or [])
    ):
        # 仅在主线板块内评估地位
        three_days_ago = (datetime.strptime(trade_date, '%Y%m%d') - timedelta(days=5)).strftime('%Y%m%d')

        # 查近3日该板块所有涨停及个股排位
        sector_zt_3d = db.fetchall("""
            SELECT code, name, board_times, seal_first_time, trade_date
            FROM daily_limit_up
            WHERE trade_date>=%s AND trade_date<=%s
              AND industry=%s
              AND (status IS NULL OR status != '跌停')
            ORDER BY trade_date DESC, board_times DESC, seal_first_time ASC
        """, (three_days_ago, trade_date, industry))

        if sector_zt_3d:
            # 该股在近3日的涨停记录
            my_zt = [s for s in sector_zt_3d if s['code'] == code]
            unique_stocks = list(dict.fromkeys(s['code'] for s in sector_zt_3d))

            if my_zt:
                my_max_board = max(int(s['board_times']) if s['board_times'] else 1 for s in my_zt)
                my_zt_cnt = len(my_zt)

                # 板块内最高连板
                max_board_in_sector = max(
                    int(s['board_times']) if s['board_times'] else 1 for s in sector_zt_3d
                )

                # 计算该股排位
                stock_ranks = []
                seen_codes = set()
                for s in sector_zt_3d:
                    if s['code'] not in seen_codes:
                        seen_codes.add(s['code'])
                        b = int(s['board_times']) if s['board_times'] else 1
                        stock_ranks.append((s['code'], b, s['seal_first_time'] or ''))
                stock_ranks.sort(key=lambda x: (-x[1], x[2]))

                my_position = 0
                for idx, (sc, _, _) in enumerate(stock_ranks):
                    if sc == code:
                        my_position = idx + 1
                        break

                # 评分：近3日首板（最先封板）或最高连板
                if my_max_board >= 3:
                    pos_score = 10
                    pos_detail = f"板块龙头（近3日最高连板{my_max_board}板）"
                elif my_max_board == 2:
                    pos_score = 8
                    pos_detail = f"板块前排（近3日2连板，排第{my_position}）"
                elif my_max_board == 1 and my_position <= 1:
                    pos_score = 8
                    pos_detail = f"板块前排（最早涨停）"
                elif my_zt_cnt >= 2:
                    pos_score = 7
                    pos_detail = f"板块前排（近3日涨停{my_zt_cnt}次，排第{my_position}）"
                elif my_position <= 3:
                    pos_score = 6
                    pos_detail = f"板块前排（近3日排第{my_position}）"
                else:
                    pos_score = 4
                    pos_detail = f"板块跟风（近3日排第{my_position}）"
            else:
                # 非涨停但处在主线板块，给基础分
                pos_score = 2
                pos_detail = f"在主线板块{industry}内，但近3日无涨停"
        else:
            pos_score = 2
            pos_detail = f"在主线板块{industry}内，但近3日无板块涨停"
    else:
        pos_score = 0
        pos_detail = f"非主线板块或不在主线TOP10"

    details['position'] = {
        'score': pos_score,
        'max_score': 10,
        'detail': pos_detail,
    }
    total += pos_score

    db.close()

    return {
        'zhuxian': round(total, 1),
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
    main_lines = {
        'mode': 'strong_trend',
        'main_lines': [
            {'sector': '半导体', 'score': 85, 'zt_count': 12, 'max_board': 4, 'change_pct': 3.5},
            {'sector': 'AI', 'score': 72, 'zt_count': 8, 'max_board': 3, 'change_pct': 2.1},
        ],
        'top_sectors': [
            {'sector': '半导体', 'score': 85, 'zt_count': 12},
            {'sector': 'AI', 'score': 72, 'zt_count': 8},
            {'sector': '机器人', 'score': 65, 'zt_count': 6},
        ],
    }
    print(score_zhuxian('688981', main_lines_info=main_lines))
