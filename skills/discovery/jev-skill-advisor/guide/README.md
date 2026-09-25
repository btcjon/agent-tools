# Jev Skill Advisor

![A wall of skills collapses through a prism into one card](../assets/hero.webp)

## One shelf. Any harness. The wall stays out of the prompt.

Look at the picture. The wall is every skill, in one Notion library. The beam is Jev, picking in a single pass. The card in her hand is the only thing the model reads.

Any harness can stand at that shelf. Codex, Hermes, and Pi inject the chosen skill. Cursor runs the same `skill-search --select` command from a startup instruction. You stop keeping a symlink farm honest across machines, and you stop paying the context window to carry skills the task will never use.

## What you get

- **One place:** search **Global Skills**. Each row is one skill. Files holds the portable package.
- **Any harness:** Codex, Hermes, Cursor, and Pi read one verified snapshot. The contract is `skill-search --select`, not a private loader.
- **One pick:** Jev sees every eligible skill in one `skill-search --select` call and returns one skill, or none. The main model does not rank the library.
- **No context hit for the rest:** the other ~600 skills stay out of the prompt. The one body that lands ran, in the activation checks, from a few kilobytes to about 16 KB.
- **Skills that used to sit unread:** a skill does not have to be in the startup list to be chosen on a later task.
- **Your named skill still wins:** `$skill-name` loads that skill. Jev is not asked.
- **A quiet miss:** timeout, no match, a bad hash, or a missing credential adds nothing. The task continues on the harness's normal path.
- **A receipt, not a transcript:** the adapter log records the skill id, the wait, and the injected size. The prompt and the skill body stay out.

## How it works

1. **Publish to Notion.** The destination is a typed Agent Skills database: Skill name, Description, Files, and Tags. Files accepts at most 100 uploads, and a plugin export holds at most 100 skills. Each skill therefore ships as two attachments, a package bundle and a transport manifest, and plugins are split into shards of 80. The bundle keeps paths, bytes, and file modes even when a package has more than 100 source files.
2. **Snapshot, then route.** Harnesses do not call Notion on every task. They read a verified local snapshot. A Notion edit reaches them after that snapshot is rebuilt and promoted. A failed refresh leaves the last good snapshot in place.
3. **Choose.** Inside one call, Jev picks a winner from each batch of the catalog, then picks among those winners. A miss adds nothing.
4. **Deliver.** The harness checks that one body against the snapshot hash and adds it.

It is a card catalog that puts one book on the desk.

## What this does not do

- Notion is the shelf and the editor. A page edit reaches harnesses after the snapshot is rebuilt. A local file write does not update the library.
- Jev judges the eligible catalog in that one call. It does not rank a lexical shortlist first.
- It does not shrink conversation history, system instructions, tool definitions, or skills the harness already loads at startup.
- It does not run the skill, grant permissions, or override a skill you named.
- Cold routing in the activation checks took a few seconds. That timing is from those runs, not a promise.
- Cloning the repo does not turn delivery on. A new install stays in shadow until a reviewed host release says otherwise.

Day-to-day checks are in the [how-to](how-to.md). A safe first trial is [Try it](../TRY_IT.md). Profiles, snapshots, and rollback are in [operations](../references/operations.md).
