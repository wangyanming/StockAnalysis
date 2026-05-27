"""
涨停数据采集模块（三重备份：AKShare -> 东财HTTP -> 新浪分页）
数据源：
  AKShare stock_zt_pool_em: 价格=元,封单量=手(x100->股),封单资金=元
  东财HTTP: 价格=元,封单=手(x100->股),成交额=元
  新浪分页: 价格=元
"""

import os
import json
from utils.dao import get_db
import pandas as pd
from typing import Dict, List, Optional, Any
from datetime import datetime, date
import logging

logger = logging.getLogger(__name__)

try:
    import akshare as ak
    HAS_AK = True
except ImportError:
    HAS_AK = False
    logger.warning("AKShare 未安装, 涨停板数据不可用")


def _get_db():
    return get_db()


def init_zt_tables():
    """初始化涨停板相关表"""
    db = _get_db()

    # 每日涨停板快照 — 使用 try-except 绕过已存在的表
    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS daily_limit_up (
                id INTEGER PRIMARY KEY AUTO_INCREMENT,
                trade_date TEXT NOT NULL,
                code TEXT NOT NULL,
                name TEXT NOT NULL,
                price REAL,
                change_pct REAL,
                turnover_rate REAL,
                seal_first_time TEXT,
                seal_last_time TEXT,
                board_times INTEGER DEFAULT 1,
                bomb_times INTEGER DEFAULT 0,
                seal_fund REAL DEFAULT 0,
                industry VARCHAR(64) DEFAULT '',
                concept TEXT,
                status VARCHAR(16) DEFAULT '首板',
                source_market VARCHAR(8) DEFAULT 'A股',
                raw_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(trade_date, code)
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS limit_up_tracking (
                id INTEGER PRIMARY KEY AUTO_INCREMENT,
                code TEXT NOT NULL,
                name TEXT NOT NULL,
                first_limit_date TEXT NOT NULL,
                latest_limit_date TEXT NOT NULL,
                total_limit_days INTEGER DEFAULT 1,
                max_board_count INTEGER DEFAULT 1,
                current_board_count INTEGER DEFAULT 1,
                first_price REAL,
                latest_price REAL,
                industry VARCHAR(64) DEFAULT '',
                status VARCHAR(32) DEFAULT '观察中',
                note TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE(code)
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS limit_up_industry_stats (
                id INTEGER PRIMARY KEY AUTO_INCREMENT,
                trade_date TEXT NOT NULL,
                industry VARCHAR(64) NOT NULL,
                count INTEGER DEFAULT 0,
                top_stocks TEXT,
                total_seal_fund REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(trade_date, industry)
            )
        """)
        try:
            db.execute("CREATE INDEX idx_zt_date ON daily_limit_up(trade_date)")
        except Exception:
            pass
        try:
            db.execute("CREATE INDEX idx_zt_code ON daily_limit_up(code)")
        except Exception:
            pass
        try:
            db.execute("CREATE INDEX idx_track_code ON limit_up_tracking(code)")
        except Exception:
            pass
        logger.info("涨停板表初始化完成")
    except Exception as e:
        logger.warning(f"建表跳过（可能已存在MySQL）: {e}")


class LimitUpAnalyzer:
    """涨停板分析器"""

    def __init__(self):
        init_zt_tables()

    def fetch_today_limit_up(self, trade_date: str = None) -> pd.DataFrame:
        """获取今日涨停板（含数据日期校验）"""
        if not HAS_AK:
            logger.error("AKShare 未安装")
            return pd.DataFrame()

        if not trade_date:
            trade_date = datetime.now().strftime("%Y%m%d")

        # 重试参数：交易时段最多3次（偶发空数据则重试，如收盘后东财短暂清缓存）
        max_retries = 3
        retry_delay = 3  # 秒
        
        for attempt in range(1, max_retries + 1):
            try:
                df = ak.stock_zt_pool_em(date=trade_date)
                if df is not None and not df.empty:
                    # 校验数据日期：东财接口在非交易时段可能返回上一交易日数据
                    # 非交易时段（盘前<09:30、午休11:30-13:00、收盘后>15:00）跳过
                    now = datetime.now()
                    hour_min = now.hour * 100 + now.minute
                    # 交易时段放宽到15:30（15:10盘中采集也需要涨停数据）
                    is_market_hours = (930 <= hour_min <= 1130) or (1300 <= hour_min <= 1530)
                    if not is_market_hours:
                        logger.debug(f"非交易时段（当前{now.strftime('%H:%M')}），跳过涨停数据采集")
                        return pd.DataFrame()
                    logger.info(f"获取涨停数据成功: {len(df)}只")
                    return df
                    
                # df为空：非交易时段或数据源暂不可用，重试
                if attempt < max_retries:
                    logger.warning(f"AKShare涨停数据为空(第{attempt}次)，{retry_delay}s后重试...")
                    import time as _time
                    _time.sleep(retry_delay)
                else:
                    logger.warning(f"AKShare涨停数据为空，{max_retries}次重试后放弃")
            except Exception as e:
                logger.warning(f"AKShare涨停接口失败(第{attempt}次): {e}")
                if attempt < max_retries:
                    import time as _time
                    _time.sleep(retry_delay)
        
        # AKShare失败时，用东财HTTP直接接口备用（同样重试3次）
        for attempt in range(1, max_retries + 1):
            try:
                import requests as _req
                url = 'https://push2.eastmoney.com/api/qt/clist/get'
                params = {
                    'fid': 'f3', 'po': 1, 'pz': 200, 'pn': 1, 'np': 1,
                    'fltt': 2, 'invt': 2,
                    'fs': 'm:0+t:6+f:!2,m:0+t:80+f:!2,m:1+t:2+f:!2,m:1+t:23+f:!2',
                    'fields': 'f2,f3,f4,f12,f14,f15,f16,f17,f18',
                    '_': '1680000000000',
                }
                headers = {
                    'User-Agent': 'Mozilla/5.0',
                    'Referer': 'https://data.eastmoney.com/',
                }
                r = _req.get(url, params=params, headers=headers, timeout=10)
                data = r.json()
                items = data.get('data', {}).get('diff', [])
                if items:
                    import pandas as _pd
                    rows = []
                    for item in items:
                        chg = item.get('f3', 0) or 0
                        cur = item.get('f2', 0) or 0
                        rows.append({
                            '代码': str(item.get('f12', '')),
                            '名称': item.get('f14', ''),
                            '最新价': cur,
                            '涨跌幅': chg,
                            '涨停统计': '首板',
                            '连板数': 1 if chg <= 10 else 1,
                            '换手率': item.get('f16', 0) or 0,
                            '封板资金': 0,
                            '首次封板时间': '',
                            '最后封板时间': '',
                            '所属行业': '',
                            '炸板次数': 0,
                        })
                    df = _pd.DataFrame(rows)
                    if not df.empty:
                        logger.info(f"备用接口获取涨停成功: {len(df)}只")
                        return df
                # 空数据，重试
                if attempt < max_retries:
                    logger.warning(f"东财备用接口涨停数据为空(第{attempt}次)，{retry_delay}s后重试...")
                    import time as _time
                    _time.sleep(retry_delay)
            except Exception as e2:
                logger.warning(f"东财备用涨停接口失败(第{attempt}次): {e2}")
                if attempt < max_retries:
                    import time as _time
                    _time.sleep(retry_delay)
        
        # 东财也失败时，用新浪接口兜底（非交易时段也可用）
        try:
            import urllib.request, json as _json
            all_zt = []
            for node in ['sh_a', 'sz_a']:
                url = (
                    f'https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/'
                    f'Market_Center.getHQNodeData?page=1&num=500&sort=changepercent&asc=0'
                    f'&node={node}&_s_r_a=page'
                )
                req = urllib.request.Request(url, headers={'Referer': 'https://finance.sina.com.cn'})
                text = urllib.request.urlopen(req, timeout=10).read().decode('gbk')
                data = _json.loads(text)
                for s in data:
                    chg = float(s.get('changepercent', 0))
                    code = s.get('code', '')
                    # 主板(60/00/000): 10%涨停; 创业板(300)/科创板(688): 20%涨停
                    if code.startswith(('60', '00')):
                        if chg >= 9.8:
                            all_zt.append(s)
                    elif code.startswith(('300', '688')):
                        if chg >= 19.8:
                            all_zt.append(s)
                    else:
                        if chg >= 9.8:
                            all_zt.append(s)
            if all_zt:
                import pandas as _pd
                rows = []
                for s in all_zt:
                    chg = float(s.get('changepercent', 0))
                    cur = float(s.get('trade', 0))
                    vol = float(s.get('amount', 0))
                    rows.append({
                        '代码': s.get('code', ''),
                        '名称': s.get('name', ''),
                        '最新价': cur,
                        '涨跌幅': chg,
                        '涨停统计': '首板',
                        '连板数': 1,
                        '换手率': float(s.get('turnratio', 0) or 0),
                        '封板资金': vol if chg >= 9.5 else 0,
                        '首次封板时间': '',
                        '最后封板时间': '',
                        '所属行业': '',
                        '炸板次数': 0,
                    })
                df = _pd.DataFrame(rows)
                logger.info(f"新浪接口获取涨停成功: {len(df)}只")
                return df
        except Exception as e3:
            logger.warning(f"新浪兜底涨停接口也失败: {e3}")
        
        logger.error("涨停数据获取失败（AKShare+东财+新浪均无数据）")
        return pd.DataFrame()

    def fetch_previous_limit_up(self, trade_date: str = None) -> pd.DataFrame:
        """获取昨日涨停股今日表现（晋级/断板）"""
        if not trade_date:
            # 往前推一天
            from datetime import timedelta
            trade_date = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
        return self.fetch_today_limit_up(trade_date)

    def save_today_limit_up(self, trade_date: str = None) -> Dict:
        """保存今日涨停板到数据库"""
        if not trade_date:
            trade_date = datetime.now().strftime("%Y%m%d")

        df = self.fetch_today_limit_up(trade_date)
        if df.empty:
            logger.warning(f"{trade_date} 无涨停数据")
            return {"count": 0, "status": "no_data"}

        db = _get_db()
        saved = 0

        for _, row in df.iterrows():
            code = str(row.get("代码", ""))
            name = str(row.get("名称", ""))

            # 判断连板状态
            board_times = self._parse_board(row.get("连板数", 1))
            bomb_times = int(row.get("炸板次数", 0))
            seal_fund = float(row.get("封板资金", 0))

            # 判断状态
            if board_times >= 3:
                status = "龙头"
            elif board_times >= 2:
                status = "连板"
            else:
                status = "首板"

            raw_json = json.dumps(row.to_dict(), ensure_ascii=False, default=str)

            try:
                db.execute("""
                    REPLACE INTO daily_limit_up 
                    (trade_date, code, name, price, change_pct, turnover_rate,
                     seal_first_time, seal_last_time, board_times, bomb_times,
                     seal_fund, industry, status, raw_json)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    trade_date, code, name,
                    float(row.get("最新价", 0)),
                    float(row.get("涨跌幅", 0)),
                    float(row.get("换手率", 0)),
                    str(row.get("首次封板时间", "")),
                    str(row.get("最后封板时间", "")),
                    board_times,
                    bomb_times,
                    seal_fund,
                    str(row.get("所属行业", "")),
                    status,
                    raw_json
                ))
                saved += 1
            except Exception as e:
                logger.error(f"保存 {code} {name} 失败: {e}")

        logger.info(f"保存涨停数据完成: {saved}/{len(df)}只")
        return {"count": saved, "total": len(df), "status": "ok"}

    def update_tracking(self, trade_date: str = None):
        """更新涨停追踪记录"""
        if not trade_date:
            trade_date = datetime.now().strftime("%Y%m%d")

        # 获取今日涨停
        df = self.fetch_today_limit_up(trade_date)
        if df.empty:
            return

        db = _get_db()

        for _, row in df.iterrows():
            code = str(row.get("代码", ""))
            name = str(row.get("名称", ""))
            board = self._parse_board(row.get("连板数", 1))

            # 检查是否已有追踪
            existing = db.fetchone("SELECT * FROM limit_up_tracking WHERE code = %s", (code,))

            if existing:
                # 更新
                max_board = max(existing["max_board_count"], board)
                db.execute("""
                    UPDATE limit_up_tracking SET
                        latest_limit_date = %s,
                        total_limit_days = total_limit_days + 1,
                        max_board_count = %s,
                        current_board_count = %s,
                        latest_price = %s,
                        status = CASE WHEN %s >= 2 THEN '连板中' ELSE '观察中' END,
                        updated_at = NOW()
                    WHERE code = %s
                """, (trade_date, max_board, board, float(row.get("最新价", 0)), board, code))
            else:
                # 新追踪
                db.execute("""
                    REPLACE INTO limit_up_tracking 
                    (code, name, first_limit_date, latest_limit_date,
                     total_limit_days, max_board_count, current_board_count,
                     first_price, latest_price, industry, status,
                     created_at, updated_at)
                    VALUES (%s, %s, %s, %s, 1, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                """, (
                    code, name, trade_date, trade_date,
                    board, board,
                    float(row.get("最新价", 0)), float(row.get("最新价", 0)),
                    str(row.get("所属行业", "")),
                    "首板" if board == 1 else "连板中"
                ))

        logger.info(f"涨停追踪更新完成")

    def save_industry_stats(self, trade_date: str = None):
        """保存板块涨停统计"""
        if not trade_date:
            trade_date = datetime.now().strftime("%Y%m%d")

        df = self.fetch_today_limit_up(trade_date)
        if df.empty:
            return

        db = _get_db()

        # 按行业分组
        for industry, group in df.groupby("所属行业"):
            count = len(group)
            top_stocks = ",".join(group["名称"].head(5).tolist())
            total_seal_fund = float(group["封板资金"].sum())

            db.execute("""
                REPLACE INTO limit_up_industry_stats
                (trade_date, industry, count, top_stocks, total_seal_fund)
                VALUES (%s, %s, %s, %s, %s)
            """, (trade_date, industry, count, top_stocks, total_seal_fund))

        logger.info(f"行业涨停统计保存完成")

    def run_daily_analysis(self, trade_date: str = None) -> Dict:
        """运行每日完整分析流程"""
        if not trade_date:
            trade_date = datetime.now().strftime("%Y%m%d")

        result = self.save_today_limit_up(trade_date)
        if result.get("count", 0) > 0:
            self.update_tracking(trade_date)
            self.save_industry_stats(trade_date)

        return result

    def get_today_limit_up(self, trade_date: str = None) -> List[Dict]:
        """查询当日涨停数据（过滤跌停）"""
        if not trade_date:
            trade_date = datetime.now().strftime("%Y%m%d")

        db = _get_db()
        rows = db.fetchall(
            "SELECT * FROM daily_limit_up WHERE trade_date = %s AND (status IS NULL OR status != '跌停') ORDER BY board_times DESC, seal_first_time ASC",
            (trade_date,)
        )
        return rows

    def get_today_limit_down(self, trade_date: str = None) -> List[Dict]:
        """查询当日跌停数据"""
        if not trade_date:
            trade_date = datetime.now().strftime("%Y%m%d")

        db = _get_db()
        rows = db.fetchall(
            "SELECT * FROM daily_limit_up WHERE trade_date = %s AND status = '跌停' ORDER BY seal_fund DESC",
            (trade_date,)
        )
        return rows

    def get_tracking_list(self, min_boards: int = 1, status: str = None) -> List[Dict]:
        """获取追踪列表"""
        db = _get_db()
        query = "SELECT * FROM limit_up_tracking WHERE 1=1"
        params = []

        if min_boards > 1:
            query += " AND max_board_count >= %s"
            params.append(min_boards)
        if status:
            query += " AND status = %s"
            params.append(status)

        query += " ORDER BY max_board_count DESC, total_limit_days DESC"
        rows = db.fetchall(query, tuple(params))
        return rows

    def get_industry_stats(self, trade_date: str = None) -> List[Dict]:
        """获取行业涨停统计"""
        if not trade_date:
            trade_date = datetime.now().strftime("%Y%m%d")

        db = _get_db()
        rows = db.fetchall(
            "SELECT * FROM limit_up_industry_stats WHERE trade_date = %s ORDER BY count DESC",
            (trade_date,)
        )
        return rows

    def get_continuous_trackers(self, min_days: int = 2) -> List[Dict]:
        """获取多天连续涨停追踪的股票"""
        db = _get_db()
        rows = db.fetchall(
            """SELECT * FROM limit_up_tracking 
               WHERE total_limit_days >= %s 
               ORDER BY total_limit_days DESC, max_board_count DESC""",
            (min_days,)
        )
        return rows

    @staticmethod
    def _parse_board(val) -> int:
        """解析连板数"""
        try:
            return int(val)
        except (ValueError, TypeError):
            return 0

    @staticmethod
    def get_available_dates() -> List[str]:
        """获取数据库中已有的涨停板数据日期列表（降序）"""
        db = _get_db()
        try:
            dates = set()
            for table in ['daily_limit_up', 'limit_up_industry_stats']:
                try:
                    rows = db.fetchall(
                        f"SELECT DISTINCT trade_date FROM {table} ORDER BY trade_date DESC"
                    )
                except Exception:
                    rows = db.fetchall(f"SELECT DISTINCT trade_date FROM {table} ORDER BY trade_date DESC")
                for row in rows:
                    dates.add(row['trade_date'])
            return sorted(dates, reverse=True)
        except Exception as e:
            logger.warning(f"获取可用日期失败: {e}")
            return []


# 初始化
init_zt_tables()
