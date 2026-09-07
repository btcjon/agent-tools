# Install and operate KSU

Requires Python 3.9+ and Topgrade 17.9.0 or newer. Tested engine schema: 17.9.0. Install Topgrade through a trusted platform manager using its [official instructions](https://github.com/topgrade-rs/topgrade#installation). Do not pipe an unreviewed remote script into a privileged shell. Keep Topgrade itself covered by the selected package manager.

Point any Agent Skills-compatible agent at:

https://github.com/btcjon/custom-skills/tree/main/skills/devops/ksu

Ask: “Install KSU on these machines; updates at 3 a.m. local time; allow app/service restarts and defer full reboots.” Clone the repository, load `skills/devops/ksu/SKILL.md`, then enroll each accessible machine locally or through its existing approved remote route. Skill source can be linked into the harness's supported skill folder; managed skill buses should publish through their authority. Do not copy runtime state between machines.

## Host setup

```sh
python3 scripts/ksu.py setup --time 03:00 --restart-policy services
python3 scripts/ksu.py preview
```

State defaults to `~/.local/state/ksu`; use `--state-dir PATH` before the subcommand to override. Setup copies the runner there so the schedule does not depend on a checkout or synced drive. The saved PATH captures the installation account's manager locations. Repeat enrollment for other users when their installed software differs. Updating the skill source does not replace an already enrolled runner: after validation, copy the new runner to the local state directory between runs.

Review `preview.log` alongside installed-app inventories. Edit `config.json`: fill `only` with the verified Topgrade step names and set `coverage_reviewed` to `true`. For example, a Homebrew-only enrollment uses `"only": ["brew_formula", "brew_cask"]`. This is partial coverage; do not label it complete-machine coverage. Add other steps only after their dry runs and prerequisites have been checked. `disable` adds explicit exclusions; it cannot override the built-in exclusions.

Complete `coverage.md` with a table of software/manager, updater step, privilege requirement, restart behavior, verification command, and status (covered, pinned, unsupported, or pending). For unmanaged apps, look up the vendor's official updater. Never silently claim an app is covered because another package manager is present.

```sh
python3 scripts/ksu.py preview
python3 scripts/ksu.py run
python3 scripts/ksu.py status
python3 scripts/ksu.py schedule
```

Read the actual first-run output and independently verify versions/outdated lists and important service health. `engine_completed` means the engine returned zero; skipped or pinned software can still exist. The `run` command performs real updates and belongs inside the user's authorized enrollment scope.

## Scheduling and privileges

- macOS: per-user LaunchAgents `org.ksu.nightly` and `org.ksu.watchdog`, 03:00 calendar schedule and hourly health check. Requires a logged-in user session. Launchd can catch a sleeping machine on wake; a powered-off or logged-out Mac is not always-on. Review `launchctl print gui/$(id -u)/org.ksu.nightly`. For boot-time unattended coverage, deploy a separately reviewed root-owned LaunchDaemon/helper; never run Homebrew as root.
- Linux: per-user systemd services/timers `ksu-nightly` and `ksu-watchdog`, persistent catch-up, lingering enabled during setup. Verify `systemctl --user list-timers 'ksu-*'` and `loginctl show-user "$USER" -p Linger`. Sandboxes without systemd need the platform's scheduler; the bundled installer reports failure rather than pretending cron was installed.
- Windows: Scheduled Tasks `KSU-nightly` and `KSU-watchdog`, catch-up enabled, overlap disabled. Default tasks use the enrolling account's interactive context. Validate signed-out execution separately before claiming unattended server coverage; configure the task's credential/logon type through Windows when required. Never save passwords in this repository or config. Windows scheduler and reboot generation have unit coverage but requires native Windows verification.

Run package managers as their owning user. The POSIX runner wraps sudo with `-n`, so it fails instead of prompting. Existing narrow admin grants can be used; KSU does not create broad passwordless sudo rules. A system updater lacking admin access is pending coverage. Many Windows installers require elevation or cannot run silently; inventory those explicitly.

`--restart-policy services` permits native updater app/service restarts, with full reboot deferred. `defer` requests no restarts but requires excluding managers that cannot enforce it. `reboot` requests a reboot only after a successful run and a recognized signal: Linux `/var/run/reboot-required` or `needs-restarting -r`, Windows servicing/Windows Update reboot registry markers, or macOS explicit restart-required updater output. Unknown signals remain pending. Reboot command outcomes are recorded. macOS/Windows reboot adapters are unit-tested, not verified by rebooting a live machine. Configuration is user-selectable; inspect native vendor reboot behavior during enrollment.

## Quiet recovery, status, rollback

Two retries follow transient failures, with 60/120-second backoff. Unknown errors are preserved for agent diagnosis. Package transactions are not killed on timeout: interrupting them can corrupt state. After four hours, the watchdog marks a running job stale. After 36 hours without a run, it marks stale; it never starts a second updater over an uncertain transaction. The watchdog updates `health.json` hourly. A fully dead machine cannot monitor itself; fleet-level monitoring requires a separate always-on controller.

`status` prints the receipt and coverage path; `watchdog` writes health quietly. Exit status is nonzero for unhealthy state. The runner retains latest and previous updater logs. Native scheduler stdout/stderr should be rotated by host log management. Default notification policy is silent, including recorded failures; opt-in delivery belongs in the user's existing notification system.

```sh
python3 scripts/ksu.py unschedule
```

This disables/removes KSU schedules and preserves receipts/config. It does not downgrade updated packages or disable other software's native updater. Use the package vendor's supported rollback or machine backup if needed. Never claim generic rollback can reverse arbitrary package upgrades.

## Sources

- [Topgrade engine and supported platforms](https://github.com/topgrade-rs/topgrade)
- [Pinned configuration reference](https://github.com/topgrade-rs/topgrade/blob/v17.9.0/config.example.toml)
- [Homebrew upgrade behavior](https://docs.brew.sh/Manpage#upgrade-options-installed_formulainstalled_cask-)
- [Windows package upgrade](https://learn.microsoft.com/en-us/windows/package-manager/winget/upgrade)
- [systemd timers](https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html)
