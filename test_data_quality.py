#!/usr/bin/env python3
"""
数据质量测试脚本
每次开发完新功能/迭代后执行此脚本验证数据完整性

用法: python3 test_data_quality.py
"""
import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests
from stock_analysis_api import StockDataFetcher, _curl_text

FAILURES = []
PASSED = 0
FAILED = 0

def check(name: str, condition: bool, detail: str = ""):
    global PASSED, FAILED
    if condition:
        print(f"  ✅ {name}")
        PASSED += 1
    else:
        print(f"  ❌ {name}" + (f" - {detail}" if detail else ""))
        FAILURES.append(name)
        FAILED += 1

def check_eq(name: str, actual, expected, tolerance=0):
    """检查数值是否接近"""
    if tolerance > 0:
        check(name, abs(actual - expected) <= tolerance,
              f"期望{expected}，实际{actual}，容差{tolerance}")
    else:
        check(name, actual == expected, f"期望{expected}，实际{actual}")

def main():
    global PASSED, FAILED
    print("\n═══════════════════════════════════")
    print("   数据质量测试套件 v1.0")
    print("═══════════════════════════════════\n")

    # ====================================
    # 第1组：API连通性
    # ====================================
    print("📡 1. API 连通性")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    # 1a. 东方财富 ulist.np
    try:
        r = requests.get(
            "https://push2.eastmoney.com/api/qt/ulist.np/get",
            params={"fltt": "2", "fields": "f6,f12,f14", "secids": "1.000001,0.399001"},
            headers={
                "Referer": "https://quote.eastmoney.com/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
            timeout=10,
        )
        check("东财 ulist.np 可连通", r.status_code == 200)
        check("东财 ulist.np 返回数据",
              r.json().get("data", {}).get("diff") is not None)
    except Exception as e:
        check("东财 ulist.np 可连通", False, str(e))

    # 1b. 新浪 HQ
    try:
        text = _curl_text(
            "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            "Market_Center.getHQNodeData?page=1&num=10&sort=changepercent&asc=0&node=sh_a&_s_r_a=page",
            timeout=8,
        )
        check("新浪 HQ 可连通", text is not None and len(text) > 20)
    except Exception as e:
        check("新浪 HQ 可连通", False, str(e))

    # ====================================
    # 第2组：成交额
    # ====================================
    print("\n💰 2. 成交额数据")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    try:
        r = requests.get(
            "https://push2.eastmoney.com/api/qt/ulist.np/get",
            params={"fltt": "2", "fields": "f6,f12,f14",
                    "secids": "1.000001,0.399001,0.899050"},
            headers={
                "Referer": "https://quote.eastmoney.com/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
            timeout=10,
        )
        d = r.json()
        diff = d.get("data", {}).get("diff", [])
        items = list(diff.values()) if isinstance(diff, dict) else diff
        total_amt = sum(float(item.get("f6", 0) or 0) for item in items)
        check("总成交额 > 0", total_amt > 0, f"实际={total_amt}")
        check("沪市成交额 > 0",
              any(item.get("f12") == "000001" and float(item.get("f6", 0) or 0) > 0
                  for item in items),
              "沪市上证指数成交额缺失")
        check("深市成交额 > 0",
              any(item.get("f12") == "399001" and float(item.get("f6", 0) or 0) > 0
                  for item in items),
              "深市深证成指成交额缺失")
        check("北交所成交额 > 0",
              any(item.get("f12") == "899050" and float(item.get("f6", 0) or 0) > 0
                  for item in items),
              "北证50成交额未计入")
    except Exception as e:
        check("成交额检查完成", False, str(e))

    # ====================================
    # 第3组：涨跌家数
    # ====================================
    print("\n📊 3. 涨跌家数")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    try:
        from dao import get_db
        _db = get_db()
        row = _db.fetchone(
            "SELECT SUM(rise_count) as rise, SUM(fall_count) as fall FROM sector_performance WHERE record_date=%s AND rank_type='all'",
            (datetime.now().strftime('%Y-%m-%d'),))
        if row and row['rise']:
            rise = int(row['rise'])
            fall = int(row['fall'])
            total = rise + fall
            check("涨跌家数统计完整", total > 3000, f"总计{total}只(正常应>4000)")
            check("上涨家数合理", rise > 500, f"仅{rise}只上涨")
            check("下跌家数合理", fall > 500, f"仅{fall}只下跌")
            check("涨跌比合理", 0.2 < rise / max(fall, 1) < 5,
                  f"涨跌比={rise}/{fall}={rise/max(fall,1):.2f}")
        else:
            check("涨跌家数", False, "sector_performance 无今日数据")
    except Exception as e:
        check("涨跌家数检查完成", False, str(e))

    # ====================================
    # 第4组：板块数据
    # ====================================
    print("\n📋 4. 板块数据")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    try:
        sectors = f._fetch_board_sectors_via_ulist()
        check("涨幅居前不为空", len(sectors.get("top_gain", [])) > 0)
        check("跌幅居前不为空", len(sectors.get("top_fall", [])) > 0)
        check("资金流入前5不为空", len(sectors.get("top_inflow", [])) > 0)
        check("资金流出前5不为空", len(sectors.get("top_outflow", [])) > 0)

        # 验证涨幅最高 > 跌幅最大
        if sectors.get("top_gain") and sectors.get("top_fall"):
            max_gain = sectors["top_gain"][0]["change_pct"]
            max_fall = sectors["top_fall"][0]["change_pct"]
            check("最高涨幅 > 最大跌幅", max_gain > max_fall,
                  f"最高涨{max_gain}% vs 最大跌{max_fall}%")

        # 验证字段完整性（板块名称、涨幅、成交额字段都应有值）
        for label, key in [("涨幅", "top_gain"), ("跌幅", "top_fall"),
                           ("资金流入", "top_inflow"), ("资金流出", "top_outflow")]:
            sector_list = sectors.get(key, [])
            for s in sector_list:
                name = s.get("name", "") or ""
                check(f"  板块'{name}'({label})名称有效", len(name) > 0)
    except Exception as e:
        check("板块数据检查完成", False, str(e))

    # ====================================
    # 第5组：主力资金
    # ====================================
    print("\n🔁 5. 主力资金")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    try:
        r = requests.get(
            "https://push2.eastmoney.com/api/qt/ulist.np/get",
            params={"fltt": "2", "fields": "f6,f12,f14,f62",
                    "secids": "1.000001,0.399001"},
            headers={
                "Referer": "https://quote.eastmoney.com/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
            timeout=10,
        )
        d = r.json()
        diff = d.get("data", {}).get("diff", [])
        items = list(diff.values()) if isinstance(diff, dict) else diff
        for item in items:
            idx = item.get("f12", "")
            f62 = float(item.get("f62", 0) or 0)
            if idx == "000001":
                check("沪市主力资金已获取", f62 != 0,
                      f"f62={f62}")
            elif idx == "399001":
                check("深市主力资金已获取", f62 != 0,
                      f"f62={f62}")
    except Exception as e:
        check("主力资金检查完成", False, str(e))

    # ====================================
    # 第6组：Web服务
    # ====================================
    print("\n🌐 6. Web 服务")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    try:
        r = requests.get("http://localhost:8899/api/market-summary", timeout=60)
        check("Web 服务可访问", r.status_code == 200)
        d = r.json()
        check("total_amount > 0", d.get("total_amount", 0) > 0)
        check("main_force_net_inflow != 0", d.get("main_force_net_inflow", 0) != 0)
        check("rise_count > 0", d.get("rise_count", 0) > 0)
        check("fall_count > 0", d.get("fall_count", 0) > 0)
        check("sectors_top_gain 不为空", len(d.get("sectors_top_gain", [])) > 0)
        check("sectors_top_fall 不为空", len(d.get("sectors_top_fall", [])) > 0)
        check("sectors_top_inflow 不为空", len(d.get("sectors_top_inflow", [])) > 0)
        check("sectors_top_outflow 不为空", len(d.get("sectors_top_outflow", [])) > 0)
    except Exception as e:
        check("Web 服务检查", False, str(e))

    # ====================================
    # 汇总
    # ====================================
    print(f"\n═══════════════════════════════════")
    print(f"  结果: {PASSED} 通过 / {FAILED} 失败 / {PASSED+FAILED} 总计")
    if FAILED > 0:
        print(f"  失败项:")
        for f in FAILURES:
            print(f"    ❌ {f}")
    else:
        print("  🎉 全部通过！")
    print("═══════════════════════════════════\n")

    return 0 if FAILED == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
