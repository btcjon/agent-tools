# Jev live acceptance — 2026-09-20

## Scope

Fresh Jev-routed Hermes session, same-session advisor fallback, native control session, transport boundary, and compression-policy persistence.

## Live evidence

- Jev session: `api_1789917848_e669bdf3`
  - selected `warehouse:plan` with receipt `22cc8227390c423b8fb0a8721d62ae86`
  - stored policy: `jev`
  - system-prompt hash on both turns: `c53cdb7515afc3103a153123f7676995bf4ab72017f85cde2243c190b50ac010`
  - system prompt: 30,790 characters; external-routing guidance present; `<available_skills>` absent
  - `skill_manage`, `skill_view`, and `skills_list` remained available
  - first turn contained the selected skill and original request
  - second turn ran with the advisor disabled and called `skills_list` followed by `skill_view`
- Native control: `api_jev_native_control_20260920`
  - stored policy: `native`
  - system prompt: 59,534 characters; `<available_skills>` present; Jev guidance absent
  - same native skill tools remained available
- Prompt reduction in this capture: 28,744 characters (48.3%) without removing skill tools.

## Verification

- Advisor: 107 passed, 1 skipped.
- Hermes router: 26 passed.
- Remote Hermes policy regression set: 206 passed.
- Remote compression-policy test file: 4 passed.
- Oversized injected context falls back to the original request with no suppression metadata.
- Compression children and API forks retain the immutable Jev catalog policy.

## Claim boundary

The remote server does not validate the per-turn `skill_context.message_sha256`. Catalog suppression is authorized by the authenticated, immutable session-creation policy. The hash is currently client-side telemetry, not a server-enforced security guarantee.
