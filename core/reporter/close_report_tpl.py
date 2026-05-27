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
    # 4️⃣ 昨日选股涨跌及复盘
    # ════════════════════════════════════════
    parts.append("─" * 50)
    parts.append("4️⃣ 昨日选股涨跌及复盘")
    parts.append("━" * 30)
    
    yp = d.get('yesterday_picks', [])
    if yp:
        for s in yp:
            chg = s.get('change_pct', 0)
            zt_tag = "（涨停）" if s.get('is_zt') else ("（近涨停）" if s.get('is_near_zt') else "")
            sym = "✅" if s.get('result') == 'win' else "❌"
            reason = s.get('reason', '')
            if reason:
                parts.append(f"{s['name']} {chg:+.2f}%{zt_tag} {sym}，{reason}")
            else:
                parts.append(f"{s['name']} {chg:+.2f}%{zt_tag} {sym}")
        
        # 汇总
        up = sum(1 for s in yp if s.get('result') == 'win')
        down = len(yp) - up
        total = len(yp)
        win_rate = up / total * 100 if total > 0 else 0
        parts.append("─" * 30)
        parts.append(f"📊 汇总：上涨{up}只 / 下跌{down}只 胜率{win_rate:.0f}%")
        parts.append("─" * 30)
    
    # ReAct复盘（兼容两种模式：字符串=新三闭环，dict=旧简版）
    rr = d.get('react_report', '')
    if isinstance(rr, str) and rr:
        # 新模式：pick_react.run_react_analysis() 返回的文本段落
        parts.append(rr)
    elif isinstance(rr, dict) and rr:
        # 旧模式 fallback：_build_react_data() 返回的 dict
        parts.append(f"📊 ReAct复盘 (选股{rr.get('pick_date', '')} → 检验{rr.get('check_date', '')})")
        parts.append(f"精选{rr.get('total_count', 0)}只 · 胜率{rr.get('win_rate', 0):.0f}% · 均涨幅+{rr.get('avg_return', 0):.2f}%")
        parts.append(f"最大盈利+{rr.get('max_gain', 0):.2f}% · 最大亏损{rr.get('max_loss', 0):.2f}%")
        parts.append(f"大涨(≥2%): {rr.get('big_gain_count', 0)}只")
        parts.append("─" * 20)
        
        for label, cnt, wr, avg_ret, icon in rr.get('score_groups', []):
            parts.append(f"{label}: {cnt}只 胜率{wr:.0f}% 均{avg_ret:+.2f}% {icon}")
        
        for label, cnt, wr, avg_ret, icon in rr.get('group_groups', []):
            parts.append(f"{label}: {cnt}只 胜率{wr:.0f}% 均涨幅{avg_ret:+.2f}% {icon}")
        
        pw = rr.get('past_week', {})
        if pw:
            parts.append("─" * 20)
            parts.append(f"近一周: {pw.get('count', 0)}只 胜率{pw.get('win_rate', 0):.0f}% 均{pw.get('avg_return', 0):+.2f}%")
    
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
        parts.append(f"📊 {title_date} 收盘选股")
        parts.append(f"📝 评分说明: 筹码结构(25分)+资金接力(25分)+板块环境(20分)+趋势位置(20分)+大盘安全(10分)+位置评分(+15分)")
        parts.append(f"✅ 大盘: {pk.get('market_status', '')} ({pk.get('market_change', 0):+.2f}%)")
        parts.append(f"⚡ 涨停: {pk.get('limit_up_total', 0)}只")
        if pk.get('hot_industries'):
            hot_inds = pk['hot_industries']
            if hot_inds and isinstance(hot_inds[0], (list, tuple)):
                hot_strs = [f"{name}({cnt})" for name, cnt in hot_inds[:4]]
            else:
                hot_strs = [str(x) for x in hot_inds[:4]]
            parts.append(f"热点板块: {' | '.join(hot_strs)}")
        parts.append("=" * 38)
        parts.append("")
        
        # 涨停接力TOP5
        up_top5 = pk.get('up_top5', [])
        if up_top5:
            parts.append(f"⚡ 涨停接力 — TOP {len(up_top5)}")
            parts.append("")
            for i, s in enumerate(up_top5, 1):
                emoji = ['①', '②', '③', '④', '⑤'][i-1]
                score_val = int(s['score']) if isinstance(s['score'], float) and s['score'] == int(s['score']) else s['score']
                parts.append(f"{emoji} {s['name']}({s['code']}) — {score_val}分")
                parts.append(f"📊 来源: {s.get('source', '')}")
                dims = s.get('dims', {})
                dim_str = ' | '.join(f"{k}{dims.get(k, 0)}/{max_s}"
                                      for k, max_s in [('筹码', 25), ('接力', 25), ('板块', 20), ('趋势', 20), ('大盘', 10)]
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
                parts.append("")
        
        # 区间潜伏TOP5
        non_up_top5 = pk.get('non_up_top5', [])
        if non_up_top5:
            parts.append("─" * 38)
            parts.append("")
            parts.append(f"📗 区间潜伏 — TOP {len(non_up_top5)}")
            parts.append("")
            for i, s in enumerate(non_up_top5, 1):
                emoji = ['①', '②', '③', '④', '⑤'][i-1]
                score_val = int(s['score']) if isinstance(s['score'], float) and s['score'] == int(s['score']) else s['score']
                parts.append(f"{emoji} {s['name']}({s['code']}) — {score_val}分")
                parts.append(f"📊 来源: {s.get('source', '')}")
                dims = s.get('dims', {})
                dim_str = ' | '.join(f"{k}{dims.get(k, 0)}/{max_s}"
                                      for k, max_s in [('筹码', 25), ('接力', 7), ('板块', 5), ('趋势', 14), ('大盘', 10)]
                                      if k in dims)
                if '位置' in dims:
                    dim_str += f" | 位置{dims['位置']}/15"
                parts.append(f"📋 {dim_str}")
        else:
            parts.append("─" * 38)
            parts.append("")
            parts.append("📗 区间潜伏 — 暂无符合条件的个股")
            parts.append("")
            parts.append("今日未选出满足底部低位+趋势初成+量能放大条件的标的")
            parts.append("")
            parts.append("")
        
        parts.append("=" * 38)
        parts.append("")
        parts.append("📋 明日操作计划")
        parts.append("")
        
        mkt_chg = pk.get('market_change', 0)
        if mkt_chg < -1.5:
            parts.append("⚠️ 大盘跌超1.5%，建议观望为主")
        elif mkt_chg < -0.5:
            parts.append("⚠️ 大盘偏弱，控制仓位≤30%，优选区间潜伏组")
        else:
            parts.append(f"✅ 大盘环境正常，可正常操作")
        parts.append("")
        
        emojis = ['❶', '❷', '❸']
        parts.append("🌟 重点盯盘 TOP 3：")
        for i, s in enumerate(pk.get('top3_advice', [])):
            if i >= 3:
                break
            sv = s['score']
            sv = int(sv) if isinstance(sv, float) and sv == int(sv) else sv
            parts.append(f"{emojis[i]} {s['name']}({s['code']}) {sv}分 | {s.get('source', '')} | {s.get('position', '')}/{s.get('trend', '')}")
            parts.append(f"→ {s.get('advice', '')}")
        parts.append("")
        
        parts.append("⚠️ 交易纪律")
        parts.append("· 单票≤50% | 止损-5%")
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
                parts.append(f"{p['name']}({p['code']}): 成本{p['cost_price']}×{p['shares']}={p['cost_total']:.0f}")
                parts.append(f"现价{p['close']:.2f} 市值{p['cur_total']:.0f} 盈亏{p['pnl_pct']:+.2f}% {p['pnl_sym']}")
                if p.get('amount_yi'):
                    parts.append(f"成交{p['amount_yi']:.1f}亿 换手{p['turnover']:.2f}%")
                flg = p.get('profit_flag')
                if flg == 'stop':
                    parts.append(f"⚠️ 已触发止损线-5%，建议严格执行卖出")
                elif flg == 'near_stop':
                    parts.append(f"⚠️ 接近止损线-5%，密切监控，可考虑减仓")
                elif flg == 'take_profit':
                    parts.append(f"💡 浮盈丰厚，可考虑止盈一部分或设移动止盈")
            total_inv = sum(p['cost_price'] * p['shares'] for p in positions)
            parts.append(f"总投入: {total_inv:.0f}元 | 持仓: {len(positions)}只")
    
    return "\n".join(parts)
