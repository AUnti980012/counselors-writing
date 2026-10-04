# Changelog

本仓库按 `docs/history/milestone-pack-v1.0.md` 的 M0→M9 协议演进。里程碑细节见 `docs/history/migration-plan.md`。

## [2.1.0] - 2026-10-04

M10 产品化收口：把「工程重构完成」收口为「可长期交付给不同 Agent 与真实用户的产品」。

### 版本号（四种语义分离）

| 版本 | 值 | 含义 |
|---|---|---|
| Skill | **2.1.0** | 用户可见能力/行为契约（新增 `--result` 回灌闭环） |
| KB | **0.10.0** | CLI/核心接口（`--result` 新接口） |
| Schema | 1.0.0（不变） | 数据契约（无字段变化） |
| DB | 3（不变） | SQLite migration（无迁移） |

### 变更

- **Agent Adapter Contract（P0-A）**：`extract` / `analysis` / `mapping` / `write` / `audit` 五命令统一三模式（互斥）——`--llm-cmd "<LLM_COMMAND>"` / `--prompt-only` / `--result <file>`。`--result` 用 `llm_fn_from_file` 复用既有 parse→inject→validate→persist 路径，零复制写入逻辑、无 shell 风险。`--prompt-only` 输出统一为 `{operation, schema_version, request, expected_output, prompt}` 机器可读 JSON。
- **选题语义闭环（P0-B）**：明确 `AnalysisRecord`（`analysis --case`）与 `TopicRecord`（`extract --extractor topic_signal`）边界；`search topic` 仅检索。`references/topic-selection.md` 已对齐。
- **文档分层（P0-C）**：`docs/` 分 `user/` `integration/` `architecture/` `history/` 四层，所有内部链接已修复。
- **SKILL.md 瘦身（P0-D）**：改为 8 段 Runtime 结构，更短且能力不缩水；移除平台专属命令示例。
- **AGENTS.md 改开发入口（P0-E）**：不再要求读历史里程碑全文；保留单一真相源、安全规则、测试门禁、版本一致性。
- **README 改用户入口（P0-F）**：增加「5 分钟第一次使用」与多 Agent 使用说明，移除固定测试数字。
- **LICENSE 落地（P0-G）**：MIT，与 frontmatter 声明一致。
- **Python 版本修正**：代码实际使用 `X | None` 联合类型（3.10+），`bootstrap.py` 与文档统一为 **Python 3.10+**（原 3.9+ 为漂移）。
- **文档一致性**：Runtime 文档清除 `claude -p` / `qwen-verify` / `WebSearch` 平台专属引用；修正标点门禁退出码漂移（findings/ko 均为 exit 2）。

### 测试

全量测试必须通过：`python scripts/kb.py test`（当前基线 **457**；新增 `--result` 回灌 / 互斥 / 非法拒绝等 10 条）。`python scripts/kb.py schemas export --check` 零漂移。

## [2.0.0] - 2026-10-04

V2 重构完成：从「一次性同步 CLI」升级为「Agent Knowledge Pipeline」（低 Token、高复用、可恢复、可检索、跨 Codex / Claude Code）。

### Milestones

- **M0** 架构审计 + 历史能力恢复（审计文档 6 份 + 12 项恢复矩阵）
- **M1** 数据契约冻结（16 实体 + common，Pydantic 单一真相源，`kb.py validate` / `schemas export`）
- **M2** 持久化基础（Artifact + SQLite/FTS5 + Cache 四层 + URL 归一 + import-legacy）
- **M3** Web 获取 + 清洗 + 分块（Fetcher/Preprocess/Chunker/Pipeline + hotlist 迁移）
- **M4** LLM 结构化提取 + 校验（content-hash 幂等 + 自纠正 ≤2 + PII 脱敏 + fact/inference 分离）
- **M5** 检索 + 分析 + 映射（L1/L2 检索 + Analysis + Mapping + Profile）
- **M6** 写作 + 审核 + 输出（写作白名单 + 单通道审核 + 标点门禁 + 占位符渲染）
- **M7** 备份 + 恢复 + GC（16 态任务状态机 + 在线快照 + 引用感知 GC）
- **M8** 加固 + 兼容 + 回归（CDP 后端 + 绝对路径清理 + 三报告 + `kb.py test`）
- **M9** Token 审计终版 + 集成 + 发布（SKILL.md V2 + architecture/release-readiness + README/CHANGELOG）

### 有意行为变更（C-01 ~ C-09）

| # | 变更 |
|---|---|
| C-01 | xinbang 子命令移除（报错指引官方替代源） |
| C-02 | fetch_douyin 结构化 [{title,topic,likes}] + 登录同意门禁 |
| C-03 | check_punctuation ko exit 2「暂不支持」+ `--max-findings` |
| C-04 | case_lib `--top 0` 钳制 ≥1（修 rows[-0:] 全量 bug） |
| C-05 | style_lib search `--top` 硬上限 10 |
| C-06 | profiles set 7 键白名单 + updated_at 托管 |
| C-07 | 思政第二意见删除（qwen-verify，单通道 + 标点门禁） |
| C-08 | 案例写入路径：文件权威 → DB → 独立 JSONL 镜像 |
| C-09 | 标点 4 规则修复（数字+CJK、em-dash、全角空格） |

### Skill 命名（三层分离）

- Canonical ID（目录名）：`fudaoyuan-baokuan`（不变，物理路径）
- Skill Identifier（frontmatter `name`）：`counselors-writing`（小写 kebab-case，机器识别）
- Display Name（`metadata.display_name` / H1）：`Counselors-Writing`（用户看到的产品名）

## [0.1.0] - V1（历史）

V1 为「文章生成 Skill」：SKILL.md 路由表 + 7 步工作流 + 8 references + 7 标准库 CLI + 3 空壳数据文件。数据层从未被真实数据验证。
