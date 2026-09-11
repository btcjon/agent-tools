# Phase 0 — Flash / OAuth kill APPLIED (PKG2)

**Date:** 2026-09-11  
**Worker:** cursor-auto-3  
**Inventory:** `docs/PHASE-0-INVENTORY.md`  
**Status:** APPLIED (Mac live + VPS config repo; VPS host unreachable for live deploy/verify)

## Intent (locked)

1. Kill Gemini Flash on Google OAuth / CAPI (`gflash*` / `gemini-*-flash*`) — fail closed; no silent fallback onto the rate-limited account.
2. Keep OpenRouter `google/gemini-3.8-flash` as the **paid bulk-reader only** (`shunt bulk-read`). Never wire it as an OAuth Flash fallback.
3. Herdr “Flash” means a non-OAuth ordinary worker class — not Gemini OAuth Flash.
4. Do not touch Cursor adapter code (PKG1). Do not commit.

## Backup (pre-apply)

`~/.shunt/backups/pkg2-flash-kill-20260911T125402Z/`

Contains snapshots of: Mac cliproxy config, VPS cliproxy config, Pi `teams.yaml` / agents / `models.json`, `cc-fallback`.

**Rollback policy:** Restoring from this backup for unrelated breakage is allowed only after **re-applying the Flash kill** (aliases removed + `oauth-excluded-models` Flash entries present). **Do not** restore OAuth Flash aliases or Flash model catalog entries as a “fix.”

## What changed (runtime)

| Surface | Change |
|---------|--------|
| Pi `teams.yaml` | Removed `gemini-flash` from `model-pool` / `fast-pool` |
| Pi agents `gemini-flash.md`, `gemini-flash-3.md`, `worker-medium.md` | Model → `openai-codex/gpt-5.4-mini` (not OpenRouter Gemini) |
| Pi `models.json` | Removed `capi/gemini-3-flash-preview` and `google-gemini-cli/gemini-3-flash-preview` (`google-gemini-cli` models list empty) |
| Mac `~/.cli-proxy-api/config.yaml` | Removed Antigravity Flash alias; `oauth-excluded-models` under **`gemini`**, `gemini-cli`, and `antigravity` for Flash IDs |
| VPS repo `cliproxy-vps-config/config/config.yaml` | Removed `gflash` alias; Flash IDs under `oauth-excluded-models.gemini-cli` |
| `~/.claude/scripts/cc-fallback` | sonnet/haiku → `gpt-5(high)` (no Gemini Flash) |
| `test-cli-rotation.sh` | Probe Pro, not Flash |
| Herdr `mode.md` | Flash = non-OAuth ordinary workers; forbid OAuth/CAPI Flash restore |
| Docs / prompts / CAPI skill / registry | Operator paths retargeted off OAuth Flash |

## Herdr drain

No dedicated Flash workers under `~/.local/share/herdr-workers/` (pool is cursor/grok/pi-glm/pi-grok/astra/canaries). Nothing to stop.

## Acceptance

Artifacts: `docs/_pkg2_probes/` (`final-acceptance.txt` **15/15 PASS**).

| Check | Result |
|-------|--------|
| Config / teams / agents / models static probes | PASS |
| Local CLIProxyAPI (port 18317 probe) catalog — no Flash IDs | PASS |
| Chat `gflash` / `gemini-3-flash-preview` / `gemini-2.5-flash` | FAIL CLOSED (`unknown provider…`) |
| `shunt bulk-read` fixture with secrets sourced | PASS — `ok:true`, `stub:false`, `model:google/gemini-3.8-flash` |
| Mac production `:8317` | Was down during apply; probe used isolated `:18317` with live auth-dir + updated config |
| VPS `5.161.192.198` SSH/HTTP | Timed out — **config repo updated; live VPS not redeployed from this worker** |

## Explicit non-actions

- Did not point `gemini-flash` / `gflash` at OpenRouter `google/gemini-3.8-flash`.
- Did not delete Gemini OAuth credentials (Pro still usable).
- Did not edit Cursor shunt adapters.
- Did not commit.

## Follow-up (operator)

1. When VPS is reachable: deploy updated `cliproxy-vps-config/config/config.yaml`, restart `cliproxyapi`, confirm `/v1/models` has no `gflash` / Flash IDs and chat to those models fails closed.
2. If Mac `:8317` service is started later, it will reload `~/.cli-proxy-api/config.yaml` (already killed).
