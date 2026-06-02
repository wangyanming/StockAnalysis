"""
财务数据获取模块 - 从AKShare获取股票基本面数据
数据源：AKShare stock_financial_abstract
  营收/净利润=元
  市盈率/市净率=倍
  每股收益=元
"""

import akshare as ak
import pandas as pd
import logging
import time
import json
import os
from typing import Dict, Optional, List, Tuple

from utils.logger import setup_logger
logger = setup_logger("fundamental")

CACHE_DIR = os.path.join(os.path.dirname(__file__), 'cache')
os.makedirs(CACHE_DIR, exist_ok=True)

# 指标名称映射（从英文metric_name到中文）
METRIC_MAP = {
    'parent_holder_net_profit': '净利润',
    'calculate_parent_holder_net_profit_yoy_growth_ratio': '净利润同比_年报',
    'deduct_net_profit_yoy_growth_ratio': '扣非净利润同比',
    'operating_income_total': '营业总收入',
    'calculate_operating_income_total_yoy_growth_ratio': '营收同比',
    'sale_gross_margin': '销售毛利率',
    'sale_net_interest_ratio': '销售净利率',
    'basic_eps': '基本每股收益',
    'calc_per_net_assets': '每股净资产',
    'index_full_diluted_roe': '净资产收益率-摊薄',
    'index_weighted_avg_roe': '净资产收益率-加权',
    'current_ratio': '流动比率',
    'quick_ratio': '速动比率',
    'assets_debt_ratio': '资产负债率',
}


def get_latest_financial(code: str) -> Optional[Dict]:
    """
    从 stock_financial_abstract_new_ths 获取最新财务数据
    返回结构化的字典
    """
    cache_key = f"financial_v2_{code}"
    cache_path = os.path.join(CACHE_DIR, f"{cache_key}.json")
    
    # 检查缓存（4小时内有效）
    if os.path.exists(cache_path):
        age = time.time() - os.path.getmtime(cache_path)
        if age < 14400:  # 4小时
            try:
                with open(cache_path) as f:
                    data = json.load(f)
                    if data.get('_ts', 0) > time.time() - 14400:
                        return data.get('_result')
            except Exception:
                pass
    
    try:
        time.sleep(0.5)
        df = ak.stock_financial_abstract_new_ths(symbol=code, indicator='按年度')
        if df is None or df.empty:
            logger.warning(f"{code}: 年报无数据，尝试季度")
            df = ak.stock_financial_abstract_new_ths(symbol=code, indicator='按季度')
        
        if df is None or df.empty:
            logger.warning(f"{code}: 无财务数据")
            return None
        
        # 取最新年报数据
        latest_year = df[df['report_name'] == df['report_name'].iloc[0]]
        
        result = {
            'report_period': str(latest_year['report_name'].iloc[0]),
            'report_date': str(latest_year['report_date'].iloc[0]),
        }
        
        for _, row in latest_year.iterrows():
            metric = row.get('metric_name', '')
            value = row.get('value')
            yoy = row.get('yoy')  # 同比数据
            
            cn_name = METRIC_MAP.get(metric, metric)
            
            try:
                result[metric] = float(value)
            except (ValueError, TypeError):
                result[metric] = None
            
            # 存同比（如果有意义的值）
            if yoy is not None and metric in ['parent_holder_net_profit', 'operating_income_total', 'sale_gross_margin']:
                try:
                    result[f'{metric}_yoy'] = float(yoy)
                except (ValueError, TypeError):
                    pass
        
        # 提取关键指标
        extracted = extract_key_metrics(result)
        
        # 缓存
        cache_data = {'_ts': time.time(), '_result': extracted}
        with open(cache_path, 'w') as f:
            json.dump(cache_data, f, ensure_ascii=False, default=str)
        
        return extracted
        
    except Exception as e:
        logger.warning(f"获取{code}财务数据失败: {e}")
        return None


def extract_key_metrics(raw: Dict) -> Dict:
    """从原始数据提取关键指标"""
    
    def safe(key):
        v = raw.get(key)
        if v is None:
            return None
        try:
            return float(v)
        except Exception:
            return None
    
    return {
        'report_period': raw.get('report_period', ''),
        'report_date': raw.get('report_date', ''),
        # 核心指标
        'net_profit': safe('parent_holder_net_profit'),  # 净利润（元）
        'net_profit_yoy': safe('calculate_parent_holder_net_profit_yoy_growth_ratio'),  # 净利润同比(%)
        'revenue': safe('operating_income_total'),  # 营收（元）
        'revenue_yoy': safe('calculate_operating_income_total_yoy_growth_ratio'),  # 营收同比(%)
        'gross_margin': safe('sale_gross_margin'),  # 毛利率(%)
        'net_profit_margin': safe('sale_net_interest_ratio'),  # 净利率(%)
        'roe': safe('index_weighted_avg_roe'),  # ROE(%)
        'eps': safe('basic_eps'),  # 每股收益
        'bvps': safe('calc_per_net_assets'),  # 每股净资产
        'dept_ratio': safe('assets_debt_ratio'),  # 资产负债率(%)
        'current_ratio': safe('current_ratio'),  # 流动比率
    }


def try_get_url(query_url, retries=2):
    """带重试的URL获取"""
    import urllib.request
    for i in range(retries):
        try:
            req = urllib.request.Request(query_url, headers={'User-Agent': 'Mozilla/5.0'})
            resp = urllib.request.urlopen(req, timeout=10)
            return resp.read().decode('utf-8', errors='ignore')
        except Exception as e:
            if i == retries - 1:
                raise e
            time.sleep(1)


def evaluate_fundamental(code: str) -> Tuple[float, Dict]:
    """
    基本面评分（满分25分）
    返回 (评分, 评分明细)
    """
    fin = get_latest_financial(code)
    if not fin:
        return 0, {'error': '无财务数据', 'score': 0}
    
    score = 0
    details = {}
    
    # 1. 净利润增速（8分）— 正向增长就行，不要求暴增
    ny = fin.get('net_profit_yoy')
    if ny is not None:
        if ny > 50:
            profit_score = 8
        elif ny > 20:
            profit_score = 6
        elif ny > 0:
            profit_score = 4
        elif ny > -20:
            profit_score = 2
        else:
            profit_score = 0
        score += profit_score
        details['净利润增速'] = {'value': f'{ny:+.1f}%', 'score': profit_score, 'max': 8}
    else:
        details['净利润增速'] = {'value': 'N/A', 'score': 0, 'max': 8}
    
    # 2. 营收增速（7分）— 比毛利更能反映增长
    ry = fin.get('revenue_yoy')
    if ry is not None:
        if ry > 40:
            rev_score = 7
        elif ry > 20:
            rev_score = 5
        elif ry > 0:
            rev_score = 3
        else:
            rev_score = 0
        score += rev_score
        details['营收增速'] = {'value': f'{ry:+.1f}%', 'score': rev_score, 'max': 7}
    else:
        details['营收增速'] = {'value': 'N/A', 'score': 0, 'max': 7}
    
    # 3. 毛利率（5分）— 看绝对值，但不苛责低毛利行业
    gm = fin.get('gross_margin')
    if gm is not None:
        if gm > 40:
            gm_score = 5
        elif gm > 20:
            gm_score = 4
        elif gm > 10:
            gm_score = 2  # 13%算普遍,拿2分
        elif gm > 5:
            gm_score = 1
        else:
            gm_score = 0
        score += gm_score
        details['毛利率'] = {'value': f'{gm:.1f}%', 'score': gm_score, 'max': 5}
    else:
        details['毛利率'] = {'value': 'N/A', 'score': 0, 'max': 5}
    
    # 4. ROE（5分）
    roe = fin.get('roe')
    if roe is not None:
        if roe > 15:
            roe_score = 5
        elif roe > 8:
            roe_score = 4
        elif roe > 4:
            roe_score = 3
        elif roe > 0:
            roe_score = 1
        else:
            roe_score = 0
        score += roe_score
        details['ROE'] = {'value': f'{roe:.1f}%', 'score': roe_score, 'max': 5}
    else:
        details['ROE'] = {'value': 'N/A', 'score': 0, 'max': 5}
    
    return score, details


def get_risk_flags(code: str) -> Dict:
    """获取风险标记"""
    flags = {'has_risk': False, 'items': []}
    
    fin = get_latest_financial(code)
    if not fin:
        return flags
    
    # 净利润下滑
    ny = fin.get('net_profit_yoy')
    if ny is not None and ny < -50:
        flags['items'].append(f'净利润同比大幅下滑{ny:.0f}%')
    
    # 亏损
    np = fin.get('net_profit')
    if np is not None and np < 0:
        flags['items'].append('当期亏损')
    
    # 高负债
    dr = fin.get('dept_ratio')
    if dr is not None and dr > 70:
        flags['items'].append(f'高负债率{dr:.1f}%')
    
    if flags['items']:
        flags['has_risk'] = True
    
    return flags


if __name__ == '__main__':
    logger.info("Running standalone test")
    
    tests = ['002297', '603095', '002185', '603005']
    for code in tests:
        print(f"\n=== {code} ===")
        fin = get_latest_financial(code)
        if fin:
            print(f"  报告期: {fin.get('report_period')}")
            print(f"  净利润: {fin.get('net_profit')}")
            print(f"  净利润同比: {fin.get('net_profit_yoy')}")
            print(f"  营收: {fin.get('revenue')}")
            print(f"  营收同比: {fin.get('revenue_yoy')}")
            print(f"  毛利率: {fin.get('gross_margin')}")
            print(f"  ROE: {fin.get('roe')}")
            print(f"  负债率: {fin.get('dept_ratio')}")
            
            score, details = evaluate_fundamental(code)
            print(f"  基本面评分: {score}/35")
            for k, v in details.items():
                print(f"    {k}: {v}")
            
            risk = get_risk_flags(code)
            if risk.get('has_risk'):
                print(f"  ⚠️ 风险: {risk['items']}")
