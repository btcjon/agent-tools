# Agent Tools

## Small tools for agents with big jobs.

This repository is a public workshop for practical agent skills: keep machines updated without losing control, find useful skills without catalog bloat, tame an inbox without reckless deletion, coordinate worker fleets, connect Codex to a persistent Hermes Agent, and stop giant files from eating the context window.

Every tool has one job, visible guardrails, and a way to prove it works before you trust it with real work.

## Pick your problem

### [KSU — Keep Stuff Updated](skills/devops/ksu/)

![KSU keeps only your chosen tools updated](skills/devops/ksu/assets/hero.webp)

Discover the harnesses, packages, skills, plugins, and programs on a machine—then quietly maintain **only the targets you approve**.

**Best for:** controlled nightly maintenance without blind upgrades or surprise reboots.

### [Proactive Skill Suggestor](skills/discovery/proactive-skill-suggestor/)

![A scout finds the few useful skills in a galaxy of options](skills/discovery/proactive-skill-suggestor/assets/hero.webp)

Search the Skills Hub from recent work patterns, inspect the real candidates, and recommend the useful few without automatically installing anything.

**Best for:** discovering capability gaps without turning your skill library into a junk drawer.

### [Jev Skill Advisor](skills/discovery/jev-skill-advisor/)

![Jev narrows a huge skill catalog to one verified recommendation](skills/discovery/jev-skill-advisor/assets/hero.webp)

Select the relevant skill from a large canonical catalog without loading the whole warehouse into the main model's context.

**Best for:** harness builders who want bounded, measurable, advisory skill routing.

### [Gmail Triage](skills/email/gmail-triage/)

![A human-controlled system sorts a crowded inbox while protecting important mail](skills/email/gmail-triage/assets/hero.webp)

Review noisy senders, approve exact cleanup actions, unsubscribe safely, verify Gmail filters, and preserve an undoable record.

**Best for:** inbox cleanup where the user—not the automation—holds the final switch.

### [Hermes Router](skills/harness/hermes-router/)

![A secure bridge connects a desktop chat to a persistent remote Hermes Agent](skills/harness/hermes-router/assets/hero.webp)

Use Codex Desktop as the chat surface for an existing remote Hermes Agent while Hermes keeps its own inference, tools, permissions, and persistent sessions.

**Best for:** bringing a remote agent into Codex without putting a second model in the middle.

### [Herdr Orchestration](skills/harness/herdr-orchestration/)

![A coordinator safely directs a fleet of specialized AI workers](skills/harness/herdr-orchestration/assets/hero.webp)

Claim workers safely, route work through explicit coordinator profiles, enforce bounded repairs, and accept results from evidence rather than status labels.

**Best for:** running Herdr worker fleets without collisions, silent model substitutions, or recursive delegation chaos.

### [Shunt](skills/harness/shunt/)

![Shunt turns a mountain of file content into a handful of useful pointers](skills/harness/shunt/assets/hero.webp)

Catch oversized file reads and return compact names, paths, line ranges, and orientation points so the main model can spend context on judgment.

**Best for:** multi-harness teams tired of paying premium-model attention to raw bulk text.

## Install a skill

Clone the repository, then link or copy only the package you want into the skill directory used by your agent:

```bash
git clone https://github.com/btcjon/agent-tools.git
ln -s "$(pwd)/agent-tools/skills/<category>/<skill-name>" \
  ~/.hermes/skills/<skill-name>
```

Some packages also need a Python or Node installation step, an external connection, or harness-specific adapter wiring. Open that package's README first; copying a `SKILL.md` does not magically install its runtime.

## The rules behind the tools

- **No hidden authority:** discovery, advice, or review is not permission to mutate.
- **No secret soup:** credentials, transcripts, mailbox data, inventories, and runtime state stay outside this public repository.
- **No proof theater:** tests, dry runs, live canaries, and UI verification are different evidence.
- **No duplicate sprawl:** upgrade an existing capability when that is cleaner than adding another near-copy.

## Add another skill

Use one package per directory:

```text
skills/<category>/<skill-name>/
├── README.md
├── SKILL.md
├── scripts/
├── references/
└── tests/
```

Include only the folders the package needs. Keep receipts, transcripts, credentials, and generated runtime state out of Git.

MIT licensed unless a package states otherwise.
