# Changelog

本仓库按 `docs/history/milestone-pack-v1.0.md` 的 M0→M9 协议演进。里程碑细节见 `docs/history/migration-plan.md`。

## [2.2.0] - 2026-10-04

M10.1 最终运行时加固 + M10.2 通用内容写作（Gray Release）：系统从「案例驱动写作」扩展为「案例驱动 + 通用校园内容」双入口，并修复灰度测试发现的三个问题（P2-1 / P2-2→P1 / P3-1）。

### 版本号（四种语义分离）

| 版本 | 值 | 含义 |
|---|---|---|
| Skill | **2.2.0** | 用户可见能力/行为（`--result` 绑定 + Retro 增量 + Generic Writing 入口） |
| KB | **0.12.0** | CLI 接口（`write --topic` 通用路径 + `--profile/--sources` + `guide/commentary` mode） |
| Schema | **1.1.0** | 数据契约（DraftRecord 新增 `mode` 字段，决定 audit 审核上下文） |
| DB | 3（不变） | SQLite migration（无迁移；仅需 `db rebuild` 归位契约版本） |

### M10.2 变更

- **Generic Content Writing（P1，原 P2-2）**：`write` 现在支持两条入口——案例写作 `--mapping <id>`（必需，topic/analysis/style 可选增强），通用写作 `--topic <id>`（不需要 mapping、不需要 Case）。通用路径复用 TopicRecord 作 Content Brief，来源片段经 `repo.source_chunks` 有界投影（≤5 来源 × ≤8 块），lineage 记录 `topic_id + source_ids`（`case_ids=[]`、`mapping_id=None`），**严禁伪 Case**。
- **P2-1 修复 — schema_summary 嵌套 $ref 展开**：`_json_type` 对 `array<$ref>` 展开一层必填字段（如 `angles → array<{name: string, score: number}>`），提高 LLM 一次通过率；深度上限 2 层，不把整个 JSON Schema 复制给模型。
- **P3-1 修复 — audit mode 措辞**：`build_audit_prompt` 按 `draft.mode` 动态生成审核上下文（article→公众号推文 / report→内部工作材料 / guide→指南 / commentary→解读），并明确「无学生个案时 privacy 判 pass」。
- **通用写作事实/推断规则**：政策数字/调查数据/时间节点/机构名称必须来自来源素材，来源没有的写清楚是推断；沿用 documented_fact/source_claim vs ai_inference 分离。
- **DraftRecord 新增 `mode` 字段**：Python 注入，决定 audit 措辞；`_DRAFT_MANAGED_KEYS` 与 `_MANAGED_FIELDS["draft"]` 同步。
- **缓存/id 幂等通用化**：`draft_id_for` 与 `writing_cache_key` 纳入 topic_id/source_ids/profile_id，通用写作同输入 cache hit 零 LLM。
- **新增 mode**：`guide`（学生/辅导员指南）、`commentary`（热点/政策解读），与 `article/report/outline/topic_proposal` 共用同一 Output/Audit 引擎。

### M10.2 测试

全量测试必须通过：`python scripts/kb.py test`（当前基线 **484**；新增 16 条：通用写作 8 + schema_summary 6 + audit mode 2）。`python scripts/kb.py schemas export --check` 零漂移。

### 灰度缺陷修复（证据链 / 血缘 / 隐私）

灰度验证后修复三处核心缺陷（不改变四种版本号，仍为 Skill 2.2.0）：

- **选题证据链**：`_inject_topic` 为每个角度生成独立 `EvidenceRecord`（回指 document/chunk，复用 case 事实证据模式），`angle.evidence_ids` 与 `topic.evidence_basis` 指向真实证据；无 `evidence_excerpt` 时退化为 paraphrase（有界、不伪造 id）。`topic_signal` prompt 增加 `evidence_excerpt` 指令。
- **通用写作血缘**：`_aggregate_generic_lineage` 的 `evidence_ids` 由选题 `evidence_basis` 透传，`draft.lineage` 不再丢血缘。
- **Prompt 隐私脱敏层**：`_source_projection` 对来源 chunk 文本/title 在进入 prompt 前统一确定性脱敏（学号/身份证/手机号/邮箱）。
- **版本漂移修复**：AGENTS.md 误标 `2.3.0` 已纠正为 `2.2.0`。

测试基线：**487**（新增 3 条：选题证据 quote 绑定、通用血缘透传、来源 PII 脱敏）。

### M10.1 变更

- **Topic / Analysis / Mapping / Writing 语义收口（P0）**：`write --topic/--analysis/--style` 已确认真实进入实体读取 → 白名单投影 → prompt → lineage → draft_id；补 `test_topic_analysis_style_enter_prompt` 锁定。TopicRecord（`extract --extractor topic_signal`）与 AnalysisRecord（`analysis --case`）边界文档对齐。
- **`--result` 误回灌保护（P0）**：新增 `ResultBindingError` + `input_digest` 绑定。`--prompt-only` 输出 `input_digest`；`--result` 文件可为原始实体 JSON（向后兼容）或绑定 wrapper `{operation, input_digest, result}`，wrapper 校验不匹配 → exit 2 拒绝写入。
- **result round-trip 完整测试（P0）**：新增 `test_result_roundtrip.py`，覆盖 analysis/mapping/write/audit 的 `result` 回灌真实落盘 + 绑定不匹配拒绝 + 原始 JSON 向后兼容 + wrapper 解包。
- **SQLite connection lifecycle 修复（P0）**：`cmd_fetch` / `cmd_ingest` / `cmd_artifact_create` / `cmd_artifact_status` 补 finally 关闭连接，消除 ResourceWarning 泄漏。
- **`output render` 崩溃修复（P0，真实缺陷）**：`cmd_output_render` 使用 `default_extraction_deps()` 却从未 import（M6 遗留，成功路径从未被测试覆盖），一跑就 NameError。补 import + 回归锁。
- **Audit 全文 Token 规则澄清（P0）**：硬规则从「禁止整文进 LLM」改为「禁止默认把整库/raw/历史/重复上下文回流；审核当前 Draft 时允许必要全文、受 `MAX_PROMPT_CHARS`=8000 上限约束」。
- **Retro 改为增量触发（P1）**：交付正文 ≠ 强制复盘；只有存在新传播数据/用户反馈/有效写法/失败原因等增量时才沉淀，无增量不额外调用 LLM。
- **发布包卫生（P1）**：`.gitignore` 补 `data/knowledge/*/`、`data/registry/`、`data/artifacts/` 运行时产物，避免运行后出现大量未跟踪文件。
- **Runtime 文档去平台耦合 + 职责收口（P1）**：复核 SKILL/README/AGENTS/references/docs 职责分离，无平台专属硬编码。

### M10.1 测试

全量测试必须通过：`python scripts/kb.py test`（当前基线 **468**；新增 result round-trip 9 + write 语义 1 + output render 回归锁 1）。`python scripts/kb.py schemas export --check` 零漂移。

## [2.1.0] - 2026-10-04

M10 产品化收口：把「工程重构完成」收口为「可长期交付给不同 Agent 与真实用户的产品」。

### 版本号（四种语义分离）

| 版本 | 值 | 含义 |
|---|---|---|
| Skill | **2.1.0** | 用户可见能力/行为契约（新增 `--result` 回灌闭环） |
| KB | **0.10.0** | CLI/核心接口（`--result` 新接口） |
| Schema | 1.0.0（不变） | 数据契约（无字段变化） |
| DB | 3（不变） | SQLite migration（无迁移） |

### 变更

- **Agent Adapter Contract（P0-A）**：`extract` / `analysis` / `mapping` / `write` / `audit` 五命令统一三模式（互斥）——`--llm-cmd "<LLM_COMMAND>"` / `--prompt-only` / `--result <file>`。`--result` 用 `llm_fn_from_file` 复用既有 parse→inject→validate→persist 路径，零复制写入逻辑、无 shell 风险。`--prompt-only` 输出统一为 `{operation, schema_version, request, expected_output, prompt}` 机器可读 JSON。
- **选题语义闭环（P0-B）**：明确 `AnalysisRecord`（`analysis --case`）与 `TopicRecord`（`extract --extractor topic_signal`）边界；`search topic` 仅检索。`references/topic-selection.md` 已对齐。
- **文档分层（P0-C）**：`docs/` 分 `user/` `integration/` `architecture/` `history/` 四层，所有内部链接已修复。
- **SKILL.md 瘦身（P0-D）**：改为 8 段 Runtime 结构，更短且能力不缩水；移除平台专属命令示例。
- **AGENTS.md 改开发入口（P0-E）**：不再要求读历史里程碑全文；保留单一真相源、安全规则、测试门禁、版本一致性。
- **README 改用户入口（P0-F）**：增加「5 分钟第一次使用」与多 Agent 使用说明，移除固定测试数字。
- **LICENSE 落地（P0-G）**：MIT，与 frontmatter 声明一致。
- **Python 版本修正**：代码实际使用 `X | None` 联合类型（3.10+），`bootstrap.py` 与文档统一为 **Python 3.10+**（原 3.9+ 为漂移）。
- **文档一致性**：Runtime 文档清除 `claude -p` / `qwen-verify` / `WebSearch` 平台专属引用；修正标点门禁退出码漂移（findings/ko 均为 exit 2）。

### 测试

全量测试必须通过：`python scripts/kb.py test`（当前基线 **457**；新增 `--result` 回灌 / 互斥 / 非法拒绝等 10 条）。`python scripts/kb.py schemas export --check` 零漂移。

## [2.0.0] - 2026-10-04

V2 重构完成：从「一次性同步 CLI」升级为「Agent Knowledge Pipeline」（低 Token、高复用、可恢复、可检索、跨 Codex / Claude Code）。

### Milestones

- **M0** 架构审计 + 历史能力恢复（审计文档 6 份 + 12 项恢复矩阵）
- **M1** 数据契约冻结（16 实体 + common，Pydantic 单一真相源，`kb.py validate` / `schemas export`）
- **M2** 持久化基础（Artifact + SQLite/FTS5 + Cache 四层 + URL 归一 + import-legacy）
- **M3** Web 获取 + 清洗 + 分块（Fetcher/Preprocess/Chunker/Pipeline + hotlist 迁移）
- **M4** LLM 结构化提取 + 校验（content-hash 幂等 + 自纠正 ≤2 + PII 脱敏 + fact/inference 分离）
- **M5** 检索 + 分析 + 映射（L1/L2 检索 + Analysis + Mapping + Profile）
- **M6** 写作 + 审核 + 输出（写作白名单 + 单通道审核 + 标点门禁 + 占位符渲染）
- **M7** 备份 + 恢复 + GC（16 态任务状态机 + 在线快照 + 引用感知 GC）
- **M8** 加固 + 兼容 + 回归（CDP 后端 + 绝对路径清理 + 三报告 + `kb.py test`）
- **M9** Token 审计终版 + 集成 + 发布（SKILL.md V2 + architecture/release-readiness + README/CHANGELOG）

### 有意行为变更（C-01 ~ C-09）

| # | 变更 |
|---|---|
| C-01 | xinbang 子命令移除（报错指引官方替代源） |
| C-02 | fetch_douyin 结构化 [{title,topic,likes}] + 登录同意门禁 |
| C-03 | check_punctuation ko exit 2「暂不支持」+ `--max-findings` |
| C-04 | case_lib `--top 0` 钳制 ≥1（修 rows[-0:] 全量 bug） |
| C-05 | style_lib search `--top` 硬上限 10 |
| C-06 | profiles set 7 键白名单 + updated_at 托管 |
| C-07 | 思政第二意见删除（qwen-verify，单通道 + 标点门禁） |
| C-08 | 案例写入路径：文件权威 → DB → 独立 JSONL 镜像 |
| C-09 | 标点 4 规则修复（数字+CJK、em-dash、全角空格） |

### Skill 命名（三层分离）

- Canonical ID（目录名）：`fudaoyuan-baokuan`（不变，物理路径）
- Skill Identifier（frontmatter `name`）：`counselors-writing`（小写 kebab-case，机器识别）
- Display Name（`metadata.display_name` / H1）：`Counselors-Writing`（用户看到的产品名）

## [0.1.0] - V1（历史）

V1 为「文章生成 Skill」：SKILL.md 路由表 + 7 步工作流 + 8 references + 7 标准库 CLI + 3 空壳数据文件。数据层从未被真实数据验证。
