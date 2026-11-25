import re
from urllib.parse import urlsplit

from openkb.ingest.ledger import Ledger
from openkb.ingest.sqlite import open_delivery_store

from review_fixtures import TABLE_RULE, paragraph, section, write_docx, write_word
from test_word_audits_web import SCOPE_QUOTE
from web_fixtures import BrowserCase


RESTRICTED = "Only data tables that span multiple pages must repeat their header row."


class WordAuditEvidenceWebTests(BrowserCase):
    def import_version(self, path):
        return re.search(r'data-version-id="([^"]+)"', self.upload(path.name, path.read_bytes()))[1]

    def audit(self, sources):
        with self.post_form("/word-audits", {
            "goal": "非网页 Word 有一张单页表，两张表都没设置重复表头。", "source": sources,
        }) as response:
            return response.read().decode("utf-8")

    def test_conflicting_supported_rules_link_both_real_originals(self):
        baseline = write_word(self.root / "section508-word.docx")
        conflicting = write_word(self.root / "508-word-checklist.docx", table_quotes=(RESTRICTED,),
                                 prefix=paragraph("模拟冲突资料"))
        originals = [self.import_version(path) for path in (baseline, conflicting)]
        answer = self.audit(originals)
        self.assertIn("证据冲突 · 待核对", answer)
        self.assertIn("用户提供", answer)
        self.assertNotIn("单页和跨页数据表格均适用。", answer)
        links = re.findall(r'href="(/evidence/[a-f0-9]+)"', answer)
        self.assertEqual(len(links), 2)
        details = [self.page(link) for link in links]
        for version, quote in zip(originals, (TABLE_RULE, RESTRICTED), strict=True):
            self.assertTrue(any(version in detail and quote in detail for detail in details))

    def test_explicit_single_version_is_retained_but_two_versions_use_latest(self):
        baseline = write_word(self.root / "section508-word.docx")
        newer = write_word(self.root / "simulated-revision.docx", table_quotes=(RESTRICTED,))
        with open_delivery_store(self.settings.database) as store:
            ledger = Ledger(store)
            before = ledger.add(baseline, "word")
            after = ledger.add(newer, "word")
        answer = self.audit((before.id, after.id))
        self.assertIn(RESTRICTED, answer)
        self.assertIn(after.id, answer)
        self.assertIn("同一资料采用所选的最新版本", answer)
        self.assertNotIn("证据冲突 · 待核对", answer)
        self.assertIn(TABLE_RULE, self.audit((before.id,)))

    def test_other_format_and_rules_in_unrelated_section_do_not_cover_table_slot(self):
        scope = write_docx(self.root / "508-applicability-checklist.docx", paragraph("☐" + SCOPE_QUOTE))
        wrong = write_docx(self.root / "508-word-checklist.docx", section("PDF", TABLE_RULE))
        answer = self.audit((self.import_version(scope), self.import_version(wrong)))
        self.assertIn(SCOPE_QUOTE, answer)
        self.assertIn("数据表格 · 依据不足", answer)
        self.assertNotIn(TABLE_RULE, answer)



    def test_deliverable_checks_actual_table_flags_without_claiming_pagination(self):
        def table(flag):
            return f"<w:tbl><w:tr>{flag}<w:tc>{paragraph('Title')}</w:tc></w:tr></w:tbl>"

        declared_repeat = '<w:trPr><w:tblHeader/></w:trPr>'
        disabled_repeat = '<w:trPr><w:tblHeader w:val="0"/></w:trPr>'
        doc = write_docx(self.root / "delivery.docx", table(declared_repeat) + table(disabled_repeat))
        boundary = "OpenKBWordAuditFixture"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="goal"\r\n\r\n'
            "两张表均未设置重复表头。\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="deliverable"; filename="delivery.docx"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode() + doc.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        with self.post("/word-audits", body, f"multipart/form-data; boundary={boundary}") as response:
            answer = response.read().decode("utf-8")
        self.assertIn("委托目标（用户提供）", answer)
        self.assertIn("已核验 · delivery.docx", answer)
        self.assertIn("第 1 张正文表格：首行已设置重复表头。", answer)
        self.assertIn("第 2 张正文表格：首行未设置重复表头。", answer)
        self.assertIn("待核对 · 表格是否跨页", answer)
        self.assertNotIn("delivery.docx", self.page("/documents"))


    def test_narrower_rule_asks_only_for_unknown_page_span_and_resumes_same_audit(self):
        scope = write_docx(self.root / "508-applicability-checklist.docx", paragraph("☐" + SCOPE_QUOTE))
        restricted = write_word(self.root / "section508-word.docx", table_quotes=(RESTRICTED,))
        self.import_version(scope)
        self.import_version(restricted)
        with self.post_form("/word-audits", {"goal": "核对这份 Word 是否需要重复表头"}) as response:
            audit_path = urlsplit(response.url).path
            waiting = response.read().decode("utf-8")
        self.assertIn('data-audit-state="waiting"', waiting)
        self.assertIn("补充表格是否跨页", waiting)
        self.assertIn(RESTRICTED, waiting)
        self.assertIn(SCOPE_QUOTE, waiting)
        token = re.search(r'name="answer_snapshot" value="([^"]+)"', waiting)[1]
        with self.post_form(audit_path + "/resume", {
            "page_span": "multipage", "answer_snapshot": token,
        }) as response:
            self.assertEqual(urlsplit(response.url).path, audit_path)
            resumed = response.read().decode("utf-8")
        self.assertIn('data-audit-state="done"', resumed)
        self.assertIn("用户提供 · 表格跨页", resumed)
        self.assertIn("跨页表格适用", resumed)
        self.assertIn(RESTRICTED, resumed)
        self.assertNotIn('name="page_span"', resumed)

        with self.post_form("/word-audits", {"goal": "已知表格跨页，且没有重复表头，请核对。"}) as response:
            known = response.read().decode("utf-8")
        self.assertIn('data-audit-state="done"', known)
        self.assertIn("用户提供 · 表格跨页", known)
        self.assertNotIn('name="page_span"', known)

        for goal in (
            "如果表格跨页，需要怎么处理？",
            "不确定表格跨页，请检查。",
            "我还没核实表格跨页。",
        ):
            with self.subTest(goal=goal), self.post_form("/word-audits", {"goal": goal}) as response:
                waiting = response.read().decode("utf-8")
                self.assertIn('data-audit-state="waiting"', waiting)
                self.assertIn('name="page_span"', waiting)
                self.assertNotIn("用户提供 · 表格跨页", waiting)
