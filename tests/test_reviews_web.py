from html import unescape
import json
import re
from unittest.mock import patch
from urllib.parse import urlsplit
from urllib.request import urlopen

from openkb.agent.model import EndpointError, ModelClient

from review_fixtures import TABLE_RULE, paragraph, write_docx, write_word
from web_fixtures import BrowserCase


REVISED_RULE = "Only data tables that span multiple pages must repeat their header row."
REVISED_CHECK = "跨页数据表格应设置重复表头。"


def reply(client, method, path, payload, timeout):
    if (method, path) == ("GET", "models"):
        return {"data": [{"id": client.config.model_id}]}
    assert (method, path) == ("POST", "chat/completions")
    inputs = json.loads(payload["messages"][0]["content"].splitlines()[-1])
    candidate, = inputs["new_candidates"]
    return {
        "choices": [{"message": {"content": json.dumps({
            "judgment": "material",
            "reason": "新版将适用范围缩小至跨页表格。",
            "revised_text": REVISED_CHECK,
            "revised_method": "检查跨页表格的重复表头设置。",
            "evidence": [{"block_id": candidate["block_id"], "quote": REVISED_RULE}],
        })}}],
        "usage": {"prompt_tokens": 40, "completion_tokens": 10},
    }


class ReviewsWebTests(BrowserCase):
    def setUp(self):
        super().setUp()
        self.settings.config.write_text(
            '[model]\nbase_url = "http://fixture.invalid/v1"\n'
            'api_key = "fixture-model-secret"\nmodel_id = "fixture-model"\n'
            '[prices]\nversion = "fixture-prices"\n',
            encoding="utf-8",
        )
        scope = write_docx(
            self.root / "508-applicability-checklist.docx",
            paragraph("☐2.4.1 Bypass Blocks (not required for non-web documents and software)"),
        )
        self.word = write_word(self.root / "section508-word.docx")
        self.revised = write_word(self.root / "section508-simulated.docx", (REVISED_RULE,))
        sources = []
        for path in (scope, self.word):
            page = self.upload(path.name, path.read_bytes())
            version = re.search(
                r'<li[^>]+data-version-id="([^"]+)"[^>]*>\s*<div[^>]*>\s*<h3>'
                + re.escape(path.name) + r"</h3>", page,
            )
            self.assertIsNotNone(version)
            sources.append(version[1])
        self.original_version = sources[1]
        with self.post_form("/checklists/generate", {"source": sources}) as response:
            self.artifact_path = urlsplit(response.url).path
        self.artifact_id = self.artifact_path.rsplit("/", 1)[1]

    def submit_revision(self):
        boundary = "OpenKBReviewFormBoundary"
        fields = {"artifact_id": self.artifact_id, "before_version": self.original_version}
        body = "".join(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n"
            for name, value in fields.items()
        ).encode()
        body += (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="source"; filename="{self.revised.name}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode() + self.revised.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        with self.post("/reviews/start", body, f"multipart/form-data; boundary={boundary}") as response:
            review_path = urlsplit(response.url).path
            self.assertRegex(review_path, r"^/reviews/[a-f0-9]+$")
            return review_path, response.read().decode("utf-8")

    def table_evidence(self, checklist):
        table = checklist.split('data-slot-id="data_tables"', 1)[1]
        return unescape(re.search(r'href="([^"]+evidence=[a-f0-9]+)#evidence"', table)[1])

    def test_accepts_revision_and_retains_both_evidence_versions_offline(self):
        listing = self.page(f"/reviews?artifact_id={self.artifact_id}")
        self.assertIn("资料版本", listing)
        self.assertIn(f'value="{self.original_version}"', listing)
        self.assertIn('name="source"', listing)
        with patch.object(ModelClient, "_call", autospec=True, side_effect=reply) as calls:
            review_path, result = self.submit_revision()
        self.assertEqual(calls.call_count, 2)
        self.assertEqual(re.findall(r'data-item-id="([^"]+)"', result), ["data_tables"])
        for content in ("旧规则", "修改建议", "新版证据", REVISED_CHECK):
            self.assertIn(content, result)
        proposal = re.search(r'action="([^"]+/accept)"', result)[1]
        with self.post_form(proposal, {}) as response:
            accepted = response.read().decode("utf-8")
        self.assertIn("已接受", accepted)
        self.assertIn(f'{self.artifact_path}?version=2', accepted)
        self.assertIn(review_path, self.page("/reviews"))

        old = self.page(self.artifact_path + "?version=1")
        new = self.page(self.artifact_path + "?version=2")
        self.assertNotIn(REVISED_CHECK, old)
        self.assertIn(REVISED_CHECK, new)
        old_items = re.findall(r'<li class="panel" data-slot-id="([^"]+)">(.*?)\s*</ul>\s*</li>', old, re.S)
        new_items = re.findall(r'<li class="panel" data-slot-id="([^"]+)">(.*?)\s*</ul>\s*</li>', new, re.S)
        self.assertGreater(len(old_items), 1)
        self.assertEqual(
            [(slot, body.replace("?version=1", "?version=2"))
             for slot, body in old_items if slot != "data_tables"],
            [item for item in new_items if item[0] != "data_tables"],
        )
        offline = EndpointError("connection failed: fixture")
        with patch.object(ModelClient, "_call", autospec=True, side_effect=offline) as calls:
            self.assertIn("端点不可达", self.page("/status"))
            self.assertEqual(calls.call_count, 1)
            documents = self.page("/documents")
            self.assertIn(self.word.name, documents)
            self.assertIn(self.revised.name, documents)
            self.assertIn("已接受", self.page(review_path))
            for version, quote, source in ((1, TABLE_RULE, self.word), (2, REVISED_RULE, self.revised)):
                checklist = self.page(f"{self.artifact_path}?version={version}")
                panel = self.page(self.table_evidence(checklist)).split('id="evidence"', 1)[1]
                self.assertIn(quote, panel)
                download = re.search(r'href="(/evidence/[a-f0-9]+/original)"', panel)[1]
                with urlopen(self.address + download, timeout=5) as response:
                    self.assertIn(source.name, response.headers["Content-Disposition"])
                    self.assertEqual(response.read(), source.read_bytes())
        calls.assert_called_once()

    def test_rejection_persists_reason_without_a_new_version(self):
        with patch.object(ModelClient, "_call", autospec=True, side_effect=reply):
            review_path, result = self.submit_revision()
        proposal = re.search(r'action="([^"]+/reject)"', result)[1]
        with self.post_form(proposal, {"reason": "保留原规则"}) as response:
            self.assertIn("已拒绝", response.read().decode("utf-8"))
        self.assertIn("保留原规则", self.page(review_path))
        self.assertIn("版本 1", self.page(self.artifact_path))
        self.assertNotIn("版本 2", self.page(self.artifact_path))

    def test_model_failure_keeps_the_review_report_and_previous_evidence_readable(self):
        connection_error = EndpointError("fixture endpoint offline: fixture-model-secret")
        with patch.object(ModelClient, "_call", autospec=True, side_effect=connection_error) as calls:
            review_path, failure = self.submit_revision()
            self.assertIn("复核失败", failure)
            self.assertIn("模型配置或连接检查失败", failure)
            self.assertIn('href="/status"', failure)
            self.assertIn(review_path, self.page("/reviews"))
            self.assertIn("复核失败", self.page(review_path))
            self.assertIn(TABLE_RULE, self.page(self.table_evidence(self.page(self.artifact_path))))
        calls.assert_called_once()
        self.assertNotIn("fixture-model-secret", failure)
