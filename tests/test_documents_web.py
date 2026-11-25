from pathlib import Path
import re
import socket
from tempfile import TemporaryDirectory
from threading import Thread
from time import monotonic, sleep
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from uvicorn import Config, Server

from openkb.server.runtime import AppSettings
from openkb.server.web import create_app


class DocumentsWebTests(unittest.TestCase):
    def setUp(self):
        workspace = TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        app = create_app(AppSettings.from_paths(Path(workspace.name)))
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        self.addCleanup(listener.close)
        self.address = f"http://127.0.0.1:{listener.getsockname()[1]}"
        server = Server(Config(app, log_config=None, access_log=False))
        worker = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        worker.start()

        def stop():
            server.should_exit = True
            worker.join(timeout=5)

        self.addCleanup(stop)
        deadline = monotonic() + 5
        while not server.started and worker.is_alive() and monotonic() < deadline:
            sleep(0.01)
        self.assertTrue(server.started)

    def page(self, path):
        with urlopen(self.address + path, timeout=5) as response:
            return response.read().decode("utf-8")

    def upload(self, filename, content):
        boundary = "OpenKBTestFormBoundary"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="source"; filename="{filename}"\r\n'
            'Content-Type: application/octet-stream\r\n\r\n'
        ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
        request = Request(
            self.address + "/documents/import",
            data=body,
            headers={"Origin": self.address, "Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urlopen(request, timeout=5) as response:
            self.assertIn("/documents?imported=", response.url)
            return response.read().decode("utf-8")

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
