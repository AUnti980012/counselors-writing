# Regression Report（回归报告）

> 对应 pack M8 STEP 6-8（历史能力回归矩阵 + legacy 薄封装检查 + 退役记录）。
> 结论：**15 项历史能力全覆盖，零静默回退**；9 处有意行为变更（C-01~C-09）全部记录在
> `docs/migration-plan.md` 第 2 节；legacy 脚本转薄封装（pack STEP 7），重复业务逻辑已消除。

## 1. 回归矩阵（15 项）

| # | 能力 | 旧行为（V1） | 现行为（V2） | 回退 | 有意变更 | 测试 |
|---|---|---|---|---|---|---|
| 1 | 热点抓取 | fetch_hotlist.py 微博/tophub/新榜 | `kb.py hotlist weibo\|tophub`（逐字段一致） | 无 | C-01 新榜移除、C-02 抖音结构化 | ✅ test_hotlist/test_cli |
| 2 | 爆款研究 | style-library.md 报媒文风 | `kb.py search style` L1/L2 + seed 种子 | 无 | 种子迁 seed.json | ✅ test_search |
| 3 | 媒体风格研究 | 同上（脚本查不到种子） | 同上（seed+user 都覆盖） | 无 | 双源检索割裂修复 | ✅ test_search |
| 4 | 用户提供回退 | 手动粘贴 | `kb.py ingest` / UserPasteBackend | 无 | 结构化管道 | ✅ test_compat/test_pipeline |
| 5 | 选题转化 | topic-selection.md 三步法 | `kb.py search topic` + `analysis` | 无 | 输出对齐 TopicCard | ✅ test_analysis |
| 6 | 写作 | writing-companion.md 逐段 | `kb.py write`（白名单上下文） | 无 | 白名单+deny+lineage | ✅ test_writer |
| 7 | 思政质检 | 七项 + 第二意见（双传） | `kb.py audit`（单通道） | 无 | C-07 第二意见删除 | ✅ test_audit |
| 8 | 隐私检查 | 七项之一 | audit privacy 项 + M4 PII 脱敏兜底 | 无 | 脱敏前置于注入 | ✅ test_audit/test_extract |
| 9 | 标点门禁 | check_punctuation.py 4 误报 | `kb.py punctuation`（C-09 修复） | 无 | C-03 ko exit 2、C-09 四规则 | ✅ test_punctuation |
| 10 | 传播复盘 | dissemination-review.md 整条 JSON 回灌 | extract effect + 沉淀命令化 | 无 | 去整条回灌（Token） | ✅ test_extract |
| 11 | 学校画像 | profiles.py set 任意 key | `kb.py profile`（7 键白名单） | 无 | C-06 白名单 + updated_at 托管 | ✅ test_profile |
| 12 | 案例库 | case_lib.py（--top 0 bug） | `kb.py search case` + `case add` | 无 | C-04 top 钳制、C-08 写入路径 | ✅ test_search/test_repo |
| 13 | 风格库 | style_lib.py 整库 dump | `kb.py search style`（--top 10） | 无 | C-05 硬上限 | ✅ test_search |
| 14 | 路由 | SKILL.md 路由表 | 保留 + V2 命令等价 | 无 | 命令统一 kb.py | ✅ test_cli |
| 15 | 模板 | assets 三模板 | 内容不动，兼作渲染模板 | 无 | 报告模板单栏（C-07） | ✅ test_output |

## 2. Legacy 薄封装状态（STEP 7）

| 旧脚本 | 状态 | 说明 |
|---|---|---|
| `scripts/case_lib.py` | 薄封装 | 逻辑迁 core/case_lib_cli.py，命令名保留 |
| `scripts/style_lib.py` | 薄封装 | 逻辑迁 core/style_lib_cli.py |
| `scripts/profiles.py` | 薄封装 | 逻辑迁 core/profile_cli.py |
| `scripts/check_punctuation.py` | 薄封装 | 透传 core/punctuation.py cli_main |
| `scripts/fetch_hotlist.py` / `fetch_article.py` | 薄封装（入 legacy/） | 透传 core/hotlist.py / compat.py |
| `scripts/fetch_douyin.py` | 保留（实验项） | 结构化改造 C-02，非薄封装 |

## 3. 退役记录（STEP 8，仅确认项）

| 退役 | 类别 | 记录 |
|---|---|---|
| xinbang 子命令 | 确认失效占位 | C-01，报错指引官方替代源/WebSearch |
| 思政第二意见 | 显式移除依赖 | C-07，用户决策删除 qwen-verify |
| fetch_douyin 整页输出 | 已改造 | C-02，结构化 [{title,topic,likes}] |

## 4. 测试基线

**447 测试全绿**（M7 441 + M8 6 个 CdpBackend）。端到端关键链路
（fetch→ingest→extract→write→audit→output→backup→gc→task）均有单测或回归锁覆盖；
legacy golden 测试保证薄封装改造前后输出 shape 一致。
