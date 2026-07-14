#!/usr/bin/env python3
"""
盘中监控模版升级 Demo
东财 push2 实时获取并展示
"""
import json, urllib.request, re

def fetch_market_data():
    url = "http://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&fields=f2,f3,f4,f6,f12,f14,f104,f105,f106,f62,f66,f69,f72,f75,f78,f81,f84,f87&secids=1.000001,0.399001&cb=j"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    resp = urllib.request.urlopen(req, timeout=5)
    raw = resp.read().decode('utf-8')
    # 去除 cb=j( 开头和 ); 结尾
    json_str = raw[3:].rstrip(';').rstrip(')')
    data = json.loads(json_str)
    items = data.get('data', {}).get('diff', [])
    result = {}
    for item in items:
        code = item.get('f12', '')
        result[code] = item
    return result

dd = fetch_market_data()
sh = dd.get('000001', {})
sz = dd.get('399001', {})

def fmt_money(val):
    if not isinstance(val, (int, float)):
        return '?'
    v = val / 1e8
    if v > 0:
        return f"+{v:.0f}亿"
    elif v < 0:
        return f"{v:.0f}亿"
    return "0亿"

rise_total = (sh.get('f104', 0) or 0) + (sz.get('f104', 0) or 0)
fall_total = (sh.get('f105', 0) or 0) + (sz.get('f105', 0) or 0)
flat_total = (sh.get('f106', 0) or 0) + (sz.get('f106', 0) or 0)
amt_total = ((sh.get('f6', 0) or 0) + (sz.get('f6', 0) or 0)) / 1e8
main_flow = ((sh.get('f62', 0) or 0) + (sz.get('f62', 0) or 0))
retail_flow = ((sh.get('f84', 0) or 0) + (sz.get('f84', 0) or 0))

sh_chg = sh.get('f3', 0) or 0
sz_chg = sz.get('f3', 0) or 0

# ====== 输出 Demo ======
print("=" * 70)
print("📋 盘中监控 — 升级模版 Demo")
print("=" * 70)
print()

# 1️⃣ 大盘概况（三段合并+新增指标）
print("**1️⃣ 大盘概况**")

# 指数行
sh_idx = f"🟢 上证 {sh_chg:+.2f}%" if sh_chg >= 0 else f"🔴 上证 {sh_chg:+.2f}%"
sz_idx = f"🟢 深证 {sz_chg:+.2f}%" if sz_chg >= 0 else f"🔴 深证 {sz_chg:+.2f}%"
print(f"  {sh_idx} / {sz_idx}")

# 涨跌家数+成交额+资金流向 合并成一行（紧凑）
if rise_total >= fall_total:
    rf = f"🟢 涨{rise_total}家 🔴 跌{fall_total}家 ➖{flat_total}家"
else:
    rf = f"🔴 跌{fall_total}家 🟢 涨{rise_total}家 ➖{flat_total}家"

main_icon = "🟢" if main_flow >= 0 else "🔴"
retail_icon = "🔴" if retail_flow > 0 else "🟢"

# 方案A：两行紧凑（指数一行 + 盘面摘要一行）
print(f"  📊 盘面摘要：{rf}")
print(f"  💰 成交额 {amt_total:.0f}亿 ｜ {main_icon} 主力 {fmt_money(main_flow)} ｜ {retail_icon} 散户 {fmt_money(retail_flow)}")

# 风险提示
risks = []
if sh_chg < -1.5:
    risks.append('⚠️ 大盘跌超1.5%，风险警示！建议只卖不买')
elif sh_chg < -1:
    risks.append('⚠️ 上证跌超1%，注意风控')
if main_flow < -500e8:
    risks.append('⚠️ 主力流出超500亿，注意系统性风险')
if rise_total < fall_total * 0.5:
    risks.append('⚠️ 涨跌比严重失衡，市场弱势')
if risks:
    for r in risks:
        print(f"  {r}")

print()
print("**2️⃣ 今日市场动态**")
print("  （新闻部分保持不变）")
print()
print("**3️⃣ 持仓监控**")
print("  （持仓部分保持不变）")
print()
print("=" * 70)
print("📊 原始数据对照")
print(f"  上证: 涨{sh.get('f104',0)} 跌{sh.get('f105',0)} 平{sh.get('f106',0)} | +{sh_chg:+.2f}%")
print(f"  深证: 涨{sz.get('f104',0)} 跌{sz.get('f105',0)} 平{sz.get('f106',0)} | +{sz_chg:+.2f}%")
print(f"  成交额: 沪{sh.get('f6',0)/1e8:.0f}亿 + 深{sz.get('f6',0)/1e8:.0f}亿 = {amt_total:.0f}亿")
print(f"  主力: 沪{sh.get('f62',0)/1e8:.0f}亿 + 深{sz.get('f62',0)/1e8:.0f}亿 = {main_flow/1e8:.0f}亿")
print(f"  散户: 沪{sh.get('f84',0)/1e8:.0f}亿 + 深{sz.get('f84',0)/1e8:.0f}亿 = {retail_flow/1e8:.0f}亿")
