# AGENTS.md（Coding Agent / Maintainer Instructions）

> 本文件是**开发/维护入口**，面向要改这个仓库的 Coding Agent。运行时 Agent 读 `SKILL.md`，人类用户读 `README.md`。三者职责分离，互不复述。

## 1. 当前状态

- M0→M9 已完成（2026-10-04），仓库已收敛为可迭代的 Agent Knowledge Pipeline。
- M10 产品化收口（Agent Adapter Contract：`--result` 回灌 + docs 分层）已落地。
- 版本：Skill `2.1.0` / KB `0.10.0` / Schema `1.0.0` / DB `3`。

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

## 5. 版本与文档一致性要求

- 四种版本号语义分离，禁止无理由同步改：Skill（用户可见行为）/ KB（CLI 接口）/ Schema（数据契约）/ DB（SQLite migration）。
- 文档写了 `--result`，代码就必须支持；代码支持了，契约说明就必须写。禁止「文档有代码无」或「代码有契约无」的漂移。
- Runtime 文档（SKILL/README/references/integration）禁止出现平台专属命令（如外部 LLM 命令名、MCP 工具名、第二模型名）与用户专属绝对路径。

## 6. 本轮（M10）开发任务范围

1. `--result <file>` 回灌（已完成，复用 `llm_fn_from_file`）。
2. docs 分 `user/` `integration/` `architecture/` `history/` 四层（已完成）。
3. SKILL/AGENTS/README 三文件职责分离（本文件）。
4. LICENSE 落地 + CHANGELOG 更新。

## 7. 历史文档路径说明

- `docs/history/` 存历史施工记录（milestone-pack、migration-plan、各类审计/报告）。**只在追溯设计决策或回归历史问题时才读，不要求每次任务先全文阅读。**
- `docs/architecture/` 存系统设计；`docs/user/` 存用户文档；`docs/integration/` 存 Agent 接入契约。
