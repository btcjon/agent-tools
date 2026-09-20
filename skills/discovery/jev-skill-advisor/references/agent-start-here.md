# Agent start here

This package lets a harness ask Jev which reviewed skill best fits the current task without placing the entire skill catalog in the main model context.

## Mental model

1. The harness supplies the current task, a frozen catalog of stable skill IDs and short descriptions, and the IDs actually available in that session.
2. Jev returns an advisory choice or abstention.
3. The service issues a receipt bound to the selected skill's content and invocation-policy hashes.
4. The harness may read that one canonical `SKILL.md` through the receipt, then inject the body into its normal instruction channel.
5. The harness—not Jev—retains authority over permissions, explicit user choices, tools, and execution.

## Before trying it

- Install this package in an isolated environment.
- Create a reviewed catalog covering the intended pilot skills. Presence in the warehouse is not enough.
- Create one host profile from `examples/service-host.example.json`; use a host-local state directory and credential.
- Run `skill-advisor-admin ... init-db`, then `doctor`.
- Keep `mode: shadow`, `read_enabled: false`, and injection off for a new harness trial. The checked-in Hermes and Codex adapters have separate reviewed activation evidence; do not treat that as automatic approval for another host.

## Success evidence

Record whether the suggested skill was available, read, applied, and independently useful; also record misses, abstentions, latency, Jev attempts, and main-model context saved. A read proves delivery, not usefulness. Do not enable automatic injection merely because suggestions look plausible.
