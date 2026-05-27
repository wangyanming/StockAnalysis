"""
预警系统模块 - 监控股票异常波动并发送预警
"""
from typing import Dict, List, Optional, Tuple, Callable
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import json
import os
import logging
from dataclasses import dataclass, field

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class Alert:
    """预警记录"""
    ticker: str
    stock_name: str
    alert_type: str
    message: str
    severity: str          # "info", "warning", "critical"
    current_value: float
    threshold: float
    timestamp: datetime = field(default_factory=datetime.now)
    triggered: bool = False


class AlertSystem:
    """股票预警系统"""

    def __init__(self, config_file: str = "alert_config.json"):
        self.config = self._load_config(config_file)
        self.alerts: List[Alert] = []
        self.alert_handlers: List[Callable] = []
        self.config_file = config_file

    def _load_config(self, config_file: str) -> Dict:
        """加载预警配置"""
        default_config = {
            "price_change_pct": {
                "threshold": 5.0,
                "severity": "warning",
                "enabled": True
            },
            "volume_surge": {
                "threshold": 1.5,
                "severity": "info",
                "enabled": True
            },
            "rsi_oversold": {
                "threshold": 30.0,
                "severity": "warning",
                "enabled": True
            },
            "rsi_overbought": {
                "threshold": 70.0,
                "severity": "info",
                "enabled": True
            },
            "bb_breakout": {
                "threshold": 0,
                "severity": "critical",
                "enabled": True
            },
            "macd_crossover": {
                "threshold": 0,
                "severity": "warning",
                "enabled": True
            },
            "daily_change_pct": {
                "threshold": 3.0,
                "severity": "info",
                "enabled": True
            }
        }

        if os.path.exists(config_file):
            try:
                with open(config_file, "r", encoding="utf-8") as f:
                    user_config = json.load(f)
                    default_config.update(user_config)
            except Exception as e:
                logger.error(f"Failed to load config: {e}")

        return default_config

    def save_config(self):
        """保存预警配置"""
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save config: {e}")

    def add_alert_handler(self, handler: Callable):
        """添加预警处理器"""
        self.alert_handlers.append(handler)

    def check_price_change(self, previous_close: float, current_price: float,
                           ticker: str, stock_name: str = "") -> Optional[Alert]:
        """检查价格异常变动"""
        if current_price == 0 or previous_close == 0:
            return None

        config = self.config.get("price_change_pct", {})
        if not config.get("enabled", True):
            return None

        change_pct = ((current_price - previous_close) / previous_close) * 100
        threshold = config.get("threshold", 5.0)

        if abs(change_pct) >= threshold:
            return Alert(
                ticker=ticker,
                stock_name=stock_name,
                alert_type="price_change",
                message=f"{stock_name} 价格变动 {change_pct:.2f}% (阈值: {threshold:.1f}%)",
                severity=config.get("severity", "warning"),
                current_value=change_pct,
                threshold=threshold,
                triggered=True
            )
        return None

    def check_volume_surge(self, previous_volume: float, current_volume: float,
                            ticker: str, stock_name: str = "") -> Optional[Alert]:
        """检查成交量异常放大"""
        if previous_volume == 0:
            return None

        config = self.config.get("volume_surge", {})
        if not config.get("enabled", True):
            return None

        vol_ratio = current_volume / previous_volume if previous_volume > 0 else 0
        threshold = config.get("threshold", 1.5)

        if vol_ratio >= threshold:
            return Alert(
                ticker=ticker,
                stock_name=stock_name,
                alert_type="volume_surge",
                message=f"{stock_name} 成交量放大 {vol_ratio:.2f} 倍 (阈值: {threshold:.1f}倍)",
                severity=config.get("severity", "info"),
                current_value=vol_ratio,
                threshold=threshold,
                triggered=True
            )
        return None

    def check_rsi(self, rsi_value: float, ticker: str,
                   stock_name: str = "") -> Optional[Alert]:
        """检查 RSI 指标"""
        if rsi_value is None:
            return None

        # 超卖检查
        oversold_config = self.config.get("rsi_oversold", {})
        if oversold_config.get("enabled", True) and rsi_value <= oversold_config.get("threshold", 30.0):
            return Alert(
                ticker=ticker,
                stock_name=stock_name,
                alert_type="rsi_oversold",
                message=f"{stock_name} RSI 超卖: {rsi_value:.2f} (阈值: {oversold_config['threshold']})",
                severity=oversold_config.get("severity", "warning"),
                current_value=rsi_value,
                threshold=oversold_config["threshold"],
                triggered=True
            )

        # 超买检查
        overbought_config = self.config.get("rsi_overbought", {})
        if overbought_config.get("enabled", True) and rsi_value >= overbought_config.get("threshold", 70.0):
            return Alert(
                ticker=ticker,
                stock_name=stock_name,
                alert_type="rsi_overbought",
                message=f"{stock_name} RSI 超买: {rsi_value:.2f} (阈值: {overbought_config['threshold']})",
                severity=overbought_config.get("severity", "info"),
                current_value=rsi_value,
                threshold=overbought_config["threshold"],
                triggered=True
            )
        return None

    def check_bb_breakout(self, close_price: float, bb_upper: float,
                           bb_lower: float, ticker: str,
                           stock_name: str = "") -> Optional[Alert]:
        """检查布林带突破"""
        if bb_upper is None or bb_lower is None:
            return None

        config = self.config.get("bb_breakout", {})
        if not config.get("enabled", True):
            return None

        if close_price >= bb_upper:
            return Alert(
                ticker=ticker,
                stock_name=stock_name,
                alert_type="bb_upper_breakout",
                message=f"{stock_name} 突破布林带上轨 (价格: {close_price:.2f}, 上轨: {bb_upper:.2f})",
                severity=config.get("severity", "critical"),
                current_value=close_price - bb_upper,
                threshold=0,
                triggered=True
            )
        elif close_price <= bb_lower:
            return Alert(
                ticker=ticker,
                stock_name=stock_name,
                alert_type="bb_lower_breakout",
                message=f"{stock_name} 跌破布林带下轨 (价格: {close_price:.2f}, 下轨: {bb_lower:.2f})",
                severity=config.get("severity", "critical"),
                current_value=bb_lower - close_price,
                threshold=0,
                triggered=True
            )
        return None

    def check_macd(self, macd_value: float, signal_value: float,
                    ticker: str, stock_name: str = "") -> Optional[Alert]:
        """检查 MACD 金叉/死叉"""
        if macd_value is None or signal_value is None:
            return None

        config = self.config.get("macd_crossover", {})
        if not config.get("enabled", True):
            return None

        if macd_value > signal_value:
            alert_type = "macd_golden_cross"
            message = f"{stock_name} MACD 金叉 (MACD: {macd_value:.4f}, 信号: {signal_value:.4f})"
        else:
            alert_type = "macd_death_cross"
            message = f"{stock_name} MACD 死叉 (MACD: {macd_value:.4f}, 信号: {signal_value:.4f})"

        return Alert(
            ticker=ticker,
            stock_name=stock_name,
            alert_type=alert_type,
            message=message,
            severity=config.get("severity", "warning"),
            current_value=macd_value - signal_value,
            threshold=0,
            triggered=True
        )

    def check_daily_change(self, current_price: float, previous_close: float,
                            ticker: str, stock_name: str = "") -> Optional[Alert]:
        """检查日涨跌"""
        if previous_close == 0:
            return None

        config = self.config.get("daily_change_pct", {})
        if not config.get("enabled", True):
            return None

        change_pct = ((current_price - previous_close) / previous_close) * 100
        threshold = config.get("threshold", 3.0)

        if abs(change_pct) >= threshold:
            return Alert(
                ticker=ticker,
                stock_name=stock_name,
                alert_type="daily_change",
                message=f"{stock_name} 今日涨跌 {change_pct:+.2f}% (阈值: {threshold:.1f}%)",
                severity=config.get("severity", "info"),
                current_value=change_pct,
                threshold=threshold,
                triggered=True
            )
        return None

    def run_all_checks(self, data: Dict) -> List[Alert]:
        """运行所有检查"""
        alerts = []

        # 价格变动检查
        alert = self.check_price_change(
            data.get("previous_close", 0),
            data.get("close", 0),
            data.get("ticker", ""),
            data.get("stock_name", "")
        )
        if alert:
            alerts.append(alert)

        # 成交量检查
        alert = self.check_volume_surge(
            data.get("previous_volume", 0),
            data.get("volume", 0),
            data.get("ticker", ""),
            data.get("stock_name", "")
        )
        if alert:
            alerts.append(alert)

        # RSI 检查
        if "indicators" in data:
            alert = self.check_rsi(
                data["indicators"].get("latest_rsi"),
                data.get("ticker", ""),
                data.get("stock_name", "")
            )
            if alert:
                alerts.append(alert)

            alert = self.check_bb_breakout(
                data.get("close", 0),
                data["indicators"].get("latest_bb_upper"),
                data["indicators"].get("latest_bb_lower"),
                data.get("ticker", ""),
                data.get("stock_name", "")
            )
            if alert:
                alerts.append(alert)

            alert = self.check_macd(
                data["indicators"].get("latest_macd"),
                data["indicators"].get("latest_signal"),
                data.get("ticker", ""),
                data.get("stock_name", "")
            )
            if alert:
                alerts.append(alert)

        # 日涨跌检查
        alert = self.check_daily_change(
            data.get("close", 0),
            data.get("previous_close", 0),
            data.get("ticker", ""),
            data.get("stock_name", "")
        )
        if alert:
            alerts.append(alert)

        return alerts

    def dispatch_alerts(self, alerts: List[Alert]):
        """分发预警"""
        for alert in alerts:
            self.alerts.append(alert)
            for handler in self.alert_handlers:
                try:
                    handler(alert)
                except Exception as e:
                    logger.error(f"Alert handler failed: {e}")

    def get_active_alerts(self, severity: str = None) -> List[Alert]:
        """获取活跃预警"""
        if severity:
            return [a for a in self.alerts if a.severity == severity and a.triggered]
        return [a for a in self.alerts if a.triggered]
