"""Slash Command Layer 统一命令契约（宿主无关）。

定义 /cw-* 命令的名称 / 用途 / 输入 / 路由 / 输出 / 实现状态，是命令层的
单一真相源。宿主适配层（Claude Code ~/.claude/commands/cw-*.md、Codex skill
参数路由等）只做薄层参数接收与呈现，核心路由与约束在此维护，避免宿主间漂移。

规则（对应工程任务）：
- routing 只引用真实存在的 kb.py / cw.py 子命令，禁止引用空壳；
- status 如实标注 implemented / partial / 未实现的能力；
- 输出结构区分 success / partial / 待用户处理 / blocked / failed；
- 未实现的能力必须在 notes 明确写出，不得宣称已实现。
"""
from __future__ import annotations

from typing import Any, Dict, List

# 统一结果语义：任何命令的输出都应能归入这五态之一（与现有退出码/门禁语义一致）
RESULT_STATES = ("success", "partial", "waiting", "blocked", "failed")

_COMMON = {
    "cw-hot": {
        "name": "/cw-hot",
        "purpose": "抓取多源热点（微博/tophub）、标准化、跨平台去重、内容风险筛查、审核状态持久化",
        "inputs": ["自然语言：时间范围 / 平台 / 主题 / 目标读者（可省略，用项目默认 weibo+tophub）"],
        "routing": [
            "python scripts/cw.py hot --sources weibo,tophub --top 20 --save [--llm-cmd CMD | --prompt-only | --result FILE]",
            "core.hotspots.fetch_hotspots 逐源抓取 + 状态聚合 + 跨平台去重（复用 core.hotlist）",
            "core.hotreview.review_hotspots 内容风险筛查：确定性规则 + 批量语义复核 → 审核结果落 hotlist_review artifact",
        ],
        "outputs": ["带审核状态的清单（每条：hot-id/标题/逐源 rank/heat/url + fetch_status + review_status + risk_flags + review_reason）",
                    "状态总览（raw_count/dedup_count/screened_count/approved/caution/needs_review/rejected/unscreened/fetch_failed）",
                    "逐源抓取状态（success/blocked/access_restricted/unavailable/timeout/rate_limited/parse_failed）",
                    "hotlist 快照（原始）+ hotlist_review 审核结果（独立、可追溯，review_id 稳定）"],
        "status": "implemented",
        "notes": "抓取成功≠审核通过；未执行筛查的记录标 needs_review，绝不标 approved；任何记录不得仅因热度高自动通过；未筛查数据不得混入推荐",
    },
    "cw-research": {
        "name": "/cw-research",
        "purpose": "资料研究与证据整理：主题 / URL / 热点标识 / 已有 artifact → 来源 + 证据",
        "inputs": ["主题（自然语言）", "URL", "热点序号 / hot-id", "已有 artifact id"],
        "routing": [
            "热点引用解析：python scripts/cw.py hot resolve <序号|hot-id>（过期/歧义/不存在明确报错；返回含审核状态）",
            "URL → python scripts/kb.py fetch <url>；文本/文件 → kb.py ingest --file/-；已有 → kb.py artifact get",
            "结构化提取：kb.py extract <doc> --extractor topic_signal|case_facts（三模式 LLM）",
            "热点审核绑定：python scripts/kb.py hot link --topic <topic_id> --hot <序号|hot-id>（把 review_status 写入 TopicRecord；写作门禁据此放行/拒绝，不把标题/热度/二手转述当已核实事实）",
            "证据整理：区分 来源存在 / 可访问 / 摘录可定位 / 证据支持断言；标记二手/冲突/不足/无法访问",
        ],
        "outputs": ["SourceRecord + Document + TopicRecord/CaseRecord + EvidenceRecord（Python 校验落盘）",
                    "事实断言 → 证据映射（documented_fact/source_claim 必带 evidence_ids）"],
        "status": "implemented",
        "notes": "证据摘录 ≤500 字符；二手转述/来源冲突/证据不足/无法访问须显式标记；不得为取数无限重试；热点审核未通过的条目不得无条件升级为确定事实",
    },
    "cw-write": {
        "name": "/cw-write",
        "purpose": "一站式文章创作：按输入自动决定热点→研究→选题→写作→审核的最小流水线",
        "inputs": ["自然语言话题", "热点序号 / hot-id", "链接", "素材文件", "已有任务标识"],
        "routing": [
            "路由判断（Case-based vs Generic）：docs/integration/agent-integration.md",
            "案例：kb.py extract case_facts → kb.py mapping --case → kb.py write --mapping",
            "通用：kb.py extract topic_signal → （热点来源须 kb.py hot link 绑定）→ kb.py write --topic [--mode …]",
            "收口：kb.py punctuation + kb.py deai + kb.py audit --draft + kb.py output render --draft --audit",
        ],
        "outputs": ["DraftRecord → AuditRecord → FINAL artifact（真实文件路径 + 统计 + 血缘）"],
        "status": "implemented",
        "notes": "写作是一次 LLM 调用产出整稿，非「边写边审」；未明确要求时不做全流水线；未核实事实不得升级为确定陈述；可修复质量问题有限次修正，不无限循环；热点来源选题未通过审核（needs_review/rejected/缺失）会被 kb.py write 拒绝，不得把未筛查热点扩写成确定新闻",
    },
    "cw-audit": {
        "name": "/cw-audit",
        "purpose": "独立文章审核：对 draft 或文章文件做七项思政自查 + 确定性门禁",
        "inputs": ["draft id", "文章文件路径", "已有 audit/draft artifact"],
        "routing": [
            "draft → python scripts/kb.py audit --draft <id>（七项 LLM 自查 + 标点/去AI 门禁）",
            "文章文件 → kb.py punctuation --lang zh <file> + kb.py deai <file>（确定性门禁，零 LLM）",
            "七项清单唯一规则源：references/ideological-review.md",
        ],
        "outputs": ["AuditRecord（verdict 由 Python 硬规则计算，不信任模型自报）",
                    "严格区分 作者自查 / 模型审核 / 确定性程序门禁 / 独立证据核验",
                    "标题/正文/声明一致性检查（格式项 format）"],
        "status": "implemented",
        "notes": "对独立文章文件仅确定性门禁可直接跑；七项结构化 AuditRecord 需要 draft（kb.py audit 的契约输入）；缺检查项不得通过（八项 LLM 自查必须齐全且唯一）",
    },
    "cw-topic": {
        "name": "/cw-topic",
        "purpose": "选题分析：从热点/研究/素材/想法生成选题卡（TopicRecord）",
        "inputs": ["热点引用", "研究产物（document/source id）", "素材文件", "自然语言想法"],
        "routing": [
            "kb.py extract <doc> --extractor topic_signal（五角度 + 标题三式 + 风险）",
            "kb.py search topic <kw>（检索已有选题，避免重复）",
            "热点来源选题须 kb.py hot link 绑定审核状态；优先 approved 候选；caution 候选须把限制写入 TopicRecord.risks；needs_review/rejected 不得无条件提升",
            "选题规则源：references/topic-selection.md",
        ],
        "outputs": ["TopicRecord（推荐理由/读者痛点/备选角度/标题方向/证据缺口/风险约束）"],
        "status": "implemented",
        "notes": "选题评分依据必须来自真实素材/风险规则，不得伪造评分依据；上游审核状态经 hot_id/hot_review_status/hot_review_id 持久化传播，不得依赖聊天上下文",
    },
    "cw-revise": {
        "name": "/cw-revise",
        "purpose": "文章修订：按修改目标（压缩/文风/逻辑/降 AI 痕迹）修订，保留事实与有效信息",
        "inputs": ["修改目标", "文章文件 / draft id"],
        "routing": [
            "机械项：kb.py deai --fix + kb.py punctuation --fix（确定性，零 LLM）",
            "语义项：LLM 读 draft 全文做有界修订（保留事实依据，不无条件重写全文）",
            "事实/结构变化 → 重新 kb.py audit --draft（按影响范围）；变更的事实须重新核验证据，不得复用旧审核结论",
        ],
        "outputs": ["修订后正文 + 修改摘要/差异"],
        "status": "partial",
        "notes": "无独立 revise CLI；语义修订为 LLM 编排，机械降 AI/标点复用确定性门禁；事实变化必须重审，不得直接沿用原 AuditRecord",
    },
    "cw-library": {
        "name": "/cw-library",
        "purpose": "知识库操作：检索/复用/入库 案例、风格、画像、来源、选题、历史成果",
        "inputs": ["检索关键词", "入库内容（案例/风格/画像字段）"],
        "routing": [
            "检索：kb.py search case|style|topic <kw> [--get id]（L1 紧凑 / L2 显式）",
            "入库：kb.py case add（stdin） / kb.py style add / kb.py profile set",
        ],
        "outputs": ["L1 紧凑投影 / L2 完整字段；入库走 Pydantic 校验 + 幂等 upsert",
                    "区分 原始素材 / 待核实 / 已验证证据 / 可复用案例（真实审核状态不丢失）"],
        "status": "implemented",
        "notes": "入库必须走现有校验与持久化；一般查询按需检索，不批量加载整库；不得把待核实素材当已验证证据入库",
    },
    "cw-export": {
        "name": "/cw-export",
        "purpose": "成果导出：从已完成 artifact 生成 Markdown 等既有格式",
        "inputs": ["draft id", "audit id（可选，写入血缘）", "目标格式"],
        "routing": [
            "python scripts/kb.py output render --draft <id> [--audit <audit_id>]（零 LLM）",
        ],
        "outputs": ["FINAL Markdown artifact（路径/文件名/正文哈希/版本/统计取自最终文件）"],
        "status": "partial",
        "notes": "仅 Markdown 已有能力；Word 等其它格式未实现；区分草稿/待定/终稿由 artifact_type 承载；审核不完整（无有效 AuditRecord）须标注，不得伪装终稿",
    },
    "cw-status": {
        "name": "/cw-status",
        "purpose": "查询任务真实进度、产物、失败原因、缓存命中与待办",
        "inputs": ["任务 id（可选，默认最近）"],
        "routing": [
            "kb.py task status <id> + kb.py task events <id> + kb.py cache status + kb.py artifact list",
        ],
        "outputs": ["状态 + context_refs + 输出引用 + 事件日志 + 缓存统计"],
        "status": "implemented",
        "notes": "只报真实状态；缓存命中须重新确认审核状态/版本/依赖仍有效，仅显示 HIT 不算验证；TaskManager 是独立手动状态机（kb.py task），ingest/extract/write/audit 不自动创建/推进任务",
    },
    "cw-continue": {
        "name": "/cw-continue",
        "purpose": "恢复未完成任务，复用已验证仍有效的结果，从可恢复步骤继续",
        "inputs": ["任务 id"],
        "routing": [
            "kb.py task resume <id>（读 status + context_refs 恢复锚点）",
            "按 context_refs 复用已落盘 artifact / knowledge，跳过已完成步骤",
        ],
        "outputs": ["恢复锚点 + 下一步建议"],
        "status": "implemented",
        "notes": "不得重复执行全部工作流，不得使用已失效中间产物（各模块缓存幂等 + TTL 保证）；恢复时须重新确认审核状态/版本/依赖，不得在缓存命中时跳过必要审核；TaskManager 是独立手动状态机（kb.py task resume 读锚点），主流程不自动推进",
    },
    "cw-help": {
        "name": "/cw-help",
        "purpose": "说明现有命令、用途、输入示例与重要限制",
        "inputs": ["（无）"],
        "routing": ["渲染本模块 COMMAND_SPECS"],
        "outputs": ["命令清单 + 输入示例 + 限制"],
        "status": "implemented",
        "notes": "只列实际可调用的命令，不列空壳",
    },
}


def specs() -> Dict[str, Dict[str, Any]]:
    """命令契约（只读访问；数据来自本模块 _COMMON，避免外部变异）。"""
    return {k: dict(v) for k, v in _COMMON.items()}


def command_names() -> List[str]:
    return list(_COMMON.keys())


def get_spec(name: str) -> Dict[str, Any]:
    """按命令名取契约（/cw-hot 或 cw-hot 均可）。"""
    key = name.lstrip("/")
    if key not in _COMMON:
        raise ValueError(f"未知命令 {name!r}（可用：{', '.join(_COMMON)}）")
    return dict(_COMMON[key])


def render_help() -> str:
    """把契约渲染为 Markdown 帮助文本（供 /cw-help 与 commands/README 共用）。"""
    lines: List[str] = ["# Counselors-Writing Slash Commands", ""]
    for key in _COMMON:
        s = _COMMON[key]
        lines.append(f"## {s['name']} — {s['purpose']}")
        lines.append(f"- 状态：{s['status']}")
        lines.append(f"- 输入：{'；'.join(s['inputs'])}")
        lines.append("- 路由：")
        for r in s["routing"]:
            lines.append(f"  - `{r}`")
        lines.append(f"- 输出：{'；'.join(s['outputs'])}")
        if s["notes"]:
            lines.append(f"- 限制：{s['notes']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
