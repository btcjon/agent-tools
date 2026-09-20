---
name: hermes-router
description: Install, configure, or troubleshoot the Hermes Agent session adapter for Codex Desktop through codex-router, including persistent task bindings, existing-session attachment, and generated-file return.
---

# Hermes router

Use this package when the user wants Codex to act as a chat surface for a remote Hermes Agent. Hermes owns inference and tool execution; the adapter returns its reply directly.

Read [README.md](README.md) for prerequisites, usage and known limitations. For installation or repair, read [references/codex-router-integration.md](references/codex-router-integration.md). **codex-router is a required external dependency**; copying this skill alone does not enable the model picker or transport.

Keep credentials on the Hermes host and session mappings/artifacts in the local runtime directory. Select an explicit SSH host. Only forward the current user text, never Codex system prompts or tool definitions. Attach existing sessions explicitly with `/hermes attach <id>` or the local chooser.

If a turn is interrupted or its outcome is uncertain, inspect the selected remote session before resolving its pending operation; do not blindly resend. Cancellation does not prove the remote agent stopped. Never clear pending state while a remote turn may still be running.

Run `npm test` from this package after changes. Verify one isolated session through the actual Codex client before claiming deployment works. Distinguish tests, router activation, cached Desktop picker refresh, and actual Desktop UI verification.
