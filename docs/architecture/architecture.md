# Architecture（V2 目标架构）

> 对应 pack M9 STEP 1-2（端到端集成 + 指针优先交接）。V1 审计基线见 `docs/history/architecture-audit.md`。

## 1. 目标流水线

```text
User Request → Routing → Task → Access → Fetch → Raw Artifact → Processed Document
→ Chunk → LLM Extraction → Validation → Knowledge → SQLite/FTS5 Index → Search
→ Analysis → School Mapping → Topic → Writing → Audit → Output → Dissemination Retro
→ Knowledge Persistence
```

## 2. 五层结构

| 层 | 载体 | 职责 |
|---|---|---|
| 编排层（Agent） | SKILL.md 路由 + references | 只做编排：读意图 → 调 kb.py 子命令 / 读 reference |
| 方法层 | `references/` 8 份 | 阶段指令，命令统一 `scripts/kb.py` |
| 执行层（确定性核心） | `scripts/core/` 22 模块 | 抓取/清洗/分块/提取/检索/分析/映射/写作/审核/输出/任务/备份/GC |
| 数据层 | `data/` + `cache/` | knowledge 文件（权威）+ index.db（索引）+ registry（账本）+ artifacts + backups + cache 四层 |
| 外部依赖 | 兄弟 skill | `read` / `fetch-skill-main` / `write` / `web-access-main`（`resolve_sibling_skill` 定位） |

## 3. 12 个 ID 跨阶段流转（STEP 1，无隐藏会话记忆）

| ID | 产生阶段 | 消费阶段 |
|---|---|---|
| task_id | Task（M7） | 全程断点续跑（resume 读 context_refs） |
| artifact_id | Fetch/提取/审核/输出落盘 | 血缘（source_ids/parent_ids）、GC 引用检查 |
| source_id | Fetch | document/chunk/evidence 回指 |
| document_id | 清洗后 | chunk、extract 输入 |
| chunk_id | 分块 | extract（chunk-only 证据回指） |
| case_id | 提取/案例沉淀 | search/analysis/mapping/writing |
| style_id | 提取/风格沉淀 | search/writing |
| topic_id | 选题 | search/writing lineage |
| analysis_id | 分析 | mapping/writing |
| mapping_id | 映射 | writing（白名单入口） |
| draft_id | 写作 | audit/output |
| audit_id | 审核 | output（血缘 metadata） |

## 4. 指针优先交接（STEP 2）

阶段间只传 `id + path + hash + schema_version + status + 摘要`；**不传**整文/全文分析/整库/重复 prompt。正文、raw HTML、完整案例背景一律落盘（cache/knowledge），经 L1/L2 检索或白名单投影按需注入。

## 5. 模型策略（STEP 4）

| 层 | 适用 |
|---|---|
| economy | 抓取/解析/确定性提取/校验/检索/索引/元数据操作 |
| standard | 常规语义提取/案例抽象/选题提取/一般分析 |
| deep | 复杂综合/深度对比分析/高质量成文（有依据时）/难审 |
