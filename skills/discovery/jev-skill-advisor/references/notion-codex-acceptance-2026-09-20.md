# Notion package and Codex acceptance — 2026-09-20

## Accepted boundary

One Notion Agent Skills package is exported into an immutable host-local snapshot and selected/injected through the installed Codex `UserPromptSubmit` hook. This proves the transport and progressive-resource path, not migration of the full global catalog or measured token savings.

## Evidence

- Notion skill page: `3e145e44-58bf-8178-891c-c658b119fd57`
- Verified snapshot: `c2b0feb1b29501790bccea0a4f69f556a404974fe639ac27fe9116cfcbc9a4a0`
- Stable package ID: `shared:plan`; legacy alias: `warehouse:plan`
- Package inventory: `SKILL.md`, `skill-package.json`, `detail.md`, `check.sh`
- The snapshot manifest binds every file hash and mode. `check.sh` is materialized as executable and prints `NOTION_PACKAGE_OK` after reading the packaged resource sentinel.
- A natural-language direct adapter canary selected `shared:plan` through Jev with two provider attempts. A repeated request used the service cache with zero provider attempts.
- An ephemeral Codex CLI task ran the configured hook and returned the exact package-root attribute supplied only in injected context. The adapter log recorded `emitted`, `explicit_selection`, `shared:plan`, zero provider attempts, and the verified snapshot ID.
- A failed Notion refresh left the selected snapshot unchanged. Rollback to the prior snapshot and forward restoration both verified.

## Verification

```text
uv run --with pytest --project skills/discovery/jev-skill-advisor pytest -q skills/discovery/jev-skill-advisor/tests
114 passed, 1 skipped

node --test skills/harness/hermes-router/test/*.test.mjs
26 passed
```

Astra accepted the bounded Notion-package/Codex pilot after reviewing snapshot validation, selection and path bindings, catalog/disk/body hashes, invocation-policy precedence, process-group timeout cleanup, fail-open behavior, context caps, and content-free logs.

## Not yet proven

- Full global-skill migration into Notion
- Removal of existing canonical projections or symlinks
- Codex catalog suppression or measured token savings
- Automatic activation on additional harnesses beyond the separately verified Hermes and Codex adapters
