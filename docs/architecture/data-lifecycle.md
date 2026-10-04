# Data Lifecycle（数据生命周期：备份 / 恢复 / 保留 / GC）

> 对应 pack M7（Backup + Recovery + Retention + GC）与 migration-plan 步骤 17-19。
> 实现：`scripts/core/task.py`（16 态状态机）、`core/backup.py`（在线快照）、
> `core/gc.py`（引用感知 GC）；命令 `kb.py task / backup / gc`。
> 相关架构总览见 `storage-architecture.md`。

## 1. Retention Matrix（保留策略矩阵）

| 数据类 | 默认保留 | 永久选项 | 过期条件 | GC 对象 |
|---|---|---|---|---|
| cache/raw（抓取原始字节） | 30d（fetch 按条目覆盖 3d） | — | TTL 过期（updated_at + ttl） | 是（`kb.py gc` → cache purge） |
| cache/processed（清洗正文） | 30d | — | TTL 过期 | 是 |
| cache/extraction（LLM 提取） | 90d | — | TTL 过期 | 是 |
| cache/tasks（任务态中间产物） | 7d | — | TTL 过期 | 是 |
| artifact `temporary` | 7d（无引用） | — | expires_at 过期 或 创建 >7d | 是（引用感知） |
| artifact `permanent` | 永久 | 默认即永久 | 无（仅手动删除） | **否** |
| artifact `task_bound` | 随任务 | 由活跃 task 引用保护 | task 完结且无引用后按 temporary 处理 | 条件性 |
| `data/knowledge/*.json`（canonical 内容） | 永久 | 内容权威 | 无 | **永不自动 GC** |
| `data/registry/*.jsonl`（账本） | 永久 | 身份/血缘权威 | 无 | 否 |
| SQLite `index.db`（索引） | 永久 | 可重建 | 契约版本不匹配 → rebuild | 否（重建而非删除） |

关键原则（pack M7 STEP 4-5）：raw HTML 默认 24–72h 可回收；当「恢复需要 / 用户显式永久 /
任务未完成 / 被活跃任务引用」时延长保留。`data/knowledge/` 是内容权威，任何自动 GC 都不触碰。

## 2. Backup Policy（备份策略）

- **不在每次启动时全量备份**（pack STEP 1）。只在 5 类触发点前备份：
  `schema_migration`（契约/结构迁移）、`major_write`（重大写）、`bulk_delete`（批量删除）、
  `bulk_transform`（批量转换）、`manual`（用户主动）。
- **版本化快照**（STEP 2）：`data/backups/kb-bak-<ts>-<hex>.db`，文件名含时间戳；
  保留最近 10 个（`--keep` 可调），**绝不删到 0 个**（唯一已知好备份保护）。
- 备份对象是索引库 `data/index.db`（`sqlite3.Connection.backup` 在线快照，不阻塞读写）。
  canonical knowledge 文件是内容权威，由用户更外层文件备份覆盖，本模块不打包整仓库。
- 快照完成后立即 `PRAGMA integrity_check`，坏快照删除（不登记、不保留）。

## 3. Recovery Workflow（恢复流程）

```text
kb.py backup list                 # 查备份登记（backup_id / reason / 时间 / 完整性）
kb.py backup restore --id <bak-…> # 恢复指定快照
```

恢复三步（`core/backup.py restore_backup`）：
1. **校验备份文件**：`integrity_check` 失败 → 拒绝恢复（坏备份不进现库）；
2. **现库安全副本**：`index.db.pre-restore-<ts>.db`（恢复失败可据此回退）；
3. **灌入**：`src.backup(dest)` 把快照内容写回现库，恢复后再 `integrity_check`。

索引恢复之外：SQLite 索引随时可从 canonical 内容确定性重建
（`kb.py db rebuild`，见 storage-architecture.md §8）——索引损坏时「rebuild 优先、
restore 兜底」；两者都失败才需要更外层文件备份。

## 4. GC Rules（GC 规则）

`kb.py gc`（默认 `--dry-run`，`--apply` 才真删）回收三类候选：

1. **cache TTL 过期**（复用 `CacheManager.purge`：只删 `status=expired`，孤儿 `.bin` 只报不删）；
2. **artifact `expires_at` 过期** 且无引用；
3. **artifact `retention=temporary` 创建 >7d**（`--temporary-ttl-days` 可调）且无引用。

**引用感知**（pack STEP 6/8，硬规则）：删除前构建「存活引用集合」——
(a) 所有 artifact 血缘 `source_ids` / `parent_ids`；(b) 活跃 task 的
`input_refs` / `output_refs` / `context_refs[kind=artifact]`。命中任一引用
的过期 artifact 一律**保留**（列入 `protected`，绝不删仍被引用的 artifact）。
`retention=permanent` 永不候选。

**dry-run**（STEP 7）：每条候选输出 `path + reason + age_days + references +
predicted action`，不删文件。**部分失败可恢复**（STEP 9）：`--apply` 逐个删除，
单条失败记入 `errors` 不中止整轮。

## 5. Disaster Scenarios（灾难场景）

| 场景 | 症状 | 恢复路径 |
|---|---|---|
| 索引库损坏/写坏 | `integrity_check` 失败、查询报错 | `kb.py db rebuild`（从 canonical 重建）；仍坏 → `backup restore` |
| 契约版本不匹配 | `IndexVersionMismatch` | `kb.py db rebuild`（跳过版本闸门，重建后归位） |
| 误删/误改 canonical 文件 | knowledge 文件丢失或损坏 | 更外层文件备份；本仓库不自动修复（内容权威由用户备份） |
| 抓取 raw 缓存失效 | 网页无法重抓 | 若恢复需要，raw 默认 3d 保留期内的最近成功榜单/正文仍在 cache/raw |
| 任务中断 | task 停在中间态 | `kb.py task resume --id` 读恢复锚点（status + context_refs）；已完成工作靠缓存幂等跳过，非重放 |
| 任务永久失败 | status=FAILED，retry 达上限 | 人工修复输入后 `task retry --id`（回退到 error_stage 活跃态） |
| 磁盘空间不足 | GC 候选堆积 | `kb.py gc --dry-run` 先审，确认后 `--apply` |

## 6. 任务状态机（16 态，`kb.py task`）

```text
CREATED → ROUTING → ACCESSING → FETCHING → PROCESSING → EXTRACTING →
VALIDATING → INDEXING → ANALYZING → MAPPING → GENERATING → REVIEWING → COMPLETED
    └─→ FAILED / BLOCKED / CANCELLED（旁路）   FAILED ─retry─→ 回退 error_stage
```

- 非法迁移（后退 / 终态再迁）拒绝（`TaskStateError`）；活跃态只允许线性前进或直接完成；
- `task_events` 追加日志（只增不删）记录 created/transition/retry 全轨迹；
- 批任务 `task batch`：parent + children，**单败不崩**（单个 child 创建失败记录后继续）；
- retry 上限：`retry_attempt` 超过 `retry_max` 拒绝（默认 3）。
