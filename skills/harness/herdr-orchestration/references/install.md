# Install

Installation, activation, and worker provisioning are **separate** steps. Install alone must not launch agents, change global contracts, bypass sandboxing, enroll remote hosts, or enable default-on Herdr.

## 1. Obtain the package

Product prerequisite: install Herdr from **https://herdr.dev** (agent guide: https://herdr.dev/agent-guide.md). Stock CLI skill discovery follows Herdr/`herdr --help` — not this repo.

```bash
# Proposed skills CLI (verify on your harness; not all environments support it)
npx skills add btcjon/agent-tools --list
npx skills add btcjon/agent-tools --skill herdr-orchestration
```

Manual:

```bash
git clone https://github.com/btcjon/agent-tools.git
# Cursor / Codex / Hermes-compatible: link ONLY the skill package directory
ln -s "$(pwd)/agent-tools/skills/harness/herdr-orchestration" \
  "<your-harness-skills-dir>/herdr-orchestration"
```

Concrete harness skill directories (consult current product docs if these move):

| Harness | Typical skill directory | 0.1.x status |
|---|---|---|
| Hermes | `~/.hermes/skills/` | Path documented; symlink method **verified** in isolated temp |
| Cursor | User/project skills dir per Cursor docs | Layout compatible; discovery **verify after link** |
| Codex | Skills path per Codex docs | Layout compatible; discovery **verify after link** |
| Claude | Skills/plugin path per product docs | Layout compatible; **unverified** discovery |

Never overwrite an existing destination skill. Verify relative references (`references/`, `scripts/`) remain inside the installed package.

## 2. Harness discovery (document per harness)

| Harness | Load notes | 0.1.x verification |
|---|---|---|
| Cursor | Link skill package; reload agent session | Symlink+resolve **verified** offline; in-app discovery verify locally |
| Codex | Skills path per Codex docs; new session | Layout compatible; verify locally |
| Hermes | Symlink into `~/.hermes/skills/`; `skill_view` / equivalent | Symlink method **verified** in isolated canary |
| Claude | Skills / plugin path per product docs | Documented; **unverified** |

Treat combinations as **unverified** until you run discovery on that harness. Reload after install. Pin to a git tag for updates; remove only package-owned links/snippets on uninstall.

## 3. Read-only prerequisite checks

After load, check without launching workers:

- `herdr --help` / version
- Reachable selected session
- Python/platform (POSIX for claims)
- Judgment + ordinary routes configured (or honest “missing” status)
- Shared claims `--state-dir` agreed among coordinators
- Permission policy understood
- If using the default `fleet bb`: a separately provisioned `bb-herdr` runtime and worker pool. This package does not install them.

## 4. Activation (optional)

- Per conversation: `orchestration on|off|status` and `fleet herdr|bb|status` (default fleet **bb**)
- Herdr-only setup: select `fleet herdr` before dispatch; do not leave the default on `bb` when `bb-herdr` is unavailable.
- Default-on for future sessions: explicitly merge [CONTRACT-SNIPPET.md](../CONTRACT-SNIPPET.md) into your global contract

## 5. Offline package verification

```bash
cd /path/to/agent-tools/skills/harness/herdr-orchestration
python3 scripts/check_package.py
python3 -m unittest discover -s tests -t .
```

## Troubleshooting

| Symptom | Action |
|---|---|
| Skill not discovered | Confirm link target is the `herdr-orchestration` package dir containing `SKILL.md` |
| Broken relative links | Do not flatten `references/` away from `SKILL.md` |
| Duplicate dispatches | Align `--state-dir` on the server host |
| `fcntl` / claims errors on Windows | Unsupported in MVP |
| Missing Astra route | Disclose blocker; do not impersonate |

## Operators with a private control plane

If you already project skills from a private control plane, keep that authority for your hosts. Prefer linking this public package or merging by hash-verified copy—do not run unattended `npx skills add` against production projections without review.
