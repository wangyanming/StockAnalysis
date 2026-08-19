"""
ReAct 自优化选股系统
========================
三闭环：Observe → Thought → Act

逻辑：
1. Observe: 候选股次日表现补全 + 写入 observe_log
2. Thought: 多维度归因分析 → 找出哪个维度虚高/虚低
3. Act: 生成配置调整建议（不改代码，只改 JSON）

集成点：close_task.py 在 analyze_yesterday_picks() 之后调用
"""

import sys
import os
import json
import logging
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from utils.logger import setup_logger
logger = setup_logger("pick_react")

from utils.dao import get_db

# ─── 配置 ─────────────────────────────────────────────

WEIGHTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config', 'scorer_weights.json')

DEFAULT_CONFIG = {
    "version": "5.5",
    "weights": {
        "chip_structure": 25,
        "momentum": 25,
        "sector_environment": 12,
        "trend_position": 20,
        "market_safety": 10,
        "position_bonus": 15,
        "risk_penalty": 15
    },
    "thresholds": {
        "sell_overload_days": 3,
        "consecutive_up_threshold": 7,
        "momentum_effective_threshold": 10
    },
    "active_since": "2026-05-26"
}

# 维度与权重名称映射
DIM_WEIGHT_MAP = [
    ('score_chip', 'chip_structure', '筹码结构'),
    ('score_money', 'momentum', '资金接力'),
    ('score_sector', 'sector_environment', '板块环境'),
    ('score_trend', 'trend_position', '趋势位置'),
    ('score_market', 'market_safety', '大盘安全'),
]

# ─── 工具函数 ─────────────────────────────────────────

def load_weights_config():
    """加载权重配置"""
    try:
        with open(WEIGHTS_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(DEFAULT_CONFIG)


def save_weights_config(cfg):
    """保存权重配置"""
    os.makedirs(os.path.dirname(WEIGHTS_FILE), exist_ok=True)
    with open(WEIGHTS_FILE, 'w') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    logger.info(f'💾 权重配置已保存: {WEIGHTS_FILE}')


# ─── Step 1: Observe ──────────────────────────────────

def update_feedback(check_date: str = None) -> int:
    """
    补全 daily_picks 中 missing 的次日涨跌幅数据
    并同步写入 observe_log 表
    """
    db = get_db()
    if not check_date:
        check_date = datetime.now().strftime('%Y%m%d')

    # 确认 stock_daily 有 check_date 的数据
    verify = db.fetchone(
        'SELECT COUNT(*) AS cnt FROM stock_daily WHERE trade_date=%s', (check_date,))
    if not verify or verify['cnt'] == 0:
        logger.warning(f'  stock_daily 无 {check_date} 数据,跳过补全')
        return 0

    # 查今天之前的选股记录中,next_day_change 为空的
    rows = db.fetchall('''
        SELECT id, trade_date, code
        FROM daily_picks
        WHERE next_day_change IS NULL
          AND trade_date < %s
        ORDER BY trade_date ASC
    ''', (check_date,))

    if not rows:
        return 0

    updated = 0
    for r in rows:
        code = r['code']
        pick_date = r['trade_date']

        # T+1 开盘价
        t1 = db.fetchone('''
            SELECT open, trade_date
            FROM stock_daily
            WHERE code=%s AND trade_date > %s AND trade_date <= %s AND open > 0
            ORDER BY trade_date ASC
            LIMIT 1
        ''', (code, pick_date, check_date))

        if not t1:
            continue

        # T+2 收盘价
        t2 = db.fetchone('''
            SELECT close
            FROM stock_daily
            WHERE code=%s AND trade_date > %s AND close > 0
            ORDER BY trade_date ASC
            LIMIT 1
        ''', (code, t1['trade_date']))
        if not t2:
            continue

        change = (t2['close'] - t1['open']) / t1['open'] * 100

        db.execute('''
            UPDATE daily_picks
            SET next_day_change=%s, next_open=%s, next_close=%s
            WHERE id=%s
        ''', (round(change, 2), t1['open'], t2['close'], r['id']))
        updated += 1

        if updated % 50 == 0:
            logger.info(f'  已更新 {updated}/{len(rows)} 条')

    # 同步到 observe_log
    _sync_observe_log(db, check_date)
    
    logger.info(f'  补全 {updated}/{len(rows)} 条次日涨跌幅')
    return updated


# ─── 回填函数（手动触发，不在自动流程中） ──────────────

def batch_fix_next_day_change(check_date: str = None):
    """
    全量回填 daily_picks 的收益率数据（按新公式）。
    对所有 next_day_change IS NOT NULL 的记录重算。

    注意：DAO 使用 autocommit=True，不需要手动 commit。
    """
    db = get_db()
    if not check_date:
        check_date = datetime.now().strftime('%Y%m%d')

    # 取 T-2 交易日（排除 T-1: 买入日 和 T: 今日）
    # 从 stock_daily 查实际交易日，跳过周末
    t2_row = db.fetchone('''
        SELECT DISTINCT trade_date FROM stock_daily
        WHERE trade_date < %s
        ORDER BY trade_date DESC
        LIMIT 1 OFFSET 1
    ''', (check_date,))
    if not t2_row:
        return 0
    cutoff_t2 = t2_row['trade_date']

    # 查所有需要重算的记录
    rows = db.fetchall('''
        SELECT id, code, trade_date
        FROM daily_picks
        WHERE trade_date >= '20260101'
          AND next_day_change IS NOT NULL
          AND trade_date <= %s
        ORDER BY id
    ''', (cutoff_t2,))

    if not rows:
        logger.info('  没有需要回填的记录')
        return 0

    logger.info(f'  开始回填 {len(rows)} 条 next_day_change ...')
    updated = 0
    for r in rows:
        code = r['code']
        pick_date = r['trade_date']

        # T+1 开盘价
        t1 = db.fetchone('''
            SELECT open, trade_date FROM stock_daily
            WHERE code=%s AND trade_date > %s AND trade_date <= %s AND open > 0
            ORDER BY trade_date ASC LIMIT 1
        ''', (code, pick_date, check_date))

        if not t1:
            continue

        # T+2 收盘价
        t2 = db.fetchone('''
            SELECT close FROM stock_daily
            WHERE code=%s AND trade_date > %s AND close > 0
            ORDER BY trade_date ASC LIMIT 1
        ''', (code, t1['trade_date']))
        if not t2:
            continue

        change = (t2['close'] - t1['open']) / t1['open'] * 100

        db.execute('''
            UPDATE daily_picks
            SET next_day_change=%s, next_open=%s, next_close=%s
            WHERE id=%s
        ''', (round(change, 2), t1['open'], t2['close'], r['id']))
        updated += 1

        if updated % 500 == 0:
            logger.info(f'  已回填 {updated}/{len(rows)} 条')

    logger.info(f'  回填完成: {updated}/{len(rows)} 条')

    # 重建 observe_log
    # 传 T-1（check_date 的前一个交易日），使得 SQL 的 trade_date < %s
    # 能包含 T-2 及更早数据，同时排除 T-1（买入日）和 T（今日）
    t1_row = db.fetchone('''
        SELECT DISTINCT trade_date FROM stock_daily
        WHERE trade_date < %s
        ORDER BY trade_date DESC LIMIT 1 OFFSET 0
    ''', (check_date,))
    cutoff_t1 = t1_row['trade_date'] if t1_row else cutoff_t2
    logger.info('  重建 observe_log ...')
    db.execute('DELETE FROM observe_log WHERE 1=1')
    _sync_observe_log(db, cutoff_t1)
    logger.info('  observe_log 重建完成')

    return updated


def batch_fix_score_pos():
    """
    补写 daily_picks 中 score_pos IS NULL 的记录。
    逻辑：取20日区间位置百分比，按 scorcer 的 _score_position_in_range 逻辑计算。

    注意：DAO 使用 autocommit=True，不需要手动 commit。
    """
    db = get_db()

    rows = db.fetchall('''
        SELECT id, code, trade_date FROM daily_picks
        WHERE (score_pos IS NULL OR score_pos = 0)
          AND trade_date >= '20260101'
        ORDER BY id
    ''')

    if not rows:
        logger.info('  没有需要回填 score_pos 的记录')
        return 0

    logger.info(f'  开始回填 {len(rows)} 条 score_pos ...')
    updated = 0
    for r in rows:
        code = r['code']
        trade_date = r['trade_date']

        # 取当前收盘价 + 20日最低/最高
        row = db.fetchone('''
            SELECT MIN(low) as min_l, MAX(high) as max_h
            FROM stock_daily
            WHERE code=%s
              AND trade_date <= %s
              AND trade_date >= DATE_FORMAT(DATE_SUB(STR_TO_DATE(%s,'%%Y%%m%%d'), INTERVAL 20 DAY), '%%Y%%m%%d')
        ''', (code, trade_date, trade_date))

        if not row or not row['max_h'] or not row['min_l'] or row['max_h'] <= row['min_l']:
            continue

        current_close = db.fetchone(
            'SELECT close FROM stock_daily WHERE code=%s AND trade_date=%s',
            (code, trade_date))
        if not current_close or not current_close['close']:
            continue

        pos_pct = (current_close['close'] - row['min_l']) / (row['max_h'] - row['min_l']) * 100

        if pos_pct < 30:
            pos_score = 15
        elif pos_pct < 60:
            pos_score = 8
        elif pos_pct < 85:
            pos_score = 3
        else:
            pos_score = 0

        db.execute('UPDATE daily_picks SET score_pos=%s WHERE id=%s', (pos_score, r['id']))
        updated += 1

        if updated % 500 == 0:
            logger.info(f'  已回填 {updated}/{len(rows)} 条')

    logger.info(f'  位置评分回填完成: {updated}/{len(rows)} 条')
    return updated


def _sync_observe_log(db, check_date: str):
    """把 daily_picks 中 total_score >= 60 且有 next_day_change 的记录同步到 observe_log"""
    rows = db.fetchall('''
        SELECT trade_date, code, name, total_score, `rank`, is_pick, source, grade,
               next_day_change, score_chip, score_money, score_sector, score_trend, score_market,
               score_pos, position_advice
        FROM daily_picks
        WHERE total_score >= 60 AND next_day_change IS NOT NULL
          AND trade_date < %s
          AND (trade_date, code) NOT IN (
              SELECT trade_date, code FROM observe_log
          )
    ''', (check_date,))

    if not rows:
        return

    synced = 0
    for r in rows:
        db.execute('''
            INSERT IGNORE INTO observe_log
                (trade_date, code, name, total_score, `rank`, is_pick, source, grade,
                 next_day_change, score_chip, score_money, score_sector, score_trend, score_market,
                 score_pos, position_advice)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (
            r['trade_date'], r['code'], r['name'], r['total_score'],
            r['rank'], r['is_pick'], r['source'], r['grade'],
            r['next_day_change'],
            r['score_chip'], r['score_money'], r['score_sector'],
            r['score_trend'], r['score_market'],
            r['score_pos'], r['position_advice']
        ))
        synced += 1

    logger.info(f'  同步 {synced} 条到 observe_log')


# ─── Step 2: Thought ──────────────────────────────────

def _build_dim_ranges(dim_field):
    """为每个维度构建合理的分析分段"""
    # 维度满分都是25（大盘安全10分），分段相对统一
    return [
        (f'高分(≥15)', f'AND {dim_field} >= 15'),
        (f'中分(5~15)', f'AND {dim_field} BETWEEN 5 AND 14'),
        (f'低分(<5)', f'AND {dim_field} < 5'),
    ]


def _dim_accuracy_analysis(db, check_date: str, window_days: int = 30) -> list:
    """
    对每个评分维度做准确率分析。

    返回：[
        {
            'dim_label': '筹码结构',
            'db_field': 'score_chip',
            'weight_key': 'chip_structure',
            'current_weight': 25,
            'overall_win_rate': 0.55,
            'high_score_win_rate': 0.67,
            'low_score_win_rate': 0.40,
            'predictive_power': '强',  # 高分票胜率显著 > 低分票
            'action': '维持',           # 建议
            'suggested_weight_delta': 0, # 建议调整值
        }, ...
    ]
    """
    results = []
    start_date = datetime.strptime(check_date, '%Y%m%d') - timedelta(days=window_days)
    start_str = start_date.strftime('%Y%m%d')

    cfg = load_weights_config()
    weights = cfg['weights']

    for db_field, weight_key, dim_label in DIM_WEIGHT_MAP:
        current_w = weights.get(weight_key, None)

        # 整体胜率
        overall = db.fetchone(f'''
            SELECT COUNT(*) as cnt,
                   AVG(next_day_change) as avg_ret,
                   SUM(CASE WHEN next_day_change > 0 THEN 1 ELSE 0 END) / COUNT(*) as win_rate
            FROM observe_log
            WHERE trade_date >= %s AND trade_date < %s
              AND {db_field} IS NOT NULL
        ''', (start_str, check_date))

        # 高分段的准确率
        high_scores = db.fetchone(f'''
            SELECT COUNT(*) as cnt,
                   SUM(CASE WHEN next_day_change > 0 THEN 1 ELSE 0 END) / COUNT(*) as win_rate,
                   AVG(next_day_change) as avg_ret
            FROM observe_log
            WHERE trade_date >= %s AND trade_date < %s
              AND {db_field} >= 15
        ''', (start_str, check_date))

        # 低分段的准确率（有分析价值时）
        low_scores = db.fetchone(f'''
            SELECT COUNT(*) as cnt,
                   SUM(CASE WHEN next_day_change > 0 THEN 1 ELSE 0 END) / COUNT(*) as win_rate,
                   AVG(next_day_change) as avg_ret
            FROM observe_log
            WHERE trade_date >= %s AND trade_date < %s
              AND {db_field} < 10
        ''', (start_str, check_date))

        # 样本太少时跳过
        if not overall or overall['cnt'] < 5:
            continue

        hr = high_scores['win_rate'] if high_scores and high_scores['cnt'] >= 3 else 0
        lr = low_scores['win_rate'] if low_scores and low_scores['cnt'] >= 3 else 0

        # 判断预测力
        if high_scores and high_scores['cnt'] >= 3 and hr >= 0.55:
            if hr >= 0.65:
                predictive_power = '强'
            else:
                predictive_power = '中'
        else:
            predictive_power = '弱'

        # 生成建议
        action = '维持'
        delta = 0
        if high_scores and high_scores['cnt'] >= 5:
            if hr < 0.40:
                action = '⬇ 降低权重（虚高严重）'
                delta = -5 if current_w and current_w >= 15 else (-3 if current_w and current_w >= 10 else 0)
            elif hr > 0.70 and current_w and current_w < 25:
                action = '⬆ 增加权重（预测力强）'
                delta = 5
            elif hr < 0.50:
                action = '⚠ 需观察（预测力偏弱）'
                delta = 0 if current_w and current_w <= 15 else -3

        results.append({
            'dim_label': dim_label,
            'db_field': db_field,
            'weight_key': weight_key,
            'current_weight': current_w,
            'cnt': overall['cnt'],
            'overall_win_rate': round(overall['win_rate'] * 100, 1) if overall['win_rate'] else 0,
            'overall_avg_ret': round(overall['avg_ret'], 2) if overall['avg_ret'] else 0,
            'high_cnt': high_scores['cnt'] if high_scores else 0,
            'high_win_rate': round(hr * 100, 1),
            'high_avg_ret': round(high_scores['avg_ret'], 2) if high_scores and high_scores['avg_ret'] else 0,
            'low_cnt': low_scores['cnt'] if low_scores else 0,
            'low_win_rate': round(lr * 100, 1),
            'predictive_power': predictive_power,
            'action': action,
            'suggested_weight_delta': delta,
        })

    return results


def _last_5_dates(db, check_date: str) -> list:
    """最近5个有 total_score>=60 且 next_day_change 已填充的交易日期"""
    rows = db.fetchall('''
        SELECT DISTINCT trade_date FROM daily_picks
        WHERE trade_date < %s AND total_score >= 60 AND next_day_change IS NOT NULL
        ORDER BY trade_date DESC LIMIT 5
    ''', (check_date,))
    return [r['trade_date'] for r in rows]


# ─── 20日滚动复盘统计（新） ────────────────────────────

# 维度区间分段配置
# 2026-08-19：板块环境满分 20→12（家数4档 0/+3/+8/+12），分段重标定为 高分(≥8)/中分(3~7)/低分(<3)；
#            新增 位置评估 维度（score_pos，满分15，与其它维度同等分档统计/胜率/均收益/预测力展示）。
DIM_SEGMENTS = [
    ('score_chip', '筹码结构', 25, [(15, 25, '高分(≥15)'), (5, 14, '中分(5~14)'), (None, 4, '低分(<5)')]),
    ('score_money', '资金接力', 25, [(15, 25, '高分(≥15)'), (5, 14, '中分(5~14)'), (None, 4, '低分(<5)')]),
    ('score_sector', '板块环境', 12, [(8, 12, '高分(≥8)'), (3, 7, '中分(3~7)'), (None, 2, '低分(<3)')]),
    # 趋势位置 v6.1 由 20 分制调整为 14 分制（满分 14）。分档阈值按 14 分制重标定：
    #   高分 ≥9（≈64%，对齐原 20 分制高分 ≥12≈60% 的语义）；
    #   中分 4~8（≈29%~57%）；低分 <4。
    ('score_trend', '趋势位置', 14, [(9, 14, '高分(≥9)'), (4, 8, '中分(4~8)'), (None, 3, '低分(<4)')]),
    ('score_market', '大盘安全', 10, [(6, 10, '高分(≥6)'), (2, 5, '中分(2~5)'), (None, 1, '低分(<2)')]),
    # 位置评估（score_pos，满分15）：高分=低位(15/10) 应胜率最高，低分=偏高/极高位(0~4) 应最弱
    ('score_pos', '位置评估', 15, [(10, 15, '高分(≥10)'), (5, 9, '中分(5~9)'), (None, 4, '低分(<5)')]),
]


def _get_active_pick_dates(db, check_date: str, max_days: int = 20) -> list:
    """获取有完整 observe_log 数据的交易日列表（最多 max_days 天）"""
    # 取 T-2 交易日（排除 T-1: 买入日 和 T: 今日）
    # 从 stock_daily 查实际交易日，跳过周末
    t2_row = db.fetchone('''
        SELECT DISTINCT trade_date FROM stock_daily
        WHERE trade_date < %s
        ORDER BY trade_date DESC
        LIMIT 1 OFFSET 1
    ''', (check_date,))
    if not t2_row:
        return []
    cutoff_t2 = t2_row['trade_date']
    rows = db.fetchall('''
        SELECT DISTINCT trade_date FROM observe_log
        WHERE next_day_change IS NOT NULL
          AND trade_date <= %s
        ORDER BY trade_date DESC LIMIT %s
    ''', (cutoff_t2, max_days))
    return sorted([r['trade_date'] for r in rows])


def _build_react_summary(rows: list) -> dict:
    """构建概览统计"""
    total = len(rows)
    if total == 0:
        return {'total': 0, 'wins': 0, 'win_rate': 0, 'avg_return': 0}
    wins = sum(1 for r in rows if r['next_day_change'] and r['next_day_change'] > 0)
    avg_ret = sum(r['next_day_change'] or 0 for r in rows) / total
    return {
        'total': total,
        'wins': wins,
        'win_rate': round(wins / total * 100, 1),
        'avg_return': round(avg_ret, 2),
    }


def _build_dimension_analysis(rows: list) -> list:
    """对每个维度做区间分段统计"""
    results = []
    for db_field, dim_label, full_score, segments in DIM_SEGMENTS:
        seg_data = []
        for lo, hi, label in segments:
            matched = [r for r in rows if r.get(db_field) is not None and (lo is None or lo <= r[db_field]) and r[db_field] <= hi]
            cnt = len(matched)
            if cnt == 0:
                seg_data.append({'label': label, 'count': 0, 'win_rate': 0, 'avg_return': 0})
                continue
            wins = sum(1 for r in matched if r['next_day_change'] and r['next_day_change'] > 0)
            avg_ret = sum(r['next_day_change'] or 0 for r in matched) / cnt
            seg_data.append({
                'label': label,
                'count': cnt,
                'win_rate': round(wins / cnt * 100, 1),
                'avg_return': round(avg_ret, 2),
            })

        # 预测力判断
        high = seg_data[0] if seg_data else None
        low = seg_data[2] if len(seg_data) > 2 else None

        if high and low and high['count'] >= 3 and low['count'] >= 3:
            diff = high['win_rate'] - low['win_rate']
            if diff > 20:
                predictive_power = '强'
                action = '维持'
            elif diff > 10:
                predictive_power = '中'
                action = '维持'
            else:
                predictive_power = '弱'
                action = '降低' if high['win_rate'] < 50 else '维持'
        elif high and high['count'] >= 3:
            predictive_power = '中' if high['win_rate'] >= 55 else '弱'
            action = '维持'
        else:
            predictive_power = '弱'
            action = '维持'

        # 高分标记
        if high and high['count'] >= 3 and high['win_rate'] >= 60:
            action = '维持'

        results.append({
            'dim_label': dim_label,
            'full_score': full_score,
            'high': seg_data[0] if len(seg_data) > 0 else None,
            'mid': seg_data[1] if len(seg_data) > 1 else None,
            'low': seg_data[2] if len(seg_data) > 2 else None,
            'predictive_power': predictive_power,
            'action': action,
        })

    return results


def _build_group_stats(rows: list) -> list:
    """B/C/D 分组统计"""
    groups = {'B': [], 'C': [], 'D': []}
    for r in rows:
        s = r['total_score'] or 0
        if 60 <= s < 65:
            groups['B'].append(r)
        elif 65 <= s < 70:
            groups['C'].append(r)
        elif s >= 70:
            groups['D'].append(r)

    label_map = {
        'B': 'B组(60~64)',
        'C': 'C组(65~69)',
        'D': 'D组(≥70)',
    }
    result = []
    for key in ['B', 'C', 'D']:
        items = groups[key]
        if not items:
            continue
        cnt = len(items)
        wins = sum(1 for r in items if r['next_day_change'] and r['next_day_change'] > 0)
        avg_ret = sum(r['next_day_change'] or 0 for r in items) / cnt
        result.append({
            'label': label_map[key],
            'count': cnt,
            'wins': wins,
            'win_rate': round(wins / cnt * 100, 1),
            'avg_return': round(avg_ret, 2),
        })
    return result


def build_react_report(check_date: str = None) -> dict:
    """
    构建20日滚动复盘统计报告（结构化 dict）。

    从 observe_log 取最近至多20个有效选股日的全量数据（total_score >= 60），
    统计概览、维度归因、B/C/D 分组。

    返回: {
        'window_info': {...},
        'summary': {...},
        'dimension_analysis': [...],
        'group_stats': [...],
        'react_analysis': {'has_changes': False, 'changes': [], 'analysis_summary': '...'}
    }
    """
    db = get_db()
    if not check_date:
        check_date = datetime.now().strftime('%Y%m%d')

    # 获取至多20个有效选股日
    active_dates = _get_active_pick_dates(db, check_date, 20)

    if not active_dates:
        logger.warning('  build_react_report: 无有效选股日数据')
        return {
            'window_info': {
                'window_size': 0,
                'start_date': None,
                'end_date': None,
                'note': '无数据'
            },
            'summary': {'total': 0, 'wins': 0, 'win_rate': 0, 'avg_return': 0},
            'dimension_analysis': [],
            'group_stats': [],
            'react_analysis': {
                'has_changes': False,
                'changes': [],
                'analysis_summary': '数据不足,暂无法进行自优化分析'
            },
        }

    window_size = len(active_dates)
    start_date = active_dates[0]
    end_date = active_dates[-1]

    # 查询 observe_log 全量数据
    placeholders = ','.join(['%s'] * len(active_dates))
    rows = db.fetchall(f'''
        SELECT trade_date, code, name, total_score, grade, next_day_change,
               score_chip, score_money, score_sector, score_trend, score_market, score_pos
        FROM observe_log
        WHERE trade_date IN ({placeholders})
          AND total_score >= 60
          AND next_day_change IS NOT NULL
        ORDER BY trade_date DESC, total_score DESC
    ''', active_dates)

    note = None
    if window_size < 20:
        note = f'仅{window_size}天数据'

    window_info = {
        'window_size': window_size,
        'start_date': start_date,
        'end_date': end_date,
        'note': note,
    }

    summary = _build_react_summary(rows)
    dimension_analysis = _build_dimension_analysis(rows)
    group_stats = _build_group_stats(rows)

    # react_analysis: 复用现有 generate_act_suggestions 的结论
    dim_analysis_legacy = _dim_accuracy_analysis(db, check_date, window_days=20)
    suggestions = generate_act_suggestions(dim_analysis_legacy)

    react_analysis = {
        'has_changes': suggestions['has_changes'],
        'changes': suggestions['changes'],
        'analysis_summary': '当前权重配置合理,无需调整' if not suggestions['has_changes']
        else '检测到需要调整的维度',
    }

    return {
        'window_info': window_info,
        'summary': summary,
        'dimension_analysis': dimension_analysis,
        'group_stats': group_stats,
        'react_analysis': react_analysis,
    }


# ─── Step 3: Act ──────────────────────────────────────

def generate_act_suggestions(dim_analysis: list) -> dict:
    """
    根据维度归因分析，生成权重调整建议。
    返回 {建议文本, 新配置, 是否建议修改}
    """
    cfg = load_weights_config()
    weights = cfg['weights']
    old_version = cfg.get('version', '5.5')
    
    suggested = {}
    changes = []
    total_delta = 0

    for d in dim_analysis:
        key = d['weight_key']
        delta = d['suggested_weight_delta']
        old_w = weights.get(key, 0)
        new_w = max(5, min(30, old_w + delta))  # 限制 5~30
        if delta != 0 and new_w != old_w:
            suggested[key] = new_w
            changes.append(f"    {d['dim_label']}: {old_w} → {new_w} ({delta:+d})")
            total_delta += new_w - old_w

    # 总分配平（避免总权重变化太大）
    if changes:
        # 主权重总和应控制在 90~110
        current_sum = sum(weights.get(k, 0) for k in ['chip_structure', 'momentum', 'sector_environment', 'trend_position', 'market_safety'])
        new_sum = current_sum + total_delta
        logger.info(f'  建议调整前主权重和: {current_sum}, 调整后: {new_sum}')

    new_version = f'{old_version}-react' if changes else old_version
    
    return {
        'has_changes': len(changes) > 0,
        'old_version': old_version,
        'new_version': new_version,
        'changes': changes,
        'suggested_weights': suggested,
        'new_weight_sum': sum(weights.values()) + total_delta,
    }


def apply_act_suggestions(suggestions: dict) -> bool:
    """
    应用建议（手动触发，非自动）
    返回是否已应用
    """
    if not suggestions or not suggestions['has_changes']:
        return False
    
    cfg = load_weights_config()
    for key, new_w in suggestions['suggested_weights'].items():
        if key in cfg['weights']:
            cfg['weights'][key] = new_w
    cfg['version'] = suggestions['new_version']
    cfg['active_since'] = datetime.now().strftime('%Y-%m-%d')
    save_weights_config(cfg)
    return True


# ─── 主分析流程 ───────────────────────────────────────

def run_react_analysis(check_date: str = None, window_days: int = 30) -> str:
    """
    运行完整的 ReAct 分析循环，返回格式化报告文本。
    
    参数:
        check_date: 分析日期(YYYYMMDD)，默认今天
        window_days: 归因分析回溯天数
        
    返回:
        多行文本报告，用于追加到复盘推送
    """
    db = get_db()
    if not check_date:
        check_date = datetime.now().strftime('%Y%m%d')

    lines = []
    
    # ─── Observe: 最近5日精选表现 ───
    week_dates = _last_5_dates(db, check_date)
    if not week_dates:
        lines.append('📊 ReAct 复盘: 暂无候选数据')
        return '\n'.join(lines)

    all_rows = []
    placeholders = ','.join(['%s'] * len(week_dates))
    all_rows = db.fetchall(f'''
        SELECT trade_date, code, name, total_score, source, grade, next_day_change,
               score_chip, score_money, score_sector, score_trend, score_market
        FROM daily_picks
        WHERE trade_date IN ({placeholders}) AND is_pick = 1
        ORDER BY trade_date DESC, total_score DESC
    ''', week_dates)

    # ymd 取最新一天给标题用
    week_start = week_dates[-1]  # 最早一天（reverse order）
    week_end = week_dates[0]     # 最近一天

    total = len(all_rows)
    wins = sum(1 for r in all_rows if r['next_day_change'] and r['next_day_change'] > 0)
    win_rate = wins / total * 100 if total > 0 else 0
    avg_ret = sum(r['next_day_change'] or 0 for r in all_rows) / total if total > 0 else 0
    best = max((r['next_day_change'] for r in all_rows if r['next_day_change']), default=0)
    worst = min((r['next_day_change'] for r in all_rows if r['next_day_change']), default=0)
    big_win_cnt = sum(1 for r in all_rows if r['next_day_change'] and r['next_day_change'] >= 2)

    lines.append('─' * 30)
    lines.append(f'📊 ReAct复盘 (选股{week_start}~{week_end} → 检验{check_date})')
    lines.append(f'精选{total}只 · 胜率{win_rate:.0f}% · 均涨幅{avg_ret:+.2f}%')
    lines.append(f'最大盈利{best:+.2f}% · 最大亏损{worst:+.2f}%')
    lines.append(f'大涨(≥2%): {big_win_cnt}只')
    lines.append('─' * 20)

    if all_rows:
        high_rows = [r for r in all_rows if r['total_score'] and r['total_score'] > 50]
        mid_rows = [r for r in all_rows if r['total_score'] and 40 <= r['total_score'] <= 50]
        low_rows = [r for r in all_rows if r['total_score'] and r['total_score'] < 40]
        
        def _win_info(rows):
            if not rows:
                return None
            cnt = len(rows)
            wins_cnt = sum(1 for r in rows if r['next_day_change'] and r['next_day_change'] > 0)
            wr = wins_cnt / cnt * 100
            avg = sum(r['next_day_change'] or 0 for r in rows) / cnt
            return cnt, wr, avg
        
        lines.append(f'评分归因(近5日 total_score):')
        hi = _win_info(high_rows)
        if hi:
            lines.append(f'高分(>50): {hi[0]}只 胜率{hi[1]:.0f}% 均{hi[2]:+.2f}% {"✅" if hi[1] > 50 else "❌"}')
        mi = _win_info(mid_rows)
        if mi:
            lines.append(f'中分(40-50): {mi[0]}只 胜率{mi[1]:.0f}% 均{mi[2]:+.2f}% {"✅" if mi[1] > 50 else "❌"}')
        lo = _win_info(low_rows)
        if lo:
            lines.append(f'低分(<40): {lo[0]}只 胜率{lo[1]:.0f}% 均{lo[2]:+.2f}% {"⚠️"}')
        
        # 分组统计
        zt_rows = [r for r in all_rows if r.get('group') in ('涨停接力','涨停回踩')]
        fz_rows = [r for r in all_rows if r.get('group') in ('区间潜伏',)]
        
        zt_info = _win_info(zt_rows)
        if zt_info:
            lines.append(f'⚡涨停接力: {zt_info[0]}只 胜率{zt_info[1]:.0f}% 均涨幅{zt_info[2]:+.2f}% {"✅" if zt_info[1] > 50 else ""}')
        fz_info = _win_info(fz_rows)
        if fz_info:
            lines.append(f'📗区间潜伏: {fz_info[0]}只 胜率{fz_info[1]:.0f}% 均涨幅{fz_info[2]:+.2f}% {"✅" if fz_info[1] > 50 else ""}')
        
        lines.append('─' * 20)
        
    # 近一周统计
    lines.append(f'近一周: {total}只 胜率{win_rate:.0f}% 均{avg_ret:+.2f}%')
    lines.append('─' * 28)
    lines.append('')

    # ─── Thought: 多维归因 ───
    dims = _dim_accuracy_analysis(db, check_date, window_days)

    if dims:
        lines.append('📊 评分维度归因(近30天):')
        for d in dims:
            # 符号
            sym = '🟢' if d['predictive_power'] == '强' else ('🟡' if d['predictive_power'] == '中' else '🔴')
            lines.append(f'   {sym} {d["dim_label"]}: 当前{d["current_weight"]}分 | 整体胜率{d["overall_win_rate"]:.0f}%')
            
            detail_parts = []
            if d['high_cnt'] >= 3:
                detail_parts.append(f'高分(≥15): {d["high_cnt"]}只胜率{d["high_win_rate"]:.0f}%均{d["high_avg_ret"]:+.2f}%')
            if d['low_cnt'] >= 3:
                detail_parts.append(f'低分(<10): {d["low_cnt"]}只胜率{d["low_win_rate"]:.0f}%')
            
            if detail_parts:
                lines.append(f'     {" | ".join(detail_parts)}')
            lines.append(f'     预测力: {d["predictive_power"]} | {d["action"]}')

        lines.append('')

    # ─── Act: 建议 ───
    suggestions = generate_act_suggestions(dims)

    if suggestions['has_changes']:
        lines.append('⚙️ 权重调整建议:')
        for ch in suggestions['changes']:
            lines.append(f'  {ch}')
        lines.append(f'  版本: {suggestions["old_version"]} → {suggestions["new_version"]}')
        lines.append(f'  💡 如需应用,运行: 主机上改 config/scorer_weights.json 或告诉我"应用建议"')
    else:
        lines.append('✅ 当前权重配置合理,无需调整')

    lines.append('')
    return '\n'.join(lines)


# ─── 简版旧接口兼容 ──────────────────────────────────

def analyze(check_date: str = None) -> str:
    """
    [兼容旧接口] 生成归因分析文本
    现在调用 run_react_analysis 完整版本
    """
    return run_react_analysis(check_date)


# ─── 入口 ─────────────────────────────────────────────

if __name__ == '__main__':
    today = datetime.now().strftime('%Y%m%d')
    print(f'🔄 ReAct 自优化系统运行: {today}')
    print()
    
    print('📡 Step 1: Observe - 补全次日涨跌幅...')
    updated = update_feedback(today)
    print(f'  更新 {updated} 条')
    print()
    
    print('📡 Step 2-3: Thought+Act - 归因分析与建议...')
    print()
    report = run_react_analysis(today)
    print(report)
    
    # 如果有建议,输出概括
    print()
    print('─' * 40)
    print('💡 提示: 查看 config/scorer_weights.json 当前配置')
    print('   直接告诉我"应用建议"来应用权重调整')
