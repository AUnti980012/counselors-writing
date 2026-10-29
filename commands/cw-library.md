---
description: 知识库操作：检索/复用/入库案例、风格、画像、选题
argument-hint: 检索词 或 入库内容
---

检索/复用/入库知识库（案例、风格、画像、来源、选题、历史成果）。

**输入**：$ARGUMENTS

**步骤**
1. 检索：`kb.py search case|style|topic <kw>`（L1 紧凑投影）；需要全文 `--get <id>`（L2 显式）
2. 入库：`kb.py case add`（stdin）/ `kb.py style add` / `kb.py profile set`（走 Pydantic 校验 + 幂等）

**输出**：L1 紧凑投影或 L2 完整字段；入库返回 case_id/style_id/profile_id

**铁律**：一般查询按需检索，不批量加载整库；入库必须走现有校验与持久化；不得把待核实素材当已验证证据入库（区分 原始素材/待核实/已验证证据/可复用案例）。
