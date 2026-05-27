"""
每日选股推荐脚本 - 收盘后运行
输出明日短线交易候选标的
"""
import sys
import os
import time
import json
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())

def pick_stocks():
    """选股主逻辑：业绩增长+技术面+板块热点"""
    from stock_analysis_api import StockDataFetcher
    from data_store import QuoteStore
    from limit_up_analysis import LimitUpAnalyzer
    
    f = StockDataFetcher()
    store = QuoteStore()
    zt = LimitUpAnalyzer()
    
    results = {}
    
    # 1. 今日涨停数据 → 识别板块热点
    logger.info("分析今日涨停...")
    try:
        today_up = zt.get_today_limit_up()
        if today_up:
            # 统计板块分布
            industry_count = {}
            for s in today_up:
                ind = s.get('industry', '其他')
                industry_count[ind] = industry_count.get(ind, 0) + 1
            hot_industries = sorted(industry_count.items(), key=lambda x: x[1], reverse=True)[:5]
            results['hot_industries'] = hot_industries
            results['total_limit_up'] = len(today_up)
            logger.info(f"涨停{len(today_up)}只, 热点板块: {hot_industries}")
    except Exception as e:
        logger.warning(f"涨停分析失败: {e}")
    
    # 2. 连板梯队分析
    logger.info("分析连板梯队...")
    try:
        trackers = zt.get_continuous_trackers(min_days=1)
        if trackers:
            boards = sorted(trackers, key=lambda x: x.get('board_count', 1), reverse=True)[:10]
            results['trackers'] = boards
            logger.info(f"连板股: {[(t.get('name'), t.get('board_count')) for t in boards[:5]]}")
    except Exception as e:
        logger.warning(f"连板分析失败: {e}")
    
    # 3. 板块表现
    logger.info("分析板块表现...")
    try:
        sectors = store.get_sector_performances(rank_type='涨幅')
        if sectors:
            top_sectors = sorted(sectors, key=lambda x: x.get('change_pct', 0), reverse=True)[:5]
            results['top_sectors'] = top_sectors
            logger.info(f"最强板块: {[(s.get('sector_name',''), s.get('change_pct')) for s in top_sectors]}")
    except Exception as e:
        logger.warning(f"板块分析失败: {e}")
    
    # 4. 大盘状况
    logger.info("分析大盘...")
    try:
        summary = f.get_market_summary()
        results['market'] = summary
        if summary:
            logger.info(f"大盘: 上涨{summary.get('up_count',0)}只 下跌{summary.get('down_count',0)}只")
    except Exception as e:
        logger.warning(f"大盘分析失败: {e}")
    
    # 5. 选股候选
    logger.info("从业绩增长+涨停数据中筛选候选...")
    candidates = []
    
    # 从涨停板中挑首板/二板热门板块的
    if today_up:
        for s in today_up:
            code = str(s.get('code', ''))
            name = s.get('name', '')
            boards = s.get('board_times', 1)
            industry = s.get('industry', '')
            turnover = s.get('turnover_rate', 0)
            
            # 筛选条件：首板或二板，有板块效应，换手适中
            if boards <= 3 and 1 < turnover < 50:
                candidates.append({
                    'code': code,
                    'name': name,
                    'board_times': boards,
                    'industry': industry,
                    'turnover': turnover,
                    'type': '涨停板'
                })
    
    # 也加入业绩增长+今天没涨停但有异动的
    # (从昨日业绩数据中筛选)
    results['candidates'] = candidates[:10]
    
    return results

def format_recommendation(results):
    """格式化推荐结果"""
    if not results:
        return "数据不足，无法生成推荐"
    
    lines = []
    lines.append(f"📊 **{datetime.now().strftime('%Y-%m-%d')} 收盘选股**")
    lines.append("")
    
    # 大盘
    mk = results.get('market', {})
    if mk:
        rise = int(mk.get('up_count', 0))
        fall = int(mk.get('down_count', 0))
        flat = int(mk.get('flat_count', 0))
        total = rise + fall + flat
        lines.append(f"大盘: 涨{rise}跌{fall} (共{total}只)")
        lines.append("")
    
    # 热点板块
    if results.get('top_sectors'):
        lines.append("🔥 **板块热点**")
        for s in results['top_sectors']:
            lines.append(f"  {s.get('sector_name','?')}: {s.get('change_pct',0):+.2f}% (成交{s.get('amount',0)/1e8:.1f}亿)")
        lines.append("")
    
    # 涨停数据
    if results.get('total_limit_up'):
        lines.append(f"⚡ 今日涨停: **{results['total_limit_up']}只**")
        if results.get('hot_industries'):
            ind_str = ", ".join([f"{ind}({cnt}只)" for ind, cnt in results['hot_industries'][:3]])
            lines.append(f"   热点集中: {ind_str}")
        lines.append("")
    
    # 连板梯队
    if results.get('trackers'):
        lines.append("📈 **连板梯队**")
        for t in results['trackers'][:8]:
            status = "🔒" if t.get('is_alive') else "⚰️"
            lines.append(f"  {t.get('name','?')}({t.get('code','?')}) {t.get('board_count',1)}连板 {t.get('last_change_pct',0):+.2f}%")
        lines.append("")
    
    # 候选标的
    if results.get('candidates'):
        lines.append("🎯 **明日候选**")
        for i, c in enumerate(results['candidates'][:5], 1):
            lines.append(f"  {i}. {c['name']}({c['code']}) - {c['board_times']}板/{c['industry']} 换手{c['turnover']:.1f}%")
        lines.append("")
    
    lines.append("---")
    lines.append("_明日计划将在09:15开盘前更新_")
    
    return "\n".join(lines)

if __name__ == '__main__':
    logger.info("=== 开始选股 ===")
    results = pick_stocks()
    output = format_recommendation(results)
    print("\n" + output + "\n")
    
    # 保存到文件供web界面使用
    with open(os.path.join(os.path.dirname(__file__), 'daily_picks.json'), 'w') as f:
        json.dump(results, f, ensure_ascii=False, default=str, indent=2)
    logger.info("选股结果已保存到 daily_picks.json")
