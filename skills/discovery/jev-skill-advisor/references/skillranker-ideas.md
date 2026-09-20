# SkillRanker reference and independent idea backlog

Upstream reference: <https://github.com/Dicklesworthstone/skillranker>

Reviewed: 2026-09-20 at commit `abf909d1e0106be8f296805af527711a8fc7d158`.

License warning: upstream uses “MIT License (with OpenAI/Anthropic Rider),” not ordinary MIT. This project does not copy or derive from SkillRanker code or documentation. Resolve compatibility before incorporating any upstream source.

## Ideas worth independently evaluating

- Offline `doctor` readiness report for profiles, catalog hashes, SQLite, credentials, and harness adapters.
- Exact dry-run disclosure preview showing every field that would reach Jev.
- Local `why-not` exclusion trace for unavailable, policy-blocked, stale, protected, or low-fit skills.
- Harness/version capability declarations and conformance fixtures.
- Versioned SQLite migrations with backup, rollback, and corruption tests.
- Explicit retention reporting and bounded cleanup.
- Separate “read/loaded” observations from independently judged usefulness.
- Reproducible offline replay datasets and holdout evaluation.

## Deferred until evidence warrants complexity

- Rust rewrite or standalone TUI.
- Transcript/session mining.
- External lexical retrieval dependencies for large catalogs.
- Adaptive thresholds or automatic calibration from self-reported outcomes.
- Automatic skill injection.

## Related upstream reading

- Design overview: <https://github.com/Dicklesworthstone/skillranker>
- Adapter contract: <https://github.com/Dicklesworthstone/skillranker/blob/main/docs/adapter-contract.md>
- Disclosure contract: <https://github.com/Dicklesworthstone/skillranker/blob/main/docs/disclosure-receipt-contract.md>
- Storage design: <https://github.com/Dicklesworthstone/skillranker/blob/main/docs/storage-foundation.md>
