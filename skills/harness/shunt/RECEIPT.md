# RECEIPT — shunt scaffold

**Job:** scaffold `shunt` repo + warehouse skill stub  
**When:** 2026-09-11  
**Worker:** cursor-auto-2 (direct; no delegation)

## Paths created

### Repo — `/Users/jonbennett/Library/CloudStorage/Dropbox/Projects/shunt`
- `README.md`, `LICENSE` (MIT), `.gitignore`, `.env.example`, `pyproject.toml`
- `config/default.toml` — `min_lines=350`, `max_bytes`, OpenRouter base URL + locked `google/gemini-3.8-flash`
- `src/shunt/{__init__.py,gate.py,bulk_read.py,cli.py}`
- `adapters/{README.md,cursor,codex,claude,pi,hermes,grok,agy}/README.md`
- `docs/{ARCHITECTURE.md,PHASE-0.md}`
- `tests/test_gate.py`
- Local `.venv/` for verification only (gitignored)

### Skill — `/Users/jonbennett/Library/CloudStorage/Dropbox/AI-Control-Plane/skills/exported/shunt/`
- `SKILL.md` (router; OpenRouter lock; no OAuth Flash)
- `references/install.md`

## Commands run

```bash
git branch -M main
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q                          # 6 passed
python -m shunt.cli --help
shunt doctor
shunt install --dry-run            # lists harness config targets; no mutations
shunt bulk-read <large stub file>  # stub points; no live OpenRouter HTTP
git add … && git commit
```

## Acceptance

| Criterion | Result |
| --- | --- |
| Layout + gate tests pass | Yes (6 passed) |
| `python -m shunt.cli --help` / `shunt` entrypoint | Yes |
| Warehouse skill stub | Yes |
| No harness config mutation | Yes (dry-run only) |
| No live OpenRouter | Yes (stub) |
| No Cursor/GitHub remote created | Yes |
| Secrets not committed | `.env` gitignored; `.env.example` only |

## Locks recorded

- Bulk-reader: OpenRouter `google/gemini-3.8-flash` only  
- Never CAPI Gemini OAuth / `gflash*` / Google account Flash fallback  
- Shared core + thin adapters; points-only output  
- Spotify Portal Claude Code plugin: not installed  
