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

Hermes should discover this package as a normal global skill, but selection integration should run as a thin hook/plugin or explicit MCP client—not inside `SKILL.md`. No Hermes adapter is installed by this package. Point a Hermes-specific host profile at the canonical warehouse and the reviewed catalog, initialize the local database, and register the stdio MCP command using absolute package and executable paths. Start with manual MCP calls or a separately reviewed shadow hook. Do not disable Hermes `skill_search`/`skill_view`; they remain the fallback and comparison baseline.

## Codex, Claude, Cursor, and other harnesses

Use the same stdio MCP server when the harness supports MCP. Otherwise send versioned JSON to `skill-advisor-service` over stdin and parse its single JSON stdout response. An adapter must provide actual session-visible IDs; it must not claim every warehouse skill is loadable. Use the harness's supported context-injection mechanism only after shadow evaluation passes.

## Activation stages

- **Off:** no Jev call.
- **Shadow:** Jev suggests and telemetry records; agent behavior is unchanged.
- **Advisory/manual read:** an operator or adapter may inspect the selected skill.
- **Automatic injection:** deferred until the shadow pilot meets predefined quality, safety, latency, and savings gates.
