# 迁移计划（执行版 · 进度追踪）

> 执行协议已切换为 **Milestone Prompt Pack v1.0**（仓库内权威副本 `docs/history/milestone-pack-v1.0.md`，M0→M9 十阶段，跨 Codex / Claude Code 无上下文执行）。本文件保留旧 22 步表作为实施粒度与进度追踪；pack ↔ 22 步映射见第 0 节。
> 权威方案历史文件（本机 `.claude/plans/` 目录，随会话保存）；本文件随实施进度持续更新。
> 审计基线日期：2026年09月30日。已批准进入实施。

## 0. 与 Milestone Prompt Pack v1.0 对齐（2026-09-30）

| pack 里程碑 | 旧 22 步 | 状态 |
|---|---|---|
| M0 架构审计 + 历史能力恢复 | 审计（前置） | ✅ 完成并已对齐：文档改名（current-architecture→architecture-audit、feature-inventory→capability-inventory、token-audit-initial→token-boundary-audit）+ 补齐 `docs/history/historical-feature-recovery.md`（12 项恢复矩阵）+ pack 存档 |
| M1 Schema Data Contract | 步骤 1-2 | ✅ 完成（2026-09-30）：见第 1 节 |
| M2 Artifact+Index+Cache | 步骤 3-6 | ✅ 完成（2026-09-30）：141 测试全绿（含 22 个审查回归锁）；SQLite 路径 data/index.db（pack 规范）；cache 四层命名空间；import-legacy 空壳源 dry-run 验证通过（真实导入留待有数据后执行）；对抗审查 40 条确认发现全部修复 |
| M3 Web 获取+清洗+分块 | 步骤 7-9 | ✅ 完成（2026-09-30）：286 测试全绿；对抗审查 96 agents 44 确认全部修复+回归锁；见第 1 节 |
| M4 LLM 提取+校验 | 步骤 10 | ✅ 完成（2026-09-30）：见第 1 节 |
| M5 检索+分析+映射 | 步骤 11-13 | ✅ 完成（2026-10-04）：见第 1 节 |
| M6 写作+审核+输出 | 步骤 14-16 | ✅ 完成（2026-10-04）：见第 1 节 |
| M7 备份+恢复+GC | 步骤 17-19 | ✅ 完成（2026-10-04）：见第 1 节 |
| M8 加固+兼容+回归 | 步骤 20-21 | ✅ 完成（2026-10-04）：见第 1 节 |
| M9 Token 审计+集成+发布 | 步骤 22 | ✅ 完成（2026-10-04）：见第 1 节 |

pack 相对旧计划的三处规格修正（已采纳并更新本文件）：
1. SQLite 规范路径 = `data/index.db`（旧计划 `data/index/kb.db`）。
2. Cache 命名空间 = `raw / processed / extraction / tasks`（旧计划第 4 层 `temporary`）。
3. M1 交付物增加：fixtures 四类、`docs/architecture/data-contract.md`、历史能力恢复矩阵；M1 含契约执行工具（Pydantic 模型 + `kb.py validate` + `schemas export`，对应 pack「M1 = MODULE 03+04+28」）。

## 1. 里程碑总览

| 里程碑 | 内容 | 状态 |
|---|---|---|
| 0 | docs/ 五份审计文档落地 + 向用户汇报 10 项 | ✅ 完成（5 份文档 + 202 条事实核验：166 confirmed / 33 adjusted 已修正 / 3 wrong 已修正）；2026-09-30 随 pack 对齐：3 份改名 + historical-feature-recovery.md 补齐 |
| M1 | 数据契约层：16+common schema、Pydantic 单一真相源、kb.py 骨架、种子迁移、fixtures、data-contract | ✅ 完成（2026-09-30）：33 测试全绿；18 schema 导出（16 实体+common+seed 信封）；对抗审查工作流（16 agents）12 条确认发现全部修复；legacy CLI 无回归 |
| M2 | 持久化基础：Artifact+SQLite/FTS5+Cache+URL 归一 | ✅ 完成（2026-09-30）：**141 测试全绿**（M1 33 + M2 108）；data/index.db（trigram 可用，unicode61 回退有测试锁定）；registry 账本 + 原子写 + 幂等复用（含文件存在性/参数一致性/单段 filename 约束）；cache 四层（TTL 30/30/90/7d，naive 时间戳按过期处理）；URL 归一保守集合（不做 percent 往返，防 a+b/a%2Bb、%2F/ 语义碰撞）；import-legacy 五实体拆分 + 超限截断全 warning + case+topic 双幂等键（中途失败重跑自愈）；rebuild 从 canonical 重建 + 版本闸门不阻断恢复路径；docs/architecture/storage-architecture.md。**对抗审查（93 agents）：44 条原始发现去重后 40 确认 + 2 分歧 + 2 驳回；40 条确认全部修复并有回归锁**（2 分歧为 M3 前置设计，接受为已知限制） |
| M3 | 数据链：Fetcher→Preprocess→Chunk | ✅ 完成（2026-09-30）：core/fetcher（FetchBlocked 七态归一、429/5xx 重试×3 退避 2/4/8s、403/验证码墙即 blocked 不重试、raw 先落 cache/raw TTL 3d、8MiB 截断探测、URL 校验含主机/长度/控制字符、IncompleteRead 等协议错误结构化归一）+ core/preprocess（纯 stdlib HTMLParser：script/style/nav/广告容器词边界匹配剔除、密度选主内容、标题/章节、GBK 解码、全噪声长页→空正文）+ core/chunker（段落/句界 1200±/overlap 100/≤2000 硬上限含分隔符预算/char 区间=文档精确切片/字符÷1.6）+ core/compat（resolve_sibling_skill 修复上溯层级 + ArticleBackend 三级回退 V1 语义）+ core/hotlist（weibo/tophub 迁入，输出逐字段一致；xinbang 移除 C-01）+ core/pipeline（URL→raw→清洗→分块→指针编排；blocked/付费墙不降级；失败保留 last-good；幂等 id；>500 块结构化拒绝）+ kb.py fetch/ingest/hotlist；fetch_douyin 改造 C-02（结构化 [{title,topic,likes}] 截断 + --consent 登录同意门禁含已登录会话检测 + 单次失败即退）；hot-trend.md 去新榜+抖音段重写+命令统一 kb.py；rebuild 覆盖 source/document/chunk（M2 缺口关闭）；legacy 薄封装入 scripts/legacy/（fetch_hotlist/fetch_article）；**286 测试全绿**（M2 141 + M3 145，含 37 个对抗审查回归锁）；**对抗审查 96 agents：44 条确认发现全部修复**（critical 2：chunker 多短段破 2000 上限、ad 子串匹配误伤 readable/header；high 3：blocked 经降级链包装成 success、expires_at 破坏 artifact 幂等复用、兄弟 skill 探测差一阶） |
| M4 | LLM 提取：Extraction+自纠正 | ✅ 完成（2026-09-30）：core/extract.py（编排序 content-hash→extraction_cache 幂等短路【key 含 model_mode，命中校验输出实体仍存在】→最小 prompt【总预算 MAX_PROMPT_CHARS=6400=4000token，有界 chunks+紧凑 schema 摘要，截断显式标注】→LLM【llm_fn 注入 / --llm-cmd 外部命令 shlex 无 shell / --prompt-only 由 Agent 编排】→parse_llm_json→确定性字段注入【剥离 LLM 越权 id/状态，Python 补 id+来源+证据，chunk-only 入口证据回指 chunk 不 crash】→PII 脱敏兜底【学号/身份证/手机号/邮箱，脱敏在注入前防 id 误伤】→Pydantic 校验→自纠正 ≤2【base_prompt 固定不累计】→extraction_failed+failure artifact【expires_at 7d，保留 input refs+errors】）；case_facts/style_pattern/topic_signal 三 extractor→case/style/topic canonical+EvidenceRecord（documented_fact/source_claim 自动配 evidence_ids 回指）；ExtractionRecord 序列化进 extraction artifact（registry 记账，exclude 时间戳保幂等）；kb.py extract 挂载（0.5.0，退出码：数据缺失 1 / 依赖失败 3 / 用法错误 4）；dissemination-review.md 沉淀流程命令化（去整条 JSON 回灌）。**317 测试全绿**（M3 286 + M4 31，含 10 个审查回归锁）；**对抗审查 43 agents：35 条原始发现 → 28 确认（7 驳回）全部修复**（high：chunk-only 证据无 ref 崩溃、prompt 超 4000token、证据摘录无脱敏、shlex 反斜杠、document 不存在误映射 exit 4） |
| M5 | 检索分析：Search→Analysis→Mapping | ✅ 完成（2026-10-04）：search 成体（case L1 五字段 case_id/title/tags/fact_count/updated_at + topic L1 + style --top 硬上限 10 + L2 --get/--fields）；core/analysis.py（case 对比分析 → AnalysisRecord，输入白名单只注入事实/推断/可迁移模式不注入 background 全文，cache tasks:analysis: 幂等，自纠正 ≤2）；core/mapping.py（case×profile → MappingRecord，显式差异/适配/可迁移/不可迁移，cache 幂等）；core/profile.py（7 键白名单 get/set/dump，school_type 自由文本→枚举+provenance，updated_at 托管）；kb.py 挂载 search/analysis/mapping/profile/case add/style add（0.6.0）；legacy case_lib/style_lib/profiles 转薄封装（C-04 --top 0 钳制/C-05 style --top 10/C-06 白名单/C-08 写入路径→JSONL 镜像）；db.py migration v2 加 analyses/mappings 表。**363 测试全绿**（M4 317 + M5 46） |
| M6 | 产出链：Writing→Audit→Output | ✅ 完成（2026-10-04）：core/writer.py（写作输入白名单代码级强制 + deny 清单 raw/全文/整库不进上下文 + 上下文包 <6K token 检查点 E + lineage 自动聚合 topic/analysis/mapping/style/profile/case_ids/source_ids/evidence_ids + draft_id 确定性 + cache 零 LLM）；core/audit.py（单通道七项结构化自查 political/factual/value/labeling/ai_trace/privacy/copyright/format + 标点门禁确定性注入 + verdict 硬规则 political/factual/privacy 任一 fail → passed false + 分组聚合 fact/style/format/risk）；core/punctuation.py（迁自 check_punctuation.py + C-09 三规则修复 + C-03 ko exit 2 + --max-findings 汇总）；core/output.py（纯字符串占位符渲染 + FINAL artifact permanent + 换格式重渲染不重研究零 LLM）；kb.py 挂载 context-for-write/write/audit/punctuation/output render（0.7.0）；legacy check_punctuation.py 转薄封装；C-07 第二意见删除（ideological-review.md 单通道 + review-report-template.md 单栏 + SKILL.md 去 qwen-verify）。**405 测试全绿**（M5 363 + M6 42） |
| M7 | 运维层：Task/Resume→Backup→GC | ✅ 完成（2026-10-04）：core/task.py（16 态状态机 + task_events 追加日志 + resume 读 context_refs 恢复锚点 + 批任务 parent+children 单败不崩 + retry 上限 3）；core/backup.py（sqlite3 Connection.backup 在线快照 → data/backups/kb-bak-<ts>-<hex>.db + backups 表登记 + 版本化保留 keep 10 绝不删到 0 + restore 前 integrity_check + 现库安全副本）；core/gc.py（cache TTL 复用 CacheManager.purge + artifact expires_at 过期 / temporary>7d 无引用候选 + 引用感知 source_ids/parent_ids/task refs 保护 + retention=permanent 永不 GC + knowledge/ 永不触碰 + 默认 dry-run + 单失败不崩）；db.py migration v3 加 tasks/task_events/backups 表（DB_VERSION 3）；kb.py 挂载 task/backup/gc（0.8.0）；docs/architecture/data-lifecycle.md（保留矩阵/备份策略/恢复流程/GC 规则/灾难场景）。**441 测试全绿**（M6 405 + M7 30）；**对抗审查 2 agent（契约/正确性）：15 条确认发现全部修复**（HIGH 1：GC temporary 用 created_at 架空 refresh_expiry 续期 → 改 expires_at 优先；MEDIUM 6：已 expired 反复进候选、evidence.ref_artifact_id 引用遗漏、restore 安全副本 WAL 不可靠、恢复中途失败不校验现库、backups 表不随文件清理、--keep 无上钳；LOW：backup_id 路径穿越、终态自迁移、--temporary-ttl-days 无下限等；+4 回归锁） |
| M8 | 收口：加固→跨平台→回归 | ✅ 完成（2026-10-04）：core/compat.py 补第 4 后端 CdpBackend（web-access-main CDP，登录同意门禁 C-02 同款语义：已登录/需登录无 consent 一律拒绝）；references/de-ai.md:10 用户专属绝对路径 → resolve_sibling_skill("write") 发现机制（grep 无运行时用户专属绝对路径）；SKILL.md 依赖段补发现机制说明；kb.py 加 test 命令（0.9.0）；docs/history/hardening-report.md（18 类检查逐项通过）、compatibility-report.md（Codex/Claude 共用 core）、regression-report.md（15 项能力回归矩阵，9 处有意变更 C-01~C-09 零静默回退）。**447 测试全绿**（M7 441 + M8 6） |
| M9 | 集成：Token 审计终版→发布 | ✅ 完成（2026-10-04）：SKILL.md V2 重写（命名三层分离：目录名 fudaoyuan-baokuan 不变 / frontmatter name→counselors-writing / display_name→Counselors-Writing + version 2.0.0 + 自然语言等价表映射 kb.py 子命令 + 铁律补 FACT/INFERENCE 与整库不塞上下文 + 数据落点 V2 化）；docs/architecture/architecture.md（五层结构 + 12 ID 流转 + 指针优先 + 模型策略）、release-readiness.md（A-H 发布判定 + Token 对比检查点 G）、README.md、CHANGELOG.md；frontmatter lint 通过（name/version/description）；全新环境演练（临时目录从零 init→seed 可检索→case 写/检索→rebuild 恢复→task 状态机→backup）通过；**447 测试全绿 + 18 schema 零漂移** |

## 2. 有意行为变更清单（实施时预告/记录）

以下变更会改变现有可观察行为，均已在里程碑 0 汇报中预告；完成后在此记录实际变更日期：

| # | 变更 | 时机 | 状态 |
|---|---|---|---|
| C-01 | xinbang 子命令移除（报错指引官方替代源/WebSearch）；SKILL.md description 与 hot-trend.md 移除新榜声明 | 步骤 7 | ✅ 2026-09-30（core/hotlist.py 无 xinbang 实现；传入报错指引 WebSearch；SKILL.md description/路由表、hot-trend.md 去新榜） |
| C-02 | fetch_douyin 输出从整页 innerText 改为结构化 JSON [{title,topic,likes}] + 限长截断；新增登录同意门禁（询问用户） | 步骤 7 | ✅ 2026-09-30（结构化提取 title≤100/topic≤50/likes≤32；login_required 结构化状态 + --consent 重试一次；单次失败即退保留） |
| C-03 | check_punctuation ko 行为：静默放行 exit 0 → stderr 警告 + exit 2「暂不支持」；新增 `--max-findings` | 步骤 15 | ✅ 2026-10-04（core/punctuation.py `cli_main` ko return 2 + `--max-findings` 截断 + 汇总统计行；legacy 薄封装透传） |
| C-04 | case_lib `--top 0` 钳制为 ≥1（修复 rows[-0:] 全量 bug） | 步骤 11 | ✅ 2026-10-04（repo.search_cases `top=max(1,top)`；kb.py `_l1_top` 再钳 [1,50]；legacy 薄封装透传） |
| C-05 | style_lib search 增加 `--top` 硬上限（默认 10） | 步骤 11 | ✅ 2026-10-04（kb.py `_l1_top` style 硬上限 10；legacy 薄封装默认 top=10，不再整库 dump） |
| C-06 | profiles set 增加 7 键白名单（未知 key 拒绝）；updated_at 统一 ISO8601 带时区（修文档/实际格式漂移） | 步骤 11 | ✅ 2026-10-04（core/profile.py `PROFILE_EDITABLE_KEYS` 6 业务字段白名单，updated_at 由 StampedRecord 托管带时区；未知 key ValueError） |
| C-07 | 思政审核删除第二意见：报告双栏改单栏；SKILL.md/references 移除全部 qwen-verify 引用 | 步骤 15 | ✅ 2026-10-04（ideological-review.md 单通道七项 + review-report-template.md 单栏 + SKILL.md compatibility/主工作流/依赖清单去 qwen-verify；全仓 grep 零残留） |
| C-08 | 案例写入路径变更：Pydantic 校验 → knowledge 文件 → DB → JSONL 镜像（文件为真相源，pack PERSISTENCE RULE） | 步骤 11 | ✅ 2026-10-04（repo.save_record 的 case 分支追加 V1 兼容 JSONL 镜像到**独立文件** `data/case_library/cases.mirror.jsonl`——审查确认写回只读导入源 cases.jsonl 会被 import-legacy 重复导入并撑大 reconcile 计数；追加式幂等、mirror path 可注入、默认 None 防测试污染） |
| C-09 | 标点中文误报 4 规则修复（数字+CJK 不强制空格、em-dash 仅 zh 禁用、全角空格细化） | 步骤 15 | ✅ 2026-10-04（core/punctuation.py：check_zh 字母类 CJK 空格、check_en 去 dash、全角空格仅报 CJK↔半角之间；test_punctuation.py 锁定） |

## 3. 22 步进度表

每步完成定义（五动作）：① 测试通过 → ② legacy CLI 跑一遍无回归 → ③ `kb.py schemas export` 比对无漂移 → ④ Token 检查点（如命中）→ ⑤ 本表勾选 + 更新相关文档。

| # | 模块 | 核心交付 | 验证要点 | 回归影响 | Token 检查点 | 状态 |
|---|---|---|---|---|---|---|
| 1 | Schema | data/schemas/ 13+common 草案；docs/architecture/schema-conventions.md；data/knowledge/styles/seed.json（迁 style-library.md:55-89 三报种子）；references/style-library.md 种子区改指针；dissemination-review.md 案例 schema 拆四类 | json.tool 全通过；14 类数据边界无混装；fact 必带 evidence_ids / inference 必带 basis 写入草案 | 无（只新增文件） | — | ✅ M1（16 实体+common 实超 13 草案；四类拆分标注已加） |
| 2 | Pydantic | core/{paths,encoding,errors}.py（退出码 0/1/2/3/4）；core/schema.py（13+ 模型）；core/validate.py（ErrorDetail）；scripts/bootstrap.py；scripts/kb.py 骨架；`kb.py validate`/`schemas export` | fixture 正反例；SchoolProfile extra=forbid；缺 evidence_ids 报路径级错误；本机 pydantic 2.13.4 已实测 | 无 | — | ✅ M1（16 模型+22 共享 defs+seed 信封独立导出；30+ 测试全绿；argparse 用法错误重定向 exit 4） |
| 3 | Artifact | core/artifact.py + data/artifacts/；ArtifactStore（原子写 tmp+os.replace；id 白名单防穿越）；`kb.py artifact create/get/status/list` | 往返一致；穿越注入被拒；fail 状态流转 | 无（新目录） | — | ✅ M2（registry 追加式账本 latest-wins；幂等复用同类型+同hash+同血缘；content_hash 校验读取） |
| 4 | SQLite | core/db.py（版本化 migrate、FTS 注册 trigram→unicode61 回退、integrity_check）；core/repo.py（Repository）；core/search.py 骨架；`kb.py db init/import-legacy --dry-run/stats/reconcile-legacy/rebuild` | 空库 init 幂等；旧混合 JSON 按 case_id 拆四表；旧三文件只读不动+备份；L1 不含长文本；--top 0 钳制 | 无（旧 JSONL 只读） | — | ✅ M2（拆分实为五实体 case/topic/draft/audit/effect + structure→user style；rebuild 从 canonical 重建；短词 <3 字符退化子串扫描） |
| 5 | Cache | core/cache.py + cache/ 四层；CacheManager（key 契约 url:/content:/extract:/analysis:；TTL 30/30/90/7d）；`kb.py cache status/lookup/purge --dry-run` | put/get 往返；过期惰性清理；hit 计数；dry-run 不删 | 无（新目录） | — | ✅ M2（TTL 定稿 raw 30d/processed 30d/extraction 90d/tasks 7d，细化旧计划歧义写法） |
| 6 | URL 归一 | core/urlutil.py（scheme/host 小写、去默认端口/fragment/utm_*、percent 解码、IDN、尾斜杠）；core/hashing.py（NFC+空白折叠 sha256） | 对拍表：utm 变体同 canonical、空白变体同 hash | 无 | — | ✅ M2（保守集合：gclid/spm 等保留；path 大小写保留；IPv6/带认证 URL 保守处理） |
| 7 | Fetcher | core/fetcher.py（FetchBlocked 结构化失败；429/5xx 重试 ×3 退避 2/4/8s；降级链；raw 先落 cache/raw）；core/hotlist.py（weibo/tophub 迁入）；fetch_article 三级回退迁入 ArticleBackend；xinbang 移除（C-01）；fetch_douyin 改造（C-02）；hot-trend.md 去新榜+抖音段重写 | mock 403 无重试风暴；同 URL 二次 CACHE HIT；hotlist 输出与旧版逐字段一致；douyin mock 结构化+截断 | C-01/C-02 记录在案；weibo/tophub 输出不变 | A | ✅ M3（286 测试全绿；live 微博冒烟输出逐字段一致；Token 检查点 A 复核通过；审查修复：blocked 不降级、验证码墙标记高特异性、URL 校验、协议错误归一、超时分类、失败指针 query 脱敏） |
| 8 | Preprocess | core/preprocess.py（纯 stdlib 主文本提取：script/style/nav/广告容器剥离+密度启发式+标题/章节） | fixture 清理断言；幂等 | 无 | — | ✅ M3（HTMLParser 栈式跳过+词边界广告匹配；密度选段=标点入选+最长连续段；GBK 解码；全噪声长页→空正文；语言/字数确定性规则） |
| 9 | Chunk | core/chunker.py（段落/标题边界 1200±/overlap 100；char offset；token_estimate=字符/1.6）；chunks 表+chunks_fts | 边界落段落头；≤2000 硬上限；FTS 命中 | 无 | — | ✅ M3（句界硬切超长段；分隔符计入预算保硬上限；char 区间=文档精确切片；overlap 不破硬上限；rebuild 覆盖 chunks_fts） |
| 10 | Extraction | core/extract.py（content-hash→extraction_cache 幂等短路→最小 prompt→validate→自纠正 ≤2→extraction_failed+保留 raw+failure artifact）；dissemination-review.md 沉淀流程命令化 | mock 三败标 failed（raw 保留）；二次运行零 LLM；prompt token 断言 | 无（新能力） | B | ✅ M4 |
| 11 | Search | search.py 成体（L1 五字段 / L2 --get）；style --top 10（C-05）；case --top 0 钳制（C-04）；写入路径 Pydantic→knowledge→DB→JSONL 镜像（C-08）；profiles 白名单（C-06）；legacy 三脚本薄封装 + references 命令示例更新 | L1 断言；legacy golden 测试；白名单外 key 拒 | C-04/05/06/08 记录在案 | C | ✅ M5（case L1 五字段=case_id/title/tags/fact_count/updated_at；topic L1；style 硬上限 10；L2 --get/--fields；legacy 薄封装 golden） |
| 12 | Analysis | core/analysis.py（topic/style_research/hotlink）；输入白名单（素材 L2+profile+seed 摘要）；analysis_cache；topic-selection.md 输出对齐 TopicCard schema | cache 命中不重跑；TopicCard 校验；token 断言 | 选题 prompt 内容不变仅输出结构化 | D | ✅ M5（pack M5 STEP 5-6 收敛为 case 分析 → AnalysisRecord；topic/style 已在 M4 extract 产出） |
| 13 | Mapping | core/mapping.py（topic×case×style×hot + rationale；cache_key=kind+input hashes）；mappings 表+artifact | 创建/查询/去重 | 无（原隐式步骤显式化） | — | ✅ M5（MappingRecord 显式差异/适配/可迁移/不可迁移字段，防「外部成功=本地适用」臆断） |
| 14 | Writing | core/writer.py（白名单代码级强制 + deny 清单）；`kb.py context-for-write --mapping <id>`；DRAFT artifact+段落级自查钩子；writing-companion.md 门禁命令更新 | 上下文包 <6K 断言；deny 断言 | 正文四段式与模板不变 | E | ✅ M6（writer.py 白名单+deny+lineage 聚合；context-for-write/write 挂载；DRAFT artifact；writing-companion.md 命令更新为 kb.py） |
| 15 | Audit | core/audit.py（七项结构化自查+verdict 硬规则：政治/隐私/事实任一 fail→不通过）；core/punctuation.py（修 4 规则 C-09/C-03 + --max-findings）；第二意见删除（C-07：ideological-review.md 去第二意见段、报告模板单栏、SKILL.md 去 qwen-verify、audits 表单通道）；scripts/check_punctuation.py 薄封装 | 标点回归（旧误报样例全通过）；ko 行为测试；verdict 逻辑单测；grep 无 qwen-verify 残留 | C-03/07/09 记录在案；exit 0/1 语义不变 | F | ✅ M6（audit.py 七项+标点注入+分组聚合+verdict 硬规则；punctuation.py 迁 core；C-07 删除 grep 零残留） |
| 16 | Output | core/output.py（纯字符串占位符渲染）；`kb.py output render --template --data`；FINAL artifact；换格式重渲染不重研究 | 无残留占位符；报告单栏渲染 | 无（模板可继续手动使用） | — | ✅ M6（output.py 占位符渲染 + FINAL artifact permanent + output render 挂载） |
| 17 | Task/Resume | core/task.py（16 态状态机+task_events 追加日志）；resume 读 context_refs；批任务 parent+children 单败不崩；retry 上限 | 中断后 resume 秒过（靠缓存）；非法迁移拒绝 | 无（新能力） | — | ✅ M7（16 态迁移表 can_transition 纯函数 + TaskManager create/transition/resume/retry/events/batch；非法迁移 TaskStateError；FAILED retry 回退 error_stage；retry_max 超限拒绝；批任务单败不崩） |
| 18 | Backup | core/backup.py（Connection.backup 在线快照 → data/backups/）；触发点 5 类；restore 前 integrity_check+安全副本 | create→损坏→restore→数据完好 | 无 | — | ✅ M7（版本化快照 kb-bak-<ts>-<hex>.db + backups 表登记；keep 10 绝不删到 0；坏备份拒绝恢复；恢复后校验失败保留安全副本提示回退） |
| 19 | GC | core/gc.py（cache TTL、artifact expires_at 且无引用、temporary>7d）；默认 --dry-run；JSON 报告；knowledge/ 永不自动 GC | dry-run 不删；引用中的过期 artifact 保留 | 无 | — | ✅ M7（引用感知 source_ids/parent_ids/task refs；permanent 永不候选；dry-run 只报不删；--apply 单失败记 errors 不崩；cache purge 复用） |
| 20 | 跨平台 | core/compat.py（resolve_sibling_skill：env→同父目录探测→None；ArticleBackend 四实现；CDP 登录同意门禁）；AGENTS.md；de-ai.md:10、hot-trend.md:46 绝对路径→发现机制；SKILL.md compatibility 段 | 兄弟 skill 缺失全链优雅降级；grep 无用户专属绝对路径 | skill 存在时行为不变 | — | ✅ M8（CdpBackend 第 4 后端带登录同意门禁；de-ai.md 绝对路径→resolve_sibling_skill("write")；SKILL.md 依赖段补发现机制；grep 运行时无用户专属绝对路径——仅 docs 历史记录残留） |
| 21 | 回归 | tests 全套 + `kb.py test`；11 项功能 happy-path（mock 网络）；legacy golden 测试；端到端 smoke；两遍幂等；Git Bash + 无 pydantic 双场景 | 全绿即 legacy 退役绿灯 | 本步是门禁，不改行为 | — | ✅ M8（`kb.py test` 挂载；447 测试全绿；15 项能力回归矩阵零回退；hardening/compatibility/regression 三报告落地；legacy 薄封装 golden 测试既有） |
| 22 | 最终集成 | SKILL.md V2 重写（路由/流水线/铁律/自然语言等价/依赖声明；去新榜）；references 命令统一 kb.py；version→2.0.0；slash commands 9 条薄包装（可选）；docs/architecture/architecture.md；README/CHANGELOG | 全新环境演练（删 cache+index 全链跑通）；frontmatter lint | 版本号与 description 变更 | G | ✅ M9（SKILL.md V2 + name→counselors-writing（display_name Counselors-Writing）+ version 2.0.0 + 自然语言等价表；architecture.md/release-readiness.md/README/CHANGELOG 落地；frontmatter lint 通过；全新环境 smoke 通过；slash commands 薄包装为可选项未做） |

## 4. 文件清单

**Create**：docs/（审计 5 份+pack 存档+schema-conventions+data-contract+historical-feature-recovery+storage-architecture+data-lifecycle+hardening-report+compatibility-report+regression-report）、data/schemas/（16 实体+common+seed 信封 = 18 份）、data/knowledge/styles/seed.json、scripts/core/（M1 已建 5 模块：paths/encoding/errors/schema/validate；M2 已建 8 模块：atomic/artifact/db/repo/search/cache/urlutil/hashing/legacy_import；M3 已建 6 模块：fetcher/hotlist/preprocess/chunker/compat（M3 子集）/pipeline；M4 已建 extract；M5 已建 analysis/mapping/profile；M6 已建 writer/audit/punctuation/output；M7 已建 task/backup/gc；M8 compat 补 CdpBackend，无 second_opinion）、scripts/kb.py、scripts/bootstrap.py、scripts/legacy/（M3 已建 fetch_hotlist.py、fetch_article.py 薄封装）、scripts/tests/（M3 共 15 文件+fixtures；M7 已建 test_task/test_backup/test_gc）、data/registry/、data/artifacts/、data/index.db（pack 规范路径，取代旧计划 data/index/kb.db）、data/backups/、cache/（4 层 raw/processed/extraction/tasks，pack 规范）、AGENTS.md、README.md、CHANGELOG.md。

**Modify**：SKILL.md（步骤 22 重写；步骤 7 删新榜声明——已完成；步骤 15 移除 qwen-verify）、references/ 8 个（按 3 节进度表逐份改造，步骤 7 hot-trend.md 已完成：去新榜+抖音段重写+命令统一 kb.py）、assets/review-report-template.md（步骤 15 单栏）、旧 7 脚本（步骤 7 已完成：fetch_hotlist/fetch_article 转薄封装入 legacy/ 并删除根目录旧实现（用户批准 2026-09-30）；fetch_douyin 例外：步骤 7 已改造为结构化可选实验模块，保留在 scripts/ 根目录）、data/ 三文件（步骤 4 只读导入、步骤 11 起镜像）。

**Deprecate**：xinbang 子命令（步骤 7 ✅ 已移除）；第二意见全部代码与文档段落（步骤 15）；fetch_douyin 整页输出逻辑（步骤 7 ✅ 已改为结构化截断）；legacy 脚本（步骤 21 验收全绿后退役）。

## 5. 风险清单（11 条）

| # | 风险 | 严重度 | 回退 |
|---|---|---|---|
| R1 | SQLite 迁移失败/写坏唯一库 | 高 | 旧 JSONL 只读导入从不原地改写 → 任何时刻可回退 legacy CLI；restore 前 integrity_check+安全副本；可选 git init 第三层保护 |
| R2 | pydantic 不可用 | 高 | 本机已实测 2.13.4；他机 bootstrap 明确报错+安装指引；legacy 脚本纯 stdlib 兜底 |
| R3 | 旧 CLI 行为变化断裂工作流 | 中高 | 薄封装+golden 测试；变更清单（2 节）集中记录并预告 |
| R4 | FTS5 trigram 不可用 | 中 | unicode61+LIKE CJK 子串回退；tokenizer 记 meta 表 |
| R5 | 微博/tophub 端点变更 | 中 | 结构化失败+换源降级链；raw 缓存保留最近成功榜单 |
| R6 | LLM 结构化输出不稳定 | 中 | 自纠正 ≤2→extraction_failed；保留 raw+failure artifact+TTL |
| R7 | 单通道审核丧失独立复核（已接受） | 中 | Evidence 强制+七项结构化+verdict 硬规则+标点兜底 |
| R8 | 抖音登录同意门禁被跳过 | 低 | 双文档写入（hot-trend.md+AGENTS.md）；拒绝路径永远可用；单次失败即退 |
| R9 | Windows Git Bash 编码/路径 | 低中 | encoding.py 集中 UTF-8；核心零 Windows 假设 |
| R10 | JSONL 镜像与 DB 漂移 | 低 | reconcile-legacy 核对；import 幂等可重放 |
| R11 | Token 超预算复发 | 低 | 检查点 A-G + 白名单断言测试（extract/writer/analysis 强制） |

## 6. Token 检查点（A-G）

| 检查点 | 触发 | 复核内容 | 目标 |
|---|---|---|---|
| A | 步骤 7 后 | raw 只落盘不回流；hotlist --top 20；douyin 结构化截断 | 热点环节 <2K |
| B | 步骤 10 后 | extraction prompt=chunks L1+schema 摘要；缓存零 LLM 二次 | 单次提取 <4K |
| C | 步骤 11 后 | L1 紧凑、L2 显式、style --top 10、无整库 dump | 检索 <1K |
| D | 步骤 12 后 | analysis 白名单输入 | 单次分析 <3K |
| E | 步骤 14 后 | context-for-write <6K；deny 断言 | 写作上下文 <6K |
| F | 步骤 15 后 | 第二意见删除：grep 无 qwen-verify；标点 --max-findings | 思政环节降 ≥50% |
| G | 步骤 22 后 | 重跑 token-audit 出对比终版 | 总预算显著下降 |

## 7. 审计结论附录（8 条 critical/high，交叉核验全部 confirmed）

1. [critical/cross_platform] 用户专属绝对路径硬编码：de-ai.md:10、hot-trend.md:46
2. [critical/cross_platform] 思政第二意见 MCP 硬依赖：SKILL.md:34、ideological-review.md:3,54 —— **用户决策删除，以删除方式关闭**
3. [critical/engineering] 无 task_id/状态机/resume/checkpoint
4. [critical/data_boundary] 案例一条 JSON 混装四类职责；无 fact/inference 区分、无 Evidence
5. [high/token] 第二意见全文双传 —— **删除后消除**
6. [high/token] style_lib search 无 --top 整库 dump：style_lib.py:64,70-71
7. [high/engineering] 抓取零缓存：无 cache/、无 URL 归一、无哈希、raw 不落盘
8. [high/engineering] 案例仅 3 键校验、坏行静默 continue；覆写式非原子保存：case_lib.py:27,41-42
