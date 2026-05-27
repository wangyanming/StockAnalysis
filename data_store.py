"""
数据存储模块 - 股票行情数据存储 (支持 SQLite / MySQL 自动切换)
"""
import json
import os
from typing import Dict, List, Optional, Any
from datetime import datetime, date
import logging

logger = logging.getLogger(__name__)

from dao import get_db
db = get_db()


def _fix_sql(sql: str) -> str:
    """MySQL 无需转换占位符"""
    return sql


def _execute(sql: str, params: tuple = ()):
    """执行 SQL 并自动修复占位符"""
    cur = db.conn.cursor()
    cur.execute(_fix_sql(sql), params)
    return cur


def get_connection():
    """获取数据库连接（后向兼容）"""
    return db.conn


def init_db():
    """初始化数据库表（MySQL 已通过 db_schema.sql 建表，此函数仅做兼容）"""
    cur = db.conn.cursor()

    # SQLite建表语句 — MySQL模式会因AUTOINCREMENT/now()语法报错，用try跳过
    for tbl_sql, compat_sql in [
        ("""CREATE TABLE IF NOT EXISTS index_quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            index_code TEXT NOT NULL,
            name TEXT NOT NULL,
            current_price REAL,
            change_pct REAL,
            open REAL,
            high REAL,
            low REAL,
            volume REAL,
            amount REAL,
            timestamp TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )""",
        """CREATE TABLE IF NOT EXISTS index_quotes (
            id INTEGER PRIMARY KEY AUTO_INCREMENT,
            index_code TEXT NOT NULL,
            name TEXT NOT NULL,
            current_price REAL,
            change_pct REAL,
            open REAL,
            high REAL,
            low REAL,
            volume REAL,
            amount REAL,
            timestamp TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )"""),
        ("""CREATE TABLE IF NOT EXISTS stock_quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code TEXT NOT NULL,
            name TEXT NOT NULL,
            current_price REAL,
            change_pct REAL,
            open REAL,
            high REAL,
            low REAL,
            pre_close REAL,
            volume REAL,
            amount REAL,
            timestamp TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )""",
        """CREATE TABLE IF NOT EXISTS stock_quotes (
            id INTEGER PRIMARY KEY AUTO_INCREMENT,
            stock_code TEXT NOT NULL,
            name TEXT NOT NULL,
            current_price REAL,
            change_pct REAL,
            open REAL,
            high REAL,
            low REAL,
            pre_close REAL,
            volume REAL,
            amount REAL,
            timestamp TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )"""),

        ("""CREATE TABLE IF NOT EXISTS sector_performance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            record_date TEXT NOT NULL,
            record_time TEXT NOT NULL,
            sector_name TEXT NOT NULL,
            change_pct REAL DEFAULT 0,
            turn_over REAL DEFAULT 0,
            amount REAL DEFAULT 0,
            net_inflow REAL DEFAULT 0,
            rise_count INTEGER DEFAULT 0,
            fall_count INTEGER DEFAULT 0,
            rank_type TEXT DEFAULT 'top_gain',
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )""",
        """CREATE TABLE IF NOT EXISTS sector_performance (
            id INTEGER PRIMARY KEY AUTO_INCREMENT,
            record_date TEXT NOT NULL,
            record_time TEXT NOT NULL,
            sector_name TEXT NOT NULL,
            change_pct REAL DEFAULT 0,
            turn_over REAL DEFAULT 0,
            amount REAL DEFAULT 0,
            net_inflow REAL DEFAULT 0,
            rise_count INTEGER DEFAULT 0,
            fall_count INTEGER DEFAULT 0,
            rank_type VARCHAR(32) DEFAULT 'top_gain',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )"""),

    ]:
        try:
            cur.execute(tbl_sql)
        except Exception:
            try:
                cur.execute(compat_sql)
            except Exception as e:
                logger.warning(f"建表跳过: {e}")

    cur.close()

    # 清理已弃用的 market_summary 表（数据迁移至 sector_performance）
    try:
        cur = _execute("DROP TABLE IF EXISTS market_summary")
        cur.close()
    except Exception:
        pass

    logger.info("数据库初始化检查完成")


class QuoteStore:
    """行情数据存储"""

    def __init__(self):
        init_db()

    def save_index_quote(self, index_code: str, data: Dict) -> bool:
        """保存指数行情"""
        try:
            cur = _execute(
                """REPLACE INTO index_quotes 
                   (index_code, name, current_price, change_pct, open, high, low, volume, amount, timestamp, record_date)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    index_code,
                    data.get("name", index_code),
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
            return True
        except Exception as e:
            logger.error(f"保存指数行情失败: {e}")
            return False

    def save_stock_quote(self, stock_code: str, data: Dict) -> bool:
        """保存个股行情"""
        try:
            cur = _execute(
                """INSERT INTO stock_quotes 
                   (stock_code, name, current_price, change_pct, open, high, low, pre_close, volume, amount, timestamp)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    stock_code,
                    data.get("name", ""),
                    data.get("current_price", 0),
                    data.get("change_pct", 0),
                    data.get("open", 0),
                    data.get("high", 0),
                    data.get("low", 0),
                    data.get("pre_close", 0),
                    data.get("volume", 0),
                    data.get("amount", 0),
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    datetime.now().strftime("%Y-%m-%d")
                )
            )
            cur.close()
            return True
        except Exception as e:
            logger.error(f"保存个股行情失败: {e}")
            return False

    def get_index_history(self, index_code: str, days: int = 30) -> List[Dict]:
        """获取指数历史行情"""
        rows = db.fetchall(
            """SELECT * FROM index_quotes 
               WHERE index_code = %s 
               ORDER BY timestamp DESC 
               LIMIT %s""",
            (index_code, days)
        )
        return rows

    def get_stock_history(self, stock_code: str, days: int = 30) -> List[Dict]:
        """获取个股历史行情"""
        rows = db.fetchall(
            """SELECT * FROM stock_quotes 
               WHERE stock_code = %s 
               ORDER BY timestamp DESC 
               LIMIT %s""",
            (stock_code, days)
        )
        return rows

    def save_sector_performances(self, sectors: List[Dict], rank_type: str) -> bool:
        """保存板块表现数据"""
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            time_str = datetime.now().strftime("%H:%M:%S")

            for s in sectors:
                _execute(
                    """REPLACE INTO sector_performance
                       (record_date, record_time, sector_name, change_pct,
                        turn_over, amount, net_inflow, rise_count, fall_count, rank_type)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        today,
                        time_str,
                        s.get("name", ""),
                        s.get("change_pct", 0),
                        s.get("turn_over", 0),
                        s.get("amount", 0),
                        s.get("net_inflow", 0),
                        s.get("rise_count", 0),
                        s.get("fall_count", 0),
                        rank_type
                    )
                )
            return True
        except Exception as e:
            logger.error(f"保存板块表现失败: {e}")
            return False

    def get_sector_performances(self, date_str: str = None, rank_type: str = None) -> List[Dict]:
        """获取板块表现数据"""
        if not date_str:
            date_str = datetime.now().strftime("%Y-%m-%d")
        if rank_type:
            rows = db.fetchall(
                "SELECT * FROM sector_performance WHERE record_date = %s AND rank_type = %s ORDER BY id",
                (date_str, rank_type)
            )
        else:
            rows = db.fetchall(
                "SELECT * FROM sector_performance WHERE record_date = %s ORDER BY id",
                (date_str,)
            )
        return rows

    @classmethod
    def save_sector_performance(cls, date_str: str, sectors: List[Dict], rank_type: str) -> bool:
        """保存板块表现数据"""
        try:
            time_str = datetime.now().strftime("%H:%M:%S")
            for s in sectors:
                _execute(
                    """INSERT INTO sector_performance
                       (record_date, record_time, sector_name, change_pct, net_inflow, rank_type)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (
                        date_str,
                        time_str,
                        s.get("sector_name", s.get("name", "")),
                        s.get("change_pct", 0),
                        s.get("inflow", 0),
                        rank_type
                    )
                )
            return True
        except Exception as e:
            logger.error(f"保存板块表现失败: {e}")
            return False

    @classmethod
    def get_sector_performance(cls, date_str: str, rank_type: str = None) -> List[Dict]:
        """获取板块表现数据"""
        if rank_type:
            rows = db.fetchall(
                "SELECT * FROM sector_performance WHERE record_date = %s AND rank_type = %s ORDER BY id",
                (date_str, rank_type)
            )
        else:
            rows = db.fetchall(
                "SELECT * FROM sector_performance WHERE record_date = %s ORDER BY id",
                (date_str,)
            )
        return rows


