#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
盘中监控模块(固定三段输出)
数据源:
  新浪实时行情: 价格=元, volume=手(x100->股), amount=元
  新浪指数: 点数/涨跌幅
"""

import sys, os, json, logging, re
import time
# 确保项目根目录在 sys.path + 日志落盘
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _project_root)
os.chdir(_project_root)
from datetime import datetime, timedelta

from utils.logger import setup_logger
logger = setup_logger("intraday_monitor")
_market_summary_cache = None
_market_summary_cache_time = 0

def _get_market_summary_cached():
    """盘中实时涨跌家数/成交额 - 同花顺直采(重试3次), 3次失败不显示, 无 DB 兜底"""
    global _market_summary_cache, _market_summary_cache_time
    now = time.time()
    # 30s TTL：实时成功时命中缓存；失败时命中 None 标记，避免高频反复打接口
    if now - _market_summary_cache_time < 30:
        return _market_summary_cache
    from utils.stock_analysis_api import StockDataFetcher
    f = StockDataFetcher()
    try:
        # 盘中实时：同花顺直采，重试3次（内部自带 retry，失败返回 {}），只读不写库
        rt = f.get_market_summary_realtime(retries=3, retry_interval=2.5)
        if rt and rt.get('up_count', 0) > 0:
            _market_summary_cache = rt
            _market_summary_cache_time = now
            logger.info(f'盘中实时涨跌家数: 涨{rt["up_count"]} 跌{rt["down_count"]}')
            return rt
        # 3 次仍失败 → 清缓存返回 None，调用方不显示（无 DB 兜底）
        _market_summary_cache = None
        _market_summary_cache_time = now
        logger.warning('实时市场汇总 3 次失败: 本次不显示涨跌家数/成交额')
        return None
    except Exception as e:
        logger.warning(f'实时市场汇总异常: {e}')
        _market_summary_cache = None
        _market_summary_cache_time = now
    return None


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)


def fetch_index(idx_code: str):
    """获取指数实时行情(新浪)"""
    import urllib.request
    # 新浪指数编码: sh000001, sz399001, sz399006, sh000688
    prefix = 'sh' if idx_code.startswith('000') else 'sz'
    url = f'https://hq.sinajs.cn/list=s_{prefix}{idx_code}'
    req = urllib.request.Request(url, headers={
        'Referer': 'https://finance.sina.com.cn',
        'User-Agent': 'Mozilla/5.0',
    })
    resp = urllib.request.urlopen(req, timeout=5)
    data = resp.read().decode('gbk')
    # 新浪指数格式: name,current_price,change_amount,change_pct%,volume,amount
    parts = data.split('"')[1].split(',')
    name = parts[0]
    cur = float(parts[1]) if parts[1] else 0
    chg = float(parts[3]) if parts[3] else 0
    return {'name': name, 'price': cur, 'change_pct': chg}


def fetch_realtime_market_summary() -> dict:
    """盘中实时涨跌家数+成交额 - 仅用实时API,拿不到就返回空"""
    try:
        d = _get_market_summary_cached()
        if d and d.get('up_count', 0) > 0:
            return {
                'rise': d['up_count'],
                'fall': d['down_count'],
                'flat': d.get('flat_count', 0),
                'total_yi': d.get('total_amount', 0) / 1e8,
            }
    except Exception as e:
        logger.warning(f'实时市场汇总获取失败: {e}')
    return {'rise': '?', 'fall': '?', 'flat': 0, 'total_yi': 0}


def fetch_amount_total_realtime() -> dict:
    """成交额 - 复用 fetch_realtime_market_summary(实时直采,3次失败不显示)"""
    ms = fetch_realtime_market_summary()
    total_yi = ms.get('total_yi', 0)
    # 前日比较：取 今天之前最近一个有成交额数据的真实交易日（跳过周末/长假，而非简单 yesterday）
    from utils.dao import get_db
    db = get_db()
    sp = db.fetchone(
        "SELECT record_date, SUM(amount) as total_amt FROM sector_performance "
        "WHERE record_date < %s AND rank_type='all' AND amount > 0 "
        "GROUP BY record_date ORDER BY record_date DESC LIMIT 1",
        (datetime.now().strftime('%Y-%m-%d'),))
    prev_yi = float(sp['total_amt']) / 1e8 if sp and sp['total_amt'] else -1
    diff_yi = total_yi - prev_yi if prev_yi > 0 else None
    return {'total_yi': total_yi, 'yesterday_same_yi': prev_yi if prev_yi > 0 else 0, 'diff_yi': diff_yi}


def fetch_news_compact(max_items: int = 6) -> list:
    """获取精炼新闻(同花顺+财联社)"""
    lines = []
    try:
        from core.fetcher.news_fetcher import _fetch_ths_news, _fetch_cls_news, _merge_news
        merged = _merge_news(_fetch_ths_news(), _fetch_cls_news(), 20)
        tags = [
            (r'特朗普|拜登|美国|会谈|关税|贸易|制裁|访华', '🇨🇳🇺🇸'),
            (r'涨停|拉升|走强|活跃|冲高|大涨', '🟢'),
            (r'跌|下挫|回落|翻绿|下跌|调整', '📉'),
            (r'AI|芯片|半导体|算力|机器人|人工智能', '🤖'),
            (r'新能源|光伏|风电|电池|储能|新能源车', '⚡'),
            (r'消费|零售|食品|电商|旅游', '🛒'),
            (r'公告|财报|业绩|分红|减持|回购', '📰'),
        ]
        seen = set()
        for item in merged:
            title = (item.get('title', '') or item.get('text', ''))[:80]
            title = re.sub(r'财联社\d+月\d+日电[,]*', '', title)
            title = title.replace('[', '').replace(']', '').strip()
            if len(title) < 10:
                continue
            dedup = re.sub(r'\d{2}:\d{2}\s*', '', title)[:30]
            if dedup in seen:
                continue
            seen.add(dedup)
            tag = ''
            for pat, emoji in tags:
                if re.search(pat, title):
                    tag = emoji
                    break
            lines.append(f'{tag} {title}' if tag else f'📰 {title}')
            if len(lines) >= max_items:
                break
    except Exception as e:
        logger.warning(f'新闻获取失败: {e}')
    return lines


def fetch_quote(code: str):
    """获取个股实时行情"""
    import urllib.request
    market = 'sz' if code.startswith('00') or code.startswith('30') else 'sh'
    url = f'https://hq.sinajs.cn/list={market}{code}'
    req = urllib.request.Request(url, headers={
        'Referer': 'https://finance.sina.com.cn',
        'User-Agent': 'Mozilla/5.0',
    })
    resp = urllib.request.urlopen(req, timeout=5)
    data = resp.read().decode('gbk')
    parts = data.split('"')[1].split(',')
    if len(parts) < 32:
        return None
    return {
        'name': parts[0],
        'open': float(parts[1]) if parts[1] else 0,
        'prev_close': float(parts[2]) if parts[2] else 0,
        'current': float(parts[3]) if parts[3] else 0,
        'high': float(parts[4]) if parts[4] else 0,
        'low': float(parts[5]) if parts[5] else 0,
        'volume_hand': int(parts[8]) if parts[8] else 0,
        'amount': float(parts[9]) if parts[9] else 0,
    }

# ============================================================
# 止损三问辅助函数(2026-06-01 新增)
# ============================================================


def _get_stock_sector(code: str):
    """查询个股所属行业板块

    数据源: 东财个股行情接口(f127=行业板块名),无需DB
    返回: 板块名称字符串,或 None
    """
    import urllib.request
    try:
        market = '1.' if code.startswith('6') else '0.'
        url = f'http://push2.eastmoney.com/api/qt/stock/get?secid={market}{code}&fields=f57,f127'
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://quote.eastmoney.com/',
        })
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read().decode('utf-8'))
        sector = data.get('data', {}).get('f127', '')
        if sector:
            return sector
    except Exception as e:
        logger.warning(f'查个股板块失败({code}): {e}')
    return None


def _get_sector_ranking(sector_name: str):
    """获取行业板块在当日所有板块中的涨跌幅排行

    数据源: 东财行业板块实时接口(push2)
    返回: {'rank': int, 'total': int, 'change_pct': float, 'judgment': str}
    排行前30%→板块强, 后30%→板块弱, 中间→中性
    """
    import urllib.request
    try:
        url = ('http://push2.eastmoney.com/api/qt/clist/get?'
               'fs=m:90+t:2&fields=f12,f14,f3&pi=0&pz=200&po=1&np=1'
               '&fltt=2&invt=2&fid=f3')
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://quote.eastmoney.com/',
        })
        resp = urllib.request.urlopen(req, timeout=8)
        data = json.loads(resp.read().decode('utf-8'))
        items = data.get('data', {}).get('diff', [])
        if not items:
            # 回退:从 sector_performance 取昨日排行
            return _get_sector_ranking_from_db(sector_name)
        total = len(items)
        target_idx = -1
        target_chg = 0
        for i, s in enumerate(items):
            if s.get('f14', '') == sector_name:
                target_idx = i
                target_chg = s.get('f3', 0)
                break
        if target_idx < 0:
            # 名字不完全匹配时,尝试模糊匹配
            for i, s in enumerate(items):
                if sector_name in s.get('f14', ''):
                    target_idx = i
                    target_chg = s.get('f3', 0)
                    break
        if target_idx < 0:
            return {'rank': 0, 'total': total, 'change_pct': 0, 'judgment': '❓未知'}
        rank = target_idx + 1
        pct_pos = rank / total
        if pct_pos <= 0.3:
            judgment = '🟢 强'
        elif pct_pos >= 0.7:
            judgment = '🔴 弱'
        else:
            judgment = '🟡 中'
        return {'rank': rank, 'total': total, 'change_pct': target_chg, 'judgment': judgment}
    except Exception as e:
        logger.warning(f'查板块排行实时失败({sector_name}): {e}')
        return _get_sector_ranking_from_db(sector_name)


def _get_sector_ranking_from_db(sector_name: str):
    """回退:从 sector_performance 取昨日板块排行"""
    try:
        from utils.dao import get_db
        db = get_db()
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        rows = db.fetchall(
            "SELECT sector_name, change_pct FROM sector_performance WHERE record_date=%s AND rank_type='all' AND change_pct IS NOT NULL ORDER BY change_pct DESC",
            (yesterday,))
        if not rows or len(rows) < 10:
            return {'rank': 0, 'total': 0, 'change_pct': 0, 'judgment': '❓未知'}
        total = len(rows)
        for i, r in enumerate(rows):
            if sector_name in r.get('sector_name', ''):
                rank = i + 1
                chg = r.get('change_pct', 0)
                pct_pos = rank / total
                if pct_pos <= 0.3:
                    judgment = '🟢 强(昨)'
                elif pct_pos >= 0.7:
                    judgment = '🔴 弱(昨)'
                else:
                    judgment = '🟡 中(昨)'
                return {'rank': rank, 'total': total, 'change_pct': chg, 'judgment': judgment}
        return {'rank': 0, 'total': total, 'change_pct': 0, 'judgment': '❓未知(昨)'}
    except Exception as e:
        logger.warning(f'查板块排行从DB失败({sector_name}): {e}')
        return {'rank': 0, 'total': 0, 'change_pct': 0, 'judgment': '❓未知'}


def _get_volume_ratio(code: str, today_vol_hand: int):
    """计算当日已成交量 vs 近5日均量

    参数:
        code: 股票代码
        today_vol_hand: 当日已成交量(手,来自新浪实时)
    返回: {'ratio': float, 'today_vol': int, 'ma5_vol': int, 'judgment': str}
    量比<0.8→缩量(洗盘信号), 0.8~1.2→平量, >1.2→放量(真跌信号)
    返回 judgment 带 emoji 前缀
    """
    try:
        from utils.dao import get_db
        db = get_db()
        # stock_daily.volume 单位是股,需 /100 转为手
        rows = db.fetchall(
            "SELECT volume FROM stock_daily WHERE code=%s AND trade_date < CURDATE() ORDER BY trade_date DESC LIMIT 5",
            (code,))
        if not rows or len(rows) < 3:
            return {'ratio': 0, 'today_vol': today_vol_hand, 'ma5_vol': 0, 'judgment': '❓未知'}
        vols = [r['volume'] for r in rows if r['volume'] and r['volume'] > 0]
        if len(vols) < 3:
            return {'ratio': 0, 'today_vol': today_vol_hand, 'ma5_vol': 0, 'judgment': '❓未知'}
        ma5_vol_hand = (sum(vols) / len(vols)) / 100  # 股→手
        ratio = today_vol_hand / ma5_vol_hand if ma5_vol_hand > 0 else 0
        if ratio < 0.8:
            judgment = '🔵 缩量(洗盘特征)'
        elif ratio <= 1.2:
            judgment = '🟡 平量'
        else:
            judgment = '🔴 放量(警惕)'
        return {
            'ratio': round(ratio, 2),
            'today_vol': today_vol_hand,
            'ma5_vol': round(ma5_vol_hand),
            'judgment': judgment,
        }
    except Exception as e:
        logger.warning(f'计算量比失败({code}): {e}')
        return {'ratio': 0, 'today_vol': today_vol_hand, 'ma5_vol': 0, 'judgment': '❓未知'}


def _judge_stock(name: str, code: str, profit_pct: float, cur_price: float, prev_close: float, today_vol_hand: int):
    """止损三问综合判断 - 输出一段结构化判断文本

    参数:
        name/code: 股票名称/代码
        profit_pct: 盈亏百分比(已算出)
        cur_price: 现价
        prev_close: 昨收
        today_vol_hand: 今日成交量(手)
    返回: str(多行判断文本)
    """
    lines = []

    # 红线:亏损超-10% 不走三问
    if profit_pct < -10:
        lines.append(f'🚨 {name}({code}) 亏损{profit_pct:.2f}%,超过-10%红线,建议立即止损')
        return '\n'.join(lines)

    # 标题行
    if profit_pct < -5:
        lines.append(f'🚨 {name}({code}) 亏损{profit_pct:.2f}%,已触及止损线')
    else:
        lines.append(f'⚠️ {name}({code}) 亏损{profit_pct:.2f}%,接近止损线')

    # 1 量能判断
    vr = _get_volume_ratio(code, today_vol_hand)
    if vr['judgment'] != '❓未知':
        lines.append(f'  1 量能:今日量/5日均量 = {vr["ratio"]} → {vr["judgment"]}')
    else:
        lines.append(f'  1 量能:❓ 数据不足(今日{vr["today_vol"]}手,5日均量{vr["ma5_vol"]}手)')

    # 2 板块判断
    sector = _get_stock_sector(code)
    sector_judgment = ''
    if sector:
        sr = _get_sector_ranking(sector)
        sector_judgment = sr.get('judgment', '')
        if sector_judgment != '❓未知':
            lines.append(f'  2 板块:{sector} 涨幅{sr["change_pct"]:+.1f}%(行业排名 {sr["rank"]}/{sr["total"]})→ {sr["judgment"]}')
        else:
            lines.append(f'  2 板块:{sector} → ❓ 排行未知')
    else:
        lines.append(f'  2 板块:❓ 未查到所属板块')

    # 3 时间判断
    now = datetime.now()
    h, m = now.hour, now.minute
    total_min = h * 60 + m
    if total_min < 14 * 60:
        time_judgment = '⏳ 建议观察,等14:30再决策'
    else:
        time_judgment = '⏰ 尾盘窗口,关注收盘价'
    lines.append(f'  3 时间:{h:02d}:{m:02d} → {time_judgment}')

    # 4 综合判断(复用已有结果,不再重复调接口)
    wash_signals = 0
    real_signals = 0

    if vr['judgment'] not in ('❓未知',):
        if vr['ratio'] < 0.8:
            wash_signals += 1  # 缩量→洗盘
        elif vr['ratio'] > 1.2:
            real_signals += 1  # 放量→真跌
    if sector_judgment:
        if '强' in sector_judgment:
            wash_signals += 1  # 板块强→洗盘
        elif '弱' in sector_judgment:
            real_signals += 1  # 板块弱→真跌
    # 时间:14:00前更倾向洗盘(保留观察时间)
    if total_min < 14 * 60:
        wash_signals += 0.5
    else:
        real_signals += 0.5

    if wash_signals >= 2:
        conclusion = '🟡 洗盘概率较大,建议观察到尾盘'
    elif real_signals >= 2:
        conclusion = '🔴 真跌特征明显,建议准备止损'
    else:
        conclusion = '🟤 信号不明确,建议手动判断'
    lines.append(f'  综合判断:{conclusion}')

    return '\n'.join(lines)


def run():
    now = datetime.now()
    lines = []

    # ── 标题 ──
    lines.append(f'📢 **盘中监控** — {now.strftime("%Y-%m-%d %H:%M")}')
    lines.append('')

    # ════════════════════════════════════════
    # 1️⃣ 大盘概况
    # ════════════════════════════════════════
    lines.append('**1️⃣ 大盘概况**')

    # 实时指数一行合并
    idx_config = [
        ('000001', '上证'),
        ('399001', '深证'),
        ('399006', '创业板'),
        ('000688', '科创50'),
    ]
    idx_parts = []
    for code, name in idx_config:
        try:
            idx = fetch_index(code)
            arrow = '🟢' if idx['change_pct'] >= 0 else '🔴'
            idx_parts.append(f'{arrow} {name} {idx["change_pct"]:+.2f}%')
        except Exception:
            idx_parts.append(f'⚠️ {name}')
    lines.append(f'  {" / ".join(idx_parts)}')

    # ── 实时成交额（新浪指数数据）──
    realtime_amt = None
    try:
        import urllib.request
        # 上证+深证实时成交额
        url = 'https://hq.sinajs.cn/list=s_sh000001,s_sz399001'
        req = urllib.request.Request(url, headers={'Referer': 'https://finance.sina.com.cn', 'User-Agent': 'Mozilla/5.0'})
        resp = urllib.request.urlopen(req, timeout=5)
        data = resp.read().decode('gbk')
        # 解析成交额：name,price,chg_amt,chg_pct,volume(手),amount(元)
        parts = data.split(';')
        total_amt = 0
        for p in parts:
            if ',' in p:
                fields = p.split('"')[1].split(',')
                if len(fields) >= 6:
                    amt_str = fields[5].strip()
                    if amt_str and amt_str.replace('.', '').isdigit():
                        # 新浪 amount 单位是万元，转换为亿元
                        total_amt += float(amt_str) / 1e4
    except Exception as e:
        logger.warning(f'获取站内实时成交额失败: {e}')

    # ── 盘面摘要（涨跌家数 + 成交额 + 资金流）──
    # 实时数据：优先 push2；失败则降级为 db 缓存
    rise, fall, flat, amt, main_flow, retail_flow = None, None, None, None, None, None
    try:
        from core.fetcher.push2_market import fetch_push2_market_data
        p2 = fetch_push2_market_data()
        if p2:
            rise, fall, flat = p2['rise_total'], p2['fall_total'], p2['flat_total']
            amt = p2['amount_total']
            main_flow = p2['main_flow']
            retail_flow = p2['retail_flow']
    except Exception as e:
        logger.warning(f'push2 实时数据获取失败: {e}')

    # 如果 push2 没有成交额，用新浪实时成交额
    if amt is None and realtime_amt:
        amt = realtime_amt

    # 如果 push2 没有涨跌家数，从 db 缓存补
    if rise is None:
        try:
            ms = _get_market_summary_cached()
            if ms and ms.get('up_count', 0) > 0:
                rise = ms.get('up_count') or 0
                fall = ms.get('down_count') or 0
                flat = ms.get('flat_count') or 0
                if amt is None and ms.get('total_amount'):
                    amt = ms['total_amount'] / 1e8
        except Exception as e:
            logger.warning(f'市场汇总缓存获取失败: {e}')

    # 输出盘面摘要行
    if rise is not None and fall is not None:
        flat_str = flat or 0
        if (rise or 0) >= (fall or 0):
            rf_line = f'🟢 涨{rise}家 🔴 跌{fall}家 ➖{flat_str}家'
        else:
            rf_line = f'🔴 跌{fall}家 🟢 涨{rise}家 ➖{flat_str}家'
        lines.append(f'  📊 {rf_line}')
    
    # 资金流行（有成交额就输出）
    if amt is not None:
        if main_flow is not None:
            main_icon = '🟢' if main_flow >= 0 else '🔴'
            main_str = f'+{main_flow:.0f}亿' if main_flow > 0 else (f'{main_flow:.0f}亿' if main_flow < 0 else '0亿')
        if retail_flow is not None:
            retail_icon = '🔴' if retail_flow > 0 else '🟢'
            retail_str = f'+{retail_flow:.0f}亿' if retail_flow > 0 else (f'{retail_flow:.0f}亿' if retail_flow < 0 else '0亿')
        if main_flow is not None and retail_flow is not None:
            lines.append(f'  💰 成交额 {amt:.0f}亿 ｜ {main_icon} 主力 {main_str} ｜ {retail_icon} 散户 {retail_str}')
        else:
            lines.append(f'  💰 成交额 {amt:.0f}亿')

    # 风险提示行（结合大盘涨跌 + 资金流）
    risks = []
    try:
        sh_idx = fetch_index('000001')
        sh_chg = sh_idx['change_pct']

        if sh_chg is not None:
            if sh_chg < -1.5:
                risks.append('⚠️ 大盘跌超1.5%，风险警示！建议只卖不买')
            elif sh_chg < -1:
                risks.append('⚠️ 上证跌超1%，注意风控')

        # 创业板风险判断
        try:
            cy = fetch_index('399006')
            if cy['change_pct'] < -1.5:
                risks.append('⚠️ 创业板跌超1.5%，题材股风险偏大')
        except Exception:
            pass

        # 资金流风险判断（push2 数据可用时）
        if push2:
            if push2['main_flow'] < -500:
                risks.append(f'⚠️ 主力流出{push2["main_flow"]:.0f}亿，注意系统性风险')
            if push2['rise_total'] > 0 and push2['fall_total'] > 0:
                ratio = push2['rise_total'] / push2['fall_total']
                if ratio < 0.5:
                    risks.append(f'⚠️ 涨跌比严重失衡（涨:跌 ≈ {ratio:.2f}），市场弱势')
    except Exception:
        pass
    if risks:
        lines.append(f'  {";".join(risks)}')

    lines.append('')

    # ════════════════════════════════════════
    # 2️⃣ 今日市场动态
    # ════════════════════════════════════════
    lines.append('')
    lines.append('**2️⃣ 今日市场动态**')
    news = fetch_news_compact(6)
    if news:
        for n in news:
            lines.append(f'  {n}')
    else:
        lines.append('  暂无盘中重要消息')
    lines.append('')

    # ════════════════════════════════════════
    # 3️⃣ 持仓监控
    # ════════════════════════════════════════
    lines.append('')
    lines.append('**3️⃣ 持仓监控**')

    total_cost = 0
    total_value = 0
    pos_rows = []

    from utils.dao import get_db
    _db = get_db()
    positions = _db.fetchall("SELECT * FROM portfolio_positions")

    for pos in positions:
        code = pos['code']
        name = pos['name']
        cost = float(pos['cost_price'])
        shares = int(pos['shares'])
        if not code or not shares:
            continue
        q = fetch_quote(code)
        if q is None or q['prev_close'] <= 0:
            lines.append(f'  ⚠️ {name}({code}) - 行情获取失败')
            continue
        cur = q['current']
        change_pct = (cur - q['prev_close']) / q['prev_close'] * 100
        profit_pct = (cur - cost) / cost * 100 if cost > 0 else 0
        market_value = cur * shares
        amp = (q['high'] - q['low']) / q['prev_close'] * 100
        amount_str = f"{q['amount']/1e8:.1f}亿" if q['amount'] >= 1e8 else f"{q['amount']/1e4:.0f}万"

        flag = '🟢' if change_pct >= 0 else '🔴'
        pos_rows.append({
            'name': name, 'code': code, 'flag': flag,
            'price': f'{cur:.2f}',
            'change': f'{change_pct:+.2f}%',
            'pnl': f'{profit_pct:+.2f}%',
            'amp': f'{amp:.1f}%',
            'vol': amount_str,
        })
        total_cost += cost * shares
        total_value += market_value

    if pos_rows:
        for r in pos_rows:
            chg_str = r['change']
            lines.append(f"  {r['flag']} {r['name']}({r['code']})  现价{r['price']}  {chg_str}  盈亏{r['pnl']}  振幅{r['amp']}  量{r['vol']}")

        if total_cost > 0:
            total_pnl = total_value - total_cost
            total_pnl_pct = total_pnl / total_cost * 100
            lines.append(f'  💰 总资产: {total_value:,.0f} | 成本: {total_cost:,.0f} | 盈亏: {total_pnl:+,.0f} (+{total_pnl_pct:.2f}%)' if total_pnl >= 0 else f'  💰 总资产: {total_value:,.0f} | 成本: {total_cost:,.0f} | 盈亏: {total_pnl:+,.0f} ({total_pnl_pct:.2f}%)')
    else:
        lines.append('  无持仓记录')
    lines.append('')

    # ════════════════════════════════════════
    # 4️⃣ 操作提醒
    # ════════════════════════════════════════
    lines.append('')
    lines.append('**4️⃣ 操作提醒**')

    stock_tips = []
    _db2 = get_db()
    positions_for_tips = _db2.fetchall("SELECT * FROM portfolio_positions")
    for pos in positions_for_tips:
        code = pos['code']
        name = pos['name']
        cost = float(pos['cost_price'])
        shares = int(pos['shares'])
        q = fetch_quote(code)
        if q and q['prev_close'] > 0:
            cur = q['current']
            profit_pct = (cur - cost) / cost * 100
            day_pct = (cur - q['prev_close']) / q['prev_close'] * 100

            if day_pct >= 9.8:
                stock_tips.append(f'🚀 {name}({code}) 涨停 {cur:.2f}(+{day_pct:.2f}%)')
            elif profit_pct > 7:
                stock_tips.append(f'💰 {name}({code}) 盈利+{profit_pct:.2f}%,可以考虑止盈')
            elif profit_pct < -3:
                # 亏损超-3% → 三问判断
                judge_text = _judge_stock(name, code, profit_pct, cur, q['prev_close'], q['volume_hand'])
                stock_tips.append(judge_text)

    if stock_tips:
        lines.append('  📌 个股提醒:')
        for t in stock_tips:
            for line in t.split('\n'):
                lines.append(f'    {line}')

    # 时间提醒
    h, m = now.hour, now.minute
    if h < 11 or (h == 11 and m < 30):
        lines.append('  ⏰ 午盘收盘 11:30')
    elif 11 <= h < 13:
        lines.append('  ⏰ 午间休市 (11:30-13:00)')
    elif 13 <= h < 15:
        lines.append(f'  ⏰ 距收盘还有 {14 - h}小时{60 - m:02d}分')
    lines.append('')

    return '\n'.join(lines)


if __name__ == '__main__':
    now = datetime.now()
    current_min = now.hour * 60 + now.minute
    if current_min < 9 * 60 + 25:
        print(f'⏰ 尚未开盘(09:25开始),跳过盘中监控')
        sys.exit(0)
    if current_min >= 15 * 60:
        print(f'⏰ 已收盘,跳过盘中监控')
        sys.exit(0)
    print(run())
