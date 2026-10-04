# Data Contract（数据契约）

> pack M1 STEP 17 交付物。权威定义 = `scripts/core/schema.py`（Pydantic 单一真相源）；`data/schemas/*.json` 为导出的机器可读契约文档（禁止手改）。本文档说明实体关系、所有权、生命周期与迁移规则。
> 契约版本：schema_version 1.0.0（2026-09-30 冻结）。变更流程见 `docs/schema-conventions.md`。

## 1. 实体总表（16 + common）

| 实体 | 文件 | 职责 | 归属（读写） | 生命周期状态 | 存储位置（M2 起） |
|---|---|---|---|---|---|
| Task | task.schema.json | 流水线任务状态机（16 态）+ 恢复锚点 | 确定性引擎（Python）；LLM 不持有任务状态 | CREATED→…→COMPLETED / FAILED / BLOCKED / CANCELLED | SQLite tasks 表（M7 建表） |
| Artifact | artifact.schema.json | 阶段产物指针：id/路径/哈希/血缘/保留策略 | Python 各模块 | created→valid / invalid / failed / expired | data/artifacts/ + registry（账本）+ SQLite artifacts 投影 |
| Source | source.schema.json | 来源元数据（**不含正文**；失败尝试也落账） | Python fetcher | success/blocked/access_restricted/unavailable/timeout/rate_limited/parse_failed | data/knowledge/sources/ + SQLite sources 表（M3 落地） |
| Document | document.schema.json | 清洗后文档：结构 + 分块引用（正文在 chunk/artifact） | Python preprocess | created→valid | data/knowledge/documents/ + SQLite documents 表（M3 落地） |
| Chunk | chunk.schema.json | 分块（≤2000 字符内联 + 位置） | Python chunker | created→valid | data/knowledge/chunks/ + SQLite chunks 表 + FTS5（M3 落地） |
| Case | case.schema.json | 案例知识（事实/推断分离，**不含传播效果**） | LLM 提取 → Pydantic 校验 → Python 持久化 | created→valid | data/knowledge/cases/ + SQLite + FTS |
| Style | style.schema.json | 风格特征（**不含整文**；origin=seed/user） | 同上 | created→valid | data/knowledge/styles/（seed.json 信封 + 用户条目）+ SQLite + FTS |
| Topic | topic.schema.json | 选题卡（五角度 + 标题三式） | LLM 分析 | draft→confirmed→used/abandoned | data/knowledge/topics/ + SQLite |
| Analysis | analysis.schema.json | 案例分析（与 Case 分离；fact_type 逐条标注） | LLM 分析 | created→valid | data/knowledge/analyses/ + SQLite（表 M5 建） |
| Mapping | mapping.schema.json | 案例 × 画像映射 | LLM 分析 | created→valid | data/knowledge/mappings/ + SQLite（表 M5 建） |
| Profile | profile.schema.json | 学校画像（7 键白名单 + 字段级溯源） | Python set 白名单；LLM 仅可提议 | created→valid | data/knowledge/profiles/ + SQLite profiles 表 |
| Audit | audit.schema.json | 审核报告（**单通道**；verdict 硬规则） | LLM 自查 → Pydantic | created→valid | data/knowledge/audits/ + SQLite |
| Effect | effect.schema.json | 传播复盘五维度（与 Case 分离） | LLM 复盘 | created→valid | data/knowledge/effects/ + SQLite |
| Draft | draft.schema.json | 正文草稿（lineage + claims 逐条溯源） | LLM 写作 | draft→reviewed→final | data/knowledge/articles/（canonical-only，无索引表） |
| Extraction | extraction.schema.json | 提取运行记录（失败不丢源；自纠正 ≤2） | Python 编排 + LLM 提取 | success/extraction_failed/validation_failed/cancelled | SQLite extraction_cache + registry（M4 落地） |
| Evidence | evidence.schema.json | 证据记录（三级回指，摘录 ≤500 字符） | LLM 提取 → Python 持久化 | created→valid | data/knowledge/evidence/ + SQLite evidence 表 |
| common | common.schema.json | 共享 $defs（22 个）+ 枚举表 | — | — | — |
| seed 信封 | seed.schema.json | seed.json 的文件信封（自含 StyleRecord $defs，独立于 common） | Python（M2 起读取/校验） | — | data/knowledge/styles/seed.json |

## 2. Ownership（所有权分工）

| 层 | 职责 | 依据 |
|---|---|---|
| Python（确定性） | 抓取/清洗/分块/哈希/缓存/索引/SQLite/文件操作/时间戳/任务状态/校验执行 | GLOBAL BOOTSTRAP TOKEN ECONOMY |
| LLM（语义） | 理解/提取/归纳/比较/迁移性分析/写作/审核 | 同上 |
| Pydantic | 校验 LLM 输出（JSON→模型→拒绝或放行） | RESPONSIBILITY BOUNDARY：LLM 不得动态定义 schema |

## 3. References（引用关系）

```
source ──1:N── document ──1:N── chunk
   │                                ↑
   └──────── evidence ←─────────────┤（ref_source/ref_document/ref_chunk/ref_artifact 至少其一）
                      ↑
case ──documented_facts/source_claims── evidence_ids（事实必带）
case ──ai_inferences── basis（推断必带，无强制证据）
topic ──source_basis── source_ids/case_ids + evidence_basis
analysis ──input_case_ids── case；patterns/comparisons 逐条 FactClaim
mapping ──case_ids + profile_id── case × profile
draft ──lineage── topic/analysis/mapping/style/profile/case/source/evidence
draft ──claims── FactClaim 列表（主要事实性断言逐条溯源）
audit ──draft_id── draft；issues 逐项 check+verdict
effect ──case_id(+draft_id)── 复盘结果不回写 Case
task ──input_refs/output_refs/context_refs── 任意实体（RefPair）
artifact ──source_ids/parent_ids── 血缘链（Artifact Registry 是 lineage ledger）
extraction ──input_refs/output_refs── 提取上下游
```

## 4. Lifecycle

- **Task 16 态**（pack M1 STEP 2）：CREATED→ROUTING→ACCESSING→FETCHING→PROCESSING→EXTRACTING→VALIDATING→INDEXING→ANALYZING→MAPPING→GENERATING→REVIEWING→COMPLETED；任意态→FAILED/BLOCKED/CANCELLED。状态迁移表在 M7（core/task.py）实现；M1 只冻结枚举与 resumable/context_refs 字段。
- **Artifact 5 态**：created（已写入）→ valid（校验通过）/ invalid / failed（处理失败）；expired（GC 后）。保留策略：temporary（TTL）/ permanent（显式）/ task_bound（跟随任务）。
- **Draft 3 态**：draft→reviewed→final。**Topic 4 态**：draft→confirmed→used/abandoned。
- **失败码**（FAILURE RULE）12 值：success/blocked/access_restricted/unavailable/timeout/rate_limited/parse_failed/extraction_failed/validation_failed/index_failed/dependency_failed/cancelled。

## 5. Versioning

- 每实体必含 `schema_version`（默认 1.0.0，Python 写入侧托管补齐；LLM 输出可省略）。
- 契约变更：改 `scripts/core/schema.py` → 全测试绿 → `kb.py schemas export` → diff 审查 → 递增版本号。导出的 JSON 永远不许手改（漂移检测：`kb.py schemas export --check`，exit 1）。

## 6. Fact / Inference 规则（硬约束，Pydantic model_validator 强制）

| fact_type | 含义 | 强制要求 |
|---|---|---|
| documented_fact | 有据可查的事实 | evidence_ids ≥1 |
| source_claim | 来源声称（未独立核实） | evidence_ids ≥1 |
| ai_inference | AI 推断 | basis 非空 |
| derived_pattern | 归纳出的模式 | basis 非空 |
| recommendation | 建议 | basis 非空 |

违反即校验失败（路径级错误供自纠正循环消费）。禁止把推断静默写成事实。

## 7. Storage Responsibilities（PERSISTENCE RULE）

> 2026-09-30 更新：以下存储层已在 **M2/M3 实现**，实现细节与失效策略见 `docs/storage-architecture.md`。

| 存储 | 角色 | 说明 |
|---|---|---|
| data/knowledge/<entity>/<id>.json | **权威内容存储** | 长期知识；M7 起永不自动 GC；原子写（tmp+fsync+os.replace） |
| data/knowledge/styles/seed.json | 种子信封（M1 冻结，只读） | 整文件信封，styles 目录内唯一非 <id>.json 文件 |
| data/index.db（M2 ✅） | 索引/搜索/元数据层 | SQLite+FTS5（trigram→unicode61 回退）；`kb.py db rebuild` 可重建，不是内容权威 |
| data/registry/artifacts.jsonl（M2 ✅） | 血缘账本 | 追加式 JSONL，同 id 最新行胜出；不是全文搜索库 |
| cache/{raw,processed,extraction,tasks}（M2 ✅，M3 起供抓取管道使用） | 临时/中间产物 | key 前缀 url:/content:/extract:/analysis:；TTL 30/30/90/7d；raw 条目按 pack 建议 3d；purge 默认 dry-run |
| data/artifacts/（M2 ✅，M3 起供抓取管道使用） | 阶段产物落盘 | ArtifactRecord.path 指向处；幂等复用（同类型+同 hash+同血缘）；抓取产物 raw_html/raw_text/processed_document/chunkset |
| data/backups/（M2 ✅） | 备份 | legacy 导入前快照（M7 起 DB 在线快照） |

## 8. Migration Rules（V1 → V2）

1. **旧三文件只读**：`data/case_library/cases.jsonl`、`data/style_library/index.json`、`data/profiles/school.json` 在 M2 只读导入，**从不原地改写**；导入前备份。
2. **V1 案例 16 字段 → 四实体拆分映射**（import 时按此拆，`case_id` 保持原 id）：

| V1 字段 | 目标 |
|---|---|
| id / created_at | CaseRecord.case_id / created_at |
| source_material | CaseRecord.background（脱敏）+ 按内容拆 documented_facts/source_claims |
| angles | TopicRecord.angles（角度评价：名称/分数/理由/核心冲突） |
| tags | CaseRecord.tags（≤10 个，单条 ≤50 字符） |
| title_used | DraftRecord.title（最终采用标题） |
| title_alt | TopicRecord.titles（备选标题，type 枚举 suspense/question/story） |
| hot_topic | TopicRecord.source_basis.source_ids（热点来源引用）+ DraftRecord.closing（落款「蹭的热点」） |
| style | DraftRecord.style_id（引用 StyleRecord，不内联内容） |
| hook | TopicRecord.hook（开头钩子） |
| structure | StyleRecord.structure（写作结构特征） |
| value_sublimation | TopicRecord.value_landing（升华落点）+ DraftRecord.sections 末段（正文） |
| review_result / review_issues | AuditRecord（issues 逐条；verdict 由硬规则重算） |
| effect / retro | EffectRecord（dimensions/feedback/retro/reusable_patterns） |
| 缺证据字段 | 迁移时生成占位 EvidenceRecord（kind=paraphrase，note=「V1 迁移，原始证据未随迁」）并被事实引用；契约强制 fact 必有 evidence_ids，禁止生成无证据 fact |

> **M2 落地时的两处映射细化**（`scripts/core/legacy_import.py` 实现为准，本表不再改动）：
> 1. `hot_topic` 是自由文本、无 source 记录可引用 → 落 `TopicRecord.source_basis.material_excerpt`（≤500）+ `DraftRecord.closing`（「热点：…」）；不伪造 source_id。
> 2. `structure` → 独立 user StyleRecord（`style-user-<case_id>`，origin=user；种子只读不可污染），DraftRecord.style_id 指向它；无 structure 时 style 自由文本按别名映射种子（人民系→style-seed-rmrb 等）。含 structure 的案例**无论是否成稿**都落该 StyleRecord。
> 3. V1 无 fact/evidence 字段 → 不生成事实条目与占位 Evidence（本条规则保留给 M4 从 V1 全文提取时用）；CaseRecord.title 以 source_material 前 200 字符投影（V1 无独立案例标题字段）。
> 4. `review_issues` 是自由文本、无 check 类别信息 → 逐条保留于 AuditRecord.summary（不逐条伪造 AuditIssue.check 枚举）；passed 语义不变（仅「通过」为 True）。

3. **V1 school.json 7 字段** → SchoolProfileRecord 同名 7 键 + provenance 补空。取值转换：school_type 自由文本 → 枚举（985/211/双一流/地方本科→university；高职→vocational；民办→college；空/其他→other）；updated_at 空字符串丢弃、由迁移时刻重算（带时区）；不兼容取值宁可落「other」并记 provenance 备注，绝不静默丢失。
4. **V1 style index.json entries** → StyleRecord（origin=user，content_hash 按规范化文本计算）。
5. 导入幂等：同 id 跳过；`reconcile-legacy` 定期核对 JSONL 镜像与 DB（M2 起）。

## 9. JSON 边界审计（pack M1 STEP 13 结果）

对全部 16 实体逐字段核查四项红线（无界数组 / 无界文本 / 源内容复制 / prompt·对话泄漏）：

| 审计项 | 处理 |
|---|---|
| 无限数组 | 全部 List 字段带 max_length（=maxItems）：最小 3（extraction refs 类），最大 500（document.chunk_ids） |
| 无界文本 | 全部 str 字段带 max_length，全部 dict 值有值级 validator（≤500-1000 字符，Pydantic 强制；JSON Schema 无法表达 dict 值长上限，跨工具以 Pydantic 为准）；唯一例外无 |
| 重复源内容 | Source 无正文字段（正文在 cache/raw + chunks）；Case 不存 source_material 全文（存结构化字段）；Style 不存整文（exemplar 摘录 ≤300）；Evidence.excerpt ≤500 |
| prompt/对话泄漏 | 无任何 prompt/transcript/context 字段；Task.context_refs 只存 kind→id 指针（总 ≤200 条 id） |
| 递归嵌套 | 无自引用类型；嵌套深度 ≤3（Case→FactClaim→str） |
| 所有权歧义 | 每实体唯一归属（见 §2）；Effect 与 Case 分离；Analysis 与 Case 分离 |
| 缺 id/版本 | 全部实体必含 *_id + schema_version（默认值托管） |
| 引用不清 | 引用一律 {kind, ref_id} 或 id 列表；Evidence 至少回指一个物理载体 |
