---
name: ksu
description: "Keep Stuff Updated (KSU): discover AI harnesses, packages, skills/plugins and programs; let users select exact update targets per machine; run quiet nightly maintenance with protected sources and bounded recovery."
---

# KSU — Keep Stuff Updated

Prioritize **AI harnesses → packages/tools → skills/plugins → other programs**. Use the bundled Python discovery and selection workflow before scheduling updates. Each host has its own inventory and saved selections; discovering software is not permission to update it.

Read [setup and operation](references/setup.md) for commands, supported adapters, scheduler limitations, and migration from v0.1. Python 3.9+ is sufficient except uv receipt inspection, which requires Python 3.11+.

## Discover, select, then enroll

1. Identify the requested machine/account through its existing access route. Run `python3 scripts/ksu.py discover`. Discovery needs no Topgrade installation and performs no upgrades. Add `--root /path/to/skills` for additional skill sources; these roots persist for subsequent scans.
2. Present `inventory.html` as a local checkbox page or `inventory.md` as an agent-readable list. Harnesses come first. Show versions, installation locations, sources, repository branches/dirty state, recommended selections, blockers, and the discovery-gaps section. Multiple installations are separate targets. Symlinked skills share their real source; source repository rows include the full update scope.
3. Ask the user to choose what should stay updated. Import the checkbox page's JSON with `select --file FILE`, use `select --interactive` in a terminal, or apply the user's explicit choices with `select --enable ID ... --exclude ID ...`. These commands save preferences only. Do not translate general enthusiasm into checking every item. Existing user selections remain valid for the same source and scope.
4. Run `setup --time 03:00 --restart-policy services`, then `preview` to show the exact plan. The default permits native app/service restarts and defers full reboots. `reboot` is opt-in; strict `defer` blocks adapters that cannot enforce no restarts.
5. Within authorized enrollment scope, run the selected updates, read the receipt, verify important service/app behavior, and then `schedule`. Report enrolled hosts, selected coverage, unresolved selections/probe errors, scheduler state, and restart policy. Leave unreachable hosts and unsupported updates explicitly pending.

## Selection contract

Selections are bound to the host/account, target ID, installation source and update method. New items remain unselected. Source, branch or installation changes require re-selection; version changes alone do not. Explicit exclusions persist. A missing item is blocked and must be re-selected if it reappears. Stale or foreign checkbox exports are rejected atomically. Never accept update commands from a selection file.

Nightly runs rediscover first and generate **exact-target commands**, not manager-wide upgrades. Native managers can also update required dependencies; disclose this scope. Selecting a skill source updates its entire repository, including non-skill files. The updater refuses held/pinned packages, dirty or custom-branch skill repositories, unverified native harness procedures, and consumer skill projections. Unsupported rows remain visible but cannot be enabled until a reviewed adapter exists.

## Harnesses and shared skills

Use package-owned harness entries when discovered and verified. Source checkouts and custom harness builds need their maintained update/deploy process; never reset branches, discard local commits, recreate environments, or restart gateways generically. Service definitions and available runtime states are inventory context, not proof of a successful restart or healthy application.

A recognized Codex Router Git checkout at `~/.local/share/codex-router` is a first-class harness. Its adapter runs the official `codex-router update` command, not a generic `git pull`. Tracked local edits, a non-`main` checkout, or an unrecognized origin stay blocked. Untracked files do not block the official updater. The managed `~/.codex/skills/codex-router*` copies are consumer projections of that checkout, not independent update targets.

Identify the shared skill authority from the local skill-system manifest. Consumer projections are never independent update targets. Use the user's authority publication workflow for governed skills. Ordinary clean main/master skill-source repositories can be selected as whole-source updates; customized, detached, dirty or unresolved repositories remain blocked. Plugin caches are not proof of active installation and are updated through their owning harness.

## Quiet maintenance and recovery

The host-local runner, native scheduler, process lock, receipt, and hourly watchdog operate without an open chat. Only transient network/lock failures get two retries; successful targets are not replayed because another failed. Unknown and permission failures remain visible locally. Never kill uncertain package transactions, remove locks, weaken signature checks, or force removals as generic recovery. Read back the installed state before diagnosing or replaying an interrupted run.

A zero updater exit plus rediscovery proves command completion and continued presence, not that every app is current or every service is healthy. Perform provider-specific checks for consequential harness changes. Reboot decisions, probe gaps, blocked selections, failures, and newly found items remain in private state. Quiet does not mean silently declaring success.

Keep this public skill secret-free. Inventories, exported selections, config, logs, locks and scheduler state remain on their owning machine. Ephemeral sandboxes need provisioning/base-image maintenance; this package does not automatically traverse or enroll a fleet.
