# Compatibility Report（跨 Agent 兼容性报告）

> 对应 pack M8 STEP 3-5（Codex / Claude Code 兼容 + Shared Core）。
> 结论：**Codex 与 Claude Code 共用同一 core，无任何 Agent 专属依赖。**

## 1. Shared Core（STEP 5）

核心业务逻辑全部落在平台无关层，Agent 层只做编排：

| 层 | 载体 | 说明 |
|---|---|---|
| 逻辑 | Python（`scripts/core/`） | 22 模块，纯 stdlib + pydantic≥2（唯一第三方依赖） |
| 契约 | JSON Schema（`data/schemas/`） | 由 `kb.py schemas export` 生成，单一真相源 |
| 索引 | SQLite + FTS5（`data/index.db`） | 可随时从 canonical 重建 |
| 内容 | 文件（`data/knowledge/`） | PERSISTENCE RULE 权威 |
| 配置 | 确定性（env `FUDAOYUAN_SKILLS_DIR` + `__file__` 相对定位） | 零硬编码路径 |

## 2. Codex 兼容（STEP 3）

- 命令清晰：`scripts/kb.py <subcommand>` 单一入口，argparse 显式接口。
- 路径可移植：全部 `__file__` 相对定位（`core/paths.py`）；兄弟 skill 用 `resolve_sibling_skill`。
- 不假设 Claude 行为：无 MCP 硬依赖（C-07 已删 qwen-verify）、无 slash command 依赖。
- 无跨会话记忆依赖：`AGENTS.md` 为入口，仓库文件是唯一真相源。
- 输出确定可检：JSON 结构化输出 + 退出码 0/1/2/3/4。

## 3. Claude Code 兼容（STEP 4）

- 业务逻辑同一份 `core/`：Claude 下自然语言触发最终等价映射到同一 kb.py 子命令。
- 不依赖 Claude-only 语法：SKILL.md 路由表为自然语言，无强制 Agent 专属特性。
- CLI 显式：`--llm-cmd "claude -p"` 由用户/平台提供 LLM 命令，仓库不声明 LLM API 依赖。
- 文件状态权威：canonical 文件 + registry 账本 + SQLite，跨 Agent 可续。

## 4. 兄弟 skill 定位（pack STEP 3 可移植性核心）

```text
resolve_sibling_skill(name):
  1. env FUDAOYUAN_SKILLS_DIR/<name>       （显式指定 skills 根）
  2. 同父目录探测：<skills>/<name>          （默认，上溯 3 层）
  3. None                                  （调用方优雅降级，不中断）
```

| 兄弟 skill | 用途 | 缺失时 |
|---|---|---|
| `read` | 报媒正文（fetch_local.py） | 降级 fetch-skill-main |
| `fetch-skill-main` | 报媒正文备选（fetch.py） | 降级用户粘贴 |
| `write` | 去 AI 规则库（write-zh.md） | 提示用户提供规则，不编造 |
| `web-access-main` | 动态页 CDP（localhost:3456） | 降级热榜站 / 用户内容 |

## 5. 登录同意门禁（合规底线）

CDP / 抖音登录态场景：检测到已登录或需登录时，**无 `--consent` 一律拒绝**（结构化
`login_required` 状态退出），绝不自动使用本机登录会话；用户拒绝 → 降级用户提供内容。
双文档写入（`references/hot-trend.md` + `AGENTS.md`）。
