# 工程修复报告：`/cw-hot` 审核关卡与下游质量控制

日期：2026年10月10日
范围：`/cw-hot` 内容风险筛查关卡 + 下游 9 命令统一质量门禁 + 回归测试
状态：已完成（全量 588/588 测试通过，schema 18/18 零漂移）

---

## 1. 根因与代码证据

**根因**：`/cw-hot` 原来只做「抓取 → 标准化 → 跨平台去重 → 落快照」，没有任何内容风险筛查，也没有把审核状态持久化。下游命令（`/cw-research`、`/cw-topic`、`/cw-write`）消费的是这份未筛查的原始热点清单，导致未筛查内容可以一路进入选题、写作、成稿。

代码证据（修复前的行为）：

- 旧 `cmd_hot` 路径 = `fetch_hotspots → save_snapshot → 直接打印`，无筛查步骤。旧 `cw-hot.md` 步骤 3 只是「用 LLM 区分原始热度与选题价值，不修改确定性清单数据」——这是选题价值评估，不是内容风险筛查，也没有 `review_status` 落盘。
- 下游 `cw-research` 旧步骤 1 = `cw.py hot resolve <ref>` 后直接 `kb.py fetch`，返回的条目里没有 `review_status`/`risk_flags`，下游无从判断「这条热点能不能用」。
- 结论：**抓取成功被隐式当成「可用」**，任何记录都可能在无风险筛查的情况下被下游无条件采用。

## 2. 受影响命令与数据流

| 命令 | 修复前 | 修复后数据流 |
|---|---|---|
| `/cw-hot` | 抓取+去重+落快照，无筛查 | 抓取 → 标准化 → 去重 → **确定性筛查 →（可选）语义复核 → 审核结果落 `hotlist_review` artifact → 按审核结果输出** |
| `/cw-research` | resolve 后直接 fetch | resolve 读 `review_status`/`risk_flags`，`needs_review`/`rejected` 不得无条件升级为已核实事实 |
| `/cw-topic` | 直接 fetch 选题 | 优先 `approved`，`caution` 写入 TopicRecord.risks，`needs_review`/`rejected` 不得无条件提升 |
| `/cw-write` | Generic 路径直接 ingest | 不得把未筛查热点扩写成确定新闻 |
| `/cw-audit` | — | 八项 LLM 自查必须齐全且唯一，缺检查项不得通过 |
| `/cw-revise` | — | 变更的事实须重新核验证据，不得复用旧审核结论 |
| `/cw-library` | — | 不得把待核实素材当已验证证据入库 |
| `/cw-export` | — | 审核不完整（无有效 AuditRecord）须标注，不得伪装终稿 |
| `/cw-status` | — | 缓存命中须重新确认审核状态/版本/依赖仍有效 |
| `/cw-continue` | — | 恢复时须重新确认审核状态/版本/依赖，不得在缓存命中时跳过必要审核 |

下游统一门禁机制：所有下游命令都经 `hot resolve` / `hot latest` 读**持久化的**审核状态（`load_latest_review_for(review_content_hash(snapshot))`），缺失审核的记录由 `attach_review` 显式标记 `review_method=unreviewed`、`review_status=needs_review`，绝不静默当通过。

## 3. 变更文件与 schema

**核心新增**

- `scripts/core/hotreview.py`（新，424 行）：确定性筛查 + 语义复核 + 审核 artifact 持久化 + 汇总输出。
  - `deterministic_review`（:108）：相关且零风险 → `approved`；仅不相关 → `caution`；命中实质风险词 → `needs_review`（保守，绝不因热度自动通过）。
  - `_apply_llm_review`（:174）：硬风险标签 floor——`sensitive_event`/`politics_legal`/`privacy_minor`/`misleading_conclusion` 即使 LLM 返回 `approved` 也降为 `caution`。
  - `review_hotspots`（:332）：主入口；LLM 抛异常不使筛查失败，语义复核降级为「未执行」，确定性结论保留，`review_method` 如实标记。
  - `save_review`（:237）：落 `hotlist_review` artifact（retention=permanent），独立于原始快照，绝不改写快照字段。

**schema 单一真相源（`scripts/core/schema.py`）**

- `REVIEW_STATUS`/`REVIEW_METHOD`/`RISK_FLAGS`（:87–100）——统一审核状态枚举 `approved`/`caution`/`needs_review`/`rejected` 与 8 个风险标签。
- `HotReviewItem`（:932–952）：`fetch_status` 与 `review_status` 强制分离（`extra="forbid"`）。
- `HotReviewRecord`（:955–971）：`review_id` 按快照内容哈希稳定派生，`semantic_review_executed`/`semantic_review_failed` 区分「执行失败」与「通过」。
- `COMMON_DEFS`（:1025–1026）与 `ENUM_VALUES`（:1059）注册；`data/schemas/common.schema.json` 已重新导出（296 行新增），零漂移。

**CLI 与命令层**

- `scripts/cw.py`：`cmd_hot`（抓取→筛查→按结果输出，`--llm-cmd`/`--prompt-only`/`--result` 三模式）、`cmd_hot_latest`、`cmd_hot_resolve`、`_review_llm_fn`。
- `scripts/core/commands.py`：11 条 COMMAND_SPECS 更新（`cw-hot` 路由/输出重写，下游命令加风险传播 notes）。
- `commands/cw-hot.md` 重写；`commands/cw-research.md`/`cw-topic.md`/`cw-write.md`/`cw-audit.md`/`cw-revise.md`/`cw-library.md`/`cw-export.md`/`cw-status.md`/`cw-continue.md` 补审核门禁铁律。
- `references/hot-trend.md`：桥接前必须过 `/cw-hot` 筛查。
- `AGENTS.md`：新增 M13 审核关卡状态。

**不新增 schema ENTITY、不 bump DB**：`hotlist_review` 是新的 artifact type（复用 `ArtifactStore`），不是 schema 实体，因此无 DB migration。

## 4. `/cw-hot` 实际行为

`python scripts/cw.py hot --sources weibo,tophub --top 20 --save [--llm-cmd CMD | --prompt-only | --result FILE]`

1. `fetch_hotspots`：逐源抓取 + 状态聚合（success/blocked/access_restricted/unavailable/timeout/rate_limited/parse_failed）+ 跨平台去重。
2. `--save` 且有结果 → 落 `hotlist` 快照（原始来源/热度/抓取时间/标题原样保留）。
3. `--prompt-only` → 输出批量语义复核 prompt + `pending_hot_ids`（不调模型、不落审核）。
4. `review_hotspots`：确定性筛查 +（可选）语义复核 → `HotReviewRecord.model_validate` 校验 → 落 `hotlist_review` artifact（review_id 由快照 content_hash 稳定派生，同快照复用不覆盖旧审核）。
5. 输出：带 `fetch_status`/`review_status`/`risk_flags`/`review_reason`/`review_method`/`reviewed_at`/`source_refs` 的清单 + overview（raw_count/dedup_count/screened_count/approved/caution/needs_review/rejected/unscreened/fetch_failed）+ 逐源抓取状态。

铁律执行：抓取成功 ≠ 审核通过；未执行筛查标 `needs_review`（`review_method=unreviewed`/`deterministic`）；任何记录不得仅因热度自动通过；硬风险标签语义复核最多 `caution`；LLM 不可用/未执行与「通过」明确区分。

## 5. 下游门禁验证

下游命令通过 `cw.py hot resolve` / `hot latest` 拿到 `attach_review` 合并后的字段：

- `attach_review`（hotreview.py:287）：有审核 → 合并 `review_status`/`risk_flags`/`review_method` 等；无审核 → `review_method=unreviewed`、`review_status=needs_review`。
- `load_latest_review_for`（:250）：按 `snapshot_content_hash` 匹配，保证审核与快照版本一致（防过期复用）。
- 因此：未筛查/过期/失败的记录在任何下游都是 `needs_review`，不可能被静默放行。COMMAND_SPECS 与 9 个 `commands/cw-*.md` 的铁律文本与这一路由一致。

## 6. 真实测试结果

- 全量：`python -m unittest discover -s scripts/tests -t scripts` → **Ran 588 tests OK**（566 原有 + 22 新增）。
- 新增 `scripts/tests/test_hotreview.py` **22/22 通过**：
  - 确定性筛查 7 项（含「抓取成功 ≠ 审核通过」「高热度不自动通过」）；
  - 语义复核 5 项（含「硬风险标签 LLM 不得 approved」「软风险可放行」「非法 JSON 抛错」）；
  - 持久化 6 项（含「审核 artifact 独立于快照」「同快照复用不重跑 LLM」「快照变化生成新 review_id 不覆盖旧审核」「overview 计数与逐条一致」「部分失败保留有效结果」「LLM 不可用标未执行不伪装通过」）；
  - attach_review 2 项；
  - CLI 冒烟 3 项（`cmd_hot --prompt-only` 产出语义复核 prompt、`cmd_hot` 路由到 `review_hotspots`、`cmd_hot_resolve` 附审核状态）。
- 契约零漂移：`python scripts/kb.py schemas export --check` → **OK：18 个 schema 与 scripts/core/schema.py 一致（exit 0）**。

测试验证的是**实际路由/持久化状态/门禁判定/结果**（`review_hotspots` 复用不重跑 LLM、`attach_review` 对无审核条目标 `unreviewed`、硬风险 floor 等），不是文件存在性或 prompt 里出现「审核」二字。

## 7. 未完成 / 不可测 / 剩余风险

- **确定性筛查是标题级 regex**：语义上风险但标题不命中关键词的条目，只能靠语义（LLM）复核兜底；LLM 不可用时保持 `needs_review`（保守，但会过度标记），属预期而非缺陷。
- **`rejected` 仅由语义复核产生**：确定性筛查从不产出 `rejected`（只到 `needs_review`），因此纯确定性运行不会主动拒绝——这是「拿不准宁可 needs_review」的设计取舍。
- **未做真实网络抓取测试**：微博/tophub 抓取在 CI 里只 mock，未对真实源端到端验证；真实抓取可能因反爬/限流失败（`fetch_status` 已如实标注，不伪装成功）。
- **语义复核依赖外部 LLM CLI**：`--llm-cmd` 的模型质量/输出格式未对真实 provider 端到端验证（仅 mock `llm_fn`）；`--result` 回灌有 `expected_operation` 绑定校验兜底。
- **未提交**：本次全部改动仍在工作区未 commit（见 §8），需人工 review 后提交。

## 8. git 状态与数据迁移

`git status --short`（工作区，未提交）：

- **已修改**：`AGENTS.md`、`data/schemas/common.schema.json`、`references/hot-trend.md`、`scripts/core/schema.py`。
- **未跟踪**：`commands/`、`scripts/core/commands.py`、`scripts/core/hotreview.py`、`scripts/core/hotspots.py`、`scripts/cw.py`、`scripts/tests/test_commands.py`、`scripts/tests/test_hotreview.py`、`scripts/tests/test_hotspots.py`。

（`scripts/core/hotspots.py`、`scripts/cw.py`、`scripts/core/commands.py` 等为上一任务 Slash Command Layer 的产物，同样尚未提交。）

**数据迁移**：无需迁移。

- 未新增 schema ENTITY（`HotReviewItem`/`HotReviewRecord` 是 `COMMON_DEFS` 中的独立 model，非知识库实体），未 bump DB 版本（仍 DB 3）。
- `hotlist_review` 是新的 artifact type，落在 `ArtifactStore` 既有持久化里；现有用户数据（已抓热点、缓存、既有 artifact）全部保留，不被改写或删除。
