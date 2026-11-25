import re
from urllib.error import HTTPError

from web_fixtures import BrowserCase


class DocumentsWebTests(BrowserCase):
    def test_import_preserves_the_original_name_and_separates_same_name_documents(self):
        empty = self.page("/documents")
        self.assertIn("暂无资料", empty)
        self.assertIn('name="source"', empty)

        result = self.upload("团队规则.md", "# 第一份规则".encode())
        self.assertIn("导入成功", result)
        self.assertIn("团队规则.md", result)
        self.upload("团队规则.md", "# 同名的另一份规则".encode())

        page = self.page("/documents")
        self.assertEqual(page.count("团队规则.md"), 2)
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
