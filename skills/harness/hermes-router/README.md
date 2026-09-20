# Hermes router

Use Codex Desktop as a chat surface for your existing remote Hermes Agent. Select **Hermes (VPS)** in the model picker: messages reach a persistent Hermes session and its replies return directly, without a second model rewriting them.

## Required dependency: codex-router

This package **depends on [codex-router](https://github.com/duolahypercho/codex-router)**. It is an adapter and local integration patch, not a standalone model provider or an upstream-supported codex-router extension API. Both the request handler and model-catalog hooks are required. Router updates may require reapplying/reviewing those hooks.

Integration was exercised against router revision `5e1b49e6` with the local hooks described below, Hermes API version `0.21.3`, and Codex on macOS. Other revisions and platforms need their own checks.

## Prerequisites

- Working Codex Desktop/CLI and codex-router installation.
- Node.js 22+ locally, OpenSSH client, and Python 3.9+ on the remote host (Python 3.9+ locally for tests).
- An existing Hermes API with session create/read/chat endpoints, bound to remote loopback. The remote `API_SERVER_KEY` remains in the remote environment or Hermes `.env` file.
- An authenticated SSH alias available to the router service, with a verified host key. The portable default alias is `hermes-host`; configure your own explicitly.

No npm runtime dependencies. The repository MIT license applies.

## Install

1. Clone this repository and run `npm test` in `skills/harness/hermes-router`.
2. Follow [codex-router integration](references/codex-router-integration.md) to add the two hooks, configure the service environment, and publish the picker entry. This is manual source integration, not an automatic installer.
3. Restart the router and reopen Codex if its model picker is cached.
4. Select **Hermes (VPS)** on a task. The first message creates a Hermes session; later messages reuse it.

Agent skill discovery is optional: link this directory into your harness's skill directory. That link alone does not install the router hooks. Keep the package at a stable path once the router references it.

## Continue an existing conversation

Send `/hermes attach <session-id>` before your next message, or use the loopback chooser. Attaching validates the session and saves the task binding without sending a chat message. The same session can then be continued from Hermes Desktop.

The chooser starts on `127.0.0.1:8879` with the first adapter request. Its private capability URL is saved to `~/.codex/hermes-picker/ui.json` (or under `CODEX_HOME`). Open the complete URL locally; do not publish it. A router restart changes the capability. The chooser lists recent Codex tasks and up to 50 Hermes sessions.

Mappings live in `~/.codex/hermes-picker/task-sessions.json`; returned files live beside them under `artifacts/`. Keep those files private and outside Git.

## Configuration

See [examples/config.env.example](examples/config.env.example). Set variables in the actual router service environment; exporting them in an unrelated shell does not affect an already-running service. The remote credential path expands `~` on the remote host.

`HERMES_PICKER_ALLOWED_SESSION_IDS` optionally restricts session access (comma-separated). `HERMES_PICKER_ALLOW_CREATE=false` disables new sessions. `HERMES_PICKER_MAP_PATH` and `HERMES_PICKER_ARTIFACTS_ROOT` override local storage. The default artifact allowlist is `/tmp/codex-hermes-artifacts`; configure any additional dedicated absolute output directories explicitly. Files outside these roots are not copied.

## Behavior and limits

- Text input only. Only the newest user text is forwarded; existing Codex history, system instructions and tools are not imported into Hermes.
- Hermes owns model selection, tools and permissions. This package does not change either harness's permission settings.
- Replies use assistant text through the Responses protocol. Tool-call display and live progress are not implemented.
- Old Hermes transcripts and rename/archive/fork actions do not mirror into Codex automatically. Task bindings provide session continuity, not full lifecycle synchronization.
- Generated Markdown file links inside allowed directories are copied locally (up to 8 files, 2 MB each). Hidden files, symlink escapes and nonregular files are rejected. `/hermes files <paths>` requests explicit allowed files.
- Concurrent turns are blocked. The last completed request identity can be replayed without re-running Hermes. Without a stable turn identity, identical complete payloads cannot be distinguished reliably; this is not a general exactly-once ledger.
- Cancellation or a transport failure may leave Hermes running. Pending state blocks further turns until the outcome is inspected and resolved; there is no pending-resolution UI.
- Token usage is unavailable; clients may render it as zero. The catalog context size is a frontend setting, not a promise about the Hermes model.

## Verification

`npm test` runs synthetic adapter, real HTTP/SSE, retry, cancellation, locking, chooser-authentication and file-boundary checks without contacting a real account. The original deployment additionally verified native Codex execution/resume, Hermes Desktop continuity and exact generated-file contents. The packaged portable defaults still require a host-specific live canary after installation. Desktop picker/chooser rendering was not part of that original visual acceptance.

## Rollback

Remove the private catalog entry and republish the router catalog to hide the picker option. Reverse only the two integration hooks and restart the router to remove execution support. Preserve other router edits and private task mappings; do not overwrite whole files from stale backups. Remote Hermes sessions and services are not removed.
