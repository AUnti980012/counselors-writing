# Architecture Gaps（V1 → V2 逐条对照）

> 5 个探针共产出 62 条原始 gap，按 V2 规格条款去重合并为 23 组。定级规则（交叉核验裁决）：同一缺口跨探针定级不一致时**取较高者**（例：「无缓存」探针 4 判 low，按探针 3/5 的 critical 采用）。每组标注对应实现步骤（22 步计划见 `docs/history/migration-plan.md`）。

## 1. 流水线与架构概念

| # | V2 要求 | 现状（证据） | 差距 | 定级 | 实现步骤 |
|---|---|---|---|---|---|
| G-01 | 流水线含 ROUTER/TASK/ARTIFACT 三概念 | ROUTER 有雏形（SKILL.md:15-26 静态路由表 + L26 反问兜底）；TASK 完全缺失（无 task_id/状态机，全仓 grep 零命中）；ARTIFACT 完全缺失（阶段间传全文/内联 JSON：ideological-review.md:54、dissemination-review.md:13-21、style-library.md:34-44） | ROUTER 升级为可执行的 TASK 生成器；TASK 状态机与 ARTIFACT 机制从零建设 | critical | 3/17/22 |
| G-02 | 实现顺序：Schema→…→最终集成 | V1 等价资产仅覆盖顺序末端（Analysis=选题/复盘方法、Writing=写作陪伴、Audit=思政七项+标点、Output=3 模板）；Schema 仅有非机器可执行的 JSON 文档（dissemination-review.md:23-49）；Pydantic/Artifact/SQLite/Cache/Fetcher/Chunk/Extraction/Search/Task/Backup/GC 全部缺失 | V2 前 8 个基础层（Schema→Extraction）从零建设，末端 4 层（Analysis→Output）迁移现有方法文档 | critical | 全部 |

## 2. 数据边界与事实区分

| # | V2 要求 | 现状（证据） | 差距 | 定级 | 实现步骤 |
|---|---|---|---|---|---|
| G-03 | 14 类数据各司其职，禁止一个 JSON 承担多个数据职责 | cases.jsonl 一行混装 RAW 素材+文章产出+审核结果+传播复盘四类（dissemination-review.md:25-49 的 16 字段 schema 含嵌套 effect/retro）；school.json 混装学校事实/学生洞察/文风偏好三类（school.json:2-8）；index.json 是风格内容库而非索引 | 无任何数据类型分层；V2 按记录类型拆分并以 id/artifact_id 关联，存量行写迁移拆分逻辑 | critical | 1/4 |
| G-04 | documented_fact 与 AI_inference 严格区分；关键事实带 Evidence | 全仓无 fact_type/evidence/provenance 字段；retro.*/review_*/value_sublimation/画像字段均为 AI 判断但以裸事实存储；风格种子条目带报媒名/文章名/（部分）日期/原句引文，缺 URL。有「待核实」惯例（SKILL.md:41）与「可核实」检查项（ideological-review.md:14），但无字段级机制 | 所有 schema 增加 fact_type + evidence 字段组，写入与读取两侧强制；fact 无证据引用直接拒收（Pydantic validator） | critical | 1/2/11 |

## 3. JSON 契约与校验

| # | V2 要求 | 现状（证据） | 差距 | 定级 | 实现步骤 |
|---|---|---|---|---|---|
| G-05 | AI→Python 结构化输出必须 JSON→Pydantic→JSON Schema；失败自纠正 ≤2 次，再失败标 extraction_failed | AI→Python 唯一结构化入口是 case_lib.py add 的 stdin JSON，仅校验 3 个必填键存在性（case_lib.py:27,56-58）；style_lib/profiles 零校验（profiles 还接受任意 key，profiles.py:60-65）；check_punctuation 输出 repr 文本行非 JSON；坏行静默跳过（case_lib.py:41-42） | 建 Pydantic models + JSON Schema（模型即真相源，`kb.py schemas export` 回写），为 TopicCard/Analysis/Mapping/AuditReport/Article 等每类 AI 输出定义契约与 2 次自纠正循环 | critical | 2/10/15 |
| G-06 | 审核输出结构化（JSON→Pydantic） | 审核输出为 Markdown 双栏表格+勾选框（review-report-template.md:9-29），七项结论仅文本值；无脚本可校验的结论字段；无自纠正协议 | 结构化审核输出 + Pydantic 契约 + 自纠正上限协议（用户决策：单通道七项结构化自查，第二意见删除） | high | 15 |

## 4. Artifact 原则

| # | V2 要求 | 现状（证据） | 差距 | 定级 | 实现步骤 |
|---|---|---|---|---|---|
| G-07 | 阶段间传递 artifact_id/path/status/summary/hash，不传完整长文本 | 阶段间全靠 LLM 上下文拼接：选题卡内联进风格检索 prompt（style-library.md:34-44）、案例 JSON 全量内联进复盘 prompt（dissemination-review.md:13-21）、推文全文直接发第二模型（ideological-review.md:54）；三模板无 id/status/hash | Artifact 层（id/path/status/summary/hash）从零引入；改造全部 CLI 输出为 artifact 摘要 | high | 3 |

## 5. SQLite 索引与检索

| # | V2 要求 | 现状（证据） | 差距 | 定级 | 实现步骤 |
|---|---|---|---|---|---|
| G-08 | SQLite+FTS5 = Index/Search/Metadata 层；长期知识存 data/knowledge/ | 无 SQLite：cases.jsonl 追加式（SKILL.md:54）检索靠 O(n) 线性扫描 + 整行 JSON 序列化子串匹配（case_lib.py:40,74-76）；style index.json、school.json 均为单 JSON 文件；无 FTS5、无相关度排序、无元数据层 | 建 SQLite schema（含 FTS5 表）+ JSONL→SQLite 迁移；检索接口从子串改为 FTS 查询；知识类数据迁移 data/knowledge/ | critical | 4/11 |
| G-09 | Search 两级：L1 返回 ID/Title/Summary/Tags/Relevance；L2 按需读 background/problem/methods/results/transferable_patterns/evidence；禁止整库塞 LLM | L1 部分存在但设计错误：摘要仅 5 字段（case_lib.py:81-88）丢弃 style/hook/structure/value_sublimation/effect/retro 等复用关键字段，且 source_material 长文本在内；L2（按 id 读单条）无任何命令；style_lib search 直接整库全字段 dump（style_lib.py:56-72）；`--top 0` 全量 bug（case_lib.py:94） | 重构 L1 摘要字段集（对齐 V2 分组）+ 新增 L2 get-by-id + Relevance 排序 + style `--top` 硬上限 10 + 禁止整库 dump 路径 | high | 11 |
| G-10 | 幂等：已有有效 Extraction → CACHE HIT；已有 Extraction 换风格不重提；已有 Analysis 换输出格式不重研究 | 所有 fetch/检索无条件执行；case_lib add / style_lib add 非幂等（重复追加产生重复行，无 id/内容去重，case_lib.py:46-66）；仅 profiles.py set 覆盖式幂等 | 以 Content Hash + Artifact status 实现幂等短路；写入路径改幂等 upsert + 去重检查 | high | 5/10/11 |

## 6. Cache 与 URL

| # | V2 要求 | 现状（证据） | 差距 | 定级 | 实现步骤 |
|---|---|---|---|---|---|
| G-11 | Cache 四层 cache/{raw,processed,extraction,temporary}；URL Cache/Content Hash/Extraction Cache/Analysis Cache | 零存在：全仓无 cache/ 目录（Glob 确认）；三 fetch 脚本均只 stdout 不落盘（fetch_hotlist.py:104,120、fetch_article.py:55,63、fetch_douyin.py:71）；唯一去重是 fetch_hotlist.py:54-61 单响应内 title 内存去重 | 四类缓存 + 内容哈希 + 幂等 CACHE HIT 全部从零实现；三类缓存主键独立（canonical_url / content_hash|extractor|schema_version / kind|input_hashes|params_hash）保证「换风格不重提」「换输出格式不重研究」 | critical | 5 |
| G-12 | Fetch 前必须 Normalize→Canonical→Cache Check（scheme/host/尾斜杠/编码/utm_*/canonical） | 完全缺失：fetch_article.py:49 原样透传 URL（53/61 行）；fetch_hotlist.py 自建 URL 仅做 quote；全仓无 utm_* 剥离、无 canonical 解析、无 URL 缓存查询 | 新建 URL 规范化模块（Python 职责）并接入 Fetch 前置检查 | critical | 6 |
| G-13 | 原始网页生命周期：Fetch→cache/raw→Clean→Extraction→Knowledge→TTL→GC；Extraction 失败保留 raw/processed + failure artifact + TTL | 完全缺失：抓取结果直接 stdout 进入对话即丢弃；无 raw/processed 缓存、无 TTL、无 failure artifact（本 skill 报媒抓取委托外部 read/fetch-skill-main，热点走第三方 API，抓取产物不进本仓库） | 定义 raw 落盘格式、TTL 策略、GC 规则（禁止无条件删整个 cache）与 failure artifact 契约 | critical | 7-10/19 |

## 7. Task / Backup / GC

| # | V2 要求 | 现状（证据） | 差距 | 定级 | 实现步骤 |
|---|---|---|---|---|---|
| G-14 | Task Engine：16 态状态机 + status/resume/retry；单 Source 失败不崩整批 | 完全缺失：全仓无 task_id/状态文件/status 命令；7 脚本均为一次性 CLI，流程编排只存在于 LLM 会话内（SKILL.md:28-36）。「单源失败不崩整批」的精神部分存在（fetch_hotlist 单源 exit 1 由上层换源；fetch_article 三级回退）但无状态机支撑 | 从零建设：task 状态存储（SQLite）、状态机定义、status/resume/retry CLI 与 LLM 调用协议 | critical | 17 |
| G-15 | Resume：长流程中断后能续跑，有 checkpoint | 完全缺失：中断后唯一保留的是已写盘的三库数据；任何中间产物（选题卡、抓取正文、初稿、质检结果）均不落盘 | Artifact 层先落地（每阶段落盘 artifact），Resume 才有可续的锚点 | critical | 3+17 |
| G-16 | Backup：主 SQLite + 版本化 Snapshot；触发：批量 Extraction 前/迁移前/批量删除前/重大写入前/用户主动 | 完全缺失：style_lib.py:34-37 与 profiles.py:35-39 的整文件覆盖写无原子写保护（无 tmp+rename），崩溃即损坏唯一副本 | 先改原子写，再建 Snapshot 机制与触发点；V1 数据为 JSONL+JSON，backup 目标随迁移切换到 SQLite | high | 18 |
| G-17 | GC：TTL/Reference Check/Status/Last Accessed/Retention；/gc --dry-run；禁止无条件删整个 cache | 完全缺失：仓库连 cache/ 目录都不存在；案例库追加式设计数据只增不减；无 TTL、无访问时间、无引用检查、无删除语义 | 与 Cache 层同步建设（先有 cache 四层才谈得上 GC）；knowledge/ 永不自动 GC | critical | 19 |

## 8. Writing / Audit 加载边界

| # | V2 要求 | 现状（证据） | 差距 | 定级 | 实现步骤 |
|---|---|---|---|---|---|
| G-18 | Writing Agent 默认只加载 User Case+Selected Case+Analysis+Mapping+Style+Profile+Evidence；禁止默认加载 Raw HTML/完整原文/整库/全部历史 | 无白名单定义：写正文流程串读 5+ reference（writing-companion→ideological-review→de-ai→style-library 含种子→school-profile）；style_lib search 整库返回、case search 返回含 source_material 全字段；de-ai.md:5-13 全量读外部 write-zh.md；画像读取不在主工作流显式步骤（school-profile.md:35 vs SKILL.md:28-36） | 白名单在代码中强制（context-for-write 可审计输出）+「禁止默认加载」负向约束；画像读入主链路 | high | 14 |
| G-19 | 审核两级：段落级自查 + 全文级门禁；无全文双传 | 主模型自查与第二意见把同一全文各消费一遍（ideological-review.md:54、writing-companion.md:20），七项规则同文件双重书写（L7-45 清单 + L57-67 外发指令） | **以删除方式关闭**（用户决策）：第二意见删除；全文审核 = 1 次七项结构化自查 + 标点门禁（零 LLM）；清单成为唯一规则源 | high | 15 |

## 9. 跨平台与合规

| # | V2 要求 | 现状（证据） | 差距 | 定级 | 实现步骤 |
|---|---|---|---|---|---|
| G-20 | 核心只用 Markdown/Python/JSON/SQLite/CLI/文件系统；不依赖平台专属 API/命令/上下文机制 | Python 核心全部 stdlib 且 7 脚本均有 UTF-8 reconfigure（好的一面）；违反点：mcp__qwen-verify__* MCP 为默认质检门禁（SKILL.md:34，**已决策删除**）；WebSearch/WebFetch 工具名写死在降级链（fetch_hotlist.py:102 等、hot-trend.md:22）；兄弟 skill 路径依赖 ~/.claude/skills 布局（fetch_article.py:24-27）；两处用户专属绝对路径（de-ai.md:10、hot-trend.md:46）；subagent 机制引用（writing-companion.md:25）；无平台适配层 | Adapter 层统一抽象（外部 skill 发现/网页搜索/浏览器后端），清理全部绝对路径与工具名硬编码；SKILL.md 声明 Python 最低版本 | critical | 20 |
| G-21 | Web 合规：禁止绕过登录/验证码/IP 限制/付费墙/Bot 检测；403/429/CAPTCHA→有限重试→官方替代源→用户提供内容 | 部分满足：未发现对抗代码（grep 无 Playwright 命中）；xinbang 主动放弃（fetch_hotlist.py:124-126）；抖音标注风控且单次失败即退（fetch_douyin.py:5）；降级链存在（hot-trend.md:22,52）。缺口：403/429/CAPTCHA 未分类、无有限重试、无官方替代源清单、抖音依赖登录态属灰区（用户决策：登录同意门禁） | 补齐错误分类→有限重试（退避 2/4/8s ×3）→官方替代源→用户提供内容的分级降级；每源登记合规状态 | medium | 7 |
| G-22 | Slash Commands 可保留但非核心依赖；自然语言必须能执行等价流程 | V1 无任何 slash command 定义（无 commands/ 目录、无 hook），纯自然语言路由（已满足「等价流程」要求）；但 CLI 依赖 python3 在 PATH + bash 管道（PowerShell/cmd 下不可直接照抄，dissemination-review.md/de-ai.md 等命令示例） | V2 可选新增 commands/ 目录作快捷方式（非核心依赖）；CLI 收敛为单一入口 kb.py 保证 NL 等价性；命令示例补 Windows 等价说明 | medium | 22 |
| G-23 | 保留现有功能 12 项（除非失效/严重冲突/有替代/维护负担） | 全部存在且可用/半可用（见 capability-inventory）；xinbang 已失效符合移除条件；抖音属「维护负担+合规灰区」候选（用户决策：保留可选+登录同意门禁）；数据三文件为空壳，首次使用从零开始 | xinbang 移除能力声明；抖音按用户决策改造；种子数据注入（seed.json 迁入，M1 已完成）改善首次体验 | medium | 1/4/7 |

## 以删除方式关闭的缺口（用户决策）

| 原缺口 | 处置 | 说明 |
|---|---|---|
| 思政第二意见 MCP 硬依赖（critical/cross_platform，SKILL.md:34、ideological-review.md:3,54） | 删除 qwen-verify 依赖，思政质检单通道（七项结构化自查 + 标点门禁） | 跨平台 critical 项随之消除 |
| 第二意见全文双传（high/token，每篇 2k-8k ×2） | 删除后无全文外传，思政环节 token 降 ≥50% | Token 热点归零 |
| 抖音 CDP 登录态灰区（medium/compliance） | 保留可选实验模块 + 运行时登录同意门禁（询问用户，拒绝→用户提供内容） | 灰区转为用户知情同意 |

## 已接受风险（记录在案）

1. **单通道审核丧失独立复核**（用户已决策）：缓解 = Evidence 强制引用（fact 无证据拒收）+ 七项结构化自查（Pydantic 防漏项）+ 政治/隐私/事实任一 fail 即退回 + 修改后复查对应项 + 标点门禁独立兜底。
2. **抖音 CDP 路径保留**（用户已决策）：缓解 = 登录同意门禁（双文档写入 hot-trend.md + AGENTS.md）+ 单次失败即退不重试 + 结构化提取限长。
