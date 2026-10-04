# AGENTS.md（Codex / Claude Code 共用）

本仓库按 `docs/milestone-pack-v1.0.md` 的 M0→M9 里程碑协议演进，**仓库文件是唯一真相源，不依赖聊天记忆**。

## 执行入口（无上下文 Agent 必读）

1. 读 `docs/milestone-pack-v1.0.md` 顶部的 GLOBAL BOOTSTRAP。
2. 读 `docs/migration-plan.md` 第 0-1 节了解当前进度。
3. 只执行当前里程碑（见进度表），完成后按 pack 的 Milestone Report 格式汇报并停止。

## 当前进度（2026-10-04，M0→M9 全部完成）

- M0 ✅（审计文档 6 份：architecture-audit / capability-inventory / historical-feature-recovery / token-boundary-audit / architecture-gaps / migration-plan）
- M1 ✅ 数据契约已冻结（16 实体 + common；`data/schemas/` 由 `scripts/kb.py schemas export` 生成）
- M2 ✅ 持久化基础（2026-09-30）：data/index.db（SQLite+FTS5，trigram→unicode61 回退）；registry 账本 + 原子写 + 幂等复用；cache 四层（TTL 30/30/90/7d）；URL 归一；import-legacy 五实体拆分（dry-run/备份/对账）；rebuild 从 canonical 重建。实现细节见 `docs/storage-architecture.md`。
- M3 ✅ Web 获取+清洗+分块（2026-09-30）：`kb.py fetch <url>` / `kb.py ingest` / `kb.py hotlist weibo|tophub`（输出与 V1 逐字段一致；xinbang 已移除）；core/fetcher（FetchBlocked 七态、429/5xx 重试×3 退避 2/4/8s、403/验证码墙不重试、raw 落 cache/raw TTL 3d）+ preprocess（纯 stdlib）+ chunker（1200±/overlap 100/≤2000）+ pipeline（指针输出，正文不进 stdout；blocked/付费墙不降级；失败保留 last-good）+ compat（ArticleBackend 三级回退）；rebuild 覆盖 source/document/chunk。fetch_douyin 已改造（结构化 [{title,topic,likes}] + --consent 登录同意门禁，C-02）。**286 测试全绿；对抗审查 96 agents 44 条确认发现全部修复（含 37 个回归锁）**。
- M4 ✅ LLM 结构化提取+校验（2026-09-30）：`scripts/core/extract.py`（content-hash→extraction_cache 幂等短路【key 含 model_mode，命中校验实体仍存在】→最小 prompt【总预算 6400 字符=4000token，Token 检查点 B】→LLM【`llm_fn` 注入 / `--llm-cmd` 外部命令 / `--prompt-only` 由 Agent 编排】→parse→确定性字段注入【剥离 LLM 越权 id/状态，Python 补 id+来源+证据，chunk-only 回指 chunk】→PII 脱敏兜底【学号/身份证/手机号/邮箱】→Pydantic 校验→自纠正 ≤2【base_prompt 不累计】→extraction_failed+失败 artifact 保留 raw）；fact/inference 证据自动注入 EvidenceRecord；case_facts/style_pattern/topic_signal 三 extractor；`kb.py extract` 挂载（0.5.0）；dissemination-review.md 沉淀流程命令化。**317 测试全绿**；**对抗审查 43 agents：35 发现→28 确认（7 驳回）全部修复（含 10 回归锁）**。
- M5 ✅ 检索+分析+映射（2026-10-04）：search 成体（case L1 五字段 case_id/title/tags/fact_count/updated_at、topic L1、style --top 硬上限 10、L2 --get/--fields）；`core/analysis.py`（case 对比分析 → AnalysisRecord，输入白名单不注入 background 全文，cache tasks:analysis: 幂等，自纠正 ≤2）；`core/mapping.py`（case×profile → MappingRecord，显式差异/适配/可迁移/不可迁移字段）；`core/profile.py`（7 键白名单 get/set/dump）；kb.py 挂载 search/analysis/mapping/profile/case add/style add（0.6.0）；legacy case_lib/style_lib/profiles 转薄封装；db.py migration v2 加 analyses/mappings 表。**363 测试全绿**（M4 317 + M5 46）。
- M6 ✅ 写作+审核+输出（2026-10-04）：`core/writer.py`（写作输入白名单代码级强制 + deny 清单 raw/全文/整库不进上下文 + 上下文包 <6K token 检查点 E + lineage 自动聚合 topic/analysis/mapping/style/profile/case_ids/source_ids/evidence_ids + cache 零 LLM）；`core/audit.py`（单通道七项结构化自查 + 标点门禁确定性注入 + verdict 硬规则 political/factual/privacy 任一 fail → passed false + 分组聚合）；`core/punctuation.py`（C-09 三规则修复 + C-03 ko exit 2 + --max-findings）；`core/output.py`（占位符渲染 + FINAL artifact permanent + 换格式重渲染零 LLM）；kb.py 挂载 context-for-write/write/audit/punctuation/output render（0.7.0）；legacy check_punctuation.py 转薄封装；C-07 第二意见删除。**405 测试全绿**（M5 363 + M6 42）。
- M7 ✅ 备份+恢复+GC（2026-10-04）：`core/task.py`（16 态状态机 + task_events 追加日志 + resume 读 context_refs + 批任务 parent+children 单败不崩 + retry 上限 3）；`core/backup.py`（Connection.backup 在线快照 → data/backups/kb-bak-<ts>-<hex>.db + backups 表 + 版本化保留 keep 10 绝不删到 0 + restore 前 integrity_check + 现库安全副本）；`core/gc.py`（cache TTL + artifact expires_at/temporary>7d 无引用候选 + 引用感知 source_ids/parent_ids/task refs 保护 + permanent 永不 GC + knowledge/ 永不触碰 + 默认 dry-run + 单失败不崩）；db.py migration v3 加 tasks/task_events/backups 表（DB_VERSION 3）；kb.py 挂载 task/backup/gc（0.8.0）；`docs/data-lifecycle.md`（保留矩阵/备份策略/恢复流程/GC 规则/灾难场景）。**441 测试全绿**（M6 405 + M7 30）；**对抗审查 2 agent：15 条确认发现全部修复**（含 HIGH 1：GC temporary 续期语义被 created_at 架空）。
- M8 ✅ 加固+兼容+回归（2026-10-04）：`core/compat.py` 补第 4 后端 `CdpBackend`（web-access-main CDP，登录同意门禁 C-02 同款语义）；`references/de-ai.md` 用户专属绝对路径 → `resolve_sibling_skill("write")` 发现机制（运行时无用户专属绝对路径硬编码）；SKILL.md 依赖段补发现机制说明；`kb.py test` 门禁命令（0.9.0）；3 份报告 `docs/hardening-report.md`（18 类检查）/ `compatibility-report.md`（Codex/Claude 共用 core）/ `regression-report.md`（15 项能力回归矩阵，9 处有意变更零回退）。**447 测试全绿**（M7 441 + M8 6）。
- M9 ✅ Token 审计终版 + 集成 + 发布（2026-10-04）：SKILL.md V2 重写（命名三层分离：目录名 `fudaoyuan-baokuan` 不变 / `name`→`counselors-writing` / `display_name`→`Counselors-Writing` + version 2.0.0 + 自然语言等价表映射 kb.py + 铁律补 FACT/INFERENCE）；`docs/architecture.md`（五层 + 12 ID 流转）+ `release-readiness.md`（A-H 发布判定 + Token 对比检查点 G）+ README + CHANGELOG；frontmatter lint 通过；全新环境演练通过（从零 init→seed 检索→case 写/检索→rebuild→task→backup）。**447 测试全绿 + 18 schema 零漂移**。
- **M0→M9 全部完成**（2026-10-04）。仓库已收敛为可迭代的 Agent Knowledge Pipeline，后续工程任务见 `docs/release-readiness.md` H 节。

## 铁律（任何 Agent 都必须遵守）

1. 数据契约以 `scripts/core/schema.py` 为单一真相源；`data/schemas/*.json` 禁止手改（`kb.py schemas export --check` 检测漂移）。
2. 事实/推断强制分离：documented_fact/source_claim 必带 evidence_ids；ai_inference/derived_pattern/recommendation 必带 basis。
3. 学生隐私是硬门槛：命中即脱敏。
4. 禁止把整库/整文塞进 LLM 上下文（两级检索 + 指针优先）。
5. 禁止绕过登录/验证码/IP 限制/付费墙/Bot 检测；403/429 → 有限重试 → 官方替代源 → 用户提供内容。
6. 旧 7 个 scripts/*.py 在 M8 前保持可用；改动前先读 `docs/migration-plan.md` 第 2 节有意变更清单。

## 常用命令

```bash
python scripts/bootstrap.py                     # 环境探测（exit 3 = 依赖缺失）
python scripts/kb.py validate <entity>          # stdin JSON 契约校验（exit 0/2）
python scripts/kb.py schemas export --check     # 契约漂移检测（exit 1 = 漂移）
python scripts/kb.py db init                    # 索引初始化（幂等；目录结构落地）
python scripts/kb.py db stats                   # 各索引表行数
python scripts/kb.py db rebuild                 # 从 canonical 文件 + registry 重建索引
python scripts/kb.py db import-legacy --dry-run # V1 旧三文件只读导入（备份+幂等）
python scripts/kb.py artifact create --type raw_html < 内容   # Artifact 注册（幂等复用）
python scripts/kb.py cache purge                # 默认 dry-run；--apply 才删过期条目
python scripts/kb.py fetch <url>                # 抓取公开 URL → 指针（正文不进 stdout）
python scripts/kb.py ingest [--file f] [--url u] # 用户提供内容 → 清洗分块管道
python scripts/kb.py hotlist weibo --top 20     # 热榜（输出与 V1 一致；xinbang 已移除）
python scripts/kb.py extract <doc-id> --extractor case_facts --llm-cmd "claude -p"  # LLM 提取（或 --prompt-only 由 Agent 编排）
python scripts/kb.py search case <kw> [--top N] [--get CASE_ID]  # L1 检索 / L2 读完整字段（case/style/topic）
python scripts/kb.py analysis --case <id> [--case <id2>] --llm-cmd "claude -p"  # 案例对比分析（或 --prompt-only）
python scripts/kb.py mapping --case <id> --profile pro-school --llm-cmd "claude -p"  # 案例×画像映射
python scripts/kb.py profile get <key> / set <key> <value> / dump   # 学校画像（7 键白名单）
python scripts/kb.py context-for-write --mapping <id>   # 打印写作上下文包（白名单审计，<6K token）
python scripts/kb.py write --mapping <id> --llm-cmd "claude -p"   # LLM 写作 → DraftRecord（或 --prompt-only）
python scripts/kb.py audit --draft <id> --llm-cmd "claude -p"   # LLM 七项自查 + 标点门禁 → AuditRecord
python scripts/kb.py punctuation <file>   # 标点门禁（确定性，零 LLM；C-03/C-09）
python scripts/kb.py output render --draft <id> [--content]   # draft → FINAL artifact（零 LLM，换格式重渲染）
python scripts/kb.py task create --id <id> --type writing   # 16 态任务状态机（非法迁移拒绝）
python scripts/kb.py task resume --id <id>   # 读恢复锚点（status + context_refs）
python scripts/kb.py backup create --reason manual   # 索引库在线快照 → data/backups/
python scripts/kb.py backup restore --id <bak-…>   # 恢复（integrity_check + 安全副本）
python scripts/kb.py gc [--apply]   # 引用感知 GC（默认 dry-run；knowledge/ 永不触碰）
python scripts/kb.py test   # 全量测试门禁（unittest discover，退出码透传）
python -m unittest discover -s scripts/tests -t scripts   # 等价原生命令
python scripts/fetch_douyin.py <kw> --consent   # 抖音实验项（登录同意门禁，C-02）
```
