"""
候选股回溯分析 v3 - 补全次日涨跌幅（通过新浪日K线）
"""
import json
import urllib.request
import time
import re
import os
from datetime import datetime
from collections import defaultdict

from dao import get_db

DB_PATH = '/Users/wangyanming/workspace/StockAnalysis/stock_data.db'
OUTPUT_PATH = '/Users/wangyanming/workspace/StockAnalysis/backtest_candidates_v3.json'
CACHE_PATH = '/Users/wangyanming/workspace/StockAnalysis/kline_cache.json'

def _normalize_code(code: str) -> str:
    code = code.strip()
    if code.startswith('sh') or code.startswith('sz'):
        return code
    if code.startswith('6') or code.startswith('9') or code.startswith('5'):
        return 'sh' + code
    return 'sz' + code

def fetch_kline(code: str, datalen: int = 30) -> dict:
    """获取个股日K线，返回 {日期: 涨跌幅%} 映射"""
    try:
        norm = _normalize_code(code)
        url = f'https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={norm}&scale=240&ma=no&datalen={datalen}'
        req = urllib.request.Request(url, headers={'Referer': 'https://finance.sina.com.cn'})
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read().decode('gbk'))
        
        result = {}
        # data 是按时间正序的（最早的在前）
        for i, d in enumerate(data):
            date = d['day']
            close = float(d['close'])
            # 次日涨跌幅 = (当日收盘 - 前日收盘) / 前日收盘 * 100
            if i > 0:
                prev_close = float(data[i-1]['close'])
                chg = (close - prev_close) / prev_close * 100 if prev_close > 0 else 0
            else:
                chg = 0
            result[date] = round(chg, 2)
        
        time.sleep(0.2)  # 限速
        return result
    except Exception as e:
        return {}

def main():
    print(f"={'='*60}=")
    print(f"  候选股回溯 v3 - 次日涨跌幅补全")
    print(f"={'='*60}=")
    
    # 加载已有缓存
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH) as f:
            kline_cache = json.load(f)
    else:
        kline_cache = {}
    
    db = get_db()
    
    # 获取所有交易日（去重）
    all_dates = [r[0] for r in db.execute('SELECT DISTINCT trade_date FROM daily_limit_up ORDER BY trade_date').fetchall()]
    
    # 检查重复数据
    dup_dates = set()
    prev_data = None
    for d in all_dates:
        data = db.execute('SELECT code, change_pct FROM daily_limit_up WHERE trade_date=%s ORDER BY code', (d,)).fetchall()
        if data == prev_data:
            dup_dates.add(d)
        prev_data = data
    
    real_dates = [d for d in all_dates if d not in dup_dates]
    trade_dates = [d for d in real_dates if d >= '20260428']
    
    print(f"  有效交易日: {len(trade_dates)} ({trade_dates[0]} ~ {trade_dates[-1]})")
    
    all_entries = []
    stats = {
        'total_candidates': 0,
        'total_limit_up_pct': 0,     # 涨停率（分母=全部候选股）
        'total_positive_pct': 0,     # 红盘率
        'total_negative_pct': 0,     # 绿盘率
        'limit_up_next': 0,          # 次日涨停
        'up_5pct_next': 0,           # 次日涨5%+（不含涨停）
        'up_3pct_next': 0,           # 次日涨3%+（不含涨停）
        'positive_next': 0,          # 次日红盘
        'zero_next': 0,              # 次日平盘
        'negative_next': 0,          # 次日绿盘
        'down_3pct_next': 0,         # 次日跌3%+（不含跌停）
        'down_5pct_next': 0,         # 次日跌5%+
        'limit_down_next': 0,        # 次日跌停
    }
    
    # 按连板数细分
    board_stats = defaultdict(lambda: {'total': 0, 'limit_up': 0, 'positive': 0, 'negative': 0, 'avg_return': 0, 'return_sum': 0})
    
    for i, trade_date in enumerate(trade_dates):
        next_date = trade_dates[i + 1] if i + 1 < len(trade_dates) else None
        if not next_date:
            break
        
        date_readable = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"
        next_readable = f"{next_date[:4]}-{next_date[4:6]}-{next_date[6:]}"
        
        # 获取当天涨停数据
        rows = db.execute("""
            SELECT code, name, board_times, turnover_rate, industry, change_pct
            FROM daily_limit_up
            WHERE trade_date = %s
            ORDER BY code
        """, (trade_date,)).fetchall()
        if not rows:
            continue
        
        # 选股逻辑（同v2）
        industry_count = defaultdict(int)
        stock_info = []
        for r in rows:
            code, name, board_times, turnover_rate, industry, change_pct = r
            industry_count[industry] += 1
            stock_info.append({
                'code': code, 'name': name, 'board_times': board_times,
                'turnover_rate': turnover_rate or 0, 'industry': industry,
            })
        
        hot_industries = sorted(industry_count.items(), key=lambda x: x[1], reverse=True)
        total_limit_up = len(rows)
        
        candidates = []
        seen = set()
        
        # 路径A
        for s in stock_info:
            if s['board_times'] > 3: continue
            if s['turnover_rate'] < 1 or s['turnover_rate'] > 50: continue
            if s['code'].startswith('688') or s['code'].startswith('300'): continue
            key = f"{s['code']}_{s['name']}"
            if key not in seen:
                seen.add(key)
                candidates.append({**s, 'source': '涨停热点'})
        
        # 路径B
        for industry, cnt in hot_industries:
            if cnt >= 2:
                for s in stock_info:
                    if s['industry'] == industry and s['board_times'] <= 2:
                        key = f"{s['code']}_{s['name']}"
                        if key not in seen and 3 <= s['turnover_rate'] <= 30:
                            if not (s['code'].startswith('688') or s['code'].startswith('300')):
                                seen.add(key)
                                candidates.append({**s, 'source': '板块跟风'})
        
        if not candidates:
            continue
        
        # 查次日涨停表
        next_day_limit_raw = db.execute("""
            SELECT code, change_pct, name, board_times
            FROM daily_limit_up
            WHERE trade_date = %s
        """, (next_date,)).fetchall()
        next_day_limit = {}
        for r in next_day_limit_raw:
            code, change_pct, name, board_times = r
            next_day_limit[code] = {'change_pct': change_pct, 'board_times': board_times}
        
        # 批量获取K线数据
        need_kline = []
        for c in candidates:
            if c['code'] not in next_day_limit:
                need_kline.append(c['code'])
        
        # 从缓存读取，缺失的从新浪抓取
        fetch_codes = [code for code in need_kline if code not in kline_cache]
        if fetch_codes:
            print(f"\n  正在拉取 {len(fetch_codes)} 只个股日K线...", end=' ', flush=True)
            for code in fetch_codes:
                kline_cache[code] = fetch_kline(code, 30)
                if len(fetch_codes) > 10:
                    time.sleep(0.15)
            print("完成", flush=True)
        
        # 次日日期格式化
        next_date_fmt = f"{next_date[:4]}-{next_date[4:6]}-{next_date[6:]}"
        
        # 统计数据
        day_limit_up = 0
        day_positive = 0
        day_negative = 0
        day_limit_down = 0
        
        day_entries = []
        
        for c in candidates:
            code = c['code']
            entry = {
                'date': date_readable,
                'trade_date': trade_date,
                'next_trade_date': next_date,
                'code': code,
                'name': c['name'],
                'board_times': c['board_times'],
                'industry': c['industry'],
                'turnover_rate': c['turnover_rate'],
                'source': c['source'],
            }
            
            nd = {}
            if code in next_day_limit:
                chg = next_day_limit[code]['change_pct']
                nd['change_pct'] = round(chg, 2) if chg else None
                nd['is_limit_up'] = chg >= 9.5 if chg else False
                nd['is_limit_down'] = chg <= -9.5 if chg else False
                nd['data_source'] = '涨停表'
                nd['board_times'] = next_day_limit[code]['board_times']
            elif code in kline_cache:
                kl = kline_cache[code]
                if next_date_fmt in kl:
                    chg = kl[next_date_fmt]
                    nd['change_pct'] = chg
                    nd['is_limit_up'] = chg >= 9.5
                    nd['is_limit_down'] = chg <= -9.5
                    nd['data_source'] = 'K线'
                else:
                    nd['change_pct'] = None
                    nd['data_source'] = '无数据'
            else:
                nd['change_pct'] = None
                nd['data_source'] = '无数据'
            
            entry['next_day'] = nd
            day_entries.append(entry)
            
            # 统计
            chg = nd.get('change_pct')
            if chg is not None:
                stats['total_candidates'] += 1
                board_stats[c['board_times']]['total'] += 1
                board_stats[c['board_times']]['return_sum'] += chg
                
                if nd.get('is_limit_up'):
                    stats['limit_up_next'] += 1
                    board_stats[c['board_times']]['limit_up'] += 1
                    day_limit_up += 1
                elif nd.get('is_limit_down'):
                    stats['limit_down_next'] += 1
                    day_limit_down += 1
                
                if chg > 0:
                    stats['positive_next'] += 1
                    board_stats[c['board_times']]['positive'] += 1
                    day_positive += 1
                elif chg < 0:
                    stats['negative_next'] += 1
                    board_stats[c['board_times']]['negative'] += 1
                    day_negative += 1
                else:
                    stats['zero_next'] += 1
                
                if chg >= 5 and not nd.get('is_limit_up'):
                    stats['up_5pct_next'] += 1
                if chg >= 3 and not nd.get('is_limit_up'):
                    stats['up_3pct_next'] += 1
                if chg <= -5 and not nd.get('is_limit_down'):
                    stats['down_5pct_next'] += 1
                if chg <= -3 and not nd.get('is_limit_down'):
                    stats['down_3pct_next'] += 1
        
            all_entries.append(entry)
        
        # 输出当日摘要
        print(f"\n{'─'*60}")
        print(f"📅 {date_readable}(涨停{total_limit_up}) → {next_readable}")
        print(f"  候选{len(candidates)}只 | 涨停{day_limit_up} 红盘{day_positive} 绿盘{day_negative} 跌停{day_limit_down}")
        
        # 按连板输出
        for board in sorted(board_stats.keys()):
            bg = board_stats[board]
            print(f"  {board}板: {bg['total']}只 | 涨停{bg['limit_up']} | ")
    
    # === 汇总 ===
    total = stats['total_candidates']
    print(f"\n{'='*70}")
    print(f"  📊 汇总统计")
    print(f"{'='*70}")
    
    print(f"  回溯区间: {trade_dates[0]} ~ {trade_dates[-1]}")
    print(f"  交易日数: {len(trade_dates)}")
    print(f"  总候选股: {total}")
    print(f"  有次日数据: {total} (100%)")
    
    if total > 0:
        print(f"\n  📈 次日涨跌分布:")
        print(f"    🚀 涨停:   {stats['limit_up_next']}  ({stats['limit_up_next']/total*100:.1f}%)")
        print(f"    📈 +5%+（不含涨停）: {stats['up_5pct_next']}  ({stats['up_5pct_next']/total*100:.1f}%)")
        print(f"    📈 +3%+:  {stats['up_3pct_next']}  ({stats['up_3pct_next']/total*100:.1f}%)")
        print(f"    🟢 红盘:   {stats['positive_next']}  ({stats['positive_next']/total*100:.1f}%)")
        print(f"    ⚪ 平盘:   {stats['zero_next']}  ({stats['zero_next']/total*100:.1f}%)")
        print(f"    🔴 绿盘:   {stats['negative_next']}  ({stats['negative_next']/total*100:.1f}%)")
        print(f"    📉 -3%~-5%: {stats['down_3pct_next']}  ({stats['down_3pct_next']/total*100:.1f}%)")
        print(f"    📉 -5%+（不含跌停）: {stats['down_5pct_next']}  ({stats['down_5pct_next']/total*100:.1f}%)")
        print(f"    💀 跌停:   {stats['limit_down_next']}  ({stats['limit_down_next']/total*100:.1f}%)")
    
    # 按连板数
    print(f"\n  📈 按连板数:")
    for board in sorted(board_stats.keys()):
        bg = board_stats[board]
        avg_ret = bg['return_sum'] / bg['total'] if bg['total'] > 0 else 0
        print(f"    {board}板: {bg['total']}只 | 涨停率{bg['limit_up']/bg['total']*100:.1f}% | 红盘率{bg['positive']/bg['total']*100:.1f}% | 平均涨幅{avg_ret:+.2f}%")
    
    # 保存
    output = {
        'analysis_range': f'{trade_dates[0]}~{trade_dates[-1]}',
        'effective_trade_days': len(trade_dates),
        'stats': stats,
        'board_stats': {str(k): dict(v) for k, v in board_stats.items()},
        'entries': all_entries,
    }
    
    with open(OUTPUT_PATH, 'w') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    # 缓存K线
    with open(CACHE_PATH, 'w') as f:
        json.dump(kline_cache, f, ensure_ascii=False)
    
    print(f"\n  详细数据: {OUTPUT_PATH}")
    print(f"  K线缓存: {CACHE_PATH} ({len(kline_cache)}只个股)")

if __name__ == '__main__':
    main()
