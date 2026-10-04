# Hardening Report（加固审计报告）

> 对应 pack M8 STEP 1-2（Bug/Hardening Audit + Token Hardening）。
> 审计范围：`scripts/core/`（22 模块）+ `scripts/kb.py` + legacy 薄封装。
> 结论：**无阻塞性缺陷**；18 类检查逐项通过或有界，M1-M7 各里程碑对抗审查已覆盖主要风险。

## 1. 18 类检查结果

| # | 检查项 | 结果 | 说明 |
|---|---|---|---|
| 1 | silent failures | ✅ | 无静默吞错：失败一律结构化（FetchBlocked/BackendUnavailable/validation_failed/failure artifact）或抛异常 |
| 2 | swallowed exceptions | ✅ | `except Exception` 共 13 处，均为正当语义：原子写清理（atomic.py:33/artifact.py:213）、V1 降级（compat.py）、stdin reconfigure（encoding.py/punctuation.py）、GC/批任务单败不崩（gc.py:173/task.py:203，pack 要求） |
| 3 | broad catch blocks | ✅ | 见上；无 `except: pass` 静默丢弃 |
| 4 | missing input validation | ✅ | 全部入参经 Pydantic 契约（extra=forbid）；CLI 用法错误 exit 4 |
| 5 | path traversal risk | ✅ | artifact_id 白名单、filename 单段校验、backup_id 正则校验（M8）、resolve_sibling_skill 单段名校验、URL 校验 |
| 6 | unsafe writes | ✅ | 原子写（tmp+fsync+os.replace）统一走 core/atomic.py；JSONL 行级追加 |
| 7 | race conditions | ✅ | SQLite WAL + busy_timeout；原子写同目录 replace；单进程 CLI 无并发写 |
| 8 | inconsistent encodings | ✅ | core/encoding.py 集中 UTF-8 reconfigure；run_subprocess 强制 PYTHONUTF8 |
| 9 | non-deterministic outputs | ✅ | id 生成确定性（content-hash/维度种子）；时间戳 Python 托管带时区 |
| 10 | unbounded loops | ✅ | fetcher while True 有 MAX_RETRIES + max_bytes 上界；chunker 有硬上限 |
| 11 | infinite retries | ✅ | 抓取重试 ×3 退避；LLM 自纠正 ≤2；task retry ≤3 |
| 12 | huge stdout | ✅ | 正文不进 stdout（指针优先）；检索 L1 有界；gc/backup list 限流 |
| 13 | full-library loads | ✅ | 去 AI 规则库改发现机制指针（de-ai.md）；检索白名单 + 两级 L1/L2 |
| 14 | accidental context duplication | ✅ | prompt 单次构建；base_prompt 自纠正不累计 |
| 15 | schema drift | ✅ | `kb.py schemas export --check` 18 schema 零漂移 |
| 16 | version drift | ✅ | KB_VERSION 0.9.0 = SCHEMA_VERSION 1.0.0 对齐；DB_VERSION 3 独立 |
| 17 | duplicate data stores | ✅ | PERSISTENCE RULE：文件权威，SQLite=索引，registry=账本，JSONL=镜像（C-08 独立文件防回环） |
| 18 | unbounded cache / missing cleanup / stale indexes | ✅ | cache TTL 惰性过期 + GC 引用感知清理；rebuild 从 canonical 重建 |

## 2. M8 本次修复

| 项 | 位置 | 说明 |
|---|---|---|
| 跨平台 critical 残留 | `references/de-ai.md:10` | 用户专属绝对路径 → `resolve_sibling_skill("write")` 发现机制（grep 已无运行时用户专属绝对路径） |
| CDP 后端补全 | `core/compat.py` | 第 4 后端 `CdpBackend`（web-access-main，登录同意门禁，C-02 同款语义） |

## 3. 已知限制（非阻塞，逐条可回退）

1. **单通道审核无独立复核**（C-07 用户决策）：七项结构化 + 标点门禁 + verdict 硬规则兜底。
2. **GC 保守性**：expired 记录血缘仍保护下游，可能过度保留（宁可少删）。
3. **shlex 无 shell**：`--llm-cmd` 用 shlex（无 shell），Windows 反斜杠路径需正斜杠。
4. **CDP 实验项**：抖音强反爬，成功率不保证，单次失败即退（C-02）。
