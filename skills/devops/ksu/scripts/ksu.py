#!/usr/bin/env python3
"""KSU: host-local nightly maintenance around Topgrade. Python 3.9+, no dependencies."""
import argparse
import contextlib
import datetime as dt
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys
import time

VERSION = "0.1.0"
DEFAULT = Path.home() / ".local" / "state" / "ksu"
# Broad engine discovery is narrowed at enrollment after reviewing its dry run.
BASE_DISABLE = ["remotes", "git_repos", "myrepos", "vagrant", "containers", "firmware", "self_update", "custom_commands", "restarts", "skills"]


def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2) + "\n")
    tmp.chmod(0o600)
    os.replace(tmp, path)


def read(path):
    return json.loads(path.read_text())


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


@contextlib.contextmanager
def lock(state):
    f = (state / "run.lock").open("a+")
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0); f.write("0"); f.flush(); f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        f.close()
        raise RuntimeError("KSU is already running")
    try:
        yield
    finally:
        f.close()


def execute(argv, env, logfile):
    # Never kill a package transaction on a wall-clock timeout. Watchdog flags it.
    # Closed stdin and noninteractive sudo prevent hidden password prompts.
    with logfile.open("ab") as out:
        out.write(("\nKSU " + now() + " " + json.dumps(argv) + "\n").encode()); out.flush()
        try:
            return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=out,
                                  stderr=subprocess.STDOUT, env=env).returncode
        except OSError as exc:
            out.write((str(exc) + "\n").encode())
            return 127


def engine_config(cfg, path):
    disabled = sorted(set(BASE_DISABLE + cfg.get("disable", [])))
    # KSU isolates the engine from the user's interactive Topgrade config/hooks.
    s = '[misc]\nassume_yes = true\nask_retry = false\nauto_retry = 0\nnotify_end = "never"\npre_sudo = false\ncleanup = false\nno_self_update = true\nshow_skipped = true\n'
    s += "disable = " + json.dumps(disabled) + "\n"
    if cfg.get("only"):
        s += "only = " + json.dumps(cfg["only"]) + "\n"
    s += '\n[brew]\ngreedy_cask = true\nautoremove = false\n'
    s += '\n[linux]\napt_arguments = "--no-remove"\n'
    s += '\n[git]\npull_predefined = false\n'
    s += '\n[windows]\naccept_all_updates = true\nupdates_auto_reboot = "no"\nwinget_silent_install = true\n'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(s)


def environment(state, cfg):
    env = os.environ.copy()
    env.update(PATH=cfg["path"],
               TOPGRADE_NO_SELF_UPGRADE="1", NONINTERACTIVE="1", CI="1",
               DEBIAN_FRONTEND="noninteractive", NEEDRESTART_MODE="a" if cfg["restart_policy"] != "defer" else "l")
    if os.name != "nt":
        import shlex
        bindir = state / "bin"
        bindir.mkdir(exist_ok=True)
        sudo = shutil.which("sudo", path=cfg["path"])
        if sudo:
            wrapper = bindir / "sudo"
            wrapper.write_text("#!/bin/sh\nexec " + shlex.quote(sudo) + ' -n "$@"\n')
            wrapper.chmod(0o700)
            env["PATH"] = str(bindir) + os.pathsep + cfg["path"]
    return env


def failure_class(text):
    text = text.lower()
    if any(x in text for x in ("password is required", "authentication", "permission denied", "not in the sudoers", "a terminal is required")):
        return "credentials_or_permissions"
    if any(x in text for x in ("could not get lock", "unable to acquire", "another process", "timed out", "temporary failure", "connection reset", "429", "503", "network is unreachable", "could not resolve")):
        return "transient"
    return "needs_diagnosis"


def run(state, dry=False):
    cfg = read(state / "config.json")
    if not dry and (not cfg.get("coverage_reviewed") or not cfg.get("only")):
        raise RuntimeError("Review coverage and enroll explicit Topgrade steps before running")
    env = environment(state, cfg)
    config = state / "engine-config" / "topgrade.toml"
    engine_config(cfg, config)
    with lock(state):
        if not dry and (state / "receipt.json").exists() and read(state / "receipt.json").get("status") == "running":
            raise RuntimeError("Previous transaction outcome is unknown; verify package processes/state before clearing the running receipt")
        log = state / ("preview.log" if dry else "latest.log")
        if log.exists():
            os.replace(log, state / (log.name + ".previous"))
        argv = [cfg["topgrade"], "--config", str(config), "--yes", "--no-retry", "--skip-notify", "--no-self-update", "--disable", *sorted(set(BASE_DISABLE + cfg.get("disable", [])))]
        if cfg.get("only"):
            argv += ["--only", *cfg["only"]]
        if dry:
            argv.append("--dry-run")
            return execute(argv, env, log)
        receipt = {"version": VERSION, "host": platform.node(), "started": now(),
                   "status": "running", "pid": os.getpid(), "attempts": [], "log": str(log)}
        save(state / "receipt.json", receipt)
        for attempt in range(cfg["retries"] + 1):
            offset = log.stat().st_size if log.exists() else 0
            code = execute(argv, env, log)
            with log.open("rb") as f:
                f.seek(offset); output = f.read().decode(errors="replace")
            category = "success" if code == 0 else failure_class(output)
            receipt["attempts"].append({"exit_code": code, "classification": category, "at": now()})
            save(state / "receipt.json", receipt)
            if code == 0 or category != "transient" or attempt == cfg["retries"]:
                break
            # Only transient faults get a repeat; unknown outcomes are not blindly replayed.
            time.sleep(min(60 * (attempt + 1), 300))
        receipt.update(status="engine_completed" if code == 0 else "needs_attention", finished=now())
        try:
            receipt["reboot_required"] = reboot_required(log, env)
        except (OSError, subprocess.TimeoutExpired):
            receipt["reboot_required"] = "unknown"
        save(state / "receipt.json", receipt)
        # Reboot is deliberately separate from the updater so receipt survives.
        if code == 0 and cfg["restart_policy"] == "reboot" and receipt["reboot_required"] is True:
            receipt["reboot_exit_code"] = execute(reboot_command(), env, log)
            save(state / "receipt.json", receipt)
        return code


def reboot_command():
    if platform.system() == "Windows":
        return ["shutdown.exe", "/r", "/t", "60", "/d", "p:2:17"]
    if platform.system() == "Darwin":
        return ["sudo", "-n", "/sbin/shutdown", "-r", "+1"]
    return ["sudo", "-n", "systemctl", "reboot"]


def reboot_required(log, env):
    system = platform.system()
    if system == "Linux":
        if Path("/var/run/reboot-required").exists():
            return True
        probe = shutil.which("needs-restarting", path=env["PATH"])
        if probe:
            result = subprocess.run([probe, "-r"], capture_output=True, env=env, timeout=30)
            return True if result.returncode == 1 else (False if result.returncode == 0 else "unknown")
        return "unknown"
    if system == "Darwin":
        output = log.read_text(errors="replace").lower()
        return True if any(x in output for x in ("restart is required", "restart required", "reboot required", "must restart")) else "unknown"
    if system == "Windows":
        script = r"if ((Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending') -or (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired')) { exit 10 } else { exit 0 }"
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, env=env, timeout=30)
        return True if result.returncode == 10 else (False if result.returncode == 0 else "unknown")
    return "unknown"


def health(state):
    cfg = read(state / "config.json")
    receipt = read(state / "receipt.json") if (state / "receipt.json").exists() else {}
    timestamp = receipt.get("finished", receipt.get("started"))
    age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(timestamp)).total_seconds() if timestamp else None
    status = "never_run" if age is None else receipt.get("status")
    if age is not None and ((status == "running" and age > 4 * 3600) or age > 36 * 3600):
        status = "stale"
    report = {"host": platform.node(), "status": status, "age_seconds": age,
              "schedule": cfg["time"], "restart_policy": cfg["restart_policy"],
              "coverage": str(state / "coverage.md"), "receipt": receipt}
    save(state / "health.json", report)
    return report


def checked(argv):
    subprocess.run(argv, check=True, stdin=subprocess.DEVNULL)


def install_scheduler(state, cfg, remove=False):
    script = state / "ksu.py"
    commands = {"nightly": [sys.executable, str(script), "--state-dir", str(state), "run"],
                "watchdog": [sys.executable, str(script), "--state-dir", str(state), "watchdog"]}
    h, m = map(int, cfg["time"].split(":"))
    if platform.system() == "Darwin":
        root = Path.home() / "Library" / "LaunchAgents"
        root.mkdir(parents=True, exist_ok=True)
        for name, argv in commands.items():
            label = "org.ksu." + name
            path = root / (label + ".plist")
            subprocess.run(["launchctl", "bootout", "gui/" + str(os.getuid()), str(path)], capture_output=True)
            if remove:
                path.unlink(missing_ok=True); continue
            spec = {"Label": label, "ProgramArguments": argv,
                    "StandardOutPath": str(state / (name + ".out.log")),
                    "StandardErrorPath": str(state / (name + ".err.log"))}
            spec.update({"StartCalendarInterval": {"Hour": h, "Minute": m}} if name == "nightly" else {"StartInterval": 3600})
            path.write_bytes(plistlib.dumps(spec))
            checked(["plutil", "-lint", str(path)])
            checked(["launchctl", "bootstrap", "gui/" + str(os.getuid()), str(path)])
            checked(["launchctl", "print", "gui/" + str(os.getuid()) + "/" + label])
    elif platform.system() == "Linux":
        root = Path.home() / ".config" / "systemd" / "user"
        root.mkdir(parents=True, exist_ok=True)
        for name, argv in commands.items():
            label = "ksu-" + name
            if remove:
                checked(["systemctl", "--user", "disable", "--now", label + ".timer"])
                for ext in ("service", "timer"):
                    (root / (label + "." + ext)).unlink(missing_ok=True)
                continue
            # systemd has its own quoting and percent specifier rules.
            command = " ".join('"' + a.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$') + '"' for a in argv)
            (root / (label + ".service")).write_text('[Unit]\nDescription=KSU ' + name + '\n[Service]\nType=oneshot\nTimeoutStartSec=infinity\nExecStart=' + command + '\n')
            timing = f"OnCalendar=*-*-* {h:02}:{m:02}:00\nPersistent=true" if name == "nightly" else "OnBootSec=10min\nOnUnitActiveSec=1h"
            (root / (label + ".timer")).write_text('[Unit]\nDescription=KSU ' + name + '\n[Timer]\n' + timing + '\n[Install]\nWantedBy=timers.target\n')
        checked(["systemctl", "--user", "daemon-reload"])
        if not remove:
            for name in commands:
                checked(["systemctl", "--user", "enable", "--now", "ksu-" + name + ".timer"])
                checked(["systemctl", "--user", "is-active", "ksu-" + name + ".timer"])
            # Lingering is necessary for operation after logout; no silent sudoers edits.
            checked(["loginctl", "enable-linger", os.environ.get("USER", "")])
    elif platform.system() == "Windows":
        def psquote(s):
            return "'" + s.replace("'", "''") + "'"
        for name, argv in commands.items():
            label = "KSU-" + name
            if remove:
                code = f"Unregister-ScheduledTask -TaskName '{label}' -Confirm:$false -ErrorAction SilentlyContinue"
            else:
                args = subprocess.list2cmdline(argv[1:])
                trigger = f"New-ScheduledTaskTrigger -Daily -At '{cfg['time']}'" if name == "nightly" else "New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(10) -RepetitionInterval (New-TimeSpan -Hours 1)"
                code = f"$a=New-ScheduledTaskAction -Execute {psquote(argv[0])} -Argument {psquote(args)}; $t={trigger}; $s=New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero); Register-ScheduledTask -TaskName '{label}' -Action $a -Trigger $t -Settings $s -Force | Out-Null; Get-ScheduledTask -TaskName '{label}'"
            checked(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", "$ErrorActionPreference='Stop'; " + code])
    else:
        raise RuntimeError("No native scheduler adapter for " + platform.system())


def setup(args, state):
    if (state / "config.json").exists():
        raise RuntimeError("Already enrolled; edit config.json and use schedule to apply changes")
    engine = shutil.which(args.topgrade)
    if not engine:
        raise RuntimeError("Install Topgrade 17.9+ using its official platform instructions first")
    version = subprocess.run([engine, "--version"], capture_output=True, text=True, check=True, timeout=10).stdout.strip()
    import re
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", version)
    if not match or tuple(map(int, match.groups())) < (17, 9, 0):
        raise RuntimeError("Topgrade 17.9.0 or newer is required")
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        state.chmod(0o700)
    cfg = {"version": VERSION, "time": args.time, "restart_policy": args.restart_policy,
           "retries": 2, "topgrade": str(Path(engine).resolve()), "path": os.environ.get("PATH", ""),
           "only": [], "disable": [], "host": platform.node()}
    save(state / "config.json", cfg)
    shutil.copy2(__file__, state / "ksu.py")
    (state / "coverage.md").write_text("# KSU host coverage\n\nEnrollment pending: review preview.log with your agent. Record discovered managers, installed apps, unsupported software, privileges, exclusions, health probes, and restart limits here. Engine success alone does not prove every application is current.\n")
    print("Prepared " + str(state) + ". Run preview; review coverage; then schedule.")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--state-dir", type=Path, default=DEFAULT)
    sub = p.add_subparsers(dest="command", required=True)
    init = sub.add_parser("setup")
    init.add_argument("--time", default="03:00")
    init.add_argument("--restart-policy", choices=["services", "reboot", "defer"], default="services")
    init.add_argument("--topgrade", default="topgrade")
    for name in ["preview", "run", "status", "watchdog", "schedule", "unschedule"]:
        sub.add_parser(name)
    args = p.parse_args(); state = args.state_dir.expanduser().resolve()
    if args.command == "setup":
        dt.datetime.strptime(args.time, "%H:%M")
        setup(args, state); return 0
    if args.command in ("run", "preview"):
        return run(state, args.command == "preview")
    if args.command in ("status", "watchdog"):
        report = health(state)
        if args.command == "status":
            print(json.dumps(report, indent=2))
        return 0 if report["status"] == "engine_completed" else 1
    cfg = read(state / "config.json")
    if args.command == "schedule" and (not cfg.get("coverage_reviewed") or not cfg.get("only")):
        raise RuntimeError("Review coverage and enroll explicit Topgrade steps before scheduling")
    install_scheduler(state, cfg, args.command == "unschedule")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, ValueError, OSError, subprocess.CalledProcessError) as exc:
        print("KSU: " + str(exc), file=sys.stderr)
        sys.exit(1)
