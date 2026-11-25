from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
from threading import Thread
import time
import unittest
from unittest.mock import patch
from urllib.error import URLError
from urllib.request import urlopen

from uvicorn import Config, Server

from openkb.agent.model import EndpointError, ModelClient
from openkb.server.cli import main
from openkb.server.runtime import AppSettings
from openkb.server.web import create_app


SECRET = "fixture-only-secret"
MODEL = "fixture-model"


class ModelEndpoint(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/v1/models" or self.headers.get("Authorization") != f"Bearer {SECRET}":
            self.send_error(401)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"data":[{"id":"fixture-model"}]}')

    def log_message(self, *_args):
        pass


class DoctorTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.data = self.root / "资料账本"
        self.config = self.root / "configured.toml"

    def write_config(self, port):
        self.config.write_text(
            f'[model]\nbase_url = "http://127.0.0.1:{port}/v1"\n'
            f'api_key = "{SECRET}"\nmodel_id = "{MODEL}"\n'
            '[prices]\nversion = "local"\n',
            encoding="utf-8",
        )

    def initialize(self):
        with redirect_stdout(StringIO()):
            self.assertEqual(main(["init", "--data-dir", str(self.data)]), 0)

    def doctor(self):
        with redirect_stdout(StringIO()) as output:
            code = main(["doctor", "--data-dir", str(self.data), "--config", str(self.config)])
        return code, output.getvalue()

    def test_doctor_checks_all_four_without_changing_workspace(self):
        self.initialize()
        self.write_config(9)
        database = self.data / "kb.db"
        initial = database.read_bytes()
        existing = set(self.data.iterdir())
        with patch.object(ModelClient, "_call", return_value={"data": [{"id": MODEL}]}) as call:
            code, output = self.doctor()
        self.assertEqual(code, 0)
        call.assert_called_once_with("GET", "models", None, 5)
        for label in ("数据目录", "SQLite", "模型配置", "模型端点"):
            self.assertIn(f"[通过] {label}", output)
        self.assertNotIn(SECRET, output)
        self.assertEqual(database.read_bytes(), initial)
        self.assertEqual(set(self.data.iterdir()), existing)

    def test_doctor_reports_broken_storage_and_hides_arbitrary_endpoint_errors(self):
        self.initialize()
        self.write_config(9)
        database = self.data / "kb.db"
        database.write_bytes(b"corrupt database")
        with patch.object(ModelClient, "_call", side_effect=EndpointError(f"server said {SECRET}")):
            code, output = self.doctor()
        self.assertEqual(code, 1)
        self.assertIn("[通过] 数据目录", output)
        self.assertIn("[失败] SQLite", output)
        self.assertIn("[通过] 模型配置", output)
        self.assertIn("[失败] 模型端点", output)
        self.assertNotIn(SECRET, output)
        self.assertEqual(database.read_bytes(), b"corrupt database")

    def test_missing_workspace_does_not_create_database_or_probe_model(self):
        with patch.object(ModelClient, "_call") as call:
            code, output = self.doctor()
        self.assertEqual(code, 1)
        self.assertIn("[失败] 数据目录", output)
        self.assertIn("[失败] SQLite", output)
        self.assertIn("[失败] 模型配置", output)
        self.assertIn("[未检查] 模型端点", output)
        self.assertFalse(self.data.exists())
        call.assert_not_called()

    def test_browser_status_reports_model_then_disconnect_without_breaking_home(self):
        self.initialize()
        endpoint = ThreadingHTTPServer(("127.0.0.1", 0), ModelEndpoint)
        worker = Thread(target=endpoint.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(endpoint.server_close)
        self.addCleanup(endpoint.shutdown)
        self.write_config(endpoint.server_port)
        browser_socket = socket.socket()
        self.addCleanup(browser_socket.close)
        browser_socket.bind(("127.0.0.1", 0))
        address = f"http://127.0.0.1:{browser_socket.getsockname()[1]}"
        app = create_app(AppSettings.from_paths(self.data, self.config))
        server = Server(Config(app, log_config=None, access_log=False))
        browser = Thread(target=server.run, kwargs={"sockets": [browser_socket]}, daemon=True)
        browser.start()
        self.addCleanup(self.stop, server, browser)

        def read(path):
            with urlopen(address + path, timeout=3) as response:
                return response.read().decode("utf-8")

        for _ in range(60):
            if not browser.is_alive():
                self.fail("Local server exited")
            try:
                home = read("/")
                break
            except (OSError, URLError):
                time.sleep(0.1)
        else:
            self.fail("Local server did not start")
        self.assertIn('href="/status"', home)
        healthy = read("/status")
        self.assertIn(MODEL, healthy)
        self.assertIn("端点可达", healthy)
        self.assertNotIn(SECRET, healthy)
        endpoint.shutdown()
        endpoint.server_close()
        offline = read("/status")
        self.assertIn(MODEL, offline)
        self.assertIn("端点不可达", offline)
        self.assertNotIn(SECRET, offline)
        self.assertIn("OpenKB", read("/"))

    @staticmethod
    def stop(server, browser):
        server.should_exit = True
        browser.join(timeout=5)
