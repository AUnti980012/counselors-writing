# AGENTS.md（Coding Agent / Maintainer Instructions）

> 本文件是**开发/维护入口**，面向要改这个仓库的 Coding Agent。运行时 Agent 读 `SKILL.md`，人类用户读 `README.md`。三者职责分离，互不复述。

## 1. 当前状态

- M0→M9 已完成（2026-10-04），仓库已收敛为可迭代的 Agent Knowledge Pipeline。
- M10 产品化收口（Agent Adapter Contract：`--result` 回灌 + docs 分层）已落地。
- M10.1 最终运行时加固已落地：`--result` 误回灌保护（绑定 wrapper）、SQLite 连接生命周期修复、Audit 全文规则澄清、Retro 增量触发。
- M10.2 通用内容写作已落地：`write --topic`（无 mapping 的 Generic 路径）+ schema_summary 嵌套 $ref 展开 + audit mode 措辞。能力模型 = Case-based（Case→optional Analysis→Mapping→Writing）+ Generic（Topic→Sources→Writing），共同收口 Punctuation→Audit→Output。
- M11 去 AI 味确定性门禁已落地：`kb.py deai`（零 LLM，Humanizer-zh 31 模式机械子集规则化）+ audit 注入 `deai` 检查项。
- 版本：Skill `3.2.0` / KB `1.2.0` / Schema `1.2.0` / DB `3`。
- **状态：3.2.0 Beta 版（成稿审核与事实校验加固）。**

## 2. 单一真相源

- **数据契约**：`scripts/core/schema.py`（Pydantic）是唯一真相源；`data/schemas/*.json` 是导出产物，禁止手改。
- **CLI**：`scripts/kb.py` 是唯一统一入口；`scripts/core/*.py` 是确定性核心。
- **方法规则**：`references/*.md` 是专项方法的唯一规则源。

## 3. 修改代码前的安全规则

1. 先读 `docs/architecture/architecture.md` 与 `docs/architecture/data-contract.md` 了解目标结构与数据边界。
2. 不重构 M0–M9 核心架构（SQLite/FTS5、canonical 文件、四层 cache、Task/Resume、Backup/GC、Pydantic 单一真相源、fetch→…→output 流程），除非为修复明确的契约问题。
3. 不加无意义扩容：MySQL/Redis/Kafka/ES/向量库/独立模型服务/MCP 必选依赖/Agent 平台专属 API。
4. 事实/推断分离、PII 硬门槛、整库不进 LLM 上下文，这三条铁律改动必须同步更新测试。
5. 新增 CLI 接口必须三模式（`--llm-cmd`/`--prompt-only`/`--result`）互斥且可测试；不引入 `shell=True`。

## 4. 测试门禁

```bash
python scripts/kb.py test                                    # 全量测试（退出码透传）
python -m unittest discover -s scripts/tests -t scripts      # 等价原生命令
python scripts/kb.py schemas export --check                  # 契约零漂移（exit 1 = 漂移）
```

改动后**全量必须通过**；新增功能必须补最小必要测试（含回归锁）。

### 灰度/MTest 报告的 LLM 调用统计口径（P3-4）

- 统计单位 = **Physical LLM Invocation**（真正向 LLM provider / `--llm-cmd` 发起的一次模型调用；`--result` 回灌不产生模型调用）。
- Retry 是 Physical Invocation 的属性，不额外计数：`1 initial + 1 retry = 2 physical calls = 1 retry`。
- 分开统计 Physical / Retries / Cache Hits / Cache Misses；`TOTAL = Σ 各 scenario physical + Σ explicit extra/contract-smoke physical`。
- 不计入 Physical LLM：Python/SQLite/FTS5/文件/Pydantic/CLI 解析/cache lookup/MCP tool call/HTTP fetch/HTML parser。
- Token 必须区分 **Actual**（provider 报告）与 **Estimated**（字符估算，如 char/1.6），不得混写。

## 5. 版本与文档一致性要求

- 四种版本号语义分离，禁止无理由同步改：Skill（用户可见行为）/ KB（CLI 接口）/ Schema（数据契约）/ DB（SQLite migration）。
- 文档写了 `--result`，代码就必须支持；代码支持了，契约说明就必须写。禁止「文档有代码无」或「代码有契约无」的漂移。
- Runtime 文档（SKILL/README/references/integration）禁止出现平台专属命令（如外部 LLM 命令名、MCP 工具名、第二模型名）与用户专属绝对路径。

## 6. 本轮（P3 收口 + 正式版发布）任务范围

P3-1 ~ P3-4 已全部收口，随 Skill 3.0.0 / KB 1.0.0 发布为正式版（GA）：

1. **P3-1 schema_summary 约束**：`--prompt-only` 的字段摘要暴露 enum / minimum / maximum / required / array item / 一层 `$ref`，且不复制完整 JSON Schema（`topic` 摘要 < 1500 字符）。
2. **P3-2 output audit lineage**：标准 audited output 统一为 `output render --draft <id> --audit <audit_id>`，文档与代码一致。
3. **P3-3 binding-before-cache**：`--result` 的 operation/input_digest 绑定校验经 `preflight_binding` 钩子在 Cache Lookup **之前**执行；Cache HIT 无法绕过 binding validation（7 个 Gate 由 `test_result_roundtrip.BindingBeforeCacheTests` 锁定）。
4. **P3-4 LLM 统计口径**：Physical LLM Invocation 定义 + Retry 不重复计数 + MCP/脚本/校验不计入（见 §4）。
5. 全量回归 500/500、schema 18/18 零漂移、Release Gate 通过。

## 7. 历史文档路径说明

- `docs/history/` 存历史施工记录（milestone-pack、migration-plan、各类审计/报告）。**只在追溯设计决策或回归历史问题时才读，不要求每次任务先全文阅读。**
- `docs/architecture/` 存系统设计；`docs/user/` 存用户文档；`docs/integration/` 存 Agent 接入契约。
