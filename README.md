# Counselors-Writing（辅导员爆款文章）

把高校辅导员的日常工作素材——一次谈心谈话、一场班会、一个学生事件——写成有传播力的公众号推文，并沉淀成越用越懂学校、越用越懂学生的案例库。

V2 是 **Agent Knowledge Pipeline**：媒体研究 → 抓取 → 清洗 → 结构化提取 → 案例检索 → 分析 → 画像映射 → 写作 → 审核 → 输出 → 复盘沉淀。核心是**确定性引擎**（Python + SQLite + 文件），LLM 只做语义步骤编排。

## 快速开始

```bash
python scripts/bootstrap.py                     # 环境探测（exit 3 = 缺 pydantic）
python scripts/kb.py db init                    # 索引初始化（幂等）
python scripts/kb.py db import-legacy --dry-run # V1 旧数据只读导入预览
python scripts/kb.py test                       # 全量测试（447 个）
```

## 架构

| 层 | 载体 | 说明 |
|---|---|---|
| 编排层（Agent） | `SKILL.md` + `references/` | 自然语言意图 → `kb.py` 子命令 |
| 确定性核心 | `scripts/core/`（22 模块） | 抓取/清洗/分块/提取/检索/分析/映射/写作/审核/输出/任务/备份/GC |
| 数据层 | `data/` + `cache/` | knowledge 文件（权威）+ SQLite/FTS5（索引）+ registry（账本）+ backups + cache 四层 |

详见 `docs/architecture.md`（目标架构）、`docs/storage-architecture.md`（存储）、`docs/data-lifecycle.md`（生命周期）。

## 常用命令

```bash
python scripts/kb.py hotlist weibo --top 20      # 热榜
python scripts/kb.py fetch <url>                 # 抓正文（指针，不进上下文）
python scripts/kb.py ingest [--file f] [--url u] # 用户提供内容
python scripts/kb.py extract <doc-id> --extractor case_facts --llm-cmd "claude -p"
python scripts/kb.py search case <kw> [--get ID] # 检索 L1/L2
python scripts/kb.py analysis --case <a> --case <b> --llm-cmd "claude -p"
python scripts/kb.py mapping --case <id> --profile pro-school --llm-cmd "claude -p"
python scripts/kb.py write --mapping <id> --llm-cmd "claude -p"
python scripts/kb.py audit --draft <id> --llm-cmd "claude -p"
python scripts/kb.py output render --draft <id>
python scripts/kb.py task create --id <id> --type writing   # 断点续跑
python scripts/kb.py backup create --reason manual          # 在线快照
python scripts/kb.py gc [--apply]                           # 引用感知 GC
```

## 依赖

- Python 3.9+，pydantic≥2（唯一第三方依赖）
- 可选兄弟 skill：`read` / `fetch-skill-main` / `write` / `web-access-main`（`resolve_sibling_skill` 定位，缺失优雅降级）

## 文档

`docs/`：migration-plan（进度）、data-contract（契约）、architecture / storage-architecture / data-lifecycle（架构）、hardening / compatibility / regression / release-readiness（M8-M9 报告）。
