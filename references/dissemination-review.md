# 传播复盘 + 案例库

复盘的目标不是「事后总结」，而是把有效经验**沉淀回案例库**，让 skill 越用越懂学校、越用越懂学生。发布后或拿到反馈后走这里。

## 一、复盘五维度

1. **标题有效性**：是否制造好奇 / 情绪 / 利益点；有无数字 / 反差 / 悬念；标题与正文是否一致（防标题党）。
2. **开头留人**：前 3 句是否抛冲突 / 悬念 / 具象画面；是否「今天我想跟大家聊聊」式空转。
3. **段落共鸣**：哪个段落学生 / 同行反馈最多（转发 / 评论 / 私聊）；识别是「情绪共鸣」还是「利益共鸣」。
4. **价值升华自然度**：结尾是否从故事自然走向道理；有无突兀拔高、口号式结尾。
5. **传播数据**（如有）：阅读 / 转发 / 在看 / 收藏，拆「标题点击率」与「内容留存率」。

## 二、复盘 prompt 骨架（直接复制）

```text
基于下面这条案例记录 + 用户反馈，复盘：标题有没有效、开头留没留人、哪段最能共鸣、升华自不自然。
把「有效的经验」提炼成一条可复用的规则（句式 / 角度 / 结构），并给出应写入案例库和风格库的字段。

案例（结构化字段，勿整条 JSON 回灌——Token 纪律）：标题 / 核心问题 / 采用方法 / 结果
反馈：{辅导员反馈 / 传播数据}
```

## 三、案例库 schema（data/case_library/cases.jsonl，每条一行 JSON）

> **V2 数据边界（M1 已冻结契约，M2 导入时按此拆分）**：下面这条 16 字段 JSON 是 V1 遗留格式，混装了四类职责。V2 拆为多个实体（逐字段映射见 `docs/architecture/data-contract.md` §8）：
> - **案例知识**（id/source_material/tags 等）→ `data/schemas/case.schema.json`（CaseRecord；红线：不含 effect/retro/review_*；angles 评价走 topic 契约）
> - **正文产出** → `data/schemas/draft.schema.json`（title_used→title；style→style_id 引用）+ `topic.schema.json`（hook/value_landing/titles）+ `style.schema.json`（structure）
> - **审核结论**（review_result/review_issues）→ `data/schemas/audit.schema.json`
> - **传播复盘**（effect/retro）→ `data/schemas/effect.schema.json`
>
> 在 M2 导入完成前，以下 16 字段格式仍是 `case_lib.py` 的现行契约，继续可用。

```json
{
  "id": "2026-09-09-001",
  "created_at": "2026-09-09T10:00:00+08:00",
  "source_material": "一句话概括原始素材（已脱敏）",
  "angles": ["成长", "选择"],
  "title_used": "最终采用标题",
  "title_alt": ["备选1", "备选2"],
  "hot_topic": "蹭的热点（可空）",
  "style": "人民系 | 青年系 | 光明系 | 自定义",
  "hook": "开头钩子写法",
  "structure": "正文结构骨架",
  "value_sublimation": "价值升华落点",
  "review_result": "通过 | 改了几处",
  "review_issues": ["改了哪几类问题"],
  "effect": { "channel": "公众号", "read_count": null, "feedback": "", "user_rating": 4 },
  "retro": {
    "title_worked": true,
    "hook_worked": true,
    "resonant_paragraph": "最能共鸣的段落",
    "sublimation_natural": true
  },
  "tags": ["班会", "十年后", "成长"]
}
```

## 四、案例库操作命令（沉淀流程命令化）

```bash
# M4 起：从素材/复盘结论结构化提取知识（LLM 提取 → 校验 → 自纠正 ≤2 → knowledge/artifact）
python3 scripts/kb.py extract <document_id> --extractor case_facts --llm-cmd "<LLM_COMMAND>"
python3 scripts/kb.py extract <document_id> --extractor style_pattern --llm-cmd "<LLM_COMMAND>"
python3 scripts/kb.py extract <document_id> --extractor topic_signal --llm-cmd "<LLM_COMMAND>"
# 无 LLM CLI 时：--prompt-only 输出最小 prompt 由 Agent 编排，再 --result 回灌
python3 scripts/kb.py extract <document_id> --extractor case_facts --prompt-only
python3 scripts/kb.py extract <document_id> --extractor case_facts --result 案例.json

# M5 起：case/style 写入与检索由 kb.py 接管（legacy case_lib.py/style_lib.py 转薄封装）
python scripts/kb.py case add < 案例.json                    # stdin 读案例 JSON（V1 3 键或完整 CaseRecord）
python scripts/kb.py search case "班会" --top 10             # L1 检索（五字段紧凑）
python scripts/kb.py search case --get case-xxx --fields methods,transferable_patterns  # L2 字段投影
```

## 五、回写纪律

- 复盘是**条件触发的增量知识沉淀阶段，不是每次交付的必经步骤**：只有存在新的传播数据 / 用户明确反馈 / 新的有效写法 / 新的失败原因 / 明确发现的改进模式时，才把经验写回案例库和风格库（M4 起用 `kb.py extract` 从复盘素材结构化提取；M5 起 `kb.py case/style` 写入命令接管）。**没有增量就直接结束，不额外调用 LLM、不强制写回。**
- 案例库里的 `source_material` 必须已脱敏（不含姓名 / 学号 / 独特可定位事件组合）。
- 只沉淀「可复用」的经验，不沉淀一次性巧合。
