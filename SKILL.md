---
name: counselors-writing
description: 帮助高校辅导员（大学辅导员、班主任、思政工作者）把日常工作素材——一次谈心谈话、一场班会、一个学生事件——写成公众号爆款推文，并沉淀成越用越懂学校、越用越懂学生的案例库。当用户需要研究人民日报、光明日报、中国青年报的话术与文风（爆款研究/仿写）、做选题转化（判断学生真正关心什么、找矛盾冲突、从成长/选择/责任/关系/家国情怀切入）、抓取微博热搜/tophub 热点、撰写推文并边写边做思政质检（政治方向/事实依据/价值表达/学生隐私/标签化语言/版权风险/AI痕迹七项）、或做传播复盘时，都应使用本 skill。触发词：辅导员、大学辅导员、辅导员公众号、公众号推文、爆款、爆款文章、标题、仿写、选题、热点、思政、谈心谈话、班会、学生工作、育人故事、校园公众号、传播复盘。
license: MIT
compatibility: "需要 Python 3.10+ + pydantic>=2（可选 readability-lxml + html2text）；抖音/动态页需 Node.js 22+ 与 web-access 的 CDP 代理；思政质检单通道（七项结构化自查 + 标点门禁）；无任何 Agent 平台专属依赖"
metadata:
  audience: 高校辅导员
  display_name: "Counselors-Writing"
  version: "2.1.0"
---

# Counselors-Writing（辅导员爆款文章）

把辅导员日常工作素材，写成有传播力的公众号推文；用案例库和风格库，让每次写得都比上次更懂这所学校、这群学生。

## 1. Identity / Purpose

本 skill 是 Agent Knowledge Pipeline。核心是**确定性引擎** `scripts/kb.py`（Python + SQLite + 文件）；LLM 只做语义步骤编排，不持有状态、不塞整库。你（Agent）负责理解用户 + 调用模型；Python 负责所有字段注入、脱敏、校验、索引、缓存、血缘。

## 2. Trigger / Routing

| 用户意图 | 读这个 reference | 走完整链 |
|---|---|---|
| 仿写 / 研究报媒文风 | `references/style-library.md` | 否 |
| 素材转选题 / 判断学生关心什么 | `references/topic-selection.md` | 否 |
| 抓热点 / 微博热搜 / tophub / 抖音 | `references/hot-trend.md` | 否 |
| 写推文 / 成文 / 边写边审 | `references/writing-companion.md` | 是 |
| 思政质检 / 审校 | `references/ideological-review.md` | 否 |
| 复盘 / 传播效果 | `references/dissemination-review.md` | 否 |

判断不了就问用户：选题、抓热点、写正文、质检、还是复盘？

## 3. Runtime Workflow（从素材到推文）

1. **收集素材**：信息不足先问；用户提供内容 `kb.py ingest [--file f] [--url u]`。
2. **案例知识**：`kb.py extract <doc> --extractor case_facts`。
3. **选题**：`kb.py extract <doc> --extractor topic_signal` 产出 TopicRecord；`kb.py search topic <kw>` 检索已有选题。`analysis --case <id>` 产出 AnalysisRecord（学生关心什么/核心冲突/角度），与选题是**两个实体**，不混用。
4. **映射**：`kb.py mapping --case <id> --profile pro-school`。
5. **写作**：`kb.py write --mapping <id> [--topic/--analysis/--style]`。
6. **过门**：`kb.py punctuation --lang zh <正文>`（零 LLM）+ `kb.py audit --draft <id>`（七项自查）。
7. **交付**：`kb.py output render --draft <id>`。
8. **复盘沉淀**：`kb.py case add` / `kb.py style add`（不阻塞交付）。

## 4. Hard Rules

- **学生隐私硬门槛**：命中即脱敏改写（改名/去学号/模糊事件组合）；无法脱敏则提示「不建议公开」。
- **事实/推断强制分离**：documented_fact/source_claim 必带 evidence_ids；ai_inference/derived_pattern/recommendation 必带 basis；拿不准标「待核实」。
- **禁止整库/整文进 LLM 上下文**：两级检索（L1 紧凑投影 / L2 显式 `--get`）+ 指针优先，正文/raw 只落盘不回流。
- **禁止绕过登录/验证码/IP 限制/付费墙/Bot 检测**：403/429 → 有限重试 → 官方替代源 → 用户提供内容。
- **每交付一次正文，都尝试沉淀一条经验**（不阻塞交付）。

## 5. Token / Context Rules

- 默认只读必要信息；L1 检索有界（style 硬上限 10），L2 必须显式 `--get`。
- 阶段间只传 `id / path / hash / status / schema_version / 摘要`，不传全文。
- 不把 raw HTML、全文、整库、重复 prompt 回灌；不为「解释完整」在 stdout 打印大段正文。

## 6. Agent Adapter Contract

LLM 语义命令 5 个（`extract` / `analysis` / `mapping` / `write` / `audit`）统一三模式（互斥）：

| 模式 | 行为 |
|---|---|
| `--llm-cmd "<LLM_COMMAND>"` | Core 直接执行外部命令，无 shell |
| `--prompt-only` | 只输出最小 prompt（供 Agent 编排） |
| `--result <file>` | 读 Agent 已产出的 JSON 回灌，复用同一落盘路径 |

`<LLM_COMMAND>` 只表示可选外部命令接口，不暗示具体 Agent。闭环：`--prompt-only` 拿 prompt → Agent 用自身模型产出 JSON → `--result` 回灌 → Python 校验/持久化。详见 `docs/integration/agent-integration.md`。

## 7. Output Rules

- 命令输出机器可读 JSON（`kb.py` 统一 `_print_json`）；正文/内容用指针 + `--content` 才打印。
- 标点门禁 exit 0 通过 / 2 有 findings 或 ko 暂不支持；审核不通过 exit 2（门禁语义）。
- 用法错误 exit 4、依赖缺失 exit 3、数据缺失 exit 1、校验失败 exit 2。

## 8. Reference Loading Rules

- 只读当前步骤需要的 reference，不预读全部 8 份。
- 方法文档 `references/*.md` 只留方法与命令；数据落点/架构细节不在此重复。
- 兄弟 skill（`read` / `fetch-skill-main` / `write` / `web-access-main`）用 `scripts/core/compat.py` 的 `resolve_sibling_skill()` 定位，缺失时提示用户提供内容/规则，优雅降级。
- 历史施工记录只存在于 `docs/history/`，**不作为普通运行入口加载**。
