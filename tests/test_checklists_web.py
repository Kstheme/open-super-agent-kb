import re
from urllib.error import HTTPError
from urllib.parse import unquote, urlsplit
from urllib.request import urlopen

from review_fixtures import TABLE_RULE, paragraph, write_docx, write_word
from web_fixtures import BrowserCase


class ChecklistsWebTests(BrowserCase):
    def setUp(self):
        super().setUp()
        scope = write_docx(
            self.root / "508-applicability-checklist.docx",
            paragraph("☐2.4.1 Bypass Blocks (not required for non-web documents and software)"),
        )
        word = write_word(self.root / "section508-word.docx")
        self.original_word = word.read_bytes()
        self.sources = {}
        for path in (scope, word):
            page = self.upload(path.name, path.read_bytes())
            match = re.search(r'<li[^>]+data-version-id="([^"]+)"[^>]*>\s*<div[^>]*>\s*<h3>'
                              + re.escape(path.name) + r'</h3>', page, re.DOTALL)
            self.assertIsNotNone(match)
            self.sources[path.name] = match[1]

    def test_generates_four_items_and_downloads_retained_original(self):
        listing = self.page("/checklists")
        self.assertIn("生成清单", listing)
        for name, version_id in self.sources.items():
            self.assertIn(name, listing)
            self.assertIn(f'value="{version_id}"', listing)

        with self.post_form("/checklists/generate", {"source": list(self.sources.values())}) as response:
            artifact_path = urlsplit(response.url).path
            self.assertRegex(artifact_path, r"^/checklists/[a-f0-9]+$")
            result = response.read().decode("utf-8")
        self.assertEqual(
            re.findall(r'data-slot-id="([^"]+)"', result),
            ["scope", "headings", "data_tables", "image_descriptions"],
        )
        self.assertIn("版本 1", result)
        self.assertIn(artifact_path, self.page("/checklists"))
        self.assertIn("数据表格", self.page(artifact_path + "?version=1"))

        table = result.split('data-slot-id="data_tables"', 1)[1]
        evidence_path = re.search(r'href="(/evidence/[a-f0-9]+)"', table)[1]
        evidence = self.page(evidence_path)
        self.assertIn(TABLE_RULE, evidence)
        self.assertIn(self.sources["section508-word.docx"], evidence)
        self.assertIn("第 2 张表，第 1 行", evidence)
        with urlopen(self.address + evidence_path + "/original", timeout=5) as download:
            self.assertIn("section508-word.docx", unquote(download.headers["Content-Disposition"]))
            self.assertEqual(download.read(), self.original_word)

    def test_lists_a_persisted_paused_delivery_and_its_gaps(self):
        with self.post_form("/checklists/generate", {"source": [self.sources["section508-word.docx"]]}) as response:
            task_path = urlsplit(response.url).path
            self.assertRegex(task_path, r"^/checklists/tasks/[a-f0-9]+$")
            result = response.read().decode("utf-8")
        self.assertIn("交付已暂停", result)
        self.assertIn("适用范围", result)
        self.assertIn(task_path, self.page("/checklists"))
        self.assertIn("交付已暂停", self.page(task_path))

    def test_generation_requires_a_source_and_missing_evidence_is_not_found(self):
        with self.assertRaises(HTTPError) as caught:
            self.post_form("/checklists/generate", {})
        self.assertEqual(caught.exception.code, 400)
        with caught.exception as response:
            self.assertIn("请选择至少一个资料版本", response.read().decode("utf-8"))
        for path in ("/evidence/missing", "/evidence/missing/original"):
            with self.subTest(path=path), self.assertRaises(HTTPError) as missing:
                self.page(path)
            with missing.exception as response:
                self.assertEqual(response.code, 404)
