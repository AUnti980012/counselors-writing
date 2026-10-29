---
description: 选题分析：热点/素材/想法→选题卡
argument-hint: 热点引用 / 素材 / 想法
---

从热点/研究/素材/想法生成选题卡（TopicRecord）。

**输入**：$ARGUMENTS

**步骤**
1. 素材入链：热点 → `python scripts/cw.py hot resolve <ref>`（读 `review_status`/`risk_flags`）取 URL 再 `kb.py fetch`；文件/文本 → `kb.py ingest`
2. 选题提取：`kb.py extract <doc_id> --extractor topic_signal`（五角度 + 标题三式 + 风险）
3. 查重：`kb.py search topic <kw>` 避免重复选题
4. 选题规则源：`references/topic-selection.md`

**输出**：TopicRecord（推荐理由/读者痛点/备选角度/标题方向/证据缺口/风险约束）

**铁律**：评分依据必须来自真实素材/风险规则，不得伪造；优先 `approved` 候选，`caution` 把限制写入 TopicRecord.risks，`needs_review`/`rejected` 不得无条件提升。
