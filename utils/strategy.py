"""
选股策略模块 - 实现各种选股策略
"""
from typing import Dict, List, Optional, Tuple
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import json
from dataclasses import dataclass


@dataclass
class StockScreeningResult:
    """选股结果"""
    ticker: str
    stock_name: str
    score: float
    factors: Dict[str, float]
    confidence: float
    suggestion: str


class StockStrategy:
    """选股策略基类"""

    def __init__(self, data_loader=None):
        self.data_loader = data_loader
        self.results: List[StockScreeningResult] = []

    def screen(self, df: pd.DataFrame) -> StockScreeningResult:
        """筛选个股"""
        raise NotImplementedError


class ValueStrategy(StockStrategy):
    """价值投资策略"""

    def __init__(self, min_pe: float = 0, max_pe: float = 25,
                 min_pb: float = 0, max_pb: float = 3,
                 min_dividend: float = 0.01):
        super().__init__()
        self.min_pe = min_pe
        self.max_pe = max_pe
        self.min_pb = min_pb
        self.max_pb = max_pb
        self.min_dividend = min_dividend

    def screen(self, df: pd.DataFrame, company_info: Dict = None) -> StockScreeningResult:
        score = 50.0
        factors = {}

        if company_info:
            pe = company_info.get("pe_ratio", {})
            pe_value = pe.get("值", float("inf")) if isinstance(pe, dict) else pe

            # PE 评分
            if self.min_pe <= pe_value <= self.max_pe:
                factors["pe_score"] = 20
                score += 20
            else:
                factors["pe_score"] = -10
                score -= 10

            # PB 评分
            pb = company_info.get("pb_ratio", 0)
            if self.min_pb <= pb <= self.max_pb:
                factors["pb_score"] = 15
                score += 15
            else:
                factors["pb_score"] = -5
                score -= 5

            # 分红评分
            dividend = company_info.get("dividend_yield", 0)
            if dividend >= self.min_dividend:
                factors["dividend_score"] = 10
                score += 10

        # 价格动量评分
        if len(df) > 20:
            returns = df["close"].pct_change().dropna()
            volatility = returns.std()
            if volatility > 0:
                sharpe = returns.mean() / volatility
                factors["sharpe_score"] = min(sharpe * 10, 15)
                score += factors["sharpe_score"]

        # RSI 评分 (超卖反弹)
        if "rsi" in df.columns and len(df) > 0:
            latest_rsi = df["rsi"].iloc[-1]
            if 30 <= latest_rsi <= 40:
                factors["rsi_score"] = 10
                score += 10
            elif latest_rsi < 30:
                factors["rsi_score"] = 5
                score += 5
            else:
                factors["rsi_score"] = 0

        confidence = min(score / 100, 1.0)

        if score >= 75:
            suggestion = "强烈推荐"
        elif score >= 60:
            suggestion = "推荐"
        elif score >= 45:
            suggestion = "观望"
        else:
            suggestion = "回避"

        return StockScreeningResult(
            ticker=df.index.name if df.index.name else "UNKNOWN",
            stock_name=company_info.get("name", "未知") if company_info else "未知",
            score=score,
            factors=factors,
            confidence=confidence,
            suggestion=suggestion
        )


class MomentumStrategy(StockStrategy):
    """动量策略"""

    def __init__(self, lookback_days: int = 60, momentum_threshold: float = 0.1):
        super().__init__()
        self.lookback_days = lookback_days
        self.momentum_threshold = momentum_threshold

    def screen(self, df: pd.DataFrame) -> StockScreeningResult:
        score = 50.0
        factors = {}

        # 计算动量
        if len(df) >= self.lookback_days:
            returns = df["close"].pct_change(self.lookback_days).iloc[-1]
            factors["momentum"] = returns

            if returns > self.momentum_threshold:
                factors["momentum_score"] = min(returns * 100, 25)
                score += factors["momentum_score"]
            else:
                factors["momentum_score"] = -5
                score -= 5

        # 相对强度 (RS)
        if len(df) > 30:
            short_ma = df["close"].rolling(window=10).mean()
            long_ma = df["close"].rolling(window=30).mean()
            if not short_ma.empty and not long_ma.empty:
                rs_ratio = short_ma.iloc[-1] / long_ma.iloc[-1] if long_ma.iloc[-1] != 0 else 1
                factors["rs_ratio"] = rs_ratio
                if rs_ratio > 1:
                    factors["rs_score"] = 10
                    score += 10

        # MACD 信号
        if "macd" in df.columns and len(df) > 0:
            latest_macd = df["macd"].iloc[-1]
            latest_signal = df["signal"].iloc[-1]
            if latest_macd > latest_signal:
                factors["macd_score"] = 10
                score += 10
            else:
                factors["macd_score"] = -5
                score -= 5

        # 布林带位置
        if "bb_upper" in df.columns and len(df) > 0:
            latest_close = df["close"].iloc[-1]
            bb_upper = df["bb_upper"].iloc[-1]
            bb_lower = df["bb_lower"].iloc[-1]
            if bb_upper != bb_lower:
                bb_position = (latest_close - bb_lower) / (bb_upper - bb_lower)
                factors["bb_position"] = bb_position
                if bb_position < 0.3:
                    factors["bb_score"] = 10
                    score += 10
                elif bb_position > 0.8:
                    factors["bb_score"] = -10
                    score -= 10

        confidence = min(score / 100, 1.0)

        if score >= 75:
            suggestion = "强烈推荐"
        elif score >= 60:
            suggestion = "推荐"
        elif score >= 45:
            suggestion = "观望"
        else:
            suggestion = "回避"

        return StockScreeningResult(
            ticker="UNKNOWN",
            stock_name="未知",
            score=score,
            factors=factors,
            confidence=confidence,
            suggestion=suggestion
        )


class PortfolioAllocator:
    """投资组合分配器"""

    def __init__(self, max_stocks: int = 10, max_per_stock: float = 0.15,
                 min_per_stock: float = 0.05):
        self.max_stocks = max_stocks
        self.max_per_stock = max_per_stock
        self.min_per_stock = min_per_stock

    def allocate_equal_weight(self, results: List[StockScreeningResult],
                               total_capital: float) -> Dict[str, float]:
        """等权重分配"""
        top_stocks = sorted(results, key=lambda x: x.score, reverse=True)[:self.max_stocks]
        weight = min(max(1 / len(top_stocks), self.min_per_stock), self.max_per_stock)

        return {r.ticker: total_capital * weight for r in top_stocks}

    def allocate_by_confidence(self, results: List[StockScreeningResult],
                                total_capital: float) -> Dict[str, float]:
        """按置信度分配"""
        top = sorted(results, key=lambda x: x.score, reverse=True)[:self.max_stocks]

        if not top:
            return {}

        total_conf = sum(r.confidence for r in top)
        if total_conf == 0:
            return self.allocate_equal_weight(results, total_capital)

        allocations = {}
        for r in top:
            weight = r.confidence / total_conf
            weight = min(max(weight, self.min_per_stock), self.max_per_stock)
            allocations[r.ticker] = total_capital * weight

        return allocations


class MarketSentiment:
    """市场情绪分析"""

    @staticmethod
    def analyze_sentiment(news_sentiment: List[float]) -> Dict:
        """分析新闻情绪"""
        if not news_sentiment:
            return {"sentiment": "中性", "score": 0}

        avg_sentiment = np.mean(news_sentiment)
        if avg_sentiment > 0.3:
            return {"sentiment": "乐观", "score": avg_sentiment}
        elif avg_sentiment < -0.3:
            return {"sentiment": "悲观", "score": avg_sentiment}
        return {"sentiment": "中性", "score": avg_sentiment}
