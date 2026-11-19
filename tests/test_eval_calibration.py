import json
from pathlib import Path
import unittest

from openkb.eval.contract import Cost, Proposal, ReviewResult, Timing
from openkb.eval.goldset import load_goldset
from openkb.eval.runner import run_goldset


GOLDSET_PATH = Path(__file__).resolve().parents[1] / "goldsets" / "m0-compliance.json"


def metered_result(proposals):
    return ReviewResult(
        proposals=tuple(proposals),
        cost=Cost(32, 16, 0.125, "calibration-fixture"),
        timing=Timing(0.25, 1.0),
        state="done",
        used_ledger=False,
    )


class Oracle:
    def __init__(self, goldset):
        self.goldset = goldset

    def review(self, request):
        case = next(case for case in self.goldset.cases if case.request == request)
        return metered_result(
            Proposal(annotation["item_id"], annotation["judgment"])
            for annotation in case.annotations
        )


class AlwaysMaterial:
    def review(self, request):
        return metered_result(Proposal(item.item_id, "material") for item in request.artifact)


class FailingOracle(Oracle):
    def review(self, request):
        if request.corpus.after[0].document_id == "travel-expense":
            raise RuntimeError("simulated adapter failure")
        return super().review(request)


class EvaluationCalibrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.goldset = load_goldset(GOLDSET_PATH)

    def evaluate(self, system_name, system):
        report = run_goldset(self.goldset, system, system_name=system_name)
        return json.loads(report.to_json())

    def test_oracle_scores_perfectly_on_bundled_goldset(self):
        report = self.evaluate("oracle-fixture", Oracle(self.goldset))
        aggregate = report["aggregate"]
        self.assertEqual(aggregate["recall"], 1)
        self.assertEqual(aggregate["false_positive_rate"], 0)
        self.assertCountEqual(
            report["runs"][0]["excluded_cases"]["recall"],
            ["meeting-wording", "visitor-unchanged"],
        )
        self.assertEqual(report["runs"][0]["incomplete_cases"], [])

        # Six calls at 32 input tokens, 16 output tokens and 0.125 each.
        # The four material cases contain 2 + 2 + 1 + 2 correct items.
        cost = aggregate["cost"]
        self.assertEqual(cost["input_tokens"], 192)
        self.assertEqual(cost["output_tokens"], 96)
        self.assertEqual(cost["estimated_amount"], 0.75)
        self.assertAlmostEqual(cost["amount_per_correct_item"], 0.75 / 7)
        timings = [aggregate["timing"]]
        timings.extend(case["result"]["timing"] for case in report["cases"])
        for timing in timings:
            self.assertGreaterEqual(timing["total_seconds"], timing["model_seconds"])

    def test_always_material_exposes_false_positives_on_both_controls(self):
        report = self.evaluate("always-material-fixture", AlwaysMaterial())
        self.assertEqual(report["aggregate"]["recall"], 1)
        self.assertEqual(report["aggregate"]["false_positive_rate"], 1)

        cases = {case["case_id"]: case["metrics"] for case in report["cases"]}
        for case_id in ("meeting-wording", "visitor-unchanged"):
            with self.subTest(case_id=case_id):
                self.assertIsNone(cases[case_id]["recall"])
                self.assertEqual(cases[case_id]["false_positive_rate"], 1)

    def test_adapter_exception_is_listed_and_counts_as_a_missed_case(self):
        report = self.evaluate("failing-oracle-fixture", FailingOracle(self.goldset))
        self.assertEqual(report["runs"][0]["incomplete_cases"], ["travel-expense"])
        cases = {case["case_id"]: case for case in report["cases"]}
        self.assertEqual(len(cases), 6)
        failure = cases["travel-expense"]
        self.assertEqual(failure["result"]["state"], "failed")
        self.assertIn("simulated adapter failure", failure["result"]["error"])
        self.assertEqual(failure["metrics"]["recall"], 0)
        self.assertEqual(cases["purchase-approval"]["result"]["state"], "done")
        # The failed case stays among the four cases with material annotations.
        self.assertEqual(report["aggregate"]["recall"], 0.75)
        self.assertEqual(report["aggregate"]["false_positive_rate"], 0)
