# Stdio host bridge — 2026-09-25

One package, `skill-advisor-mcp`, serves every host over stdio. The model still sees exactly five tools: `skill_suggest`, `skill_read`, `skill_report_outcome`, `capability_describe`, and `notion-fetch`. The other manifest cards stay ids and one-line summaries until `capability_describe`. `capability_call` is not a model tool.

## Route

A host names one preferred route: `hermes` means `mcp`, and `cli` means the Mac `ntn` API. `--notion-fallback` is used only with `--notion-fallback-reason` (`unauthenticated`, `operation_gap`, `harness_missing`, `identity_mismatch`) and only when that reason is why the preferred route is not ready. A ready preferred route does not open the other one. Each `notion-fetch` can append one content-free `notion_route` row (`route`, `fallback_reason`, `executable`) when `--route-log` is set.

Mac registration requires a CLI-only manifest with exactly `notion.cli.page_read`, an explicit immutable release ID, an absolute `ntn` executable, and the tested `ntn 0.23.2` version. A later active-release change makes the bridge deny calls rather than silently follow a new pointer. The launcher passes the declared host-local token to the CLI child. A bad-token negative canary returns unauthenticated despite a saved login being available.

`mcp` is the existing Hermes Notion client. With no fallback flag, that path is unchanged.

## CLI read

`ntn whoami --json` on this Mac returned a bot whose `workspace_id` matched the host-local pinned workspace. The page read is `ntn api v1/pages/<uuid>/markdown -X GET`, whose 200 body is the spec object `page_markdown` (`markdown`, `truncated`, `unknown_block_ids`). `ntn pages get --json` was not used; its help does not define the object. The process argv is allowlisted, `shell` is false, and stdin is closed so a GET cannot take a body. Stdout is not logged. A workspace mismatch, a schema hash mismatch, a null or empty receipt selection, a non-zero read, or a truncated body denies the call and does not switch routes. Skill selection still fail-opens to an empty capability list.

The CLI schema is statically pinned to `{id: string}` and the host manifest offers only `notion.cli.page_read`; unavailable MCP cards are not selectable on this host. A manifest pinned to a different schema denies execution. A live read canary matched the declared workspace and returned a complete page through the authorized CLI receipt; its content was not logged.

## Still unavailable

Writes, search, query, comments, uploads, and the other 44 tools are not bridge operations. The bridge does not register those schemas or mint write approval. Codex and Cursor require separate registered canaries; Pi remains blocked until its MCP adapter is verified. Graphify is not part of routing. A CLI body over the existing 64 KiB result budget is denied.
