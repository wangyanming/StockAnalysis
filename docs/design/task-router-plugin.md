# 方案设计：task-router-plugin — Multi-Agent 任务路由插件

**版本:** v1.0
**日期:** 2026-07-22
**关联需求:** 主人 Multi-Agent 协作流程需求
**状态:** 草稿

---

## 1. 总体方案

设计一个 OpenClaw `before_tool_call` 插件，强制实施 Multi-Agent 协作架构：**main agent 只负责「理解需求 → 委派子Agent → 监控进度 → 验收结果」**，所有写文件/改文件/跑命令等操作必须通过 `sessions_spawn` 委托给对应的子 Agent 执行。

插件拦截 main agent 对特定工具（write / edit / apply_patch / exec 含写操作）的调用，在 StockAnalysis 项目目录下生效，子 Agent 不受限制。同时提供 `requireApproval` 兜底机制和 qa-agent 自动收尾流程追加。

### 关键设计原则

1. **least privilege** — main agent 默认不具备写操作的直接权限，必须委派
2. **agent identity policing** — 通过 `ctx.agentId` 区分 main / 子Agent，子Agent豁免
3. **path scoping** — 仅拦截 StockAnalysis/ 项目路径下的文件操作
4. **graceful override** — 紧急情况通过 `requireApproval` 让主人批准绕过
5. **self-healing** — qa-agent spawn 时自动补充强制收尾流程

---

## 2. 模块划分

### 2.1 新增文件

| 文件 | 类型 | 说明 |
|------|------|------|
| `skills/task-router-plugin/package.json` | 新增 | npm 包元数据 |
| `skills/task-router-plugin/openclaw.plugin.json` | 新增 | OpenClaw 插件 manifest |
| `skills/task-router-plugin/tsconfig.json` | 新增 | TypeScript 编译配置 |
| `skills/task-router-plugin/src/index.ts` | 新增 | 主入口：注册 before_tool_call hook |
| `skills/task-router-plugin/src/handler.ts` | 新增 | 拦截逻辑核心：工具检测、agent 身份识别 |
| `skills/task-router-plugin/src/rules.ts` | 新增 | 拦截规则定义（tool 白名单 + 路径匹配） |
| `skills/task-router-plugin/dist/index.js` | 编译输出 | 编译产物 |
| `docs/design/task-router-plugin.md` | 新增 | 本方案文档 |

### 2.2 模块职责

| 模块 | 职责 |
|------|------|
| `src/index.ts` | 插件入口，注册 `before_tool_call` 和 `subagent_spawned` hook |
| `src/handler.ts` | 核心决策逻辑：是否拦截、拦截后返回什么 |
| `src/rules.ts` | 声明式规则配置：哪些 tool 被拦截、哪些路径匹配、哪些 agent 豁免 |

---

## 3. 拦截规则详细定义

### 3.1 拦截的 tool 列表

| 工具 | 拦截条件 | 拦截原因 |
|------|---------|---------|
| `write` | path 参数匹配 `StockAnalysis/` | 写文件属于 engineering-agent 职责 |
| `edit` | path 参数匹配 `StockAnalysis/` | 改文件属于 engineering-agent 职责 |
| `apply_patch` | path 参数匹配 `StockAnalysis/` | 改文件属于 engineering-agent 职责 |
| `exec` | command 含 `>` / `>>` / `tee` 且 in 目录匹配 `StockAnalysis/` | 文件写入操作属于 engineering-agent 职责 |

### 3.2 路径匹配规则

```
StockAnalysis/ 项目路径:
  - /Users/wangyanming/workspace/StockAnalysis/   (绝对路径)
  - StockAnalysis/                                  (相对路径)
  - ./StockAnalysis/                                (带 ./ 的相对路径)

匹配逻辑:
  1. 读取 args.path / args.command
  2. 检查是否包含 StockAnalysis/ 子串
  3. 如果是，触发拦截逻辑
```

### 3.3 豁免 Agent 列表

以下 agent 调用上述工具时**不拦截**：

| Agent ID | 理由 |
|----------|------|
| `product-agent` | 负责写需求文档和原型 |
| `architecture-agent` | 负责出技术方案和审核代码 |
| `engineering-agent` | 负责编码实现 |
| `qa-agent` | 负责测试验证 |

### 3.4 身份识别逻辑

通过 `ctx.agentId` 判断：

- `ctx.agentId === "main"` → 需要拦截
- `ctx.agentId` 在豁免列表中 → 放行
- `ctx.agentId` 为其他值 → 放行（只限制 main agent）

**后备识别**：如果 `ctx.agentId` 不可靠，通过 `ctx.sessionKey` 正则匹配：

- 匹配 `^agent:main:subagent:(product|architecture|engineering|qa)-agent` → 豁免
- 匹配 `^agent:main:main` 或包含 `agent:main:subagent:` 但不匹配豁免列表 → 拦截
- 其他 → 放行

### 3.5 exec 写操作检测

```
exec 命令检测规则:
  1. 提取 command 字符串（或 command 数组拼接）
  2. 正则匹配 shell 重定向:
     - 标准重定向: `> `, `>  `, `>> `
     - heredoc: `<<`, `<<-`
     - tee: `| tee`, `| tee -a`
     - 写入重定向: `&>`, `&>>`, `1>`, `2>`, `1>>`, `2>>`
  3. 如果命中且 command 中有 StockAnalysis/ 路径 → 拦截
```

---

## 4. 核心代码架构

### 4.1 Handler 设计

```
before_tool_call(event, ctx)
    │
    ├─ 1️⃣ 工具级快速过滤
    │   if toolName not in [write, edit, apply_patch, exec]
    │       → return (放行)
    │
    ├─ 2️⃣ Agent 身份识别
    │   if ctx.agentId in EXEMPT_AGENTS
    │       → return (放行)
    │   if ctx.agentId === "main"
    │       → 继续拦截判断
    │   if sessionKey 匹配豁免子Agent
    │       → return (放行)
    │
    ├─ 3️⃣ 路径检查
    │   if toolName === "exec"
    │       → 检查 command 是否含 StockAnalysis/ + 写操作符
    │   else
    │       → 检查 path 是否以 StockAnalysis/ 开头
    │   if 不匹配 StockAnalysis/ 路径
    │       → return (放行)
    │
    ├─ 4️⃣ 拦截决策
    │   if 拦截条件成立:
    │       → return { block: true, blockReason: "..." }
    │   else:
    │       → return (放行)
```

### 4.2 subagent_spawned Hook

```
subagent_spawned(event)
    │
    ├─ if event.childAgentId === "qa-agent"
    │   ├─ 检查 event.task 末尾是否包含 "⚠️ 强制收尾流程"
    │   ├─ 如果不包含:
    │   │   ├─ 追加强制收尾流程文本到 task
    │   │   └─ 记录日志: "已自动追加强制收尾流程"
    │   └─ 如果已包含:
    │       └─ 放行
    └─ else
        └─ 放行
```

### 4.3 数据结构

```typescript
// rules.ts

// 被拦截的工具列表
const BLOCKED_TOOLS = new Set([
  'write',
  'edit',
  'apply_patch',
  'exec',
] as const);

// 豁免 Agent 列表
const EXEMPT_AGENTS = new Set([
  'product-agent',
  'architecture-agent',
  'engineering-agent',
  'qa-agent',
] as const);

// StockAnalysis 项目路径标识
const PROJECT_PATH_MARKER = 'StockAnalysis/';

// exec 写操作正则
const WRITE_OPS_REGEX = /(?:^|\s)(?:>|>>|tee|&>|&>>)\s/i;
const HEREDOC_REGEX = /<<[-]?\s*\w+/;
```

### 4.4 handler.ts 核心函数

```typescript
// handler.ts

import type { BeforeToolCallEvent, BeforeToolCallResult } from 'openclaw/plugin-sdk/hooks';
import { BLOCKED_TOOLS, EXEMPT_AGENTS, PROJECT_PATH_MARKER, WRITE_OPS_REGEX, HEREDOC_REGEX } from './rules';

export function shouldBlock(event: BeforeToolCallEvent): BeforeToolCallResult | null {
  // 1. 工具过滤
  if (!BLOCKED_TOOLS.has(event.toolName)) {
    return null;
  }

  // 2. Agent 身份识别
  if (isExemptAgent(event.context)) {
    return null;
  }

  // 3. 路径检查
  if (!matchesProjectPath(event)) {
    return null;
  }

  // 4. exec 特殊检查：仅拦截含写操作的 exec
  if (event.toolName === 'exec' && !hasWriteOperation(event)) {
    return null;
  }

  // 5. 返回拦截
  return {
    block: true,
    blockReason: getBlockReason(event.toolName, event.params),
  };
}

function isExemptAgent(ctx: any): boolean {
  const agentId = ctx?.agentId;
  if (EXEMPT_AGENTS.has(agentId)) return true;

  const sessionKey = ctx?.sessionKey ?? '';
  return EXEMPT_AGENTS.has(detectAgentFromSessionKey(sessionKey));
}

function matchesProjectPath(event: BeforeToolCallEvent): boolean {
  const path = extractTargetPath(event);
  return path?.includes(PROJECT_PATH_MARKER) ?? false;
}

function hasWriteOperation(event: BeforeToolCallEvent): boolean {
  const cmd = String(event.params?.command ?? event.params?.cmd ?? '');
  return WRITE_OPS_REGEX.test(cmd) || HEREDOC_REGEX.test(cmd);
}
```

---

## 5. Agent 身份识别逻辑

### 5.1 识别来源

| 来源 | 字段 | 示例值 |
|------|------|--------|
| ctx.agentId | `ctx.agentId` | `"main"`, `"engineering-agent"` |
| ctx.sessionKey | `ctx.sessionKey` | `"agent:main:subagent:engineering-agent:uuid"` |
| ctx.sessionId | `ctx.sessionId` | `"agent:engineering-agent:subagent:uuid..."` |

### 5.2 决策树

```
ctx.agentId 存在?
  ├─ Yes → 在 EXEMPT_AGENTS 中? → Yes → 放行
  │                                   No  → 继续
  ├─ No  → 继续

ctx.sessionKey 存在?
  ├─ Yes → 解析 agentId from sessionKey:
  │         pattern: agent:{parent}:subagent:{agentId}:{uuid}
  │         → 提取 {agentId} 段
  │         → 在 EXEMPT_AGENTS 中? → Yes → 放行
  │                                   No  → 继续
  ├─ No  → 继续
  │
  继续 = 需要拦截判断 (main agent 或未知 agent)
```

### 5.3 安全性考虑

- `ctx.agentId` 由 OpenClaw 运行时注入，子Agent无法伪造
- `ctx.sessionKey` 也是运行时自动生成，不可篡改
- 无需自行实现 Agent 身份验证

---

## 6. 异常处理

### 6.1 逃逸路径分析

| 逃逸路径 | 风险等级 | 说明 | 缓解措施 |
|---------|---------|------|---------|
| `exec` 使用内置重定向（python3 with open） | 中 | 通过 `python3 -c "open('file','w')"` 可绕过 shell 重定向检测 | `exec` 的 command 包含 `.py` + `StockAnalysis/` + `open\|write` 时触发 requireApproval |
| `exec` 通过 subprocess 调用外部脚本 | 低 | 同样属于写操作，但较难自动检测 | 建议在 SKILL.md 中声明，通过 requireApproval 兜底 |
| include file 操作 | 低 | `exec` 的 `include` 参数可能包含文件内容 | 非 StockAnalysis/ 路径的文件 include 不受限；项目文件需主人确认 |
| 用 `message` 传递文件内容 | 低 | Agent 可能把文件内容拼接进 message 绕过 | message 不是文件写入；不拦截。工程规范约束 |
| 通过 `exec` 运行 git 命令 | 低 | `git add` / `git commit` 不产生新文件写入 | 不属于拦截目标；git 操作单独规范约 |
| `exec` 含 `sed -i` / `perl -i` / `cp` | 中 | 就地编辑或复制文件 | 检测 `-i` 标志和 `cp` 目标路径含 StockAnalysis/ → requireApproval |
| llama_index / 其他库内部写文件 | 高 | 库内部可能有缓存/持久化写操作 | 无法在 tool 层检测。建议在 agent-level prompt 中约束 |

### 6.2 异常处理策略汇总

| 场景 | 处理方式 |
|------|---------|
| 正常拦截（main 写文件） | `{ block: true, blockReason: "请通过 sessions_spawn 委托对应子 Agent 执行" }` |
| exec 写操作模糊匹配 | 不直接拦截，改为 `requireApproval` 让主人判断 |
| 路径解析失败 | 放行（fail-open）；记录 warn 日志 |
| agent 身份无法识别 | 放行（fail-open）；记录 warn 日志 |
| subagent_spawned 事件处理失败 | 不影响 spawn；记录 error 日志 |
| 规则文件加载失败 | 插件启动时 throw，防止规则不生效 |

### 6.3 include file 逃逸深入分析

`exec` 的 `include` 参数允许把文件内容注入到命令中执行：

```
exec:
  command: python3 -c "import sys; eval(sys.argv[1])" {include:path/to/file}
```

或者通过模板注入：

```
exec:
  command: python3 -c "
    import subprocess
    subprocess.run(['python3', '{include:StockAnalysis/new_module.py}'])
  "
```

**缓解方案**：当 `include` 的文件路径包含 `StockAnalysis/` 时，在 `before_tool_call` 中检查 `event.params.include` 是否指向项目路径。但 OpenClaw 的 `include` 机制在传入路径参数之前已经解析完毕，插件层的拦截时机是 tool 被选中后、execute 之前。

**建议**：
1. 在 main agent 的 system prompt 中声明：「你的 `exec` 指令将被检查，不得 include StockAnalysis 下的文件」
2. 插件层尽最大努力检查 `event.params` 中的 `include` 字段
3. `include` 路径包含 `StockAnalysis/` → `requireApproval`

---

## 7. requireApproval 兜底策略

### 7.1 触发条件

| 条件 | 行为 |
|------|------|
| main agent 试图写文件但规则可明确判定拦截 | `{ block: true, blockReason }` |
| main agent 试图写文件但规则模糊（exec 写操作检测、include 检测等） | `{ requireApproval }` |
| 主人明确要求 main agent 直接操作 | `requireApproval` 让主人点 /approve |

### 7.2 requireApproval 配置

```typescript
{
  requireApproval: {
    title: 'Main agent 直接写操作',
    description: `Main agent (${agentId}) 试图通过 ${toolName} 操作文件: ${path}`,
    severity: 'warning',
    timeoutMs: 120_000,
    timeoutBehavior: 'deny',
    allowedDecisions: ['allow-once', 'deny'],
    pluginId: 'task-router',
    onResolution: async (decision) => {
      logger.info(`task-router approval resolved: ${decision}`);
    },
  }
}
```

### 7.3 优先级规则

1. `block: true` 优先级高于 `requireApproval`
2. 如果多个 `before_tool_call` handler 存在，高 priority 先执行
3. 本插件使用 `priority: 100`（较高），确保在常规 policy handler 之前执行
4. 如果其他插件已返回 `block: true`，本插件不再处理

### 7.4 使用场景

- 开发调试：主人临时需要 main 直接改文件
- 紧急修复：线上问题需要 main 立即介入
- 边界情况：插件规则无法准确判断 exec 是否写文件

---

## 8. 测试方案

### 8.1 单元测试

| 测试用例 | 输入 | 预期输出 |
|---------|------|---------|
| main agent 调用 write 写 StockAnalysis/ 文件 | toolName=write, params={path: "...StockAnalysis/xxx.py"}, ctx.agentId="main" | block: true |
| engineering-agent 调用 write | toolName=write, params={path: "...StockAnalysis/xxx.py"}, ctx.agentId="engineering-agent" | null (放行) |
| main agent 读文件 | toolName=read, params={path: "...StockAnalysis/xxx.py"}, ctx.agentId="main" | null (放行) |
| main agent exec 无写操作 | toolName=exec, params={command: "ls -la StockAnalysis/"}, ctx.agentId="main" | null (放行) |
| main agent exec 含重定向 | toolName=exec, params={command: "echo x > StockAnalysis/test.txt"}, ctx.agentId="main" | block: true |
| main agent exec 含 tee | toolName=exec, params={command: "python3 test.py | tee StockAnalysis/log.txt"}, ctx.agentId="main" | block: true |
| unknown agent (非main、非豁免) | toolName=write, params={path: "StockAnalysis/x.py"}, ctx.agentId="helper" | null (放行) |
| main agent 写非项目路径 | toolName=write, params={path: "/tmp/test.txt"}, ctx.agentId="main" | null (放行) |
| qa-agent spawn 含强制收尾 | childAgentId="qa-agent", task="...已含⚠️ 强制收尾流程" | 不追加 |
| qa-agent spawn 不含强制收尾 | childAgentId="qa-agent", task="验证下功能" | 自动追加 |
| 非 qa-agent spawn | childAgentId="engineering-agent", task="写个模块" | 不追加 |

### 8.2 集成测试

| 测试场景 | 步骤 | 预期 |
|---------|------|------|
| main 尝试 edit StockAnalysis/file.py | main 调用 edit | 被拦截，提示委派 |
| main 通过 sessions_spawn 委托 engineering-agent | main spawn engineering-agent | 不被拦截 |
| 紧急 bypass | main 触发 requireApproval → 主人 /approve | 文件被写入 |
| 路径边界 | main 写 StockAnalysis/../../tmp/file | 路径含 StockAnalysis/ 被拦截 |

### 8.3 测试工具

- 使用 `vitest` 作为测试框架（与 OpenClaw 插件推荐一致）
- `src/__tests__/handler.test.ts`：handler 逻辑纯函数测试
- `src/__tests__/rules.test.ts`：规则匹配测试
- 模拟 `BeforeToolCallEvent` 对象覆盖各种场景

---

## 9. 安装与配置

### 9.1 安装方式

```bash
# clone 或下载到 StockAnalysis/skills/task-router-plugin/
cd skills/task-router-plugin/

# 安装依赖
npm install

# 编译 TypeScript
npm run build

# 通过本地路径安装到 OpenClaw
cd ~/workspace/StockAnalysis
openclaw plugins install --link ./skills/task-router-plugin

# 重启 Gateway
openclaw gateway restart

# 验证
openclaw plugins inspect task-router --runtime --json
```

### 9.2 配置文件

```json5
// openclaw.json (in user config)
{
  plugins: {
    allow: ["task-router"],
    entries: {
      "task-router": {
        enabled: true,
        config: {
          // 被拦截的项目路径标识
          projectPathMarker: "StockAnalysis/",
          // 豁免 agent 列表
          exemptAgents: [
            "product-agent",
            "architecture-agent",
            "engineering-agent",
            "qa-agent"
          ],
          // requireApproval 超时（毫秒）
          approvalTimeoutMs: 120_000,
        }
      }
    }
  }
}
```

---

## 10. 安全风险分析

### 10.1 风险矩阵

| 风险 | 概率 | 影响 | 等级 | 缓解措施 |
|------|------|------|------|---------|
| agent 身份伪造 | 低 | 高 | 中 | ctx.agentId 由运行时注入，不可伪造 |
| 规则绕过（库内部写文件） | 中 | 中 | 中 | requireApproval 兜底 + System Prompt 约束 |
| 规则过于严格影响效率 | 中 | 低 | 低 | exemptAgents 可配置，requireApproval 可兜底 |
| exec 写操作漏检 | 中 | 中 | 中 | 正则精确匹配 + requireApproval 模糊场景 |
| 插件自身存在漏洞 | 低 | 高 | 中 | 单元测试覆盖 + Code Review |
| subagent_spawned handler 异常 | 低 | 低 | 低 | 不影响 spawn，仅记录日志 |

### 10.2 与 OpenClaw 安全模型的关系

- 本插件运行在 OpenClaw 插件沙箱中，与 Gateway 同进程
- 使用 `before_tool_call` hook 拦截，该 hook 的 `block: true` 是本插件的安全边界
- 不依赖额外的文件系统或网络权限
- 日志通过 `api.logger` 输出，遵循 OpenClaw 日志规范

### 10.3 回滚策略

1. 关闭插件：`openclaw config set plugins.entries.task-router.enabled false`
2. 重启 Gateway：`openclaw gateway restart`
3. 恢复后：main agent 恢复所有工具权限

---

## 11. 附录

### 11.1 插件 manifest

```json
{
  "id": "task-router",
  "name": "Task Router",
  "description": "Enforces Multi-Agent workflow: blocks main agent from writing files directly",
  "version": "1.0.0",
  "activation": {
    "onStartup": true
  },
  "configSchema": {
    "type": "object",
    "properties": {
      "projectPathMarker": {
        "type": "string",
        "default": "StockAnalysis/",
        "description": "Project path marker to detect file operations"
      },
      "exemptAgents": {
        "type": "array",
        "items": { "type": "string" },
        "default": ["product-agent", "architecture-agent", "engineering-agent", "qa-agent"],
        "description": "Agent IDs exempt from tool blocking"
      },
      "approvalTimeoutMs": {
        "type": "number",
        "default": 120000,
        "description": "Timeout for requireApproval prompts in milliseconds"
      }
    },
    "additionalProperties": false
  },
  "contracts": {
    "hooks": ["before_tool_call", "subagent_spawned"]
  }
}
```

### 11.2 与现有项目的关系

本插件独立于 StockAnalysis 的业务代码，仅作为 OpenClaw 插件运行：

- **不依赖** StockAnalysis 内任何 Python 代码或数据库
- **不影响** 现有业务逻辑、API 接口、测试流程
- **不影响** 现有 OpenClaw skills（engineering-rules、qa-subagent 等）
- **互不冲突** 与现有的 `before_tool_call` hook 插件可共存（通过 priority 排序）

### 11.3 依赖关系

```
openclaw (peer dependency, >=2026.3.24-beta.2)
├── plugin-sdk/plugin-entry (definePluginEntry)
├── plugin-sdk/hooks (BeforeToolCallEvent types)
└── typebox (Type validation schemas)
```

无其他外部依赖，保持轻量。
