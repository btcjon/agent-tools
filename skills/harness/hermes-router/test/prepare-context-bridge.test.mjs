import assert from "node:assert/strict";
import { chmod, mkdtemp, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { createPrepareContextClient } from "../../../discovery/jev-skill-advisor/adapters/node/prepare-context.mjs";

async function executable(source) {
  const dir = await mkdtemp(path.join(os.tmpdir(), "jev-bridge-"));
  const file = path.join(dir, "fake.mjs");
  await writeFile(file, `#!/usr/bin/env node\n${source}\n`); await chmod(file, 0o700); return file;
}

test("bridge handles immediate child exit and broken stdin without crashing", async () => {
  const prepare = createPrepareContextClient({ executable: "/usr/bin/true", profile: "/tmp/profile" });
  const result = await prepare({ task: "x", harness: "hermes", session_id: "s" });
  assert.equal(result.fallback, "native_discovery"); assert.deepEqual(result.skills, []);
});

test("bridge rejects forbidden bodies on fallback and malformed aggregates", async () => {
  const fake = await executable('process.stdin.resume(); process.stdin.on("end",()=>{console.log(JSON.stringify({protocol_version:1,status:"uncertain",fallback:"native_discovery",selected_ids:["x"],skills:[{id:"x",body:"BAD",body_bytes:3,content_hash:"a".repeat(64)}],body_bytes:0}))})');
  const prepare = createPrepareContextClient({ executable: fake, profile: "/tmp/profile" });
  const result = await prepare({ task: "x", harness: "hermes", session_id: "s" });
  assert.equal(result.reason, "invalid_response"); assert.deepEqual(result.skills, []);
});

test("bridge does not spawn for an already aborted request", async () => {
  const controller = new AbortController(); controller.abort();
  const prepare = createPrepareContextClient({ executable: "/does/not/exist", profile: "/tmp/profile" });
  const result = await prepare({ task: "x", harness: "hermes", session_id: "s" }, controller.signal);
  assert.equal(result.reason, "cancelled");
});

test("bridge rejects delivery from a failed process", async () => {
  const fake = await executable('process.stdin.resume(); process.stdin.on("end",()=>{console.log(JSON.stringify({protocol_version:1,status:"suggested",fallback:null,selected_ids:["x"],skills:[{id:"x",body:"OK",body_bytes:2,content_hash:"a".repeat(64)}],body_bytes:2}));process.exit(7)})');
  const prepare = createPrepareContextClient({ executable: fake, profile: "/tmp/profile" });
  const result = await prepare({ task: "x", harness: "hermes", session_id: "s" });
  assert.equal(result.reason, "process_failure"); assert.deepEqual(result.skills, []);
});
