# RECEIPT — WP1 Shared core (live OpenRouter bulk-read)

**Package:** `/Users/jonbennett/Library/CloudStorage/Dropbox/Projects/custom-skills/skills/harness/shunt/`  
**When:** 2026-09-11  
**Worker:** cursor-auto-2 (direct; no delegation)

## Done

1. Live `bulk_read` → `POST https://openrouter.ai/api/v1/chat/completions` with model **`google/gemini-3.8-flash` only**.
2. Prompt demands POINTS ONLY (bullets, names, paths, line ranges, short quotes, coverage) + `content_hash`.
3. Timeouts (`timeout_seconds=60`), `max_payload_bytes=400000` in `config/default.toml`; `SHUNT_INTERNAL=1` recursion guard skips network.
4. Failures return `detail` + `guidance` (bounded Read offset/limit). **No** CAPI / gflash / Gemini OAuth fallback.
5. Tests: gate suite + mocked HTTP success/401-path/timeout/missing-key/recursion; live smoke optional via `SHUNT_LIVE_TEST=1`.

## Files touched

- `src/shunt/bulk_read.py` — live client
- `src/shunt/cli.py` — JSON fields `content_hash`, `guidance`, `http_status` (left install/check-read intact)
- `config/default.toml` — timeout + max_payload
- `tests/test_bulk_read.py` — mocks + optional live
- `docs/ARCHITECTURE.md` — locks / guidance note
- `RECEIPT-WP1.md` (this file)

**Not edited:** `~/.cursor`, `~/.codex`, `~/.claude`, `HARNESS-HOOKS.md`, `adapters/`

## Commands

```bash
cd …/custom-skills/skills/harness/shunt
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
# 15 passed, 1 skipped (live smoke unless SHUNT_LIVE_TEST=1)

shunt bulk-read tests/fixtures/live_smoke.py
```

## Sample output (key redacted)

Live smoke against a 360-line fixture with `OPENROUTER_API_KEY` present in the environment:

```json
{
  "ok": false,
  "stub": false,
  "model": "google/gemini-3.8-flash",
  "detail": "openrouter_http_401:User not found.",
  "content_hash": "d3547b1cbefda53e103f3c7e773551945dd13bd5c44f981e77e53042388032af",
  "http_status": 401,
  "guidance": "Use a normal Read with offset/limit (e.g. ~200-line windows) instead of bulk-read. Do not fall back to CAPI Gemini OAuth, gflash*, or a Google-account Flash path.",
  "gate": {
    "allow": true,
    "reason": "ok",
    "path": "tests/fixtures/live_smoke.py",
    "lines": 360,
    "bytes": 6380
  },
  "points": []
}
```

**Acceptance note:** OpenRouter rejected the key (`401 User not found`) — treated as a **clear blocker**, not a stub. `stub: false`, guidance present, no alternate-provider fallback. Mocked success path covers non-stub points when HTTP 200.

Mocked success (unit) returns `ok: true`, `stub: false`, `detail: openrouter_ok`, multi-bullet `points` including `content_hash`.

## Acceptance

| Check | Result |
| --- | --- |
| `pytest` | 15 passed, 1 skipped |
| Live `shunt bulk-read` >350 lines | Blocker recorded (`401`); not stub |
| No harness hook edits | Yes |
| Model lock | `google/gemini-3.8-flash` only |
