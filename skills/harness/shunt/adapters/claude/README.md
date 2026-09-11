# Claude Code adapter (hard hooks)

Spotify-style PreToolUse gate for oversized full reads.

| Matcher | Behavior |
| --- | --- |
| `Read` | Deny full read of ≥ `min_lines` files; allow `offset`/`limit` |
| `Bash` | Deny `cat` of oversized files; allow `head`/`tail` windows |

On deny: `permissionDecision: deny` + reason telling the agent to run `shunt bulk-read <path>`.

## Install (merge-safe)

`shunt install` merges `registration.json` into `~/.claude/settings.json` under `hooks`, **keeping** existing hooks (Orca / `UserPromptSubmit` / others). It only replaces prior shunt-marked entries.

```bash
shunt install --dry-run
shunt install
```

Do not hand-delete unrelated hook blocks when merging manually.

## Proof

```bash
python3 adapters/run_fixture.py \
  adapters/claude/pre_tool_use.py \
  adapters/claude/tests/fixtures/read_oversized.json
# expect: permissionDecision deny + exit 2 + stderr mentions shunt bulk-read
```
