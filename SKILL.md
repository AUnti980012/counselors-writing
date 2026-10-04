---
name: counselors-writing
description: 帮助高校辅导员（大学辅导员、班主任、思政工作者）把日常工作素材——一次谈心谈话、一场班会、一个学生事件——写成公众号爆款推文，并沉淀成越用越懂学校、越用越懂学生的案例库。当用户需要研究人民日报、光明日报、中国青年报的话术与文风（爆款研究/仿写）、做选题转化（判断学生真正关心什么、找矛盾冲突、从成长/选择/责任/关系/家国情怀切入）、抓取微博热搜/tophub 热点、撰写推文并边写边做思政质检（政治方向/事实依据/价值表达/学生隐私/标签化语言/版权风险/AI痕迹七项）、或做传播复盘时，都应使用本 skill。触发词：辅导员、大学辅导员、辅导员公众号、公众号推文、爆款、爆款文章、标题、仿写、选题、热点、思政、谈心谈话、班会、学生工作、育人故事、校园公众号、传播复盘。
license: MIT
compatibility: "需要 Python3 + pydantic>=2（可选 readability-lxml + html2text）；抖音/动态页需 Node.js 22+ 与 web-access 的 CDP 代理(localhost:3456)；思政质检单通道（七项结构化自查 + 标点门禁，无第二意见）；无 Claude 专属依赖，Codex 可用"
metadata:
  audience: 高校辅导员
  display_name: "Counselors-Writing"
  version: "2.0.0"
---

# Counselors-Writing（辅导员爆款文章）

把辅导员的日常工作素材，变成有传播力的公众号推文；用案例库和风格库，让每次写得都比上次更懂这所学校、这群学生。

本 skill 是 Agent Knowledge Pipeline：媒体研究 → 抓取 → 清洗 → 结构化提取 → 案例检索 → 分析 → 画像映射 → 写作 → 审核 → 输出 → 复盘沉淀。核心是**确定性引擎**（`scripts/kb.py`，Python + SQLite + 文件），LLM 只做语义步骤编排，不持有状态、不塞整库。

## 先判断用户要什么（路由）

| 用户意图（关键词） | 读这个 reference | 走完整链路 |
|---|---|---|
| 仿写 / 研究报媒文风 / 爆款研究 | `references/style-library.md` | 否 |
| 素材转选题 / 判断学生关心什么 / 选题角度 | `references/topic-selection.md` | 否 |
| 抓热点 / 今日热搜 / 微博热搜 / tophub / 抖音 | `references/hot-trend.md` | 否 |
| 写推文 / 成文 / 正文 / 边写边审 | `references/writing-companion.md` | 是 |
| 思政质检 / 审校 / 检查政治方向 / 隐私 | `references/ideological-review.md` | 否 |
| 复盘 / 传播效果 / 标题行不行 / 开头留人 | `references/dissemination-review.md` | 否 |

判断不了时，先问用户「你要我帮你做哪一步：选题、抓热点、写正文、思政质检、还是复盘？」

## 主工作流（用户要「从素材到推文」或「成文」时走这里）

每步的自然语言意图 → `scripts/kb.py` 子命令等价（确定性核心）：

1. **收集素材**：素材缺失或信息不足时先问；用户提供内容用 `kb.py ingest [--file f] [--url u]`。
2. **选题转化**：读 `references/topic-selection.md` → `kb.py search topic <kw>` / `kb.py analysis --case <id>` 产出选题卡。
3. **可选抓热点**：读 `references/hot-trend.md` → `kb.py hotlist weibo|tophub --top 20` 找「热点 × 学生工作」接口，不强蹭。
4. **陪伴创作**：读 `references/writing-companion.md` → `kb.py write --mapping <id> [--topic/--analysis/--style] --llm-cmd "claude -p"` 白名单上下文写作。
5. **过门**：`kb.py punctuation --lang zh <正文>`（标点门禁，零 LLM）+ `kb.py audit --draft <id> --llm-cmd "claude -p"`（单通道七项自查）。
6. **交付**：`kb.py output render --draft <id>` → 正文 + 质检报告。
7. **传播复盘**：读 `references/dissemination-review.md` → `kb.py case add` / `kb.py style add` 沉淀（不阻塞交付）。

## 自然语言等价（Codex / Claude Code 通用，路由到确定性命令）

| 用户说 | 等价命令 |
|---|---|
| 抓今天微博热搜 / 热点 | `kb.py hotlist weibo --top 20` |
| 抓这篇报媒正文 | `kb.py fetch <url>`（三级回退，正文落 cache 不进上下文） |
| 查案例库 | `kb.py search case <kw> --top N [--get CASE_ID]` |
| 查风格库 | `kb.py search style <kw>`（种子 + 用户条目） |
| 看学校画像 | `kb.py profile dump` / `kb.py profile get <key>` |
| 对比两个案例 | `kb.py analysis --case <a> --case <b> --llm-cmd "claude -p"` |
| 案例 × 画像映射 | `kb.py mapping --case <id> --profile pro-school --llm-cmd "claude -p"` |
| 打印写作上下文 | `kb.py context-for-write --mapping <id>` |
| 标点检查 | `kb.py punctuation --lang zh <file>` |
| 跑全量测试 | `kb.py test` |

## 铁律

- **学生隐私是硬门槛**：命中即默认脱敏改写（改名 / 去学号 / 模糊事件组合），不硬推；无法脱敏则提示「不建议公开」。
- **事实/推断强制分离**：documented_fact/source_claim 必带 evidence_ids；ai_inference/derived_pattern/recommendation 必带 basis。拿不准就标注「待核实」，绝不编造。
- **禁止把整库/整文塞进 LLM 上下文**：两级检索（L1 紧凑投影 / L2 显式 `--get`）+ 指针优先，正文/raw 只落盘不回流。
- **每交付一次正文，都尝试把一条经验沉淀进案例库**（不阻塞交付）。

## 依赖的既有 skill（直接复用，不重造）

> 兄弟 skill 均用 `scripts/core/compat.py` 的 `resolve_sibling_skill()` 定位（env
> `FUDAOYUAN_SKILLS_DIR` → 同父目录探测 → None 优雅降级），无跨平台硬编码路径、
> 无 Claude 专属语法；缺失时提示用户提供内容/规则，不编造。

- 抓报媒正文 → `read`（`scripts/fetch.sh` / `fetch_local.py`）；备选 `fetch-skill-main`（`scripts/fetch.py`）。
- 去 AI 味 → `write`（`references/write-zh.md`，24 类 AI 痕迹 + 禁用词替换表）。
- 标点门禁 → `scripts/kb.py punctuation`（legacy 薄封装 `scripts/check_punctuation.py` 仍可用）。
- 抖音/动态页 → `web-access-main`（CDP 浏览器，`localhost:3456`；`core/compat.py` 的 `CdpBackend` 带登录同意门禁）。

## 数据落在哪（文件权威 + SQLite 索引）

- 权威内容 `data/knowledge/<entity>/<id>.json`（案例/风格/选题/分析/映射/画像/审核/草稿…），追加式永不整体重写。
- 索引 `data/index.db`（SQLite + FTS5，可随时 `kb.py db rebuild` 从 canonical 重建）。
- 血缘账本 `data/registry/artifacts.jsonl`；阶段产物 `data/artifacts/`；快照 `data/backups/`。
- 缓存 `cache/{raw,processed,extraction,tasks}/`（TTL 惰性过期 + `kb.py gc` 引用感知清理）。
- 种子风格库只读，在 `data/knowledge/styles/seed.json`（方法文档 `references/style-library.md` 只留指针）。
- V1 兼容镜像 `data/case_library/cases.mirror.jsonl`（追加式，独立于只读导入源）。
