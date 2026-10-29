---
description: 恢复未完成任务，复用已验证结果
argument-hint: 任务 id
---

恢复未完成任务，复用已验证仍有效的结果，从可恢复步骤继续。

**输入**：$ARGUMENTS（任务 id）

**步骤**
1. `kb.py task resume <id>`（读 status + context_refs 恢复锚点）
2. 按 context_refs 复用已落盘 artifact/knowledge，跳过已完成步骤

**输出**：恢复锚点 + 下一步建议

**铁律**：不重复执行全部工作流；不使用已失效中间产物；恢复时须重新确认审核状态/版本/依赖，不得在缓存命中时跳过必要审核。
