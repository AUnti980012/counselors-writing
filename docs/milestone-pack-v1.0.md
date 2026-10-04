# fudaoyuan-baokuan

## Milestone Prompt Pack v1.0

> 用途：供 Codex / Claude Code 等 Coding Agent 在无历史聊天上下文情况下，按 M0 → M9 分阶段执行项目重构与能力恢复。
>
> 核心原则：
>
> * 不要求 Agent 记住上一轮对话。
> * 每个 Milestone 开始前重新读取仓库现状。
> * 每个 Milestone 只处理自己负责的范围。
> * 不为了“完成阶段”而擅自提前实现后续阶段。
> * 代码、Schema、知识文件、数据库、缓存和 Artifact 必须有明确责任边界。
> * 所有阶段都必须优先保证已有能力不被无意删除。
> * Codex 与 Claude Code 只作为不同运行适配器，业务逻辑保持平台无关。
>
> 推荐执行顺序：
>
> `M0 → M1 → M2 → M3 → M4 → M5 → M6 → M7 → M8 → M9`

---

# 0. GLOBAL BOOTSTRAP

## 所有 Milestone 均可附加在本协议后使用

```text
You are working on the repository "fudaoyuan-baokuan".

You may have NO useful context from previous conversations.

Therefore:
1. Do not assume previous chat messages exist.
2. Do not assume the repository is already in the expected architecture.
3. Read the current repository state before making decisions.
4. Treat repository files as the current source of truth.
5. Treat the current user request as the source of truth for this milestone.
6. Do not invent files, schemas, dependencies, commands, or completed features.
7. Preserve existing capabilities unless there is an explicit retirement/migration decision.
8. Do not perform work belonging to a later milestone unless it is strictly required for the current milestone.
9. Never replace an existing implementation merely because a new implementation looks cleaner.
10. Prefer incremental, reviewable changes.

PLATFORM COMPATIBILITY:
- The solution must work conceptually in both Codex and Claude Code.
- Do not make core business logic depend on one agent platform.
- Agent-specific behavior should remain limited to:
  - skill loading
  - command invocation
  - file interaction
  - runtime adaptation
- Natural-language execution and optional slash commands must eventually map to the same underlying operations.

TOKEN ECONOMY:
- Do not repeatedly place large documents into LLM context when a small artifact pointer is sufficient.
- Prefer:
  artifact_id
  path
  status
  hash
  schema_version
  summary
  source_ids
  parent_ids
- Python/local deterministic processing should handle:
  - fetching
  - parsing
  - cleaning
  - normalization
  - hashing
  - deduplication
  - chunking
  - validation
  - indexing
  - cache lookup
  - filesystem operations
  - SQLite operations
  - GC
- LLM should be reserved primarily for:
  - semantic understanding
  - structured extraction
  - case interpretation
  - method abstraction
  - comparison
  - transferability analysis
  - writing
  - review

DATA BOUNDARY:
Do not mix these concepts:
Source
Document
Chunk
Case
Style
Topic
Analysis
Mapping
Profile
Draft
Review
Artifact
Task

PERSISTENCE RULE:
- Files are the primary long-term content store.
- SQLite/FTS5 is an index/search layer, not the authoritative content store.
- Artifact Registry is a lineage/identity ledger, not the primary search database.
- Do not store complete historical prompts, full analysis transcripts, or unlimited context inside persistent knowledge objects.

FACT / INFERENCE RULE:
Whenever semantic extraction is involved, distinguish:
- documented_fact
- source_claim
- ai_inference
- derived_pattern
- recommendation

Do not silently convert inference into fact.

FAILURE RULE:
Standardize machine-readable failure states where appropriate:
- success
- blocked
- access_restricted
- unavailable
- timeout
- rate_limited
- parse_failed
- extraction_failed
- validation_failed
- index_failed
- dependency_failed
- cancelled

RETRY RULE:
- Retries must be finite.
- Extraction self-correction maximum: 2 attempts after the initial failure.
- Never create infinite LLM correction loops.

IDEMPOTENCY:
Repeated execution of the same deterministic operation should reuse a valid existing artifact/cache entry whenever possible.

CHANGE CONTROL:
Before destructive or high-impact modifications:
- inspect current implementation
- identify dependencies
- preserve useful legacy behavior
- document retirement/migration
- avoid silent deletion

At the end of the milestone:
1. Run relevant tests/checks.
2. Report files changed.
3. Report files intentionally not changed.
4. Report known limitations.
5. Report regression risks.
6. Report the next milestone readiness.
7. Do not claim zero regression risk.
```

---

# M0｜Architecture Audit + Historical Feature Recovery

## 目标

建立真实基线，恢复旧项目能力地图，确认哪些能力必须保留、迁移、修复、退休。

## 对应模块

`MODULE 00 + MODULE 27`

## 可直接执行 Prompt

```text
=== MILESTONE M0 : ARCHITECTURE AUDIT + HISTORICAL FEATURE RECOVERY ===

Read the GLOBAL BOOTSTRAP first.

MISSION:
Perform a read-only architecture audit of the current "fudaoyuan-baokuan" repository and recover historical feature requirements from available project history/files.

IMPORTANT:
This milestone is primarily an audit and specification milestone.
Do NOT perform production refactoring unless explicitly necessary for documentation correctness.

STEP 1 — FULL REPOSITORY INVENTORY

Inspect:
- SKILL.md
- references/
- assets/
- scripts/
- data/
- cache/
- configuration files
- tests if present
- README / AGENTS / CHANGELOG if present
- dependency/configuration manifests

Build an inventory of:
- existing modules
- commands
- scripts
- schemas
- data stores
- references
- templates
- CLI entry points
- runtime dependencies
- external services
- undocumented coupling
- dead files
- placeholders
- duplicated functionality

Do not rely on filenames alone. Read the actual contents.

STEP 2 — CURRENT CAPABILITY MAP

For every existing capability determine:

- capability_name
- entry_point
- inputs
- outputs
- dependencies
- persistence
- current status
- token cost risk
- data quality risk
- regression sensitivity

Classify each capability as one of:
- usable
- semi_usable
- broken
- dead
- placeholder
- duplicated
- unclear

STEP 3 — HISTORICAL FEATURE RECOVERY

Search available history, old prompts, previous documentation, archived files, and project materials.

Recover functionality that existed previously but may have disappeared from the current repository.

For each recovered feature record:
- feature_name
- historical_source
- what it did
- current equivalent
- missing functionality
- whether recovery is required
- suggested target milestone
- retirement reason if intentionally excluded

Do not blindly restore historical code.

Historical behavior is evidence of requirements, not automatically the preferred implementation.

STEP 4 — CRITICAL RISK AUDIT

Pay particular attention to:

- hardcoded user-specific absolute paths
- platform-specific assumptions
- qwen-verify or other unnecessary verification dependencies
- xinbang or other permanently failing placeholders
- full webpage output sent directly to the Agent
- Douyin raw innerText flooding context
- unrestricted case/style retrieval
- full-library loading
- lack of cache
- lack of task state
- lack of resume
- mixed data responsibilities
- weak schema validation
- silent invalid-record skipping
- non-atomic writes
- whole-library rewrite patterns
- duplicate source content
- missing lineage
- persistent prompt/context pollution
- full analysis transcript persistence

STEP 5 — TOKEN / CONTEXT AUDIT

Identify every location where potentially large content enters the LLM context.

For each location estimate:
- input size
- repeated size
- worst-case size
- whether the content can be replaced by a pointer
- whether local preprocessing can reduce it
- whether field-level retrieval is possible

Pay special attention to:
- long external references
- article fetch output
- media pages
- Douyin pages
- style library
- case library
- writing workflow
- review workflow

STEP 6 — RESPONSIBILITY MAP

Create a table:

Capability | Current Layer | Correct Target Layer | Reason | Milestone

Correct target layers should include:
- Agent
- deterministic Python
- LLM extraction
- SQLite/FTS5
- JSON knowledge
- Artifact
- cache
- task engine
- audit

STEP 7 — DOCUMENTATION

Create/update:

docs/
- architecture-audit.md
- capability-inventory.md
- historical-feature-recovery.md
- token-boundary-audit.md
- migration-plan.md

The migration plan must clearly distinguish:
- retain
- migrate
- rewrite
- wrap
- deprecate
- remove

STEP 8 — DO NOT OVER-IMPLEMENT

Do not build:
- SQLite runtime
- Task Engine
- Fetcher rewrite
- extraction pipeline
- vector database
- multi-agent architecture
- backup engine
- GC engine

Those belong to later milestones.

STEP 9 — FINAL OUTPUT

Produce:
1. current architecture summary
2. capability inventory
3. historical feature recovery matrix
4. critical risks
5. token hotspots
6. data-boundary problems
7. proposed migration map
8. explicit scope for M1
9. list of files changed

SUCCESS CRITERIA:
- Repository is understood.
- Existing features are mapped.
- Historical capabilities are identified.
- Critical risks are documented.
- No production behavior is unintentionally changed.
```

---

# M1｜Schema Data Contract Layer

## 目标

冻结 Data Contract，让后续 Task Engine、Fetcher、Extraction、Knowledge、Search 都建立在统一数据边界上。

## 对应模块

`MODULE 03 + MODULE 04 + MODULE 28`

## 可直接执行 Prompt

```text
=== MILESTONE M1 : SCHEMA DATA CONTRACT LAYER ===

Read the GLOBAL BOOTSTRAP first.

Read:
- docs/architecture-audit.md
- docs/capability-inventory.md
- docs/historical-feature-recovery.md
- docs/migration-plan.md

MISSION:
Freeze the project's core data contract.

IMPORTANT:
This milestone must establish schemas before runtime-heavy implementation.

Do NOT build the full runtime pipeline yet.

STEP 1 — CREATE SCHEMA DIRECTORY

Create:

data/schemas/

Initial required schemas:

- task.schema.json
- artifact.schema.json
- source.schema.json
- document.schema.json
- chunk.schema.json
- case.schema.json

Then create the contract for:

- style.schema.json
- topic.schema.json
- analysis.schema.json
- mapping.schema.json
- profile.schema.json
- audit.schema.json

STEP 2 — DEFINE TASK CONTRACT

Task must support:
- task_id
- task_type
- status
- progress
- created_at
- updated_at
- input references
- output references
- retry information
- error information
- schema_version
- resumability

Recommended lifecycle:

CREATED
ROUTING
ACCESSING
FETCHING
PROCESSING
EXTRACTING
VALIDATING
INDEXING
ANALYZING
MAPPING
GENERATING
REVIEWING
COMPLETED
FAILED
BLOCKED
CANCELLED

Do not make the LLM the owner of task state.

STEP 3 — DEFINE ARTIFACT CONTRACT

Artifact must identify:
- artifact_id
- artifact_type
- status
- path
- content_hash
- schema_version
- source_ids
- parent_ids
- retention
- created_at
- updated_at

Artifact records must support lineage.

STEP 4 — DEFINE SOURCE CONTRACT

Source should contain metadata such as:

- source_id
- url
- canonical_url
- domain
- title
- author
- publisher
- published_at
- retrieved_at
- retrieval_method
- status
- content_hash
- metadata

Do NOT make the source metadata object a permanent container for the complete raw webpage body.

STEP 5 — DEFINE DOCUMENT / CHUNK

Document:
- document_id
- source_id
- content_hash
- language
- word_count
- sections
- chunk_ids
- processing metadata

Chunk:
- chunk_id
- document_id
- source_id
- sequence
- heading
- text
- estimated_tokens
- location information

STEP 6 — DEFINE CASE

Case must support the knowledge needed for counselor-oriented case analysis.

Suggested fields:

- case_id
- source_ids
- title
- background
- problem
- actors
- events
- methods
- technology
- data
- process
- management
- values
- innovation
- results
- transferable_patterns
- writing_features
- evidence
- documented_facts
- source_claims
- ai_inferences

Keep evidence references explicit.

DO NOT mix dissemination effect/feedback/retro fields into the core Case object.

STEP 7 — STYLE

Style should describe writing/communication characteristics rather than duplicate article content.

Support:
- style_id
- source_ids
- structure
- tone
- sentence_features
- paragraph_features
- title_patterns
- opening_patterns
- ending_patterns
- narrative_patterns
- communication_features

DO NOT store whole articles as style data.

STEP 8 — TOPIC

Create a structured topic contract suitable for later selection and generation.

Include enough fields to support:
- topic identity
- source/case basis
- relevance
- novelty
- applicability
- evidence basis
- expected audience
- risks
- generation status

STEP 9 — ANALYSIS

Analysis must be separate from Case.

Support:
- analysis_id
- input_case_ids
- topic
- patterns
- comparisons
- transferable_methods
- risks
- recommendations
- evidence

Separate facts from analysis.

STEP 10 — MAPPING

Mapping must connect case knowledge with school profile.

Support:
- mapping_id
- case_ids
- profile_id
- matching_points
- differences
- adaptation_requirements
- transferable_elements
- non_transferable_elements
- risks

STEP 11 — PROFILE

Profile must remain bounded and structured.

Do not use an unrestricted text blob as the school profile.

STEP 12 — AUDIT

Audit should support:
- audit_id
- draft_id
- issues
- fact_check
- style_check
- format_check
- risk_check
- passed

STEP 13 — JSON BOUNDARY AUDIT

Audit every schema for:
- unlimited arrays
- unrestricted text fields
- duplicated source content
- prompt leakage
- historical conversation leakage
- complete analysis transcript storage
- recursive/nested structures
- ambiguous ownership
- missing identifiers
- missing schema versions
- unclear references

Where possible replace embedded large objects with IDs/references.

STEP 14 — RESPONSIBILITY BOUNDARY

Do not allow LLMs to define schema dynamically.

Schema is deterministic infrastructure.

LLM outputs must conform to schema.

STEP 15 — SEED MIGRATION

Migrate the style seed currently embedded in references into a single facts source, for example:

data/knowledge/styles/seed.json

Then reduce the reference document to:
- usage guidance
- interpretation rules
- pointer to canonical structured data

Do not create multiple competing sources of truth.

STEP 16 — FIXTURE DATA

Create minimal representative fixtures for schema validation.

Fixtures should include:
- valid example
- invalid example
- minimal example
- edge case example

Do not create huge datasets.

STEP 17 — DOCUMENT CONTRACT

Create:

docs/data-contract.md

Document:
- entities
- ownership
- references
- lifecycle
- versioning
- fact/inference rules
- storage responsibilities
- migration rules

STEP 18 — PRODUCTION SAFETY

Do not redirect the current production workflow to the new schemas unless explicitly required.

This milestone is about contract establishment.

SUCCESS CRITERIA:
- Core schemas exist.
- Schema relationships are coherent.
- JSON boundary is documented.
- Seed data has a canonical location.
- Fixtures exist.
- Future runtime modules can build against frozen contracts.
- No major production runtime behavior is silently changed.
```

---

# M2｜Artifact + Index + Cache Foundation

## 目标

先建立持久化骨架：Artifact Registry、SQLite/FTS5、Cache 分层。

## 对应模块

`MODULE 13 + MODULE 10 + MODULE 12`

```text
=== MILESTONE M2 : ARTIFACT + INDEX + CACHE FOUNDATION ===

Read GLOBAL BOOTSTRAP and M1 docs/contracts first.

MISSION:
Implement the persistence and reuse foundation without yet replacing the entire research pipeline.

ARCHITECTURE:

Files = authoritative content
SQLite/FTS5 = searchable index
Artifact Registry = lineage/identity ledger
Cache = reusable intermediate/temporary computation

STEP 1 — DIRECTORY STRUCTURE

Create, as appropriate:

data/
  registry/
  knowledge/
  artifacts/
  index/
  backups/

cache/
  raw/
  processed/
  extraction/
  tasks/

Prefer the canonical SQLite path:

data/index.db

Do not introduce both:
data/index.db
and
data/index/kb.db

unless a documented reason exists.

STEP 2 — ARTIFACT REGISTRY

Create:

data/registry/artifacts.jsonl

Registry responsibility:
- artifact identity
- type
- path
- status
- content hash
- schema version
- lineage
- retention
- timestamps

Do not turn Registry into a full-text search database.

STEP 3 — ATOMIC WRITE

Artifact writes must:
- write to temporary path
- validate content
- fsync where appropriate
- atomically replace target
- avoid partially written JSONL/JSON artifacts

Invalid writes must not silently replace valid content.

STEP 4 — CONTENT HASH

Implement deterministic hashing.

Hash should be based on normalized content where appropriate.

Distinguish:
- URL/cache identity hash
- content hash

Do not assume URL identity means content identity.

STEP 5 — SQLITE / FTS5

Implement a local SQLite index suitable for single-user usage.

At minimum index:
- source metadata
- document metadata
- chunk content/metadata
- case metadata where appropriate
- style metadata where appropriate
- topic metadata where appropriate
- artifact references

Use FTS5 for text retrieval.

SQLite is not the canonical long-term content store.

STEP 6 — CACHE

Implement cache namespaces:

raw
processed
extraction
tasks

Cache entries should support:
- key
- artifact/path reference
- created_at
- updated_at
- last_accessed
- status
- TTL
- content hash
- schema version

STEP 7 — CACHE KEY NORMALIZATION

Normalize URLs before generating cache identity.

Handle common tracking parameters such as:
- utm_*
- common referral tracking parameters

Normalize:
- scheme
- host casing
- trailing slash
- encoding
- canonical URL when known

Do not remove parameters that may alter actual content.

STEP 8 — IDEMPOTENCY

Same normalized input + same operation + same relevant version should reuse existing valid artifact when possible.

Examples:
same URL fetch
same extraction
same preprocessing
same chunking

Do not rerun expensive operations unnecessarily.

STEP 9 — INDEX REBUILD

Provide deterministic rebuilding of index from canonical files.

The system must not depend on SQLite being the only copy of knowledge.

STEP 10 — DOCUMENTATION

Create:

docs/storage-architecture.md

Explain:
- file store
- artifact registry
- SQLite index
- cache
- lineage
- rebuild strategy
- invalidation

SUCCESS CRITERIA:
- Artifact registration works.
- Atomic writes work.
- Hashing is deterministic.
- SQLite/FTS5 works.
- Cache namespaces are isolated.
- Index can be rebuilt.
- No large LLM context dependency is introduced.
```

---

# M3｜Web Acquisition + Compliance + Processing + Chunking

## 目标

把原来的“网页直接灌给 Agent”改造成：

`URL → Access → Fetch → Clean → Normalize → Chunk → Artifact Pointer`

## 对应模块

`MODULE 05 + MODULE 06 + MODULE 07 + MODULE 08`

```text
=== MILESTONE M3 : WEB ACQUISITION + PROCESSING PIPELINE ===

Read GLOBAL BOOTSTRAP and M1/M2 contracts.

MISSION:
Build a compliant public-web acquisition and preprocessing pipeline.

CORE PRINCIPLE:

Python handles:
- access
- HTTP
- browser rendering
- extraction
- cleaning
- normalization
- hashing
- chunking

LLM receives:
- structured, bounded text
- metadata
- chunk references

Never use the LLM as the web crawler.

STEP 1 — ACCESS ORDER

Preferred order:

1. official API/public structured endpoint
2. normal HTTP request
3. normal browser rendering for publicly accessible dynamic pages
4. user-provided text/file fallback

Do not implement anti-bot bypass.

STEP 2 — COMPLIANCE BOUNDARY

Allowed:
- public pages
- ordinary HTTP requests
- public APIs
- normal browser rendering
- DevTools/network inspection for debugging public resources

Forbidden:
- bypass login
- bypass CAPTCHA
- solve CAPTCHA
- fingerprint evasion
- anti-bot evasion
- bypass IP restrictions
- bypass paywalls
- bypass Cloudflare/DataDome or equivalent access controls

Technical accessibility does not automatically mean authorized access.

Respect:
- access controls
- reasonable rate limits
- finite retries
- source attribution

STEP 3 — STANDARD FETCH STATUS

Normalize statuses:

success
blocked
access_restricted
unavailable
timeout
rate_limited
parse_failed

Do not convert blocked access into fabricated success.

STEP 4 — FETCH OUTPUT

Fetcher should NOT dump the complete webpage into stdout for the Agent.

Instead return a small structured pointer object:

- artifact_id
- path
- status
- source_id
- content_hash
- metadata
- schema_version
- summary if available

STEP 5 — RAW STORAGE

Store raw HTML in:

cache/raw/

Raw content is temporary by default.

Suggested default TTL:
24–72 hours

Failed artifacts may be retained longer for recovery.

User-explicitly retained sources may be marked permanent.

STEP 6 — HTML PROCESSING

Pipeline:

raw HTML
→ remove irrelevant DOM
→ identify article/main content
→ preserve headings
→ preserve paragraphs
→ preserve meaningful lists/tables where possible
→ normalize whitespace
→ normalize encoding
→ remove navigation/advertisement/noise
→ produce processed representation

Do not blindly keyword-truncate long articles.

STEP 7 — DOCUMENT

Generate a Document object with:
- document_id
- source_id
- content_hash
- language
- word_count
- sections
- chunk references

Store processed document under the appropriate artifact/processed location.

STEP 8 — TOKEN-AWARE CHUNKING

Chunk according to:
- heading
- section
- paragraph
- semantic boundary

Prefer meaningful sections over arbitrary character slicing.

Use:
- token estimate
- maximum chunk size
- limited overlap
- sequence
- source location

Do not create enormous overlap.

STEP 9 — DEDUPLICATION

Use content_hash to detect:
- same content under multiple URLs
- repeated crawl
- mirrored content

Keep source attribution even when content is deduplicated.

STEP 10 — MEDIA-SPECIFIC HANDLING

Preserve existing media acquisition capabilities:
- general web articles
- Weibo/hotlist-related sources where currently supported
- Douyin research flow
- media style research
- fallback sources

For Douyin:
Do NOT send giant page innerText to the Agent.

Prefer structured extraction:
- title
- author/account
- publish information if available
- visible description
- available engagement indicators
- public text
- resource/source metadata
- fetch status

STEP 11 — FALLBACK

If source cannot be accessed:
return an explicit blocked/access_restricted/unavailable state and allow:
- user pasted text
- user uploaded file
- alternate public source

Do not fabricate missing content.

STEP 12 — SOURCE ATTRIBUTION

Every processed document and extracted knowledge item should retain source references.

STEP 13 — TEST

Test:
- normal article
- malformed article
- long article
- duplicate article
- blocked page
- timeout
- rate limit
- dynamic public page
- Douyin structured extraction
- user fallback

SUCCESS CRITERIA:
- Web content no longer floods Agent context.
- Raw/processed/chunk artifacts exist.
- Fetch statuses are standardized.
- Access controls are respected.
- Long documents are structurally chunked.
- Same content can be deduplicated.
```

---

# M4｜LLM Structured Extraction + Validation

## 目标

把 AI 放在它最有价值的地方：语义理解、事实抽取、案例抽取、方法归纳、风格理解。

## 对应模块

`MODULE 09`

```text
=== MILESTONE M4 : LLM STRUCTURED EXTRACTION + VALIDATION ===

Read GLOBAL BOOTSTRAP and all schemas from M1.

MISSION:
Build the semantic extraction layer.

CORE RESPONSIBILITY SPLIT:

Python:
fetch / clean / normalize / chunk / hash / persistence

LLM:
understand / classify / extract / abstract / infer

Pydantic / JSON Schema:
validate LLM output

STEP 1 — INPUT

LLM must not receive:
- entire raw webpage
- unnecessary navigation
- repeated source metadata
- unrelated previous prompts
- entire historical conversation

LLM should receive:
- relevant metadata
- bounded chunks
- extraction task
- explicit schema
- source/chunk references

STEP 2 — EXTRACTION TYPES

Support at least:

Case extraction
Style extraction
Topic signal extraction

Future:
Analysis
Mapping
other knowledge objects

STEP 3 — FACT / INFERENCE

Each semantic result must distinguish:

documented_fact
source_claim
ai_inference
derived_pattern

Do not state AI inference as source fact.

STEP 4 — EVIDENCE

Every important extracted claim should retain:
- source_id
- document_id when applicable
- chunk_id when applicable
- source location where possible

Do not allow evidence to become an unbounded copied article.

STEP 5 — PYDANTIC VALIDATION

Implement deterministic validation between LLM output and persistence.

Pipeline:

LLM
→ parse JSON
→ Pydantic
→ JSON Schema / contract validation
→ normalize
→ persist

STEP 6 — SELF-CORRECTION

When validation fails:

attempt 1:
return concise validation errors to the LLM

attempt 2:
return remaining validation errors

After maximum correction attempts:
create extraction_failed artifact.

Never retry indefinitely.

STEP 7 — ERROR ARTIFACT

An extraction failure should preserve:
- source reference
- input artifact reference
- schema_version
- validation errors
- timestamp
- model/runtime metadata where appropriate
- recovery status

Do not discard the failed source.

STEP 8 — SEMANTIC QUALITY CHECKS

Check for:
- empty mandatory fields
- hallucinated URLs
- unsupported facts
- missing evidence
- duplicate case fields
- contradictory values
- oversized fields
- malformed arrays
- mixing style with article content
- mixing case with dissemination feedback

STEP 9 — CASE EXTRACTION

Extract:
- background
- problem
- actors
- events
- methods
- technology
- data
- process
- management
- values
- innovation
- results
- transferable patterns
- writing features
- evidence

STEP 10 — STYLE EXTRACTION

Extract:
- structure
- tone
- sentence patterns
- paragraph patterns
- title patterns
- opening patterns
- ending patterns
- narrative patterns
- communication characteristics

Do NOT copy the whole article.

STEP 11 — TOPIC EXTRACTION

Identify:
- topic signal
- source basis
- relevance
- novelty signals
- applicability
- evidence basis
- audience
- risks

STEP 12 — COST CONTROL

Do not use deep/high-cost reasoning for deterministic work.

Use economy mode for:
- routine extraction

Use standard/deep reasoning only when semantic complexity justifies it.

STEP 13 — OUTPUT

Every successful extraction should produce:
- structured artifact
- artifact_id
- schema_version
- source_ids
- evidence
- status
- hash

SUCCESS CRITERIA:
- LLM outputs are schema-constrained.
- Validation is deterministic.
- Fact and inference are separated.
- Evidence is traceable.
- Failed extraction is recoverable.
- No infinite correction loop exists.
```

---

# M5｜Search + Analysis + School Mapping

## 目标

让后续任务尽量从知识库和索引读取，而不是重新读取原文。

## 对应模块

`MODULE 11 + MODULE 16 + MODULE 17`

```text
=== MILESTONE M5 : SEARCH + ANALYSIS + SCHOOL PROFILE MAPPING ===

Read GLOBAL BOOTSTRAP.
Read all M1 schemas.
Read M2 storage/index architecture.
Read M3/M4 extraction outputs.

MISSION:
Build the retrieval-to-analysis pipeline.

CORE PRINCIPLE:

After initial extraction, downstream operations should use structured knowledge and indexed retrieval instead of repeatedly reading the raw article.

PIPELINE:

query
→ search
→ retrieve bounded fields
→ analyze
→ map to profile

STEP 1 — TWO-LEVEL SEARCH

Level 1:
broad retrieval

Use:
- FTS5
- metadata filters
- source/domain
- date
- type
- tags if available

Level 2:
targeted field retrieval

Retrieve only needed fields such as:
- methods
- results
- transferable_patterns
- risks
- evidence
- writing_features

Do not return entire libraries by default.

STEP 2 — RESULT LIMITS

Every search operation must have explicit:
- limit
- offset/cursor if relevant
- maximum character/token budget

Never allow:
search case library
→ return entire case library

STEP 3 — CASE SEARCH

Support queries such as:
- similar cases
- same problem
- same target population
- similar intervention
- same technology
- same management mechanism
- similar outcomes

Results should be ranked/relevant without claiming a political or subjective "best" result.

STEP 4 — STYLE SEARCH

Style retrieval must return structured style records.

Do not dump whole articles.

STEP 5 — ANALYSIS

Analysis must use selected case IDs and evidence.

Create:
- analysis_id
- input_case_ids
- topic
- patterns
- comparisons
- transferable_methods
- risks
- recommendations
- evidence

Keep descriptive facts separate from analytical conclusions.

STEP 6 — COMPARISON

Compare cases using structured dimensions:
- problem
- target
- mechanism
- resources
- implementation
- technology
- governance
- outcome
- evidence
- constraints

Avoid unsupported generalization.

STEP 7 — SCHOOL PROFILE

Load bounded school profile data.

Profile should be explicit and structured.

Do not infer sensitive facts about the school from thin evidence.

STEP 8 — MAPPING

Generate mapping:

matching_points
differences
adaptation_requirements
transferable_elements
non_transferable_elements
risks

Do not assume that successful external case = directly applicable local solution.

STEP 9 — EVIDENCE

Every significant mapping claim should point to:
- case evidence
- profile field
- source artifact

STEP 10 — TOKEN CONTROL

Do not send:
- entire case library
- entire style library
- whole SQLite table
- complete source documents

Use field retrieval.

STEP 11 — CACHE

Cache reusable analysis inputs/results when:
- input IDs
- query
- relevant schema/version
- model mode

are stable enough to produce a reusable result.

STEP 12 — OUTPUT

Return:
- analysis artifact
- mapping artifact
- artifact references
- evidence references
- schema versions
- status

SUCCESS CRITERIA:
- Search is bounded.
- Search does not dump full libraries.
- Analysis uses structured knowledge.
- Mapping uses explicit profile/case references.
- Source evidence remains traceable.
```

---

# M6｜Writing + Audit + Output

## 目标

把“研究 → 写作”变成白名单驱动的知识生成，而不是重新搜索和自由发挥。

## 对应模块

`MODULE 18 + MODULE 19 + MODULE 20`

```text
=== MILESTONE M6 : WRITING + AUDIT + OUTPUT ===

Read GLOBAL BOOTSTRAP.
Read:
- source/document/chunk schemas
- case/style/topic/analysis/mapping/profile/audit schemas
- writing templates
- review templates
- project references

MISSION:
Build the generation, audit, and final output layer.

CORE PRINCIPLE:

Writing should consume approved structured knowledge.

Writing must NOT independently "rediscover" facts from the web.

STEP 1 — WRITING INPUT WHITELIST

A writing task may receive only explicitly approved sources such as:
- topic artifact
- case artifact
- analysis artifact
- mapping artifact
- style artifact
- school profile
- approved evidence

Define a whitelist.

Reject or flag undeclared content sources.

STEP 2 — CONTENT LINEAGE

Every major factual claim in a generated document should be traceable to:
- case_id
- source_id
- analysis_id
- mapping_id
or approved reference

STEP 3 — WRITING MODES

Support at least:
- article
- report
- outline
- topic proposal

Use existing templates where available.

Do not replace templates merely for stylistic preference.

STEP 4 — STYLE APPLICATION

Style library should control:
- structure
- tone
- sentence rhythm
- paragraph organization
- title pattern
- opening pattern
- ending pattern
- narrative/communication technique

Style must not inject unsupported facts.

STEP 5 — FACT SAFETY

Generated claims must distinguish:
- documented fact
- attributed source claim
- analysis
- inference
- recommendation

No fabricated:
- statistics
- quotations
- institutions
- dates
- outcomes
- source references

STEP 6 — AUDIT

Run independent checks for:

Fact check
- source support
- contradictions
- unsupported claims

Style check
- requested structure
- style constraints
- tone
- unnecessary copying

Format check
- headings
- length
- template requirements

Risk check
- privacy
- personal information
- inappropriate disclosure
- unsupported sensitive claims

STEP 7 — AUDIT OUTPUT

Produce audit artifact:

- audit_id
- draft_id
- issues
- fact_check
- style_check
- format_check
- risk_check
- passed

STEP 8 — ITERATION

When audit finds repairable issues:
- identify exact issue
- repair minimal scope
- rerun affected checks

Do not regenerate the entire document unnecessarily.

STEP 9 — FINAL OUTPUT

Generate:
- final document
- output metadata
- lineage references
- audit reference

Keep the raw research artifacts separate.

STEP 10 — DISSEMINATION

Preserve existing dissemination-related capabilities where present.

Do not mix dissemination feedback into the immutable core Case object.

Use separate effect/feedback/retro structures where needed.

SUCCESS CRITERIA:
- Writing consumes approved knowledge.
- Every important factual claim is traceable.
- Audit is machine-readable.
- Repairs are localized.
- Final output is separate from raw research.
```

---

# M7｜Backup + Recovery + Garbage Collection

## 目标

建立可恢复、可清理、可长期运行的数据生命周期。

## 对应模块

`MODULE 14 + MODULE 15`

```text
=== MILESTONE M7 : BACKUP + RECOVERY + RETENTION + GC ===

Read GLOBAL BOOTSTRAP and storage architecture.

MISSION:
Implement safe lifecycle management for artifacts, cache, indexes, and backups.

STEP 1 — BACKUP PRINCIPLE

Do not create a full backup on every startup.

Backup before:
- major schema migration
- major write operation
- bulk deletion
- bulk transformation
- manual backup request

STEP 2 — VERSIONED SNAPSHOTS

Use versioned snapshots.

Recommended retention:
5–10 recent snapshots

Do not overwrite the only known good backup.

STEP 3 — RECOVERY

Recovery should be possible for:
- artifact files
- registry
- knowledge files
- configuration
- rebuildable index

SQLite index must be rebuildable from canonical content whenever practical.

STEP 4 — RETENTION

Every artifact/cache class should have:
- default retention
- permanent/explicit retention option
- expiration condition

STEP 5 — RAW CACHE

Default raw HTML TTL:
24–72 hours

Retain longer when:
- recovery requires it
- user explicitly marked it permanent
- task is incomplete
- artifact is referenced by an active task

STEP 6 — GC

GC should consider:
- TTL
- status
- last_accessed
- reference count
- retention
- active tasks
- lineage

Never delete an artifact that is still referenced by a live artifact/task.

STEP 7 — DRY RUN

Provide GC dry-run mode.

Dry run must show:
- candidate path
- reason
- age
- references
- predicted action

STEP 8 — REFERENCE CHECK

Before deletion:
- search Artifact Registry
- inspect parent/child relationships
- inspect active tasks
- inspect explicit retention

STEP 9 — PARTIAL FAILURE

GC must be resumable and should not leave the system in an unrecoverable state if one deletion fails.

STEP 10 — DOCUMENTATION

Create:

docs/data-lifecycle.md

Include:
- retention matrix
- backup policy
- recovery workflow
- GC rules
- disaster scenarios

SUCCESS CRITERIA:
- Backups are versioned.
- Recovery is documented/tested.
- GC is reference-aware.
- Dry-run exists.
- Active/referenced artifacts are protected.
```

---

# M8｜Hardening + Compatibility + Regression

## 目标

把“能运行”推进到“跨 Agent 可复现、旧能力不回退、异常可诊断”。

## 对应模块

`MODULE 22 + MODULE 23 + MODULE 24`

```text
=== MILESTONE M8 : HARDENING + CODEX/CLAUDE COMPATIBILITY + REGRESSION ===

Read GLOBAL BOOTSTRAP and all previous milestone documentation.

MISSION:
Perform hardening, cross-agent compatibility validation, and regression testing.

STEP 1 — BUG / HARDENING AUDIT

Inspect for:

- silent failures
- swallowed exceptions
- broad catch blocks
- missing input validation
- path traversal risk
- unsafe writes
- race conditions
- inconsistent encodings
- non-deterministic outputs where determinism is expected
- unbounded loops
- infinite retries
- huge stdout
- full-library loads
- accidental context duplication
- schema drift
- version drift
- duplicate data stores
- unbounded cache
- missing cleanup
- stale indexes

STEP 2 — TOKEN HARDENING

Search for code paths that:
- print complete documents
- return huge JSON
- load all records
- concatenate entire libraries
- embed unnecessary reference documents
- duplicate prompts
- repeat identical source text

Replace with:
- pointers
- bounded retrieval
- field-level retrieval
- chunk references
- summaries where appropriate

STEP 3 — CODEX COMPATIBILITY

Validate:
- commands are clear
- paths are portable
- execution does not assume Claude-specific behavior
- no hidden dependency on another Agent's memory
- scripts can be invoked from repository context
- outputs are deterministic and inspectable

STEP 4 — CLAUDE CODE COMPATIBILITY

Validate:
- same business logic is callable
- instructions do not depend on Claude-only syntax
- no mandatory proprietary agent feature is embedded
- CLI interfaces are explicit
- file-based state remains authoritative

STEP 5 — SHARED CORE

Verify:
Core behavior is in:
- Python
- JSON schemas
- SQLite
- files
- deterministic configuration

Agent layer should only orchestrate.

STEP 6 — REGRESSION MATRIX

Test historical capabilities recovered in M0.

At minimum examine:
- hotlist fetching
- media research
- media style research
- user-provided fallback
- topic selection
- writing
- ideological review
- privacy review
- punctuation check
- dissemination retro
- profile handling
- case library
- style library
- routing
- templates

For each:
- old behavior
- current behavior
- regression
- intentional change
- test result

STEP 7 — LEGACY WRAPPER CHECK

Where old scripts remain:
- preserve CLI compatibility where practical
- convert them into thin wrappers if appropriate
- avoid maintaining duplicate business logic

STEP 8 — RETIREMENT

Only retire:
- confirmed dead features
- confirmed impossible placeholders
- explicitly removed dependencies

Document each retirement.

STEP 9 — ERROR DIAGNOSTICS

Failures should identify:
- task_id
- artifact_id where available
- stage
- error type
- retry state
- recoverability

STEP 10 — TEST REPORT

Create:

docs/regression-report.md
docs/compatibility-report.md
docs/hardening-report.md

SUCCESS CRITERIA:
- Old useful capabilities are covered.
- New architecture does not silently regress them.
- Hardware/encoding issues are handled.
- Codex and Claude Code can use the same core.
- Large-output/token hazards are reduced.
- Failures are diagnosable.
```

---

# M9｜Token Audit + Integration + Release Readiness

## 目标

最终把整个 Skill 收敛成一个低 Token、低重复、可恢复、可持续扩展的 Agent Knowledge Pipeline。

## 对应模块

`MODULE 21 + MODULE 25 + MODULE 26`

```text
=== MILESTONE M9 : FINAL TOKEN AUDIT + INTEGRATION + RELEASE READINESS ===

Read GLOBAL BOOTSTRAP and all previous milestone reports.

MISSION:
Integrate the entire pipeline and determine release readiness.

TARGET PIPELINE:

User Request
→ Routing
→ Task
→ Access
→ Fetch
→ Raw Artifact
→ Processed Document
→ Chunk
→ LLM Extraction
→ Validation
→ Knowledge
→ SQLite/FTS5 Index
→ Search
→ Analysis
→ School Mapping
→ Topic
→ Writing
→ Audit
→ Output
→ Dissemination Retro
→ Knowledge Persistence

STEP 1 — END-TO-END INTEGRATION

Verify that artifact references flow correctly across stages:

task_id
artifact_id
source_id
document_id
chunk_id
case_id
style_id
topic_id
analysis_id
mapping_id
draft_id
audit_id

No stage should require hidden conversational memory.

STEP 2 — POINTER-FIRST HANDOFF

Verify that inter-stage communication uses:

- IDs
- paths
- hashes
- schema versions
- status
- concise summaries

rather than:
- entire documents
- full analysis transcripts
- complete libraries
- repeated prompts

STEP 3 — TOKEN AUDIT

Identify expensive context paths.

Calculate or estimate:
- fetch input to Agent
- extraction input
- search input
- analysis input
- writing input
- audit input

Compare:
BEFORE architecture
vs
AFTER architecture

Flag any path that unnecessarily passes:
- full webpages
- entire case library
- entire style library
- redundant references
- repeated historical prompts

STEP 4 — MODEL POLICY

Establish:

ECONOMY:
- fetching
- parsing
- deterministic extraction
- classification
- validation
- search
- indexing
- metadata operations

STANDARD:
- routine semantic extraction
- case abstraction
- topic extraction
- normal analysis

DEEP:
- difficult synthesis
- complex comparative analysis
- high-quality final writing when justified
- difficult review

Do not force expensive reasoning onto deterministic tasks.

STEP 5 — INTEGRATION RULES

Verify:
- same normalized URL does not trigger redundant work
- same content can be deduplicated
- failed extraction can resume
- index can rebuild
- cache can invalidate
- stale cache can be collected
- schemas are versioned
- artifacts retain lineage
- old scripts do not maintain duplicate logic

STEP 6 — RELEASE CHECK

Verify:

Architecture
[ ] clear layer boundaries
[ ] Agent/runtime separation
[ ] deterministic core

Data
[ ] schemas frozen
[ ] versioning present
[ ] fact/inference separation
[ ] lineage

Storage
[ ] file store
[ ] artifact registry
[ ] SQLite/FTS5
[ ] cache

Web
[ ] compliant public access
[ ] finite retries
[ ] structured fetch output
[ ] fallback

LLM
[ ] bounded input
[ ] structured output
[ ] Pydantic validation
[ ] maximum correction retries
[ ] extraction failure recovery

Search
[ ] bounded
[ ] field-level
[ ] no full-library dumping

Analysis
[ ] evidence-based
[ ] separate from source data
[ ] profile mapping

Writing
[ ] whitelist inputs
[ ] traceable facts
[ ] audit

Lifecycle
[ ] backup
[ ] recovery
[ ] GC
[ ] TTL

Compatibility
[ ] Codex
[ ] Claude Code
[ ] no platform-specific core dependency

Regression
[ ] historical feature matrix
[ ] tests
[ ] known limitations

STEP 7 — DOCUMENTATION

Finalize:

README.md
AGENTS.md
CHANGELOG.md

and:

docs/
- architecture.md
- data-contract.md
- storage-architecture.md
- data-lifecycle.md
- regression-report.md
- compatibility-report.md
- release-readiness.md

STEP 8 — RELEASE VERDICT FORMAT

Do not merely say "done".

Report:

A. Completed
B. Partially completed
C. Known limitations
D. Intentional feature changes
E. Remaining technical debt
F. Regression risks
G. Token-cost improvements
H. Recommended next engineering tasks

Do not claim zero regression risk.

SUCCESS CRITERIA:
The Skill is structurally ready for real-world iterative use and future expansion without requiring a complete architectural rewrite.
```

---

# Milestone Execution Protocol

每次启动一个 Milestone 时，建议使用下面这一段作为固定尾部：

```text
=== MILESTONE EXECUTION PROTOCOL ===

Before editing:
1. Read the repository.
2. Read the current milestone's required docs.
3. Inspect existing implementation.
4. Confirm which files are in scope.
5. Identify dependencies and regression-sensitive paths.

During editing:
1. Make the smallest coherent change.
2. Reuse existing functionality where possible.
3. Do not duplicate logic.
4. Do not silently delete existing capability.
5. Keep schemas and deterministic logic outside the LLM.
6. Keep large data outside Agent context.
7. Preserve source/evidence lineage.
8. Keep failures explicit and recoverable.

After editing:
1. Run relevant tests.
2. Inspect generated files.
3. Validate schemas.
4. Check for syntax/import errors.
5. Check for unintended large stdout/context.
6. Check for accidental duplicate data stores.
7. Check file references and paths.
8. Update documentation.
9. Produce a milestone report.

MILESTONE REPORT FORMAT:

Milestone:
Status:

Changed:
- ...

Added:
- ...

Preserved:
- ...

Retired:
- ...

Tests:
- ...

Schema changes:
- ...

Data migration:
- ...

Token/context changes:
- ...

Known issues:
- ...

Regression risks:
- ...

Next milestone readiness:
- READY / PARTIAL / BLOCKED

Do not proceed into the next milestone automatically.
Stop after this milestone and leave the repository in a coherent state.
```

---

# Milestone Dependency Graph

```text
M0
│
├── Architecture Audit
└── Historical Feature Recovery
        │
        ▼
M1
│
├── Data Contract
├── JSON Boundary
└── Responsibility Boundary
        │
        ▼
M2
│
├── Artifact
├── SQLite / FTS5
└── Cache
        │
        ▼
M3
│
├── Web Fetch
├── Compliance
├── Cleaning
└── Chunking
        │
        ▼
M4
│
└── LLM Extraction
    ├── Pydantic
    ├── Evidence
    └── Self-Correction
        │
        ▼
M5
│
├── Search
├── Analysis
└── School Mapping
        │
        ▼
M6
│
├── Writing
├── Audit
└── Output
        │
        ▼
M7
│
├── Backup
├── Recovery
└── Garbage Collection
        │
        ▼
M8
│
├── Hardening
├── Codex Compatibility
├── Claude Code Compatibility
└── Regression
        │
        ▼
M9
│
├── Token Audit
├── Integration
└── Release Readiness
```

---

# Recommended Invocation Order

## 第一次：完全无上下文的 Agent

```text
Read and follow the GLOBAL BOOTSTRAP.

Execute ONLY M0.

Do not modify production runtime behavior.

Inspect the entire repository and recover historical feature requirements.

Produce the M0 audit documents and milestone report.

Do not execute M1 or any later milestone.
```

## 第二次：开始冻结数据契约

```text
Read and follow the GLOBAL BOOTSTRAP.

Read the current repository and all M0 documentation.

Execute ONLY M1.

Freeze the project data contract and JSON boundaries.

Do not build M2+ runtime infrastructure yet.

Run schema/fixture validation and produce the M1 report.

Do not execute M2 automatically.
```

## 后续

每一个 Milestone 都可以使用完全相同的方式：

```text
Read GLOBAL BOOTSTRAP.
Read the current repository.
Read the previous milestone report.
Execute ONLY M[N].
Run tests.
Write the milestone report.
Stop.
```

---

# Important Operating Rule

```text
The previous chat is NOT part of the runtime contract.

The repository is.

Therefore:

Previous conversation
        ↓
historical requirement only

Current repository
        ↓
current implementation truth

Current milestone prompt
        ↓
current execution authority

Schemas / artifacts / task state
        ↓
persistent machine-readable state
```

这意味着后续你即使把 **Codex 换成 Claude Code、Claude Code 换成 Codex，或者重新开一个全新的 Session**，也不应该依赖“AI 记得之前做过什么”。

真正跨 Agent、跨 Session 保留下来的应该是：

```text
docs/
data/schemas/
data/registry/
data/knowledge/
data/artifacts/
data/index.db
cache/
task state
CHANGELOG
milestone reports
```

而不是聊天记录。

---

# Final Milestone Pack Mapping

```text
M0 = MODULE 00 + MODULE 27
    架构审计 + 历史能力恢复

M1 = MODULE 03 + MODULE 04 + MODULE 28
    Data Contract + JSON Boundary + Responsibility Boundary

M2 = MODULE 13 + MODULE 10 + MODULE 12
    Artifact + SQLite/FTS5 + Cache

M3 = MODULE 05 + MODULE 06 + MODULE 07 + MODULE 08
    Web Fetch + Compliance + Preprocess + Chunking

M4 = MODULE 09
    LLM Extraction + Pydantic Validation + Self-Correction

M5 = MODULE 11 + MODULE 16 + MODULE 17
    Search + Analysis + School Mapping

M6 = MODULE 18 + MODULE 19 + MODULE 20
    Writing + Audit + Output

M7 = MODULE 14 + MODULE 15
    Backup + Recovery + Retention + GC

M8 = MODULE 22 + MODULE 23 + MODULE 24
    Hardening + Cross-Agent Compatibility + Regression

M9 = MODULE 21 + MODULE 25 + MODULE 26
    Token Audit + Integration + Release Readiness
```

---

# One-Line Rule

```text
先冻结数据契约，再建立持久化与缓存，
然后把网页处理成结构化知识，
再让 LLM 做语义理解，
最后由搜索、分析、映射、写作、审计组成上层能力。
```

---

# 附录：与旧 22 步计划的映射（仓库内历史协议）

本 pack 取代旧的 22 步实施计划（原权威方案历史文件见本机 `.claude/plans/` 目录）。映射关系见 `docs/migration-plan.md` 第 0 节：

| 本 pack 里程碑 | 旧 22 步 |
|---|---|
| M0 | 审计（已完成） |
| M1 | 步骤 1-2（Schema + Pydantic） |
| M2 | 步骤 3-6（Artifact + SQLite + Cache + URL 归一） |
| M3 | 步骤 7-9（Fetcher + Preprocess + Chunk） |
| M4 | 步骤 10（Extraction） |
| M5 | 步骤 11-13（Search + Analysis + Mapping） |
| M6 | 步骤 14-16（Writing + Audit + Output） |
| M7 | 步骤 17-19（Task/Resume + Backup + GC） |
| M8 | 步骤 20-21（跨平台 + 回归） |
| M9 | 步骤 22（最终集成 + Token 审计终版） |

pack 相对旧计划的三处规格修正（已采纳）：

1. SQLite 规范路径 = `data/index.db`（旧计划为 `data/index/kb.db`）。
2. Cache 命名空间 = `raw / processed / extraction / tasks`（旧计划第 4 层为 `temporary`）。
3. M1 交付物增加：fixtures（正/反/最小/边界四类）、`docs/data-contract.md`、历史能力恢复矩阵。
