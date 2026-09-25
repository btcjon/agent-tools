**Hold gateway-default promotion; retain the opt-in implementation.** The reported 10/10 native correctness and zero write calls support continued testing.

The decisive gap is the comparator: production currently uses **bridge**, but this experiment compared **off versus native**. It establishes that native hints can help unguided discovery, not that native improves the current production route. The single bridge canary cannot settle that comparison. Five tasks on one host also leave latency, gateway-session behavior, and cross-harness compatibility unresolved. Cache-inclusive totals cannot establish initial-context or cost savings.

Smallest next step: build one reusable acceptance runner, then finish this decision:

1. Compare **bridge versus native**, using the same five tasks, two repeats, reversed order: 20 fresh sessions. Record correctness, Jev latency, whole-task latency, main-model calls, separately reported cache/input/output usage, and invoked operations.
2. Add deterministic route checks: native available → select it; native unavailable → select CLI/API fallback; expose only the selected method; skill scripts/references remain accessible. Run these against Hermes and one other existing adapter.
3. Promote only if native preserves correctness and shows a useful measured advantage or demonstrated capability coverage. Require zero writes, expected read dependencies, one gateway-path smoke test, and restoration of the backed-up adapter/environment.

I reviewed the packet and evidence note; I did not independently inspect raw traces.
