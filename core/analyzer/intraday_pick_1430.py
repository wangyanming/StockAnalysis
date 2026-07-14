#!/usr/bin/env python3
"""
盘中14:30选股模块
=================
职责：
  1. 工作日14:30采集全量个股行情（腾讯日K）、涨停数据、板块数据、指数数据
  2. 过滤链筛选候选股
  3. 综合评分并输出TOP5候选股
  4. 数据写入 intraday_ 前缀的独立表，与收盘选股完全解耦

依赖：
  - utils.dao.get_db (MySQL)
  - utils.logger.setup_logger
  - akshare（涨停数据、同花顺板块）
  - urllib（腾讯日K、新浪指数）

独立性：
  - 不引用 core/analyzer/ 下的任何文件
  - 5张表前缀统一为 intraday_
"""

import os
import sys
import re
import json
import time
import urllib.request
import traceback
from datetime import datetime, date
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── 项目根目录 ──
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from utils.dao import get_db
from utils.logger import setup_logger, TimingHelper

# ── 日志 ──
logger = setup_logger("intraday_pick")
today_str = date.today().strftime("%Y-%m-%d")
today_dashed = today_str
today_compact = date.today().strftime("%Y%m%d")

# ── 常量 ──
BATCH_SIZE = 200       # 腾讯接口每批拉取数量
CONCURRENCY = 20       # 并发数
RETRY_TIMES = 2        # 每批重试次数
TIMEOUT_SEC = 15       # HTTP超时

# ─────────────────────────────────────────────
# 1. 数据采集
# ─────────────────────────────────────────────

def get_tx_code(code: str) -> str:
    """获取腾讯接口代码前缀"""
    if code.startswith(('60', '688')):
        return 'sh' + code
    return 'sz' + code


def fetch_stock_qt(code: str) -> dict:
    """
    拉取单只股票腾讯实时行情（qt.gtimg.cn）。
    和复盘日K采集 `fetch_all_stocks_daily.py` 的 `_fetch_one` 使用相同接口、相同字段索引。

    接口: qt.gtimg.cn/q=
    [3] current_price     现价
    [4] pre_close          昨收
    [5] open               今开
    [6] volume             成交量（手，688/4/8开头为股）
    [32] change_pct        涨跌幅(%)
    [33] high              最高价
    [34] low               最低价
    [37] amount            成交额（万元）
    [38] turnover_rate     换手率(%)
    [39] pe_ratio          市盈率
    [45] total_market_cap  总市值(亿)
    [46] pb_ratio          市净率

    返回 dict 或 None
    """
    prefix = 'sz' if not code.startswith('6') and not code.startswith('9') else 'sh'
    if code.startswith('4') or code.startswith('8'):
        prefix = 'bj'
    tx_code = f'{prefix}{code}'
    url = f'https://qt.gtimg.cn/q={tx_code}'

    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        resp = urllib.request.urlopen(req, timeout=TIMEOUT_SEC)
        text = resp.read().decode('gbk')
        parts = text.split('~')
        if len(parts) < 49:
            logger.warning(f"qt.gtimg.cn字段不足 {tx_code}: {len(parts)}")
            return None

        name = parts[1]
        cur_price = float(parts[3]) if parts[3] else None
        pre_close = float(parts[4]) if parts[4] else None
        if cur_price is None:
            return None

        open_p = float(parts[5]) if parts[5] else cur_price
        high_p = float(parts[33]) if parts[33] else cur_price
        low_p = float(parts[34]) if parts[34] else cur_price
        change_pct = float(parts[32]) if parts[32] else None

        # 成交量：主板/创业板为手*100，科创板/北交所为股
        volume_raw = int(float(parts[6])) if parts[6] else 0
        if not (code.startswith('688') or code.startswith('4') or code.startswith('8')):
            volume = volume_raw * 100  # 手→股
        else:
            volume = volume_raw  # 已经是股

        # 成交额：万元→元
        amount_yuan = float(parts[37]) * 10000 if parts[37] else 0

        # 换手率/PE(动态)/PB
        turnover_rate = float(parts[38]) if parts[38] else None
        pe_ratio = float(parts[52]) if parts[52] else None  # [52]=动态市盈率, [39]=静态市盈率
        pb_ratio = float(parts[46]) if parts[46] else None

        # 市值：亿→元
        total_market_cap = float(parts[45]) * 1e8 if parts[45] else None

        # 前收盘价存在时才计算涨跌幅
        if change_pct is None and pre_close and pre_close > 0:
            change_pct = round((cur_price - pre_close) / pre_close * 100, 2)

        return {
            'code': code,
            'name': name,
            'open': open_p,
            'close': cur_price,
            'high': high_p,
            'low': low_p,
            'volume': volume,
            'amount': amount_yuan,
            'change_pct': change_pct,
            'total_market_cap': total_market_cap,
            'turnover_rate': turnover_rate,
            'pe_ratio': pe_ratio,
            'pb_ratio': pb_ratio,
        }
    except Exception as e:
        logger.warning(f"qt.gtimg.cn请求失败 {tx_code}: {e}")
        return None


def fetch_batch(batch_codes: list) -> list:
    """拉取一批股票行情"""
    results = []
    for code in batch_codes:
        rec = fetch_stock_qt(code)
        if rec:
            results.append(rec)
    return results


def collect_stock_data() -> list:
    """
    全量采集个股行情（腾讯日K，20路并发，200只/批）。
    返回 list[dict]，每条记录包含完整行情数据。
    """
    db = get_db()
    codes_rows = db.fetchall(
        "SELECT DISTINCT code FROM stock_daily WHERE trade_date = (SELECT MAX(trade_date) FROM stock_daily)"
    )
    all_codes = [r['code'] for r in codes_rows]
    logger.info(f"采集阶段1: 全量个股行情 (共{len(all_codes)}只，{CONCURRENCY}路并发)")

    # 分批
    batches = [all_codes[i:i + BATCH_SIZE] for i in range(0, len(all_codes), BATCH_SIZE)]
    all_records = []
    failed_batches = 0

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as executor:
        futures = {}
        for batch in batches:
            future = executor.submit(fetch_batch, batch)
            futures[future] = batch

        for future in as_completed(futures):
            batch = futures[future]
            try:
                records = future.result()
                all_records.extend(records)
            except Exception as e:
                failed_batches += 1
                logger.warning(f"批处理失败 [{batch[0]}..{batch[-1]}]: {e}")
                # 重试一次
                for retry in range(RETRY_TIMES):
                    try:
                        records = fetch_batch(batch)
                        all_records.extend(records)
                        failed_batches -= 1
                        logger.info(f"批处理重试{RETRY_TIMES+1}成功 [{batch[0]}..{batch[-1]}]")
                        break
                    except Exception as e2:
                        logger.warning(f"批处理重试{retry+1}失败: {e2}")
                        time.sleep(2)

    success_count = len(all_records)
    logger.info(f"采集完成: {success_count}/{len(all_codes)} ✅ ({len(all_codes)-success_count}只失败)")
    return all_records


def save_intraday_stock(records: list):
    """批量写入 intraday_stock"""
    db = get_db()
    sql = """INSERT IGNORE INTO intraday_stock
        (trade_date, code, name, open, high, low, close, volume, amount,
         change_pct, total_market_cap, turnover_rate, pe_ratio, pb_ratio)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
    batch = []
    for r in records:
        batch.append((
            today_compact, r['code'], r['name'],
            r['open'], r['high'], r['low'], r['close'],
            r['volume'], r['amount'],
            r['change_pct'], r['total_market_cap'],
            r['turnover_rate'], r['pe_ratio'], r['pb_ratio'],
        ))
        if len(batch) >= 500:
            db.executemany(sql, batch)
            batch.clear()
    if batch:
        db.executemany(sql, batch)
    logger.info(f"入库 intraday_stock: {len(records)}条")


def compute_position_60d(records: list):
    """
    计算60日价格分位并回填 intraday_stock。
    position_60d = (close - low_60d) / (high_60d - low_60d) * 100
    从 stock_daily 取历史收盘价计算。
    """
    db = get_db()
    codes = [r['code'] for r in records]
    if not codes:
        return

    # 批量查询：取每只股票最近60个交易日的收盘价
    placeholders = ','.join(['%s'] * len(codes))
    sql = f"""
        SELECT code, trade_date, close
        FROM stock_daily
        WHERE code IN ({placeholders})
          AND close IS NOT NULL
        ORDER BY code, trade_date DESC
    """
    rows = db.fetchall(sql, codes)

    # 分组：每只股票取最多60条
    from collections import defaultdict
    price_map = defaultdict(list)
    for r in rows:
        code = r['code']
        if len(price_map[code]) < 60:
            price_map[code].append(float(r['close']))

    update_sql = "UPDATE intraday_stock SET position_60d = %s WHERE trade_date = %s AND code = %s"
    updated = 0
    for r in records:
        code = r['code']
        prices = price_map.get(code, [])
        if len(prices) < 60:
            continue
        try:
            high_60d = max(prices)
            low_60d = min(prices)
            if high_60d == low_60d:
                position = 50.0
            else:
                position = round((r['close'] - low_60d) / (high_60d - low_60d) * 100, 2)
            db.execute(update_sql, (position, today_compact, code))
            r['position_60d'] = position
            updated += 1
        except (ValueError, TypeError):
            continue
    logger.info(f"计算 position_60d: {updated}条")


def collect_limit_up():
    """采集涨停数据 -> intraday_limit_up"""
    db = get_db()
    try:
        import akshare as ak
        df = ak.stock_zt_pool_em(date=today_compact)
        if df is None or df.empty:
            logger.warning("涨停数据为空，跳过")
            return
        sql = """INSERT IGNORE INTO intraday_limit_up
            (trade_date, code, name, price, board_times, industry)
            VALUES (%s, %s, %s, %s, %s, %s)"""
        batch = []
        for _, row in df.iterrows():
            batch.append((
                today_compact,
                row.get('代码', ''),
                row.get('名称', ''),
                float(row.get('最新价', 0)),
                int(row.get('连板数', 0)),
                row.get('所属行业', ''),
            ))
            if len(batch) >= 200:
                db.executemany(sql, batch)
                batch.clear()
        if batch:
            db.executemany(sql, batch)
        logger.info(f"采集涨停: {len(df)}只")
    except Exception as e:
        logger.warning(f"涨停数据采集失败: {e}")


def collect_sectors():
    """采集同花顺板块数据 -> intraday_sector"""
    db = get_db()
    try:
        import akshare as ak
        df = ak.stock_board_industry_summary_ths()
        if df is None or df.empty:
            logger.warning("板块数据为空，跳过")
            return
        # 按涨跌幅排序标记 sector_rank
        df = df.sort_values('涨跌幅', ascending=False).reset_index(drop=True)
        sql = """INSERT IGNORE INTO intraday_sector
            (trade_date, sector_name, change_pct, amount, net_inflow, rise_count, fall_count, sector_rank)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"""
        batch = []
        for idx, (_, row) in enumerate(df.iterrows()):
            # 总成交额单位可能是亿，统一转元
            amount_val = float(row.get('总成交额', 0)) * 1e8
            net_inflow = float(row.get('净流入', 0)) * 1e8
            batch.append((
                today_compact,
                row.get('板块', ''),
                float(row.get('涨跌幅', 0)),
                amount_val,
                net_inflow,
                int(row.get('上涨家数', 0)),
                int(row.get('下跌家数', 0)),
                idx + 1,  # sector_rank
            ))
            if len(batch) >= 100:
                db.executemany(sql, batch)
                batch.clear()
        if batch:
            db.executemany(sql, batch)
        logger.info(f"采集板块: {len(df)}个行业")
    except Exception as e:
        logger.warning(f"板块数据采集失败: {e}")


def collect_index():
    """采集新浪指数 -> intraday_index"""
    db = get_db()
    codes_map = {
        'sh000001': '上证指数',
        'sz399001': '深证成指',
        'sz399006': '创业板指',
        'sz399300': '沪深300',
        'sh000688': '科创50',
    }
    url = f'http://hq.sinajs.cn/list={",".join(codes_map.keys())}'
    try:
        req = urllib.request.Request(url, headers={'Referer': 'https://finance.sina.com.cn'})
        resp = urllib.request.urlopen(req, timeout=TIMEOUT_SEC)
        text = resp.read().decode('gbk')
    except Exception as e:
        logger.warning(f"新浪指数请求失败: {e}")
        return

    sql = """INSERT IGNORE INTO intraday_index
        (trade_date, index_code, name, current_price, change_pct, open, high, low, volume, amount)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
    batch = []
    for line in text.strip().split('\n'):
        line = line.strip()
        if not line or '=' not in line:
            continue
        try:
            key = line.split('=')[0].replace('var hq_str_', '').strip()
            if key not in codes_map:
                continue
            value_match = re.search(r'"([^"]*)"', line)
            if not value_match:
                continue
            parts = value_match.group(1).split(',')
            if len(parts) < 10:
                continue
            # 0=name, 1=open, 2=prev_close, 3=current, 4=high, 5=low, 8=volume(手), 9=amount(元)
            name = parts[0]
            open_p = float(parts[1])
            prev_close = float(parts[2])
            current = float(parts[3])
            high = float(parts[4])
            low = float(parts[5])
            volume_hand = int(float(parts[8])) if parts[8] else 0
            amt_yuan = float(parts[9]) if parts[9] else 0
            change_pct = round((current - prev_close) / prev_close * 100, 2) if prev_close > 0 else 0.0
            batch.append((
                today_compact, key, name, current, change_pct,
                open_p, high, low, volume_hand, int(amt_yuan),
            ))
        except (IndexError, ValueError, TypeError) as e:
            logger.warning(f"解析新浪指数行失败: {e}")
            continue
    if batch:
        db.executemany(sql, batch)
    logger.info(f"采集指数: {len(batch)}条")


# ─────────────────────────────────────────────
# 2. 过滤链与评分
# ─────────────────────────────────────────────

def filter_and_score(records: list) -> list:
    """
    过滤链筛选 + 综合评分。
    返回 list[dict]，按总分降序排列。
    """
    # ── 获取指数数据用于大盘安全判断 ──
    db = get_db()
    index_rows = db.fetchall(
        "SELECT index_code, current_price, change_pct FROM intraday_index WHERE trade_date = %s",
        (today_compact,),
    )
    index_map = {r['index_code']: r for r in index_rows}
    # 大盘涨跌幅
    sh_change = 0.0
    sz_change = 0.0
    cyb_change = 0.0
    if 'sh000001' in index_map:
        sh_change = float(index_map['sh000001'].get('change_pct', 0) or 0)
    if 'sz399001' in index_map:
        sz_change = float(index_map['sz399001'].get('change_pct', 0) or 0)
    if 'sz399006' in index_map:
        cyb_change = float(index_map['sz399006'].get('change_pct', 0) or 0)

    # ── 大盘安全检查 ──
    market_safe = True
    if sh_change <= -2.0:
        market_safe = False
        logger.warning(f"大盘风险: 上证{sh_change:.2f}% 深证{sz_change:.2f}% 创业板{cyb_change:.2f}%")

    # ── 加载换手率缓存 ──
    _load_tor_ratio_cache()

    candidates = []
    filter_stats = {
        'total': len(records),
        'st_filtered': 0,
        'kline_insufficient': 0,
        'limit_up': 0,
        'limit_down': 0,
        'change_pct_out': 0,
        'pe_out': 0,
        'position_out': 0,
        'market_risk': 0,
        'passed': 0,
    }

    for r in records:
        code = r['code']
        name = r['name']
        change_pct = r['change_pct']
        close_p = r['close']
        pe_ratio = r['pe_ratio']

        # 过滤1: 不含ST/退市
        if 'ST' in name or '退' in name or 'st' in name.lower():
            filter_stats['st_filtered'] += 1
            continue

        # 过滤1.5: 仅主板（去掉科创板/创业板/北交所）
        if code.startswith(('688', '300', '301', '4', '8')):
            filter_stats['st_filtered'] += 1
            continue

        # 过滤2: 已涨停跳过 (change_pct >= 9.5)
        if change_pct >= 9.5:
            filter_stats['limit_up'] += 1
            continue

        # 过滤3: 已跌停跳过 (change_pct <= -9.5)
        if change_pct <= -9.5:
            filter_stats['limit_down'] += 1
            continue

        # 过滤4: 3% <= change_pct <= 7% 核心条件
        if not (3.0 <= change_pct <= 7.0):
            filter_stats['change_pct_out'] += 1
            continue

        # 过滤5: 0 < pe_ratio < 200（动态PE）
        if not (0 < pe_ratio < 200):
            filter_stats['pe_out'] += 1
            continue

        # 过滤6: position_60d <= 70（None或空=新股默认100跳过）
        position_60d = r.get('position_60d', 100)
        if position_60d is None or position_60d > 70:
            filter_stats['position_out'] += 1
            continue

        # 过滤7: 大盘安全
        if not market_safe:
            filter_stats['market_risk'] += 1
            continue

        # ── 评分 ──
        score_intraday = _score_intraday(change_pct)
        score_sector = _score_sector(code, r.get('sector_name'), r.get('sector_rank'))
        score_volume = _score_volume(code, r.get('turnover_rate'))
        score_position = _score_position(position_60d)
        score_market = _score_market(sh_change, sz_change, cyb_change)

        total_score = score_intraday + score_sector + score_volume + score_position + score_market

        reason_parts = []
        if 4 <= change_pct < 5:
            reason_parts.append("涨幅适中")
        elif 3 <= change_pct < 4:
            reason_parts.append("温和启动")
        elif 5 <= change_pct <= 7:
            reason_parts.append("强势上攻")
        if score_volume >= 15:
            reason_parts.append("明显放量")
        elif score_volume >= 10:
            reason_parts.append("温和放量")
        if position_60d < 50:
            reason_parts.append("位置安全")
        else:
            reason_parts.append("趋势向上")

        candidates.append({
            'code': code,
            'name': name,
            'score': total_score,
            'score_intraday': score_intraday,
            'score_sector': score_sector,
            'score_value': score_volume,
            'score_position': score_position,
            'score_market': score_market,
            'current_price': close_p,
            'change_pct': change_pct,
            'pe_ratio': pe_ratio,
            'sector_name': r.get('sector_name'),
            'sector_rank': r.get('sector_rank'),
            'position_60d': position_60d,
            'reason': '+'.join(reason_parts),
        })

    filter_stats['passed'] = len(candidates)
    logger.info(
        f"过滤链: 全量{filter_stats['total']}→"
        f"非ST{filter_stats['total']-filter_stats['st_filtered']}→"
        f"非涨停{filter_stats['total']-filter_stats['st_filtered']-filter_stats['limit_up']}→"
        f"非跌停{filter_stats['total']-filter_stats['st_filtered']-filter_stats['limit_up']-filter_stats['limit_down']}→"
        f"涨幅3-7%:N/A→PE合理:N/A→位置≤70%:N/A→大盘安全:N/A→"
        f"通过:{filter_stats['passed']}"
    )

    # 按总分降序
    candidates.sort(key=lambda x: (x['score'], x['change_pct']), reverse=True)
    return candidates


def _score_intraday(change_pct: float) -> int:
    """日内强度评分 (30分)"""
    if 4.0 <= change_pct < 5.0:
        return 30
    elif 3.0 <= change_pct < 4.0:
        return 25
    elif 5.0 <= change_pct <= 7.0:
        return 20
    return 15


# ── 板块排名 -> 板块排名 → 板块热度缓存 ──
_sector_rank_cache = None


def _get_sector_rank_map():
    """获取同花顺板块排名映射：板块名->排名"""
    global _sector_rank_cache
    if _sector_rank_cache is not None:
        return _sector_rank_cache
    try:
        from utils.dao import get_db
        db = get_db()
        rows = db.fetchall(
            "SELECT sector_name, sector_rank FROM intraday_sector WHERE trade_date = %s ORDER BY sector_rank ASC",
            (today_compact,),
        )
        _sector_rank_cache = {r['sector_name']: int(r['sector_rank']) for r in rows}
        db.close()
    except Exception as e:
        logger.warning(f"获取板块排名失败: {e}")
        _sector_rank_cache = {}
    return _sector_rank_cache


def _score_sector(code: str, sector_name: str, sector_rank: int) -> int:
    """板块热度评分 (25分)"""
    # 先看records里是否有板块名（后续可以加的话）
    if sector_rank is not None and 1 <= sector_rank <= 3:
        return 25
    elif sector_rank is not None and 4 <= sector_rank <= 6:
        return 20
    elif sector_rank is not None and 7 <= sector_rank <= 10:
        return 15
    return 10  # 不在前列也给保底分


# ── 换手率放量倍数评分缓存（批量加载） ──
_tor_ratio_cache = {}
_tor_ratio_loaded = False


def _load_tor_ratio_cache():
    """批量加载今日所有有换手率股票的近20日均值"""
    global _tor_ratio_cache, _tor_ratio_loaded
    if _tor_ratio_loaded:
        return
    try:
        from utils.dao import get_db
        db = get_db()
        start_date = (datetime.strptime(today_compact, '%Y%m%d') - timedelta(days=40)).strftime('%Y%m%d')
        rows = db.fetchall(
            "SELECT code, AVG(turnover_rate) as avg_tor FROM stock_daily "
            "WHERE trade_date >= %s AND trade_date < %s AND turnover_rate > 0 "
            "GROUP BY code",
            (start_date, today_compact),
        )
        for r in rows:
            avg_tor = float(r['avg_tor']) if r['avg_tor'] else 0
            _tor_ratio_cache[r['code']] = avg_tor
        db.close()
        _tor_ratio_loaded = True
        logger.info(f"换手率缓存加载完成: {len(rows)}条")
    except Exception as e:
        logger.warning(f"换手率缓存加载失败: {e}")


def _get_tor_ratio(code: str, today_tor: float) -> float:
    """计算放量倍数 = 今日换手率 / 近20日均换手率"""
    avg_tor = _tor_ratio_cache.get(code, 0)
    return today_tor / avg_tor if avg_tor > 0 else 0


def _score_volume(code: str, today_tor: float) -> int:
    """换手率评分 (20分) - 基于放量倍数
    P99 ~ 2.0x 异常放量 → 20分
    P95 ~ 1.6x 明显放量 → 18分
    P90 ~ 1.4x 温和放量 → 15分
    P50 ~ 0.85x 正常 → 10分
    < 0.85x 缩量 → 5分
    """
    if not today_tor or today_tor <= 0:
        return 5
    ratio = _get_tor_ratio(code, today_tor)
    if ratio >= 2.0:
        return 20
    elif ratio >= 1.6:
        return 18
    elif ratio >= 1.4:
        return 15
    elif ratio >= 0.85:
        return 10
    return 5


def _score_position(position_60d: float) -> int:
    """位置评分 (15分)"""
    if 30 <= position_60d <= 50:
        return 15
    elif 50 < position_60d <= 60:
        return 12
    elif 15 <= position_60d < 30:
        return 10
    elif 60 < position_60d <= 70:
        return 8
    elif position_60d < 15:
        return 5
    return 0


def _score_market(sh_pct: float, sz_pct: float, cyb_pct: float) -> int:
    """大盘环境评分 (10分)"""
    # 取上证作为主要参考
    avg_pct = sh_pct
    if -1.0 <= avg_pct <= 0.5:
        return 10
    elif (-1.5 <= avg_pct < -1.0) or (0.5 < avg_pct <= 1.5):
        return 7
    else:
        return 5


def save_picks(candidates: list):
    """写入 intraday_picks"""
    db = get_db()
    sql = """INSERT IGNORE INTO intraday_picks
        (trade_date, code, name, score, score_intraday, score_sector, score_value,
         score_position, score_market, current_price, change_pct, pe_ratio,
         sector_name, sector_rank, position_60d, reason)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
    batch = []
    for c in candidates:
        batch.append((
            today_compact, c['code'], c['name'],
            c['score'], c['score_intraday'], c['score_sector'], c['score_value'],
            c['score_position'], c['score_market'], c['current_price'], c['change_pct'],
            c['pe_ratio'], c['sector_name'], c['sector_rank'], c['position_60d'],
            c['reason'],
        ))
    if batch:
        db.executemany(sql, batch)
    logger.info(f"入库 intraday_picks: {len(batch)}条")


# ─────────────────────────────────────────────
# 3. 输出
# ─────────────────────────────────────────────

def get_sector_top5() -> list:
    """获取板块TOP5"""
    db = get_db()
    rows = db.fetchall(
        "SELECT sector_name, change_pct FROM intraday_sector "
        "WHERE trade_date = %s ORDER BY sector_rank ASC LIMIT 5",
        (today_compact,),
    )
    return [(r['sector_name'], float(r.get('change_pct', 0) or 0)) for r in rows]


def format_output(candidates: list, elapsed_collect: float, elapsed_filter: float, market_safe: bool):
    """
    格式化输出结果。
    """
    lines = []
    lines.append(f"📊 盘中14:30 选股结果（{today_str}）")
    lines.append("")

    if not market_safe:
        # 指数信息
        db = get_db()
        index_rows = db.fetchall(
            "SELECT name, change_pct FROM intraday_index WHERE trade_date = %s",
            (today_compact,),
        )
        index_strs = []
        for r in index_rows:
            pct = float(r.get('change_pct', 0) or 0)
            emoji = "🔴" if pct >= 0 else "🟢"
            index_strs.append(f"{r['name']}{emoji}{pct:+.2f}%")
        lines.append(f"⚠️ 今日大盘风险")
        for s in index_strs:
            lines.append(f"  {s}")
        lines.append("跳过选股")
    elif not candidates:
        lines.append("💡 今日无符合条件的候选股")
    else:
        # 热点板块 TOP5
        sector_top5 = get_sector_top5()
        if sector_top5:
            sector_strs = [f"{name}({pct:+.1f}%)" for name, pct in sector_top5]
            lines.append(f"🔥 热点板块 TOP5：{'、'.join(sector_strs)}")
        lines.append("")

        # 大盘评估
        db = get_db()
        sh_row = db.fetchone(
            "SELECT change_pct FROM intraday_index WHERE trade_date = %s AND index_code = 'sh000001'",
            (today_compact,),
        )
        sh_pct = float(sh_row['change_pct']) if sh_row else 0
        market_label = "安全" if abs(sh_pct) < 1.5 else "温和" if abs(sh_pct) < 2 else "风险"
        lines.append("─" * 30)
        lines.append(f"总候选：{len(candidates)}只  |  大盘评估：{market_label}（上证 {sh_pct:+.2f}%）")
        lines.append("")
        lines.append("─── 候选股 TOP5 ───")
        lines.append("")

        top5 = candidates[:5]
        for i, c in enumerate(top5, 1):
            sector_str = f"\n  📌 所属板块：{c['sector_name']}（全市场 #{c['sector_rank']}）" if c.get('sector_name') else ""
            lines.append(f"{i}️⃣ {c['code']} {c['name']}")
            lines.append(f"  💹 现价 {c['current_price']:.2f} | 涨幅 {c['change_pct']:+.2f}% | PE {c['pe_ratio']:.2f}")
            if sector_str:
                lines.append(sector_str)
            lines.append(f"  📊 位置：60日分位 {c['position_60d']:.0f}%")
            lines.append(f"  🔍 选入理由：{c['reason']}")
            lines.append("")

        lines.append("⚙️ 操作提示：")
        lines.append("• 尾盘建仓半仓，次日T+1卖出")
        lines.append("• 止损 -5%")
        lines.append("• 单票仓位 ≤ 40%")

    lines.append("")
    lines.append(f"【耗时】采集 {elapsed_collect:.0f}秒 | 选股 {elapsed_filter:.0f}秒")

    return "\n".join(lines)


# ─────────────────────────────────────────────
# 4. 主流程
# ─────────────────────────────────────────────

def main():
    logger.info(f"===== 盘中14:30选股开始: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} =====")
    timing_total = TimingHelper("总耗时", logger)

    # ── 阶段1: 采集全量个股行情 ──
    t1 = TimingHelper("采集个股行情", logger)
    records = collect_stock_data()
    if not records:
        logger.error("采集个股行情失败，终止选股")
        print(format_output([], 0, 0, True))
        return
    t1.done()

    # 写入 intraday_stock
    t_save1 = TimingHelper("入库个股行情", logger)
    save_intraday_stock(records)
    t_save1.done()

    # 计算 position_60d
    t_pos = TimingHelper("计算60日分位", logger)
    compute_position_60d(records)
    t_pos.done()

    t_collect_end = time.time()

    # ── 阶段2: 采集其他数据 (并发) ──
    t2 = TimingHelper("采集其他数据", logger)
    collect_limit_up()
    collect_sectors()
    collect_index()
    t2.done()

    # ── 阶段3: 过滤链 + 评分 ──
    t3 = TimingHelper("过滤评分", logger)
    # 重新从数据库读取position_60d回填到records
    db = get_db()
    pos_rows = db.fetchall(
        "SELECT code, position_60d FROM intraday_stock WHERE trade_date = %s AND position_60d IS NOT NULL",
        (today_compact,),
    )
    pos_map = {r['code']: r['position_60d'] for r in pos_rows}
    for r in records:
        if r['code'] in pos_map:
            r['position_60d'] = pos_map[r['code']]

    candidates = filter_and_score(records)
    t3.done()
    t_filter_end = time.time()

    elapsed_collect = t_collect_end - timing_total.start
    elapsed_filter = t_filter_end - t_collect_end

    # ── 入库候选股 ──
    if candidates:
        save_picks(candidates)

    # ── 输出 ──
    sh_row = db.fetchone(
        "SELECT change_pct FROM intraday_index WHERE trade_date = %s AND index_code = 'sh000001'",
        (today_compact,),
    )
    market_safe = True
    if sh_row and float(sh_row.get('change_pct', 0) or 0) <= -2.0:
        market_safe = False

    output = format_output(candidates, elapsed_collect, elapsed_filter, market_safe)
    print(output)

    timing_total.done()
    logger.info(f"===== 选股完成 总耗时:{time.time()-timing_total.start:.0f}秒 =====\n")


if __name__ == '__main__':
    main()
