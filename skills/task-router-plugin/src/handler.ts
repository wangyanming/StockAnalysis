/**
 * handler.ts — 拦截逻辑核心
 *
 * 核心函数 shouldBlock(event) 决定是否拦截工具调用。
 *
 * 流程：
 *   1. 工具级快速过滤（非 BLOCKED_TOOLS 放行）
 *   2. Agent 身份识别（豁免 Agent 放行）
 *   3. 路径检查（不匹配 StockAnalysis/ 放行）
 *   4. exec 写操作检测（无写操作符的 exec 放行）
 *   5. 返回拦截结果（block / requireApproval）
 */

import type {
  PluginHookBeforeToolCallEvent,
  PluginHookBeforeToolCallResult,
  PluginHookToolContext,
} from 'openclaw/plugin-sdk/types';

import {
  BLOCKED_TOOLS,
  EXEMPT_AGENTS,
  PROJECT_PATH_MARKER,
  WRITE_OPS_REGEX,
  HEREDOC_REGEX,
} from './rules.js';

/**
 * 判断是否需要拦截当前工具调用。
 *
 * @param event  工具调用事件
 * @param ctx    工具调用上下文（含 agentId、sessionKey 等）
 * @returns 拦截结果（block / requireApproval）或 undefined（放行）
 */
export function shouldBlock(
  event: PluginHookBeforeToolCallEvent,
  ctx: PluginHookToolContext,
): PluginHookBeforeToolCallResult | undefined {
  // 1. 工具级快速过滤
  if (!BLOCKED_TOOLS.has(event.toolName)) {
    return undefined;
  }

  // 2. Agent 身份识别
  if (isExemptAgent(ctx)) {
    return undefined;
  }

  // 3. 路径检查 - 只拦截 StockAnalysis/ 路径下的操作
  if (!matchesProjectPath(event)) {
    return undefined;
  }

  // 4. exec 特殊检查
  if (event.toolName === 'exec') {
    const cmd = extractCommand(event);
    if (!cmd) {
      // 无法解析命令，保守放行并记录日志
      return undefined;
    }

    if (hasWriteOperation(cmd)) {
      // exec 含重定向写操作 → 拦截
      return {
        block: true,
        blockReason: `Main agent 不能直接通过 exec 写文件到 StockAnalysis/ 项目路径。请通过 sessions_spawn 委托 engineering-agent 执行。`,
      };
    }

    if (hasInlineEditOperation(cmd)) {
      // exec 含 sed -i / cp 到 StockAnalysis/ → requireApproval（模糊判定）
      return buildRequireApproval(event, ctx, 'exec 包含就地编辑操作 (sed -i / cp)');
    }

    if (hasIncludeProjectFile(event)) {
      // exec 的 include 参数包含 StockAnalysis/ → requireApproval
      return buildRequireApproval(event, ctx, 'exec include 参数包含 StockAnalysis/ 路径');
    }

    // exec 不含写操作 → 放行
    return undefined;
  }

  // 5. write / edit → 明确拦截
  return {
    block: true,
    blockReason: `Main agent 不能直接使用 ${event.toolName} 操作 StockAnalysis/ 项目路径下的文件。请通过 sessions_spawn 委托对应子 Agent 执行。`,
  };
}

// ─── 辅助函数 ──────────────────────────────────────────────

/**
 * 判断 agent 是否在豁免列表中。
 * 通过 ctx.agentId 和 ctx.sessionKey 双重判断。
 */
function isExemptAgent(ctx: PluginHookToolContext): boolean {
  // 直接匹配 agentId
  if (ctx.agentId && EXEMPT_AGENTS.has(ctx.agentId)) {
    return true;
  }

  // 从 sessionKey 解析 agent 身份
  // sessionKey 格式: agent:{parent}:subagent:{agentId}:{uuid}
  if (ctx.sessionKey) {
    const agentId = detectAgentFromSessionKey(ctx.sessionKey);
    if (agentId && EXEMPT_AGENTS.has(agentId)) {
      return true;
    }
  }

  return false;
}

/**
 * 从 sessionKey 中提取 agentId。
 *
 * sessionKey 格式示例：
 *   "agent:main:subagent:engineering-agent:uuid-string"
 *   "agent:engineering-agent:subagent:..."
 */
function detectAgentFromSessionKey(sessionKey: string): string | null {
  // 匹配格式: agent:{parent}:subagent:{agentId}:...
  const match = sessionKey.match(/^agent:[^:]+:subagent:([^:]+):/);
  if (match) {
    return match[1];
  }
  return null;
}

/**
 * 检查事件参数是否包含 StockAnalysis/ 路径。
 */
function matchesProjectPath(event: PluginHookBeforeToolCallEvent): boolean {
  const path = extractTargetPath(event);
  if (!path) return false;
  return path.includes(PROJECT_PATH_MARKER);
}

/**
 * 从事件参数中提取目标路径。
 */
function extractTargetPath(event: PluginHookBeforeToolCallEvent): string | null {
  const params = event.params;
  if (!params) return null;

  // write / edit 的 path 参数
  if (typeof params.path === 'string') {
    return params.path;
  }

  // exec 的 command 参数
  if (typeof params.command === 'string') {
    return params.command;
  }

  // exec 的 cmd 参数（部分场景）
  if (typeof params.cmd === 'string') {
    return params.cmd;
  }

  return null;
}

/**
 * 从 exec 事件中提取命令字符串。
 */
function extractCommand(event: PluginHookBeforeToolCallEvent): string | null {
  const params = event.params;
  if (!params) return null;

  if (typeof params.command === 'string') {
    return params.command;
  }

  if (typeof params.cmd === 'string') {
    return params.cmd;
  }

  // 如果 command 是数组，拼接成字符串
  if (Array.isArray(params.command)) {
    return params.command.join(' ');
  }

  return null;
}

/**
 * 检测 exec 命令是否包含写操作（重定向、tee 等）。
 */
function hasWriteOperation(cmd: string): boolean {
  return WRITE_OPS_REGEX.test(cmd) || HEREDOC_REGEX.test(cmd);
}

/**
 * 检测 exec 命令是否包含就地编辑操作（sed -i / cp 等）。
 */
function hasInlineEditOperation(cmd: string): boolean {
  // sed -i 就地编辑
  if (/\bsed\b.*\s+-i\b/.test(cmd)) return true;

  // cp 到 StockAnalysis/ 目录
  if (/\bcp\b/.test(cmd) && cmd.includes(PROJECT_PATH_MARKER)) return true;

  // perl -i 就地编辑
  if (/\bperl\b.*\s+-i\b/.test(cmd)) return true;

  // mv 到 StockAnalysis/ 目录
  if (/\bmv\b/.test(cmd) && cmd.includes(PROJECT_PATH_MARKER)) return true;

  return false;
}

/**
 * 检查 exec 的 include 参数是否指向 StockAnalysis/ 路径。
 */
function hasIncludeProjectFile(event: PluginHookBeforeToolCallEvent): boolean {
  const params = event.params;
  if (!params) return false;

  // 检查 include 字段
  const includeVal = params.include;
  if (typeof includeVal === 'string' && includeVal.includes(PROJECT_PATH_MARKER)) {
    return true;
  }

  // 检查 quoted 字符串中的 {include:...} 模板
  const cmd = extractCommand(event);
  if (cmd && /\{include:([^}]+)\}/.test(cmd)) {
    const match = cmd.match(/\{include:([^}]+)\}/);
    if (match && match[1].includes(PROJECT_PATH_MARKER)) {
      return true;
    }
  }

  return false;
}

/**
 * 构建 requireApproval 结果。
 */
function buildRequireApproval(
  event: PluginHookBeforeToolCallEvent,
  ctx: PluginHookToolContext,
  reason: string,
): PluginHookBeforeToolCallResult {
  return {
    requireApproval: {
      title: 'Main agent 直接写操作',
      description: `Main agent (${ctx.agentId ?? 'unknown'}) 试图通过 ${event.toolName} 操作文件: ${extractTargetPath(event)}, 原因: ${reason}`,
      severity: 'warning',
      timeoutMs: 120_000,
      timeoutBehavior: 'deny',
      allowedDecisions: ['allow-once', 'deny'],
      pluginId: 'task-router',
    },
  };
}
