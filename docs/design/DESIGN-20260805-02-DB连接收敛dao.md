# DESIGN-20260805-02 DB 连接统一收敛到 dao.py 单一入口

- **编号**：DESIGN-20260805-02
- **日期**：2026-08-05
- **状态**：待主人 check 审核（审核通过后由 RDAgent 实施）
- **开发目标**：开发区 `/Users/wangyanming/workspace/StockAnalysis-dev`（提交后生产拉取）

---

## 一、背景与问题

### 1.1 现状：DB 连接配置散落多处，开发/生产靠"改默认值"区分

当前有 **4 个文件各自维护** DB 连接默认值，配置不统一、易误提交污染生产：

| 文件 | 位置 | 内容 |
|------|------|------|
| `utils/dao.py` | 第 35-36 行 | `_DEFAULT_MYSQL_URL` + `DB_URL = os.environ.get("STOCK_DB_URL", _DEFAULT_MYSQL_URL)`（唯一权威入口） |
| `core/fetcher/daily_fetch.py` | 第 27-28 行 | `if 'STOCK_DB_URL' not in os.environ: os.environ['STOCK_DB_URL']=...` 兜底块 |
| `core/fetcher/fetch_all_stocks_daily.py` | 第 21-22 行 | 同上兜底块 |
| `core/analyzer/close_task.py` | 第 20-21 行 | 同上兜底块 |

**已核实**：这 3 个文件虽然各自写了 `STOCK_DB_URL` 兜底块，但它们实际都通过 `from utils.dao import get_db`（或 `DB = get_db()`）连库 —— 即真正连哪个库由 `dao.py` 的默认值决定，那 3 个兜底块是**纯冗余**，与 dao 默认值水平重复。其余所有模块（`morning_check` / `intraday_monitor` / `limit_up_analysis` / `scorer` / `daily_pick_v2` / `pick_react` 等）都正确只用 `from utils.dao import get_db`，不自己写 DB 配置（已 grep 全库核实）。

### 1.2 核心矛盾：开发想连 dev 库、生产必须连生产库，却靠"改默认值"实现

- **现状机制**：开发区想跑起来连 `stock_analysis_dev`（方便自测不碰生产数据），靠把 `dao.py` 及 3 个兜底块的默认值写成 dev 库；生产区必须连 `stock_analysis`，靠默认值写生产库。
- **代价**：
  1. dev 配置散落 4 处，改动要同步 4 个文件，易漏改；
  2. dev 区手滑把这 4 个"连 dev 库"改动提交 → 生产拉取后就会污染成连 dev 库，风险极高；
  3. 同类脚本（web/cron）本身不写兜底、纯靠 dao 默认值，改 dao 默认值会是全局影响，超范围。
- **部署事实（已核实）**：生产 web 服务走 launchd（`com.stock.web.server.plist`：`/usr/bin/python3 /Users/wangyanming/workspace/StockAnalysis/web_server.py`，WorkingDirectory=生产根目录），**不设任何环境变量**；生产 cron 同样不设环境变量。全靠代码内默认值连库。故 `dao.py` 的默认值就是生产的 DB 决策点。

### 1.3 目标

把 DB 连接**收敛到唯一入口 `utils/dao.py`**，并按项目所在路径自动判定默认连"生产库 / 开发库"：

1. **删掉 3 个文件**（`daily_fetch` / `fetch_all_stocks_daily` / `close_task`）的独立 `STOCK_DB_URL` 兜底块；
2. **`dao.py` 按项目路径自动选库**：dev 项目目录（磁盘路径含 `StockAnalysis-dev`）→ 默认连开发库；生产目录 → 默认连生产库。实现"dev 默认连 dev db、生产默认连 db"且互不污染、各自目录都可正常提交；
3. **环境变量 `STOCK_DB_URL` 保持最高优先级可覆盖**（语义不变，`DB_URL = os.environ.get("STOCK_DB_URL", 路径判定默认值)`）。

---

## 二、关键设计决策

### 2.1 路径判定实现（SAAgent 明确）

**⚠️ 真实目录名核实（重要修正）**：本机两个项目的**真实磁盘目录名**是
- 生产：`/Users/wangyanming/workspace/StockAnalysis`
- 开发：`/Users/wangyanming/workspace/StockAnalysis-dev`（**大写 S + 连字符 `-dev`**，不是全小写下划线的 `stock_analysis_dev`）

> 任务描述中的「按 “stock_analysis_dev” 判定」与真实目录名不符；若照搬 `"stock_analysis_dev" in path` 匹配，**开发目录会被误判成生产库**（大小写/连字符均不匹配）。本方案以真实目录名 `StockAnalysis-dev` 为准。

**原则**：精确匹配 dev 项目**目录名** `StockAnalysis-dev`，**避免用宽泛的 `'dev'` 子串误判**其他含 dev 的路径。

项目根目录以 `utils/dao.py` 所在位置推导：`utils` 是项目根下的第一级子目录，故

```
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
```

（`dao.py` 位于 `utils/` 下，上溯一级即项目根。）

**判定逻辑**（写入 `utils/dao.py`，替换当前第 35-36 行）：

```python
# ─────────────────────────────────────────────
# 默认 MySQL 连接（无需环境变量即可工作，按项目路径自动选库）
#   - 生产目录（路径不含 StockAnalysis-dev）→ 连生产库 stock_analysis
#   - 开发目录（路径含 StockAnalysis-dev）  → 连开发库 stock_analysis_dev
#   - 环境变量 STOCK_DB_URL 优先级最高，可手动覆盖任一默认值
# ─────────────────────────────────────────────

_PROD_MYSQL_URL  = "mysql://root:stock123@127.0.0.1:3306/stock_analysis"
_DEV_MYSQL_URL   = "mysql://dev_app:dev123456@127.0.0.1:3306/stock_analysis_dev"

# 项目根 = dao.py 所在目录的上一级（utils/ 上溯一级）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 精确判定开发目录：路径中必须出现完整的 dev 项目目录名 StockAnalysis-dev
#（匹配完整目录名，而非宽泛的 'dev' 子串，避免误判其他含 dev 的路径）
def _default_mysql_url() -> str:
    if "StockAnalysis-dev" in _PROJECT_ROOT:
        return _DEV_MYSQL_URL
    return _PROD_MYSQL_URL

_DEFAULT_MYSQL_URL = _default_mysql_url()
DB_URL = os.environ.get("STOCK_DB_URL", _DEFAULT_MYSQL_URL)
```

> 说明：用 `"StockAnalysis-dev" in _PROJECT_ROOT` 精确子串匹配（大小写与连字符均精确），仅命中真实开发目录。`_PROJECT_ROOT` 在本机就是 `/Users/wangyanming/workspace/StockAnalysis-dev`（命中→dev 库）或 `/Users/wangyanming/workspace/StockAnalysis`（不命中→生产库），判定稳定、可正常提交。已用真实路径实测：`"StockAnalysis-dev" in "/Users/wangyanming/workspace/StockAnalysis-dev"` → `True`，`"StockAnalysis-dev" in "/Users/wangyanming/workspace/StockAnalysis"` → `False`。

### 2.2 两套库完整连接串（单一真相来源，写入 dao.py 注释）

将下列两套连接串在 `dao.py` 注释中**完整列清**，作为唯一权威来源，后续无需再到别处查找：

| 环境 | 目录判定特征 | 库 | 连接串（user / host:port / db） |
|------|------------|----|------------------------------|
| **生产** | 路径**不含** `StockAnalysis-dev` | `stock_analysis` | `mysql://root:stock123@127.0.0.1:3306/stock_analysis` |
| **开发** | 路径**包含** `StockAnalysis-dev`（真实目录名） | `stock_analysis_dev` | `mysql://dev_app:dev123456@127.0.0.1:3306/stock_analysis_dev` |

> 覆盖：任何环境显式设 `STOCK_DB_URL` 即优先走该值（含 `backtest/` 等脚本用 `STOCK_DB_UNIX=1` 走 unix socket，见 §2.4）。

### 2.3 保留 unix socket 能力（**新增的必改点，防止回归**）【重要】

**已核实的高风险点**：生产 `dao.py`（HEAD `4196eca`）**带有 unix socket 支持**（`STOCK_DB_UNIX=1` → `/tmp/mysql.sock`，`parse_mysql_url` 内处理、`_init_pool` 透传 `unix_socket`），生产 `backtest/` 系列脚本靠它连库。而**开发分支 `dao.py` 全历史从未有过这段 unix socket 代码**（开发镜像已被替换成纯 TCP 版本）。若本次直接在开发 dao.py 上加路径判定而不补回 unix_socket 能力，则**生产日后拉取新 dao.py 会静默丢失 unix socket 支持，导致 `backtest/` 脚本连库失败**。

因此：**本次 dao.py 改写必须以「生产 dao.py 的 unix_socket 能力」为基线**（在其上叠加路径判定），即合并后的统一 dao.py 同时具备：路径判定 + unix_socket 支持。实施时 RDAgent 应把生产 dao.py 的 `STOCK_DB_UNIX` 相关逻辑并入开发 dao.py 后再改默认值判定。

### 2.4 环境变量优先级（语义保持）

```
DB_URL = os.environ.get("STOCK_DB_URL", _DEFAULT_MYSQL_URL)
```

- `STOCK_DB_URL` 显式设置 → 最高优先级，无视路径判定；
- 未设置 → 走 `_PROJECT_ROOT` 路径判定选出的默认值；
- `STOCK_DB_UNIX=1` → 在选定 URL 基础上改走 `/tmp/mysql.sock`（unix socket 覆盖 host/port），专供 `backtest/` 脚本，不冲突。

---

## 三、影响面分析

### 3.1 删 3 个兜底块后连库是否仍正确

**正确**。3 处兜底块内容与 `dao.py` 默认值在各自目录下本就一致（dev 目录→dev 库、生产目录→生产库）；它们实际都走 `get_db()`，dao 按路径判定给出正确库：

- **生产目录跑** `daily_fetch` / `fetch_all_stocks_daily` / `close_task`：`_PROJECT_ROOT` 不含 `StockAnalysis-dev` → 默认连 `stock_analysis`（与 4196eca 生产默认一致）✅
- **开发目录跑** 同上 3 文件：`_PROJECT_ROOT` 含 `StockAnalysis-dev` → 默认连 `stock_analysis_dev` ✅（替代原来"靠改 4 处默认值连 dev"的临时做法）
- 删除的只是冗余 `if 'STOCK_DB_URL' not in os.environ: ...` 块，不影响各自对 `get_db()` 的引用。

### 3.2 生产路径是否无影响

- **web_server / morning_check / intraday_monitor / limit_up_analysis / scorer / daily_pick_v2 / pick_react** 等：全部只用 `get_db()`，从不碰 `STOCK_DB_URL`；生产目录下 dao 路径判定给出 `stock_analysis`，与现状（生产默认连生产库）**行为完全一致**，无影响。
- **launchd / cron**：本来就不设环境变量，走了 dao 默认值；现在默认值仍是生产库，**无需改任何 plist/cron 配置**。
- **web_server（prod 4196eca）**：无 `STOCK_DB_UNIX`，走 TCP 生产默认，无影响。

### 3.3 dev 区开发是否默认连 dev 库

**是**。dev 目录 `_PROJECT_ROOT` 含 `StockAnalysis-dev` → 默认连 `stock_analysis_dev`。开发自测结果与现状（靠 4 处临时默认值连 dev）一致，但收敛为单一入口，且**该配置可正常提交**（提交的是"路径判定逻辑"，不是"dev 配置"），不会污染生产。

> 注：判定匹配键是**磁盘目录名** `StockAnalysis-dev`（§2.1），而 `stock_analysis_dev` 是**目标数据库库名**，二者不同、勿混用。

### 3.4 与 69285bc / 4196eca 的冲突核查（SAAgent 核实）

- `git show 69285bc --stat` **确认未改动 `utils/dao.py`** → 本次 dao.py 改动与 69285bc 无冲突，独立、可叠加。
- `git show 4196eca --stat` 显示生产在 4196eca 改了 dao.py（加 unix_socket + 池参数 10/2→5/1）；但该改动在生产本地已被提交，本次开发 dao.py 变更需要在 merge 进生产时与它协调 —— 详见 §2.3，本次以"带 unix_socket 的生产 dao.py"为基线叠加路径判定，方向一致、无逻辑冲突。
  > ⚠️ 附带发现：生产与开发 git **已在 `eef5a41` 处分叉**（生产 4196eca 父=9e3a5f0，开发 69285bc 父=b777b64）。这属于历史分叉问题，**不在本次任务范围**内解决，仅记录；本次改动本身与两者均无文件级冲突。

### 3.5 待处置的 4 处未提交"连 dev 库"本地改动【明确】

开发工作区当前有 4 处未提交改动（`git diff` 已核实），均为把默认值临时改成 dev 库：

- `utils/dao.py:35`（`_DEFAULT_MYSQL_URL` → dev 库）
- `core/fetcher/daily_fetch.py:28`（兜底块 → dev 库）
- `core/fetcher/fetch_all_stocks_daily.py:22`（兜底块 → dev 库）
- `core/analyzer/close_task.py:21`（兜底块 → dev 库）

**处置方式**：本次收敛后这 4 处临时改动**全部不再需要**，统一被 dao 路径判定取代：
1. `dao.py` 的临时 dev 默认值 → 被 §2.1 的路径判定逻辑取代（RDAgent 以生产 dao.py 为基线，将默认值改为路径判定，并把池参数 / unix_socket 一并统一）；
2. 3 个文件的兜底块 → **直接删除**（连同其临时 dev 值一起删，无残留）。

最终提交内容是：删 3 处兜底块 + dao.py 改为路径判定。不再出现任何"把默认值写成 dev"的临时改动。

---

## 四、不做的事（明确排除）

- **不改 launchd / cron 配置**：它们不设环境变量也正确，dao 按路径判定给对库。
- **不加 commit hook**：提交的已是"路径判定逻辑"而非"dev 配置"。
- **不动 `limit_up_tracking` 相关**：那是已完成任务 `DESIGN-20260805-01`，本次不涉及。
- **不做 git 分叉合并**：生产/开发历史分叉（§3.4）本次只记录不处理。
- **不新增环境变量机制**：仅 `STOCK_DB_URL`（最高优先）+ `STOCK_DB_UNIX`（unix socket，仅 backtest 用）。

---

## 五、实施清单（供 RDAgent）

### A. `utils/dao.py`（核心）
1. 以**生产 dao.py（4196eca，含 unix_socket）**为基线文件；
2. 顶部注释列清两套连接串（§2.2）；
3. 新增 `_PROD_MYSQL_URL` / `_DEV_MYSQL_URL` / `_PROJECT_ROOT` / `_default_mysql_url()`，替换 `_DEFAULT_MYSQL_URL` 与 `DB_URL` 两行（§2.1）；
4. 保留 unix_socket 处理逻辑（`STOCK_DB_UNIX` → `/tmp/mysql.sock`）不加删改；
5. 连接池参数：与生产一致（`_POOL_MAXCONNECTIONS=5`、`_POOL_MINCACHED=1`）—— 随基线并入，避免生产回归。

### B. 删 3 个兜底块
- `core/fetcher/daily_fetch.py:27-28`：删除 2 行兜底块；
- `core/fetcher/fetch_all_stocks_daily.py:21-22`：删除 2 行兜底块；
- `core/analyzer/close_task.py:20-21`：删除 2 行兜底块。

> 若 `os` 只因兜底块才被 import，删除后顺手清理多余 import（`daily_fetch`/`close_task` 另用 `os` 做路径，需逐一核对，勿误删）。

### C. 文档同步
- 按 `PROJECT_STATE.md` 约定（"改数据库连接串 → 同步更新工程规则头部"），在 `AGENTS.md` / 工程规则头部同步数据库连接说明，注明「dao.py 按路径自动判定生产/开发库」。
- 更新 `PROJECT_STATE.md` 数据库章节。

---

## 六、验证方案

在**开发目录** `StockAnalysis-dev` 逐项验证：

1. **语法/导入门禁**：`python3 -c "import utils.dao"` 无报错；分别 `python3 -m py_compile core/fetcher/daily_fetch.py core/fetcher/fetch_all_stocks_daily.py core/analyzer/close_task.py` 通过。
2. **dev 目录默认连 dev 库**：`cd /Users/wangyanming/workspace/StockAnalysis-dev && STOCK_DB_URL= python3 -c "from utils.dao import parse_mysql_url,DB_URL; print(DB_URL)"` → 应输出 `mysql://dev_app:dev123456@127.0.0.1:3306/stock_analysis_dev`；再跑一个只读连通脚本确认能 SELECT 到 dev 库（不写生产）。
3. **环境变量覆盖仍生效**：`STOCK_DB_URL=mysql://foo:bar@127.0.0.1:3306/other python3 -c "from utils.dao import DB_URL; print(DB_URL)"` → 输出 `other`（最高优先级）。
4. **删兜底后 import/运行正常**：直接执行 3 个脚本（或 `--help` / dry 模式）确认不再因删兜底块报错，且连接对象指向 dev 库；抽样跑 `get_db().fetchone("SELECT DATABASE()")` → 返回 `stock_analysis_dev`。
5. **unix socket 保留**：`STOCK_DB_UNIX=1 python3 -c "from utils.dao import parse_mysql_url; print(parse_mysql_url('mysql://root:stock123@127.0.0.1:3306/stock_analysis'))"` → 输出含 `unix_socket=/tmp/mysql.sock`，确认 backtest 连库能力未回归。

生产目录验证（拉取/复制合并后，或直接对生产目录单独测）：
6. **生产目录默认连生产库**：`cd /Users/wangyanming/workspace/StockAnalysis && python3 -c "from utils.dao import DB_URL; print(DB_URL)"` → 输出 `mysql://root:stock123@127.0.0.1:3306/stock_analysis`。

> 门禁遵循 `engineering-rules` v6.0（§5.1）：语法、工程规范、数据校验、数据库连接正常、模块可导入无循环依赖均须通过；开发完成后由 QA subagent 独立验收。

---

## 七、回滚方案

- **改动内容**：`utils/dao.py`（路径判定 + unix_socket） + 删 3 处兜底块 + 文档同步，均为单次小改动，回滚成本低。
- **回滚方式**：
  1. 若已提交：`git revert <本次提交>`（dev 分支），或 `git checkout <上一提交> -- utils/dao.py core/fetcher/daily_fetch.py core/fetcher/fetch_all_stocks_daily.py core/analyzer/close_task.py` 精确还原 4 个文件；
  2. 生产若已拉取：对生产 4 个文件 `git checkout 4196eca -- <文件>` 恢复生产基线；
  3. 临时应急（不改代码）：在 launchd/cron 或部署脚本设 `STOCK_DB_URL` 显式指回对应库（优先级最高，绕过路径判定），等修复后移除。
- **恢复开发自测**：路径判定天然覆盖，无需额外动作。

---

## 八、风险与备注

- **风险-1（最高）**：若 RDAgent 误以「开发无 unix_socket 的 dao.py」为基线，会静默丢 unix_socket → 生产 backtest 连不上库。**务必以生产 dao.py 为基线**（§2.3）。
- **风险-2**：`_PROJECT_ROOT` 判定依赖目录结构固定（`utils/dao.py`）。若未来 dao.py 迁移到别处，需同步调整推导。已用 `abspath` 确保非 cwd 依赖。
- **风险-3**：生产/开发历史分叉（§3.4）非本任务处理，但生产合并时需注意其他文件级冲突（尤其 4196eca 改过的 web_server / scorer / stock_analysis_api 等），建议在真正 merge 前单独评审。
