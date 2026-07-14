"""
低吸抄底选股引擎 V3
基于数据回测结论：
  - 核心策略A：今日跌幅≥5% + MA20乖离率≤-8% + 量比<1.5
    胜率65.9% (N=3,478)，平均+0.86%
  - 备用策略D：阳线(close>open) + 涨幅1-9.5% + 昨日跌≥2% + devMA20≤-6% + 量比>1.5
    胜率60.6% (N=343)

数据源：MySQL stock_daily 表（腾讯实时行情已入库）
"""
import sys
import os
import json
import time
from datetime import datetime, timedelta

# 项目根路径
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from utils.logger import setup_logger
from utils.dao import get_db

logger = setup_logger("daily_pick_v3")


def _compute_ma20(db, code: str, trade_date: str) -> float:
    """计算某股票在 trade_date 日的前20日简单移动均线（不含当日）
    
    Args:
        db: 数据库连接
        code: 股票代码
        trade_date: 日期 YYYYMMDD
    
    Returns:
        float: MA20 值，数据不足时返回0
    """
    rows = db.fetchall(
        "SELECT close FROM stock_daily "
        "WHERE code=%s AND trade_date<%s AND close>0 "
        "ORDER BY trade_date DESC LIMIT 20",
        (code, trade_date))
    if not rows or len(rows) < 20:
        return 0.0
    total = sum(float(r['close']) for r in rows if r['close'])
    return total / 20


def _compute_avg_volume_5d(db, code: str, trade_date: str) -> float:
    """计算前5日平均成交量（不含当日）
    
    Args:
        db: 数据库连接
        code: 股票代码
        trade_date: 日期 YYYYMMDD
    
    Returns:
        float: 前5日均量，数据不足时返回0
    """
    rows = db.fetchall(
        "SELECT volume FROM stock_daily "
        "WHERE code=%s AND trade_date<%s AND volume>0 "
        "ORDER BY trade_date DESC LIMIT 5",
        (code, trade_date))
    if not rows or len(rows) < 5:
        return 0.0
    total = sum(float(r['volume']) for r in rows if r['volume'])
    return total / 5


def _compute_deviation(today_close: float, ma20: float) -> float:
    """计算MA20乖离率
    (close - ma20) / ma20 * 100
    """
    if ma20 <= 0:
        return 0.0
    return (today_close - ma20) / ma20 * 100


def _compute_volume_ratio(today_volume: float, avg_volume_5d: float) -> float:
    """计算量比
    今日volume / 前5日均量
    """
    if avg_volume_5d <= 0:
        return 0.0
    return today_volume / avg_volume_5d


def _compute_5d_change(db, code: str, trade_date: str) -> float:
    """计算前5日累计涨跌幅（不含当日）
    
    Args:
        db: 数据库连接
        code: 股票代码
        trade_date: 日期 YYYYMMDD
    
    Returns:
        float: 前5日累计涨跌幅（%），数据不足时返回0
    """
    rows = db.fetchall(
        "SELECT close FROM stock_daily "
        "WHERE code=%s AND trade_date<%s AND close>0 "
        "ORDER BY trade_date DESC LIMIT 5",
        (code, trade_date))
    if not rows or len(rows) < 5:
        return 0.0
    close_5d_ago = float(rows[4]['close'])
    if close_5d_ago <= 0:
        return 0.0
    today_close = float(rows[0]['close'])
    return (today_close - close_5d_ago) / close_5d_ago * 100


def _fill_name(db, code: str) -> str:
    """从stock_daily表中补全股票名称"""
    r = db.fetchone(
        'SELECT name FROM stock_daily WHERE code=%s AND name!="" ORDER BY trade_date DESC LIMIT 1',
        (code,))
    return r['name'] if r else code


def _is_red_candle(today_open: float, today_close: float) -> bool:
    """判断是否为阳线（收盘>开盘）"""
    return today_close > today_open


def _get_yesterday_quote(db, code: str, trade_date: str) -> dict:
    """获取昨日行情数据"""
    yesterday = db.fetchone(
        "SELECT close, change_pct FROM stock_daily "
        "WHERE code=%s AND trade_date<%s AND trade_date>=%s "  # 近20日内找前一个交易日
        "ORDER BY trade_date DESC LIMIT 1",
        (code, trade_date, (datetime.now() - timedelta(days=20)).strftime('%Y%m%d')))
    if yesterday:
        return {
            'close': float(yesterday['close']),
            'change_pct': float(yesterday['change_pct']) if yesterday['change_pct'] else 0,
        }
    return {}


def pick_stocks_v3() -> dict:
    """
    低吸抄底选股主流程：
    1. 全市场扫描当日跌幅≥5%的股票
    2. 计算MA20乖离率≤-8%
    3. 量比<1.5
    4. 正分因子加分
    5. 总分排序TOP5
    """
    db = get_db()
    results = {}

    today_str = datetime.now().strftime('%Y%m%d')
    # 检查今日是否有数据
    check = db.fetchone(
        'SELECT COUNT(*) as c FROM stock_daily WHERE trade_date=%s', (today_str,))
    target_date = today_str if check and check['c'] > 10 else None
    if not target_date:
        last_date = db.fetchone('SELECT MAX(trade_date) as d FROM stock_daily')
        target_date = last_date['d'] if last_date else None
    if not target_date:
        logger.error("数据库无有效交易日数据")
        db.close()
        return {}

    logger.info(f"目标交易日: {target_date}")
    results['trade_date'] = target_date

    # ========== 1. 全市场扫描：今日跌幅≥5% ==========
    logger.info("1. 全市场扫描（跌幅≥5%）...")

    # 排除科创/创业板/北交所/ST/退市
    # ⚠️ SQL务必包含change_pct，loop中依赖此字段判断策略条件
    candidates = db.fetchall(
        "SELECT code, name, trade_date, open, close, high, low, volume, amount, "
        "total_market_cap, turnover_rate, change_pct "
        "FROM stock_daily "
        "WHERE trade_date=%s "
        "AND change_pct <= -5 "
        "AND code NOT LIKE '688%%' "
        "AND code NOT LIKE '300%%' "
        "AND code NOT LIKE '301%%' "
        "AND code NOT LIKE '8%%' "
        "AND code NOT LIKE '4%%' "
        "AND name NOT LIKE '%%ST%%' "
        "AND name NOT LIKE '%%退%%' "
        "ORDER BY change_pct ASC",
        (target_date,))

    logger.info(f"   符合跌幅≥5%条件: {len(candidates)}只")

    if not candidates:
        logger.info("无候选标的，提前退出")
        results['candidates'] = []
        results['scored'] = []
        db.close()
        return results

    # ========== 2. 评分筛选 ==========
    logger.info("2. 计算MA20乖离率 & 量比 & 评分...")

    scored = []
    total = len(candidates)
    
    for idx, row in enumerate(candidates):
        code = row['code']
        name = _fill_name(db, code) if not row['name'] else row['name']
        today_open = float(row['open']) if row['open'] else 0
        today_close = float(row['close']) if row['close'] else 0
        today_volume = float(row['volume']) if row['volume'] else 0
        turnover_rate = float(row['turnover_rate']) if row['turnover_rate'] else 0
        total_market_cap = float(row['total_market_cap']) if row['total_market_cap'] else 0
        change_pct = float(row['change_pct']) if row.get('change_pct') else 0.0

        # 跳过无效数据
        if today_close <= 0 or today_volume <= 0:
            continue

        # 计算MA20乖离率
        ma20 = _compute_ma20(db, code, target_date)
        if ma20 <= 0:
            continue
        deviation = _compute_deviation(today_close, ma20)

        # 计算量比
        avg_volume_5d = _compute_avg_volume_5d(db, code, target_date)
        volume_ratio = _compute_volume_ratio(today_volume, avg_volume_5d)

        # ========== 核心策略A：大跌抄底+缩量 ==========
        is_strategy_a = (change_pct <= -5 and deviation <= -8 and volume_ratio < 1.5)

        # ========== 备用策略D：放量反转 ==========
        yesterday = _get_yesterday_quote(db, code, target_date)
        is_red = _is_red_candle(today_open, today_close)
        yesterday_chg = yesterday.get('change_pct', 0)
        is_strategy_d = (is_red and 1 <= change_pct <= 9.5 and yesterday_chg <= -2
                         and deviation <= -6 and volume_ratio > 1.5)

        # 不符合任一策略则跳过
        if not is_strategy_a and not is_strategy_d:
            continue

        # ========== 正分因子（加分，不做硬门槛） ==========
        bonus_score = 0
        bonus_details = []

        # ① 阳线 → +10分
        if _is_red_candle(today_open, today_close):
            bonus_score += 10
            bonus_details.append("阳线+10")

        # ② 连跌2天（今日也跌 + 昨日也跌）→ +10分
        if yesterday_chg < 0:
            bonus_score += 10
            bonus_details.append("连跌2天+10")

        # ③ 前5日跌幅≥10%（超跌）→ +15分
        chg_5d = _compute_5d_change(db, code, target_date)
        if chg_5d <= -10:
            bonus_score += 15
            bonus_details.append("前5日跌{:.1f}%超跌+15".format(abs(chg_5d)))

        # ④ 换手率<5%（缩量企稳）→ +10分
        if turnover_rate < 5:
            bonus_score += 10
            bonus_details.append("换手{:.1f}%<5%+10".format(turnover_rate))

        # ⑤ 流通市值>30亿（排除庄股）→ +5分
        if total_market_cap > 3_000_000_000:  # 30亿
            bonus_score += 5
            bonus_details.append("市值{:.0f}亿>30亿+5".format(total_market_cap / 1e8))

        scored.append({
            'code': code,
            'name': name,
            'close': today_close,
            'change_pct': change_pct,
            'deviation': round(deviation, 2),
            'volume_ratio': round(volume_ratio, 2),
            'turnover_rate': turnover_rate,
            'total_market_cap': total_market_cap,
            'yesterday_change': yesterday_chg,
            'bonus_score': bonus_score,
            'bonus_details': bonus_details,
            'total_score': bonus_score,  # 总分=正分因子之和
            'strategy': 'A' if is_strategy_a else ('D' if is_strategy_d else ''),
        })

        if (idx + 1) % 100 == 0:
            logger.info(f"   进度: {idx+1}/{total}")

    logger.info(f"   核心条件筛选后: {len(scored)}只")

    # 按总分排序
    scored.sort(key=lambda x: x['total_score'], reverse=True)

    # ========== 3. TOP5精选 ==========
    top5 = scored[:5]
    results['scored'] = scored
    results['candidates'] = []
    seen_codes = set()
    for r in top5:
        if r['code'] not in seen_codes:
            seen_codes.add(r['code'])
            results['candidates'].append({
                'code': r['code'],
                'name': r['name'],
                'score': r['total_score'],
                'strategy': r['strategy'],
                'deviation': r['deviation'],
                'volume_ratio': r['volume_ratio'],
                'change_pct': r['change_pct'],
                'bonus_details': r['bonus_details'],
            })

    # 落库
    _save_picks_to_db(results, target_date)
    db.close()

    logger.info(f"选股完成，TOP5: {[c['name'] for c in results.get('candidates', [])]}")
    return results


def _save_picks_to_db(results: dict, trade_date: str):
    """将选股结果保存到 daily_picks 表"""
    db = get_db()
    try:
        db.execute('DELETE FROM daily_picks WHERE trade_date=%s', (trade_date,))

        scored = results.get('scored', [])
        candidate_codes = {c['code'] for c in results.get('candidates', [])}

        for rank, r in enumerate(scored):
            code = r['code']
            is_top = code in candidate_codes
            change = r.get('change_pct', 0) or 0

            # 构建 highlights（与daily_picks表 compatible）
            highlights = [
                f"策略{r['strategy']}",
                f"乖离率{r['deviation']:.1f}%",
                f"量比{r['volume_ratio']:.2f}",
            ]
            if r.get('bonus_details'):
                highlights.append(' '.join(r['bonus_details'][:3]))

            db.insert_or_ignore('daily_picks', {
                'trade_date': trade_date,
                'code': code,
                'name': r['name'],
                'board_times': 1,
                'total_score': r['total_score'],
                'grade': 'A' if r['total_score'] >= 20 else ('B' if r['total_score'] >= 10 else 'C'),
                'position_advice': _get_advice(r),
                'source': f"低吸抄底-策略{r['strategy']}",
                'rank': rank + 1,
                'change_pct': change,
                'is_pick': 1 if is_top else 0,
                'highlights': ' | '.join(highlights),
                'score_chip': 0,
                'score_money': 0,
                'score_sector': 0,
                'score_trend': 0,
                'score_market': 0,
            })

        logger.info(f"选股结果已落库: {len(scored)}只, {len(results.get('candidates', []))}只精选")
        db.close()
    except Exception as e:
        logger.error(f"保存选股结果到数据库失败: {e}")
        try:
            db.close()
        except Exception:
            pass


def _get_advice(r: dict) -> str:
    """生成操作建议"""
    if r['strategy'] == 'A':
        if r['volume_ratio'] < 0.7:
            return "极度缩量-可低吸"
        elif r['volume_ratio'] < 1.0:
            return "缩量企稳-低吸"
        else:
            return "缩量-轻仓试"
    elif r['strategy'] == 'D':
        return "放量反转-竞价参与"
    return "观望"


def format_v3_report(results: dict) -> str:
    """格式化V3低吸抄底选股结果"""
    if not results or not results.get('candidates'):
        return "📊 低吸抄底选股完成，今日无符合条件的标的"

    now = datetime.now()
    trade_date = results.get('trade_date', now.strftime('%Y%m%d'))
    lines = [f"📊 {trade_date} 低吸抄底选股引擎"]
    lines.append("━" * 30)
    lines.append("")
    lines.append("📝 **策略说明**:")
    lines.append("  · 核心A: 跌幅≥5% + MA20乖离率≤-8% + 量比<1.5（胜率65.9%）")
    lines.append("  · 备用D: 阳线 + 涨幅1-9.5% + 昨日跌≥2% + 乖离率≤-6% + 量比>1.5（胜率60.6%）")
    lines.append("  · 正分因子: 阳线+10 | 连跌+10 | 超跌+15 | 低换手+10 | 市值大+5")
    lines.append("")

    candidates = results.get('candidates', [])
    scored = results.get('scored', [])

    lines.append(f"**全市场扫描**: {len(scored)}只通过核心条件")
    lines.append("")

    lines.append(f"**🌟 TOP 5 精选（低吸抄底）**")
    lines.append("")

    emojis = ['①', '②', '③', '④', '⑤']
    for i, c in enumerate(candidates):
        emoji = emojis[i] if i < 5 else f"{i+1}."
        strategy_tag = "大跌抄底" if c['strategy'] == 'A' else "放量反转"
        lines.append(f"{emoji} **{c['name']}({c['code']}) — {c['score']}分**")
        lines.append(f"  📊 策略: {strategy_tag} | 涨幅{c['change_pct']:+.2f}%")
        lines.append(f"  📉 MA20乖离率: {c['deviation']:.2f}% | 量比: {c['volume_ratio']:.2f}")
        if c.get('bonus_details'):
            lines.append(f"  ✅ 加分: {' '.join(c['bonus_details'][:4])}")
        lines.append("")

    lines.append("━" * 30)
    lines.append("")
    lines.append("**📋 明日操作计划**")
    lines.append("")
    lines.append("✅ T+1开盘买入，T+2开盘卖出")
    lines.append("· 核心A（大跌抄底）可竞价低吸，缩量越明显越安全")
    lines.append("· 备用D（放量反转）需竞价量比确认，量比>3可参与")
    lines.append("")
    lines.append("**⚠️ 交易纪律**")
    lines.append("· 单票≤50% | 止损-5%（先三问，详见 docs/交易纪律.md）")
    lines.append("· 大盘跌>1.5%不买")
    lines.append("· 低吸不追高，竞价高开>3%放弃")

    return "\n".join(lines)


def format_v3_picks_for_report(picks_data: dict) -> dict:
    """将V3选股结果格式化为close_report_tpl.py 所需的 picks 字典格式
    
    Returns dict matched to close_report_tpl render_report() expectations
    """
    results = picks_data  # from pick_stocks_v3()
    scored = results.get('scored', [])
    candidates = results.get('candidates', [])

    # 构建up_top5/non_up_top5风格的输出（用v3逻辑替代v2两组结构）
    up_top5 = []
    non_up_top5 = []

    for c in candidates:
        strategy_tag = "大跌抄底" if c['strategy'] == 'A' else "放量反转"

        entry = {
            'name': c['name'],
            'code': c['code'],
            'score': c['score'],
            'source': f"低吸抄底-{strategy_tag}",
            'dims': {
                '乖离率': int(abs(c['deviation']) * 2),  # 乖离越深越高分
                '量比': int((2 - c['volume_ratio']) * 10) if c['volume_ratio'] < 2 else 0,
                '加分因子': c['score'],
                '跌幅': int(abs(c['change_pct'])),
            },
            'notes': c.get('bonus_details', []),
            'risks': [],
        }

        if c['strategy'] == 'A':
            up_top5.append(entry)
        else:
            non_up_top5.append(entry)

    market_status = '正常'
    market_change = 0.0

    from utils.dao import get_db
    db = get_db()
    try:
        today_dash = f"{picks_data['trade_date'][:4]}-{picks_data['trade_date'][4:6]}-{picks_data['trade_date'][6:8]}"
        szzs = db.fetchone(
            "SELECT current_price, change_pct FROM index_quotes WHERE index_code='000001' AND record_date=%s",
            (today_dash,))
        if szzs:
            market_change = float(szzs['change_pct']) if szzs['change_pct'] else 0
            if market_change < -1.5:
                market_status = '谨慎'
            elif market_change < -0.5:
                market_status = '偏弱'
    except Exception:
        pass
    db.close()

    # TOP3 盯盘建议
    top3_advice = []
    for c in candidates[:3]:
        strategy_tag = "大跌抄底" if c['strategy'] == 'A' else "放量反转"
        if c['strategy'] == 'A':
            advice = f"缩量低吸，乖离率{c['deviation']:.1f}%，量比{c['volume_ratio']:.2f}，竞价可小仓试"
        else:
            advice = f"放量反转，需竞价量比>3确认，轻仓试"
        top3_advice.append({
            'name': c['name'],
            'code': c['code'],
            'score': c['score'],
            'source': f"低吸抄底-{strategy_tag}",
            'position': '低位',
            'trend': '超跌',
            'advice': advice,
        })

    picks_output = {
        'total_candidates': len(scored),
        'max_name': candidates[0]['name'] if candidates else '',
        'max_score': candidates[0]['score'] if candidates else 0,
        'market_status': market_status,
        'market_change': market_change,
        'limit_up_total': 0,  # 低吸抄底不关心涨停数
        'hot_industries': [],
        'up_top5': up_top5,
        'non_up_top5': non_up_top5,
        'top3_advice': top3_advice,
    }

    return picks_output


if __name__ == '__main__':
    logger.info("=== V3 低吸抄底选股启动 ===")
    results = pick_stocks_v3()
    output = format_v3_report(results)
    print("\n" + output + "\n")

    save_path = os.path.join(os.path.dirname(__file__), 'daily_picks_v3.json')
    with open(save_path, 'w') as f:
        json.dump(results, f, ensure_ascii=False, default=str, indent=2)
    logger.info(f"V3选股结果已保存到 daily_picks_v3.json")
