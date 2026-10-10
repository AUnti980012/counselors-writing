# Counselors-Writing（辅导员爆款文章）

> **v3.2.0-beta** — Skill `3.2.0` / KB `1.2.0` / Schema `1.2.0` / DB `3`。成稿审核与事实校验加固：审核完整性门禁、事实 grounding（携带来源正文片段）、内容契约、交付统计。

把高校辅导员的日常工作素材——一次谈心谈话、一场班会、一个学生事件——写成有传播力的公众号推文，并沉淀成越用越懂学校、越用越懂学生的案例库。

## 它是什么

Counselors-Writing 是一个 **Agent Knowledge Pipeline**：媒体研究 → 抓取 → 清洗 → 结构化提取 → 案例检索 → 分析 → 画像映射 → 写作 → 审核 → 输出 → 复盘沉淀。核心是**确定性引擎**（`scripts/kb.py`，Python + SQLite + 文件），LLM 只做语义步骤编排、不持有状态、不塞整库。

## 谁适合用 / 能解决什么

- **高校辅导员、班主任、思政工作者**：把「不知道写什么」「写出来没流量」「怕踩红线」变成一套可复用的选题→写作→质检流程。
- **能解决**：素材转选题、抓热点、报媒文风仿写、成文与思政七项自查、传播复盘沉淀，越用越懂这所学校、这群学生。

## 5 分钟第一次使用

```bash
# 1) 依赖探测（唯一第三方依赖 pydantic>=2）
python scripts/bootstrap.py
# 2) 初始化索引（幂等）
python scripts/kb.py db init
# 3) 放入案例素材 → document_id
python scripts/kb.py ingest --file 素材.txt
# 4) 选题（从素材提取选题信号；或检索已有选题）
python scripts/kb.py extract <document_id> --extractor topic_signal --llm-cmd "<LLM_COMMAND>"
python scripts/kb.py search topic <关键词>
# 5) 写作（案例 → 映射 → 草稿）
python scripts/kb.py extract <document_id> --extractor case_facts --llm-cmd "<LLM_COMMAND>"
python scripts/kb.py mapping --case <case_id> --profile pro-school --llm-cmd "<LLM_COMMAND>"
python scripts/kb.py write --mapping <mapping_id> --llm-cmd "<LLM_COMMAND>"
# 6) 审核（标点门禁 + 七项思政自查）
python scripts/kb.py punctuation --lang zh 正文.md
python scripts/kb.py audit --draft <draft_id> --llm-cmd "<LLM_COMMAND>"
# 7) 输出（audited output 带 --audit 写入血缘）
python scripts/kb.py output render --draft <draft_id> --audit <audit_id>
```

> 没有外部 LLM 命令时，用 `--prompt-only` 拿 prompt → 你的 Agent 生成 JSON → `--result <file>` 回灌，三步等价。详见下文「在不同 Agent 里使用」。

### 通用内容写作（政策 / 热点 / 指南，无学生案例）

```bash
# 1) 素材（抓取或粘贴）
python scripts/kb.py ingest --file 政策资料.txt
# 2) 内容简报（复用 TopicRecord 作为 Content Brief）
python scripts/kb.py extract <document_id> --extractor topic_signal --llm-cmd "<LLM_COMMAND>"
# 3) 通用写作（不需要 mapping、不需要伪造 case）
python scripts/kb.py write --topic <topic_id> --mode guide --llm-cmd "<LLM_COMMAND>"
# 4) 过门 + 输出（与案例写作一致，audited output 带 --audit）
python scripts/kb.py audit --draft <draft_id> --llm-cmd "<LLM_COMMAND>"
python scripts/kb.py output render --draft <draft_id> --audit <audit_id>
```

> 政策数字、调查数据、时间节点、机构名称必须来自来源素材；来源没有的写清楚是「推断/建议」。严禁把通用内容捏造成学生案例。

完整逐步说明见 `docs/user/quickstart.md`；典型工作流见 `docs/user/workflows.md`。

## 典型工作流

| 你要做的 | 入口 |
|---|---|
| 从素材到推文（主链） | `docs/user/workflows.md` |
| 抓热点 → 选题桥接 | `kb.py hotlist weibo\|tophub --top 20` + `references/hot-trend.md` |
| 报媒文风仿写 | `references/style-library.md` |
| 思政质检 | `references/ideological-review.md` + `kb.py audit` |
| 传播复盘沉淀 | `references/dissemination-review.md` |

## 没有某个兄弟 skill 时怎么办

可选兄弟 skill：`read` / `fetch-skill-main`（抓正文）、`write`（去 AI 味）、`web-access-main`（抖音 CDP）。全部用 `scripts/core/compat.py` 的 `resolve_sibling_skill()` 定位，**缺失时优雅降级**：提示你提供内容或规则，不编造、不静默失败。核心链路不依赖任何一个兄弟 skill。

## 在不同 Agent 里使用（Claude Code / Codex / 其他）

Core 不声明任何 LLM API 依赖。任何能「执行 CLI + 读写 JSON 文件 + 调用自身模型」的 Agent 都可接入，三种互斥模式：

```bash
python scripts/kb.py <command> ... --llm-cmd "<LLM_COMMAND>"   # 全自动
python scripts/kb.py <command> ... --prompt-only               # 拿 prompt，Agent 自己跑模型
python scripts/kb.py <command> ... --result <file>             # 回灌 Agent 产出的 JSON
```

统一契约见 `docs/integration/agent-integration.md`。

## 高级 CLI 索引（简表）

| 类别 | 命令 |
|---|---|
| 抓取/导入 | `fetch <url>`、`ingest`、`hotlist weibo\|tophub` |
| 检索 | `search case\|style\|topic <kw> [--get ID]` |
| 语义 | `extract`、`analysis`、`mapping`、`write`、`audit` |
| 确定性 | `punctuation`、`output render`、`context-for-write` |
| 运维 | `db init/rebuild/import-legacy`、`task`、`backup`、`gc`、`cache`、`schemas export` |
| 校验 | `validate <entity>`、`schemas export --check`、`test` |

完整参数以 `python scripts/kb.py --help` 为准。

## 依赖

- Python 3.10+，pydantic≥2（唯一第三方依赖；可选 readability-lxml + html2text）
- 抖音/动态页需 Node.js 22+ 与 web-access 的 CDP 代理（可选实验项）

## 高级工程文档

- 系统设计：`docs/architecture/`（架构 / 数据契约 / 存储 / 生命周期）
- Agent 接入：`docs/integration/agent-integration.md`
- 历史施工记录：`docs/history/`（milestone-pack、migration-plan、各类审计/报告）
- 版本变更：`CHANGELOG.md`

全量测试必须通过（`python scripts/kb.py test`）；当前基线测试数量见 `CHANGELOG.md`。

## License

MIT（见 `LICENSE`）。
