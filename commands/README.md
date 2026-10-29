# Slash Command Layer（/cw-* 命令层）

本目录是 `/cw-*` 命令的**宿主无关契约文件**：每个 `cw-*.md` 是薄路由指令，
只引用真实存在的 `scripts/cw.py` / `scripts/kb.py` 子命令与 `references/*.md`，
不重复实现业务逻辑。命令的**单一真相源**是 `scripts/core/commands.py` 的
`COMMAND_SPECS`（名称/用途/输入/路由/输出/状态），`scripts/cw.py help` 渲染它。

## 命令清单

| 命令 | 用途 | 状态 |
|---|---|---|
| `/cw-hot` | 抓取多源热点、去重、评估选题价值 | implemented |
| `/cw-research` | 资料研究与证据整理 | implemented |
| `/cw-write` | 一站式写作（自动决定最小流水线） | implemented |
| `/cw-audit` | 独立文章审核（七项思政 + 确定性门禁） | implemented |
| `/cw-topic` | 选题分析 → TopicRecord | implemented |
| `/cw-revise` | 文章修订 | partial（无独立 revise CLI，语义项 LLM 编排） |
| `/cw-library` | 知识库检索/复用/入库 | implemented |
| `/cw-export` | 成果导出（Markdown） | partial（仅 Markdown） |
| `/cw-status` | 任务进度/产物/失败原因/缓存 | implemented |
| `/cw-continue` | 恢复未完成任务 | implemented |
| `/cw-help` | 命令清单与限制 | implemented |

### 独立命令 `/database-check`

`/database-check` 不属于 `/cw-*` 知识库操作体系，是独立的可视化便捷命令：把最新
知识库导出为 RhineLabUI 档案阵列并在浏览器打开。路由到 `scripts/database_check.py`
（内部调用 `scripts/export_rhinelab.py`），命令文件为 `database-check.md`，
Claude Code 接线方式与 `/cw-*` 相同（软链接到 `~/.claude/commands/database-check.md`）。

| 命令 | 用途 | 状态 |
|---|---|---|
| `/database-check` | 导出知识库并打开 RhineLabUI 可视化档案阵列 | implemented |

## 宿主接线

Slash Command 是宿主特性，各宿主入口格式不同。本目录的 `cw-*.md` 是契约主体，
各宿主只做薄层「参数接收 + 呈现」，核心路由不漂移。

### Claude Code

Claude Code 的 slash command = `~/.claude/commands/*.md`（每个命令一个文件；
一个 skill 只能是一条命令，承载不了 11 条）。接线方式：符号链接到本目录：

```bash
mkdir -p ~/.claude/commands
for f in cw-*.md; do
  ln -sfn "$(pwd)/$f" ~/.claude/commands/"$f"
done
```

与现有 `~/.claude/skills/counselors-writing` → `~/.agents/skills/counselors-writing`
的符号链接模式一致，仓库文件是唯一真相，无副本漂移。前端 `description` /
`argument-hint` 供 Claude Code 命令列表与参数占位展示，正文是 `$ARGUMENTS`
注入后的路由指令。

### Codex

Codex（v0.117.0 起）已移除 slash commands。`/cw-*` 等价能力通过两条入口获得：

1. **skill 自然语言路由**：`~/.agents/skills/counselors-writing/SKILL.md` 已按意图
   路由到各 references（与 `/cw-*` 一一对应）；
2. **薄 CLI 直调**：`python scripts/cw.py hot|specs|help` 与
   `python scripts/kb.py <28 子命令>`，Agent 直接编排。

Codex 侧无需复制本目录文件；若未来 Codex 恢复 command 入口，按 Claude Code
同样方式接线即可。

### 其它宿主

任何能「执行 CLI + 读写 JSON」的 Agent 都可用 `scripts/cw.py` / `scripts/kb.py`
三模式（`--llm-cmd`/`--prompt-only`/`--result`），详见
`docs/integration/agent-integration.md`。

## 维护

- 新增/修改命令：先改 `scripts/core/commands.py`（COMMAND_SPECS），再同步本目录
  的 `cw-*.md` 正文与 `scripts/cw.py` 子命令，保持三方一致。
- 路由只允许引用真实存在的子命令；未实现能力必须在 `status`/`notes` 如实标注。
- 验证一致性：`python scripts/cw.py help`（契约渲染）与
  `python scripts/cw.py specs`（JSON 契约）。
