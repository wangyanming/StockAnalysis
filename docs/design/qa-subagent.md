# QA Subagent 设计方案

## 1. 背景

当前开发流程缺少独立的质量保障环节。开发者（主 agent）写完代码后自我验证，存在以下问题：

- **自测盲区**：改完跑一遍 preflight 就过了，但 preflight 覆盖的场景有限（比如今天漏了 `_log_dir` 定义，语法检查过了但运行时炸了）
- **验证不一致**：每次改完"跑一下"靠口头执行，没有标准化的测试清单
- **无人把关**：没有独立的通过/打回决策者，质量取决于 developer 当天心情

## 2. QA Subagent 定位

- **独立于主 agent**：不参与开发决策，只负责验证
- **有否决权**：测试不通过，开发者不能提交 git
- **全栈验证**：语法、导入、运行、数据一致性全覆盖
- **可追溯**：每次提测输出标准测试报告，存档至 `logs/qa/`

## 3. 工作流程

```
开发者                              QA                           Git
  │                                  │                            │
  ├─ 完成开发                         │                            │
  ├─ 输出【功能清单】────提测通知─────→                              │
  │                                  ├─ 写测试用例                  │
  │                                  ├─ 执行验证                    │
  │                                  │    ├─ 语法检查                │
  │                                  │    ├─ 模块导入                │
  │                                  │    ├─ 数据校验                │
  │                                  │    ├─ 功能测试                │
  │                                  │    └─ 影响面扫描              │
  │                                  │                              │
  │                                  ├─ 输出【测试报告】            │
  │                                  │                              │
  │               ←─── 通过 ──────   │  ───→  允许提交              │
  │               ←─── 不通过 ────   │                              │
  ├─ 收到反馈                         │                            │
  ├─ 修复问题                         │                            │
  └─ 重新提测 ──────────────────→    │                              │
                                     │                              │
                                     └─ (允许提交) ──→  开发者提交  │
```

### 3.1 提测触发方式

开发者通过 `sessions_spawn` 调用 QA subagent，传入参数：

```json
{
  "taskName": "qa_review_20260528",
  "task": "请根据以下功能清单进行QA测试……\n【功能清单】\n……\n【修改文件】\n……\n【根目录】\n……",
  "context": "isolated"
}
```

### 3.2 测试范围

QA 根据功能清单自动判断测试范围，分 4 级，每级内包含多个维度：

| 级别 | 维度 | 覆盖范围 | 触发条件 | 预计耗时 |
|------|------|---------|---------|---------|
| **L1 基础规范** | 代码规范 | 命名规范、函数长度、注释完整度、无魔法数字 | 任何改动 | < 10s |
| | 目录规范 | 文件路径合规、import路径匹配、无冗余文件 | 任何改动 | |
| | 安全红线 | 无明文密码/密钥、无 `rm -rf`、无硬编码路径 | 任何改动 | |
| | 语法检查 | 所有 .py 语法 + import 路径 | 任何改动 | |
| | 工程检查 | 跑 `check_engineering.sh` + `preflight.sh` | 任何改动 | |
| **L2 功能验证** | 模块导入 | 修改文件及其依赖可正常导入 | 任何改动 | < 60s |
| | 核心函数 | 被修改函数/脚本运行测试 | 改动了逻辑 | |
| | cron 环境 | 模拟 cron 环境（`cd root && python3 path/to/file`） | 改动了cron脚本 | |
| | 异常覆盖 | try/except 分支有日志、不静默 | 任何改动 | |
| **L3 数据校验** | 入库一致性 | 数据写入后查询返回符合预期 | 改动了数据流程 | < 120s |
| | 单位校验 | 单位转换有注释、偏差 < 5% | 改动了数据流程 | |
| | 边界测试 | 空列表、None、异常值输入 | 改动了数据流程 | |
| **L4 回归扫描** | 同类检查 | 全项目扫描同类代码模式（如所有`_log_dir`都定义了） | 任何改动 | < 180s |
| | 影响面分析 | 修改文件的所有调用方检查 | 改了公共模块 | |
| | 全量导入 | 全量脚本逐个 import | 改了 utils/ 等公用模块 | |

### 3.3 测试报告格式

QA 输出标准格式报告：

```
╔═══════════════════════════════════════╗
║  QA Test Report                       ║
║  时间: 2026-05-28 17:20               ║
║  提测内容: fix: morning_check.py      ║
╚═══════════════════════════════════════╝

【L1 语法检查】 ✅ 全部通过
  • morning_check.py: ✅
  • utils/__init__.py: ✅

【L2 功能验证】 ❌ 2/3 通过
  • morning_check.py 手动运行: ✅
  • fetch_all_stocks_daily.py 手动运行: ❌
    → 错误: name '_log_dir' is not defined
    → 根因: _log_dir 在 imports 之后、basicConfig 之前未定义
    → 建议: 在 logging.basicConfig 前插入 _log_dir = ...

【L3 数据校验】 ⏭️ 跳过（本次无数据流程改动）

【L4 回归扫描】 ❌ 发现同类问题
  • core/analyzer/pick_react.py: _log_dir 未定义
  • core/analyzer/daily_pick_v2.py: _log_dir 未定义

═══════════════════════════════════════
【结论】❌ 不通过
【打回原因】2 个运行时错误 + 2 个同类文件遗漏
```

## 4. 测试用例设计

QA subagent 根据功能清单 + 修改文件，自动生成测试用例。测试用例不是预设写死的，而是**动态生成**的：

```python
# 测试用例生成逻辑（伪代码）
def generate_cases(feature_list, modified_files, project_dir):
    cases = []
    
    # 1. 每个 .py 文件：语法 + import + 单文件运行
    for f in modified_files:
        cases.append(SyntaxTest(f))
        cases.append(ImportTest(f))
        if is_runnable(f):
            cases.append(RunTest(f))
    
    # 2. cron 脚本：模拟定时任务环境运行
    for f in modified_files:
        if is_cron_script(f):
            cases.append(CronEnvTest(f))  # cd root + python3 path/to/file
    
    # 3. 数据相关：校验数据库写入
    if has_db_changes(modified_files):
        cases.append(DBWriteTest())
        cases.append(DataConsistencyTest())
    
    # 4. 全量扫描：检查同类代码模式
    cases.append(PatternScan(project_dir, patterns))
    
    return cases
```

每条测试用例包含：
- **测试名称**：`morning_check.py import utils.dao`
- **测试命令**：`cd /root && python3 -c "from utils.dao import get_db"`
- **预期结果**：`ModuleNotFoundError` not in stderr
- **通过标准**：exit code = 0

## 5. QA Subagent 的 Skill 文件

QA subagent 需要一个 SKILL.md，定义其行为：

<details>
<summary>QA subagent SKILL.md （点击展开）</summary>

```markdown
# QA Subagent SKILL

## 职责
对 StockAnalysis 项目的代码变更进行独立质量验证。

## 工作流
1. 接收主 agent 的提测通知（包含功能清单、修改文件列表、项目根目录）
2. 自动生成测试用例
3. 执行测试，生成标准化报告
4. 输出测试结论：通过/不通过
5. 如不通过，标注根因、修复建议、波及范围

## 关键行为规则
- 不做代码修复，只做验证
- 提交前必须跑 check_engineering.sh + preflight.sh
- 发现同类问题必须扫描全项目
- 测试报告必须写入 logs/qa/ 目录持久化
- 输出的结论必须是明确的 ✅/❌，不能是"建议"
```

</details>

## 6. 工程规范更新

在现有 `skills/engineering-rules/SKILL.md` 中新增：

```
## 11. QA 验证规范

### 11.1 提测时机
所有非 trivial 的代码改动，必须通过 QA subagent 验证后才能提交 git。

### 11.2 trivial 例外清单（不需要提QA）
- 仅改注释/文档/README
- 仅改工程规范 SKILL.md 自身
- 仅更新 PROJECT_STATE.md / MEMORY.md
- 仅改非代码文件（配置、模板等）

### 11.3 QA 结论处理
- ✅ 通过 → 开发者可以提交 git
- ❌ 不通过 → 开发者修复后重新提测
```

## 7. 开发计划

| 步骤 | 内容 | 责任人 |
|------|------|--------|
| 1 | 写 QA subagent SKILL.md | 主 agent |
| 2 | 配置 OpenClaw 的 QA agent（在 AGENTS.md 或 gateway 中注册） | 主 agent |
| 3 | 更新工程规范，加入 QA 验证流程 | 主 agent |
| 4 | 首次试跑：用今天已修复的日志落盘改动做一次完整提测 | 主 agent + QA |
| 5 | 运行 1-2 天验证流程稳定性 | 主 agent + 主人 |
| 6 | 优化测试用例生成逻辑 | 主 agent |

## 8. 备注

- QA subagent 运行环境：`runtime="subagent"`，继承主 agent 的 workspace
- 不需要独立的数据库连接，复用主 agent 的库
- 测试结果持久化：`logs/qa/<YYYYMMDD_HHMMSS>.report.md`
- 后续可升级：自动对比数据一致性、基准测试性能
