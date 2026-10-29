---
description: 资料研究与证据整理：主题/URL/热点→来源+证据
argument-hint: 主题 / URL / 热点序号 / artifact id
---

把主题/URL/热点引用/已有 artifact 变成有证据支撑的研究结论。

**输入**：$ARGUMENTS

**步骤**
1. 热点引用先解析：`python scripts/cw.py hot resolve <序号|hot-id|关键词>`；返回含 `review_status`/`risk_flags`，过期/歧义/不存在会明确报错
2. URL → `python scripts/kb.py fetch <url>`；文本/文件 → `kb.py ingest --file <f>`（或粘贴）；已有 → `kb.py artifact get <id>`
3. 结构化提取：`kb.py extract <doc_id> --extractor topic_signal`（通用）或 `case_facts`（案例），用 `--llm-cmd`/`--prompt-only`/`--result` 三模式之一
4. 继承上游热点审核状态：`approved` 可直接作为来源；`caution` 把限制写入下游风险；`needs_review`/`rejected` 不得无条件升级为已核实事实
5. 证据整理：区分「来源存在/可访问/摘录可定位/证据支持断言」；二手转述、来源冲突、证据不足、无法访问都要显式标记

**输出**：SourceRecord + Document + TopicRecord/CaseRecord + EvidenceRecord；documented_fact/source_claim 必须带 evidence_ids

**铁律**：事实/推断强制分离；拿不准标「待核实」；证据摘录 ≤500 字符；不为取数无限重试；热点标题/热度/二手转述一律不当已核实事实。
