from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import APIRouter, FastAPI
from fastapi import Request as ASGIRequest

from openkb.server.cli import main
from openkb.server.web import render


class LocalAppTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.data = self.root / "资料账本"
        self.config = self.root / "configured.toml"
        self.config.write_text(
            '[model]\nbase_url = "http://127.0.0.1:9/v1"\n'
            'api_key = "fixture-only-secret"\nmodel_id = "fixture-model"\n'
            '[prices]\nversion = "local"\n',
            encoding="utf-8",
        )

    def initialize(self, *flags):
        with redirect_stdout(StringIO()) as output:
            code = main(["init", "--data-dir", str(self.data), *flags])
        self.assertEqual(code, 0)
        self.assertNotIn("fixture-only-secret", output.getvalue())

    def test_initialization_preserves_local_database_and_reuses_configuration(self):
        self.initialize("--config", str(self.config))
        own_config = self.data / "config.toml"
        self.assertEqual(own_config.read_bytes(), self.config.read_bytes())
        database = self.data / "kb.db"
        self.assertTrue(database.read_bytes().startswith(b"SQLite format 3"))
        self.initialize()
        self.assertEqual(own_config.read_bytes(), self.config.read_bytes())

    def test_new_configuration_uses_a_hidden_prompt(self):
        with patch("openkb.server.local_cli.getpass", return_value="fixture-only-secret") as prompt:
            self.initialize("--base-url", "http://127.0.0.1:9/v1", "--model-id", "fixture-model")
        prompt.assert_called_once()
        contents = (self.data / "config.toml").read_text(encoding="utf-8")
        self.assertIn('api_key = "fixture-only-secret"', contents)
        self.assertIn('model_id = "fixture-model"', contents)

    def test_starts_outside_checkout_with_local_assets_and_rejects_cross_origin(self):
        self.initialize("--config", str(self.config))
        with socket.socket() as port_socket:
            port_socket.bind(("127.0.0.1", 0))
            port = port_socket.getsockname()[1]
        address = f"http://127.0.0.1:{port}"
        # The CLI is installed in the test environment, like a pipx installation.
        process = subprocess.Popen(
            [sys.executable, "-m", "openkb.server.cli", "serve", "--port", str(port),
             "--data-dir", str(self.data)],
            cwd=self.root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(self.stop, process)
        for _ in range(60):
            if process.poll() is not None:
                self.fail(f"Local server stopped with exit code {process.returncode}")
            try:
                with urlopen(address, timeout=1) as response:
                    html = response.read().decode("utf-8")
                break
            except (OSError, URLError):
                time.sleep(0.1)
        else:
            self.fail("Local server did not start")

        self.assertIn("OpenKB", html)
        self.assertIn("资料", html)
        self.assertIn('href="/reviews">复核</a>', html)
        self.assertNotIn("fixture-only-secret", html)
        with urlopen(f"{address}/static/app.css", timeout=3) as response:
            self.assertIn("text/css", response.headers["Content-Type"])
            self.assertIn(b"#1e3a5f", response.read())

        requests = [
            (Request(address, headers={"Host": "rebind.example"}), 400),
            (Request(address, data=b"", headers={"Origin": "http://untrusted.example"}), 403),
            (Request(address, data=b"", headers={"Origin": address}), 405),
        ]
        for request, status in requests:
            with self.assertRaises(HTTPError) as caught:
                urlopen(request, timeout=3)
            self.assertEqual(caught.exception.code, status)

    def test_navigation_includes_registered_workspace_routers(self):
        app = FastAPI()
        router = APIRouter()
        router.add_api_route("/documents", lambda: {}, name="documents")
        app.include_router(router)
        request = ASGIRequest({"type": "http", "path": "/", "headers": [], "app": app})

        response = render(request, "workspace.html", title="工作区")
        html = response.body.decode("utf-8")
        self.assertIn('href="/documents"', html)
        self.assertNotIn('href="/reviews"', html)

    @staticmethod
    def stop(process):
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=5)
