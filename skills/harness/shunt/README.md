# Shunt

![Shunt turns a mountain of file content into a handful of useful pointers](assets/hero.webp)

## Stop feeding whole files to your most expensive brain.

Shunt catches oversized file reads and routes the bulk through a cheaper reader that returns **points only**: names, paths, line ranges, coverage bullets, and useful orientation.

The main model still decides. The main model still edits. It simply gets a map instead of swallowing the mountain.

## What you get

- **Less context waste:** large reads become compact pointers instead of giant prompt payloads.
- **One shared policy:** a common core with thin adapters for Codex, Cursor, Claude, Pi, Grok, Agy, and Hermes.
- **Hard safety boundaries:** secret paths are blocked and Shunt cannot act as a coding agent or editor.
- **Predictable routing:** one locked primary provider and one explicit fallback—never a mystery third model.
- **Privacy-safe telemetry:** operational outcomes are recorded without file contents or clear file paths.

## How it works

1. **Gate:** a harness hook checks whether a native file read is oversized.
2. **Shunt:** blocked bulk content goes through the installed reader.
3. **Return:** the main model receives concise orientation, then performs any targeted reads and edits itself.

Shunt is the side channel that keeps a dump truck of text from parking in your context window.

## Install

```bash
cd skills/harness/shunt
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
shunt doctor
shunt install
```

Additional harness adapters and reload requirements are documented in [`references/install.md`](references/install.md), [`adapters/README.md`](adapters/README.md), and [`docs/RELOAD-POLICY.md`](docs/RELOAD-POLICY.md).

## Core commands

| Command | What it does |
| --- | --- |
| `shunt bulk-read PATH` | Returns points-only orientation for an allowed large file |
| `shunt check-read PATH` | Allows or blocks a native full-file read |
| `shunt doctor` | Reports configuration, credentials, and registrations |
| `shunt install --dry-run` | Previews adapter hook changes before installation |

## Locked provider route

- Primary: direct Z.ai Coding Plan `glm-5.3` using `ZAI_API`.
- Fallback: Cerebras `gpt-oss-120b` using `CEREBRAS_API_KEY`.
- A third provider or model is not allowed.

## Not a second decision-maker

Shunt does not rewrite files, make product decisions, or replace targeted reading. It handles oversized orientation work so the lead model can spend its attention where judgment matters.

Run the package tests before changing the shared core or adapters:

```bash
pytest -q
```

MIT licensed.
