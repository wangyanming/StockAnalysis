#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
东财 push2 实时市场数据获取模块

接口：push2.eastmoney.com/api/qt/ulist.np/get
返回两市指数行情、涨跌家数、成交额、主力/散户资金流。

使用方式：
    data = fetch_push2_market_data()
    if data:
        print(data['rise_total'], data['fall_total'])
"""

import json
import time
import subprocess
import sys
import os

_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _project_root)

from utils.logger import setup_logger

logger = setup_logger('push2_market')

PUSH2_URL = (
    'http://push2.eastmoney.com/api/qt/ulist.np/get'
    '?fltt=2'
    '&fields=f2,f3,f4,f6,f12,f14,f104,f105,f106,f62,f66,f69,f72,f75,f78,f81,f84,f87'
    '&secids=1.000001,0.399001'
)

_cache = None
_cache_time = 0
CACHE_TTL = 30  # 30秒缓存


def fetch_push2_market_data():
    """
    通过东方财富 push2 ulist.np 接口获取两市实时市场数据。

    返回 dict:
        sh_index_change: float  上证涨跌幅(%)
        sz_index_change: float  深证涨跌幅(%)
        rise_total: int         两市总上涨家数
        fall_total: int         两市总下跌家数
        flat_total: int         两市平盘家数
        amount_total: float     两市总成交额(亿)
        main_flow: float        主力净流入(亿)
        retail_flow: float      散户(小单)净流入(亿)

    接口失败时返回 None，调用方负责降级处理。
    """
    global _cache, _cache_time
    now = time.time()
    if _cache is not None and now - _cache_time < CACHE_TTL:
        return _cache

    try:
        result = subprocess.run([
            'curl', '-s',
            '-H', 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            '-m', '5',
            PUSH2_URL,
        ], capture_output=True, text=True, timeout=10)
        raw = result.stdout.strip()
        if not raw:
            logger.warning('push2 接口返回空响应（可能非交易时段）')
            return None
        data = json.loads(raw)
        items = data.get('data', {}).get('diff', [])

        sh = {}
        sz = {}
        for item in items:
            code = item.get('f12', '')
            if code == '000001':
                sh = item
            elif code == '399001':
                sz = item

        # 安全取值函数
        def _v(d: dict, key: str) -> float:
            v = d.get(key)
            return float(v) if v is not None else 0.0

        # 合并两市数据
        rise_total = int(_v(sh, 'f104') + _v(sz, 'f104'))
        fall_total = int(_v(sh, 'f105') + _v(sz, 'f105'))
        flat_total = int(_v(sh, 'f106') + _v(sz, 'f106'))
        amount_yuan = _v(sh, 'f6') + _v(sz, 'f6')
        main_flow_yuan = _v(sh, 'f62') + _v(sz, 'f62')
        retail_flow_yuan = _v(sh, 'f84') + _v(sz, 'f84')

        result = {
            'sh_index_change': _v(sh, 'f3'),
            'sz_index_change': _v(sz, 'f3'),
            'rise_total': rise_total,
            'fall_total': fall_total,
            'flat_total': flat_total,
            'amount_total': round(amount_yuan / 1e8, 0),
            'main_flow': round(main_flow_yuan / 1e8, 0),
            'retail_flow': round(retail_flow_yuan / 1e8, 0),
        }

        _cache = result
        _cache_time = now
        logger.info(
            f'push2实时数据: 涨{rise_total}跌{fall_total} '
            f'成交{result["amount_total"]:.0f}亿 '
            f'主力{result["main_flow"]:+.0f}亿 '
            f'散户{result["retail_flow"]:+.0f}亿'
        )
        return result

    except json.JSONDecodeError as e:
        logger.warning(f'push2 JSON解析失败: {e}')
    except subprocess.TimeoutExpired:
        logger.warning('push2 curl 请求超时')
    except Exception as e:
        logger.warning(f'push2 实时行情获取异常: {e}')

    return None
