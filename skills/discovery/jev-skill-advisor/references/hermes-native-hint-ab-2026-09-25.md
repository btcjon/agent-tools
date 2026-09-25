# Hermes native capability hint CLI A/B (2026-09-25)

## Boundary

The opt-in adapter was deployed only to dest Hermes's `warehouse-skills/capability_hint.py`, plus the matching `jev_skill_advisor/capability_observability.py` telemetry module in its maintained venv. Both old files are preserved under `/home/dev/.local/state/jev-skill-advisor/registration-backups/native-hint-20260925-7b375c0/`. The whole candidate wheel was **not** installed: its `capability_choice.py` and `mcp_server.py` changes would have confounded this A/B. Gateway environment/config was not changed; unset mode still means `bridge`. Plugin `__init__.py` SHA256 `759a0da0...`, config SHA256 `686683f5...`, active release `31cd802e18e2...`, and guarded runtime pointer `31cd802e18e2-dc3bac9` remained unchanged after testing. Local adapter commit: `7b375c0`.

Fresh one-shot CLI sessions used `gpt-6-sol`, the same fixed read-only prompt per pair, and `JEV_HERMES_CAPABILITY_MODE=off|native`. Five tasks were run twice, with the arm order reversed on the second pass. `-z` was used only with read-only prompts; every redacted session export was checked for invoked Notion tool names. Direct read-only Notion MCP checks immediately before the runs established truth: page `3e145e44-58bf-813a-a125-f94953b1c02c` had title `obsidian-vault-operations`, unchanged body hash `809ec2f...`, exactly 13 `## ` lines; exact title search returned that ID; favorites first page and joined teams were both empty. No page body, credentials, or raw session transcript were written here.

| Task | Truth | Off answers | Native answers | Off API calls / tokens | Native API calls / tokens |
| --- | --- | --- | --- | --- | --- |
| Page ID → title | `obsidian-vault-operations` | correct, correct | correct, correct | 4 / 116,537; 3 / 85,277 | 3 / 85,688; 3 / 85,688 |
| Exact title search → page ID | `3e145e44-58bf-813a-a125-f94953b1c02c` | correct, correct | correct, correct | 6 / 184,273; 6 / 195,272 | 5 / 146,637; 7 / 222,325 |
| Count exact `## ` lines | 13 | 13, **21 (wrong)** | 13, 13 | 4 / 116,732; 4 / 118,760 | 3 / 85,788; 3 / 85,830 |
| Favorite pages, limit 20 | 0 | 0, 0 | 0, 0 | 3 / 77,160; 3 / 77,160 | 3 / 77,559; 3 / 77,559 |
| Joined teams | 0 | 0, 0 | 0, 0 | 3 / 77,339; 3 / 77,339 | 3 / 77,744; 3 / 77,744 |

Aggregate: native 10/10 correct, 36 main API calls, 1,022,562 Hermes-accounted total tokens; off 9/10 correct, 39 calls, 1,125,849 tokens. Native's 9.2% lower aggregate token count is **not** a stable per-task saving: the search repeat cost more with native (7 vs 6 calls), and favorite/team runs were slightly more expensive in both repeats. Hermes `total_tokens` includes cache-read accounting; it is not a direct bill or initial-context measurement. This is a small, single-host, single-model sample, not a production quality claim.

Redacted export review found **zero Notion MCP write tool names** in all 20 sessions. This was a tool-name audit, not an independent proof that every other tool call was side-effect-free. Each native hint selected the expected read-only capability (`fetch`, `search`, `list-favorite-pages`, `get-teams`), and native invocation telemetry recorded the selected target. Search runs also invoked `notion-get-tool-access`, as the native Notion schema requests; that unselected read is expected and demonstrates the hint is advisory rather than a tool restriction. The second native search additionally fetched the page twice. A separate default/unset bridge canary read the same page title correctly via `mcp__jev_skill_advisor__notion_fetch` with discovery event `route_mode=bridge`; its 3 API calls / 77,185 tokens are not included in the A/B totals.

Grok 4.7 was delegated a reusable runner (`references/worker-native-hint-eval-runner-2026-09-25.md`) but timed out after 600 seconds with no handoff or edits. The lead ran this bounded comparison directly. No gateway restart, persistent native enablement, Notion mutation, or symlink retirement occurred.
