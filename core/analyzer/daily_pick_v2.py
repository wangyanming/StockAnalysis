"""
每日选股推荐 V4 - 综合量化评分体系
收盘后运行，输出明日短线交易候选标的

v2.0 (2026-05-25) 候选池构建重写：
- 涨停回踩组：近5天有过首板涨停 → 今天缩量回调不破起涨点 → 可低吸
- 区间潜伏组：底部低位 → 趋势初成（阳线多、量能温和放大）→ 等回踩
"""
import sys
import os
import time
import json
import logging
from datetime import datetime, timedelta

from utils.logger import setup_logger
logger = setup_logger("daily_pick_v2")

os.chdir(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.getcwd())

from utils.dao import get_db as _get_db
from core.fetcher.limit_up_analysis import LimitUpAnalyzer
from core.analyzer.scorer import check_market_status, score_candidate, format_score_report


def _log_timing(t_start: float, label: str) -> None:
    """打耗时日志"""
    elapsed = time.time() - t_start
    logger.info(f"  [TIMING] {label}: {elapsed:.1f}s")


def pick_stocks_v2():
    """
    新版选股逻辑：涨停发现热点 → 基本面筛选 → 综合评分 → 风险过滤
    """
    from core.analyzer.scorer import fetch_sina_quote

    _T_START = time.time()
    zt = LimitUpAnalyzer()

    results = {}

    # 1. 大盘环境
    logger.info("========== V2选股 ==========")
    logger.info("1. 大盘环境判断...")
    market = check_market_status()
    results['market'] = market

    logger.info(f"大盘: {market['status']} | {market['reason']}")
    _log_timing(_T_START, "大盘环境判断")

    # 2. 涨停数据分析
    logger.info("2. 涨停板分析...")
    try:
        today_up = zt.get_today_limit_up()
        if today_up:
            seen = {}
            unique_up = []
            for s in today_up:
                code = s['code']
                if code not in seen or s['board_times'] > seen[code]['board_times']:
                    seen[code] = s
                    unique_up.append(s)
            today_up = unique_up

            industry_count = {}
            for s in today_up:
                ind = s.get('industry', '其他')
                industry_count[ind] = industry_count.get(ind, 0) + 1
            hot_industries = sorted(industry_count.items(), key=lambda x: x[1], reverse=True)[:5]
            results['hot_industries'] = hot_industries
            results['total_limit_up'] = len(today_up)
            logger.info(f"总涨停{len(today_up)}只 | 热点: {hot_industries[:3]}")
    except Exception as e:
        logger.warning(f"涨停分析失败: {e}")
    _log_timing(_T_START, "涨停数据分析")

    # 3. 连板梯队
    logger.info("3. 连板梯队...")
    try:
        trackers = zt.get_continuous_trackers(min_days=1)
        if trackers:
            boards = sorted(trackers, key=lambda x: x.get('board_count', 1), reverse=True)[:10]
            results['trackers'] = boards
            logger.info(f"连板: {[(t.get('name'), t.get('board_count')) for t in boards[:5]]}")
    except Exception as e:
        logger.warning(f"连板分析失败: {e}")
    _log_timing(_T_START, "连板梯队分析")

    # 4. 板块表现
    logger.info("4. 板块表现分析...")
    try:
        sectors = _get_db().fetchall(
            "SELECT * FROM sector_performance WHERE record_date = %s AND rank_type = %s ORDER BY id",
            (datetime.now().strftime('%Y-%m-%d'), '涨幅')
        )
        if sectors:
            top_sectors = sorted(sectors, key=lambda x: x.get('change_pct', 0), reverse=True)[:5]
            results['top_sectors'] = top_sectors
    except Exception as e:
        logger.warning(f"板块分析失败: {e}")
    _log_timing(_T_START, "板块表现分析")

    # 5. 构建候选池：全市场扫描
    logger.info("5. 构建候选池（全市场扫描）...")
    raw_candidates = set()
    up_codes_set = set()
    if today_up:
        up_codes_set = {str(s.get('code', '')) for s in today_up}

    from utils.dao import get_db as _get_db
    db_pool = _get_db()
    today_str = datetime.now().strftime('%Y%m%d')
    check = db_pool.fetchone('SELECT COUNT(*) as c FROM stock_daily WHERE trade_date=%s', (today_str,))
    target_date = today_str if check and check['c'] > 10 else None
    if not target_date:
        last_date = db_pool.fetchone('SELECT MAX(trade_date) as d FROM stock_daily')
        target_date = last_date['d'] if last_date else None

    def _fill_name(code, name):
        if name:
            return name
        from utils.dao import get_db
        _db2 = get_db()
        r = _db2.fetchone('SELECT name FROM stock_daily WHERE code=%s AND name!="" ORDER BY trade_date DESC LIMIT 1', (code,))
        return r['name'] if r else name

    try:
        three_months_ago = (datetime.now() - timedelta(days=90)).strftime('%Y%m%d')
        all_strong = db_pool.fetchall('''
            SELECT d.code, d.name, d.change_pct, d.close, d.amount, d.volume, d.turnover_rate
            FROM stock_daily d
            WHERE d.trade_date=%s
              AND d.amount > 500000000
              AND d.total_market_cap >= 10000000000
              AND d.code NOT LIKE '688%%'
              AND d.code NOT LIKE '300%%'
              AND d.code NOT LIKE '9%%'
              AND d.code NOT LIKE '301%%'
              AND d.name NOT LIKE '%%ST%%'
              AND d.name NOT LIKE '%%退%%'
              AND EXISTS (
                  SELECT 1 FROM daily_limit_up l
                  WHERE l.code = d.code
                    AND l.trade_date >= %s
                    AND l.trade_date <= %s
                    AND (l.status IS NULL OR l.status != '跌停')
              )
            ORDER BY d.amount DESC
        ''', (target_date, three_months_ago, target_date))
        logger.info(f"  全市场符合条件的活跃股: {len(all_strong)}只")
        _log_timing(_T_START, "全市场扫描+建候选池")

        for sr in all_strong:
            code = sr['code']
            name = _fill_name(sr['code'], sr['name'])
            change = sr['change_pct']
            close = sr['close']
            amt = sr['amount']

            if code in up_codes_set:
                source = '涨停热点'
            elif change >= 5:
                source = '大阳线'
            elif change >= 2:
                source = '中阳线'
            elif amt > 500000000:
                source = '大成交'
            elif change >= 0:
                source = '活跃平盘'
            else:
                source = '活跃下跌'

            raw_candidates.add((code, name, source))

    except Exception as e:
        logger.warning(f"全市场筛选失败: {e}")

    db_pool.close()

    if today_up:
        for s in today_up:
            code = str(s.get('code', ''))
            name = _fill_name(code, s.get('name', ''))
            if code.startswith("688") or code.startswith("300") or code.startswith("301"):
                continue
            if code in {c for c, _, _ in raw_candidates}:
                continue
            raw_candidates.add((code, name, '涨停热点'))

    # 6. 综合评分
    logger.info(f"6. 综合评分 (共{len(raw_candidates)}个候选)...")
    _t0_step6 = time.time()
    _log_timing(_T_START, "综合评分(含候选池构建)")
    scored = []
    for idx600, (code, name, source) in enumerate(raw_candidates, 1):
        try:
            r = score_candidate(code, name)
            r['source'] = source
            scored.append(r)
            if idx600 % 100 == 0:
                logger.info(f"    评分进度: {idx600}/{len(raw_candidates)} ({idx600*100//len(raw_candidates)}%)")
        except Exception as e:
            logger.warning(f"评分{name}失败: {e}")

    _log_timing(_t0_step6, "综合评分循环")

    scored.sort(key=lambda x: x.get('total_score', 0), reverse=True)
    results['scored'] = scored

    # 7. 补查候选股补充信息（批量查询优化）
    logger.info("  补查候选股补充信息（批量查询）...")
    try:
        from utils.dao import get_db as _get_db2
        _db2 = _get_db2()
        _today_str2 = datetime.now().strftime('%Y%m%d')
        _five_days_ago = (datetime.now() - timedelta(days=7)).strftime('%Y%m%d')
        _sixty_days_ago = (datetime.now() - timedelta(days=65)).strftime('%Y%m%d')
        
        if scored:
            codes_list = [r['code'] for r in scored]
            _pl = ','.join(['%s'] * len(codes_list))
            
            # 批量查市值+收盘价
            all_market = {}
            _mrows = _db2.fetchall(f'''
                SELECT code, total_market_cap, close, turnover_rate
                FROM stock_daily
                WHERE trade_date=%s AND code IN ({_pl})
            ''', (_today_str2, *codes_list))
            for _mr in _mrows:
                all_market[_mr['code']] = _mr
            
            # 批量查今日涨停数据（行业/连板/封板/换手率）
            all_zt_today = {}
            _zrows = _db2.fetchall(f'''
                SELECT code, board_times, bomb_times, price, turnover_rate, industry
                FROM daily_limit_up
                WHERE trade_date=%s AND code IN ({_pl})
            ''', (_today_str2, *codes_list))
            for _zr in _zrows:
                all_zt_today[_zr['code']] = _zr
            
            # 批量查近5天的首板涨停（涨停回踩用）
            all_zt5 = {}
            _z5rows = _db2.fetchall(f'''
                SELECT l.code, l.trade_date, l.price, l.turnover_rate as zt_turnover_rate,
                       s.close as zt_close
                FROM daily_limit_up l
                LEFT JOIN stock_daily s ON s.code=l.code AND s.trade_date=l.trade_date
                WHERE l.trade_date>=%s AND l.trade_date<=%s AND l.board_times=1
                  AND (l.status IS NULL OR l.status!=%s)
                  AND l.code IN ({_pl})
                ORDER BY l.code, l.trade_date DESC
            ''', (_five_days_ago, _today_str2, '跌停', *codes_list))
            for _z5r in _z5rows:
                c = _z5r['code']
                if c not in all_zt5:  # 只保留最新的（ORDER BY DESC 第一条）
                    all_zt5[c] = _z5r
            
            # 批量查60天K线
            all_klines = {}
            _krows = _db2.fetchall(f'''
                SELECT code, trade_date, close, low, high, change_pct, volume, amount
                FROM stock_daily
                WHERE trade_date>=%s AND trade_date<=%s AND code IN ({_pl})
                ORDER BY code, trade_date
            ''', (_sixty_days_ago, _today_str2, *codes_list))
            for _kr in _krows:
                c = _kr['code']
                if c not in all_klines:
                    all_klines[c] = []
                all_klines[c].append(_kr)
            
            # 批量查近5天的换手率（分组过滤用）
            all_turn = {}
            _trows = _db2.fetchall(f'''
                SELECT code, trade_date, turnover_rate
                FROM stock_daily
                WHERE trade_date>=%s AND trade_date<=%s AND code IN ({_pl})
                ORDER BY code, trade_date
            ''', (_five_days_ago, _today_str2, *codes_list))
            for _tr in _trows:
                all_turn[_tr['code']] = all_turn.get(_tr['code'], []) + [_tr]
            
            for r in scored:
                code = r['code']
                _mr = all_market.get(code, {})
                r['total_market_cap'] = _mr.get('total_market_cap', 0) or 0
                r['today_close'] = _mr.get('close', 0) or 0
                r['today_turnover'] = _mr.get('turnover_rate', 0) or 0
                
                _zr = all_zt_today.get(code, {})
                r['board_times'] = _zr.get('board_times', 1) or 1
                r['bomb_times'] = _zr.get('bomb_times', 0) or 0
                r['industry'] = _zr.get('industry', '')
                
                _z5r = all_zt5.get(code, {})
                r['recent_zt_date'] = _z5r.get('trade_date', '')
                r['recent_zt_price'] = _z5r.get('price', 0.0) or 0.0
                r['zt_turnover_rate'] = _z5r.get('zt_turnover_rate', 0) or 0
                
                _klines_60 = all_klines.get(code, [])
                if _klines_60:
                    _low = min((k['low'] for k in _klines_60 if k['low']), default=0)
                    _high = max((k['high'] for k in _klines_60 if k['high']), default=0)
                    _close_now = _klines_60[-1]['close']
                    r['_60d_low'] = _low
                    r['_60d_high'] = _high
                    r['_60d_position'] = ((_close_now - _low) / (_high - _low) * 100) if (_high - _low) > 0 else 50
                    _last5 = [k for k in _klines_60 if k['trade_date'] >= _five_days_ago][-5:]
                    r['_5d_up_days'] = sum(1 for k in _last5 if (k.get('change_pct') or 0) > 0)
                    if len(_last5) >= 2:
                        r['_5d_chg'] = (_last5[-1]['close'] - _last5[0]['close']) / _last5[0]['close'] * 100 if _last5[0]['close'] else 0
                    else:
                        r['_5d_chg'] = 0
                    _vol_last5 = [k['amount'] for k in _last5 if k['amount']]
                    _vol_prev10 = [k['amount'] for k in _klines_60 if k['amount']][-15:-5] if len(_klines_60) >= 15 else []
                    r['_5d_avg_amount'] = sum(_vol_last5) / len(_vol_last5) if _vol_last5 else 0
                    r['_10d_prev_avg_amount'] = sum(_vol_prev10) / len(_vol_prev10) if _vol_prev10 else 0
                    _closes_last5 = [k['close'] for k in _klines_60 if k['close']][-5:]
                    r['_ma5'] = sum(_closes_last5) / len(_closes_last5) if _closes_last5 else 0
                else:
                    r['_60d_position'] = 50
                    r['_5d_up_days'] = 0
                    r['_5d_chg'] = 0
                    r['_5d_avg_amount'] = 0
                    r['_10d_prev_avg_amount'] = 0
                    r['_ma5'] = 0
        logger.info(f"  补查完成，{len(scored)}只候选处理完毕")
        _log_timing(_t0_step6, "补查+过滤")
    except Exception as e:
        logger.warning(f"补查信息失败: {e}")
        _log_timing(_t0_step6, "补查+过滤(出错)")

    # ============================================================
    # 8. 分组过滤
    # ============================================================
    logger.info("8. 分组过滤...")

    # ── ⚡ 涨停回踩组 ──
    # 条件：近5日首板涨停 → 今日未涨停 → 缩量回踩不破起涨点
    # 使用补查阶段已批量获取的数据，不再逐条查DB
    try:
        up_group = []
        for r in scored:
            code = r['code']
            change = r.get('change_pct', 0) or 0
            if change >= 9.5:
                continue
            zt_date = r.get('recent_zt_date', '')
            zt_price = r.get('recent_zt_price', 0.0)
            if not zt_date or zt_price <= 0:
                continue
            # ✅ 涨停日不能是今天（必须是隔天回踩）
            if zt_date >= _today_str2:
                continue
            today_close_ = r.get('today_close', 0) or 0
            if today_close_ < zt_price * 0.97:
                continue
            # ✅ 今天收盘在涨停价的-5%~+5%区间（排除一字板/涨停延续/大跌跌破起涨点）
            zt_change_pct = (today_close_ - zt_price) / zt_price * 100
            if zt_change_pct < -5 or zt_change_pct > 5:
                logger.info(f"    🚫 {r['name']} 相对涨停日涨跌{zt_change_pct:+.2f}%超出-5%~+5%区间")
                continue
            # 换手率已在补查阶段通过 all_market/all_klines 获取
            td_turn = r.get('today_turnover', 0) or 0
            # 放量过滤：如果涨停日换手率 > 0，检查今日换手是否超过涨停日的2倍
            zt_turn = r.get('zt_turnover_rate', 0) or 0
            if zt_turn > 0 and td_turn > zt_turn * 2.0:
                logger.info(f"    🚫 {r['name']} 放量(今日换手{td_turn}% > 涨停日{zt_turn}%*2.0={zt_turn*2.0:.1f}%)")
                continue
            mcap = r.get('total_market_cap', 0) or 0
            if mcap < 5000000000:
                continue
            if code.startswith('688') or code.startswith('300') or code.startswith('301') or code.startswith('8') or code.startswith('4'):
                continue
            if 'ST' in r.get('name', '') or '退' in r.get('name', ''):
                continue
            recoil_pct = (today_close_ - zt_price) / zt_price * 100
            r['zt_date'] = zt_date
            r['zt_price'] = zt_price
            r['today_close'] = today_close_
            r['recoil_pct'] = recoil_pct
            r['group'] = '涨停回踩'
            up_group.append(r)
            logger.info(f"    ✅ 涨停回踩: {r['name']}({code}) 涨停{zt_date} 回踩{recoil_pct:+.2f}% 换手{td_turn}%")
    except Exception as e:
        logger.warning(f"涨停回踩组过滤失败: {e}")
        up_group = []

    up_group.sort(key=lambda x: (x.get('total_score', 0), -x.get('recoil_pct', 0)), reverse=True)
    results['up_top5'] = up_group[:5]

    # ── 📗 区间潜伏组 ──
    non_up_group = []
    for r in scored:
        if r.get('source') == '涨停热点':
            continue
        trend_score = (r.get('breakdown', {}).get('趋势位置', {}).get('score', 0) or 0)
        if trend_score < 5:
            continue
        pos_score = (r.get('breakdown', {}).get('位置评估', {}).get('score', 0) or 0)
        if pos_score < 3:
            continue
        mcap = r.get('total_market_cap', 0) or 0
        if mcap < 5000000000:
            continue
        code = r['code']
        if code.startswith('688') or code.startswith('300') or code.startswith('301') or code.startswith('8') or code.startswith('4'):
            continue
        if 'ST' in r.get('name', '') or '退' in r.get('name', ''):
            continue
        pos_60 = r.get('_60d_position', 100)
        if pos_60 > 50:
            continue
        high_60 = r.get('_60d_high', 0)
        low_60 = r.get('_60d_low', 0)
        if high_60 > 0 and (high_60 - low_60) / high_60 < 0.20:
            continue
        up_days = r.get('_5d_up_days', 0)
        if up_days < 3:
            logger.info(f"    🚫 {r['name']} 近5日阳线{up_days}天 < 3")
            continue
        chg_5 = r.get('_5d_chg', 0)
        if chg_5 < 2 or chg_5 > 15:
            continue
        avg_5 = r.get('_5d_avg_amount', 0)
        avg_10 = r.get('_10d_prev_avg_amount', 0)
        if avg_10 > 0 and avg_5 < avg_10 * 0.8:
            logger.info(f"    🚫 {r['name']} 量能不足(近5日均量{avg_5/1e8:.1f}亿 < 前10日{avg_10/1e8:.1f}亿*0.8={avg_10*0.8/1e8:.1f}亿)")
            continue
        if avg_10 > 0 and avg_5 > avg_10 * 2.5:
            logger.info(f"    🚫 {r['name']} 量能过大(近5日均量{avg_5/1e8:.1f}亿 > 前10日{avg_10/1e8:.1f}亿*2.5={avg_10*2.5/1e8:.1f}亿)")
            continue
        ma5 = r.get('_ma5', 0)
        close_now = r.get('today_close', 0) or 0
        if ma5 > 0 and close_now < ma5:
            continue
        r['group'] = '区间潜伏'
        non_up_group.append(r)
        logger.info(f"    ✅ 区间潜伏: {r['name']}({code}) {r['total_score']}分 pos60={pos_60:.0f}% 5d_chg={chg_5:+.1f}%")

    # 区间潜伏组二级排序：总分降序，同分时60日位置低（低位更安全）优先
    non_up_group.sort(key=lambda x: (x.get('total_score', 0), x.get('_60d_position', 50)), reverse=True)
    results['non_up_top5'] = non_up_group[:5]

    # 精选
    results['candidates'] = []
    seen_codes = set()
    for r in results['up_top5'] + results['non_up_top5']:
        if r['code'] not in seen_codes:
            seen_codes.add(r['code'])
            results['candidates'].append({
                'code': r['code'],
                'name': r['name'],
                'score': r['total_score'],
                'grade': r['grade'],
                'position_advice': r['position_advice'],
                'source': r.get('source', ''),
            })

    _save_picks_to_db(results)

    _log_timing(_T_START, "总耗时")

    if scored:
        logger.info(f"选股完成，最高分: {scored[0]['name']}({scored[0]['total_score']}分)")
    else:
        logger.info("选股完成，候选池为空")

    return results


def format_v2_report(results: dict) -> str:
    """格式化V2选股结果"""
    if not results:
        return "数据不足，无法生成推荐"

    now = datetime.now()
    lines = [f"📊 **{now.strftime('%Y-%m-%d')} 收盘选股**"]
    lines.append("")
    lines.append("📝 **评分说明**: 筹码结构(25分)+资金接力(25分)+板块环境(20分)+趋势位置(20分)+大盘安全(10分)+位置评分(+15分)")
    lines.append("")

    market = results.get('market', {})
    if market:
        emoji = '✅' if market['status'] == '正常' or market['status'] == '做多' else '⚠️' if market['status'] == '谨慎' else '❌'
        lines.append(f"{emoji} 大盘: {market['status']} ({market.get('sh_change', 0):+.2f}%)")
        lines.append("")

    tot = results.get('total_limit_up', 0)
    if tot:
        lines.append(f"⚡ 涨停: **{tot}只**")
        if results.get('hot_industries'):
            inds = " | ".join([f"{ind}({cnt})" for ind, cnt in results['hot_industries'][:4]])
            lines.append(f"   热点板块: {inds}")
        lines.append("")

    up_top5 = results.get('up_top5', [])
    non_up_top5 = results.get('non_up_top5', [])

    if not up_top5 and not non_up_top5:
        lines.append("无符合条件的候选标的")
        return "\n".join(lines)

    def _format_stock(r, num, group_label):
        code = r['code']
        name = r['name'] if r.get('name') else code
        score = r['total_score']
        bd = r.get('breakdown', {})
        src = r.get('source', '')
        group = r.get('group', '')

        emoji = ['①', '②', '③', '④', '⑤'][num-1]
        buf = [f"{emoji} **{name}({code}) — {score}分**"]
        buf.append(f"  📊 来源: {src}")

        dims = [('筹码结构', '筹码', 25), ('资金接力', '接力', 25), ('板块环境', '板块', 20), ('趋势位置', '趋势', 20), ('大盘安全', '大盘', 10)]
        dim_parts = [f"{label}{bd.get(key, {}).get('score', 0)}/{max_score}" for key, label, max_score in dims]
        pos_s = bd.get('位置评估', {}).get('score', 0)
        dim_parts.append(f"位置{pos_s}/15")
        buf.append(f"  📋 {' | '.join(dim_parts)}")

        best_notes = []
        if group == '涨停回踩':
            # 取资金接力(换手)和趋势位置的 detail
            money_notes = [d for d in (bd.get('资金接力', {}).get('details') or []) if d and len(d) < 60]
            trend_notes = [d for d in (bd.get('趋势位置', {}).get('details') or []) if d and len(d) < 60]
            if money_notes:
                best_notes.append(money_notes[0])
            if trend_notes:
                best_notes.append(trend_notes[0])
            if not best_notes:
                best_notes.append(f"涨停{r.get('zt_date','')}，回踩{r.get('recoil_pct',0):+.2f}%")
        elif group == '区间潜伏':
            best_notes.append(f"60日底部{r.get('_60d_position',0):.0f}%分位，近5日{r.get('_5d_chg',0):+.1f}%底部长阳启动")
        else:
            for key, label, max_score in dims:
                dim = bd.get(key, {})
                s = dim.get('score', 0)
                notes = [d for d in (dim.get('details') or []) if d and len(d) < 60]
                if s > 0 and notes:
                    best_notes.append(notes[0])
                    break
            if not best_notes and pos_s >= 8:
                best_notes.append(f"位置适中(+{pos_s})")

        if best_notes:
            buf.append(f"  🔍 {best_notes[0]}")
            if len(best_notes) > 1:
                buf.append(f"     {best_notes[1]}")

        risks = r.get('risks', [])
        risk_strs = []
        if risks:
            risk_strs = [str(risk)[:30] for risk in risks[:2]]
        else:
            chip = bd.get('筹码结构', {}).get('score', 0)
            trend = bd.get('趋势位置', {}).get('score', 0)
            if chip < 10:
                risk_strs.append('筹码偏高' if chip < 5 else '位置一般')
            if trend < 5:
                risk_strs.append('趋势偏弱')
            if market.get('sh_change', 0) < -1:
                risk_strs.append('大盘谨慎')
        if risk_strs:
            buf.append(f"  ⚠️ {' '.join(risk_strs)}")
        return '\n'.join(buf)

    lines.append("")
    lines.append("=" * 38)
    lines.append("")

    if up_top5:
        lines.append(f"**⚡ 涨停接力 — TOP {len(up_top5)}**")
        lines.append("")
        for i, r in enumerate(up_top5, 1):
            lines.append(_format_stock(r, i, '涨停'))
            lines.append("")

    if non_up_top5:
        lines.append("─" * 38)
        lines.append("")
        lines.append(f"**📗 区间潜伏 — TOP {len(non_up_top5)}**")
        lines.append("")
        for i, r in enumerate(non_up_top5, 1):
            lines.append(_format_stock(r, i, '低位'))
            lines.append("")

    lines.append("=" * 38)
    lines.append("")
    lines.append("**📋 明日操作计划**")
    lines.append("")

    sh_chg = market.get('sh_change', 0) if market else 0
    if sh_chg < -1.5:
        lines.append("⚠️ 大盘跌超1.5%，建议观望为主")
    elif sh_chg < -0.5:
        lines.append("⚠️ 大盘偏弱，控制仓位≤30%，优选区间潜伏组")
    else:
        lines.append("✅ 大盘环境正常，可正常操作")
    lines.append("")

    all_top = up_top5[:5] + non_up_top5[:5]
    all_sorted = sorted(all_top, key=lambda r: r.get('total_score', 0), reverse=True)
    lines.append("**🌟 重点盯盘 TOP 3：**")

    def _build_entry_advice(r):
        group = r.get('group', '')
        if group == '涨停回踩':
            recoil = r.get('recoil_pct', 0)
            if recoil > -2:
                return "回踩确认，竞价量比>3可小仓试"
            else:
                return f"回踩{recoil:+.1f}%，关注是否企稳"
        elif group == '区间潜伏':
            return "回踩5日线低吸"
        return "观望"

    for i, r in enumerate(all_sorted[:3], 1):
        name = r['name']
        code = r['code']
        score = r['total_score']
        group = r.get('group', '')
        bd = r.get('breakdown', {})
        pos_s = bd.get('位置评估', {}).get('score', 0)
        trend_s = bd.get('趋势位置', {}).get('score', 0)
        pos_tag = '低位' if pos_s >= 15 else ('中位' if pos_s >= 8 else '偏高')
        trend_tag = '趋势强' if trend_s >= 14 else ('趋势好' if trend_s >= 10 else '趋势弱')
        advice = _build_entry_advice(r)
        lines.append(f"  {'❶❷❸'[i-1]} **{name}({code})** {score}分 | {group} | {pos_tag}/{trend_tag}")
        lines.append(f"     → {advice}")
    lines.append("")

    lines.append("**⚠️ 交易纪律**")
    lines.append("· 单票≤50% | 止损-5%（先三问，详见 docs/交易纪律.md）")
    lines.append("· 大盘跌>1.5%不买")
    lines.append("· 涨停回踩组低吸不追高；区间潜伏组回踩均线低吸")

    return "\n".join(lines)


def _save_picks_to_db(results: dict):
    """将选股结果保存到数据库"""
    from utils.dao import get_db
    try:
        db = get_db()
        today = datetime.now().strftime('%Y%m%d')
        db.execute('DELETE FROM daily_picks WHERE trade_date=%s', (today,))

        def _build_highlights(r: dict) -> str:
            bd = r.get('breakdown', {})
            parts = []
            news = bd.get('个股消息催化', {})
            news_detail = news.get('details', [])
            if news_detail:
                t = news_detail[0].replace('📰','').replace('💰','').replace('🔥','').replace('🐲','').replace('📋','').strip()[:30]
                parts.append(t)
            fin = bd.get('基本面', {}).get('details', {})
            fin_parts = []
            for k, v in fin.items():
                if isinstance(v, dict) and 'value' in v:
                    val = str(v['value'])
                    if '%' in val or '亿' in val:
                        fin_parts.append(f"{k}{val}")
            if fin_parts:
                parts.append(' · '.join(fin_parts[:2]))
            sec = bd.get('板块热度', {}).get('details', [])
            if sec:
                sec_text = ' · '.join([s.replace('🔥','').replace('📊','').strip() for s in sec[:1]])
                if sec_text:
                    parts.append(f"[{sec_text}]")
            form = bd.get('个股形态', {})
            form_detail = form.get('details', [])
            if form_detail:
                form_text = form_detail[0].replace('📊','').strip()[:25]
                parts.append(form_text)
            return ' | '.join(parts) if parts else ''

        scored = results.get('scored', [])
        candidates = results.get('candidates', [])
        candidate_codes = {c['code'] for c in candidates}

        for rank, r in enumerate(scored):
            code = r['code']
            is_top = code in candidate_codes
            bd = r.get('breakdown', {})
            db.insert_or_ignore('daily_picks', {
                'trade_date': today,
                'code': code,
                'name': r['name'],
                'board_times': r.get('board_times', 1),
                'total_score': r['total_score'],
                'grade': r.get('grade', ''),
                'position_advice': r.get('position_advice', ''),
                'source': r.get('source', ''),
                'rank': rank + 1,
                'change_pct': r.get('change_pct', None),
                'is_pick': 1 if is_top else 0,
                'highlights': _build_highlights(r),
                'score_chip': bd.get('筹码结构', {}).get('score', None),
                'score_money': bd.get('资金接力', {}).get('score', None),
                'score_sector': bd.get('板块环境', {}).get('score', None),
                'score_trend': bd.get('趋势位置', {}).get('score', None),
                'score_market': bd.get('大盘安全', {}).get('score', None),
            })

        logger.info(f"选股结果已落库: {len(scored)}只评分, {len(candidates)}只精选")
        db.close()
    except Exception as e:
        logger.error(f"保存选股结果到数据库失败: {e}")


if __name__ == '__main__':
    logger.info("=== V2选股启动 ===")
    results = pick_stocks_v2()
    output = format_v2_report(results)
    print("\n" + output + "\n")

    save_path = os.path.join(os.path.dirname(__file__), 'daily_picks_v2.json')
    with open(save_path, 'w') as f:
        json.dump(results, f, ensure_ascii=False, default=str, indent=2)
    logger.info(f"V2选股结果已保存到 daily_picks_v2.json")

    old_path = os.path.join(os.path.dirname(__file__), 'daily_picks.json')
    with open(old_path, 'w') as f:
        old_data = {
            **results,
            'candidates': [{
                'code': c['code'],
                'name': c['name'],
                'score': c.get('score', 0),
                'grade': c.get('grade', ''),
                'board_times': 1,
                'source': c.get('source', ''),
            } for c in results.get('candidates', [])],
        }
        json.dump(old_data, f, ensure_ascii=False, default=str, indent=2)
    logger.info("已同步更新 daily_picks.json")

    top5_path = os.path.join(os.path.dirname(__file__), 'daily_top5.json')
    with open(top5_path, 'w') as f:
        top5_data = {
            'date': datetime.now().strftime('%Y%m%d'),
            'market': results.get('market', {}),
            'total_limit_up': results.get('total_limit_up', 0),
            'hot_industries': results.get('hot_industries', []),
            'top5': results.get('candidates', [])[:5],
        }
        json.dump(top5_data, f, ensure_ascii=False, indent=2)
    logger.info("已保存 daily_top5.json")
