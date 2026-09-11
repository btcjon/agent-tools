# RECEIPT — PKG6 (acceptance integrate + ship)

**When:** 2026-09-11  
**Worker:** cursor-auto-5  
**Repo:** `/Users/jonbennett/Library/CloudStorage/Dropbox/Projects/custom-skills`

## Preconditions

Polled `docs/` (~60s) until PKG1–5 signals present:
- PKG1: `RECEIPT-PKG1.md`, `docs/LIVE-CURSOR-SHELL.md`
- PKG2: `docs/PHASE-0-APPLIED.md`
- PKG3/4: `docs/PARITY-MAC-VPS.md`, `docs/RELOAD-POLICY.md`
- PKG5: `docs/LIVE-CLAUDE-AGY-E2E.md` (Claude 401 revoked; AGY 429 — not passes)

## Done

1. Updated `docs/ACCEPTANCE-LIVE.md` final matrix  
2. Updated package `README.md` + root `README.md` (shunt + Hermes link)  
3. Warehouse `AI-Control-Plane/skills/exported/shunt/SKILL.md` already points at custom-skills (unchanged)  
4. `ln -sfn …/skills/harness/shunt ~/.hermes/skills/shunt`  
5. Git commit + push authorized scope: `skills/harness/`, `README.md`  
   - Commit: `eda16dd21175e12c7c7c8a3d978798f378606b02`  
   - Remote: `origin/main` matches (`git ls-remote` verified)  
6. Pytest before ship: **42 passed**

## Residuals carried into ACCEPTANCE-LIVE

Claude OAuth revoked; AGY quota 429; VPS parity blocked; reload policy for stale panes.
