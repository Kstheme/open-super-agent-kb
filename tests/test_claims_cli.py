import contextlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from openkb.agent.model import ModelClient, ModelConfig
from openkb.ingest.sqlite import SqliteStore
from openkb.server.cli import main


class ClaimsCliTests(unittest.TestCase):
    def setUp(self):
        work = TemporaryDirectory()
        self.addCleanup(work.cleanup)
        root = Path(work.name)
        self.database = root / "ledger.db"
        source = root / "policy.md"
        source.write_text("# 报销制度\n\n员工应于出差结束后十五天内提交报销。\n", encoding="utf-8")
        self.version = self.command("ingest", "add", str(source))["id"]
        version = self.command("ingest", "show", self.version)
        self.block = next(block for block in version["blocks"] if "十五天" in block["text"])
        self.responses = []
        self.calls = []
        model = ModelConfig("https://offline.invalid/v1", "fixture", "fixed", "fixture-v1")
        client = ModelClient(model, transport=self.transport)
        patcher = patch("openkb.server.claims_cli.ModelClient", return_value=client)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch("openkb.server.claims_cli.load_model_config", return_value=model)
        patcher.start()
        self.addCleanup(patcher.stop)

    def command(self, *args, expected_exit=0):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            status = main([*args, "--database", str(self.database)])
        self.assertEqual(status, expected_exit)
        return json.loads(output.getvalue())

    def transport(self, method, url, headers, payload, timeout):
        self.calls.append(method)
        if method == "GET":
            return {"data": [{"id": "fixed"}]}
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        content = json.dumps(response, ensure_ascii=False)
        return {"choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50}}

    def claim(self, quote):
        return {"claims": [{"text": "员工的报销期限为出差结束后十五天。",
                            "evidence": [{"block_id": self.block["id"], "quote": quote}]}]}

    def test_corrected_evidence_is_persisted_with_claim(self):
        quote = "出差结束后十五天内提交报销"
        self.responses = [self.claim("虚构引文"), self.claim(quote)]

        result = self.command("claims", "extract", self.version)
        self.assertEqual(result["state"], "done")
        self.assertEqual(self.calls, ["GET", "POST", "POST"])
        claim, = self.command("claims", "list", self.version)["claims"]
        evidence, = claim["evidence"]
        self.assertEqual(evidence["quote"], quote)
        self.assertEqual(evidence["version_id"], self.version)
        self.assertEqual(evidence["block_id"], self.block["id"])
        self.assertEqual(claim["id"], result["claims"][0]["id"])
        located = self.command("ingest", "evidence-show", evidence["id"])
        self.assertEqual(located["quote"], quote)
        self.assertEqual(located["version_id"], self.version)
        self.assertEqual(located["locator"]["line_start"], 3)

    def test_extraction_reads_the_selected_version_once_across_evidence_retries(self):
        valid = self.claim("出差结束后十五天内提交报销")
        valid["claims"][0]["evidence"].append(
            {"block_id": self.block["id"], "quote": "员工应于"}
        )
        self.responses = [self.claim("虚构引文"), valid]
        original = SqliteStore.version
        reads = []

        def version(store, version_id):
            reads.append(version_id)
            return original(store, version_id)

        with patch.object(SqliteStore, "version", version):
            result = self.command("claims", "extract", self.version)

        self.assertEqual(result["state"], "done")
        self.assertEqual(len(result["claims"][0]["evidence"]), 2)
        self.assertEqual(reads, [self.version])

    def test_unverifiable_evidence_reports_reason_without_saving_claim(self):
        self.responses = [self.claim("虚构引文") for _ in range(3)]

        result = self.command("claims", "extract", self.version, expected_exit=1)
        self.assertEqual(result["state"], "failed")
        self.assertEqual(self.calls, ["GET", "POST", "POST", "POST"])
        self.assertTrue(result["rejected"][0]["reason"])
        self.assertEqual(self.command("claims", "list", self.version)["claims"], [])


    def test_retry_failure_preserves_previously_verified_claim(self):
        valid, = self.claim("出差结束后十五天内提交报销")["claims"]
        invalid, = self.claim("虚构引文")["claims"]
        invalid["text"] = "还需要另外提交审批单。"
        self.responses = [{"claims": [valid, invalid]}, OSError("endpoint offline")]

        result = self.command("claims", "extract", self.version, expected_exit=1)
        self.assertEqual(result["state"], "failed")
        self.assertIn("endpoint offline", result["error"])
        self.assertEqual([item["text"] for item in result["rejected"]], [invalid["text"]])
        saved, = self.command("claims", "list", self.version)["claims"]
        self.assertEqual(saved["text"], valid["text"])
        self.assertEqual(result["claims"], [saved])

    def test_float_evidence_offset_is_rejected_without_crashing(self):
        response = self.claim(self.block["text"])
        response["claims"][0]["evidence"][0]["quote_start"] = 0.0
        self.responses = [response, response, response]
        result = self.command("claims", "extract", self.version, expected_exit=1)
        self.assertIn("must be an integer", result["rejected"][0]["reason"])
        self.assertEqual(self.command("claims", "list", self.version)["claims"], [])

    def test_omitting_invalid_claim_on_retry_keeps_rejection_reason(self):
        self.responses = [self.claim("虚构引文"), {"claims": []}, {"claims": []}]

        result = self.command("claims", "extract", self.version, expected_exit=1)
        self.assertEqual(result["state"], "failed")
        self.assertEqual(len(result["rejected"]), 1)
        self.assertEqual(self.command("claims", "list", self.version)["claims"], [])
