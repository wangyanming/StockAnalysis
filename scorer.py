"""
短线选股评分器 v5 — 判断"明天上涨概率"，而不是给"今天表现"打分

核心哲学：
  - 今天涨得多 ≠ 明天继续涨；今天涨停 ≠ 明天能连板；好公司 ≠ 短线好标的
  - 重点看：筹码结构、资金接力、板块环境、趋势位置、大盘安全垫

评分维度（满分100）：
  1. 筹码结构（25分）— 上方有没有套牢盘？是不是低位启动？
  2. 资金接力（25分）— 换手是否健康？有没有接力气质？
  3. 板块环境（20分）— 板块有没有合力？是不是主线？
  4. 趋势位置（20分）— 均线排列？回调到支撑了吗？
  5. 大盘安全垫（10分）— 明天还有多少安全空间？
  风险扣分（-15分）

⚠️ 如果修改评分逻辑，必须同步更新 docs/选股评分规则.md
⚠️ 权重修改请改 config/scorer_weights.json，不要改这里的硬编码
"""

import logging
import os
from datetime import datetime
from typing import Dict, Optional, List, Tuple
import urllib.request
import json
import time
import re
import subprocess

from fundamental import get_latest_financial, evaluate_fundamental, get_risk_flags

# ─── 权重配置（从 JSON 读取） ───
_CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config')
_WEIGHTS_FILE = os.path.join(_CONFIG_DIR, 'scorer_weights.json')

_DEFAULT_WEIGHTS = {
    'chip_structure': 25,
    'momentum': 25,
    'sector_environment': 20,
    'trend_position': 20,
    'market_safety': 10,
    'position_bonus': 15,
    'risk_penalty': 15,
}

def load_weights() -> dict:
    """加载权重配置，失败返回默认值"""
    try:
        with open(_WEIGHTS_FILE, 'r') as f:
            cfg = json.load(f)
        return cfg.get('weights', dict(_DEFAULT_WEIGHTS))
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(_DEFAULT_WEIGHTS)

_W = load_weights()      # 全局权重缓存

# 各维度满分（辅助计算用，与权重解耦）
MAX_SCORES = {
    'chip_structure': 25,
    'momentum': 25,
    'sector_environment': 20,
    'trend_position': 20,
    'market_safety': 10,
}

logger = logging.getLogger(__name__)

_market_cache = {'time': 0, 'data': None}


def _normalize_code(code: str) -> str:
    code = code.strip()
    if code.startswith('sh') or code.startswith('sz'):
        return code
    if code.startswith('6') or code.startswith('9'):
        return 'sh' + code
    return 'sz' + code


def fetch_sina_quote(codes: list) -> Dict[str, dict]:
    """批量获取新浪财经实时行情"""
    norm_codes = [_normalize_code(c) for c in codes]
    codes_str = ','.join(norm_codes)
    url = f'https://hq.sinajs.cn/list={codes_str}'
    req = urllib.request.Request(url, headers={'Referer': 'https://finance.sina.com.cn'})
    resp = urllib.request.urlopen(req, timeout=15)
    text = resp.read().decode('gbk')

    results = {}
    for line in text.strip().split('\n'):
        if not line.strip():
            continue
        match = re.match(r'var hq_str_(\w+)="(.+)"', line.strip())
        if not match:
            continue
        sid = match.group(1)
        vals = match.group(2).split(',')
        if len(vals) < 30:
            continue
        short_code = sid[2:]
        results[short_code] = {
            'name': vals[0],
            'open': float(vals[1]) if vals[1] else 0,
            'prev_close': float(vals[2]) if vals[2] else 0,
            'price': float(vals[3]) if vals[3] else 0,
            'high': float(vals[4]) if vals[4] else 0,
            'low': float(vals[5]) if vals[5] else 0,
            'volume': int(vals[8]) if vals[8] else 0,
            'amount': float(vals[9]) if vals[9] else 0,
            'bid1': float(vals[11]) if vals[11] else 0,
            'ask1': float(vals[21]) if vals[21] else 0,
            'bid_vol': int(vals[10]) if vals[10] else 0,
            'date': vals[30] if len(vals) > 30 else '',
            'time': vals[31] if len(vals) > 31 else '',
        }
    return results


def _get_today_quote_from_db(code: str) -> dict:
    """
    从 stock_daily 获取今日行情数据（收盘后使用，避免调新浪接口超时）。
    返回格式与 fetch_sina_quote 一致。
    """
    try:
        from dao import get_db
        db = get_db()
        today = datetime.now().strftime('%Y%m%d')
        row = db.fetchone('''
            SELECT open, close, high, low, volume, change_pct
            FROM stock_daily
            WHERE code = %s AND trade_date = %s
        ''', (code, today))
        if row and row.get('close', 0) > 0:
            return {
                'price': row['close'],
                'prev_close': row['close'] / (1 + row['change_pct'] / 100) if row.get('change_pct') else row['open'],
                'open': row['open'],
                'high': row['high'],
                'low': row['low'],
                'volume': int(row['volume'] * 100) if row['volume'] else 0,  # stock_daily是手，转股
                'amount': 0,
            }
    except Exception as e:
        import logging
        logging.getLogger('scorer').warning(f'_get_today_quote_from_db({code})失败: {e}')
    return {}


def check_market_status() -> Dict:
    """大盘环境判断（从 index_quotes 表读取，不调外部API）"""
    global _market_cache
    now = time.time()
    if now - _market_cache['time'] < 900 and _market_cache['data']:
        return _market_cache['data']

    result = {'status': '正常', 'score': 80, 'sh_change': 0, 'reason': ''}
    try:
        from dao import get_db
        db = get_db()
        today_dash = datetime.now().strftime('%Y-%m-%d')
        row = db.fetchone(
            'SELECT current_price, change_pct, open FROM index_quotes '
            'WHERE index_code=%s AND record_date=%s',
            ('szzs', today_dash))
        if row and row.get('current_price', 0) > 0:
            change = float(row['change_pct'])
            result['sh_change'] = round(change, 2)
            if change < -1.5:
                result['status'] = '不做'
                result['score'] = 20
                result['reason'] = f'跌{abs(change):.1f}% > 1.5%，逆势不买'
            elif change < -0.5:
                result['status'] = '谨慎'
                result['score'] = 50
                result['reason'] = f'跌{abs(change):.1f}%，轻仓谨慎为主'
            elif change > 1.5:
                result['status'] = '做多'
                result['score'] = 95
                result['reason'] = f'涨{change:.1f}%，做多窗口期'
            else:
                result['status'] = '正常'
                result['score'] = 80
                result['reason'] = f'{change:+.1f}%，正常交易环境'
        db.close()
        _market_cache = {'time': now, 'data': result}
    except Exception as e:
        logger.warning(f"从 index_quotes 获取大盘失败: {e}")
    return result


# ══════════════════════════════════════════════════════════════
# 评分函数（五个核心维度）
# ══════════════════════════════════════════════════════════════


def score_chip_structure(code: str, name: str, q: dict) -> Tuple[int, list]:
    """
    筹码结构评分（25分）

    核心：上方有多少套牢盘？现在进去是给前人抬轿还是抄底？
    数据源：stock_daily 近20日K线 + 新浪实时行情
    """
    score = 0
    details = []

    try:
        from dao import get_db
        db = get_db()
        today = datetime.now().strftime('%Y%m%d')

        klines = db.fetchall('''
            SELECT trade_date, close, high, low, volume, change_pct
            FROM stock_daily
            WHERE code = %s AND trade_date <= %s
            ORDER BY trade_date DESC
            LIMIT 20
        ''', (code, today))

        if not klines or len(klines) < 3:
            details.append('K线数据不足')
            return 5, details

        price = q.get('price', 0)
        prev_close = q.get('prev_close', 0)
        today_change = (price - prev_close) / prev_close * 100 if prev_close > 0 else 0
        is_limit_up = today_change >= 9.5

        low_20 = min(k['low'] for k in klines if k['low'] > 0)
        high_20 = max(k['high'] for k in klines if k['high'] > 0)
        # 用 stock_daily 的成交量（手），新浪实时 volume 是股
        today_vol = klines[0]['volume'] if klines and klines[0].get('volume', 0) > 0 else 0

        # ① 位置（10分）
        if low_20 > 0 and price > 0 and high_20 > low_20:
            pos = (price - low_20) / (high_20 - low_20) * 100
            if pos < 30:
                score += 10
                details.append(f'低位启动(20日低点分位{pos:.0f}%)(+10)')
            elif pos < 50:
                score += 6
                details.append(f'中位启动(分位{pos:.0f}%)(+6)')
            elif pos < 75:
                score += 3
                details.append(f'中高位(分位{pos:.0f}%)(+3)')
            else:
                score -= 5
                details.append(f'高位追涨(分位{pos:.0f}%)(-5)')

        # ② 突破性放量（8分）
        vols_20 = [k['volume'] for k in klines if k['volume'] > 0]
        vol_avg = sum(vols_20) / max(len(vols_20), 1) if vols_20 else 0
        if today_vol > 0 and vol_avg > 0 and today_vol < vol_avg * 100:
            vol_ratio = today_vol / vol_avg
            if vol_ratio > 2:
                score += 8
                details.append(f'放量{vol_ratio:.1f}倍启动(+8)')
            elif vol_ratio > 1.5:
                score += 4
                details.append(f'温和放量{vol_ratio:.1f}倍(+4)')
            elif vol_ratio > 0.8:
                details.append(f'量能正常({vol_ratio:.1f}倍)')
            else:
                score -= 3
                details.append(f'缩量{vol_ratio:.1f}倍(-3)')
        elif today_vol > 0 and vol_avg > 0:
            details.append('量比数据异常')
        else:
            details.append('量能数据不足')

        # ③ 筹码沉淀（7分）
        vols = [k['volume'] for k in klines if k['volume'] > 0]
        max_vol = max(vols) if vols else 0
        if max_vol > 0 and today_vol > 0:
            if today_vol < max_vol * 0.5:
                score += 5
                details.append('距前期巨量还有空间(+5)')
            elif today_vol >= max_vol * 0.8:
                score -= 3
                details.append('接近前期套牢区(-3)')

        if is_limit_up:
            score += 2
            details.append('涨停低位突破(+2)')

    except Exception as e:
        logger.warning(f'筹码{code}评分失败: {e}')
        details.append(f'评分异常: {e}')

    score = max(-10, min(25, score))
    return score, details


def score_momentum(code: str, name: str, q: dict) -> Tuple[int, list]:
    """
    资金接力评分（25分）

    核心：今天进来的资金明天还会继续买吗？有没有接力气质？
    数据源：新浪实时行情 + daily_limit_up 涨停明细
    """
    score = 0
    details = []

    try:
        from dao import get_db
        db = get_db()
        today = datetime.now().strftime('%Y%m%d')

        price = q.get('price', 0)
        prev_close = q.get('prev_close', 0)
        volume = q.get('volume', 0)
        high = q.get('high', 0)
        low = q.get('low', 0)
        today_change = (price - prev_close) / prev_close * 100 if prev_close > 0 else 0
        is_limit_up = today_change >= 9.5

        # 获取换手率
        up_info = None
        turnover_rate = 0
        try:
            up_info = db.fetchone(
                'SELECT board_times, turnover_rate, seal_first_time, seal_last_time, bomb_times '
                'FROM daily_limit_up WHERE code=%s AND trade_date=%s', (code, today))
            if up_info and up_info['turnover_rate']:
                turnover_rate = float(up_info['turnover_rate'])
        except:
            pass

        # ① 换手率判断（12分）
        if is_limit_up:
            if 5 <= turnover_rate <= 25:
                score += 12
                details.append(f'换手{round(turnover_rate,1)}%适中(+12)')
            elif 25 < turnover_rate <= 35:
                score += 6
                details.append(f'换手{round(turnover_rate,1)}%偏大(+6)')
            elif turnover_rate > 35:
                score -= 5
                details.append(f'换手{round(turnover_rate,1)}%过大(-5)')
            elif 0 < turnover_rate < 3:
                score -= 8
                details.append(f'换手{round(turnover_rate,1)}%过低一字板(-8)')
            elif turnover_rate > 0:
                score += 6
                details.append(f'换手{round(turnover_rate,1)}%(+6)')
            else:
                details.append('换手率未知')
        else:
            prev_daily = db.fetchone(
                'SELECT volume FROM stock_daily WHERE code=%s AND trade_date<%s ORDER BY trade_date DESC LIMIT 1',
                (code, today))
            if prev_daily and prev_daily['volume'] > 0 and volume > 0:
                vol_ratio = volume / prev_daily['volume']
                if 1.2 <= vol_ratio <= 3:
                    score += 8
                    details.append(f'温和放量{vol_ratio:.1f}倍(+8)')
                elif vol_ratio > 3:
                    score += 4
                    details.append(f'放量{vol_ratio:.1f}倍(+4)')
                else:
                    score -= 3
                    details.append(f'缩量{vol_ratio:.1f}倍(-3)')

        # ② 封板质量（8分，仅涨停）
        if is_limit_up and up_info:
            board_times = int(up_info['board_times']) if up_info['board_times'] else 1
            first_time = str(up_info.get('seal_first_time', '') or '')
            open_times = int(up_info.get('bomb_times', 0)) if up_info.get('bomb_times') else 0

            if first_time and first_time <= '10:00':
                score += 5
                details.append('早盘封板(+5)')
            elif first_time and first_time <= '11:30':
                score += 3
                details.append('午前封板(+3)')
            elif first_time and first_time <= '14:30':
                score += 1
                details.append('午后封板(+1)')
            elif first_time:
                score -= 3
                details.append('尾盘偷板(-3)')

            if open_times == 0:
                score += 3
                details.append('未炸板(+3)')
            elif open_times > 2:
                score -= 3
                details.append(f'多次炸板{open_times}次(-3)')

            if board_times == 2:
                score += 2
                details.append('2板晋级(+2)')
            elif board_times >= 3:
                score -= 5
                details.append(f'连{board_times}板太高(-5)')

        # ③ 盘中走势（5分）
        if high > low:
            pos = (price - low) / (high - low) * 100
            if is_limit_up and pos > 95:
                score += 5
                details.append('封板在顶部(+5)')
            elif not is_limit_up and pos > 60:
                score += 3
                details.append(f'偏强(分位{pos:.0f}%)(+3)')
            elif not is_limit_up and pos < 30:
                score -= 2
                details.append(f'弱势(分位{pos:.0f}%)(-2)')

    except Exception as e:
        logger.warning(f'接力评分{code}失败: {e}')
        details.append(f'评分异常: {e}')

    score = max(-10, min(25, score))
    return score, details


def score_sector_environment(code: str, name: str) -> Tuple[int, list]:
    """
    板块环境评分（20分）

    核心：板块有没有合力？是不是主线？
    数据源：daily_limit_up 涨停行业分布
    """
    score = 0
    details = []

    try:
        from dao import get_db
        db = get_db()
        today = datetime.now().strftime('%Y%m%d')

        ind_row = db.fetchone(
            'SELECT industry FROM daily_limit_up WHERE code=%s AND trade_date=%s AND industry IS NOT NULL AND industry!="" LIMIT 1',
            (code, today))
        if not ind_row or not ind_row['industry']:
            details.append('行业信息不足')
            return 5, details

        industry = ind_row['industry']
        cnt_row = db.fetchone('SELECT COUNT(*) as cnt FROM daily_limit_up WHERE trade_date=%s AND industry=%s AND (status IS NULL OR status != \'跌停\')',
                              (today, industry))
        industry_up_count = int(cnt_row['cnt']) if cnt_row else 0

        # ① 板块涨停家数（12分）
        if industry_up_count >= 8:
            score += 12
            details.append(f'{industry}({industry_up_count}涨停,强合力)(+12)')
        elif industry_up_count >= 5:
            score += 10
            details.append(f'{industry}({industry_up_count}涨停,有合力)(+10)')
        elif industry_up_count >= 3:
            score += 6
            details.append(f'{industry}({industry_up_count}涨停,有效应)(+6)')
        elif industry_up_count >= 2:
            score += 3
            details.append(f'{industry}({industry_up_count}涨停,起步)(+3)')
        else:
            details.append(f'{industry}(独苗{industry_up_count}只)')

        # ② 最高连板（3分）
        if industry_up_count >= 3:
            boards = db.fetchall(
                'SELECT board_times FROM daily_limit_up WHERE trade_date=%s AND industry=%s AND (status IS NULL OR status != \'跌停\')',
                (today, industry))
            max_b = max(int(r['board_times'] or 1) for r in boards)
            if max_b >= 3:
                score += 3
                details.append(f'最高{max_b}板(+3)')

        # ③ 政策题材（5分）
        combined = name + industry
        for topic, keywords in [
            ('低空经济', ['低空', 'eVTOL', '飞行器']),
            ('AI/算力', ['AI', '算力', '大模型', '芯片']),
            ('半导体', ['半导体', '光刻', '封装']),
            ('机器人', ['机器人', '减速器']),
            ('新能源', ['光伏', '锂电池', '固态电池', '氢能']),
            ('消费', ['消费', '首发经济']),
        ]:
            if any(kw.lower() in combined.lower() for kw in keywords):
                score += 5
                details.append(f'题材:{topic}(+5)')
                break
        score = min(score, 20)

    except Exception as e:
        logger.warning(f'板块评分{code}失败: {e}')

    score = max(0, min(20, score))
    return score, details


def score_trend_position(code: str, name: str) -> Tuple[int, list]:
    """
    趋势位置评分（20分）

    v5.2 改写：先判大方向（硬开关），再给细分评分。

    阶段一：大方向判断（一票否决制）
    - 下跌趋势特征：价格在MA20下方 + MA20方向向下
    - 加速下跌：近3日累计跌 > 7%
    - 高位破位：从20日高点回落 > 10%
    满足任一条 → 趋势分≤5，不参与候选

    阶段二：通过后再评分
    - 均线排列（8分）
    - 距MA20位置（6分）
    - 近期涨跌节奏（6分）

    数据源：stock_daily 近30日K线
    """
    score = 0
    details = []

    try:
        from dao import get_db
        db = get_db()
        today = datetime.now().strftime('%Y%m%d')

        klines = db.fetchall('''
            SELECT trade_date, close, high, low, change_pct, volume
            FROM stock_daily
            WHERE code = %s AND trade_date <= %s
            ORDER BY trade_date DESC
            LIMIT 30
        ''', (code, today))

        if not klines or len(klines) < 10:
            details.append('K线数据不足')
            return 5, details

        closes = [k['close'] for k in klines if k['close'] > 0]
        current = closes[0] if closes else 0

        # ════════════════════════════════════════════════════
        # 阶段一：大方向硬判别（先判方向再打分）
        # ════════════════════════════════════════════════════
        trend_bad = False
        trend_reasons = []

        # 1a. MA20方向判断（5天前的MA20 vs 今天的MA20）
        if len(closes) >= 25:
            ma20_today = sum(closes[:20]) / 20
            ma20_5dago = sum(closes[5:25]) / 20
            if current > 0 and current < ma20_today and ma20_today < ma20_5dago:
                trend_bad = True
                trend_reasons.append(f'在MA20({ma20_today:.2f})下方且MA20下行')

        # 1b. 短线破位：现价在MA5/MA10下方 + MA5方向向下（短期弱势确认）
        if len(closes) >= 6:
            ma5 = sum(closes[:5]) / 5
            ma10 = sum(closes[:10]) / 10
            ma5_5dago = sum(closes[1:6]) / 5
            if current < ma5 and current < ma10 and ma5 < ma5_5dago:
                trend_bad = True
                trend_reasons.append(f'短线破位(MA5={ma5:.2f} MA10={ma10:.2f} 均线向下)')

        # 1c. 加速下跌：近3日累计跌 > 7%
        chg_3d = [k['change_pct'] for k in klines[:3] if k['change_pct']]
        if len(chg_3d) >= 2:
            cum_3d = sum(chg_3d)
            if cum_3d < -7:
                trend_bad = True
                trend_reasons.append(f'近3日累计跌{cum_3d:.1f}%(加速下跌)')

        # 1c. 高位破位：从20日高点回落 > 8%（趋势票8%即确认反转）
        if len(klines) >= 20:
            high_20 = max(k['high'] for k in klines[:20] if k['high'] > 0)
            if high_20 > 0 and current > 0:
                drop = (high_20 - current) / high_20 * 100
                if drop > 8:
                    trend_bad = True
                    trend_reasons.append(f'从20日高点回撤{drop:.0f}%(高位破位)')

        # 1d. 近5日连续4天收阴（连跌不止）
        if len(klines) >= 5:
            neg_5d = sum(1 for k in klines[:5] if k['change_pct'] and k['change_pct'] < 0)
            if neg_5d >= 4:
                trend_bad = True
                trend_reasons.append(f'近5日{neg_5d}天收阴(连跌不止)')

        # 1e. 冲高乏力：从20日高点回落>6% + 近5日无一次反弹>1%
        if len(klines) >= 20:
            high_20 = max(k['high'] for k in klines[:20] if k['high'] > 0)
            if high_20 > 0 and current > 0:
                drop = (high_20 - current) / high_20 * 100
                if drop > 6:
                    pos_5d = max((k['change_pct'] or 0) for k in klines[:5])
                    if pos_5d < 1:
                        trend_bad = True
                        trend_reasons.append(f'冲高回落{drop:.0f}%且无反弹(持续走弱)')

        if trend_bad:
            # 跌势票直接给低分，不再进入阶段二
            details.append(f'❌ 下跌趋势: {"; ".join(trend_reasons)}')
            score = max(-10, min(5, score - 5))
            return score, details

        # ════════════════════════════════════════════════════
        # 阶段二：趋势达标，正常评分
        # ════════════════════════════════════════════════════

        # ② 均线排列（8分）
        if len(closes) >= 20:
            ma5 = sum(closes[:5]) / 5
            ma10 = sum(closes[:10]) / 10
            ma20 = sum(closes[:20]) / 20

            if ma5 > ma10 > ma20:
                score += 8
                details.append(f'均线多头排列(MA5>{ma5:.1f})(+8)')
            elif ma5 > ma10 and ma10 < ma20:
                score += 4
                details.append(f'短多中整理(+4)')
            elif ma5 < ma10 and ma10 > ma20:
                score += 2
                details.append('短期回调中(+2)')
            else:
                score -= 5
                details.append('空头排列(-5)')
        elif len(closes) >= 10:
            score += 4
            details.append(f'数据仅{len(closes)}日(+4)')

        # ③ 距MA20位置（6分）
        if len(closes) >= 20 and current > 0:
            ma20 = sum(closes[:20]) / 20
            dist = (current - ma20) / ma20 * 100
            if -3 <= dist <= 0:
                score += 6
                details.append(f'回踩MA20({dist:+.1f}%)(+6)')
            elif 0 < dist < 5:
                score += 3
                details.append(f'MA20上方({dist:+.1f}%)(+3)')
            elif dist > 10:
                score -= 3
                details.append(f'远离MA20({dist:+.1f}%)(-3)')
            elif dist < -3:
                score += 3
                details.append(f'跌破MA20等企稳(+3)')

        # ④ 近期涨跌节奏（6分）
        if len(klines) >= 10:
            recent_chg = [k['change_pct'] for k in klines[:10] if k['change_pct']]
            avg_chg = sum(recent_chg) / len(recent_chg) if recent_chg else 0

            if avg_chg > 3:
                score -= 4
                details.append(f'连续大涨均值{avg_chg:+.1f}%(-4)')
            elif avg_chg > 1:
                score -= 1
                details.append(f'小幅上涨均值{avg_chg:+.1f}%')
            elif avg_chg > -1:
                score += 3
                details.append(f'横盘蓄力均值{avg_chg:+.1f}%(+3)')
            elif avg_chg > -3:
                score += 5
                details.append(f'缩量回调均值{avg_chg:+.1f}%(+5)')
            else:
                score -= 3
                details.append(f'持续下跌均值{avg_chg:+.1f}%(-3)')

    except Exception as e:
        logger.warning(f'趋势评分{code}失败: {e}')

    score = max(-10, min(20, score))
    return score, details


def score_market_safety(market: dict) -> Tuple[int, list]:
    """
    大盘安全垫评分（10分）

    核心：明天大盘继续跌的概率多大？
    """
    score = 0
    details = []
    status = market.get('status', '正常')
    sh_change = market.get('sh_change', 0)

    if status == '做多':
        score = 10
        details.append(f'做多窗口({sh_change:+.2f}%)(+10)')
    elif status == '正常':
        score = 7
        details.append(f'大盘正常({sh_change:+.2f}%)(+7)')
    elif status == '谨慎':
        score = 3
        details.append(f'大盘谨慎({sh_change:+.2f}%)(+3)')
    else:
        # 大盘不做时仍给基础分，由其他4个维度决定总分
        score = 0
        details.append(f'大盘不宜交易({sh_change:+.2f}%)(+0)')
        # 不直接return，继续后面的加分逻辑

    # 加分：板块活跃度高
    try:
        from dao import get_db
        db = get_db()
        today = datetime.now().strftime('%Y%m%d')
        cnt = db.fetchone(
            'SELECT COUNT(DISTINCT industry) as cnt FROM daily_limit_up WHERE trade_date=%s AND (status IS NULL OR status != \'跌停\')',
            (today,))
        if cnt and int(cnt['cnt']) >= 5:
            score += 3
            details.append(f'{cnt["cnt"]}个行业有涨停(+3)')
    except:
        pass

    score = min(score, 10)
    return score, details


# ══════════════════════════════════════════════════════════════
# 候选池构建
# ══════════════════════════════════════════════════════════════


def build_candidate_pool(today_up: list = None) -> List[Dict]:
    """
    构建候选池（3条路径，已合并简化为两条主要路径）
    排除科创板(688)和创业板(300)

    返回: [{'code','name','source','board_times','turnover','industry'}, ...]
    """
    candidates = []
    seen = set()

    from dao import get_db
    today = datetime.now().strftime('%Y%m%d')
    db = get_db()

    if today_up is None:
        today_up = db.fetchall(
            'SELECT code,name,board_times,turnover_rate,industry FROM daily_limit_up WHERE trade_date=%s AND (status IS NULL OR status != \'跌停\')',
            (today,))

    up_codes = set()
    for s in (today_up or []):
        sc = str(s.get('code', ''))
        sn = str(s.get('name', ''))
        up_codes.add(sc)
        seen.add((sc, sn))

    # 路径A：涨停热点（1-2板）
    for s in (today_up or []):
        code = str(s.get('code', ''))
        name = str(s.get('name', ''))
        boards = int(s.get('board_times', 1))
        turnover = float(s.get('turnover_rate', 0))

        if code.startswith('688') or code.startswith('300') or code.startswith('301'):
            continue
        if boards > 2:  # 3板以上不接力
            continue
        if 0 < turnover < 1:  # 换手太低一字板
            continue

        candidates.append({
            'code': code, 'name': name, 'source': '涨停热点',
            'board_times': boards, 'turnover': turnover,
            'industry': str(s.get('industry', '')),
        })

    # 路径B：强势非涨停股
    try:
        strong = db.fetchall('''
            SELECT code, name, change_pct, close, amount
            FROM stock_daily
            WHERE trade_date=%s
              AND change_pct BETWEEN 2.5 AND 9.0
              AND close BETWEEN 5 AND 200
              AND amount > 20000000
              AND NOT (code LIKE '688%%' OR code LIKE '300%%' OR code LIKE '301%%')
            ORDER BY change_pct DESC
            LIMIT 30
        ''', (today,))
        for r in strong:
            code = str(r['code']); name = str(r['name'])
            if (code, name) in seen or code in up_codes:
                continue
            seen.add((code, name))
            candidates.append({
                'code': code, 'name': name, 'source': '强势涨幅',
                'board_times': 0, 'turnover': 0, 'industry': ''})
    except Exception as e:
        logger.warning(f'强势候选失败: {e}')

    return candidates


# ══════════════════════════════════════════════════════════════
# 6. 位置评分（补充维度，作为 bonus 加在总分上）
# ══════════════════════════════════════════════════════════════

def _score_position_in_range(code: str, q: dict) -> int:
    """
    位置评分（0~15分）：判断当前价格在20日区间的位置。
    
    逻辑：
    - 取近20日（含当日）最高价和最低价，计算当前价在区间百分位
    - <30% 低位 → +15
    - 30~60% 中位 → +8
    - 60~85% 偏高 → +3
    - >85% 极高位 → 0（不扣分，已通过风险扣分/趋势扣分惩罚）
    
    目的：解决"高分票都在追涨位"的问题，让低位的票获得加分补偿。
    """
    current_close = q.get('close') or q.get('price') or 0
    if current_close <= 0:
        return 0
    
    try:
        from dao import get_db
        db = get_db()
        cur = db.execute(
            "SELECT MIN(low) as min_l, MAX(high) as max_h FROM stock_daily "
            "WHERE code=%s AND trade_date >= DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 20 DAY), '%%Y%%m%%d')",
            (code,)
        )
        row = cur.fetchone()
        db.close()
        
        if not row or not row['max_h'] or not row['min_l'] or row['max_h'] <= row['min_l']:
            return 0
        
        pos_pct = (current_close - row['min_l']) / (row['max_h'] - row['min_l']) * 100
        
        if pos_pct < 30:
            return 15
        elif pos_pct < 60:
            return 8
        elif pos_pct < 85:
            return 3
        else:
            return 0
    except Exception as e:
        logger.warning(f"位置评分异常 {code}: {e}")
        return 0


# ══════════════════════════════════════════════════════════════
# 主评分入口
# ══════════════════════════════════════════════════════════════


def score_candidate(code: str, name: str) -> Dict:
    """
    综合评分（v5.4新版，预测型）

    评分结构（满分115→上限100）：
    - 筹码结构  25分（低位/放量/沉淀）
    - 资金接力  25分（换手/封板/分时）
    - 板块环境  20分（涨停家数/合力/题材）
    - 趋势位置  20分（均线/MA20距离/节奏）
    - 大盘安全  10分（大盘环境+情绪）
    - 位置评估  +15分（20日区间低位加分，高位不加分）
    - 风险扣分  -15分

    注意：收盘后从DB取今日数据，不再调新浪实时接口（避免超时）。
    位置评分独立加在总分后，仅补充不稀释其他维度权重。
    """
    report = {
        'code': code, 'name': name,
        'total_score': 0, 'grade': '',
        'breakdown': {}, 'risks': [],
        'position_advice': ''
    }

    # 0. 大盘开关（大盘跌>1.5%时不拦截评分，仅在大盘安全维度体现）
    market = check_market_status()

    # 获取今日行情（优先DB，回退新浪）
    q = _get_today_quote_from_db(code)
    if not q:
        quotes = fetch_sina_quote([code])
        q = quotes.get(code, {})

    # 1. 筹码结构
    chip, chip_det = score_chip_structure(code, name, q)
    report['breakdown']['筹码结构'] = {'score': chip, 'max': MAX_SCORES['chip_structure'], 'details': chip_det}

    # 2. 资金接力
    momentum, momentum_det = score_momentum(code, name, q)
    report['breakdown']['资金接力'] = {'score': momentum, 'max': MAX_SCORES['momentum'], 'details': momentum_det}

    # 3. 板块环境
    sector_env, sector_det = score_sector_environment(code, name)
    report['breakdown']['板块环境'] = {'score': sector_env, 'max': MAX_SCORES['sector_environment'], 'details': sector_det}

    # 4. 趋势位置
    trend, trend_det = score_trend_position(code, name)
    report['breakdown']['趋势位置'] = {'score': trend, 'max': MAX_SCORES['trend_position'], 'details': trend_det}

    # 5. 大盘安全
    market_safe, market_det = score_market_safety(market)
    report['breakdown']['大盘安全'] = {'score': market_safe, 'max': MAX_SCORES['market_safety'], 'details': market_det}

    # 6. 位置评分（20日区间低位加分，高位不加分）
    pos_score = _score_position_in_range(code, q)
    max_pos = _W.get('position_bonus', 15)
    report['breakdown']['位置评估'] = {'score': pos_score, 'max': max_pos, 'details': [f'20日区间位置评分: {pos_score}/{max_pos}']}

    # 7. 风险扣分
    risk = get_risk_flags(code)
    if risk['has_risk']:
        report['risks'] = risk['items']

    # 总分（按权重加权）
    w = _W  # 配置权重
    # 各维度原始分 / 各自满分 × 配置权重
    weighted = (
        max(0, chip) / MAX_SCORES['chip_structure'] * w['chip_structure']
        + max(0, momentum) / MAX_SCORES['momentum'] * w['momentum']
        + max(0, sector_env) / MAX_SCORES['sector_environment'] * w['sector_environment']
        + max(0, trend) / MAX_SCORES['trend_position'] * w['trend_position']
        + max(0, market_safe) / MAX_SCORES['market_safety'] * w['market_safety']
        + min(1, max(0, pos_score) / max_pos) * w['position_bonus']
    )
    total = weighted
    if report['risks']:
        max_penalty = w.get('risk_penalty', 15)
        penalty = min(len(report['risks']) * (max_penalty / 3), max_penalty)
        total -= penalty
        report['breakdown']['风险扣分'] = {'penalty': -round(penalty, 1)}

    report['total_score'] = max(0, min(100, round(total, 1)))

    # 评级 & 仓位建议
    if report['total_score'] >= 80:
        report['grade'] = '★★★★★ 强烈推荐'
        report['position_advice'] = '≤50%仓位，竞价看量比确认'
    elif report['total_score'] >= 65:
        report['grade'] = '★★★★ 推荐'
        report['position_advice'] = '≤30%仓位'
    elif report['total_score'] >= 50:
        report['grade'] = '★★★ 观察'
        report['position_advice'] = '≤15%仓位，等开盘确认'
    elif report['total_score'] >= 35:
        report['grade'] = '★★ 谨慎'
        report['position_advice'] = '不推荐，仅做自选跟踪'
    else:
        report['grade'] = '★ 放弃'
        report['position_advice'] = '放弃'

    return report


def format_score_report(reports: list, title: str = '') -> str:
    """格式化评分报告"""
    if not title:
        title = f"{datetime.now().strftime('%Y-%m-%d %H:%M')}"

    lines = [f"📊 **{title}**", ""]
    if not reports:
        lines.append("无候选标的")
        return "\n".join(lines)

    sorted_reports = sorted(reports, key=lambda r: r.get('total_score', 0), reverse=True)

    for i, r in enumerate(sorted_reports, 1):
        score = r.get('total_score', 0)
        grade = r.get('grade', '')

        if r.get('market_block'):
            lines.append(f"{i}. ❌ **{r['name']}({r['code']})** 大盘不宜交易")
            lines.append(r['breakdown'].get('大盘', {}).get('note', ''))
            lines.append("")
            continue

        if score >= 80:
            emoji = '🟢'
        elif score >= 65:
            emoji = '🟡'
        elif score >= 50:
            emoji = '🟠'
        else:
            emoji = '⚪'

        lines.append(f"{i}. {emoji} **{r['name']}({r['code']})** — {score}分 {grade}")

        bd = r.get('breakdown', {})

        dims = []
        for k, m in [('筹码结构', 25), ('资金接力', 25), ('板块环境', 20),
                      ('趋势位置', 20), ('大盘安全', 10)]:
            v = bd.get(k, {})
            dims.append(f"{k[:2]}{v.get('score', 0)}/{m}")
        rk = bd.get('风险扣分', {})
        if rk and rk.get('penalty', 0) < 0:
            dims.append(f"风险{rk['penalty']}")
        lines.append(f"  {' | '.join(dims)}")

        # 详细说明（每个维度取第一条）
        for k, label in [('筹码结构', '📊'), ('资金接力', '💰'), ('板块环境', '🔥'),
                          ('趋势位置', '📈'), ('大盘安全', '🛡️')]:
            det = bd.get(k, {}).get('details', [])
            if det:
                lines.append(f"  {label} {' · '.join(det[:2])}")

        if r.get('risks'):
            lines.append(f"  ⚠️ {'; '.join(r['risks'][:2])}")

        lines.append(f"  💡 {r['position_advice']}")
        lines.append("")

    return "\n".join(lines)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    print("=== 评分器 v5 测试 ===")
    print(check_market_status())
