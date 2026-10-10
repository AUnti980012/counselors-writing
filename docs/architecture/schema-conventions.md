# Schema Conventions（契约约定）

> 单一真相源：`scripts/core/schema.py`（Pydantic ≥2）。`data/schemas/*.json` 由 `kb.py schemas export` 生成（17 份：16 实体 + common），**禁止手改**；`kb.py schemas export --check` 检测漂移（exit 1）。

## 1. 命名与 id 前缀

- 字段一律 snake_case；JSON 键与 Python 字段同名（LLM 直接产出同构 JSON）。
- id 模式：`^[a-z0-9][a-z0-9_-]{2,63}$`；**推荐前缀**：

| 实体 | 前缀 | 实体 | 前缀 |
|---|---|---|---|
| task | `task-` | analysis | `ana-` |
| artifact | `raw-`/`fin-`/…（`^[a-z]+-\d{8}-[0-9a-f]{8}$`） | mapping | `map-` |
| source | `src-` | profile | `pro-` |
| document | `doc-` | audit | `aud-` |
| chunk | `chk-` | effect | `eff-` |
| case | `case-` | draft | `drf-` |
| style | `style-` | extraction | `ext-` |
| topic | `top-` | evidence | `evd-` |

- artifact_id 是唯一特例（含日期+8hex，血缘账本按日期归档）；content_hash = 64 位小写 hex；schema_version = semver `^\d+\.\d+\.\d+$`。

## 2. 有界性分级（JSON 边界红线）

| 分级 | 上限 | 适用 |
|---|---|---|
| 超短 | ≤100-300 字符 | 标题、摘录（EvidenceRef.excerpt 300 / Evidence.excerpt 500 / Artifact.summary 500） |
| 中 | ≤500-2000 | 陈述、特征条目、反馈、风险、素材摘录 |
| 长 | ≤3000-5000 | case.background/methods、draft 段落、style.structure |
| 数组 | maxItems 3-500（逐字段声明） | 最大 document.chunk_ids 500、task.context_refs 总 id ≤200 |
| 字典 | maxProperties ≤20；值 ≤500-1000 由 Pydantic field_validator 强制 | 导出的 JSON Schema 对 additionalProperties 无法表达值长上限，跨工具消费者以 Pydantic 与本文件为准 |

新增字段必须先声明上限，无界字段视为契约违规。

## 3. 时间戳

- Python 托管：`created_at`/`updated_at` 带默认值（UTC now），LLM 输出可省略；显式给出时必须带时区（ISO8601，如 `2026-09-30T10:00:00+08:00`），无时区即拒绝。
- `expires_at`/`published_at` 可选，给则必带时区。
- 已知限制：导出的 JSON Schema 只能声明 `format: date-time`，无法表达「必须带时区」；该约束由 Pydantic 强制（字段 description 已标注），跨工具消费者需自行实现。

## 4. 事实/推断硬规则

见 `docs/architecture/data-contract.md` §6。要点：事实类（documented_fact/source_claim）缺 evidence_ids、AI 类（ai_inference/derived_pattern/recommendation）缺 basis → Pydantic model_validator 直接拒绝，错误为路径级（如 `documented_facts.0.evidence_ids`），供 M4 自纠正循环（≤2 次）消费。

## 5. 枚举（权威列表在 common.json `enums`）

关键枚举：task_status 16 值、failure_code 12 值、fact_type 5 值、verdict pass/warn/fail、retention temporary/permanent/task_bound、extraction 自纠正 attempts ≤3（初次 + ≤2 次修正，MAX_SELF_CORRECT=2 见 core/validate.py）。

## 6. $ref 解析规则

实体 schema 文件内的 `"$ref": "#/$defs/Xxx"` 统一解析到 `common.json` 的 `$defs`（共享模型：FactClaim/EvidenceRef/RefPair/TopicAngle 等 22 个）。例外：`seed.schema.json` 是种子文件信封，自含 `StyleRecord` $defs（实体模型不进 common 共享区，避免悬空引用）。运行时校验以 Pydantic 模型为准，JSON 文件是跨工具可读的契约文档。

## 7. 责任边界（RESPONSIBILITY BOUNDARY）

1. Schema 是确定性基础设施：LLM 不得动态定义/修改 schema；LLM 输出必须**符合**契约。
2. 校验确定性：`kb.py validate <entity>`（stdin JSON → 机器可读 {valid, errors:[{path,message,type}]}；exit 0/2；用法错误 4；依赖缺失 3）。
3. extra=forbid 全实体生效：未知键一律拒绝（对齐 V1 profiles.py 无白名单缺陷的修复目标）。

## 8. 契约变更流程

```
改 scripts/core/schema.py（+注释说明动机）
  → python -m unittest discover -s scripts/tests -t scripts（全绿）
  → python scripts/kb.py schemas export（重新导出）
  → git diff data/schemas/（审查导出 diff；本仓库非 git 时人工比对 export --check）
  → schema_version 按语义化版本递增 + 更新 docs/architecture/data-contract.md
```
