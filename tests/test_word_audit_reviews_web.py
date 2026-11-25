import re
from urllib.error import HTTPError
from urllib.parse import urlsplit

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
