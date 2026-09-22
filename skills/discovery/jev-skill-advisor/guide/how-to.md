# How to use it

## Browse the library

In Notion, search for **Jev Skills Pilot**.

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

**Hermes.** The `warehouse-skills` plugin runs the same command before the first model call and also exposes `skill_search` and `skill_view`. Native Hermes skills stay in `~/.hermes/skills`. Scheduled jobs skip selection. A null result leaves those native skills available.

**Cursor.** Cursor lists skills. It does not run a prompt hook for this, and listing a skill does not make the command run. Put an always-on instruction in a file the Cursor IDE already loads at startup: a user rule (`~/.cursor/rules/*.mdc` with `alwaysApply: true`) or the global instructions file. The instruction tells the agent to run `skill-search --select` before other tools when the task needs a procedure, a named service, or a check of the machine. If nothing is selected, continue with the tools already available. Keep `skill-discovery` in Cursor's skills directory so a later turn can open the same instructions. The `agent` CLI reads rules and `AGENTS.md` from the workspace you pass. A workspace that does not contain them will not run the selector.

**Pi.** Copy `adapters/pi/skill-select.ts` to `~/.pi/agent/extensions/skill-select.ts` and restart Pi. On each new prompt it runs `skill-search --select` and adds one verified body before the model starts. A null result adds nothing. Pi also loads `skill-discovery` from `~/.pi/agent/skills` when that package is installed. `--no-skills` turns skill listing off; the extension still runs.

**Claude, Grok, and Agy.** Point their shared skill entry at the same `skill-discovery` package and the same command. Do not copy the library into each harness's private skills folder.

## What has been tested

These are host checks, not a promise that cloning the repo turns delivery on.

- A tailnet check selects the Tailscale skill. "Ask whether this system is as good as it can be" and "help me with this" select nothing. "Implement the authorized plan and verify it" selects the implement-plan skill.
- Hermes and Pi inject one verified skill and finish the task. Pi does this from `adapters/pi/skill-select.ts`. A prose instruction alone did not.
- Codex injects the skill from its `UserPromptSubmit` hook. A silent post-tool repair used to hold the turn for the 10-minute Grok stall. The forwarder now ends that repair on a 90-second deadline. A tailnet check after that change injected the Tailscale skill, finished `tailscale status`, and returned a final answer.
- Cursor IDE support is provisional. A new IDE chat has not been tested. An `agent` CLI run does call `skill-search` when the workspace itself contains the instruction, and that run is not proof the IDE loaded the home rule. An already-open chat does not pick up a rule added after it started.
- The `skill-discovery` file reached through the old warehouse mount matches the snapshot copy. The selector reads the snapshot either way.
- What is running on a host is not what a fresh clone contains. On the authoring machine, `skill-search` loads this package's working tree, and the Pi extension is a copy of `adapters/pi/skill-select.ts`. The Codex router change lives in the host router, outside this repo. Those files are not on GitHub until they are committed.

## See whether a skill was injected

On Codex, the adapter appends one line per task. Hermes appends a content-free receipt under `~/.local/state/jev-skill-advisor/hermes-adapter/events.jsonl`. Pi appends the same kind of receipt under `~/.local/state/jev-skill-advisor/pi-adapter/events.jsonl`. Each line has the skill id, snapshot id, hash, and byte count, and no skill body.

```bash
tail -n 1 ~/.local/state/jev-skill-advisor/codex-adapter/events.jsonl
```

A delivery has `"status":"emitted"`, plus `stable_ids`, `latency_ms`, and `context_bytes`. A `fallback_reason` means nothing was injected. The line has no prompt and no skill body.

`skill-advisor-admin --config PROFILE doctor` reports mode, catalog size, and whether a credential is present, without printing the secret. Point it at the profile from the active release. The command is documented in [operations](../references/operations.md).

## Stop delivery

The active release names an emergency-stop file. On the current installs that file is:

```text
~/.local/state/jev-skill-advisor/releases/EMERGENCY_STOP
```

Create it to stop injection on the next task. Remove it to resume. The Codex log then shows `fallback_reason` as `emergency_stop`. Each host has its own file.

A new machine stays in shadow until you choose delivery. [Try it](../TRY_IT.md) is that path. The benefit writeup is the [guide](README.md).
