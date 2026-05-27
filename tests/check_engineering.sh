#!/usr/bin/env bash
# ============================================
# 工程规范自动检查 — 改代码后运行
# 用法: bash tests/check_engineering.sh
# ============================================

set -euo pipefail
FAIL=0
BASE="$(cd "$(dirname "$0")/.." && pwd)"

echo "========================================"
echo " 🔬 工程规范自动检查"
echo "========================================"

# ─── 1️⃣ 检查PROJECT_STATE.md是否要更新 ───
echo ""
echo "📋 1/4 检查文档是否需要更新..."

GIT_CHANGED=$(cd "$BASE" && git diff --name-only HEAD 2>/dev/null || true)
if [ -z "$GIT_CHANGED" ]; then
    GIT_CHANGED=$(cd "$BASE" && git diff --name-only --cached 2>/dev/null || true)
fi

if [ -n "$GIT_CHANGED" ]; then
    echo "   检测到以下文件有改动:"
    echo "$GIT_CHANGED" | sed 's/^/      /'
    
    # 检查是否Python文件有改动
    PY_CHANGED=$(echo "$GIT_CHANGED" | grep '\.py$' || true)
    MD_CHANGED=$(echo "$GIT_CHANGED" | grep -i 'project_state\|memory' || true)
    CFG_CHANGED=$(echo "$GIT_CHANGED" | grep -iE 'cron|env|config|\.yaml|\.yml|\.json' || true)
    
    if [ -n "$PY_CHANGED" ] && [ -z "$MD_CHANGED" ]; then
        echo "    ⚠️  ⚠️ ⚠️  改了Python文件但PROJECT_STATE.md/MEMORY.md没更新!"
        echo "    → 请在PROJECT_STATE.md中记录改动的文件/原因"
        echo "    → 请在MEMORY.md中记录重要状态变更"
        FAIL=1
    fi
    
    if [ -n "$CFG_CHANGED" ] && [ -z "$MD_CHANGED" ]; then
        echo "    ⚠️  ⚠️ ⚠️  改了配置相关文件但MEMORY.md没更新!"
        echo "    → 请同步更新MEMORY.md的定时任务/端口/配置列表"
        FAIL=1
    fi
    
    # 检查 scorer.py 改了但没更新 选股评分规则文档
    if echo "$GIT_CHANGED" | grep -q 'scorer\.py'; then
        if ! echo "$GIT_CHANGED" | grep -qi '选股评分规则'; then
            echo "    ⚠️  改了 scorer.py，确认是否需要同步更新 docs/选股评分规则.md？"
            echo "    → 若仅修改 except/注释/格式等不影响评分的变更，可忽略此提醒"
            # 检查是否真正改了评分逻辑（含 score/权重/公式的关键词）
            if git diff --cached -- scorer.py | grep -qE 'weight|score|param|标准|公式|阈值'; then
                echo "    ⚠️  ⚠️ ⚠️  检测到评分逻辑变更！必须更新 docs/选股评分规则.md"
                FAIL=1
            fi
        fi
    fi
    
    # 检查 morning_check.py 改了但没更新 news_fetcher.py
    if echo "$GIT_CHANGED" | grep -q 'morning_check\.py'; then
        if ! echo "$GIT_CHANGED" | grep -q 'news_fetcher\.py'; then
            echo "    ⚠️  ⚠️ ⚠️  改了 morning_check.py，确认是否需要同步改 news_fetcher.py？"
        fi
    fi
else
    echo "   ⏭️  无git变更记录，跳过文档检查"
fi

# ─── 2️⃣ 检查SQL中MySQL保留字使用 ───
echo ""
echo "🗄️  2/4 检查MySQL保留字字段..."
RESERVED_WORDS="rank|order|group|key|index|primary|unique|status|type|value|count|select|from|where|position|action|date|time|name|comment|level"
FOUND_FIELDS=$(grep -rn "INSERT.*INTO\|insert_or_ignore\|INSERT IGNORE\|REPLACE INTO" "$BASE" --include='*.py' 2>/dev/null | grep -o "'[a-z_][a-z_]*'" | tr -d "'" | sort -u | grep -iE "$RESERVED_WORDS" || true)
if [ -n "$FOUND_FIELDS" ]; then
    echo "    ⚠️  数据库字段使用了MySQL保留字:"
    echo "$FOUND_FIELDS" | sed 's/^/      → /'
    echo "    → 建议检查这些字段是否在 insert_or_ignore() 或手动拼SQL时会被用到"
    echo "    → 拼SQL时需加反引号: \`字段名\`"
    FAIL=1
else
    echo "   ✅ 字段名安全"
fi

# ─── 3️⃣ 运行preflight ───
echo ""
echo "🧪 3/4 运行预检脚本..."
if [ -f "$BASE/tests/preflight.sh" ]; then
    STOCK_DB_URL="${STOCK_DB_URL:-mysql://root:stock123@127.0.0.1:3306/stock_analysis}" \
        bash "$BASE/tests/preflight.sh" || { FAIL=1; echo "   ❌ 预检失败"; }
else
    echo "   ⏭️  preflight.sh 不存在，跳过"
fi

# ─── 4️⃣ 检查数据一致性（如果指定了） ───
echo ""
echo "📊 4/4 数据一致性检查..."
cd "$BASE"
if python3 -c "from dao import get_db; db=get_db(); print('DB OK')" 2>/dev/null; then
    echo "   ✅ 数据库连接正常"
else
    echo "   ⚠️  数据库连接失败（可能不是问题）"
fi

echo ""
echo "========================================"
if [ $FAIL -eq 1 ]; then
    echo " ❌ 有检查项未通过，请修正后重新提交"
else
    echo " ✅ 工程规范检查全部通过"
fi
echo "========================================"
exit $FAIL
