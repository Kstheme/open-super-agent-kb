import re
from urllib.error import HTTPError
from urllib.parse import urlsplit

from openkb.ingest.sqlite import open_delivery_store

from review_fixtures import TABLE_RULE, paragraph, write_docx, write_word
from web_fixtures import BrowserCase


SCOPE_QUOTE = "2.4.1 Bypass Blocks (not required for non-web documents and software)"


class WordAuditDecisionWebTests(BrowserCase):
    def audit(self) -> str:
        paths = (
            write_docx(
                self.root / "508-applicability-checklist.docx",
                paragraph("☐" + SCOPE_QUOTE),
            ),
            write_word(self.root / "508-word-checklist.docx"),
        )
        for path in paths:
            self.upload(path.name, path.read_bytes())
        with self.post_form("/word-audits", {
            "goal": "帮我核对非网页 Word 的适用范围和重复表头。",
        }) as response:
            return urlsplit(response.url).path

    def test_human_decisions_keep_their_original_answers_and_evidence(self):
        audit = self.audit()
        answer = self.page(audit)
        action = audit + "/decisions"
        self.assertIn(f'action="{action}"', answer)
        self.assertIn("审阅建议", answer)
        self.assertIn("记录人工决定", answer)
        self.assertIn("尚未记录人工决定", self.page("/word-audit-decisions"))

        expected = (
            ("release", "放行", "已核对可交付"),
            ("return", "退回修改", "交付件还有一处表头问题"),
        )
        destinations = []
        for choice, label, reason in expected:
            with self.subTest(choice=choice):
                with self.post_form(action, {"choice": choice, "reason": reason}) as response:
                    path = urlsplit(response.url).path
                    self.assertRegex(path, r"^/word-audit-decisions/[a-f0-9]+$")
                    destinations.append(path)
                decision = self.page(path)
                self.assertIn("审阅建议", decision)
                self.assertIn("人工决定", decision)
                self.assertIn(label, decision)
                self.assertIn(reason, decision)

        # A later continuation can replace the current audit snapshot.
        audit_id = audit.rsplit("/", 1)[-1]
        with open_delivery_store(self.settings.database) as store:
            current = store.word_audit(audit_id)
            current["goal"] = "重新审阅另一份文件"
            current["covered"] = []
            store.save_word_audit(current)
        self.assertIn("重新审阅另一份文件", self.page(audit))

        self.assertIn('href="/word-audit-decisions"', self.page("/"))
        history = self.page("/word-audit-decisions")
        for path, (_, label, reason) in zip(destinations, expected, strict=True):
            self.assertIn(f'href="{path}"', history)
            self.assertIn(reason, history)
            decision = self.page(path)
            self.assertIn(label, decision)
            self.assertIn("帮我核对非网页 Word 的适用范围和重复表头。", decision)
            self.assertNotIn("重新审阅另一份文件", decision)
            for text in (SCOPE_QUOTE, TABLE_RULE, "声明", "证据", "原文块", "资料版本", "第 2 张表，第 1 行"):
                self.assertIn(text, decision)
            evidence_paths = re.findall(r'href="(/evidence/[a-f0-9]+)"', decision)
            self.assertEqual(len(evidence_paths), 2)
            for evidence in evidence_paths:
                self.assertIn("下载此版本原件", self.page(evidence))

    def test_a_changed_answer_requires_review_before_recording_a_decision(self):
        audit = self.audit()
        answer_snapshot = re.search(r'name="answer_snapshot" value="([a-f0-9]+)"', self.page(audit))[1]
        audit_id = audit.rsplit("/", 1)[-1]
        with open_delivery_store(self.settings.database) as store:
            current = store.word_audit(audit_id)
            current["goal"] = "重新审阅另一份文件"
            store.save_word_audit(current)
        with self.assertRaises(HTTPError) as caught:
            self.post_form(audit + "/decisions", {
                "choice": "release", "reason": "核对的是原答案", "answer_snapshot": answer_snapshot,
            })
        with caught.exception as response:
            self.assertEqual(response.code, 400)
            self.assertIn("审阅建议已更新，请核对后再保存决定。", response.read().decode("utf-8"))
        self.assertIn("尚未记录人工决定", self.page("/word-audit-decisions"))

    def test_recording_a_decision_requires_a_choice_and_reason(self):
        audit = self.audit()
        for fields in (
            {"choice": "release", "reason": "  "},
            {"choice": "invalid", "reason": "无法识别的决定"},
        ):
            with self.subTest(fields=fields):
                with self.assertRaises(HTTPError) as caught:
                    self.post_form(audit + "/decisions", fields)
                self.assertEqual(caught.exception.code, 400)
        self.assertIn("尚未记录人工决定", self.page("/word-audit-decisions"))
