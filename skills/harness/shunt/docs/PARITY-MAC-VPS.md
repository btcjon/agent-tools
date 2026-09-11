# PARITY — Mac ↔ VPS (SDMM795)

**When:** 2026-09-11T12:56Z (UTC)  
**Worker:** cursor-auto-4  
**Mac package:** `/Users/jonbennett/Library/CloudStorage/Dropbox/Projects/custom-skills/skills/harness/shunt/`  
**Do not commit** (per task). Mac Cursor gates were **not** weakened.

## Verdict

| Side | Result |
| --- | --- |
| **Mac** | `shunt doctor` OK; adapters registered; native gate deny/allow PASS |
| **VPS SDMM795** | **BLOCKED** — Tailscale peer offline; SSH timed out |
| **Parity install** | **Not completed** (no reachable host) |

---

## B1 — Access inventory

| Target | How resolved | Status (this run) |
| --- | --- | --- |
| SSH `Host mac-mini` | `~/.ssh/config` → `jon@100.99.248.72` (comment: SDMM795 Tailscale) IdentityFile `~/.ssh/mac-edge` | **Timeout** (`ConnectTimeout` 8–15s) |
| Tailscale `sdmm795` | `100.99.248.72` / `sdmm795.tailf3d7b6.ts.net` | **Offline** — `last seen ~10d` (Tailscale: `2026-08-31T16:23:13Z`) |
| Tailscale `Mac mini` (`mac-mini.tail6ff4a0.ts.net`) | `100.83.7.125` (different peer, `wcoreymoore@`) | Reported **Online** via relay `nyc`, but SSH **still timed out** (tx without rx) |
| `pai_deploy` | Not in DNS / no Host entry | **Unresolved** |

**Exact blocker:** SDMM795 is offline on Tailscale; no SSH session could be opened to install or run `shunt doctor` remotely. Dropbox path on VPS was not inspectable.

When SDMM795 is online again:

```bash
ssh -o BatchMode=yes -o ConnectTimeout=15 mac-mini 'hostname; whoami'
# Expected Dropbox mirror (if present):
#   ~/Library/CloudStorage/Dropbox/Projects/custom-skills/skills/harness/shunt
# Else rsync/git sync the Mac package revision, then:
#   python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
#   shunt doctor
#   shunt install && bash adapters/pi/install.sh && bash adapters/grok/install.sh …
```

---

## B2 — Mac package identity (hashes)

Content SHA-256 (files that define gate policy + PHG adapters):

```text
55bbfad616cee9423e161f7767e9eaf93d71ac4cf22d6bfae01a2874f0bab7f3  src/shunt/gate.py
52d44d7764a137d3552571697704dd0adfef9f957759adae022c492994e3f035  src/shunt/agent_read.py
7367f58a586a8e117dc280b122ebf994b8d7e4cfd753aadefdcde7339e577c1a  adapters/_common/native_read_gate.py
91cc31eff210993c13330200537edee84d3f3d0efa7de16dfc3e4f64d72ef4f2  adapters/pi/shunt-gate.ts
9fa9c53c7e407da12c00abad646f9877ceab963d7822e95e84608e6adac6dfd4  adapters/grok/pretooluse.sh
6ba7ff40402e8c9cf6318a0e5396051bbf6d7ea106e352fcb73f5d1676d96dca  adapters/hermes/pre_tool_call.sh
f309d5fa0e67ed2399719cc0b9636a38a6aa3997db547506963f42b20cdc9c6b  adapters/cursor/pre_tool_use_read.py
09189936b3796f5103f56a6f628735b98768c3514e2e76e934a9a77262cf2aab  adapters/codex/pre_tool_use.py
b1040e6d851269506a42b4156505af16261f0263230ff3ab31a6769b45e434d0  adapters/claude/pre_tool_use.py
```

Note: enclosing `custom-skills` git tip may lag local uncommitted shunt work; **use these content hashes** for VPS parity, not `git rev-parse` alone.

---

## B3 — `shunt doctor` (Mac)

```text
shunt 0.1.0 — doctor (capability report)
package_root=…/skills/harness/shunt
openrouter.model=google/gemini-3.8-flash  # LOCKED
OPENROUTER_API_KEY=set
harnesses:
  cursor / codex / claude / pi / hermes / grok / agy → shunt_registered=yes
policy_HARNESS-HOOKS: present shunt_allowed=True
```

**VPS doctor:** not run (host unreachable). Diff: N/A.

---

## B4 — Live deny / allow (Mac)

Fixture: `tests/fixtures/live_harness_gate.txt`

| Check | Result |
| --- | --- |
| Native gate full read | `block=true` `reason=oversized_full_read` |
| Native gate `offset=40 limit=21` | `block=false` `reason=scoped_window` |
| Grok hook stdin deny | `decision=deny` + `shunt bulk-read` (see LIVE-PHG) |
| Pi live session | deny + window L50 (see `docs/LIVE-PHG.md`) |

**VPS live proofs:** absent (no SSH).

---

## Installed artifact mtimes (Mac)

```text
2026-09-11T08:29:26Z  ~/.pi/agent/extensions/shunt-gate.ts
2026-09-11T08:29:26Z  ~/.grok/hooks/shunt-pretooluse.json
2026-09-11T08:55:37Z  ~/.cursor/hooks.json
2026-09-11T08:16:50Z  ~/.codex/hooks.json
2026-09-11T08:16:50Z  ~/.claude/settings.json
2026-09-11T08:02:47Z  ~/.hermes/config.yaml
```

---

## Absent / deferred on VPS

| Item | Status |
| --- | --- |
| Package tree sync | Deferred |
| `shunt doctor` | Deferred |
| Adapter install | Deferred |
| Harness live deny/allow | Deferred |
| Hash match vs Mac table | Deferred |

## Reload after future VPS install

Follow `docs/RELOAD-POLICY.md`. On Mac, candidate panes (dry-run):

```bash
./scripts/herdr-shunt-reload-hint.sh
# → interrupts=false; lists RELOAD_CANDIDATE vs DO_NOT_INTERRUPT
```

---

## Snapshot path

Machine-local capture also written to `/tmp/shunt-parity-mac-snapshot.txt` during this job (not committed).
