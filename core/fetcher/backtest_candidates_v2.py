"""
候选股回溯分析 v2 - 更准确版
排除数据重复问题，增加次日涨跌幅统计
"""
import json
from datetime import datetime
from collections import defaultdict

from dao import get_db

def main():
    db = get_db()
    
    # 获取所有交易日
    all_dates = [r[0] for r in db.execute('SELECT DISTINCT trade_date FROM daily_limit_up ORDER BY trade_date').fetchall()]
    
    # 检查数据质量 - 每天的涨停数量
    print(f"={'='*60}=")
    print(f"  第一步：数据质量检查")
    print(f"={'='*60}=")
    date_counts = {}
    for d in all_dates:
        cnt = db.fetchone('SELECT COUNT(*) as cnt FROM daily_limit_up WHERE trade_date=%s', (d,))['cnt']
        date_counts[d] = cnt
        print(f"  {d}: {cnt}只涨停")
    
    # 如有重复数据（同一天涨跌数据完全相同），标记
    dup_dates = set()
    prev_data = None
    prev_date = None
    for d in all_dates:
        data = db.execute('SELECT code, change_pct, board_times, turnover_rate, industry FROM daily_limit_up WHERE trade_date=%s ORDER BY code', (d,)).fetchall()
        if data == prev_data:
            print(f"  ⚠️ {d} 数据与 {prev_date} 完全相同！")
            dup_dates.add(d)
        prev_data = data
        prev_date = d
    
    # 只取真实交易日（去重）
    real_trade_dates = [d for d in all_dates if d not in dup_dates]
    print(f"\n  有效交易日数: {len(real_trade_dates)} ({len(dup_dates)}天重复已排除)")
    
    # 项目启动日期 20260428
    start_date = '20260428'
    trade_dates = [d for d in real_trade_dates if d >= start_date]
    
    print(f"\n={'='*60}=")
    print(f"  第二步：候选股回溯 ({trade_dates[0]} ~ {trade_dates[-1]})")
    print(f"  交易日数: {len(trade_dates)}")
    print(f"={'='*60}=")
    
    all_entries = []
    stats = {
        'total_candidates': 0,
        'next_limit_up': 0,      # 次日涨停（在涨停表中且涨幅>=9.5%）
        'next_up_5pct': 0,       # 次日大涨5%+
        'next_up_3pct': 0,       # 次日上涨3%+
        'next_positive': 0,      # 次日红盘
        'next_negative': 0,      # 次日绿盘
        'next_down_5pct': 0,     # 次日大跌5%+
        'next_limit_down': 0,    # 次日跌停
        'no_limit_up_next_day': 0, # 次日不在涨停表中（无法判断）
    }
    
    # 实时行情缓存（避免重复请求）
    daily_cache = {}
    
    for i, trade_date in enumerate(trade_dates):
        # 获取当天涨停数据
        rows = db.execute("""
            SELECT code, name, board_times, turnover_rate, industry, change_pct
            FROM daily_limit_up
            WHERE trade_date = %s
            ORDER BY code
        """, (trade_date,)).fetchall()
        
        if not rows:
            continue
        
        date_readable = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"
        
        # 统计板块热点
        industry_count = defaultdict(int)
        stock_info = []
        for r in rows:
            code, name, board_times, turnover_rate, industry, change_pct = r
            industry_count[industry] += 1
            stock_info.append({
                'code': code, 'name': name, 'board_times': board_times,
                'turnover_rate': turnover_rate or 0, 'industry': industry,
                'change_pct': change_pct,
            })
        
        hot_industries = sorted(industry_count.items(), key=lambda x: x[1], reverse=True)
        total_limit_up = len(rows)
        
        # === 选股逻辑 ===
        candidates = []
        seen = set()
        
        # 路径A：涨停板1-3板，换手1-50%，排除688/300
        for s in stock_info:
            if s['board_times'] > 3:
                continue
            if s['turnover_rate'] < 1 or s['turnover_rate'] > 50:
                continue
            if s['code'].startswith('688') or s['code'].startswith('300'):
                continue
            
            key = f"{s['code']}_{s['name']}"
            if key not in seen:
                seen.add(key)
                candidates.append({
                    'code': s['code'], 'name': s['name'],
                    'board_times': s['board_times'],
                    'turnover_rate': round(s['turnover_rate'], 2),
                    'industry': s['industry'],
                    'source': '涨停热点',
                })
        
        # 路径B：热点板块跟风
        for industry, cnt in hot_industries:
            if cnt >= 2:
                for s in stock_info:
                    if s['industry'] == industry and s['board_times'] <= 2:
                        key = f"{s['code']}_{s['name']}"
                        if key not in seen and 3 <= s['turnover_rate'] <= 30:
                            if not (s['code'].startswith('688') or s['code'].startswith('300')):
                                seen.add(key)
                                candidates.append({
                                    'code': s['code'], 'name': s['name'],
                                    'board_times': s['board_times'],
                                    'turnover_rate': round(s['turnover_rate'], 2),
                                    'industry': industry,
                                    'source': '板块跟风',
                                })
        
        if not candidates:
            continue
        
        # 下一个交易日
        if i + 1 < len(trade_dates):
            next_date = trade_dates[i + 1]
        else:
            next_date = None
        
        # 查次日表现
        next_day_results = {}
        if next_date:
            next_day_data_raw = db.execute("""
                SELECT code, change_pct, name, price, turnover_rate, board_times
                FROM daily_limit_up
                WHERE trade_date = %s
            """, (next_date,)).fetchall()
            next_day_data = {}
            for r in next_day_data_raw:
                code, change_pct, name, price, turnover_rate, board_times = r
                next_day_data[code] = {
                    'change_pct': change_pct, 'name': name, 'price': price,
                    'turnover_rate': turnover_rate, 'board_times': board_times,
                }
            
            for c in candidates:
                code = c['code']
                if code in next_day_data:
                    nd = next_day_data[code]
                    next_day_results[code] = {
                        'change_pct': round(nd['change_pct'], 2) if nd['change_pct'] else None,
                        'is_limit_up': nd['change_pct'] >= 9.5 if nd['change_pct'] else False,
                        'is_limit_down': nd['change_pct'] <= -9.5 if nd['change_pct'] else False,
                        'found_in_limit_up': True,
                        'price': nd['price'],
                        'turnover_rate': round(nd['turnover_rate'], 2) if nd['turnover_rate'] else None,
                        'board_times': nd['board_times'],
                    }
                else:
                    next_day_results[code] = {
                        'change_pct': None,
                        'is_limit_up': False,
                        'is_limit_down': False,
                        'found_in_limit_up': False,
                    }
        
        # 统计
        limit_up_n = sum(1 for v in next_day_results.values() if v.get('is_limit_up'))
        stats['next_limit_up'] += limit_up_n
        no_data = sum(1 for v in next_day_results.values() if not v.get('found_in_limit_up'))
        stats['no_limit_up_next_day'] += no_data
        stats['total_candidates'] += len(candidates)
        
        positive = sum(1 for v in next_day_results.values() if v.get('change_pct') is not None and v['change_pct'] > 0)
        negative = sum(1 for v in next_day_results.values() if v.get('change_pct') is not None and v['change_pct'] < 0)
        up_5 = sum(1 for v in next_day_results.values() if v.get('change_pct') is not None and v['change_pct'] >= 5)
        up_3 = sum(1 for v in next_day_results.values() if v.get('change_pct') is not None and v['change_pct'] >= 3)
        down_5 = sum(1 for v in next_day_results.values() if v.get('change_pct') is not None and v['change_pct'] <= -5)
        limit_down = sum(1 for v in next_day_results.values() if v.get('is_limit_down'))
        stats['next_positive'] += positive
        stats['next_negative'] += negative
        stats['next_up_5pct'] += up_5
        stats['next_up_3pct'] += up_3
        stats['next_down_5pct'] += down_5
        stats['next_limit_down'] += limit_down
        
        # 打印摘要
        next_readable = f"{next_date[:4]}-{next_date[4:6]}-{next_date[6:]}" if next_date else "无"
        print(f"\n{'─'*60}")
        print(f"📅 {date_readable} 候选股 {len(candidates)}只 (涨停{total_limit_up}只)")
        print(f"  热点: {' | '.join([f'{ind}({cnt})' for ind, cnt in hot_industries[:3]])}")
        
        # 按连板分组
        board_counts = defaultdict(list)
        for c in candidates:
            board_counts[c['board_times']].append(c)
        
        for board in sorted(board_counts.keys()):
            group = board_counts[board]
            limit_cnt = sum(1 for c in group if next_day_results.get(c['code'], {}).get('is_limit_up'))
            total_g = len(group)
            print(f"  {board}板 {total_g}只 → 次日涨停{limit_cnt}")
        
        # 打印所有候选及次日表现
        for c in candidates:
            nd = next_day_results.get(c['code'], {})
            change = nd.get('change_pct')
            
            if change is not None:
                flag = '🚀' if nd.get('is_limit_up') else '🟢' if change > 0 else '🔴'
                chg_str = f"{change:+.2f}%"
                if nd.get('is_limit_up'):
                    chg_str += " ✨涨停"
                elif nd.get('is_limit_down'):
                    chg_str += " 💀跌停"
                elif change >= 5:
                    chg_str += " 大涨"
                elif change <= -5:
                    chg_str += " 大跌⚠️"
            else:
                flag = '⚪'
                chg_str = '无涨停数据'
            
            board_str = f"{c['board_times']}板" if c['board_times'] else ''
            print(f"  {flag} {c['name']}({c['code']}) {board_str} {c['industry']} | {chg_str}")
        
        print(f"  📊 次日({next_readable}): 涨停{limit_up_n}/{len(candidates)} | 红盘{positive} 绿盘{negative}")
        
        # 记录
        for c in candidates:
            nd = next_day_results.get(c['code'], {})
            all_entries.append({
                'date': date_readable,
                'trade_date': trade_date,
                'next_trade_date': next_date,
                'code': c['code'],
                'name': c['name'],
                'board_times': c['board_times'],
                'industry': c['industry'],
                'turnover_rate': c['turnover_rate'],
                'source': c['source'],
                'next_day_change': nd.get('change_pct'),
                'next_day_limit_up': nd.get('is_limit_up', False),
                'next_day_limit_down': nd.get('is_limit_down', False),
                'next_day_found': nd.get('found_in_limit_up', False),
                'next_day_price': nd.get('price'),
            })
    
    # === 汇总 ===
    total = stats['total_candidates']
    print(f"\n{'='*70}")
    print(f"  📊 汇总统计")
    print(f"{'='*70}")
    print(f"  回溯区间: {trade_dates[0]} ~ {trade_dates[-1]}")
    print(f"  有效交易日数: {len(trade_dates)}")
    print(f"  总候选股: {total} (平均每交易日~{total//len(trade_dates) if len(trade_dates) > 0 else 0}只)")
    
    if total > 0:
        # 能查到次日数据的样本
        known_next = sum(1 for e in all_entries if e['next_day_change'] is not None)
        known_next_limit = sum(1 for e in all_entries if e['next_day_limit_up'])
        known_next_pos = sum(1 for e in all_entries if e['next_day_change'] is not None and e['next_day_change'] > 0)
        known_next_neg = sum(1 for e in all_entries if e['next_day_change'] is not None and e['next_day_change'] < 0)
        known_next_5up = sum(1 for e in all_entries if e['next_day_change'] is not None and e['next_day_change'] >= 5)
        known_next_5down = sum(1 for e in all_entries if e['next_day_change'] is not None and e['next_day_change'] <= -5)
        
        print(f"\n  次日可查数据: {known_next}/{total} ({known_next/total*100:.1f}%)")
        print(f"  ※ 未查到=次日未涨停（表中只有涨停数据）")
        if known_next > 0:
            print(f"\n  📈 次日涨跌分布 (基于{known_next}只可查数据):")
            print(f"     🚀 涨停: {known_next_limit} ({known_next_limit/known_next*100:.1f}%)")
            print(f"     🟢 红盘(含涨停): {known_next_pos} ({known_next_pos/known_next*100:.1f}%)")
            print(f"     🔴 绿盘: {known_next_neg} ({known_next_neg/known_next*100:.1f}%)")
            print(f"     📈 大涨5%+: {known_next_5up}")
            print(f"     📉 大跌5%+: {known_next_5down}")
            
            # 涨停率（分母=总候选股，因为未在涨停表=大概率跌或平）
            print(f"\n  候选股次日涨停率 (基于全部{total}只): {known_next_limit}/{total} = {known_next_limit/total*100:.1f}%")
            print(f"  红盘率 (基于全部{total}只): {known_next_pos}/{total} = {known_next_pos/total*100:.1f}%")
    
    # 按连板数分类
    print(f"\n  📈 按连板数统计涨停率:")
    board_groups = defaultdict(list)
    for e in all_entries:
        board_groups[e['board_times']].append(e)
    for boards in sorted(board_groups.keys()):
        group = board_groups[boards]
        total_g = len(group)
        limit_g = sum(1 for e in group if e['next_day_limit_up'])
        pos_g = sum(1 for e in group if e['next_day_change'] is not None and e['next_day_change'] > 0)
        print(f"    {boards}板: {limit_g}/{total_g}={limit_g/total_g*100:.1f}%涨停 | 红盘{pos_g}/{total_g}={pos_g/total_g*100:.1f}%")
    
    # 按板块热度分类
    print(f"\n  📈 按板块热度统计:")
    # 用前一天的涨停数量来近似区分"强板块日"和"弱板块日"
    # 这个需要按日期维度再分析
    
    # 保存
    output = {
        'analysis_range': f'{trade_dates[0]}~{trade_dates[-1]}',
        'effective_trade_days': len(trade_dates),
        'stats': stats,
        'entries': all_entries,
    }
    output_path = 'os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))/backtest_candidates_v2.json'
    with open(output_path, 'w') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n  详细数据已保存到: {output_path}")

if __name__ == '__main__':
    main()
