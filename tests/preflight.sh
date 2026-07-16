#!/bin/bash
# 每次SQL改动后运行：验证语法+数据库连接+核心表
# Usage: bash tests/preflight.sh
set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

echo "================================"
echo " 🛩️  Preflight Check"
echo "================================"

# 1. 语法检查
echo ""
echo "📝 1/4 语法检查..."
PY_FILES=$(find . -name "*.py" ! -name "__pycache__" ! -name "migrate_to_mysql.py" ! -name "dao.py")
ERRORS=0
for f in $PY_FILES; do
    if ! python3 -c "import py_compile; py_compile.compile('$f', doraise=True)" 2>/dev/null; then
        echo "   ❌ $f"
        ERRORS=$((ERRORS+1))
    fi
done
if [ $ERRORS -eq 0 ]; then
    echo "   ✅ 全部通过 ($(echo "$PY_FILES" | wc -l) 个文件)"
fi

# 2. MySQL连接+表结构
echo ""
echo "🔌 2/4 数据库连接..."
if STOCK_DB_URL='mysql://root:stock123@127.0.0.1:3306/stock_analysis' python3 -c "
import sys; sys.path.insert(0, '.')
from utils.dao import get_db
db = get_db()
r = db.fetchone('SELECT COUNT(*) as c FROM stock_daily')
print(f'   ✅ 连接成功, stock_daily: {r[\"c\"]} 条')
" 2>/dev/null; then
    :
else
    echo "   ❌ MySQL连接失败"
    ERRORS=$((ERRORS+1))
fi

# 3. 核心表存在检查
echo ""
echo "🗂️  3/4 核心表检查..."
TABLES="stock_daily sector_performance daily_limit_up limit_up_tracking"
MISSING=0
for tbl in $TABLES; do
    if ! STOCK_DB_URL='mysql://root:stock123@127.0.0.1:3306/stock_analysis' python3 -c "
import sys; sys.path.insert(0, '.')
from utils.dao import get_db
db = get_db()
db.fetchone('SELECT 1 FROM $tbl LIMIT 1')
" 2>/dev/null; then
        echo "   ⚠️  表 $tbl 可能不存在或不可读"
        MISSING=$((MISSING+1))
    fi
done
if [ $MISSING -eq 0 ]; then
    echo "   ✅ 全部 $TABLES 个表正常"
fi

# 4. 关键模块导入
echo ""
echo "📦 4/4 模块导入检查..."
python3 -c "
import sys; sys.path.insert(0, '.')
from utils.data_store import QuoteStore
from core.fetcher.limit_up_analysis import LimitUpAnalyzer
from core.analyzer.scorer import build_candidate_pool
from core.fetcher.daily_fetch import fetch_all
from core.analyzer.close_task import daily_close_task
print('   ✅ 全部模块导入成功')
" 2>/dev/null

echo ""
echo "================================"
if [ $ERRORS -gt 0 ]; then
    echo " ❌ 发现 $ERRORS 个问题，请修复后重试"
    exit 1
else
    echo " ✅ Preflight 全部通过"
fi
echo "================================"
