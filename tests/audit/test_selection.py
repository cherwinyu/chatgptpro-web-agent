"""Synthetic control traces, not evidence of a live ChatGPT run."""
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/pro_bridge"))
import cli
import ui_selection


def trace(kind="slider"):
    return {"mode": "CHAT", "logged_in": True, "permission_confirmed": True,
            "selection_unambiguous": True, "locked": False, "control_kind": kind,
            "observed_at": time.time(), "evidence_ref": "synthetic-control-fixture",
            "steps": [{"model_label": "Observed Model", "effort_label": "Medium", "value": -10},
                      {"model_label": "Observed Model", "effort_label": "High", "value": 7},
                      {"model_label": "Observed Model", "effort_label": "Pro", "value": 11.5}]}


def target():
    return {"mode": "CHAT", "project_id": "synthetic-project", "conversation_id": "synthetic-chat",
            "browser_context": "synthetic-context", "tab_id": "synthetic-tab",
            "model_label": "Observed Model", "effort_label": "Pro"}


class SelectionTests(unittest.TestCase):
    def check(self, item, effort="Pro"):
        return ui_selection.check_selection(item, model="Observed Model", effort=effort, now=time.time())

    def test_slider_without_pro_menuitem(self):
        value = trace()
        value["menu_items"] = ["Observed Model", "Other Model"]
        result = self.check(value)
        self.assertTrue(result["check_passed"])
        self.assertFalse(result["send_authorized"])
        self.assertEqual(result["backend_model_id"], "NOT_OBSERVABLE")

    def test_slider_no_fixed_position_or_key_count(self):
        value = trace()
        value["steps"] = [value["steps"][0], value["steps"][-1]]
        value["steps"][-1]["value"] = 101
        self.assertTrue(self.check(value)["check_passed"])

    def test_already_selected_stops_without_adjustment(self):
        value = trace()
        value["steps"] = value["steps"][-1:]
        self.assertTrue(self.check(value)["check_passed"])

    def test_menu_and_radio(self):
        for kind in ("menu", "radio"):
            with self.subTest(kind=kind):
                value = trace(kind)
                for step in value["steps"]:
                    del step["value"]
                self.assertTrue(self.check(value)["check_passed"])

    def test_locked_denied_work_and_ambiguity(self):
        for field, wrong in (("locked", True), ("locked", None), ("permission_confirmed", False),
                             ("mode", "WORK"), ("mode", "UNKNOWN"), ("selection_unambiguous", False)):
            with self.subTest(field=field, wrong=wrong):
                self.assertFalse(self.check(trace() | {field: wrong})["check_passed"])

    def test_wrong_model(self):
        value = trace()
        value["steps"][-1]["model_label"] = "Wrong Model"
        self.assertFalse(self.check(value)["check_passed"])

    def test_moving_value_with_unchanged_label_refused(self):
        value = trace()
        value["steps"][1]["effort_label"] = "Medium"
        self.assertIn("slider_label_did_not_update", self.check(value)["reasons"])

    def test_unchanged_value_contradicts_label(self):
        value = trace()
        value["steps"][1]["value"] = value["steps"][0]["value"]
        self.assertIn("slider_did_not_move", self.check(value)["reasons"])

    def test_no_adjustments_after_target(self):
        value = trace()
        value["steps"].append(value["steps"][-1].copy())
        self.assertIn("adjustment_after_requested_labels_reached", self.check(value)["reasons"])

    def test_absence_cannot_satisfy_pro(self):
        value = trace("none") | {"absence_confirmed": True}
        value["steps"] = [{"model_label": "Observed Model", "effort_label": "NOT_AVAILABLE"}]
        self.assertFalse(self.check(value)["check_passed"])
        self.assertTrue(self.check(value, "NOT_AVAILABLE")["check_passed"])

    def test_stale_unbounded_and_nonfinite(self):
        for changes in ({"observed_at": time.time()-1000}, {"observed_at": float("nan")},
                        {"steps": []}, {"steps": trace()["steps"] * 33}):
            with self.subTest(changes=str(changes)[:50]):
                self.assertFalse(self.check(trace() | changes)["check_passed"])

    def test_invalid_selection_rejected_before_prepare(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "target.json").write_text(json.dumps(target()), encoding="utf-8")
            for field, value in (("locked", True), ("mode", "WORK"), ("permission_confirmed", False),
                                 ("selection_unambiguous", False)):
                (root / "selection.json").write_text(json.dumps(trace() | {field: value}), encoding="utf-8")
                output = io.StringIO()
                with redirect_stdout(output):
                    code = cli.main(["prepare", "--ledger", str(root / "ledger"), "--task-type", "RESEARCH",
                                     "--repository", "example/repo", "--source-sha", "a"*40, "--source-ref", "main",
                                     "--orchestration", "synthetic", "--browser-target", str(root / "target.json"),
                                     "--selection-evidence", str(root / "selection.json")])
                self.assertEqual(code, 3, output.getvalue())
                self.assertFalse((root / "ledger").exists())

    @unittest.skipUnless(os.name == "nt", "real ledger mutex requires Windows")
    def test_bound_claim_requires_fresh_selection_and_at_most_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            selection = trace()
            (root / "target.json").write_text(json.dumps(target()), encoding="utf-8")
            (root / "selection.json").write_text(json.dumps(selection), encoding="utf-8")
            base = [sys.executable, "-B", str(ROOT / "adapters/generic/run_bridge.py")]
            def run(command, *args):
                result = subprocess.run([*base, command, "--ledger", str(root / "ledger"), *args],
                                        capture_output=True, text=True, encoding="utf-8", timeout=30)
                return result.returncode, json.loads(result.stdout)
            code, prepared = run("prepare", "--task-type", "RESEARCH", "--repository", "example/repo",
                                 "--source-sha", "a"*40, "--source-ref", "main", "--orchestration", "synthetic",
                                 "--browser-target", str(root / "target.json"), "--selection-evidence", str(root / "selection.json"))
            self.assertEqual(code, 0, prepared)
            ledger = root / "ledger/ledger.json"
            observation = target() | {"logged_in": True, "composer_ready": True, "selection_unambiguous": True,
                          "mode_evidence": "synthetic", "project_evidence": "synthetic", "conversation_evidence": "synthetic",
                          "page_url": "https://chatgpt.com/c/synthetic", "observed_at": time.time()}
            before = ledger.read_bytes()
            for nested in (None, selection | {"locked": True}, selection | {"permission_confirmed": False}):
                value = observation | ({"selection_evidence": nested} if nested is not None else {})
                (root / "observation.json").write_text(json.dumps(value), encoding="utf-8")
                code, refused = run("claim", "--task", prepared["task_id"], "--browser-observation", str(root / "observation.json"))
                self.assertEqual(code, 3, refused)
                self.assertEqual(ledger.read_bytes(), before)
            observation["selection_evidence"] = trace()
            observation["observed_at"] = time.time()
            (root / "observation.json").write_text(json.dumps(observation), encoding="utf-8")
            code, claimed = run("claim", "--task", prepared["task_id"], "--browser-observation", str(root / "observation.json"))
            self.assertEqual(code, 0, claimed)
            self.assertEqual(claimed["send_attempts"], 1)
            self.assertEqual(run("claim", "--task", prepared["task_id"], "--browser-observation", str(root / "observation.json"))[0], 2)


if __name__ == "__main__":
    unittest.main()
