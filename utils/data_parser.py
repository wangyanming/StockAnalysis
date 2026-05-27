"""
数据解析模块 - 解析股票数据并计算关键指标
"""
from typing import Dict, List, Optional, Tuple, Union
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import json


class DataParser:
    """金融数据解析器"""

    # 中文行业板块映射
    SECTOR_MAP = {
        "科技": ["600100", "600718", "600588", "002230", "300059"],
        "金融": ["600036", "600016", "601398", "601628", "601318"],
        "新能源": ["600104", "601633", "300750", "002074", "600886"],
        "医药": ["600196", "600276", "300015", "002007", "300122"],
        "消费": ["600519", "000858", "002304", "600887", "000568"],
        "制造": ["600031", "000651", "000100", "601766", "600690"],
        "地产": ["000002", "600048", "001979", "600340", "000069"],
    }

    def __init__(self, data_loader=None):
        self.data_loader = data_loader

    def parse_stock_data(self, raw_data: Dict) -> Dict[str, Union[float, str, pd.DataFrame]]:
        """解析原始股票数据"""
        result = {
            "date": raw_data.get("date", datetime.now().strftime("%Y-%m-%d")),
            "symbol": raw_data.get("symbol", "000000"),
            "name": raw_data.get("name", "未知"),
            "open": float(raw_data.get("open", 0)),
            "close": float(raw_data.get("close", 0)),
            "high": float(raw_data.get("high", 0)),
            "low": float(raw_data.get("low", 0)),
            "volume": int(raw_data.get("volume", 0)),
            "amount": float(raw_data.get("amount", 0)),
        }

        # 解析完整数据
        result["parsed_date"] = datetime.strptime(raw_data.get("date", ""), "%Y-%m-%d")
        result["parsed_open"] = float(raw_data.get("open", 0))
        result["parsed_high"] = float(raw_data.get("high", 0))
        result["parsed_low"] = float(raw_data.get("low", 0))
        result["parsed_close"] = float(raw_data.get("close", 0))
        result["parsed_volume"] = int(raw_data.get("volume", 0))

        return result

    def parse_index_data(self, raw_data: Dict) -> Dict:
        """解析指数数据"""
        result = self.parse_stock_data(raw_data)
        result["index_code"] = raw_data.get("code", "000000")
        result["level"] = raw_data.get("level", "上证 50")
        return result

    def parse_sector_data(self, sector: str, price_data: List[Dict]) -> Dict:
        """解析行业板块数据"""
        if sector not in self.SECTOR_MAP:
            return {"error": f"未知板块: {sector}"}

        result = {
            "sector": sector,
            "stocks": [],
            "timestamp": datetime.now()
        }

        for stock_data in price_data:
            parsed = self.parse_stock_data(stock_data)
            result["stocks"].append(parsed)

        # 计算板块整体表现
        if result["stocks"]:
            prices = [s["close"] for s in result["stocks"] if s["close"] > 0]
            result["average_price"] = np.mean(prices) if prices else 0
            result["total_volume"] = sum(s["volume"] for s in result["stocks"])
            result["price_std"] = np.std(prices) if len(prices) > 1 else 0
            result["volatility"] = result["price_std"] / result["average_price"] if result["average_price"] > 0 else 0

        return result

    def calculate_financial_metrics(self, price: float, shares_outstanding: int) -> Dict:
        """计算财务指标"""
        # 股息率
        dividend_yield = 0.03  # placeholder

        return {
            "price": price,
            "market_cap": price * shares_outstanding,
            "dividend_yield": dividend_yield,
            "pe_ratio": 0,  # 需要利润数据
            "pb_ratio": 0,  # 需要净资产数据
        }

    def calculate_technical_indicators(self, data: List[Dict]) -> Dict:
        """计算技术指标"""
        if not data or len(data) < 20:
            return {"error": "Not enough data points"}

        # 转换为 DataFrame
        df = pd.DataFrame(data)
        df.index = pd.to_datetime(df["date"])
        close = df["close"]

        # RSI (14-day)
        delta = close.diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        avg_gain = gain.rolling(window=14).mean()
        avg_loss = loss.rolling(window=14).mean()
        rs = avg_gain / avg_loss
        df["rsi"] = 100 - (100 / (1 + rs))

        # MACD
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        df["macd"] = ema12 - ema26
        df["signal"] = df["macd"].ewm(span=9, adjust=False).mean()
        df["macd_hist"] = df["macd"] - df["signal"]

        # 布林带
        df["bb_mid"] = close.rolling(window=20).mean()
        std = close.rolling(window=20).std()
        df["bb_upper"] = df["bb_mid"] + 2 * std
        df["bb_lower"] = df["bb_mid"] - 2 * std

        # KDJ
        low14 = df["low"].rolling(window=14).min()
        high14 = df["high"].rolling(window=14).max()
        rsv = (close - low14) / (high14 - low14) * 100
        df["k"] = rsv.ewm(com=2, adjust=False).mean()
        df["d"] = df["k"].ewm(com=2, adjust=False).mean()
        df["j"] = 3 * df["k"] - 2 * df["d"]

        result = {
            "latest_rsi": float(df["rsi"].iloc[-1]),
            "latest_macd": float(df["macd"].iloc[-1]),
            "latest_signal": float(df["signal"].iloc[-1]),
            "latest_macd_hist": float(df["macd_hist"].iloc[-1]),
            "latest_bb_upper": float(df["bb_upper"].iloc[-1]),
            "latest_bb_lower": float(df["bb_lower"].iloc[-1]),
            "latest_k": float(df["k"].iloc[-1]),
            "latest_d": float(df["d"].iloc[-1]),
            "latest_j": float(df["j"].iloc[-1]),
            "dataframe": df,
        }

        return result

    def parse_company_info(self, company_data: Dict) -> Dict:
        """解析公司信息"""
        return {
            "name": company_data.get("name", "未知"),
            "industry": company_data.get("industry", "未知"),
            "employees": company_data.get("employees", 0),
            "total_market_cap": company_data.get("total_market_cap", 0),
            "pe_ratio": company_data.get("pe_ratio", {"值": 0}),
            "dividend_yield": company_data.get("dividend_yield", 0),
            "earnings_per_share": company_data.get("earnings_per_share", 0),
            "book_value_per_share": company_data.get("book_value_per_share", 0),
            "index_weight": company_data.get("index_weight", 0),
        }

    def parse_macro_data(self, macro_data: Dict) -> Dict:
        """解析宏观经济数据"""
        return {
            "gdp_growth": macro_data.get("gdp_growth", 0),
            "cpi": macro_data.get("cpi", 0),
            "ppi": macro_data.get("ppi", 0),
            "interest_rate": macro_data.get("interest_rate", 0),
            "m2_supply": macro_data.get("m2_supply", 0),
            "trade_balance": macro_data.get("trade_balance", 0),
            "unemployment": macro_data.get("unemployment", 0),
            "date": macro_data.get("date", datetime.now().strftime("%Y-%m-%d")),
        }
