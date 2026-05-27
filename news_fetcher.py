"""
news_fetcher — 已迁移到 core/fetcher/news_fetcher.py
此文件为向后兼容的 stub，新代码请改用：
    from core.fetcher.news_fetcher import _fetch_ths_news, _fetch_cls_news, _merge_news
"""
# 显式导出私有函数（供 intraday_monitor/morning_check 使用）
from core.fetcher.news_fetcher import _fetch_ths_news, _fetch_cls_news, _merge_news  # noqa: F401, E402
# 全量导出公开函数
from core.fetcher.news_fetcher import *  # noqa: F401, F403
