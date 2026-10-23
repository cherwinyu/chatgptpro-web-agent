"""Saved-receipt parsing and no-mutation recovery regressions."""
import argparse
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/pro_bridge"))
import cli
import receipts

SAVED = {"task_id": "11111111-1111-4111-8111-111111111111", "request_sha256": "a"*64,
         "source_repository": "example/repo", "baseline_sha": "b"*40,
         "branch": "test/receipt", "ledger_id": "c"*64}


class ReceiptParsingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "原始 回执.json"

    def save(self, value, encoding="utf-8"):
        self.path.write_text(json.dumps(value, ensure_ascii=False), encoding=encoding)
        return self.path

    def test_bare_and_full_prepare_envelope(self):
        for value in (SAVED, SAVED | {"receipt": SAVED, "resolved_ledger_path": "/not/used/ledger.json"},
                      {"receipt": SAVED, "resolved_ledger_path": "/not/used/ledger.json"}):
            self.assertEqual(receipts.load(self.save(value)), SAVED)

    def test_bom_supported(self):
        self.assertEqual(receipts.load(self.save(SAVED, "utf-8-sig")), SAVED)

    def test_reject_error_response_even_with_complete_receipt(self):
        self.save({"receipt": SAVED, "error": "receipt mismatch: ledger_id"})
        with self.assertRaisesRegex(receipts.ReceiptInputError, "error response"):
            receipts.load(self.path)

    def test_reject_missing_and_wrong_types(self):
        for value in ([], {k: v for k, v in SAVED.items() if k != "ledger_id"}, SAVED | {"ledger_id": 2},
                      SAVED | {"ledger_id": ""}, SAVED | {"branch": "line\nbreak"}):
            self.save(value)
            with self.assertRaises(receipts.ReceiptInputError):
                receipts.load(self.path)

    def test_duplicate_keys_rejected(self):
        self.path.write_text('{"receipt": {}, "receipt": {}}', encoding="utf-8")
        with self.assertRaisesRegex(receipts.ReceiptInputError, "duplicate"):
            receipts.load(self.path)

    def test_truncated_invalid_encoding_and_oversized(self):
        for raw in (b'{"receipt":', b'\xff', b' '*(receipts.MAX_BYTES+1)):
            self.path.write_bytes(raw)
            with self.assertRaises(receipts.ReceiptInputError):
                receipts.load(self.path)

    def test_envelope_conflicts_are_structured(self):
        self.save(SAVED | {"receipt": SAVED, "branch": "test/wrong"})
        with self.assertRaises(receipts.ReceiptInputError) as caught:
            receipts.load(self.path)
        self.assertEqual(caught.exception.mismatches[0], {"field": "branch", "provided": "test/wrong",
                         "actual": "test/receipt", "actual_source": "nested_receipt"})

    def test_file_fills_task_and_forces_full_identity(self):
        self.save(SAVED)
        args = cli.parser().parse_args(["resume", "--receipt", str(self.path)])
        receipts.apply_file(args)
        self.assertEqual(args.task, SAVED["task_id"])
        self.assertTrue(args.cross_workspace)
        self.assertEqual(args.expect_ledger_id, SAVED["ledger_id"])

    def test_equal_legacy_inputs_allowed(self):
        self.save(SAVED)
        args = cli.parser().parse_args(["resume", "--receipt", str(self.path), "--task", SAVED["task_id"],
                                       "--expect-branch", SAVED["branch"]])
        receipts.apply_file(args)
        self.assertEqual(args.expect_branch, SAVED["branch"])

    def test_all_conflicts_refused_before_opening_ledger(self):
        self.save(SAVED)
        output = io.StringIO()
        with patch.object(cli, "open_ledger", side_effect=AssertionError("must not open")), redirect_stdout(output):
            code = cli.main(["resume", "--ledger", str(self.root / "absent"), "--receipt", str(self.path),
                             "--task", "wrong", "--expect-branch", "test/wrong", "--expect-ledger-id", ""])
        body = json.loads(output.getvalue())
        self.assertEqual(code, 3)
        self.assertEqual([m["field"] for m in body["receipt_mismatches"]], ["task_id", "branch", "ledger_id"])
        self.assertFalse(body["ledger_mutated"])
        self.assertFalse((self.root / "absent").exists())
        self.assertNotIn("receipt", body)

    def test_directory_hash_located_without_changing_algorithm(self):
        record = dict(SAVED)
        args = argparse.Namespace(receipt=str(self.path), task=SAVED["task_id"],
                expect_request_sha256="WRONG", expect_repository=SAVED["source_repository"],
                expect_baseline=SAVED["baseline_sha"], expect_branch=SAVED["branch"],
                expect_ledger_id=cli.ledger_id_for(self.root))
        actual = cli.ledger_id_for(self.root / "ledger.json")
        differences = receipts.mismatches(record, args, actual)
        self.assertEqual([d["field"] for d in differences], ["request_sha256", "ledger_id"])
        self.assertEqual(differences[-1]["actual"], actual)
        self.assertIn("not_directory", differences[-1]["identity_input"])


@unittest.skipUnless(os.name == "nt", "real CLI ledger tests require Windows mutex")
class ReceiptCLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="receipt-audit-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ledger = self.root / "账本 有空格"
        code, self.prepared = self.run_cli("prepare", "--task-type", "RESEARCH", "--repository", "example/repo",
                           "--source-sha", "a"*40, "--source-ref", "main", "--orchestration", "receipt-offline")
        self.assertEqual(code, 0, self.prepared)
        self.receipt = self.root / "prepare.json"
        self.receipt.write_text(json.dumps(self.prepared), encoding="utf-8-sig")
        self.file = self.ledger / "ledger.json"

    def run_cli(self, command, *args, ledger=None):
        env = dict(os.environ)
        env.pop("PRO_BRIDGE_LOCAL_SMOKE", None)
        env.pop("PRO_BRIDGE_TRUSTED_MANIFEST_SHA256", None)
        completed = subprocess.run([sys.executable, "-B", str(ROOT / "adapters/generic/run_bridge.py"), command,
                                    "--ledger", str(ledger or self.ledger), *args], env=env,
                                   capture_output=True, text=True, encoding="utf-8", timeout=30)
        return completed.returncode, json.loads(completed.stdout)

    def test_original_prepare_output_recovers_all_three_commands(self):
        before = self.file.read_bytes()
        for command, expected in (("status", 0), ("resume", 0), ("report", 2)):
            code, result = self.run_cli(command, "--receipt", str(self.receipt))
            self.assertEqual(code, expected, result)
            self.assertEqual(result["receipt"], self.prepared["receipt"])
            self.assertEqual(result["resolved_ledger_directory"], str(self.ledger.resolve()))
            self.assertEqual(self.file.read_bytes(), before)

    def test_wrong_ledger_and_directory_hash_no_mutation(self):
        wrong = self.root / "other"
        shutil.copytree(self.ledger, wrong)
        before = self.file.read_bytes()
        code, result = self.run_cli("resume", "--receipt", str(self.receipt), ledger=wrong)
        self.assertEqual(code, 3, result)
        self.assertEqual(result["receipt_mismatches"][0]["field"], "ledger_id")
        self.assertEqual((wrong / "ledger.json").read_bytes(), before)
        value = self.prepared["receipt"] | {"ledger_id": cli.ledger_id_for(self.ledger)}
        self.receipt.write_text(json.dumps(value), encoding="utf-8")
        code, result = self.run_cli("resume", "--receipt", str(self.receipt))
        self.assertEqual(code, 3, result)
        self.assertEqual(result["receipt_mismatches"][0]["actual"], self.prepared["ledger_id"])
        self.assertEqual(self.file.read_bytes(), before)
        self.assertNotIn("receipt", result)

    def test_multiple_ledger_mismatches_and_verify_not_called(self):
        value = self.prepared["receipt"] | {"request_sha256": "b"*64, "source_repository": "wrong/repo",
                                           "baseline_sha": "c"*40, "branch": "test/wrong", "ledger_id": "d"*64}
        self.receipt.write_text(json.dumps(value), encoding="utf-8")
        before = self.file.read_bytes()
        code, result = self.run_cli("resume", "--verify", "--receipt", str(self.receipt))
        self.assertEqual(code, 3, result)
        self.assertEqual(len(result["receipt_mismatches"]), 5)
        self.assertFalse(result["verification_attempt_succeeded"])
        self.assertEqual(self.file.read_bytes(), before)

    def test_blocked_and_unknown_remain_the_same_task(self):
        self.run_cli("block", "--task", self.prepared["task_id"], "--reason", "synthetic block")
        before = self.file.read_bytes()
        code, result = self.run_cli("resume", "--receipt", str(self.receipt))
        self.assertEqual(code, 0, result)
        self.assertEqual(result["state"], "BLOCKED")
        self.assertEqual(result["send_attempts"], 0)
        self.assertEqual(self.file.read_bytes(), before)
        code, other = self.run_cli("prepare", "--task-type", "RESEARCH", "--repository", "example/repo",
                                  "--source-sha", "a"*40, "--source-ref", "main", "--orchestration", "unknown-offline")
        self.assertEqual(code, 0)
        self.run_cli("claim", "--task", other["task_id"])
        self.receipt.write_text(json.dumps(other["receipt"]), encoding="utf-8")
        before = self.file.read_bytes()
        code, result = self.run_cli("resume", "--receipt", str(self.receipt))
        self.assertEqual(result["state"], "SUBMISSION_UNKNOWN")
        self.assertEqual(result["send_attempts"], 1)
        self.assertEqual(self.file.read_bytes(), before)
        self.assertEqual(len(json.loads(before)["tasks"]), 2)

    def test_missing_task_diagnostic_no_replacement(self):
        value = self.prepared["receipt"] | {"task_id": "22222222-2222-4222-8222-222222222222"}
        self.receipt.write_text(json.dumps(value), encoding="utf-8")
        before = self.file.read_bytes()
        code, result = self.run_cli("resume", "--receipt", str(self.receipt))
        self.assertEqual(code, 3, result)
        self.assertEqual(result["error"], "task_not_found")
        self.assertEqual(result["receipt_mismatches"][0]["field"], "task_id")
        self.assertFalse(result["replacement_prepared"])
        self.assertEqual(self.file.read_bytes(), before)


class RunBridgeGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="run-bridge-guard-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = dict(os.environ)
        for name in ("PRO_BRIDGE_LEDGER", "PRO_BRIDGE_PROTECTED_LEDGER",
                     "PRO_BRIDGE_DEPLOYMENT_LOCK", "PRO_BRIDGE_DEPLOYMENT_LOCK_SHA256"):
            self.env.pop(name, None)

    def assert_refused(self, *args, keyword):
        completed = subprocess.run([sys.executable, "-B", str(ROOT / "adapters/generic/run_bridge.py"),
                                    *args], env=self.env, capture_output=True, text=True,
                                   encoding="utf-8", timeout=30)
        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertIn(keyword, completed.stderr)

    def test_no_arguments_shows_usage(self):
        self.assert_refused(keyword="usage")

    def test_duplicate_ledger_refused(self):
        self.assert_refused("status", "--ledger", str(self.root / "first"),
                            "--ledger", str(self.root / "second"), keyword="only once")

    def test_demo_refuses_explicit_ledger(self):
        self.assert_refused("demo", "--ledger", str(self.root), keyword="demo refuses --ledger")

    def test_inspect_requires_explicit_ledger_despite_environment(self):
        self.env["PRO_BRIDGE_LEDGER"] = str(self.root)
        self.assert_refused("inspect", keyword="explicit --ledger")

    def test_stateful_command_requires_ledger(self):
        self.assert_refused("status", keyword="refusing to start")

    def test_protected_ledger_refused(self):
        self.env["PRO_BRIDGE_PROTECTED_LEDGER"] = str(self.root)
        self.assert_refused("status", "--ledger", str(self.root), keyword="PRO_BRIDGE_PROTECTED_LEDGER")


if __name__ == "__main__":
    unittest.main()
