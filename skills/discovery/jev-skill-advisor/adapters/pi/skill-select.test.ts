import assert from "node:assert/strict";
import test from "node:test";

import { capabilityCorrelation, contentFreeCapabilityFields, formatCapabilityHint } from "./skill-select.ts";

const HASH = "ab".repeat(32);
const SKILL = "warehouse:notion";

function cards(count: number, extra: Record<string, unknown> = {}) {
  return Array.from({ length: count }, (_, index) => ({
    id: index === 0 ? "notion.mcp.fetch" : `notion.mcp.tool${index}`,
    description: index === 0 ? "CAPABILITY_SENTINEL" : `CARD_${index}`,
    ...extra,
  }));
}

function payload(count: number, extra: Record<string, unknown> = {}) {
  const shown = cards(count, extra);
  return {
    advisory: true,
    authorizes_calls: false,
    status: "selected",
    reason: "capability_choice_selected",
    manifest_hash: HASH,
    skill_id: SKILL,
    cards: shown,
    correlation: capabilityCorrelation(HASH, SKILL, shown.map((card) => card.id), "selected"),
  };
}

test("correlation matches the python canonical vector", () => {
  assert.equal(
    capabilityCorrelation(HASH, SKILL, ["notion.mcp.fetch"], "selected"),
    "9607ea6dbb78580be5234954cf534e0613528df0cd043951baf1ef5c40dcf22a",
  );
});

test("emitted context is one advisory hint and the receipt omits the description", () => {
  const value = payload(1, { inputSchema: { type: "object", properties: { page_id: {} } } });
  const hint = formatCapabilityHint(value, SKILL);
  assert.match(hint, /<capability-hint advisory="true" authorizes-calls="false"/);
  assert.match(hint, /notion\.mcp\.fetch: CAPABILITY_SENTINEL/);
  assert.equal(hint.includes("inputSchema"), false);
  assert.equal(hint.includes("page_id"), false);
  const fields = contentFreeCapabilityFields(value, SKILL);
  assert.deepEqual(fields?.capability_ids, ["notion.mcp.fetch"]);
  assert.equal(fields?.capability_surface, "cli");
  assert.equal(JSON.stringify(fields).includes("CAPABILITY_SENTINEL"), false);
  assert.equal(JSON.stringify(fields).includes("inputSchema"), false);
});

test("forty-five cards and a bad digest add no capability context", () => {
  assert.equal(formatCapabilityHint(payload(45), SKILL), "");
  assert.equal(contentFreeCapabilityFields(payload(45), SKILL), undefined);
  const tampered = payload(1);
  tampered.correlation = "0".repeat(64);
  assert.equal(formatCapabilityHint(tampered, SKILL), "");
  const authorizing = payload(1);
  authorizing.authorizes_calls = true;
  assert.equal(formatCapabilityHint(authorizing, SKILL), "");
});
