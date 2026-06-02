#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据校验工具类（工程规范 4.2 数据代码强制规范）
所有数据采集代码必须经过此工具校验：
  1. 单位转换合理性：amount / volume ≈ close, 偏差 < 5%
  2. 字段非空检查
  3. 数值范围/阈值检查
  4. 数据时效性检查

用法：
    from utils.data_validator import validate_amount_volume, validate_not_null, validate_range
"""
import logging
from typing import Optional, Union

from utils.logger import setup_logger
logger = setup_logger("data_validator")


def validate_amount_volume(
    amount: Union[int, float],
    volume: Union[int, float],
    close: Union[int, float],
    name: str = "",
    threshold: float = 0.05
) -> bool:
    """
    校验 amount/volume ≈ close，偏差 < threshold（默认5%）
    
    数据源单位校验公式：amount / volume ≈ close
    如果偏差超过阈值，说明单位很可能搞错了
    
    返回 True=通过, False=不通过
    """
    if not volume or not close:
        return True  # 缺数据时跳过（可能是0值）
    
    avg_price = amount / volume
    if avg_price <= 0:
        return True
    
    deviation = abs(avg_price - close) / close
    
    if deviation > threshold:
        logger.warning(
            f"⚠️ 单位校验异常 [{name}]: "
            f"amount/volume={avg_price:.2f} vs close={close:.2f}, "
            f"偏差{deviation*100:.1f}%（阈值{threshold*100:.0f}%）"
        )
        return False
    
    return True


def validate_not_null(
    value,
    field_name: str,
    context: str = ""
) -> bool:
    """
    校验字段非空
    返回 True=通过, False=不通过
    """
    if value is None or (isinstance(value, (str, list, dict)) and len(value) == 0):
        ctx = f" [{context}]" if context else ""
        logger.warning(f"⚠️ 字段为空 [{field_name}]{ctx}")
        return False
    return True


def validate_range(
    value: Union[int, float],
    field_name: str,
    min_val: Optional[float] = None,
    max_val: Optional[float] = None,
    context: str = ""
) -> bool:
    """
    校验数值在合理范围内
    返回 True=通过, False=不通过
    
    usage:
        validate_range(price, "price", min_val=0, max_val=10000)
        validate_range(change_pct, "change_pct", min_val=-20, max_val=20)
    """
    if value is None:
        ctx = f" [{context}]" if context else ""
        logger.warning(f"⚠️ 字段为空 [{field_name}]{ctx}")
        return False
    
    if min_val is not None and value < min_val:
        ctx = f" [{context}]" if context else ""
        logger.warning(f"⚠️ 低于下限 [{field_name}]={value} < {min_val}{ctx}")
        return False
    
    if max_val is not None and value > max_val:
        ctx = f" [{context}]" if context else ""
        logger.warning(f"⚠️ 超出上限 [{field_name}]={value} > {max_val}{ctx}")
        return False
    
    return True


def validate_freshness(
    record_date: str,
    expected_date: str,
    table_name: str = "",
    max_stale_days: int = 1
) -> bool:
    """
    校验数据时效性：记录日期与预期日期相差不超过 max_stale_days
    
    返回 True=最新, False=已过期
    """
    try:
        from datetime import datetime
        
        if len(record_date) == 8:
            rd = datetime.strptime(record_date, "%Y%m%d")
        elif len(record_date) == 10 and '-' in record_date:
            rd = datetime.strptime(record_date, "%Y-%m-%d")
        else:
            return True
        
        if len(expected_date) == 8:
            ed = datetime.strptime(expected_date, "%Y%m%d")
        elif len(expected_date) == 10 and '-' in expected_date:
            ed = datetime.strptime(expected_date, "%Y-%m-%d")
        else:
            return True
        
        diff = (ed - rd).days
        if diff > max_stale_days:
            tbl = f" [{table_name}]" if table_name else ""
            logger.warning(f"⚠️ 数据过期{tbl}: 最新={record_date}, 预期={expected_date}, 相差{diff}天")
            return False
        
        return True
    except (ValueError, TypeError):
        return True


def validate_fetch_result(
    data,
    min_rows: int = 1,
    name: str = "",
    fields: Optional[list] = None
) -> bool:
    """
    校验采集结果是否合理
    
    Args:
        data: 采集到的数据（list of dict 或 DataFrame）
        min_rows: 最少条数要求
        name: 数据名称（用于日志）
        fields: 必填字段列表
    
    Returns: True=正常, False=异常
    """
    n = 0
    if hasattr(data, '__len__'):
        n = len(data)
    
    ctx = f" [{name}]" if name else ""
    
    if n < min_rows:
        logger.warning(f"⚠️ 数据量不足{ctx}: {n}条 < {min_rows}条")
        return False
    
    if fields and n > 0:
        for item in data[:10]:  # 只检查前10条
            for f in fields:
                if f not in item or item[f] is None:
                    logger.warning(f"⚠️ 缺少必填字段{ctx}: {f}")
                    return False
    
    logger.info(f"✅ 数据采集正常{ctx}: {n}条")
    return True


if __name__ == '__main__':
    # 自测
    logger.info("Starting self-test")
    
    # 测试单位校验
    assert validate_amount_volume(10000, 1000, 10, "test") == True  # 10000/1000=10 ≈ 10
    assert validate_amount_volume(10000, 1000, 20, "test") == False  # 10000/1000=10 ≠ 20
    print("✅ 自测通过")
