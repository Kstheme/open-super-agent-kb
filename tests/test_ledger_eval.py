from contextlib import redirect_stdout
from dataclasses import asdict
import hashlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from openkb.agent.model import ModelClient, ModelConfig
from openkb.eval.contract import Timing
from openkb.eval.goldset import load_goldset
from openkb.eval.ledger import LedgerReviewSystem
from openkb.eval.metrics import MetricCost, Metrics
from openkb.eval.presentation import compare_reports
from openkb.eval.report import RunReport
from openkb.eval.runner import run_goldset
from openkb.server.cli import main

from test_eval_cli import goldset


def evidence(version, block):
    return {"document_id": "policy", "version_id": version, "block_key": block["block_key"]}


def provenance_goldset():
    data = goldset()
    data["format_version"] = 1
    for case in data["cases"]:
        old, new = (case["corpus"][side][0]["version"]["blocks"] for side in ("before", "after"))
        context = {
            "block_key": "policy/archive", "heading_path": ["归档"], "ordinal": 2,
            "start_offset": 5, "end_offset": 9, "text": "保留原件",
        }
        old.append(context.copy())
        new.append(context.copy())
        annotation = case["annotations"][0]
        annotation.update(
            baseline_claim_ids=["notice-claim"], baseline_evidence=[evidence("v1", block) for block in old],
            annotation_private_note="SCORING_ONLY",
        )
        if annotation["judgment"] == "material":
            annotation.update(revised_text="GOLD_EXPECTED_TEXT", evidence=[evidence("v2", new[0])])
        case["budget"]["max_tokens"] = 12000
    return data


class LedgerEvalTests(unittest.TestCase):
    def setUp(self):
        workspace = TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.gold = self.root / "gold.json"

    def write_goldset(self, data):
        self.gold.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_projects_only_old_provenance_and_keeps_source_hash(self):
        data = provenance_goldset()
        self.write_goldset(data)
        loaded = load_goldset(self.gold)
        case = loaded.cases[0]
        annotation = data["cases"][0]["annotations"][0]
        self.assertEqual(case.request.ledger, {"items": {"notice": {
            "claim_ids": tuple(annotation["baseline_claim_ids"]),
            "evidence": tuple(annotation["baseline_evidence"]),
        }}})
        self.assertEqual(case.annotations[0], annotation)
        self.assertEqual(loaded.goldset_hash, hashlib.sha256(self.gold.read_bytes()).hexdigest())

    def test_keeps_explicit_legacy_ledger(self):
        data = goldset()
        explicit = {"legacy": {"value": 7}}
        data["cases"][0]["ledger"] = explicit
        self.write_goldset(data)
        loaded = load_goldset(self.gold)
        self.assertEqual(loaded.cases[0].request.ledger, explicit)
        self.assertIsNone(loaded.cases[1].request.ledger)

    def test_ledger_cli_uses_scoped_candidates_and_preserves_unchanged_evidence(self):
        self.write_goldset(provenance_goldset())
        config = self.root / "config.toml"
        config.write_text(
            '[model]\nbase_url = "http://fixture.invalid/v1"\n'
            'api_key = "fixture-key"\nmodel_id = "fixture-model"\n'
            '[prices]\nversion = "fixture-prices"\n',
            encoding="utf-8",
        )
        report = self.root / "report.json"
        material_attempts = 0

        def call(client, method, path, payload, timeout):
            nonlocal material_attempts
            if (method, path) == ("GET", "models"):
                return {"data": [{"id": client.config.model_id}]}
            self.assertEqual((method, path), ("POST", "chat/completions"))
            prompt = payload["messages"][0]["content"]
            self.assertNotIn("GOLD_EXPECTED_TEXT", prompt)
            self.assertNotIn("SCORING_ONLY", prompt)
            source = json.loads(next(line for line in prompt.splitlines() if line.startswith('{"item_text":')))
            self.assertEqual(set(source["old_evidence"]), {"七天", "保留原件"})
            self.assertEqual([block["text"] for block in source["new_candidates"]], ["十四天"])
            candidate = source["new_candidates"][0]
            material_attempts += 1
            if material_attempts == 1:
                # Valid old wording in the retained block cannot ground a material change.
                evidence = {"block_id": "policy/archive", "quote": "保留原件"}
            else:
                self.assertIn("Evidence must reference a candidate block", prompt)
                evidence = {"block_id": candidate["block_id"], "quote": "十四天"}
            answer = {
                "judgment": "material", "reason": "提交时间延长。",
                "revised_text": "十四天内提交。",
                "evidence": [evidence],
            }
            return {
                "choices": [{"message": {"content": json.dumps(answer)}}],
                "usage": {"prompt_tokens": 55, "completion_tokens": 10},
            }

        with patch.object(ModelClient, "_call", autospec=True, side_effect=call) as calls, redirect_stdout(io.StringIO()):
            status = main([
                "eval", "run", "--goldset", str(self.gold), "--system", "ledger",
                "--config", str(config), "--out", str(report),
            ])
        self.assertEqual(status, 0)
        self.assertEqual(calls.call_count, 3)
        result = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(result["system"], "ledger")
        self.assertEqual(result["aggregate"]["recall"], 1)
        self.assertEqual(result["aggregate"]["false_positive_rate"], 0)
        revision, unchanged = [case["result"] for case in result["cases"]]
        self.assertTrue(revision["used_ledger"])
        self.assertTrue(unchanged["used_ledger"])
        proposal, = revision["proposals"]
        self.assertEqual(proposal["revised_text"], "十四天内提交。")
        self.assertEqual(
            {(ref["document_id"], ref["version_id"], ref["block_key"]) for ref in proposal["evidence"]},
            {("policy", "v2", "policy/1-十四天"), ("policy", "v2", "policy/archive")},
        )
        self.assertEqual(unchanged["proposals"], [])
        self.assertEqual(unchanged["cost"]["input_tokens"], 0)

    def test_budget_pause_remains_counted_as_a_miss(self):
        data = provenance_goldset()
        data["cases"][0]["budget"]["max_steps"] = 0
        self.write_goldset(data)

        def unexpected_call(*args):
            self.fail("Neither the budget-exhausted revision nor the unchanged case should call the model")

        client = ModelClient(
            ModelConfig("http://fixture.invalid/v1", "fixture-key", "fixture-model", "fixture-prices"),
            transport=unexpected_call,
        )
        system = LedgerReviewSystem(client, checkpoint_dir=self.root)
        report = run_goldset(load_goldset(self.gold), system, system_name="ledger")
        paused, unchanged = report.cases
        self.assertEqual((paused.result.state, paused.result.gaps), ("paused", ("notice",)))
        self.assertTrue(paused.result.used_ledger)
        self.assertEqual(unchanged.result.state, "done")
        self.assertEqual(report.aggregate.recall, 0)
        self.assertEqual(report.runs[0].incomplete_cases, ("revision",))

    def test_gate_is_directional_and_does_not_depend_on_price_amount(self):
        report = RunReport(
            harness_version="1", goldset_hash="hash", system="ledger", model_id="model",
            profile_version="profile", price_table_version="v1", cases=(),
            aggregate=Metrics(0.75, 0.1, Timing(1, 2), MetricCost(100, 50, None, None)),
        )
        ledger = asdict(report)
        ledger["runs"] = [{}]
        regenerate = asdict(report)
        regenerate["system"] = "regenerate"
        regenerate["runs"] = [{}]
        self.assertIn("质量门槛 · 通过", compare_reports(regenerate, ledger))
        regenerate["aggregate"]["recall"] = 0.8
        self.assertIn("质量门槛 · 未通过", compare_reports(ledger, regenerate))
        regenerate["aggregate"]["recall"] = 0.75
        regenerate["aggregate"]["false_positive_rate"] = 0.05
        self.assertIn("质量门槛 · 未通过", compare_reports(ledger, regenerate))
        for field, value in (("model_id", "another"), ("runs", [{}, {}])):
            mismatch = {**regenerate, field: value}
            self.assertIn("质量门槛 · 不可比", compare_reports(ledger, mismatch))
