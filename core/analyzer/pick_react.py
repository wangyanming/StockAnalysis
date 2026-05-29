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

_log_dir = os.path.join(PROJECT_ROOT, "logs")
if not os.path.exists(_log_dir):
    os.makedirs(_log_dir, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(_log_dir, "pick_react.log"))
    ]
)
logger = logging.getLogger(__name__)

from utils.dao import get_db

# ─── 配置 ─────────────────────────────────────────────

WEIGHTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config', 'scorer_weights.json')

DEFAULT_CONFIG = {
    "version": "5.5",
    "weights": {
        "chip_structure": 25,
        "momentum": 25,
        "sector_environment": 20,
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

        day_data = db.fetchone('''
            SELECT close, open, trade_date
            FROM stock_daily
            WHERE code=%s AND trade_date > %s AND trade_date <= %s AND close > 0
            ORDER BY trade_date ASC
            LIMIT 1
        ''', (code, pick_date, check_date))

        if not day_data:
            continue

        close_price = day_data['close']
        open_price = day_data['open']

        pick_data = db.fetchone(
            'SELECT close FROM stock_daily WHERE code=%s AND trade_date=%s',
            (code, pick_date))

        if not pick_data or not pick_data['close'] or pick_data['close'] == 0:
            continue

        change = (close_price - pick_data['close']) / pick_data['close'] * 100

        db.execute('''
            UPDATE daily_picks
            SET next_day_change=%s, next_open=%s, next_close=%s
            WHERE id=%s
        ''', (round(change, 2), open_price, close_price, r['id']))
        updated += 1

        if updated % 50 == 0:
            logger.info(f'  已更新 {updated}/{len(rows)} 条')

    # 同步到 observe_log
    _sync_observe_log(db, check_date)
    
    logger.info(f'  补全 {updated}/{len(rows)} 条次日涨跌幅')
    return updated


def _sync_observe_log(db, check_date: str):
    """把 daily_picks 有 next_day_change 且 is_pick=1 的记录同步到 observe_log"""
    rows = db.fetchall('''
        SELECT trade_date, code, name, total_score, `rank`, is_pick, source, grade,
               next_day_change, score_chip, score_money, score_sector, score_trend, score_market,
               position_advice
        FROM daily_picks
        WHERE is_pick = 1 AND next_day_change IS NOT NULL
          AND trade_date < %s
          AND (trade_date, code, is_pick) NOT IN (
              SELECT trade_date, code, is_pick FROM observe_log
          )
    ''', (check_date,))

    if not rows:
        return

    synced = 0
    for r in rows:
        db.execute('''
            INSERT IGNORE INTO observe_log
                (trade_date, code, name, total_score, `rank`, is_pick, source, grade,
                 next_day_change, score_chip, score_money, score_sector, score_trend, score_market, position_advice)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (
            r['trade_date'], r['code'], r['name'], r['total_score'],
            r['rank'], r['is_pick'], r['source'], r['grade'],
            r['next_day_change'],
            r['score_chip'], r['score_money'], r['score_sector'],
            r['score_trend'], r['score_market'], r['position_advice']
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
    """最近5个有 is_pick=1 且 next_day_change 已填充的交易日期"""
    rows = db.fetchall('''
        SELECT DISTINCT trade_date FROM daily_picks
        WHERE trade_date < %s AND is_pick = 1 AND next_day_change IS NOT NULL
        ORDER BY trade_date DESC LIMIT 5
    ''', (check_date,))
    return [r['trade_date'] for r in rows]


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
