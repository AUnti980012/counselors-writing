"""备份与恢复（pack M7 STEP 1-3 / migration-plan 步骤 18）。

原则（pack M7 STEP 1-2）：
- 不在每次启动时全量备份；只在「重大写操作前」触发；
- 版本化快照（文件名含时间戳），保留 5-10 个最近快照，绝不覆盖唯一已知好备份；
- 恢复前先 integrity_check 备份文件 + 现库安全副本（防二次损坏）。

SQLite 索引可随时从 canonical 内容重建（pack STEP 3 / repo.rebuild_index），
故备份对象是索引库（data/index.db）；canonical knowledge 文件是内容权威，
由用户自行纳入更外层的文件备份（本模块不做整仓库打包）。

触发点 5 类（pack STEP 1）：schema_migration / major_write / bulk_delete /
bulk_transform / manual。
"""
from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.paths import BACKUPS_DIR, INDEX_DB_PATH

DEFAULT_KEEP = 10       # 保留最近快照数（pack STEP 2 建议 5-10）
MAX_KEEP = 10           # 上钳（pack 建议 5-10，超量保留无意义且占盘）
MIN_KEEP = 1            # 绝不删到 0 个（唯一已知好备份保护）

TRIGGER_REASONS = ("schema_migration", "major_write", "bulk_delete",
                   "bulk_transform", "manual")

# backup_id 格式：bak-YYYYMMDD-HHMMSS-8hex（restore 前校验，防路径穿越注入 ../）
_BACKUP_ID_PATTERN = re.compile(r"^bak-\d{8}-\d{6}-[0-9a-f]{8}$")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _now_stamp() -> str:
    return _utcnow().strftime("%Y%m%d-%H%M%S")


def _backup_id() -> str:
    return f"bak-{_now_stamp()}-{secrets.token_hex(4)}"


def _integrity_ok(db_path: Path) -> bool:
    """对给定 db 文件做 integrity_check（只读，不改动）。"""
    try:
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("PRAGMA integrity_check").fetchall()
        conn.close()
        return len(rows) == 1 and rows[0][0] == "ok"
    except sqlite3.Error:
        return False


class BackupError(RuntimeError):
    """备份/恢复失败（不静默）。"""


def create_backup(conn: sqlite3.Connection, reason: str, *,
                  backups_dir: Path = BACKUPS_DIR,
                  keep: int = DEFAULT_KEEP) -> Dict[str, Any]:
    """在线快照 conn 指向的索引库到 data/backups/kb-<id>.db，并登记 backups 表。

    版本化保留：快照成功后删除超出 keep 的旧快照（按文件名时间戳排序），
    同步删除 backups 表旧行（表与文件一致）；至少保留 MIN_KEEP 个，上钳
    MAX_KEEP（pack 建议 5-10），绝不覆盖唯一已知好备份。
    """
    if reason not in TRIGGER_REASONS:
        raise ValueError(f"未知备份触发点：{reason!r}（可用：{TRIGGER_REASONS}）")
    keep = max(MIN_KEEP, min(int(keep), MAX_KEEP))
    backups_dir = Path(backups_dir)
    backups_dir.mkdir(parents=True, exist_ok=True)
    backup_id = _backup_id()
    target = backups_dir / f"kb-{backup_id}.db"

    dest = sqlite3.connect(str(target))
    try:
        with dest:
            conn.backup(dest)
    except sqlite3.Error as exc:
        dest.close()
        target.unlink(missing_ok=True)
        raise BackupError(f"在线快照失败：{exc}") from exc
    dest.close()

    if not _integrity_ok(target):
        target.unlink(missing_ok=True)
        raise BackupError("快照后 integrity_check 未通过，已删除坏快照")

    db_bytes = target.stat().st_size
    now = _utcnow().isoformat()
    try:
        with conn:
            conn.execute(
                "INSERT INTO backups(backup_id, path, reason, db_bytes, integrity_ok, "
                "created_at) VALUES (?,?,?,?,?,?)",
                (backup_id, str(target), reason, db_bytes, 1, now))
    except sqlite3.Error as exc:
        raise BackupError(f"登记 backups 表失败：{exc}") from exc

    pruned = _prune_old(backups_dir, keep)
    if pruned:  # 表与文件一致：删除超出保留窗口的旧登记行
        with conn:
            conn.executemany("DELETE FROM backups WHERE backup_id=?", [(i,) for i in pruned])
    return {"backup_id": backup_id, "path": str(target), "reason": reason,
            "db_bytes": db_bytes, "integrity_ok": True, "pruned": pruned}


def _prune_old(backups_dir: Path, keep: int) -> List[str]:
    """删除超出 keep 的最旧快照（保留至少 MIN_KEEP 个）。返回被删 backup_id。"""
    keep = max(MIN_KEEP, keep)
    snaps = sorted(backups_dir.glob("kb-bak-*.db"), key=lambda p: p.name)
    removed: List[str] = []
    while len(snaps) > keep:
        oldest = snaps.pop(0)
        backup_id = oldest.name[len("kb-"):-len(".db")]
        oldest.unlink(missing_ok=True)
        removed.append(backup_id)
    return removed


def list_backups(conn: sqlite3.Connection, limit: int = 50) -> List[Dict[str, Any]]:
    try:
        rows = conn.execute(
            "SELECT * FROM backups ORDER BY created_at DESC LIMIT ?", (max(1, limit),)).fetchall()
    except sqlite3.Error:
        return []  # backups 表不存在（未 init 或旧库）
    return [dict(r) for r in rows]


def _backup_path(backup_id: str, backups_dir: Path) -> Path:
    return Path(backups_dir) / f"kb-{backup_id}.db"


def _safety_path(target: Path) -> Path:
    """现库安全副本路径（含随机后缀，防同秒两次 restore 互相覆盖）。"""
    return target.parent / f"{target.name}.pre-restore-{_now_stamp()}-{secrets.token_hex(4)}.db"


def _snapshot_file(src: Path, dst: Path) -> None:
    """用 backup API 复制 SQLite 文件（正确处理 WAL，比文件 copy 可靠）。"""
    s = sqlite3.connect(str(src))
    d = sqlite3.connect(str(dst))
    try:
        with d:
            s.backup(d)
    finally:
        s.close()
        d.close()


def restore_backup(backup_id: str, *, conn: Optional[sqlite3.Connection] = None,
                   target_path: Path = INDEX_DB_PATH,
                   backups_dir: Path = BACKUPS_DIR) -> Dict[str, Any]:
    """用指定快照恢复索引库。

    顺序（pack STEP 3 + migration-plan 步骤 18 验证要点）：
    1. backup_id 格式校验（防路径穿越）+ integrity_check 备份文件（坏备份拒绝）；
    2. 现库安全副本（backup API 含 WAL，非文件 copy）；
    3. 备份文件内容灌入现库（Connection.backup 反向）；
    4. 恢复后 integrity_check；中途失败也校验现库并提示安全副本位置。
    """
    if not _BACKUP_ID_PATTERN.fullmatch(backup_id):
        raise BackupError(f"非法 backup_id：{backup_id!r}（格式 bak-YYYYMMDD-HHMMSS-8hex）")
    src = _backup_path(backup_id, backups_dir)
    if not src.exists():
        raise BackupError(f"备份文件不存在：{backup_id}")
    if not _integrity_ok(src):
        raise BackupError(f"备份文件 integrity_check 未通过，拒绝恢复：{backup_id}")

    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    safety = _safety_path(target)
    if target.exists():
        _snapshot_file(target, safety)  # backup API：含 WAL 页，非 shutil.copy2

    src_conn = sqlite3.connect(str(src))
    dest_conn = conn if conn is not None else sqlite3.connect(str(target))
    try:
        with dest_conn:
            src_conn.backup(dest_conn)
    except sqlite3.Error as exc:
        partial_ok = _integrity_ok(target)
        raise BackupError(
            f"恢复失败：{exc}；现库 integrity_check={'通过' if partial_ok else '未通过'}；"
            f"安全副本保留在 {safety}，请据此回退") from exc
    finally:
        src_conn.close()

    ok = _integrity_ok(target)
    if not ok:
        # 恢复后校验失败：安全副本仍在，提示回退
        raise BackupError(
            f"恢复后 integrity_check 未通过；安全副本保留在 {safety}，请据此回退")
    return {"backup_id": backup_id, "restored": True, "integrity_ok": ok,
            "safety_copy": str(safety)}


def backup_before(reason: str, conn: sqlite3.Connection) -> Dict[str, Any]:
    """便捷触发：重大写操作前调用（schema_migration/major_write/bulk_delete/
    bulk_transform）。失败抛 BackupError（调用方决定是否中止操作）。"""
    return create_backup(conn, reason)
