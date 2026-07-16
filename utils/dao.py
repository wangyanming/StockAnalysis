"""
数据库统一访问层 (DAO) — 连接池版本
默认连接 MySQL，使用 DBUtils.PooledDB 连接池替代单连接模式。

使用方式:
    from utils.dao import get_db, DB
    db = get_db()

环境变量:
    STOCK_DB_URL=mysql://root:***@127.0.0.1:3306/stock_analysis
    如果未设置，默认走 MySQL (127.0.0.1:3306/stock_analysis)

连接池:
    使用 DBUtils.PooledDB，maxconnections=10, mincached=2。
    每个 execute/fetchone/fetchall 方法内部临时从池取连接，用完自动归还。
    自动重试 1 次（连接断开后重建再试）。
"""

import os
import re
import logging
from typing import Any, Optional

import pymysql
from dbutils.pooled_db import PooledDB

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# 环境变量配置
# ─────────────────────────────────────────────

# 默认 MySQL 连接（无需环境变量即可工作）
# 密码可通过环境变量 STOCK_DB_URL 覆盖
_DEFAULT_MYSQL_URL = "mysql://root:stock123@127.0.0.1:3306/stock_analysis"
DB_URL = os.environ.get("STOCK_DB_URL", _DEFAULT_MYSQL_URL)

# 连接池参数
_POOL_MAXCONNECTIONS = 10  # 最大连接数
_POOL_MINCACHED = 2        # 最小缓存连接数
_POOL_BLOCKING = True      # 无可用连接时阻塞等待
_POOL_MAXUSAGE = 1000      # 单连接复用上限，达到后自动重建
_POOL_CHARSET = "utf8mb4"  # 字符集


# ─────────────────────────────────────────────
# 连接解析
# ─────────────────────────────────────────────

def parse_mysql_url(url: str) -> dict:
    """解析 mysql://user:pass@host:port/dbname"""
    m = re.match(r'mysql://(.+?):(.+?)@(.+?):(\d+)/(.+)', url)
    if not m:
        m = re.match(r'mysql://(.+?):(.+?)@(.+?)/(.+)', url)
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
# DAO 类（连接池版）
# ─────────────────────────────────────────────

class DB:
    """统一数据库访问接口（连接池版，仅 MySQL）"""

    def __init__(self):
        self._mysql_cfg = parse_mysql_url(DB_URL)
        self._pool: Optional[PooledDB] = None
        self._init_pool()
        logger.info(
            f"🔗 连接池已初始化: {self._mysql_cfg['host']}:{self._mysql_cfg['port']}/"
            f"{self._mysql_cfg['database']} "
            f"(max={_POOL_MAXCONNECTIONS}, min={_POOL_MINCACHED})"
        )

    def _init_pool(self):
        """初始化连接池"""
        self._pool = PooledDB(
            creator=pymysql,
            mincached=_POOL_MINCACHED,
            maxcached=_POOL_MAXCONNECTIONS,
            maxconnections=_POOL_MAXCONNECTIONS,
            blocking=_POOL_BLOCKING,
            maxusage=_POOL_MAXUSAGE,
            ping=1,  # 从池取出时 ping 检查连接健康
            # 以下为 pymysql.connect 参数（通过 kwargs 透传）
            host=self._mysql_cfg['host'],
            port=self._mysql_cfg['port'],
            user=self._mysql_cfg['user'],
            password=self._mysql_cfg['password'],
            database=self._mysql_cfg['database'],
            charset=_POOL_CHARSET,
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
        )

    def _get_conn(self):
        """从连接池获取一条连接"""
        if self._pool is None:
            self._init_pool()
        return self._pool.connection()

    def _execute_with_retry(self, sql: str, params: tuple,
                            method: str = "execute") -> Any:
        """
        执行 SQL，连接异常时自动重试 1 次

        每次调用都从连接池取新连接，执行完后自动归还。
        如果不指定 method，默认执行 execute 并返回 cursor。
        支持 method="fetchone"/"fetchall" 直接返回结果。
        """
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                if method == "fetchone":
                    return cur.fetchone()
                elif method == "fetchall":
                    return cur.fetchall()
                return cur
        except (pymysql.err.InterfaceError,
                pymysql.err.OperationalError,
                pymysql.err.InternalError) as exc:
            # 连接相关异常：关闭旧连接（触发 PooledDB 丢弃），重试 1 次
            logger.warning(f"连接异常，准备重试: {exc}")
            try:
                conn.close()  # 归还/丢弃旧连接
            except Exception:
                pass
            # 重试：取新连接
            conn2 = self._get_conn()
            try:
                with conn2.cursor() as cur:
                    cur.execute(sql, params)
                    if method == "fetchone":
                        return cur.fetchone()
                    elif method == "fetchall":
                        return cur.fetchall()
                    return cur
            except Exception as e2:
                logger.error(
                    f"重试仍失败 | SQL: {sql} | params: {params} | error: {e2}"
                )
                raise
        except Exception:
            conn.close()
            raise
        else:
            conn.close()  # 正常情况：归还连接到池

    def close(self):
        """
        关闭连接池中所有连接

        保持原语义不变。close() 后再次调用会重新初始化连接池。
        调用方无需手动管理连接归还（各方法内部自动归还）。
        """
        if self._pool is not None:
            try:
                self._pool.close()
            except Exception as e:
                logger.warning(f"关闭连接池异常 (可能已关闭): {e}")
            self._pool = None
            logger.info("🔌 连接池已关闭")

    def execute(self, sql: str, params: tuple = ()) -> Any:
        """执行 SQL，返回 cursor"""
        return self._execute_with_retry(sql, params, method="execute")

    def executemany(self, sql: str, params_list: list):
        """批量执行"""
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.executemany(sql, params_list)
                return cur
        except Exception:
            raise
        finally:
            conn.close()

    def fetchone(self, sql: str, params: tuple = ()) -> Optional[dict]:
        """查询单条"""
        return self._execute_with_retry(sql, params, method="fetchone")

    def fetchall(self, sql: str, params: tuple = ()) -> list:
        """查询多条"""
        return self._execute_with_retry(sql, params, method="fetchall")

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
        # __exit__ 不关闭连接池（DB 是单例，关闭后后续调用无法使用）
        # 连接已在各方法内部自动归还到池
        pass


# ─────────────────────────────────────────────
# 快捷函数
# ─────────────────────────────────────────────

_db_instance: Optional[DB] = None


def get_db() -> DB:
    """获取全局 DB 实例（单例，内部持有连接池）"""
    global _db_instance
    if _db_instance is None:
        _db_instance = DB()
    return _db_instance
