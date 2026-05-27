#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
持仓同步工具 — 从MySQL读取持仓和交易数据，同步到 POSITIONS.md。

用法:
  python3 sync_portfolio.py              # 查看当前持仓
  python3 sync_portfolio.py --sync       # 生成 POSITIONS.md
  python3 sync_portfolio.py --sync -f    # 强制重新生成（覆盖已有）

数据库表:
  - portfolio_positions: 当前持仓表
    code, name, buy_date, cost_price, shares, updated_at
  - portfolio_trades: 交易记录表
    trade_date, trade_time, trade_type(buy/sell), code, name,
    shares, price, amount, pnl

操作流程:
  买卖操作 → 主人告知 → 更新DB(INSERT/UPDATE/DELETE) → 运行 sync_portfolio --sync
"""
import sys, os, json
from datetime import datetime, date
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.dao import get_db

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POSITIONS_FILE = os.path.expanduser('~/.openclaw/workspace/POSITIONS.md')

def get_positions(db) -> list:
    """从数据库读当前持仓"""
    return db.fetchall("SELECT * FROM portfolio_positions ORDER BY buy_date")

def get_trades(db) -> list:
    """从数据库读交易记录"""
    return db.fetchall("SELECT * FROM portfolio_trades ORDER BY trade_date, id")

def calc_total_cash(db) -> float:
    """计算可用资金 = 初始资金 + 卖出收入 - 买入支出"""
    trades = get_trades(db)
    initial = 81000.0
    spent = sum(float(t['amount']) for t in trades if t['trade_type'] == 'buy')
    earned = sum(float(t['amount']) for t in trades if t['trade_type'] == 'sell')
    holdings_cost = sum(float(p['cost_price']) * int(p['shares']) for p in get_positions(db))
    return initial + earned - spent

def sync_to_positions_md(db):
    """从数据库生成 POSITIONS.md"""
    positions = get_positions(db)
    trades = get_trades(db)
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    cash = calc_total_cash(db)

    lines = []
    lines.append('# 📊 当前持仓 (Position Book)')
    lines.append('')
    lines.append('> 持仓数据由 `sync_portfolio.py` 从 MySQL 自动生成。')
    lines.append(f'> 更新：{now}')
    lines.append('')
    lines.append('## 短线交易计划')
    lines.append('- 启动日：2026-04-28')
    lines.append('- 初始资金：8.1万元')
    lines.append('- 策略：AI选股 + 主人交易')
    lines.append('- 交易纪律：主板票、单票≤50%、止损-5%、大盘跌>1.5%不买')
    lines.append('')
    lines.append('## 当前持仓')
    lines.append('')
    lines.append('| # | 代码 | 名称 | 建仓日 | 成本 | 数量 | 市值 |')
    lines.append('|:-:|:----:|:----:|:-----:|:----:|:----:|:----:|')

    total_shares = 0
    total_cost = 0.0
    for i, p in enumerate(positions, 1):
        cost_price = float(p['cost_price'])
        shares = int(p['shares'])
        total_cost += cost_price * shares
        total_shares += shares
        buy_date = p['buy_date'].isoformat() if hasattr(p['buy_date'], 'isoformat') else str(p['buy_date'])
        lines.append(f"| {i} | {p['code']} | **{p['name']}** | {buy_date} | {cost_price:.3f} | {shares:,} | {cost_price * shares:,.0f} |")

    lines.append(f'| **合计** | | | | | **{total_shares:,}** | **{total_cost:,.0f}** |')
    lines.append('')
    lines.append(f'💰 可用资金: {cash:,.0f}')
    lines.append('')

    # 交易记录
    lines.append('## 交易记录')
    lines.append('')
    lines.append('| 日期 | 类型 | 代码 | 名称 | 数量 | 价格 | 金额 | 盈亏 |')
    lines.append('|:----:|:----:|:----:|:----:|:----:|:----:|:----:|:----:|')
    for t in trades:
        td = t['trade_date'].isoformat() if hasattr(t['trade_date'], 'isoformat') else str(t['trade_date'])
        ttype = '买入' if t['trade_type'] == 'buy' else '卖出'
        pnl_str = f"{float(t['pnl']):+}" if float(t['pnl']) else '—'
        lines.append(f"| {td} | {ttype} | {t['code']} | {t['name']} | {t['shares']} | {float(t['price']):.3f} | {float(t['amount']):,.2f} | {pnl_str} |")

    lines.append('')

    os.makedirs(os.path.dirname(POSITIONS_FILE), exist_ok=True)
    with open(POSITIONS_FILE, 'w') as f:
        f.write('\n'.join(lines) + '\n')
    print(f"✅ POSITIONS.md 已同步 ({POSITIONS_FILE})")

def show_summary(db):
    positions = get_positions(db)
    trades = get_trades(db)
    cash = calc_total_cash(db)
    cost_total = sum(float(p['cost_price']) * int(p['shares']) for p in positions)

    print("📊 持仓概览")
    print(f"   当前持仓: {len(positions)} 只")
    for p in positions:
        print(f"     {p['code']} {p['name']} 建仓{p['buy_date']} 成本{p['cost_price']} {p['shares']}股")
    print(f"   持仓成本: {cost_total:,.0f} | 可用资金: {cash:,.0f} | 总资产: {cash + cost_total:,.0f}")
    print(f"\n📋 交易记录: {len(trades)} 笔")
    for t in trades:
        ttype = '🟢买入' if t['trade_type'] == 'buy' else '🔴卖出'
        pnl = f' pnl={t["pnl"]}' if float(t['pnl'] or 0) else ''
        print(f"     {ttype} {t['trade_date']} {t['code']} {t['name']} {t['shares']}股 @{t['price']}{pnl}")

if __name__ == '__main__':
    db = get_db()
    if '--sync' in sys.argv:
        sync_to_positions_md(db)
    else:
        show_summary(db)
        print(f"\n用法: python3 sync_portfolio.py --sync")
