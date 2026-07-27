/**
 * index.ts — task-router-plugin 主入口
 *
 * 使用 definePluginEntry 注册插件。
 *
 * 注册的 Hook:
 *   - before_tool_call (priority: 100): 拦截 main agent 直接写文件
 *   - subagent_spawned: 自动检查 qa-agent task 是否包含强制收尾流程
 *
 * 插件配置:
 *   - projectPathMarker: 项目路径标识（默认 "StockAnalysis/"）
 *   - exemptAgents: 豁免 Agent 列表
 *   - approvalTimeoutMs: requireApproval 超时（毫秒）
 */

import { definePluginEntry } from 'openclaw/plugin-sdk/plugin-entry';

import type {
  PluginHookBeforeToolCallEvent,
  PluginHookBeforeToolCallResult,
  PluginHookToolContext,
  PluginHookSubagentSpawnedEvent,
  PluginHookSubagentContext,
} from 'openclaw/plugin-sdk/types';

import { shouldBlock } from './handler.js';

// ── 类型辅助 ──
// OpenClawPluginApi 没有声明 `.on()` 方法，但运行时在 full 模式下提供。
// 这里使用宽松的 any 签名安全访问，运行时调用 registerTypedHook。


export default definePluginEntry({
  id: 'task-router',
  name: 'Task Router',
  description: 'Enforces Multi-Agent workflow: blocks main agent from writing files directly',

  register(api) {
    const logger = api.logger;

    // ── 获取插件配置 ──
    const pluginConfig = api.pluginConfig as Record<string, unknown> | undefined;

    logger.info(`[task-router] 初始化完成, projectPathMarker=${(pluginConfig?.projectPathMarker as string) ?? 'StockAnalysis/'}`);

    // 通过类型断言访问 `.on()`（Runtime 提供，TS 类型不包含）
    // 运行时提供了 `on(hookName, handler, opts?)` 方法注册 typed hooks。
    const apiOn = (api as { on: Function }).on;

    // ── 注册 before_tool_call hook ──
    apiOn(
      'before_tool_call',
      (
        event: PluginHookBeforeToolCallEvent,
        ctx: PluginHookToolContext,
      ): PluginHookBeforeToolCallResult | void => {
        try {
          const result = shouldBlock(event, ctx);

          if (result?.block) {
            logger.warn(
              `[task-router] 拦截: agent=${ctx.agentId ?? 'unknown'}, tool=${event.toolName}, ` +
              `params=${JSON.stringify(event.params)}, reason=${result.blockReason}`,
            );
          } else if (result?.requireApproval) {
            logger.info(
              `[task-router] requireApproval: agent=${ctx.agentId ?? 'unknown'}, tool=${event.toolName}, ` +
              `description=${result.requireApproval.description}`,
            );
          }

          return result ?? undefined;
        } catch (err) {
          logger.error(`[task-router] shouldBlock 异常: ${err}`);
          return undefined;
        }
      },
      {
        priority: 100,
        name: 'task-router.before_tool_call',
        description: '拦截 main agent 直接写文件操作，强制委托子 Agent 执行',
      },
    );

    // ── 注册 subagent_spawned hook ──
    apiOn(
      'subagent_spawned',
      (
        event: PluginHookSubagentSpawnedEvent,
        // eslint-disable-next-line @typescript-eslint/no-unused-vars
        _ctx: PluginHookSubagentContext,
      ): void => {
        try {
          if (event.agentId === 'qa-agent') {
            // 注: subagent_spawned 事件是只读的 (post-launch 观察),
            // 无法修改 task 内容。此处仅记录日志作为提醒。
            logger.info(
              `[task-router] qa-agent 已启动 (sessionKey=${event.childSessionKey})。` +
              `注意: 请确保 task 中包含 "⚠️ 强制收尾流程"`,
            );
          }
        } catch (err) {
          logger.error(`[task-router] subagent_spawned handler 异常: ${err}`);
        }
      },
      {
        name: 'task-router.subagent_spawned',
        description: '自动检查 qa-agent task 是否包含强制收尾流程',
      },
    );

    logger.info('[task-router] hook 注册完成');
  },
});
