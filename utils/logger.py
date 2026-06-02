"""
统一日志工具
============
职责：
  1. 提供全局日志初始化函数 setup_logger()
  2. 日志同时输出到文件 (logs/ 目录) 和 stdout
  3. 自动创建日志目录，自动按天轮转 (保留 30 天)
  4. 统一格式：YYYY-MM-DD HH:MM:SS [LEVEL] module: message
  5. 提供 timing() 便捷函数替代到处 print[TIMING]

用法：
    from utils.logger import setup_logger, timing
    logger = setup_logger("close_task")
    t = timing("数据加载")   # 开始计时
    # ... 做事情 ...
    t.done()                # 输出: 2026-06-02 16:30:00 [INFO] close_task: [TIMING] 数据加载: 3.2s

    # 或原始方式：
    # t_start = time.time()
    # ... 做事情 ...
    # timing("数据加载", t_start)
"""

import os
import sys
import time
import logging
from logging.handlers import TimedRotatingFileHandler
from typing import Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")

# 统一日志格式
_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_DEFAULT_LEVEL = logging.INFO

# 记录已初始化的 logger 名称，防止重复 basicConfig
_initialized = set()


class TimingHelper:
    """计时辅助类，用于分步计时。"""

    def __init__(self, label: str, logger_obj: logging.Logger):
        self.label = label
        self.logger = logger_obj
        self.start = time.time()

    def done(self, extra: str = "") -> float:
        """停止计时并输出 [TIMING] 日志。返回耗时秒数。"""
        elapsed = time.time() - self.start
        msg = f"[TIMING] {self.label}: {elapsed:.1f}s"
        if extra:
            msg += f" | {extra}"
        self.logger.info(msg)
        return elapsed


def _ensure_log_dir() -> str:
    """确保日志目录存在。"""
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR, exist_ok=True)
    return LOG_DIR


def _resolve_log_file(name: str) -> str:
    """将 logger 名称转为日志文件路径。"""
    return os.path.join(_ensure_log_dir(), f"{name}.log")


def setup_logger(
    name: str,
    level: int = _DEFAULT_LEVEL,
    log_file: Optional[str] = None,
    console: bool = True,
    when: str = "midnight",
    backup_count: int = 30,
) -> logging.Logger:
    """
    初始化一个命名的 logger，同时输出到文件和 stdout。

    参数:
        name: logger 名称（也是日志文件名，如 "close_task" → logs/close_task.log）
        level: 日志级别，默认 INFO
        log_file: 日志文件路径，默认 logs/<name>.log
        console: 是否同时输出到 stdout，默认 True
        when: 日志轮转周期，默认每天轮转，可选 'midnight', 'H', 'D', 'W0'-'W6', 'S'
        backup_count: 保留历史日志文件数，默认 30 天

    返回:
        配置好的 logging.Logger 实例

    注意:
        同一个 name 多次调用只初始化一次（全局共享 logger），
        但不会重复加 handler（防止日志重复输出）。
    """
    # 如果已初始化直接返回
    if name in _initialized:
        return logging.getLogger(name)

    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.handlers.clear()  # 清除可能残留的 handler

    # 控制台输出
    if console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(logging.Formatter(_FORMAT, _DATE_FORMAT))
        logger.addHandler(console_handler)

    # 文件输出（按天轮转）
    file_path = log_file or _resolve_log_file(name)
    file_handler = TimedRotatingFileHandler(
        file_path,
        when=when,
        interval=1,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(logging.Formatter(_FORMAT, _DATE_FORMAT))
    logger.addHandler(file_handler)

    # 阻止日志向上传播到 root logger
    logger.propagate = False

    _initialized.add(name)
    return logger


def timing(label: str, t_start: Optional[float] = None, logger_obj: Optional[logging.Logger] = None) -> float:
    """
    输出分步计时日志（兼容旧版 print[TIMING] 调用）。

    参数:
        label: 步骤名称
        t_start: 起始时间 (time.time())，如果为 None 则返回当前时间供手动管理
        logger_obj: logger 实例，默认为 "close_task" 的 logger

    返回:
        t_start 为 None → 返回当前时间（供后续调用传入）
        否则 → 返回耗时秒数
    """
    if t_start is None:
        # 只返回当前时间，供调用方记录起点
        return time.time()

    elapsed = time.time() - t_start
    logger = logger_obj or logging.getLogger("close_task")
    logger.info(f"[TIMING] {label}: {elapsed:.1f}s")
    return elapsed


# ============================================================
# 快速旧版迁移辅助：兼容 close_task.py 的 _log_timing 调用方式
# 新代码建议用 TimingHelper 或 setup_logger + logger.info
# ============================================================

def log_timing_legacy(t_start: float, label: str, logger_name: str = "close_task") -> None:
    """兼容旧版 _log_timing 函数签名，直接用 logger 输出。"""
    elapsed = time.time() - t_start
    logging.getLogger(logger_name).info(f"[TIMING] {label}: {elapsed:.1f}s")
