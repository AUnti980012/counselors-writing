"""Artifact Registry + ArtifactStore（pack M2 STEP 2/3/8）。

Registry = 血缘/身份账本（data/registry/artifacts.jsonl，追加式 JSONL）：
- 每行一条 ArtifactRecord（契约校验后写入，行级原子追加）；
- 账本语义：同 artifact_id 可多行（状态流转追加新行），**最新行胜出**；
- 不是全文搜索库（pack STEP 2：Do not turn Registry into a full-text search database）。

ArtifactStore = 阶段产物落盘 + 注册：
- 原子写：tmp + fsync + os.replace（pack STEP 3）；
- 无效写不静默替换有效内容：先 Pydantic 校验，再写文件，最后追加账本；
- 幂等（pack STEP 8）：同 artifact_type + 同 content_hash + 同血缘且状态有效
  → 复用已有记录，不重复落盘；
- 路径穿越防护：path 由 store 从校验过的 artifact_id 构造（相对路径 + 无 ..）。

ID 生成规则：TYPE 段 = artifact_type 折叠非小写字母后截断 16 字符
（契约 ARTIFACT_ID_PATTERN 的 TYPE 段只允许 [a-z]+，raw_html → rawhtml）。
"""
from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from pydantic import ValidationError

from core import atomic
from core.hashing import content_hash
from core.paths import ARTIFACTS_DIR, REGISTRY_ARTIFACTS_PATH
from core.schema import ArtifactRecord

# 幂等复用仅限「内容有效」状态；failed/expired/invalid 不复用
_REUSABLE_STATUSES = ("created", "valid")

# 落盘文件名约束：单段文件名（无路径分隔符、无绝对路径/UNC），防路径穿越与目录逃逸
_FILENAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Registry:
    """追加式 artifact 账本。"""

    def __init__(self, path: Path = REGISTRY_ARTIFACTS_PATH):
        self.path = Path(path)

    def load(self) -> Tuple[List[ArtifactRecord], List[str]]:
        """读全部记录（最新行胜出前先全量返回）。坏行跳过并报告（读侧容错）。"""
        records: List[ArtifactRecord] = []
        bad: List[str] = []
        if not self.path.exists():
            return records, bad
        for lineno, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                records.append(ArtifactRecord.model_validate(data))
            except (json.JSONDecodeError, ValidationError) as exc:
                bad.append(f"第 {lineno} 行：{exc}")
        return records, bad

    def latest(self) -> Dict[str, ArtifactRecord]:
        """按 artifact_id 取最新行（账本同 id 多行时最新胜出）。"""
        records, _ = self.load()
        latest: Dict[str, ArtifactRecord] = {}
        for r in records:
            latest[r.artifact_id] = r
        return latest

    def append(self, record: ArtifactRecord) -> None:
        atomic.atomic_append_jsonl(self.path, record.model_dump(mode="json"))


class ArtifactStore:
    """阶段产物落盘 + 注册。路径均可注入（测试用临时目录）。

    index_sync：可选回调（如 repo.upsert_artifact），create/set_status 成功后
    同步 SQLite artifacts 投影表，保持投影与 registry latest-wins 一致。
    """

    def __init__(self, artifacts_dir: Path = ARTIFACTS_DIR,
                 registry: Optional[Registry] = None,
                 index_sync: Optional[Callable[[ArtifactRecord], None]] = None):
        self.artifacts_dir = Path(artifacts_dir)
        self.registry = registry if registry is not None else Registry()
        self.index_sync = index_sync

    # ---- 查询 ----

    def get(self, artifact_id: str) -> Optional[ArtifactRecord]:
        return self.registry.latest().get(artifact_id)

    def list_records(self, artifact_type: str = None, status: str = None) -> List[ArtifactRecord]:
        out = list(self.registry.latest().values())
        if artifact_type:
            out = [r for r in out if r.artifact_type == artifact_type]
        if status:
            out = [r for r in out if r.status == status]
        out.sort(key=lambda r: r.updated_at)
        return out

    def read(self, artifact_id: str, *, verify_hash: bool = False) -> bytes:
        """读产物字节；可选校验 content_hash（防磁盘损坏/被篡改）。"""
        record = self.get(artifact_id)
        if record is None:
            raise FileNotFoundError(f"artifact 不存在：{artifact_id}")
        # record.path 相对仓库 ROOT；实际落盘目录 = self.artifacts_dir（可注入），取文件名即可
        target = self.artifacts_dir / Path(record.path).name
        data = target.read_bytes()
        if verify_hash and content_hash(data) != record.content_hash:
            raise ValueError(f"artifact 内容哈希不匹配（注册 {record.content_hash[:12]}…）：{artifact_id}")
        return data

    # ---- 写入 ----

    @staticmethod
    def _type_segment(artifact_type: str) -> str:
        seg = "".join(ch for ch in artifact_type if ch.isalpha() and ch.isascii()).lower()
        return (seg or "artifact")[:16]

    @staticmethod
    def _file_ext(artifact_type: str, filename: str = None) -> str:
        if filename:
            ext = Path(filename).suffix
            if 1 <= len(ext) <= 10 and ext[1:].isalnum():
                return ext.lower()
        tail = artifact_type.rsplit("_", 1)[-1]
        if tail and tail.isalnum() and len(tail) <= 10:
            return "." + tail.lower()
        return ".bin"

    def create(self, artifact_type: str, content: str | bytes, *,
               filename: str = None,
               source_ids: List[str] = None,
               parent_ids: List[str] = None,
               retention: str = "temporary",
               summary: str = "",
               expires_at: Optional[datetime] = None,
               metadata: Dict[str, str] = None) -> Tuple[ArtifactRecord, bool]:
        """创建（或幂等复用）artifact。返回 (记录, 是否复用)。

        复用条件（全部满足才复用）：
        - 同 artifact_type + 同 content_hash + 同血缘（source_ids/parent_ids 集合）；
        - 状态 created/valid；
        - 落盘文件确实存在（文件丢失 = 不是 valid artifact，重新落盘）；
        - retention/summary/metadata 与请求一致（参数语义不被静默丢弃）；
        - expires_at：请求为 None（未指定）时不构成不复用理由——expires_at 是
          易变的保留时间戳（每次运行 now+72h 都不同），作为复用条件会永久
          破坏幂等（审查确认）；显式指定时仍严格一致。过期窗口刷新走
          refresh_expiry（复用后追加新账本行，latest-wins）。
        """
        data = content.encode("utf-8") if isinstance(content, str) else content
        digest = content_hash(data)
        source_ids = sorted(source_ids or [])
        parent_ids = sorted(parent_ids or [])
        metadata = metadata or {}

        for r in self.registry.latest().values():
            if (r.artifact_type == artifact_type and r.content_hash == digest
                    and r.status in _REUSABLE_STATUSES
                    and sorted(r.source_ids) == source_ids
                    and sorted(r.parent_ids) == parent_ids
                    and r.retention == retention and r.summary == summary
                    and r.metadata == metadata
                    and (expires_at is None or r.expires_at == expires_at)
                    and (self.artifacts_dir / Path(r.path).name).exists()):
                return r, True

        now = _utcnow()
        if filename is not None:
            if not _FILENAME_PATTERN.fullmatch(filename):
                raise ValueError(
                    f"filename {filename!r} 必须是单段文件名"
                    f"（^[A-Za-z0-9][A-Za-z0-9._-]{{0,63}}$），"
                    f"禁止路径分隔符/绝对路径/UNC（防路径穿越）")
            if any(r.path.endswith(f"/{filename}") for r in self.registry.latest().values()):
                raise ValueError(f"filename {filename!r} 已被其他 artifact 占用（同名会互相覆盖）")

        type_seg = self._type_segment(artifact_type)
        for _ in range(20):  # 8hex 碰撞重试（防同秒同随机）
            artifact_id = f"{type_seg}-{now:%Y%m%d}-{secrets.token_hex(4)}"
            if self.get(artifact_id) is None:
                break
        else:
            raise RuntimeError("无法生成唯一 artifact_id（随机碰撞）")

        filename = filename or f"{artifact_id}{self._file_ext(artifact_type)}"

        rel_path = f"data/artifacts/{filename}"
        record = ArtifactRecord(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            status="created",
            path=rel_path,
            content_hash=digest,
            source_ids=source_ids,
            parent_ids=parent_ids,
            retention=retention,
            summary=summary,
            expires_at=expires_at,
            metadata=metadata,
        )
        # 先校验（无效内容不得静默替换有效内容）→ 原子落盘 → 最后追加账本
        target = self.artifacts_dir / filename
        try:
            atomic.atomic_write_bytes(target, data)
            self.registry.append(record)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        if self.index_sync is not None:
            self.index_sync(record)
        return record, False

    def set_status(self, artifact_id: str, status: str) -> ArtifactRecord:
        """状态流转（created→valid/invalid/failed；expired 由 GC 标记）。

        追加新账本行前用 ArtifactRecord 重新校验——非法状态（契约外枚举值）
        绝不进入账本（无效写不得静默替换有效内容）。
        """
        record = self.get(artifact_id)
        if record is None:
            raise KeyError(f"artifact 不存在：{artifact_id}")
        updated = ArtifactRecord(**{**record.model_dump(), "status": status,
                                    "updated_at": _utcnow()})
        self.registry.append(updated)
        if self.index_sync is not None:
            self.index_sync(updated)
        return updated

    def refresh_expiry(self, artifact_id: str,
                       expires_at: Optional[datetime]) -> ArtifactRecord:
        """续期/登记过期时间（M3）：追加一行新账本（latest-wins）。

        与 create 的 expires_at 参数分离的原因：expires_at 每次运行都不同
        （now+72h 微秒级变化），若作为幂等复用条件则永不复用（审查确认）；
        create 不带 expires_at 才能复用，复用后再用本方法刷新保留窗口。
        """
        record = self.get(artifact_id)
        if record is None:
            raise KeyError(f"artifact 不存在：{artifact_id}")
        updated = ArtifactRecord(**{**record.model_dump(), "expires_at": expires_at,
                                    "updated_at": _utcnow()})
        self.registry.append(updated)
        if self.index_sync is not None:
            self.index_sync(updated)
        return updated
