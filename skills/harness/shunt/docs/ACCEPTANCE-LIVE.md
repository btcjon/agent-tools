# ACCEPTANCE-LIVE — final matrix (PKG6 integrate)

**When:** 2026-09-11  
**Package:** `custom-skills/skills/harness/shunt/`  
**Integrator:** cursor-auto-5 (PKG5 E2E + PKG6 ship)

## Sibling signals (wait complete)

| Signal | Path | Status |
| --- | --- | --- |
| PKG1 | `RECEIPT-PKG1.md`, `docs/LIVE-CURSOR-SHELL.md` | present |
| PKG2 | `docs/PHASE-0-APPLIED.md` | present |
| PKG3/4 | `docs/PARITY-MAC-VPS.md`, `docs/RELOAD-POLICY.md` | present |
| PKG5 | `docs/LIVE-CLAUDE-AGY-E2E.md` | present (both live loops **blocked**) |

## Final harness matrix

| Harness | Mode | Live oversized deny | Windowed / L50 | Notes |
| --- | --- | --- | --- | --- |
| **Cursor** | hard | **PASS** (PKG1 Shell+Read) | **PASS** | See `LIVE-CURSOR-SHELL.md` / `RECEIPT-PKG1.md` |
| **Codex** | hard (Bash); Read advisory | **PASS** | **PASS** | Hook-trust may be required |
| **Claude** | hard (registered) | Hook stdin PASS; **live E2E BLOCKED** | Hook stdin PASS; **live E2E BLOCKED** | PKG5: `401 OAuth access token has been revoked` (refresh 403; no capi sync) |
| **Pi** | hard (extension) | **PASS** | **PASS** | Fresh session / reload required |
| **Hermes** | hard (`config.yaml`) | Hook stdin PASS | PASS | Remote Hermes host timed out earlier; Mac config append present |
| **Grok** | hard | **PASS** | **PASS** | Fresh session required |
| **AGY** | hard (`~/.gemini`) | Hook stdin PASS; **live E2E BLOCKED** | Hook stdin PASS; **live E2E BLOCKED** | PKG5: `RESOURCE_EXHAUSTED` **429** (~48h reset) — **not a pass** |
| **Herdr** | inherit host | n/a | n/a | `docs/HERDR.md` + `RELOAD-POLICY.md` |

## Bulk-reader

OpenRouter `google/gemini-3.8-flash`: live `ok:true`, `stub:false`, HTTP 200 (key from `secrets.common.env`; never printed). Never CAPI/`gflash*` OAuth Flash.

## Phase-0 Flash kill

`docs/PHASE-0-APPLIED.md` — Mac applied; VPS config repo updated; VPS host unreachable for live verify.

## Mac ↔ VPS parity

`docs/PARITY-MAC-VPS.md` — **BLOCKED** (SDMM795 Tailscale offline).

## Residuals (honest)

1. Claude live CLI OAuth revoked — needs interactive re-login  
2. AGY live agent loop quota 429 — re-run after reset  
3. VPS parity / remote Hermes unreachable  
4. Stale Herdr panes need drain/restart per `RELOAD-POLICY.md`  
5. `shunt install` still registers noise `install.sh` hooks for some WP4 harnesses; WP4 installers remain authoritative for Pi/Grok/AGY/Hermes

## Evidence index

`docs/LIVE-*.md`, `docs/ACCEPTANCE.md`, `docs/PHASE-0-APPLIED.md`, `docs/PARITY-MAC-VPS.md`, `docs/RELOAD-POLICY.md`, `RECEIPT-PKG1.md`, `docs/LIVE-CLAUDE-AGY-E2E.md`, `RECEIPT-PKG6.md`
