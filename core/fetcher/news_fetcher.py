#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
新闻采集模块
数据源（按优先级）：
  1. 同花顺快讯（实时行情/板块异动）
  2. 财联社电报（消息面/政策深度）
"""

import sys, os, json, logging, re
import requests
# 确保项目根目录在 sys.path + 日志落盘
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_log_dir = os.path.join(_project_root, "logs")
if not os.path.exists(_log_dir):
    os.makedirs(_log_dir, exist_ok=True)
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(_log_dir, "news_fetcher.log"))
    ]
)
logger = logging.getLogger(__name__)

# ============================================================
#  同花顺快讯
# ============================================================

def _fetch_ths_news() -> list:
    """从同花顺获取快讯"""
    items = []
    try:
        url = "https://news.10jqka.com.cn/tapp/news/push/stock"
        r = requests.get(url, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
            'Referer': 'https://www.10jqka.com.cn/',
        }, timeout=10)
        data = r.json()
        raw = data.get('data', {}).get('list', [])
        for item in raw:
            title = (item.get('title') or '').strip()
            digest = (item.get('digest') or '').strip()
            seq = str(item.get('seq', ''))
            ts = ''
            if seq.isdigit() and len(seq) >= 10:
                try:
                    ts = datetime.fromtimestamp(int(seq[:10])).strftime('%H:%M')
                except Exception:
                    pass
            # 去重去空
            if not title:
                continue
            text = title
            if digest and digest != title:
                text += f"【{digest[:200]}】"
            items.append({
                'source': '同花顺',
                'time': ts,
                'title': title,
                'text': text,
            })
        logger.info(f"同花顺: 获取 {len(items)} 条")
    except Exception as e:
        logger.warning(f"同花顺快讯获取失败: {e}")
    return items


# ============================================================
#  财联社快讯
# ============================================================

def _fetch_cls_news() -> list:
    """从财联社telegraph页面解析快讯"""
    items = []
    try:
        url = "https://www.cls.cn/telegraph"
        r = requests.get(url, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        }, timeout=10)
        html = r.text

        # 找到 telegraphList 数组
        idx = html.find('"telegraphList"')
        if idx < 0:
            logger.warning("财联社: 未找到telegraphList")
            return items

        snippet = html[idx:]
        # 匹配顶层 JSON 数组
        stack = 0
        end = -1
        for i, ch in enumerate(snippet):
            if ch == '[':
                stack += 1
            elif ch == ']':
                stack -= 1
                if stack == 0:
                    end = i + 1
                    break
        if end <= 0:
            logger.warning("财联社: 未找到数组结束")
            return items

        raw = snippet[:end]

        # 逐对象解析
        i = raw.find('{')
        while i >= 0:
            bct = 0
            for j in range(i, min(len(raw), i + 5000)):
                if raw[j] == '{':
                    bct += 1
                elif raw[j] == '}':
                    bct -= 1
                    if bct == 0:
                        try:
                            obj = json.loads(raw[i:j + 1])
                            if 'content' in obj:
                                title = obj.get('title', '') or ''
                                content = obj.get('content', '')
                                text = title or content
                                text = re.sub(r'<[^>]+>', '', text)
                                ctime = obj.get('ctime', 0)
                                ts = ''
                                if ctime:
                                    ts = datetime.fromtimestamp(ctime).strftime('%H:%M')
                                if text:
                                    items.append({
                                        'source': '财联社',
                                        'time': ts,
                                        'title': title[:100] if title else text[:60],
                                        'text': text[:300],
                                    })
                        except json.JSONDecodeError:
                            pass
                        i = raw.find('{', j + 1)
                        break
            else:
                break

        logger.info(f"财联社: 提取 {len(items)} 条")
    except Exception as e:
        logger.warning(f"财联社快讯获取失败: {e}")

    return items


# ============================================================
#  合并去重 + 按时间排序
# ============================================================

def _merge_news(ths: list, cls: list, max_items: int) -> str:
    """合并多个源，去重后返回格式化字符串"""
    seen = set()
    merged = []

    # 财联社优先（消息面更深），同花顺补充（实时行情）
    for item in cls + ths:
        key = item['title'][:40]  # 取前40字去重
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)

    return merged[:max_items]


# ============================================================
#  对外接口
# ============================================================

def fetch_morning_news(max_items: int = 6) -> str:
    """获取晨间快讯，返回格式化字符串"""
    ths = _fetch_ths_news()
    cls = _fetch_cls_news()

    merged = _merge_news(ths, cls, max_items)

    if not merged:
        return "  暂无重大盘前消息"

    lines = []
    for item in merged:
        ts = item.get('time', '')
        source = item.get('source', '')
        text = item.get('text', item.get('title', ''))
        if ts:
            lines.append(f"  [{ts}] [{source}] {text[:150]}")
        else:
            lines.append(f"  [{source}] {text[:150]}")

    return '\n'.join(lines)


if __name__ == '__main__':
    print(f"📰 快讯 — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    news = fetch_morning_news(max_items=8)
    lines = news.split('\n')
    for i, line in enumerate(lines, 1):
        print(f"{i}. {line}")
        print()
