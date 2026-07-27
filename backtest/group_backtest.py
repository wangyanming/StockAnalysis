#!/usr/bin/env python3
"""
分组回测脚本 — 根据评分 A/B/C/D 四组独立模拟交易

分组规则:
  A组: total_score < 60, 取评分前5名（同分按score desc, source asc）
  B组: 60 <= total_score < 65, 取全部
  C组: 65 <= total_score < 70, 取全部
  D组: total_score >= 70, 取全部

交易规则:
  T日选股 -> T+1开盘买入 -> T+2开盘卖出
  跳过: 成交量=0(停牌); T+1日涨跌幅>=9.5%且成交量<前20日均量*0.3(一字板)

用法:
  python3 backtest/group_backtest.py

输出:
  写入表 backtest_group_results（自动建表）
  控制台输出四组明细+统计+对比矩阵
"""

import sys
import os
from datetime import datetime, timedelta
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.dao import get_db

# ─────────────────────────────────────────────
# 全局常量
# ─────────────────────────────────────────────
START_DATE = "20260513"
END_DATE = "20260721"
BATCH_ID = "group_v1"

HOLIDAY_20260619 = "20260619"  # 假期，选股日跳过 且 T+1/T+2 不取这天

GROUP_DEFS = [
    ("A", "<60分(TOP5)",   lambda s: s < 60,      5),
    ("B", "60~65分(全部)", lambda s: 60 <= s < 65, None),
    ("C", "65~70分(全部)", lambda s: 65 <= s < 70, None),
    ("D", ">=70分(全部)",   lambda s: s >= 70,      None),
]

# ─────────────────────────────────────────────
# 建表
# ─────────────────────────────────────────────
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS backtest_group_results (
    id INT AUTO_INCREMENT PRIMARY KEY,
    batch_id VARCHAR(32) NOT NULL DEFAULT 'group_v1',
    trade_date VARCHAR(8) NOT NULL COMMENT 'T选股日',
    code VARCHAR(8) NOT NULL,
    name VARCHAR(32) DEFAULT '',
    total_score DECIMAL(5,1) DEFAULT 0,
    group_tag CHAR(1) NOT NULL COMMENT 'A/B/C/D',
    source VARCHAR(32) DEFAULT '',
    entry_price DECIMAL(10,2) DEFAULT 0 COMMENT 'T日收盘',
    buy_date VARCHAR(8) DEFAULT NULL COMMENT 'T+1买入日',
    buy_price DECIMAL(10,2) DEFAULT 0 COMMENT 'T+1开盘买入价',
    sell_date VARCHAR(8) DEFAULT NULL COMMENT 'T+2卖出日',
    sell_price DECIMAL(10,2) DEFAULT 0 COMMENT 'T+2开盘卖出价',
    profit_rate DECIMAL(6,4) DEFAULT NULL COMMENT '收益率(小数)',
    stop_reason VARCHAR(128) DEFAULT NULL COMMENT '跳过/失败原因',
    sh_change DECIMAL(6,2) DEFAULT 0 COMMENT '当日上证涨跌幅',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_batch (batch_id),
    INDEX idx_group (batch_id, group_tag)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='分组回测明细';
"""


# ─────────────────────────────────────────────
# 辅助函数
# ─────────────────────────────────────────────
def ensure_table(db):
    """若表不存在则创建"""
    db.execute(CREATE_TABLE_SQL)


def get_trade_dates(db, start, end):
    """获取区间内所有交易日（排除假期 20260619）"""
    rows = db.fetchall(
        "SELECT DISTINCT trade_date FROM stock_daily "
        "WHERE trade_date >= %s AND trade_date <= %s "
        "AND trade_date NOT IN (%s) "
        "ORDER BY trade_date",
        (start, end, HOLIDAY_20260619),
    )
    return [r["trade_date"] for r in rows]


def get_next_trade_dates(db, code, after_date, limit=2):
    """
    获取某只股票在 after_date 之后的后续交易日数据
    跳过 holiday 20260619
    返回 [(trade_date, open, close, volume, change_pct), ...]
    """
    rows = db.fetchall(
        "SELECT trade_date, open, close, volume, change_pct "
        "FROM stock_daily "
        "WHERE code = %s AND trade_date > %s "
        "AND trade_date != %s "
        "ORDER BY trade_date LIMIT %s",
        (code, after_date, HOLIDAY_20260619, limit),
    )
    return [(r["trade_date"], r["open"], r["close"], r["volume"], r["change_pct"]) for r in rows]


def get_avg_volume_20(db, code, before_date):
    """
    获取某只股票在 before_date 之前 20 个交易日的平均成交量
    跳过 holiday 20260619
    """
    rows = db.fetchall(
        "SELECT volume FROM stock_daily "
        "WHERE code = %s AND trade_date < %s "
        "AND trade_date != %s "
        "ORDER BY trade_date DESC LIMIT 20",
        (code, before_date, HOLIDAY_20260619),
    )
    volumes = [r["volume"] for r in rows if r["volume"] and r["volume"] > 0]
    if not volumes:
        return 0


# ─────────────────────────────────────────────
# 扩展回测：不同买卖时点
# ─────────────────────────────────────────────

def get_candidates_from_group_v1(db):
    """
    从 backtest_group_results 的 group_v1 批次读取候选股清单。
    去重（同一trade_date+code只回测一次）。
    """
    rows = db.fetchall(
        "SELECT DISTINCT trade_date, code, name, total_score, group_tag, source, entry_price "
        "FROM backtest_group_results WHERE batch_id = 'group_v1' "
        "ORDER BY trade_date, group_tag"
    )
    result = []
    seen = set()
    for r in rows:
        key = (r["trade_date"], r["code"])
        if key not in seen:
            seen.add(key)
            result.append({
                "trade_date": r["trade_date"],
                "code": r["code"],
                "name": r["name"],
                "total_score": r["total_score"],
                "group_tag": r["group_tag"],
                "source": r["source"],
                "entry_price": float(r["entry_price"]) if r["entry_price"] else 0,
            })
    return result


def simulate_with_timing(db, batch_id, buy_mode, sell_mode):
    """
    从 group_v1 候选股做不同买卖时点的回测。

    buy_mode: 'open' 或 'close'（T+1开盘/收盘买入）
    sell_mode: 'open' 或 'close'（T+2开盘/收盘卖出）

    跳过规则与基线相同：停牌跳过、一字板跳过。
    """
    candidates = get_candidates_from_group_v1(db)
    print("📦 Batch ID: %s | 候选股: %d 笔" % (batch_id, len(candidates)))

    # 清除旧数据
    db.execute("DELETE FROM backtest_group_results WHERE batch_id = %s", (batch_id,))

    codes = list(set(c["code"] for c in candidates))
    placeholders = ",".join(["%s"] * len(codes))
    rows = db.fetchall(
        "SELECT code, trade_date, open, close, volume, change_pct FROM stock_daily "
        "WHERE code IN (%s) AND trade_date != %%s ORDER BY code, trade_date" % placeholders,
        codes + [HOLIDAY_20260619],
    )

    # 按 code 分组构建 日期 -> 数据 映射
    code_data = defaultdict(list)
    for r in rows:
        code_data[r["code"]].append({
            "trade_date": r["trade_date"],
            "open": float(r["open"]) if r["open"] else 0,
            "close": float(r["close"]) if r["close"] else 0,
            "volume": float(r["volume"]) if r["volume"] else 0,
            "change_pct": float(r["change_pct"]) if r["change_pct"] else 0,
        })

    # 预计算前20日均量（每个 code 的所有交易日）
    code_avg_vol = {}
    for code, daily_list in code_data.items():
        vols = []
        code_avg_vol[code] = {}
        for d in daily_list:
            if d["volume"] > 0:
                vols.append(d["volume"])
            if len(vols) > 20:
                vols.pop(0)
            code_avg_vol[code][d["trade_date"]] = sum(vols) / len(vols) if vols else 0

    all_records = []
    group_summary = defaultdict(list)

    for cand in candidates:
        trade_date = cand["trade_date"]
        code = cand["code"]
        entry_price = cand["entry_price"]

        daily_list = code_data.get(code, [])

        idx = None
        for i, d in enumerate(daily_list):
            if d["trade_date"] == trade_date:
                idx = i
                break

        record = {
            "trade_date": trade_date,
            "code": code,
            "name": cand["name"],
            "total_score": cand["total_score"],
            "group_tag": cand["group_tag"],
            "source": cand["source"],
            "entry_price": entry_price,
            "buy_date": None,
            "buy_price": 0,
            "sell_date": None,
            "sell_price": 0,
            "profit_rate": None,
            "stop_reason": None,
            "sh_change": get_sh_change(db, trade_date),
        }

        if idx is None:
            record["stop_reason"] = "T日无数据"
            all_records.append(record)
            group_summary[cand["group_tag"]].append(record)
            continue

        # T+1 (buy_date)
        if idx + 1 >= len(daily_list):
            record["stop_reason"] = "无T+1数据"
            all_records.append(record)
            group_summary[cand["group_tag"]].append(record)
            continue
        d1 = daily_list[idx + 1]

        # T+2 (sell_date)
        if idx + 2 >= len(daily_list):
            record["buy_date"] = d1["trade_date"]
            record["buy_price"] = round(d1["open"], 2) if buy_mode == "open" else round(d1["close"], 2)
            record["stop_reason"] = "无T+2数据"
            all_records.append(record)
            group_summary[cand["group_tag"]].append(record)
            continue
        d2 = daily_list[idx + 2]

        # T+1 买入
        if d1["volume"] == 0:
            record["buy_date"] = d1["trade_date"]
            record["stop_reason"] = "T+1停牌(量0)"
            all_records.append(record)
            group_summary[cand["group_tag"]].append(record)
            continue

        if buy_mode == "open":
            buy_price = d1["open"]
        else:
            buy_price = d1["close"]

        # 一字板检查：买入日能买到吗？
        d1_change_val = d1["change_pct"]
        if d1_change_val >= 9.5:
            avg_vol_20 = code_avg_vol.get(code, {}).get(d1["trade_date"], 0)
            if avg_vol_20 > 0 and d1["volume"] < avg_vol_20 * 0.3:
                record["buy_date"] = d1["trade_date"]
                record["stop_reason"] = "一字板(涨%.2f%% 量低)" % d1_change_val
                all_records.append(record)
                group_summary[cand["group_tag"]].append(record)
                continue

        # T+2 卖出
        if d2["volume"] == 0:
            record["buy_date"] = d1["trade_date"]
            record["buy_price"] = round(buy_price, 2)
            record["sell_date"] = d2["trade_date"]
            record["stop_reason"] = "T+2停牌(量0)"
            all_records.append(record)
            group_summary[cand["group_tag"]].append(record)
            continue

        if sell_mode == "open":
            sell_price = d2["open"]
        else:
            sell_price = d2["close"]

        if buy_price > 0 and sell_price > 0:
            profit_rate = (sell_price - buy_price) / buy_price
        else:
            profit_rate = None

        record["buy_date"] = d1["trade_date"]
        record["buy_price"] = round(buy_price, 2)
        record["sell_date"] = d2["trade_date"]
        record["sell_price"] = round(sell_price, 2)
        record["profit_rate"] = round(profit_rate, 4) if profit_rate is not None else None
        all_records.append(record)
        group_summary[cand["group_tag"]].append(record)

    # 批量写入数据库
    insert_sql = (
        "INSERT INTO backtest_group_results "
        "(batch_id, trade_date, code, name, total_score, group_tag, source, "
        " entry_price, buy_date, buy_price, sell_date, sell_price, "
        " profit_rate, stop_reason, sh_change) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
    )

    for rec in all_records:
        db.execute(
            insert_sql,
            (
                batch_id,
                rec["trade_date"],
                rec["code"],
                rec["name"],
                rec["total_score"],
                rec["group_tag"],
                rec["source"],
                rec["entry_price"],
                rec["buy_date"],
                rec["buy_price"],
                rec["sell_date"],
                rec["sell_price"],
                rec["profit_rate"],
                rec["stop_reason"],
                rec["sh_change"],
            ),
        )

    return all_records, group_summary


def run_extended_backtests():
    """依次运行3组扩展回测。"""
    db = get_db()

    timing_configs = [
        ("open_close", "open", "close"),
        ("close_open", "close", "open"),
        ("close_close", "close", "close"),
    ]

    for batch_id, buy_mode, sell_mode in timing_configs:
        print()
        print("━" * 80)
        print("🚀 扩展回测: batch_id=%s  (T+1%s → T+2%s)" % (
            batch_id,
            "开盘买入" if buy_mode == "open" else "收盘买入",
            "开盘卖出" if sell_mode == "open" else "收盘卖出",
        ))
        print("━" * 80)

        all_records, group_summary = simulate_with_timing(db, batch_id, buy_mode, sell_mode)
        total = sum(len(v) for v in group_summary.values())
        print("✅ batch_id=%s 完成: %d 条记录" % (batch_id, total))

    print()
    print_extended_report(db)


def print_extended_report(db):
    """
    横向对比4种买卖时点的4个组别（共16组数据）
    格式为矩阵表格。
    """
    batch_ids = ["open_open", "open_close", "close_open", "close_close"]
    mode_labels = {
        "open_open": "open→open",
        "open_close": "open→close",
        "close_open": "close→open",
        "close_close": "close→close",
    }
    source_batch = {
        "open_open": "group_v1",
        "open_close": "open_close",
        "close_open": "close_open",
        "close_close": "close_close",
    }

    def _fmt_pct(val):
        if val is None:
            return "-"
        sign = "+" if val >= 0 else ""
        return "%s%.2f%%" % (sign, float(val) * 100)

    print()
    print("=" * 110)
    print("📊 扩展回测对比矩阵 (16组)")
    print("=" * 110)
    print("%-16s %-6s %-8s %-8s %-12s %-12s %s" % (
        "时点", "组别", "笔数", "胜率", "总收益", "均值/笔", "skip"
    ))
    print("-" * 110)

    for batch_id in batch_ids:
        actual_batch = source_batch[batch_id]
        rows = db.fetchall(
            "SELECT * FROM backtest_group_results WHERE batch_id = %s ORDER BY group_tag, trade_date",
            (actual_batch,),
        )

        if not rows:
            continue

        group_data = defaultdict(list)
        for r in rows:
            group_data[r["group_tag"]].append(r)

        for group_tag in ["A", "B", "C", "D"]:
            records = group_data.get(group_tag, [])
            traded = [r for r in records if r["buy_date"] and r["sell_date"] and r["profit_rate"] is not None]
            skipped = [r for r in records if not (r["buy_date"] and r["sell_date"] and r["profit_rate"] is not None)]

            tc = len(traded)
            if tc == 0:
                print("%-16s %-6s %-8s %-8s %-12s %-12s %s" % (
                    mode_labels[batch_id], group_tag, "0", "-", "-", "-", len(skipped),
                ))
                continue

            win = [r for r in traded if r["profit_rate"] and r["profit_rate"] > 0]
            wc = len(win)
            win_rate = wc / tc * 100
            total_profit = sum(r["profit_rate"] for r in traded if r["profit_rate"] is not None)
            avg_profit = total_profit / tc

            print("%-16s %-6s %-8d %-7.0f%% %-12s %-12s %s" % (
                mode_labels[batch_id],
                group_tag,
                tc,
                win_rate,
                _fmt_pct(total_profit),
                _fmt_pct(avg_profit),
                len(skipped),
            ))

    print("=" * 110)



def get_sh_change(db, date_str):
    """获取当日上证涨跌幅(%)"""
    r = db.fetchone(
        "SELECT change_pct FROM index_quotes "
        "WHERE index_code = 'szzs' AND record_date = %s",
        (date_str,),
    )
    if r and r["change_pct"] is not None:
        return r["change_pct"]
    return 0


# ─────────────────────────────────────────────
# 核心函数
# ─────────────────────────────────────────────
def get_group_picks(db, trade_date, group_tag, top_k=None):
    """
    获取某日某分组的候选股列表

    分组规则:
      A: total_score < 60
      B: 60 <= total_score < 65
      C: 65 <= total_score < 70
      D: total_score >= 70

    Args:
        trade_date: 选股日期 YYYYMMDD
        group_tag: A/B/C/D
        top_k: 若指定，取评分前 top_k 只（同分按 score desc, source asc）

    Returns:
        [dict(code, name, total_score, source), ...]
    """
    # 分组分数区间
    score_ranges = {
        "A": (None, 60),
        "B": (60, 65),
        "C": (65, 70),
        "D": (70, None),
    }
    lo, hi = score_ranges[group_tag]

    clauses = ["trade_date = %s", "total_score IS NOT NULL"]
    params = [trade_date]

    if lo is not None:
        clauses.append("total_score >= %s")
        params.append(lo)
    if hi is not None:
        clauses.append("total_score < %s")
        params.append(hi)

    where = " AND ".join(clauses)
    sql = (
        f"SELECT code, name, total_score, source FROM daily_picks "
        f"WHERE {where} ORDER BY total_score DESC, source ASC"
    )

    rows = db.fetchall(sql, tuple(params))

    # 去重（同一日同一code只能出现一次，取max score）
    seen = {}
    for r in rows:
        c = r["code"]
        if c not in seen or r["total_score"] > seen[c]["total_score"]:
            seen[c] = r

    candidates = list(seen.values())

    # 按 score desc, source asc 排序
    candidates.sort(key=lambda x: (-x["total_score"], x.get("source", "")))

    if top_k is not None and len(candidates) > top_k:
        candidates = candidates[:top_k]

    return candidates


def simulate_group_trade(db, trade_date, candidates, group_tag):
    """
    模拟某日某组所有候选的交易

    交易规则:
      - T日选股 -> T+1开盘买入 -> T+2开盘卖出
      - 跳过: 成交量=0(停牌)
      - 跳过: T+1日涨跌幅>=9.5%且成交量<前20日均量*0.3(一字板)

    Returns:
        [dict(...交易记录...), ...]
    """
    results = []

    for c in candidates:
        code = c["code"]
        name = c["name"]
        total_score = c["total_score"]
        source = c.get("source", "")

        # T日收盘价
        t_day = db.fetchone(
            "SELECT close FROM stock_daily WHERE code = %s AND trade_date = %s",
            (code, trade_date),
        )
        entry_price = float(t_day["close"]) if t_day and t_day["close"] else 0

        # 获取 T+1, T+2 交易日数据（跳过假期 20260619）
        next_dates = get_next_trade_dates(db, code, trade_date, limit=3)

        record = {
            "trade_date": trade_date,
            "code": code,
            "name": name,
            "total_score": total_score,
            "group_tag": group_tag,
            "source": source,
            "entry_price": entry_price,
            "buy_date": None,
            "buy_price": 0,
            "sell_date": None,
            "sell_price": 0,
            "profit_rate": None,
            "stop_reason": None,
            "sh_change": get_sh_change(db, trade_date),
        }

        if len(next_dates) < 2:
            # 无足够后续交易日数据
            record["stop_reason"] = "无后续T+1/T+2数据"
            results.append(record)
            continue

        d1_date, d1_open, d1_close, d1_volume, d1_change = next_dates[0]
        d2_date, d2_open, d2_close, d2_volume, d2_change = next_dates[1]

        ##############################################
        # T+1 开盘买入
        ##############################################
        # 跳过条件1: 成交量=0（停牌）
        if d1_volume is None or d1_volume == 0:
            record["buy_date"] = d1_date
            record["stop_reason"] = "T+1停牌(量0)"
            results.append(record)
            continue

        buy_price = float(d1_open) if d1_open else 0

        # 跳过条件2: T+1 一字板（涨跌幅>=9.5% 且 成交量<前20日均量*0.3）
        d1_change_val = float(d1_change) if d1_change else 0
        if d1_change_val >= 9.5:
            avg_vol_20 = get_avg_volume_20(db, code, d1_date)
            d1_vol_val = float(d1_volume) if d1_volume else 0
            if avg_vol_20 > 0 and d1_vol_val < avg_vol_20 * 0.3:
                record["buy_date"] = d1_date
                record["stop_reason"] = "一字板(涨%.2f%% 量低)" % float(d1_change)
                results.append(record)
                continue

        ##############################################
        # T+2 开盘卖出
        ##############################################
        # 跳过条件: T+2 成交量=0（停牌）
        if d2_volume is None or d2_volume == 0:
            record["buy_date"] = d1_date
            record["buy_price"] = buy_price
            record["sell_date"] = d2_date
            record["stop_reason"] = "T+2停牌(量0)"
            results.append(record)
            continue

        sell_price = float(d2_open) if d2_open else 0

        # 计算收益率
        if buy_price > 0 and sell_price > 0:
            profit_rate = (sell_price - buy_price) / buy_price
        else:
            profit_rate = None

        record["buy_date"] = d1_date
        record["buy_price"] = round(buy_price, 2)
        record["sell_date"] = d2_date
        record["sell_price"] = round(sell_price, 2)
        record["profit_rate"] = round(profit_rate, 4) if profit_rate is not None else None
        results.append(record)

    return results


def run_group_backtest(start_date=None, end_date=None, batch_id=None):
    """分组回测主循环"""
    if start_date is None:
        start_date = START_DATE
    if end_date is None:
        end_date = END_DATE
    if batch_id is None:
        batch_id = BATCH_ID

    db = get_db()
    ensure_table(db)

    # 清除旧数据
    db.execute("DELETE FROM backtest_group_results WHERE batch_id = %s", (batch_id,))

    # 获取交易日列表（排除假期）
    trade_dates = get_trade_dates(db, start_date, end_date)
    print("📅 回测区间: %s ~ %s" % (start_date, end_date))
    print("📦 Batch ID: %s" % batch_id)
    print("📅 总交易日数: %d" % len(trade_dates))
    print()

    all_records = []
    group_summary = defaultdict(list)

    # 逐日处理每组
    for td in trade_dates:
        for group_tag, group_label, score_func, top_k in GROUP_DEFS:
            candidates = get_group_picks(db, td, group_tag, top_k=top_k)
            if not candidates:
                continue

            records = simulate_group_trade(db, td, candidates, group_tag)
            for rec in records:
                # 写入数据库
                db.execute(
                    "INSERT INTO backtest_group_results "
                    "(batch_id, trade_date, code, name, total_score, group_tag, source, "
                    " entry_price, buy_date, buy_price, sell_date, sell_price, "
                    " profit_rate, stop_reason, sh_change) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        batch_id,
                        rec["trade_date"],
                        rec["code"],
                        rec["name"],
                        rec["total_score"],
                        rec["group_tag"],
                        rec["source"],
                        rec["entry_price"],
                        rec["buy_date"],
                        rec["buy_price"],
                        rec["sell_date"],
                        rec["sell_price"],
                        rec["profit_rate"],
                        rec["stop_reason"],
                        rec["sh_change"],
                    ),
                )

            all_records.extend(records)
            group_summary[group_tag].extend(records)

    return all_records, group_summary


# ─────────────────────────────────────────────
# 报告输出
# ─────────────────────────────────────────────
def fmt_pct(val, signed=True):
    """格式化百分比"""
    if val is None:
        return "N/A"
    sign = "+" if signed and val >= 0 else ""
    return "%s%.2f%%" % (sign, float(val) * 100)


def fmt_price(val):
    if val is None or val == 0:
        return "N/A"
    return "%.2f" % float(val)


def print_group_report(db, batch_id=None):
    """输出分组报告"""
    if batch_id is None:
        batch_id = BATCH_ID

    print()
    print("=" * 100)
    print("📊 分组回测报告")
    print("=" * 100)

    group_labels = {
        "A": "A组 (<60分, TOP5)",
        "B": "B组 (60~65分)",
        "C": "C组 (65~70分)",
        "D": "D组 (>=70分)",
    }

    # 从数据库读取该 batch 的所有记录
    rows = db.fetchall(
        "SELECT * FROM backtest_group_results WHERE batch_id = %s ORDER BY group_tag, trade_date",
        (batch_id,),
    )

    group_data = defaultdict(list)
    for r in rows:
        group_data[r["group_tag"]].append(r)

    comparison = []

    for group_tag in ["A", "B", "C", "D"]:
        records = group_data.get(group_tag, [])
        label = group_labels.get(group_tag, "%s组" % group_tag)

        print()
        print("=" * 80)
        print("=== %s 共%d笔交易" % (label, len(records)))
        print("%-10s %-8s %-10s %-5s %-10s %-10s %-10s %-10s %s" % (
            "日期", "代码", "名称", "评分", "买入日", "买入价", "卖出日", "卖出价", "收益率"
        ))
        print("-" * 80)

        traded = []  # 成功交易的（有买入卖出）
        skipped = []  # 跳过的

        for r in records:
            if r["buy_date"] and r["sell_date"] and r["profit_rate"] is not None:
                traded.append(r)
            else:
                skipped.append(r)

            profit_str = fmt_pct(r["profit_rate"])
            if r["stop_reason"]:
                profit_str = r["stop_reason"]

            print("%-10s %-8s %-10s %-5s %-10s %-10s %-10s %-10s %s" % (
                r["trade_date"],
                r["code"],
                r["name"],
                r["total_score"],
                r["buy_date"] or "N/A",
                fmt_price(r["buy_price"]),
                r["sell_date"] or "N/A",
                fmt_price(r["sell_price"]),
                profit_str,
            ))

        # 统计
        tc = len(traded)
        win = [r for r in traded if r["profit_rate"] and r["profit_rate"] > 0]
        loss = [r for r in traded if r["profit_rate"] is not None and r["profit_rate"] <= 0]
        wc = len(win)
        lc = len(loss)

        # 跳过统计
        skip_one_txt = 0  # 一字板
        skip_pause = 0  # 停牌
        skip_nodata = 0  # 无数据

        for r in skipped:
            reason = r.get("stop_reason") or ""
            if "一字板" in reason:
                skip_one_txt += 1
            elif "停牌" in reason:
                skip_pause += 1
            elif "无数据" in reason:
                skip_nodata += 1

        total_profit = sum(r["profit_rate"] for r in traded if r["profit_rate"] is not None) if tc else 0.0
        avg_profit = total_profit / tc if tc else 0.0
        win_rate = wc / tc * 100 if tc else 0.0

        max_win = max(traded, key=lambda r: r["profit_rate"] or -999) if win else None
        max_loss = min(traded, key=lambda r: r["profit_rate"] or 999) if loss else None

        print("-" * 80)
        print("总计: %d笔 | 盈利%d | 亏损%d | 胜率%.0f%%" % (tc, wc, lc, win_rate))
        print("总收益: %s | 均值: %s/笔" % (fmt_pct(total_profit), fmt_pct(avg_profit)))

        if max_win:
            print("最大盈利: %s(%s) %s" % (max_win["name"], max_win["code"], fmt_pct(max_win["profit_rate"])))
        if max_loss:
            print("最大亏损: %s(%s) %s" % (max_loss["name"], max_loss["code"], fmt_pct(max_loss["profit_rate"])))

        skip_parts = []
        if skip_one_txt:
            skip_parts.append("一字板%d" % skip_one_txt)
        if skip_pause:
            skip_parts.append("停牌%d" % skip_pause)
        if skip_nodata:
            skip_parts.append("无数据%d" % skip_nodata)
        if skip_parts:
            print("跳过: %d笔 (%s)" % (len(skipped), ", ".join(skip_parts)))

        comparison.append({
            "group": group_tag,
            "total": tc,
            "win_rate": win_rate,
            "total_profit": total_profit,
            "avg_profit": avg_profit,
            "max_win_name": "%s(%s)" % (max_win["name"], max_win["code"]) if max_win else "-",
            "max_win_val": max_win["profit_rate"] if max_win else 0,
            "max_loss_name": "%s(%s)" % (max_loss["name"], max_loss["code"]) if max_loss else "-",
            "max_loss_val": max_loss["profit_rate"] if max_loss else 0,
        })

    # -- 对比矩阵 --
    print()
    print("=" * 100)
    print("📊 四组对比矩阵")
    print("=" * 100)
    print("%-6s %-8s %-10s %-12s %-12s %-30s %s" % (
        "组别", "笔数", "胜率", "总收益", "均值", "最大盈利", "最大亏损"
    ))
    print("-" * 100)
    for c in comparison:
        max_win_str = "%s %s" % (c["max_win_name"], fmt_pct(c["max_win_val"]))
        max_loss_str = "%s %s" % (c["max_loss_name"], fmt_pct(c["max_loss_val"]))
        print("%-6s %-8d %-8.0f%% %-12s %-12s %-30s %s" % (
            c["group"],
            c["total"],
            c["win_rate"],
            fmt_pct(c["total_profit"]),
            fmt_pct(c["avg_profit"]),
            max_win_str,
            max_loss_str,
        ))
    print("=" * 100)


# ─────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────
def main():
    print("🚀 分组回测启动...")
    print("   区间: %s ~ %s" % (START_DATE, END_DATE))
    print()

    # 运行回测
    all_records, group_summary = run_group_backtest(
        start_date=START_DATE,
        end_date=END_DATE,
        batch_id=BATCH_ID,
    )

    total_trades = sum(len(v) for v in group_summary.values())
    print()
    print("✅ 回测完成! 共处理 %d 条记录" % total_trades)

    # 输出报告
    db = get_db()
    print_group_report(db, batch_id=BATCH_ID)

    print()
    print("✅ 数据已写入 backtest_group_results 表 (batch_id=%s)" % BATCH_ID)


if __name__ == "__main__":
    if "--extended" in sys.argv:
        run_extended_backtests()
    else:
        main()
