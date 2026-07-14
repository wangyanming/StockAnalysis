"""
涨停观察池模块（V7 新增）
用于构建当日涨停观察池，独立于精选输出

目的：
  1. 涨停票仍有价值——作为次日竞价参考
  2. 区分"选股"和"竞价"：涨停观察池告诉你"明天看哪些"；精选买入池告诉你"明天买哪些"
  3. 涨停观察池的票如果次日低开/平开，在竞价时仍然可以关注

涨停观察池构建规则：
  所有满足候选条件 + 当天涨停(≥9.5%) + 无任何一票否决
  → 输出为zt_observation: [{code, name, industry, board_times, seal_time, total_score}]
  
  zt_observation中的票仍然计算完整评分，但：
  - T日状态评分固定为 -12（涨停扣分）
  - 不进入精选TOP5
  - 在报告模版中单独一段"🌩 涨停观察池（参考）"
"""

import sys
import os
from datetime import datetime
from typing import Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from utils.logger import setup_logger
from utils.dao import get_db

logger = setup_logger("zt_observation_pool")


def build_zt_observation_pool(trade_date: str, candidates: list,
                               scored_list: list) -> list:
    """
    从候选池和评分结果中提取涨停观察池

    Args:
        trade_date: YYYYMMDD 交易日
        candidates: 候选池列表（来自 _build_candidate_pool 的原始数据）
        scored_list: 评分结果列表（包含总评分等详细信息）

    Returns:
        list: 涨停观察池 [{code, name, industry, board_times, seal_time, total_score, ...}]
    """
    db = get_db()
    observation = []

    # 获取当天所有涨停数据
    zt_today = db.fetchall("""
        SELECT code, name, industry, board_times, seal_first_time, seal_last_time,
               bomb_times, change_pct, price
        FROM daily_limit_up
        WHERE trade_date=%s
          AND (status IS NULL OR status != '跌停')
          AND change_pct >= 9.5
        ORDER BY board_times DESC, seal_first_time ASC
    """, (trade_date,))

    if not zt_today:
        logger.info("  涨停观察池：当日无涨停票")
        db.close()
        return []

    # 过滤：排除688/300/301/4/8/ST/退市
    zt_filtered = []
    for zt in zt_today:
        code = zt['code']
        name = zt['name'] if zt['name'] else ''

        if code.startswith('688') or code.startswith('300') or code.startswith('301'):
            continue
        if code.startswith('4') or code.startswith('8'):
            continue
        if 'ST' in name or '退' in name:
            continue

        zt_filtered.append(zt)

    # 为候选池中的涨停票补充评分信息
    # 如果该票也在候选池/评分列表中，则直接使用其评分
    scored_code_map = {}
    if scored_list:
        for s in scored_list:
            scored_code_map[s['code']] = s

    for zt in zt_filtered:
        code = zt['code']
        industry = zt.get('industry', '').strip() if zt.get('industry') else ''
        board_times = int(zt['board_times']) if zt['board_times'] else 1
        seal_time = zt.get('seal_first_time', '') or ''

        total_score = 0
        if code in scored_code_map:
            total_score = scored_code_map[code].get('total_score', 0)

        observation.append({
            'code': code,
            'name': zt.get('name', ''),
            'industry': industry,
            'board_times': board_times,
            'seal_time': seal_time,
            'total_score': total_score,
        })

    db.close()
    logger.info(f"  涨停观察池构建完成: {len(observation)}只")
    return observation


def format_zt_observation(zt_obs: list) -> str:
    """格式化涨停观察池为文本"""
    if not zt_obs:
        return "🌩 涨停观察池：今日无符合条件涨停票"

    # 按连板数排序
    zt_sorted = sorted(zt_obs, key=lambda x: (-x['board_times'], x['seal_time']))

    lines = ["🌩 涨停观察池（参考，不构成买入建议）"]
    lines.append("━" * 30)

    # 连板梯队
    board_groups = {}
    for zt in zt_sorted:
        bt = zt['board_times']
        if bt not in board_groups:
            board_groups[bt] = []
        board_groups[bt].append(zt)

    for board_cnt in sorted(board_groups.keys(), reverse=True):
        group = board_groups[board_cnt]
        label = f"🏆 {board_cnt}板" if board_cnt >= 2 else "📌 首板"
        items = []
        for zt in group:
            score_str = f"({zt['total_score']}分)" if zt['total_score'] else ""
            ind_str = f"[{zt['industry']}]" if zt['industry'] else ""
            seal_str = f"封板{zt['seal_time']}" if zt['seal_time'] else ""
            items.append(f"{zt['name']}({zt['code']}){ind_str}{score_str}{seal_str}")
        lines.append(f"{label}: {' | '.join(items[:4])}")

    lines.append(f"\n共{len(zt_sorted)}只涨停票（已排除北交所/科创板/创业板/ST/退市）")
    lines.append("")

    return "\n".join(lines)


if __name__ == '__main__':
    # 测试
    td = datetime.now().strftime('%Y%m%d')
    obs = build_zt_observation_pool(td, [], [])
    print(format_zt_observation(obs))
