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

from utils.date_utils import get_display_date
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.stock_analysis_api import StockDataFetcher
from utils.dao import get_db
from core.fetcher.limit_up_analysis import LimitUpAnalyzer

f = StockDataFetcher()
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

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            self.redirect("/web_app.html")
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
        elif path == "/api/picks":
            self.api_picks(params)
        elif path == "/api/limit-down":
            self.api_limit_down(params)
        elif path == "/web_app.html":
            self.serve_web_app()
        elif path.startswith("/static/"):
            self.serve_static()
        else:
            self.send_json({"error": "not found"}, 404)

    def send_json(self, data: dict, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"))

    def redirect(self, location: str):
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

    def api_market_overview(self):
        """API: 市场概况 — 从 index_quotes 表按 get_display_date() 取 5 个指数"""
        def _fetch():
            from utils.dao import get_db
            db = get_db()
            display_date = get_display_date()
            # 统一为 YYYY-MM-DD 格式
            date_str = f"{display_date[:4]}-{display_date[4:6]}-{display_date[6:8]}"
            rows = db.fetchall(
                "SELECT index_code, name, current_price, change_pct, open, high, low "
                "FROM index_quotes WHERE record_date = %s "
                "ORDER BY FIELD(index_code, 'szzs','szcz','hs300','cyb','kc50')",
                (date_str,)
            )
            indexes = {}
            for r in rows:
                indexes[r["index_code"]] = {
                    "symbol": r["index_code"],
                    "name": r["name"],
                    "current_price": float(r["current_price"] or 0),
                    "change_pct": round(float(r["change_pct"] or 0), 2),
                    "open": float(r["open"] or 0),
                    "high": float(r["high"] or 0),
                    "low": float(r["low"] or 0),
                }
            return {
                "indexes": indexes,
                "popular_stocks": {},
                "updated_at": date_str,
                "display_date": display_date,
            }
        ov = _cached_call("market_overview", _fetch, ttl=60)
        self.send_json(ov)

    def api_market_summary(self):
        """API: 今日市场总结 — 从 sector_performance 按 get_display_date() 汇总"""
        raw = _cached_call("market_summary", f.get_market_summary, ttl=30)
        display_date = get_display_date()
        self.send_json({
            "total_amount": raw.get("total_amount", 0),
            "prev_amount": raw.get("prev_amount", 0),
            "amount_change": raw.get("amount_change", 0),
            "rise_count": raw.get("up_count", 0),
            "fall_count": raw.get("down_count", 0),
            "flat_count": 0,
            "display_date": display_date,
            "updated_at": datetime.now().strftime("%H:%M:%S"),
        })

    def api_index(self, params):
        """API: 指数详情"""
        code = params.get("code", ["szzs"])[0]
        data = f.fetch_index_quote(code)
        kline = f.fetch_index_kline(code, days=30)
        if data:
            try:
                cur = get_db().execute(
                    """REPLACE INTO index_quotes 
                       (index_code, name, current_price, change_pct, open, high, low, volume, amount, timestamp, record_date)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        code,
                        data.get("name", code),
                        data.get("current_price", 0),
                        data.get("change_pct", 0),
                        data.get("open", 0),
                        data.get("high", 0),
                        data.get("low", 0),
                        data.get("volume", 0),
                        data.get("amount", 0),
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        datetime.now().strftime("%Y-%m-%d")
                    )
                )
                cur.close()
            except Exception as e:
                pass
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
            try:
                cur = get_db().execute(
                    """INSERT INTO stock_quotes 
                       (stock_code, name, current_price, change_pct, open, high, low, pre_close, volume, amount, timestamp)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        secid,
                        data.get("name", ""),
                        data.get("current_price", 0),
                        data.get("change_pct", 0),
                        data.get("open", 0),
                        data.get("high", 0),
                        data.get("low", 0),
                        data.get("pre_close", 0),
                        data.get("volume", 0),
                        data.get("amount", 0),
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    )
                )
                cur.close()
            except Exception as e:
                pass
        self.send_json({"quote": data or {}})

    def api_sectors(self):
        """API: 行业板块 — 从 sector_performance 表按 get_display_date() 取数据"""
        def _fetch():
            from utils.dao import get_db
            db = get_db()
            display_date = get_display_date()
            # 统一为 YYYY-MM-DD 格式
            date_str = f"{display_date[:4]}-{display_date[4:6]}-{display_date[6:8]}"
            rows = db.fetchall(
                "SELECT * FROM sector_performance WHERE record_date = %s AND rank_type = 'all' ORDER BY ABS(change_pct) DESC",
                (date_str,)
            )
            sectors = []
            for r in rows:
                sectors.append({
                    "name": r["sector_name"],
                    "change_pct": float(r["change_pct"] or 0),
                    "amount": float(r["amount"] or 0),
                    "net_inflow": float(r["net_inflow"] or 0),
                    "rise_count": int(r["rise_count"] or 0),
                    "fall_count": int(r["fall_count"] or 0),
                    "record_date": date_str,
                })
            return {"sectors": sectors, "display_date": display_date}
        data = _cached_call("sectors_full", _fetch, ttl=60)
        self.send_json(data)

    def api_history(self, params):
        """API: 历史行情"""
        kind = params.get("kind", ["index"])[0]
        code = params.get("code", ["szzs"])[0]
        days = int(params.get("days", [30])[0])

        db = get_db()
        if kind == "index":
            data = db.fetchall(
                """SELECT * FROM index_quotes 
                   WHERE index_code = %s 
                   ORDER BY timestamp DESC 
                   LIMIT %s""",
                (code, days)
            )
        else:
            data = db.fetchall(
                """SELECT * FROM stock_quotes 
                   WHERE stock_code = %s 
                   ORDER BY timestamp DESC 
                   LIMIT %s""",
                (code, days)
            )
        self.send_json({"data": data})

    def api_limit_up(self):
        """API: 当日涨停板 — 默认日期走 get_display_date()"""
        params = parse_qs(urlparse(self.path).query)
        date_str = params.get("date", [get_display_date()])[0]
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
        """API: 行业涨停统计 — 默认日期走 get_display_date()"""
        params = parse_qs(urlparse(self.path).query)
        date_str = params.get("date", [get_display_date()])[0]
        data = zt.get_industry_stats(date_str)
        self.send_json({"date": date_str, "count": len(data), "industries": data})

    def api_limit_up_refresh(self):
        """API: 手动刷新涨停数据"""
        date_str = datetime.now().strftime("%Y%m%d")
        result = zt.run_daily_analysis(date_str)
        self.send_json({"status": "ok", "result": result})

    def api_limit_up_dates(self):
        """API: 可用涨停数据日期列表，含默认日期(17:00前用最新,17:00后用今天)"""
        dates = zt.get_available_dates()
        today = datetime.now().strftime("%Y%m%d")
        now_hour = datetime.now().hour
        if now_hour < 17:
            default_date = dates[0] if dates else today
        else:
            default_date = today if today in dates else (dates[0] if dates else today)
        self.send_json({"dates": dates, "default_date": default_date})

    def api_picks(self, params):
        """API: 选股追踪 - 按日期和维度筛选候选股，返回后续5个交易日涨跌幅"""
        tag = params.get("tag", ["all"])[0]

        from utils.dao import get_db
        db = get_db()

        # 1. 获取所有可用日期
        dates_rows = db.fetchall("SELECT DISTINCT trade_date FROM daily_picks ORDER BY trade_date DESC")
        available_dates = [str(r["trade_date"]) for r in dates_rows]

        # 2. 默认日期逻辑：17:00前默认展示最新日期（昨天选股），17:00后默认展示今天（T日选股）
        #    如果今天已有选股数据则默认选中今天，否则选中最新日期
        today = datetime.now().strftime("%Y%m%d")
        now_hour = datetime.now().hour
        # 判断今天是否已有选股数据
        has_today = today in available_dates
        # 17:00前：今天选股还没跑，默认用最新日期（昨天或更早）
        # 17:00后：如果今天有选股数据则默认用今天，否则用最新日期
        if now_hour < 17:
            default_date = available_dates[0] if available_dates else today
        else:
            default_date = today if has_today else (available_dates[0] if available_dates else today)

        date_str = params.get("date", [default_date])[0]
        date_str = date_str.replace("-", "")

        # 2. 按日期和维度筛选候选股
        if tag == "all":
            rows = db.fetchall(
                "SELECT * FROM daily_picks WHERE trade_date = %s ORDER BY total_score DESC",
                (date_str,)
            )
        else:
            rows = db.fetchall(
                "SELECT * FROM daily_picks WHERE trade_date = %s AND data_tag = %s ORDER BY total_score DESC",
                (date_str, tag)
            )

        # 3. 获取后续5个交易日
        def _get_next_trade_dates(base_date, n=5):
            """获取base_date之后n个交易日"""
            all_dates = db.fetchall(
                "SELECT DISTINCT trade_date FROM stock_daily WHERE trade_date > %s ORDER BY trade_date ASC LIMIT %s",
                (base_date, n + 10)  # 多取一些，跳过非交易日
            )
            dates = [r["trade_date"] for r in all_dates]
            # 去重并取前n个
            seen = set()
            result = []
            for d in dates:
                if d not in seen:
                    seen.add(d)
                    result.append(d)
                if len(result) >= n:
                    break
            return result

        # 4. 组装返回值
        picks = []
        for row in rows:
            # 映射 data_tag 为显示名称
            tag_display_map = {"real": "涨停接力", "simulated": "区间潜伏", "limitup": "涨停接力", "range": "区间潜伏"}
            display_tag = tag_display_map.get(row.get("data_tag", "") or "", row.get("data_tag", ""))

            # 维度分
            dimensions = {
                "score_chip": row.get("score_chip", 0) or 0,
                "score_money": row.get("score_money", 0) or 0,
                "score_sector": row.get("score_sector", 0) or 0,
                "score_trend": row.get("score_trend", 0) or 0,
                "score_market": row.get("score_market", 0) or 0,
                "score_position": row.get("score_pos", 0) or 0,
            }

            # 后续5个交易日涨跌幅
            next_dates = _get_next_trade_dates(date_str)
            tracking = []
            for nd in next_dates:
                tr = db.fetchone(
                    "SELECT change_pct FROM stock_daily WHERE code = %s AND trade_date = %s",
                    (row["code"], nd)
                )
                if tr and tr["change_pct"] is not None:
                    tracking.append({
                        "date": nd,
                        "change_pct": round(float(tr["change_pct"]), 2)
                    })
                else:
                    tracking.append({
                        "date": nd,
                        "change_pct": None
                    })

            # 查询入选价（当天收盘价）
            entry = db.fetchone(
                "SELECT close FROM stock_daily WHERE code = %s AND trade_date = %s",
                (row["code"], date_str)
            )
            entry_price = float(entry["close"]) if entry and entry["close"] else 0

            picks.append({
                "code": row["code"],
                "name": row["name"],
                "total_score": row.get("total_score", 0) or 0,
                "data_tag": display_tag,
                "trade_date": str(row.get("trade_date", "")),
                "entry_price": entry_price,
                "dimensions": dimensions,
                "tracking": tracking
            })

        self.send_json({
            "picks": picks,
            "available_dates": available_dates[:50],  # 最多返回50个日期
            "default_date": default_date,
        })

    def api_limit_down(self, params):
        """API: 当日跌停板 — 默认日期走 get_display_date()"""
        date_str = params.get("date", [get_display_date()])[0]
        data = zt.get_today_limit_down(date_str)
        self.send_json({"date": date_str, "count": len(data), "stocks": data})

    def serve_web_app(self):
        """提供 web_app.html（真实数据接入版）"""
        filepath = os.path.join(DATA_DIR, "docs", "design", "web_app.html")
        if os.path.exists(filepath):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            with open(filepath, "rb") as f:
                self.wfile.write(f.read())
        else:
            self.send_json({"error": "web_app.html not found"}, 404)

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
