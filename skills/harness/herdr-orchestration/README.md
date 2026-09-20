# Herdr Orchestration

![A coordinator safely directs a fleet of specialized AI workers](assets/hero.webp)

## Run a fleet of AI workers without turning the job into a stampede.

Herdr Orchestration gives a user-facing agent a portable operating policy for delegating work through Herdr: claim workers before prompting them, route the right work to the right tier, demand evidence, allow one bounded repair, and keep final acceptance with the coordinator.

It does not ship Herdr, create worker pools, or make missing models magically appear. It supplies the traffic rules that keep a capable fleet from becoming expensive chaos.

## What you get

- **Clear ownership:** the coordinator owns the objective, decomposition, tradeoffs, integration, and acceptance.
- **Collision control:** workers are claimed before prompts, and busy or foreign-owned panes stay untouched.
- **Deliberate routing:** Astra-main, Sol-main, and Auto-main profiles preserve explicit model and harness choices.
- **Bounded repairs:** one same-job repair budget prevents endless “one more pass” loops.
- **Evidence-first closure:** receipts and lifecycle labels are treated as claims—not proof that the work is correct.
- **No delegation recursion:** workers execute their assignment directly instead of spawning another fleet beneath it.

## How it works

1. **Choose a profile:** define who owns judgment and when an Astra review gate is required.
2. **Claim and dispatch:** reserve an eligible worker, send one bounded packet, and track one awaited handle.
3. **Verify and accept:** inspect the actual artifacts and tests, request at most one repair, then make the final call.

Think air-traffic control, not a group chat full of pilots shouting “I got it.”

## Requirements

- An installed Herdr CLI and a reachable session
- Python 3.10+
- macOS or Linux; the claims helper uses POSIX `fcntl`
- Configured judgment and ordinary-worker routes
- One shared host-local claims directory for cooperating coordinators

The stock `herdr` skill remains the CLI and product authority. This package governs coordination behavior; it does not replace the Herdr manual.

The policy currently defaults to `fleet bb`. That route requires a separately provisioned `bb-herdr` runtime and worker pool; this package does not install either one. If you have Herdr but not `bb-herdr`, select `fleet herdr` before dispatching work.

## Install

```bash
git clone https://github.com/btcjon/agent-tools.git
ln -s "$(pwd)/agent-tools/skills/harness/herdr-orchestration" \
  "<your-harness-skills-dir>/herdr-orchestration"
```

Installation alone does not launch workers, change global defaults, or enable orchestration. Read [installation and activation](references/install.md) before connecting it to a harness.

## Verify the package offline

```bash
cd skills/harness/herdr-orchestration
python3 scripts/check_package.py
python3 -m unittest discover -s tests -t .
```

## Start here

- [`SKILL.md`](SKILL.md) — concise operating contract
- [`references/mode.md`](references/mode.md) — ownership, routing, and repair rules
- [`references/profiles.md`](references/profiles.md) — Astra-main, Sol-main, and Auto-main
- [`references/packets.md`](references/packets.md) — worker briefs and receipts
- [`references/claims.md`](references/claims.md) — collision prevention
- [`CONTRACT-SNIPPET.md`](CONTRACT-SNIPPET.md) — optional default-on global fragment

Private host bindings, credentials, live pool layouts, transcripts, and benchmark claims stay outside this package.
