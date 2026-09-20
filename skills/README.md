# Skill layout

One package per directory:

```
skills/
  <category>/
    <skill-name>/
      SKILL.md
      scripts/
      references/
      tests/
```

Current categories:

- `devops/` — controlled discovery, updates, and host maintenance
- `discovery/` — finding, ranking, and suggesting skills
- `email/` — email triage, review, and mailbox hygiene
- `harness/` — agent routing, orchestration, and context controls

Rules:

- `SKILL.md` `name:` must match the directory name.
- Do not nest two skill packages in one folder.
- Keep receipts and transcripts out of git.
