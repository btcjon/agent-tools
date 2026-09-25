**Accept keeping bridge as the default and committing the bounded results; send the reusable runner back for two repairs.** I independently summed the 20 JSONL rows: the summary's correctness counts, tokens, calls, and timings match. Native showed no advantage sufficient to justify promotion.

Before accepting the runner:

1. Validate the requested arm against `discovery.route_mode`, require the expected model, selected capability/status, and unique session ID. Currently missing or mismatched discovery can still produce `correct: true`.
2. Audit direct tool calls as well as `tool_call` targets, covering both native and bridge namespaces. The present write detector checks only native Notion names matching a verb regex. It also runs **after execution**; describe it as detection, not write prevention. Add tests for these bypasses and mismatches.

These defects do not invalidate the recorded performance comparison: the supplied rows have matching modes, models, and selected capabilities. They limit the strength of the zero-write claim and the runner's suitability for reuse. I inspected the runner/tests but did not rerun the reported 469-test suite or inspect full transcripts.

The next cross-harness step should be one shared route-conformance fixture, run against Hermes and one other existing adapter: available preferred MCP, unavailable MCP with CLI/API fallback, and no usable method. Verify one method is disclosed, selected skill resources remain accessible, and no full catalog/schema load occurs.
