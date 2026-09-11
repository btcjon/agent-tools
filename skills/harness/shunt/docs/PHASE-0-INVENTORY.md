# Phase 0 — Flash / Gemini → OAuth·CAPI pool inventory

**Package:** `custom-skills/skills/harness/shunt`  
**Date:** 2026-09-11  
**Worker:** cursor-auto-3  
**Scope:** Live paths that route “Flash” / Gemini into the rate-limited Google OAuth / CAPI Gemini pool.  
**Locked decisions (coordinator):**

1. Remove Gemini models from the Flash lane / CAPI OAuth path entirely (rate-limited).
2. OpenRouter `google/gemini-3.8-flash` is the **paid bulk-reader only** — separately named; **never** an OAuth Flash fallback.
3. This inventory is evidence + a kill list only. Do **not** disable live CAPI here.

**Bulk-reader policy (explicit):** Zero recommended OAuth fallback for bulk-reader traffic. If OpenRouter bulk-reader is unavailable, fail closed or use a non-Google paid route — do not fall back to `gflash` / `gemini-3-flash-preview` / `google-gemini-cli` / Antigravity Flash.

---

## Pool map (what “OAuth / CAPI Gemini” means here)

| Pool | Evidence | Host |
|------|----------|------|
| Local CLIProxyAPI Gemini OAuth | `~/.cli-proxy-api/config.yaml` + Gemini auth JSON under `~/.cli-proxy-api/`; docs say Gemini OAuth on `/v1/chat/completions` with `gflash*` aliases | Mac (live files). Local `127.0.0.1:8317` was **down** during Phase 0 probe (`curl` empty). |
| VPS CLIProxyAPI `gemini-cli` OAuth | `Dropbox/Projects/cliproxy-vps-config/config/config.yaml` aliases `gemini-3-flash-preview` → `gflash` | VPS (config repo on Mac Dropbox) |
| Pi `google-gemini-cli` | `~/.pi/agent/models.json` provider `google-gemini-cli` → `https://cloudcode-pa.googleapis.com`; agents pin `gemini-3-flash-preview` | Mac (and historically VPS Pi under `/home/dev`) |
| Pi / remote `capi` Gemini Flash | `~/.pi/agent/models.json` provider `capi` → `https://llm-proxy.genr8ive.ai/v1` with model `gemini-3-flash-preview` | Mac + remote CAPI edge |
| Gemini CLI OAuth (direct) | `gemini` shell function + `~/.gemini/`; docs default Fast = `gemini-3-flash-preview` | Mac |

**Not in the kill set as OAuth Flash:** Hermes OpenRouter catalog entry `google/gemini-3.8-flash` (`~/.hermes/cache/model_catalog.json`) — treat as **paid bulk-reader identity only**, never aliased to Flash/OAuth slots.

---

## Inventory table

| Path/config | Mechanism | Model/alias | Host | Risk if left | Proposed action |
|-------------|-----------|-------------|------|--------------|-----------------|
| `~/.cli-proxy-api/config.yaml` | `oauth-model-alias.antigravity`: `name: gemini-3-flash` → `alias: gemini-3-flash-preview` | `gemini-3-flash` / `gemini-3-flash-preview` | Mac | Any client asking Antigravity/Gemini Flash hits OAuth pool; quota thrash | **Disable** Flash alias / exclude Flash from Gemini OAuth exposure (keep Pro if still needed under separate policy) |
| `~/.cli-proxy-api/*gemini*.json` (auth tokens) | Gemini OAuth credentials for CLIProxyAPI | Gemini account pool | Mac | Enables rate-limited Flash completions even after alias cleanup if models still registered | **Doc-fix** + later disable Flash models only; do not delete Pro auth in Phase 0 |
| `Dropbox/Projects/cliproxy-vps-config/config/config.yaml` | `oauth-model-alias.gemini-cli`: `gemini-3-flash-preview` → `gflash` (+ fork) | `gflash` | VPS | Canonical short alias for bulk/fast traffic on VPS CAPI | **Disable** `gflash` alias; exclude `gemini-3-flash-preview` from OAuth catalog (or add to `oauth-excluded-models.gemini-cli`) |
| `~/.agents/docs/architecture/cliproxyapi.md` | Documents Gemini OAuth + aliases `gflash`, `gflash-low`, `gflash-high` and upstream `gemini-3-flash-preview` | `gflash*` | Doc (Mac/VPS shared) | Operators keep sending Flash to OAuth | **Doc-fix**: remove Flash from OAuth lane; point bulk-read to OpenRouter `google/gemini-3.8-flash` under a distinct name |
| `Dropbox/AI-Control-Plane/hermes/native-skills/capi/SKILL.md` | Lists local `8317` / remote CAPI and short aliases including `gflash*` | `gflash*` | Doc | Same | **Doc-fix** |
| `Dropbox/Projects/cliproxy-vps-config/docs/CURSOR-SETUP.md` | Examples use `model: gemini-3-flash-preview` | `gemini-3-flash-preview` | Doc | Cursor/SDK copy-paste onto OAuth | **Doc-fix** → non-OAuth model IDs |
| `~/.pi/agent/agents/gemini-flash.md` | Fast-pool worker definition | `google-gemini-cli/gemini-3-flash-preview` | Mac | **Highest runtime blast:** `teams.yaml` `fast-pool` includes `gemini-flash` | **Reroute** agent to non-OAuth fast worker (e.g. keep slot name or rename); **never** point to OpenRouter Gemini as silent Flash fallback — if Gemini bulk-read needed, new separately named agent |
| `~/.pi/agent/agents/gemini-flash-3.md` | Fast worker instance 3 | `google-gemini-cli/gemini-3-flash-preview` | Mac | Same OAuth Cloud Code Assist 429 class | **Disable** or reroute off Gemini OAuth |
| `~/.pi/agent/agents/worker-medium.md` | Medium worker | `capi/gemini-3-flash-preview` | Mac | Medium lane still burns CAPI Gemini Flash | **Reroute** off Gemini Flash CAPI |
| `~/.pi/agent/teams.yaml` | `fast-pool` / `model-pool` list `gemini-flash` | slot `gemini-flash` | Mac | Coordinators auto-dispatch recon to OAuth Flash | **Remove** from `fast-pool` (or replace with non-Gemini slot) |
| `~/.pi/agent/prompts/delegate.md` | Tier 1 Fast/Recon table + dispatch examples use `gemini-flash`; `flash`⇒`fast` alias | `gemini-flash` / `flash` | Mac | Prompt gravity toward Flash OAuth | **Doc/prompt-fix** |
| `~/.pi/agent/models.json` | Providers `capi` + `google-gemini-cli` both expose `gemini-3-flash-preview` | `gemini-3-flash-preview` | Mac | Registry keeps Flash callable | **Disable** Flash model entries on those providers (leave Pro if authorized) |
| `~/.agents/docs/architecture/delegation.md` | States Hermes `fast_worker` → `gemini-3-flash-preview` via `http://localhost:8317/v1`; Pi fast pool includes `gemini-flash` | `gemini-3-flash-preview` / `gemini-flash` | Doc | Stale Hermes mapping may be re-applied | **Doc-fix**; verify live Hermes has no `fast_worker` Gemini lane (live `~/.hermes/config.yaml` has empty `delegation.model/provider` — no live Flash pin found) |
| `~/.agents/docs/architecture/overview.md` | Architecture diagram: Gemini Flash via CAPI for cron/light/heartbeats | GFlash | Doc | Mental model keeps Flash on free OAuth | **Doc-fix** |
| `~/.agents/docs/architecture/cli-agents.md` | “Fast (free)” = `gemini-3-flash-preview`; `gemini` default Flash | `gemini-3-flash-preview` | Doc | Encourages CLI OAuth Flash | **Doc-fix** |
| `~/.agents/docs/architecture/pi-memory.md` / `memory.md` | Observer primary `gemini-3-flash-preview` via Google Gemini CLI OAuth | `gemini-3-flash-preview` | Doc / runtime if still wired | Background observers burn OAuth Flash | **Reroute** observer off OAuth Flash; **not** to bulk-reader OpenRouter Gemini unless separately budgeted and named |
| `~/.claude/scripts/cc-fallback` | Emergency CC fallback: sonnet/haiku → `gemini-3-flash-preview` via `llm-proxy.genr8ive.ai` | `gemini-3-flash-preview` | Mac | Exhaustion path dumps onto CAPI Gemini Flash | **Disable** Gemini Flash targets; use non-Gemini fallbacks |
| `~/.claude/scripts/test-cli-rotation.sh` | Smoke: `gemini -y -m gemini-3-flash-preview` | `gemini-3-flash-preview` | Mac | Test noise on OAuth | **Doc/script-fix** |
| `~/.claude/tools/registry.json` | Tool blurb: default `gemini-3-flash` | `gemini-3-flash` | Mac | Tool routing hints | **Doc-fix** |
| `~/.hermes/skills/herdr/references/mode.md` | Prefer “… or Flash …” for ordinary pool | “Flash” (worker class) | Doc | Ambiguous: may mean Gemini Flash lane | **Doc-fix**: define Flash as non-OAuth ordinary worker class; forbid Gemini OAuth Flash |
| Gemini CLI defaults (`cli-agents.md`, shell `gemini` function) | Direct Gemini CLI → Google OAuth / Cloud Code Assist | default Flash per docs | Mac | Interactive CLI burns same quota | **Policy**: default CLI off Flash OAuth; Pro-only or paid path if Gemini CLI retained |
| Hermes `model_catalog.json` `google/gemini-3.8-flash` | OpenRouter catalog ID present | `google/gemini-3.8-flash` | Mac | **Safe if isolated**; risk only if someone aliases it as Flash/OAuth fallback | **Keep** as paid bulk-reader only; **never** wire as `gflash` / `gemini-flash` fallback |
| Cursor IDE model aliases | No live `gflash` / `gemini-flash` alias found under `~/.cursor` configs (only unrelated plan text) | — | Mac | Low | No action |
| Live Hermes `~/.hermes/config.yaml` | OpenRouter main; no `8317` / `gflash` / `gemini-3-flash` pins found | — | Mac | Low for Hermes main chat | Monitor auxiliary/delegation if reintroduced |

### Evidence snippets (non-secret)

Mac CLIProxyAPI alias (live file):

```yaml
# ~/.cli-proxy-api/config.yaml
oauth-model-alias:
  antigravity:
    - name: gemini-3-flash
      alias: gemini-3-flash-preview
```

VPS CLIProxyAPI alias (repo config):

```yaml
# cliproxy-vps-config/config/config.yaml
oauth-model-alias:
  gemini-cli:
    - name: "gemini-3-flash-preview"
      alias: "gflash"
      fork: true
```

Pi fast worker (live):

```yaml
# ~/.pi/agent/agents/gemini-flash.md
model: google-gemini-cli/gemini-3-flash-preview
```

```yaml
# ~/.pi/agent/teams.yaml (excerpt)
fast-pool:
  - claude-haiku-fast
  - gemini-flash
  - gpt53-spark-fast
```

Historical proof of OAuth rate-limit pain: Pi quarantine sessions show repeated `Cloud Code Assist API error (429)` on `google-gemini-cli` / `gemini-3-flash-preview`.

---

## Kill list (ordered by blast radius)

Do not apply in Phase 0 unless a later job authorizes. Order = stop the most concurrent OAuth Flash traffic first.

1. **Pi fast-pool slot `gemini-flash`** — remove from `teams.yaml` `fast-pool` / `model-pool`; change `agents/gemini-flash.md` (+ `-3`) off `google-gemini-cli/gemini-3-flash-preview`. Highest automatic dispatch volume.
2. **Pi `worker-medium` → `capi/gemini-3-flash-preview`** — reroute off CAPI Gemini Flash.
3. **VPS `gflash` alias** in `cliproxy-vps-config/config/config.yaml` — delete/disable alias; exclude `gemini-3-flash-preview` from Gemini OAuth catalog.
4. **Mac CLIProxyAPI Antigravity Flash alias** `gemini-3-flash` → `gemini-3-flash-preview` in `~/.cli-proxy-api/config.yaml` — disable Flash exposure.
5. **`cc-fallback` Gemini Flash targets** — remove sonnet/haiku → `gemini-3-flash-preview` mapping.
6. **Observational memory / reflector** paths still on `gemini-3-flash-preview` OAuth (per architecture docs) — move off OAuth Flash.
7. **Architecture / CAPI / Cursor-setup docs** advertising `gflash*` / Fast-free Flash — doc-fix so operators stop aiming at OAuth.
8. **Herdr “Flash” wording** — clarify worker class ≠ Gemini OAuth Flash; forbid Gemini OAuth as ordinary Flash.
9. **Gemini CLI default Fast** documentation / habits — stop defaulting interactive CLI to Flash OAuth.
10. **Test scripts** (`test-cli-rotation.sh`, etc.) — stop probing OAuth Flash.

**Explicit non-action / guardrail:** Do **not** “fix” Flash by pointing `gemini-flash` / `gflash` at OpenRouter `google/gemini-3.8-flash`. That ID stays a **separately named paid bulk-reader** only.

---

## Gaps / caveats

- Local `127.0.0.1:8317` was not answering during inventory; Mac live `/v1/models` Flash membership not re-verified at runtime (docs + config files used).
- Dropbox `Projects/CLIProxyAPI` tree is large; VPS **config repo** was the primary VPS evidence surface.
- `~/.pi/agent/models.json` contains credentials — never copy keys into shunt docs or commits.
- Hermes live config no longer pins `fast_worker` to Gemini; architecture docs are stale and still dangerous if followed.
