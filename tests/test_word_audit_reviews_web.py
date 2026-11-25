import re
from urllib.error import HTTPError
from urllib.parse import urlsplit
from unittest.mock import patch

from review_fixtures import TABLE_RULE, paragraph, write_docx, write_word
from web_fixtures import BrowserCase


SCOPE = "2.4.1 Bypass Blocks (not required for non-web documents and software)"
NEW_RULE = "Only data tables that span multiple pages must repeat their header row."


class WordAuditReviewWebTests(BrowserCase):
    def recorded_decisions(self):
        policy = write_docx(
            self.root / "508-applicability-checklist.docx", paragraph("☐" + SCOPE),
        )
        word = write_word(self.root / "508-word-checklist.docx")
        self.upload(policy.name, policy.read_bytes())
        imported = self.upload(word.name, word.read_bytes())
        version = re.search(r'data-version-id="([^"]+)"', imported)[1]

        decisions = []
        for goal in ("核对交付件甲的表头要求", "核对交付件乙的表头要求"):
            with self.post_form("/word-audits", {"goal": goal}) as response:
                audit = urlsplit(response.url).path
            with self.post_form(audit + "/decisions", {
                "choice": "return", "reason": "按原规则退回修改。",
            }) as response:
                decisions.append(urlsplit(response.url).path)
        return version, decisions

    def import_revision(self, version, path):
        boundary = "OpenKBReviewTestBoundary"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="previous_version"\r\n\r\n'
            f"{version}\r\n"
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="source"; filename="{path.name}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        with self.post("/documents/import", body, f"multipart/form-data; boundary={boundary}") as response:
            self.assertIn("/documents?imported=", response.url)
            return response.read().decode("utf-8")

    def test_revision_finds_each_affected_decision_and_preserves_human_history(self):
        version, decisions = self.recorded_decisions()
        revision = write_word(self.root / "scope-revision.docx", (NEW_RULE,))
        self.assertIn("导入新版", self.page("/documents"))
        self.assertNotIn("/word-audit-reviews/", self.page("/"))
        self.import_revision(version, revision)

        workspace = self.page("/")
        self.assertIn("待人工确认", workspace)
        reviews = re.findall(r'href="(/word-audit-reviews/[^"]+)"', workspace)
        self.assertEqual(len(set(reviews)), 2)
        for decision_path in decisions:
            self.assertTrue(any(f'href="{decision_path}"' in self.page(review) for review in reviews))
            self.assertIn("按原规则退回修改。", self.page(decision_path))

        review = self.page(reviews[0])
        for text in (TABLE_RULE, NEW_RULE, "只有跨页", "受影响声明", "人工决定", "第 2 张表，第 1 行"):
            self.assertIn(text, review)
        evidence = re.findall(r'href="(/evidence/[a-f0-9]+)"', review)
        self.assertEqual(len(set(evidence)), 2)
        self.assertTrue(any(NEW_RULE in self.page(path) for path in evidence))

        with self.post_form(reviews[0] + "/confirm", {
            "choice": "adjust", "adjusted_choice": "release", "reason": "单页表可继续交付。",
        }) as response:
            confirmed = response.read().decode("utf-8")
        self.assertIn("单页表可继续交付。", confirmed)
        self.assertIn("按原规则退回修改。", confirmed)
        self.assertIn("放行", confirmed)
        self.assertIn(TABLE_RULE, confirmed)
        self.assertIn(NEW_RULE, confirmed)
        with self.assertRaises(HTTPError) as second:
            self.post_form(reviews[0] + "/confirm", {"choice": "keep", "reason": "重复确认"})
        with second.exception as response:
            self.assertEqual(response.code, 409)

        self.import_revision(version, revision)
        after = self.page("/")
        self.assertEqual(len(set(re.findall(r'href="(/word-audit-reviews/[^"]+)"', after))), 1)
        self.assertIn("待人工确认", self.page(reviews[1]))


    def test_equivalent_wording_records_review_without_waiting_for_human(self):
        version, decisions = self.recorded_decisions()
        revision = write_word(self.root / "clarification.docx", ("Repeat the header row for each table.",))
        self.import_revision(version, revision)
        self.assertNotIn("待人工确认", self.page("/"))
        inbox = self.page("/word-audit-reviews")
        reviews = re.findall(r'href="(/word-audit-reviews/[^"]+)"', inbox)
        self.assertEqual(len(reviews), 2)
        self.assertEqual(inbox.count("已复核"), 2)
        for review in reviews:
            html = self.page(review)
            self.assertIn("原决定继续有效", html)
            self.assertIn("Repeat the header row for each table.", html)
            self.assertNotIn("保存人工确认", html)
        self.assertIn("按原规则退回修改。", self.page(decisions[0]))

    def test_unrelated_edit_creates_no_review(self):
        version, _ = self.recorded_decisions()
        revision = write_word(self.root / "other-section.docx", heading_rule="Another headings description.")
        self.import_revision(version, revision)
        self.assertIn("暂无决定复核", self.page("/word-audit-reviews"))

    def test_missing_basis_and_conflict_are_pending_with_truthful_links(self):
        version, _ = self.recorded_decisions()
        missing = write_word(self.root / "missing.docx", ("Table captions are descriptive.",))
        self.import_revision(version, missing)
        inbox = self.page("/word-audit-reviews")
        reviews = re.findall(r'href="(/word-audit-reviews/[^"]+)"', inbox)
        self.assertEqual(len(reviews), 2)
        for review in reviews:
            html = self.page(review)
            self.assertIn("新版缺少支撑原结论的依据", html)
            self.assertNotIn('打开新版原文与原件', html)

        conflict = write_word(self.root / "conflict.docx", (TABLE_RULE, NEW_RULE))
        self.import_revision(version, conflict)
        self.import_revision(version, conflict)
        inbox = self.page("/word-audit-reviews")
        reviews = re.findall(r'href="(/word-audit-reviews/[^"]+)"', inbox)
        self.assertEqual(len(reviews), 4)
        conflict_page = self.page(reviews[0])
        self.assertIn("新版依据相互冲突", conflict_page)
        evidence = re.findall(r'href="(/evidence/[a-f0-9]+)"', conflict_page)
        self.assertEqual(len(set(evidence)), 3)
        self.assertTrue(any(NEW_RULE in self.page(link) for link in evidence))
        self.assertTrue(any(TABLE_RULE in self.page(link) for link in evidence))

    def test_failed_review_is_readable_and_retryable_without_a_second_item(self):
        version, decisions = self.recorded_decisions()
        revision = write_word(self.root / "retry.docx", (NEW_RULE,))
        with patch("openkb.server.word_audit_reviews.trace_blocks", side_effect=OSError):
            self.import_revision(version, revision)
            self.assertIn("复核失败", self.page("/"))
        inbox = self.page("/word-audit-reviews")
        reviews = re.findall(r'href="(/word-audit-reviews/[^"]+)"', inbox)
        self.assertEqual(len(reviews), 2)
        self.assertIn("重试复核", self.page(reviews[0]))
        self.assertIn("按原规则退回修改。", self.page(decisions[0]))
        with self.post_form(reviews[0] + "/retry", {}) as response:
            self.assertIn("待人工确认", response.read().decode("utf-8"))
        self.assertEqual(self.page("/word-audit-reviews").count('href="/word-audit-reviews/'), 2)
