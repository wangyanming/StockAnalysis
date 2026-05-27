"""
候选股回溯分析 - 从数据库还原每天候选清单，并追踪次日表现
"""
import json
import sys
from datetime import datetime, timedelta
from collections import defaultdict

from dao import get_db

def get_trade_date(date_str: str, direction: str = 'next') -> str:
    """
    获取下一个或上一个交易日（取数据库中最近的数据作为近似）
    因为数据库只有交易日的数据，所以按 trade_date 排序找最近邻
    """
    db = get_db()
    all_dates = [r[0] for r in db.execute('SELECT DISTINCT trade_date FROM daily_limit_up ORDER BY trade_date').fetchall()]
    
    if direction == 'next':
        for d in all_dates:
            if d > date_str:
                return d
    elif direction == 'prev':
        for d in reversed(all_dates):
            if d < date_str:
                return d
    return None

def simulate_picks_for_date(trade_date: str) -> list:
    """
    模拟选股逻辑，返回该收盘日产生的候选股列表
    匹配 daily_pick_v2.py 的选股逻辑：
    - 路径A: 涨停板中挑首板/二板，换手1-50%，排除688/300
    - 路径B: 热点板块（涨停≥2）内的1-2板跟风票，换手3-30%
    """
    db = get_db()
    
    # 1. 获取当天的涨停数据
    rows = db.execute("""
        SELECT code, name, board_times, turnover_rate, industry, seal_first_time, seal_last_time, change_pct
        FROM daily_limit_up
        WHERE trade_date = %s
    """, (trade_date,)).fetchall()
    
    if not rows:
        return []
    
    # 2. 统计板块热点
    industry_count = defaultdict(int)
    stock_info = []
    for r in rows:
        code, name, board_times, turnover_rate, industry, seal_first, seal_last, change_pct = r
        industry_count[industry] += 1
        stock_info.append({
            'code': code,
            'name': name,
            'board_times': board_times,
            'turnover_rate': turnover_rate,
            'industry': industry,
            'seal_first_time': seal_first,
            'seal_last_time': seal_last,
            'change_pct': change_pct,
        })
    
    total_limit_up = len(rows)
    hot_industries = sorted(industry_count.items(), key=lambda x: x[1], reverse=True)
    
    # 3. 路径A：涨停板选股
    candidates = []
    seen = set()
    
    for s in stock_info:
        code = s['code']
        name = s['name']
        boards = s['board_times']
        turnover = s['turnover_rate'] if s['turnover_rate'] else 0
        industry = s['industry']
        
        # 条件：1-3板
        if boards > 3:
            continue
        # 换手适中
        if turnover < 1 or turnover > 50:
            continue
        # 排除688和300
        if code.startswith('688') or code.startswith('300'):
            continue
        
        key = f'{code}_{name}'
        if key not in seen:
            seen.add(key)
            candidates.append({
                'code': code,
                'name': name,
                'board_times': boards,
                'turnover_rate': round(turnover, 2),
                'industry': industry,
                'source': '涨停热点',
            })
    
    # 4. 路径B：热点板块跟风票
    for industry, cnt in hot_industries:
        if cnt >= 2:  # 板块效应
            for s in stock_info:
                if s['industry'] == industry and s['board_times'] <= 2:
                    code = s['code']
                    name = s['name']
                    turnover = s['turnover_rate'] if s['turnover_rate'] else 0
                    key = f'{code}_{name}'
                    
                    if key not in seen and 3 <= turnover <= 30:
                        if not (code.startswith('688') or code.startswith('300')):
                            seen.add(key)
                            candidates.append({
                                'code': code,
                                'name': name,
                                'board_times': s['board_times'],
                                'turnover_rate': round(turnover, 2),
                                'industry': industry,
                                'source': '板块跟风',
                            })
    
    return candidates

def get_next_day_price(codes: list, trade_date: str, next_date: str) -> dict:
    """
    获取候选股在次日（next_date）的涨跌表现
    用 daily_limit_up 表来查，因为是涨停板数据，能查到当天涨停的信息
    但有些票没涨停也能查到，可以用 change_pct 字段
    """
    db = get_db()
    
    result = {}
    for item in codes:
        code = item['code']
        name = item['name']
        
        # 查次日涨停数据
        row = db.fetchone("""
            SELECT change_pct, board_times, price, turnover_rate
            FROM daily_limit_up
            WHERE trade_date = %s AND code = %s
        """, (next_date, code))
        
        if row:
            result[code] = {
                'name': name,
                'change_pct': round(row['change_pct'], 2) if row['change_pct'] else None,
                'is_limit_up': row['change_pct'] >= 9.5 if row['change_pct'] else False,
                'board_times': row['board_times'],
                'price': row['price'],
                'turnover_rate': round(row['turnover_rate'], 2) if row['turnover_rate'] else None,
                'found_in_limit_up': True,
            }
        else:
            # 不在涨停表中，尝试从 stock_quotes 或 index_quotes 拿
            # 但 stock_quotes 只有实时数据，没有历史
            result[code] = {
                'name': name,
                'change_pct': None,
                'is_limit_up': False,
                'found_in_limit_up': False,
                'note': '次日未涨停',
            }
    
    return result

def main():
    # 项目启动日期
    start_date = '20260427'  # 从有数据开始
    end_date = '20260508'    # 到最新数据
    
    db = get_db()
    all_dates = [r[0] for r in db.execute('SELECT DISTINCT trade_date FROM daily_limit_up ORDER BY trade_date').fetchall()]
    
    # 只取 start_date ~ end_date 之间的交易日
    trade_dates = [d for d in all_dates if start_date <= d <= end_date]
    
    print(f"={'='*60}=")
    print(f"  候选股回溯分析 ({start_date} ~ {end_date})")
    print(f"  交易日数: {len(trade_dates)}")
    print(f"={'='*60}=")
    
    all_candidates = []
    total_stats = {
        'days': 0,
        'total_candidates': 0,
        'picked_again': 0,       # 次日继续涨停/再次入选
        'limit_up_next': 0,      # 次日涨停
        'missed_limit_up': 0,    # 候选股次日没涨停但有涨幅
        'no_data': 0,
    }
    
    for i, trade_date in enumerate(trade_dates):
        # 找下一个交易日
        if i + 1 < len(trade_dates):
            next_date = trade_dates[i + 1]
        else:
            next_date = None
        
        candidates = simulate_picks_for_date(trade_date)
        
        if not candidates:
            continue
        
        total_stats['days'] += 1
        total_stats['total_candidates'] += len(candidates)
        
        # 查次日表现
        if next_date:
            next_perf = get_next_day_price(candidates, trade_date, next_date)
        else:
            next_perf = {}
        
        # 统计
        limit_up_next = sum(1 for v in next_perf.values() if v.get('is_limit_up'))
        total_stats['limit_up_next'] += limit_up_next
        
        no_data = sum(1 for v in next_perf.values() if v.get('change_pct') is None and not v.get('found_in_limit_up'))
        total_stats['no_data'] += no_data
        
        # 输出
        date_readable = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"
        print(f"\n{'─'*60}")
        print(f"📅 {date_readable} 候选股 ({len(candidates)}只)")
        
        for c in candidates:
            perf = next_perf.get(c['code'], {})
            change = perf.get('change_pct')
            
            if change is not None:
                flag = '🟢' if change >= 0 else '🔴'
                if perf.get('is_limit_up'):
                    flag = '🚀'
                    note = f"次日涨停+{change:.2f}%"
                elif change > 5:
                    note = f"次日大涨+{change:.2f}%"
                elif change > 2:
                    note = f"次日上涨+{change:.2f}%"
                elif change > 0:
                    note = f"次日微涨+{change:.2f}%"
                elif change > -3:
                    note = f"次日微跌{change:.2f}%"
                elif change > -5:
                    note = f"次日下跌{change:.2f}%"
                else:
                    note = f"次日大跌{change:.2f}%⚠️"
            elif perf.get('found_in_limit_up') and perf.get('change_pct') is not None:
                change = perf['change_pct']
                if change >= 0:
                    flag = '🟢'
                    note = f"次日{change:+.2f}% (涨停表中)"
                else:
                    flag = '🔴'
                    note = f"次日{change:.2f}% (涨停表中)"
            else:
                flag = '⚪'
                note = '次日未涨停/数据不足'
            
            boards_str = f"{c['board_times']}板" if c['board_times'] else ''
            print(f"  {flag} {c['name']}({c['code']}) | {boards_str} {c['industry']} | 换手{c['turnover_rate']:.1f}% | {c['source']}")
            print(f"     → {note}")
            
            entry = {
                'date': date_readable,
                'trade_date': trade_date,
                'next_trade_date': next_date,
                'code': c['code'],
                'name': c['name'],
                'board_times': c['board_times'],
                'industry': c['industry'],
                'turnover_rate': c['turnover_rate'],
                'source': c['source'],
                'next_day_change': change,
                'next_day_limit_up': perf.get('is_limit_up', False),
                'next_day_found': perf.get('found_in_limit_up', False),
            }
            all_candidates.append(entry)
        
        # 免重复输出
        if next_date:
            next_readable = f"{next_date[:4]}-{next_date[4:6]}-{next_date[6:]}"
            tracked = len(candidates)
            print(f"\n  📊 次日({next_readable})表现: 涨停{limit_up_next}/{tracked}")
    
    # 汇总
    print(f"\n{'='*60}")
    print(f"📊 汇总统计")
    print(f"{'='*60}")
    
    total = total_stats['total_candidates']
    limit_next = total_stats['limit_up_next']
    
    if total > 0:
        limit_rate = limit_next / total * 100
    else:
        limit_rate = 0
    
    print(f"  分析交易日: {total_stats['days']}")
    print(f"  总候选股: {total}")
    print(f"  次日涨停: {limit_next} ({limit_rate:.1f}%)")
    print(f"  次日数据不足: {total_stats['no_data']}")
    
    # 按板块/连板数分类统计
    if all_candidates:
        print(f"\n  📈 按连板数统计次日涨停率:")
        board_groups = defaultdict(list)
        for c in all_candidates:
            board_groups[c['board_times']].append(c)
        
        for boards in sorted(board_groups.keys()):
            group = board_groups[boards]
            total_g = len(group)
            limit_g = sum(1 for c in group if c['next_day_limit_up'])
            print(f"    {boards}板: {limit_g}/{total_g} = {limit_g/total_g*100:.1f}% 涨停率")
        
        print(f"\n  📈 按来源统计:")
        source_groups = defaultdict(list)
        for c in all_candidates:
            source_groups[c['source']].append(c)
        
        for src in sorted(source_groups.keys()):
            group = source_groups[src]
            total_g = len(group)
            limit_g = sum(1 for c in group if c['next_day_limit_up'])
            print(f"    {src}: {limit_g}/{total_g} = {limit_g/total_g*100:.1f}% 涨停率")
    
    # 所有数据保存到 JSON
    output = {
        'analysis_range': f'{start_date}~{end_date}',
        'total_trade_days': len(trade_dates),
        'stats': total_stats,
        'entries': all_candidates,
    }
    
    output_path = '/Users/wangyanming/workspace/StockAnalysis/backtest_candidates_output.json'
    with open(output_path, 'w') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n  详细数据已保存到: {output_path}")
    
    return all_candidates, total_stats

if __name__ == '__main__':
    main()
