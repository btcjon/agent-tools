#!/usr/bin/env python3
"""KSU: host-local discovery, selected updates, and nightly maintenance. Python 3.9+, no dependencies."""
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

VERSION = "0.2.0"
DEFAULT = Path.home() / ".local" / "state" / "ksu"


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
    if any(x in text for x in ("could not get lock", "unable to acquire", "another process", "timed out", "temporary failure", "connection reset", "429 too many", "503 service unavailable", "http 429", "http 503", "network is unreachable", "could not resolve")):
        return "transient"
    return "needs_diagnosis"


def run(state, dry=False):
    """Fresh discovery, exact saved targets, independent failure receipts, version readback."""
    import ksu_inventory as inventory
    cfg = read(state / "config.json")
    env = environment(state, cfg)
    with lock(state):
        if not dry and (state / "receipt.json").exists() and read(state / "receipt.json").get("status") == "running":
            raise RuntimeError("Previous transaction outcome is unknown; inspect it before another run")
        if not (state / "selections.json").exists():
            raise RuntimeError("Run discover and select first; legacy broad Topgrade steps do not authorize item updates")
        current = inventory.scan(state, probe=inventory.Probe(path=cfg["path"]))
        planned = inventory.plan(state, current, cfg["restart_policy"])
        if dry:
            print(json.dumps(planned, indent=2)); return 1 if planned["blocked"] else 0
        if not planned["actions"] and not planned["blocked"]:
            raise RuntimeError("No selected update targets")
        log = state / "latest.log"
        if log.exists(): os.replace(log, state / "latest.log.previous")
        receipt = dict(version=VERSION, host=platform.node(), started=now(), status="running", pid=os.getpid(),
                       items=[], blocked=planned["blocked"], new_items=planned["new_items"], log=str(log))
        save(state / "receipt.json", receipt)
        refreshed = {}
        def attempt(argv):
            outcomes = []
            for n in range(min(max(cfg.get("retries", 2), 0), 2) + 1):
                offset = log.stat().st_size if log.exists() else 0
                command_env = env.copy()
                if Path(argv[0]).is_absolute():
                    command_env['PATH'] = str(state/'bin') + os.pathsep + str(Path(argv[0]).parent) + os.pathsep + env['PATH']
                code = execute(argv, command_env, log)
                with log.open("rb") as f: f.seek(offset); output = f.read().decode(errors="replace")
                category = "success" if code == 0 else failure_class(output)
                outcomes.append(dict(exit_code=code, classification=category))
                if code == 0 or category != "transient" or n >= min(cfg.get("retries", 2), 2): break
                time.sleep(60 * (n + 1))
            return code, outcomes
        for target in planned["actions"]:
            result = dict(id=target["id"], name=target["name"], before=target["before"], attempts=[])
            # Recheck a source checkout immediately before mutation. Never stash or reset it.
            original = next(r for r in current['items'] if r['id'] == target['id'])
            if original['kind'] == 'skill_repo':
                git = inventory.Probe(path=cfg['path']).git(original['location'])
                if not git or git['dirty'] or git['branch'] != original['git']['branch'] or git['upstream'] != original['git']['upstream'] or git['origin'] != original['git']['origin']:
                    result.update(status='blocked', reason='Repository changed since discovery')
                    receipt['items'].append(result); save(state/'receipt.json',receipt); continue
            code = 0
            for command in target["refresh"]:
                key = tuple(command)
                if key not in refreshed: refreshed[key] = attempt(command)
                code, outcomes = refreshed[key]
                if code:
                    result["attempts"] = outcomes; result["reason"] = "Metadata refresh failed"; break
            if code == 0: code, result["attempts"] = attempt(target["argv"])
            result["status"] = "command_completed" if code == 0 else "needs_attention"
            receipt["items"].append(result); save(state / "receipt.json", receipt)
        after = inventory.scan(state, probe=inventory.Probe(path=cfg["path"]))
        indexed = {r['id']: r for r in after['items']}
        for result in receipt['items']:
            row = indexed.get(result['id'])
            result['after'] = row['version'] if row else None
            if result['status'] == 'command_completed':
                result['status'] = 'command_completed_and_present' if row and not row['blocked'] else 'verification_incomplete'
        okay = not receipt['blocked'] and all(r['status'] == 'command_completed_and_present' for r in receipt['items'])
        receipt.update(status='engine_completed' if okay else 'needs_attention', finished=now(), discovery_errors=after['errors'])
        try: receipt['reboot_required'] = reboot_required(log, env) if log.exists() else 'unknown'
        except (OSError, subprocess.TimeoutExpired): receipt['reboot_required'] = 'unknown'
        save(state/'receipt.json',receipt)
        if okay and cfg['restart_policy'] == 'reboot' and receipt['reboot_required'] is True:
            receipt['reboot_exit_code'] = execute(reboot_command(),env,log); save(state/'receipt.json',receipt)
        return 0 if okay else 1


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
              "coverage": str(state / "inventory.html"), "receipt": receipt}
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


def install_runtime(state):
    """Stage both runtime modules; replace the entrypoint last while holding the run lock."""
    with lock(state):
        receipt = read(state/'receipt.json') if (state/'receipt.json').exists() else {}
        if receipt.get('status') == 'running': raise RuntimeError('Inspect the unfinished update before replacing its runner')
        staged = []
        for name in ['ksu_inventory.py', 'ksu.py']:
            source = Path(__file__).parent/name
            if source.resolve() == (state/name).resolve(): continue
            target = state/(name+'.new')
            shutil.copy2(source,target); staged.append((target,state/name))
        for temp,target in staged: os.replace(temp,target)


def setup(args, state):
    if (state / "config.json").exists():
        raise RuntimeError("Already enrolled; edit config.json and use schedule to apply changes")
    engine = shutil.which(args.topgrade)
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        state.chmod(0o700)
    cfg = {"version": VERSION, "time": args.time, "restart_policy": args.restart_policy,
           "retries": 2, "topgrade": str(Path(engine).resolve()) if engine else None, "path": os.environ.get("PATH", ""),
           "only": [], "disable": [], "host": platform.node()}
    save(state / "config.json", cfg)
    install_runtime(state)
    print("Prepared " + str(state) + ". Run discover, review inventory.html, and save selections before scheduling.")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--state-dir", type=Path, default=DEFAULT)
    sub = p.add_subparsers(dest="command", required=True)
    init = sub.add_parser("setup")
    init.add_argument("--time", default="03:00")
    init.add_argument("--restart-policy", choices=["services", "reboot", "defer"], default="services")
    init.add_argument("--topgrade", default="topgrade")
    discover = sub.add_parser("discover")
    discover.add_argument("--root", action="append", help="Additional skill root; saved for future scans")
    select = sub.add_parser("select")
    select.add_argument("--file", type=Path, help="Import the checkbox page's exported JSON")
    select.add_argument("--enable", nargs="*", default=[])
    select.add_argument("--exclude", nargs="*", default=[])
    select.add_argument("--interactive", action="store_true", help="Review supported items in a terminal")
    for name in ["preview", "run", "status", "watchdog", "schedule", "unschedule", "install-runtime"]:
        sub.add_parser(name)
    args = p.parse_args(); state = args.state_dir.expanduser().resolve()
    if args.command == "setup":
        dt.datetime.strptime(args.time, "%H:%M")
        setup(args, state); return 0
    if args.command == "install-runtime":
        install_runtime(state); print("Updated host-local runner; selections and schedule preserved."); return 0
    if args.command == "discover":
        import ksu_inventory as inventory
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        with lock(state):
            result = inventory.scan(state, roots=args.root)
        print(json.dumps({"items": len(result["items"]), "probe_errors": result["errors"], "review": str(state / "inventory.html")}, indent=2))
        return 0
    if args.command == "select":
        import ksu_inventory as inventory
        with lock(state):
            if args.interactive:
                if not sys.stdin.isatty(): raise ValueError("Interactive selection needs a terminal; use --file or explicit IDs")
                data = read(state / "inventory.json")
                enabled, excluded = [], []
                for row in data["items"]:
                    if row["blocked"]: continue
                    answer = input(row['category'] + ' | ' + row['name'] + ' | ' + row['location'] + ' [y/n/Enter=keep/q=cancel]: ').lower()
                    if answer == 'q': return 0
                    if answer == 'y': enabled.append(row['id'])
                    elif answer == 'n': excluded.append(row['id'])
                choices = inventory.choose(state, enabled, excluded)
            else:
                choices = inventory.choose(state, args.enable, args.exclude, read(args.file) if args.file else None)
        print("Saved " + str(sum(c['enabled'] for c in choices['choices'].values())) + " selected targets; nothing was updated.")
        return 0
    if args.command in ("run", "preview"):
        return run(state, args.command == "preview")
    if args.command in ("status", "watchdog"):
        report = health(state)
        if args.command == "status":
            print(json.dumps(report, indent=2))
        return 0 if report["status"] == "engine_completed" else 1
    cfg = read(state / "config.json")
    if args.command == "schedule":
        import ksu_inventory as inventory
        selection = inventory.plan(state, restart_policy=cfg["restart_policy"])
        if not selection["actions"] or selection["blocked"]:
            raise RuntimeError("Save supported selections and resolve blocked selected targets before scheduling")
    install_scheduler(state, cfg, args.command == "unschedule")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, ValueError, OSError, subprocess.CalledProcessError) as exc:
        print("KSU: " + str(exc), file=sys.stderr)
        sys.exit(1)
