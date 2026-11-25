"""Drive the local browser application through a live loopback server."""

from pathlib import Path
import socket
from tempfile import TemporaryDirectory
from threading import Thread
from time import monotonic, sleep
from unittest import TestCase
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from uvicorn import Config, Server

from openkb.server.runtime import AppSettings
from openkb.server.web import create_app


class BrowserCase(TestCase):
    def setUp(self):
        workspace = TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.settings = AppSettings.from_paths(self.root)
        app = create_app(self.settings)
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

    def post_form(self, path, fields):
        return self.post(path, urlencode(fields, doseq=True).encode(), "application/x-www-form-urlencoded")

    def post(self, path, body, content_type):
        return urlopen(Request(
            self.address + path,
            data=body,
            headers={"Origin": self.address, "Content-Type": content_type},
        ), timeout=5)

    def upload(self, filename, content):
        boundary = "OpenKBTestFormBoundary"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="source"; filename="{filename}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
        with self.post("/documents/import", body, f"multipart/form-data; boundary={boundary}") as response:
            self.assertIn("/documents?imported=", response.url)
            return response.read().decode("utf-8")
