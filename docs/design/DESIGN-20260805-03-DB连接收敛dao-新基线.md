# DESIGN-20260805-03 DB 连接统一收敛到 dao.py（基于新基线 4196eca 重做）

- **编号**：DESIGN-20260805-03
- **日期**：2026-08-05
- **状态**：待实施
- **开发分支**：dev（从 main/4196eca 切出；main 保持生产稳定基线不动）
- **背景**：原 DESIGN-20260805-02 基于过期基线 b777b64 实施，已因生产/开发脱节被舍弃。本次在新基线 4196eca（生产最新）上重做 DB 连接收敛。

---

## 一、目标

把 DB 连接收敛到唯一入口 `utils/dao.py`，按项目路径自动判定连"生产库 / 开发库"。

## 二、现状（4196eca 基线实测）

| 文件 | 现状 | 需改 |
|------|------|------|
| `utils/dao.py` | `_DEFAULT_MYSQL_URL = root:...@stock_analysis`(35)；**已有 unix_socket**(65)；池 5/1(39) | 加路径判定，其余已具备 |
| `core/fetcher/daily_fetch.py` | 有 `if 'STOCK_DB_URL'` 兜底块(27-28)，值=生产 root | 删兜底块，保留 os import |
| `core/fetcher/fetch_all_stocks_daily.py` | 同上(21-22) | 删兜底块，保留 os import |
| `core/analyzer/close_task.py` | 同上(20-21) | 删兜底块，保留 os import |

**要点**：4196eca 的 dao.py 已含 unix_socket + 池参数 5/1（无需再合并基线，比 DESIGN-20260805-02 更简单）。

## 三、实施内容

### A. `utils/dao.py` 加路径判定（替换 35-36 行）
```python
_PROD_MYSQL_URL  = "mysql://root:***@127.0.0.1:3306/stock_analysis"
_DEV_MYSQL_URL   = "mysql://dev_app:***@127.0.0.1:3306/stock_analysis_dev"

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _default_mysql_url() -> str:
    if "StockAnalysis-dev" in _PROJECT_ROOT:
        return _DEV_MYSQL_URL
    return _PROD_MYSQL_URL

_DEFAULT_MYSQL_URL = _default_mysql_url()
DB_URL = os.environ.get("STOCK_DB_URL", _DEFAULT_MYSQL_URL)
```
- 顶部注释列清两套连接串（单一真相）
- **保留** unix_socket 逻辑（65-66 行）与池参数（5/1）不动

### B. 删 3 个文件独立兜底块
- `daily_fetch.py` 27-28、`fetch_all_stocks_daily.py` 21-22、`close_task.py` 20-21
- 只删 `if 'STOCK_DB_URL' not in os.environ: os.environ['STOCK_DB_URL']=...` 两行
- ⚠️ **保留 os import**（3 文件都用 os 做路径处理：os.path.dirname/os.chdir/sys.path）

### C. 文档同步
- PROJECT_STATE.md 数据库章节：注明"dao.py 按项目路径自动判定生产/开发库"

## 四、环境变量优先级
```
DB_URL = os.environ.get("STOCK_DB_URL", _DEFAULT_MYSQL_URL)
```
- STOCK_DB_URL 显式设 → 最高优先，无视路径判定
- 未设 → 走 _PROJECT_ROOT 路径判定（dev分支路径含 StockAnalysis-dev → dev 库）
- STOCK_DB_UNIX=1 → unix socket 覆盖 host/port（保留）

## 五、不做的事
- 不改 launchd/cron（它们不设环境变量，靠 dao 路径判定）
- 不加 commit hook
- 不动 main 分支、不动 limit_up_tracking（那是改造1，本次不做）

## 六、验证
1. py_compile 4 文件 + import utils.dao 无循环依赖
2. dev 分支默认连 dev 库：`SELECT DATABASE()` → stock_analysis_dev
3. STOCK_DB_URL 覆盖 → other（最高优先）
4. STOCK_DB_UNIX=1 → parse_mysql_url 含 unix_socket=/tmp/mysql.sock
5. 删兜底后 3 文件 import/运行正常，os 保留
6. 全局 grep：STOCK_DB_URL|os.environ 仅剩 dao.py 一处
7. bash tests/check_engineering.sh 门禁

## 七、回滚
- 未提交：git checkout -- 4 文件还原
- 已提交：git revert <提交>；或临时设 STOCK_DB_URL 显式指回对应库（最高优先绕过路径判定）

## 八、风险
- 风险-1：误删 os import（3 文件都用 os）→ 保留 os import
- 风险-2：路径判定依赖目录结构（utils/dao.py 上溯一级），已用 abspath 非 cwd 依赖
- 风险-3：dev 分支连 dev 库成功与否取决于本地 dev 库可用；若 dev 库不存在需先建库/或用 STOCK_DB_URL 覆盖
