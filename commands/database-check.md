---
description: 打开数据库可视化系统，查看 counselors-writing 知识库档案阵列
argument-hint: 可选 RhineLabUI 目录
---

把 counselors-writing 知识库最新数据导出为 RhineLabUI 的档案阵列，并在浏览器打开可视化网页。

**输入**：默认使用仓库内的 `visualization/rhinelab`。指定其他副本时传入目录路径。

**步骤**
1. 第一次使用，运行 `npm ci --prefix visualization/rhinelab` 安装内置副本的依赖。
2. 运行 `python scripts/database_check.py`。指定其他兼容副本时加 `--rhinelab-dir <目录>`。

**输出**：浏览器打开 `http://127.0.0.1:5173`，展示案例 / 选题 / 来源 / 审核 / 风格五类档案。

**铁律**：只导出并打开可视化，不修改知识库任何数据；可视化数据以 `data/knowledge/` 的 canonical JSON 为唯一真相源，`content/archives.json` 是导出产物。
