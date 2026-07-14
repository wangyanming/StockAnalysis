"""
状态+量价评分 V7（满分30分）← 重写
评估个股的T日状态、技术结构与量能健康度

v7 重写重点：
  - 新增T日状态评分(12分)：T日大涨扣分、缩量回调加分
  - 重写技术结构评分(12分)：低位加分、高位不涨/涨停扣分
  - 精简量能健康度(6分)

子维度：
  - T日状态评分(12分)：根据T日涨跌幅+量价配合判断买入时机
  - 技术结构评分(12分)：均线排列+位置评估+支撑有效性
  - 量能健康度(6分)：量比+筹码换手

策略权重配置：
  - strong_trend: 标准权重
  - pullback: 缩量回踩权重×1.5
  - rotation: 无特殊调节
"""

import sys
import os
from datetime import datetime, timedelta
from typing import Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from utils.logger import setup_logger
from utils.dao import get_db

logger = setup_logger("score_liangjia")


def score_liangjia(code: str, trade_date: Optional[str] = None,
                   strategy: str = 'strong_trend') -> dict:
    """
    状态+量价评分（v7重写版）
    ★ 新增T日状态评分，T日涨停扣-12分
    ★ 技术结构不再奖励高位，改为低位加分

    Args:
        code: 股票代码
        trade_date: YYYYMMDD，默认今天
        strategy: 'strong_trend' | 'pullback' | 'rotation'

    Returns:
        {liangjia: float, details: dict,
         state_score: float, tech_score: float, volume_score: float}
    """
    if trade_date is None:
        trade_date = datetime.now().strftime('%Y%m%d')

    db = get_db()
    details = {}
    total = 0.0

    # 获取今日行情
    today = db.fetchone("""
        SELECT code, open, close, high, low, change_pct, amount, volume, turnover_rate
        FROM stock_daily
        WHERE code=%s AND trade_date=%s
    """, (code, trade_date))

    if not today:
        db.close()
        return {
            'liangjia': 0,
            'details': {},
            'state_score': 0,
            'tech_score': 0,
            'volume_score': 0,
            'volume_ratio': 0,
        }

    close = float(today['close']) if today['close'] else 0
    open_p = float(today['open']) if today['open'] else 0
    high = float(today['high']) if today['high'] else 0
    low = float(today['low']) if today['low'] else 0
    change_pct = float(today['change_pct']) if today['change_pct'] else 0
    volume = float(today['volume']) if today['volume'] else 0
    amount = float(today['amount']) if today['amount'] else 0

    if close <= 0:
        db.close()
        return {'liangjia': 0, 'details': {}, 'state_score': 0, 'tech_score': 0, 'volume_score': 0, 'volume_ratio': 0}

    # ─────────── 计算均线和量比 ───────────
    klines = db.fetchall("""
        SELECT trade_date, close, volume, low, high, change_pct
        FROM stock_daily
        WHERE code=%s AND trade_date<=%s AND close>0
        ORDER BY trade_date DESC
        LIMIT 20
    """, (code, trade_date))

    closes = [float(k['close']) for k in klines if k['close']]
    volumes = [float(k['volume']) for k in klines if k['volume']]
    lows = [float(k['low']) for k in klines if k.get('low') and float(k['low']) > 0]

    ma5 = sum(closes[:5]) / 5 if len(closes) >= 5 else 0
    ma10 = sum(closes[:10]) / 10 if len(closes) >= 10 else 0
    ma20 = sum(closes[:20]) / 20 if len(closes) >= 20 else 0

    # 量比（今日量/前5日均量，不含当日）
    prev_volumes = [float(k['volume']) for k in klines[1:6] if k['volume']] if len(klines) > 1 else []
    avg_vol_5d = sum(prev_volumes) / len(prev_volumes) if prev_volumes else volume
    volume_ratio = volume / avg_vol_5d if avg_vol_5d > 0 else 1.0

    # 近20日最高最低
    high_20d = max(closes[:20]) if closes else close
    low_20d = min(closes[:20]) if closes else close
    range_20d = high_20d - low_20d if high_20d > low_20d else 1

    # 20日区间位置（0~1，0最低1最高）
    position_20d = (close - low_20d) / range_20d if range_20d > 0 else 0.5

    # ─────────────── 1. T日状态评分(12分) ───────────────
    # ★ v7核心新增：T日大涨扣分、缩量回调加分
    state_score = 0.0
    state_detail = ""

    if change_pct >= 9.5:
        # 涨停 → 严重扣分
        state_score = -12
        state_detail = f"涨停(涨幅{change_pct:.1f}%，涨停票不推荐，-12分)"
    elif change_pct >= 7:
        # 大涨7~9.5%
        state_score = -8
        state_detail = f"大涨(涨幅{change_pct:.1f}%，次日接力困难，-8分)"
    elif change_pct >= 5:
        # 中阳5~7%
        state_score = -5
        state_detail = f"中阳(涨幅{change_pct:.1f}%，中阳上影多，-5分)"
    elif change_pct >= 2:
        # 温和放量启动2~5%
        if 1.5 <= volume_ratio <= 3:
            state_score = 10
            state_detail = f"温和放量启动(涨幅{change_pct:.1f}%，量比{volume_ratio:.2f}，+10分)"
        else:
            state_score = 5
            state_detail = f"涨幅{change_pct:.1f}%，量比{volume_ratio:.2f}，普通放量(+5分)"
    elif change_pct >= 0:
        # 平盘小幅震荡0~2%
        if 0.8 <= volume_ratio <= 1.5:
            state_score = 8
            state_detail = f"平盘震荡(涨幅{change_pct:.1f}%，量比{volume_ratio:.2f}，+8分)"
        else:
            state_score = 5
            state_detail = f"小幅震荡(涨幅{change_pct:.1f}%，量比{volume_ratio:.2f}，+5分)"
    elif change_pct >= -4:
        # 跌幅0~-4%
        if volume_ratio < 0.8:
            # 缩量回调，检查支撑
            if ma10 > 0 and close >= ma10:
                state_score = 12
                state_detail = f"缩量回调不破支撑(跌幅{change_pct:.1f}%，量比{volume_ratio:.2f}，支撑有效，+12分)"
            elif ma20 > 0 and close >= ma20:
                state_score = 10
                state_detail = f"缩量回调MA20支撑(跌幅{change_pct:.1f}%，量比{volume_ratio:.2f}，+10分)"
            else:
                state_score = 6
                state_detail = f"缩量回调(跌幅{change_pct:.1f}%，量比{volume_ratio:.2f}，破位，+6分)"
        elif volume_ratio < 1.5:
            state_score = 6
            state_detail = f"小幅回调(跌幅{change_pct:.1f}%，量比{volume_ratio:.2f}，+6分)"
        else:
            state_score = 0
            state_detail = f"放量回调(跌幅{change_pct:.1f}%，量比{volume_ratio:.2f}，观望)"
    elif change_pct >= -5:
        # 跌幅-4~-5%
        if volume_ratio < 0.8:
            state_score = 2
            state_detail = f"缩量阴跌(跌幅{change_pct:.1f}%，量比{volume_ratio:.2f}，+2分)"
        else:
            state_score = -5
            state_detail = f"放量下跌(跌幅{change_pct:.1f}%，量比{volume_ratio:.2f}，-5分)"
    else:
        # 大跌<-5%
        state_score = -8
        state_detail = f"大幅下跌(跌幅{change_pct:.1f}%，趋势破坏，-8分)"

    # 策略调节
    if strategy == 'pullback':
        # 回调低吸策略：缩量回踩评分翻倍（上限12）
        if '缩量回调' in state_detail or '缩量阴跌' in state_detail:
            state_score = min(12, state_score * 1.5)
        # 涨停/大涨扣分加重
        if '涨停' in state_detail or '大涨' in state_detail:
            state_score = max(-12, state_score - 2)

    details['state'] = {
        'score': round(state_score, 1),
        'max_score': 12,
        'detail': state_detail,
    }
    total += state_score

    # ─────────────── 2. 技术结构评分(12分) ───────────────
    # ★ v7重写：不再奖励高位，改为低位加分
    tech_score = 0.0
    tech_detail = ""

    # 2a. 均线排列(5分)：多头排列加分，空头扣分
    if ma5 > 0 and ma10 > 0 and ma20 > 0:
        if ma5 > ma10 > ma20:
            ma_score = 5
            ma_detail = "多头排列(MA5>MA10>MA20)"
        elif ma5 > ma10 or ma10 > ma20:
            ma_score = 3
            ma_detail = "均线胶着，部分多头"
        else:
            ma_score = 0
            ma_detail = "空头排列，均线向下"
    else:
        ma_score = 0
        ma_detail = "均线数据不足"

    # 2b. 位置评估(4分)：低位加分，涨停扣分
    if change_pct >= 9.5:
        # 涨停 → 扣分
        pos_score = -2
        pos_detail = "涨停价在高位(-2分)"
    elif position_20d <= 0.33:
        # 价格在20日区间下1/3 → 低位安全
        pos_score = 4
        pos_detail = f"低位安全(20日区间下1/3，+4分)"
    elif position_20d <= 0.5:
        pos_score = 3
        pos_detail = f"中低位(20日区间中部偏下，+3分)"
    elif position_20d <= 0.66:
        pos_score = 1
        pos_detail = f"中高位(20日区间中部偏上，+1分)"
    else:
        # 价格在20日区间上1/3
        if change_pct >= 0 and change_pct < 5:
            pos_score = 0
            pos_detail = "高位但未涨停，正常"
        else:
            pos_score = -1
            pos_detail = "高位，追高风险(-1分)"

    # 2c. 回调位置/支撑有效性(3分)
    support_score = 0.0
    support_detail = ""
    if change_pct < 0:
        # 下跌时看支撑
        if close >= ma5 and volume_ratio < 1.0:
            support_score = 3
            support_detail = "缩量回踩MA5企稳(+3分)"
        elif close >= ma10 and volume_ratio < 1.2:
            support_score = 2
            support_detail = "缩量回踩MA10企稳(+2分)"
        elif close >= ma20 and volume_ratio < 1.5:
            support_score = 1
            support_detail = "回踩MA20缩量企稳(+1分)"
        elif close < ma20:
            support_score = -2
            support_detail = "破位MA20(-2分)"
        else:
            support_score = 0
            support_detail = "回调中，支撑待确认"
    else:
        # 上涨时位置合理
        if ma10 > 0 and close <= ma10 * 1.05 and volume_ratio > 1.0:
            support_score = 2
            support_detail = "刚突破MA10，启动位置合理(+2分)"
        elif ma20 > 0 and close <= ma20 * 1.05:
            support_score = 2
            support_detail = "刚突破MA20，启动位置合理(+2分)"
        else:
            support_score = 1
            support_detail = "上涨状态，位置正常(+1分)"

    tech_score = ma_score + pos_score + support_score
    tech_score = max(-10, min(12, tech_score))

    tech_detail_parts = [ma_detail, pos_detail, support_detail]
    tech_detail = ' | '.join(tech_detail_parts)

    details['tech'] = {
        'score': round(tech_score, 1),
        'max_score': 12,
        'detail': tech_detail,
    }
    total += tech_score

    # 策略调节：回调策略下技术结构权重×1.2
    if strategy == 'pullback':
        tech_score = min(12, tech_score * 1.2)

    # ─────────────── 3. 量能健康度(6分) ───────────────
    # ★ v7精简版
    volume_score = 0.0
    volume_detail = ""

    if volume_ratio > 0:
        if 1.5 <= volume_ratio <= 3.0:
            volume_score = 6
            volume_detail = f"量比{volume_ratio:.2f}，活跃健康(+6分)"
        elif 0.8 <= volume_ratio < 1.5:
            volume_score = 4
            volume_detail = f"量比{volume_ratio:.2f}，温和正常(+4分)"
        elif volume_ratio > 3.0:
            volume_score = 2
            volume_detail = f"量比{volume_ratio:.2f}，量能过热(+2分)"
        elif 0.5 <= volume_ratio < 0.8:
            volume_score = 3
            volume_detail = f"量比{volume_ratio:.2f}，缩量(+3分)"
        else:
            volume_score = 0
            volume_detail = f"量比{volume_ratio:.2f}，流动性枯竭"
    else:
        volume_score = 0
        volume_detail = "无成交数据"

    details['volume'] = {
        'score': round(volume_score, 1),
        'max_score': 6,
        'detail': volume_detail,
    }
    total += volume_score

    db.close()

    return {
        'liangjia': round(total, 1),
        'details': details,
        'state_score': round(state_score, 1),
        'tech_score': round(tech_score, 1),
        'volume_score': round(volume_score, 1),
        'volume_ratio': round(volume_ratio, 2),
    }


if __name__ == '__main__':
    for code in ['600839', '000858']:
        for s in ['strong_trend', 'pullback', 'rotation']:
            r = score_liangjia(code, strategy=s)
            print(f"{code}[{s}]: 量价{r['liangjia']}/30 状态{r['state_score']} 技术{r['tech_score']} 量能{r['volume_score']}")
            for k, v in r['details'].items():
                print(f"  {k}: {v}")
            print()
