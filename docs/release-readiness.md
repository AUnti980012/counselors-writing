# Release Readiness（发布就绪判定）

> 对应 pack M9 STEP 8（A-H 发布判定格式）与 STEP 3（Token 审计终版，检查点 G）。
> 审计基线：2026-09-30（V1）；终版：2026-10-04（V2，M9）。

## A. Completed（已完成）

- M0-M9 十阶段全部完成：架构审计 → 契约冻结 → 持久化 → 抓取清洗分块 → LLM 提取 → 检索分析映射 → 写作审核输出 → 任务/备份/GC → 加固兼容回归 → 集成发布。
- `scripts/kb.py` 单一确定性入口（0.9.0），覆盖全链路子命令；`scripts/core/` 22 模块纯 stdlib + pydantic≥2。
- 数据契约 16 实体 + common（18 schema 零漂移）；事实/推断强制分离；lineage 全链路可追溯。
- 447 测试全绿；9 处有意变更（C-01~C-09）全部记录；15 项历史能力零回退。
- 跨平台：无用户专属绝对路径硬编码、无 Claude 专属依赖、Codex 可用。

## B. Partially completed（部分完成）

- 端到端「真实数据」闭环：管道各阶段单测/回归锁全覆盖，但 V1 数据层为空壳、真实 add→search→write→audit→output 全链尚未用真实案例跑通（`data/knowledge` 现仅 seed.json）。
- slash commands 9 条薄包装（可选项，未做，非核心）。

## C. Known limitations（已知限制）

1. 单通道审核无独立复核（C-07 用户决策）；报告不再有第二意见列。
2. GC 保守性：expired 记录血缘仍保护下游，可能过度保留。
3. `--llm-cmd` 用 shlex（无 shell），Windows 反斜杠路径需正斜杠。
4. CDP/抖音实验项：强反爬，成功率不保证，单次失败即退（C-02）。
5. draft/audit 无索引表（canonical-only），检索靠文件扫描；数据量大后再补。
6. task CLI 无 `--input-ref/--context-ref`（锚点仅 Python API 可设）。

## D. Intentional feature changes（有意变更，C-01~C-09）

见 `docs/migration-plan.md` 第 2 节：xinbang 移除、抖音结构化+登录门禁、标点 ko exit 2、case `--top 0` 钳制、style `--top` 上限、profile 白名单、思政第二意见删除、案例写入路径（文件权威+独立镜像）、标点 4 规则修复。

## E. Remaining technical debt（剩余技术债）

- draft/audit/effect 的检索索引（当前 canonical-only 文件扫描）。
- task 状态机与各管道模块的 `--task` 接线（M7 只建状态机，未逐模块挂 task_id 锚点）。
- 真实数据导入 + 端到端 smoke（见 B）。
- `docs/token-boundary-audit.md` 的 V1 基线数字为量级估算，未逐条实测（已声明）。

## F. Regression risks（回归风险，不声称零风险）

- 标点门禁 4 规则（C-03/C-09）改变旧 check_punctuation 可观察行为，依赖旧精确输出的调用方需注意。
- 思政第二意见删除（C-07）使质检单通道化，独立复核能力消失。
- legacy 脚本转薄封装：golden 测试保证输出 shape 一致，但未覆盖全部边角用法。

## G. Token-cost improvements（Token 成本改进，检查点 G 终版）

| 热点（V1 基线） | V1 量级 | V2 现状 | 缓解 |
|---|---|---|---|
| 去AI规则库全量加载 | ~1.5-2 万 token | 按需指针 | `resolve_sibling_skill("write")` + de-ai.md 只留速查 |
| 成文全链路 5 ref + 3 模板 | 12-15K | <6K（检查点 E） | 阶段惰性 + 写作白名单投影 |
| 报媒全文 stdout | 3k-15k/篇 | 0（不进上下文） | cache/raw 落盘 + 指针 |
| 抖音整页 innerText | 3k-20k | ≤限长截断 | C-02 结构化 [{title,topic,likes}] |
| style_lib 整库 dump | 线性增长 | ≤10 | C-05 `--top` 硬上限 |
| case 摘要含长文本 | 随库增长 | L1 五字段 | 紧凑投影 + `--top` 钳制（C-04） |
| 边写边审 N+1 重读 | 多次重读 | 1 次 audit | 单通道七项 + 标点门禁零 LLM |
| 七项清单双重书写 | 2 份 | 1 份 | schema_summary 单一源 |
| 热榜 JSON 桥接 prompt | 0.5k-2k | ≤top 裁剪 | rank+title 摘要 |
| 标点 findings 逐行 | 无上限 | ≤--max-findings | 汇总统计行 |

**结论**：V1 十大热点全部收敛到有界（检索 <1K、分析 <3K、提取 <4K、写作 <6K、思政单传 + 标点零 LLM）。检查点 A-F 已命中，G 终版对比如上；思政环节较 V1 双传降 ≥50%（C-07）。

## H. Recommended next engineering tasks（建议后续工程任务）

1. 真实数据导入 + 端到端 smoke（用一份真实班会案例跑通 add→search→analysis→mapping→write→audit→output）。
2. draft/audit/effect 检索索引（数据量增长后）。
3. task 状态机接线各管道（`--task` 锚点贯穿）。
4. slash commands 9 条薄包装（如团队用 slash 习惯）。
5. `docs/token-boundary-audit.md` 实测化（真实场景 token 计数替代估算）。
