"""
候选股回溯 v5 - 每日精选8只（简化评分版）
从 v3 的389只数据中按当天可用的因子评分排序，取前8
评分因子：板块热度(50%) + 连板数(30%) + 换手率(20%)
"""
import json
from collections import defaultdict, Counter

from dao import get_db

DB_PATH = 'os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))/stock_data.db'
INPUT_PATH = 'os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))/backtest_candidates_v3.json'
OUTPUT_PATH = 'os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))/backtest_candidates_v5.json'

def main():
    print(f"={'='*60}=")
    print(f"  每日精选8只 - 基于当天因子评分排序")
    print(f"={'='*60}=")
    
    # 加载v3完整数据
    with open(INPUT_PATH) as f:
        v3_data = json.load(f)
    
    db = get_db()
    
    # 交易日
    all_dates = [r[0] for r in db.execute('SELECT DISTINCT trade_date FROM daily_limit_up ORDER BY trade_date').fetchall()]
    prev_data = None
    dup_dates = set()
    for d in all_dates:
        data = db.execute('SELECT code, change_pct FROM daily_limit_up WHERE trade_date=%s ORDER BY code', (d,)).fetchall()
        if data == prev_data:
            dup_dates.add(d)
        prev_data = data
    real_dates = [d for d in all_dates if d not in dup_dates]
    trade_dates = [d for d in real_dates if d >= '20260428']
    trade_dates_effective = trade_dates[:-1]
    
    print(f"  交易日数: {len(trade_dates_effective)}")
    
    # 按日期分组v3数据
    date_entries = defaultdict(list)
    for e in v3_data['entries']:
        date_entries[e['date']].append(e)
    
    all_picks = []
    stats = {
        'days': 0, 'total_picks': 0,
        'limit_up': 0, 'positive': 0, 'negative': 0, 'zero': 0,
        'up_5pct': 0, 'up_3pct': 0, 'down_3pct': 0, 'down_5pct': 0, 'limit_down': 0,
        'return_sum': 0.0,
    }
    board_stats = defaultdict(lambda: {'total': 0, 'limit_up': 0, 'positive': 0, 'return_sum': 0.0})
    score_stats = {
        'high_score_picks': [],     # >=60分
        'mid_score_picks': [],      # 40-59分
        'low_score_picks': [],      # <40分
    }
    
    for trade_date in trade_dates_effective:
        date_repr = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"
        entries = date_entries.get(date_repr, [])
        
        if not entries:
            continue
        
        idx = trade_dates.index(trade_date)
        next_date = trade_dates[idx + 1]
        next_date_fmt = f"{next_date[:4]}-{next_date[4:6]}-{next_date[6:]}"
        
        # 获取当天涨停总数（用于板块热度评分）
        total_zt = db.fetchone('SELECT COUNT(*) as cnt FROM daily_limit_up WHERE trade_date=%s', (trade_date,))['cnt']
        
        # 获取当天板块热度
        hot_rows = db.execute('''
            SELECT industry, COUNT(*) as cnt 
            FROM daily_limit_up 
            WHERE trade_date=%s 
            GROUP BY industry 
            ORDER BY cnt DESC
        ''', (trade_date,)).fetchall()
        hot_industries = {r[0]: r[1] for r in hot_rows}
        
        # 给每只候选股评分
        scored = []
        for e in entries:
            code = e['code']
            name = e['name']
            board = e['board_times']
            turnover = e['turnover_rate']
            industry = e['industry']
            nd = e['next_day']
            chg = nd.get('change_pct')
            
            if chg is None:
                continue
            
            # 1. 板块热度评分 (0-40分)
            sec_score = 0
            cnt = hot_industries.get(industry, 0)
            if cnt >= 10: sec_score = 40
            elif cnt >= 7: sec_score = 35
            elif cnt >= 5: sec_score = 28
            elif cnt >= 3: sec_score = 20
            elif cnt >= 2: sec_score = 12
            elif cnt >= 1: sec_score = 5
            
            # 2. 连板评分 (0-30分)
            board_score = 0
            if board == 1: board_score = 10
            elif board == 2: board_score = 20
            elif board == 3: board_score = 30
            
            # 3. 换手评分 (0-30分) - 适中最好
            turnover_score = 0
            if 5 <= turnover <= 20: turnover_score = 30
            elif 3 <= turnover < 5 or 20 < turnover <= 30: turnover_score = 22
            elif 1 <= turnover < 3 or 30 < turnover <= 40: turnover_score = 12
            elif turnover > 40: turnover_score = 5
            
            total_score = sec_score + board_score + turnover_score
            
            scored.append({
                'code': code, 'name': name, 'board': board,
                'turnover': turnover, 'industry': industry,
                'sec_score': sec_score, 'board_score': board_score,
                'turnover_score': turnover_score,
                'total_score': total_score,
                'next_chg': chg,
                'is_limit_up': nd.get('is_limit_up', False),
                'is_limit_down': nd.get('is_limit_down', False),
            })
        
        # 按总分排序，取前8
        scored.sort(key=lambda x: x['total_score'], reverse=True)
        picks = scored[:8]
        
        stats['days'] += 1
        print(f"\n{'─'*60}")
        print(f"📅 {date_repr}(涨停{total_zt}只) 8只精选→{next_date_fmt}")
        print(f"  排序规则: 板块热度(40)+连板(30)+换手(30) ≈ 满分100分")
        
        day_limit_up = 0
        day_positive = 0
        day_negative = 0
        
        for i, p in enumerate(picks, 1):
            chg = p['next_chg']
            stats['total_picks'] += 1
            board_stats[p['board']]['total'] += 1
            board_stats[p['board']]['return_sum'] += chg
            stats['return_sum'] += chg
            
            flag = '🚀' if p['is_limit_up'] else '🟢' if chg > 0 else '🔴' if chg < 0 else '⚪'
            chg_str = f"{chg:+.2f}%"
            
            if p['is_limit_up']:
                stats['limit_up'] += 1
                board_stats[p['board']]['limit_up'] += 1
                day_limit_up += 1
            elif p['is_limit_down']:
                stats['limit_down'] += 1
            
            if chg > 0:
                stats['positive'] += 1
                board_stats[p['board']]['positive'] += 1
                day_positive += 1
            elif chg < 0:
                stats['negative'] += 1
                day_negative += 1
            else:
                stats['zero'] += 1
            
            if 5 <= chg < 9.5: stats['up_5pct'] += 1
            if chg >= 3: stats['up_3pct'] += 1
            if -5 < chg <= -3: stats['down_3pct'] += 1
            if chg <= -5: stats['down_5pct'] += 1
            
            # 按分数段记录
            category = score_stats['high_score_picks'] if p['total_score'] >= 60 else \
                       score_stats['mid_score_picks'] if p['total_score'] >= 40 else \
                       score_stats['low_score_picks']
            category.append(p)
            
            print(f"  {i}. {flag} {p['name']}({p['code']}) {p['board']}板 | {p['total_score']}分(板块{p['sec_score']}+连板{p['board_score']}+换手{p['turnover_score']}) | ->{chg_str}")
        
        print(f"  📊 次日概括: 涨停{day_limit_up} 红盘{day_positive} 绿盘{day_negative}")
        
        for p in picks:
            all_picks.append({
                'date': date_repr,
                'next_date': next_date_fmt,
                'code': p['code'],
                'name': p['name'],
                'board_times': p['board'],
                'turnover_rate': p['turnover'],
                'industry': p['industry'],
                'total_score': p['total_score'],
                'next_day_change': p['next_chg'],
                'is_limit_up': p['is_limit_up'],
            })
    
    # 汇总
    total = stats['total_picks']
    print(f"\n{'='*70}")
    print(f"  📊 汇总统计（每日精选8只口径）")
    print(f"{'='*70}")
    print(f"  回溯区间: {trade_dates_effective[0]} ~ {trade_dates_effective[-1]}")
    print(f"  交易日数: {stats['days']}天 × 8只 = {stats['days'] * 8}次选股")
    print(f"  有效统计: {total}只")
    
    if total > 0:
        print(f"\n  📈 次日涨跌分布:")
        print(f"    🚀 涨停:   {stats['limit_up']}  ({stats['limit_up']/total*100:.1f}%)")
        print(f"    📈 +5%+:   {stats['up_5pct']}  ({stats['up_5pct']/total*100:.1f}%)")
        print(f"    🟢 红盘:   {stats['positive']}  ({stats['positive']/total*100:.1f}%)")
        print(f"    ⚪ 平盘:   {stats['zero']}  ({stats['zero']/total*100:.1f}%)")
        print(f"    🔴 绿盘:   {stats['negative']}  ({stats['negative']/total*100:.1f}%)")
        print(f"    📉 -3%~-5%: {stats['down_3pct']}  ({stats['down_3pct']/total*100:.1f}%)")
        print(f"    📉 -5%+:   {stats['down_5pct']}  ({stats['down_5pct']/total*100:.1f}%)")
        print(f"    💀 跌停:   {stats['limit_down']}  ({stats['limit_down']/total*100:.1f}%)")
        
        avg_return = stats['return_sum'] / total
        print(f"\n  💰 平均次日涨幅: {avg_return:+.2f}%")
        
        # 与v3全量对比
        print(f"\n  📊 与前8略筛选版对比:")
        print(f"    v3 全量389只: 涨停率19.5% 红盘率62.2% 均收+2.06%")
        print(f"    v5 精选40只: 涨停率{stats['limit_up']/total*100:.1f}% 红盘率{stats['positive']/total*100:.1f}% 均收{avg_return:+.2f}%")
    
    # 按连板
    print(f"\n  📈 按连板数:")
    for board in sorted(board_stats.keys()):
        bg = board_stats[board]
        avg = bg['return_sum'] / bg['total'] if bg['total'] > 0 else 0
        print(f"    {board}板: {bg['total']}只 | 涨停{bg['limit_up']}({bg['limit_up']/bg['total']*100:.1f}%) | 红盘{bg['positive']}({bg['positive']/bg['total']*100:.1f}%) | 均收{avg:+.2f}%")
    
    # 按分数段
    print(f"\n  📈 按分数段:")
    for label, group in [('>=60分(高分)', score_stats['high_score_picks']),
                          ('40-59分(中分)', score_stats['mid_score_picks']),
                          ('<40分(低分)', score_stats['low_score_picks'])]:
        if group:
            n = len(group)
            limit = sum(1 for p in group if p['is_limit_up'])
            pos = sum(1 for p in group if p['next_chg'] > 0)
            neg = sum(1 for p in group if p['next_chg'] < 0)
            avg = sum(p['next_chg'] for p in group) / n
            print(f"    {label}: {n}只 | 涨停{limit/n*100:.1f}% | 红盘{pos/n*100:.1f}% | 绿盘{neg/n*100:.1f}% | 均收{avg:+.2f}%")
    
    # 保存
    output = {
        'analysis_range': f'{trade_dates_effective[0]}~{trade_dates_effective[-1]}',
        'days': stats['days'],
        'picks_per_day': 8,
        'scoring_method': '板块热度(40分)+连板数(30分)+换手率(30分)',
        'stats': dict(stats),
        'board_stats': {str(k): dict(v) for k, v in board_stats.items()},
        'entries': all_picks,
    }
    
    with open(OUTPUT_PATH, 'w') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n  数据已保存: {OUTPUT_PATH}")

if __name__ == '__main__':
    main()
