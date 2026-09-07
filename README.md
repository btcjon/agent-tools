# custom-skills

Public home for custom Hermes Agent skills that are not part of the bundled
Hermes tree.

Skills live under `skills/<category>/<skill-name>/` so later additions stay
grouped (discovery, devops, media, …). Add a category folder when the first
skill in that class lands. Do not dump new skills at the repo root.

## Skills

| Skill | Category | Purpose |
|---|---|---|
| [KSU — Keep Shit Updated](skills/devops/ksu/) | devops | Quiet nightly maintenance across enrolled Macs, Linux/VPS, Windows, and persistent sandboxes, with restart choices and bounded recovery. |
| [proactive-skill-suggestor](skills/discovery/proactive-skill-suggestor/) | discovery | High-bar Skills Hub scout from recent Hermes work. Suggest-only. Upgrade similar local skills instead of installing duplicates. |
| [gmail-triage](skills/email/gmail-triage/) | email | Portable Gmail triage, sender cleanup, unsubscribe, undo, and mailbox health through Composio-managed authentication. |

## Install (Hermes)

```bash
git clone https://github.com/btcjon/custom-skills.git
ln -s "$(pwd)/custom-skills/skills/discovery/proactive-skill-suggestor" \
  ~/.hermes/skills/proactive-skill-suggestor
```

Load with `skill_view(name="proactive-skill-suggestor")` in a new session.

For Gmail Triage, link or copy `skills/email/gmail-triage` into the skill
directory used by your agent, then connect Gmail through Composio. The agent needs
both Agent Skills support and an approved Composio Gmail connection.

## Try Gmail Triage without a mailbox

New users and their agents: [Start here](skills/email/gmail-triage/references/quickstart.md)
for installation, connection, preferences, first-batch verification and limits.

The whole decision pipeline is offline and standard library only, so it can be
proven before any account is connected:

```bash
cd skills/email/gmail-triage
python3 scripts/triage_core.py selftest      # synthetic fixtures, no network
python3 -m unittest discover -s tests -t tests
```

`selftest` walks rank, plan, duplicate-safe filter check, readback verification,
receipts, resume, and health on the bundled fixtures and reports a pass per step.
Runtime state lives outside the repository in `--state-dir` (default
`~/.gmail-triage`, `0700` directory with a `0600` database) and mailbox-derived
files are covered by `.gitignore`.

## Scout once (no cron)

```bash
python3 skills/discovery/proactive-skill-suggestor/scripts/mine_session_themes.py --hours 72 \
  > /tmp/mine.json
python3 skills/discovery/proactive-skill-suggestor/scripts/search_hub.py --mine-json /tmp/mine.json
# Agent inspects top hits per SKILL.md. Suggest only.
```

Empty output means silence. Suggestions are a proposal, not a mutation.

## Adding a skill

1. Pick or create `skills/<category>/`.
2. Put a complete package there: `SKILL.md`, then `scripts/`, `references/`, `tests/` as needed.
3. Symlink the package directory into `~/.hermes/skills/<skill-name>` (not the whole repo).

Packages may target other Agent Skills-compatible harnesses as well. Their
external tool connections and permissions remain separate from the skill files.

## Privacy

The miner emits compact work-class JSON (titles, skill names, query seeds).
Do not commit `receipts/` or raw transcripts. This repo is public.

Gmail Triage keeps mailbox-derived data out of the repository entirely: normalized
message files, candidate lists, plans, results, and its SQLite audit state live in
a private state directory, and its receipts store sender addresses, action classes,
outcome classes, and Gmail filter IDs only — never bodies, subjects, unsubscribe
URLs, tokens, or credentials. The only committed message data is the synthetic
`fixtures/` set used by `selftest`.

## KSU: nightly maintenance

Point your agent at [KSU](skills/devops/ksu/SKILL.md) and ask it to enroll your machines. The default is 03:00 local time, app/service restarts allowed, full reboots deferred, and quiet local health reports. [Setup, coverage, and limitations](skills/devops/ksu/references/setup.md).
