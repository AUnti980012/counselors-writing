---
description: 一站式写作：热点/话题/素材→草稿→审核→成稿
argument-hint: 话题 / 热点序号 / 链接 / 素材文件
---

按输入自动决定热点→研究→选题→写作→审核的最小流水线，产出可交付成稿。

**输入**：$ARGUMENTS

**步骤**
1. 判断入口：有学生案例走 Case-based，政策/热点/指南走 Generic（见 `docs/integration/agent-integration.md`）
2. Case-based：`kb.py extract <doc> --extractor case_facts` → `kb.py mapping --case <id> --profile pro-school` → `kb.py write --mapping <id>`
3. Generic：`kb.py ingest`/`kb.py fetch` 得来源 → `kb.py extract <doc> --extractor topic_signal` → `kb.py write --topic <id> [--mode ...]`
4. 收口（两条路径共用）：`kb.py punctuation --lang zh <正文>` + `kb.py deai <正文>` + `kb.py audit --draft <id>` + `kb.py output render --draft <id> --audit <audit_id>`

**输出**：DraftRecord → AuditRecord → FINAL 文件（真实路径 + 统计 + 血缘）

**铁律**：未明确要求不做全流水线；未核实事实不得升级为确定陈述；禁止把 Generic 内容捏造成学生案例；可修复问题有限次修正；不得把未筛查热点扩写成确定新闻。
