# Packets and receipts

Keep packets compact. Never paste the parent transcript into a judgment session. Same-job continuity: reuse the claimed judgment session; reset only when the job is accepted or explicitly settled.

## Early problem packet (before plan lock)

Include:

- `job_id`
- Objective (outcome, not a solution dump)
- Constraints and non-goals
- Material prior decisions
- Relevant file paths or evidence pointers
- Proposed direction **if any** (optional)
- Requested response shape: risks, hidden complexity, recommended plan/approach, criterion sketch
- Explicit: **do not** select named ordinary workers or pool routes

See [examples/early-packet.md](../examples/early-packet.md).

## Task packet (ordinary worker)

Include:

- `job_id`, worker role, **no redelegation**
- Outcome and authorized writable paths
- Stable criterion IDs (`mandatory` vs `optional`)
- Verification commands
- Repair budget (`used` / `remaining`; default at most one same-job repair)
- Receipt path and **receipt-last** rule

See [examples/task-packet.md](../examples/task-packet.md).

## Closing plan-versus-result packet (Auto-main; Sol-main when required)

Include at least:

- Job/claim identity
- Requested outcome and authorized scope
- Approved plan with stable criterion IDs and approved deviations
- Artifact paths with revision/hash
- Criterion-to-evidence mapping (`met` / `unmet` / `unknown`)
- Decisive artifacts (not executor summaries alone)
- Coordinator verification commands/results/limits
- Uncertainties and scope changes
- Repair attempts used and remaining allowance
- Requested Astra response shape: `aligned` | `repair_required` | `blocked`

See [examples/closing-packet.json](../examples/closing-packet.json).

## Receipt contract (worker → coordinator)

Write last. Target 200–400 bytes, ceiling ~600 bytes.

```json
{
  "job_id": "…",
  "status": "done",
  "artifacts": ["relative/path"],
  "checks": ["cmd → ok"],
  "blocker": null
}
```

`status` is `done` or `blocked`. A blocked receipt names missing evidence without inventing it. See [examples/receipts.json](../examples/receipts.json).
