"""
Web 服务 - 股票分析系统 - 基于 HTTP 内置服务
"""
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import sys
import time
import threading
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stock_analysis_api import StockDataFetcher
from data_store import QuoteStore
from limit_up_analysis import LimitUpAnalyzer

f = StockDataFetcher()
store = QuoteStore()
zt = LimitUpAnalyzer()

# 简单缓存
_cache = {}
_cache_lock = threading.Lock()

def _cached_call(key: str, func, ttl: int = 60):
    """带缓存的函数调用"""
    now = time.time()
    with _cache_lock:
        if key in _cache and now - _cache[key]["ts"] < ttl:
            return _cache[key]["data"]
    data = func()
    with _cache_lock:
        _cache[key] = {"data": data, "ts": now}
    return data

PORT = int(os.environ.get("PORT", 8899))
HOST = os.environ.get("HOST", "0.0.0.0")
DATA_DIR = os.path.dirname(os.path.abspath(__file__))


class RequestHandler(BaseHTTPRequestHandler):
    """HTTP 请求处理"""

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            self.serve_html()
        elif path == "/api/market-overview":
            self.api_market_overview()
        elif path == "/api/market-summary":
            self.api_market_summary()
        elif path == "/api/index":
            self.api_index(params)
        elif path == "/api/stock":
            self.api_stock(params)
        elif path == "/api/sectors":
            self.api_sectors()
        elif path == "/api/history":
            self.api_history(params)

        elif path == "/api/limit-up":
            self.api_limit_up()
        elif path == "/api/limit-up/track":
            self.api_limit_up_track()
        elif path == "/api/limit-up/industry":
            self.api_limit_up_industry()
        elif path == "/api/limit-up/refresh":
            self.api_limit_up_refresh()
        elif path == "/api/limit-up/dates":
            self.api_limit_up_dates()
        elif path.startswith("/static/"):
            self.serve_static()
        else:
            self.send_json({"error": "not found"}, 404)

    def serve_html(self):
        """提供 HTML 页面"""
        html = self._build_html()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def send_json(self, data: dict, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"))

    def api_market_overview(self):
        """API: 市场概况"""
        ov = _cached_call("market_overview", f.get_market_overview, ttl=30)
        # 存储快照
        store.save_market_snapshot(ov)
        self.send_json(ov)

    def api_market_summary(self):
        """API: 今日市场总结 — 从 sector_performance 汇总"""
        raw = _cached_call("market_summary", f.get_market_summary, ttl=30)
        self.send_json({
            "total_amount": raw.get("total_amount", 0),
            "prev_amount": raw.get("prev_amount", 0),
            "amount_change": raw.get("amount_change", 0),
            "rise_count": raw.get("up_count", 0),
            "fall_count": raw.get("down_count", 0),
            "flat_count": 0,
            "updated_at": datetime.now().strftime("%H:%M:%S"),
        })

    def api_index(self, params):
        """API: 指数详情"""
        code = params.get("code", ["szzs"])[0]
        data = f.fetch_index_quote(code)
        kline = f.fetch_index_kline(code, days=30)
        if data:
            store.save_index_quote(code, data)
        result = {"quote": data}
        if kline is not None:
            kline_data = []
            for idx, row in kline.iterrows():
                kline_data.append({
                    "date": str(idx),
                    "open": float(row["open"]),
                    "close": float(row["close"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "volume": float(row.get("volume", 0)),
                })
            result["kline"] = kline_data
        self.send_json(result)

    def api_stock(self, params):
        """API: 个股详情"""
        secid = params.get("secid", ["1.600519"])[0]
        data = f.fetch_stock_quote(secid)
        if data:
            store.save_stock_quote(secid, data)
        self.send_json({"quote": data or {}})

    def api_sectors(self):
        """API: 行业板块"""
        sectors = f.fetch_sector_data()
        self.send_json({"sectors": sectors})

    def api_history(self, params):
        """API: 历史行情"""
        kind = params.get("kind", ["index"])[0]
        code = params.get("code", ["szzs"])[0]
        days = int(params.get("days", [30])[0])

        if kind == "index":
            data = store.get_index_history(code, days)
        else:
            data = store.get_stock_history(code, days)
        self.send_json({"data": data})

    def api_limit_up(self):
        """API: 当日涨停板"""
        params = parse_qs(urlparse(self.path).query)
        date_str = params.get("date", [datetime.now().strftime("%Y%m%d")])[0]
        data = zt.get_today_limit_up(date_str)
        self.send_json({"date": date_str, "count": len(data), "stocks": data})

    def api_limit_up_track(self):
        """API: 涨停追踪"""
        params = parse_qs(urlparse(self.path).query)
        min_boards = int(params.get("min_boards", [1])[0])
        status = params.get("status", [None])[0]
        data = zt.get_tracking_list(min_boards=min_boards, status=status)
        self.send_json({"count": len(data), "stocks": data})

    def api_limit_up_industry(self):
        """API: 行业涨停统计"""
        params = parse_qs(urlparse(self.path).query)
        date_str = params.get("date", [datetime.now().strftime("%Y%m%d")])[0]
        data = zt.get_industry_stats(date_str)
        self.send_json({"date": date_str, "count": len(data), "industries": data})

    def api_limit_up_refresh(self):
        """API: 手动刷新涨停数据"""
        date_str = datetime.now().strftime("%Y%m%d")
        result = zt.run_daily_analysis(date_str)
        self.send_json({"status": "ok", "result": result})

    def api_limit_up_dates(self):
        """API: 可用涨停数据日期列表"""
        dates = zt.get_available_dates()
        self.send_json({"dates": dates})

    def serve_static(self):
        """静态文件"""
        filepath = DATA_DIR + self.path
        if os.path.exists(filepath) and os.path.isfile(filepath):
            self.send_response(200)
            if filepath.endswith(".js"):
                self.send_header("Content-Type", "application/javascript")
            elif filepath.endswith(".css"):
                self.send_header("Content-Type", "text/css")
            elif filepath.endswith(".png"):
                self.send_header("Content-Type", "image/png")
            self.end_headers()
            with open(filepath, "rb") as f:
                self.wfile.write(f.read())
        else:
            self.send_json({"error": "file not found"}, 404)

    def log_message(self, format, *args):
        """自定义日志"""
        logger = f"[{datetime.now().strftime('%H:%M:%S')}] {args[0]} {args[1]} {args[2]}"
        print(f"  {logger}")

    def _build_html(self) -> str:
        """构建完整仪表盘 HTML"""
        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>股票分析系统</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font: 15px -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; background: #f0f2f5; color: #333; }}
.header {{ background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%); color: #fff; padding: 24px 20px; text-align: center; position: relative; }}
.header h1 {{ font-size: 24px; letter-spacing: 2px; }}
.header p {{ opacity: .7; margin-top: 6px; font-size: 13px; }}
.header .refresh {{ position: absolute; right: 20px; top: 50%; transform: translateY(-50%); background: rgba(255,255,255,.15); border: none; color: #fff; padding: 8px 18px; border-radius: 20px; cursor: pointer; font-size: 13px; }}
.header .refresh:hover {{ background: rgba(255,255,255,.25); }}
.header .refresh:disabled {{ opacity: .5; cursor: not-allowed; }}
.container {{ max-width: 1100px; margin: 0 auto; padding: 20px; }}
.card {{ background: #fff; border-radius: 10px; margin-bottom: 16px; overflow: hidden; box-shadow: 0 1px 4px rgba(0,0,0,.06); }}
.card-title {{ font-size: 15px; font-weight: 600; padding: 14px 18px; border-bottom: 1px solid #f0f2f5; color: #333; }}
.card-body {{ padding: 0; }}
table {{ width: 100%; border-collapse: collapse; }}
th {{ background: #f8f9fa; padding: 10px 14px; text-align: left; font-weight: 600; font-size: 13px; color: #555; }}
td {{ padding: 10px 14px; border-bottom: 1px solid #f5f5f5; font-size: 14px; }}
tr:hover td {{ background: #fafbfc; }}
.up {{ color: #ef4444; }}
.down {{ color: #22c55e; }}
.tag {{ display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 12px; font-weight: 600; }}
.tag-up {{ background: #fef2f2; color: #ef4444; }}
.tag-down {{ background: #f0fdf4; color: #22c55e; }}
.change-cell {{ display: flex; align-items: center; gap: 6px; }}
.loading {{ text-align: center; padding: 40px; color: #999; }}
.error {{ text-align: center; padding: 20px; color: #ef4444; }}
.last-update {{ text-align: right; padding: 6px 18px 14px; color: #999; font-size: 12px; }}
.refreshing {{ opacity: .6; pointer-events: none; }}
.grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
@media (max-width: 700px) {{ .grid-2 {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<div class="header">
    <h1>📊 股票分析系统</h1>
    <p id="updateTime">加载中...</p>
    <button class="refresh" id="refreshBtn" onclick="refresh()">🔄 刷新</button>
</div>
<div class="container">
    <div id="content"><div class="loading">⏳ 加载市场数据...</div></div>
</div>

<script>
async function refresh() {{
    const btn = document.getElementById('refreshBtn');
    btn.disabled = true;
    btn.textContent = '⏳ 刷新中...';
    document.getElementById('content').innerHTML = '<div class="loading">⏳ 刷新中...</div>';
    await loadData();
    btn.disabled = false;
    btn.textContent = '🔄 刷新';
}}

const up = d => d >= 0 ? 'up' : 'down';
const fmtPct = v => (v >= 0 ? '+' : '') + v.toFixed(2) + '%';
const icon = v => v >= 0 ? '🔴' : '🟢';
const boardLabel = b => b >= 3 ? '🔥' : b == 2 ? '⭐' : '';

async function loadData() {{
    try {{
        const mktRes = await fetch('/api/market-overview');
        const mkt = await mktRes.json();

        let html = '';

        // Tab navigation
        html += '<div style="display:flex;gap:8px;margin-bottom:12px;">';
        html += '<button class="tab-btn active" onclick="showTab(&#39;mkt&#39;)" style="flex:1;padding:10px;border:none;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;background:#667eea;color:#fff;">📈 行情</button>';
        html += '<button class="tab-btn" onclick="showTab(&#39;zt&#39;)" style="flex:1;padding:10px;border:none;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;background:#f0f2f5;color:#333;">🚀 涨停板</button>';
        html += '</div>';
        html += '<div id="tab-mkt">';

        // 指数
        html += '<div class="card"><div class="card-title">📈 主要指数</div><div class="card-body"><table><tr><th>指数</th><th>最新价</th><th>涨跌幅</th></tr>';
        for (const key of ['szzs','szcz','hs300','cyb','kc50']) {{
            const d = mkt.indexes[key];
            if (!d) continue;
            html += `<tr><td>${{icon(d.change_pct)}} ${{d.name}}</td><td>${{d.current_price.toFixed(2)}}</td><td class="${{up(d.change_pct)}} change-cell"><span class="tag tag-${{up(d.change_pct)}}">${{fmtPct(d.change_pct)}}</span></td></tr>`;
        }}
        html += '</table></div></div>';

        // 市场总结
        const msRes = await fetch('/api/market-summary');
        const ms = await msRes.json();

        html += '<div class="card"><div class="card-title">📊 今日市场总结</div><div class="card-body" style="padding:14px 18px;">';

        // 资金情况
        const totalAmt = (ms.total_amount / 1e8).toFixed(0);
        const amtChg = ms.amount_change;
        const amtChgStr = amtChg !== 0 ? '<span class="' + up(amtChg) + '">' + (amtChg > 0 ? '+' : '') + (amtChg / 1e8).toFixed(0) + '亿</span>' : '--';
        html += '<div style="margin-bottom:12px;"><strong>💰 资金情况</strong><br>' +
            '<span style="font-size:22px;font-weight:700;">' + totalAmt + '亿</span>' +
            '<span style="font-size:13px;color:#999;margin-left:8px;">两市总成交额</span>' +
            '<span style="font-size:13px;margin-left:12px;">较昨日: ' + amtChgStr + '</span>' +
        '</div>';

        // 资金流向
        html += '<div style="margin-bottom:12px;"><strong>🏦 资金流向</strong><br>' +
            '<span>主力资金: <span class="' + up(ms.main_force_net_inflow) + '">' + (ms.main_force_net_inflow >= 0 ? '+' : '') + (ms.main_force_net_inflow / 1e8).toFixed(2) + '亿</span></span>' +
            '<span style="margin-left:16px;">沪市: <span class="' + up(ms.sh_main_force_inflow) + '">' + (ms.sh_main_force_inflow / 1e8).toFixed(2) + '亿</span></span>' +
            '<span style="margin-left:16px;">深市: <span class="' + up(ms.sz_main_force_inflow) + '">' + (ms.sz_main_force_inflow / 1e8).toFixed(2) + '亿</span></span>' +
        '</div>';

        // 涨跌家数
        const totalStocks = ms.rise_count + ms.fall_count + ms.flat_count;
        html += '<div style="margin-bottom:12px;"><strong>📈 涨跌家数</strong><br>' +
            '<span class="up">上涨 ' + ms.rise_count + '</span>' +
            '<span style="margin-left:12px;" class="down">下跌 ' + ms.fall_count + '</span>' +
            '<span style="margin-left:12px;color:#999;">平盘 ' + ms.flat_count + '</span>' +
            '<span style="margin-left:12px;color:#999;">总数 ' + totalStocks + '</span>' +
        '</div>';

        // 涨幅居前板块
        if (ms.sectors_top_gain && ms.sectors_top_gain.length > 0) {{
            html += '<div style="margin-bottom:12px;"><strong>🏅 涨幅居前板块</strong><table><tr><th>板块</th><th>涨幅</th><th>成交额</th></tr>';
            for (const s of ms.sectors_top_gain) {{
                const pct = typeof s.change_pct === 'number' ? s.change_pct : 0;
                html += '<tr><td>' + s.name + '</td><td class="' + up(pct) + '">' + fmtPct(pct) + '</td><td>' + ((s.inflow || 0) / 1e8).toFixed(0) + '亿</td></tr>';
            }}
            html += '</table></div>';
        }}

        // 跌幅居前板块
        if (ms.sectors_top_fall && ms.sectors_top_fall.length > 0) {{
            html += '<div style="margin-bottom:12px;"><strong>📉 跌幅居前板块</strong><table><tr><th>板块</th><th>跌幅</th><th>成交额</th></tr>';
            for (const s of ms.sectors_top_fall) {{
                const pct = typeof s.change_pct === 'number' ? s.change_pct : 0;
                html += '<tr><td>' + s.name + '</td><td class="' + up(pct) + '">' + pct.toFixed(2) + '%</td><td>' + ((s.inflow || 0) / 1e8).toFixed(0) + '亿</td></tr>';
            }}
            html += '</table></div>';
        }}

        // 资金流入板块
        if (ms.sectors_top_inflow && ms.sectors_top_inflow.length > 0) {{
            html += '<div style="margin-bottom:12px;"><strong>⤴️ 资金流入前5板块</strong><table><tr><th>板块</th><th>资金流入</th></tr>';
            for (const s of ms.sectors_top_inflow) {{
                html += '<tr><td>' + s.name + '</td><td class="up">+' + ((s.inflow || 0) / 1e8).toFixed(2) + '亿</td></tr>';
            }}
            html += '</table></div>';
        }}

        // 资金流出板块
        if (ms.sectors_top_outflow && ms.sectors_top_outflow.length > 0) {{
            html += '<div style="margin-bottom:12px;"><strong>⤵️ 资金流出前5板块</strong><table><tr><th>板块</th><th>资金流出</th></tr>';
            for (const s of ms.sectors_top_outflow) {{
                html += '<tr><td>' + s.name + '</td><td class="down">' + ((s.inflow || 0) / 1e8).toFixed(2) + '亿</td></tr>';
            }}
            html += '</table></div>';
        }}

        html += '<div style="text-align:right;font-size:12px;color:#999;margin-top:4px;">🕐 ' + (ms.updated_at || '') + '</div>';
        html += '</div></div>';

        html += '<div class="last-update">🕐 ' + mkt.updated_at + '</div>';
        html += '</div>'; // end tab-mkt

        // ===== 涨停板 Tab =====
        html += '<div id="tab-zt" style="display:none;">';
        
        // 日期选择器
        const datesRes = await fetch('/api/limit-up/dates');
        const datesData = await datesRes.json();
        let dateOpts = '<option value="">请选择日期</option>';
        const todayStr = new Date().toISOString().slice(0,10).replace(/-/g, '');
        for (const d of datesData.dates) {{
            const label = d.slice(0,4) + '-' + d.slice(4,6) + '-' + d.slice(6,8);
            const sel = d === todayStr ? ' selected' : '';
            dateOpts += '<option value="' + d + '"' + sel + '>' + label + '</option>';
        }}
        html += '<div class="card"><div class="card-body" style="padding:12px 18px;display:flex;align-items:center;gap:10px;">';
        html += '<label style="font-weight:600;">📅 选择日期:</label>';
        html += '<select id="ztDateSelect" onchange="loadZtData()" style="padding:6px 10px;border:1px solid #ddd;border-radius:6px;font-size:14px;max-width:200px;">' + dateOpts + '</select>';
        html += '<button onclick="loadZtData()" style="padding:6px 14px;background:#667eea;color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:13px;">查询</button>';
        html += '</div></div>';
        
        // 占位容器
        html += '<div id="ztContent"></div>';

        html += '<div style="text-align:right;padding-bottom:14px;"><a href="/api/limit-up/refresh" target="_blank" style="color:#667eea;font-size:13px;">🔄 手动刷新涨停数据 →</a></div>';
        html += '</div>'; // end tab-zt

        document.getElementById('content').innerHTML = html;
        document.getElementById('updateTime').textContent = '更新时间: ' + mkt.updated_at;
    }} catch (e) {{
        document.getElementById('content').innerHTML = '<div class="error">❌ 加载失败: ' + e.message + '</div>';
    }}
}}

async function loadZtData() {{
    const sel = document.getElementById("ztDateSelect");
    const date = sel ? sel.value : "";
    if (!date) {{
        document.getElementById("ztContent").innerHTML = '<div style="text-align:center;padding:30px;color:#999;">请选择日期</div>';
        return;
    }}
    try {{
        const [ztRes, indRes] = await Promise.all([
            fetch('/api/limit-up?date=' + date),
            fetch('/api/limit-up/industry?date=' + date)
        ]);
        const zt = await ztRes.json();
        const ind = await indRes.json();

        let html = '';

        // 涨停统计
        html += '<div class="card"><div class="card-title">🚀 涨停板 <span style="font-size:13px;color:#999;font-weight:400;">' + date.slice(0,4) + '-' + date.slice(4,6) + '-' + date.slice(6,8) + ' 共 ' + zt.count + ' 只</span></div><div class="card-body"><table><tr><th>代码</th><th>名称</th><th>连板</th><th>涨幅</th><th>价格</th><th>换手</th><th>封板</th><th>炸板</th><th>封板资金</th><th>行业</th></tr>';
        for (const s of zt.stocks) {{
            const bd = boardLabel(s.board_times);
            html += `<tr><td>${{s.code}}</td><td>${{bd}} ${{s.name}}</td><td style="font-weight:600;">${{s.board_times}}板</td><td class="up">+${{s.change_pct.toFixed(2)}}%</td><td>${{s.price.toFixed(2)}}</td><td>${{s.turnover_rate.toFixed(2)}}%</td><td>${{s.seal_first_time}}-<br>${{s.seal_last_time}}</td><td>${{s.bomb_times}}次</td><td>${{(s.seal_fund/10000).toFixed(0)}}万</td><td>${{s.industry}}</td></tr>`;
        }}
        html += '</table></div></div>';

        // 行业分布
        html += '<div class="card"><div class="card-title">🏭 涨停行业分布</div><div class="card-body"><table><tr><th>行业</th><th>涨停数</th><th>代表个股</th></tr>';
        for (const i of ind.industries) {{
            html += `<tr><td>${{i.industry}}</td><td style="font-weight:600;">${{i.count}}只</td><td>${{i.top_stocks}}</td></tr>`;
        }}
        html += '</table></div></div>';

        document.getElementById("ztContent").innerHTML = html;
    }} catch (e) {{
        document.getElementById("ztContent").innerHTML = '<div class="error">❌ 加载失败: ' + e.message + '</div>';
    }}
}}

function showTab(tab) {{
    document.querySelectorAll('.tab-btn').forEach(b => {{
        b.style.background = '#f0f2f5';
        b.style.color = '#333';
    }});
    event.target.style.background = '#667eea';
    event.target.style.color = '#fff';
    document.getElementById('tab-mkt').style.display = tab === 'mkt' ? 'block' : 'none';
    document.getElementById('tab-zt').style.display = tab === 'zt' ? 'block' : 'none';
    if (tab === 'zt') {{
        loadZtData();
    }}
}}

loadData();
setInterval(loadData, 60000);
</script>
</body>
</html>"""


def start_server():
    """启动 Web 服务（前台调试模式）"""
    server = ThreadingHTTPServer((HOST, PORT), RequestHandler)
    print(f"\n  {'═' * 50}")
    print(f"  📊 股票分析 Web 服务")
    print(f"  {'═' * 50}")
    print(f"  地址: http://localhost:{PORT}")
    print(f"  API:  http://localhost:{PORT}/api/market-overview")
    print(f"  {'─' * 50}")
    print(f"  按 Ctrl+C 停止服务")
    print(f"  {'═' * 50}\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  服务已停止")
        server.server_close()


def start_daemon():
    """后台 Daemon 模式启动 Web 服务"""
    import subprocess
    import sys

    script_path = os.path.abspath(__file__)
    log_file = os.path.join(DATA_DIR, "web_server.log")

    # 用 nohup 在后台启动
    cmd = [
        sys.executable, script_path, "--daemon",
    ]

    with open(log_file, "a") as log:
        process = subprocess.Popen(
            cmd,
            stdout=log,
            stderr=log,
            close_fds=True,
        )

    pid = process.pid
    pid_file = os.path.join(DATA_DIR, "web_server.pid")
    with open(pid_file, "w") as f:
        f.write(str(pid))

    print(f"\n  {'═' * 50}")
    print(f"  📊 股票分析 Web 服务 [Daemon]")
    print(f"  {'═' * 50}")
    print(f"  地址: http://localhost:{PORT}")
    print(f"  日志: {log_file}")
    print(f"  PID:  {pid}")
    print(f"  {'─' * 50}")
    print(f"  使用以下命令停止:")
    print(f"    kill $(cat {pid_file})")
    print(f"  {'═' * 50}\n")


def _run_daemon():
    """实际的后台服务运行入口"""
    import signal

    server = ThreadingHTTPServer((HOST, PORT), RequestHandler)

    def shutdown(sig, frame):
        print(f"\n  [Daemon] 收到信号 {sig}, 关闭服务...")
        server.server_close()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    print(f"  [Daemon] 启动 http://localhost:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--daemon":
        _run_daemon()
    else:
        start_server()
