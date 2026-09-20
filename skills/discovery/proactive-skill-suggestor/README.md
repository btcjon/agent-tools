# Proactive Skill Suggestor

![A scout finds the few useful skills in a galaxy of options](assets/hero.webp)

## Find the skill you are missing—without collecting 40 copies of the same idea.

Proactive Skill Suggestor reviews the shape of recent Hermes work, searches the Skills Hub hard, inspects the best candidates, and recommends only the additions or upgrades that appear genuinely useful.

It is a scout, not an installer. It can point at the gold. It cannot quietly bring the whole mine home.

## What you get

- **Work-aware suggestions:** recent task patterns become bounded search themes—not raw transcript dumps.
- **Inspect-backed recommendations:** a name match is not enough; finalists must be opened and evaluated.
- **Duplicate control:** if a local skill already owns the job, the suggestion becomes an upgrade or a rejection.
- **Useful silence:** no strong match means no recommendation.
- **Zero surprise mutations:** it never installs, patches, enables, publishes, or creates a schedule on its own.

## How it works

1. **Mine:** turn a recent work window into compact work classes and search queries.
2. **Hunt:** search multiple angles and inspect the strongest Skills Hub candidates.
3. **Subtract:** compare them with local skills and return at most five net-new or upgrade recommendations.

The result is a shortlist with receipts, not a junk drawer with a dependency problem.

## Try it once

```bash
cd skills/discovery/proactive-skill-suggestor
python3 scripts/mine_session_themes.py --hours 72 > /tmp/mine.json
python3 scripts/search_hub.py --mine-json /tmp/mine.json
```

Then inspect the finalists and follow the decision rubric in [`SKILL.md`](SKILL.md). Installation remains a separate, explicit decision.

## Guardrails

- Maximum five suggestions per run.
- Similar local capability means upgrade, merge, or reject—not another near-duplicate.
- Public output never includes raw transcripts, emails, IDs, tokens, or secrets.
- Scheduled runs are designed around Hermes cron's three-minute interruption window.

## Test it

```bash
python3 -m unittest discover -s tests -t tests
```
