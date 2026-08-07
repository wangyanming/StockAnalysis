---
name: requirement-doc
description: "StockAnalysis 需求文档编写 skill：确认需求后生成/更新需求文档，按 project-doc 文档规范存 requirement/，保留历史版本"
---

# 需求文档编写

**适用场景：** 你提了一个新需求，需要先形成正式的需求文档，再进入方案设计阶段。

## 你触发的方式

直接说"写需求文档"或"记录这个需求"，我就会按规范执行。

## 工作流

### 1. 确认需求范围

画清楚 4 条边界线：

- **做（Scope In）：** 要实现的全部功能
- **不做（Scope Out）：** 明确排除、推迟的内容
- **输入/触发条件：** 数据来源、用户操作、调度触发
- **输出/结果形态：** 推送到哪里、存到什么表、生成什么文件

### 2. 生成文档

用 `assets/requirement_template.md` 填充，产出存到（路径与命名规则见 `project-doc/文档规范.md`）：

```
/Users/wangyanming/workspace/project-doc/<项目名>/requirement/req-<日期yyyyMMdd>-<需求描述>-v<X.X>.md
```

**示例：** `req-20260806-大盘概率改造-v1.0.md`

### 3. 保留历史版本

已有文档要修改时：

1. 先读当前版本 vN
2. 写新版 vN+1，在文件头 `**版本:** vN+1`
3. 旧版本不做物理移动，因为 git 版本控制已覆盖

### 4. 存档位置

所有需求文档统一存放在 project-doc 根路径（权威规则见 `project-doc/文档规范.md`）：

```
/Users/wangyanming/workspace/project-doc/StockAnalysis/requirement/
├── req-20260526-集合竞价检查-v1.0.md
├── req-20260527-定时任务重构-v1.0.md
└── ...
```

## 触发条件

以下场景需要写需求文档：

- 新增一个独立的功能模块
- 新增一条 cron 定时任务
- 数据源或 API 替换
- 架构层面的重构
- 新增外部依赖
