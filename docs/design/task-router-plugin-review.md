# 代码审核报告：task-router-plugin

**审核时间:** 2026-07-22
**审核人:** architecture-agent (subagent)
**审核对象:** task-router-plugin v1.0.0

---

## 1. 逐文件检查结果

### 1.1 `docs/design/task-router-plugin.md` — 技术方案设计文档

| 检查项 | 结果 | 说明 |
|--------|------|------|
| 文档结构完整性 | ✅ | 包含总体方案、模块划分、拦截规则、核心架构、身份识别、异常处理等完整章节 |
| 拦截规则定义 | ✅ | 覆盖 write/edit/apply_patch/exec 四种工具的规则和路径匹配 |
| 豁免 Agent 列表 | ✅ | product/architecture/engineering/qa-agent 均在方案中定义 |
| exec 写操作检测 | ✅ | shell 重定向、heredoc、tee、sed -i 等均已覆盖 |
| subagent_spawned hook 定义 | ✅ | 明确标注为只读观察事件，不可修改 task 内容 |
| requireApproval 兜底 | ✅ | 触发条件、配置、优先级规则完整 |

### 1.2 `src/index.ts` — 插件主入口

| 检查项 | 结果 | 说明 |
|--------|------|------|
| definePluginEntry 使用正确 | ✅ | 使用 `openclaw/plugin-sdk/plugin-entry` 标准导入 |
| before_tool_call 注册 | ✅ | priority: 100，hook 签名匹配 `PluginHookBeforeToolCallEvent` / `PluginHookToolContext` |
| subagent_spawned 注册 | ⚠️ | **查看问题 #1** |
| 异常捕获 | ✅ | try-catch 包裹两个 handler，异常时返回 undefined (fail-open) |
| 日志记录 | ✅ | info/warn/error 级别分明 |
| 类型导入路径 | ⚠️ | **查看问题 #2** |
| 插件配置读取 | ✅ | 从 api.pluginConfig 读取配置 |

### 1.3 `src/handler.ts` — 拦截逻辑核心

| 检查项 | 结果 | 说明 |
|--------|------|------|
| shouldBlock 函数入口 | ✅ | 参数签名与类型定义一致 |
| 工具级过滤 (BLOCKED_TOOLS) | ✅ | 非列表内工具快速放行 |
| Agent 身份识别 (isExemptAgent) | ✅ | 双重识别：agentId 直接匹配 + sessionKey 解析 |
| 路径匹配 (matchesProjectPath) | ✅ | 使用 `String.includes('StockAnalysis/')` 匹配 |
| exec 写操作检测 (hasWriteOperation) | ✅ | 覆盖 `>` / `>>` / `tee` / heredoc |
| exec 就地编辑检测 (hasInlineEditOperation) | ✅ | 覆盖 sed -i / perl -i / cp / mv |
| exec include 检测 (hasIncludeProjectFile) | ✅ | 检查 include 参数和 command 中的 `{include:...}` 模板 |
| requireApproval 构建 (buildRequireApproval) | ✅ | 包含 title/description/severity/timeoutMs 等完整字段 |
| fail-open 安全策略 | ✅ | 无法解析命令或身份时返回 undefined 放行 |
| 函数级别异常处理 | ❌ | **查看问题 #3** |

### 1.4 `src/rules.ts` — 规则常量

| 检查项 | 结果 | 说明 |
|--------|------|------|
| BLOCKED_TOOLS 定义 | ✅ | 包含 write/edit/apply_patch/exec |
| EXEMPT_AGENTS 定义 | ✅ | 包含 4 个豁免 agent |
| PROJECT_PATH_MARKER | ✅ | 'StockAnalysis/' |
| WRITE_OPS_REGEX | ⚠️ | **查看问题 #4** |
| HEREDOC_REGEX | ✅ | `<<[-]?\s*\w+` 正确匹配 heredoc |

### 1.5 `package.json` — 包定义

| 检查项 | 结果 | 说明 |
|--------|------|------|
| 包名 | ✅ | `task-router` 与 manifest 一致 |
| type: module | ✅ | ESM 模式 |
| main 指向 dist/index.js | ✅ | 正确 |
| openclaw.src/字段 | ✅ | 正确指向 src/index.ts |
| openclaw.dist | ✅ | dist/index.js |
| 依赖声明 | ⚠️ | **查看问题 #5** |
| TypeScript 版本 | ✅ | ^5.4.0 足够 |

### 1.6 `tsconfig.json` — 编译配置

| 检查项 | 结果 | 说明 |
|--------|------|------|
| target: ES2022 | ✅ | 支持现代 JS 特性 |
| module: NodeNext | ✅ | 适用 ESM |
| strict: true | ✅ | 开启动态类型检查 |
| outDir/rootDir | ✅ | dist/src 正确对应 |
| declaration/sourceMap | ✅ | 均为 false (插件产物不需要) |

### 1.7 `openclaw.plugin.json` — 插件 manifest

| 检查项 | 结果 | 说明 |
|--------|------|------|
| id/name/version | ✅ | 与 package.json 一致 |
| activation.onStartup | ✅ | 启动时自动加载 |
| contracts.hooks | ✅ | before_tool_call 和 subagent_spawned |
| configSchema | ✅ | projectPathMarker / exemptAgents / approvalTimeoutMs |
| 配置默认值 | ✅ | 与 rules.ts 常量一致 |

---

## 2. 发现的问题清单

### 问题 #1 ❌ subagent_spawned handler 使用了错误的事件字段

**文件:** `src/index.ts` 第 94 行
**代码:**
```typescript
if (event.agentId === 'qa-agent') {
```
**类型定义** (`PluginHookSubagentSpawnedEvent`):
```typescript
type PluginHookSubagentSpawnedEvent = PluginHookSubagentSpawnBase & {
  runId: string;
  resolvedModel?: string;
  resolvedProvider?: string;
}
type PluginHookSubagentSpawnBase = {
  childSessionKey: string;
  agentId: string;
  // ...
}
```
**问题分析:**
- 类型定义中字段名为 `agentId` 而非 `childAgentId`。
- 检查实际类型定义发现：`PluginHookSubagentSpawnBase` 中的字段就是 `agentId`，而不是 `childAgentId`。
- 技术方案（设计文档）中使用的是 `event.childAgentId`，但实际运行时 SDK 类型是 `event.agentId`。
- 重新检查类型：`PluginHookSubagentSpawnBase` 的字段确实是 `agentId`，而不是方案中写的 `childAgentId`。
- ⚠️ **但注意**：技术方案 4.2 节和 8.1 节中使用的 `event.childAgentId` 与 SDK 实际类型中的预提字段名 `agentId` 不匹配。不过查看实际 ts 代码已使用 `event.agentId`，说明 engineering-agent 已对齐实际类型。**此问题为设计文档与技术方案之间的字段名不一致，代码本身是正确的。标记为 ⚠️ 文档不一致。**

### 问题 #2 ⚠️ 类型导入路径与 SDK 导出不完全一致

**文件:** `src/index.ts` 第 10-12 行
**代码:**
```typescript
import type {
  PluginHookBeforeToolCallEvent,
  PluginHookBeforeToolCallResult,
  PluginHookToolContext,
  PluginHookSubagentSpawnedEvent,
  PluginHookSubagentContext,
} from 'openclaw/plugin-sdk/types';
```
**问题分析:**
- `PluginHookBeforeToolCallEvent`, `PluginHookBeforeToolCallResult`, `PluginHookToolContext`, `PluginHookSubagentContext` 这些类型确实从 `openclaw/plugin-sdk/types` 导出（已验证 DTS）。
- `PluginHookSubagentSpawnedEvent` 也从此路径导出。
- ✅ 实际验证了 `types.d.ts` 的导出列表，这些类型均在导出中。
- **结论：导入路径和类型名称均正确。无问题。**

更新：重新仔细检查后发现导入路径实际上没问题。但要注意 `handler.ts` 中从 `./rules.js` 导入（带 `.js` 扩展名），这是 ESM NodeNext 模式的正确做法。

### 问题 #3 ❌ handler.ts 缺少异常保护 — extractTargetPath 对 apply_patch 的 input 字段判断脆弱

**文件:** `src/handler.ts` 第 111-112 行
**代码:**
```typescript
// apply_patch 的 input（patch 内容中有文件路径）
if (typeof params.input === 'string') {
  return params.input;
}
```
**问题分析:**
- `apply_patch` 的 `input` 参数是 patch diff 内容（多行字符串），不是文件路径。
- 使用 `params.input.includes('StockAnalysis/')` 作为路径匹配会误判：patch diff 中有 `--- a/StockAnalysis/xxx.py` 这种行，会被误判为命中项目路径。
- `input` 字段作为字符串可能恰好包含 `StockAnalysis/`（diff 的一部分），导致误拦截。
- 设计文档的测试用例中也包含 `params={input: "..."}` 作为路径检查输入，但这是不准确的。

**建议:** 
1. 对 `apply_patch` 不从 `input` 中提取路径，而是检查 `params.path` 如果存在
2. 或者检查 `params.input` 中的 `--- a/` 或 `+++ b/` 行模式来提取实际文件路径

### 问题 #4 ⚠️ WRITE_OPS_REGEX 正则的 tee 匹配可能误匹配命令名中包含 "tee" 的情况

**文件:** `src/rules.ts` 第 37 行
**代码:**
```typescript
export const WRITE_OPS_REGEX = /(?:^|\s)(?:[12]?[>&]?|&)?>+>?\s|(?:^|\|\s*)tee(?:\s+-[aA]+)?\s/;
```
**问题分析:**
- `tee` 部分匹配 `(?:^|\|\s*)tee(?:\s+-[aA]+)?\s` — 使用 `|` 管道前必须匹配 `|\s*`，这是一个合理约束。
- 但如果命令中有单词包含 "tee" 子串（如 `python3 -m pytest | tee_file`），会误匹配。
- 风险较低，因为 `tee` 前面必须有 `|` 管道符号才是真正的 tee 命令。

**建议:** 添加单词边界 `\b` 确保匹配的是完整的 `tee` 命令：
```typescript
export const WRITE_OPS_REGEX = /(?:^|\s)(?:[12]?[>&]?|&)?>+>?\s|(?:^|\|\s*)tee(?:\s+-[aA]+)?\s/;
```
改为：
```typescript
export const WRITE_OPS_REGEX = /(?:^|\s)(?:[12]?[>&]?|&)?>+>?\s|(?:^|\|\s*)\btee\b(?:\s+-[aA]+)?\s/;
```

### 问题 #5 ⚠️ package.json 中的 openclaw 依赖问题

**文件:** `package.json` 第 14 行
**代码:**
```json
"dependencies": {
  "openclaw": "^2026.3.24-beta.2"
}
```
**问题分析:**
- `openclaw` 作为运行时库依赖声明在 `dependencies` 中而非 `peerDependencies`。
- 这在插件场景下可能有风险：如果安装插件的宿主环境是不同版本的 OpenClaw，会导致版本冲突。
- 不过本地 `--link` 安装方式只引用本地的 node_modules，实际影响小。

**建议:** 将 `openclaw` 移到 `peerDependencies`，同时在 `devDependencies` 保留用于编译。
```json
{
  "peerDependencies": {
    "openclaw": "^2026.3.24-beta.2"
  },
  "devDependencies": {
    "openclaw": "^2026.3.24-beta.2",
    "typescript": "^5.4.0"
  }
}
```

### 问题 #6 ⚠️ 缺少单元测试

**说明:** 技术方案 8.1 节列出了完整的单元测试计划（13 个测试用例），包括：
- main agent 拦截/放行场景
- 豁免 agent 放行场景
- exec 重定向/tee 场景
- qa-agent spawn 收尾流程场景

但实际代码中 `src/__tests__/` 目录不存在，没有测试文件。技术方案设计的 13 个测试用例均未实现。

**建议:** 按技术方案编写测试，使用 vitest。

### 问题 #7 ✅ subagent_spawned 只读属性处理正确

**文件:** `src/index.ts` 第 89-97 行
**代码:**
```typescript
// 注: subagent_spawned 事件是只读的 (post-launch 观察),
// 无法修改 task 内容。此处仅记录日志作为提醒。
logger.info(
  `[task-router] qa-agent 已启动 (sessionKey=${event.childSessionKey})。` +
  `注意: 请确保 task 中包含 "⚠️ 强制收尾流程"`,
);
```
**分析:**
- ✅ 工程代码正确认识到 `subagent_spawned` 是只读的 post-launch 观察事件
- ✅ 没有尝试修改事件对象（不存在 task 字段可修改）
- ✅ 仅通过日志记录来提醒
- ✅ 异常处理捕获所有错误，不影响 spawn 流程
- 这是与技术方案一致的正确实现

### 问题 #8 ✅ requireApproval 字段名对齐

**检查:** `PluginHookBeforeToolCallResult` 中 `requireApproval.allowedDecisions` 的值类型
**类型定义:**
```typescript
allowedDecisions?: Array<"allow-once" | "allow-always" | "deny">;
```
**代码中:**
```typescript
allowedDecisions: ['allow-once', 'deny'],
```
✅ 与 SDK 类型定义完全一致。

---

## 3. 需要修改的建议

| 优先级 | 问题 | 建议 |
|--------|------|------|
| **P0-Must** | #3 apply_patch 的 `path` 检查脆弱 | 对于 `apply_patch`，优先检查 `params.path` 字段。如果不存在，再从 `params.input` 中解析 diff header 中的文件路径（`--- a/...` 或 `+++ b/...`） |
| **P1-Should** | #5 `openclaw` 依赖应该在 peerDependencies | 将 openclaw 移到 peerDependencies，确保插件版本与宿主兼容 |
| **P1-Should** | #6 缺少单元测试 | 按技术方案中的 13 个测试用例编写测试，覆盖：拦截/放行场景、exec 写操作检测、豁免 agent、路径边界、qa-agent spawn 等 |
| **P2-Could** | #4 WRITE_OPS_REGEX tee 匹配 | 添加 `\b` 单词边界避免命令名中含 "tee" 的误匹配 |
| **P3-Nice** | #1 技术方案与代码字段名不一致 | 更新技术方案中使用 `event.childAgentId` 的地方为 `event.agentId` |

---

## 4. 总体评价

### 结论：✅ 条件通过

**理由：** 核心功能实现正确，主要的架构设计原则（before_tool_call 拦截、agent 身份识别、exec 写操作检测、requireApproval 兜底、subagent_spawned 只读处理）均按技术方案正确实现。

**需要修复后方可上线：**

| # | 问题 | 影响评估 |
|---|------|---------|
| P0-#3 | apply_patch 的 input 路径解析可能导致误拦截 | 假阳性（false positive） — 如果其他 agent 给 main 的 apply_patch 中包含 `StockAnalysis/` 的 diff 内容，会导致误拦截。但实践中这种情况极少在 main agent 出现，主要是 review-agent 场景。 |
| P1-#6 | 缺少单元测试 | 无法确保边界场景正确性 |

**代码质量整体评价：**
- ✅ 代码风格清晰，注释完整（中英双语）
- ✅ 类型安全：TypeScript strict 模式，类型导入正确
- ✅ 架构一致：handler 逻辑与技术方案流程图完全对应
- ✅ 安全设计：fail-open 策略优先，异常吞干净
- ✅ 子Agent 身份识别双重验证（agentId + sessionKey）
- ⚠️ 缺少测试保障
- ⚠️ 依赖管理可改进
- ⚠️ apply_patch 路径解析可能不够精确

**建议：** 修复 P0 问题并补充测试后即可上线生产环境。
