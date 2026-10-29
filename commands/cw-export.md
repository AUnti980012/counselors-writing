---
description: 成果导出：已完成 artifact → Markdown
argument-hint: draft id [audit id]
---

从已完成 artifact 生成 Markdown 成稿。

**输入**：$ARGUMENTS（draft id，可选 audit id）

**步骤**
1. `python scripts/kb.py output render --draft <id> [--audit <audit_id>]`（零 LLM）

**输出**：FINAL Markdown artifact（路径/文件名/哈希/版本/统计取自最终文件）

**铁律**：仅 Markdown 已有能力；Word 等其它格式未实现，如实说明；audited output 应带 `--audit` 写血缘；审核不完整（无有效 AuditRecord）须标注，不得伪装终稿。
