"""
stock_analysis_api — 已迁移到 utils/stock_analysis_api.py
此文件为向后兼容的 stub，新代码请改用：
    from utils.stock_analysis_api import StockDataFetcher, _curl_text
"""
# 显式暴露内部函数
from utils.stock_analysis_api import StockDataFetcher  # noqa: F401, F403
try:
    from utils.stock_analysis_api import _curl_text  # noqa: F401
except ImportError:
    pass
