---
name: herdr-orchestration
description: Coordinator orchestration for Herdr fleets (Astra-main, Sol-main, Auto-main). Use when claiming panes, gating plans with Astra, routing ordinary workers, closing alignment, or supervising Herdr jobs. Companion to the stock herdr CLI skill—not a replacement.
---

# Herdr Orchestration: Astra-main, Sol-main, and Auto-main

## Trigger

Use this skill when the main agent should **orchestrate** work through Herdr worker panes: claims, profile rules, early/closing Astra gates, ordinary-pool execution, receipts, and acceptance.

Do **not** use this skill as the Herdr CLI manual. Discover and follow the stock **`herdr`** skill for product commands. Neither skill replaces the other.

## Coordinator vs worker

| Role | Owns |
|---|---|
| **Coordinator** (user-facing main agent) | Objective, decomposition, acceptance criteria, routing, evidence judgment, quality enforcement, final acceptance |
| **Ordinary workers** | Implementation, research, debugging, repairs, tests/fixtures, routine checks |
| **Astra (Sol-main / Auto-main)** | Plan/approach intelligence, quality clarification, closing recommendation—**not** named worker selection. In **Auto-main**, Astra **does not implement** |

Workers execute their bounded assignment **directly**. They never recursively activate Herdr orchestration mode.

## Core invariants

1. **Claim before any `agent prompt`.** Claim failure → zero prompts on that pane.
2. Never prompt a busy, blocked, or foreign-owned pane.
3. One outstanding awaited handle per job; receipts and lifecycle (`idle`/`done`) are **claims**, not proof of completion.
4. At most **one** same-job repair budget; closing review does **not** reset it.
5. No silent model substitution; missing required Astra is a disclosed blocker.
6. Single writer per file set unless explicitly coordinated.
7. Direct coordinator execution only for documented exceptions (see mode reference).

## Profiles (one per conversation)

- **Astra-main** — Astra owns judgment/routing/acceptance; ordinary non-OpenAI pool executes.
- **Sol-main** — Sol-class coordinator owns conversation/routing/acceptance; early Astra gate before plan lock; Astra may own consequential core implementation when assigned.
- **Auto-main** — Auto owns conversation/routing/quality/acceptance; early Astra gate; Astra intelligence-only; mandatory checks then closing alignment before accept.

Full rules: [references/mode.md](references/mode.md). Surface matrix: [references/profiles.md](references/profiles.md).

## Progressive disclosure

| Need | Read |
|---|---|
| Activation / ownership / routing | [references/mode.md](references/mode.md) |
| Packets & receipts | [references/packets.md](references/packets.md) |
| Claims semantics | [references/claims.md](references/claims.md) |
| Job runbooks | [references/runbooks.md](references/runbooks.md) |
| Herdr CLI adapter | [references/adapters/herdr.md](references/adapters/herdr.md) |
| Install / harness load | [references/install.md](references/install.md) |
| Opt-in default-on contract | [CONTRACT-SNIPPET.md](CONTRACT-SNIPPET.md) |
| Synthetic examples | [examples/](examples/) |

## Claims helper

```bash
python3 <skill>/scripts/claims.py claim --session <session> --worker <pane-id> --owner <owner>
python3 <skill>/scripts/claims.py status --session <session> --worker <pane-id>
python3 <skill>/scripts/claims.py release --session <session> --worker <pane-id> --owner <owner>
```

Default state dir: `~/.local/state/herdr-mode` (override with `--state-dir`). POSIX only in MVP.
