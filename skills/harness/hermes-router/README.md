# Hermes Router

![A secure bridge connects a desktop chat to a persistent remote Hermes Agent](assets/hero.webp)

## Put your remote Hermes Agent inside Codex Desktop—without replacing its brain.

Hermes Router turns Codex Desktop into a clean chat surface for an existing remote Hermes Agent. Pick **Hermes (VPS)**, send a message, and the adapter routes it into a persistent Hermes session. The reply comes straight back—no second model rewriting it.

Your Hermes host keeps inference, tools, permissions, credentials, and session history. Codex gets the conversation surface.

## What you get

- **Persistent conversations:** each Codex task stays bound to the same Hermes session.
- **Existing-session attachment:** continue a conversation already living in Hermes.
- **Direct replies:** Hermes output returns through the Responses protocol without a model in the middle.
- **Safe file return:** approved generated files can be copied back through strict path, type, count, and size boundaries.
- **Failure awareness:** uncertain or interrupted turns remain pending instead of being blindly sent twice.

## How it works

1. **Select:** choose the private Hermes entry in the Codex model picker.
2. **Route:** codex-router forwards only the newest user text over your explicit SSH host.
3. **Continue:** the local task-to-session binding preserves continuity and returns the response or approved files.

It is a secure bridge between two workspaces—not a transcript blender.

## Required dependency

This package depends on [codex-router](https://github.com/duolahypercho/codex-router). It is an adapter and local integration patch, not a standalone model provider or an upstream extension API.

The integration was exercised against codex-router revision `5e1b49e6`, Hermes API `0.21.3`, and Codex on macOS. Other revisions and platforms require their own checks.

## Prerequisites

- Working Codex Desktop or CLI plus a codex-router installation.
- Node.js 22+ and an OpenSSH client on the local machine.
- Python 3.9+ on the remote Hermes host, plus Python 3.9+ locally for tests.
- An existing Hermes API with session create, read, and chat endpoints bound to remote loopback.
- An authenticated SSH alias available to the router service with a verified host key.

The remote `API_SERVER_KEY` stays in the remote environment or Hermes `.env` file; it is never copied into this repository.

## Install

```bash
cd skills/harness/hermes-router
npm test
```

Then follow [codex-router integration](references/codex-router-integration.md) to install the request handler and model-catalog hooks, configure the router service environment, restart the router, and refresh Codex Desktop.

To continue an existing Hermes conversation, send:

```text
/hermes attach <session-id>
```

## Important boundaries

- Text input only; Codex history, system instructions, and tools are not forwarded.
- Credentials remain on the Hermes host. Local mappings and returned artifacts remain outside Git.
- Tool-call display, live progress, token usage, and full lifecycle mirroring are not implemented.
- Cancellation does not prove the remote task stopped. Inspect uncertain remote state before retrying.
- A host-specific live canary is required after installation; passing package tests alone does not prove Desktop integration.

See [`SKILL.md`](SKILL.md) for the operating contract and the integration guide for configuration, verification, and rollback.
