import json
from pathlib import Path
import tempfile
import unittest

from openkb.agent.model import (
    EndpointError, ModelClient, load_model_config,
)


SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}},
          "required": ["answer"], "additionalProperties": False}


class ModelClientTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.config_path = self.directory / "config.toml"
        self.config_path.write_text(
            '[model]\nbase_url = "https://example.test/v1/"\n'
            'api_key = "fixture-key"\nmodel_id = "fixture"\n'
            '[prices]\nversion = "fixture-v1"\n'
            '[prices.models.fixture]\ninput_per_million = 1\noutput_per_million = 2\n',
            encoding="utf-8",
        )
        self.requests = []
        self.replies = []

    def fake_transport(self, method, url, headers, payload, timeout):
        self.requests.append((method, url, headers, payload, timeout))
        if method == "GET":
            return {"data": [{"id": "fixture"}]}
        content, prompt, completion = self.replies.pop(0)
        return {
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": prompt, "completion_tokens": completion},
        }

    def client(self, *, transport=None, clock=None):
        return ModelClient(load_model_config(self.config_path),
                           transport=transport or self.fake_transport, clock=clock)

    def session(self, *, client=None, max_steps=3, max_tokens=3000, max_wall_seconds=5):
        return (client or self.client()).session(
            max_steps=max_steps, max_tokens=max_tokens, max_wall_seconds=max_wall_seconds,
            checkpoint_dir=self.directory / "pauses",
        )

    def test_validates_and_retries_and_accounts_for_every_call(self):
        self.replies = [('{"answer":42}', 11, 2), ('{"answer":"ok"}', 13, 5)]
        client = self.client()
        client.preflight()
        session = self.session(client=client)
        result = session.generate("Return the answer.", SCHEMA, gaps=("answer",))
        self.assertEqual(result.state, "done")
        self.assertEqual(result.value, {"answer": "ok"})
        self.assertEqual(client.config.price_table_version, "fixture-v1")
        self.assertEqual([call.input_tokens for call in session.calls], [11, 13])
        self.assertEqual([call.output_tokens for call in session.calls], [2, 5])
        self.assertEqual(session.usage.input_tokens, 24)
        self.assertEqual(session.usage.output_tokens, 7)
        self.assertAlmostEqual(session.usage.estimated_amount, 0.000038)
        self.assertEqual([call[0] for call in self.requests], ["GET", "POST", "POST"])
        method, url, headers, body, timeout = self.requests[1]
        self.assertEqual(url, "https://example.test/v1/chat/completions")
        self.assertEqual(headers["Authorization"], "Bearer fixture-key")
        self.assertEqual(body["model"], "fixture")
        self.assertNotIn("tools", body)
        self.assertLessEqual(timeout, 5)

    def test_unknown_price_is_null(self):
        self.config_path.write_text(
            self.config_path.read_text(encoding="utf-8").split("[prices.models.fixture]")[0],
            encoding="utf-8",
        )
        self.replies = [('{"answer":"ok"}', 4, 1)]
        result = self.session()
        self.assertEqual(result.generate("Try", SCHEMA, gaps=()).state, "done")
        self.assertIsNone(result.usage.estimated_amount)

    def test_missing_configuration_or_unreachable_endpoint_fails_explicitly(self):
        with self.assertRaisesRegex(ValueError, "Model configuration"):
            load_model_config(self.directory / "missing.toml")
        def unavailable(*_args):
            raise OSError("fixture offline")
        with self.assertRaisesRegex(EndpointError, "preflight.*fixture offline"):
            self.client(transport=unavailable).preflight()

    def test_retry_exhausts_step_budget_and_persists_original_gaps(self):
        self.replies = [('{"answer":42}', 8, 3)]
        session = self.session(max_steps=1)
        result = session.generate("Generate", SCHEMA, gaps=("answer", "source"))
        self.assertEqual(result.state, "paused")
        self.assertEqual(result.gaps, ("answer", "source"))
        self.assertEqual(result.reason, "step budget exhausted")
        checkpoint = json.loads(result.checkpoint.read_text(encoding="utf-8"))
        self.assertEqual(checkpoint["state"], "paused")
        self.assertEqual(checkpoint["gaps"], ["answer", "source"])
        self.assertEqual(checkpoint["usage"]["input_tokens"], 8)
        self.assertEqual(checkpoint["calls"][0]["output_tokens"], 3)
        self.assertEqual(len(self.requests), 1)

    def test_does_not_submit_requests_which_cannot_fit_token_budget(self):
        result = self.session(max_tokens=1).generate("Request", SCHEMA, gaps=("answer",))
        self.assertEqual(result.state, "paused")
        self.assertEqual(result.reason, "token budget exhausted")
        self.assertEqual(self.requests, [])

    def test_detects_usage_beyond_cap_and_retains_observed_usage(self):
        self.replies = [('{"answer":"ok"}', 1, 4000)]
        session = self.session(max_tokens=3000)
        result = session.generate("Generate", SCHEMA, gaps=("answer",))
        self.assertEqual(result.state, "paused")
        self.assertEqual(result.reason, "token budget exhausted")
        self.assertIsNone(result.value)
        self.assertEqual(session.usage.output_tokens, 4000)

    def test_discards_result_returned_after_deadline(self):
        elapsed = [0.0]
        self.replies = [('{"answer":"ok"}', 1, 1)]
        def slow(*args):
            elapsed[0] += 6
            return self.fake_transport(*args)
        client = self.client(transport=slow, clock=lambda: elapsed[0])
        result = self.session(client=client).generate("Generate", SCHEMA, gaps=("answer",))
        self.assertEqual(result.state, "paused")
        self.assertEqual(result.reason, "wall clock budget exhausted")
        self.assertIsNone(result.value)

    def test_malformed_response_exhausts_number_of_attempts(self):
        self.replies = [('invalid JSON', 2, 1)] * 3
        result = self.session().generate("Try", SCHEMA, gaps=("answer",))
        self.assertEqual(result.state, "failed")
        self.assertIn("JSON Schema", result.reason)
        self.assertEqual(len(self.requests), 3)


if __name__ == "__main__":
    unittest.main()
