---
description: 独立文章审核：七项思政自查+确定性门禁
argument-hint: draft id / 文章文件路径
---

对 draft 或文章文件独立做七项思政自查 + 确定性门禁。

**输入**：$ARGUMENTS

**步骤**
1. 有 draft id → `python scripts/kb.py audit --draft <id>`（七项 LLM 自查 + 标点/去 AI 门禁，verdict 由 Python 硬规则计算）
2. 只有文章文件 → `kb.py punctuation --lang zh <file>` + `kb.py deai <file>`（确定性门禁，零 LLM）
3. 七项清单唯一规则源：`references/ideological-review.md`

**输出**：AuditRecord（政治方向/事实依据/价值表达/学生隐私/标签化语言/版权风险/AI痕迹七项 + verdict）

**铁律**：严格区分 作者自查/模型审核/确定性门禁/独立证据核验；不信任模型自报的 verdict；对独立文件七项结构化审核需 draft（kb.py audit 契约输入），如实说明；缺检查项不得通过（八项 LLM 自查必须齐全且唯一）。
