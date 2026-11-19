import json
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openkb.agent.model import EndpointError, ModelClient, ModelConfig, Price
from openkb.eval.contract import ArtifactItem, Block, Budget, Corpus, Document, ReviewRequest, Version
from openkb.eval.regenerate import RegenerateReviewSystem
from openkb.server.cli import main


MODEL = ModelConfig("https://model.invalid/v1", "test-key", "test-model", "test-prices", Price(1, 2))
EVIDENCE = {"document_id": "policy", "version_id": "v2", "block_key": "notice/new"}


class FakeEndpoint:
    def __init__(self, *outputs):
        self.outputs = iter(outputs)
        self.calls = []

    def __call__(self, method, url, headers, payload, timeout):
        self.calls.append((method, url))
        if method == "GET":
            return {"data": [{"id": "test-model"}]}
        return {
            "choices": [{"message": {"content": json.dumps(next(self.outputs))}}],
            "usage": {"prompt_tokens": 150, "completion_tokens": 50},
        }


def _request(steps=2):
    old = Block("notice/old", ("通知期限",), 1, 0, 2, "七天")
    new = Block("notice/new", ("通知期限",), 1, 0, 3, "十四天")
    return ReviewRequest(
        Corpus((Document("policy", Version("v1", (old,))),),
               (Document("policy", Version("v2", (new,))),)),
        (
            ArtifactItem("notice", "七天", "通知期限", 1),
            ArtifactItem("channel", "书面提交", "通知期限", 2),
        ),
        "维护合规检查清单",
        Budget(steps, 12000, 10),
        {"private": "must not enter regeneration"},
    )


class RegenerateTests(unittest.TestCase):
    def test_id_takes_priority_and_slot_aligns_the_remaining_item(self):
        endpoint = FakeEndpoint(
            {"items": [
                {"section": "通知期限", "ordinal": 1, "text": "十四天",
                 "item_id": "notice", "evidence": [EVIDENCE]},
                {"section": "通知期限", "ordinal": 2, "text": "使用书面形式"},
            ]},
            {"notice": "material", "channel": "immaterial"},
        )
        # The stable ID and original slot intentionally disagree.
        request = _request()
        request = ReviewRequest(
            request.corpus,
            (ArtifactItem("notice", "七天", "通知期限", 3), request.artifact[1]),
            request.goal, request.budget, request.ledger,
        )
        result = RegenerateReviewSystem(ModelClient(MODEL, transport=endpoint)).review(request)

        self.assertEqual(result.state, "done")
        self.assertFalse(result.used_ledger)
        self.assertEqual(
            [(proposal.item_id, proposal.judgment, proposal.revised_text)
             for proposal in result.proposals],
            [("notice", "material", "十四天"), ("channel", "immaterial", "使用书面形式")],
        )
        self.assertEqual(result.proposals[0].evidence[0].block_key, "notice/new")
        self.assertEqual((result.cost.input_tokens, result.cost.output_tokens), (300, 100))
        self.assertAlmostEqual(result.cost.estimated_amount, 0.0005)
        self.assertGreaterEqual(result.timing.total_seconds, result.timing.model_seconds)

    def test_budget_pause_preserves_case_gaps_and_first_call_usage(self):
        endpoint = FakeEndpoint({"items": []})
        with tempfile.TemporaryDirectory() as directory:
            result = RegenerateReviewSystem(
                ModelClient(MODEL, transport=endpoint), checkpoint_dir=Path(directory),
            ).review(_request(steps=1))
            record, = list(Path(directory).glob("*.json"))
            checkpoint = json.loads(record.read_text(encoding="utf-8"))

        self.assertEqual(result.state, "paused")
        self.assertEqual(result.gaps, ("notice", "channel"))
        self.assertEqual(checkpoint["gaps"], ["notice", "channel"])
        self.assertEqual(result.cost.input_tokens, 150)
        self.assertEqual(len(endpoint.calls), 1)

    def test_regenerate_and_noop_have_same_goldset_version(self):
        data = {
            "profile_version": "notice-v1",
            "cases": [{
                "case_id": "notice-change",
                "goal": "维护通知清单",
                "budget": {"max_steps": 2, "max_tokens": 12000, "max_wall_seconds": 10},
                "corpus": {},
                "artifact": [{"item_id": "notice", "snapshot_text": "七天"}],
                "annotations": [{"item_id": "notice", "judgment": "material"}],
            }],
        }
        for side, version, key, value in (
            ("before", "v1", "notice/old", "七天"), ("after", "v2", "notice/new", "十四天"),
        ):
            data["cases"][0]["corpus"][side] = [{
                "document_id": "policy", "version": {"version_id": version, "blocks": [{
                    "block_key": key, "heading_path": ["通知期限"], "ordinal": 1,
                    "start_offset": 0, "end_offset": len(value), "text": value,
                }]},
            }]
        endpoint = FakeEndpoint(
            {"items": [{"section": "未分节", "ordinal": 1, "text": "十四天"}]},
            {"notice": "material"},
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, noop, regenerated = (root / name for name in ("gold.json", "noop.json", "regen.json"))
            source.write_text(json.dumps(data), encoding="utf-8")
            command = ["eval", "run", "--goldset", str(source)]
            self.assertEqual(main([*command, "--out", str(noop)]), 0)
            before = json.loads(noop.read_text(encoding="utf-8"))
            reference = root / "reference.json"
            reference.write_text(json.dumps(dict(
                before, system="ledger", model_id=MODEL.model_id,
                price_table_version=MODEL.price_table_version,
                aggregate={"recall": 1.0, "cost": {"estimated_amount": 0.002}},
            )), encoding="utf-8")
            with (
                patch("openkb.server.cli.load_model_config", return_value=MODEL),
                patch("openkb.server.cli.ModelClient", return_value=ModelClient(MODEL, transport=endpoint)),
                patch("sys.stdout", new_callable=StringIO) as stdout,
            ):
                self.assertEqual(main([*command, "--system", "regenerate", "--out", str(regenerated),
                                       "--reference-report", str(reference)]), 0)
            self.assertIn("只提供定位", stdout.getvalue())
            after = json.loads(regenerated.read_text(encoding="utf-8"))

        self.assertEqual(before["goldset_hash"], after["goldset_hash"])
        self.assertEqual(after["system"], "regenerate")
        self.assertEqual(after["model_id"], "test-model")
        self.assertEqual(after["price_table_version"], "test-prices")
        self.assertTrue(after["notes"])
        self.assertEqual(after["aggregate"]["recall"], 1.0)
        self.assertEqual([call[0] for call in endpoint.calls], ["GET", "POST", "POST"])

    def test_endpoint_preflight_does_not_publish_a_failed_evaluation(self):
        root = Path(__file__).resolve().parents[1]
        client = ModelClient(MODEL, transport=self._unreachable)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            with (
                patch("openkb.server.cli.load_model_config", return_value=MODEL),
                patch("openkb.server.cli.ModelClient", return_value=client),
                self.assertRaises(SystemExit) as failure,
            ):
                main(["eval", "run", "--goldset", str(root / "goldsets/m0-compliance.json"),
                      "--system", "regenerate", "--out", str(output)])
            self.assertEqual(failure.exception.code, 2)
            self.assertFalse(output.exists())

    @staticmethod
    def _unreachable(method, url, headers, payload, timeout):
        if method != "GET":
            raise AssertionError("case operation ran after preflight failed")
        raise EndpointError("offline fixture")
