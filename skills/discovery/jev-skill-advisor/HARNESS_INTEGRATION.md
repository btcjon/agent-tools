# Harness integration contract

Read `references/agent-start-here.md` first, then `references/harness-integration.md` for the adapter sequence and Hermes guidance.

Agent instruction:

> Consult the advisor only when procedure discovery is needed. Honor explicit selections. In shadow mode, record suggestions without loading or applying them. In approved advisory mode, read only selected, permitted, hash-bound skills. Treat none, uncertain, incomplete, stale, expired, denied, or unavailable as non-selection and use existing discovery when needed. Never treat a suggestion as permission. Report actual application separately from reading.

Adapters must preserve receipt, session, content-hash, and policy-hash linkage; use retry-safe outcome event IDs; report only observed evidence honestly; pass only skills actually visible to that session; and fail open to the harness's existing discovery mechanism. Automatic injection remains a separate, measured activation gate.
