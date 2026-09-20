#!/usr/bin/env python3
"""Unit tests for the package-local scripts/claims.py (temp state only)."""
from __future__ import annotations

import concurrent.futures
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

CLAIMS_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "claims.py"
)


def load_claims():
    spec = importlib.util.spec_from_file_location("claims_under_test", CLAIMS_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ClaimsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mod = load_claims()
        self.tmp = tempfile.TemporaryDirectory()
        self.state_dir = self.tmp.name

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_cli(self, argv: list[str]) -> tuple[int, dict]:
        try:
            self.mod.main(argv)
        except SystemExit as e:
            code = int(e.code or 0)
        else:
            code = 0
        # main always emits via emit() which SystemExits; capture via subprocess-like redirect
        return code, {}

    def invoke(self, argv: list[str]) -> tuple[int, dict]:
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        code = 0
        with redirect_stdout(buf):
            try:
                self.mod.main(argv)
            except SystemExit as e:
                code = int(e.code or 0)
        line = buf.getvalue().strip().splitlines()[-1]
        return code, json.loads(line)

    def test_claim_status_release_happy_path(self) -> None:
        code, payload = self.invoke(
            [
                "claim",
                "--state-dir",
                self.state_dir,
                "--session",
                "s1",
                "--worker",
                "w1",
                "--owner",
                "owner-a",
            ]
        )
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["created"])

        code, payload = self.invoke(
            ["status", "--state-dir", self.state_dir, "--session", "s1", "--worker", "w1"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "claimed")
        self.assertEqual(payload["owner"], "owner-a")

        code, payload = self.invoke(
            [
                "release",
                "--state-dir",
                self.state_dir,
                "--session",
                "s1",
                "--worker",
                "w1",
                "--owner",
                "owner-a",
            ]
        )
        self.assertEqual(code, 0)
        self.assertTrue(payload["released"])

    def test_same_owner_idempotent(self) -> None:
        args = [
            "claim",
            "--state-dir",
            self.state_dir,
            "--session",
            "s1",
            "--worker",
            "w1",
            "--owner",
            "owner-a",
        ]
        self.invoke(args)
        code, payload = self.invoke(args)
        self.assertEqual(code, 0)
        self.assertFalse(payload["created"])

    def test_foreign_owner_conflict(self) -> None:
        self.invoke(
            [
                "claim",
                "--state-dir",
                self.state_dir,
                "--session",
                "s1",
                "--worker",
                "w1",
                "--owner",
                "owner-a",
            ]
        )
        code, payload = self.invoke(
            [
                "claim",
                "--state-dir",
                self.state_dir,
                "--session",
                "s1",
                "--worker",
                "w1",
                "--owner",
                "owner-b",
            ]
        )
        self.assertEqual(code, 3)
        self.assertEqual(payload["error"], "conflict")

    def test_release_owner_mismatch(self) -> None:
        self.invoke(
            [
                "claim",
                "--state-dir",
                self.state_dir,
                "--session",
                "s1",
                "--worker",
                "w1",
                "--owner",
                "owner-a",
            ]
        )
        code, payload = self.invoke(
            [
                "release",
                "--state-dir",
                self.state_dir,
                "--session",
                "s1",
                "--worker",
                "w1",
                "--owner",
                "owner-b",
            ]
        )
        self.assertEqual(code, 4)
        self.assertEqual(payload["error"], "owner_mismatch")

    def test_corrupt_state_fail_closed(self) -> None:
        state_path = Path(self.state_dir) / "state.json"
        state_path.write_text("{not-json", encoding="utf-8")
        code, payload = self.invoke(
            [
                "claim",
                "--state-dir",
                self.state_dir,
                "--session",
                "s1",
                "--worker",
                "w1",
                "--owner",
                "owner-a",
            ]
        )
        self.assertEqual(code, 2)
        self.assertEqual(payload["error"], "corrupt_state")

    def test_separate_session_namespaces(self) -> None:
        self.invoke(
            [
                "claim",
                "--state-dir",
                self.state_dir,
                "--session",
                "s1",
                "--worker",
                "w1",
                "--owner",
                "owner-a",
            ]
        )
        code, payload = self.invoke(
            [
                "claim",
                "--state-dir",
                self.state_dir,
                "--session",
                "s2",
                "--worker",
                "w1",
                "--owner",
                "owner-b",
            ]
        )
        self.assertEqual(code, 0)
        self.assertTrue(payload["created"])

    def test_separate_state_dirs(self) -> None:
        other = tempfile.TemporaryDirectory()
        try:
            self.invoke(
                [
                    "claim",
                    "--state-dir",
                    self.state_dir,
                    "--session",
                    "s1",
                    "--worker",
                    "w1",
                    "--owner",
                    "owner-a",
                ]
            )
            code, payload = self.invoke(
                [
                    "claim",
                    "--state-dir",
                    other.name,
                    "--session",
                    "s1",
                    "--worker",
                    "w1",
                    "--owner",
                    "owner-b",
                ]
            )
            self.assertEqual(code, 0)
            self.assertTrue(payload["created"])
        finally:
            other.cleanup()

    def test_concurrent_claim_one_winner(self) -> None:
        import subprocess

        def attempt(owner: str) -> tuple[int, dict]:
            proc = subprocess.run(
                [
                    "python3",
                    str(CLAIMS_PATH),
                    "claim",
                    "--state-dir",
                    self.state_dir,
                    "--session",
                    "s1",
                    "--worker",
                    "w1",
                    "--owner",
                    owner,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            payload = json.loads(proc.stdout.strip().splitlines()[-1])
            return proc.returncode, payload

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            futs = [ex.submit(attempt, f"owner-{i}") for i in range(8)]
            results = [f.result() for f in futs]

        wins = [p for c, p in results if c == 0 and p.get("created") is True]
        conflicts = [p for c, p in results if c == 3]
        idempotent = [p for c, p in results if c == 0 and p.get("created") is False]
        self.assertEqual(len(wins), 1)
        self.assertEqual(len(wins) + len(conflicts) + len(idempotent), 8)

        code, status = self.invoke(
            ["status", "--state-dir", self.state_dir, "--session", "s1", "--worker", "w1"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(status["status"], "claimed")
        self.assertEqual(status["owner"], wins[0]["owner"])

    def test_no_auto_expiry_api(self) -> None:
        # Helper exposes only claim/status/release — no expire/steal actions.
        src = CLAIMS_PATH.read_text(encoding="utf-8")
        self.assertNotIn("force-steal", src)
        self.assertNotIn("expire", src.lower().split("choices=")[0])  # weak
        self.assertIn('choices=["claim", "status", "release"]', src)


if __name__ == "__main__":
    unittest.main()
