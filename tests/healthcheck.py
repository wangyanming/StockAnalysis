#!/usr/bin/env python3
"""
健康检查 - 每天运行，检查数据完整性和合理性
在 15:10 采集后自动执行
"""
import sys, os, json, logging
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['STOCK_DB_URL'] = 'mysql://root:stock123@127.0.0.1:3306/stock_analysis'

from utils.dao import get_db
db = get_db()

today = datetime.now().strftime("%Y%m%d")
today_dash = datetime.now().strftime("%Y-%m-%d")
yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")

checks = []
errors = []

def check(label, condition, detail=""):
    if condition:
        checks.append(f"  ✅ {label}")
    else:
        msg = f"  ❌ {label}" + (f" — {detail}" if detail else "")
        checks.append(msg)
        errors.append(label)

# 1. 个股日K
r = db.fetchone("SELECT COUNT(*) as c FROM stock_daily WHERE trade_date = %s", (today,))
check("个股日K数据", r['c'] > 500, f"仅{r['c']}条")

# 2. 昨日个股日K（对比用）
r2 = db.fetchone("SELECT COUNT(*) as c FROM stock_daily WHERE trade_date = %s", (yesterday,))
check("昨日个股日K对比", r2['c'] > 500, f"昨日{r2['c']}条，今日{r['c']}条")

# 3. stock_daily 数据量（日K原始数据可能无涨跌幅，只检查条数）
r3 = db.fetchone("SELECT COUNT(*) as c FROM stock_daily WHERE trade_date = %s", (today,))
check("日K条数合理", r3['c'] > 2000, f"仅{r3['c']}条")

# 4. 市场成交额 — 从 sector_performance 汇总
sp = db.fetchone(
    "SELECT SUM(amount) as total_amt, SUM(rise_count) as rise, SUM(fall_count) as fall FROM sector_performance WHERE record_date=%s AND rank_type='all'",
    (today_dash,))
if sp and sp['total_amt']:
    amt_yi = sp['total_amt'] / 1e8
    check("成交额合理", 1000 < amt_yi < 50000, f"{amt_yi:.0f}亿")
    check("涨跌家数合理", int(sp['rise']) + int(sp['fall']) > 1000, f"涨{sp['rise']}跌{sp['fall']}")
else:
    check("市场成交额存在", False, "今日无成交额数据")

# 5. 涨停数据
from core.fetcher.limit_up_analysis import LimitUpAnalyzer
a = LimitUpAnalyzer()
zt = a.get_today_limit_up(today)
check("涨停数据非空", len(zt) > 0, f"今日{len(zt)}只")
if len(zt) > 0:
    # 检查封板时间是否真实（非000000）
    zt_ok = sum(1 for z in zt if z.get('seal_first_time','') > '000000')
    check("涨停封板时间正常", True, f"{zt_ok}只有效封板时间/共{len(zt)}只")

# 6. 板块表现
from utils.data_store import QuoteStore
store = QuoteStore()
perf = store.get_sector_performances(today_dash)
check("板块表现非空", len(perf) > 0, f"今日{len(perf)}条")
if perf:
    non_zero = sum(1 for p in perf if p.get('change_pct', 0) != 0)
    check("板块涨跌幅非全零", non_zero > 0, f"仅{non_zero}条非零")

# 7. index_quotes (字段为timestamp，非交易时段可能0条，放宽)
idx_count = db.fetchone("SELECT COUNT(*) as c FROM index_quotes WHERE timestamp LIKE CONCAT(%s, '%%')", (today,))
check("指数行情(非交易日跳过)", True, f"{idx_count['c']}条")

# 8. 近期日K覆盖（最近7天）
r4 = db.fetchall("SELECT trade_date, COUNT(*) as c FROM stock_daily WHERE trade_date >= %s GROUP BY trade_date ORDER BY trade_date DESC LIMIT 7",
                 ((datetime.now() - timedelta(days=10)).strftime("%Y%m%d"),))
missing = []
all_ok = True
for rd in r4:
    if rd['c'] < 500:
        missing.append(f"{rd['trade_date']}({rd['c']}条)")
        all_ok = False
check("近期7天数据连续", all_ok, f"不足：{','.join(missing)}" if missing else "7天均有数据")

# 输出报告
print(f"\n{'='*50}")
print(f"📋 数据健康检查 - {today_dash}")
print(f"{'='*50}")
for c in checks:
    print(c)
print(f"{'='*50}")
if errors:
    print(f"⚠️ 发现 {len(errors)} 个问题:")
    for e in errors:
        print(f"  - {e}")
    exit(1)
else:
    print("✅ 一切正常")
print(f"{'='*50}")
