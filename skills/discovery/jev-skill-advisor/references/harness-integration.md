# Harness integration

Every harness uses the same service contract. The adapter's job is deliberately small.

## Shared adapter sequence

1. On a new user task, resolve explicit skill requests locally. Explicit requests bypass Jev.
2. Build `skill_suggest` input with a unique request/session ID, the current task, a compact non-secret context, and the stable IDs visible to this harness.
3. In shadow mode, record the response and continue normally. Do not inject anything.
4. In a later advisory pilot, call `skill_read` only for a receipt-authorized card and only when its content hash matches. Put the returned body into the harness's supported instruction/context channel.
5. Report `read`, `applied`, `dismissed`, or `blocked` with honest evidence. Self-reported application is not proof of quality.
6. On timeout, abstention, stale policy/source, unavailable provider, or malformed output, fail open to the harness's existing discovery path.

## Hermes

Hermes uses the thin adapter under `skills/harness/hermes-router`. Point its host profile at an immutable verified catalog snapshot. It injects selected bodies before the first model request and retains `skill_search`/`skill_view` as the failure and comparison path.

## Codex, Claude, Cursor, and other harnesses

Use the same stdio MCP server when the harness supports MCP. Otherwise send versioned JSON to `skill-advisor-service` over stdin and parse its single JSON stdout response. An adapter must provide actual session-visible IDs; it must not claim every warehouse skill is loadable.

Codex uses `scripts/codex_skill_hook.py` as the single owned `UserPromptSubmit` command. The adapter validates the event, calls `prepare-context` under a shorter internal deadline than the hook timeout, and emits one bounded `hookSpecificOutput.additionalContext`. It returns empty stdout on failure so native discovery continues. Keep the separate `Stop` observer when outcome telemetry is wanted.

For both adapters, `warehouse_root` may be an immutable Notion Agent Skills cache snapshot. The injected record includes the entrypoint and package root so the agent can progressively open referenced package resources without loading unrelated skills.

## Activation stages

- **Off:** no Jev call.
- **Shadow:** Jev suggests and telemetry records; agent behavior is unchanged.
- **Advisory/manual read:** an operator or adapter may inspect the selected skill.
- **Automatic injection:** enabled only for a reviewed host profile and immutable snapshot; native discovery remains the fail-open path.
