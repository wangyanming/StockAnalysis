"""
数据库统一访问层 (DAO)
默认连接 MySQL，不再支持 SQLite。

使用方式:
    from dao import get_db, DB
    db = get_db()

环境变量:
    STOCK_DB_URL=mysql://root:stock123@127.0.0.1:3306/stock_analysis
    如果未设置，默认走 MySQL (127.0.0.1:3306/stock_analysis)
"""

import os
import re
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# 环境变量配置
# ─────────────────────────────────────────────

# 默认 MySQL 连接（无需环境变量即可工作）
_DEFAULT_MYSQL_URL = "mysql://root:stock123@127.0.0.1:3306/stock_analysis"
DB_URL = os.environ.get("STOCK_DB_URL", _DEFAULT_MYSQL_URL)


# ─────────────────────────────────────────────
# 连接解析
# ─────────────────────────────────────────────

def parse_mysql_url(url: str) -> dict:
    """解析 mysql://user:pass@host:port/dbname"""
    m = re.match(r'mysql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)', url)
    if not m:
        m = re.match(r'mysql://([^:]+):([^@]+)@([^/]+)/(.+)', url)
        if m:
            return {'user': m.group(1), 'password': m.group(2),
                    'host': m.group(3), 'port': 3306, 'database': m.group(4)}
        raise ValueError(f"无法解析 MySQL URL: {url}")
    return {
        'user': m.group(1),
        'password': m.group(2),
        'host': m.group(3),
        'port': int(m.group(4)),
        'database': m.group(5),
    }


# ─────────────────────────────────────────────
# DAO 抽象类
# ─────────────────────────────────────────────

class DB:
    """统一数据库访问接口（仅 MySQL）"""

    def __init__(self):
        self._conn = None
        self._mysql_cfg = parse_mysql_url(DB_URL)
        logger.info(
            f"🔗 使用 MySQL: {self._mysql_cfg['host']}:{self._mysql_cfg['port']}/{self._mysql_cfg['database']}"
        )

    @property
    def conn(self):
        if self._conn is None:
            self._connect()
        return self._conn

    def _connect(self):
        """建立 MySQL 连接"""
        import pymysql
        self._conn = pymysql.connect(
            host=self._mysql_cfg['host'],
            port=self._mysql_cfg['port'],
            user=self._mysql_cfg['user'],
            password=self._mysql_cfg['password'],
            database=self._mysql_cfg['database'],
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
        )

    def close(self):
        """关闭连接"""
        if self._conn:
            self._conn.close()
            self._conn = None

    def execute(self, sql: str, params: tuple = ()) -> Any:
        """执行 SQL，返回 cursor"""
        cur = self.conn.cursor()
        cur.execute(sql, params)
        return cur

    def executemany(self, sql: str, params_list: list):
        """批量执行"""
        cur = self.conn.cursor()
        cur.executemany(sql, params_list)
        return cur

    def fetchone(self, sql: str, params: tuple = ()) -> Optional[dict]:
        """查询单条"""
        cur = self.execute(sql, params)
        return cur.fetchone()

    def fetchall(self, sql: str, params: tuple = ()) -> list:
        """查询多条"""
        cur = self.execute(sql, params)
        return cur.fetchall()

    def insert(self, table: str, data: dict) -> int:
        """插入一条记录，返回影响行数"""
        cols = ', '.join(data.keys())
        placeholders = ', '.join(['%s' for _ in data])
        sql = f"INSERT INTO {table} ({cols}) VALUES ({placeholders})"
        cur = self.execute(sql, tuple(data.values()))
        return cur.rowcount

    def insert_or_ignore(self, table: str, data: dict) -> int:
        """插入或忽略（存在唯一约束时跳过）"""
        cols = ', '.join(f'`{k}`' for k in data.keys())
        placeholders = ', '.join(['%s' for _ in data])
        sql = f"INSERT IGNORE INTO {table} ({cols}) VALUES ({placeholders})"
        cur = self.execute(sql, tuple(data.values()))
        return cur.rowcount

    def table_exists(self, table_name: str) -> bool:
        """检查表是否存在"""
        r = self.fetchone(
            "SELECT TABLE_NAME FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s",
            (self._mysql_cfg['database'], table_name)
        )
        return r is not None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


# ─────────────────────────────────────────────
# 快捷函数
# ─────────────────────────────────────────────

_db_instance: Optional[DB] = None


def get_db() -> DB:
    """获取全局 DB 实例（单例）"""
    global _db_instance
    if _db_instance is None:
        _db_instance = DB()
    return _db_instance
