import re
from urllib.parse import urlsplit

from review_fixtures import paragraph, write_docx, write_word
from test_word_audits_web import SCOPE_QUOTE
from web_fixtures import BrowserCase


class WordAuditWorkflowWebTests(BrowserCase):
    def test_graph_tracks_the_actual_audit_across_missing_and_completed_evidence(self):
        self.assertIn('href="#word-audit-goal"', self.node(self.page("/"), "intake", "待提交"))
        scope = write_docx(
            self.root / "508-applicability-checklist.docx", paragraph("☐" + SCOPE_QUOTE),
        )
        self.upload(scope.name, scope.read_bytes())
        with self.post_form("/word-audits", {"goal": "核对 Word 表头。"}) as response:
            route = urlsplit(response.url).path
            waiting = response.read().decode("utf-8")
        self.assertIn('href="#word-audit-scope"', self.node(waiting, "scope", "已查证"))
        self.assertIn('href="#word-audit-next"', self.node(waiting, "table", "等待补充"))
        self.assertIn(route + "#word-audit-next", self.node(self.page("/"), "table", "等待补充"))

        word = write_word(self.root / "508-word-checklist.docx")
        self.upload(word.name, word.read_bytes())
        with self.post_form(route + "/resume", {}) as response:
            self.assertEqual(route, urlsplit(response.url).path)
            ready = response.read().decode("utf-8")
        self.assertIn('href="#word-audit-data_tables"', self.node(ready, "table", "已查证"))
        self.assertIn('href="#word-audit-decision"', self.node(ready, "decision", "待确认"))
        self.assertIn("Super Agent 行动", ready)

    def node(self, body, key, status):
        node = re.search(rf'<li[^>]*data-workflow-step="{key}"[^>]*>(.*?)</li>', body, re.DOTALL)
        self.assertIsNotNone(node, f"缺少流程节点 {key}")
        self.assertIn(status, node[1])
        return node[1]
