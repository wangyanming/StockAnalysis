"""
候选股回溯 v4 - 每日精选8只口径
模拟 daily_pick_v2.py 的完整流程：筛选→评分→排序→取前8
"""
import json
import urllib.request
import time
import re
import os
import sys
from datetime import datetime
from collections import defaultdict, Counter

from dao import get_db

CACHE_PATH = '/Users/wangyanming/workspace/StockAnalysis/kline_cache.json'
OUTPUT_PATH = '/Users/wangyanming/workspace/StockAnalysis/backtest_candidates_v4.json'

sys.path.insert(0, '/Users/wangyanming/workspace/StockAnalysis')
os.chdir('/Users/wangyanming/workspace/StockAnalysis')

from scorer import (
    fetch_sina_quote,
    check_market_status,
    get_sector_hot_score,
    tech_score_via_sina,
)
from fundamental import get_latest_financial, evaluate_fundamental

def _normalize_code(code: str) -> str:
    code = code.strip()
    if code.startswith('sh') or code.startswith('sz'):
        return code
    if code.startswith('6') or code.startswith('9') or code.startswith('5'):
        return 'sh' + code
    return 'sz' + code

def fetch_kline_range(codes: list, datalen: int = 10) -> dict:
    """批量拉取日K线并缓存"""
    kline_cache = {}
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH) as f:
            kline_cache = json.load(f)
    
    fetch_codes = [c for c in codes if c not in kline_cache]
    if fetch_codes:
        print(f"  拉取 {len(fetch_codes)} 只日K线...", end=' ', flush=True)
        for code in fetch_codes:
            try:
                norm = _normalize_code(code)
                url = f'https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={norm}&scale=240&ma=no&datalen={datalen}'
                req = urllib.request.Request(url, headers={'Referer': 'https://finance.sina.com.cn'})
                resp = urllib.request.urlopen(req, timeout=10)
                data = json.loads(resp.read().decode('gbk'))
                
                result = {}
                for i, d in enumerate(data):
                    date = d['day']
                    close = float(d['close'])
                    open_p = float(d['open'])
                    if open_p > 0:
                        chg = (close - open_p) / open_p * 100
                    else:
                        if i > 0:
                            prev_close = float(data[i-1]['close'])
                            chg = (close - prev_close) / prev_close * 100 if prev_close > 0 else 0
                        else:
                            chg = 0
                    result[date] = round(chg, 2)
                
                kline_cache[code] = result
                time.sleep(0.15)
            except Exception:
                kline_cache[code] = {}
        
        with open(CACHE_PATH, 'w') as f:
            json.dump(kline_cache, f, ensure_ascii=False)
        print("完成")
    
    return kline_cache

def main():
    print(f"={'='*60}=")
    print(f"  每日精选8只 - 回溯分析")
    print(f"={'='*60}=")
    
    db = get_db()
    
    # 交易日
    all_dates = [r[0] for r in db.execute('SELECT DISTINCT trade_date FROM daily_limit_up ORDER BY trade_date').fetchall()]
    
    # 去重
    prev_data = None
    dup_dates = set()
    for d in all_dates:
        data = db.execute('SELECT code, change_pct FROM daily_limit_up WHERE trade_date=%s ORDER BY code', (d,)).fetchall()
        if data == prev_data:
            dup_dates.add(d)
        prev_data = data
    
    real_dates = [d for d in all_dates if d not in dup_dates]
    trade_dates = [d for d in real_dates if d >= '20260428']
    trade_dates_effective = trade_dates[:-1]  # 最后一天没有次日数据
    
    print(f"  交易日数: {len(trade_dates_effective)} ({trade_dates_effective[0]} ~ {trade_dates_effective[-1]})")
    print(f"  每日精选: 前8只（按综合评分排序）")
    
    # 收集所有需要拉取K线的代码
    all_need_kline = set()
    
    for trade_date in trade_dates_effective:
        rows = db.execute('''
            SELECT code, name, board_times, turnover_rate, industry
            FROM daily_limit_up
            WHERE trade_date=%s
        ''', (trade_date,)).fetchall()
        
        from collections import Counter
        industry_count = Counter()
        stock_info = []
        for r in rows:
            code, name, boards, turnover, industry = r
            industry_count[industry] += 1
            stock_info.append({
                'code': code, 'name': name, 'board_times': boards,
                'turnover_rate': turnover or 0, 'industry': industry,
            })
        
        hot_industries = sorted(industry_count.items(), key=lambda x: x[1], reverse=True)
        
        # 选股逻辑
        candidates = []
        seen = set()
        
        for s in stock_info:
            if s['board_times'] > 3: continue
            if s['turnover_rate'] < 1 or s['turnover_rate'] > 50: continue
            if s['code'].startswith('688') or s['code'].startswith('300'): continue
            key = f"{s['code']}_{s['name']}"
            if key not in seen:
                seen.add(key)
                candidates.append({**s, 'source': '涨停热点'})
        
        for industry, cnt in hot_industries:
            if cnt >= 2:
                for s in stock_info:
                    if s['industry'] == industry and s['board_times'] <= 2:
                        key = f"{s['code']}_{s['name']}"
                        if key not in seen and 3 <= s['turnover_rate'] <= 30:
                            if not (s['code'].startswith('688') or s['code'].startswith('300')):
                                seen.add(key)
                                candidates.append({**s, 'source': '板块跟风'})
        
        # 获取次日
        idx = trade_dates.index(trade_date)
        next_date = trade_dates[idx + 1]
        
        for c in candidates:
            all_need_kline.add(c['code'])
    
    # 拉取K线
    print(f"\n  预拉取 {len(all_need_kline)} 只个股K线...")
    kline_cache = fetch_kline_range(list(all_need_kline), 10)
    
    # 正式回溯
    print(f"\n{'='*60}")
    
    all_picks = []
    stats = {
        'days': 0,
        'total_picks': 0,
        'limit_up': 0,
        'up_5pct': 0,
        'up_3pct': 0,
        'positive': 0,
        'zero': 0,
        'negative': 0,
        'down_3pct': 0,
        'down_5pct': 0,
        'limit_down': 0,
        'return_sum': 0.0,
    }
    board_stats = defaultdict(lambda: {'total': 0, 'limit_up': 0, 'positive': 0, 'return_sum': 0.0})
    
    for i, trade_date in enumerate(trade_dates_effective):
        next_date = trade_dates[trade_dates.index(trade_date) + 1]
        date_readable = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"
        next_readable = f"{next_date[:4]}-{next_date[4:6]}-{next_date[6:]}"
        next_date_fmt = f"{next_date[:4]}-{next_date[4:6]}-{next_date[6:]}"
        
        # 获取当天涨停数据
        rows = db.execute('''
            SELECT code, name, board_times, turnover_rate, industry
            FROM daily_limit_up
            WHERE trade_date=%s
        ''', (trade_date,)).fetchall()
        
        from collections import Counter
        industry_count = Counter()
        stock_info = []
        for r in rows:
            code, name, boards, turnover, industry = r
            industry_count[industry] += 1
            stock_info.append({
                'code': code, 'name': name, 'board_times': boards,
                'turnover_rate': turnover or 0, 'industry': industry,
            })
        
        hot_industries = sorted(industry_count.items(), key=lambda x: x[1], reverse=True)
        total_limit_up = len(rows)
        
        # 选股逻辑
        candidates = []
        seen = set()
        
        for s in stock_info:
            if s['board_times'] > 3: continue
            if s['turnover_rate'] < 1 or s['turnover_rate'] > 50: continue
            if s['code'].startswith('688') or s['code'].startswith('300'): continue
            key = f"{s['code']}_{s['name']}"
            if key not in seen:
                seen.add(key)
                candidates.append({**s, 'source': '涨停热点'})
        
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
        
        # 评分（简化版）
        scored = []
        for c in candidates:
            code = c['code']
            name = c['name']
            
            # 板块热度（用当天数据）
            sec_score = 0
            sec_detail = []
            industry = c['industry']
            
            total_zt = total_limit_up
            for sec_name, cnt in hot_industries:
                if industry in sec_name or (sec_name in industry):
                    if cnt >= 10: sec_score = 25
                    elif cnt >= 7: sec_score = 20
                    elif cnt >= 5: sec_score = 15
                    elif cnt >= 3: sec_score = 10
                    elif cnt >= 2: sec_score = 5
                    break
            
            # 市场热度
            if total_zt > 60: market_heat = 10
            elif total_zt > 40: market_heat = 8
            elif total_zt > 25: market_heat = 5
            else: market_heat = 3
            
            # 连板高度
            board_score = 0
            alive = sorted(candidates, key=lambda x: x['board_times'], reverse=True)
            if alive:
                max_board = alive[0]['board_times']
                if max_board >= 7: board_score = 5
                elif max_board >= 5: board_score = 4
                elif max_board >= 3: board_score = 3
                elif max_board >= 2: board_score = 1
            
            # 位置
            spot_score = 0
            if c['board_times'] == 1: spot_score = 2
            elif c['board_times'] == 2: spot_score = 4
            else: spot_score = 5
            
            # 政策（提取名字和行业关键词）
            policy_score = 0
            combined = name + industry
            policy_keywords = {
                '低空': '低空经济', 'AI': '人工智能', '芯片': '半导体',
                '机器人': '机器人', '新能源': '新能源', '光伏': '新能源',
                '航天': '商业航天', '卫星': '商业航天',
            }
            for kw, topic in policy_keywords.items():
                if kw.lower() in combined.lower():
                    policy_score += 5
                    break
            policy_score = min(policy_score, 10)
            
            sec_total = min(sec_score + market_heat + board_score + spot_score + policy_score, 50)
            
            # 财务评分
            fin_score, fin_details = evaluate_fundamental(code)
            
            # 技术评分（简化：用换手率+连板数估算）
            tech_score = 0
            tech_detail = []
            if c['turnover_rate'] > 20: tech_score = 15
            elif c['turnover_rate'] > 10: tech_score = 12
            elif c['turnover_rate'] > 5: tech_score = 10
            elif c['turnover_rate'] > 3: tech_score = 8
            elif c['turnover_rate'] > 1: tech_score = 5
            
            if c['board_times'] >= 3: tech_score += 5
            elif c['board_times'] == 2: tech_score += 3
            
            tech_score = min(tech_score, 25)
            
            total_score = sec_total + fin_score + tech_score
            
            scored.append({
                'code': code,
                'name': name,
                'total_score': total_score,
                'board_times': c['board_times'],
                'industry': industry,
                'turnover_rate': c['turnover_rate'],
                'source': c['source'],
                'fin_score': fin_score,
                'tech_score': tech_score,
                'sec_score': sec_total,
                'sec_detail': sec_detail,
            })
        
        scored.sort(key=lambda x: x['total_score'], reverse=True)
        picks = scored[:8]  # 每日精选前8
        
        # 查次日表现
        next_limit_raw = db.execute("""
            SELECT code, change_pct
            FROM daily_limit_up
            WHERE trade_date=%s
        """, (next_date,)).fetchall()
        next_limit = {r[0]: r[1] for r in next_limit_raw}
        
        stats['days'] += 1
        
        print(f"\n{'─'*60}")
        print(f"📅 {date_readable}(涨停{total_limit_up}只) 推荐8只→{next_readable}")
        
        day_limit_up = 0
        day_positive = 0
        day_negative = 0
        
        for pick in picks:
            code = pick['code']
            name = pick['name']
            board = pick['board_times']
            
            # 查次日涨跌幅
            chg = None
            if code in next_limit:
                chg = round(next_limit[code], 2)
            elif code in kline_cache and next_date_fmt in kline_cache[code]:
                chg = kline_cache[code][next_date_fmt]
            
            if chg is not None:
                stats['total_picks'] += 1
                board_stats[board]['total'] += 1
                board_stats[board]['return_sum'] += chg
                stats['return_sum'] += chg
                
                if chg >= 9.5:
                    stats['limit_up'] += 1
                    board_stats[board]['limit_up'] += 1
                    day_limit_up += 1
                elif chg <= -9.5:
                    stats['limit_down'] += 1
                
                if chg > 0:
                    stats['positive'] += 1
                    board_stats[board]['positive'] += 1
                    day_positive += 1
                elif chg < 0:
                    stats['negative'] += 1
                    day_negative += 1
                else:
                    stats['zero'] += 1
                
                if chg >= 5 and chg < 9.5: stats['up_5pct'] += 1
                if chg >= 3: stats['up_3pct'] += 1
                if chg <= -3 and chg > -9.5: stats['down_3pct'] += 1
                if chg <= -5: stats['down_5pct'] += 1
            
            flag = '🚀' if (chg and chg >= 9.5) else '🟢' if (chg and chg > 0) else '🔴' if (chg and chg < 0) else '⚪'
            chg_str = f"{chg:+.2f}%" if chg is not None else "无数据"
            print(f"  {flag} {name}({code}) {board}板 | {pick['total_score']}分(情绪{pick['sec_score']}+财务{pick['fin_score']}+技术{pick['tech_score']}) | 次日{chg_str}")
        
        print(f"  📊 结果: 涨停{day_limit_up} 红盘{day_positive} 绿盘{day_negative}")
        
        for pick in picks:
            next_chg = (
                round(next_limit[pick['code']], 2) if pick['code'] in next_limit
                else (kline_cache.get(pick['code'], {}).get(next_date_fmt))
            )
            all_picks.append({
                'date': date_readable,
                'next_date': next_readable,
                'code': pick['code'],
                'name': pick['name'],
                'board_times': pick['board_times'],
                'industry': pick['industry'],
                'total_score': pick['total_score'],
                'turnover_rate': pick['turnover_rate'],
                'source': pick['source'],
                'next_day_change': next_chg,
            })
    
    # 汇总
    total = stats['total_picks']
    print(f"\n{'='*70}")
    print(f"  📊 汇总统计（每日精选8只口径）")
    print(f"{'='*70}")
    print(f"  回溯区间: {trade_dates_effective[0]} ~ {trade_dates_effective[-1]}")
    print(f"  交易日数: {stats['days']}")
    print(f"  总精选股: {total}（{stats['days']}天 × 8只 = {stats['days']*8}，少的是有重复或数据缺失）")
    
    if total > 0:
        print(f"\n  📈 次日涨跌分布:")
        print(f"    🚀 涨停:   {stats['limit_up']} ({stats['limit_up']/total*100:.1f}%)")
        print(f"    📈 +5%+:   {stats['up_5pct']} ({stats['up_5pct']/total*100:.1f}%)")
        print(f"    🟢 红盘:   {stats['positive']} ({stats['positive']/total*100:.1f}%)")
        print(f"    ⚪ 平盘:   {stats['zero']} ({stats['zero']/total*100:.1f}%)")
        print(f"    🔴 绿盘:   {stats['negative']} ({stats['negative']/total*100:.1f}%)")
        print(f"    📉 -3%~-5%: {stats['down_3pct']} ({stats['down_3pct']/total*100:.1f}%)")
        print(f"    📉 -5%+:   {stats['down_5pct']} ({stats['down_5pct']/total*100:.1f}%)")
        print(f"    💀 跌停:   {stats['limit_down']} ({stats['limit_down']/total*100:.1f}%)")
        
        avg_return = stats['return_sum'] / total
        print(f"\n  💰 平均次日涨幅: {avg_return:+.2f}%")
    
    print(f"\n  📈 按连板数:")
    for board in sorted(board_stats.keys()):
        bg = board_stats[board]
        avg = bg['return_sum'] / bg['total'] if bg['total'] > 0 else 0
        print(f"    {board}板: {bg['total']}只 | 涨停{bg['limit_up']}({bg['limit_up']/bg['total']*100:.1f}%) | 红盘{bg['positive']}({bg['positive']/bg['total']*100:.1f}%) | 均收{avg:+.2f}%")
    
    # 保存
    output = {
        'analysis_range': f'{trade_dates_effective[0]}~{trade_dates_effective[-1]}',
        'effective_trade_days': stats['days'],
        'picks_per_day': 8,
        'stats': dict(stats),
        'board_stats': {str(k): dict(v) for k, v in board_stats.items()},
        'entries': all_picks,
    }
    
    with open(OUTPUT_PATH, 'w') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n  详细数据: {OUTPUT_PATH}")

if __name__ == '__main__':
    main()
