"""
SQLite → MySQL 数据迁移脚本
将 stock_data.db 全部数据迁移到 MySQL stock_analysis 库
用法: STOCK_DB_URL=mysql://root:stock123@127.0.0.1:3306/stock_analysis python3 migrate_to_mysql.py
"""

import os
import sys
import sqlite3
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# 连接 MySQL
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dao import DB, DB_PATH

# 排除的表（SQLite 内部表）
EXCLUDE_TABLES = {'sqlite_sequence', 'sqlite_stat1', 'sqlite_stat4'}

# 需要处理的 TEXT DEFAULT 兼容问题
TEXT_DEFAULT_FIX = {
    'daily_picks': {'highlights', 'grade', 'position_advice', 'source', 'data_tag'},
    'limit_up_industry_stats': {'top_stocks'},
    'limit_up_tracking': {'note'},
}

def get_sqlite_tables():
    conn = sqlite3.connect(DB_PATH)
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    conn.close()
    return [t[0] for t in tables if t[0] not in EXCLUDE_TABLES]


def get_sqlite_data(table: str, batch_size: int = 5000):
    """分批读取 SQLite 数据"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    offset = 0
    while True:
        rows = conn.execute(
            f"SELECT * FROM \"{table}\" ORDER BY id LIMIT ? OFFSET ?",
            (batch_size, offset)
        ).fetchall()
        if not rows:
            break
        yield [dict(r) for r in rows]
        offset += batch_size
    conn.close()


def migrate_table(db: DB, table: str):
    """迁移单张表"""
    # 获取 SQLite 总行数
    conn = sqlite3.connect(DB_PATH)
    total = conn.execute(f"SELECT COUNT(*) FROM \"{table}\"").fetchone()[0]
    conn.close()

    if total == 0:
        logger.info(f"  ⏭️ {table}: 空表，跳过")
        return

    # 清空 MySQL 表（防止重复迁移）
    try:
        db.execute(f"TRUNCATE TABLE {table}")
    except Exception:
        db.execute(f"DELETE FROM {table}")

    migrated = 0
    for batch_idx, batch_rows in enumerate(get_sqlite_data(table)):
        if not batch_rows:
            break

        # 构建批量插入
        if not batch_rows:
            continue

        row = batch_rows[0]
        cols = list(row.keys())

        # 检查是否需要 TEXT DEFAULT 修复
        text_fix_set = TEXT_DEFAULT_FIX.get(table, set())
        
        placeholders = ', '.join(['%s'] * len(cols))
        col_names = ', '.join(cols)

        # MySQL 批量插入
        values = []
        for r in batch_rows:
            vals = []
            for c in cols:
                v = r[c]
                # None → empty string for text fix
                if v is None and c in text_fix_set:
                    v = ''
                vals.append(v)
            values.append(vals)

        sql = f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})"
        
        try:
            cur = db.conn.cursor()
            cur.executemany(sql, values)
            db.conn.commit()
            migrated += len(batch_rows)
        except Exception as e:
            logger.error(f"  写入失败 {table} 第{batch_idx}批: {e}")
            # 单行插入，跳过有问题的行
            for v in values:
                try:
                    db.execute(sql, tuple(v))
                    migrated += 1
                except Exception as e2:
                    logger.warning(f"    跳过一行: {e2}")
        
        if (batch_idx + 1) % 5 == 0:
            logger.info(f"    {table}: {migrated}/{total} ({migrated/total*100:.0f}%)")
    
    logger.info(f"  ✅ {table}: 迁移完成 {migrated}/{total} 条")


def main():
    logger.info("🚀 开始 SQLite → MySQL 数据迁移")
    logger.info(f"  源: SQLite {DB_PATH}")
    logger.info(f"  目标: MySQL {os.environ.get('STOCK_DB_URL', '')}")
    
    # 检查是否有表
    mysql_db = DB()
    
    # 确认
    tables = get_sqlite_tables()
    logger.info(f"  待迁移表: {len(tables)} 张")
    for t in tables:
        conn = sqlite3.connect(DB_PATH)
        cnt = conn.execute(f"SELECT COUNT(*) FROM \"{t}\"").fetchone()[0]
        conn.close()
        logger.info(f"    {t}: {cnt:,} 条")
    
    # 确认
    total_estimate = sum(
        sqlite3.connect(DB_PATH).execute(f"SELECT COUNT(*) FROM \"{t}\"").fetchone()[0]
        for t in tables
    )
    logger.info(f"  总记录数: {total_estimate:,} 条")
    logger.info("  开始迁移...")
    
    start = datetime.now()
    for table in tables:
        migrate_table(mysql_db, table)
    
    elapsed = (datetime.now() - start).total_seconds()
    logger.info(f"\n🎉 迁移完成！耗时 {elapsed:.0f} 秒 ({elapsed/60:.1f} 分钟)")
    
    # 验证
    logger.info("\n📊 迁移后 MySQL 数据量:")
    for t in tables:
        cnt = mysql_db.fetchone(f"SELECT COUNT(*) as cnt FROM {t}")
        logger.info(f"  {t}: {cnt['cnt']:,} 条")
    
    mysql_db.close()


if __name__ == "__main__":
    # 注意: 迁移需要 1~5 分钟（280万条 stock_daily）
    main()
