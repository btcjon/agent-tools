# Harness integration

Every harness uses one command. The adapter's job is to run it and check the result.

```bash
skill-search --select --task "<the task>" --json
skill-search --select --name "<skill-name>" --json
```

`--select` asks Jev to choose one eligible skill from the active snapshot, or none. `--name` skips Jev and still checks the snapshot hash. The JSON result is either one `selected` object (`skill_id`, `snapshot_id`, `hash`, `content`, `path`, `package_root`) or `selected: null`.

## What the adapter must do

1. On a new user task, honor an explicit skill name first.
2. Otherwise run `skill-search --select` against the active snapshot.
3. If `selected.content` is present, confirm its hash matches the snapshot and put that one body in the harness's instruction channel.
4. On timeout, abstention, a hash mismatch, a missing credential, or malformed output, add nothing and continue. Do not open the skill directory and do not run a keyword search as a fallback.

A keyword search without `--select` scores the same snapshot catalog. It is a lookup, not the delivery path.

## Where a skill is written

Update an existing skill before creating another. Put durable, reusable, harness-independent procedures in Global Skills, even when Hermes authors them. Keep reusable Hermes-specific procedures native under `~/.hermes/skills`. Leave one-off instructions and transient task details in the chat.

## Codex

`scripts/codex_skill_hook.py` is the `UserPromptSubmit` command. It validates the event, calls the selector, and emits one bounded `hookSpecificOutput.additionalContext` when the hash matches. Empty stdout means nothing was added. A `Stop` observer can record the outcome. After a hook change, store the hash Codex itself reports as trusted. A guessed hash is skipped silently.

## Hermes

The `warehouse-skills` plugin selects one skill before the first model request and registers `skill_search` and `skill_view`. Hermes does not run `skill-search` again from the shared startup text. Native Hermes skills stay in Hermes's own skills directory. A cron job gets a shared skill only when the job body already contains the procedure. A null result adds no shared skill. `skill_view` reads a Global Skills package from the snapshot before any native lookup.

## Cursor

Cursor discovers skills. It does not inject this selector from a prompt hook. Two pieces have to be present:

- `skill-discovery` in the skills directory Cursor already scans.
- An always-on startup instruction that runs `skill-search --select --receipt` before other tools. Listing the skill path is not enough. Put `--receipt` only on the Cursor command. Hermes, Codex, and Pi already write their own receipts. The app and the `agent` CLI both receive the user rule. The app also loads `~/.cursor/rules` when `alwaysApply` is true. A chat that is already open keeps the instructions it started with.

## Pi

Copy `adapters/pi/skill-select.ts` to `~/.pi/agent/extensions/skill-select.ts` and restart Pi. The extension handles `before_agent_start`, runs `skill-search --select`, and injects one verified body. A null result adds nothing. A receipt without the body is appended to `~/.local/state/jev-skill-advisor/pi-adapter/events.jsonl`. Listing `skill-discovery` alone does not make the command run.

## Other harnesses

Use the same command. If the harness can run a startup hook, have the hook call it and inject one verified body. If it can only list skills, install `skill-discovery` and an always-on instruction that runs the command. Do not paste the catalog into the prompt.

## Activation

- **Off:** no Jev call.
- **Shadow:** Jev suggests and telemetry records; agent behavior is unchanged.
- **Delivery:** the active release points at one immutable snapshot, and the harness adds one verified body or nothing.

Cloning this repo does not turn delivery on. Credentials, catalogs, prompts, transcripts, skill bodies, and the advisor database stay outside Git.
