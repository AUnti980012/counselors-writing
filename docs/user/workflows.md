# 典型工作流

三条主链，按需取用。每条链都是「素材 → 知识 → 产出」的确定性核心 + LLM 语义编排。

## 1. 从素材到推文（主链）

```
ingest（素材 → document）
  ↓ extract --extractor case_facts（→ Case）
  ↓ extract --extractor topic_signal（→ Topic 选题卡）
  ↓ search topic（检索已有选题，可选）
  ↓ mapping --case <id> --profile pro-school（→ Mapping）
  ↓ write --mapping <id>（→ Draft）
  ↓ punctuation + audit（标点门禁 + 七项思政自查）
  ↓ output render（→ 最终 Markdown）
  ↓ case add / style add（复盘沉淀，不阻塞交付）
```

职责边界（重要）：`analysis --case` 产出 `AnalysisRecord`（学生关心什么 / 核心冲突 / 角度匹配）；`extract --extractor topic_signal` 产出 `TopicRecord`（选题卡：五角度 + 标题三式 + 钩子 + 价值落点）；`search topic` 只**检索**已有选题，不生成新选题。

## 2. 抓热点 → 选题桥接

```bash
python scripts/kb.py hotlist weibo --top 20    # 微博热搜
python scripts/kb.py hotlist tophub --top 20   # 聚合多榜
```

找「热点 × 学生工作」真实接口，不强蹭。方法见 `references/hot-trend.md`。

## 3. 传播复盘 → 沉淀

发布后回到 `references/dissemination-review.md`：把有效经验用 `kb.py extract` / `kb.py case add` / `kb.py style add` 写回案例库与风格库，让 skill 越用越懂这所学校。

## 4. Agent 编排模式（跨平台）

Agent 不用 `--llm-cmd` 也可以驱动 Core：

```bash
python scripts/kb.py <command> ... --prompt-only   # A. 拿 prompt
# Agent 调用自身模型，产出 JSON 存文件
python scripts/kb.py <command> ... --result f.json # C. 回灌 → Python 校验/持久化
```

详见 `docs/integration/agent-integration.md`。
