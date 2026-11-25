import re
from urllib.error import HTTPError

from web_fixtures import BrowserCase


class DocumentsWebTests(BrowserCase):
    def test_unavailable_ledger_explains_how_to_recover(self):
        self.settings.database.write_bytes(b"not a SQLite ledger")
        for path in ("/documents", "/documents/import"):
            with self.subTest(path=path), self.assertRaises(HTTPError) as caught:
                if path == "/documents":
                    self.page(path)
                else:
                    self.upload("rules.md", b"# Rules")
            self.assertEqual(caught.exception.code, 503)
            html = caught.exception.read().decode("utf-8")
            self.assertIn("本地账本暂不可用", html)
            self.assertIn("检查数据目录", html)
            self.assertIn('href="/status"', html)

    def test_import_preserves_the_original_name_and_separates_same_name_documents(self):
        empty = self.page("/documents")
        self.assertIn("暂无资料", empty)
        self.assertIn('name="source"', empty)

        result = self.upload("团队规则.md", "# 第一份规则".encode())
        self.assertIn("导入成功", result)
        self.assertIn("团队规则.md", result)
        self.upload("团队规则.md", "# 同名的另一份规则".encode())

        page = self.page("/documents")
        self.assertEqual(page.count("<h3>团队规则.md</h3>"), 2)
        ids = re.findall(r'data-(?:document|version)-id="([^"]+)"', page)
        self.assertEqual(len(set(ids)), 4)
        self.assertIn("已入库", page)

    def test_invalid_upload_shows_the_reason_and_adds_no_version(self):
        for name, reason in (("../private.md", "文件名"), ("no-format.txt", "不支持")):
            with self.subTest(name=name), self.assertRaises(HTTPError) as caught:
                self.upload(name, b"# Contents")
            self.assertEqual(caught.exception.code, 400)
            html = caught.exception.read().decode("utf-8")
            self.assertIn("导入失败", html)
            self.assertIn(reason, html)
        self.assertIn("暂无资料", self.page("/documents"))
