import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from openkb.eval.contract import Cost, Proposal, ReviewRequest, ReviewResult, Timing
from openkb.eval.goldset import load_goldset
from openkb.eval.runner import run_goldset


ROOT = Path(__file__).resolve().parents[1]


def goldset():
    def document(version, text):
        return {"document_id": "policy", "version": {"version_id": version, "blocks": [{
            "block_key": f"policy/1-{text}", "heading_path": ["通知"], "ordinal": 1,
            "start_offset": 0, "end_offset": len(text), "text": text,
        }]}}

    def case(case_id, after, judgment):
        return {
            "case_id": case_id,
            "corpus": {
                "before": [document("v1", "七天")],
                "after": [document("v2", after)],
            },
            "artifact": [{"item_id": "notice", "snapshot_text": "七天"}],
            "goal": "维护通知清单",
            "budget": {"max_steps": 3, "max_tokens": 500, "max_wall_seconds": 5},
            "annotations": [{"item_id": "notice", "judgment": judgment}],
        }

    return {"profile_version": "notice-v1", "cases": [
        case("revision", "十四天", "material"), case("control", "七天", "not_affected"),
    ]}


class EvalRunnerTests(unittest.TestCase):
    def test_adapter_error_is_reported_and_later_cases_still_run(self):
        class FailingReviewSystem:
            def review(self, request: ReviewRequest) -> ReviewResult:
                if request.corpus.after[0].version.blocks[0].text == "十四天":
                    raise RuntimeError("could not review revision")
                return ReviewResult((), Cost(0, 0, 0, "fixed-model"), Timing(0, 0), "done", False)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "goldset.json"
            path.write_text(json.dumps(goldset(), ensure_ascii=False), encoding="utf-8")
            report = run_goldset(load_goldset(path), FailingReviewSystem(), system_name="fixed")

        failed, done = json.loads(report.to_json())["cases"]
        self.assertEqual(failed["case_id"], "revision")
        self.assertEqual(failed["result"]["state"], "failed")
        self.assertIn("could not review revision", failed["result"]["error"])
        self.assertEqual(failed["result"]["proposals"], [])
        self.assertEqual(done["case_id"], "control")
        self.assertEqual(done["result"]["state"], "done")

    def test_injected_system_receives_both_versions_and_is_reported(self):
        class FixedReviewSystem:
            def review(self, request: ReviewRequest) -> ReviewResult:
                before = request.corpus.before[0].version.blocks[0]
                after = request.corpus.after[0].version.blocks[0]
                item, = request.artifact
                judgment = "material" if before.text != after.text else "not_affected"
                proposal = Proposal(item.item_id, judgment, after.text)
                return ReviewResult(
                    (proposal,), Cost(60, 40, None, "fixed-model"), Timing(1, 2),
                    "paused", False, (before.block_key, after.block_key),
                )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "goldset.json"
            path.write_text(json.dumps(goldset(), ensure_ascii=False), encoding="utf-8")
            report = run_goldset(
                load_goldset(path), FixedReviewSystem(), system_name="fixed", price_table_version="v1",
            )
        decoded = json.loads(report.to_json())
        self.assertEqual(decoded["system"], "fixed")
        self.assertEqual(decoded["model_id"], "fixed-model")
        self.assertEqual(decoded["price_table_version"], "v1")
        revision, control = [case["result"] for case in decoded["cases"]]
        self.assertEqual(revision["state"], "paused")
        self.assertEqual(revision["gaps"], ["policy/1-七天", "policy/1-十四天"])
        self.assertEqual(revision["proposals"][0]["judgment"], "material")
        self.assertEqual(control["proposals"][0]["judgment"], "not_affected")


class EvalCliTests(unittest.TestCase):
    def run_cli(self, directory, data, *args):
        source = directory / "goldset.json"
        source.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        installed_cli = os.environ.get("OPENKB_TEST_CLI")
        command = [installed_cli] if installed_cli else [sys.executable, "-m", "openkb.server.cli"]
        env = os.environ.copy()
        if not installed_cli:
            env["PYTHONPATH"] = str(ROOT)
        return subprocess.run(
            [*command, "eval", "run", "--goldset", str(source), *args],
            cwd=directory, env=env, capture_output=True, text=True, encoding="utf-8",
        )

    def test_noop_writes_a_report_for_the_entire_goldset(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            completed = self.run_cli(directory, goldset(), "--system", "noop")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["goldset_hash"], hashlib.sha256(
                (directory / "goldset.json").read_bytes(),
            ).hexdigest())
            self.assertEqual(report["harness_version"], "1")
            self.assertEqual(report["system"], "noop")
            self.assertEqual(report["profile_version"], "notice-v1")
            self.assertIsNone(report["model_id"])
            self.assertIsNone(report["price_table_version"])
            self.assertEqual([case["case_id"] for case in report["cases"]], ["revision", "control"])
            for case in report["cases"]:
                self.assertEqual(case["result"]["proposals"], [])
                self.assertEqual(case["result"]["state"], "done")
                self.assertIs(case["result"]["used_ledger"], False)

    def test_default_system_writes_to_the_requested_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            completed = self.run_cli(directory, goldset(), "--out", "custom.json")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            report = json.loads((directory / "custom.json").read_text(encoding="utf-8"))
            self.assertEqual(report["system"], "noop")
            self.assertFalse((directory / "report.json").exists())

    def test_blocks_require_a_key_in_both_snapshots(self):
        for side in ("before", "after"):
            with self.subTest(side=side), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp)
                data = goldset()
                block = data["cases"][1]["corpus"][side][0]["version"]["blocks"][0]
                del block["block_key"]
                completed = self.run_cli(directory, data, "--out", "custom.json")
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("block_key", completed.stderr)
                self.assertIn(side, completed.stderr)
                self.assertFalse((directory / "custom.json").exists())
