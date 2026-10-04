# Token 审计基线（V1 初始值）

> 本文件对应 Milestone Prompt Pack v1.0 的 M0 文档 `token-boundary-audit.md`（原名 `token-audit-initial.md`，2026-09-30 随 pack 对齐改名）。
> 本文档是 V2 重构的 Token 消耗基线。V2 完成后重跑本审计，出对比终版（检查点 G）。
> 口径：中文约 1 字 ≈ 1 token 的量级估算（未用 tokenizer 实测）；标注「点算」的为逐字计数，「实测」的为文件字节测量。

## 1. 单文件 Token 明细

| 文件 | 规模 | 估算 token | 说明 |
|---|---|---|---|
| SKILL.md | 4.4KB | ~1.2K | 每次 skill 触发自动注入 frontmatter description（约 340 字） |
| references/style-library.md | 6.0KB | ~2.2K（其中种子段 ~1K，点算） | 含 3 条种子风格条目（L55-89；点算 951 字符，其中严格汉字 810） |
| references/ideological-review.md | 4.9KB | ~2.2K | 七项清单（L7-45，约 0.95K 字符）与外发指令（L57-67，约 0.47K 字符）内容同构双写 |
| references/topic-selection.md | 3.6KB | ~1.5K | |
| references/dissemination-review.md | 2.9KB | ~1.4K | 含案例 schema 文档副本 |
| references/hot-trend.md | 2.8KB | ~1.1K | |
| references/writing-companion.md | 2.3KB | ~0.9K | |
| references/de-ai.md | 1.9KB | ~0.7K | 仅本地速查部分 |
| references/school-profile.md | 1.6KB | ~0.6K | |
| assets/ 3 模板 | 3.1KB | ~1.5K | |
| **外部 write skill 的 write-zh.md** | **49KB / 722 行（实测）** | **~15-20K** | de-ai.md:5-13 指令「去读这份」「以它为准」要求每次写正文全量读取；审计初估 5-10K 偏保守，已修正 |

## 2. 会话级消耗模型（典型场景）

| 场景 | 消耗构成 | 估算 |
|---|---|---|
| 完整成文会话（素材→推文） | 5 reference 串联（topic-selection→hot-trend→writing-companion→ideological-review→dissemination-review）+ 3 模板 + 外部 write-zh.md + 逐段反复对照审核清单 | 12-15K + 外部 15-20K ≈ **27-35K token** |
| 思政两道门（单篇推文） | 主模型自查全文 + 第二模型全文（content=全文）+ 七项指令两遍 | 2k-8k × 2 + 指令 ≈ **4-16K token/篇**（⚠ 用户决策：第二意见删除，此热点归零） |
| 热点研究（单次） | 热榜 JSON top20 注入桥接 prompt | 0.5-2K |
| 爆款研究（单次） | style-library.md 全文（含种子）+ 选题卡内联进检索 prompt | ~3K |

## 3. Token 热点 Top 10

| # | 热点 | 位置 | 估算 | 触发场景 | 缓解 → V2 步骤 |
|---|---|---|---|---|---|
| 1 | 去AI规则库跨 skill 全量加载 | de-ai.md:5-13 → 外部 write-zh.md | **15-20K/次（实测 49KB）** | 每次写正文/润色 | 提取结构化 RULE 入仓 + L1/L2 检索 → 步骤 14/20 |
| 2 | 成文全链路方法文档顺序加载 | SKILL.md:28-36 | 12-15K/会话 | 完整链路 | 阶段惰性加载 + Writing 白名单 → 步骤 14 |
| 3 | 报媒全文 Markdown 直出 stdout | fetch_article.py:55,63 | 3k-15K/篇，每次重抓 | 爆款研究/仿写 | cache/raw 落盘 + 摘要注入 + 内容哈希 → 步骤 5-7 |
| 4 | 抖音整页 innerText | fetch_douyin.py:70-71 | 3k-20K/次 | 抖音热点（实验） | 结构化提取 [{title,topic,likes}] + 限长 → 步骤 7 |
| 5 | style_lib search 整库 dump | style_lib.py:56-72 | 200 条 × 150 字 ≈ 30-50K/次 | 风格检索（kw 为空即触发） | `--top` 硬上限 10 + 摘要投影 + FTS5 → 步骤 11 |
| 6 | case_lib --top 0 全量 bug + 摘要含长文本 | case_lib.py:81-99 | 500 条 × 摘要 ≈ 70K+/次 | 案例检索 | top 校验 [1,50] + L1 五字段紧凑化 → 步骤 11 |
| 7 | 边写边审清单 N+1 次重读 | writing-companion.md:14-21,27-29 | 每段 1-2K × 4-6 段 + 全文 1 次 | 写作会话 | Writing 白名单 + 段落级规则摘要 → 步骤 14 |
| 8 | 七项审核规则同文件双重书写 | ideological-review.md:7-45 vs 57-67 | 同会话约 1.4K 字符重复消费 | 写作+审核会话 | 清单为唯一规则源（第二意见删除后外发指令自然消失）→ 步骤 15 |
| 9 | 热榜 JSON 全量注入桥接 prompt | hot-trend.md:24-34 | 0.5-2K/次 | 热点×素材桥接 | Python 裁剪 top5-10 的 rank+title 再注入 → 步骤 12/13 |
| 10 | 标点 findings 逐行无上限 | check_punctuation.py:416-420 | 极端数百行（数千 token） | 标点门禁 | `--max-findings` + 汇总统计行 → 步骤 15 |

## 4. 数据增长曲线（外推）

| 数据 | 当前 | 单条体积推算 | 100 条 | 500 条 |
|---|---|---|---|---|
| cases.jsonl | 0 字节 | 按 16 字段 schema（含 source_material/hook/structure/value_sublimation/effect.feedback/retro 长文本）推算单条 0.6k-1.8K | 60K-180K token | 300K-900K token |
| style index.json | 15 字节 | 单条 {source,type,text,added_at}，text 约 150 字 | 15K | 75K |
| school.json | 171 字节 | 7 字段，增长空间有限 | — | — |

结论：当前空壳状态下热点未暴露；案例库 100 条时，任何「整库读取」行为即产生 6 万-18 万 token 的单次消耗——V2 两级检索是硬性需求而非优化。

## 5. 未实证项声明（审计盲区）

以下项目在本轮审计中未验证，V2 完成时（检查点 G）必须补证：

1. **运行时可达性**：微博 ajax 端点与 tophub 页面结构的当前可达性未实测（全部探针为静态分析）。
2. **check_punctuation 误报率**：4 条误报规则经代码复核属实，但未在真实中文推文上实测误报率。
3. **PII 扫描**：未执行正则级敏感信息扫描（姓名/学号/手机号/密钥模式）；references 与 scripts 中嵌入的示例文本未做 PII 检查。
4. **端到端闭环**：case_lib/style_lib 的 add→search 闭环从未被真实数据验证（cases.jsonl 为 0 字节）。
5. **CDP/MCP 环境可用性**：localhost:3456 CDP 代理、qwen-verify MCP 的当前可用性未实测（MCP 已决策删除，此项降级）。
6. **tokenizer 实测**：全部估算为「1 字≈1 token」量级；种子风格库点算 0.95-1.05K 汉字（探针间分歧 2.5 倍，以点算为准）。
7. **版本漂移**：check_punctuation.py 与 write skill 上游副本的漂移无法证实（非 git 仓库，无历史可比）。

## 6. 缓解方案 → 实现步骤/检查点映射

| 缓解方案 | 实现步骤 | 检查点 | 目标 |
|---|---|---|---|
| raw 只落盘不回流；hotlist --top 20；douyin 结构化截断 | 7 | A | 热点环节 <2K |
| extraction prompt = 相关 chunks L1 + schema 摘要；缓存零 LLM 二次 | 10 | B | 单次提取 <4K |
| L1 紧凑五字段 + L2 显式 --get；style --top 10；无整库 dump | 11 | C | 检索环节 <1K |
| analysis 输入白名单（素材 L2 + profile + seed 摘要） | 12 | D | 单次分析 <3K |
| context-for-write 上下文包 + deny 清单断言 | 14 | E | 写作上下文 <6K |
| 第二意见删除（全文外传归零）+ 标点 --max-findings | 15 | F | 思政环节较 V1 降 ≥50% |
| 全链路重审 + 本文件出对比终版 | 22 | G | 总预算较 V1 显著下降；热点 #1/#4 归零 |
