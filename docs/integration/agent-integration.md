# Agent Integration（统一 LLM Adapter Contract）

> 本文回答一个问题：**任何 Agent（Claude Code / Codex / 其他）如何在不依赖平台私有行为的前提下，驱动 Counselors-Writing Core 完成一次语义步骤。**
>
> 核心分工：**Agent = 语义编排者**（理解用户 + 调用模型）；**Core = 确定性执行与状态持久化**（`scripts/kb.py`，Python + SQLite + 文件）。Agent 不持有状态，不写知识库；Python 负责所有字段注入、PII 脱敏、Pydantic 校验、artifact、索引、缓存、血缘。

## 1. 通用 Agent 模式（四步）

```
用户意图
  ↓
Agent Routing（读 SKILL.md / references 判断该走哪条链）
  ↓
A. prepare    kb.py <command> ... --prompt-only   → 拿到最小 prompt + 请求元数据
  ↓
B. invoke     Agent 用自身模型执行 prompt，产出 JSON
  ↓
C. commit     把 JSON 存成文件，kb.py <command> ... --result <file>
  ↓
D. validate + persist   Python 完成字段注入 → PII 脱敏 → Pydantic 校验
                       → artifact → 索引 → 缓存 → lineage → 返回 ID/指针
```

## 2. 支持的命令与三种模式

LLM 语义命令共 5 个，统一支持三种**互斥**模式：

| 命令 | 作用 | 结果实体 |
|---|---|---|
| `extract <doc> --extractor case_facts\|style_pattern\|topic_signal` | 从素材结构化提取 | case / style / topic |
| `analysis --case <id> [--case <id2>]` | 案例对比分析 | analysis |
| `mapping --case <id> --profile <id>` | 案例 × 画像映射 | mapping |
| `write --mapping <id> [--topic/--analysis/--style]` | 白名单上下文写作 | draft |
| `audit --draft <id>` | 单通道七项自查 | audit |

三种模式（互斥，`argparse` 强制）：

| 模式 | 行为 | 适用 |
|---|---|---|
| `--llm-cmd "<CMD>"` | Core 直接执行外部命令（stdin 传 prompt、stdout 收 JSON），无 shell | CLI 自动化 / 全自动流水线 |
| `--prompt-only` | 只输出最小机器可读请求，不调用模型 | Agent 编排（A 步） |
| `--result <file>` | 不调用模型，只读 Agent 已产出的 JSON 文件回灌（C 步） | Agent 编排闭环 |

三者都**不重复读取 raw/全文/整库**；`--result` 复用与 `--llm-cmd` 完全相同的 parse → inject → validate → persist 路径，不复制任何写入逻辑。

## 3. `--prompt-only` 输出结构

统一为机器可读 JSON：

```json
{
  "operation": "analysis",
  "schema_version": "1.0.0",
  "request": { "case_ids": ["case-..."] },
  "expected_output": "analysis",
  "prompt": "…最小 prompt…"
}
```

- `operation` / `expected_output`：告诉 Agent 这一步产出什么实体。
- `request`：最小输入元数据（id 列表，**不含正文**）。
- `prompt`：Agent 唯一需要交给模型的文本。
- Agent 不需要解析人类说明文字就能拿到 prompt。

## 4. `--result` 回灌

Agent 在外部调用模型拿到 JSON 后，把 JSON 原样写入文件（**就是实体 JSON，不是包装对象**），再回灌：

```bash
python scripts/kb.py analysis --case case-abc --result /tmp/analysis_result.json
```

Python 读取文件 → parse → 确定性字段注入（剥离 LLM 越权的 id/状态，补 id+来源+证据）→ PII 脱敏 → Pydantic 校验 → 落 knowledge/artifact/索引/缓存。返回结构与 `--llm-cmd` 完全一致。

## 5. Claude Code 示例（适配示例，非 core 假设）

```bash
# A. prepare
python scripts/kb.py write --mapping map-abc --prompt-only > req.json
# B. invoke（Claude Code 读 req.json 的 prompt 字段，产出 JSON 存到 draft.json）
# C. commit
python scripts/kb.py write --mapping map-abc --result draft.json
```

## 6. Codex 示例（适配示例，非 core 假设）

```bash
# A. prepare
python scripts/kb.py extract doc-xyz --extractor case_facts --prompt-only > req.json
# B. invoke（Codex 执行 prompt，产出 JSON 存到 case.json）
# C. commit
python scripts/kb.py extract doc-xyz --extractor case_facts --result case.json
```

## 7. Other Agent

任何能满足以下三点的 Agent 都可接入，无需任何平台专属适配：

1. 能执行 CLI；
2. 能读写 JSON 文件；
3. 能调用自身模型。

对 Core 而言，Agent 只是一个「能从文件读回 JSON 的模型」，与平台无关。

## 8. 失败恢复

| 场景 | 行为 |
|---|---|
| LLM 失败（--llm-cmd 超时/非零退出/命令不存在） | exit 3（dependency_failed），**源数据不损坏**（canonical 只在校验通过后才写） |
| `--result` 文件不存在/为空 | exit 3，不写任何数据 |
| JSON 无效 | Python 拒绝（parse 失败），不落 knowledge |
| 校验失败 | 进入既有 self-correction（≤2）→ 仍失败则落 `failed` artifact + 保留 raw，可修复后重跑 |
| 中断 | 用 `kb.py task create/resume` 断点续跑（16 态状态机 + context_refs） |
| 二次回灌同一结果 | extraction/analysis/write/audit cache 幂等短路，不产生重复 artifact |

## 9. 边界

- Core 不声明任何 LLM API 依赖（Pydantic 是唯一第三方依赖）；`--llm-cmd` 的命令由用户/平台提供。
- `--result` 不是新建 adapter 框架，只是「从文件读 JSON」的轻量 `llm_fn`；不引入 shell 执行风险（`--llm-cmd` 用 `shlex` + 直接 argv，无 `shell=True`）。
- 完整架构细节见 `docs/architecture/`；历史设计决策见 `docs/history/`。
