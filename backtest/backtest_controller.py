"""
backtest_controller - 回测主控制器

对回测区间内的每个交易日：
  1. 用 TimeMachine 模拟历史日期
  2. 调用 pick_stocks_v2() 重跑当日选股
  3. 取最高分候选进行交易模拟
  4. 将结果写入 backtest_picks 表

交易规则：
  T日评分 -> T+1开盘买入 -> T+2开盘卖出
  停牌/一字板自动跳过

回测区间: 2026-05-13 ~ 2026-07-23（跳过 2026-06-19）
batch_id: bt_baseline
"""
import sys
import os
import time
import logging

# 设置项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from utils.dao import get_db

# ── 回测参数 ──
START_DATE = "20260513"
END_DATE = "20260723"
SKIP_DATES = {"20260619"}  # 假期，只有3200条stock_daily数据
BATCH_ID = "bt_baseline"

# ── LOG 配置 ──
LOG_FILE = os.path.join(PROJECT_ROOT, "logs", "backtest_baseline.log")
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger("backtest")


# ═════════════════════════════════════════════════════════════════
# 辅助函数
# ═════════════════════════════════════════════════════════════════

def get_all_trade_dates(start: str, end: str) -> list:
    """获取[start, end]区间所有交易日（按stock_daily表实际数据）"""
    db = get_db()
    rows = db.fetchall(
        "SELECT DISTINCT trade_date FROM stock_daily "
        "WHERE trade_date >= %s AND trade_date <= %s "
        "ORDER BY trade_date",
        (start, end)
    )
    db.close()
    return [r["trade_date"] for r in rows]


def get_next_trade_dates(db, code: str, after_date: str, count: int = 2) -> list:
    """获取指定股票在after_date之后的N个交易日"""
    return db.fetchall(
        "SELECT trade_date, open, volume, change_pct FROM stock_daily "
        "WHERE code = %s AND trade_date > %s "
        "ORDER BY trade_date LIMIT %s",
        (code, after_date, count)
    )


def get_stock_daily(db, code: str, trade_date: str) -> dict:
    """获取某股票某日K线数据"""
    return db.fetchone(
        "SELECT * FROM stock_daily WHERE code = %s AND trade_date = %s",
        (code, trade_date)
    )


def get_market_sh_change(db, trade_date: str) -> float:
    """获取当日大盘涨跌幅（上证指数）"""
    dash_date = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"
    row = db.fetchone(
        "SELECT change_pct FROM index_quotes "
        "WHERE index_code = 'szzs' AND record_date = %s",
        (dash_date,)
    )
    return float(row["change_pct"]) if row and row.get("change_pct") else 0.0


def get_avg_volume(db, code: str, trade_date: str, lookback: int = 20) -> float:
    """获取某股票前N日均量"""
    row = db.fetchone(
        "SELECT AVG(volume) as avg_vol FROM ("
        "  SELECT volume FROM stock_daily "
        "  WHERE code = %s AND trade_date < %s "
        "  ORDER BY trade_date DESC LIMIT %s"
        ") t",
        (code, trade_date, lookback)
    )
    return float(row["avg_vol"]) if row and row.get("avg_vol") else 0


# ═════════════════════════════════════════════════════════════════
# 交易模拟核心
# ═════════════════════════════════════════════════════════════════

def simulate_trade(db, trade_date: str, candidates: list, consecutive_loss_count: int) -> dict:
    """
    模拟T日选股后的交易
    
    Args:
        db: 数据库连接
        trade_date: T日 (YYYYMMDD)
        candidates: pick_stocks_v2() 返回的 scored 列表
        consecutive_loss_count: 当前连续亏损次数
    
    Returns:
        dict: 交易记录字段
    """
    # 默认结果结构
    result = {
        "trade_date": trade_date,
        "code": None,
        "name": None,
        "total_score": None,
        "source": None,
        "entry_price": None,
        "buy_date": None,
        "buy_price": None,
        "sell_date": None,
        "sell_price": None,
        "profit_rate": None,
        "stop_reason": None,
        "sh_change": get_market_sh_change(db, trade_date),
        "consecutive_loss": consecutive_loss_count,
    }

    if not candidates:
        result["stop_reason"] = "当日无候选股"
        return result

    # 取 total_score 最高的候选
    top = max(candidates, key=lambda x: x.get("total_score", 0))
    code = top.get("code", "")
    name = top.get("name", "")
    total_score = top.get("total_score", 0)
    source = top.get("source", "")

    result["code"] = code
    result["name"] = name
    result["total_score"] = total_score
    result["source"] = source

    # 获取T日收盘价（作为entry_price参考）
    t_data = get_stock_daily(db, code, trade_date)
    if t_data:
        result["entry_price"] = float(t_data.get("close", 0))

    # 检查T日该股是否停牌（成交量=0）
    if t_data and float(t_data.get("volume", 0)) == 0:
        result["stop_reason"] = f"{code} {name} - T日停牌"
        return result

    # 获取T+1和T+2交易日
    next_dates = get_next_trade_dates(db, code, trade_date, 2)

    if len(next_dates) == 0:
        result["stop_reason"] = f"{code} {name} - 无后续交易日数据"
        return result

    # ── T+1 买入判断 ──
    d1 = next_dates[0]
    buy_date = d1["trade_date"]
    buy_open = float(d1.get("open", 0))
    buy_volume = float(d1.get("volume", 0))
    buy_change_pct = float(d1.get("change_pct", 0))

    # 停牌检查
    if buy_volume == 0:
        result["stop_reason"] = f"{code} {name} - T+1({buy_date})停牌"
        return result

    # 一字板检查（开盘涨停且成交量<30%均值）
    if buy_change_pct >= 9.5:
        avg_vol = get_avg_volume(db, code, buy_date, 20)
        if avg_vol > 0 and buy_volume < avg_vol * 0.3:
            result["stop_reason"] = f"{code} {name} - T+1({buy_date})一字板无法买入"
            return result

    result["buy_date"] = buy_date
    result["buy_price"] = buy_open

    # ── T+2 卖出 ──
    if len(next_dates) < 2:
        result["stop_reason"] = f"{code} {name} - T+1持有中，无T+2数据"
        return result

    sell_date = next_dates[1]["trade_date"]
    sell_open = float(next_dates[1].get("open", 0))
    sell_volume = float(next_dates[1].get("volume", 0))

    # 如果T+2停牌，顺延至下一个交易日
    while sell_volume == 0:
        extra = get_next_trade_dates(db, code, sell_date, 1)
        if not extra:
            result["stop_reason"] = f"{code} {name} - T+2({sell_date})及后续停牌"
            return result
        sell_date = extra[0]["trade_date"]
        sell_open = float(extra[0].get("open", 0))
        sell_volume = float(extra[0].get("volume", 0))
        logger.info(f"  -> {code} T+2停牌，顺延至 {sell_date}")

    result["sell_date"] = sell_date
    result["sell_price"] = sell_open

    # 计算收益率 (小数，如 0.0523 = 5.23%)
    if buy_open > 0:
        result["profit_rate"] = round((sell_open - buy_open) / buy_open, 4)

    return result


# ═════════════════════════════════════════════════════════════════
# 回测主循环
# ═════════════════════════════════════════════════════════════════

def run_backtest(start_date: str = START_DATE, end_date: str = END_DATE,
                 batch_id: str = BATCH_ID):
    """
    运行回测主流程
    
    Args:
        start_date: 起始日期 (YYYYMMDD)
        end_date: 结束日期 (YYYYMMDD)
        batch_id: 回测批次标识
    """
    from backtest.time_machine import TimeMachine

    t_start = time.time()
    db = get_db()

    # 获取交易日列表
    all_dates = get_all_trade_dates(start_date, end_date)
    logger.info(f"Backtest range: {start_date} ~ {end_date}")
    logger.info(f"Total trade days: {len(all_dates)}")
    logger.info(f"Skip dates: {', '.join(sorted(SKIP_DATES))}")
    logger.info(f"Batch ID: {batch_id}")
    logger.info("")

    # 清除该batch的旧数据
    db.execute("DELETE FROM backtest_picks WHERE batch_id = %s", (batch_id,))
    logger.info(f"Cleared old data for batch_id={batch_id}")

    results = []
    consecutive_loss_count = 0

    for idx, trade_date in enumerate(all_dates):
        # 跳过指定日期
        if trade_date in SKIP_DATES:
            logger.info(f"  [{idx+1}/{len(all_dates)}] {trade_date} SKIP (holiday)")
            continue

        logger.info(f"  [{idx+1}/{len(all_dates)}] {trade_date} picking...")

        try:
            # 使用 TimeMachine 模拟历史日期，运行选股引擎
            with TimeMachine(trade_date):
                from core.analyzer.daily_pick_v2 import pick_stocks_v2
                picks_result = pick_stocks_v2()

            # 获取评分后的候选列表
            scored = picks_result.get("scored", [])

            # 模拟交易
            trade = simulate_trade(db, trade_date, scored, consecutive_loss_count)

            # 更新连续亏损计数
            if trade["profit_rate"] is not None and trade["profit_rate"] < 0:
                consecutive_loss_count += 1
            elif trade["profit_rate"] is not None and trade["profit_rate"] > 0:
                consecutive_loss_count = 0
            # profit_rate=0 或 stop_reason 不为空：不改变计数

            results.append(trade)

            # 写入数据库
            trade["batch_id"] = batch_id
            db.insert_or_ignore("backtest_picks", {
                "batch_id": batch_id,
                "trade_date": trade["trade_date"],
                "code": trade["code"],
                "name": trade["name"],
                "total_score": trade["total_score"],
                "source": trade["source"],
                "strategy_group": "baseline",
                "entry_price": trade["entry_price"],
                "buy_date": trade["buy_date"],
                "buy_price": trade["buy_price"],
                "sell_date": trade["sell_date"],
                "sell_price": trade["sell_price"],
                "profit_rate": trade["profit_rate"],
                "stop_reason": trade["stop_reason"],
                "sh_change": trade["sh_change"],
                "consecutive_loss": trade["consecutive_loss"],
            })

            # 输出该日日志
            _log_daily_result(trade)

        except Exception as e:
            logger.error(f"  FAIL [{idx+1}/{len(all_dates)}] {trade_date}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            continue

    db.close()

    total_elapsed = time.time() - t_start
    logger.info("")
    logger.info("=" * 80)
    logger.info(f"Backtest complete! Elapsed: {total_elapsed:.1f}s")
    logger.info("")

    # 输出汇总统计
    _print_summary(results)


def _log_daily_result(trade: dict):
    """输出单个交易日的日志"""
    td = trade["trade_date"]
    code = trade.get("code", "N/A")
    name = trade.get("name", "N/A")
    score = trade.get("total_score")

    if trade["stop_reason"]:
        logger.info(f"  -> {td} {code} {name} {score}pt | SKIP: {trade['stop_reason']}")
    else:
        bd = trade.get("buy_date", "")
        bp = trade.get("buy_price")
        sd = trade.get("sell_date", "")
        sp = trade.get("sell_price")
        pr = trade.get("profit_rate")

        bp_str = f"{bp:.2f}" if bp is not None else "N/A"
        sp_str = f"{sp:.2f}" if sp is not None else "N/A"

        if pr is not None:
            pr_pct = f"{'+' if pr >= 0 else ''}{pr * 100:.2f}%"
            emoji = "WIN" if pr > 0 else "LOSS"
            logger.info(f"  -> {td} {code} {name} {score}pt | Buy{bd}@{bp_str} -> Sell{sd}@{sp_str} = {pr_pct} {emoji}")
        else:
            logger.info(f"  -> {td} {code} {name} {score}pt | Buy{bd}@{bp_str} -> Pending")


def _print_summary(results: list):
    """输出回测汇总统计"""
    traded = [r for r in results if r.get("buy_date") and r.get("sell_date")]
    win = [r for r in traded if r.get("profit_rate") is not None and r["profit_rate"] > 0]
    loss = [r for r in traded if r.get("profit_rate") is not None and r["profit_rate"] <= 0]

    tc = len(traded)
    wc = len(win)

    # 总收益率
    total_profit = sum(r["profit_rate"] for r in traded if r["profit_rate"] is not None)
    avg_profit = total_profit / tc if tc > 0 else 0.0
    win_rate = wc / tc * 100 if tc > 0 else 0.0

    # 最大盈利/亏损
    max_win = max(traded, key=lambda r: r["profit_rate"] or -999) if win else None
    max_loss = min(traded, key=lambda r: r["profit_rate"] or 999) if loss else None

    # 按评分分组
    low = [r for r in traded if r.get("total_score") is not None and r["total_score"] < 60]
    mid = [r for r in traded if r.get("total_score") is not None and 60 <= r["total_score"] < 70]
    high = [r for r in traded if r.get("total_score") is not None and r["total_score"] >= 70]

    # 最大回撤计算
    max_drawdown = 0.0
    if traded:
        sorted_trades = sorted(traded, key=lambda r: r.get("trade_date", ""))
        cumulative = 0.0
        peak = 0.0
        for r in sorted_trades:
            if r.get("profit_rate") is not None:
                cumulative += r["profit_rate"]
                peak = max(peak, cumulative)
                max_drawdown = min(max_drawdown, cumulative - peak)

    # 连续亏损统计
    max_consecutive_loss = 0
    cur_loss = 0
    for r in traded:
        if r.get("profit_rate") is not None and r["profit_rate"] < 0:
            cur_loss += 1
            max_consecutive_loss = max(max_consecutive_loss, cur_loss)
        else:
            cur_loss = 0

    # 格式辅助
    def fmtp(v):
        if v is None:
            return "N/A"
        return f"{'+' if v >= 0 else ''}{v * 100:.2f}%"

    def fmt_avg(lst):
        if not lst:
            return "N/A"
        vals = [r["profit_rate"] for r in lst if r["profit_rate"] is not None]
        if not vals:
            return "N/A"
        return fmtp(sum(vals) / len(vals))

    def fmt_win_rate(lst):
        if not lst:
            return "N/A"
        wins = sum(1 for r in lst if r.get("profit_rate") is not None and r["profit_rate"] > 0)
        return f"{wins / len(lst) * 100:.0f}%"

    lines = []
    lines.append("=" * 80)
    lines.append(f"Backtest Summary (batch={BATCH_ID}, {START_DATE}~{END_DATE})")
    lines.append("-" * 80)
    lines.append(f"Total pick days:  {len(results)}")
    lines.append(f"Traded:           {tc} ({len(results) - tc} skipped)")
    lines.append(f"Wins:             {wc} ({win_rate:.0f}%)")
    lines.append(f"Losses:           {len(loss)} ({100 - win_rate:.0f}%)")
    lines.append(f"Total return:     {fmtp(total_profit)}")
    lines.append(f"Avg return:       {fmtp(avg_profit)}/trade")

    if max_win:
        lines.append(f"Max win:          {max_win['name']}({max_win['code']}) {fmtp(max_win['profit_rate'])}")
    if max_loss:
        lines.append(f"Max loss:         {max_loss['name']}({max_loss['code']}) {fmtp(max_loss['profit_rate'])}")

    lines.append("")
    lines.append("By score group:")

    if low:
        lines.append(f"  <60:             {len(low)} trades  WR{fmt_win_rate(low)}  Avg{fmt_avg(low)}")
    if mid:
        lines.append(f"  60~70:           {len(mid)} trades  WR{fmt_win_rate(mid)}  Avg{fmt_avg(mid)}")
    if high:
        lines.append(f"  >=70:            {len(high)} trades  WR{fmt_win_rate(high)}  Avg{fmt_avg(high)}")

    lines.append("")
    lines.append("Drawdown analysis:")
    lines.append(f"  Max drawdown:     {fmtp(max_drawdown)}")
    lines.append(f"  Max consecutive loss: {max_consecutive_loss}")
    lines.append("")
    lines.append("=" * 80)

    for line in lines:
        if line:
            logger.info(line)
    print("\n".join(lines))


# ═════════════════════════════════════════════════════════════════
# 入口
# ═════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    run_backtest()
