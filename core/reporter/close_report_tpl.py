"""
收盘复盘报告模版 - 纯模版层
不执行任何数据查询，只负责按5.25定稿模版渲染字符串

依赖：无（纯字符串拼接，不导入任何业务模块）
"""

def render_report(data: dict) -> str:
    """
    按5.25定稿模版渲染收盘复盘报告
    
    data 结构:
    {
        'date': '2026-05-26',            # 报告日期
        'indexes': {                       # 指数数据
            'szzs': {'current_price': 4145.37, 'change_pct': -0.17},
            'szcz': {'current_price': 15876.16, 'change_pct': 0.12},
            'cyb': {'current_price': 4043.07, 'change_pct': 0.54},
            'kc50': {'current_price': 1867.71, 'change_pct': -1.49},
        },
        'amount': 32643.6,                 # 成交额(亿)
        'amount_chg_text': '',             # 成交额变化说明
        'rise_fall': {'rise': 1354, 'fall': 4082},  # 涨跌家数
        'sectors': {                       # 板块表现
            'top_gain': ['贵金属', '工业金属', '小金属'],
            'top_fall': ['通信设备', '半导体', '橡胶制品'],
            'top3': [('贵金属', 4.1), ('工业金属', 1.9), ('小金属', 1.3)],
        },
        'limit_up': {                      # 涨停/跌停分析
            'count': 46,                   # 涨停数
            'board_count': 12,             # 连板数
            'board_ladder': [              # 连板梯队
                {'name': '华升股份', 'board_times': 2},
                ...
            ],
            'continuous_down': [           # 连续跌停
                {'name': '华升股份', 'board_times': 2},
            ],
            'big_seal_down': [             # 大额封跌停
                {'name': '合百集团', 'seal_fund': 1.7},
            ],
            'down_count': 16,              # 跌停数
        },
        'yesterday_picks': [               # 昨日选股复盘
            {
                'name': '蔚蓝锂芯',
                'code': '002245',
                'change_pct': 9.99,
                'is_zt': True,
                'is_near_zt': False,
                'result': 'win',
                'reason': '大成交',
            },
        ],
        'react_report': {                  # ReAct复盘
            'pick_date': '20260525',
            'check_date': '20260526',
            'total_count': 5,
            'win_rate': 60,
            'avg_return': 3.61,
            'max_gain': 9.99,
            'max_loss': -2.96,
            'big_gain_count': 2,
            'score_groups': [
                ('高分(>50)', 2, 100, 9.98, '✅'),
                ('低分(<40)', 3, 33, -0.64, '⚠️'),
            ],
            'group_groups': [
                ('⚡涨停接力', 5, 80, 3.80, '✅'),
                ('📗区间潜伏', 5, 60, 2.64, '✅'),
            ],
            'past_week': {
                'count': 67,
                'win_rate': 24,
                'avg_return': 0.19,
            },
        },
        'picks': {                         # 明日候选
            'total_candidates': 416,
            'max_name': '皇台酒业',
            'max_score': 59,
            'market_status': '正常',
            'market_change': 0.96,
            'limit_up_total': 103,
            'hot_industries': ['电力(9)', '半导体(9)', '元件(5)', '专用设备(5)'],
            'up_top5': [                    # 涨停接力TOP5
                {
                    'name': '晶方科技',
                    'code': '603005',
                    'score': 54,
                    'source': '涨停热点',
                    'dims': {'筹码': 1, '接力': 22, '板块': 17, '趋势': 4, '大盘': 10, '位置': 0},
                    'notes': ['换手16.4%适中(+12)', '早盘封板(+5)'],
                    'risks': ['筹码偏高', '趋势偏弱'],
                },
            ],
            'non_up_top5': [                # 区间潜伏TOP5
                {
                    'name': '建投能源',
                    'code': '000600',
                    'score': 52,
                    'source': '大成交',
                    'dims': {'筹码': 8, '接力': 7, '板块': 5, '趋势': 14, '大盘': 10, '位置': 8},
                    'notes': ['位置适中(+8)', '均线多头排列(MA5>10.3)(+8)'],
                    'risks': ['位置一般'],
                },
            ],
            'top3_advice': [                # 重点盯盘TOP3策略
                {
                    'name': '晶方科技',
                    'code': '603005',
                    'score': 54,
                    'source': '涨停热点',
                    'position': '高位',
                    'trend': '趋势弱',
                    'advice': '首板，竞价量比>3可参与，评分偏低，小仓试',
                },
            ],
        },
        'positions': [                     # 持仓
            {
                'name': '中贝通信',
                'code': '603220',
                'cost_price': 32.877,
                'shares': 2100,
                'cost_total': 69042,
                'close': 27.56,
                'cur_total': 57876,
                'pnl_pct': -16.17,
                'pnl_sym': '❌',
                'amount_yi': 11.4,
                'turnover': 9.15,
                'profit_flag': None,        # None=正常, 'stop':触及止损, 'near_stop':接近止损, 'take_profit':止盈
            },
        ],
    }
    """
    d = data
    parts = []
    
    # ════════════════════════════════════════
    # 标题
    # ════════════════════════════════════════
    title_date = d.get('date', '')
    parts.append(f"📊 {title_date} 收盘复盘")
    parts.append("━" * 30)
    parts.append("")
    
    # ════════════════════════════════════════
    # 1️⃣ 大盘概况
    # ════════════════════════════════════════
    parts.append("1️⃣ 大盘概况")
    parts.append("━" * 30)
    
    idx = d.get('indexes', {})
    def _flag(c): return "🔴" if c >= 0 else "🟢"
    szzs = idx.get('szzs', {})
    szcz = idx.get('szcz', {})
    cyb = idx.get('cyb', {})
    kc50 = idx.get('kc50', {})
    sz_str = f"上证 {szzs.get('current_price', 0):.2f} ({szzs.get('change_pct', 0):+.2f}%)"
    sc_str = f"深证 {szcz.get('current_price', 0):.2f} ({szcz.get('change_pct', 0):+.2f}%)"
    cyb_str = f"创业板 {cyb.get('current_price', 0):.2f} ({cyb.get('change_pct', 0):+.2f}%)"
    kc50_str = f"科创50 {kc50.get('current_price', 0):.2f} ({kc50.get('change_pct', 0):+.2f}%)"
    parts.append(f"📈 指数：{_flag(szzs.get('change_pct', 0))} {sz_str} / {_flag(szcz.get('change_pct', 0))} {sc_str} / {_flag(cyb.get('change_pct', 0))} {cyb_str} / {_flag(kc50.get('change_pct', 0))} {kc50_str}")
    
    amount = d.get('amount', 0)
    amt_chg = d.get('amount_chg_text', '')
    parts.append(f"💰 成交额：{amount:.1f}亿{amt_chg}")
    
    rf = d.get('rise_fall', {})
    parts.append(f"📊 涨跌分布：涨 {rf.get('rise', 0)} / 跌 {rf.get('fall', 0)}")
    parts.append("")
    
    # ════════════════════════════════════════
    # 2️⃣ 板块表现
    # ════════════════════════════════════════
    parts.append("2️⃣ 板块表现")
    parts.append("━" * 30)
    
    sec = d.get('sectors', {})
    if sec.get('top_gain'):
        parts.append(f"📈 涨幅居前：{' '.join(sec['top_gain'])}")
    if sec.get('top_fall'):
        parts.append(f"📉 跌幅居前：{' '.join(sec['top_fall'])}")
    if sec.get('top3'):
        t3_parts = [f"{name}({pct:+.1f}%)" for name, pct in sec['top3']]
        parts.append(f"🏅 领涨板块：{' '.join(t3_parts)}")
    parts.append("")
    
    # ════════════════════════════════════════
    # 3️⃣ 涨停/跌停分析
    # ════════════════════════════════════════
    parts.append("3️⃣ 涨停/跌停分析")
    parts.append("━" * 30)
    
    lu = d.get('limit_up', {})
    parts.append(f"🚀 涨停：{lu.get('count', 0)}只 ｜ 连板：{lu.get('board_count', 0)}只")
    
    ladder = lu.get('board_ladder', [])
    if ladder:
        ld_str = ' '.join(f"{s['name']}({s['board_times']}板)" for s in ladder[:5])
        parts.append(f"📊 连板梯队：{ld_str}")
    
    parts.append(f"💀 跌停：{lu.get('down_count', 0)}只")
    
    cont_down = lu.get('continuous_down', [])
    if cont_down:
        cd_str = ' '.join(f"{s['name']}({s['board_times']}跌停)" for s in cont_down[:3])
        parts.append(f"⚠️ 连续跌停：{cd_str}")
    
    big_seal = lu.get('big_seal_down', [])
    if big_seal:
        bs_str = ' '.join(f"{s['name']}({s['seal_fund']:.1f}亿)" for s in big_seal[:3])
        parts.append(f"🔴 大额封跌停：{bs_str}")
    
    parts.append("")
    
    # ════════════════════════════════════════
    # 4️⃣ T-2日选股涨跌及复盘
    # ════════════════════════════════════════
    parts.append("─" * 50)
    parts.append("4️⃣ T-2日选股涨跌及复盘")
    parts.append("━" * 30)
    
    yp = d.get('yesterday_picks', [])
    if yp:
        # 总体统计（仅 ≥60分）
        qualified = [p for p in yp if p.get('total_score', 0) >= 60]
        total = len(qualified)
        if total > 0:
            wins = sum(1 for p in qualified if p['change_pct'] > 0)
            losses = total - wins
            win_rate = round(wins / total * 100, 1)
            max_gain = max((p['change_pct'] for p in qualified), default=0)
            max_loss = min((p['change_pct'] for p in qualified), default=0)
            parts.append("📌 总体统计（≥60分）")
            parts.append("─" * 20)
            parts.append(f"总数: {total} 只")
            parts.append(f"盈利: {wins} 只")
            parts.append(f"亏损: {losses} 只")
            parts.append(f"胜率: {win_rate}%")
            parts.append(f"最大盈利: {max_gain:+.2f}% ｜ 最大亏损: {max_loss:+.2f}%")
            parts.append("")
        
        # 分组统计（B/C/D三组）
        groups = {
            'B组（60 ≤ total_score < 65）': [],
            'C组（65 ≤ total_score < 70）': [],
            'D组（total_score ≥ 70）': [],
        }
        for p in yp:
            sc = p.get('total_score', 0)
            if 60 <= sc < 65:
                groups['B组（60 ≤ total_score < 65）'].append(p)
            elif 65 <= sc < 70:
                groups['C组（65 ≤ total_score < 70）'].append(p)
            elif sc >= 70:
                groups['D组（total_score ≥ 70）'].append(p)
        
        parts.append("📊 分组统计")
        parts.append("─" * 20)
        for label, members in groups.items():
            parts.append(f"┌─ {label}")
            if members:
                cnt = len(members)
                grp_wins = sum(1 for p in members if p['change_pct'] > 0)
                grp_wr = round(grp_wins / cnt * 100, 1) if cnt > 0 else 0
                grp_avg = sum(p['change_pct'] for p in members) / cnt
                parts.append(f"│ 个股数量: {cnt} 只")
                parts.append(f"│ 盈利: {grp_wins} 只")
                parts.append(f"│ 胜率: {grp_wr}%")
                parts.append(f"│ 平均收益率: {grp_avg:+.2f}%")
            else:
                parts.append(f"│ 该分组今日无数据")
            parts.append("└─" + "─" * 28)
        parts.append("")
    
    # ReAct复盘（新：20日滚动统计 + 评分归因）
    rr = d.get('react_report', '')
    if isinstance(rr, dict) and rr.get('window_info'):
        # 新模式：build_react_report() 返回的结构化 dict
        parts.append(render_react_section(rr))
    elif isinstance(rr, str) and rr:
        # 回退：旧版文本段落
        parts.append(rr)
    
    parts.append("")
    
    # ════════════════════════════════════════
    # 5️⃣ 明日候选评分及操作
    # ════════════════════════════════════════
    parts.append("─" * 50)
    parts.append("5️⃣ 明日候选评分及操作")
    parts.append("━" * 30)
    
    pk = d.get('picks', {})
    if pk:
        parts.append(f"✅ 选股完成")
        parts.append(f"候选: {pk.get('total_candidates', 0)}只")
        ms = pk.get('max_score', 0)
        ms_val = int(ms) if isinstance(ms, float) and ms == int(ms) else ms
        parts.append(f"最高分: {pk.get('max_name', '')}({ms_val}分)")
        parts.append(f"📝 评分说明: 筹码结构(25分)+资金接力(25分)+板块环境(20分)+趋势位置(14分)+大盘安全(10分)+位置评分(+15分)")
        
        # B/C/D 三组展示
        group_labels = {
            'B': 'B组（60 ≤ total_score < 65）',
            'C': 'C组（65 ≤ total_score < 70）',
            'D': 'D组（total_score ≥ 70）',
        }
        score_groups = pk.get('score_groups', {})
        
        for g_key in ['B', 'C', 'D']:
            label = group_labels[g_key]
            members = score_groups.get(g_key, [])
            
            parts.append("")
            parts.append("━" * 38)
            parts.append(label)
            parts.append("━" * 38)
            
            if not members:
                parts.append("该分组今日无候选股")
                continue
            
            for i, s in enumerate(members, 1):
                emoji = ['①', '②', '③'][i-1]
                score_val = int(s['score']) if isinstance(s['score'], float) and s['score'] == int(s['score']) else s['score']
                parts.append(f"{emoji} {s['name']}({s['code']}) — {score_val}分 | {s.get('source', '')}")
                
                dims = s.get('dims', {})
                dim_str = ' | '.join(f"{k}{dims.get(k, 0)}/{max_s}"
                                      for k, max_s in [('筹码', 25), ('接力', 25), ('板块', 20), ('趋势', 14), ('大盘', 10)]
                                      if k in dims)
                if '位置' in dims:
                    dim_str += f" | 位置{dims['位置']}/15"
                parts.append(f"📋 {dim_str}")
                
                notes = s.get('notes', [])
                if notes:
                    parts.append(f"🔍 {notes[0]}")
                    if len(notes) > 1:
                        parts.append(f"    {notes[1]}")
                
                risks = s.get('risks', [])
                if risks:
                    parts.append(f"⚠️ {' '.join(risks)}")
                
                if i < len(members):
                    parts.append("")
        
        parts.append("")
        parts.append("=" * 38)
        parts.append("")
        parts.append("📋 明日操作计划")
        parts.append("")
        
        parts.append("⚠️ 交易纪律")
        parts.append("· 单票≤50% | 止损-5%（看三问，不盲目卖）")
        parts.append("· 详见 docs/交易纪律.md")
        parts.append("· 大盘跌>1.5%不买")
        parts.append("· 涨停接力组高开>5%不追；区间潜伏组回踩均线低吸")
        parts.append("")
    
        # 持仓合并到明日操作计划段内
        positions = d.get('positions', [])
        if positions:
            parts.append("")
            parts.append("📋 持仓股：关注要点")
            parts.append("─" * 20)
            for p in positions:
                # 渲染防御：用 .get() 取值，即使上游缺键也不会崩溃（正常路径值不变）
                parts.append(f"{p['name']}({p['code']}): 成本{p['cost_price']}×{p['shares']}={p['cost_total']:.0f}")
                parts.append(f"现价{p.get('close', 0):.2f} 市值{p.get('cur_total', 0):.0f} 盈亏{p.get('pnl_pct', 0):+.2f}% {p.get('pnl_sym', '⚠️')}")
                if p.get('amount_yi'):
                    parts.append(f"成交{p['amount_yi']:.1f}亿 换手{p.get('turnover', 0):.2f}%")
                flg = p.get('profit_flag')
                if flg == 'stop':
                    parts.append(f"⚠️ 已触发止损线-5%，先做三问判断（量能/板块/时间），详见 docs/交易纪律.md")
                elif flg == 'near_stop':
                    parts.append(f"⚠️ 接近止损线-5%，密切监控，等14:30再决策")
                elif flg == 'take_profit':
                    parts.append(f"💡 浮盈丰厚，可考虑止盈一部分或设移动止盈")
            total_inv = sum(p['cost_price'] * p['shares'] for p in positions)
            parts.append(f"总投入: {total_inv:.0f}元 | 持仓: {len(positions)}只")
    
    return "\n".join(parts)


def render_react_section(data: dict) -> str:
    """
    渲染 ReAct 复盘报告（20日滚动统计 + 评分归因）。

    data 结构由 build_react_report() 返回：
    {
        'window_info': {'window_size': 20, 'start_date': '...', 'end_date': '...', 'note': None},
        'summary': {'total': 120, 'wins': 68, 'win_rate': 56.7, 'avg_return': 1.23},
        'dimension_analysis': [{...}, ...],
        'group_stats': [{...}, ...],
        'react_analysis': {'has_changes': False, 'changes': [], 'analysis_summary': '...'},
    }
    """
    lines = []
    wi = data.get('window_info', {})
    summary = data.get('summary', {})
    dims = data.get('dimension_analysis', [])
    groups = data.get('group_stats', [])
    ra = data.get('react_analysis', {})

    window_size = wi.get('window_size', 0)
    if window_size == 0:
        return '📊 ReAct复盘：暂无有效数据'

    start_date = wi.get('start_date', '')
    end_date = wi.get('end_date', '')
    note = wi.get('note')

    if start_date and end_date:
        start_fmt = f'{start_date[:4]}/{start_date[4:6]}/{start_date[6:8]}'
        end_fmt = f'{end_date[:4]}/{end_date[4:6]}/{end_date[6:8]}'
        date_str = f'当前窗口：{start_fmt}~{end_fmt}'
        if note:
            date_str += f'（{note}）'
    else:
        date_str = ''

    lines.append('')
    lines.append('─' * 30)
    lines.append('')
    lines.append('📊 ReAct复盘：近20日滚动统计')
    if date_str:
        lines.append(date_str)

    # 概览
    total = summary.get('total', 0)
    wins = summary.get('wins', 0)
    win_rate = summary.get('win_rate', 0)
    avg_ret = summary.get('avg_return', 0)

    if total > 0:
        lines.append(f'共计{total}只 · 盈利{wins}只 · 胜率{win_rate:.1f}% · 均收益率{avg_ret:+.2f}%')
    else:
        lines.append('（无满足条件的样本数据）')

    lines.append('')

    # 评分归因分析
    if dims:
        lines.append('')
        lines.append('📈 评分归因分析：')
        lines.append('')
        for d in dims:
            dim_label = d.get('dim_label', '')
            full_score = d.get('full_score', 0)
            pp = d.get('predictive_power', '中')
            action = d.get('action', '维持')

            # 预测力符号
            if pp == '强':
                pp_sym = '🟢'
            elif pp == '中':
                pp_sym = '🟡'
            else:
                pp_sym = '🔴'

            lines.append(f'  {pp_sym} {dim_label}({full_score}分)')

            segs = [d.get(k) for k in ['high', 'mid', 'low']]
            for seg in segs:
                if not seg:
                    continue
                cnt = seg.get('count', 0)
                wr = seg.get('win_rate', 0)
                avg_r = seg.get('avg_return', 0)
                label = seg.get('label', '')

                if cnt == 0:
                    lines.append(f'     {label}: 无样本')
                    continue

                # 样本不足标记
                marker = ''
                if cnt < 3:
                    marker = '（样本不足）'

                lines.append(f'     {label}: {cnt}只  胜率{wr:.1f}%  均{avg_r:+.2f}%{marker}')

            # 预测力 + 区分度
            diff = d.get('diff')
            if diff is not None:
                diff_str = f'{diff:+.1f}pp'
                pred_check = ' ✅' if pp == '强' else ('' if pp == '中' else ' ❌')
                pred_display = {'强': '强', '中': '中', '弱': '弱'}.get(pp, pp)
                lines.append(f'     区分度: {diff_str}  → 预测力：{pred_display}{pred_check}')
            elif pp and action:
                pred_map = {'强': '强', '中': '中', '弱': '弱'}
                pred_label = pred_map.get(pp, pp)
                lines.append(f'     预测力: {pred_label} | {action}')

            lines.append('')  # 每个维度结束后加空行

        lines.append('  （位置评估暂不纳入）')
        lines.append('')

    # 分组统计
    if groups:
        lines.append('')
        lines.append('📋 分组统计：')
        lines.append('')
        for g in groups:
            label = g.get('label', '')
            cnt = g.get('count', 0)
            wr = g.get('win_rate', 0)
            avg_r = g.get('avg_return', 0)
            lines.append(f'  {label}: {cnt}只 胜率{wr:.1f}% 均{avg_r:+.2f}%')
            lines.append('')

    # 自优化建议
    lines.append('')
    lines.append('⚙️ 自优化建议：')
    if ra.get('has_changes'):
        for ch in ra.get('changes', []):
            lines.append(f'  {ch}')
    else:
        lines.append(f'  {ra.get("analysis_summary", "当前权重配置合理，无需调整")}')

    lines.append('')
    return '\n'.join(lines)
