# Paired context and task-outcome probe

`scripts/context_benefit_probe.py` reports paired baseline-versus-bridge observations by harness. It does **not** collect startup context, run tasks, or judge whether a task succeeded. It refuses prompt/page text and unproven startup byte counts. Run:

```sh
skills/discovery/jev-skill-advisor/.venv/bin/python skills/discovery/jev-skill-advisor/scripts/context_benefit_probe.py --input /path/to/observed-cases.json --report /path/to/paired-report.json
```

The input is `{"schema_version":1,"cases":[...]}`. Each case has `case_id`, `harness`, `baseline`, and `bridge`. Each arm may have only `session_id`, `startup_tool_bytes`, `startup_evidence`, `tools_list_schema_bytes`, `task_success`, `task_evidence`, `input_tokens`, and `latency_ms`. Use `null` for missing measurements. The baseline and bridge must have different session IDs, and `all` is reserved as the aggregate label. A non-null `startup_tool_bytes` requires a session ID and `startup_evidence` of `provider_request_capture` or `harness_trace`; a server `tools/list` dump qualifies only for `tools_list_schema_bytes`. A non-null `task_success` requires a session ID and `task_evidence` of `human_review` or `heldout_answer_check`. The report counts only cases with both arms observed for each metric and retains missing metrics as null; it makes no automatic go/no-go claim.

Current evidence boundary: the 40-case Jev holdout tests capability selection, not whole-task outcome. `skill-advisor-usage` reports 55 direct deliveries in the last 24 hours as of 2026-09-25 08:46 UTC, all without follow-through labels. Fresh Codex/Pi/Hermes read canaries are not paired baseline-versus-bridge tasks. Model-visible startup tool bytes, paired task success, and paired model input tokens remain unmeasured. Capture these from actual first model requests or an authoritative harness trace; do not substitute a configured MCP tool count, server schema size, or an adapter receipt.

The initial evaluation should freeze a held-out set of at least 20 tasks before either route runs; include representative skill-only, Notion read, unsupported Notion operation, and abstention cases. Run each task from comparable fresh sessions with the same model and permissions, record the exact session IDs and trace provenance, and judge success against a prewritten expected result. Keep private prompts and Notion content outside this report. Do not hide dest's native Notion tools or retire skill symlinks from this report alone.
