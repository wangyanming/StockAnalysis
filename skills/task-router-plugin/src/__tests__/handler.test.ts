/**
 * handler.test.ts — shouldBlock 函数单元测试
 *
 * 覆盖场景：
 *   - 工具级快速过滤（read / 非 BLOCKED_TOOLS）
 *   - Agent 身份识别（main vs 豁免 agent）
 *   - 路径检查（StockAnalysis/ 匹配）
 *   - exec 写操作检测（> / >> / tee / heredoc）
 *   - exec 就地编辑检测（sed -i / cp / mv）
 *   - include 参数检测
 *   - apply_patch 不再拦截（P0 修复验证）
 *   - requireApproval 场景
 */

import { describe, it, expect } from 'vitest';

// 由于 handler.ts 使用的是 ESM import 路径（import from './rules.js'），
// 我们直接测试 shouldBlock 函数。vitest 会自动处理 ESM 转换。
import { shouldBlock } from '../handler.js';

import type {
  PluginHookBeforeToolCallEvent,
  PluginHookBeforeToolCallResult,
  PluginHookToolContext,
} from 'openclaw/plugin-sdk/types';

// ── 辅助：创建事件对象 ──

function makeEvent(overrides: Partial<PluginHookBeforeToolCallEvent> = {}): PluginHookBeforeToolCallEvent {
  return {
    toolName: 'write',
    params: { path: '/Users/wangyanming/workspace/StockAnalysis/test.py' },
    runId: 'test-run-1',
    ...overrides,
  } as PluginHookBeforeToolCallEvent;
}

function makeContext(overrides: Partial<PluginHookToolContext> = {}): PluginHookToolContext {
  return {
    agentId: 'main',
    sessionKey: 'agent:main:main:uuid',
    ...overrides,
  } as unknown as PluginHookToolContext;
}

// ── 测试套件 ──

describe('shouldBlock', () => {
  // ── 测试 1: main agent write .py 文件 ──
  it('1. main agent write .py 文件 → block: true', () => {
    const event = makeEvent({
      toolName: 'write',
      params: { path: '/Users/wangyanming/workspace/StockAnalysis/xxx.py' },
    });
    const ctx = makeContext({ agentId: 'main' });
    const result = shouldBlock(event, ctx);
    expect(result).toBeDefined();
    expect(result).toHaveProperty('block', true);
    expect(result?.blockReason).toContain('Main agent');
  });

  // ── 测试 2: main agent edit .md 文件 ──
  it('2. main agent edit .md 文件 → block: true', () => {
    const event = makeEvent({
      toolName: 'edit',
      params: { path: '/Users/wangyanming/workspace/StockAnalysis/README.md' },
    });
    const ctx = makeContext({ agentId: 'main' });
    const result = shouldBlock(event, ctx);
    expect(result).toBeDefined();
    expect(result).toHaveProperty('block', true);
    expect(result?.blockReason).toContain('Main agent');
  });

  // ── 测试 3: engineering-agent write 文件 → 放行 ──
  it('3. engineering-agent write 文件 → undefined（放行）', () => {
    const event = makeEvent({
      toolName: 'write',
      params: { path: '/Users/wangyanming/workspace/StockAnalysis/xxx.py' },
    });
    const ctx = makeContext({ agentId: 'engineering-agent', sessionKey: 'agent:main:subagent:engineering-agent:uuid' });
    const result = shouldBlock(event, ctx);
    expect(result).toBeUndefined();
  });

  // ── 测试 4: qa-agent write 文件 → 放行 ──
  it('4. qa-agent write 文件 → undefined（放行）', () => {
    const event = makeEvent({
      toolName: 'write',
      params: { path: '/Users/wangyanming/workspace/StockAnalysis/xxx.py' },
    });
    const ctx = makeContext({ agentId: 'qa-agent', sessionKey: 'agent:main:subagent:qa-agent:uuid' });
    const result = shouldBlock(event, ctx);
    expect(result).toBeUndefined();
  });

  // ── 测试 5: main agent read 文件 → 放行（read 不在 BLOCKED_TOOLS 中） ──
  it('5. main agent read 文件 → undefined（放行）', () => {
    const event = makeEvent({
      toolName: 'read',
      params: { path: '/Users/wangyanming/workspace/StockAnalysis/xxx.py' },
    });
    const ctx = makeContext({ agentId: 'main' });
    const result = shouldBlock(event, ctx);
    expect(result).toBeUndefined();
  });

  // ── 测试 6: main agent exec 无写操作 → 放行 ──
  it('6. main agent exec 无写操作 → undefined（放行）', () => {
    const event = makeEvent({
      toolName: 'exec',
      params: { command: 'ls -la StockAnalysis/' },
    });
    const ctx = makeContext({ agentId: 'main' });
    const result = shouldBlock(event, ctx);
    expect(result).toBeUndefined();
  });

  // ── 测试 7: main agent exec 含 > 重定向 → block ──
  it('7. main agent exec 含 > 重定向 → block: true', () => {
    const event = makeEvent({
      toolName: 'exec',
      params: { command: 'echo x > StockAnalysis/x.txt' },
    });
    const ctx = makeContext({ agentId: 'main' });
    const result = shouldBlock(event, ctx);
    expect(result).toBeDefined();
    expect(result).toHaveProperty('block', true);
  });

  // ── 测试 8: main agent exec 含 >> 追加 → block ──
  it('8. main agent exec 含 >> 追加 → block: true', () => {
    const event = makeEvent({
      toolName: 'exec',
      params: { command: 'echo x >> StockAnalysis/x.txt' },
    });
    const ctx = makeContext({ agentId: 'main' });
    const result = shouldBlock(event, ctx);
    expect(result).toBeDefined();
    expect(result).toHaveProperty('block', true);
  });

  // ── 测试 9: main agent exec 含 tee → block ──
  it('9. main agent exec 含 tee → block: true', () => {
    const event = makeEvent({
      toolName: 'exec',
      params: { command: 'cmd | tee StockAnalysis/log.txt' },
    });
    const ctx = makeContext({ agentId: 'main' });
    const result = shouldBlock(event, ctx);
    expect(result).toBeDefined();
    expect(result).toHaveProperty('block', true);
  });

  // ── 测试 10: main agent 写非项目路径 → 放行 ──
  it('10. main agent 写非项目路径 → undefined（放行）', () => {
    const event = makeEvent({
      toolName: 'write',
      params: { path: '/tmp/test.txt' },
    });
    const ctx = makeContext({ agentId: 'main' });
    const result = shouldBlock(event, ctx);
    expect(result).toBeUndefined();
  });

  // ── 测试 11: apply_patch 不再拦截（P0 修复验证） ──
  it('11. apply_patch 不再拦截 → undefined（放行）', () => {
    const event = makeEvent({
      toolName: 'apply_patch',
      params: { input: '--- a/StockAnalysis/xxx.py\n+++ b/StockAnalysis/xxx.py' },
    });
    const ctx = makeContext({ agentId: 'main' });
    const result = shouldBlock(event, ctx);
    // apply_patch 不在 BLOCKED_TOOLS 中 → 立即放行
    expect(result).toBeUndefined();
  });

  // ── 测试 12: main agent exec sed -i → requireApproval ──
  it('12. main agent exec sed -i → requireApproval', () => {
    const event = makeEvent({
      toolName: 'exec',
      params: { command: "sed -i 's/a/b/g' StockAnalysis/x.py" },
    });
    const ctx = makeContext({ agentId: 'main' });
    const result = shouldBlock(event, ctx);
    expect(result).toBeDefined();
    expect(result).toHaveProperty('requireApproval');
    expect(result?.requireApproval?.title).toBe('Main agent 直接写操作');
    expect(result?.requireApproval?.allowedDecisions).toContain('allow-once');
  });

  // ── 额外测试覆盖 ──

  // 测试 13: main agent exec 含 heredoc → block
  it('13. main agent exec 含 heredoc → block: true', () => {
    const event = makeEvent({
      toolName: 'exec',
      params: { command: 'cat << EOF > StockAnalysis/x.py' },
    });
    const ctx = makeContext({ agentId: 'main' });
    const result = shouldBlock(event, ctx);
    expect(result).toBeDefined();
    expect(result).toHaveProperty('block', true);
  });

  // 测试 14: main agent exec mv 到 StockAnalysis/ → requireApproval
  it('14. main agent exec mv 到 StockAnalysis/ → requireApproval', () => {
    const event = makeEvent({
      toolName: 'exec',
      params: { command: 'mv /tmp/tmp.py StockAnalysis/new_module.py' },
    });
    const ctx = makeContext({ agentId: 'main' });
    const result = shouldBlock(event, ctx);
    expect(result).toBeDefined();
    expect(result).toHaveProperty('requireApproval');
  });

  // 测试 15: main agent exec cp 到 StockAnalysis/ → requireApproval
  it('15. main agent exec cp 到 StockAnalysis/ → requireApproval', () => {
    const event = makeEvent({
      toolName: 'exec',
      params: { command: 'cp /tmp/backup.py StockAnalysis/src/main.py' },
    });
    const ctx = makeContext({ agentId: 'main' });
    const result = shouldBlock(event, ctx);
    expect(result).toBeDefined();
    expect(result).toHaveProperty('requireApproval');
  });

  // 测试 16: exec 含 &> 重定向 → block
  it('16. exec 含 &> 重定向 → block: true', () => {
    const event = makeEvent({
      toolName: 'exec',
      params: { command: 'python3 run.py &> StockAnalysis/build.log' },
    });
    const ctx = makeContext({ agentId: 'main' });
    const result = shouldBlock(event, ctx);
    expect(result).toBeDefined();
    expect(result).toHaveProperty('block', true);
  });

  // 测试 17: exec 含 &>> 追加重定向 → block
  it('17. exec 含 &>> 追加重定向 → block: true', () => {
    const event = makeEvent({
      toolName: 'exec',
      params: { command: 'python3 run.py &>> StockAnalysis/build.log' },
    });
    const ctx = makeContext({ agentId: 'main' });
    const result = shouldBlock(event, ctx);
    expect(result).toBeDefined();
    expect(result).toHaveProperty('block', true);
  });

  // 测试 18: product-agent 写文件 → 放行（豁免 agent）
  it('18. product-agent write 文件 → undefined（放行）', () => {
    const event = makeEvent({
      toolName: 'write',
      params: { path: '/Users/wangyanming/workspace/StockAnalysis/docs/requirements.md' },
    });
    const ctx = makeContext({ agentId: 'product-agent' });
    const result = shouldBlock(event, ctx);
    expect(result).toBeUndefined();
  });

  // 测试 19: architecture-agent write 文件 → 放行
  it('19. architecture-agent write 文件 → undefined（放行）', () => {
    const event = makeEvent({
      toolName: 'write',
      params: { path: '/Users/wangyanming/workspace/StockAnalysis/docs/design/arch.md' },
    });
    const ctx = makeContext({ agentId: 'architecture-agent' });
    const result = shouldBlock(event, ctx);
    expect(result).toBeUndefined();
  });

  // 测试 20: main agent exec 通过 cmd 参数传递（非 command）→ 放行（无写操作）
  it('20. main agent exec (cmd 参数) 无写操作 → undefined', () => {
    const event = makeEvent({
      toolName: 'exec',
      params: { cmd: 'ls -la StockAnalysis/' },
    });
    const ctx = makeContext({ agentId: 'main' });
    const result = shouldBlock(event, ctx);
    expect(result).toBeUndefined();
  });

  // 测试 21: main agent exec command 含 tee -a → block
  it('21. main agent exec 含 tee -a → block: true', () => {
    const event = makeEvent({
      toolName: 'exec',
      params: { command: 'python3 test.py | tee -a StockAnalysis/output.log' },
    });
    const ctx = makeContext({ agentId: 'main' });
    const result = shouldBlock(event, ctx);
    expect(result).toBeDefined();
    expect(result).toHaveProperty('block', true);
  });

  // 测试 22: 通过 sessionKey 识别的豁免 agent（非 agentId）
  it('22. 通过 sessionKey 识别的豁免 agent → undefined（放行）', () => {
    const event = makeEvent({
      toolName: 'write',
      params: { path: '/Users/wangyanming/workspace/StockAnalysis/xxx.py' },
    });
    // sessionKey 包含 engineering-agent 但 agentId 可能是其他值
    const ctx= makeContext({
      agentId: 'unknown-agent-id',
      sessionKey: 'agent:main:subagent:engineering-agent:uuid-abc',
    });
    const result = shouldBlock(event, ctx);
    expect(result).toBeUndefined();
  });

  // 测试 23: 相对路径 StockAnalysis/ → 匹配
  it('23. main agent write 相对路径 StockAnalysis/ → block: true', () => {
    const event = makeEvent({
      toolName: 'write',
      params: { path: 'StockAnalysis/test.py' },
    });
    const ctx = makeContext({ agentId: 'main' });
    const result = shouldBlock(event, ctx);
    expect(result).toBeDefined();
    expect(result).toHaveProperty('block', true);
  });

  // 测试 24: include 参数含 StockAnalysis/ → requireApproval
  // 注意：command 中也需要包含 StockAnalysis/ 才能通过 matchesProjectPath 检查
  it('24. exec include 参数含 StockAnalysis/ → requireApproval', () => {
    const event = makeEvent({
      toolName: 'exec',
      params: {
        command: 'python3 -c "..." StockAnalysis/script.py',
        include: 'StockAnalysis/config.yaml',
      },
    });
    const ctx = makeContext({ agentId: 'main' });
    const result = shouldBlock(event, ctx);
    expect(result).toBeDefined();
    expect(result).toHaveProperty('requireApproval');
  });

  // 测试 25: exec command 字符串含重定向 → block: true
  // extractTargetPath 只检查 typeof string 类型的 command，数组类型在路径检查阶段会放行
  it('25. exec command 字符串含重定向 → block: true', () => {
    const event = makeEvent({
      toolName: 'exec',
      params: { command: 'echo hello > StockAnalysis/output.txt' },
    });
    const ctx = makeContext({ agentId: 'main' });
    const result = shouldBlock(event, ctx);
    expect(result).toBeDefined();
    expect(result).toHaveProperty('block', true);
  });

  // 测试 26: perl -i 就地编辑 → requireApproval
  it('26. exec perl -i 就地编辑 → requireApproval', () => {
    const event = makeEvent({
      toolName: 'exec',
      params: { command: "perl -i -pe 's/foo/bar/g' StockAnalysis/file.pl" },
    });
    const ctx = makeContext({ agentId: 'main' });
    const result = shouldBlock(event, ctx);
    expect(result).toBeDefined();
    expect(result).toHaveProperty('requireApproval');
  });

  // 测试 27: exec 但无params → undefined（extractCommand 返回 null）
  it('27. exec 无 params → undefined（放行）', () => {
    const event = makeEvent({
      toolName: 'exec',
      params: {},
    });
    const ctx = makeContext({ agentId: 'main' });
    const result = shouldBlock(event, ctx);
    // params 中没有 command/cmd → extractCommand 返回 null → 放行
    expect(result).toBeUndefined();
  });

  // 测试 28: main agent 写 ./StockAnalysis/ 路径 → 匹配
  it('28. main agent write ./StockAnalysis/ 路径 → block: true', () => {
    const event = makeEvent({
      toolName: 'write',
      params: { path: './StockAnalysis/src/main.py' },
    });
    const ctx = makeContext({ agentId: 'main' });
    const result = shouldBlock(event, ctx);
    expect(result).toBeDefined();
    expect(result).toHaveProperty('block', true);
  });
});
