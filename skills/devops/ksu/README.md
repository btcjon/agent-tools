# KSU — Keep Stuff Updated

![KSU keeps only your chosen tools updated](assets/hero.webp)

## Stop babysitting updates. Keep control of what changes.

KSU finds the AI harnesses, packages, skills, plugins, and programs on a machine—then keeps **only the targets you approve** up to date.

No blind “update everything” button. No surprise reboot at 3 a.m. No bulldozing a dirty Git checkout. KSU inventories first, saves your exact choices, runs quiet maintenance, and leaves a receipt.

## What you get

- **One clear inventory:** versions, locations, update sources, blockers, and gaps.
- **Exact-target updates:** new discoveries stay off until you select them.
- **Safer automation:** held packages, dirty repositories, custom branches, and uncertain procedures are refused.
- **Host-by-host control:** every machine keeps its own selections, schedule, state, and receipts.
- **Quiet recovery:** transient failures get bounded retries; uncertain transactions are never force-killed.

## How it works

1. **Discover** what is installed—without changing anything.
2. **Select** the exact targets you want KSU to maintain.
3. **Preview, run, and schedule** the approved plan with your restart policy.

Think of it as a careful night-shift mechanic with a clipboard: it touches the machines you circled and leaves the rest alone.

## Quick start

```bash
cd skills/devops/ksu
python3 scripts/ksu.py discover
```

Review the generated `inventory.html` or `inventory.md`, then save your choices:

```bash
python3 scripts/ksu.py select --interactive
python3 scripts/ksu.py setup --time 03:00 --restart-policy services
python3 scripts/ksu.py preview
```

KSU does not treat discovery or setup as permission to update everything. Read [setup and operation](references/setup.md) before enrolling a machine.

## Good fit

Use KSU when you want repeatable maintenance across macOS, Linux, or Windows without surrendering target selection. It is not a fleet-wide “YOLO upgrade” command, and it does not automatically enroll ephemeral machines.

## Requirements

- Python 3.9+
- Python 3.11+ for uv receipt inspection
- A reviewed updater for each target you enable

## Test it

```bash
python3 -m unittest discover -s tests -t tests
```
