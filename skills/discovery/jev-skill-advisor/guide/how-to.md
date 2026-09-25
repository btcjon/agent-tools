# How to use it

## Browse the library

In Notion, search for **Global Skills**.

- Each row is a skill.
- Open a row to read its instructions.
- The Files property contains the portable package, including supporting files.

Harnesses do not read that page on each task. They read a verified snapshot. A Notion edit shows up in routing after that snapshot is rebuilt and promoted. Sync and promote commands are in [operations](../references/operations.md).

## Install the command

From this package, with `uv`:

```bash
uv sync
uv run skill-advisor-select --task "the task in one sentence"
```

That prints one JSON object: a `selected` skill, or `selected: null`. Put `TYPESAFE_API_KEY` in the environment, outside the repo. The selector reads the active release at `~/.local/state/jev-skill-advisor/releases`. Build that release with `skill-advisor-release build-delivery`, then `activate` it. The snapshot root and evidence files stay on the host. Cloning the repo does not turn delivery on.

Harness instructions call the same selector as `skill-search --select --task "<task>" --json` once that wrapper is on `PATH`. A keyword search without `--select` only scores the snapshot catalog. It is not what a new task uses.

## What happens on a new task

Every harness calls the same command:

```bash
skill-search --select --task "<the task>" --json
```

- A `$skill-name` in the task is authoritative. Jev is not called. Up to five names are recognized. `--name` does the same thing and still checks the snapshot hash.
- Otherwise Jev chooses one skill that clearly applies, or none. A weak or topical resemblance adds nothing. A short task still gets a skill when that skill clearly applies.
- The selected body is hash-checked against the snapshot. A match is one skill body.
- A timeout, a bad hash, or no match adds nothing. The task continues. Nothing falls through to a directory of every skill.

**Codex.** A `UserPromptSubmit` hook runs that command before the model sees the task and adds a matching body as extra context. Trust the hook in Codex after you change it. Codex reports the hash to store; do not invent one. `additionalContextLimit` of `0` keeps that context in the prompt rather than spilling it to a file.

**Hermes.** The `warehouse-skills` plugin selects one skill before the first model call and also exposes `skill_search` and `skill_view`. Hermes does not run `skill-search` again from the shared startup text. Native Hermes skills stay in `~/.hermes/skills`. A scheduled job gets a shared skill only when the job body already contains the procedure. A null result leaves those native skills available.

**Cursor.** Cursor lists skills. It does not run a prompt hook for this, and listing a skill does not make the command run. Put the same instruction in a Cursor user rule, with `--receipt` on that command. The app and the `agent` CLI both receive user rules, including in a workspace that has no `.cursor/rules` of its own. The app also loads `~/.cursor/rules/*.mdc` when `alwaysApply` is true. The instruction tells the agent to run `skill-search --select` before other tools. Jev returns one skill or nothing. If nothing is selected, continue with the tools already available. A chat that is already open keeps the instructions it started with.

**Pi.** Copy `adapters/pi/skill-select.ts` to `~/.pi/agent/extensions/skill-select.ts` and restart Pi. On each new prompt it runs `skill-search --select` and adds one verified body before the model starts. A null result adds nothing. Pi also loads `skill-discovery` from `~/.pi/agent/skills` when that package is installed. `--no-skills` turns skill listing off; the extension still runs.

**Claude, Grok, and Agy.** Point their shared skill entry at the same `skill-discovery` package and the same command. Do not copy the library into each harness's private skills folder.

## Discover an execution capability

The host-local `jev-skill-advisor` MCP bridge is a second, optional surface beside pre-model skill injection. It offers five stable tools: `skill_suggest`, `skill_read`, `skill_report_outcome`, `capability_describe`, and `notion-fetch`. `skill_suggest` selects from compact capability cards after a skill match; `capability_describe` reveals only the chosen operation's live schema and checks its pinned hash. A selection is advice, not permission to execute. A stored, session-bound receipt is required for `notion-fetch`.

Current bridge execution is **read-only Notion page fetch**, not general access to Notion's 45 native MCP tools. On Mac Codex, Cursor, and Pi, the preferred read route is the authenticated local `ntn` CLI. On dest Hermes, it is the existing Notion OAuth MCP connection. Search, database queries, edits, and attachments still use a separately available native/CLI route when supported; do not present `notion-fetch` as covering them. A fallback needs a concrete operation, auth, identity, or transport reason—not merely low Jev confidence. Neither the entire warehouse nor all 45 native schemas should be injected just to pick one capability.

Host registration and rollback evidence is in [the Mac bundle note](../references/host-local-bundle-build-2026-09-25.md) and [the dest cutover note](../references/dest-host-local-cutover-2026-09-25.md). Those canaries prove a read and receipt denial in fresh sessions; they do not yet prove lower startup context or better downstream task results. Existing sessions may retain old tool registrations until safely refreshed.

The bundle launch guard checks the pinned interpreter binary, a separate `libpython` when one is used, and the active release. It does not hash the host standard library or site-packages outside the frozen bundle; keep the host runtime patched and review those dependencies separately.

## What has been tested

These are host checks, not a promise that cloning the repo turns delivery on.

- A tailnet check selects the Tailscale skill. "Ask whether this system is as good as it can be" and "help me with this" select nothing. "Implement the authorized plan and verify it" selects the implement-plan skill.
- Hermes and Pi inject one verified skill and finish the task. Pi does this from `adapters/pi/skill-select.ts`. A prose instruction alone did not.
- Codex injects the skill from its `UserPromptSubmit` hook. A silent post-tool repair used to hold the turn for the 10-minute Grok stall. The forwarder now ends that repair on a 90-second deadline. A tailnet check after that change injected the Tailscale skill, finished `tailscale status`, and returned a final answer.
- Cursor. A user rule tells both the app and the `agent` CLI to run `skill-search --select` before other tools. A CLI run in a directory with no project rules called the selector. A new IDE chat has not been watched. An already-open chat keeps the instructions it started with. Each request may spend up to 160 provider calls. That window starts again on the next request. The lifetime total stays in the database as history and does not lock later requests out. A local window stop is `local_provider_attempt_budget`, not a provider failure.
- The `skill-discovery` file reached through the old warehouse mount matches the snapshot copy. The selector reads the snapshot either way.
- What is running on a host is not what a fresh clone contains. Check the active release and installed host-local bundle before claiming runtime parity. The Codex router lives outside this repo, and an already-open session may keep its old tool catalog.

## See whether a skill was injected

On Codex, the adapter appends one line per task. Hermes appends a content-free receipt under `~/.local/state/jev-skill-advisor/hermes-adapter/events.jsonl`. Pi appends the same kind of receipt under `~/.local/state/jev-skill-advisor/pi-adapter/events.jsonl`. Cursor appends one only when its command includes `--receipt`, under `~/.local/state/jev-skill-advisor/cursor-adapter/events.jsonl`. Each line has the skill id, snapshot id, hash, and byte count, and no skill body. New lines also have `usage_id`, `adapter`, `host`, and `selected_at`. Older lines have no timestamp; they still count, and they do not set Last selected.

Update an existing skill before creating another. Put durable, reusable, harness-independent procedures in Global Skills, even when Hermes authors them. Keep reusable Hermes-specific procedures native under `~/.hermes/skills`. Leave one-off instructions and transient task details in the chat.

`skill-advisor-usage import` folds those receipt files into `~/.local/state/jev-skill-advisor/usage/ledger.json`. A receipt with a session, request, harness, or release stays in `receipts` and keeps those fields. A receipt with none of them stays in `unattributable` and does not change Selection count. Import never invents a timestamp or a session id, and it never deletes an older row. `skill-advisor-usage publish --data-source 4e55bed1-83a4-429d-9090-6c44fe53376f` writes `Selection count` and `Last selected` on the matching Global Skills pages. Importing the same lines again changes nothing. A later run that sees only one host keeps receipts already stored from the other. The command does not call Jev and does not rebuild the snapshot. The count is how often a skill was supplied, not proof it was followed. `imported_at` is when the ledger was last written. The count is stale as soon as a harness selects a skill that has not been imported. Until a Cursor receipt exists, say that the report excludes Cursor.

```bash
tail -n 1 ~/.local/state/jev-skill-advisor/codex-adapter/events.jsonl
```

A delivery has `"status":"emitted"`, plus `stable_ids`, `latency_ms`, and `context_bytes`. A `fallback_reason` means nothing was injected. The line has no prompt and no skill body.

`skill-advisor-admin --config PROFILE doctor` reports mode, catalog size, and whether a credential is present, without printing the secret. Point it at the profile from the active release. The command is documented in [operations](../references/operations.md).

## Check whether the picker is helping

Run `skill-advisor-usage summary --since 24 --remote-host dest` for a read-only Mac-plus-VPS attempt summary. A missing remote log is reported, not silently omitted. Older logs recorded mostly successes, so their `status_rates` are withheld until complete attempt logs accumulate. The report distinguishes a skill delivered into context, an agent's optional self-report that it followed it, and a human-reviewed outcome. None of these is inferred from a raw selection count.

For a small direct comparison, run `skill-advisor-selector-eval --cases examples/live-selector-comparison-cases.json --report /path/to/report.json`. It exercises the real implicit Jev selector and a lexical baseline against the same snapshot. The six synthetic cases are a smoke test; use a larger independently labeled sample before claiming better task outcomes. Details and the privacy boundary are in [operations](../references/operations.md).

## Stop delivery

The active release names an emergency-stop file. On the current installs that file is:

```text
~/.local/state/jev-skill-advisor/releases/EMERGENCY_STOP
```

Create it to stop injection on the next task. Remove it to resume. The Codex log then shows `fallback_reason` as `emergency_stop`. Each host has its own file.

A new machine stays in shadow until you choose delivery. [Try it](../TRY_IT.md) is that path. The benefit writeup is the [guide](README.md).
