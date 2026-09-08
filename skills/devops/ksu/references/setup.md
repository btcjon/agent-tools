# KSU v0.2: discovery and selection

Point an Agent Skills-compatible agent at [KSU](https://github.com/btcjon/custom-skills/tree/main/skills/devops/ksu) and ask it to discover your machines' AI harnesses, packages and skills before enabling maintenance. Clone the public repository and load `SKILL.md`. No private infrastructure, paid agent session, or Topgrade installation is required for the selection runner.

## Review without updating anything

```sh
python3 scripts/ksu.py discover
# Open ~/.local/state/ksu/inventory.html locally.
# Or have your agent present inventory.md.
python3 scripts/ksu.py select --file /path/to/ksu-selections.json
```

The page has searchable categories, per-item checkboxes, a user-triggered “Select recommended” button, saved exclusions, source/branch information, blocker explanations, and an export button. Recommendations favor supported harnesses, global tools and skill sources. OS packages and other programs remain available but are not pre-recommended. No choices are initially enabled. Exporting downloads a small selection JSON; it does not update software or silently save a schedule.

Alternative selection surfaces:

```sh
python3 scripts/ksu.py select --interactive
python3 scripts/ksu.py select --enable npm:THE_DISCOVERED_ID
python3 scripts/ksu.py select --exclude npm:THE_DISCOVERED_ID
```

Use actual IDs from this machine's inventory, never the illustrative ID above. Selection imports reject unknown IDs, blocked targets, stale revisions, and foreign host IDs before writing anything. Additional inventory roots use `discover --root /path/to/skills` (repeat the option). They enumerate skill folders at up to two category levels, not arbitrary project dependencies.

## What discovery covers

| Area | Discovery and update scope |
|---|---|
| AI harnesses | Known CLI names across PATH and common user locations, package-owned installations, versions where available, common Hermes/source checkout locations, Mac app bundles, branch/upstream/local-change evidence. Unknown installers remain visible and blocked. A recognized Codex Router checkout at `~/.local/share/codex-router` uses `codex-router update` and may restart its LaunchAgent/systemd service. |
| Homebrew | Installed formulae and casks, versions, taps, pins; exact selected package upgrades. |
| npm | Each discovered global prefix and configured registry; exact selected global packages. Linked/local packages remain blocked. Multiple Node installations can be separate targets. |
| pipx | Installed tool environments and versions; uncomplicated registry tools can be upgraded individually. Constrained/direct/local installs require review. |
| uv tools | Versions and receipt requirements; constraints are retained and source changes invalidate selections. Local/VCS/direct-source tools remain blocked. Receipt inspection requires Python 3.11+. |
| Cargo / Rust | Installed registry binaries and toolchains; exact crate/channel updates. Cargo requirements, features, profile and target are retained from install metadata; missing metadata, Git-source crates and version-pinned/custom toolchains stay blocked. |
| Debian/Ubuntu | Installed APT packages and holds; exact `--only-upgrade --no-remove` targets. Dependencies can change. |
| RPM/DNF | Installed name/architecture/version; exact upgrades retaining DNF exclusions. |
| Windows | WinGet export with versions and source IDs; exact ID/source upgrades after a readable pin check. Unknown/truncated pin output stays blocked. Unmatched software is not covered. Native Windows validation remains pending. |
| Skills | Conventional skill roots plus explicit extra roots, real-path deduplication, projection aliases, repository origin/branch/dirty state. Clean tracked main/master source repos are whole-repository selections. |
| Shared skills | Authority/consumer detection when a skill-system manifest exists. Consumer projections and authority publication workflows are visible but excluded from generic updates. |
| Plugins | Conventional plugin directories and Codex plugin cache versions. Active installation is not inferred from a cached directory. Update through the harness. |
| Other programs | Mac app-bundle versions and supported package-manager entries. Unmanaged vendor updaters require dedicated procedures. |
| Environments/services | Common Python/Node environment directories are inventoried separately. Relevant launchd/systemd definitions and available user-manager runtime states are shown for context. |

Discovery checks common `.local/bin`, Cargo, Bun, npm-global, mise/asdf, nvm and fnm locations even in a short noninteractive PATH. Manager commands use their own executable directory first so different Node installations do not accidentally share the wrong interpreter. Probes time out after 20 seconds and a 120-second command budget; unavailable or malformed probes appear in the gaps report. Placeholder files are not forcibly downloaded. Expected Git metadata gaps may also appear in that report.

This is current-account discovery, not proof that every account, application, package manager, native installer, remote host or virtual environment was found. Flatpak, Snap, Conda, arbitrary pip environments, project lockfiles, firmware, containers and custom deployment systems need additional adapters. Do not claim blanket machine coverage.

## Save and activate a schedule

```sh
python3 scripts/ksu.py setup --time 03:00 --restart-policy services
python3 scripts/ksu.py preview
# Within the user's selected update authorization:
python3 scripts/ksu.py run
python3 scripts/ksu.py status
python3 scripts/ksu.py schedule
```

Discovery and selection may happen before or after setup. State defaults to `~/.local/state/ksu`; pass `--state-dir PATH` before the subcommand to override. Setup copies both Python modules into that state directory. Config is local. The schedule never depends on the public repository or a synced skill file being available at 3 a.m.

A nightly run rediscovers, checks saved source fingerprints and blockers, updates only selected supported targets, retries only failing transient operations (twice), then rediscovers for version/presence readback. Each item has its own outcome. Commands are static adapters with validated identifiers, not strings imported from JSON. Package updates may change dependencies; a selected skill repo includes its whole checkout. A successful command with unchanged version can simply mean already current, but KSU does not label that as independently proved latest-version coverage.

`preview` performs discovery and writes/prints `plan.json`; it does not execute upgrade commands. Legacy `only`/`coverage_reviewed` fields from v0.1 are insufficient to authorize item updates. Empty selections cannot start an updater.

## Restart, scheduler and privilege details

- `services` (default): native updater app/service restarts are permitted; no KSU-requested full reboot.
- `defer`: strict no-restart mode blocks adapters whose native installers may restart things. Currently only skill-repository, uv and pipx actions are eligible; these do not ask KSU to restart services.
- `reboot`: opt-in reboot only after success and a recognized OS signal. Linux uses `/var/run/reboot-required` or `needs-restarting -r`; Windows uses servicing/Windows Update registry markers; macOS uses explicit restart-required updater output. Unknown signals remain pending. Reboot adapters have unit coverage, not destructive live reboot testing.

macOS uses per-user LaunchAgents `org.ksu.nightly` and `org.ksu.watchdog`. A logged-in session is required; this is not a boot-time root daemon. Linux uses persistent user systemd timers `ksu-nightly` and `ksu-watchdog`, with lingering enabled. Windows uses Scheduled Tasks under the enrolling account; signed-out/elevated operation requires native setup and verification. Sandboxes without these schedulers need their platform's scheduler. KSU never creates broad passwordless sudo rules; POSIX sudo uses `-n` and permission failures remain visible.

Check the actual scheduler with `launchctl print`, `systemctl --user list-timers 'ksu-*'` plus `loginctl show-user "$USER" -p Linger`, or Windows Task Scheduler. Existing update services appear in the inventory so the agent can prevent overlapping coverage. Their presence alone does not automatically disable KSU or those services.

## Runtime upgrades, health and removal

After obtaining a new KSU release:

```sh
python3 scripts/ksu.py install-runtime
```

This replaces the host-local modules under the same process lock, staging the entrypoint last and preserving selections/config. It refuses an unfinished transaction. For v0.1 migration, then run discovery and make selections before the next scheduled update. Do not distribute one host's state to another.

The hourly watchdog records stale jobs after four hours, missed runs after 36 hours, and failures in `health.json`. It does not launch overlapping transactions or notify the user by default. A dead machine cannot monitor itself; fleet-wide health needs a separate always-on controller. Logs retain the latest and previous update run. Scheduler logs use host log management. Inspect `receipt.json` before clearing an interrupted transaction's `running` state.

```sh
python3 scripts/ksu.py unschedule
```

This removes KSU schedules and preserves local evidence. It does not undo package updates or change other updaters. Use supported package rollback or backups when needed.

## Validation and upstream behavior

Run `python3 -m unittest discover -s tests -v`. Tests use synthetic inventories and mocked update commands; they never upgrade the test machine. Linux/macOS discovery has also been exercised on real hosts. Selection/export/import data contracts are tested. Native Windows and rendered-browser validation remain pending.

Primary references: [Homebrew](https://docs.brew.sh/Manpage), [npm update](https://docs.npmjs.com/cli/commands/npm-update), [pipx](https://pipx.pypa.io/stable/reference/cli.html), [uv tools](https://docs.astral.sh/uv/concepts/tools/), [Cargo install](https://doc.rust-lang.org/cargo/commands/cargo-install.html), [WinGet export](https://learn.microsoft.com/en-us/windows/package-manager/winget/export), [WinGet upgrade](https://learn.microsoft.com/en-us/windows/package-manager/winget/upgrade).
