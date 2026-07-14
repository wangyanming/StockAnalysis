"""
策略中枢 V7 - 根据市场模式路由到对应的选股策略

v7 修正：候选条件放宽
  - 近5日有涨停 → 改为近10日/近15日有涨停（任一交易日）
  - 成交额门槛降低（5亿→3亿/2亿）
  - 市值门槛降低（50亿→30亿/20亿）

输入：
  mode: 'strong_trend' | 'pullback' | 'rotation'
  market: 大盘指数信息
  main_lines: F1_市场主线识别的输出

输出：
  strategy: 策略配置（过滤条件 + 评分权重 + 量价权重）
"""

import sys
import os
from datetime import datetime
from typing import Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from utils.logger import setup_logger

logger = setup_logger("策略中枢")


def route_strategy(mode: str, market: dict,
                   main_lines: Optional[list] = None) -> dict:
    """
    根据市场模式路由策略

    Args:
        mode: 'strong_trend' | 'pullback' | 'rotation'
        market: {'sh_change': float, 'status': str, ...}
        main_lines: F1_市场主线识别 的 main_lines list

    Returns:
        dict: 策略配置
    """
    sh_change = market.get('sh_change', 0)

    # Strategy A: 强主线趋势
    if mode == 'strong_trend' and sh_change >= -0.5:
        return {
            'strategy': 'A_强主线趋势',
            'candidate_filter': {
                'require_zt_history': True,
                'require_main_line': True,
                'zt_history_days': 10,          # ★ v7: 近10日有涨停
                'min_amount': 300000000,          # ★ v7: 5亿→3亿
                'min_market_cap': 3000000000,     # ★ v7: 50亿→30亿
                'max_3d_drop': 8,
            },
            'scoring_weights': {
                'renqi': 30,
                'zhuxian': 25,
                'liangjia': 25,
                'market': 10,
                'risk': 10,
            },
            'qijia_weight': {
                'main_strategy': 'strong_trend',
                'fenqi_multiplier': 1.0,
                'pullback_bonus': False,
            },
            'strategy_label': 'v3强主线趋势',
        }

    # Strategy B: 主线回调低吸
    if mode == 'pullback' or (mode == 'strong_trend' and sh_change < -0.5):
        return {
            'strategy': 'B_主线回调低吸',
            'candidate_filter': {
                'require_zt_history': True,
                'require_main_line': True,
                'zt_history_days': 10,          # ★ v7: 近10日有涨停
                'min_amount': 200000000,          # ★ v7: 3亿→2亿
                'min_market_cap': 2000000000,     # ★ v7: 30亿→20亿
                'max_drop_from_high': 20,         # ★ v7: 15%→20%
                'pullback_targets': True,
            },
            'scoring_weights': {
                'renqi': 20,
                'zhuxian': 20,
                'liangjia': 35,
                'market': 15,
                'risk': 10,
            },
            'qijia_weight': {
                'main_strategy': 'pullback',
                'fenqi_multiplier': 1.0,
                'pullback_bonus': True,
            },
            'strategy_label': 'v3主线回调',
        }

    # Strategy C: 无主线轮动
    return {
        'strategy': 'C_无主线轮动',
        'candidate_filter': {
            'require_zt_history': True,
            'require_main_line': False,
            'zt_history_days': 15,             # ★ v7: 近15日有涨停
            'min_amount': 200000000,            # ★ v7: 5亿→2亿
            'min_market_cap': 2000000000,       # ★ v7: 30亿→20亿
            'focus': 'leadership',
        },
        'scoring_weights': {
            'renqi': 40,
            'zhuxian': 10,
            'liangjia': 20,
            'market': 20,
            'risk': 10,
        },
        'qijia_weight': {
            'main_strategy': 'rotation',
            'fenqi_multiplier': 0.5,
            'pullback_bonus': False,
        },
        'strategy_label': 'v3无主线轮动',
    }


def get_scoring_config(strategy_config: dict) -> dict:
    """从策略配置中提取评分配置"""
    return {
        'weights': strategy_config['scoring_weights'],
        'qijia': strategy_config['qijia_weight'],
        'filter': strategy_config['candidate_filter'],
        'strategy': strategy_config['strategy'],
        'strategy_label': strategy_config['strategy_label'],
    }


if __name__ == '__main__':
    test_modes = ['strong_trend', 'pullback', 'rotation']
    for mode in test_modes:
        cfg = route_strategy(mode, {'sh_change': 0.5, 'status': '正常'})
        print(f"=== {mode} ===")
        print(f"  策略: {cfg['strategy']}")
        print(f"  label: {cfg['strategy_label']}")
        print(f"  权重: {cfg['scoring_weights']}")
        print(f"  过滤: {cfg['candidate_filter']}")
        print()
