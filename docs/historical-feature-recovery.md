# Historical Feature Recovery（历史能力恢复矩阵）

> pack M0 STEP 3 交付物。恢复来源说明：本仓库**非 git 仓库**（无提交历史），全仓无 archives/ 目录、无旧 prompt 存档、无备份文件。因此「历史」唯一可考来源 = 仓库内现存物证（SKILL.md frontmatter 与描述、references 内的种子数据与过期注释、scripts 内的占位与副本声明、文档与代码的漂移痕迹）+ 审计期外部物证（外部 write skill 的 write-zh.md、`.claude/plans/` 计划文件）。
> 原则（pack STEP 3）：历史行为是需求证据，不是首选实现；不盲目恢复历史代码。

## 1. 恢复矩阵

| # | feature_name | historical_source | what_it_did | current_equivalent | missing_functionality | recovery_required | suggested_target_milestone | retirement_reason |
|---|---|---|---|---|---|---|---|---|
| H-01 | 三报种子风格库（真实文章沉淀） | references/style-library.md:61-89（人民日报 2025-04-30、光明日报教育周刊、中国青年报 2026-09-09 三篇文章的标题公式/句式/话术，含原句引文） | 为「爆款研究/仿写」提供三报风格指纹 + 代表性句式/标题公式，是 V1 唯一非空的知识数据 | 仍内嵌于 reference（L61-89），与 data/style_library/index.json 双源、两套机制检索割裂 | 结构化（style 契约）、可检索（SQLite+FTS5）、带来源标注 | 是 | **M1**（迁 data/knowledge/styles/seed.json，本里程碑完成）+ M2（索引） | — |
| H-02 | 三报「风格指纹」速查表 | references/style-library.md:5-11（三报气质/典型手法/适用场景对照表） | 快速决定「这篇推文仿哪家报媒」的方法论 | 仍在原处、可用 | 与结构化 Style 记录联动（指纹表 = style 摘要投影） | 部分 | M5（style 检索 L1 摘要对齐指纹表） | — |
| H-03 | 案例库 16 字段 schema | references/dissemination-review.md:25-49（id/created_at/source_material/angles/title_used/title_alt/hot_topic/style/hook/structure/value_sublimation/review_result/review_issues/effect/retro/tags） | 定义案例沉淀的单条 JSON 结构（LLM 复盘后按此构造） | schema 仅存在于文档；脚本 case_lib.py 只校验 3 键（id/source_material/angles）——文档与实现漂移；cases.jsonl 至今 0 字节（从未真实使用） | 字段级校验、fact/inference 分离、evidence、幂等 upsert | 是 | **M1**（拆四类契约：CASE/ARTICLE/DRAFT/AUDIT/EFFECT，本里程碑完成）+ M2（旧 JSONL 只读导入时按映射拆分） | — |
| H-04 | 新榜热点抓取 | SKILL.md:3,21 宣称支持 + references/hot-trend.md:13 声明 + scripts/fetch_hotlist.py:124-126（stub：打印「反爬较强，暂不支持直抓」exit 1） | 历史宣称可抓新榜热榜；实际唯一实现是永久失败占位 | 占位 stub 仍在，文档仍宣称支持（文档 bug） | — | 否 | —（移除声明） | 用户决策：反爬不可行，移除能力声明，占位代码退役（M3 执行） |
| H-05 | 抖音热点（CDP 实验项） | scripts/fetch_douyin.py（经 web-access-main CDP 代理 localhost:3456 驱动已登录浏览器）+ references/hot-trend.md:46-48 | 打开 douyin.com/hot，滚动后取整页 innerText | 实现仍在但输出与文档声称不符（文档称提取标题/话题/点赞，代码实为整页 innerText）；依赖登录态 | 结构化提取 [{title,topic,likes}]、限长、登录同意门禁 | 部分 | M3（改造为可选实验模块） | 用户决策：保留可选 + 运行时登录同意门禁（拒绝→用户提供内容） |
| H-06 | 思政第二意见（qwen-verify MCP） | SKILL.md:34,49 + references/ideological-review.md:3,54 + 报告模板双栏 | 主模型七项自查后，把全文外发第二模型独立复核 | 仅文档与 MCP 调用说明；MCP 依赖 Claude Code 平台绑定 | — | 否 | —（删除） | 用户决策：彻底删除；质检收敛单通道（七项结构化自查 + 标点门禁） |
| H-07 | 去 AI 味完整规则库（24 类痕迹 + 替换表） | references/de-ai.md:5-13 外链至外部 write skill 的 references/write-zh.md（实测 49KB/722 行；de-ai.md:10 硬编码用户绝对路径） | 每次写正文/润色全量加载外部 24 类 AI 痕迹规则库 | 速查 7 条（de-ai.md 本体）+ 外部全量库（换机即失效） | 结构化 RULE 入仓 + L1/L2 检索 + 绝对路径改发现机制 | 是 | M6（写作层规则检索）+ M8（跨平台发现机制） | — |
| H-08 | 标点门禁检查器（write skill 副本） | scripts/check_punctuation.py:1-3 自述「adapted from write skill」；SKILL.md:48 声明「本 skill 已含 write 的副本」 | 字符级标点/空格/破折号检查 + --fix + exit 0/1/2 | 仍可用（426 行，质量最高脚本）；与 write skill 上游双份维护、漂移不可考 | 4 条中文误报规则修复、--max-findings、JSON 输出、ko 改 exit 2 | 是 | M6（改造为唯一权威实现 + legacy 薄封装） | — |
| H-09 | 并行审校 subagent（可选增强） | references/writing-companion.md:23-25（「环境支持时 spawn 只读审校 subagent 并行质检」） | 文档描述的并行质检增强，默认不依赖 | 未实现（仅文档描述；subagent 机制为平台专属） | 平台 Adapter 抽象 + Task Engine 支持 | 否（占位） | M8+（有 Adapter 后再评估） | — |
| H-10 | 五角度选题框架 + 三步法 | references/topic-selection.md 全文（成长/选择/责任/关系/家国情怀五角度 + 三步法 + 质量自检 4 问） | 素材→选题卡的转化方法论 | 仍可用；选题卡输出为自由文本，模板与 prompt 字段 drift（标题 3 vs 3-5 槽） | TopicCard 结构化（angles 数组/titles 数组带 type 枚举） | 部分 | M5（TopicCard 契约已在本里程碑随 topic.schema.json 冻结） | — |
| H-11 | 版本与许可声明 | SKILL.md frontmatter：version 0.1.0、license: MIT（仓库无 LICENSE 文件） | 声明版本与许可 | 版本号过时（V2 重构后更新） | LICENSE 文件落地、CHANGELOG | 是 | M9（version→2.0.0 + README/CHANGELOG/LICENSE） | — |
| H-12 | 数据落点约定（追加式 JSONL「永不整体重写」） | SKILL.md:52-57 + case_lib.py 追加写实现 | 案例库 JSONL 追加式、风格库/画像覆写式 | 仍是 V1 生产行为（数据三文件空壳） | 原子写、幂等 upsert、DB 索引层 | 是 | M2（Artifact/原子写）+ M5（写入路径改 Pydantic→knowledge→DB→JSONL 镜像） | — |

## 2. 结论

1. **可恢复的历史资产共 3 项知识数据**：三报种子（H-01，本里程碑完成迁移）、案例库 16 字段 schema（H-03，本里程碑完成契约拆分）、五角度框架（H-10，契约已冻结，M5 实现结构化）。
2. **4 项按用户决策处置**：新榜移除（H-04）、第二意见删除（H-06）、抖音登录门禁保留（H-05）、subagent 占位不恢复（H-09）。
3. **3 项外部依赖资产**：去AI规则库（H-07）、标点检查器（H-08）在 M6/M8 以「结构化入仓 + 发现机制」方式恢复能力，不复制历史实现。
4. 全部历史代码无一被「盲目恢复」；恢复的是**需求与知识数据**，实现走 V2 管线。
