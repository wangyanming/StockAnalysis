"""
股票分析系统 - 主程序入口
"""
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
import json
import argparse
import time
import threading
from datetime import datetime, timedelta
import logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stock_analysis_api import StockDataFetcher
from strategy import ValueStrategy, MomentumStrategy
from alert_system import AlertSystem
from visualization import StockVisualizer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)


class StockAnalysisApp:
    """股票分析系统"""

    def __init__(self):
        self.fetcher = StockDataFetcher()
        self.alert_system = AlertSystem()
        self.visualizer = StockVisualizer()
        self.strategies = {
            "value": ValueStrategy(),
            "momentum": MomentumStrategy(),
        }

    def clear(self):
        os.system("cls" if os.name == "nt" else "clear")

    def menu(self):
        """交互菜单"""
        while True:
            self.clear()
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            print(f"\n  {'═' * 50}")
            print(f"  📊  股票分析系统 v1.0    {now}")
            print(f"  {'═' * 50}")
            print(f"  │  1. 📈 市场概况              │")
            print(f"  │  2. 🔍 个股查询              │")
            print(f"  │  3. 🏢 指数分析              │")
            print(f"  │  4. 🏭 行业板块              │")
            print(f"  │  5. 🚨 预警检查              │")
            print(f"  │  6. 📄 生成报告              │")
            print(f"  │  0. 🚪 退出                  │")
            print(f"  {'─' * 50}")

            choice = input("\n  请选择 [0-6]: ").strip()

            if choice == "0" or choice.lower() in ("q", "quit", "exit"):
                print("\n  再见! 👋\n")
                break
            elif choice == "1":
                self.cmd_overview()
            elif choice == "2":
                self.cmd_stock_detail()
            elif choice == "3":
                self.cmd_index()
            elif choice == "4":
                self.cmd_sector()
            elif choice == "5":
                self.cmd_alert()
            elif choice == "6":
                self.cmd_report()
            else:
                print(f"\n  ⚠️  无效选择: {choice}")
                input("  按 Enter 继续...")

    def cmd_overview(self):
        """市场概况"""
        self.clear()
        print(f"\n  {'═' * 52}")
        print(f"   📊 市场概况")
        print(f"  {'═' * 52}")

        ov = self.fetcher.get_market_overview()

        print(f"\n  📈 主要指数:")
        print(f"  {'指数':<12} {'最新价':>10} {'涨跌幅':>10}")
        print(f"  {'─' * 34}")
        for data in ov.get("indexes", {}).values():
            chg = data.get("change_pct", 0)
            flag = "🔴" if chg >= 0 else "🟢"
            print(f"  {flag} {data['name']:<10} {data.get('current_price', 0):>10.2f} {chg:>+9.2f}%")

        print(f"\n  🔍 热门个股:")
        print(f"  {'名称':<12} {'最新价':>10} {'涨跌幅':>10}")
        print(f"  {'─' * 34}")
        for name, data in ov.get("popular_stocks", {}).items():
            chg = data.get("change_pct", 0)
            flag = "🔴" if chg >= 0 else "🟢"
            print(f"  {flag} {name:<10} {data.get('current_price', 0):>10.2f} {chg:>+9.2f}%")

        print(f"\n  ⏱️  {ov.get('updated_at', '')}")
        input("\n  按 Enter 返回...")

    def cmd_stock_detail(self):
        """个股详情"""
        self.clear()
        print(f"\n  {'─' * 50}")
        print(f"  🔍 个股查询")

        # 列出热门股
        print(f"\n  选择股票:")
        stocks = list(self.fetcher.POPULAR_STOCKS.items())
        for i, (code, name) in enumerate(stocks, 1):
            print(f"    {i}. {name} ({code})")
        print(f"    {len(stocks)+1}. 自定义代码")

        try:
            choice = int(input(f"\n  输入编号 [1-{len(stocks)+1}]: ").strip())
            if 1 <= choice <= len(stocks):
                code = stocks[choice - 1][0]
            else:
                code = input("  输入股票代码 (如 600519): ").strip()
        except (ValueError, IndexError):
            code = input("  输入股票代码: ").strip()
            if not code:
                return

        data = self.fetcher.fetch_stock_quote(code)
        if not data:
            print(f"\n  ⚠️  无法获取 {code} 数据")
            input("  按 Enter 返回...")
            return

        print(f"\n  {'═' * 40}")
        print(f"   {data.get('name', '')} ({data.get('symbol', '')})")
        print(f"  {'═' * 40}")
        print(f"    最新价:  {data.get('current_price', 0):.2f}")
        print(f"    涨跌幅:  {data.get('change_pct', 0):+.2f}%")
        print(f"    今开:    {data.get('open', 0):.2f}")
        print(f"    最高:    {data.get('high', 0):.2f}")
        print(f"    最低:    {data.get('low', 0):.2f}")
        print(f"    昨收:    {data.get('pre_close', 0):.2f}")

        # 简单技术分析
        cp = data.get("current_price", 0)
        pc = data.get("pre_close", 0)
        if pc > 0:
            ratio = cp / pc - 1
            if abs(ratio) > 0.05:
                print(f"    📊 波动较大, 注意风险")
            elif ratio > 0.03:
                print(f"    📈 强势上涨")
            elif ratio < -0.03:
                print(f"    📉 明显下跌")
            else:
                print(f"    📊 窄幅震荡")

        input("\n  按 Enter 返回...")

    def cmd_index(self):
        """指数分析"""
        self.clear()
        print(f"\n  {'─' * 50}")
        print(f"  📈 指数分析")

        for code, name in self.fetcher.INDEX_NAMES.items():
            data = self.fetcher.fetch_index_quote(code)
            if data:
                chg = data.get("change_pct", 0)
                flag = "🔴" if chg >= 0 else "🟢"
                print(f"  {flag} {name:<12} {data.get('current_price', 0):>10.2f}  {chg:>+8.2f}%")
            else:
                print(f"  ⚪ {name:<12} 暂无可获取数据")

        input("\n  按 Enter 返回...")

    def cmd_sector(self):
        """行业板块"""
        self.clear()
        print(f"\n  {'─' * 50}")
        print(f"  🏭 行业板块")
        sectors = self.fetcher.fetch_sector_data()
        if sectors:
            for s in sectors:
                chg = s.get("change_pct", 0)
                flag = "🔴" if chg >= 0 else "🟢"
                print(f"  {flag} {s['name']:<10} {chg:>+7.2f}%")
        else:
            print("  ⚠️  暂无行业板块数据")
        input("\n  按 Enter 返回...")

    def cmd_alert(self):
        """预警检查"""
        self.clear()
        print(f"\n  {'─' * 50}")
        print(f"  🚨 预警检查")

        alerts = []
        for code, name in self.fetcher.POPULAR_STOCKS.items():
            data = self.fetcher.fetch_stock_quote(code)
            if not data:
                continue

            result = self.alert_system.run_all_checks({
                "ticker": code,
                "stock_name": name,
                "close": data.get("current_price", 0),
                "previous_close": data.get("pre_close", 0),
                "volume": data.get("volume", 0),
                "previous_volume": 0,
            })
            if result:
                alerts.extend(result)

        if alerts:
            print(f"\n  ⚠️  触发 {len(alerts)} 条预警:\n")
            for a in alerts:
                icon = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}.get(a.severity, "ℹ️")
                print(f"  {icon} [{a.severity.upper()}] {a.message}")
        else:
            print(f"\n  ✅ 暂无预警触发")

        input("\n  按 Enter 返回...")

    def cmd_report(self):
        """生成 HTML 报告"""
        self.clear()
        print(f"\n  {'─' * 50}")
        print(f"  📄 生成 HTML 报告...")

        ov = self.fetcher.get_market_overview()
        now = datetime.now().strftime("%Y-%m-%d %H:%M")

        index_rows = ""
        for data in ov.get("indexes", {}).values():
            chg = data.get("change_pct", 0)
            cls = "up" if chg >= 0 else "down"
            index_rows += f"<tr><td>{data['name']}</td><td>{data.get('current_price', 0):.2f}</td><td class='{cls}'>{chg:+.2f}%</td></tr>\n"

        stock_rows = ""
        for name, data in ov.get("popular_stocks", {}).items():
            chg = data.get("change_pct", 0)
            cls = "up" if chg >= 0 else "down"
            stock_rows += f"<tr><td>{name}</td><td>{data.get('current_price', 0):.2f}</td><td class='{cls}'>{chg:+.2f}%</td></tr>\n"

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>股票分析报告</title>
<style>
* {{ margin: 0; padding: 0; }}
body {{ font: 16px -apple-system, sans-serif; background: #f0f2f5; }}
.container {{ max-width: 800px; margin: 30px auto; background: #fff; border-radius: 12px; overflow: hidden; box-shadow: 0 2px 20px rgba(0,0,0,0.08); }}
.header {{ background: linear-gradient(135deg, #667eea, #764ba2); color: #fff; padding: 30px; text-align: center; }}
.header h1 {{ font-size: 28px; }}
.header p {{ opacity: .85; margin-top: 6px; font-size: 14px; }}
.content {{ padding: 24px; }}
h2 {{ font-size: 18px; margin: 20px 0 12px; padding-bottom: 8px; border-bottom: 2px solid #f0f2f5; }}
table {{ width: 100%; border-collapse: collapse; margin: 8px 0 16px; }}
th {{ background: #f8f9fa; padding: 10px 12px; text-align: left; font-weight: 600; font-size: 14px; }}
td {{ padding: 10px 12px; border-bottom: 1px solid #f0f2f5; font-size: 15px; }}
.up {{ color: #ef4444; font-weight: 600; }}
.down {{ color: #22c55e; font-weight: 600; }}
.footer {{ text-align: center; padding: 20px; color: #999; font-size: 12px; }}
</style></head>
<body>
<div class="container">
<div class="header"><h1>📊 市场行情报告</h1><p>生成时间: {now}</p></div>
<div class="content">
<h2>📈 主要指数</h2>
<table><tr><th>指数</th><th>最新价</th><th>涨跌幅</th></tr>{index_rows}</table>
<h2>🔍 热门个股</h2>
<table><tr><th>名称</th><th>最新价</th><th>涨跌幅</th></tr>{stock_rows}</table>
</div>
<div class="footer"><p>Stock Analysis System | {now}</p></div>
</div></body></html>"""

        report_path = os.path.abspath("market_report.html")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"\n  ✅ 报告已生成: {report_path}")
        try:
            import webbrowser
            webbrowser.open(f"file://{report_path}")
            print("  🌐 浏览器已自动打开")
        except Exception:
            pass
        input("\n  按 Enter 返回...")


def main():
    app = StockAnalysisApp()

    if len(sys.argv) > 1:
        parser = argparse.ArgumentParser(description="股票分析系统")
        parser.add_argument("--mode", choices=["overview", "stock", "index", "report"],
                          default="overview")
        parser.add_argument("--code", help="股票代码")
        args = parser.parse_args()

        if args.mode == "overview":
            import json
            ov = app.fetcher.get_market_overview()
            print(json.dumps(ov, ensure_ascii=False, indent=2, default=str))
        elif args.mode == "stock":
            if not args.code:
                args.code = "600519"
            d = app.fetcher.fetch_stock_quote(args.code)
            if d:
                for k, v in d.items():
                    print(f"  {k}: {v}")
        elif args.mode == "index":
            for code in app.fetcher.INDEX_CODES:
                d = app.fetcher.fetch_index_quote(code)
                if d:
                    print(f"  {d['name']}: {d.get('current_price', 0):.2f} ({d.get('change_pct', 0):+.2f}%)")
        elif args.mode == "report":
            app.cmd_report()
    else:
        app.menu()


def schedule_close_task():
    """定时调度收盘任务（每天 15:30 执行）"""
    def _run():
        while True:
            now = datetime.now()
            # 每天 15:30 执行，避开周末
            if now.weekday() < 5:  # 周一到周五
                target = now.replace(hour=15, minute=30, second=0, microsecond=0)
                if now >= target and (now - target).seconds < 300:
                    # 在 15:30~15:35 窗口内执行
                    from close_task import daily_close_task
                    try:
                        result = daily_close_task()
                        logger.info(f"收盘任务完成: {json.dumps(result, ensure_ascii=False)}")
                    except Exception as e:
                        logger.error(f"收盘任务执行失败: {e}")
                    time.sleep(3600)  # 避免重复执行

            time.sleep(60)  # 每分钟检查一次

    t = threading.Thread(target=_run, daemon=True, name="close-task-scheduler")
    t.start()
    logger.info("收盘任务调度已启动 (每天 15:30 执行)")
    return t


def main_with_schedule():
    """主入口 - 带收盘定时调度"""
    logger.info("=" * 50)
    logger.info("📊 股票分析系统启动")
    logger.info("=" * 50)

    # 启动收盘定时调度
    schedule_close_task()

    # 如果命令行指定了模式，按模式执行
    if len(sys.argv) > 1:
        # 处理 web 相关命令
        if len(sys.argv) >= 2 and sys.argv[1] == "--start-web":
            logger.info("启动 Web 服务 (带收盘调度)...")
            from web_server import start_server
            start_server()
        elif len(sys.argv) >= 2 and sys.argv[1] == "--start-daemon":
            logger.info("启动 Web 服务 (Daemon, 带收盘调度)...")
            from web_server import start_daemon
            start_daemon()
        elif len(sys.argv) >= 2 and sys.argv[1] == "--close-task":
            logger.info("手动执行收盘任务...")
            from close_task import daily_close_task
            result = daily_close_task()
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        else:
            main()
    else:
        logger.info("未指定模式，启动交互菜单")
        main()

    logger.info("=" * 50)
    logger.info("📊 股票分析系统关闭")
    logger.info("=" * 50)


if __name__ == "__main__":
    main_with_schedule()
