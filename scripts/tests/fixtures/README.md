# Fixtures 说明

按 pack M1 STEP 16 组织的契约校验样例（正 / 反 / 最小 / 边界四类）。

- 目录 = 实体名；`valid.json` / `minimal.json` / `edge.json` 必须通过校验，`invalid-*.json` 必须失败。
- 边界用例（如 chunk 2000/2001 字符、时间戳缺时区）中，适合静态 JSON 的放在 `edge.json`，
  依赖程序化构造的放在 `scripts/tests/test_validate.py` 的动态用例里。
- 所有样例均为虚构/脱敏示例数据，不含任何真实学生信息。

| 实体 | valid | minimal | invalid | edge |
|---|---|---|---|---|
| task | ✓ | ✓ | invalid-status | ✓ |
| artifact | ✓ | ✓ | invalid-id / invalid-path-traversal | ✓ |
| source | ✓ | — | invalid-url | — |
| document | ✓ | ✓ | invalid-section-range | — |
| chunk | ✓ | — | invalid-sequence | — |
| case | ✓ | ✓ | invalid-fact-no-evidence / invalid-inference-no-basis | — |
| style | ✓ | ✓ | — | — |
| topic | ✓ | — | invalid-score | — |
| analysis | ✓ | — | invalid-empty-input-cases | — |
| mapping | ✓ | — | invalid-no-profile | — |
| profile | ✓ | — | invalid-extra-key | — |
| audit | ✓ | — | invalid-verdict-conflict | — |
| effect | ✓ | — | invalid-unknown-dimension | — |
| draft | ✓ | — | invalid-empty-sections | — |
| extraction | ✓ | — | invalid-attempts-over-limit | — |
| evidence | ✓ | — | invalid-no-ref | — |
