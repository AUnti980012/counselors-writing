# 常见问题（Troubleshooting）

## 依赖 / 环境

| 现象 | 处理 |
|---|---|
| `bootstrap.py` exit 3 | 缺 `pydantic>=2`：`python -m pip install "pydantic>=2"` |
| `kb.py` 报 `依赖缺失` | 同上；`--llm-cmd` 命令不存在时也报 exit 3（dependency_failed） |
| 中文乱码 | 脚本已自动 reconfigure UTF-8；仍乱码时设 `PYTHONUTF8=1` |

## 三种 LLM 模式

| 现象 | 处理 |
|---|---|
| 报「需要 --llm-cmd / --result / --prompt-only 之一」 | 三个模式至少给一个；`--llm-cmd`、`--prompt-only`、`--result` 三者互斥，别同时给 |
| `--llm-cmd "<LLM_COMMAND>"` 在 Windows 执行失败 | `shlex` POSIX 规则 + 无 shell：反斜杠路径被吞、`.cmd` shim 不能直跑。用正斜杠路径或完整 `.exe`，或改写为 `cmd /c ...` |
| 想不接外部命令 | 用 `--prompt-only` 拿 prompt，自己（Agent）生成 JSON 后 `--result <file>` 回灌 |
| `--result` 报文件无法读取 | 检查文件路径存在且非空；这是 LLM 依赖失败（exit 3），不写任何数据 |

## 数据 / 索引

| 现象 | 处理 |
|---|---|
| 报「document / case / mapping 不存在」 | 先跑对应上游：`ingest` 产 document，`extract case_facts` 产 case，`mapping` 产 mapping |
| 索引与知识不一致 | `python scripts/kb.py db rebuild` 从 canonical 文件重建索引 |
| 契约漂移（exit 1） | `python scripts/kb.py schemas export` 重新导出（schema.py 是唯一真相源，`data/schemas/*.json` 禁止手改） |

## 审核 / 输出

| 现象 | 处理 |
|---|---|
| `audit` 报不通过 | 政治/隐私/事实任一 fail 即不过；改正文后重审 |
| `punctuation` exit 2 | 有标点 findings（中文数字+CJK 等）；ko locale 暂不支持也是 exit 2 |

## 其他

- **备份**：`python scripts/kb.py backup create --reason manual`；恢复 `backup restore`。
- **断点续跑**：`python scripts/kb.py task create --id <id> --type writing`，`task resume` 读锚点。
- **清理**：`python scripts/kb.py gc`（默认 dry-run，`--apply` 才删，knowledge/ 永不触碰）。
- **全量测试**：`python scripts/kb.py test`（退出码透传）。
