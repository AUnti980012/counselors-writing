"""数据契约单一真相源：Pydantic 模型（M1 冻结）。

本文件是全部数据契约的唯一权威定义：
- data/schemas/*.json 由 `kb.py schemas export` 从本文件生成，禁止手工修改；
- 契约变更流程：改本文件 → scripts/tests 全绿 → 重新导出 → 检查 diff；
- LLM 不得动态定义 schema（RESPONSIBILITY BOUNDARY），LLM 输出必须符合本契约。

事实/推断强制分离（FACT / INFERENCE RULE）：
- documented_fact / source_claim 必须提供 evidence_ids（至少 1 条证据引用）；
- ai_inference / derived_pattern / recommendation 必须提供 basis（推断依据说明）。

数据边界红线（DATA BOUNDARY）：
- Source 不承载完整网页正文（raw 落 cache/raw）；
- Case 不混装传播效果/复盘字段（见 EffectRecord / AuditRecord / DraftRecord）；
- Style 不存储整篇文章（只存写作特征，例证摘录 ≤300 字符）。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Annotated, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "1.1.0"

# ---- 模式常量 ----
ID_PATTERN = r"^[a-z0-9][a-z0-9_-]{2,63}$"
ARTIFACT_ID_PATTERN = r"^[a-z]+-\d{8}-[0-9a-f]{8}$"
HASH_PATTERN = r"^[0-9a-f]{64}$"
SEMVER_PATTERN = r"^\d+\.\d+\.\d+$"
REL_PATH_PATTERN = r"^[A-Za-z0-9_./-]{1,255}$"

IdStr = Annotated[str, Field(pattern=ID_PATTERN)]
HashStr = Annotated[str, Field(pattern=HASH_PATTERN)]

# ---- 枚举 ----
TASK_STATUSES = [
    "CREATED", "ROUTING", "ACCESSING", "FETCHING", "PROCESSING",
    "EXTRACTING", "VALIDATING", "INDEXING", "ANALYZING", "MAPPING",
    "GENERATING", "REVIEWING", "COMPLETED", "FAILED", "BLOCKED", "CANCELLED",
]
FAILURE_CODES = [
    "success", "blocked", "access_restricted", "unavailable", "timeout",
    "rate_limited", "parse_failed", "extraction_failed", "validation_failed",
    "index_failed", "dependency_failed", "cancelled",
]
FACT_TYPES = ["documented_fact", "source_claim", "ai_inference", "derived_pattern", "recommendation"]

TASK_STATUS = Literal["CREATED", "ROUTING", "ACCESSING", "FETCHING", "PROCESSING", "EXTRACTING",
                      "VALIDATING", "INDEXING", "ANALYZING", "MAPPING", "GENERATING", "REVIEWING",
                      "COMPLETED", "FAILED", "BLOCKED", "CANCELLED"]
TASK_TYPE = Literal["research", "writing", "review", "retro", "extraction", "import", "maintenance", "generic"]
FAILURE_CODE = Literal["success", "blocked", "access_restricted", "unavailable", "timeout", "rate_limited",
                       "parse_failed", "extraction_failed", "validation_failed", "index_failed",
                       "dependency_failed", "cancelled"]
FACT_TYPE = Literal["documented_fact", "source_claim", "ai_inference", "derived_pattern", "recommendation"]
ARTIFACT_STATUS = Literal["created", "valid", "invalid", "failed", "expired"]
RETENTION = Literal["temporary", "permanent", "task_bound"]
LANGUAGE = Literal["zh", "en", "ja", "auto", "unknown"]
RETRIEVAL_METHOD = Literal["api", "http", "browser", "user_provided", "sibling_skill", "unknown"]
SOURCE_STATUS = Literal["success", "blocked", "access_restricted", "unavailable", "timeout",
                        "rate_limited", "parse_failed"]
EVIDENCE_KIND = Literal["quote", "paraphrase", "data_point", "link", "screenshot_desc"]
EXTRACTOR_KIND = Literal["case_facts", "style_pattern", "topic_signal", "hot_interface", "media_style"]
EXTRACTION_STATUS = Literal["success", "extraction_failed", "validation_failed", "cancelled"]
MODEL_MODE = Literal["economy", "standard", "deep"]
VERDICT = Literal["pass", "warn", "fail"]
RISK_LEVEL = Literal["low", "medium", "high"]
DRAFT_STATUS = Literal["draft", "reviewed", "final"]
TOPIC_STATUS = Literal["draft", "confirmed", "used", "abandoned"]
SCHOOL_TYPE = Literal["university", "college", "vocational", "high_school", "other"]
STYLE_ORIGIN = Literal["seed", "user"]
AUDIT_CHECK = Literal["political", "factual", "value", "labeling", "ai_trace", "privacy",
                      "copyright", "punctuation", "format"]
TITLE_TYPE = Literal["suspense", "question", "story"]
REF_KIND = Literal["task", "artifact", "source", "document", "chunk", "case", "style", "topic",
                   "analysis", "mapping", "profile", "audit", "effect", "draft", "evidence"]
REF_KINDS = list(REF_KIND.__args__)  # 供 validator 使用


def _utcnow() -> datetime:
    """时间戳由 Python 托管（LLM 可省略，写入侧自动补齐）。"""
    return datetime.now(timezone.utc)


class BaseRecord(BaseModel):
    """所有实体基类：未知键一律拒绝（extra=forbid）+ 强制 schema_version。"""

    model_config = ConfigDict(extra="forbid")
    schema_version: str = Field(default=SCHEMA_VERSION, pattern=SEMVER_PATTERN,
                                description="契约版本；变更契约时按语义化版本递增")


class StampedRecord(BaseRecord):
    """带时间戳的实体基类：ISO8601 + 必须带时区。"""

    created_at: datetime = Field(default_factory=_utcnow, description="创建时间（Python 托管；若显式给出必须带时区，如 +08:00）")
    updated_at: datetime = Field(default_factory=_utcnow, description="更新时间（Python 托管；若显式给出必须带时区）")

    @field_validator("created_at", "updated_at")
    @classmethod
    def _aware_tz(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("时间戳必须带时区（ISO8601，如 2026-09-30T10:00:00+08:00）")
        return v


# ---- 共享定义（导出到 common.json 的 $defs） ----

class RefPair(BaseModel):
    """通用引用：kind + ref_id，用于 task 的 input/output refs 等。"""

    model_config = ConfigDict(extra="forbid")
    kind: REF_KIND
    ref_id: IdStr


class EvidenceRef(BaseModel):
    """轻量证据引用：直接指回 artifact/document/chunk + 有界摘录（禁止整文复制）。"""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["artifact", "source", "document", "chunk"]
    ref_id: IdStr
    excerpt: str = Field(default="", max_length=300, description="证据摘录（≤300 字符）")
    note: str = Field(default="", max_length=200)


class FactClaim(BaseModel):
    """事实/推断统一载体。跨字段硬规则见 model_validator。"""

    model_config = ConfigDict(extra="forbid")
    statement: str = Field(min_length=1, max_length=1000, description="陈述内容")
    fact_type: FACT_TYPE = Field(description="documented_fact/source_claim 属事实；其余属 AI 产物")
    evidence_ids: List[IdStr] = Field(default_factory=list, max_length=20,
                                      description="证据记录 id 列表（事实必填，≥1）")
    basis: str = Field(default="", max_length=1000, description="推断依据说明（AI 产物必填）")

    @model_validator(mode="after")
    def _fact_inference_rule(self) -> "FactClaim":
        if self.fact_type in ("documented_fact", "source_claim") and not self.evidence_ids:
            raise ValueError(f"{self.fact_type} 必须提供 evidence_ids（至少 1 条证据引用）")
        if self.fact_type in ("ai_inference", "derived_pattern", "recommendation") and not self.basis:
            raise ValueError(f"{self.fact_type} 必须提供 basis（推断依据说明）")
        return self


class SourceBasis(BaseModel):
    """选题/分析的素材依据（只存引用与摘录，不存全文）。"""

    model_config = ConfigDict(extra="forbid")
    source_ids: List[IdStr] = Field(default_factory=list, max_length=50)
    case_ids: List[IdStr] = Field(default_factory=list, max_length=50)
    material_excerpt: str = Field(default="", max_length=500, description="素材摘录（已脱敏，≤500 字符）")


class RetryInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attempt: int = Field(default=0, ge=0, le=10, description="已重试次数")
    max_attempts: int = Field(default=3, ge=1, le=5, description="最大重试次数（有限重试）")
    last_error: str = Field(default="", max_length=500)


class ErrorInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: TASK_STATUS = Field(description="失败发生时所在的流水线阶段")
    code: FAILURE_CODE = Field(description="机器可读失败码")
    message: str = Field(default="", max_length=1000)


class TopicAngle(BaseModel):
    """五角度框架的单角度评价（FEAT-10：成长/选择/责任/关系/家国情怀）。"""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=50)
    score: float = Field(ge=0, le=5, description="0-5 分")
    reasoning: str = Field(default="", max_length=1000)
    core_conflict: str = Field(default="", max_length=500, description="核心冲突")
    student_concerns: List[str] = Field(default_factory=list, max_length=3, description="学生真正关心的点（≤3 条）")
    evidence_ids: List[IdStr] = Field(default_factory=list, max_length=10)

    @field_validator("student_concerns")
    @classmethod
    def _each_bounded(cls, v: List[str]) -> List[str]:
        if any(len(s) > 200 for s in v):
            raise ValueError("student_concerns 单条 ≤200 字符")
        return v


class TopicTitle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: TITLE_TYPE = Field(description="悬念式 / 反问式 / 故事式")
    text: str = Field(min_length=1, max_length=100)
    note: str = Field(default="", max_length=200)


class DraftSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    heading: str = Field(default="", max_length=200)
    content: str = Field(min_length=1, max_length=5000)


class DraftLineage(BaseModel):
    """写作内容血缘：每个主要事实性断言必须可追溯到以下引用之一。"""

    model_config = ConfigDict(extra="forbid")
    topic_id: Optional[IdStr] = None
    analysis_id: Optional[IdStr] = None
    mapping_id: Optional[IdStr] = None
    style_id: Optional[IdStr] = None
    profile_id: Optional[IdStr] = None
    case_ids: List[IdStr] = Field(default_factory=list, max_length=20)
    source_ids: List[IdStr] = Field(default_factory=list, max_length=50)
    evidence_ids: List[IdStr] = Field(default_factory=list, max_length=50)


class ProfileProvenance(BaseModel):
    """画像字段级溯源：谁在什么时候基于什么证据写下这个字段。"""

    model_config = ConfigDict(extra="forbid")
    field: Literal["school_name", "school_type", "student_profile", "common_topics",
                   "sensitive_points", "title_style_preference"]
    fact_type: FACT_TYPE
    evidence_ids: List[IdStr] = Field(default_factory=list, max_length=10)
    basis: str = Field(default="", max_length=1000)
    note: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def _fact_inference_rule(self) -> "ProfileProvenance":
        if self.fact_type in ("documented_fact", "source_claim") and not self.evidence_ids:
            raise ValueError(f"{self.fact_type} 必须提供 evidence_ids（至少 1 条证据引用）")
        if self.fact_type in ("ai_inference", "derived_pattern", "recommendation") and not self.basis:
            raise ValueError(f"{self.fact_type} 必须提供 basis（推断依据说明）")
        return self


# ---- 实体 1：Task ----

class TaskRecord(StampedRecord):
    """16 态任务状态机契约。任务状态由确定性引擎管理，LLM 不持有任务状态。

    状态迁移表在 M7（core/task.py）实现；本契约只约束枚举与可恢复性字段。
    """

    task_id: IdStr = Field(description="推荐前缀 task-")
    task_type: TASK_TYPE
    status: TASK_STATUS = "CREATED"
    progress: int = Field(default=0, ge=0, le=100, description="0-100")
    input_refs: List[RefPair] = Field(default_factory=list, max_length=50)
    output_refs: List[RefPair] = Field(default_factory=list, max_length=50)
    retry: Optional[RetryInfo] = None
    error: Optional[ErrorInfo] = None
    context_refs: Dict[str, List[str]] = Field(default_factory=dict, max_length=20,
                                               description="恢复锚点：kind → id 列表（resume 依据）")
    parent_task_id: Optional[IdStr] = None
    resumable: bool = Field(default=True, description="是否可恢复（中断后 resume 的依据）")

    @field_validator("context_refs")
    @classmethod
    def _context_bounded(cls, v: Dict[str, List[str]]) -> Dict[str, List[str]]:
        total = 0
        for key, ids in v.items():
            if key not in REF_KINDS:
                raise ValueError(f"context_refs 键 {key!r} 不是合法引用类型（{REF_KINDS}）")
            if len(ids) > 100:
                raise ValueError(f"context_refs[{key}] 最多 100 条 id")
            for i in ids:
                if not re.fullmatch(ID_PATTERN, i):
                    raise ValueError(f"context_refs[{key}] 含非法 id：{i!r}")
            total += len(ids)
        if total > 200:
            raise ValueError("context_refs 总 id 数 ≤200（防恢复锚点膨胀）")
        return v


# ---- 实体 2：Artifact ----

class ArtifactRecord(StampedRecord):
    """Artifact 契约：身份/血缘/保留策略。Artifact 是阶段间传递的指针载体。"""

    artifact_id: str = Field(pattern=ARTIFACT_ID_PATTERN,
                             description="格式 TYPE-yyyymmdd-8hex，如 raw-20260930-a1b2c3d4")
    artifact_type: str = Field(pattern=r"^[a-z][a-z0-9_]{1,31}$",
                               description="如 raw_html / processed_document / extraction / final_output")
    status: ARTIFACT_STATUS = "created"
    path: str = Field(pattern=REL_PATH_PATTERN, description="仓库内相对路径（禁止绝对路径）")
    content_hash: HashStr = Field(description="内容哈希（NFC + 空白折叠后 sha256，M2 实现）")
    source_ids: List[IdStr] = Field(default_factory=list, max_length=100)
    parent_ids: List[IdStr] = Field(default_factory=list, max_length=100, description="血缘：上游 artifact_id 列表")
    retention: RETENTION = "temporary"
    expires_at: Optional[datetime] = Field(default=None, description="过期时间（必须带时区）")
    summary: str = Field(default="", max_length=500, description="注入 LLM 上下文的摘要（唯一长文本字段）")
    metadata: Dict[str, str] = Field(default_factory=dict, max_length=20,
                                     description="键值元数据（键 ≤20、值 ≤500）")

    @field_validator("path")
    @classmethod
    def _no_traversal(cls, v: str) -> str:
        parts = v.replace("\\", "/").split("/")
        if any(p == ".." for p in parts):
            raise ValueError("path 禁止 .. 路径穿越")
        return v

    @field_validator("metadata")
    @classmethod
    def _meta_bounded(cls, v: Dict[str, str]) -> Dict[str, str]:
        if any(len(val) > 500 for val in v.values()):
            raise ValueError("metadata 单个值 ≤500 字符")
        return v

    @field_validator("expires_at")
    @classmethod
    def _expires_aware(cls, v: Optional[datetime]) -> Optional[datetime]:
        if v is not None and v.tzinfo is None:
            raise ValueError("expires_at 必须带时区")
        return v


# ---- 实体 3：Source ----

class SourceRecord(StampedRecord):
    """来源元数据契约。红线：不承载完整网页正文（raw 落 cache/raw，见 data-contract）。"""

    source_id: IdStr = Field(description="推荐前缀 src-")
    url: str = Field(min_length=8, max_length=2048, description="原始 URL（http/https）")
    canonical_url: Optional[str] = Field(default=None, max_length=2048, description="归一化 canonical URL（M2）")
    domain: str = Field(default="", max_length=253)
    title: str = Field(default="", max_length=500)
    author: str = Field(default="", max_length=200)
    publisher: str = Field(default="", max_length=200)
    published_at: Optional[datetime] = Field(default=None, description="发布时间（必须带时区）")
    retrieved_at: datetime = Field(default_factory=_utcnow)
    retrieval_method: RETRIEVAL_METHOD = "unknown"
    status: SOURCE_STATUS = "success"
    content_hash: Optional[HashStr] = None
    metadata: Dict[str, str] = Field(default_factory=dict, max_length=20)

    @field_validator("url", "canonical_url")
    @classmethod
    def _http_only(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not re.match(r"^https?://", v):
            raise ValueError("URL 必须以 http:// 或 https:// 开头")
        return v

    @field_validator("published_at")
    @classmethod
    def _aware(cls, v: Optional[datetime]) -> Optional[datetime]:
        if v is not None and v.tzinfo is None:
            raise ValueError("published_at 必须带时区")
        return v

    @field_validator("metadata")
    @classmethod
    def _meta_bounded(cls, v: Dict[str, str]) -> Dict[str, str]:
        if any(len(val) > 500 for val in v.values()):
            raise ValueError("metadata 单个值 ≤500 字符")
        return v


# ---- 实体 4/5：Document / Chunk ----

class DocSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    heading: str = Field(default="", max_length=200)
    level: int = Field(default=1, ge=1, le=6)
    char_start: int = Field(default=0, ge=0)
    char_end: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _range_ok(self) -> "DocSection":
        if self.char_end < self.char_start:
            raise ValueError("char_end 必须 ≥ char_start")
        return self


class DocumentRecord(StampedRecord):
    """清洗后的正文文档（结构元数据 + 分块引用；正文本体存 chunk/artifact）。"""

    document_id: IdStr = Field(description="推荐前缀 doc-")
    source_id: IdStr
    content_hash: HashStr
    language: LANGUAGE = "unknown"
    word_count: int = Field(default=0, ge=0)
    sections: List[DocSection] = Field(default_factory=list, max_length=200)
    chunk_ids: List[IdStr] = Field(default_factory=list, max_length=500)
    processing: Dict[str, str] = Field(default_factory=dict, max_length=20,
                                       description="处理元数据（extractor/cleaned/耗时等，键 ≤20、值 ≤500）")

    @field_validator("processing")
    @classmethod
    def _processing_bounded(cls, v: Dict[str, str]) -> Dict[str, str]:
        if any(len(val) > 500 for val in v.values()):
            raise ValueError("processing 单个值 ≤500 字符")
        return v


class ChunkRecord(StampedRecord):
    """分块契约：≤2000 字符内联 + 位置信息（M3 落盘，M2 建索引）。"""

    chunk_id: IdStr = Field(description="推荐前缀 chk-")
    document_id: IdStr
    source_id: IdStr
    sequence: int = Field(ge=0, description="文档内顺序号（从 0 起）")
    heading: str = Field(default="", max_length=200, description="所属标题（段落/标题边界切块）")
    text: str = Field(min_length=1, max_length=2000, description="块文本（硬上限 2000 字符）")
    estimated_tokens: int = Field(default=1, ge=1, le=4000, description="估算：字符数/1.6")
    char_start: int = Field(default=0, ge=0)
    char_end: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _range_ok(self) -> "ChunkRecord":
        if self.char_end < self.char_start:
            raise ValueError("char_end 必须 ≥ char_start")
        return self


# ---- 实体 6：Case ----

class CaseRecord(StampedRecord):
    """案例知识契约（辅导员工作案例）。

    红线：不混装传播效果/反馈/复盘字段（→ EffectRecord）；
    事实与推断强制分离（documented_facts / source_claims vs ai_inferences）。
    """

    case_id: IdStr = Field(description="推荐前缀 case-")
    source_ids: List[IdStr] = Field(default_factory=list, max_length=50)
    title: str = Field(min_length=1, max_length=200)
    background: str = Field(min_length=1, max_length=5000, description="背景（已脱敏的素材陈述）")
    problem: str = Field(default="", max_length=2000, description="要解决的问题")
    actors: List[str] = Field(default_factory=list, max_length=50, description="参与者（≤50 个，单个 ≤200 字符）")
    events: List[str] = Field(default_factory=list, max_length=100, description="关键事件（≤100 个，单个 ≤1000 字符）")
    methods: str = Field(default="", max_length=5000, description="采用的方法/做法")
    technology: str = Field(default="", max_length=2000)
    data: str = Field(default="", max_length=2000, description="相关数据（仅真实数据，禁编造）")
    process: str = Field(default="", max_length=3000)
    management: str = Field(default="", max_length=3000, description="管理机制")
    values: str = Field(default="", max_length=2000, description="价值内涵")
    innovation: str = Field(default="", max_length=2000)
    results: str = Field(default="", max_length=3000, description="结果/效果（仅陈述，不展开复盘）")
    transferable_patterns: List[str] = Field(default_factory=list, max_length=20, description="可迁移模式（≤20 条，单条 ≤1000 字符）")
    writing_features: str = Field(default="", max_length=3000, description="写作特征（hook/structure/升华落点）")
    evidence: List[EvidenceRef] = Field(default_factory=list, max_length=100)
    documented_facts: List[FactClaim] = Field(default_factory=list, max_length=50)
    source_claims: List[FactClaim] = Field(default_factory=list, max_length=50)
    ai_inferences: List[FactClaim] = Field(default_factory=list, max_length=50)
    tags: List[str] = Field(default_factory=list, max_length=10, description="检索标签（单条 ≤50 字符；M5 检索元数据过滤用）")

    @field_validator("tags")
    @classmethod
    def _tags_bounded(cls, v: List[str]) -> List[str]:
        if any(len(s) > 50 for s in v):
            raise ValueError("tags 单条 ≤50 字符")
        return v

    @field_validator("actors")
    @classmethod
    def _actors_bounded(cls, v: List[str]) -> List[str]:
        if any(len(s) > 200 for s in v):
            raise ValueError("actors 单条 ≤200 字符")
        return v

    @field_validator("events")
    @classmethod
    def _events_bounded(cls, v: List[str]) -> List[str]:
        if any(len(s) > 1000 for s in v):
            raise ValueError("events 单条 ≤1000 字符")
        return v

    @field_validator("transferable_patterns")
    @classmethod
    def _patterns_bounded(cls, v: List[str]) -> List[str]:
        if any(len(s) > 1000 for s in v):
            raise ValueError("transferable_patterns 单条 ≤1000 字符")
        return v

    @field_validator("documented_facts")
    @classmethod
    def _facts_only(cls, v: List[FactClaim]) -> List[FactClaim]:
        if any(f.fact_type != "documented_fact" for f in v):
            raise ValueError("documented_facts 只允许 fact_type=documented_fact 的条目")
        return v

    @field_validator("source_claims")
    @classmethod
    def _claims_only(cls, v: List[FactClaim]) -> List[FactClaim]:
        if any(f.fact_type != "source_claim" for f in v):
            raise ValueError("source_claims 只允许 fact_type=source_claim 的条目")
        return v

    @field_validator("ai_inferences")
    @classmethod
    def _inferences_only(cls, v: List[FactClaim]) -> List[FactClaim]:
        if any(f.fact_type not in ("ai_inference", "derived_pattern") for f in v):
            raise ValueError("ai_inferences 只允许 fact_type=ai_inference/derived_pattern 的条目")
        return v


# ---- 实体 7：Style ----

class StyleRecord(StampedRecord):
    """风格知识契约：只描述写作/传播特征，不存储整篇文章。"""

    style_id: IdStr = Field(description="推荐前缀 style-")
    origin: STYLE_ORIGIN = "user"
    source_ids: List[IdStr] = Field(default_factory=list, max_length=50)
    structure: str = Field(default="", max_length=3000)
    tone: str = Field(default="", max_length=1000)
    sentence_features: List[str] = Field(default_factory=list, max_length=20)
    paragraph_features: List[str] = Field(default_factory=list, max_length=20)
    title_patterns: List[str] = Field(default_factory=list, max_length=20)
    opening_patterns: List[str] = Field(default_factory=list, max_length=20)
    ending_patterns: List[str] = Field(default_factory=list, max_length=20)
    narrative_patterns: List[str] = Field(default_factory=list, max_length=20)
    communication_features: List[str] = Field(default_factory=list, max_length=20)
    exemplar_refs: List[EvidenceRef] = Field(default_factory=list, max_length=10)
    exemplar_notes: str = Field(default="", max_length=2000,
                                description="种子条目出处备注（媒体/文章名/日期/引文；待 M3 建 source 后迁移为正式引用）")
    content_hash: Optional[HashStr] = None
    usage_count: int = Field(default=0, ge=0)
    tags: List[str] = Field(default_factory=list, max_length=10)

    @field_validator("sentence_features", "paragraph_features", "title_patterns", "opening_patterns",
                     "ending_patterns", "narrative_patterns", "communication_features")
    @classmethod
    def _features_bounded(cls, v: List[str]) -> List[str]:
        if any(len(s) > 500 for s in v):
            raise ValueError("特征条目单条 ≤500 字符")
        return v

    @field_validator("tags")
    @classmethod
    def _tags_bounded(cls, v: List[str]) -> List[str]:
        if any(len(s) > 50 for s in v):
            raise ValueError("tags 单条 ≤50 字符")
        return v


class StyleSeedEnvelope(BaseRecord):
    """data/knowledge/styles/seed.json 的文件信封（种子随交付固化，origin 一律 seed）。

    导出为独立文件 data/schemas/seed.schema.json：其 entries 项的 StyleRecord
    定义内联进该文件自己的 $defs（StyleRecord 是实体模型，不在 common 共享区），
    避免 common.json 出现指向实体模型的悬空引用。
    """

    entries: List[StyleRecord] = Field(min_length=1, max_length=100)


# ---- 实体 8：Topic ----

class TopicRecord(StampedRecord):
    """选题卡契约（对齐五角度框架与选题卡模板，修复 V1 字段 drift）。"""

    topic_id: IdStr = Field(description="推荐前缀 top-")
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=500, description="素材一句话（已脱敏）")
    source_basis: SourceBasis = SourceBasis()
    evidence_basis: List[IdStr] = Field(default_factory=list, max_length=20)
    relevance: str = Field(default="", max_length=1000, description="与学生群体的相关性")
    novelty: str = Field(default="", max_length=1000)
    applicability: str = Field(default="", max_length=1000)
    expected_audience: str = Field(default="", max_length=500, description="读者画像")
    risks: str = Field(default="", max_length=1000)
    generation_status: TOPIC_STATUS = "draft"
    angles: List[TopicAngle] = Field(default_factory=list, max_length=5,
                                     description="五角度评价（成长/选择/责任/关系/家国情怀）")
    titles: List[TopicTitle] = Field(default_factory=list, max_length=5,
                                     description="标题备选（悬念/反问/故事三类各至少一个时由上层约束）")
    hook: str = Field(default="", max_length=500, description="开头钩子")
    value_landing: str = Field(default="", max_length=500, description="升华落点")


# ---- 实体 9：Analysis ----

class ComparisonItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dimension: str = Field(min_length=1, max_length=100, description="对比维度（problem/target/mechanism/…）")
    case_ids: List[IdStr] = Field(default_factory=list, max_length=20)
    finding: FactClaim


class MethodItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: str = Field(min_length=1, max_length=1000)
    evidence_ids: List[IdStr] = Field(default_factory=list, max_length=20)
    basis: str = Field(default="", max_length=1000)


class RiskItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    risk: str = Field(min_length=1, max_length=1000)
    level: RISK_LEVEL = "medium"


class RecommendationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recommendation: str = Field(min_length=1, max_length=1000)
    basis: str = Field(default="", max_length=1000)


class AnalysisRecord(StampedRecord):
    """案例分析契约：与 Case 分离；描述性事实与分析结论强制区分（fact_type 逐条标注）。"""

    analysis_id: IdStr = Field(description="推荐前缀 ana-")
    input_case_ids: List[IdStr] = Field(min_length=1, max_length=20)
    topic: str = Field(default="", max_length=200)
    patterns: List[FactClaim] = Field(default_factory=list, max_length=20, description="规律发现（逐条标注 fact_type）")
    comparisons: List[ComparisonItem] = Field(default_factory=list, max_length=20)
    transferable_methods: List[MethodItem] = Field(default_factory=list, max_length=20)
    risks: List[RiskItem] = Field(default_factory=list, max_length=20)
    recommendations: List[RecommendationItem] = Field(default_factory=list, max_length=20)
    evidence: List[EvidenceRef] = Field(default_factory=list, max_length=50)


# ---- 实体 10：Mapping ----

class MatchingPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    point: str = Field(min_length=1, max_length=1000)
    case_id: Optional[IdStr] = None
    profile_field: str = Field(default="", max_length=50)
    evidence_ids: List[IdStr] = Field(default_factory=list, max_length=10)


class DifferenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    difference: str = Field(min_length=1, max_length=1000)
    case_id: Optional[IdStr] = None
    profile_field: str = Field(default="", max_length=50)


class MappingRecord(StampedRecord):
    """案例 × 学校画像映射契约：外部成功案例 ≠ 本地可直接套用。"""

    mapping_id: IdStr = Field(description="推荐前缀 map-")
    case_ids: List[IdStr] = Field(min_length=1, max_length=20)
    profile_id: IdStr
    matching_points: List[MatchingPoint] = Field(default_factory=list, max_length=20)
    differences: List[DifferenceItem] = Field(default_factory=list, max_length=20)
    adaptation_requirements: List[str] = Field(default_factory=list, max_length=20)
    transferable_elements: List[str] = Field(default_factory=list, max_length=20)
    non_transferable_elements: List[str] = Field(default_factory=list, max_length=20)
    risks: List[RiskItem] = Field(default_factory=list, max_length=20)
    rationale: str = Field(default="", max_length=3000)

    @field_validator("adaptation_requirements", "transferable_elements", "non_transferable_elements")
    @classmethod
    def _items_bounded(cls, v: List[str]) -> List[str]:
        if any(len(s) > 1000 for s in v):
            raise ValueError("映射条目单条 ≤1000 字符")
        return v


# ---- 实体 11：Profile ----

class SchoolProfileRecord(StampedRecord):
    """学校画像契约：7 键白名单（对应 V1 school.json），extra=forbid 拒绝未知键。"""

    profile_id: IdStr = Field(description="推荐前缀 pro-")
    school_name: str = Field(default="", max_length=200)
    school_type: SCHOOL_TYPE = "other"
    student_profile: str = Field(default="", max_length=3000)
    common_topics: List[str] = Field(default_factory=list, max_length=20)
    sensitive_points: List[str] = Field(default_factory=list, max_length=20)
    title_style_preference: str = Field(default="", max_length=1000)
    provenance: List[ProfileProvenance] = Field(default_factory=list, max_length=20,
                                                description="字段级溯源（事实带证据，推断带依据）")

    @field_validator("common_topics")
    @classmethod
    def _topics_bounded(cls, v: List[str]) -> List[str]:
        if any(len(s) > 200 for s in v):
            raise ValueError("common_topics 单条 ≤200 字符")
        return v

    @field_validator("sensitive_points")
    @classmethod
    def _sensitive_bounded(cls, v: List[str]) -> List[str]:
        if any(len(s) > 500 for s in v):
            raise ValueError("sensitive_points 单条 ≤500 字符")
        return v


# ---- 实体 12：Audit ----

class AuditIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    check: AUDIT_CHECK = Field(description="七项自查（political/factual/value/labeling/ai_trace/privacy/copyright）+ 标点/格式")
    verdict: VERDICT
    note: str = Field(default="", max_length=1000)
    location: str = Field(default="", max_length=200, description="问题定位（段落/句子引用）")
    evidence_ids: List[IdStr] = Field(default_factory=list, max_length=10)


class CheckGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    passed: bool = True
    notes: str = Field(default="", max_length=1000)


class AuditRecord(StampedRecord):
    """审核契约（单通道：七项结构化自查 + 标点门禁；无第二意见列）。

    分组映射：fact_check=[political, factual]；style_check=[value, labeling, ai_trace]；
    format_check=[punctuation, format]；risk_check=[privacy, copyright]。
    verdict 硬规则：political/factual/privacy 任一 fail → passed 必须为 false。
    """

    audit_id: IdStr = Field(description="推荐前缀 aud-")
    draft_id: IdStr
    issues: List[AuditIssue] = Field(default_factory=list, max_length=100)
    fact_check: CheckGroup = CheckGroup()
    style_check: CheckGroup = CheckGroup()
    format_check: CheckGroup = CheckGroup()
    risk_check: CheckGroup = CheckGroup()
    passed: bool = True
    summary: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def _verdict_rule(self) -> "AuditRecord":
        fatal = {"political", "factual", "privacy"}
        if any(i.verdict == "fail" and i.check in fatal for i in self.issues) and self.passed:
            raise ValueError("政治/隐私/事实任一 fail → passed 必须为 false（verdict 硬规则）")
        groups = (self.fact_check, self.style_check, self.format_check, self.risk_check)
        if not all(g.passed for g in groups) and self.passed:
            raise ValueError("存在未通过的检查分组，passed 必须为 false")
        return self


# ---- 实体 13：Effect（传播复盘，与 Case 分离） ----

class EffectDimension(BaseModel):
    model_config = ConfigDict(extra="forbid")
    score: Optional[int] = Field(default=None, ge=1, le=5)
    note: str = Field(default="", max_length=1000)


class EffectRecord(StampedRecord):
    """传播效果/复盘契约：V1 案例 16 字段里的 effect/retro 独立成表，不回写 Case。"""

    effect_id: IdStr = Field(description="推荐前缀 eff-")
    case_id: IdStr
    draft_id: Optional[IdStr] = None
    dimensions: Dict[str, EffectDimension] = Field(
        default_factory=dict, max_length=5,
        description="五维度：title/opening/paragraph_resonance/sublimation/communication_data")
    feedback: str = Field(default="", max_length=2000, description="辅导员反馈/传播数据")
    retro: str = Field(default="", max_length=3000, description="复盘结论")
    reusable_patterns: List[str] = Field(default_factory=list, max_length=20,
                                         description="可复用经验（回写风格库/案例库的依据，单条 ≤1000 字符）")

    @field_validator("dimensions")
    @classmethod
    def _dims_known(cls, v: Dict[str, EffectDimension]) -> Dict[str, EffectDimension]:
        allowed = {"title", "opening", "paragraph_resonance", "sublimation", "communication_data"}
        unknown = set(v) - allowed
        if unknown:
            raise ValueError(f"未知复盘维度：{sorted(unknown)}（允许：{sorted(allowed)}）")
        return v

    @field_validator("reusable_patterns")
    @classmethod
    def _patterns_bounded(cls, v: List[str]) -> List[str]:
        if any(len(s) > 1000 for s in v):
            raise ValueError("reusable_patterns 单条 ≤1000 字符")
        return v


# ---- 实体 14：Draft（写作产物） ----

class DraftRecord(StampedRecord):
    """正文草稿契约：白名单血缘（lineage）+ 逐条可追溯断言（claims）。"""

    draft_id: IdStr = Field(description="推荐前缀 drf-")
    task_id: Optional[IdStr] = None
    title: str = Field(min_length=1, max_length=200)
    subtitle: str = Field(default="", max_length=200)
    sections: List[DraftSection] = Field(min_length=1, max_length=20,
                                         description="四段式：钩子/事件叙述/冲突展开/价值升华")
    closing: str = Field(default="", max_length=500, description="落款（文风/热点/阅读时长）")
    word_count: int = Field(default=0, ge=0)
    style_id: Optional[IdStr] = None
    lineage: DraftLineage = DraftLineage()
    claims: List[FactClaim] = Field(default_factory=list, max_length=100,
                                    description="主要事实性断言（逐条标注 fact_type + 证据）")
    status: DRAFT_STATUS = "draft"
    mode: str = Field(default="article", max_length=32,
                      description="内容形态（article/report/outline/topic_proposal/guide/commentary；Python 注入，决定 audit 审核上下文）")


# ---- 实体 15：Extraction（M4 提取运行记录） ----

class ModelMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(default="", max_length=100)
    mode: MODEL_MODE = "economy"
    temperature: Optional[float] = Field(default=None, ge=0, le=2)


class ExtractionRecord(StampedRecord):
    """提取运行契约：失败不丢弃源，保留 input refs + 校验错误（M4 自纠正 ≤2 次后标 failed）。"""

    extraction_id: IdStr = Field(description="推荐前缀 ext-")
    extractor: EXTRACTOR_KIND
    input_refs: Dict[str, List[IdStr]] = Field(default_factory=dict, max_length=3,
                                               description="artifact_ids / document_ids / chunk_ids")
    output_refs: Dict[str, List[IdStr]] = Field(default_factory=dict, max_length=3,
                                                description="case_ids / style_ids / topic_ids")
    status: EXTRACTION_STATUS = "success"
    attempts: int = Field(default=1, ge=0, le=3, description="自纠正上限：初次 + ≤2 次修正")
    validation_errors: List[Dict[str, str]] = Field(default_factory=list, max_length=50,
                                                    description="[{path,message,type}]（机器可读，供自纠正循环消费）")
    model_metadata: ModelMetadata = ModelMetadata()

    @field_validator("input_refs", "output_refs")
    @classmethod
    def _refs_bounded(cls, v: Dict[str, List[IdStr]]) -> Dict[str, List[IdStr]]:
        for ids in v.values():
            if len(ids) > 200:
                raise ValueError("每类 ref 最多 200 条")
        return v

    @field_validator("validation_errors")
    @classmethod
    def _verr_bounded(cls, v: List[Dict[str, str]]) -> List[Dict[str, str]]:
        for item in v:
            unknown = set(item) - {"path", "message", "type"}
            if unknown:
                raise ValueError(f"validation_errors 只允许 path/message/type 键，未知键：{sorted(unknown)}")
            if any(len(val) > 1000 for val in item.values()):
                raise ValueError("validation_errors 单项值 ≤1000 字符")
        return v


# ---- 实体 16：Evidence ----

class EvidenceRecord(StampedRecord):
    """证据契约：独立记录，三级回指（artifact/document/chunk/source 至少其一）。

    excerpt 为有界摘录（≤500 字符），禁止把整篇文章复制为「证据」。
    GC 引用检查以本表为事实/推断的引用来源之一（M7）。
    """

    evidence_id: IdStr = Field(description="推荐前缀 evd-")
    kind: EVIDENCE_KIND
    ref_artifact_id: Optional[IdStr] = None
    ref_source_id: Optional[IdStr] = None
    ref_document_id: Optional[IdStr] = None
    ref_chunk_id: Optional[IdStr] = None
    excerpt: str = Field(default="", max_length=500)
    note: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def _at_least_one_ref(self) -> "EvidenceRecord":
        if not (self.ref_artifact_id or self.ref_source_id or self.ref_document_id or self.ref_chunk_id):
            raise ValueError("证据必须至少回指 artifact/source/document/chunk 之一")
        return self


# ---- 注册表与导出 ----

ENTITIES: Dict[str, type] = {
    "task": TaskRecord,
    "artifact": ArtifactRecord,
    "source": SourceRecord,
    "document": DocumentRecord,
    "chunk": ChunkRecord,
    "case": CaseRecord,
    "style": StyleRecord,
    "topic": TopicRecord,
    "analysis": AnalysisRecord,
    "mapping": MappingRecord,
    "profile": SchoolProfileRecord,
    "audit": AuditRecord,
    "effect": EffectRecord,
    "draft": DraftRecord,
    "extraction": ExtractionRecord,
    "evidence": EvidenceRecord,
}

COMMON_DEFS: Dict[str, type] = {
    "RefPair": RefPair,
    "EvidenceRef": EvidenceRef,
    "FactClaim": FactClaim,
    "SourceBasis": SourceBasis,
    "RetryInfo": RetryInfo,
    "ErrorInfo": ErrorInfo,
    "TopicAngle": TopicAngle,
    "TopicTitle": TopicTitle,
    "DraftSection": DraftSection,
    "DraftLineage": DraftLineage,
    "ProfileProvenance": ProfileProvenance,
    "ComparisonItem": ComparisonItem,
    "MethodItem": MethodItem,
    "RiskItem": RiskItem,
    "RecommendationItem": RecommendationItem,
    "AuditIssue": AuditIssue,
    "CheckGroup": CheckGroup,
    "EffectDimension": EffectDimension,
    "ModelMetadata": ModelMetadata,
    "DocSection": DocSection,
    "MatchingPoint": MatchingPoint,
    "DifferenceItem": DifferenceItem,
}

# 独立导出的文件信封（引用实体模型，须自含 $defs，不能进 common 共享区）
SEED_FILES: Dict[str, type] = {
    "seed": StyleSeedEnvelope,
}

ENUM_VALUES: Dict[str, List[str]] = {
    "task_status": TASK_STATUSES,
    "task_type": ["research", "writing", "review", "retro", "extraction", "import", "maintenance", "generic"],
    "failure_code": FAILURE_CODES,
    "fact_type": FACT_TYPES,
    "artifact_status": ["created", "valid", "invalid", "failed", "expired"],
    "retention": ["temporary", "permanent", "task_bound"],
    "language": ["zh", "en", "ja", "auto", "unknown"],
    "retrieval_method": ["api", "http", "browser", "user_provided", "sibling_skill", "unknown"],
    "source_status": ["success", "blocked", "access_restricted", "unavailable", "timeout", "rate_limited", "parse_failed"],
    "evidence_kind": ["quote", "paraphrase", "data_point", "link", "screenshot_desc"],
    "extractor_kind": ["case_facts", "style_pattern", "topic_signal", "hot_interface", "media_style"],
    "extraction_status": ["success", "extraction_failed", "validation_failed", "cancelled"],
    "model_mode": ["economy", "standard", "deep"],
    "verdict": ["pass", "warn", "fail"],
    "risk_level": ["low", "medium", "high"],
    "draft_status": ["draft", "reviewed", "final"],
    "topic_status": ["draft", "confirmed", "used", "abandoned"],
    "school_type": ["university", "college", "vocational", "high_school", "other"],
    "style_origin": ["seed", "user"],
    "audit_check": ["political", "factual", "value", "labeling", "ai_trace", "privacy", "copyright", "punctuation", "format"],
    "title_type": ["suspense", "question", "story"],
    "ref_kind": REF_KINDS,
}

JSON_SCHEMA_URI = "https://json-schema.org/draft/2020-12/schema"
REF_TEMPLATE = "#/$defs/{model}"


def export_schemas() -> Dict[str, dict]:
    """导出全部契约：common.json（共享 $defs + 枚举表）+ 16 个实体 schema。

    实体文件内的 "$ref": "#/$defs/Xxx" 统一解析到 common.json 的 $defs
    （运行时校验以 Pydantic 模型为准，JSON 文件是跨工具可读的契约文档）。
    """
    out: Dict[str, dict] = {}
    for name, model in ENTITIES.items():
        schema = model.model_json_schema(ref_template=REF_TEMPLATE)
        # pydantic 对无 $defs 的模型不输出 $schema 键；统一补齐保证每份文件自声明草案版本
        schema.setdefault("$schema", JSON_SCHEMA_URI)
        out[name] = schema
    for name, model in SEED_FILES.items():
        schema = model.model_json_schema(ref_template=REF_TEMPLATE)
        schema.setdefault("$schema", JSON_SCHEMA_URI)
        out[name] = schema
    defs = {name: model.model_json_schema(ref_template=REF_TEMPLATE) for name, model in COMMON_DEFS.items()}
    out["common"] = {
        "$schema": JSON_SCHEMA_URI,
        "title": "common",
        "description": "共享定义与枚举表；实体文件中的 #/$defs/Xxx 引用解析到本文件。",
        "$defs": defs,
        "enums": ENUM_VALUES,
    }
    return out
