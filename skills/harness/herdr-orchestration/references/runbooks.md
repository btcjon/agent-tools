# Runbooks

## New substantive job (Sol-main / Auto-main)

1. Confirm orchestration on and selected fleet (`bb` or `herdr`) (or explicit one-shot authorization).
2. Write early problem packet → claim judgment pane → fresh process → verify model banner → prompt pointer only.
3. Integrate plan; coordinator chooses ordinary routes (**not** Astra).
4. Claim ordinary pane → settle prior attempt if repurposing → **fresh conversation for a different independent job** (pane may stay) → readiness recheck → task packet → short pointer.
5. Supervise one awaited handle; correlate receipt + artifacts + independent checks.
6. Auto-main: mandatory checks → closing packet → Astra recommendation → accept/repair/block.
7. Release claims after settlement.

## Supervision loop

- Bounded wait or receipt poll at milestones.
- Lifecycle `idle`/`done` alone ≠ done.
- Stalled prompt → inspect → short pointer retry, not duplicate long prompt.
- Return repairs to owning worker with exact failing evidence.

## Quality clarification

Focused question to the **same** judgment session. Do not open a new job context. Do not ask Astra to name workers.

## One repair budget

Declare `used` / `remaining` in packets. Closing review does not reset. Exhausted ⇒ change approach or settle blocked.

## Closing alignment (Auto-main)

After execution + mandatory hard checks only. Reject rubber-stamp when mandatory gaps remain. See [packets.md](packets.md).

## Blocked / cancelled settlement

Write durable blocker; release matching-owner claims; do not leave foreign claims dangling; tell the user the concrete missing evidence or decision.

## Lost judgment context

Retain claim if still valid; start replacement process with compact checkpoint + approved plan evidence; record continuity loss; do not invent prior Astra conclusions.

## Stalled prompt / route failure

Inspect pane process and agent status. Record auth/quota once. Select authorized alternative; never silent substitute for required judgment model.

## Mode off

Stop new dispatches. Continue observing owned in-flight work. Do not kill workers. Status should still report owned jobs.
