# Storage Architecture（存储架构）

> pack M2 STEP 10 交付物（M6 起持续更新）。对应实现：`scripts/core/{atomic,artifact,cache,db,repo,search,hashing,urlutil,fetcher,preprocess,chunker,pipeline,hotlist,compat,extract,analysis,mapping,profile,writer,audit,punctuation,deai,output}.py`。
> 与 `docs/architecture/data-contract.md` 的分工：本文件讲「存储怎么实现」；data-contract 讲「数据怎么定义」。

## 1. 总览（四层存储）

```
┌─────────────────────────────────────────────────────────────────┐
│ data/knowledge/<entity>/<id>.json   文件 = 权威内容（长期）        │
│ data/knowledge/styles/seed.json     种子信封（唯一例外：整文件）    │
├─────────────────────────────────────────────────────────────────┤
│ data/registry/artifacts.jsonl       血缘账本（追加式 JSONL）       │
│ data/artifacts/<file>               阶段产物字节（Artifact 落盘）   │
├─────────────────────────────────────────────────────────────────┤
│ data/index.db（SQLite + FTS5）      索引/搜索层（可重建，非权威）   │
├─────────────────────────────────────────────────────────────────┤
│ cache/{raw,processed,extraction,tasks}/   临时中间产物（分 TTL）   │
│ data/backups/                       备份（legacy 导入前/快照）     │
└─────────────────────────────────────────────────────────────────┘
```

PERSISTENCE RULE（pack GLOBAL BOOTSTRAP）：**文件是主要长期内容存储；SQLite/FTS5 是索引层不是权威；Registry 是血缘账本不是搜索库**。这条规则的直接推论：任何时刻可以删除 `data/index.db` 并从 canonical 文件确定性重建，不丢失知识。

## 2. 文件存储（权威内容）

- 每实体一文件：`data/knowledge/<entity>/<entity_id>.json`（entity 子目录映射见 `scripts/core/paths.py` 的 `KNOWLEDGE_SUBDIRS`；draft 落在 `articles/`）。
- 种子风格是**整文件信封** `data/knowledge/styles/seed.json`（M1 冻结，只读，校验契约 `seed.schema.json`）。
- 写入一律原子：`tmp（同目录随机后缀）→ fsync → os.replace`（`core/atomic.py`）；先 Pydantic 校验、再写文件、最后同步索引——**无效写入绝不静默替换有效内容**（pack M2 STEP 3）。
- M3 起 `sources` / `documents` / `chunks` 的 canonical 文件由抓取管道（`core/pipeline.py`）落地：source=来源元数据（含失败状态记录），document=清洗后文档（章节结构 + chunk 引用），chunk=分块文本（≤2000 字符内联，契约有界）。

## 3. Artifact Registry（血缘账本）

- 位置：`data/registry/artifacts.jsonl`（追加式 JSONL，行级原子追加 + fsync）。
- 每行一条 `ArtifactRecord`（契约校验后写入）：身份（artifact_id/artifact_type）、路径、状态（created/valid/invalid/failed/expired）、content_hash、schema_version、血缘（source_ids/parent_ids）、retention（temporary/permanent/task_bound）、时间戳。
- **账本语义**：同 artifact_id 状态流转追加新行，读取时**最新行胜出**——保留完整历史，不覆写。
- 读取侧容错：坏行跳过并报告（不因一行损坏读不出全部）。
- **不是全文搜索库**（pack M2 STEP 2），全文检索走 SQLite/FTS5 或 canonical 扫描。

## 4. Artifact 落盘与幂等（pack M2 STEP 3/8）

- 内容字节落 `data/artifacts/<filename>`（原子写）；`ArtifactRecord.path` 存仓库相对路径。
- ID 生成：`<TYPE段>-<yyyymmdd>-<8hex>`；TYPE 段 = artifact_type 折叠非小写字母后截断 16 字符（契约 `ARTIFACT_ID_PATTERN` 的 TYPE 段只允许 `[a-z]+`，`raw_html` → `rawhtml`）。默认 filename = `<artifact_id>.<type 后缀>`。
- **filename 约束**（对抗审查 C13/C22/C28）：只接受单段文件名 `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`——禁止路径分隔符/绝对路径/UNC（防路径穿越与目录逃逸），且不得与 registry 中已有记录同名（防互相覆盖）。
- **幂等复用**（全部满足才复用）：同 artifact_type + 同 content_hash + 同血缘（source_ids/parent_ids 集合）+ 同 retention/summary/metadata + 状态 created/valid + **落盘文件确实存在**（文件丢失 = 非 valid，重新落盘）。expires_at 请求为 None（未指定）时不构成不复用理由——它是易变的保留时间戳（now+72h 每次不同），作为复用条件会永久破坏幂等（M3 审查确认后修正）；保留窗口刷新走 `refresh_expiry`（追加新账本行，latest-wins）。
- 状态流转：`set_status` 追加账本行前用 ArtifactRecord 重新校验——契约外状态值绝不进入账本。
- 索引投影同步：ArtifactStore 的 `index_sync` 回调（CLI 注入 repo.upsert_artifact），create/set_status 后 artifacts 投影表与 registry 保持 latest-wins 一致。
- 内容校验：`read(verify_hash=True)` 比对 content_hash，防磁盘损坏/篡改。

## 5. 哈希（pack M2 STEP 4，两类严格区分）

| 哈希 | 定义 | 用途 |
|---|---|---|
| `content_hash` | NFC 归一 + 空白折叠后 utf-8 字节的 sha256（str 输入）；bytes 输入走原始字节 sha256 | 内容身份：幂等复用、损坏检测 |
| `url_identity` | 规范化 URL 的 sha256 | cache key 的 url: 段 |

**URL identity ≠ content identity**：同一 URL 内容变化时 url_identity 不变、content_hash 变；缓存命中用 url_identity，内容新鲜度用 content_hash 对比。

## 6. URL 归一化（pack M2 STEP 7）

`core/urlutil.normalize_url`，保守集合（**不删、不改写可能改变内容的成分**）：

- scheme/host 小写；非 ASCII host 做 IDN（idna）编码，失败保留原样；
- 去默认端口（http:80/https:443）、去 fragment；
- 删 `utm_*` 跟踪参数（大小写不敏感，含 percent 编码形式；**gclid/spm 等一律保留**——宁可少归一，不可误归一）；
- path：仅去尾斜杠（根保留 `/`），**不做 percent 解码/重编码往返**——`%2F`（段内字符）与 `/`（段分隔符）在服务器端语义可能不同，往返会把语义不同的 URL 归并为同一 canonical；
- query：值**原样保留**（`a+b` 表单空格与 `a%2Bb` 字面加号语义不同，不做任何值归一）；参数顺序保留；
- **path 大小写保留**（/A 与 /a 是不同资源）；
- 带认证信息（user:pass@）的 URL 整体跳过归一；
- 无法解析（缺 scheme/host）→ 返回原串（调用方决定，幂等性不劣化）。

## 7. SQLite / FTS5 索引（pack M2 STEP 5/9）

- 位置：`data/index.db`（pack 规范路径；**不得**同时引入 `data/index/kb.db`）。
- 连接：WAL + busy_timeout=5000；迁移：`schema_migrations(version, applied_at)` 版本化，`init` 幂等重放。
- `meta` 表记录运行时事实：`schema_version`（数据契约版本）、`db_version`、`fts_tokenizer`。
- **契约版本闸门**：meta.schema_version ≠ 当前 `SCHEMA_VERSION` → `IndexVersionMismatch`，拒绝带着过期契约静默运行，提示 rebuild。
- 表：sources / documents / chunks / cases / styles / topics / artifacts / evidence / audits / effects / profiles / knowledge_files（canonical 文件 ↔ 索引行映射，rebuild 与对账依据）。
- FTS5 虚表：`chunks_fts` / `cases_fts` / `styles_fts`（独立内容表）。
- **FTS 注册回退**（R4）：先试 `tokenize='trigram'`（中文 3-gram 子串检索）；`OperationalError` → 回退 `unicode61`；实际 tokenizer 记入 meta。本机 SQLite 3.40.1 实测 trigram 可用，回退路径有测试锁定。
- 检索分流（`core/search.py` + `core/repo.py`）：
  - trigram：FTS5 MATCH（关键词包双引号短语 + 内部引号转义，防查询注入）；**查询词 <3 字符时退化子串扫描**（trigram 不索引 2-gram）；
  - unicode61：LIKE/Python 子串扫描 canonical 文件（unicode61 把连续中文当一个 token，CJK 不走 FTS）。

## 8. 索引重建（pack M2 STEP 9）

`kb.py db rebuild`：确定性从 canonical 文件 + registry 重建：

1. 清空可重建实体的索引表 + FTS + knowledge_files（case/style/topic/profile/audit/effect/evidence/source/document/chunk + artifacts）；
2. 逐文件 Pydantic 解析 → 重灌索引行 + FTS（chunks 含 chunks_fts）；
3. artifacts 从 registry 账本（latest-wins）重建投影表；
4. **损坏/不合契约的 canonical 文件记入 errors 并 exit 1**（不静默跳过）；
5. M3 起全部实体都有 canonical 文件 → `skipped_no_canonical` 恒为空（保留字段向后兼容）。

## 9. Cache（pack M2 STEP 6）

- 四层命名空间（`core/cache.py`），每层一个目录，条目 = `<sha256(key)[:24]>.json`（元数据）+ `.bin`（内容字节）：

| 命名空间 | key 前缀（契约） | TTL | 内容 |
|---|---|---|---|
| raw | `url:` | 30d | 抓取原始字节 |
| processed | `content:` | 30d | 清洗后正文/处理产物 |
| extraction | `extract:` | 90d | LLM 提取结果（幂等复用） |
| tasks | `analysis:` | 7d | 任务态中间产物 |

- key 必须带所属命名空间前缀（`check_key` 强制）；文件名用 key 的 sha256（防穿越、免转义）。
- 元数据：key/namespace/created_at/updated_at/last_accessed/status/ttl_days/content_hash/schema_version/artifact_ref/hits/metadata（M3 起：写入侧附加键值元数据，如 http_status/truncated/canonical_url）。
- **惰性过期**：get 时检查 TTL，过期视为 miss（status=expired，不立即删）；命中更新 hits/last_accessed。
- **purge 默认 dry-run**：只报不删；`--apply` 才删，且只删过期条目；孤儿 .bin 只报告不删（保守）。
- TTL 值（M2 定稿，细化了旧计划的歧义写法）：raw 30d / processed 30d / extraction 90d / tasks 7d。
- **按条目 TTL 覆盖（M3）**：`put(ttl_days=N)` 可覆盖命名空间默认值；抓取管道给 raw 条目用 3 天（pack M3 STEP 5 建议 raw 默认 24-72h），其余命名空间维持默认。

## 10. 血缘（Lineage）

- Artifact → Artifact：`parent_ids`（上游 artifact_id 列表）+ `source_ids`（来源实体）。
- 知识实体 → 证据：case 的 fact 强制带 `evidence_ids`；Evidence 三级回指 artifact/source/document/chunk。
- 检索链路：chunk → document → source（表外键语义，由应用层保证，索引层不声明 FK 约束）。

## 11. 失效策略（Invalidation）

| 对象 | 失效方式 | 触发 |
|---|---|---|
| cache 条目 | TTL 惰性过期 → purge 删除 | 读时检查 / `kb.py cache purge` |
| artifact | `expires_at` 过期 → status=expired（GC 标记，M7） | `kb.py gc`（M7） |
| 索引行 | canonical 文件更新时 upsert 覆盖（save 即同步） | 每次 save |
| 整个索引 | 契约版本不匹配 → rebuild | init 闸门 |
| canonical 知识 | **永不自动 GC**（M7 起仍不） | — |

## 12. V1 → V2 导入（`kb.py db import-legacy`）

- 旧三文件（cases.jsonl / style_library/index.json / school.json）**只读导入**，从不原地改写；导入前备份到 `data/backups/legacy-import-<ts>/`。
- 一条 V1 案例拆五实体（case/topic/draft/audit/effect，映射见 data-contract §8.2）；含 structure 的案例独立落 user StyleRecord（种子只读不可污染）。
- 幂等：同 id 跳过；`reconcile-legacy` 数量级对账（含 structure 案例修正项）。
- 无法映射/超长截断一律进 warnings（**绝不静默丢失**，原文在备份里）。

## 13. 已知限制

- FTS unicode61 回退路径的检索性能随 canonical 文件数线性增长（单用户数据量下足够；trigram 可用时不触发）。
- Registry 追加式账本不做压缩（历史行保留）；万级条目内线性扫描 latest() 足够，超量后按需加索引文件。
- Registry JSONL 行级追加（open('a')+fsync）而非整体 os.replace：账本语义要求历史行保留、整体重写代价高（对抗审查 C06 确认为有意取舍）；极端场景（写中途掉电）可能在末尾留下半行，读侧逐行容错（坏行跳过并报告）保证不整体损坏。
- 检索短词（<3 字符）退化子串扫描而非 FTS：trigram 不索引 2-gram 的本质限制，正确性优先。
- rebuild 是「全量重灌」而非增量：删除后可重跑恢复（幂等）；中途异常中断时索引可能半空，重跑即自愈（审查 S02 接受为已知行为）。
- 抓取失败**不做负缓存**（blocked 等失败状态不缓存）：同 URL 重复抓取会重新发起（每次尝试内重试仍有限）；失败本身已记录为 source canonical（状态归一，血缘不丢），且**失败不覆盖 last-good content_hash/title**（M3 审查修复）。
- stdlib 预处理器（HTMLParser）对复杂 JS 渲染页面提不出正文——由 ArticleBackend 降级链（read/fetch-skill）或用户提供内容兜底（pack M3 STEP 11）；**全噪声长页（巨型菜单/链接墙）返回空正文**（parse_failed 语义），绝不把噪声当正文（M3 审查修复）。
- 验证码墙检测只收高特异性中文短语（请输入验证码/滑动验证/人机验证/拖动滑块/verify you are human 等）——裸英文词 captcha/access denied 会误伤正文（技术科普文），刻意不判；仍存在未收录文案的漏报空间（M3 审查权衡后接受）。
- 单文档 >500 块（契约 chunk_ids 上限，≈60 万字符）拒绝入库：结构化失败且不写半个管道（M3 审查修复）；指针 chunk_ids 只带前 50（chunk_count 记总数）。
- 抖音结构化提取是行状启发式（rank→标题→热度），DOM 改版后可能提取为空 → 显式 parse_failed 降级热榜站（实验项固有风险，单次失败即退）。
- M4 证据是**文档级/块级**自动溯源（document 入口回指 document，chunk-only 入口回指首个 chunk），非逐 claim 的 chunk 级精确对齐——精确 chunk 级证据留 M5/M6 细化；无 evidence_excerpt 时 excerpt 留空并 note 标注（不自引用 statement，诚实「转述型」证据）。
- M4 语义字段的 PII 脱敏是**确定性模式兜底**（学号/身份证/手机号/邮箱），姓名/可定位事件组合依赖 LLM prompt 指令脱敏，无离线姓名识别。
- M4 LLM 提取依赖外部 `llm_fn`（`--llm-cmd` 或 Agent 编排）；仓库不声明任何 LLM API 依赖（用户决策「Pydantic 为唯一第三方依赖」），无内置模型、无离线兜底。
- M4 `llm_fn_from_cmd` 用 shlex（POSIX 规则）无 shell 执行：Windows 反斜杠路径会被吞（请用正斜杠/PATH 命令名）、.cmd/.bat shim 无法被 CreateProcess 执行（请用 .exe 或 `cmd /c`/`sh -c` 包装）。
- M4 自纠正 prompt 基于固定 base_prompt + 最新反馈（不累计历史反馈），但自纠正轮 = base+反馈仍可能 >4K（检查点 B 只约束初次 prompt）。
- M4 单次提取只注入前 20 块且受总预算 6400 字符约束，超长文档提取基于截断前缀（截断显式标注，含块数截断），全量提取留 M5 分批/检索增强。
- M5 案例检索 L1 的 `problem` 字段不进投影（用 fact_count/updated_at 替代），问题概述需 L2 `--get` 读——这是 Token 纪律的有意取舍，检索 <1K。
- M5 analysis/mapping 的 case 输入白名单只注入紧凑语义字段（事实/推断/可迁移模式），不注入 background/methods/results 长文本；超长案例的分析可能因截断丢失细节——精确长字段分析留 M6 写作前按需 L2 读取。
- M5 案例 JSONL 镜像（C-08）是追加式（同 id 多行，读取 latest-wins），与 registry 语义一致；镜像不反向导入（V2 权威仍是 knowledge 文件），reconcile-legacy 按 latest-wins 对账。
- M5 legacy 薄封装的输出 shape 从 V1 收敛（case 的 source_material 长文本、style 的 text 全文不再返回），字段命名有变化（id vs case_id 等）——这是有意变更（C-04/05/08 + Token 纪律），非字节兼容；依赖 V1 精确输出的下游需改用 `kb.py search --get`。
- M6 标点门禁 ko locale 无规则（exit 2「暂不支持」，不假装已检查）；C-09 数字+CJK 不强制空格是审慎取舍（"2026年" 不误报，但「数字紧邻字母且确需空格」的罕见场景也放行）。
- M6 写作只注入案例的紧凑投影（background/events/methods/results 字段级截断 ≤800/≤10 条），超长案例的完整叙事细节写作前需按需 L2 读取——白名单 Token 纪律的有意取舍。
- M6 审核是单通道（C-07 第二意见删除）：政治/隐私/事实 fail 靠七项结构化自查 + verdict 硬规则（political/factual/privacy 任一 fail → passed=false）拦截，无独立复核（用户已决策，缓解见 architecture-gaps 已接受风险 1）。

## 14. Web 获取管道（M3）

`URL → Access → Fetch → Clean → Normalize → Chunk → Artifact Pointer`（`core/pipeline.py` 编排）：

```
kb.py fetch <url> / kb.py ingest（用户内容）
  ├─ core/fetcher.py    合规 HTTP 抓取（FetchBlocked 结构化失败 / 429、5xx 重试×3 退避 2/4/8s
  │                     / 403、CAPTCHA 墙即 blocked 不重试 / raw 先落 cache/raw TTL 3d /
  │                     URL 校验（http(s)、无认证信息、有主机、≤2048、无控制字符）/
  │                     协议错误（IncompleteRead/BadStatusLine）结构化归一 / 8MiB 截断探测）
  │                     → raw_html artifact（retention=temporary，expires_at 72h）
  ├─ 直抓提不出正文（纯 JS 页）→ core/compat.py ArticleBackend 降级链
  │                     （read 本地提取器 → fetch-skill web → 用户提供内容）
  │                     仅技术性失败可降级：blocked/access_restricted（403/付费墙/CAPTCHA）
  │                     对 URL 生效，换后端抓同一 URL 属付费墙绕过 → 不降级（审查修复）
  │                     后端输出同样做验证码墙筛查
  ├─ core/preprocess.py 纯 stdlib 清洗：剔除 script/style/nav/广告容器（词边界匹配防
  │                     readable/header 误伤）→ 标题/章节 → 密度选主内容（全噪声→空正文）
  │                     → 空白归一 → 语言/字数；GBK 页面 gb18030 解码不静默毁中文
  │                     → document canonical + processed_document artifact + cache/processed
  ├─ core/chunker.py    段落/句界分块（target 1200 / overlap 100 / ≤2000 硬上限含分隔符预算 /
  │                     char 区间=文档精确切片 / estimated_tokens=字符/1.6）
  │                     → chunk canonical + chunks 索引行 + chunks_fts + chunkset artifact
  └─ 输出：小型结构化指针（pack STEP 4）——artifact_id/path/status/source_id/
      content_hash/标题/语言/字数/chunk 引用（前 50）/摘要，正文绝不进 stdout
```

- **幂等**：source/document/chunk id 由（canonical URL / 内容哈希）确定性推导——同 URL 同内容重复执行 → 同 id → canonical 覆写 + 索引 upsert + cache/artifact 复用，不产生重复数据。**artifact 幂等复用不受 expires_at 影响**（create 的 expires_at=None 表示未指定；保留窗口刷新走 refresh_expiry 追加账本行，latest-wins——审查修复）。
- **去重**（pack STEP 9）：同内容多 URL → 各自 source 归属保留（source attribution 不丢），content_hash 相同供下游去重；processed 缓存按内容哈希跨 URL 复用（不写归属字段，防覆盖）。
- **状态归一**（pack STEP 3）：success/blocked/access_restricted/unavailable/timeout/rate_limited/parse_failed；失败也落 source canonical（尝试记录入账，保留 last-good content_hash/title），绝不伪造正文；最终失败状态按结构化优先级推断（不做文本子串匹配）。
- **热榜**（`core/hotlist.py`）：weibo/tophub 输出与 V1 逐字段一致，走同一合规 Fetcher + cache；xinbang 已移除（C-01）。
- **抖音**（`scripts/fetch_douyin.py`，C-02）：结构化 JSON [{title,topic,likes}] 限长截断 + 登录同意门禁（--consent；**已登录会话（sessionid cookie）未同意同样拦截**——审查修复）+ 单次失败即退；不再输出整页 innerText。
- **Token 检查点 A**：raw 只落盘不回流；hotlist 默认 --top 20；douyin 结构化截断；fetch 输出指针 <2K（chunk_ids 只带前 50）。

## 15. LLM 提取管道（M4）

`document → content-hash → extraction_cache 短路 → 最小 prompt → LLM → parse → 确定性字段注入 → 校验 → 自纠正 ≤2 → knowledge/artifact`（`core/extract.py` 编排）：

```
kb.py extract <document_id> --extractor case_facts|style_pattern|topic_signal
  ├─ 幂等短路：extraction_cache（extract: 前缀）命中 → 零 LLM（Token 检查点 B）
  ├─ 最小 prompt：指令 + schema 摘要（字段名/必填/类型/说明，排除 id/时间戳/来源等
  │              托管字段）+ 有界 chunks（MAX_INPUT_CHARS=6000 / MAX_CHUNKS_IN_PROMPT=20，
  │              超预算截断并明确标注）+ source/document 引用
  ├─ LLM 调用：llm_fn 注入（仓库不声明 LLM API 依赖；CLI 用 --llm-cmd 外部命令
  │            stdin 传 prompt / stdout 收 JSON，或 --prompt-only 由 Agent 编排）
  ├─ parse_llm_json（剥 ```json 围栏 / 首尾杂讯）→ 确定性字段注入（剥离 LLM 越权的
  │            id/schema_version/source_ids，Python 补 case-/style-/top- id + 来源 +
  │            证据）→ Pydantic 校验（validate_entity）
  ├─ 自纠正 ≤2（MAX_SELF_CORRECT）：校验失败 → 追加「上次输出 + 错误 + 修复指令」
  │            → 重试；用尽仍失败 → extraction_failed + failure artifact
  └─ 输出：小型指针（status/extractor/extraction_id/artifact_id/input_refs/output_refs/
      attempts/content_hash），正文绝不进输出
```

- **extractor → 实体**：case_facts → CaseRecord，style_pattern → StyleRecord，topic_signal → TopicRecord（pack M4 STEP 2 至少三种）。
- **FACT/INFERENCE 证据注入**：documented_fact / source_claim 由 Python 自动生成 EvidenceRecord（回指 source/document，摘录有界 ≤500，优先 LLM 提供的 evidence_excerpt），注入 fact.evidence_ids；ai_inference / derived_pattern 必须由 LLM 填 basis（缺则校验失败进自纠正）。
- **幂等**：cache key = `extract:{content_hash}:{extractor}:{schema_version}`；id 确定性推导（case-/style-/top- + content_hash 前 12 位；ext-、evd- 同法）；同文档同 extractor 重复执行 → 同 id → canonical 覆写 + 索引 upsert + cache 命中，不产生重复数据。
- **失败不丢源**：extraction_failed 保留 raw/processed（source/document/chunk 不删），failure artifact 内容 = ExtractionRecord（input_refs + validation_errors（≤50 条）+ schema_version + model_metadata），status=failed；成功 artifact status=created。
- **extraction 落地**（data-contract §1）：ExtractionRecord 序列化进 extraction artifact（registry 记账），**不落 knowledge 目录**（extraction 是运行记录非知识；无索引表、无 canonical 子目录）。
- **Token 检查点 B**：初次 prompt estimated_tokens < 4000（有界 chunks + 紧凑 schema 摘要）；同输入二次提取走 cache 零 LLM。

## 16. 检索 + 分析 + 映射管道（M5）

`query → search（L1/L2）→ analysis（case → AnalysisRecord）→ mapping（case × profile → MappingRecord）`（`core/search.py` + `core/repo.py` + `core/analysis.py` + `core/mapping.py` + `core/profile.py` 编排）：

```
kb.py search case|style|topic <kw> [--top N] [--get ID] [--fields a,b]
  ├─ L1（默认）：紧凑投影，绝不含长文本（Token 检查点 C：检索 <1K）
  │   case → case_id/title/tags/fact_count/updated_at（五字段）
  │   style → style_id/origin/tags/usage_count（--top 硬上限 10，C-05）
  │   topic → topic_id/title/summary/generation_status/updated_at
  └─ L2（--get）：读单个实体完整字段（--fields 可投影，如 methods,transferable_patterns）

kb.py analysis --case <id> [--case <id2>] [--llm-cmd ... | --prompt-only]
  ├─ 输入白名单（Token 检查点 D：<3K）：只注入案例紧凑语义字段（标题/问题/标签/
  │   事实/推断/可迁移模式），绝不注入 background/methods 全文、绝不整库
  ├─ LLM → parse → PII 脱敏 → 确定性字段注入（analysis_id=ana-+hash、input_case_ids、
  │   evidence 从输入案例聚合）→ Pydantic 校验 → 自纠正 ≤2
  ├─ cache（tasks 命名空间 analysis: 前缀，TTL 7d）：case_ids 组合 + schema_version
  │   稳定时幂等复用（同输入零 LLM）
  └─ 成功 → AnalysisRecord（patterns 用 derived_pattern+basis，逐条 fact_type）→
      canonical + analyses 索引表 + analysis artifact（permanent）

kb.py mapping --case <id> --profile pro-school [--llm-cmd ... | --prompt-only]
  ├─ 输入：案例紧凑投影 + 画像 6 业务字段投影
  ├─ 输出 MappingRecord：matching_points/differences/adaptation_requirements/
  │   transferable_elements/non_transferable_elements/risks/rationale
  │   （pack M5 STEP 8：外部成功案例 ≠ 本地直接套用，显式差异字段）
  └─ cache（analysis: 前缀）幂等；canonical + mappings 索引表 + mapping artifact

kb.py profile get <key> / set <key> <value> / dump
  └─ 7 键白名单（C-06）：6 业务字段（school_name/school_type/student_profile/
      common_topics/sensitive_points/title_style_preference）+ updated_at 托管；
      school_type 自由文本 → 枚举（宁可 other + provenance，不静默丢失）
```

- **L1 投影纪律**：检索绝不返回长文本（case 的 background/methods、style 的全文、topic 的 relevance/novelty 均不进 L1）；`--top` 有界（case/topic [1,50]，style 硬上限 10），`--top 0` 钳制 ≥1（C-04，修复 V1 rows[-0:] 全量 bug）。
- **写入路径（C-08）**：case 写入 = Pydantic 校验 → knowledge 文件（权威）→ DB 索引 → JSONL 镜像（**独立文件** `data/case_library/cases.mirror.jsonl` V1 兼容投影，追加式幂等；mirror path 可注入、默认 None 防测试污染，CLI 层传 `CASE_MIRROR_PATH`）。镜像独立于只读导入源 `cases.jsonl`（审查确认：写回只读源会被 import-legacy 重复导入并撑大 reconcile 计数）。文件是真相源（pack PERSISTENCE RULE），SQLite/JSONL 都是投影/镜像。
- **analysis/mapping 索引表**（db migration v2）：`analyses`/`mappings` 只存 L1 紧凑投影（analysis_id/topic/case_count 等），权威内容在 canonical 文件；rebuild 覆盖。
- **legacy 薄封装**：`scripts/case_lib.py`/`style_lib.py`/`profiles.py` 转薄封装（逻辑迁 core/case_lib_cli.py、style_lib_cli.py、profile_cli.py），V1 命令名保留、输出 shape 收敛为紧凑字段（source_material 长文本被替代——Token 纪律）。
- **Token 检查点 C/D**：C（检索 <1K）——L1 五字段 + top 有界 + 无整库 dump；D（分析 <3K）——白名单输入只注入紧凑语义字段 + cache 零 LLM 二次。

## 17. 写作 + 审核 + 输出管道（M6）

`mapping → 白名单上下文包 → LLM 写作 → DraftRecord → 七项审核 + 标点/去AI 门禁 → AuditRecord → 渲染 → FINAL artifact`（`core/writer.py` + `core/audit.py` + `core/punctuation.py` + `core/deai.py` + `core/output.py` 编排）：

```
kb.py context-for-write --mapping <id>    白名单审计输出（Token 检查点 E：<6K）
kb.py write --mapping <id> --llm-cmd ...  LLM 写作 → DraftRecord（articles/ canonical + draft artifact）
kb.py audit --draft <id> --llm-cmd ...    LLM 七项自查 + 标点/去AI 门禁 → AuditRecord（audits/ + audit artifact）
kb.py punctuation <file>                   标点门禁（确定性，零 LLM；C-03/C-09）
kb.py deai <file> [--fix]                  去 AI 味门禁（确定性，零 LLM；Humanizer-zh 机械子集）
kb.py output render --draft <id> --audit <audit_id>   draft → FINAL artifact（permanent，零 LLM；--audit 写入血缘 metadata）
```

- **写作白名单**（pack M6 STEP 1 / G-18）：writer 代码级只注入批准的 topic/case/analysis/mapping/style/profile（结构化投影，字段级截断）；deny 清单（raw HTML/完整原文/整库/历史）不进上下文；lineage 由 Python 从输入聚合（case_ids/source_ids/evidence_ids 去重）——每个主要事实性断言可追溯。
- **draft 存储**：canonical-only（无索引表，落在 `data/knowledge/articles/`），DRAFT artifact 记账；draft_id = sha256(mapping_id+mode+model_mode) 幂等覆盖；cache（tasks 命名空间 `analysis:writing:` 前缀）含内容哈希，编辑后 miss 重跑。
- **审核单通道**（C-07）：七项（political/factual/value/labeling/ai_trace/privacy/copyright/format）由 LLM 填 issues，标点（punctuation）与去 AI（deai）由 Python 确定性注入（零 LLM）；分组聚合 fact/style/format/risk；verdict 硬规则 political/factual/privacy 任一 fail → passed=false（代码级强制，schema `_verdict_rule`）。
- **标点门禁**（`core/punctuation.py`）：迁自 check_punctuation.py；C-09 三规则修复（数字+CJK 不强制空格、em-dash 仅 zh 禁用、全角空格只报 CJK↔半角之间）+ C-03 ko exit 2 + `--max-findings` 截断；legacy `scripts/check_punctuation.py` 薄封装透传 `cli_main`。
- **去 AI 味门禁**（`core/deai.py`）：Humanizer-zh 31 模式机械子集规则化（AUTO_FIX 连接词/套话/进行+动词/限定词堆叠/emoji + FLAG_ONLY 高频词/拔高/客服腔等）；复用 punctuation 的豁免逻辑并跳过 YAML frontmatter；零 LLM。
- **输出**：纯字符串占位符渲染（`{title}/{subtitle}/{sections}/{closing}`，无模板引擎）；FINAL artifact（`final_output`，permanent）；换格式重渲染 = 读 draft 重新渲染，零 LLM。
- **Token 检查点 E/F**：E（写作上下文 <6K）——白名单投影 + 总预算 9600 字符 + cache 零 LLM；F（思政降 ≥50%）——第二意见删除后全文单传 + 标点门禁零 LLM。

## 18. 数据生命周期：备份 + 恢复 + 保留 + GC（M7）

`core/task.py`（16 态状态机）+ `core/backup.py`（在线快照）+ `core/gc.py`（引用感知 GC）
+ db migration v3（tasks/task_events/backups 表）。完整文档见 `docs/architecture/data-lifecycle.md`，
本节只列与存储架构的交叉点：

- **tasks/task_events/backups 是运行态表**（非 canonical 内容）：`rebuild_index` 不删这三张表，
  tasks 是中断恢复依据（context_refs 锚点）、backups 是恢复登记。
- **备份对象 = index.db**（在线快照 `Connection.backup`），canonical knowledge 是内容权威、
  可随时重建索引，故不打包整仓库（`docs/architecture/data-lifecycle.md` §2-3）。
- **GC 引用感知**：删除 artifact 前构建存活引用集合 = registry 血缘（source_ids/parent_ids）
  ∪ tasks 表引用（input_refs/output_refs/context_refs[kind=artifact]）；`permanent` 永不候选，
  `data/knowledge/` 永不触碰（`docs/architecture/data-lifecycle.md` §4）。
- **cache purge 复用**：GC 的 cache 部分直接调 `CacheManager.purge`（默认 dry-run，只删
  `status=expired`，孤儿 `.bin` 只报不删）。
- **已知限制（GC 保守性）**：`collect_references` 遍历 registry 全部 latest 记录（含已
  `expired` 的血缘），故已清理记录仍会保守保护其下游——可能过度保留，宁可少删不误删
  （pack STEP 8「绝不删仍被引用的 artifact」优先）。
