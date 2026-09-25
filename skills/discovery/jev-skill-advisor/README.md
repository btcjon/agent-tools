# Jev Skill Advisor

![A wall of skills collapses through a prism into one card](assets/hero.webp)

## One shelf. Any harness. No catalog in the prompt.

Put every skill in one Notion library. Point Codex, Hermes, or the next harness at that same shelf. Jev reads the task and returns one skill in a single pass. The other six hundred never enter the context window.

That is the trade the old setup could not make. The symlink farm was either loaded, and expensive, or left on disk, and invisible. Here the library stays whole, the pick stays fast, and the prompt stays small.

Jev chooses. Your harness checks the card. A skill you named by hand always wins.

The payoff and the day-to-day checks are in the [guide](guide/README.md).

## What you get

- **One place for the whole library:** Notion's Agent Skills API is the shelf. Search **Global Skills**. Stop copying skill folders from machine to machine.
- **Harness agnostic:** Codex and Hermes already read the same verified copy. Another harness uses the same Python, JSON, or MCP door.
- **Jev's speed, not another model loop:** Jev chooses from the whole eligible catalog inside one call. It does not write an essay about which skill to open, and it does not spend a dozen turns searching.
- **No context-window bill:** the catalog stays out. The task receives one skill body. In the activation checks, that body ran from a few kilobytes to about 16 KB.
- **A card that still matches the shelf:** the body is checked against the catalog before it lands in the prompt. A mismatch adds nothing.
- **A receipt, not a transcript:** the log records which skill, how long, and how many bytes. It leaves the prompt and the skill body out.
- **No extra keys:** Jev cannot run a skill, grant a permission, or overrule a skill you named.

## How it works

1. **Publish.** Each skill becomes one Notion row: name, description, and a portable package in Files. The page body is the skill instructions. The Agent Skills API caps Files at 100 uploads and a plugin export at 100 skills, so each package travels as one bundle plus a manifest, and plugins are split into shards of 80.
2. **Snapshot.** Harnesses do not call Notion on each task. A rebuild promotes one verified snapshot. A failed rebuild leaves the previous snapshot in place.
3. **Choose.** `skill-search --select` asks Jev to pick one eligible skill, or none. A name you give skips Jev and still checks the snapshot hash.
4. **Deliver.** The harness checks that body against the snapshot and adds it. A timeout, a bad hash, or a missing credential adds nothing, and the task continues.

It is a card catalog that puts one book on the desk. It is not a librarian with the keys to the building.

## Try it

With a snapshot already active and `TYPESAFE_API_KEY` available outside the repo:

```bash
skill-search --select --task "the task in one sentence" --json
```

A result has one `selected` object, including the skill id, snapshot id, content hash, and body. `selected: null` adds nothing. Setup, per-harness wiring, and the day-to-day checks are in the [guide](guide/README.md). The command contract is in [`references/harness-integration.md`](references/harness-integration.md).

## Safe operating boundary

Keep credentials, catalogs, prompts, transcripts, skill bodies, and `advisor.sqlite3` outside Git and synchronized folders. A fresh install stays in shadow until you turn delivery on. A confident pick is still only a suggestion.

## Test it

```bash
uv run --with 'mcp>=2,<3' --with-editable . \
  python -m unittest discover -s tests
```
