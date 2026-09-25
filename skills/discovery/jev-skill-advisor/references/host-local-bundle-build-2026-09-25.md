# Host-local Jev bridge bundle: build and current boundary

The Mac bridge's release/profile data is already under ~/.local/state/jev-skill-advisor/releases. Its Python code and launcher still run from the Dropbox checkout in the installed Cursor, Codex, and Pi clients. Do not claim a host-local cutover or Desktop proof from the bundle build alone.

The builder is adapters/registration/freeze_bundle.py. It exports only skills/discovery/jev-skill-advisor from pinned Git revision 3fc6118, exports the archived uv.lock's MCP dependency hashes, builds a wheel offline, installs it non-editably into a host-local venv, copies run_bridge.py, replaces the MCP entrypoint with an isolated Python launcher, rejects .pth, editable installs, secrets, and Dropbox path bytes, then writes a file-hash manifest and removes write permission. Existing bundle builds verify and return unchanged.

The current bundle is /Users/jonbennett/.local/state/jev-skill-advisor/runtime/bundles/a528d001220e-3fc6118. On 2026-09-25 the first build returned built; the exact repeat returned unchanged. verify checked 1,125 entries. An isolated Python import resolved into that bundle, and its skill-advisor-mcp --help worked. The release pin remains a528d001220eec155f1172f3b9b76793177ae2479dd5d054ae246c047e2e26bc. The Python suite including registration/builder tests passed 420/420; Pi Node suite passed 13/13. A Grok 4.7 worker timed out after 900 seconds without a handoff or source file; the lead implemented and verified this bounded builder.

Build or verify from the agent-tools repo:

```sh
skills/discovery/jev-skill-advisor/.venv/bin/python skills/discovery/jev-skill-advisor/adapters/registration/freeze_bundle.py build --repo /Users/jonbennett/Library/CloudStorage/Dropbox/Projects/agent-tools --output-root /Users/jonbennett/.local/state/jev-skill-advisor/runtime/bundles
skills/discovery/jev-skill-advisor/.venv/bin/python skills/discovery/jev-skill-advisor/adapters/registration/freeze_bundle.py verify --bundle /Users/jonbennett/.local/state/jev-skill-advisor/runtime/bundles/a528d001220e-3fc6118
```

Cutover is not done. Explicit Opus review attempt returned opus_timeout and, per the explicit-advisor rule, no substitute was used. Before switching clients: get the review; back up Codex, Cursor and installed Pi settings; make runtime/current initially point to a host-local shim of the old command; register the stable paths; verify no behavioral change; atomically point current to the verified bundle; restart clients and run exact five-schema, live read-byte parity, denial, Dropbox-read-denied sandbox, import-provenance and lsof checks. Rollback switches current back to the shim. Keep native Notion routes and skill symlinks until their independent gates pass.
