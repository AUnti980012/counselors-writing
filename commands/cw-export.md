---
description: 成果导出：已完成 artifact → Markdown
argument-hint: draft id + audit id
---

从已完成 artifact 生成 Markdown 成稿。

**输入**：$ARGUMENTS（草稿 ID、当前草稿已通过的审核 ID）

**步骤**
1. `python scripts/kb.py output render --draft <id> --audit <audit_id>`（零 LLM）

**输出**：FINAL Markdown artifact（路径/文件名/哈希/版本/统计取自最终文件）

**规则**：正式成稿为 Markdown。若审核缺失、未通过，或稿件内容已经改动，先审核当前稿件再导出。
