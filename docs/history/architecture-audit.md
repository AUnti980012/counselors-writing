# fudaoyuan-baokuan V1 当前架构（审计基线）

> 本文件对应 Milestone Prompt Pack v1.0 的 M0 文档 `architecture-audit.md`（原名 `current-architecture.md`，2026-09-30 随 pack 对齐改名）。
> 审计日期：2026年09月30日。审计方式：5 个并行只读探针（编排层/数据层脚本/抓取与 Web/数据与模板/跨平台总检）+ 1 个交叉核验员，仓库 22 个文件全覆盖；8 条 critical/high findings 经逐行复核全部 confirmed（零 refuted）。
> 本文件为 V2 重构的架构基线。版本：SKILL.md metadata `0.1.0`。非 git 仓库。

## 1. 定位与规模

辅导员公众号爆款文章生成 skill：把辅导员日常工作素材（一次谈心谈话、一场班会、一个学生事件）写成公众号推文，并用案例库/风格库/学校画像实现「越用越懂学校、越用越懂学生」。

| 项 | 值 |
|---|---|
| 位置 | `~/.claude/skills/fudaoyuan-baokuan/` |
| 规模 | 22 文件 / 71KB（SKILL.md 1 + references 8 + scripts 7 + assets 3 + data 3） |
| 版本 | 0.1.0；无 README / CHANGELOG / LICENSE 文件（frontmatter 声明 `license: MIT` 但无对应文件） |
| 触发方式 | 纯自然语言意图路由（无 slash command、无 hooks、无 commands/ 目录） |
| Python 依赖 | 全部标准库（argparse/json/os/sys/re/datetime/urllib/subprocess/time + `__future__` 注解；hashlib 尚未被使用） |

## 2. 目录结构与职责

```
fudaoyuan-baokuan/
├── SKILL.md                     # 编排层：意图路由表 + 7 步主工作流 + 3 铁律 + 外部依赖声明 + 数据落点声明
├── references/                  # 方法层（8 个阶段指令文档）
│   ├── style-library.md         #   爆款研究方法 + 三报种子风格条目（内嵌，L55-89）
│   ├── topic-selection.md       #   选题转化：五角度框架 + 三步法 prompt + 质量自检
│   ├── hot-trend.md             #   热点抓取方法 + 桥接 prompt + 抖音 CDP 实验项 + 降级链
│   ├── writing-companion.md     #   写作陪伴：四段式 + 逐段自查 + 两道门
│   ├── ideological-review.md    #   思政七项清单 + 千问第二意见外发指令 + 结论判定
│   ├── dissemination-review.md  #   传播复盘五维度 + 案例库 schema 文档 + CLI 文档 + 回写纪律
│   ├── de-ai.md                 #   去 AI 味高频速查 + 外链外部 write skill 完整规则库
│   └── school-profile.md        #   学校画像字段 schema + CLI + 使用纪律
├── scripts/                     # 执行层（7 个一次性同步 CLI，全标准库）
│   ├── fetch_hotlist.py         #   微博 ajax / tophub 正则 / xinbang 永久失败占位
│   ├── fetch_article.py         #   报媒正文三级回退（read → fetch-skill-main → 用户粘贴）
│   ├── fetch_douyin.py          #   抖音 CDP 实验项（输出整页 innerText）
│   ├── case_lib.py              #   案例库 JSONL add/search/list（仅 3 键校验）
│   ├── style_lib.py             #   风格库 index.json add/search（无 top 上限）
│   ├── profiles.py              #   学校画像 school.json get/set/dump（无 key 白名单）
│   └── check_punctuation.py     #   标点门禁（426 行，write skill 副本，质量最高）
├── assets/                      # 输出模板（3 个 Markdown 占位符模板）
│   ├── topic-card-template.md   #   选题卡（与三步法 prompt 存在字段 drift）
│   ├── article-template.md      #   正文四段式 + 标题三式 + 落款
│   └── review-report-template.md#   质检报告（双栏）+ 复盘报告（五维）
└── data/                        # 数据层（全部为空壳占位）
    ├── case_library/cases.jsonl #   0 字节（追加式，永不整体重写）
    ├── style_library/index.json #   15 字节 {"entries": []}
    └── profiles/school.json     #   171 字节（7 字段全空）
```

## 3. 五层架构

### 3.1 编排层（SKILL.md，57 行）

| 区块 | 位置 | 内容 |
|---|---|---|
| frontmatter | L1-9 | name/description（触发词：辅导员/爆款/仿写/选题/热点/思政/复盘等）、license MIT、compatibility（Python3；CDP Node 22+；qwen-verify MCP） |
| 意图路由表 | L15-26 | 6 意图 → 单 reference 映射；「写推文」走完整链路，其余单文件直读；判断不了先反问 |
| 主工作流 | L28-36 | 7 步：收集素材 → 选题转化 → 可选抓热点 → 陪伴创作 → 过两道门 → 交付 → 传播复盘回写 |
| 铁律 | L38-42 | ①学生隐私硬门槛 ②数据/案例必须可核实（拿不准标待核实）③每交付一次都尝试沉淀 |
| 依赖声明 | L44-50 | read / write / qwen-verify MCP / web-access-main 四个外部依赖 |
| 数据落点 | L52-57 | 案例库 JSONL（追加式永不整体重写）/ 风格库 index.json / 画像 school.json / 种子只读于 reference |

### 3.2 方法层（references/，8 文件）

| 文件 | 单一职责 | 关键互链 | 职责纯净度问题 |
|---|---|---|---|
| style-library.md | 爆款研究方法 | 被 SKILL.md 路由 | **内嵌 3 条种子风格数据（L55-89）**，方法+数据混装 |
| ideological-review.md | 思政审核规则 | writing-companion → 本文件 | 七项清单（L7-45）与外发指令（L57-67）**双重书写** |
| topic-selection.md | 选题方法 | 被 SKILL.md 路由 | 基本单一 |
| writing-companion.md | 写作流程 | → ideological-review / de-ai | 基本单一 |
| hot-trend.md | 热点方法 | 被 SKILL.md 路由 | **L46 硬编码用户绝对路径** |
| dissemination-review.md | 复盘方法 | → case_lib / style_lib | **四职责混装**：方法论 + 案例 schema 文档（L23-49，与 case_lib.py 双源）+ CLI 文档 + 回写纪律 |
| de-ai.md | 去 AI 速查 | → 外部 write-zh.md | **L10 硬编码用户绝对路径**；与 ideological-review AI 痕迹项重复 |
| school-profile.md | 画像方法 | → dissemination-review | schema 文档与 profiles.py 双源；updated_at 示例与脚本实际格式漂移 |

### 3.3 执行层（scripts/，7 个一次性 CLI）

| 脚本 | 行数 | 子命令 | 数据契约 | 关键缺陷 |
|---|---|---|---|---|
| check_punctuation.py | 426 | `--lang zh/en/ja/auto`、`--fix`、stdin | findings 文本行 `source:line:col [kind] snippet -> suggestion`；exit 0/1/2 | 中文误报 4 规则（数字+CJK 强制空格、em-dash 一刀切、全角空格、ko 静默放行）；findings 无上限 |
| case_lib.py | 131 | add（stdin JSON）/ search / list | cases.jsonl 追加；仅校验 3 必填键；5 字段摘要 | `--top 0` 全量 bug（rows[-0:]）；坏行静默 continue；无 id 去重；整行 blob 子串匹配命中字段名 |
| style_lib.py | 98 | add / search | index.json `{"entries":[...]}` 覆写式保存 | search 无 `--top`，kw 空即整库 dump；非原子写；缺 entries 键 KeyError |
| profiles.py | 98 | get / set / dump | school.json 覆写式保存 | set 任意 key 无白名单；非原子写 |
| fetch_hotlist.py | 144 | weibo / tophub / xinbang | stdout JSON `[{rank,title,heat,url}]` | tophub 正则易失效、url 恒空、heat 类型跨源不一致；无重试；xinbang 永久失败占位 |
| fetch_article.py | 76 | 单参数 URL | stdout Markdown；backend 标记走 stderr | 无 URL 校验透传子进程；无缓存；依赖兄弟 skill 相对路径 |
| fetch_douyin.py | 82 | douyin.com/hot / 搜索页 | stdout 整页 innerText（与文档声称不符） | 无结构化提取、无截断；依赖 CDP 代理 + 登录态 |

共同点：全部一次性同步命令（无 task_id/状态/恢复）；全部带 Windows UTF-8 reconfigure（`errors=replace`）；路径均基于 `__file__` 相对定位（跨平台安全）；无缓存、无哈希、无锁、无备份。

### 3.4 数据层（data/ + assets/）

| 文件 | 大小 | 结构 | 状态 |
|---|---|---|---|
| data/case_library/cases.jsonl | 0 字节 | JSONL 追加式（schema 文档在 dissemination-review.md:25-49，16 字段含嵌套 effect/retro） | 空壳；add→search 闭环从未被真实数据验证 |
| data/style_library/index.json | 15 字节 | `{"entries": []}`（条目结构 {source,type,text,added_at}） | 空壳；与种子风格库（reference 内嵌）两套机制检索割裂 |
| data/profiles/school.json | 171 字节 | 7 字段（school_name/school_type/student_profile/common_topics/sensitive_points/title_style_preference/updated_at）全空 | 空壳占位 |
| assets/*.md | 3 文件 / 3.1KB | Markdown 占位符模板 | 选题卡模板与三步法 prompt 字段 drift（标题 3 vs 3-5）；报告模板双栏含第二意见列 |

### 3.5 外部依赖层

| 外部依赖 | 用途 | 依赖方式 | 风险 |
|---|---|---|---|
| read skill（fetch_local.py） | 报媒正文本地提取（readability-lxml） | fetch_article.py 三级回退第 1 级，`BASE/../..` 相对路径 | 兄弟 skill 缺失时静默退化 |
| fetch-skill-main（fetch.py） | 报媒正文第三方链回退 | 三级回退第 2 级 | 同上 |
| write skill（write-zh.md） | 去 AI 味完整规则库（24 类痕迹+替换表） | **de-ai.md:10 硬编码用户绝对路径** | 换机失效；实测 49KB/722 行 |
| web-access-main（CDP localhost:3456） | 抖音动态页 | **hot-trend.md:46 硬编码用户绝对路径** + fetch_douyin.py | 需 Node 22+ + 用户登录态浏览器 |
| qwen-verify（MCP） | 思政第二意见 | `mcp__qwen-verify__verify(content=全文,...)`，SKILL.md:34 门禁 | Claude Code MCP 平台绑定；**已由用户决策删除** |

## 4. 主工作流（SKILL.md:28-36，7 步）

| 步 | 动作 | 加载文件 | 产出 | 门禁 |
|---|---|---|---|---|
| 1 | 收集素材（不足先问） | topic-selection.md | 素材 | — |
| 2 | 选题转化 | topic-selection.md + topic-card-template.md | 选题卡 → 用户确认角度 | 质量自检 4 问 |
| 3 | 可选抓热点 | hot-trend.md → fetch_hotlist.py（weibo/tophub） | 热点×素材桥接 | 降级链（换源→放弃）、不强蹭 |
| 4 | 陪伴创作 | writing-companion.md + article-template.md + ideological-review.md（逐段对照）+ de-ai.md | 四段式正文（逐段写+逐段自查） | 段落级：政治方向/隐私/标签化/AI 痕迹 |
| 5 | 过两道门 | check_punctuation.py --lang zh + qwen-verify MCP（content=全文） | 门禁结果 | 标点 exit 0 + 第二意见 |
| 6 | 交付 | review-report-template.md（双栏） | 正文 + 质检报告 | — |
| 7 | 传播复盘 | dissemination-review.md → case_lib.py add + style_lib.py add | 案例/风格沉淀回写 | 脱敏纪律、只沉淀可复用经验 |

## 5. 数据流

```
用户意图（自然语言）
   ↓ 静态路由表（SKILL.md:15-26）
单 reference 直读（5 个意图）  或   完整链路（写推文）
                                   ↓
        素材 → 选题卡(md) → [热点 JSON 内联] → 逐段正文(md)
           → 标点门禁(py) → 第二意见(MCP,全文) → 质检报告(md)
           → 交付 → 复盘 prompt(内联 case JSON) → case_lib/style_lib 追加写
                                   ↓
                 cases.jsonl / index.json / school.json（三空壳）
```

数据流特点：阶段间传递全靠 **LLM 上下文内联全文**（选题卡内联进风格检索 prompt、case JSON 内联进复盘 prompt、正文全文发给第二模型）；唯一落盘的持久点在三库文件；无任何中间产物落盘。

## 6. 关键机制与已知缺陷速览

- **意图路由**：静态 Markdown 表，关键词→单 reference；覆盖不足时反问兜底（可用，无逻辑风险）。
- **两道门**：标点（Python 字符级，硬门禁）+ 第二意见（MCP，硬依赖，有降级但丢失独立复核）——**用户已决策删除第二意见**。
- **回写纪律**：复盘后回写案例库+风格库；source_material 必须脱敏（仅文档纪律，脚本零校验）。
- **追加式存储**：cases.jsonl「永不整体重写」——数据只增不减，无 update/delete/GC。
- **已知缺陷清单**：详见 `docs/history/architecture-gaps.md`（23 组 gap，G-01~G-23）与 `docs/history/token-boundary-audit.md`（Top 10 热点）；8 条 critical/high findings 证据见 `docs/history/migration-plan.md` 附录。

## 7. 组件职责总表

| 组件 | 职责 | 数据职责（V2 视角） | 边界问题 |
|---|---|---|---|
| SKILL.md | 路由+编排+纪律 | 无 | 引用本仓库不存在的 scripts/fetch.sh（L46，实属 read skill） |
| references/ 8 文件 | 阶段方法 | STYLE 数据内嵌（style-library）、schema 文档副本（dissemination-review/school-profile） | 双源漂移风险 |
| scripts/ 7 文件 | 确定性执行 | 无 | 无状态、无缓存、无校验层 |
| data/ 3 文件 | 持久化 | CASE/STYLE/PROFILE 空壳 | 一文件多职责（案例 16 字段混装四类） |
| assets/ 3 文件 | 输出形状 | 无 | 非结构化（无 JSON 契约） |
