import assert from "node:assert/strict";
import { mkdtemp } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { HermesPickerAdapter, HERMES_PICKER_SLUG, explicitSkillsFromText, trustedSkillContext } from "../src/hermes-picker-adapter.mjs";

const TASK = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee";
function response() { return { headersSent: false, setHeader() {}, writeHead() { this.headersSent = true; }, write() {}, end() {} }; }
function payload(text) { return { model: HERMES_PICKER_SLUG, stream: false, input: [{ role: "user", content: [{ type: "input_text", text }] }] }; }

test("selected bodies reach first Hermes request and replay does not prepare twice", async () => {
  const dir = await mkdtemp(path.join(os.tmpdir(), "hermes-skills-"));
  const messages = []; const contexts = []; const creates = []; let preparations = 0;
  const client = {
    allowedSessions: new Set(),
    async request(_path, options) { creates.push(options.body); return { session: { id: "s1" } }; },
    async continueSession(id, message, options) { messages.push(message); contexts.push(options.skillContext); return { sessionId: id, content: "ok" }; },
  };
  const skillPreparer = async () => {
    preparations += 1;
    return { status: "suggested", receipt_id: "r1", selected_ids: ["warehouse:alpha"], body_bytes: 10,
      telemetry: { provider_attempts: 1, elapsed_ms: 12 }, fallback: null,
      skills: [{ id: "warehouse:alpha", body: "ALPHA_BODY", body_bytes: 10, content_hash: "a".repeat(64) }] };
  };
  const adapter = new HermesPickerAdapter({ client, skillPreparer, copyFile: null,
    env: { HERMES_PICKER_MAP_PATH: path.join(dir, "map.json"), HERMES_PICKER_ALLOW_CREATE: "true" } });
  const call = () => adapter.handleResponses({ request: { headers: { "thread-id": TASK, "x-request-id": "turn-1" } }, response: response(), payload: payload("ORIGINAL_TEXT") });
  await call(); const replay = await call();
  assert.equal(preparations, 1); assert.equal(messages.length, 1); assert.equal(replay.replayed, true);
  assert.match(messages[0], /ALPHA_BODY/); assert.match(messages[0], /ORIGINAL_TEXT/);
  assert.doesNotMatch(messages[0], /BETA_BODY|warehouse:beta/);
  assert.deepEqual(contexts[0].selected_ids, ["warehouse:alpha"]);
  assert.deepEqual(contexts[0].body_hashes, { "warehouse:alpha": "a".repeat(64) });
  assert.equal(contexts[0].message_sha256.length, 64);
  assert.equal(creates[0].skill_catalog_policy, "jev");
});

test("fallback preserves exact original message", async () => {
  const dir = await mkdtemp(path.join(os.tmpdir(), "hermes-skills-")); const messages = [];
  const client = { allowedSessions: new Set(), async request() { return { session: { id: "s1" } }; },
    async continueSession(id, message) { messages.push(message); return { sessionId: id, content: "ok" }; } };
  const adapter = new HermesPickerAdapter({ client, copyFile: null, mappingPath: path.join(dir, "map.json"),
    env: { HERMES_PICKER_MAP_PATH: path.join(dir, "map.json"), HERMES_PICKER_ALLOW_CREATE: "true" },
    skillPreparer: async () => ({ status: "unavailable", selected_ids: [], skills: [], body_bytes: 0, telemetry: {}, fallback: "native_discovery" }) });
  await adapter.handleResponses({ request: { headers: { "thread-id": TASK, "x-request-id": "turn-x" } }, response: response(), payload: payload("ORIGINAL_TEXT") });
  assert.deepEqual(messages, ["ORIGINAL_TEXT"]);
});

test("trusted skill context is fail-closed and bound to the final outbound message", () => {
  assert.equal(trustedSkillContext(null, "message"), null);
  assert.equal(trustedSkillContext({ status: "unavailable", skills: [] }, "message"), null);
  assert.equal(trustedSkillContext({ status: "suggested", receipt_id: "r", skills: [{ id: "x", content_hash: "bad" }] }, "message"), null);
  const context = trustedSkillContext({ status: "suggested", receipt_id: "r", skills: [{ id: "x", content_hash: "b".repeat(64) }] }, "message");
  assert.equal(context.version, 1);
  assert.equal(context.message_sha256, trustedSkillContext({ status: "suggested", receipt_id: "r", skills: [{ id: "x", content_hash: "b".repeat(64) }] }, "message  ").message_sha256);
  assert.notEqual(context.message_sha256, trustedSkillContext({ status: "suggested", receipt_id: "r", skills: [{ id: "x", content_hash: "b".repeat(64) }] }, "different").message_sha256);
});

test("oversize injected context falls back to original text without suppression metadata", async () => {
  const dir = await mkdtemp(path.join(os.tmpdir(), "hermes-skills-"));
  const sent = [];
  const client = { allowedSessions: new Set(), async request() { return { session: { id: "s1" } }; },
    async continueSession(_id, message, options) { sent.push({ message, context: options.skillContext }); return { sessionId: "s1", content: "ok" }; } };
  const adapter = new HermesPickerAdapter({ client, copyFile: null, mappingPath: path.join(dir, "map.json"),
    env: { HERMES_PICKER_MAP_PATH: path.join(dir, "map.json"), HERMES_PICKER_ALLOW_CREATE: "true" },
    skillPreparer: async () => ({ status: "suggested", receipt_id: "r", selected_ids: ["warehouse:huge"], body_bytes: 21000,
      telemetry: {}, fallback: null, skills: [{ id: "warehouse:huge", body: "x".repeat(21000), body_bytes: 21000, content_hash: "c".repeat(64) }] }) });
  await adapter.handleResponses({ request: { headers: { "thread-id": TASK, "x-request-id": "oversize" } }, response: response(), payload: payload("ORIGINAL_TEXT") });
  assert.deepEqual(sent, [{ message: "ORIGINAL_TEXT", context: null }]);
  const mapping = await adapter.inspectMapping(TASK);
  assert.equal(mapping.skillContext.fallback, "transport_limit");
});

test("explicit skill names are passed deterministically", async () => {
  assert.deepEqual(explicitSkillsFromText("use $alpha and $beta then $alpha"), ["alpha", "beta"]);
  let request;
  const dir = await mkdtemp(path.join(os.tmpdir(), "hermes-skills-"));
  const client = { allowedSessions: new Set(), async request() { return { session: { id: "s1" } }; },
    async continueSession(id) { return { sessionId: id, content: "ok" }; } };
  const adapter = new HermesPickerAdapter({ client, copyFile: null, mappingPath: path.join(dir, "map.json"),
    env: { HERMES_PICKER_MAP_PATH: path.join(dir, "map.json"), HERMES_PICKER_ALLOW_CREATE: "true" },
    skillPreparer: async (value) => { request = value; return { status: "unavailable", selected_ids: [], skills: [], body_bytes: 0, telemetry: {}, fallback: "native_discovery" }; } });
  await adapter.handleResponses({ request: { headers: { "thread-id": TASK, "x-request-id": "explicit" } }, response: response(), payload: payload("use $alpha") });
  assert.deepEqual(request.explicit_skills, ["alpha"]);
});
