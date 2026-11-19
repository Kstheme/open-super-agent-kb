import json
from dataclasses import replace
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from openkb.eval.contract import Timing
from openkb.eval.metrics import MetricCost, Metrics
from openkb.eval.presentation import render_run_summary
from openkb.eval.report import RunReport
from openkb.eval.thresholds import EvalThresholds

from test_eval_cli import ROOT, goldset


class ReportCliTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.directory = Path(workspace.name)

    def run_cli(self, command, *args):
        installed_cli = os.environ.get("OPENKB_TEST_CLI")
        executable = [installed_cli] if installed_cli else [
            sys.executable, "-m", "openkb.server.cli",
        ]
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        if not installed_cli:
            env["PYTHONPATH"] = str(ROOT)
        return subprocess.run(
            [*executable, "eval", command, *map(str, args)],
            cwd=self.directory, env=env, capture_output=True, text=True, encoding="utf-8",
        )

    def write_json(self, name, data):
        path = self.directory / name
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return path

    def compare(self, *, changed_hash=False, missing_version=False):
        baseline = {
            "harness_version": "1",
            "goldset_hash": "same",
            "system": "noop",
            "model_id": None,
            "profile_version": "notice-v1",
            "price_table_version": None,
            "aggregate": {
                "recall": 0.25,
                "false_positive_rate": 0.5,
                "timing": {"model_seconds": 1, "total_seconds": 2},
                "cost": {
                    "input_tokens": 10,
                    "output_tokens": 4,
                    "estimated_amount": None,
                    "amount_per_correct_item": None,
                },
            },
        }
        opponent = json.loads(json.dumps(baseline))
        opponent["system"] = "regenerate"
        opponent["aggregate"]["recall"] = 0.75
        if changed_hash:
            opponent["goldset_hash"] = "different"
        if missing_version:
            del opponent["profile_version"]
        return self.run_cli(
            "compare",
            "--report-a", self.write_json("a.json", baseline),
            "--report-b", self.write_json("b.json", opponent),
        )

    def test_comparison_reports_signed_deltas_without_a_judgment(self):
        completed = self.compare()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("+0.5", completed.stdout)
        self.assertIn("noop", completed.stdout)
        self.assertIn("regenerate", completed.stdout)
        self.assertNotIn("建议性", completed.stdout)

    def test_comparison_preserves_large_token_counts_and_fractional_medians(self):
        report = {
            "harness_version": "1", "goldset_hash": "same", "system": "regenerate",
            "model_id": "fixture", "profile_version": "notice-v1", "price_table_version": "prices-v1",
            "cases": (),
        }
        baseline = RunReport(
            **report, aggregate=Metrics(0.25, 0.0, Timing(1, 2),
                                        MetricCost(1_000_000, 1_000_000.5, None, None)),
        )
        opponent = replace(
            baseline, aggregate=Metrics(0.75, 0.0, Timing(1, 2),
                                        MetricCost(1_000_001, 1_000_001.5, None, None)),
        )
        a, b = self.directory / "a.json", self.directory / "b.json"
        a.write_text(baseline.to_json(), encoding="utf-8")
        b.write_text(opponent.to_json(), encoding="utf-8")

        completed = self.run_cli("compare", "--report-a", a, "--report-b", b)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        lines = completed.stdout.splitlines()
        self.assertIn("输入 token | 1000000 | 1000001 | +1", lines)
        self.assertIn("输出 token | 1000000.5 | 1000001.5 | +1", lines)

    def test_comparison_warns_and_omits_deltas_for_different_goldsets(self):
        completed = self.compare(changed_hash=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("不可比", completed.stdout)
        self.assertNotIn("+0.5", completed.stdout)

    def test_comparison_rejects_a_missing_version_field(self):
        completed = self.compare(missing_version=True)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("profile_version", completed.stderr)

    def test_run_advice_uses_the_active_recall_and_cost_thresholds(self):
        report = RunReport(
            harness_version="1", goldset_hash="same", system="regenerate",
            model_id="fixture", profile_version="notice-v1", price_table_version="prices-v1",
            cases=(),
            aggregate=Metrics(0.86, 0.0, Timing(1, 2), MetricCost(0, 0, 0.6, None)),
            thresholds=EvalThresholds(0.05, 0.6),
        )
        reference = json.loads(report.to_json())
        reference["system"] = "ledger"
        reference["aggregate"]["recall"] = 0.90
        reference["aggregate"]["cost"]["estimated_amount"] = 1.00
        self.assertIn("只提供定位", render_run_summary(report, reference=reference))
        for thresholds in (EvalThresholds(0.02, 0.6), EvalThresholds(0.05, 0.5)):
            with self.subTest(thresholds=thresholds):
                tighter = replace(report, thresholds=thresholds)
                self.assertNotIn("只提供定位", render_run_summary(tighter, reference=reference))
        reference["price_table_version"] = "other"
        self.assertIn("不可比", render_run_summary(report, reference=reference))

    def test_run_echoes_actual_thresholds_and_excluded_cases(self):
        config = self.directory / "config.toml"
        config.write_text(
            "[eval.thresholds]\nrecall_tolerance = 0.125\ncheaper_cost_ratio = 0.6\n",
            encoding="utf-8",
        )
        report_path = self.directory / "report.json"
        completed = self.run_cli(
            "run", "--goldset", self.write_json("gold.json", goldset()),
            "--config", config, "--out", report_path,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(report["thresholds"], {"recall_tolerance": 0.125, "cheaper_cost_ratio": 0.6})
        for expected in (
            "notice-v1", report["goldset_hash"], "0.125", "0.6", "建议性", "control", "命令墙钟时间",
        ):
            self.assertIn(expected, completed.stdout)
