"""
股票分析仪表盘 - Streamlit Web 界面 (可选)
"""
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
import time
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

# 确保能导入同级模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.stock_analysis_api import StockDataFetcher
from utils.data_parser import DataParser
from utils.strategy import ValueStrategy, MomentumStrategy
from utils.alert_system import AlertSystem
from utils.visualization import StockVisualizer

from utils.logger import setup_logger
logger = setup_logger("dashboard")


class DashboardApp:
    """Web 仪表盘 (纯 HTML)"""

    def __init__(self, data_dir: str = "./reports"):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.fetcher = StockDataFetcher()
        self.parser = DataParser()
        self.visualizer = StockVisualizer(data_dir)
        self.alert_system = AlertSystem()

    def generate_dashboard(self) -> str:
        """生成完整仪表盘"""
        index_data = self._collect_index_data()
        stock_data = self._collect_stock_data()
        alerts = self._check_alerts()

        html = self._render_html(index_data, stock_data, alerts)
        filepath = os.path.join(self.data_dir, "dashboard.html")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html)
        logger.info(f"仪表盘已生成: {filepath}")
        return filepath

    def _collect_index_data(self) -> Dict:
        """采集指数数据"""
        indexes = {
            "sh50": "上证 50",
            "sz100": "深证 100",
            "hs300": "沪深 300",
            "cyb": "创业板",
            "kc50": "科创板 50"
        }
        data = {}
        for code, name in indexes.items():
            raw = self.fetcher.fetch_index_data(code)
            if raw:
                parsed = self.parser.parse_index_data(raw)
                data[code] = {
                    "name": name,
                    "close": parsed.get("close", 0),
                    "change": (parsed.get("close", 0) - parsed.get("open", 0)),
                    "volume": parsed.get("volume", 0),
                }
        return data

    def _collect_stock_data(self) -> Dict:
        """采集个股数据"""
        stocks = {
            "贵州茅台": "600519",
            "宁德时代": "300750",
            "招商银行": "600036",
            "中国平安": "601318",
            "恒瑞医药": "600276",
        }
        data = {}
        for name, ticker in stocks.items():
            raw = self.fetcher.fetch_data(ticker)
            if raw:
                parsed = self.parser.parse_stock_data(raw)
                indicators = self.parser.calculate_technical_indicators([parsed])
                data[name] = {
                    "ticker": ticker,
                    "price": parsed.get("close", 0),
                    "volume": parsed.get("volume", 0),
                    "indicators": indicators
                }
        return data

    def _check_alerts(self) -> List[Dict]:
        """检查预警"""
        sample_data = {
            "ticker": "600036",
            "stock_name": "招商银行",
            "close": 35.50,
            "previous_close": 34.00,
            "volume": 50000000,
            "previous_volume": 30000000,
            "indicators": {
                "latest_rsi": 35,
                "latest_macd": 0.15,
                "latest_signal": 0.12,
                "latest_bb_upper": 36.80,
                "latest_bb_lower": 33.20
            }
        }
        alerts = self.alert_system.run_all_checks(sample_data)
        return [{
            "type": a.alert_type,
            "message": a.message,
            "severity": a.severity
        } for a in alerts]

    def _render_html(self, index_data: Dict, stock_data: Dict, alerts: List[Dict]) -> str:
        """渲染仪表盘 HTML"""
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M")

        # 构建指数表格
        index_rows = ""
        for code, info in index_data.items():
            change_class = "up" if info["change"] >= 0 else "down"
            index_rows += f"""
            <tr>
                <td>{info["name"]}</td>
                <td>{info["close"]:.2f}</td>
                <td class="{change_class}">{info["change"]:+.2f}</td>
                <td>{info["volume"]:,}</td>
            </tr>"""

        # 构建个股表格
        stock_rows = ""
        for name, info in stock_data.items():
            ind = info["indicators"]
            stock_rows += f"""
            <tr>
                <td>{name}<br><small>{info["ticker"]}</small></td>
                <td>{info["price"]:.2f}</td>
                <td>{info["volume"]:,}</td>
                <td>{ind.get("latest_rsi", "-"):.1f}</td>
                <td>{ind.get("latest_macd", "-"):.4f}</td>
                <td>{ind.get("latest_k", "-"):.1f}/{ind.get("latest_d", "-"):.1f}</td>
            </tr>"""

        # 构建预警列表
        alert_items = ""
        for alert in alerts:
            severity_class = alert["severity"]
            alert_items += f'<li class="{severity_class}"><strong>{alert["type"]}</strong>: {alert["message"]}</li>'

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>股票分析仪表盘</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: #f0f2f5; color: #333; }}
        .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px; text-align: center; }}
        .header h1 {{ font-size: 28px; }}
        .header p {{ opacity: 0.9; margin-top: 5px; }}
        .container {{ max-width: 1200px; margin: 20px auto; padding: 0 20px; }}
        .card {{ background: white; border-radius: 10px; padding: 20px; margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
        .card h2 {{ margin-bottom: 15px; font-size: 20px; color: #444; border-bottom: 2px solid #f0f2f5; padding-bottom: 10px; }}
        table {{ width: 100%; border-collapse: collapse; }}
        th {{ background: #f8f9fa; padding: 10px; text-align: left; font-weight: 600; }}
        td {{ padding: 10px; border-bottom: 1px solid #eee; }}
        .up {{ color: #22c55e; }}
        .down {{ color: #ef4444; }}
        .info {{ color: #3b82f6; }}
        .warning {{ color: #f59e0b; font-weight: 600; }}
        .critical {{ color: #ef4444; font-weight: 700; }}
        ul {{ list-style: none; }}
        li {{ padding: 8px 12px; margin-bottom: 5px; background: #f8f9fa; border-radius: 6px; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 15px; }}
        .stat-card {{ background: white; border-radius: 10px; padding: 15px; text-align: center; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }}
        .stat-card .value {{ font-size: 24px; font-weight: bold; }}
        .stat-card .label {{ color: #666; margin-top: 5px; font-size: 14px; }}
        .footer {{ text-align: center; padding: 20px; color: #666; font-size: 12px; }}
        @media (max-width: 768px) {{ .container {{ padding: 10px; }} table {{ font-size: 14px; }} }}
    </style>
</head>
<body>
    <div class="header">
        <h1>📊 股票分析仪表盘</h1>
        <p>更新时间: {current_time}</p>
    </div>

    <div class="container">
        <div class="card">
            <h2>📈 主要指数</h2>
            <table>
                <thead><tr><th>指数</th><th>价格</th><th>涨跌</th><th>成交量</th></tr></thead>
                <tbody>{index_rows}</tbody>
            </table>
        </div>

        <div class="card">
            <h2>🔍 重点个股</h2>
            <table>
                <thead><tr><th>名称</th><th>价格</th><th>成交量</th><th>RSI</th><th>MACD</th><th>K/D</th></tr></thead>
                <tbody>{stock_rows}</tbody>
            </table>
        </div>

        <div class="card">
            <h2>🚨 预警信息</h2>
            <ul>{alert_items if alert_items else "<li>暂无预警</li>"}</ul>
        </div>

        <div class="card">
            <h2>📋 快捷操作</h2>
            <div class="grid">
                <div class="stat-card"><div class="value">📄</div><div class="label">生成分析报告</div></div>
                <div class="stat-card"><div class="value">🔔</div><div class="label">配置预警规则</div></div>
                <div class="stat-card"><div class="value">💹</div><div class="label">选股策略</div></div>
                <div class="stat-card"><div class="value">🔄</div><div class="label">数据刷新</div></div>
            </div>
        </div>
    </div>

    <div class="footer">
        <p>Stock Analysis System v1.0 | Powered by Python</p>
    </div>
</body>
</html>"""
        return html


def main():
    """主入口"""
    import argparse
    parser = argparse.ArgumentParser(description="股票分析仪表盘")
    parser.add_argument("--output", "-o", default="./reports", help="输出目录")
    parser.add_argument("--open", action="store_true", help="自动打开浏览器")
    args = parser.parse_args()

    app = DashboardApp(data_dir=args.output)
    filepath = app.generate_dashboard()

    print(f"✅ 仪表盘已生成: {filepath}")
    print("💡 在浏览器中打开查看")

    if args.open:
        import webbrowser
        webbrowser.open(f"file://{os.path.abspath(filepath)}")


if __name__ == "__main__":
    main()
