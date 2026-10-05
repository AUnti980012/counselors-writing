# 快速开始（5 分钟第一次使用）

从零到第一篇推文的最小路径。所有命令由 `python scripts/kb.py` 统一入口。

## 1. 安装 / 依赖

```bash
python scripts/bootstrap.py
# 输出 ready: True 即环境就绪；exit 3 表示缺依赖
```

唯一第三方依赖是 `pydantic>=2`。缺了装一下：

```bash
python -m pip install "pydantic>=2"
```

## 2. 初始化

```bash
python scripts/kb.py db init
# 幂等：落地目录结构 + SQLite/FTS5 索引 + 契约版本检查
```

## 3. 放入案例素材

```bash
# 从文件导入
python scripts/kb.py ingest --file 素材.txt
# 或从 stdin 粘贴
echo "一次谈心谈话的内容…" | python scripts/kb.py ingest --title "谈心谈话记录"
```

`ingest` 产出 `document_id`（后续 `extract` 的输入）。

## 4. 选题

```bash
# 从素材结构化提取选题信号 → TopicRecord
python scripts/kb.py extract <document_id> --extractor topic_signal --llm-cmd "<你的 LLM 命令>"
# 无 LLM CLI 时：先 --prompt-only 拿 prompt，用你的 Agent 生成 JSON 再 --result 回灌
python scripts/kb.py extract <document_id> --extractor topic_signal --prompt-only
python scripts/kb.py extract <document_id> --extractor topic_signal --result 选题.json

# 检索已有选题
python scripts/kb.py search topic <关键词>
```

## 5. 写作

```bash
# 1) 素材 → 案例知识
python scripts/kb.py extract <document_id> --extractor case_facts --llm-cmd "<你的 LLM 命令>"
# 2) 案例 → 学校画像映射
python scripts/kb.py mapping --case <case_id> --profile pro-school --llm-cmd "<你的 LLM 命令>"
# 3) 白名单上下文写作 → 草稿
python scripts/kb.py write --mapping <mapping_id> --llm-cmd "<你的 LLM 命令>"
```

## 6. 审核

```bash
# 标点门禁（确定性，零 LLM）
python scripts/kb.py punctuation --lang zh 正文.md
# 单通道七项思政自查
python scripts/kb.py audit --draft <draft_id> --llm-cmd "<你的 LLM 命令>"
```

## 7. 输出

```bash
python scripts/kb.py output render --draft <draft_id> --audit <audit_id>
# --audit 把审核 id 写进 final_output 血缘（audited output 应带）；加 --content 直接打印正文
```

---

- 命令与三模式（`--llm-cmd` / `--prompt-only` / `--result`）详见 `docs/integration/agent-integration.md`。
- 完整工作流与热点/复盘见 `docs/user/workflows.md`。
- 出问题看 `docs/user/troubleshooting.md`。
