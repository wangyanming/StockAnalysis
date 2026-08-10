---
name: design-doc
description: "StockAnalysis 方案设计文档 skill：需求确认后编写技术方案，按 project-doc 文档规范存 design/，保留历史版本"
---

# 方案设计文档编写

**适用场景：** 需求文档已确认，进入技术方案设计阶段。

## 前置条件

需要先确认以下信息才能开始：

- **关联需求文档** — 是哪份需求文档的技术方案？
- **涉及哪些现有模块** — 需要知道现有代码结构
- **数据源和技术约束** — 有没有特殊限制？

如果以上信息不确定，我会先问你。

## 工作流

### 1. 读前置资料

读以下文件获取上下文：

```
StockAnalysis/skills/engineering-rules/SKILL.md（单位校验等规范）
StockAnalysis/PROJECT_STATE.md（当前阶段和已知问题）
/Users/wangyanming/workspace/project-doc/StockAnalysis/requirement/req-*.md（关联需求文档，见 `project-doc/文档规范.md`）
```

### 2. 方案设计要点

必须说清楚：

- **模块划分：** 新增/改哪些文件，每个文件做什么
- **数据流：** 采集→清洗→存储→计算→展示，各阶段变化
- **存储设计：** 新增/改哪些表，字段单位约定（引用 engineering-rules 的校验规则）
- **异常处理：** 表格式汇总各类异常的处理策略
- **测试用例：** 正常流程 + 边界条件 + 失败场景

### 3. 生成文档

用 `assets/design_template.md` 填充，产出存到（路径与命名规则见 `project-doc/文档规范.md`）：

```
/Users/wangyanming/workspace/project-doc/<项目名>/design/des-<日期yyyyMMdd>-<方案描述>-v<X.X>.md
```

**示例：** `des-20260806-大盘概率改造-v1.0.md`

### 4. 保留历史版本

与需求文档相同的版本管理方式。

## 交付履约契约（强制）

> 依据总规范 `engineering-rules/SKILL.md` 第 6.5 节「文档交付履约契约」。

### 方案文档交付 = 真实落盘 + exec 验证，缺一不可

1. **真实落盘**：(按上节路径)用 `write` 工具把方案文档实际写入：
   `/Users/wangyanming/workspace/project-doc/StockAnalysis/design/des-<日期yyyyMMdd>-<方案描述>-v<X.X>.md`
2. **exec 验证**：执行 `ls -l <绝对路径>` + `wc -c <绝对路径>` 确认文件存在 + 字节数符合预期。
3. **回复必须含**以下三项（缺任一即视为未交付）：
   ```
   【落盘路径】<绝对路径>
   【文件字节数】<wc -c 实际输出值>
   【落盘验证】<ls -l / wc -c 原始输出>
   ```

🚨 **禁止只回「开始写 / 我写了」就宣告完成**。未实际落盘 + 文件系统可见 = 未交付。主 agent 会 exec 复核，不符即打回重做。

## 触发条件

以下情况需要写方案设计文档：

- 新增模块或功能（对应一份需求文档）
- 重构现有模块
- 数据源替换
- 配置架构变更
- 新增定时任务
