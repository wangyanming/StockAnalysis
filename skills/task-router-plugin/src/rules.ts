/**
 * rules.ts — 拦截规则定义
 *
 * 声明式配置：被拦截的工具、豁免 Agent、路径匹配、exec 写操作检测。
 */

/** 被拦截的工具列表（main agent 直接调用时） */
export const BLOCKED_TOOLS = new Set<string>([
  'write',
  'edit',
  'exec',
]);

/** 豁免 Agent 列表（子 Agent 不受限制） */
export const EXEMPT_AGENTS = new Set<string>([
  'product-agent',
  'architecture-agent',
  'engineering-agent',
  'qa-agent',
]);

/** StockAnalysis 项目路径标识 */
export const PROJECT_PATH_MARKER = 'StockAnalysis/';

/**
 * exec 写操作正则——检测 shell 重定向操作符。
 *
 * 匹配：
 *   > file      (标准重定向)
 *   >> file     (追加重定向)
 *   &> file     (同时重定向 stdout+stderr)
 *   &>> file    (同时追加重定向 stdout+stderr)
 *   1> file     (只重定向 stdout)
 *   2> file     (只重定向 stderr)
 *   1>> file
 *   2>> file
 *   | tee file  (tee 写文件)
 *   | tee -a file (tee 追加写文件)
 *
 * 注意：tee 前使用 \b 单词边界避免误匹配含 "tee" 子串的命令。
 */
export const WRITE_OPS_REGEX = /(?:^|\s)(?:[12]?[>&]?|&)?>+>?\s|(?:^|\|\s*)\btee\b(?:\s+-[aA]+)?\s/;

/** exec heredoc 检测 */
export const HEREDOC_REGEX = /<<[-]?\s*\w+/;
