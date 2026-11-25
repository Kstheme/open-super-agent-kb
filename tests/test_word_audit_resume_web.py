import re
from unittest.mock import patch
from urllib.parse import urlsplit

from openkb.server import word_audit

from review_fixtures import TABLE_RULE, paragraph, write_docx, write_word
from test_word_audits_web import SCOPE_QUOTE
from web_fixtures import BrowserCase


GOAL = "请审阅非网页 Word 的适用范围和数据表格。"


class WordAuditResumeWebTests(BrowserCase):
    def import_rule(self, path):
        return re.search(r'data-version-id="([^"]+)"', self.upload(path.name, path.read_bytes()))[1]

    def scope_rule(self):
        return self.import_rule(write_docx(
            self.root / "508-applicability-checklist.docx", paragraph("☐" + SCOPE_QUOTE),
        ))

    def table_rule(self):
        return self.import_rule(write_word(self.root / "508-word-checklist.docx"))

    def submit(self, route, fields):
        with self.post_form(route, fields) as response:
            return urlsplit(response.url).path, response.read().decode("utf-8")

    def start(self, sources=()):
        return self.submit("/word-audits", {"goal": GOAL, "source": sources})

    def resume(self, route):
        return self.submit(route + "/resume", {})

    def test_imported_evidence_completes_the_same_audit_without_replacing_completed_work(self):
        scope = self.scope_rule()
        route, partial = self.start((scope,))
        self.assertIn("等待补充资料", partial)
        self.assertIn("缺少支持重复表头判断的 Word 检查表", partial)
        self.assertIn(SCOPE_QUOTE, partial)
        scope_link = re.search(r'href="(/evidence/[a-f0-9]+)"', partial)[1]
        self.assertIn(f'action="{route}/resume"', partial)

        word = self.table_rule()
        resumed_route, complete = self.submit(route + "/resume", {"source": word})
        self.assertEqual(resumed_route, route)
        self.assertIn(scope_link, complete)
        self.assertIn(TABLE_RULE, complete)
        self.assertIn("已查证适用范围和表头要求", complete)
        self.assertNotIn(f'action="{route}/resume"', complete)

    def test_empty_workspace_accepts_a_goal_and_explains_both_missing_sources(self):
        self.assertIn('action="/word-audits"', self.page("/"))
        route, answer = self.start()
        self.assertIn(GOAL, answer)
        self.assertIn("缺少说明非网页文档适用范围的检查表", answer)
        self.assertIn("缺少支持重复表头判断的 Word 检查表", answer)
        self.assertIn(f'action="{route}/resume"', answer)

    def test_tool_error_and_budget_pause_have_distinct_retryable_statuses(self):
        self.scope_rule()
        self.table_rule()
        identify = word_audit.audit_coverages

        def fail_table(slot, *args, **kwargs):
            if slot.id == "data_tables":
                raise OSError("private fixture error")
            return identify(slot, *args, **kwargs)

        with patch.object(word_audit, "audit_coverages", side_effect=fail_table):
            failed_route, failed = self.start()
        self.assertIn("查证工具失败", failed)
        self.assertIn(SCOPE_QUOTE, failed)
        self.assertNotIn("private fixture error", failed)
        self.assertIn(f'action="{failed_route}/resume"', failed)
        self.assertIn(TABLE_RULE, self.resume(failed_route)[1])

        with patch.object(word_audit, "MAX_STEPS", 1):
            paused_route, paused = self.start()
        self.assertIn("预算已用完", paused)
        self.assertIn(SCOPE_QUOTE, paused)
        self.assertNotIn("查证工具失败", paused)
        self.assertIn(f'action="{paused_route}/resume"', paused)
        self.assertIn(TABLE_RULE, self.resume(paused_route)[1])
