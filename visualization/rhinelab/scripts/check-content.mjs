import assert from "node:assert/strict";
import { test } from "node:test";
import { loadContent, validateContent } from "./archive-content.mjs";

test("accepts exported archives and empty categories", async () => {
  const content = await loadContent();
  assert.equal(content.categories.length, 5);
  assert.doesNotThrow(() => validateContent({ ...content, records: [] }));
});

test("accepts more than eight archives in one category", async () => {
  const content = await loadContent();
  const record = {
    id: "X-001", title: "测试档案", en: "TEST", department: "—",
    category: content.categories[0], date: "—", lead: "—",
    clearance: "—", abstract: "测试资料", source: "https://example.org/",
    findings: ["测试内容"],
  };
  const records = Array.from({ length: 42 }, (_, i) => ({
    ...record,
    id: `X-${String(i + 1).padStart(3, "0")}`,
  }));
  assert.doesNotThrow(() => validateContent({ ...content, records }));
  assert.throws(() => validateContent({ ...content, records: null }), /必须是数组/);
});
