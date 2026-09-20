# Policy scenarios (expected decisions)

Synthetic canaries for coordinators. Not automated executable tests.

| ID | Setup | Expected |
|---|---|---|
| P1 | Auto-main substantive job | Early judgment gate before plan lock; ordinary pool implements; Astra does not implement |
| P2 | Auto-main after exec + mandatory checks pass | Closing packet → `aligned`/`repair_required`/`blocked`; Auto accepts |
| P3 | Mandatory check failed | Repair or blocked — **not** closing approval rubber-stamp |
| P4 | Closing says repair_required | Use remaining repair budget; budget not reset |
| P5 | Repair budget exhausted | Change approach or settle blocked |
| P6 | Astra-main ordinary task | No recursive Astra consultation; ordinary pool executes |
| P7 | Sol-main ordinary owns material outcome | Closing alignment unless final combined result already reviewed |
| P8 | Mechanical hard-checked task | May skip early and closing with recorded exemption |
| P9 | Scope grows after skip | Re-open gates |
| P10 | Claim conflict | Zero prompts on that pane |
| P11 | Busy / foreign pane | Refuse dispatch |
| P12 | Wrong judgment model banner | Blocker; no silent substitute |
| P13 | Receipt done without artifacts | Reject; correlate independently |
| P14 | Mode off with owned running job | No new dispatch; settle owned work; do not kill |
| P15 | Takeover exception | State exception, scope, return of ownership |
| P16 | Lost judgment session mid-job | Reconstruct plan from durable evidence before closing review |
