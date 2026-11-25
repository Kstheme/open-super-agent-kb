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
        reviews = tuple(dict.fromkeys(re.findall(r'href="(/word-audit-reviews/[^"]+)"', workspace)))
        self.assertEqual(len(set(reviews)), 2)
        for decision_path in decisions:
            self.assertTrue(any(f'href="{decision_path}"' in self.page(review) for review in reviews))
            self.assertIn("按原规则退回修改。", self.page(decision_path))

        review = self.page(reviews[0])
        self.assertIn('<a href="#decision-impact-title" aria-current="step">', review)
        self.assertRegex(review, r'href="/word-audit-decisions/[^"]+#word-audit-data_tables"')
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

    def test_successive_revisions_use_each_decisions_latest_acknowledged_basis(self):
        version, decisions = self.recorded_decisions()
        history = "/word-audit-reviews"
        revision = write_word(self.root / "narrowed.docx", (NEW_RULE,))
        self.import_revision(version, revision)
        second_reviews = re.findall(r'href="(/word-audit-reviews/[^"]+)"', self.page(history))
        acknowledged = next(link for link in second_reviews if decisions[0] in self.page(link))
        with self.post_form(acknowledged + "/confirm", {
            "choice": "adjust", "adjusted_choice": "release", "reason": "采用新版跨页规则。",
        }) as response:
            self.assertIn("已人工确认", response.read().decode("utf-8"))

        notes_only = write_word(
            self.root / "notes.docx", (NEW_RULE,), heading_rule="Unrelated heading notes.",
        )
        self.import_revision(version, notes_only)
        third_reviews = re.findall(r'href="(/word-audit-reviews/[^"]+)"', self.page(history))
        new_reviews = set(third_reviews) - set(second_reviews)
        self.assertEqual(len(new_reviews), 1)
        self.assertIn(decisions[1], self.page(new_reviews.pop()))

        broad_again = write_word(
            self.root / "expanded.docx", heading_rule="Another unrelated heading note.",
        )
        self.import_revision(version, broad_again)
        fourth_reviews = re.findall(r'href="(/word-audit-reviews/[^"]+)"', self.page(history))
        new_reviews = set(fourth_reviews) - set(third_reviews)
        self.assertEqual(len(new_reviews), 1)
        current = self.page(new_reviews.pop())
        self.assertIn(decisions[0], current)
        self.assertIn(NEW_RULE, current)
        self.assertIn(TABLE_RULE, current)
        self.assertIn("按原规则退回修改。", self.page(decisions[0]))


    def test_keep_uses_latest_human_result_and_older_review_cannot_override_newer_confirmation(self):
        version, decisions = self.recorded_decisions()
        seen = set()
        def import_and_get(rule, heading):
            revision = write_word(self.root / f"revision-{heading}.docx", (rule,), heading_rule=heading)
            self.import_revision(version, revision)
            recent = set(re.findall(r'href="(/word-audit-reviews/[^"]+)"', self.page("/word-audit-reviews")))
            new = recent - seen
            seen.update(new)
            return next(link for link in new if decisions[0] in self.page(link))

        narrowed = import_and_get(NEW_RULE, "Heading 2")
        with self.post_form(narrowed + "/confirm", {
            "choice": "adjust", "adjusted_choice": "release", "reason": "人工改为放行。",
        }):
            pass
        broadened = import_and_get(TABLE_RULE, "Heading 3")
        with self.post_form(broadened + "/confirm", {
            "choice": "keep", "reason": "保留当前放行。",
        }) as response:
            self.assertIn("保留原决定</strong> · 放行", response.read().decode("utf-8"))
        decision = self.page(decisions[0])
        self.assertIn("当前人工决定</strong> · 放行", decision)
        self.assertIn("按原规则退回修改。", decision)

        older = import_and_get(NEW_RULE, "Heading 4")
        newer = import_and_get("Table captions should be informative.", "Heading 5")
        with self.post_form(newer + "/confirm", {"choice": "keep", "reason": "继续放行。"}):
            pass
        with self.assertRaises(HTTPError) as stale:
            self.post_form(older + "/confirm", {
                "choice": "adjust", "adjusted_choice": "return", "reason": "过期确认。",
            })
        with stale.exception as failure:
            self.assertEqual(failure.code, 409)
            self.assertIn("更新的复核已确认", failure.read().decode("utf-8"))
        self.assertIn("当前人工决定</strong> · 放行", self.page(decisions[0]))

    def test_first_decision_requires_a_current_cited_document(self):
        policy = write_docx(self.root / "508-applicability-checklist.docx", paragraph("☐" + SCOPE))
        source = write_word(self.root / "508-word-checklist.docx")
        self.upload(policy.name, policy.read_bytes())
        imported = self.upload(source.name, source.read_bytes())
        version = re.search(r'data-version-id="([^"]+)"', imported)[1]
        with self.post_form("/word-audits", {"goal": "核对 Word 表头要求"}) as response:
            audit = urlsplit(response.url).path
        form = self.page(audit)
        token = re.search(r'name="answer_snapshot" value="([^"]+)"', form)[1]
        revision = write_word(self.root / "before-decision.docx", (NEW_RULE,))
        self.import_revision(version, revision)
        with self.assertRaises(HTTPError) as stale:
            self.post_form(audit + "/decisions", {
                "choice": "return", "reason": "根据已过期的原规则。", "answer_snapshot": token,
            })
        with stale.exception as failure:
            self.assertEqual(failure.code, 400)
            self.assertIn("引用的资料已有新版", failure.read().decode("utf-8"))
        self.assertNotIn("根据已过期的原规则。", self.page("/word-audit-decisions"))

    def test_acknowledged_missing_basis_does_not_reopen_on_unrelated_edit(self):
        version, decisions = self.recorded_decisions()
        removed = write_word(self.root / "basis-removed.docx", ("Table captions are descriptive.",))
        self.import_revision(version, removed)
        inbox = "/word-audit-reviews"
        second = set(re.findall(r'href="(/word-audit-reviews/[^"]+)"', self.page(inbox)))
        acknowledged = next(link for link in second if decisions[0] in self.page(link))
        with self.post_form(acknowledged + "/confirm", {"choice": "keep", "reason": "已知依据缺失。"}):
            pass
        notes = write_word(
            self.root / "still-removed.docx", ("Table captions are descriptive.",), heading_rule="Unrelated notes.",
        )
        self.import_revision(version, notes)
        third = set(re.findall(r'href="(/word-audit-reviews/[^"]+)"', self.page(inbox)))
        new = third - second
        self.assertEqual(len(new), 1)
        self.assertIn(decisions[1], self.page(new.pop()))
        restored = write_word(self.root / "restored.docx", heading_rule="New notes on headings.")
        self.import_revision(version, restored)
        fourth = set(re.findall(r'href="(/word-audit-reviews/[^"]+)"', self.page(inbox)))
        new = fourth - third
        self.assertEqual(len(new), 1)
        self.assertIn(decisions[0], self.page(new.pop()))
