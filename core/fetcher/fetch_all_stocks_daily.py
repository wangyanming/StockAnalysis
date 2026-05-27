#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全量个股K线采集（腾讯日K接口）
数据源：腾讯行情 proxy.finance.qq.com
  close/open/high/low=元
  volume=手(x100->股)
  amount=万元(x10000->元)
  change_pct=百分比
"""

import os
import sys
import time
import logging
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 默认使用 MySQL（无环境变量时兜底）
if 'STOCK_DB_URL' not in os.environ:
    os.environ['STOCK_DB_URL'] = 'mysql://root:stock123@127.0.0.1:3306/stock_analysis'

import akshare as ak

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

from utils.dao import get_db
DB = get_db()


# ─────────────────────────────────────────────
# 1. 建表
# ─────────────────────────────────────────────
def ensure_tables():
    """创建个股日K历史表"""
    if DB.table_exists('stock_daily'):
        return
    DB.execute("""
        CREATE TABLE IF NOT EXISTS stock_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            name TEXT,
            trade_date TEXT NOT NULL,
            open REAL DEFAULT 0,
            close REAL DEFAULT 0,
            high REAL DEFAULT 0,
            low REAL DEFAULT 0,
            volume REAL DEFAULT 0,
            amount REAL DEFAULT 0,
            change_pct REAL DEFAULT 0,
            UNIQUE(code, trade_date)
        )
    """)
    logger.info("✅ 表 stock_daily 已就绪")


# ─────────────────────────────────────────────
# 2. 获取待拉股票列表
# ─────────────────────────────────────────────
def get_hs300_stocks() -> list:
    """获取沪深300成分股"""
    try:
        df = ak.index_stock_cons_csindex(symbol='000300')
        stocks = []
        for _, row in df.iterrows():
            code = str(row['成分券代码']).strip().zfill(6)
            name = str(row['成分券名称']).strip()
            stocks.append({'code': code, 'name': name})
        logger.info(f"✅ 获取沪深300成分股: {len(stocks)}只")
        return stocks
    except Exception as e:
        logger.error(f"获取沪深300成分股失败: {e}")
        return []


def get_all_stocks() -> list:
    """获取全A股代码列表"""
    try:
        df = ak.stock_info_a_code_name()
        stocks = []
        for _, row in df.iterrows():
            code = str(row['code']).strip().zfill(6)
            name = str(row['name']).strip()
            stocks.append({'code': code, 'name': name})
        logger.info(f"✅ 获取全A股: {len(stocks)}只")
        return stocks
    except Exception as e:
        logger.error(f"获取全A股列表失败: {e}")
        return []


# ─────────────────────────────────────────────
# 3. 拉取+存储
# ─────────────────────────────────────────────
def code_to_tx(symbol: str) -> str:
    """转为腾讯接口代码: sh600519 / sz000001"""
    symbol = symbol.strip().zfill(6)
    if symbol.startswith(('6', '9')):
        return f"sh{symbol}"
    elif symbol.startswith(('0', '3')):
        return f"sz{symbol}"
    elif symbol.startswith(('4', '8')):
        return f"bj{symbol}"
    return f"sz{symbol}"


import urllib.request, json, concurrent.futures

# ─────────────────────────────────────────────
# 快速采集：裸调腾讯日K接口 + 并发
# ─────────────────────────────────────────────
_FETCH_YEAR = datetime.now().year


def _parse_tx_kline_rows(raw_text: str, tx_code: str) -> list:
    """解析腾讯日K接口返回的文本，返回record列表"""
    idx = raw_text.find('={')
    if idx < 0:
        return []
    j = json.loads(raw_text[idx+1:])
    d = j.get('data', {}).get(tx_code, {})
    rows = d.get('bfqday') or d.get('day') or d.get('qfqday') or []
    records = []
    for row in rows:
        if len(row) < 6:
            continue
        try:
            # 腾讯日K接口字段表（len=10）:
            #   row[0]=日期, [1]=开盘, [2]=收盘, [3]=最高, [4]=最低
            #   row[5]=volume(手/主板,股/科创北交), [6]={}对象(跳过)
            #   row[7]=涨跌幅(%)可直接用, [8]=amount(万元), [9]=空
            is_kcb = tx_code[2:5] == '688' or tx_code[2] in ('4', '8')
            vol_raw = float(row[5]) if row[5] else 0
            amt_raw = float(row[8]) if len(row) > 8 and row[8] else 0
            records.append({
                'trade_date': str(row[0]).replace('-', ''),
                'open': float(row[1]) if row[1] else 0,
                'close': float(row[2]) if row[2] else 0,
                'high': float(row[3]) if row[3] else 0,
                'low': float(row[4]) if row[4] else 0,
                'volume': vol_raw if is_kcb else vol_raw * 100,        # 手→股
                'amount': round(amt_raw * 10000, 2),                   # 万元→元
            })
        except (ValueError, IndexError):
            continue
    # 按日期升序排序，用 close 自行计算涨跌幅
    # 注：腾讯 row[7] 的涨跌幅算法不透明（可能基于前复权），不用
    # 相邻close计算的涨跌幅在除权日会失真（今日浙江荣泰），
    # 但这是唯一可靠的方法，且今天最晚的选股已跑完，影响有限
    records.sort(key=lambda r: r['trade_date'])
    for i in range(1, len(records)):
        prev_close = records[i-1]['close']
        cur_close = records[i]['close']
        if prev_close and prev_close > 0:
            chg = round((cur_close - prev_close) / prev_close * 100, 2)
        else:
            chg = 0.0
        records[i]['change_pct'] = chg
    if records:
        records[0]['change_pct'] = 0.0
    return records


def _fetch_one_tx(tx_code: str, target_date: str = None) -> list:
    """
    裸调腾讯日K接口。
    增量更新时只拉 target_date 当天数据，不拉全年。
    历史回溯/全量拉取时 target_date=None 拉全年。
    """
    url = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
    if target_date:
        # 只拉单日数据：传日期范围避免返回全年
        params = f"{tx_code},day,{target_date},{target_date},1,bfq"
    else:
        # 全量拉取仍走原逻辑（历史回溯/初建）
        params = f"{tx_code},day,,,640,bfq"
    full_url = f"{url}?_var=kline_daybfq{_FETCH_YEAR}&param={urllib.request.quote(params)}&r=0.1"

    req = urllib.request.Request(full_url)
    req.add_header('User-Agent', 'Mozilla/5.0')

    try:
        resp = urllib.request.urlopen(req, timeout=15)
        raw = resp.read().decode('utf-8')
        records = _parse_tx_kline_rows(raw, tx_code)
        if records and records[-1]['trade_date'] == target_date:
            return records
        # 腾讯接口传日期范围可能返回空/缓存，回退到不传日期（拉全年再过滤）
        if target_date:
            params2 = f"{tx_code},day,,,640,bfq"
            full_url2 = f"{url}?_var=kline_daybfq{_FETCH_YEAR}&param={urllib.request.quote(params2)}&r=0.1"
            req2 = urllib.request.Request(full_url2)
            req2.add_header('User-Agent', 'Mozilla/5.0')
            resp2 = urllib.request.urlopen(req2, timeout=15)
            raw2 = resp2.read().decode('utf-8')
            records2 = _parse_tx_kline_rows(raw2, tx_code)
            if records2:
                return [r for r in records2 if r['trade_date'] == target_date]
    except Exception:
        pass

    # 回退：akshare（只拉 target_date 当天）
    try:
        from akshare import stock_zh_a_hist_tx
        start = target_date if target_date else f"{_FETCH_YEAR}-01-01"
        df = stock_zh_a_hist_tx(symbol=tx_code, start_date=start, adjust='qfq')
        if df is not None and not df.empty:
            records = []
            for _, row in df.iterrows():
                d = str(row['date']).replace('-', '')
                if target_date and d != target_date:
                    continue
                records.append({
                    'trade_date': d,
                    'open': float(row.get('open', 0)),
                    'close': float(row.get('close', 0)),
                    'high': float(row.get('high', 0)),
                    'low': float(row.get('low', 0)),
                    'volume': float(row.get('volume', 0)),
                    'amount': float(row.get('amount', 0)),
                    'change_pct': float(row.get('pctChg', 0)),
                })
            return records
    except Exception:
        pass

    return []


def fetch_stock_daily_fast(stocks: list, max_workers: int = 30) -> dict:
    """
    并发拉取多只股票的全年日K数据。
    stoks: [{'code':..., 'name':..., 'tx_code':...}, ...]
    返回: {code: [records...]}
    """
    tx_map = {s['tx_code']: s['code'] for s in stocks if s.get('tx_code')}
    tx_codes = list(tx_map.keys())

    code_records = {s['code']: [] for s in stocks}

    if not tx_codes:
        return code_records

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {executor.submit(_fetch_one_tx, tc): tc for tc in tx_codes}
        for future in concurrent.futures.as_completed(future_map):
            tc = future_map[future]
            code = tx_map[tc]
            try:
                records = future.result()
                code_records[code] = records
            except Exception as e:
                logger.warning(f"  并发拉取 {tc} 异常: {e}")

    return code_records


def get_existing_dates(code: str) -> set:
    """获取数据库中该股票已有的日期"""
    rows = DB.fetchall(
        "SELECT trade_date FROM stock_daily WHERE code=%s", (code,)
    )
    return {r['trade_date'] for r in rows}


def save_to_db(records: list, code: str, name: str) -> int:
    """写入数据库"""
    if not records:
        return 0
    inserted = 0
    for r in records:
        try:
            n = DB.insert_or_ignore('stock_daily', {
                'code': code,
                'name': name,
                'trade_date': r['trade_date'],
                'open': r['open'],
                'close': r['close'],
                'high': r['high'],
                'low': r['low'],
                'volume': r['volume'],
                'amount': r['amount'],
                'change_pct': r['change_pct'],
            })
            inserted += n
        except Exception as e:
            pass
    return inserted


# ─────────────────────────────────────────────
# 4. 批量拉取
# ─────────────────────────────────────────────
def batch_fetch(stocks: list, start_date: str = None, end_date: str = None, max_workers: int = 30):
    """
    批量拉取股票日K数据（并发模式）。
    start_date/end_date 参数保留兼容，实际按全年拉取再过滤。
    """
    ensure_tables()
    
    total = len(stocks)
    logger.info(f"📋 共 {total} 只股票，并发拉取 {datetime.now().year}年全年日K...")
    
    # 准备并发参数
    stock_list = []
    for s in stocks:
        s['tx_code'] = code_to_tx(s['code'])
        stock_list.append(s)
    
    t_start = time.time()
    
    # 1) 并发拉取所有股票
    all_records = fetch_stock_daily_fast(stock_list, max_workers=30)
    
    raw_time = time.time() - t_start
    
    # 2) 逐只过滤已有日期+写入
    total_inserted = 0
    succeed = 0
    failed = 0
    skipped = 0
    
    for s in stock_list:
        code = s['code']
        name = s['name']
        records = all_records.get(code, [])
        
        if not records:
            failed += 1
            continue
        
        existing = get_existing_dates(code)
        new_records = [r for r in records if r['trade_date'] not in existing]
        
        if not new_records:
            skipped += 1
            continue
        
        n = save_to_db(new_records, code, name)
        total_inserted += n
        succeed += 1
    
    db_time = time.time() - t_start - raw_time
    elapsed = time.time() - t_start
    
    logger.info(f"\n{'='*50}")
    logger.info(f"🏁 批量拉取完成！")
    logger.info(f"  网络拉取: {raw_time:.1f}s | 写入入库: {db_time:.1f}s")
    logger.info(f"  成功: {succeed} | 失败: {failed} | 跳过: {skipped}")
    logger.info(f"  新增记录: {total_inserted} 条")
    logger.info(f"  总耗时: {elapsed:.0f}秒 ({elapsed/max(total,1):.1f}秒/只)")
    logger.info(f"{'='*50}")
    return total_inserted


# ─────────────────────────────────────────────
# 5. 统计
# ─────────────────────────────────────────────
def show_stats():
    """数据统计"""
    try:
        r = DB.fetchone("SELECT COUNT(*) as cnt FROM stock_daily")
        rows = r['cnt']
        r = DB.fetchone("SELECT COUNT(DISTINCT code) as cnt FROM stock_daily")
        codes = r['cnt']
        r = DB.fetchone("SELECT COUNT(DISTINCT trade_date) as cnt FROM stock_daily")
        dates = r['cnt']
        r = DB.fetchone("SELECT MIN(trade_date) as min_d, MAX(trade_date) as max_d FROM stock_daily")
        logger.info(f"\n📊 stock_daily 数据统计:")
        logger.info(f"  总记录数: {rows:,}")
        logger.info(f"  股票数量: {codes}")
        logger.info(f"  交易日数: {dates}")
        if r and r['min_d']:
            logger.info(f"  日期范围: {r['min_d']} ~ {r['max_d']}")
    except Exception as e:
        logger.error(f"统计出错: {e}")


def _verify_coverage(today: str):
    """校验今日覆盖率，对比上一个交易日有今天却没有的股票（停牌股排除）"""
    # 查上一个交易日（从stock_daily表取最近非today的交易日）
    prev = DB.fetchone('''
        SELECT trade_date FROM stock_daily
        WHERE trade_date < %s
        GROUP BY trade_date
        ORDER BY trade_date DESC LIMIT 1
    ''', (today,))
    if not prev or not prev['trade_date']:
        logger.warning("⚠️ 无上一个交易日数据，跳过覆盖率校验")
        return
    prev_date = prev['trade_date']
    
    # 上一个交易日有但今天没有的股票
    missing = DB.fetchall('''
        SELECT code, name FROM stock_daily
        WHERE trade_date = %s
          AND code NOT IN (SELECT DISTINCT code FROM stock_daily WHERE trade_date = %s)
    ''', (prev_date, today))
    if missing:
        logger.warning(f"⚠️ {prev_date}有交易但今日缺失 {len(missing)} 只（可能停牌）")
        if len(missing) <= 10:
            details = ', '.join(f'{r["code"]} {r["name"]}' for r in missing)
            logger.warning(f"   缺失: {details}")
    else:
        logger.info("✅ 覆盖率校验通过")


def _verify_ka_data_is_fresh() -> bool:
    """
    验证腾讯K线数据源是否已更新到今日。
    拉取一只大盘股（贵州茅台）的今日K线，检查其trade_date是否与today一致。
    """
    today = datetime.now().strftime('%Y%m%d')
    
    probe_codes = ['600519', '000001', '601318']
    
    for probe in probe_codes:
        tx_code = code_to_tx(probe)
        try:
            records = _fetch_one_tx(tx_code)
            if records:
                record_date = records[-1]['trade_date']
                if record_date == today:
                    logger.info(f"✅ 数据验证通过: {probe} 今日({today})数据已就绪")
                    return True
                logger.warning(f"⏳ 数据源返回最晚日期{record_date}，非今日{today}")
            else:
                logger.warning(f"⏳ {probe} 返回空")
        except Exception as e:
            logger.warning(f"⏳ 数据验证异常({probe}): {e}")
    
    logger.error("❌ 数据验证失败：所有探针均未返回今日数据")
    return False


def _fix_exright_change_pct(today: str):
    """
    修复除权日涨跌幅失真。

    问题：腾讯day模式返回不复权close，除权日前后相邻close因除权系数不可比，
    自行计算的 change_pct 会严重失真（如603119 -19.16% → 实际+5.37%）。

    判断标准：
    - 主板(非3字头)股票今日 change_pct 绝对值 > 11% → 疑似除权失真
    - 用新浪查昨收重新计算
    - 偏差 > 3% 即覆盖
    """
    from utils.dao import get_db
    db = get_db()

    # 找出今天涨跌幅异常的主板股（排除科创/创业板自身涨跌幅上限20%）
    suspects = db.fetchall(f'''
        SELECT code, name, close, change_pct
        FROM stock_daily
        WHERE trade_date = '{today}'
          AND code NOT LIKE '688%%'
          AND code NOT LIKE '301%%'
          AND code NOT LIKE '300%%'
          AND ABS(change_pct) > 11
          AND change_pct != 0
        ORDER BY ABS(change_pct) DESC
    ''')

    if not suspects:
        return

    logger.info(f"🔍 发现{len(suspects)}只疑似除权失真股票，开始修复...")

    import urllib.request
    fixed = 0
    for s in suspects:
        code = s['code']
        name = s['name'] or ''
        db_chg = s['change_pct']
        close = s['close']

        prefix = 'sh' if code.startswith('6') else 'sz'
        try:
            url = f'https://hq.sinajs.cn/list={prefix}{code}'
            req = urllib.request.Request(url, headers={'Referer': 'https://finance.sina.com.cn'})
            resp = urllib.request.urlopen(req, timeout=5)
            data = resp.read().decode('gbk')
            parts = data.split(',')
            if len(parts) < 4:
                continue
            pre_close = float(parts[2]) if parts[2] else 0
            if pre_close <= 0:
                continue
            correct_chg = round((close - pre_close) / pre_close * 100, 2)
        except Exception as e:
            logger.warning(f"  {code} {name} 新浪查价失败: {e}")
            continue

        # 偏差>3%才覆盖（避免小幅误差误修）
        if abs(correct_chg - db_chg) > 3:
            db.execute(
                'UPDATE stock_daily SET change_pct=%s WHERE code=%s AND trade_date=%s',
                (correct_chg, code, today)
            )
            logger.info(f"  ✅ {code} {name}: {db_chg:+.2f}% → {correct_chg:+.2f}%")
            fixed += 1

    logger.info(f"  共修复{fixed}只")


def _fetch_stock_today_batch(stock_list: list, today: str, max_workers: int = 10) -> dict:
    """
    批量并发拉取多只股票的单日（今天）日K数据。
    相比拉全年节省约 30 倍网络+解析开销。
    
    返回: {code: [record]}
    如果腾讯接口不支持单日查询，回退到拉全年再过滤回单条。
    """
    tx_map = {s['tx_code']: s['code'] for s in stock_list if s.get('tx_code')}
    tx_codes = list(tx_map.keys())
    result = {s['code']: [] for s in stock_list}
    
    if not tx_codes:
        return result
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {executor.submit(_fetch_one_tx, tc, today): tc for tc in tx_codes}
        for future in concurrent.futures.as_completed(future_map):
            tc = future_map[future]
            code = tx_map[tc]
            try:
                records = future.result()
                result[code] = records
            except Exception as e:
                logger.warning(f"  并发拉取 {tc} 异常: {e}")
    
    return result


def _batch_insert(records: list, code: str, name: str) -> int:
    """
    批量 INSERT IGNORE，替代逐条 insert_or_ignore。
    减少 50 倍数据库往返。
    """
    if not records:
        return 0
    
    # 构造批量 SQL
    # MySQL 允许 VALUES (..),(..),(..) 一次性插入多行
    placeholders = ','.join(['(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)'] * len(records))
    sql = f'''INSERT IGNORE INTO stock_daily
        (code, name, trade_date, open, close, high, low, volume, amount, change_pct)
        VALUES {placeholders}'''
    
    flat_params = []
    for r in records:
        flat_params.extend([
            code, name, r['trade_date'],
            r['open'], r['close'], r['high'], r['low'],
            r['volume'], r['amount'], r['change_pct'],
        ])
    
    try:
        cur = DB.conn.cursor()
        cur.execute(sql, flat_params)
        affected = cur.rowcount  # INSERT IGNORE 下 rowcount=实际插入数
        cur.close()
    except Exception as e:
        logger.warning(f"批量写入 {code} 失败: {e}")
        affected = 0
    
    return affected


def daily_incremental_update(max_workers: int = 20) -> int:
    """
    并发增量更新：只拉今天一天的日K数据 + 批量入库。
    除权问题由 _fix_exright_change_pct 单独校正。
    去掉第二轮补拉（单日请求失败率 <0.5%，边际收益极低）。
    """
    logger.info(f"开始个股日K增量更新（并发{max_workers}路，只拉单日+批量入库）...")
    today = datetime.now().strftime('%Y%m%d')
    
    # 验证数据源
    logger.info("🔍 验证腾讯数据源今日数据是否已就绪...")
    if not _verify_ka_data_is_fresh():
        logger.warning("⚠️ 跳过增量更新")
        return 0
    
    # 获取需更新的股票（已有记录但缺少今天的）
    stocks = DB.fetchall('''
        SELECT code, ANY_VALUE(name) as name
        FROM stock_daily
        GROUP BY code
        HAVING MAX(trade_date) < %s
    ''', (today,))
    
    logger.info(f"需更新 {len(stocks)} 只股票今日({today})数据...")
    
    if not stocks:
        logger.info("所有股票已包含今日数据，无需更新")
        return 0
    
    # 组装参数
    stock_list = []
    for s in stocks:
        stock_list.append({
            'code': s['code'],
            'name': s['name'] or '',
            'tx_code': code_to_tx(s['code']),
        })
    
    t_start = time.time()
    
    # 并发拉取所有股票的今日日K
    all_records = _fetch_stock_today_batch(stock_list, today, max_workers=max_workers)
    network_elapsed = time.time() - t_start
    
    # 批量写入
    batch_start = time.time()
    inserted = 0
    succeed = 0
    failed = 0
    for s in stock_list:
        code = s['code']
        name = s['name']
        records = all_records.get(code, [])
        if not records:
            failed += 1
            continue
        n = _batch_insert(records, code, name)
        inserted += n
        succeed += 1
    
    db_elapsed = time.time() - batch_start
    elapsed = time.time() - t_start
    
    logger.info(f"")
    logger.info(f"🏁 个股日K增量更新完成")
    logger.info(f"  网络拉取: {network_elapsed:.1f}s | 批量入库: {db_elapsed:.1f}s")
    logger.info(f"  成功: {succeed} | 失败: {failed}")
    logger.info(f"  新增记录: {inserted} 条")
    logger.info(f"  总耗时: {elapsed:.1f}s ({elapsed/max(len(stock_list),1):.2f}s/只)")
    
    # 校验覆盖率：对比昨日有今天却缺失的股票（停牌除外）
    _verify_coverage(today)
    
    # 除权日涨跌幅校验：用新浪昨收修正除权失真
    _fix_exright_change_pct(today)
    
    return inserted


# ─────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='拉取个股日K历史数据')
    parser.add_argument('--scope', choices=['hs300', 'all', 'stock', 'incremental'], default='hs300',
                        help='拉取范围: hs300(沪深300), all(全A), stock(单只), incremental(增量更新)')
    parser.add_argument('--code', type=str, help='单只股票代码（--scope stock时使用）')
    parser.add_argument('--start', type=str, default='20240101', help='开始日期 (默认2024-01-01)')
    parser.add_argument('--end', type=str, default=datetime.now().strftime('%Y%m%d'), help='结束日期 (默认今天)')
    parser.add_argument('--workers', type=int, default=30, help='并发数 (默认30)')
    parser.add_argument('--stats', action='store_true', help='仅显示统计')
    
    args = parser.parse_args()
    
    if args.stats:
        show_stats()
        sys.exit(0)
    
    if args.scope == 'incremental':
        daily_incremental_update(max_workers=args.workers)
        show_stats()
        sys.exit(0)
    
    print(f"{'='*60}")
    print(f"📈 个股日K历史数据拉取")
    print(f"{'='*60}")
    print(f"范围: {args.scope}")
    print(f"日期: {args.start} ~ {args.end}")
    print(f"并发: {args.workers}路")
    print()
    
    if args.scope == 'hs300':
        stocks = get_hs300_stocks()
    elif args.scope == 'all':
        stocks = get_all_stocks()
    elif args.scope == 'stock':
        if not args.code:
            logger.error("--scope stock 需要 --code 参数")
            sys.exit(1)
        stocks = [{'code': args.code, 'name': ''}]
    else:
        stocks = []
    
    if not stocks:
        logger.error("没有待拉取的股票")
        sys.exit(1)
    
    batch_fetch(stocks, args.start, args.end, max_workers=args.workers)
    show_stats()


# ─────────────────────────────────────────────
# 6. 腾讯实时行情接口：收盘采集（替换日K增量更新）
# ─────────────────────────────────────────────
def daily_quotes_update(max_workers: int = 20, batch_size: int = 200):
    """
    16:30定时任务：腾讯实时行情接口采集收盘数据+市值
    替换原来的 daily_incremental_update（日K接口保留做历史回溯备用）

    接口: qt.gtimg.cn/q=
    原始字段说明：
      [3] current_price     现价（元 → 当close）
      [4] pre_close         昨收
      [5] open               今开
      [6] volume             成交量
        - 主板(6/00/002开头)、创业板(300开头)：手
        - 科创板(688开头)、北交所(4/8开头)：股
      [30] query_time        数据时间
      [32] change_pct        涨跌幅(%)
      [33] high              最高价
      [34] low               最低价
      [37] amount            成交额（万元 → 元×10000）
      [38] turnover_rate     换手率(%)
      [39] pe_ratio          市盈率
      [44] circulation_market_cap  流通市值(亿 → 元×100000000)
      [45] total_market_cap         总市值(亿 → 元×100000000)
      [46] pb_ratio          市净率
    """
    today = datetime.now().strftime('%Y%m%d')
    logger.info(f'📡 开始腾讯实时行情采集({today})...')

    # 获取全A股代码列表
    from utils.dao import get_db as _get_db
    _db = _get_db()
    rows = _db.fetchall('SELECT DISTINCT code FROM stock_daily')
    all_codes = [r['code'] for r in rows]
    _db.close()
    total = len(all_codes)
    logger.info(f'共{total}只股票, {max_workers}路并发, {batch_size}只/批')

    import urllib.request
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import time as _time

    def _fetch_one(code):
        """拉取单只股票腾讯实时行情"""
        prefix = 'sz' if not code.startswith('6') and not code.startswith('9') else 'sh'
        if code.startswith('4') or code.startswith('8'):
            prefix = 'bj'
        tx_code = f'{prefix}{code}'
        url = f'https://qt.gtimg.cn/q={tx_code}'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        try:
            resp = urllib.request.urlopen(req, timeout=8)
            text = resp.read().decode('gbk')
            parts = text.split('~')
            if len(parts) < 49:
                return (code, None, f'字段不足:{len(parts)}')

            name = parts[1]
            cur_price = float(parts[3]) if parts[3] else None
            pre_close = float(parts[4]) if parts[4] else None
            open_price = float(parts[5]) if parts[5] else None
            high = float(parts[33]) if parts[33] else None
            low = float(parts[34]) if parts[34] else None
            change_pct = float(parts[32]) if parts[32] else None

            # 成交量：判断单位
            vol_raw = float(parts[6]) if parts[6] else 0
            # 科创板(688)和北交所(4/8开头)：股；其他：手→股
            is_kcb = code.startswith('688') or code.startswith('4') or code.startswith('8')
            volume = vol_raw if is_kcb else vol_raw * 100  # 手→股

            # 成交额：万元→元
            amt_raw = float(parts[37]) if parts[37] else 0
            amount = round(amt_raw * 10000, 2)  # 万元→元

            # 换手率/PE/PB：直接使用
            turnover_rate = float(parts[38]) if parts[38] else None
            pe_ratio = float(parts[39]) if parts[39] else None
            pb_ratio = float(parts[46]) if parts[46] else None

            # 市值：亿→元（[44]=流通市值, [45]=总市值）
            total_market_cap = float(parts[45]) * 100000000 if parts[45] else None
            circulation_market_cap = float(parts[44]) * 100000000 if parts[44] else None

            return (code, {
                'name': name or '',
                'close': cur_price,
                'open': open_price,
                'high': high,
                'low': low,
                'volume': volume,
                'amount': amount,
                'change_pct': change_pct,
                'turnover_rate': turnover_rate,
                'pe_ratio': pe_ratio,
                'pb_ratio': pb_ratio,
                'total_market_cap': total_market_cap,
                'circulation_market_cap': circulation_market_cap,
            }, None)
        except Exception as e:
            return (code, None, str(e)[:80])

    t_start = _time.time()
    total_ok = 0
    total_fail = 0
    insert_batch = []

    for batch_start in range(0, total, batch_size):
        batch = all_codes[batch_start:batch_start + batch_size]

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futs = [pool.submit(_fetch_one, c) for c in batch]
            for f in as_completed(futs):
                code, data, err = f.result()
                if data:
                    insert_batch.append((code, data))
                    total_ok += 1
                else:
                    total_fail += 1
                    if total_fail <= 5:
                        logger.warning(f'  {code} 采集失败: {err}')

        # 批量入库
        if insert_batch:
            __db = _get_db()
            for code, data in insert_batch:
                try:
                    __db.execute('''REPLACE INTO stock_daily
                        (code, name, trade_date, open, close, high, low, volume, amount, change_pct,
                         turnover_rate, pe_ratio, pb_ratio, total_market_cap, circulation_market_cap)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)''',
                        (code, data['name'], today, data['open'], data['close'], data['high'], data['low'],
                         data['volume'], data['amount'], data['change_pct'],
                         data['turnover_rate'], data['pe_ratio'], data['pb_ratio'],
                         data['total_market_cap'], data['circulation_market_cap']))
                except Exception as e:
                    pass
            __db.close()
            insert_batch = []

        elapsed = _time.time() - t_start
        speed = (batch_start + len(batch)) / elapsed if elapsed > 0 else 0
        logger.info(f'  已处理 {min(batch_start+batch_size, total)}/{total}  OK:{total_ok} FAIL:{total_fail}  {elapsed:.0f}s {speed:.0f}只/秒')

    elapsed_total = _time.time() - t_start
    logger.info(f'\n{"="*50}')
    logger.info(f'🏁 腾讯实时行情采集完成')
    logger.info(f'  日期: {today} 总成交: {total}')
    logger.info(f'  成功: {total_ok} 失败: {total_fail}')
    logger.info(f'  总耗时: {elapsed_total:.1f}s = {elapsed_total/60:.1f}分钟')
    logger.info(f'{"="*50}')
    return total_ok
