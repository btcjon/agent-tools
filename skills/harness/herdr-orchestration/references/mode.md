# Orchestration mode (portable)

Authoritative portable activation, ownership, lifecycle, routing, fleet backends, and three-profile rules for this package. Host-specific pane/slot names, counts, and start flags belong in local configuration and [adapters/herdr.md](adapters/herdr.md)—not as silent defaults that imply another operator’s fleet.

## Activation and controls

Default-on **orchestration** in primary user-facing sessions is an **instruction policy**, not a built-in Herdr or bb switch. Installing this skill does **not** turn mode on. Operators who want default-on behavior must opt in via [CONTRACT-SNIPPET.md](../CONTRACT-SNIPPET.md) or equivalent.

The coordinator profile follows **where the main agent is running**. Explicit user model/harness routing wins. Delegated workers execute their bounded assignment directly and never recursively activate this mode.

Orchestration is the delegation policy. **Fleet** is the execution backend for new jobs:

| Control | Meaning |
|---|---|
| **orchestration on** | Default to delegation for new substantive work. |
| **orchestration off** | Stop new dispatches; settle owned in-flight jobs; do not kill workers. |
| **orchestration status** | Report mode, selected fleet, profile, owned jobs (with backend), availability. |
| **fleet bb** | New jobs via `bb-herdr` (threads). JB reference default: **bb**. |
| **fleet herdr** | New jobs via Herdr CLI/panes + claims. |
| **fleet status** | Report selected fleet and backend-scoped ownership. |

Fleet switches do not enable/disable orchestration and do not migrate in-flight jobs. Never silent-fallback between fleets. Changing the global default requires a contract edit; an ordinary off/fleet command does not.

Preserve mode, fleet, and job ownership in the task’s normal continuation checkpoint. Do not treat an idle pane/thread as proof a job completed. Turning mode off does not abandon ownership. Stopping workers is a separate user instruction.

## Where the main agent runs (reference preset)

| Main agent surface | Default coordinator | Profile |
|---|---|---|
| **Codex** (primary user chat) | Sol-class long coordinator | **Sol-main** |
| **Cursor** (primary user chat) | **Auto** | **Auto-main** |
| **Codex** with Astra selected as the main model | **Astra** | **Astra-main** |

Ordinary Herdr worker panes are **not** the main agent. A worker pane does not change the conversation profile. Actual selected main model and explicit user routing govern; do not infer model identity from the application alone. Hermes/Claude (or other harnesses) may load this package but must select a supported coordinator binding explicitly—loading support does not prove execution compatibility.

## Coordinator profiles

Use exactly one profile for the conversation. Do not change profiles merely to save quota or because another conversation used a different model. Profiles are not equal intelligence and do not guarantee a savings percentage.

### Astra-main

Astra owns the objective, decomposition, judgment, tradeoffs, evidence review, integration, routing, and final acceptance. Ordinary implementation, research, debugging, repairs, tests/fixtures, and routine checks go to the configured **non-OpenAI** ordinary pool. Do not launch Astra children for ordinary work. Use an Astra child only when fresh-context isolation itself is useful, and follow the fresh-session rules below.

**There is no mandatory early “consult another Astra” gate in Astra-main.** The coordinator already is Astra.

### Sol-main

The Sol-class coordinator owns the long user conversation, coordination, **delegation/routing**, testing, evidence integration, and final acceptance. It is not the sole judge of which work needs superior reasoning.

Before locking the plan for every substantive job, start one fresh claimed judgment session (reference name pattern `astra-*` or your configured equivalent) and give it a compact problem packet: objective, constraints, material prior decisions, relevant files or evidence, and the proposed direction if one exists—**never** the parent transcript. Ask it to identify hidden complexity, challenge assumptions, and shape the smartest plan and approach. **Do not ask it to pick named ordinary workers or pool routes**—that is the coordinator’s job after the plan is clear. Then keep that same claimed judgment session for any judgment-owned core implementation the coordinator assigns, focused questions, test failures, quality clarifications, and at most one repair on the job. The coordinator independently tests and accepts the artifacts. If the repair fails, change approach or report the blocker rather than cycling.

### Auto-main

Cursor Auto (or the configured Auto-class coordinator) owns the long user conversation, coordination, **delegation/routing**, testing, **quality enforcement**, and final acceptance. Lean on Astra for planning, direction, framing, risks, approach, quality clarification, and **closing alignment recommendation**. **In Auto-main, Astra does not do the actual implementation work.** The ordinary Herdr pool executes. The coordinator adopts Astra’s plan, then delegates.

Before locking the plan for every substantive job, run the same early Astra judgment checkpoint and compact problem packet rules as Sol-main (plan/approach intelligence only). Keep that same claimed Astra session for focused questions, quality clarifications, and the closing alignment review on the job; reset before a different job.

**Auto-main execution gate (fail closed):**

1. Before substantive execution, claim and dispatch ordinary work on the selected fleet, unless a qualifying direct-work exception is recorded and disclosed before that work. Direct exceptions remain strictly limited to existing categories: unique inaccessible connector or access, time-critical safety, tiny isolated closure cheaper than re-dispatch, or demonstrated unavailable/failed worker after a bounded attempt. Prefer `bb-herdr batch` when fleet is bb.
2. The coordinator may own planning, bounded evidence inspection, user communication, task/closing packets, independent verification, routing/waiting/settlement, and focused Astra clarifications. Substantive research, debugging, test authoring, and outcome-bearing code or documentation remain ordinary-worker work; relabeling them as “verification” or writing them into task files does not exempt them from delegation.
3. Keep the same claimed Astra session across the job. Each additional clarification must identify an unresolved judgment question or changed evidence; do not repeatedly seek approval on unchanged evidence. Worker repairs return to ordinary workers within the existing repair budget. Re-review with Astra only for judgment-dependent gaps or material scope/behavior changes; exhausted repairs settle blocked. Astra never implements in Auto-main.
4. Settlement and status reports must record backend, ordinary job IDs/slots, and assigned scope—or disclose the qualifying direct-work exception. An empty ordinary-worker list is a process failure when substantive execution occurred without a disclosed qualifying exception. Settling blocked or cancelled before execution begins remains valid.
5. Preserve gate distinctions: any recorded early-gate skip must qualify under existing criteria (pure conversation, straightforward retrieval/formatting, or clearly mechanical changes); Astra unavailability is a disclosed blocker rather than a bypass waiver; and an early skip does not waive the required closing alignment review once work becomes substantive.

**Quality clarification / enforcement:** If the coordinator has a real question about quality—plan soundness, whether an artifact is good enough, the right repair, ambiguous evidence, or acceptance that would otherwise be a guess—send a focused clarification to the same Astra session. If worker output is sub-par, do not shrug or lower the bar: return exact failing evidence for a bounded repair within the **existing** repair budget and/or ask Astra to clarify the bar. Known mandatory check failures go to repair or blocked settlement—not to an approval-shaped review.

**Closing alignment review (Auto-main, required for substantive jobs):** After ordinary-pool execution returns and the coordinator has run mandatory hard/automated checks:

1. Do not accept yet. Send the **same** claimed Astra session a compact plan-versus-result packet (see [packets.md](packets.md)).
2. Astra inspects decisive evidence and returns only `aligned` | `repair_required` | `blocked`, naming unmet criterion IDs and bounded repairs. No implementation. No worker naming. Astra recommends; the coordinator retains final acceptance.
3. Accept only if mandatory gaps are resolved; otherwise enforce repair within the remaining budget or settle blocked. Closing review **does not reset** the repair budget. After repair, recheck only changed criteria/affected evidence; return to Astra for judgment-dependent gaps or material scope/behavior changes.
4. If Astra is unavailable, do not silently bypass: record the blocker. If the session is lost, reclaim a replacement and reconstruct the approved plan from durable evidence before reviewing.
5. Release the Astra claim only after acceptance or explicit blocked/cancelled settlement.

Skip closing review only when the **final** task is pure conversation, straightforward retrieval/formatting, or clearly mechanical bounded work whose **full** acceptance criteria are covered by direct hard checks—and record the exemption. Skipping the early gate is not itself an exemption. If scope grew substantive, closing review is required even if the early gate was skipped. Worker receipts, “tests passed,” or executor reputation are never sufficient to skip.

### Sol-main closing alignment

Require a closing Astra alignment pass when ordinary workers own material outcome-bearing execution, when later changes materially affect Astra-owned core behavior, or when the coordinator is uncertain. A separate closing pass may be skipped only if Astra already reviewed the **final combined result** against the criteria in its implementation handoff and the coordinator independently verifies it. Core authorship alone is insufficient.

### Astra intelligence gate (Sol-main and Auto-main only)

Required when the job needs substantive implementation, non-obvious diagnosis, architecture or cross-component decisions, security/auth/data/infrastructure judgment, consequential research or recommendations, or resolution of ambiguous requirements. When uncertain, use the gate. Pure conversation, retrieval, formatting, and clearly mechanical bounded changes may skip the **early** gate. Astra may conclude the work is straightforward once planned correctly; that is plan intelligence, not a delegation order. The coordinator still chooses who executes. If no judgment slot is available, do not silently impersonate the missing review: continue safe routine work, use an explicitly authorized specialist when suitable, and disclose the missing checkpoint before consequential acceptance. Missing early or closing Astra review is a disclosed blocker for consequential acceptance—not a silent waiver.

### Judgment slots and fresh sessions

Configure a small pool of judgment slots (reference names: `astra-1` … `astra-5`). Discover live handles each time. Do not hardcode IDs from a previous task, add slots without authorization, or silently substitute another model. **Select the conversation fleet before claiming.**

**`fleet bb` (default):** claim/dispatch/wait/release via `bb-herdr`. Fresh bb thread per new judgment job; continue the same thread for same-job follow-ups. Verify spawn returns the expected provider/model. Claims are advisory under `~/.local/share/bb-herdr` with `backend: bb`.

**`fleet herdr`:** claim an available judgment **pane** and confirm its agent is idle or done. Never interrupt a working, blocked, or foreign-owned pane. **Claim failure must abort before any agent prompt.** Exit any idle agent occupant safely, verify the shell is in the foreground, start a **new** agent process with the configured judgment model/reasoning, and require the expected product + model + effort from the startup banner. Wrong/unavailable route is a blocker—do not prompt or substitute. Keep the claim through verification; release only after acceptance or explicit settlement. Next job restarts the agent process. If the process dies mid-job, retain the claim and start a replacement only with a compact checkpoint; record lost continuity.

The worker’s elevated permission mode (if any) never expands the user-authorized task scope. Missing or busy judgment slots do not stop routine work from using the ordinary pool.

Default fleet is **bb** (`bb-herdr`); `fleet herdr` remains fully supported. Alternate orchestrators (for example Firstmate-style) stay parked unless the user explicitly requests a bounded evaluation—no automatic alternate routing and no universal savings claims.

## Route and reserve

Select **fleet** before discovery and claims. Prefer ordinary routes by fit on either fleet: **Pi DeepSeek Flash** (`pi-dsflash-*`), Grok or Cursor Auto when tools/evidence need them; premium models sparingly. Judgment slots are authorized only under the coordinator profiles above. Do not broadcast routine work to the entire pool. On quota/auth failure, record once, avoid retrying the unavailable route, and select an authorized alternative—do not change subscriptions/credentials to recover. Never silent-fallback between fleets.

### When `fleet bb`

1. Use `bb-herdr list|claim|dispatch|wait|release|status|canary`.
2. Fresh thread per independent job; `--continue-job` for same-job follow-ups.
3. Claims are backend-scoped (`backend: bb`); do not use Herdr `claims.py` for bb slots.
4. Give files a single writer across workers.

### When `fleet herdr`

1. Discover available sessions, then inspect the intended existing session explicitly. Prefer the local pool when available; resolve remote access only through the operator’s host procedure. Never hardcode pane IDs from a previous task. Missing access means explain a direct fallback or concrete blocker, not pretend delegation.
2. Discover live agent names and pane IDs each time—do not hardcode pool layout.
3. Before dispatch, claim the returned pane ID on the **server host** with [claims.py](../scripts/claims.py). Use a unique owner such as conversation ID plus job UUID. All coordinators must use the same default host-local state directory (or an explicitly shared `--state-dir`). Example:

   `python3 <skill>/scripts/claims.py claim --session <session> --worker <pane-id> --owner <owner>`

   Claim failure means choose another available worker or wait. Recheck live occupant and readiness after claiming. Do not dispatch to a busy, blocked, or foreign-owned pane. Do not move claimed panes. Status uses the same arguments without owner; release requires the matching owner. Before reset or repurposing a slot, verify ownership and that the preceding attempt is settled (see Ordinary-worker conversation boundaries).
4. Claims have no automatic expiry or force-steal. Recover an abandoned claim only after verifying its prior job is no longer active and resolving ownership; never remove a claim merely because it is old. Reservations coordinate cooperating clients; they do not prevent manual terminal input.
5. Give files a single writer. Concurrent conversations must not edit the same files without explicit coordination, even when using different workers. Shared directories remain the default unless isolation is justified.

## Ordinary-worker conversation boundaries

Before the first prompt for a **different independent job**, the coordinator MUST establish a **fresh agent conversation** using a supported harness mechanism. The persistent pool pane MAY remain. If conversation freshness cannot otherwise be established, the coordinator MUST use a supported process restart that starts a new conversation without resuming prior history. If no supported clean-start path is available, dispatch MUST stop for that slot and the blocker MUST be disclosed; the coordinator MUST NOT silently continue the old conversation or substitute a model.

For **same-job** clarification, repair, blocked resume, or closing-related follow-up, the coordinator MUST continue the existing claimed conversation when it remains available, usable, and correctly owned. Before resuming after a pause or blocker, the coordinator MUST revalidate ownership and relevant current state. A worker’s `done` message alone does not end the job or require a reset. If the same-job conversation is unavailable or demonstrably unusable, the coordinator MAY use a fresh replacement after settling any uncertain attempt and providing a scoped evidence-based handoff; required gates and repair budgets remain unchanged.

Before resetting or repurposing a slot, the coordinator MUST verify ownership and establish that the preceding attempt is settled, with no unresolved running work or uncertain effects. The coordinator MUST preserve required receipts, artifacts, and outstanding obligations outside the conversation. The coordinator MUST NOT prompt or reset a busy or foreign-owned session. Claim-before-prompt and all existing reservation, role, model, and authorization requirements remain in force; a reset does not satisfy or replace them.

The coordinator SHOULD perform the reset immediately before the next different assignment, rather than automatically after every `done` message. An already verified fresh, unused conversation satisfies the requirement; a redundant reset is not required. Job identity follows the objective and acceptance contract, not an idle pane, elapsed time, a new message, or an arbitrary job label. Unrelated objectives MUST NOT be treated as same-job follow-ups to avoid reset.

Conversation freshness does not establish a clean working directory, filesystem, environment, or external execution state. The coordinator MUST verify relevant runtime state separately and preserve unrelated work. These ordinary-worker rules do not relax the judgment-slot requirement: judgment sessions start a fresh process/conversation for each new job and retain the same session for same-job follow-ups.

Cleared terminal output and prompt text alone are **not** evidence of freshness. Prefer observable evidence such as a new session identity or documented non-resuming launch behavior. Reset commands and installed-version details stay host-local.

## Ownership

The user-facing coordinator owns the objective, decomposition, acceptance criteria, prioritization and tradeoffs, delegation direction, evidence judgment, integration, quality enforcement, and final acceptance. Workers own substantive implementation, research and exploration, debugging, repairs, test and fixture authoring, and routine checks. In Auto-main, Astra is intelligence-only (plan/direction/quality clarification/closing recommendation), not an implementation worker. Thinking, answering the user, planning briefs, and routing are coordinator work; do not launch a worker just for conversation.

## Assignment

Default assignment is a complete reviewable result: clear constraints, writable paths, exact runtime or test command where relevant, reproducible checks, compact receipt, and proof. When a QA fixture is required, it must be runnable and render-smoke-checked unless the task forbids it. The worker fixes its ordinary failures within the bounded scope.

Before the first prompt, apply [Ordinary-worker conversation boundaries](#ordinary-worker-conversation-boundaries): classify same-job vs different independent job; for a different job, verify conversation freshness (not merely a cleared pane); verify claim/owner, intended model/role, and relevant cwd/runtime state; then send the scoped assignment.

Write a small task file: job ID, worker role/no redelegation, outcome, source access, writable paths, constraints, acceptance criteria, time/repair budget and deliverables. Share only necessary authorized data. Keep credentials host-local.

Send a short pointer. Require a fresh receipt written last and the same compact final answer: `job_id`, `status`, `artifacts`, `checks`, `blocker`. Target 200–400 bytes, ceiling 600 bytes; use relative artifact paths. Status is done or blocked. Detailed code, research and logs stay in files. A blocked receipt must identify missing evidence without inventing it.

## Supervision

Keep one outstanding awaited handle per job. Prefer bounded waits or compact receipt/status checks at meaningful milestones or timeouts. Do not overlap waits or reread transcripts while nothing changes. Batch independent checks.

Do not reproduce an ongoing worker investigation or take over ordinary edits merely because convenient. First reproduce or review evidence before requesting a repair, and return repairs to the owning worker. Retain independent verification and material evidence. Never accept worker self-report blindly. A passing test suite alone does not establish complete quality. A multiplexer lifecycle transition (`idle`/`done`) or a prompt wait error is not proof the task finished—correlate receipt, artifacts, and independent checks. Stalled prompts require inspection before retry; retry with a short pointer, not a duplicate long prompt.

After acceptance or explicit settlement, release the claim. Keep user-requested persistent workers idle; close only temporary workers owned by this task.

## Direct exceptions

Necessary direct work only: unique inaccessible connector or access; time-critical safety; a tiny isolated closure cheaper than re-dispatch; a demonstrated unavailable or failed worker after a bounded attempt. State the material exception, the scope, and the return of ownership. A series of small edits is substantive execution, not a triviality loophole.

“I might do better” is insufficient. If workers are unavailable, disclose the fallback and continue within authorization.

## Efficiency and routing

Measure efficiency by coordinator responses, input/output, and real acceptance or rework — not receipt size alone. Do not invent a savings percentage. Keep model-route boundaries: no silent substitution; no recursive workers; claims, user mode on/off, and scopes preserved.

Track meaningful worker failures and successful repairs in receipts and checkpoints to improve routing. Do not infer universal rankings from small tests.
