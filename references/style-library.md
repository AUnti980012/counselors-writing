# 爆款研究：报媒话术 / 风格库

风格库 = 「报媒话术 / 句式 / 标题公式」的检索表，用于**仿写与润色**，不是死模板。核心能力：让一篇学生工作推文，长得像人民日报评论、光明日报评论、或中国青年报，而不失辅导员的真实感。

## 一、三报「风格指纹」速查

| 报纸 | 气质 | 典型手法 | 适用场景 |
|---|---|---|---|
| **人民日报**（人民系） | 宏大叙事、排比递进、政策话语锚点、结论式标题 | 短句排比、把个人故事挂到时代背景、结尾一句铿锵结论 | 家国情怀、重大节点、需要「大报气质」的选题 |
| **光明日报**（光明系） | 文化 / 理论气质、沉稳克制、讲道理多于讲故事 | 设问推进、层层说理、典故引用、克制而不煽情 | 讲道理型、涉及价值辨析的选题 |
| **中国青年报**（青年系） | 青年视角、故事化、接地气、情绪 + 金句 | 白描开头、对话还原、口语金句收尾 | 成长、选择、关系，贴近学生日常的选题 |

## 二、三件套沉淀法

每读一篇报媒，都提炼三样东西：

1. **话术**：高频词、固定搭配、政策话语锚点（例如人民系的「新征程」「赶考路」这类锚点词，青年系的「谁不是一边…一边…」这类句式）。
2. **句式**：开头句式 / 过渡句式 / 升华句式（抄真实例句 + 标注出处）。
3. **标题风格**：悬念式 / 反问式 / 对比式 / 金句式的公式化拆解。

沉淀结果写进两个地方：
- **只读种子** → 结构化种子库 `data/knowledge/styles/seed.json`（origin=seed，随 skill 交付固化；查看与校验见文末）。
- **实时补充** → `python scripts/kb.py style add --source 人民日报 --type 句式 --text "..."`（M5 起写入 V2 StyleRecord，origin=user）。

## 三、写作时实时补充

写作中若发现某句式好使，立即回写：

```bash
python scripts/kb.py style add --source 人民日报 --type 句式 --text "把个人的…融入时代的…"
python scripts/kb.py style add --source 中国青年报 --type 标题 --text "那个…的学生，后来…"
```

## 四、检索 prompt 骨架（直接复制，替换 {选题卡内容}）

```text
请从风格库中，为下面这个选题匹配最合适的一种报媒风格，并给出该风格的：
1. 3 个可套用的标题公式；
2. 2 个开头句式；
3. 1 个价值升华句式；
4. 3 个高频话术词（要真实存在于该报文风，不要生造）。

选题：{选题卡内容}
```

## 五、风格库检索命令

```bash
python scripts/kb.py search style "排比 升华"        # 按关键词查已沉淀条目（L1 紧凑，--top 硬上限 10）
python scripts/kb.py search style "标题" --get style-seed-rmrb   # L2 读完整风格字段
```

> 命令已统一到 `kb.py`（M5 起）；旧 `scripts/style_lib.py` 保留为薄封装（转调 kb.py）。

---

# 种子风格库（结构化数据，单一真相源）

> V2 起种子不再内嵌于本文件，而是迁移到结构化单一真相源：**`data/knowledge/styles/seed.json`**（schema：`data/schemas/style.schema.json`；条目 origin=seed）。
>
> 内容：三报各 1 条真实文章沉淀（人民日报《在大有可为的时代大有作为》2025-04-30 / 光明日报《构建以学生成长为中心的体制机制》/ 中国青年报《因室友高考排名低就想退学？醒醒吧！》2026-09-09），字段含 structure/tone/sentence_features/title_patterns/opening_patterns/ending_patterns/narrative_patterns/communication_features + 出处备注。
>
> 使用规则：
> 1. 需要种子内容时，读 `data/knowledge/styles/seed.json`（约 2KB，勿复制回本文件——禁止双源）。
> 2. 校验种子合法性：`python scripts/kb.py validate style --json - < …` 逐条，或 `python -m unittest tests.test_seed -k SeedTests -v`。
> 3. 优先「仿其神」而非「抄其句」；套用句式时必须替换具体内容，避免版权风险。
