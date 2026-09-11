# RECEIPT — PKG2 Flash/OAuth kill

**Task:** `…/herdr-workers/cursor-auto-3/tasks/pkg2-flash-kill.md`  
**Worker:** cursor-auto-3  
**When:** 2026-09-11  
**Result:** APPLIED + acceptance 15/15 (see `docs/_pkg2_probes/final-acceptance.txt`)

## Deliverables

- `docs/PHASE-0-APPLIED.md`
- `docs/RECEIPT-PKG2.md` (this file)
- Probe artifacts under `docs/_pkg2_probes/`

## Backup

`~/.shunt/backups/pkg2-flash-kill-20260911T125402Z/`

## Exact files changed

### Live runtime (Mac)

- `~/.cli-proxy-api/config.yaml` — remove Flash alias; exclude Flash under `gemini` / `gemini-cli` / `antigravity`
- `~/.pi/agent/teams.yaml` — remove `gemini-flash` from pools
- `~/.pi/agent/agents/gemini-flash.md` — model `openai-codex/gpt-5.4-mini`
- `~/.pi/agent/agents/gemini-flash-3.md` — model `openai-codex/gpt-5.4-mini`
- `~/.pi/agent/agents/worker-medium.md` — model `openai-codex/gpt-5.4-mini`
- `~/.pi/agent/models.json` — remove Flash entries from `capi` + `google-gemini-cli`; `_pkg2_notes` do-not-restore
- `~/.pi/agent/prompts/delegate.md` — Fast tier off OAuth Flash dispatch
- `~/.claude/scripts/cc-fallback` — sonnet/haiku → gpt-5(high)
- `~/.claude/scripts/test-cli-rotation.sh` — Pro probe only
- `~/.claude/tools/registry.json` — Gemini tool enum/description off Flash
- `~/.hermes/skills/herdr/references/mode.md` — Flash ≠ OAuth Gemini Flash

### VPS config repo (Dropbox; host unreachable for live apply)

- `Dropbox/Projects/cliproxy-vps-config/config/config.yaml` — remove `gflash`; exclude Flash models
- `Dropbox/Projects/cliproxy-vps-config/docs/CURSOR-SETUP.md` — (prior pass) examples off Flash

### Architecture / operator docs

- `~/.agents/docs/architecture/cliproxyapi.md`
- `~/.agents/docs/architecture/cli-agents.md`
- `~/.agents/docs/architecture/delegation.md`
- `~/.agents/docs/architecture/memory.md`
- `~/.agents/docs/architecture/pi-memory.md`
- `~/.agents/docs/architecture/overview.md`
- `~/.agents/docs/architecture/litellm-proxy.md`
- `Dropbox/AI-Control-Plane/hermes/native-skills/capi/SKILL.md`

### Package docs (this repo)

- `docs/PHASE-0-APPLIED.md`
- `docs/RECEIPT-PKG2.md`
- `docs/_pkg2_probes/*` (acceptance evidence; may include ephemeral probe config/logs)

## Not changed (by design)

- Cursor shunt adapter code (PKG1)
- OpenRouter bulk-reader path / `google/gemini-3.8-flash` identity
- Gemini OAuth credential JSON under `~/.cli-proxy-api/` (Pro retained)
- No git commit

## Acceptance evidence (decisive)

1. Isolated local `cli-proxy-api` on `:18317` with live auth-dir + updated Mac config:
   - `/v1/models` Flashish IDs: **none**
   - chat `gflash` / `gemini-3-flash-preview` / `gemini-2.5-flash` → **error, no choices**
2. `shunt bulk-read` on `tests/fixtures/live_harness_gate.txt` (secrets sourced, key not printed):
   - `ok: true`, `stub: false`, `model: google/gemini-3.8-flash`
3. Herdr pool: **no** Flash-named workers to drain

## Rollback notes (MUST NOT restore OAuth Flash)

Safe rollback of unrelated breakage:

1. Diff against `~/.shunt/backups/pkg2-flash-kill-20260911T125402Z/` for the specific file.
2. Restore only non-Flash intent (e.g. accidental pool membership, Pro alias typos).
3. **Never** re-add:
   - `gflash*` aliases
   - `gemini-3-flash` → `gemini-3-flash-preview` Antigravity/Gemini aliases
   - `capi/gemini-3-flash-preview` or `google-gemini-cli/gemini-3-flash-preview` in Pi models/agents
   - `cc-fallback` sonnet/haiku → Gemini Flash
   - OpenRouter `google/gemini-3.8-flash` as a silent Flash/OAuth fallback

If a full restore from backup is required for emergency continuity, **immediately re-apply** the Flash exclusions and alias removals from this receipt before returning the system to service.

## Caveats

- VPS live endpoint/SSH timed out from this worker; repo config is ready for deploy.
- Production Mac `:8317` was down; fail-closed proven on isolated `:18317` with the same auth-dir and updated config.
