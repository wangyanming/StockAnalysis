"""
可视化模块 - 股票数据及分析结果的可视化
"""
from typing import Dict, List, Optional, Tuple
import pandas as pd
import numpy as np
import json
import webbrowser
from datetime import datetime


class StockVisualizer:
    """股票数据可视化器"""

    def __init__(self, output_dir: str = "."):
        self.output_dir = output_dir

    def generate_chart_data(self, df: pd.DataFrame, indicators: Dict) -> Dict:
        """生成适合图表显示的数据"""
        chart_data = {
            "dates": df.index.strftime("%Y-%m-%d").tolist()
            if hasattr(df.index, "strftime") else df.index.tolist(),
            "close": df["close"].tolist() if "close" in df else [],
            "open": df["open"].tolist() if "open" in df else [],
            "high": df["high"].tolist() if "high" in df else [],
            "low": df["low"].tolist() if "low" in df else [],
            "volume": df["volume"].tolist() if "volume" in df else [],
            "rsi": indicators.get("latest_rsi", None),
            "macd": indicators.get("latest_macd", None),
            "signal": indicators.get("latest_signal", None),
            "macd_hist": indicators.get("latest_macd_hist", None),
            "bb_upper": indicators.get("latest_bb_upper", None),
            "bb_lower": indicators.get("latest_bb_lower", None),
            "k": indicators.get("latest_k", None),
            "d": indicators.get("latest_d", None),
            "j": indicators.get("latest_j", None),
        }

        if "dataframe" in indicators:
            df_full = indicators["dataframe"]
            chart_data["full_rsi"] = df_full.index.strftime("%Y-%m-%d").tolist()

        return chart_data

    def generate_multi_chart_data(self, df: pd.DataFrame) -> Dict:
        """生成多指标图表数据"""
        return {
            "dates": df.index.strftime("%Y-%m-%d").tolist() if hasattr(df.index, "strftime") else df.index.tolist(),
            "close": df["close"].tolist() if "close" in df else [],
            "volume": df["volume"].tolist() if "volume" in df else [],
        }

    def _export_html(self, html_content: str, filename: str = "chart.html") -> str:
        """导出 HTML 文件"""
        filepath = f"{self.output_dir}/{filename}"
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html_content)
        return filepath

    def create_analysis_report(self, stock_name: str, data: Dict, indicators: Dict) -> str:
        """生成分析报告"""
        report = f"""
        <!DOCTYPE html>
        <html>
        <head><meta charset="utf-8"><title>{stock_name} 分析报告</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }}
            .container {{ max-width: 1200px; margin: auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
            h1 {{ color: #333; }}
            .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 20px; }}
            .card {{ background: white; border: 1px solid #ddd; border-radius: 6px; padding: 15px; }}
            .card h3 {{ margin-top: 0; color: #666; }}
            .value {{ font-size: 24px; font-weight: bold; color: #333; }}
            .tag {{ display: inline-block; padding: 2px 8px; border-radius: 3px; font-size: 12px; margin: 2px; }}
            .tag-green {{ background: #d4edda; color: #155724; }}
            .tag-red {{ background: #f8d7da; color: #721c24; }}
            .tag-yellow {{ background: #fff3cd; color: #856404; }}
            table {{ width: 100%; border-collapse: collapse; margin: 10px 0; }}
            th, td {{ padding: 8px; text-align: left; border-bottom: 1px solid #ddd; }}
            th {{ background: #f8f9fa; }}
        </style>
        </head>
        <body>
        <div class="container">
            <h1>{stock_name}</h1>
            <p>报告生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M")}</p>

            <div class="grid">
                <div class="card">
                    <h3>当前价格</h3>
                    <div class="value">{data.get("close", "N/A"):.2f}</div>
                    <p>开盘: {data.get("open", "N/A"):.2f} | 最高: {data.get("high", "N/A"):.2f} | 最低: {data.get("low", "N/A"):.2f}</p>
                </div>
                <div class="card">
                    <h3>成交量</h3>
                    <div class="value">{data.get("volume", "N/A"):,}</div>
                    <p>成交额: {data.get("amount", 0) / 100000000:.2f} 亿</p>
                </div>
            </div>

            <h2>技术指标</h2>
            <table>
                <tr><th>指标</th><th>值</th><th>信号</th></tr>
                <tr><td>RSI(14)</td><td>{indicators.get("latest_rsi", "N/A"):.2f}</td>
                    <td>{'<span class="tag tag-green">超买</span>' if isinstance(indicators.get("latest_rsi"), (int, float)) and indicators["latest_rsi"] > 70 else '<span class="tag tag-red">超卖</span>' if isinstance(indicators.get("latest_rsi"), (int, float)) and indicators["latest_rsi"] < 30 else '<span class="tag tag-yellow">中性</span>'}</td></tr>
                <tr><td>MACD</td><td>{indicators.get("latest_macd", "N/A"):.2f}</td>
                    <td>{'<span class="tag tag-green">多头</span>' if isinstance(indicators.get("latest_macd"), (int, float)) and indicators["latest_macd"] > 0 else '<span class="tag tag-red">空头</span>'}</td></tr>
                <tr><td>布林上轨</td><td>{indicators.get("latest_bb_upper", "N/A"):.2f}</td><td>压力位</td></tr>
                <tr><td>布林下轨</td><td>{indicators.get("latest_bb_lower", "N/A"):.2f}</td><td>支撑位</td></tr>
            </table>
        </div>
        </body>
        </html>
        """
        return self._export_html(report, f"{stock_name}_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html")

    def create_sector_comparison(self, sector_data: Dict) -> str:
        """生成行业对比图"""
        html = f"""
        <!DOCTYPE html>
        <html>
        <head><meta charset="utf-8"><title>行业分析</title>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        </head>
        <body>
        <div style="max-width:800px; margin:20px auto; padding:20px;">
            <h1>{sector_data['sector']} 行业分析</h1>
            <canvas id="sectorChart" width="800" height="400"></canvas>
            <script>
            const ctx = document.getElementById('sectorChart').getContext('2d');
            new Chart(ctx, {{
                type: 'bar',
                data: {{
                    labels: { json.dumps(sector_data.get('stocks', [])) },
                    datasets: [{{
                        label: '均价',
                        data: { json.dumps(sector_data.get('average_price', 0)) },
                        backgroundColor: 'rgba(54, 162, 235, 0.2)',
                        borderColor: 'rgba(54, 162, 235, 1)',
                        borderWidth: 1
                    }}]
                }},
                options: {{ responsive: true }}
            }});
            </script>
        </div>
        </body>
        </html>
        """
        return self._export_html(html, f"sector_{sector_data.get('sector', 'unknown')}.html")


# Helper for JSON serialization
def json_serialize(obj):
    """JSON serialization helper"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    return str(obj)
