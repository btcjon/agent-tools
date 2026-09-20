# Start here: person and agent

Give your agent this request: "Read Gmail Triage's SKILL.md and quickstart. Help
me connect my Gmail, run the offline demo, then show a read-only sender review."

## 1. Load the package

Clone https://github.com/btcjon/agent-tools and locate
`skills/email/gmail-triage`. Load the entire package, including references,
scripts and fixtures. Use the agent's supported skill installer or project skill
directory. Do not overwrite an existing installation. If the harness cannot
install skills, ask it to read SKILL.md directly; it still needs tool access and
Python 3.10+ to run the helpers. MCP access alone does not provide Python.

Hermes users can link the package into their configured skills directory as in
the repository README. Codex, Claude Code and Cursor users should ask their
agent to install this exact package using its current skill installation
instructions, then start a fresh session and confirm the skill is discoverable.
This avoids assuming every version or organization uses the same global path.

From the package directory run:

```bash
python3 scripts/triage_core.py selftest
```

Expected: all synthetic steps pass. This does not connect Gmail.

## 2. Connect Composio

If the agent already has Composio tools, use that connection flow. Otherwise:

- Terminal-capable agents: follow the official installation instructions at
  https://docs.composio.dev/docs/cli. The documented installer is
  `curl -fsSL https://composio.dev/install | sh`. Inspect and run it on the
  person's chosen machine, then open a new terminal, run `composio --version`
  and `composio login`. The human completes sign-in in their browser.
- Codex or Claude Code: `composio setup --target codex` or
  `composio setup --target claude` installs the native Composio integration.
- MCP clients: add `https://connect.composio.dev/mcp` through the client's
  supported remote-MCP setup and complete its authorization flow. See
  https://docs.composio.dev/docs/composio-connect.
- Windows CLI users: use WSL as described by Composio's installer documentation.

Then connect Gmail (`composio link gmail` on the CLI). The human selects the
mailbox and grants access. Never request passwords, OAuth tokens or API keys in
chat. SDK developers may need a Composio project key; the personal CLI/MCP path
should not invent a project-key requirement.

Composio is a third party handling Gmail access; the agent/model also sees the
mail data supplied to it. Review their privacy terms and current plan limits.
Costs and permitted scopes vary. Managed OAuth can avoid a personal Google Cloud
project, but Workspace policy or unapproved scopes can still block features.

## 3. Prove access, then choose preferences

Read references/composio-setup.md. Fetch the connected profile and a maximum of
five messages using discovered tool schemas. Confirm the mailbox matches the
person's choice. Inspect actual tool availability; do not put an invented
allowlist into preflight and call that verification. A profile read proves
identity/read access only, not permission to create filters.

Store any outputs under a private directory outside the clone:

```bash
umask 077
mkdir -p "$HOME/.gmail-triage/review"
chmod 700 "$HOME/.gmail-triage/review"
```

Use absolute paths under that directory for every input/output in the examples.
Ask which mailbox, protected senders, scan window, and preferred action to use.
Start read-only. Explain that selecting an unsubscribe action submits a request;
it does not prove the sender stopped. Explain Trash retention before selecting
Trash; archive-and-label is available without deletion.

## 4. First real batch

Follow SKILL.md's cleanup review with a small initial batch, recording the scan
window and whether the fetch was truncated. Show counts and precise action
choices. Only the person's explicit selection authorizes execution. Run filter
comparison first, perform selected actions through Composio/the unsubscribe
helper, read results back, then record each outcome. If a request times out,
its outcome may be unknown: inspect state before any retry.

Finish by showing completed, pending and failed actions, how to request undo,
and that nothing runs between sessions. Increase batch size after this succeeds.

## Verification boundary

This release has offline behavioral tests and a synthetic CLI walkthrough. A
new user's OAuth flow, granted scopes, real Gmail writes and actual publisher
unsubscribe endpoints must be verified in that user's environment. Continuous
classification and its retry/dedupe state require a separately implemented
runtime; the SQLite receipts here are for cleanup, not a production classifier.
