# Pi capability bridge

`skill-select.ts` remains Pi's startup skill selector. When its selected skill includes a correlated Jev capability, it opens the same host-local, full-release-pinned stdio bridge used by Cursor and Codex. It validates the exact five-tool schema before registering tools, then removes those tools from the active set on a later irrelevant, repeated, oversized or marker-bearing turn. The bridge—not Pi—checks receipts, release identity, Notion workspace and read-only execution.

The extension imports `bridge-protocol.ts`, so install both files together under `~/.pi/agent/extensions/` after backing up any existing extension. Do not copy only `skill-select.ts`. The helper uses the host-local `~/.local/state/jev-skill-advisor/runtime/current` pointer, independent of its installed directory. A release or schema change requires reviewing the full pin/path/schema contract; mismatches fail closed. The current Mac release pin is `a528d001220eec155f1172f3b9b76793177ae2479dd5d054ae246c047e2e26bc`.

The 2026-09-25 Mac installation backed up the old extension at `/Users/jonbennett/.local/state/jev-skill-advisor/registration-backups/pi-bridge-20260925-before/skill-select.ts`. Check rollback readiness with `bash adapters/pi/rollback-bridge.sh --check`; restore in one command with `bash adapters/pi/rollback-bridge.sh`. The script refuses to overwrite intervening host edits and moves the new helper into the backup directory rather than deleting it.

Checks before installation:

```sh
node --test adapters/pi/*.test.ts
```

For an isolated read-only Pi canary, run Pi with `--no-extensions --extension <absolute-path-to-skill-select.ts> --no-session`. Check `~/.local/state/jev-skill-advisor/pi-adapter/bridge.jsonl`: an irrelevant turn should have no `bridge_open` and add zero bridge schemas; a relevant Notion page-read turn should record five schemas (currently 2,927 JSON bytes). No page body, page ID, prompt or token is logged. The existing skill selector can still inject an unrelated skill on an irrelevant turn; that is a separate selector-quality issue, not bridge schema overhead.

Installed-path checks on 2026-09-25: a fresh Pi model called `skill_suggest` and `notion-fetch`; an adapter read of an existing Notion skill page matched an independent `ntn` GET at 10,135 markdown bytes and SHA-256. Missing and forged receipts were denied with `unauthorized_capability` and no markdown; a bad token produced no usable receipt or page body, and a mismatched full release pin did not open. Relevant/repeated/marker/relevant turns had active bridge tool counts 5/0/0/5; a separate relevant/irrelevant sequence had 5/0. Pi's content-free logs contained no page ID, body-text probe or credential value. Keep native Notion routes intact until broader rollout gates pass.

Production caveats: confirm the host-local runtime pointer targets a verified immutable bundle before relying on it long-term; release rollover currently requires a Pi restart after hard schema/release mismatch; repeated call timeouts can leave a hung child; and task de-duplication is module-global rather than session-scoped. The rollback script is one-use and should be followed by a Pi restart, because already-open sessions retain their loaded code.
