import re
from urllib.parse import urlsplit

from review_fixtures import TABLE_RULE, paragraph, write_docx, write_word
from web_fixtures import BrowserCase


SCOPE_QUOTE = "2.4.1 Bypass Blocks (not required for non-web documents and software)"


class WordAuditWebTests(BrowserCase):
    def import_version(self, path):
        page = self.upload(path.name, path.read_bytes())
        return re.search(r'data-version-id="([^"]+)"', page)[1]

    def test_homepage_word_audit_traces_both_answers_to_selected_originals(self):
        scope = write_docx(
            self.root / "508-applicability-checklist.docx",
            paragraph("☐" + SCOPE_QUOTE),
        )
        word = write_word(self.root / "508-word-checklist.docx")
        sources = (self.import_version(scope), self.import_version(word))
        home = self.page("/")
        self.assertIn('action="/word-audits"', home)
        self.assertIn('name="goal"', home)
        for version in sources:
            self.assertIn(f'value="{version}"', home)

        prompts = (
            "这份非网页 Word 有一张单页表和一张跨页表。我该如何检查重复表头和 Bypass Blocks？",
            "帮我审阅非网页 Word 的无障碍交付要求，确认跳过区块和数据表格表头规则。",
        )
        for goal in prompts:
            with self.subTest(goal=goal):
                with self.post_form("/word-audits", {"goal": goal, "source": sources}) as response:
                    location = urlsplit(response.url).path
                    self.assertRegex(location, r"^/word-audits/[a-f0-9]+$")
                answer = self.page(location)
                self.assertIn(goal, answer)
                self.assertIn("不要求 2.4.1 Bypass Blocks", answer)
                self.assertIn("单页", answer)
                self.assertIn("跨页", answer)
                self.assertIn("重复表头", answer)
                for label in ("声明", "证据", "原文块", "资料版本"):
                    self.assertIn(label, answer)
                for slot, quote, version, filename, position in (
                    ("scope", SCOPE_QUOTE, sources[0], scope.name, "第 1 段"),
                    ("data_tables", TABLE_RULE, sources[1], word.name, "第 2 张表，第 1 行"),
                ):
                    item = re.search(rf'<li data-slot-id="{slot}">(.+?)</li>', answer, re.DOTALL)[1]
                    self.assertIn(quote, item)
                    self.assertIn(version, item)
                    self.assertIn(position, item)
                    self.assertRegex(item, r'data-claim-id="[a-f0-9]+"')
                    evidence = re.search(r'href="(/evidence/[a-f0-9]+)"', item)[1]
                    detail = self.page(evidence)
                    for proof in (quote, version, filename, position):
                        self.assertIn(proof, detail)
                self.assertNotIn(location, self.page("/checklists"))

    def test_selected_source_with_the_wrong_content_cannot_support_the_table_answer(self):
        scope = write_docx(
            self.root / "508-applicability-checklist.docx", paragraph("☐" + SCOPE_QUOTE),
        )
        lookalike = write_word(self.root / "section508-word.docx", table_quotes=())
        word = write_word(self.root / "508-word-checklist.docx")
        scope_id = self.import_version(scope)
        unsupported_id = self.import_version(lookalike)
        self.import_version(word)
        with self.post_form("/word-audits", {
            "goal": "为非网页 Word 核对适用范围和数据表格。", "source": (scope_id, unsupported_id),
        }) as response:
            answer = response.read().decode("utf-8")
        self.assertIn(SCOPE_QUOTE, answer)
        self.assertIn("依据不足", answer)
        self.assertNotIn(TABLE_RULE, answer)
