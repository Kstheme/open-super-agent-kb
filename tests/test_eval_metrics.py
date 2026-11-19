import json
from pathlib import Path
from unittest.mock import patch
import tempfile
import unittest

from openkb.eval.contract import Budget, Corpus, Cost, Proposal, ReviewRequest, ReviewResult, Timing
from openkb.eval.goldset import GoldCase, GoldSet
from openkb.eval.metrics import cost, false_positive_rate, latency, recall, score_case
from openkb.eval.runner import run_goldset
from openkb.server.cli import main


def result(*proposals, state="done", amount=0.12):
    return ReviewResult(
        proposals, Cost(30, 10, amount, "fixed-model"), Timing(0.5, 1),
        state, False, ("review missing evidence",) if state == "paused" else (),
    )


def proposal(item, judgment="material"):
    return Proposal(item, judgment)


def case(name, **labels):
    return GoldCase(
        name, ReviewRequest(Corpus((), ()), (), name, Budget(1, 100, 5)),
        tuple(dict(item_id=item, judgment=judgment) for item, judgment in labels.items()),
    )


class MetricTests(unittest.TestCase):
    def test_rates_against_hand_calculated_examples(self):
        scenarios = [
            ("perfect", {"a": "material", "b": "immaterial", "c": "not_affected"},
             result(proposal("a"), proposal("b", "immaterial")), (1, 0)),
            ("all material", {"a": "material", "b": "immaterial", "c": "not_affected"},
             result(proposal("a"), proposal("b"), proposal("c")), (1, 1)),
            ("negative labels both count", {"a": "material", "b": "immaterial", "c": "not_affected"},
             result(proposal("b")), (0, 0.5)),
            ("noop", {"a": "material", "b": "not_affected"}, result(), (0, 0)),
            ("immaterial only", {"a": "immaterial"}, result(proposal("a")), (None, 1)),
            ("no denominator", {}, result(), (None, None)),
            ("no negatives", {"a": "material"}, result(proposal("a")), (1, None)),
            ("paused", {"a": "material", "b": "not_affected"},
             result(proposal("a"), proposal("b"), state="paused"), (0, 0)),
            ("failed", {"a": "material"}, result(proposal("a"), state="failed"), (0, None)),
        ]
        for name, labels, review, expected in scenarios:
            with self.subTest(name=name):
                annotations = case(name, **labels).annotations
                self.assertEqual(
                    (recall(review, annotations), false_positive_rate(review, annotations)), expected,
                )

    def test_cost_and_latency_retain_known_values_and_unknown_prices(self):
        annotations = case("cost", first="material", second="material").annotations
        known = result(proposal("first"), proposal("second"))
        self.assertEqual(latency(known), Timing(0.5, 1))
        known_cost = cost(known, correct_items=2)
        self.assertEqual((known_cost.input_tokens, known_cost.output_tokens), (30, 10))
        self.assertEqual(known_cost.estimated_amount, 0.12)
        self.assertEqual(known_cost.amount_per_correct_item, 0.06)
        unknown = score_case(result(proposal("first"), amount=None), annotations)
        self.assertIsNone(unknown.cost.estimated_amount)
        self.assertIsNone(unknown.cost.amount_per_correct_item)
        self.assertIsNone(cost(known, correct_items=0).amount_per_correct_item)


class MetricReportTests(unittest.TestCase):
    def test_runner_measures_case_wall_time_instead_of_trusting_the_adapter(self):
        class FixedSystem:
            def review(self, request):
                return result()

        goldset = GoldSet("fixture", "hash", (case("timed", a="material"),))
        with patch("openkb.eval.runner.perf_counter", side_effect=[10.0, 12.0]):
            report = run_goldset(goldset, FixedSystem(), system_name="fixed")
        self.assertEqual(report.cases[0].result.timing, Timing(0.5, 2.0))
        self.assertEqual(report.aggregate.timing, Timing(0.5, 2.0))

    def test_macro_medians_exclusions_and_disagreement(self):
        cases = (
            case("small", a="material"),
            case("large", a="material", b="material", c="material", d="not_affected"),
            case("control", a="immaterial"),
        )

        class FixedSystem:
            def __init__(self):
                self.reviews = iter([
                    result(proposal("a")), result(), result(),
                    result(), result(proposal("a"), proposal("b")), result(),
                    result(proposal("a")), result(proposal("a"), state="paused"), result(),
                ])

            def review(self, request):
                return next(self.reviews)

        goldset = GoldSet("fixture-profile", "known-hash", cases)
        report = json.loads(run_goldset(goldset, FixedSystem(), system_name="fixed", runs=3).to_json())
        self.assertEqual(report["aggregate"]["recall"], 0.5)
        first, second, third = report["runs"]
        self.assertEqual(first["aggregate"]["recall"], 0.5)
        self.assertAlmostEqual(second["aggregate"]["recall"], 1 / 3)
        self.assertEqual(third["aggregate"]["recall"], 0.5)
        self.assertEqual(first["aggregate"]["false_positive_rate"], 0)
        self.assertEqual(first["aggregate"]["cost"]["input_tokens"], 90)
        self.assertEqual(first["aggregate"]["cost"]["output_tokens"], 30)
        self.assertAlmostEqual(first["aggregate"]["cost"]["amount_per_correct_item"], 0.36)
        self.assertEqual(first["aggregate"]["timing"]["model_seconds"], 1.5)
        self.assertGreaterEqual(first["aggregate"]["timing"]["total_seconds"], 1.5)
        self.assertEqual(first["excluded_cases"], {"recall": ["control"], "false_positive_rate": ["small"]})
        self.assertEqual(third["incomplete_cases"], ["large"])
        self.assertEqual(third["cases"][1]["result"]["gaps"], ["review missing evidence"])
        self.assertEqual(report["disagreement"], {"small": 1.0, "large": 0.5, "control": 0.0})

    def test_requested_runs_are_written_by_the_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "goldset.json"
            output = Path(tmp) / "report.json"
            block = {
                "block_key": "notice/1", "heading_path": ["通知"], "ordinal": 1,
                "start_offset": 0, "end_offset": 2, "text": "七天",
            }
            document = {"document_id": "notice", "version": {"version_id": "v1", "blocks": [block]}}
            source.write_text(json.dumps({"profile_version": "fixture-profile", "cases": [{
                "case_id": "control", "artifact": [],
                "corpus": {"before": [document], "after": [document]},
                "goal": "review", "budget": {"max_steps": 1, "max_tokens": 10, "max_wall_seconds": 1},
                "annotations": [],
            }]}), encoding="utf-8")
            self.assertEqual(main(["eval", "run", "--goldset", str(source), "--runs", "3",
                                   "--out", str(output)]), 0)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(len(report["runs"]), 3)
            self.assertEqual(report["aggregate"]["cost"]["input_tokens"], 0)
