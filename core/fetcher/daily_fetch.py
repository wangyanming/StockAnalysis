"""
每日定时数据采集脚本
每天 15:10 盘中快照, 18:30 收盘后完整拉取（含个股日K增量）
数据源：同花顺板块(90行业) + 东财涨停 + 腾讯日K
  同花顺：涨跌点数/涨跌幅 百分比单位
  东财涨停：价格=元, 封单量=手(x100->股), 成交额=元
  腾讯日K：close/open/high/low=元, volume=手(x100->股), amount=万元(x10000->元)
"""

import sys
import os
import time
import json
import logging
from datetime import datetime

# 切换到项目根目录（必须在任何项目内部import之前执行）
_script_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(os.path.dirname(_script_dir))  # core/fetcher -> core -> 项目根
os.chdir(_project_root)
sys.path.insert(0, _project_root)

from utils.logger import setup_logger
logger = setup_logger("daily_fetch")

# 默认使用 MySQL（无环境变量时兜底）
if 'STOCK_DB_URL' not in os.environ:
    os.environ['STOCK_DB_URL'] = 'mysql://root:stock123@127.0.0.1:3306/stock_analysis'

def fetch_all(do_stock_daily: bool = False):
    """执行完整数据采集
    Args:
        do_stock_daily: 是否执行个股日K增量更新（15:10太快不跑，17:00/18:30跑）
    """
    from utils.stock_analysis_api import StockDataFetcher
    from core.fetcher.limit_up_analysis import LimitUpAnalyzer
    
    t0 = time.time()
    f = StockDataFetcher()
    zt = LimitUpAnalyzer()

    def _save_index_quote(index_code, data):
        from utils.dao import get_db
        try:
            cur = get_db().execute(
                """REPLACE INTO index_quotes 
                   (index_code, name, current_price, change_pct, open, high, low, volume, amount, timestamp, record_date)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    index_code,
                    data.get("name", index_code),
                    data.get("current_price", 0),
                    data.get("change_pct", 0),
                    data.get("open", 0),
                    data.get("high", 0),
                    data.get("low", 0),
                    data.get("volume", 0),
                    data.get("amount", 0),
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    datetime.now().strftime("%Y-%m-%d")
                )
            )
            cur.close()
        except Exception as e:
            logger.error(f"保存指数行情失败: {e}")
    
    results = {}
    
    # 1. 指数
    logger.info("拉取指数行情...")
    try:
        for idx in ['szzs', 'szcz', 'hs300', 'cyb', 'kc50']:
            data = f.fetch_index_data(idx)
            if data:
                _save_index_quote(idx, data)
        results['index'] = 'OK'
    except Exception as e:
        results['index'] = f'ERR: {e}'
        logger.error(f"指数拉取失败: {e}")
    
    # 2. 大盘概览（已废弃，daily_snapshots表已删除）
    results['snapshot'] = 'OK'
    
    # 3. 板块 — 同花顺全量行业板块（10次重试，不回落、不复用上日数据）
    logger.info("拉取板块表现...")
    today = datetime.now().strftime('%Y-%m-%d')
    try:
        board_ok = False
        for retry in range(10):
            try:
                import akshare as ak
                df = ak.stock_board_industry_summary_ths()
                if df is not None and not df.empty and '涨跌幅' in df.columns:
                    now_str = datetime.now().strftime('%H:%M:%S')
                    from utils.dao import get_db as _get_db
                    _db = _get_db()
                    
                    sql = '''INSERT IGNORE INTO sector_performance
                       (record_date, record_time, sector_name, change_pct, amount, net_inflow, rise_count, fall_count, rank_type)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)'''
                    rows = []
                    for _, r in df.iterrows():
                        rows.append((
                            today,
                            now_str,
                            r.get('板块', ''),
                            float(r.get('涨跌幅', 0)),
                            float(r.get('总成交额', 0) or 0) * 1e8,
                            float(r.get('净流入', 0) or 0) * 1e8,
                            int(float(r.get('上涨家数', 0))),
                            int(float(r.get('下跌家数', 0))),
                            'all',
                        ))
                    _db.executemany(sql, rows)
                    saved = len(rows)
                    logger.info(f"同花顺板块全量写入完成: {saved}条")
                    board_ok = True
                    break
            except Exception as e:
                if retry < 9:
                    logger.warning(f"同花顺板块接口失败 (第{retry+1}次): {e}，重试中...")
                    time.sleep(3)
                else:
                    logger.error(f"同花顺板块接口10次重试均失败: {e}")
                    results['sectors'] = f'ERR: {e}'
                    raise
        if board_ok:
            results['sectors'] = f'{saved}条'
    except Exception as e:
        if 'results' not in dir() or results.get('sectors', '').startswith('ERR'):
            results['sectors'] = f'ERR: {e}'
        logger.error(f"板块拉取失败: {e}")
    
    # 4. 涨停（10次重试，不回落、不复用上日数据）
    logger.info("拉取涨停数据...")
    try:
        limit_up_count = 0
        for retry in range(10):
            try:
                res = zt.save_today_limit_up()
                limit_up_count = res.get('count', 0)
                if limit_up_count > 0 or res.get('status') == 'no_data':
                    break
                if retry < 9:
                    logger.warning(f"涨停数据为空 (第{retry+1}次)，等待3秒重试...")
                    time.sleep(3)
            except Exception as e2:
                if retry < 9:
                    logger.warning(f"涨停拉取异常 (第{retry+1}次): {e2}，重试中...")
                    time.sleep(3)
                else:
                    logger.error(f"涨停10次重试均失败: {e2}")
                    raise e2
        # 行业统计：涨停拉取成功后，同步保存行业分布
        if limit_up_count > 0:
            try:
                zt.save_industry_stats()
                logger.info(f"涨停行业分布已保存")
            except Exception as e_inds:
                logger.warning(f"行业统计保存失败: {e_inds}")
        results['limit_up'] = f'{limit_up_count}只'
    except Exception as e:
        results['limit_up'] = f'ERR: {e}'
        logger.error(f"涨停拉取失败: {e}")
    
    # 5. 跌停
    logger.info("拉取跌停数据...")
    try:
        import akshare as ak
        today_dt = datetime.now().strftime('%Y%m%d')
        df_down = ak.stock_zt_pool_dtgc_em(date=today_dt)
        if df_down is not None and not df_down.empty:
            from utils.dao import get_db as _get_db
            _db = _get_db()
            saved_down = 0
            for _, r in df_down.iterrows():
                change_str = str(r.get('涨跌幅', '')).replace('%','')
                try:
                    _db.execute("""
                        REPLACE INTO daily_limit_up
                        (trade_date, code, name, price, change_pct, turnover_rate,
                         seal_last_time, board_times, bomb_times, seal_fund, industry, status)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'跌停')
                    """, (
                        today_dt,
                        str(r.get('代码', '')).strip(),
                        str(r.get('名称', '')).strip(),
                        float(r.get('最新价', 0)),
                        float(change_str),
                        float(r.get('换手率', 0)),
                        str(r.get('最后封板时间', '')).zfill(6),
                        int(r.get('连续跌停', 1)),
                        int(r.get('开板次数', 0)),
                        float(r.get('封单资金', 0)),
                        str(r.get('所属行业', '')).strip()
                    ))
                    saved_down += 1
                except Exception as e:
                    pass
            try:
                _db.commit()
            except Exception:
                pass  # 已启用 autocommit，无需显式 commit
            logger.info(f"跌停数据入库: {saved_down}条")
            results['limit_down'] = f'{saved_down}只'
        else:
            logger.info("今日无跌停数据")
            results['limit_down'] = '0只'
    except Exception as e:
        results['limit_down'] = f'ERR: {e}'
        logger.error(f"跌停拉取失败: {e}")
    
    # 6. 个股日K增量更新（仅在收盘后执行，盘中腾讯无数据）
    if do_stock_daily:
        logger.info("增量更新个股日K...")
        try:
            from core.fetcher.fetch_all_stocks_daily import daily_incremental_update
            inserted = daily_incremental_update(sleep_sec=0.3)
            results['stock_daily'] = f'+{inserted}条'
        except Exception as e:
            results['stock_daily'] = f'ERR: {e}'
            logger.error(f"个股日K增量更新失败: {e}")
    
    elapsed = time.time() - t0
    results['elapsed'] = elapsed
    logger.info(f"✅ 采集完成 ({elapsed:.1f}s): {results}")
    return results

if __name__ == '__main__':
    mode = '收盘完整' if '--stock-daily' in sys.argv else '盘中快照'
    logger.info(f"=== 开始数据采集 [{mode}]: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
    results = fetch_all(do_stock_daily='--stock-daily' in sys.argv)
    
    # 标准输出给 command 模式推送
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    if mode == '盘中快照':
        index_status = results.get('index', 'ERR')
        sector_status = results.get('sectors', 'ERR')
        limit_up = results.get('limit_up', 'ERR')
        limit_down = results.get('limit_down', 'ERR')
        elapsed = results.get('elapsed', 0)
        print(f"盘中快照采集完成 ✅")
        print(f"{now} 快照摘要：")
        print(f"指数行情 & 盘口快照：{'✅' if index_status == 'OK' else '❌'}")
        print(f"板块表现：{sector_status} 已入库")
        print(f"涨停：{limit_up}")
        print(f"跌停：{limit_down}")
        print(f"耗时：{elapsed:.1f}s")
    else:
        print(f"收盘数据采集完成 ✅")
        print(f"{now} 采集摘要：")
        print(f"指数行情：{'✅' if results.get('index') == 'OK' else '❌'}")
        print(f"板块表现：{results.get('sectors', 'ERR')} 已入库")
        print(f"涨停：{results.get('limit_up', 'ERR')}")
        print(f"跌停：{results.get('limit_down', 'ERR')}")
        if 'stock_daily' in results:
            print(f"个股日K：{results['stock_daily']}")
        print(f"耗时：{elapsed:.1f}s")
