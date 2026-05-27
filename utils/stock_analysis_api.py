#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据获取模块 - 基于新浪财经 API + AKShare 的股票数据获取
数据源：
  新浪实时行情: 价格=元, volume=手(x100->股), amount=元
  新浪指数: 点数/涨跌幅
  AKShare 同花顺板块: 涨跌幅=百分比
"""

import json
import time
import os
import subprocess
import requests
import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Union, Tuple
from datetime import datetime, timedelta
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def _get_store():
    """延迟导入并返回 QuoteStore 实例"""
    from utils.data_store import QuoteStore
    return QuoteStore()


def _curl_text(url: str, timeout: int = 10, headers: Optional[Dict] = None) -> Optional[str]:
    """通过 subprocess + curl 发起 HTTP 请求"""
    try:
        cmd = ["curl", "-s", "--connect-timeout", str(timeout)]
        if headers:
            for k, v in headers.items():
                cmd.extend(["-H", f"{k}: {v}"])
        cmd.append(url)

        result = subprocess.run(cmd, capture_output=True, timeout=timeout + 5)
        # 尝试解码，新浪返回 GBK
        raw = result.stdout
        if not raw:
            return None
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            try:
                return raw.decode("gbk")
            except UnicodeDecodeError:
                return raw.decode("gbk", errors="replace")
    except subprocess.TimeoutExpired:
        logger.warning(f"curl 超时: {url[:80]}")
        return None


# ---------- 新浪财经 API ----------

def _sina_code(code: str) -> str:
    """转为新浪股票代码格式: 600519 -> sh600519, 000333 -> sz000333"""
    code = code.strip()
    if code.startswith(("sh", "sz")):
        return code
    if code.startswith(("0", "3")):
        return f"sz{code}"
    if code.startswith("6"):
        return f"sh{code}"
    return f"sz{code}"


def _sina_index_list(codes: List[str]) -> str:
    """转为新浪指数格式: 000001 -> s_sh000001, 399001 -> s_sz399001"""
    result = []
    for c in codes:
        c = c.strip()
        if c.startswith("sh"):
            c = c[2:]
        if c.startswith("sz"):
            c = c[2:]
        if c.startswith("0"):
            result.append(f"s_sh{c}")
        elif c.startswith("3"):
            result.append(f"s_sz{c}")
        elif c.startswith("6"):
            result.append(f"s_sh{c}")  # 00 -> sh (上证综指)
        else:
            result.append(f"s_sh{c}")
    return ",".join(result)


def _sina_parse_stock(line: str) -> Optional[Dict]:
    """解析新浪股票行情数据"""
    try:
        line = line.strip()
        if not line or "=" not in line:
            return None

        # 格式: var hq_str_sh600519="name,open,prev_close,current,high,low,...,date,time,...";
        value = line.split("=", 1)[1].strip().strip('"').strip(";").strip('"')
        parts = value.split(",")
        if len(parts) < 30:
            return None

        # 提取证券代码
        var_part = line.split("=", 1)[0]
        code_type = var_part.split("_")[-1] if "_" in var_part else ""

        return {
            "name": parts[0],
            "open": float(parts[1]) if parts[1] else 0,
            "prev_close": float(parts[2]) if parts[2] else 0,
            "current_price": float(parts[3]) if parts[3] else 0,
            "high": float(parts[4]) if parts[4] else 0,
            "low": float(parts[5]) if parts[5] else 0,
            "date": parts[30] if len(parts) > 30 else "",
            "time": parts[31] if len(parts) > 31 else "",
        }
    except (ValueError, IndexError, AttributeError) as e:
        return None


def _sina_parse_index(line: str) -> Optional[Dict]:
    """解析新浪指数行情数据"""
    try:
        line = line.strip()
        if not line or "=" not in line:
            return None

        value = line.split("=", 1)[1].strip().strip('"').strip(";").strip('"')
        parts = value.split(",")
        if len(parts) < 6:
            return None

        # 新浪指数格式: name,current,change,change_pct,volume,amount
        name = parts[0]
        current = float(parts[1]) if parts[1] else 0
        change = float(parts[2]) if parts[2] else 0
        change_pct = float(parts[3]) if parts[3] else 0
        vol_parts = parts[4] if len(parts) > 4 else "0"
        amount_parts = parts[5] if len(parts) > 5 else "0"

        return {
            "name": name,
            "current_price": current,
            "change_pct": change_pct,
            "volume": float(vol_parts) if vol_parts else 0,
            "amount": float(amount_parts) if amount_parts else 0,
        }
    except (ValueError, IndexError, AttributeError) as e:
        return None


def fetch_sina_quotes(codes: List[str]) -> List[Dict]:
    """批量获取新浪行情"""
    if not codes:
        return []

    sina_codes = [_sina_code(c) for c in codes]
    url = f"https://hq.sinajs.cn/list={','.join(sina_codes)}"
    headers = {"Referer": "https://finance.sina.com.cn"}

    text = _curl_text(url, headers=headers)
    if not text:
        return []

    results = []
    for line in text.strip().split("\n"):
        if not line.strip():
            continue
        data = _sina_parse_stock(line)
        if data:
            results.append(data)
    return results


def fetch_sina_index_quotes(index_codes: List[str]) -> List[Dict]:
    """批量获取指数行情 (新浪)"""
    sina_list = _sina_index_list(index_codes)
    url = f"https://hq.sinajs.cn/list={sina_list}"
    headers = {"Referer": "https://finance.sina.com.cn"}

    text = _curl_text(url, headers=headers)
    if not text:
        return []

    results = []
    for line in text.strip().split("\n"):
        if not line.strip():
            continue
        data = _sina_parse_index(line)
        if data:
            results.append(data)
    return results


# ---------- AKShare 封装 ----------

try:
    import akshare as ak
    HAS_AKSHARE = True
except ImportError:
    HAS_AKSHARE = False


class StockDataFetcher:
    """股票数据获取器 - 新浪财经 + AKShare"""

    # 指数代码 (新浪格式)
    INDEX_CODES = {
        "szzs": "000001",    # 上证指数
        "szcz": "399001",    # 深证成指
        "hs300": "000300",   # 沪深 300
        "cyb": "399006",     # 创业板指
        "kc50": "000688",    # 科创 50
        "sh50": "000016",    # 上证 50
        "sz100": "399330",   # 深证 100
    }

    INDEX_NAMES = {
        "szzs": "上证指数", "szcz": "深证成指", "hs300": "沪深300",
        "cyb": "创业板指", "kc50": "科创50", "sh50": "上证50", "sz100": "深证100"
    }

    # 热门个股
    POPULAR_STOCKS = {
        "600519": "贵州茅台",
        "300750": "宁德时代",
        "600036": "招商银行",
        "601318": "中国平安",
        "600276": "恒瑞医药",
        "002594": "比亚迪",
        "000333": "美的集团",
        "300059": "东方财富",
    }

    def fetch_index_quote(self, index_code: str) -> Optional[Dict]:
        """获取指数行情"""
        code = self.INDEX_CODES.get(index_code)
        if not code:
            return None

        results = fetch_sina_index_quotes([code])
        if results:
            r = results[0]
            return {
                "symbol": index_code,
                "name": self.INDEX_NAMES.get(index_code, r.get("name", index_code)),
                "current_price": r.get("current_price", 0),
                "open": r.get("open", 0),
                "high": r.get("high", 0),
                "low": r.get("low", 0),
                "pre_close": r.get("prev_close", 0),
                "change_pct": round(r.get("change_pct", 0), 2),
            }
        return None

    def fetch_stock_quote(self, secid: str) -> Optional[Dict]:
        """获取个股行情"""
        code = secid
        if "." in code:
            code = code.split(".")[1]
        # 去掉 sh/sz 前缀
        if code.startswith(("sh", "sz")):
            code = code[2:]
        # 纯代码，保留原样
        code = code.strip()

        results = fetch_sina_quotes([code])
        if results:
            r = results[0]
            return {
                "symbol": code,
                "name": r.get("name", ""),
                "current_price": r.get("current_price", 0),
                "open": r.get("open", 0),
                "high": r.get("high", 0),
                "low": r.get("low", 0),
                "pre_close": r.get("prev_close", 0),
                "change_pct": round(((r.get("current_price", 0) - r.get("prev_close", 0)) / max(r.get("prev_close", 0), 0.01)) * 100, 2),
                "volume": 0,  # 新浪不直接提供
                "amount": 0,
            }
        return None

    def fetch_index_kline(self, index_code: str, period: str = "day",
                          days: int = 60) -> Optional[pd.DataFrame]:
        """获取指数K线 (使用 AKShare，它有独立的后端)"""
        if not HAS_AKSHARE:
            logger.warning("AKShare 未安装，无法获取K线数据")
            return None

        akshare_code = f"sh{self.INDEX_CODES.get(index_code, index_code)}"
        if index_code in ("szcz", "cyb", "sz100"):
            akshare_code = f"sz{self.INDEX_CODES.get(index_code, index_code)}"

        try:
            df = ak.stock_zh_index_daily(symbol=akshare_code)
            if df is not None and not df.empty:
                df = df.tail(days)
                df.set_index("date", inplace=True)
                return df
        except Exception as e:
            logger.error(f"获取K线 {index_code} 失败: {e}")
        return None

    def fetch_index_data(self, index_code: str) -> Optional[Dict]:
        """获取指数数据"""
        quote = self.fetch_index_quote(index_code)
        if not quote:
            return None
        kline = self.fetch_index_kline(index_code, days=30)
        result = dict(quote)
        if kline is not None:
            result["kline"] = kline
        return result

    def fetch_data(self, secid: str) -> Optional[Dict]:
        """通用数据获取"""
        return self.fetch_stock_quote(secid)

    def get_market_overview(self) -> Dict:
        """市场概况"""
        # 批量获取指数行情
        index_codes = list(self.INDEX_CODES.values())[:5]
        index_results = fetch_sina_index_quotes(index_codes)

        indexes = {}
        for i, (key, name) in enumerate([("szzs", "上证指数"), ("szcz", "深证成指"), ("hs300", "沪深300"),
                                           ("cyb", "创业板指"), ("kc50", "科创50")]):
            if i < len(index_results):
                r = index_results[i]
                indexes[key] = {
                    "symbol": key,
                    "name": name,
                    "current_price": r.get("current_price", 0),
                    "change_pct": round(r.get("change_pct", 0), 2),
                    "open": r.get("open", 0),
                    "high": r.get("high", 0),
                    "low": r.get("low", 0),
                    "pre_close": r.get("prev_close", 0),
                }

        # 批量获取个股行情
        stock_codes = list(self.POPULAR_STOCKS.keys())
        stock_results = fetch_sina_quotes(stock_codes)

        stocks = {}
        for code, r in zip(stock_codes, stock_results):
            name = self.POPULAR_STOCKS[code]
            stocks[name] = {
                "symbol": code,
                "name": name,
                "current_price": r.get("current_price", 0),
                "change_pct": round(((r.get("current_price", 0) - r.get("prev_close", 0)) / max(r.get("prev_close", 0), 0.01)) * 100, 2),
            }

        return {
            "indexes": indexes,
            "popular_stocks": stocks,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    def fetch_sector_data(self) -> List[Dict]:
        """获取行业板块实时行情"""
        try:
            # 优先从 sector_daily_history 表拉最新数据
            from utils.dao import get_db
            db = get_db()
            last_date_r = db.fetchone('SELECT MAX(trade_date) as max_d FROM sector_daily_history')
            last_date = last_date_r['max_d'] if last_date_r else None
            if last_date:
                rows = db.fetchall('''
                    SELECT sector_name, change_pct, amount
                    FROM sector_daily_history
                    WHERE trade_date = %s AND change_pct IS NOT NULL
                      AND sector_name NOT LIKE 'BK%%'
                    ORDER BY ABS(change_pct) DESC
                    LIMIT 30
                ''', (last_date,))
                sectors = []
                seen = set()
                for name, chg, amt in rows:
                    if name not in seen:
                        seen.add(name)
                        sectors.append({
                            'name': name,
                            'change_pct': chg,
                            'amount': amt or 0
                        })
                return sectors
            conn.close()
        except Exception as e:
            logger.warning(f"fetch_sector_data从DB取板块失败: {e}")
        
        # fallback
        return [
            {"name": "科技", "change_pct": 1.5},
            {"name": "金融", "change_pct": -0.3},
            {"name": "新能源", "change_pct": 2.1},
            {"name": "医药", "change_pct": 0.8},
        ]

    def _fetch_index_amount(self, index_code: str) -> Optional[float]:
        """获取指数成交额（新浪格式）"""
        try:
            results = fetch_sina_index_quotes([index_code])
            if results:
                return results[0].get("amount", 0)
        except Exception as e:
            logger.warning(f"获取指数{index_code}成交额失败: {e}")
        return None

    def _fetch_money_flow(self) -> Dict:
        """获取资金流向数据（东方财富 curl 方式）"""
        try:
            # 沪市主力资金
            url_sh = "https://push2.eastmoney.com/api/qt/stock/get?secid=1.000001&fields=f57,f58,f170,f171,f177"
            headers = {
                "Referer": "https://quote.eastmoney.com/",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
            }
            sh_text = _curl_text(url_sh, headers=headers)

            # 深市主力资金
            url_sz = "https://push2.eastmoney.com/api/qt/stock/get?secid=0.399001&fields=f57,f58,f170,f171,f177"
            sz_text = _curl_text(url_sz, headers=headers)

            result = {"main_force_net": 0, "sh_main": 0, "sz_main": 0}

            if sh_text:
                try:
                    sh_data = json.loads(sh_text)
                    if sh_data.get("data"):
                        d = sh_data["data"]
                        # f170: 主力净流入, f171: 小单净流入
                        result["sh_main"] = d.get("f170", 0)
                except (json.JSONDecodeError, AttributeError):
                    pass

            if sz_text:
                try:
                    sz_data = json.loads(sz_text)
                    if sz_data.get("data"):
                        d = sz_data["data"]
                        result["sz_main"] = d.get("f170", 0)
                except (json.JSONDecodeError, AttributeError):
                    pass

            result["main_force_net"] = result["sh_main"] + result["sz_main"]

            # 尝试获取板块资金流向
            try:
                result["sector_inflow"] = []
                result["sector_outflow"] = []
                url_sector = "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=10&po=1&np=1&ut=bd1d9ddb04089700cf9c27f6f7426281&fltt=2&invt=2&fid=f62&fs=m:90+t:2&fields=f12,f14,f3,f62,f184,f66,f69"
                sec_text = _curl_text(url_sector, headers=headers)
                if sec_text:
                    sec_data = json.loads(sec_text)
                    if sec_data.get("data") and sec_data["data"].get("diff"):
                        for item in sec_data["data"]["diff"]:
                            inflow = float(item.get("f62", 0))
                            result["sector_inflow"].append({
                                "name": item.get("f14", ""),
                                "change_pct": float(item.get("f3", 0)),
                                "net_inflow": inflow,
                            })
                        # 涨幅前5
                        result["sector_inflow"] = result["sector_inflow"][:5]

                url_sector_out = "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=10&po=0&np=1&ut=bd1d9ddb04089700cf9c27f6f7426281&fltt=2&invt=2&fid=f62&fs=m:90+t:2&fields=f12,f14,f3,f62,f184,f66,f69"
                out_text = _curl_text(url_sector_out, headers=headers)
                if out_text:
                    out_data = json.loads(out_text)
                    if out_data.get("data") and out_data["data"].get("diff"):
                        for item in out_data["data"]["diff"]:
                            inflow = float(item.get("f62", 0))
                            result["sector_outflow"].append({
                                "name": item.get("f14", ""),
                                "change_pct": float(item.get("f3", 0)),
                                "net_inflow": inflow,
                            })
                        result["sector_outflow"] = result["sector_outflow"][:5]
            except Exception as e:
                logger.warning(f"获取板块资金流向失败: {e}")

            return result
        except Exception as e:
            logger.warning(f"获取资金流向失败: {e}")
            return {"main_force_net": 0, "sh_main": 0, "sz_main": 0}

    def fetch_sector_full_data(self) -> Dict:
        """获取完整板块数据（涨幅居前、跌幅居前、资金流入、资金流出各5个）
        数据源优先级：同花顺行业板块总结(stock_board_industry_summary_ths) — 收盘后也可用
        """
        result = {
            "top_gain": [],
            "top_fall": [],
            "top_inflow": [],
            "top_outflow": [],
        }

        # 使用同花顺行业板块总结（收盘后也可用，含90个行业板块，有涨跌家数和净流入）
        try:
            df = ak.stock_board_industry_summary_ths()
            if df is not None and not df.empty and '涨跌幅' in df.columns:
                logger.info(f"同花顺行业板块数据获取成功: {len(df)}条")
                # 涨幅居前5
                for _, row in df.sort_values("涨跌幅", ascending=False).head(5).iterrows():
                    result["top_gain"].append({
                        "name": row.get("板块", ""),
                        "change_pct": float(row.get("涨跌幅", 0)),
                        "turn_over": 0,
                        "amount": float(row.get("总成交额", 0) or 0) * 1e8,
                        "rise_count": int(float(row.get("上涨家数", 0))),
                        "fall_count": int(float(row.get("下跌家数", 0))),
                        "net_inflow": float(row.get("净流入", 0) or 0) * 1e8,
                    })
                # 跌幅居前5
                for _, row in df.sort_values("涨跌幅", ascending=True).head(5).iterrows():
                    result["top_fall"].append({
                        "name": row.get("板块", ""),
                        "change_pct": float(row.get("涨跌幅", 0)),
                        "turn_over": 0,
                        "amount": float(row.get("总成交额", 0) or 0) * 1e8,
                        "rise_count": int(float(row.get("上涨家数", 0))),
                        "fall_count": int(float(row.get("下跌家数", 0))),
                        "net_inflow": float(row.get("净流入", 0) or 0) * 1e8,
                    })
                # 资金流入前5（按净流入排序）
                if '净流入' in df.columns:
                    for _, row in df.sort_values("净流入", ascending=False).head(5).iterrows():
                        result["top_inflow"].append({
                            "name": row.get("板块", ""),
                            "change_pct": float(row.get("涨跌幅", 0)),
                            "net_inflow": float(row.get("净流入", 0) or 0) * 1e8,
                        })
                    # 资金流出前5
                    for _, row in df.sort_values("净流入", ascending=True).head(5).iterrows():
                        result["top_outflow"].append({
                            "name": row.get("板块", ""),
                            "change_pct": float(row.get("涨跌幅", 0)),
                            "net_inflow": float(row.get("净流入", 0) or 0) * 1e8,
                        })
                return result
        except Exception as e:
            logger.warning(f"同花顺行业板块总结接口失败: {e}")

        # 同花顺失败时，尝试东财AKShare
        try:
            if HAS_AKSHARE:
                df = ak.stock_board_industry_name_em()
                if df is not None and not df.empty:
                    logger.info(f"东财AKShare板块数据获取成功: {len(df)}条")
                    for _, row in df.sort_values("涨跌幅", ascending=False).head(5).iterrows():
                        result["top_gain"].append({
                            "name": row.get("板块名称", ""),
                            "change_pct": float(row.get("涨跌幅", 0)),
                            "turn_over": float(row.get("换手率", 0)),
                            "amount": float(row.get("成交额", 0)),
                            "rise_count": int(float(row.get("上涨家数", 0))),
                            "fall_count": int(float(row.get("下跌家数", 0))),
                        })
                    for _, row in df.sort_values("涨跌幅", ascending=True).head(5).iterrows():
                        result["top_fall"].append({
                            "name": row.get("板块名称", ""),
                            "change_pct": float(row.get("涨跌幅", 0)),
                            "turn_over": float(row.get("换手率", 0)),
                            "amount": float(row.get("成交额", 0)),
                            "rise_count": int(float(row.get("上涨家数", 0))),
                            "fall_count": int(float(row.get("下跌家数", 0))),
                        })
        except Exception as e:
            logger.warning(f"东财AKShare板块接口失败: {e}")

        # 东财资金流向（仅当AKShare成功时补充）
        try:
            headers = {
                "Referer": "https://quote.eastmoney.com/",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
            }
            url_in = "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=5&po=1&np=1&ut=bd1d9ddb04089700cf9c27f6f7426281&fltt=2&invt=2&fid=f62&fs=m:90+t:2&fields=f12,f14,f3,f62,f184,f66,f69"
            in_text = _curl_text(url_in, headers=headers)
            if in_text:
                in_data = json.loads(in_text)
                if in_data.get("data") and in_data["data"].get("diff"):
                    for item in in_data["data"]["diff"]:
                        result["top_inflow"].append({
                            "name": item.get("f14", ""),
                            "change_pct": float(item.get("f3", 0)),
                            "net_inflow": float(item.get("f62", 0)),
                        })
            url_out = "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=5&po=0&np=1&ut=bd1d9ddb04089700cf9c27f6f7426281&fltt=2&invt=2&fid=f62&fs=m:90+t:2&fields=f12,f14,f3,f62,f184,f66,f69"
            out_text = _curl_text(url_out, headers=headers)
            if out_text:
                out_data = json.loads(out_text)
                if out_data.get("data") and out_data["data"].get("diff"):
                    for item in out_data["data"]["diff"]:
                        result["top_outflow"].append({
                            "name": item.get("f14", ""),
                            "change_pct": float(item.get("f3", 0)),
                            "net_inflow": float(item.get("f62", 0)),
                        })
        except Exception:
            pass

        return result

    # ──────────────────────────────────────────────
    # New methods added per subagent task
    # ──────────────────────────────────────────────

    def fetch_sector_performance_em(self):
        """
        获取行业板块排行：涨幅/跌幅/资金流入/资金流出各前5。
        数据源优先级：同花顺(收盘后可用) > 东财AKShare > 空列表(不下硬编码假数据)
        Returns: (top_gainers, top_losers, top_inflow, top_outflow)
        """
        top_gainers = []
        top_losers = []
        top_inflow = []
        top_outflow = []

        # 优先用同花顺（收盘后也可用）
        try:
            df = ak.stock_board_industry_summary_ths()
            if df is not None and not df.empty and '涨跌幅' in df.columns:
                for _, row in df.sort_values("涨跌幅", ascending=False).head(5).iterrows():
                    top_gainers.append({
                        "name": str(row.get("板块", "")),
                        "change_pct": float(row.get("涨跌幅", 0)),
                        "inflow": float(row.get("净流入", 0) or 0) * 1e8,
                    })
                for _, row in df.sort_values("涨跌幅", ascending=True).head(5).iterrows():
                    top_losers.append({
                        "name": str(row.get("板块", "")),
                        "change_pct": float(row.get("涨跌幅", 0)),
                        "inflow": float(row.get("净流入", 0) or 0) * 1e8,
                    })
                if '净流入' in df.columns:
                    for _, row in df.sort_values("净流入", ascending=False).head(5).iterrows():
                        top_inflow.append({
                            "name": str(row.get("板块", "")),
                            "change_pct": float(row.get("涨跌幅", 0)),
                            "inflow": float(row.get("净流入", 0) or 0) * 1e8,
                        })
                    for _, row in df.sort_values("净流入", ascending=True).head(5).iterrows():
                        top_outflow.append({
                            "name": str(row.get("板块", "")),
                            "change_pct": float(row.get("涨跌幅", 0)),
                            "inflow": float(row.get("净流入", 0) or 0) * 1e8,
                        })
                if top_gainers:
                    return top_gainers, top_losers, top_inflow, top_outflow
        except Exception as e:
            logger.warning(f"同花顺板块总结接口失败: {e}")

        # 回退到东财AKShare
        try:
            if HAS_AKSHARE:
                df = ak.stock_board_industry_name_em()
                if df is not None and not df.empty:
                    for _, row in df.sort_values("涨跌幅", ascending=False).head(5).iterrows():
                        top_gainers.append({
                            "name": str(row.get("板块名称", "")),
                            "change_pct": float(row.get("涨跌幅", 0)),
                            "inflow": float(row.get("成交额", 0)),
                        })
                    for _, row in df.sort_values("涨跌幅", ascending=True).head(5).iterrows():
                        top_losers.append({
                            "name": str(row.get("板块名称", "")),
                            "change_pct": float(row.get("涨跌幅", 0)),
                            "inflow": float(row.get("成交额", 0)),
                        })
                    if "主力净流入-净额" in df.columns:
                        for _, row in df.sort_values("主力净流入-净额", ascending=False).head(5).iterrows():
                            top_inflow.append({
                                "name": str(row.get("板块名称", "")),
                                "change_pct": float(row.get("涨跌幅", 0)),
                                "inflow": float(row.get("主力净流入-净额", 0)),
                            })
                        for _, row in df.sort_values("主力净流入-净额", ascending=True).head(5).iterrows():
                            top_outflow.append({
                                "name": str(row.get("板块名称", "")),
                                "change_pct": float(row.get("涨跌幅", 0)),
                                "inflow": float(row.get("主力净流入-净额", 0)),
                            })
        except Exception as e:
            logger.warning(f"东财板块接口失败: {e}")

        return top_gainers, top_losers, top_inflow, top_outflow

    def get_market_summary(self, date_str: str = None) -> Dict:
        """
        从 sector_performance 表汇总市场数据（涨跌家数、成交额、前日对比）。
        保留原有方法签名兼容之前的使用方。
        """
        from utils.dao import get_db
        db = get_db()
        # 统一为 YYYY-MM-DD 格式
        raw = date_str or datetime.now().strftime('%Y%m%d')
        raw = str(raw).replace('-', '')
        today = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
        yesterday_dt = datetime.strptime(today, '%Y-%m-%d') - timedelta(days=1)
        yesterday = yesterday_dt.strftime('%Y-%m-%d')

        cur = db.execute(
            "SELECT SUM(rise_count) as rise, SUM(fall_count) as fall, SUM(amount) as total_amt FROM sector_performance WHERE record_date=%s AND rank_type='all'",
            (today,))
        row = cur.fetchone()

        prev_cur = db.execute(
            "SELECT SUM(amount) as total_amt FROM sector_performance WHERE record_date=%s AND rank_type='all'",
            (yesterday,))
        prev_row = prev_cur.fetchone()

        return {
            'up_count': int(row['rise'] or 0) if row else 0,
            'down_count': int(row['fall'] or 0) if row else 0,
            'flat_count': 0,
            'total_amount': float(row['total_amt'] or 0) if row else 0,
            'prev_amount': float(prev_row['total_amt'] or 0) if prev_row else 0,
            'amount_change': float(row['total_amt'] or 0) - float(prev_row['total_amt'] or 0) if row and prev_row else 0,
        }

    def _fetch_board_sectors_via_ulist(self) -> Dict:
        """
        通过东方财富 ulist.np 批量获取行业+概念板块行情。
        返回: { "top_gain": [...], "top_fall": [...], "top_inflow": [...], "top_outflow": [...] }
        """
        # 东方财富精选行业板块代码（仅行业板块，不含概念/标签）
        board_codes = [
            "BK0420","BK0421","BK0422","BK0424","BK0425","BK0427","BK0428","BK0429","BK0433","BK0436",
            "BK0437","BK0438","BK0440","BK0447","BK0448","BK0450","BK0451","BK0454","BK0456","BK0457",
            "BK0458","BK0459","BK0464","BK0465","BK0470","BK0471","BK0473","BK0474","BK0475","BK0476",
            "BK0478","BK0479","BK0480","BK0481","BK0482","BK0484","BK0485","BK0486","BK0490",
            "BK0493","BK0494","BK0512","BK0538","BK0539","BK0545","BK0546",
            "BK0725","BK0726","BK0727","BK0728","BK0729","BK0730","BK0731","BK0732","BK0733","BK0734",
            "BK0735","BK0736","BK0737","BK0738","BK0739","BK0740",
        ]
        
        sectors = []
        chunk_size = 50
        headers = {
            "Referer": "https://quote.eastmoney.com/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }
        # 分块查询（每块50个BK代码，避免URL过长）
        for i in range(0, len(board_codes), chunk_size):
            chunk = board_codes[i:i+chunk_size]
            secids = ",".join([f"90.{c}" for c in chunk])
            try:
                url = f"https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&fields=f2,f3,f4,f6,f12,f14,f62,f184&secids={secids}"
                text = _curl_text(url, headers=headers)
                if not text:
                    continue
                d = json.loads(text)
                if not d.get("data"):
                    continue
                diff = d["data"].get("diff", [])
                items = list(diff.values()) if isinstance(diff, dict) else diff
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    name = item.get("f14", "") or ""
                    pct = float(item.get("f3", 0) or 0)
                    amt = float(item.get("f6", 0) or 0)
                    inflow = float(item.get("f62", 0) or 0)
                    if name:
                        sectors.append({"name": name, "change_pct": pct, "amount": amt, "inflow": inflow})
            except Exception as e:
                logger.warning(f"板块查询批次{i}失败: {e}")
                continue
        
        if not sectors:
            return {}
        
        sectors.sort(key=lambda x: x["change_pct"], reverse=True)
        top_gain = [{"name": s["name"], "change_pct": s["change_pct"], "inflow": s["amount"]} for s in sectors[:5]]
        
        sectors.sort(key=lambda x: x["change_pct"])
        top_fall = [{"name": s["name"], "change_pct": s["change_pct"], "inflow": s["amount"]} for s in sectors[:5]]
        
        sectors.sort(key=lambda x: x["inflow"], reverse=True)
        top_inflow = [{"name": s["name"], "change_pct": s["change_pct"], "inflow": s["inflow"]} for s in sectors[:5]]
        
        sectors.sort(key=lambda x: x["inflow"])
        top_outflow = [{"name": s["name"], "change_pct": s["change_pct"], "inflow": s["inflow"]} for s in sectors[:5]]
        
        return {
            "top_gain": top_gain,
            "top_fall": top_fall,
            "top_inflow": top_inflow,
            "top_outflow": top_outflow,
        }

    def batch_quote(self, secid_list: List[str]) -> Dict[str, Dict]:
        """批量获取行情"""
        codes = [s.split(".")[1] if "." in s else s for s in secid_list]
        results = fetch_sina_quotes(codes)
        return {secid_list[i]: r for i, r in enumerate(results) if r}
