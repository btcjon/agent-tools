import hashlib
import stat
import sys
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fcntl
import hermes_registration
from hermes_registration import RegistrationError, install, replace_command


OLD = "/old/skill-advisor-mcp"
NEW = "/new/skill-advisor-mcp"
CONFIG = b"""other: keep
mcp_servers:
  notion:
    url: https://mcp.notion.com/mcp
  jev-skill-advisor:
    command: /old/skill-advisor-mcp
    args:
      - --release-root
      - /release
    enabled: true
  elsewhere:
    command: /bin/true
"""
RICH = b"""# note /old/skill-advisor-mcp stays
other: keep
quoted: "/old/skill-advisor-mcp"
mcp_servers:
  notion:
    url: https://mcp.notion.com/mcp
  jev-skill-advisor:
    command: /old/skill-advisor-mcp
    args:
      - --release-root
      - /release
    enabled: true
  elsewhere:
    command: /bin/true
trail: /old/skill-advisor-mcp
"""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class HermesRegistrationTests(unittest.TestCase):
    def test_replaces_one_line_and_preserves_every_other_byte(self):
        changed = replace_command(CONFIG, OLD, NEW)
        self.assertEqual(changed, CONFIG.replace(OLD.encode(), NEW.encode()))
        with TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.yaml"
            config.write_bytes(CONFIG)
            before = _sha(CONFIG)
            result = install(config, OLD, NEW, before)
            self.assertEqual(result["status"], "installed")
            self.assertEqual(config.read_bytes(), changed)
            with self.assertRaisesRegex(RegistrationError, "config_changed"):
                install(config, OLD, NEW, before)
            install(config, NEW, OLD, result["after_sha256"])
            self.assertEqual(config.read_bytes(), CONFIG)

    def test_unmanaged_or_extra_section_is_refused(self):
        with self.assertRaisesRegex(RegistrationError, "current_mismatch"):
            replace_command(CONFIG, "/wrong", NEW)
        with self.assertRaisesRegex(RegistrationError, "section_invalid"):
            replace_command(CONFIG + CONFIG, OLD, NEW)

    def test_same_hash_install_is_idempotent(self):
        with TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.yaml"
            config.write_bytes(CONFIG)
            config.chmod(0o640)
            inode = config.stat().st_ino
            sha = _sha(CONFIG)
            first = install(config, OLD, OLD, sha)
            second = install(config, OLD, OLD, sha)
            self.assertEqual(first["status"], "unchanged")
            self.assertEqual(second["status"], "unchanged")
            self.assertEqual(first["before_sha256"], sha)
            self.assertEqual(first["after_sha256"], sha)
            self.assertEqual(second["before_sha256"], sha)
            self.assertEqual(second["after_sha256"], sha)
            self.assertEqual(config.read_bytes(), CONFIG)
            self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o640)
            self.assertEqual(config.stat().st_ino, inode)
            lock = config.with_name(config.name + ".jev-registration.lock")
            self.assertTrue(lock.is_file())
            self.assertFalse(lock.is_symlink())
            self.assertEqual(stat.S_IMODE(lock.stat().st_mode), 0o600)

    def test_stale_expected_hash_refuses_without_writing(self):
        with TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.yaml"
            config.write_bytes(CONFIG)
            config.chmod(0o640)
            inode = config.stat().st_ino
            with self.assertRaisesRegex(RegistrationError, "config_changed"):
                install(config, OLD, NEW, "f" * 64)
            self.assertEqual(config.read_bytes(), CONFIG)
            self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o640)
            self.assertEqual(config.stat().st_ino, inode)
            self.assertFalse(any(path.name.startswith(".jev-mcp-") for path in Path(tmp).iterdir()))

    def test_reread_refuses_snapshot_changed_before_replace(self):
        drift = CONFIG.replace(b"other: keep", b"other: drift")
        with TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.yaml"
            config.write_bytes(CONFIG)
            config.chmod(0o640)
            real = hermes_registration._read_snapshot
            calls = {"n": 0}

            def wrapped(path):
                calls["n"] += 1
                snapshot = real(path)
                if calls["n"] == 1:
                    path.write_bytes(drift)
                    return snapshot
                return snapshot

            hermes_registration._read_snapshot = wrapped
            try:
                with self.assertRaisesRegex(RegistrationError, "config_changed"):
                    install(config, OLD, NEW, _sha(CONFIG))
            finally:
                hermes_registration._read_snapshot = real
            self.assertEqual(config.read_bytes(), drift)
            self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o640)
            self.assertNotIn(NEW.encode(), config.read_bytes())
            self.assertFalse(any(path.name.startswith(".jev-mcp-") for path in Path(tmp).iterdir()))

    def test_cooperating_installers_commit_one_change(self):
        with TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.yaml"
            config.write_bytes(CONFIG)
            config.chmod(0o640)
            sha = _sha(CONFIG)
            original = fcntl.flock
            state = threading.Lock()
            calls = {"n": 0}
            second_waiting = threading.Event()
            results = []
            errors = []
            collected = threading.Lock()

            def wrapped(fd, operation):
                if operation != fcntl.LOCK_EX:
                    return original(fd, operation)
                with state:
                    calls["n"] += 1
                    turn = calls["n"]
                if turn == 1:
                    original(fd, operation)
                    if not second_waiting.wait(10):
                        raise TimeoutError("second installer did not reach the lock")
                    return None
                if turn == 2:
                    second_waiting.set()
                return original(fd, operation)

            def worker(command):
                try:
                    result = install(config, OLD, command, sha)
                except Exception as exc:
                    with collected:
                        errors.append(exc)
                else:
                    with collected:
                        results.append(result)

            fcntl.flock = wrapped
            threads = [
                threading.Thread(target=worker, args=("/new/skill-advisor-a",), daemon=True),
                threading.Thread(target=worker, args=("/new/skill-advisor-b",), daemon=True),
            ]
            try:
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(10)
                    self.assertFalse(thread.is_alive())
            finally:
                fcntl.flock = original
            self.assertEqual(len(results), 1)
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], RegistrationError)
            self.assertEqual(str(errors[0]), "config_changed")
            final = config.read_bytes()
            winners = [
                CONFIG.replace(OLD.encode(), b"/new/skill-advisor-a"),
                CONFIG.replace(OLD.encode(), b"/new/skill-advisor-b"),
            ]
            self.assertIn(final, winners)
            self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o640)
            self.assertEqual(results[0]["before_sha256"], sha)
            self.assertEqual(results[0]["after_sha256"], _sha(final))
            self.assertEqual(final.count(b"\n  notion:\n"), 1)
            self.assertIn(b"    url: https://mcp.notion.com/mcp\n", final)
            self.assertIn(b"  elsewhere:\n    command: /bin/true\n", final)
            self.assertFalse(any(path.name.startswith(".jev-mcp-") for path in Path(tmp).iterdir()))

    def test_preserves_unrelated_copies_of_the_command_text(self):
        changed = replace_command(RICH, OLD, NEW)
        self.assertEqual(RICH.count(OLD.encode()) - 1, changed.count(OLD.encode()))
        self.assertIn(b"# note /old/skill-advisor-mcp stays\n", changed)
        self.assertIn(b'quoted: "/old/skill-advisor-mcp"\n', changed)
        self.assertIn(b"trail: /old/skill-advisor-mcp\n", changed)
        self.assertIn(b"  notion:\n    url: https://mcp.notion.com/mcp\n", changed)
        self.assertIn(b"  elsewhere:\n    command: /bin/true\n", changed)
        with TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.yaml"
            config.write_bytes(RICH)
            config.chmod(0o604)
            result = install(config, OLD, NEW, _sha(RICH))
            self.assertEqual(result["status"], "installed")
            self.assertEqual(config.read_bytes(), changed)
            self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o604)

    def test_ambiguous_command_line_is_refused(self):
        ambiguous = CONFIG.replace(
            b"    enabled: true\n",
            b"    command: /old/skill-advisor-mcp\n    enabled: true\n",
            1,
        )
        with TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.yaml"
            config.write_bytes(ambiguous)
            inode = config.stat().st_ino
            with self.assertRaisesRegex(RegistrationError, "command_line_invalid"):
                install(config, OLD, NEW, _sha(ambiguous))
            self.assertEqual(config.read_bytes(), ambiguous)
            self.assertEqual(config.stat().st_ino, inode)

    def test_symlink_lock_is_refused(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.yaml"
            config.write_bytes(CONFIG)
            target = root / "other-lock"
            target.write_bytes(b"unlocked")
            target.chmod(0o644)
            lock = root / "config.yaml.jev-registration.lock"
            lock.symlink_to(target)
            with self.assertRaisesRegex(RegistrationError, "lock_invalid"):
                install(config, OLD, NEW, _sha(CONFIG))
            self.assertEqual(config.read_bytes(), CONFIG)
            self.assertEqual(target.read_bytes(), b"unlocked")
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)
            self.assertTrue(lock.is_symlink())

    def test_hardlinked_lock_is_refused_without_chmod(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.yaml"
            config.write_bytes(CONFIG)
            target = root / "other-lock"
            target.write_bytes(b"unlocked")
            target.chmod(0o644)
            lock = root / "config.yaml.jev-registration.lock"
            lock.hardlink_to(target)
            with self.assertRaisesRegex(RegistrationError, "lock_invalid"):
                install(config, OLD, NEW, _sha(CONFIG))
            self.assertEqual(config.read_bytes(), CONFIG)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)


if __name__ == "__main__":
    unittest.main()
