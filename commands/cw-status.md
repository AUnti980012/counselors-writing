---
description: 查询任务进度/产物/失败原因/缓存
argument-hint: 任务 id（可选）
---

查询任务真实进度、产物、失败原因、缓存命中与待办。

**输入**：$ARGUMENTS（任务 id，可选，默认最近）

**步骤**
1. `kb.py task status <id>` + `kb.py task events <id>`
2. `kb.py cache status` + `kb.py artifact list`

**输出**：状态 + context_refs + 输出引用 + 事件日志 + 缓存统计

**铁律**：只报真实状态；缓存命中须重新确认审核状态/版本/依赖仍有效，仅显示 HIT 不算验证。
